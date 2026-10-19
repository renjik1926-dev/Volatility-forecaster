"""One evening's run (§14): fetch → validate → store → score yesterday → forecast tomorrow →
write → proof snapshot. Building the site, the privacy check, the pushes and the deploy follow
in `cli.run-daily` and `daily.yml`.

The job never trusts the runner's clock date; it asks the calendar for the latest completed
session. Re-running for a session that is already processed changes nothing.
"""

from __future__ import annotations

import datetime as dt
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from volforecast.calendar import TradingCalendar
from volforecast.config import MAX_ABS_RETURN, TRADING_DAYS_PER_YEAR, VIX_TICKER, Settings
from volforecast.estimators import daily_frame
from volforecast.forecast import ModelState, build_model_data, forecast_next
from volforecast.models import Model, all_models
from volforecast.proofs import Prover, snapshot_path
from volforecast.providers.base import (
    AuthError,
    BudgetExceededError,
    PremiumEndpointError,
    PriceProvider,
    ProviderError,
    RateLimitError,
    SeriesProvider,
)
from volforecast.scoring import Realized, score
from volforecast.store import (
    FORECAST_COLUMNS,
    PrivateStore,
    PublicStore,
    input_hash,
    write_rows,
)
from volforecast.validate import check_bars, check_dates, large_returns, merge_bars

# Run statuses written to runs.csv
OK = "ok"
PARTIAL = "partial"
ALREADY_DONE = "already_done"
DATA_NOT_READY = "data_not_ready"
BUDGET_EXHAUSTED = "budget_exhausted"
RATE_LIMITED = "rate_limited"
PREMIUM = "premium_endpoint"
AUTH_ERROR = "auth_error"
PROVIDER_ERROR = "provider_error"
FAILED = "failed"
PUBLISHABLE = {OK, PARTIAL}
FATAL = {BUDGET_EXHAUSTED, RATE_LIMITED, PREMIUM, AUTH_ERROR, PROVIDER_ERROR, FAILED}


@dataclass
class LiveContext:
    settings: Settings
    calendar: TradingCalendar
    prices: PriceProvider
    vix: SeriesProvider | None
    private: PrivateStore
    public: PublicStore
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(tz=dt.UTC)
    sleep: Callable[[float], None] = time.sleep
    run_kind: str = "manual"  # primary | backup | manual
    code_version: str = "unknown"
    prover: Prover | None = None
    models: list[Model] | None = None
    retry_minutes: float = 10.0
    api_calls: Callable[[], int] = lambda: 0


@dataclass
class RunResult:
    run_id: str
    status: str
    session: dt.date | None
    notes: list[str] = field(default_factory=list)
    forecasts_written: int = 0
    scores_written: int = 0
    api_calls: int = 0
    ready_tickers: list[str] = field(default_factory=list)


class StopRun(Exception):
    def __init__(self, status: str, note: str) -> None:
        super().__init__(note)
        self.status = status
        self.note = note


# --- helpers -----------------------------------------------------------------------------------
def already_processed(public: PublicStore, session: dt.date) -> bool:
    runs = public.runs.read()
    if runs.empty:
        return False
    done = runs[(runs["session_date"] == session.isoformat()) & (runs["status"] == OK)]
    return not done.empty


def _provider_status(exc: ProviderError) -> str:
    if isinstance(exc, BudgetExceededError):
        return BUDGET_EXHAUSTED
    if isinstance(exc, RateLimitError):
        return RATE_LIMITED
    if isinstance(exc, PremiumEndpointError):
        return PREMIUM
    if isinstance(exc, AuthError):
        return AUTH_ERROR
    return PROVIDER_ERROR


