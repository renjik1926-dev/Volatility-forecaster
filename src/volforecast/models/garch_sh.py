"""`garch_t_sh`: GARCH(1,1) with Student-t errors for short histories (§11, D14).

Penalized likelihood: maximize log-likelihood + log-prior, where the prior pulls parameters
toward values typical for daily ETF returns. With little data the prior dominates; as history
grows the data take over and the estimates approach plain maximum likelihood.

- Variance targeting: ω = σ̄²(1 − α − β), σ̄² = mean of squared (zero-mean) returns in the window.
- Reparameterization: p = α + β ∈ (0, 1), a = α/p ∈ (0, 1), ν > 2. Normal priors on
  θ = (logit p, logit a, ln(ν − 2)).
- Prior centers (fixed before any backtest, sources in METHODOLOGY.md): p₀ = 0.97, α₀ = 0.08,
  β₀ = 0.89, ν₀ = 7.
- Prior strength: one number, `prior_sessions` (n₀). The prior precision on each θᵢ is
  n₀ · Iᵢ, where Iᵢ is the per-observation Fisher information at the prior centers
  (`UNIT_INFORMATION`, from `scripts/unit_information.py`). At n = n₀ sessions the prior and
  the data carry roughly equal information. Tuned on the S&P 500 study period only, then frozen.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize
from scipy.signal import lfilter
from scipy.special import expit, gammaln, logit

from volforecast.models.base import (
    Array,
    FitError,
    Model,
    ModelData,
    Params,
    Prediction,
    check_variance,
)

SCALE = 100.0
P0, ALPHA0, NU0 = 0.97, 0.08, 7.0
# Per-observation Fisher information for (logit p, logit a, ln(ν−2)) at the prior centers,
# estimated by simulation (scripts/unit_information.py, seed 20261018, 400k sessions).
UNIT_INFORMATION = np.array([0.0074, 0.0213, 0.0157])
DEFAULT_PRIOR_SESSIONS = 250.0  # placeholder until tuned on the study period (§13)
BOUNDS = [(-8.0, 12.0), (-10.0, 6.0), (-4.0, 6.0)]


def prior_center() -> Array:
    return np.array([logit(P0), logit(ALPHA0 / P0), math.log(NU0 - 2.0)])


def to_natural(theta: Array) -> tuple[float, float, float]:
    """θ → (α, β, ν). Always gives α, β ≥ 0, α + β < 1 and ν > 2."""
    p = float(expit(theta[0]))
    a = float(expit(theta[1]))
    nu = 2.0 + math.exp(float(theta[2]))
    return a * p, (1.0 - a) * p, nu


def from_natural(alpha: float, beta: float, nu: float) -> Array:
    p = alpha + beta
    return np.array([logit(p), logit(alpha / p), math.log(nu - 2.0)])


def backcast(x: Array) -> float:
    """Same starting value as `arch`: EWMA (0.94) of the first 75 squared returns."""
    tau = min(75, x.size)
    w = 0.94 ** np.arange(tau)
    w = w / w.sum()
    return float(np.sum(x[:tau] ** 2 * w))


def variance_path(x: Array, omega: float, alpha: float, beta: float, start: float) -> Array:
    """h₀ … h_n for x₀ … x_{n−1}; the last element is the forecast for the next session.

    hₜ = ω + α xₜ₋₁² + β hₜ₋₁, with x₋₁² = h₋₁ = `start` (the backcast).
    """
    lagged = np.concatenate(([start], x**2))  # x²ₜ₋₁ for t = 0 … n
    u = omega + alpha * lagged
    h, _ = lfilter([1.0], [1.0, -beta], u, zi=[beta * start])
    return np.asarray(h, dtype=np.float64)


def neg_loglik(theta: Array, x: Array, target: float, start: float) -> float:
    alpha, beta, nu = to_natural(theta)
    omega = target * (1.0 - alpha - beta)
    h = variance_path(x, omega, alpha, beta, start)[:-1]
    if not np.all(h > 0):
        return 1e12
    const = gammaln((nu + 1) / 2) - gammaln(nu / 2) - 0.5 * math.log(math.pi * (nu - 2))
    ll = const - 0.5 * np.log(h) - (nu + 1) / 2 * np.log1p(x**2 / ((nu - 2) * h))
    value = -float(np.sum(ll))
    return value if math.isfinite(value) else 1e12


@dataclass(frozen=True)
class ShortHistoryFit:
    alpha: float
    beta: float
    nu: float
    omega: float
    target: float
    objective: float


def fit_short_history(
    x: Array, prior_sessions: float, unit_information: Array = UNIT_INFORMATION
) -> ShortHistoryFit:
    """Penalized-likelihood fit on percent returns `x`. `prior_sessions = 0` is plain ML."""
    x = np.asarray(x, dtype=np.float64)
    if x.size < 20 or not np.all(np.isfinite(x)):
        raise FitError("garch_t_sh: need at least 20 finite returns")
    target = float(np.mean(x**2))
    start = backcast(x)
    center = prior_center()
    precision = prior_sessions * np.asarray(unit_information, dtype=np.float64)

    def objective(theta: Array) -> float:
        penalty = 0.5 * float(np.sum(precision * (theta - center) ** 2))
        return neg_loglik(theta, x, target, start) + penalty

    starts = [
        center,
        center + np.array([-1.0, 0.5, 0.0]),
        center + np.array([1.0, -0.5, 0.5]),
        center + np.array([0.0, 0.0, -1.0]),
    ]
    best = None
    for theta0 in starts:
        res = minimize(objective, theta0, method="L-BFGS-B", bounds=BOUNDS)
        if (
            np.all(np.isfinite(res.x))
            and math.isfinite(res.fun)
            and (best is None or res.fun < best.fun)
        ):
            best = res
    if best is None or best.fun >= 1e12:
        raise FitError("garch_t_sh: optimizer found no valid solution")
    alpha, beta, nu = to_natural(best.x)
    if not (alpha >= 0 and beta >= 0 and alpha + beta < 1 and nu > 2):
        raise FitError("garch_t_sh: invalid parameters")
    return ShortHistoryFit(alpha, beta, nu, target * (1 - alpha - beta), target, float(best.fun))


class GarchTShortHistory(Model):
    name = "garch_t_sh"
    min_history = 100
    statistical = True

    def __init__(self, prior_sessions: float = DEFAULT_PRIOR_SESSIONS) -> None:
        self.prior_sessions = prior_sessions

    def fit(self, data: ModelData) -> Params:
        res = fit_short_history(data.r * SCALE, self.prior_sessions)
        return {"omega": res.omega, "alpha": res.alpha, "beta": res.beta, "nu": res.nu}

    def predict(self, data: ModelData, params: Params) -> list[Prediction]:
        x = data.r * SCALE
        h = variance_path(x, params["omega"], params["alpha"], params["beta"], backcast(x))
        variance = check_variance(float(h[-1]) / SCALE**2, self.name)
        return [Prediction(self.name, variance, "t", nu=params["nu"])]
