"""Command-line interface (§10.9)."""

from __future__ import annotations

import datetime as dt
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Annotated

import pandas as pd
import typer

from volforecast import __version__
from volforecast.backtest import (
    LABEL,
    backtest_models,
    default_start,
    short_history_experiment,
    summarize_by_period,
    tune_prior_sessions,
    walk_forward,
)
from volforecast.calendar import NEW_YORK, TradingCalendar
from volforecast.config import BACKTEST_TICKER, Settings, code_version, load_settings
from volforecast.estimators import close_only_frame
from volforecast.forecast import ModelState, build_model_data, forecast_next
from volforecast.health import run_checks
from volforecast.live import FATAL, PUBLISHABLE, LiveContext, run_daily
from volforecast.models import all_models
from volforecast.privacy import scan
from volforecast.proofs import Prover, verify_forecasts
from volforecast.providers.alphavantage import AlphaVantageClient
from volforecast.providers.base import PriceProvider, ProviderError, SeriesProvider
from volforecast.providers.budget import CallBudget
from volforecast.providers.fred import FredClient
from volforecast.providers.recorded import RecordedProvider
from volforecast.site.build import build_site
from volforecast.store import FORECAST_COLUMNS, SCORE_COLUMNS, PrivateStore, PublicStore, write_rows

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)


def _now(value: str | None) -> dt.datetime:
    if value is None:
        return dt.datetime.now(tz=dt.UTC)
    parsed = dt.datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def _stores(settings: Settings) -> tuple[PublicStore, PrivateStore, TradingCalendar]:
    public = PublicStore(settings.public_dir)
    private = PrivateStore(settings.private_dir)
    return public, private, TradingCalendar(public.no_session_dates())


def _budget(
    settings: Settings, private: PrivateStore, now: dt.datetime | None = None
) -> CallBudget:
    """The Alpha Vantage call budget; with `--now`, the budget day follows the pretend time."""
    if now is None:
        return CallBudget(private.budget_path, settings.budget_limit)
    day = now.astimezone(NEW_YORK).date()
    return CallBudget(private.budget_path, settings.budget_limit, today=lambda: day)


def _write_output(path: Path | None, **values: object) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


@app.command("run-daily")
def run_daily_cmd(
    run_kind: Annotated[str, typer.Option(help="primary, backup or manual")] = "manual",
    github_output: Annotated[Path | None, typer.Option(help="Append status=… for Actions")] = None,
    wait_minutes: Annotated[
        float | None, typer.Option(help="How long to wait for a late bar")
    ] = None,
    recorded: Annotated[
        Path | None, typer.Option(help="Replay recorded responses (no network)")
    ] = None,
    now: Annotated[str | None, typer.Option(help="Pretend the time is this ISO timestamp")] = None,
    build: Annotated[bool, typer.Option(help="Rebuild the site afterwards")] = True,
    proofs: Annotated[
        bool, typer.Option(help="Stamp the proof snapshot with OpenTimestamps")
    ] = True,
) -> None:
    """The whole evening job: fetch, validate, score, forecast, proof, site, privacy check."""
    settings = load_settings()
    if wait_minutes is not None:
        settings = replace(settings, data_wait_minutes=wait_minutes)
    public, private, calendar = _stores(settings)
    fixed_now = _now(now) if now else None
    budget = _budget(settings, private, fixed_now)
    calls_before = budget.used_today()
    prices: PriceProvider
    vix: SeriesProvider | None
    if recorded is not None:
        prices = vix = RecordedProvider(recorded, budget)
    else:
        prices = AlphaVantageClient(
            settings.alphavantage_api_key, budget, spacing_seconds=settings.call_spacing_seconds
        )
        vix = FredClient(settings.fred_api_key) if settings.fred_api_key else None
    ctx = LiveContext(
        settings=settings,
        calendar=calendar,
        prices=prices,
        vix=vix,
        private=private,
        public=public,
        now=(lambda: fixed_now) if fixed_now else (lambda: dt.datetime.now(tz=dt.UTC)),
        run_kind=run_kind,
        code_version=code_version(),
        prover=Prover(public.proofs_dir) if proofs else None,
        api_calls=lambda: budget.used_today() - calls_before,
    )
    result = run_daily(ctx)
    typer.echo(
        json.dumps(
            {
                "run_id": result.run_id,
                "status": result.status,
                "session": result.session.isoformat() if result.session else None,
                "forecasts": result.forecasts_written,
                "scores": result.scores_written,
                "api_calls": result.api_calls,
                "notes": result.notes,
            }
        )
    )
    privacy_ok = True
    if build and result.status in PUBLISHABLE:
        build_site(public, settings.site_dir, calendar, ctx.now())
    findings = scan([public.root, settings.site_dir])
    if findings:
        privacy_ok = False
        for finding in findings:
            typer.echo(f"PRIVACY: {finding}", err=True)
    publish = result.status in PUBLISHABLE and privacy_ok
    _write_output(github_output, status=result.status, publish=str(publish).lower())
    if not privacy_ok:
        raise typer.Exit(2)
    if result.status in FATAL or (result.status == "data_not_ready" and run_kind == "backup"):
        raise typer.Exit(1)


