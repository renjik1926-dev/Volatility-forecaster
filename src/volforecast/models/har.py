"""`har_log`: HAR on the log proxy, fitted by OLS on the rolling window (§11).

ln v̂ₜ₊₁ = β₀ + β_d ln v̂ₜ + β_w avg₅(ln v̂) + β_m avg₂₂(ln v̂) + ε, converted back with the
log-normal correction h = exp(ŷ + s²/2). Proxies are floored at 1e-10 before taking logs.
"""

from __future__ import annotations

import numpy as np

from volforecast.config import PROXY_FLOOR
from volforecast.models.base import (
    Array,
    FitError,
    Model,
    ModelData,
    Params,
    Prediction,
    check_variance,
)


def har_features(log_proxy: Array) -> Array:
    """Rows [1, daily, weekly, monthly] for every day with 22 days of history (index ≥ 21)."""
    y = np.asarray(log_proxy, dtype=np.float64)
    if y.size < 22:
        return np.empty((0, 4))
    csum = np.concatenate(([0.0], np.cumsum(y)))
    idx = np.arange(21, y.size)
    weekly = (csum[idx + 1] - csum[idx - 4]) / 5.0
    monthly = (csum[idx + 1] - csum[idx - 21]) / 22.0
    return np.column_stack([np.ones(idx.size), y[idx], weekly, monthly])


class HarLog(Model):
    name = "har_log"
    min_history = 150
    statistical = True

    def _log_proxy(self, data: ModelData) -> tuple[Array, int]:
        floored = int(np.sum(data.proxy < PROXY_FLOOR))
        return np.log(np.maximum(data.proxy, PROXY_FLOOR)), floored

    def fit(self, data: ModelData) -> Params:
        y, _ = self._log_proxy(data)
        x_all = har_features(y)
        x, target = x_all[:-1], y[22:]  # features at t predict y at t+1
        if target.size < 30:
            raise FitError("har_log: not enough observations")
        beta, *_ = np.linalg.lstsq(x, target, rcond=None)
        resid = target - x @ beta
        dof = target.size - x.shape[1]
        s2 = float(resid @ resid / dof)
        if not (np.all(np.isfinite(beta)) and np.isfinite(s2)):
            raise FitError("har_log: non-finite estimates")
        return {"b0": beta[0], "bd": beta[1], "bw": beta[2], "bm": beta[3], "s2": s2}

    def predict(self, data: ModelData, params: Params) -> list[Prediction]:
        y, floored = self._log_proxy(data)
        x_last = har_features(y)[-1]
        beta = np.array([params["b0"], params["bd"], params["bw"], params["bm"]])
        value = float(np.exp(x_last @ beta + params["s2"] / 2.0))
        notes = f"floored_proxies={floored}" if floored else ""
        return [Prediction(self.name, check_variance(value, self.name), "normal", notes=notes)]
