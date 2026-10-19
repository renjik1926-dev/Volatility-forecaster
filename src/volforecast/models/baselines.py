"""Baselines (§11): a model is only interesting if it beats these (D4)."""

from __future__ import annotations

import numpy as np
from scipy.signal import lfilter

from volforecast.estimators import yang_zhang
from volforecast.models.base import Model, ModelData, Params, Prediction, check_variance


class RandomWalk(Model):
    """hₜ₊₁ = v̂ₜ."""

    name = "rw"
    min_history = 22

    def predict(self, data: ModelData, params: Params) -> list[Prediction]:
        return [
            Prediction(self.name, check_variance(max(data.proxy[-1], 1e-10), self.name), "normal")
        ]


class Hist21(Model):
    """Mean of the last 21 daily proxies."""

    name = "hist21"
    min_history = 22

    def predict(self, data: ModelData, params: Params) -> list[Prediction]:
        value = float(np.mean(data.proxy[-21:]))
        return [Prediction(self.name, check_variance(value, self.name), "normal")]


class YangZhang21(Model):
    """Yang–Zhang variance over the last 21 sessions (needs open/high/low)."""

    name = "yz21"
    min_history = 22
    needs_range = True

    def predict(self, data: ModelData, params: Params) -> list[Prediction]:
        assert data.overnight is not None and data.open_close is not None and data.rs is not None
        value = yang_zhang(data.overnight[-21:], data.open_close[-21:], data.rs[-21:])
        return [Prediction(self.name, check_variance(value, self.name), "normal")]


def ewma_variance(r: np.ndarray, lam: float, init_window: int = 22) -> float:
    """RiskMetrics recursion hₜ₊₁ = λhₜ + (1−λ)rₜ², started from the mean r² of the first days.

    Returns the forecast for the session after the last return.
    """
    r2 = np.asarray(r, dtype=np.float64) ** 2
    h0 = float(np.mean(r2[: min(init_window, r2.size)]))
    # y[n] = λ y[n−1] + (1−λ) x[n] with y[−1] = h0, so y[n] is the forecast made after day n.
    out, _ = lfilter([1.0 - lam], [1.0, -lam], r2, zi=[lam * h0])
    return float(out[-1])


class Ewma094(Model):
    name = "ewma_094"
    min_history = 22
    lam = 0.94

    def predict(self, data: ModelData, params: Params) -> list[Prediction]:
        value = ewma_variance(data.r, self.lam)
        return [Prediction(self.name, check_variance(value, self.name), "normal")]
