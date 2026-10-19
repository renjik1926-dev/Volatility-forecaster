"""Returns and range-based variance estimators (§3, §10.4).

The formulas are plain NumPy on arrays, with input checks, so they are easy to test against
hand-computed values. `daily_frame` is the only pandas function: it lines bars up with
dividends and splits and applies the formulas.

Everything this module returns contains returns or prices. It stays in memory or in the private
data repo and is never published (D7, D13).
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt
import pandas as pd

Array = npt.NDArray[np.float64]
FloatLike = float | Array

FOUR_LN2 = 4.0 * math.log(2.0)
GK_C = 2.0 * math.log(2.0) - 1.0


def _arr(x: FloatLike) -> Array:
    return np.asarray(x, dtype=np.float64)


def _check_positive(**arrays: FloatLike) -> None:
    for name, values in arrays.items():
        a = _arr(values)
        if np.any(~np.isfinite(a)) or np.any(a <= 0):
            raise ValueError(f"{name} must be finite and positive")


def _check_range(open_: FloatLike, high: FloatLike, low: FloatLike, close: FloatLike) -> None:
    o, h, lo, c = _arr(open_), _arr(high), _arr(low), _arr(close)
    tol = 1e-12
    if np.any(h < np.maximum(o, c) * (1 - tol)) or np.any(lo > np.minimum(o, c) * (1 + tol)):
        raise ValueError("high must be >= max(open, close) and low <= min(open, close)")


def close_to_close_return(
    close: FloatLike, prev_close: FloatLike, dividend: FloatLike = 0.0, split: FloatLike = 1.0
) -> FloatLike:
    """rₜ = ln((Cₜ + Dₜ) / (Cₜ₋₁ / sₜ))."""
    _check_positive(close=close, prev_close=prev_close, split=split)
    out = np.log((_arr(close) + _arr(dividend)) / (_arr(prev_close) / _arr(split)))
    return float(out) if np.ndim(out) == 0 else out


def overnight_return(
    open_: FloatLike, prev_close: FloatLike, dividend: FloatLike = 0.0, split: FloatLike = 1.0
) -> FloatLike:
    """oₜ = ln((Oₜ + Dₜ) / (Cₜ₋₁ / sₜ))."""
    _check_positive(open_=open_, prev_close=prev_close, split=split)
    out = np.log((_arr(open_) + _arr(dividend)) / (_arr(prev_close) / _arr(split)))
    return float(out) if np.ndim(out) == 0 else out


def parkinson(high: FloatLike, low: FloatLike) -> FloatLike:
    """σ²ₚ = (ln(H/L))² / (4 ln 2)."""
    _check_positive(high=high, low=low)
    if np.any(_arr(high) < _arr(low)):
        raise ValueError("high must be >= low")
    out = np.log(_arr(high) / _arr(low)) ** 2 / FOUR_LN2
    return float(out) if np.ndim(out) == 0 else out


def garman_klass(open_: FloatLike, high: FloatLike, low: FloatLike, close: FloatLike) -> FloatLike:
    """σ²_GK = ½ (ln(H/L))² − (2 ln 2 − 1)(ln(C/O))². Non-negative because |ln(C/O)| ≤ ln(H/L)."""
    _check_positive(open_=open_, high=high, low=low, close=close)
    _check_range(open_, high, low, close)
    hl = np.log(_arr(high) / _arr(low))
    co = np.log(_arr(close) / _arr(open_))
    out = 0.5 * hl**2 - GK_C * co**2
    return float(out) if np.ndim(out) == 0 else out


def rogers_satchell(
    open_: FloatLike, high: FloatLike, low: FloatLike, close: FloatLike
) -> FloatLike:
    """σ²_RS = ln(H/C)·ln(H/O) + ln(L/C)·ln(L/O). Non-negative and unaffected by drift."""
    _check_positive(open_=open_, high=high, low=low, close=close)
    _check_range(open_, high, low, close)
    o, h, lo, c = _arr(open_), _arr(high), _arr(low), _arr(close)
    out = np.log(h / c) * np.log(h / o) + np.log(lo / c) * np.log(lo / o)
    out = np.maximum(out, 0.0)  # clears -0.0 and rounding noise at exact zeros
    return float(out) if np.ndim(out) == 0 else out


def yang_zhang(overnight: Array, open_close: Array, rs: Array) -> float:
    """Yang–Zhang (2000) variance over a window of n ≥ 2 days.

    σ²_YZ = σ²_o + k σ²_c + (1 − k) σ̄²_RS, k = 0.34 / (1.34 + (n + 1)/(n − 1)), where σ²_o and
    σ²_c are sample variances of overnight and open-to-close log returns.
    """
    o, c, r = _arr(overnight), _arr(open_close), _arr(rs)
    n = o.size
    if n < 2 or c.size != n or r.size != n:
        raise ValueError("yang_zhang needs equal-length windows of at least 2 days")
    if not (np.all(np.isfinite(o)) and np.all(np.isfinite(c)) and np.all(np.isfinite(r))):
        raise ValueError("yang_zhang inputs must be finite")
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    return float(np.var(o, ddof=1) + k * np.var(c, ddof=1) + (1 - k) * np.mean(r))


DAILY_COLUMNS = [
    "date",
    "r",  # close-to-close log return, dividend- and split-adjusted
    "overnight",  # overnight log return, dividend- and split-adjusted
    "open_close",  # ln(C/O)
    "park",
    "gk",
    "rs",
    "proxy_rs",  # primary proxy: overnight² + RS
    "proxy_r2",  # r²
    "proxy_gk",  # overnight² + GK
    "early_close",
]


def daily_frame(
    bars: pd.DataFrame,
    dividends: pd.DataFrame | None = None,
    splits: pd.DataFrame | None = None,
    early_closes: set[pd.Timestamp] | None = None,
) -> pd.DataFrame:
    """Per-session returns and proxies. The first bar has no previous close and is dropped."""
    bars = bars.sort_values("date").reset_index(drop=True)
    dates = pd.DatetimeIndex(pd.to_datetime(bars["date"])).astype("datetime64[ns]")
    d = np.zeros(len(bars))
    if dividends is not None and len(dividends):
        amounts = dividends.groupby(pd.to_datetime(dividends["date"]))["amount"].sum()
        amounts.index = pd.DatetimeIndex(amounts.index).astype("datetime64[ns]")
        d = amounts.reindex(dates).fillna(0.0).to_numpy(dtype=np.float64)
    s = np.ones(len(bars))
    if splits is not None and len(splits):
        factors = splits.groupby(pd.to_datetime(splits["date"]))["factor"].prod()
        factors.index = pd.DatetimeIndex(factors.index).astype("datetime64[ns]")
        s = factors.reindex(dates).fillna(1.0).to_numpy(dtype=np.float64)

    o = bars["open"].to_numpy(dtype=np.float64)
    h = bars["high"].to_numpy(dtype=np.float64)
    lo = bars["low"].to_numpy(dtype=np.float64)
    c = bars["close"].to_numpy(dtype=np.float64)
    if len(bars) < 2:
        return pd.DataFrame(columns=DAILY_COLUMNS)

    prev = c[:-1]
    r = _arr(close_to_close_return(c[1:], prev, d[1:], s[1:]))
    on = _arr(overnight_return(o[1:], prev, d[1:], s[1:]))
    oc = np.log(c[1:] / o[1:])
    park = _arr(parkinson(h[1:], lo[1:]))
    gk = _arr(garman_klass(o[1:], h[1:], lo[1:], c[1:]))
    rs = _arr(rogers_satchell(o[1:], h[1:], lo[1:], c[1:]))
    out_dates = pd.Series(dates[1:])
    early = early_closes or set()
    return pd.DataFrame(
        {
            "date": out_dates,
            "r": r,
            "overnight": on,
            "open_close": oc,
            "park": park,
            "gk": gk,
            "rs": rs,
            "proxy_rs": on**2 + rs,
            "proxy_r2": r**2,
            "proxy_gk": on**2 + gk,
            "early_close": [pd.Timestamp(x) in early for x in out_dates],
        }
    )


def close_only_frame(closes: pd.Series) -> pd.DataFrame:
    """Returns and the squared-return proxy from closing prices only (S&P 500 index, §13)."""
    closes = closes.dropna().sort_index()
    values = closes.to_numpy(dtype=np.float64)
    if values.size < 2:
        return pd.DataFrame(columns=DAILY_COLUMNS)
    r = _arr(close_to_close_return(values[1:], values[:-1]))
    nan = np.full(r.size, np.nan)
    return pd.DataFrame(
        {
            "date": pd.DatetimeIndex(closes.index[1:]).astype("datetime64[ns]"),
            "r": r,
            "overnight": nan,
            "open_close": nan,
            "park": nan,
            "gk": nan,
            "rs": nan,
            "proxy_rs": nan,
            "proxy_r2": r**2,
            "proxy_gk": nan,
            "early_close": False,
        }
    )
