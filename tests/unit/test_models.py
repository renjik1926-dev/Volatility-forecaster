"""Models, the shared forecast function and short-history GARCH checks (§11, §16)."""

from __future__ import annotations

import warnings

import numpy as np
import pytest
from arch import arch_model
from hypothesis import given, settings
from hypothesis import strategies as st

from volforecast.forecast import FAILED, FALLBACK, OK, WARMING_UP, ModelState, forecast_next
from volforecast.models import FitError, Model, ModelData, Params, Prediction, all_models
from volforecast.models.baselines import Ewma094, Hist21, RandomWalk, ewma_variance
from volforecast.models.ensemble import Ensemble
from volforecast.models.garch_sh import (
    ALPHA0,
    NU0,
    P0,
    GarchTShortHistory,
    fit_short_history,
    from_natural,
    to_natural,
)
from volforecast.models.har import HarLog, har_features
from volforecast.models.vix import VixMZ, vix_to_variance
from volforecast.synthetic import simulate_garch_returns


def make_data(r: np.ndarray, proxy: np.ndarray | None = None) -> ModelData:
    n = r.size
    start = np.datetime64("2020-01-01", "D")
    dates = np.arange(start, start + np.timedelta64(n, "D"))
    return ModelData(dates=dates, r=r, proxy=r**2 if proxy is None else proxy)


def test_baselines_known_values() -> None:
    proxy = np.arange(1.0, 31.0) * 1e-5
    data = make_data(np.full(30, 0.01), proxy)
    assert RandomWalk().predict(data, {})[0].variance == pytest.approx(30e-5)
    assert Hist21().predict(data, {})[0].variance == pytest.approx(np.mean(proxy[-21:]))


def test_ewma_matches_a_plain_loop() -> None:
    r = np.random.default_rng(1).normal(0, 0.01, 300)
    h = np.mean(r[:22] ** 2)
    for x in r:
        h = 0.94 * h + 0.06 * x**2
    assert ewma_variance(r, 0.94) == pytest.approx(h, rel=1e-12)
    assert Ewma094().predict(make_data(r), {})[0].variance == pytest.approx(h, rel=1e-12)


def test_har_features_windows() -> None:
    y = np.arange(30, dtype=float)
    x = har_features(y)
    assert x.shape == (9, 4)
    assert x[0].tolist() == [1.0, 21.0, np.mean(y[17:22]), np.mean(y[0:22])]


def test_har_recovers_a_known_linear_relation() -> None:
    rng = np.random.default_rng(3)
    log_v = np.empty(600)
    log_v[:22] = -9.0
    for t in range(21, 599):
        w, m = log_v[t - 4 : t + 1].mean(), log_v[t - 21 : t + 1].mean()
        log_v[t + 1] = -1.0 + 0.4 * log_v[t] + 0.3 * w + 0.2 * m + rng.normal(0, 0.3)
    params = HarLog().fit(make_data(np.zeros(600), np.exp(log_v)))
    assert params["bd"] == pytest.approx(0.4, abs=0.12)
    # With normal residuals (sd 0.3), smearing matches the log-normal factor exp(0.09 / 2).
    assert params["smear"] == pytest.approx(np.exp(0.045), rel=0.03)


def test_har_smearing_is_unbiased_for_squared_returns() -> None:
    """On squared returns the log residuals are far from normal; the forecast level must still
    match the average proxy (version 1 overshot it about threefold)."""
    rng = np.random.default_rng(8)
    true_var = 1e-4
    proxy = true_var * rng.chisquare(1, 1500)  # squared returns with constant variance
    model = HarLog()
    data = make_data(np.zeros(1500), proxy)
    forecast = model.predict(data, model.fit(data))[0].variance
    assert forecast == pytest.approx(true_var, rel=0.25)


def test_vix_mz_scales_the_vix_and_stays_positive() -> None:
    rng = np.random.default_rng(9)
    n = 300
    vix_var = rng.uniform(5e-5, 3e-4, n)
    proxy = np.empty(n)
    proxy[0] = 1e-4
    proxy[1:] = 0.7 * vix_var[:-1] * rng.chisquare(1, n - 1)  # realized ≈ 70% of implied
    start = np.datetime64("2020-01-01", "D")
    data = ModelData(
        dates=np.arange(start, start + np.timedelta64(n, "D")),
        r=np.zeros(n),
        proxy=proxy,
        vix_var=vix_var,
    )
    model = VixMZ()
    params = model.fit(data)
    assert params["scale"] == pytest.approx(0.7, rel=0.2)
    pred = model.predict(data, params)[0]
    assert pred.variance == pytest.approx(params["scale"] * vix_var[-1])
    assert pred.variance > 0


def test_vix_conversion() -> None:
    assert vix_to_variance(20.0, 21) == pytest.approx(0.04 * 30 / 365 / 21)