def refresh_actions(ctx: LiveContext, ticker: str, session: dt.date) -> None:
    dividends = ctx.prices.dividends(ticker)
    splits = ctx.prices.splits(ticker)
    old_div, old_spl = ctx.private.dividends(ticker), ctx.private.splits(ticker)
    ctx.private.save_actions(ticker, dividends, splits, refreshed=session)
    # A dividend or split that appears for an already-processed date is a revision (§10.2).
    stamp = ctx.now().isoformat(timespec="seconds")
    revisions = []
    for kind, old, new, col in (
        ("dividend", old_div, dividends, "amount"),
        ("split", old_spl, splits, "factor"),
    ):
        known = {(pd.Timestamp(d), float(v)) for d, v in zip(old["date"], old[col], strict=True)}
        for d, v in zip(new["date"], new[col], strict=True):
            new_record = (pd.Timestamp(d), float(v)) not in known
            # A record dated on or before this session that wasn't known before is a revision;
            # the very first refresh of a ticker isn't.
            if new_record and pd.Timestamp(d) <= pd.Timestamp(session) and len(old):
                revisions.append(
                    {
                        "ticker": ticker,
                        "date": pd.Timestamp(d).date().isoformat(),
                        "field": kind,
                        "old": "",
                        "new": v,
                        "detected_utc": stamp,
                    }
                )
    ctx.private.revisions.append(revisions)


def fetch_missing(ctx: LiveContext, session: dt.date, tickers: list[str]) -> list[str]:
    """Fetch the compact series for tickers without a bar dated `session`; return still-missing."""
    missing = []
    for ticker in tickers:
        if ctx.private.has_bar(ticker, session):
            continue
        fetched = ctx.prices.daily_bars(ticker)
        merged, revisions = merge_bars(ticker, ctx.private.bars(ticker), fetched, ctx.now())
        ctx.private.save_bars(ticker, merged)
        ctx.private.revisions.append(revisions.to_dict("records"))
        if not ctx.private.has_bar(ticker, session):
            missing.append(ticker)
    return missing


def _frame(ctx: LiveContext, ticker: str, session: dt.date) -> pd.DataFrame:
    bars = ctx.private.bars(ticker)
    bars = bars[bars["date"] <= pd.Timestamp(session)]
    early = {pd.Timestamp(d) for d in bars["date"] if ctx.calendar.is_early_close(d.date())}
    return daily_frame(bars, ctx.private.dividends(ticker), ctx.private.splits(ticker), early)


def validate_ticker(ctx: LiveContext, ticker: str, session: dt.date, notes: list[str]) -> bool:
    bars = ctx.private.bars(ticker)
    bars = bars[bars["date"] <= pd.Timestamp(session)]
    issues = check_bars(ticker, bars)
    if len(bars):
        expected = ctx.calendar.sessions(bars["date"].iloc[0].date(), session)
        issues += check_dates(ticker, bars, expected)
    errors = [i for i in issues if i.severity == "error"]
    if not errors:
        big = large_returns(ticker, _frame(ctx, ticker, session), MAX_ABS_RETURN)
        if big and ctx.private.actions_refreshed_on(ticker) != session:
            refresh_actions(ctx, ticker, session)  # a missed split is the usual cause
            big = large_returns(ticker, _frame(ctx, ticker, session), MAX_ABS_RETURN)
        errors = big
    for issue in errors[:5]:
        notes.append(f"validation: {issue}")
    return not errors


def realized_for(frame: pd.DataFrame, session: dt.date) -> Realized | None:
    row = frame[frame["date"] == pd.Timestamp(session)]
    if row.empty:
        return None
    rec = row.iloc[0]
    return Realized(
        r=float(rec["r"]),
        proxy_rs=float(rec["proxy_rs"]),
        proxy_r2=float(rec["proxy_r2"]),
        proxy_gk=float(rec["proxy_gk"]),
        early_close=bool(rec["early_close"]),
    )


def score_session(
    ctx: LiveContext, session: dt.date, tickers: list[str], notes: list[str]
) -> list[dict[str, Any]]:
    forecasts = ctx.public.forecasts.read()
    scored = ctx.public.scores.keys() if ctx.public.scores.exists() else set()
    rows: list[dict[str, Any]] = []
    for ticker in tickers:
        mine = forecasts[
            (forecasts["ticker"] == ticker) & (forecasts["target_date"] == session.isoformat())
        ]
        if mine.empty:
            if not forecasts.empty and (forecasts["ticker"] == ticker).any():
                notes.append(f"gap: no {ticker} forecasts targeted {session}")
            continue
        realized = realized_for(_frame(ctx, ticker, session), session)
        if realized is None:
            notes.append(f"score: no {ticker} bar for {session}")
            continue
        for rec in mine.to_dict("records"):
            if rec["forecast_id"] in scored or rec["variance"] == "":
                continue
            rows.append(score(rec, realized, ctx.now()))
    return rows


