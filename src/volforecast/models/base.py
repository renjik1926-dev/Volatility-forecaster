"""Shared model interface (§11).

Every model turns data up to and including day t into a variance forecast for t+1. Fitting and
predicting are separate so the backtest can re-estimate every 21 sessions and update the
variance recursion daily with fixed parameters, while the live job refits every evening.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
import numpy.typing as npt

Array = npt.NDArray[np.float64]
Params = dict[str, float]


class FitError(RuntimeError):
    """The fit did not converge or produced invalid parameters (§11 fit failures)."""


@dataclass(frozen=True)
class ModelData:
    """History up to day t. All arrays are aligned with `dates` and contain only past data.

    `proxy` is the primary variance proxy: overnight² + RS for ETFs, r² for the close-only
    S&P 500 index. Range arrays are None when only closes are available. `vix_var[i]` is the
    VIX-implied variance for the session after `dates[i]`, using the latest VIX on or before
    `dates[i]` (NaN if none).
    """

    dates: npt.NDArray[np.datetime64]
    r: Array
    proxy: Array
    overnight: Array | None = None
    open_close: Array | None = None
    rs: Array | None = None
    vix_var: Array | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        n = self.dates.shape[0]
        for name in ("r", "proxy", "overnight", "open_close", "rs", "vix_var"):
            values = getattr(self, name)
            if values is not None and values.shape[0] != n:
                raise ValueError(f"{name} has {values.shape[0]} rows, dates has {n}")

    @property
    def n(self) -> int:
        return int(self.dates.shape[0])

    @property
    def has_range(self) -> bool:
        return self.rs is not None and self.overnight is not None and self.open_close is not None

    @property
    def has_vix(self) -> bool:
        return self.vix_var is not None and bool(np.isfinite(self.vix_var).any())

    def _slice(self, sl: slice) -> ModelData:
        def cut(a: Array | None) -> Array | None:
            return None if a is None else a[sl]

        return replace(
            self,
            dates=self.dates[sl],
            r=self.r[sl],
            proxy=self.proxy[sl],
            overnight=cut(self.overnight),
            open_close=cut(self.open_close),
            rs=cut(self.rs),
            vix_var=cut(self.vix_var),
        )

    def head(self, n: int) -> ModelData:
        """The first n sessions (history up to the n-th session).

        `meta` describes the last row (e.g. which VIX date it used), so it is dropped when the
        last row changes.
        """
        out = self._slice(slice(0, n))
        return out if out.n == self.n else replace(out, meta={})

    def tail(self, n: int) -> ModelData:
        return self._slice(slice(max(self.n - n, 0), self.n))


@dataclass(frozen=True)
class Prediction:
    output: str  # name written to forecasts.csv, e.g. "garch_t" or "garch_t_fhs"
    variance: float
    dist: str  # normal | t | fhs
    nu: float | None = None
    z: Array | None = None  # standardized residuals, for filtered historical simulation
    notes: str = ""


class Model(ABC):
    name: str
    version: int = 1
    min_history: int = 22
    statistical: bool = False  # member of the ensemble (§11)
    needs_range: bool = False
    needs_vix: bool = False

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"

    @property
    def outputs(self) -> tuple[str, ...]:
        return (self.name,)

    def applies_to(self, data: ModelData) -> bool:
        if self.needs_range and not data.has_range:
            return False
        return not (self.needs_vix and data.vix_var is None)

    def fit(self, data: ModelData) -> Params:
        """Estimate parameters on the window. Closed-form models have none."""
        return {}

    @abstractmethod
    def predict(self, data: ModelData, params: Params) -> list[Prediction]:
        """Forecast the variance of r(t+1) with fixed parameters."""


def check_variance(value: float, model: str) -> float:
    if not np.isfinite(value) or value <= 0:
        raise FitError(f"{model}: forecast variance {value!r} is not positive and finite")
    return float(value)
