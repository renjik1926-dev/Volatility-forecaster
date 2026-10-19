"""Maximum-likelihood GARCH family via `arch` (§11): garch_n, garch_t, gjr_t, egarch_t.

All are zero-mean. Returns are scaled by 100 before fitting (arch works best on percent
returns) and the variance is scaled back. `garch_t` also produces a filtered historical
simulation VaR variant, `garch_t_fhs`.
"""

from __future__ import annotations

import warnings
from typing import Any, Literal

import numpy as np
from arch import arch_model

from volforecast.models.base import (
    FitError,
    Model,
    ModelData,
    Params,
    Prediction,
    check_variance,
)

SCALE = 100.0


class ArchGarch(Model):
    statistical = True
    min_history = 500

    def __init__(
        self, name: str, *, vol: Literal["GARCH", "EGARCH"], o: int, dist: str, fhs: bool = False
    ) -> None:
        self.name = name
        self.vol = vol
        self.o = o
        self.dist = dist  # "normal" or "t"
        self.fhs = fhs

    @property
    def outputs(self) -> tuple[str, ...]:
        return (self.name, f"{self.name}_fhs") if self.fhs else (self.name,)

    def _model(self, data: ModelData) -> Any:
        return arch_model(
            data.r * SCALE,
            mean="Zero",
            vol=self.vol,
            p=1,
            o=self.o,
            q=1,
            dist="t" if self.dist == "t" else "normal",
            rescale=False,
        )

    def fit(self, data: ModelData) -> Params:
        model = self._model(data)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                res = model.fit(disp="off", show_warning=False)
            except (ValueError, np.linalg.LinAlgError, RuntimeError) as exc:
                raise FitError(f"{self.name}: fit raised {type(exc).__name__}") from exc
        if res.convergence_flag != 0:
            raise FitError(f"{self.name}: optimizer did not converge")
        params = {str(k): float(v) for k, v in res.params.items()}
        self._check(params)
        return params

    def _check(self, p: Params) -> None:
        if not all(np.isfinite(v) for v in p.values()):
            raise FitError(f"{self.name}: non-finite parameters")
        if self.dist == "t" and not p.get("nu", 0.0) > 2.0:
            raise FitError(f"{self.name}: nu <= 2")
        if self.vol == "EGARCH":
            if not abs(p["beta[1]"]) < 1:
                raise FitError(f"{self.name}: |beta| >= 1")
            return
        alpha, beta = p["alpha[1]"], p["beta[1]"]
        gamma = p.get("gamma[1]", 0.0)
        if p["omega"] <= 0 or alpha < 0 or beta < 0 or alpha + gamma < 0:
            raise FitError(f"{self.name}: negative parameters")
        if alpha + gamma / 2 + beta >= 1:
            raise FitError(f"{self.name}: persistence >= 1")

    def predict(self, data: ModelData, params: Params) -> list[Prediction]:
        model = self._model(data)
        # Zero mean has no parameters; arch orders volatility then distribution parameters.
        names = list(model.volatility.parameter_names()) + list(
            model.distribution.parameter_names()
        )
        try:
            values = [params[k] for k in names]
        except KeyError as exc:
            raise FitError(f"{self.name}: stored parameters don't match the model") from exc
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fixed = model.fix(values)
            forecast = fixed.forecast(horizon=1, reindex=False)
        variance = check_variance(float(forecast.variance.to_numpy()[-1, 0]) / SCALE**2, self.name)
        nu = params.get("nu") if self.dist == "t" else None
        out = [Prediction(self.name, variance, "t" if self.dist == "t" else "normal", nu=nu)]
        if self.fhs:
            sigma = np.asarray(fixed.conditional_volatility, dtype=np.float64)
            z = (data.r * SCALE) / sigma
            out.append(Prediction(f"{self.name}_fhs", variance, "fhs", z=z[np.isfinite(z)]))
        return out


def garch_n() -> ArchGarch:
    return ArchGarch("garch_n", vol="GARCH", o=0, dist="normal")


def garch_t() -> ArchGarch:
    return ArchGarch("garch_t", vol="GARCH", o=0, dist="t", fhs=True)


def gjr_t() -> ArchGarch:
    return ArchGarch("gjr_t", vol="GARCH", o=1, dist="t")


def egarch_t() -> ArchGarch:
    # Nelson's EGARCH(1,1) with the asymmetry term (o=1), the usual specification.
    return ArchGarch("egarch_t", vol="EGARCH", o=1, dist="t")
