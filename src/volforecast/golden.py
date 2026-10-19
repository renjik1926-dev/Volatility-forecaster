"""Golden outputs: every model's forecasts on a fixed synthetic dataset (§16, §17).

Any change in these numbers, from code or from a dependency upgrade, must come with a model
version bump, so live records never silently mix behaviors.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from volforecast.calendar import TradingCalendar
from volforecast.estimators import daily_frame
from volforecast.forecast import ModelState, build_model_data, forecast_next
from volforecast.models import all_models
from volforecast.synthetic import simulate, simulate_vix

HISTORY_LENGTHS = (60, 160, 620)
# Closed-form models must match exactly (up to float noise); optimizer-based ones within 1e-6.
OPTIMIZER_MODELS = frozenset(
    {"garch_t_sh", "garch_n", "garch_t", "garch_t_fhs", "gjr_t", "egarch_t", "ensemble"}
)


def golden_forecasts() -> dict[str, Any]:
    cal = TradingCalendar()
    sessions = cal.sessions(dt.date(2023, 1, 3), dt.date(2025, 12, 31))[:700]
    bars, true_h = simulate(sessions, seed=2026)
    vix = simulate_vix(bars, true_h, seed=2027)
    data = build_model_data(daily_frame(bars), vix=vix, calendar=cal)
    out: dict[str, Any] = {}
    for n in HISTORY_LENGTHS:
        results = forecast_next(data.head(n), all_models(), ModelState())
        out[str(n)] = {
            r.model: {
                "version": r.model_version,
                "fit_status": r.fit_status,
                "variance": r.variance,
                "var95": r.var95,
                "var99": r.var99,
            }
            for r in results
        }
    return out
