"""Synthetic market data for tests, fixtures and demos. Never real prices.

Bars are built from a GARCH(1,1) daily variance with Student-t shocks: an overnight jump, then a
finely sampled intraday random walk aggregated to open/high/low/close, so the range estimators
have something realistic to measure.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from volforecast.providers.base import BAR_COLUMNS


@dataclass(frozen=True)
class SimSpec:
    omega: float = 2e-6
    alpha: float = 0.08
    beta: float = 0.90
    nu: float = 7.0
    overnight_share: float = 0.2
    steps: int = 78  # 5-minute bars in a 6.5-hour session
    start_price: float = 100.0
    drift: float = 0.0  # per-session log drift


DEFAULT_SPEC = SimSpec()


def simulate(
    sessions: Sequence[dt.date], seed: int, spec: SimSpec = DEFAULT_SPEC
) -> tuple[pd.DataFrame, np.ndarray]:
    """Bars with BAR_COLUMNS and the true conditional variance of each session's return."""
    rng = np.random.default_rng(seed)
    n = len(sessions)
    h = spec.omega / (1 - spec.alpha - spec.beta)
    close = spec.start_price
    rows, true_h = [], np.empty(n)
    scale = np.sqrt((spec.nu - 2) / spec.nu)
    for i, day in enumerate(sessions):
        true_h[i] = h
        on = np.sqrt(spec.overnight_share * h) * rng.standard_t(spec.nu) * scale
        # A normal variance mixture: the day's intraday return is Student-t with the right variance
        # (E[w] = 1), while the path within the day stays a Brownian-like random walk.
        w = (spec.nu - 2) / rng.chisquare(spec.nu)
        sd = np.sqrt(w * (1 - spec.overnight_share) * h / spec.steps)
        path = np.cumsum(rng.standard_normal(spec.steps) * sd + spec.drift / spec.steps)
        open_ = close * np.exp(on)
        prices = open_ * np.exp(path)
        high = max(open_, prices.max())
        low = min(open_, prices.min())
        new_close = float(prices[-1])
        r = np.log(new_close / close)
        rows.append(
            (
                pd.Timestamp(day),
                open_,
                high,
                low,
                new_close,
                int(rng.integers(1_000_000, 5_000_000)),
            )
        )
        h = spec.omega + spec.alpha * r**2 + spec.beta * h
        close = new_close
    frame = pd.DataFrame(rows, columns=BAR_COLUMNS)
    frame["date"] = frame["date"].astype("datetime64[ns]")
    return frame, true_h


def simulate_vix(
    bars: pd.DataFrame, true_h: np.ndarray, seed: int, premium: float = 1.15
) -> pd.Series:
    """A VIX-like series: annualized expected volatility in percent, plus a premium and noise."""
    rng = np.random.default_rng(seed)
    level = 100 * np.sqrt(252 * true_h) * premium * np.exp(rng.normal(0, 0.05, true_h.size))
    index = pd.DatetimeIndex(bars["date"], name="date").astype("datetime64[ns]")
    return pd.Series(level, index=index, dtype="float64")


def simulate_garch_returns(
    n: int, seed: int, omega: float, alpha: float, beta: float, nu: float | None = None
) -> np.ndarray:
    """Plain GARCH(1,1) returns (normal, or unit-variance Student-t if `nu` is given)."""
    rng = np.random.default_rng(seed)
    z = rng.standard_normal(n) if nu is None else rng.standard_t(nu, n) * np.sqrt((nu - 2) / nu)
    h = omega / (1 - alpha - beta)
    out = np.empty(n)
    for t in range(n):
        out[t] = np.sqrt(h) * z[t]
        h = omega + alpha * out[t] ** 2 + beta * h
    return out
