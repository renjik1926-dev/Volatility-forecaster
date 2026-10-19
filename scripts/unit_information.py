"""Estimate the per-observation Fisher information used to scale the garch_t_sh prior.

Simulates a long GARCH(1,1)-t path at the prior centers and takes the diagonal of the numerical
Hessian of the mean negative log-likelihood in θ = (logit p, logit a, ln(ν−2)). The result is
hard-coded as `UNIT_INFORMATION` in `volforecast/models/garch_sh.py`.

Usage: python scripts/unit_information.py [n_sessions] [seed]
"""

from __future__ import annotations

import sys

import numpy as np

from volforecast.models.garch_sh import ALPHA0, NU0, P0, backcast, neg_loglik, prior_center


def simulate_garch_t(n: int, alpha: float, beta: float, nu: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    z = rng.standard_t(nu, size=n) * np.sqrt((nu - 2) / nu)
    omega = 1.0 - alpha - beta  # unconditional variance 1 (percent²)
    x = np.empty(n)
    h = 1.0
    for t in range(n):
        x[t] = np.sqrt(h) * z[t]
        h = omega + alpha * x[t] ** 2 + beta * h
    return x


def unit_information(n: int = 400_000, seed: int = 20261018, step: float = 1e-3) -> np.ndarray:
    x = simulate_garch_t(n, ALPHA0, P0 - ALPHA0, NU0, seed)
    target, start = float(np.mean(x**2)), backcast(x)
    theta = prior_center()

    def f(t: np.ndarray) -> float:
        return neg_loglik(t, x, target, start) / n

    diag = np.empty(3)
    f0 = f(theta)
    for i in range(3):
        e = np.zeros(3)
        e[i] = step
        diag[i] = (f(theta + e) - 2 * f0 + f(theta - e)) / step**2
    return diag


if __name__ == "__main__":
    n_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 400_000
    seed_arg = int(sys.argv[2]) if len(sys.argv) > 2 else 20261018
    print(np.round(unit_information(n_arg, seed_arg), 4).tolist())