@settings(max_examples=200, deadline=None)
@given(st.lists(st.floats(min_value=-12, max_value=12), min_size=3, max_size=3))
def test_reparameterization_respects_constraints(theta: list[float]) -> None:
    alpha, beta, nu = to_natural(np.array(theta))
    assert alpha >= 0 and beta >= 0 and alpha + beta < 1 and nu > 2


def test_reparameterization_round_trips() -> None:
    alpha, beta, nu = to_natural(from_natural(0.05, 0.9, 6.0))
    assert (alpha, beta, nu) == pytest.approx((0.05, 0.9, 6.0))


def test_huge_prior_returns_the_prior_centers() -> None:
    x = simulate_garch_returns(300, seed=5, omega=0.1, alpha=0.15, beta=0.7, nu=5.0)
    fit = fit_short_history(x, prior_sessions=1e9)
    assert (fit.alpha, fit.beta, fit.nu) == pytest.approx((ALPHA0, P0 - ALPHA0, NU0), abs=1e-3)


def test_zero_prior_matches_arch_maximum_likelihood() -> None:
    x = simulate_garch_returns(4000, seed=11, omega=0.03, alpha=0.08, beta=0.89, nu=7.0)
    fit = fit_short_history(x, prior_sessions=0.0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ml = arch_model(x, mean="Zero", vol="GARCH", p=1, q=1, dist="t", rescale=False).fit(
            disp="off"
        )
    # arch estimates ω freely while garch_t_sh targets the variance, so allow a small gap.
    assert fit.alpha == pytest.approx(ml.params["alpha[1]"], abs=0.015)
    assert fit.beta == pytest.approx(ml.params["beta[1]"], abs=0.02)
    assert fit.nu == pytest.approx(ml.params["nu"], abs=0.8)


def test_short_history_garch_forecasts_from_100_sessions() -> None:
    r = simulate_garch_returns(100, seed=2, omega=2e-6, alpha=0.08, beta=0.9, nu=7.0)
    model = GarchTShortHistory()
    data = make_data(r)
    pred = model.predict(data, model.fit(data))[0]
    assert pred.dist == "t" and pred.nu is not None and pred.nu > 2
    assert 0.2 * np.var(r) < pred.variance < 5 * np.var(r)


class Flaky(Model):
    """Fails to fit when told to; used to exercise the fallback rule."""

    name = "flaky"
    statistical = True
    min_history = 22

    def __init__(self) -> None:
        self.fail = False

    def fit(self, data: ModelData) -> Params:
        if self.fail:
            raise FitError("forced failure")
        return {"scale": 1.0}

    def predict(self, data: ModelData, params: Params) -> list[Prediction]:
        return [Prediction(self.name, float(np.mean(data.proxy[-5:])) * params["scale"], "normal")]


def test_fallback_reuses_last_good_parameters_and_counts() -> None:
    data = make_data(np.random.default_rng(0).normal(0, 0.01, 60))
    model, state = Flaky(), ModelState()
    assert forecast_next(data, [model], state)[0].fit_status == OK
    model.fail = True
    first = forecast_next(data, [model], state)[0]
    second = forecast_next(data, [model], state)[0]
    assert first.fit_status == second.fit_status == FALLBACK
    assert first.variance is not None
    assert state.consecutive_fallbacks(model.key) == 2


def test_failed_fit_without_previous_parameters() -> None:
    model = Flaky()
    model.fail = True
    res = forecast_next(make_data(np.full(60, 0.01)), [model], ModelState())[0]
    assert res.fit_status == FAILED and res.variance is None


def test_warming_up_and_ensemble_membership() -> None:
    r = simulate_garch_returns(160, seed=4, omega=2e-6, alpha=0.08, beta=0.9, nu=7.0)
    res = {x.model: x for x in forecast_next(make_data(r), all_models(), ModelState())}
    assert res["garch_t"].fit_status == WARMING_UP
    assert res["garch_t"].notes == "needs 339 more sessions"  # 160 returns = 161 sessions
    assert res["har_log"].fit_status == OK
    assert res["ensemble"].fit_status == OK
    assert res["ensemble"].notes == "members=garch_t_sh|har_log"
    assert res["ensemble"].variance == pytest.approx(
        (res["garch_t_sh"].variance + res["har_log"].variance) / 2
    )
    assert "yz21" not in res and "vix" not in res  # need range data / VIX


def test_ensemble_needs_two_members() -> None:
    with pytest.raises(FitError):
        Ensemble().combine([("garch_t_sh", 1e-4)])


def test_refit_false_keeps_parameters_fixed() -> None:
    r = simulate_garch_returns(300, seed=6, omega=2e-6, alpha=0.08, beta=0.9, nu=7.0)
    state = ModelState()
    model = GarchTShortHistory()
    forecast_next(make_data(r[:250]), [model], state, refit=True)
    before = dict(state.params(model.key) or {})
    forecast_next(make_data(r), [model], state, refit=False)
    assert state.params(model.key) == before