@app.command()
def fetch(
    now: Annotated[str | None, typer.Option(help="Pretend the time is this ISO timestamp")] = None,
) -> None:
    """Fetch and store missing bars for the latest completed session (no forecasts)."""
    from volforecast.live import fetch_missing

    settings = load_settings()
    public, private, calendar = _stores(settings)
    budget = _budget(settings, private)
    client = AlphaVantageClient(settings.alphavantage_api_key, budget)
    session = calendar.latest_completed_session(_now(now))
    ctx = LiveContext(settings, calendar, client, None, private, public)
    try:
        missing = fetch_missing(ctx, session, list(settings.tickers))
    except ProviderError as exc:
        typer.echo(f"fetch failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"session {session}: missing {missing or 'none'}; calls today {budget.used_today()}")


@app.command()
def forecast(
    ticker: Annotated[str, typer.Option(help="Ticker to forecast")] = "SPY",
) -> None:
    """Print tomorrow's forecasts from the stored private data. Writes nothing."""
    from volforecast.estimators import daily_frame

    settings = load_settings()
    _, private, calendar = _stores(settings)
    bars = private.bars(ticker)
    if bars.empty:
        typer.echo(f"no stored bars for {ticker}; run `volforecast fetch` first", err=True)
        raise typer.Exit(1)
    frame = daily_frame(bars, private.dividends(ticker), private.splits(ticker))
    vix = private.series("vix") if ticker == "SPY" else None
    data = build_model_data(
        frame, vix=vix if vix is not None and len(vix) else None, calendar=calendar
    )
    results = forecast_next(data, all_models(), ModelState(), max_window=settings.max_window)
    for res in results:
        vol = "" if res.variance is None else f"{(res.variance * 252) ** 0.5:.2%}"
        typer.echo(f"{res.model:14} {res.fit_status:10} {vol:>8} {res.notes}")


@app.command()
def score() -> None:
    """Print the live scoreboard (on-time forecasts only) to the terminal."""
    from volforecast.evaluate import loss_table, var_table
    from volforecast.site.build import joined, load

    settings = load_settings()
    public, _, _ = _stores(settings)
    data = load(public)
    live = joined(data.forecasts, data.scores, live_only=True)
    if live.empty:
        typer.echo("no scored forecasts yet")
        return
    with pd.option_context("display.width", 200, "display.max_columns", 30):
        for ticker, group in live.groupby("ticker"):
            typer.echo(f"\n== {ticker}")
            typer.echo(loss_table(group).to_string(index=False))
            typer.echo(var_table(group).to_string(index=False))


@app.command()
def snapshot(
    force: Annotated[bool, typer.Option(help="Overwrite an existing SP500 snapshot")] = False,
) -> None:
    """One-time FRED snapshot of SP500 and VIXCLS into the private data repo (§10.2)."""
    settings = load_settings()
    _, private, _ = _stores(settings)
    if not private.series("sp500").empty and not force:
        typer.echo("SP500 snapshot already exists; it is never refreshed (use --force to replace)")
        raise typer.Exit(1)
    client = FredClient(settings.fred_api_key)
    sp500 = client.series("SP500")
    vix = client.series("VIXCLS")
    private.save_series("sp500", sp500)
    private.merge_series("vix", vix)
    typer.echo(
        f"saved SP500 {sp500.index.min().date()}–{sp500.index.max().date()} "
        f"({sp500.notna().sum()} values) and VIXCLS ({vix.notna().sum()} values)"
    )


@app.command()
def backtest(
    out: Annotated[Path | None, typer.Option(help="Output folder (default data/backtest)")] = None,
    prior_sessions: Annotated[
        float | None, typer.Option(help="Frozen garch_t_sh prior strength")
    ] = None,
    tune: Annotated[bool, typer.Option(help="Only tune the prior on the study period")] = False,
    short_history: Annotated[
        bool, typer.Option(help="Also run the short-history experiment")
    ] = True,
) -> None:
    """Walk-forward S&P 500 index backtest, prior tuning and the short-history experiment."""
    settings = load_settings()
    public, private, calendar = _stores(settings)
    out = out or public.backtest_dir
    closes = private.series("sp500")
    if closes.empty:
        typer.echo("no SP500 snapshot; run `volforecast snapshot` first", err=True)
        raise typer.Exit(1)
    vix = private.series("vix")
    data = build_model_data(
        close_only_frame(closes), vix=vix if len(vix) else None, calendar=calendar
    )
    if tune:
        table = tune_prior_sessions(data)
        out.mkdir(parents=True, exist_ok=True)
        table.to_csv(out / "prior_tuning.csv", index=False)
        typer.echo(table.to_string(index=False))
        return

    def progress(done: int, total: int) -> None:
        if done % 250 == 0 or done == total:
            typer.echo(f"  {done}/{total} sessions", err=True)

    typer.echo(f"Backtest: {LABEL}", err=True)
    result = walk_forward(
        data,
        backtest_models(prior_sessions),
        start_index=default_start(data),
        refit_every=settings.backtest_refit_every,
        max_window=settings.max_window,
        ticker=BACKTEST_TICKER,
        code_version=code_version(),
        progress=progress,
    )
    write_rows(out / "spx_forecasts.csv", FORECAST_COLUMNS, result.forecasts)
    write_rows(out / "spx_scores.csv", SCORE_COLUMNS, result.scores)
    summarize_by_period(result).to_csv(out / "spx_summary.csv", index=False)
    if short_history:
        typer.echo("Short-history experiment", err=True)
        short_history_experiment(data, prior_sessions=prior_sessions).to_csv(
            out / "short_history.csv", index=False
        )
    findings = scan([out])
    if findings:
        for finding in findings:
            typer.echo(f"PRIVACY: {finding}", err=True)
        raise typer.Exit(2)
    typer.echo(f"wrote {len(result.forecasts)} forecasts and {len(result.scores)} scores to {out}")


@app.command()
def site(
    now: Annotated[str | None, typer.Option(help="Pretend the time is this ISO timestamp")] = None,
) -> None:
    """Rebuild the static site from data/."""
    settings = load_settings()
    public, _, calendar = _stores(settings)
    written = build_site(public, settings.site_dir, calendar, _now(now))
    typer.echo(f"built {len(written)} pages in {settings.site_dir}")


@app.command()
def health(
    now: Annotated[str | None, typer.Option(help="Pretend the time is this ISO timestamp")] = None,
) -> None:
    """Check the last run, last forecast, fallbacks, API calls, private repo and proofs."""
    settings = load_settings()
    public, private, calendar = _stores(settings)
    checks = run_checks(
        public, private, calendar, _budget(settings, private), _now(now), Prover(public.proofs_dir)
    )
    for check in checks:
        typer.echo(str(check))
    if any(c.level == "problem" for c in checks):
        raise typer.Exit(1)


@app.command("verify-proofs")
def verify_proofs() -> None:
    """Check that every committed forecast row appears unchanged in a stamped proof file."""
    settings = load_settings()
    public, _, _ = _stores(settings)
    problems = verify_forecasts(public.forecasts.read(), public.proofs_dir)
    for problem in problems:
        typer.echo(problem, err=True)
    if problems:
        raise typer.Exit(1)
    typer.echo("every forecast row is covered by a stamped proof file")


@app.command("upgrade-proofs")
def upgrade_proofs() -> None:
    """Run `ots upgrade` on pending proofs (weekly, in proofs.yml)."""
    settings = load_settings()
    public, _, _ = _stores(settings)
    for name, status in Prover(public.proofs_dir).upgrade_all().items():
        typer.echo(f"{name}: {status}")


@app.command("privacy-check")
def privacy_check(paths: list[Path]) -> None:
    """Fail on any raw price, volume or return column in the given files or folders."""
    findings = scan(paths)
    for finding in findings:
        typer.echo(f"PRIVACY: {finding}", err=True)
    if findings:
        raise typer.Exit(2)
    typer.echo("privacy check passed")


@app.command()
def smoke() -> None:
    """At most 3 real API calls to confirm the keys and response formats. Never run in CI."""
    settings = load_settings()
    _, private, _ = _stores(settings)
    budget = _budget(settings, private)
    failures = 0
    try:
        client = AlphaVantageClient(settings.alphavantage_api_key, budget)
        bars = client.daily_bars("SPY")
        last_day = bars["date"].max().date()
        typer.echo(f"Alpha Vantage TIME_SERIES_DAILY: ok, {len(bars)} sessions through {last_day}")
        divs = client.dividends("SPY")
        typer.echo(f"Alpha Vantage DIVIDENDS: ok, {len(divs)} records")
    except ProviderError as exc:
        failures += 1
        typer.echo(f"Alpha Vantage: FAILED ({type(exc).__name__}: {exc})", err=True)
    try:
        vix = FredClient(settings.fred_api_key).series(
            "VIXCLS", start=dt.date.today() - dt.timedelta(days=14)
        )
        latest = vix.dropna()
        last_obs = latest.index.max().date() if len(latest) else "none"
        typer.echo(f"FRED VIXCLS: ok, latest observation {last_obs}")
    except ProviderError as exc:
        failures += 1
        typer.echo(f"FRED: FAILED ({type(exc).__name__}: {exc})", err=True)
    typer.echo(f"Alpha Vantage calls used today: {budget.used_today()} of {budget.limit}")
    if failures:
        raise typer.Exit(1)


@app.command()
def version() -> None:
    """Print the package and code versions."""
    typer.echo(f"volforecast {__version__} ({code_version()}) on Python {sys.version.split()[0]}")


if __name__ == "__main__":
    app()
