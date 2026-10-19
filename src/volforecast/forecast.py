"""`forecast_next`: the one forecasting function shared by the live job and the backtest (D5).

Given history up to day t, every model whose minimum history is met forecasts the variance of
r(t+1), and VaR at 95% and 99% follows from the model's distribution. Fit failures reuse the
last good parameters (`fallback`); models short of history are reported as `warming_up` with
the sessions they still need.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from volforecast.calendar import TradingCalendar
from volforecast.models import FitError, Model, ModelData
from volforecast.models.ensemble import Ensemble
from volforecast.models.vix import vix_to_variance
from volforecast.var import var_pair

OK, FALLBACK, WARMING_UP, FAILED = "ok", "fallback", "warming_up", "failed"


@dataclass
class ModelState:
    """Last good parameters per model version, persisted in the private repo (`params/`)."""

    entries: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelState:
        return cls({k: dict(v) for k, v in data.items()})

    def to_dict(self) -> dict[str, Any]:
        return self.entries

    def params(self, key: str) -> dict[str, float] | None:
        entry = self.entries.get(key)
        return None if entry is None else entry.get("params")

    def consecutive_fallbacks(self, key: str) -> int:
        return int(self.entries.get(key, {}).get("fallbacks", 0))

    def record_fit(self, key: str, params: dict[str, float], through: str) -> None:
        self.entries[key] = {
            "params": params,
            "status": OK,
            "fallbacks": 0,
            "fitted_through": through,
        }

    def record_fallback(self, key: str) -> None:
        entry = self.entries.setdefault(key, {"params": None})
        entry["status"] = FALLBACK
        entry["fallbacks"] = int(entry.get("fallbacks", 0)) + 1

    def status(self, key: str) -> str:
        return str(self.entries.get(key, {}).get("status", OK))


@dataclass(frozen=True)
class ForecastResult:
    model: str  # output name, e.g. garch_t or garch_t_fhs
    model_version: int
    fit_status: str
    variance: float | None = None
    var95: float | None = None
    var99: float | None = None
    dist: str | None = None
    notes: str = ""

    @property
    def has_values(self) -> bool:
        return self.variance is not None


def _with_var(model: Model, pred: Any, status: str, extra_notes: str = "") -> ForecastResult:
    v95, v99 = var_pair(pred.variance, pred.dist, nu=pred.nu, z=pred.z)
    notes = ";".join(n for n in (pred.notes, extra_notes) if n)
    return ForecastResult(
        pred.output, model.version, status, pred.variance, v95, v99, pred.dist, notes
    )


def _day(value: np.datetime64) -> str:
    return str(np.datetime_as_string(value, unit="D"))


def forecast_next(
    data: ModelData,
    models: Sequence[Model],
    state: ModelState,
    *,
    refit: bool = True,
    max_window: int = 1000,
) -> list[ForecastResult]:
    """Forecasts for the session after `data.dates[-1]`, using only `data`."""
    if data.n == 0:
        return []
    window = data.tail(max_window)
    # Minimum histories (§11) count sessions of price history; n returns need n + 1 sessions,
    # so the free tier's first 100 sessions are enough for the 100-session models on day one.
    sessions = data.n + 1
    through = _day(data.dates[-1])
    results: list[ForecastResult] = []
    members: list[tuple[str, float]] = []
    ensemble: Ensemble | None = None

    for model in models:
        if isinstance(model, Ensemble):
            ensemble = model
            continue
        if not model.applies_to(data):
            continue
        if sessions < model.min_history:
            needed = model.min_history - sessions
            results += [
                ForecastResult(
                    out, model.version, WARMING_UP, notes=f"needs {needed} more sessions"
                )
                for out in model.outputs
            ]
            continue

        status = state.status(model.key)
        params = state.params(model.key)
        if refit or params is None:
            try:
                params = model.fit(window)
                state.record_fit(model.key, params, through)
                status = OK
            except FitError as exc:
                state.record_fallback(model.key)
                params = state.params(model.key)
                status = FALLBACK
                if params is None:
                    results += [
                        ForecastResult(out, model.version, FAILED, notes=str(exc)[:120])
                        for out in model.outputs
                    ]
                    continue
        try:
            predictions = model.predict(window, params)
        except (FitError, ValueError) as exc:
            results += [
                ForecastResult(out, model.version, FAILED, notes=str(exc)[:120])
                for out in model.outputs
            ]
            continue
        for pred in predictions:
            results.append(_with_var(model, pred, status))
        if model.statistical:
            members.append((model.name, predictions[0].variance))

    if ensemble is not None:
        if sessions < ensemble.min_history:
            needed = ensemble.min_history - sessions
            results.append(
                ForecastResult(
                    ensemble.name,
                    ensemble.version,
                    WARMING_UP,
                    notes=f"needs {needed} more sessions",
                )
            )
        else:
            try:
                results.append(_with_var(ensemble, ensemble.combine(members), OK))
            except FitError as exc:
                results.append(
                    ForecastResult(ensemble.name, ensemble.version, FAILED, notes=str(exc)[:120])
                )
    return results


# --- preparing ModelData from the daily frame ---------------------------------------------------
def vix_variance_series(
    dates: pd.DatetimeIndex, vix: pd.Series, calendar: TradingCalendar
) -> tuple[np.ndarray, str | None]:
    """VIX-implied next-session variance at each date, from the latest VIX on or before it."""
    clean = vix.dropna().sort_index()
    out = np.full(len(dates), np.nan)
    if clean.empty or len(dates) == 0:
        return out, None
    vix_days = clean.index.to_numpy(dtype="datetime64[ns]")
    pos = np.searchsorted(vix_days, dates.to_numpy(dtype="datetime64[ns]"), side="right") - 1
    first, last = dates[0].date(), dates[-1].date() + dt.timedelta(days=31)
    sessions = np.array(calendar.sessions(first, last), dtype="datetime64[D]")
    day_arr = dates.to_numpy(dtype="datetime64[D]")
    lo = np.searchsorted(sessions, day_arr, side="right")
    hi = np.searchsorted(sessions, day_arr + np.timedelta64(30, "D"), side="right")
    n_next = hi - lo
    values = clean.to_numpy(dtype=np.float64)
    for i, p in enumerate(pos):
        if p >= 0 and n_next[i] > 0:
            out[i] = vix_to_variance(values[p], int(n_next[i]))
    last_pos = pos[-1]
    vix_date = pd.Timestamp(vix_days[last_pos]).date().isoformat() if last_pos >= 0 else None
    return out, vix_date


def build_model_data(
    frame: pd.DataFrame,
    *,
    vix: pd.Series | None = None,
    calendar: TradingCalendar | None = None,
) -> ModelData:
    """ModelData from a `daily_frame`/`close_only_frame`. Range arrays only if all present."""
    frame = frame.sort_values("date").reset_index(drop=True)
    dates = pd.DatetimeIndex(pd.to_datetime(frame["date"]))
    has_range = bool(len(frame)) and bool(frame["rs"].notna().all())
    proxy = frame["proxy_rs"] if has_range else frame["proxy_r2"]
    vix_var, vix_date = None, None
    if vix is not None:
        if calendar is None:
            raise ValueError("VIX conversion needs the trading calendar")
        vix_var, vix_date = vix_variance_series(dates, vix, calendar)

    def arr(col: str) -> np.ndarray:
        return frame[col].to_numpy(dtype=np.float64)

    return ModelData(
        dates=dates.to_numpy(dtype="datetime64[D]"),
        r=arr("r"),
        proxy=proxy.to_numpy(dtype=np.float64),
        overnight=arr("overnight") if has_range else None,
        open_close=arr("open_close") if has_range else None,
        rs=arr("rs") if has_range else None,
        vix_var=vix_var,
        meta={"vix_date": vix_date} if vix_date else {},
    )
