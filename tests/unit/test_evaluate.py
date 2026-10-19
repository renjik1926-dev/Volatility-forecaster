"""Statistical tests against known values and simple cases (§12, §16)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from volforecast import evaluate as ev


def test_kupiec_known_value() -> None:
    # 10 violations in 100 sessions at the 95% level.
    lr, p = ev.kupiec(10, 100, 0.05)
    expected = -2 * (
        90 * math.log(0.95) + 10 * math.log(0.05) - 90 * math.log(0.9) - 10 * math.log(0.1)
    )
    assert lr == pytest.approx(expected)
    assert lr == pytest.approx(4.1308, abs=1e-3)
    assert p == pytest.approx(0.0421, abs=1e-3)


def test_kupiec_with_zero_violations() -> None:
    lr, _ = ev.kupiec(0, 100, 0.01)
    assert lr == pytest.approx(-2 * 100 * math.log(0.99))


def test_christoffersen_needs_two_violations() -> None:
    assert ev.christoffersen_independence(np.array([0, 0, 1, 0, 0])) is None
    lr, p = ev.christoffersen_independence(np.array([0, 1, 0, 0, 1, 0, 0, 0, 1, 0]))  # type: ignore[misc]
    assert lr >= 0 and 0 <= p <= 1


def test_clustered_violations_are_detected() -> None:
    hits = np.zeros(500, dtype=int)
    hits[100:110] = 1
    hits[300:310] = 1
    tests = ev.var_tests(hits, 0.95)
    assert tests.ind_p is not None and tests.ind_p < 0.001
    assert tests.violations == 20


def test_low_power_label() -> None:
    assert ev.var_tests(np.zeros(100), 0.99).low_power
    assert not ev.var_tests(np.zeros(1000), 0.99).low_power


def test_benjamini_hochberg_known_example() -> None:
    adjusted = ev.benjamini_hochberg([0.01, 0.04, 0.03, 0.005, float("nan")])
    assert adjusted[:4] == pytest.approx([0.02, 0.04, 0.04, 0.02])
    assert math.isnan(adjusted[4])


def test_traffic_light_zones() -> None:
    assert ev.traffic_light(np.r_[np.ones(4), np.zeros(246)])[0] == "green"
    assert ev.traffic_light(np.r_[np.ones(5), np.zeros(245)])[0] == "yellow"
    assert ev.traffic_light(np.r_[np.ones(10), np.zeros(240)])[0] == "red"


def test_diebold_mariano_direction() -> None:
    rng = np.random.default_rng(0)
    base = rng.gamma(2.0, 1.0, 500)
    better = base * 0.8
    assert ev.diebold_mariano(better, base).pvalue < 0.01
    assert ev.diebold_mariano(base * 1.2, base).pvalue > 0.99


def test_mincer_zarnowitz_on_an_unbiased_forecast() -> None:
    rng = np.random.default_rng(1)
    h = rng.uniform(1e-5, 4e-4, 2000)
    proxy = h * rng.chisquare(1, 2000)
    res = ev.mincer_zarnowitz(proxy, h)
    assert res.b == pytest.approx(1.0, abs=0.2)
    assert res.pvalue > 0.01


def test_bootstrap_is_reproducible() -> None:
    x = np.random.default_rng(2).gamma(2.0, 1.0, 300)
    a, b = ev.mean_ci(x), ev.mean_ci(x)
    assert (a.low, a.high) == (b.low, b.high)
    assert a.low < x.mean() < a.high


def test_loss_table_labels() -> None:
    rng = np.random.default_rng(3)
    dates = [f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(200)]
    base = rng.gamma(2.0, 0.1, 200)
    rows = []
    for model, losses in (("ewma_094", base), ("garch_t_sh", base * 0.7), ("rw", base * 1.5)):
        rows += [
            {"target_date": d, "model": model, "qlike": v}
            for d, v in zip(dates, losses, strict=True)
        ]
    table = ev.loss_table(pd.DataFrame(rows)).set_index("model")
    assert table.loc["garch_t_sh", "best"]
    assert not table.loc["rw", "best"]
    assert not table["early"].any()
