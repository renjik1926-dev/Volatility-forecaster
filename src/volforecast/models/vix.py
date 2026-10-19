"""VIX models for SPY (§3, §11).

`vix`: h = (VIXₜ/100)² · (30/365) / Nₜ, prepared in `ModelData.vix_var`.
`vix_mz` (version 2): the VIX rescaled to remove the usual gap between implied and realized
variance, h = c · h_VIX with c = mean(v̂ₛ₊₁ / h_VIX,ₛ) over the rolling window. That c is the
scale that minimizes QLIKE, so the forecast is always positive. Version 1 used a linear
regression a + b·h_VIX, which could predict variances at or below zero on squared-return
proxies (S&P 500 study period: mean QLIKE 609); see METHODOLOGY.md, "Revisions".
"""

from __future__ import annotations

import numpy as np

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
    version = 2
    min_history = 100
    needs_vix = True

    def fit(self, data: ModelData) -> Params:
        assert data.vix_var is not None
        x, y = data.vix_var[:-1], data.proxy[1:]
        ok = np.isfinite(x) & np.isfinite(y) & (x > 0)
        if ok.sum() < 60:
            raise FitError("vix_mz: fewer than 60 usable pairs")
        scale = float(np.mean(y[ok] / x[ok]))
        if not (np.isfinite(scale) and scale > 0):
            raise FitError("vix_mz: scale is not positive")
        return {"scale": scale}

    def predict(self, data: ModelData, params: Params) -> list[Prediction]:
        assert data.vix_var is not None
        h_vix = float(data.vix_var[-1])
        if not np.isfinite(h_vix):
            raise FitError("vix_mz: no VIX value on or before the forecast date")
        value = params["scale"] * h_vix
        notes = f"vix_date={data.meta['vix_date']}" if data.meta.get("vix_date") else ""
        return [Prediction(self.name, check_variance(value, self.name), "normal", notes=notes)]
