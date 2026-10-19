"""VIX models for SPY (§3, §11).

`vix`: h = (VIXₜ/100)² · (30/365) / Nₜ, prepared in `ModelData.vix_var`.
`vix_mz`: VIX recalibrated by a rolling regression of past proxies on past VIX variance,
v̂ₛ₊₁ = a + b·h_VIX,ₛ, which removes the usual gap between implied and realized volatility.
"""

from __future__ import annotations

import numpy as np

from volforecast.config import PROXY_FLOOR
from volforecast.models.base import (
    FitError,
    Model,
    ModelData,
    Params,
    Prediction,
    check_variance,
)


def vix_to_variance(vix: float, sessions_next_30d: int) -> float:
    return (vix / 100.0) ** 2 * (30.0 / 365.0) / sessions_next_30d


class Vix(Model):
    name = "vix"
    min_history = 22
    needs_vix = True

    def predict(self, data: ModelData, params: Params) -> list[Prediction]:
        assert data.vix_var is not None
        value = float(data.vix_var[-1])
        if not np.isfinite(value):
            raise FitError("vix: no VIX value on or before the forecast date")
        notes = f"vix_date={data.meta['vix_date']}" if data.meta.get("vix_date") else ""
        return [Prediction(self.name, check_variance(value, self.name), "normal", notes=notes)]


class VixMZ(Model):
    name = "vix_mz"
    min_history = 100
    needs_vix = True

    def fit(self, data: ModelData) -> Params:
        assert data.vix_var is not None
        x, y = data.vix_var[:-1], data.proxy[1:]
        ok = np.isfinite(x) & np.isfinite(y)
        if ok.sum() < 60:
            raise FitError("vix_mz: fewer than 60 usable pairs")
        design = np.column_stack([np.ones(ok.sum()), x[ok]])
        (a, b), *_ = np.linalg.lstsq(design, y[ok], rcond=None)
        if not (np.isfinite(a) and np.isfinite(b)) or b <= 0:
            raise FitError("vix_mz: slope is not positive")
        return {"a": float(a), "b": float(b)}

    def predict(self, data: ModelData, params: Params) -> list[Prediction]:
        assert data.vix_var is not None
        h_vix = float(data.vix_var[-1])
        if not np.isfinite(h_vix):
            raise FitError("vix_mz: no VIX value on or before the forecast date")
        value = params["a"] + params["b"] * h_vix
        notes = []
        if value <= PROXY_FLOOR:
            value = PROXY_FLOOR
            notes.append("floored")
        if data.meta.get("vix_date"):
            notes.append(f"vix_date={data.meta['vix_date']}")
        return [
            Prediction(self.name, check_variance(value, self.name), "normal", notes=";".join(notes))
        ]
