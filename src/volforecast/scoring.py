"""Losses and VaR violations for one forecast against what happened (§10.6).

`score` is the one scoring function used by both the live job and the backtest (D5).
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from typing import Any

from volforecast.config import PROXY_FLOOR


@dataclass(frozen=True)
class Realized:
    """What happened on the target session. `r` never leaves memory or the private repo."""

    r: float
    proxy_rs: float | None  # None for close-only data (the S&P 500 index backtest)
    proxy_r2: float
    proxy_gk: float | None
    early_close: bool = False

    @property
    def primary_proxy(self) -> float:
        """Range-based proxy when available, else squared returns (§13)."""
        return self.proxy_rs if self.proxy_rs is not None else self.proxy_r2


def qlike(proxy: float, forecast: float, floor: float = PROXY_FLOOR) -> float:
    """L = v̂/h − ln(v̂/h) − 1; zero at a perfect forecast. Zero proxies are floored."""
    if not forecast > 0:
        raise ValueError("forecast variance must be positive")
    ratio = max(proxy, floor) / forecast
    return ratio - math.log(ratio) - 1.0


def mse(proxy: float, forecast: float) -> float:
    return (proxy - forecast) ** 2


def violation(r: float, var: float) -> int:
    """1 if the loss exceeded VaR, i.e. r < −VaR."""
    return int(r < -var)


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    out = float(value)
    return None if math.isnan(out) else out


def score(forecast: dict[str, Any], realized: Realized, scored_utc: dt.datetime) -> dict[str, Any]:
    """One scores.csv row (§9). Forecasts without values (warming up, failed) aren't scored."""
    variance = _num(forecast.get("variance"))
    if variance is None:
        raise ValueError(f"forecast {forecast.get('forecast_id')} has no variance to score")
    proxy = realized.primary_proxy
    var95, var99 = _num(forecast.get("var95")), _num(forecast.get("var99"))
    return {
        "forecast_id": forecast["forecast_id"],
        "proxy_rs": realized.proxy_rs,
        "proxy_r2": realized.proxy_r2,
        "proxy_gk": realized.proxy_gk,
        "qlike": qlike(proxy, variance),
        "mse": mse(proxy, variance),
        "viol95": None if var95 is None else violation(realized.r, var95),
        "viol99": None if var99 is None else violation(realized.r, var99),
        "early_close": realized.early_close,
        "scored_utc": scored_utc.isoformat(timespec="seconds"),
    }
