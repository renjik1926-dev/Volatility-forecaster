"""Simulation tests (§16). Slow; run by nightly.yml with `pytest -m slow`."""

from __future__ import annotations

import warnings

import numpy as np
import pytest
from arch import arch_model
from scipy import stats

from volforecast import estimators as est
from volforecast import evaluate as ev
from volforecast.models.garch_sh import ALPHA0, P0, fit_short_history
from volforecast.synthetic import simulate_garch_returns

pytestmark = pytest.mark.slow


def gbm_bars(
    n_days: int, sigma: float, steps: int, drift: float, seed: int
) -> tuple[np.ndarray, ...]:
    """Finely sampled geometric Brownian motion aggregated to daily OHLC (no overnight gap)."""
    rng = np.random.default_rng(seed)
    inc = rng.standard_normal((n_days, steps)) * sigma / np.sqrt(steps) + drift / steps
    paths = np.concatenate([np.zeros((n_days, 1)), np.cumsum(inc, axis=1)], axis=1)
    o = np.full(n_days, 100.0)
    prices = o[:, None] * np.exp(paths)
    return o, prices.max(axis=1), prices.min(axis=1), prices[:, -1]


def test_range_estimators_recover_sigma_squared() -> None:
    sigma = 0.01
    o, h, lo, c = gbm_bars(20_000, sigma, steps=390, drift=0.0, seed=1)
    for name, values in (
        ("parkinson", est.parkinson(h, lo)),
        ("garman_klass", est.garman_klass(o, h, lo, c)),
        ("rogers_satchell", est.rogers_satchell(o, h, lo, c)),
    ):
        ratio = float(np.mean(values)) / sigma**2
        # Discrete sampling misses the true extremes, so the estimators run slightly low.
        assert 0.90 < ratio < 1.03, (name, ratio)


def test_rogers_satchell_is_robust_to_drift_while_parkinson_is_not() -> None:
    sigma, drift = 0.01, 0.01  # a drift as large as one daily standard deviation
    o, h, lo, c = gbm_bars(20_000, sigma, steps=390, drift=drift, seed=2)
    rs = float(np.mean(est.rogers_satchell(o, h, lo, c))) / sigma**2
    park = float(np.mean(est.parkinson(h, lo))) / sigma**2
    # RS loses a little more to discrete sampling under drift, but stays near σ²; Parkinson
    # mistakes the drift for volatility.
    assert 0.85 < rs < 1.03
    assert park > rs + 0.1


def test_arch_recovers_garch_parameters() -> None:
    x = simulate_garch_returns(6000, seed=3, omega=0.02, alpha=0.08, beta=0.9)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = arch_model(x, mean="Zero", vol="GARCH", p=1, q=1, rescale=False).fit(disp="off")
    assert res.params["alpha[1]"] == pytest.approx(0.08, abs=0.02)
    assert res.params["beta[1]"] == pytest.approx(0.90, abs=0.03)


def test_var_tests_have_the_right_size() -> None:
    rng = np.random.default_rng(4)
    reps, n, level = 2000, 500, 0.95
    hits = rng.random((reps, n)) < (1 - level)
    kupiec = np.mean([ev.kupiec(int(h.sum()), n, 1 - level)[1] < 0.05 for h in hits])
    binom = np.mean([stats.binomtest(int(h.sum()), n, 1 - level).pvalue < 0.05 for h in hits])
    assert 0.03 < kupiec < 0.075
    assert 0.02 < binom < 0.07  # the exact test is conservative with a discrete count


def test_diebold_mariano_size_with_equal_forecasts() -> None:
    rng = np.random.default_rng(5)
    rejections = 0
    reps = 1000
    for _ in range(reps):
        common = rng.gamma(2.0, 1.0, 400)
        a = common + rng.normal(0, 0.5, 400)
        b = common + rng.normal(0, 0.5, 400)
        rejections += ev.diebold_mariano(a, b).pvalue < 0.05
    assert 0.03 < rejections / reps < 0.075


def test_bootstrap_interval_coverage() -> None:
    rng = np.random.default_rng(6)
    reps, covered = 300, 0
    for k in range(reps):
        # AR(1) losses with a known mean of 1.
        e = rng.normal(0, 1, 400)
        x = np.empty(400)
        x[0] = e[0]
        for t in range(1, 400):
            x[t] = 0.5 * x[t - 1] + e[t]
        ci = ev.mean_ci(1.0 + x, reps=500, seed=k)
        covered += ci.low <= 1.0 <= ci.high
    assert 0.88 < covered / reps < 0.98


def test_short_history_estimates_move_from_prior_to_truth() -> None:
    true_alpha, true_beta = 0.15, 0.75  # deliberately far from the prior (p = 0.90)
    x = simulate_garch_returns(8000, seed=7, omega=0.1, alpha=true_alpha, beta=true_beta, nu=7.0)
    gaps = []
    for n in (100, 1000, 8000):
        fit = fit_short_history(x[:n], prior_sessions=250.0)
        gaps.append(abs(fit.alpha + fit.beta - (true_alpha + true_beta)))
    small = fit_short_history(x[:100], prior_sessions=250.0)
    # With 100 sessions the estimate sits near the prior; with 8,000 it is near the truth.
    assert abs(small.alpha + small.beta - P0) < abs(small.alpha + small.beta - 0.90) + 0.05
    assert gaps[-1] < 0.03
    assert gaps[-1] < gaps[0]
    big = fit_short_history(x, prior_sessions=250.0)
    assert big.alpha == pytest.approx(true_alpha, abs=0.03) and big.alpha != pytest.approx(ALPHA0)