def forecast_rows(
    ctx: LiveContext, session: dt.date, tickers: list[str], vix: pd.Series | None
) -> list[dict[str, Any]]:
    target = ctx.calendar.next_session(session)
    open_utc = ctx.calendar.session_open_utc(target)
    existing = ctx.public.forecasts.keys() if ctx.public.forecasts.exists() else set()
    models = ctx.models if ctx.models is not None else all_models()
    rows: list[dict[str, Any]] = []
    for ticker in tickers:
        frame = _frame(ctx, ticker, session)
        use_vix = vix if ticker == VIX_TICKER and vix is not None else None
        vix_upto = None if use_vix is None else use_vix[use_vix.index <= pd.Timestamp(session)]
        data = build_model_data(frame, vix=vix_upto, calendar=ctx.calendar)
        state = ModelState.from_dict(ctx.private.params(ticker))
        results = forecast_next(data, models, state, refit=True, max_window=ctx.settings.max_window)
        ctx.private.save_params(ticker, state.to_dict())
        bars = ctx.private.bars(ticker)
        inputs = [
            bars[bars["date"] <= pd.Timestamp(session)],
            ctx.private.dividends(ticker),
            ctx.private.splits(ticker),
        ]
        if vix_upto is not None:
            inputs.append(vix_upto)
        digest = input_hash(*inputs)
        created = ctx.now()
        for res in results:
            fid = f"{ticker}-{target.isoformat()}-{res.model}"
            if fid in existing:
                continue
            vol = None if res.variance is None else math.sqrt(res.variance * TRADING_DAYS_PER_YEAR)
            rows.append(
                {
                    "forecast_id": fid,
                    "ticker": ticker,
                    "made_after_close_of": session.isoformat(),
                    "target_date": target.isoformat(),
                    "model": res.model,
                    "model_version": res.model_version,
                    "fit_status": res.fit_status,
                    "variance": res.variance,
                    "vol_annualized": vol,
                    "var95": res.var95,
                    "var99": res.var99,
                    "dist": res.dist or "",
                    "code_version": ctx.code_version,
                    "input_hash": digest,
                    "created_utc": created,
                    "on_time": created < open_utc,
                    "notes": res.notes,
                }
            )
    return rows


def _load_vix(ctx: LiveContext, session: dt.date, notes: list[str]) -> pd.Series | None:
    if ctx.vix is None:
        return ctx.private.series("vix") if not ctx.private.series("vix").empty else None
    # Normally the last 45 days are enough to pick up new and revised values. If the stored VIX
    # doesn't reach back to the start of SPY's price history (e.g. on the first run), fetch
    # from there, so vix_mz has as many past pairs as the prices allow. FRED calls are free.
    start = session - dt.timedelta(days=45)
    spy = ctx.private.bars(VIX_TICKER)
    stored_before = ctx.private.series("vix").dropna()
    if len(spy):
        first_bar = spy["date"].iloc[0].date()
        # A week of slack, so a holiday at the start doesn't trigger a full refetch every run.
        if stored_before.empty or stored_before.index[0].date() > first_bar + dt.timedelta(days=7):
            start = min(start, first_bar)
    try:
        fresh = ctx.vix.series("VIXCLS", start=start)
        stored = ctx.private.merge_series("vix", fresh)
    except ProviderError as exc:
        notes.append(f"vix: {exc}")
        stored = ctx.private.series("vix")
    if stored.empty:
        return None
    upto = stored[stored.index <= pd.Timestamp(session)].dropna()
    if not upto.empty and upto.index[-1].date() < session:
        notes.append(f"vix: latest value is from {upto.index[-1].date()}")
    return stored


