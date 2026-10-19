"""Golden outputs (§16) and the leak test (NFR-1): no forecast uses data from after t."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from tests.conftest import FIXTURES
from volforecast.calendar import TradingCalendar
from volforecast.estimators import daily_frame
from volforecast.forecast import ModelState, build_model_data, forecast_next
from volforecast.golden import OPTIMIZER_MODELS, golden_forecasts
from volforecast.models import all_models
from volforecast.synthetic import simulate_vix

GOLDEN = FIXTURES / "golden" / "forecasts.json"


def test_golden_outputs_unchanged() -> None:
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    actual = golden_forecasts()
    assert actual.keys() == expected.keys()
    for n, models in expected.items():
        assert actual[n].keys() == models.keys(), f"model list changed at n={n}"
        for model, want in models.items():
            got = actual[n][model]
            assert got["version"] == want["version"] and got["fit_status"] == want["fit_status"]
            rel = 1e-6 if model in OPTIMIZER_MODELS else 1e-12
            for key in ("variance", "var95", "var99"):
                if want[key] is None:
                    assert got[key] is None, (n, model, key)
                else:
                    assert got[key] == pytest.approx(want[key], rel=rel), (
                        f"{model} at n={n}: {key} changed; bump the model version and run "
                        "scripts/update_golden.py"
                    )


@pytest.mark.parametrize("t", [40, 130, 260, 505])
def test_truncated_data_gives_the_same_forecast(
    t: int, spy_bars, calendar: TradingCalendar
) -> None:  # type: ignore[no-untyped-def]
    bars, true_h = spy_bars
    vix = simulate_vix(bars, true_h, seed=8)
    full = build_model_data(daily_frame(bars), vix=vix, calendar=calendar)
    cutoff = pd.Timestamp(full.dates[t])
    truncated = build_model_data(
        daily_frame(bars[bars["date"] <= cutoff]), vix=vix[vix.index <= cutoff], calendar=calendar
    )
    a = forecast_next(full.head(t + 1), all_models(), ModelState())
    b = forecast_next(truncated, all_models(), ModelState())
    assert [r.model for r in a] == [r.model for r in b]
    for x, y in zip(a, b, strict=True):
        assert x.fit_status == y.fit_status, x.model
        if x.variance is not None:
            assert x.variance == pytest.approx(y.variance, rel=1e-10), x.model
            assert x.var99 == pytest.approx(y.var99, rel=1e-10), x.model
