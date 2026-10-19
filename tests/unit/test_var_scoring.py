"""VaR quantiles and scoring (§3, §10.5, §10.6)."""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy import stats

from tests.conftest import utc
from volforecast.scoring import Realized, mse, qlike, score, violation
from volforecast.var import t_unit_quantile, value_at_risk, var_pair


def test_normal_var() -> None:
    v95, v99 = var_pair(1e-4, "normal")
    assert v95 == pytest.approx(0.01 * 1.6448536269514722)
    assert v99 == pytest.approx(0.01 * 2.3263478740408408)


def test_student_t_quantile_is_unit_variance() -> None:
    nu = 5.0
    q = t_unit_quantile(nu, 0.99)
    assert q == pytest.approx(stats.t.ppf(0.01, nu) * math.sqrt(3 / 5))
    # The unit-variance t has fatter tails than the normal at 99% but not at 95%.
    assert -q > 2.3263478740408408
    assert -t_unit_quantile(nu, 0.95) < 1.6448536269514722


def test_fhs_uses_the_empirical_quantile() -> None:
    z = np.linspace(-3, 3, 1000)
    assert value_at_risk(1e-4, 0.95, "fhs", z=z) == pytest.approx(-0.01 * np.quantile(z, 0.05))
    with pytest.raises(ValueError):
        value_at_risk(1e-4, 0.95, "fhs", z=z[:10])


def test_qlike_is_zero_at_a_perfect_forecast_and_floors_zero_proxies() -> None:
    assert qlike(2e-4, 2e-4) == pytest.approx(0.0)
    assert qlike(2e-4, 1e-4) == pytest.approx(2 - math.log(2) - 1)
    assert math.isfinite(qlike(0.0, 1e-4))
    assert mse(3.0, 1.0) == 4.0


def test_violation_and_score_row() -> None:
    assert violation(-0.03, 0.02) == 1
    assert violation(-0.01, 0.02) == 0
    forecast = {
        "forecast_id": "SPY-2026-10-19-rw",
        "variance": "1e-4",
        "var95": "0.0164",
        "var99": "0.0233",
    }
    realized = Realized(r=-0.02, proxy_rs=4e-4, proxy_r2=4e-4, proxy_gk=3e-4)
    row = score(forecast, realized, utc(2026, 10, 19, 23, 40))
    assert row["viol95"] == 1 and row["viol99"] == 0
    assert row["qlike"] == pytest.approx(4 - math.log(4) - 1)
    assert "r" not in row  # returns are never written to scores.csv


def test_close_only_scoring_uses_squared_returns() -> None:
    realized = Realized(r=0.01, proxy_rs=None, proxy_r2=1e-4, proxy_gk=None)
    row = score(
        {"forecast_id": "x", "variance": 1e-4, "var95": None, "var99": None},
        realized,
        utc(2026, 1, 1),
    )
    assert row["qlike"] == pytest.approx(0.0)
    assert row["viol95"] is None
