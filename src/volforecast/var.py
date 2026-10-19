"""1-day Value-at-Risk from a variance forecast (§3, §10.5).

Zero-mean convention for every model (D11): VaR(α) = −√h · q₁₋α, where q is the quantile of the
model's standardized return distribution. VaR is reported as a positive loss.
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt
from scipy import stats

LEVELS = (0.95, 0.99)
FHS_WINDOW = 1000


def normal_quantile(alpha: float) -> float:
    return float(stats.norm.ppf(1.0 - alpha))


def t_unit_quantile(nu: float, alpha: float) -> float:
    """(1−α) quantile of a Student-t rescaled to unit variance: t_ν⁻¹(1−α)·√((ν−2)/ν)."""
    if not nu > 2:
        raise ValueError("Student-t needs nu > 2 for a finite variance")
    return float(stats.t.ppf(1.0 - alpha, nu) * math.sqrt((nu - 2.0) / nu))


def fhs_quantile(z: npt.ArrayLike, alpha: float, window: int = FHS_WINDOW) -> float:
    """Empirical (1−α) quantile of the last `window` standardized residuals (§11)."""
    values = np.asarray(z, dtype=np.float64)
    values = values[np.isfinite(values)][-window:]
    if values.size < 50:
        raise ValueError("filtered historical simulation needs at least 50 residuals")
    return float(np.quantile(values, 1.0 - alpha))


def value_at_risk(
    variance: float,
    alpha: float,
    dist: str,
    *,
    nu: float | None = None,
    z: npt.ArrayLike | None = None,
) -> float:
    if not variance > 0 or not math.isfinite(variance):
        raise ValueError("variance must be positive and finite")
    if dist == "normal":
        q = normal_quantile(alpha)
    elif dist == "t":
        if nu is None:
            raise ValueError("dist 't' needs nu")
        q = t_unit_quantile(nu, alpha)
    elif dist == "fhs":
        if z is None:
            raise ValueError("dist 'fhs' needs standardized residuals z")
        q = fhs_quantile(z, alpha)
    else:
        raise ValueError(f"unknown distribution {dist!r}")
    return -math.sqrt(variance) * q


def var_pair(
    variance: float, dist: str, *, nu: float | None = None, z: npt.ArrayLike | None = None
) -> tuple[float, float]:
    """(VaR95, VaR99)."""
    v95, v99 = (value_at_risk(variance, a, dist, nu=nu, z=z) for a in LEVELS)
    return v95, v99
