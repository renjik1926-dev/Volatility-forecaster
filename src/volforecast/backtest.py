"""Walk-forward backtest on the S&P 500 index stand-in, and the short-history experiment (§13).

The loop calls the same `forecast_next` and `score` functions as the live job (D5). At each
session t it forecasts from history ≤ t and scores against t+1. Parameters are re-estimated
every 21 sessions; in between, the variance recursion is updated daily with fixed parameters.

Every table and chart produced from here is labeled "S&P 500 index, closing prices, no
dividends: a stand-in for SPY". Outputs contain derived values only.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from volforecast.config import BACKTEST_TICKER, OOS_START, STUDY_END
from volforecast.evaluate import var_tests
from volforecast.forecast import ForecastResult, ModelState, forecast_next
from volforecast.models import Model, ModelData, select
from volforecast.models.garch_sh import GarchTShortHistory
from volforecast.scoring import Realized, score

LABEL = "S&P 500 index, closing prices, no dividends: a stand-in for SPY"
BACKTEST_MODELS = (
    "rw",
    "hist21",
    "ewma_094",
    "garch_t_sh",
    "garch_n",
    "garch_t",
    "gjr_t",
    "egarch_t",
    "har_log",
    "vix",
    "vix_mz",
    "ensemble",
)
SHORT_HISTORY_NS = (100, 150, 250, 500, 1000)
SHORT_HISTORY_MODELS = ("ewma_094", "garch_t", "garch_t_sh")


def period_of(day: dt.date) -> str:
    if day <= STUDY_END:
        return "study"
    return "out_of_sample" if day >= OOS_START else "gap"


def _date(value: np.datetime64) -> dt.date:
    return pd.Timestamp(value).date()


def _realized(data: ModelData, i: int) -> Realized:
    """What happened at index i, from the full data (used only after the forecast is made)."""
    r2 = float(data.r[i] ** 2)
    if data.has_range:
        return Realized(
            r=float(data.r[i]), proxy_rs=float(data.proxy[i]), proxy_r2=r2, proxy_gk=None
        )
    return Realized(r=float(data.r[i]), proxy_rs=None, proxy_r2=r2, proxy_gk=None)


@dataclass
class BacktestOutput:
    forecasts: list[dict[str, Any]]
    scores: list[dict[str, Any]]


def _forecast_row(
    ticker: str, made: dt.date, target: dt.date, res: ForecastResult, code_version: str
) -> dict[str, Any]:
    from volforecast.store import format_value  # local import keeps this module light

    vol = None if res.variance is None else float(np.sqrt(res.variance * 252))
    return {
        "forecast_id": f"{ticker}-{target.isoformat()}-{res.model}",
        "ticker": ticker,
        "made_after_close_of": made.isoformat(),
        "target_date": target.isoformat(),
        "model": res.model,
        "model_version": res.model_version,
        "fit_status": res.fit_status,
        "variance": res.variance,
        "vol_annualized": vol,
        "var95": res.var95,
        "var99": res.var99,
        "dist": res.dist or "",
        "code_version": code_version,
        "input_hash": "",
        "created_utc": "",
        "on_time": "",
        "notes": format_value(res.notes),
    }


def walk_forward(
    data: ModelData,
    models: Sequence[Model],
    *,
    start_index: int = 22,
    end_index: int | None = None,
    refit_every: int = 21,
    max_window: int = 1000,
    ticker: str = BACKTEST_TICKER,
    code_version: str = "",
    scored_utc: dt.datetime | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> BacktestOutput:
    """Forecast after each session i in [start_index, end_index) and score against i+1."""
    last = data.n - 1 if end_index is None else min(end_index, data.n - 1)
    stamp = scored_utc or dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
    state = ModelState()
    forecasts: list[dict[str, Any]] = []
    scores: list[dict[str, Any]] = []
    for step, i in enumerate(range(start_index, last)):
        history = data.head(i + 1)  # data up to and including session i only
        results = forecast_next(
            history, models, state, refit=step % refit_every == 0, max_window=max_window
        )
        made, target = _date(data.dates[i]), _date(data.dates[i + 1])
        realized = _realized(data, i + 1)
        for res in results:
            row = _forecast_row(ticker, made, target, res, code_version)
            forecasts.append(row)
            if res.has_values:
                scores.append(score(row, realized, stamp))
        if progress is not None:
            progress(step + 1, last - start_index)
    return BacktestOutput(forecasts, scores)


def default_start(data: ModelData) -> int:
    return 22 if data.n > 22 else data.n


def summarize_by_period(out: BacktestOutput) -> pd.DataFrame:
    """Mean QLIKE and VaR violation rates per model and period (study / out-of-sample)."""
    if not out.scores:
        return pd.DataFrame()
    f = pd.DataFrame(out.forecasts)[["forecast_id", "model", "target_date"]]
    s = pd.DataFrame(out.scores).merge(f, on="forecast_id")
    s["period"] = [period_of(dt.date.fromisoformat(d)) for d in s["target_date"]]
    rows = []
    for (period, model), g in s.groupby(["period", "model"]):
        t95 = var_tests(g["viol95"].astype(float), 0.95)
        t99 = var_tests(g["viol99"].astype(float), 0.99)
        rows.append(
            {
                "period": period,
                "model": model,
                "n": len(g),
                "mean_qlike": float(g["qlike"].mean()),
                "rate95": t95.rate,
                "kupiec_p95": t95.kupiec_p,
                "rate99": t99.rate,
                "kupiec_p99": t99.kupiec_p,
                "label": LABEL,
            }
        )
    return pd.DataFrame(rows)


# --- prior strength tuning (study period only) ---------------------------------------------------
def tune_prior_sessions(
    data: ModelData,
    grid: Sequence[float] = (0.0, 50.0, 100.0, 250.0, 500.0, 1000.0),
    *,
    window: int = 250,
    refit_every: int = 21,
) -> pd.DataFrame:
    """Mean QLIKE of `garch_t_sh` for each prior strength, on the study period only (§13).

    The data are truncated at STUDY_END before anything else happens, so no out-of-sample
    session can influence the choice. A short rolling window is used because the prior only
    matters when history is short.
    """
    cutoff = np.datetime64(STUDY_END.isoformat())
    study = data.head(int(np.searchsorted(data.dates, cutoff, side="right")))
    rows = []
    for strength in grid:
        out = walk_forward(
            study,
            [GarchTShortHistory(strength)],
            start_index=min(window, study.n - 1),
            refit_every=refit_every,
            max_window=window,
        )
        losses = [s["qlike"] for s in out.scores]
        rows.append(
            {
                "prior_sessions": strength,
                "n": len(losses),
                "mean_qlike": float(np.mean(losses)) if losses else np.nan,
                "last_study_date": str(np.datetime_as_string(study.dates[-1], unit="D")),
            }
        )
    return pd.DataFrame(rows)


# --- short-history experiment -------------------------------------------------------------------
def short_history_experiment(
    data: ModelData,
    ns: Sequence[int] = SHORT_HISTORY_NS,
    model_names: Sequence[str] = SHORT_HISTORY_MODELS,
    *,
    refit_every: int = 21,
    prior_sessions: float | None = None,
) -> pd.DataFrame:
    """Fit on only the last n sessions, for each n, from the first point with 1,000 sessions."""
    start = max(ns)
    rows = []
    for n in ns:
        models = select(model_names, prior_sessions)
        out = walk_forward(data, models, start_index=start, refit_every=refit_every, max_window=n)
        summary = summarize_by_period(out)
        if summary.empty:
            continue
        summary.insert(0, "window", n)
        rows.append(summary)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def backtest_models(prior_sessions: float | None = None) -> list[Model]:
    return select(BACKTEST_MODELS, prior_sessions)