# --- the evening job -----------------------------------------------------------------------------
def run_daily(ctx: LiveContext) -> RunResult:
    started = ctx.now()
    run_id = f"{started.strftime('%Y%m%dT%H%M%SZ')}-{ctx.run_kind}"
    session = ctx.calendar.latest_completed_session(started)
    result = RunResult(run_id, OK, session)
    try:
        _run(ctx, session, result)
    except StopRun as stop:
        result.status = stop.status
        result.notes.append(stop.note)
    except ProviderError as exc:
        result.status = _provider_status(exc)
        result.notes.append(str(exc))
    except Exception as exc:  # recorded in runs.csv, then re-raised by the CLI as a failure
        result.status = FAILED
        result.notes.append(f"{type(exc).__name__}: {exc}"[:300])
    result.api_calls = ctx.api_calls()
    ctx.public.runs.append(
        [
            {
                "run_id": run_id,
                "started_utc": started,
                "finished_utc": ctx.now(),
                "status": result.status,
                "session_date": session.isoformat(),
                "api_calls": result.api_calls,
                "notes": " | ".join(result.notes)[:1000],
            }
        ]
    )
    return result


def _run(ctx: LiveContext, session: dt.date, result: RunResult) -> None:
    notes = result.notes
    if already_processed(ctx.public, session):
        raise StopRun(ALREADY_DONE, f"session {session} already processed")
    tickers = list(ctx.settings.tickers)

    # Step 2: fetch only what's missing. Corporate actions refresh weekly on the primary run,
    # and right away for a ticker whose dividends and splits were never fetched (first run).
    missing = fetch_missing(ctx, session, tickers)
    weekly = ctx.run_kind != "backup" and ctx.calendar.is_first_session_of_week(session)
    for ticker in tickers:
        refreshed = ctx.private.actions_refreshed_on(ticker)
        if refreshed is None or (weekly and refreshed != session):
            refresh_actions(ctx, ticker, session)
    waited = 0.0
    while missing and waited < ctx.settings.data_wait_minutes:
        step = min(ctx.retry_minutes, ctx.settings.data_wait_minutes - waited)
        ctx.sleep(step * 60)
        waited += step
        missing = fetch_missing(ctx, session, missing)
    if missing and (ctx.run_kind == "primary" or len(missing) == len(tickers)):
        raise StopRun(DATA_NOT_READY, f"no bar dated {session} for {', '.join(missing)}")
    if missing:
        notes.append(f"partial: no bar dated {session} for {', '.join(missing)}")
        result.status = PARTIAL

    # Steps 3–4: VIX, validation.
    vix = _load_vix(ctx, session, notes) if VIX_TICKER in tickers else None
    ready = [t for t in tickers if t not in missing and validate_ticker(ctx, t, session, notes)]
    if not ready:
        raise StopRun(FAILED, "no ticker passed validation")
    if len(ready) < len(tickers):
        result.status = PARTIAL
    result.ready_tickers = ready

    # Step 5: score the forecasts that targeted this session.
    scores = score_session(ctx, session, ready, notes)
    result.scores_written = ctx.public.scores.append(scores)

    # Steps 6–7: forecast the next session and append.
    rows = forecast_rows(ctx, session, ready, vix)
    result.forecasts_written = ctx.public.forecasts.append(rows)
    fallbacks = sorted({r["model"] for r in rows if r["fit_status"] == "fallback"})
    if fallbacks:
        notes.append(f"fallback fits: {', '.join(fallbacks)}")
    late = [r for r in rows if not r["on_time"]]
    if late:
        notes.append(f"{len(late)} forecasts created after the next open (on_time=false)")

    # Step 8: proof snapshot of this evening's rows, plus any file a previous run missed.
    if rows:
        # A second run for the same session (after a partial one) gets its own file, so a
        # file that is already stamped is never rewritten.
        snapshot = snapshot_path(ctx.public.proofs_dir, session)
        write_rows(snapshot, FORECAST_COLUMNS, rows)
        if ctx.prover is not None:
            outcome = ctx.prover.stamp(snapshot, ctx.now())
            if not outcome.ok:
                notes.append(f"proof: stamping failed ({outcome.detail}); next run retries")
            for retry in ctx.prover.stamp_missing(ctx.now(), today=snapshot):
                if retry.ok:
                    notes.append(f"proof: stamped {retry.file.name} late")
        else:
            notes.append("proof: no prover configured")
