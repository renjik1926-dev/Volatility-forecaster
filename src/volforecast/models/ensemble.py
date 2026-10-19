"""`ensemble`: equal-weight average of the statistical models that forecast this evening (§11).

Models still warming up, or whose fit failed with no fallback, are left out. The members are
recorded in the forecast's notes. Needs at least two live statistical models and 150 sessions.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from volforecast.models.base import FitError, Model, ModelData, Params, Prediction

MIN_MEMBERS = 2


class Ensemble(Model):
    name = "ensemble"
    version = 2  # version 2: its member har_log changed to version 2
    min_history = 150

    def predict(self, data: ModelData, params: Params) -> list[Prediction]:
        raise NotImplementedError("the ensemble is combined from other forecasts; use combine()")

    def combine(self, members: Sequence[tuple[str, float]]) -> Prediction:
        if len(members) < MIN_MEMBERS:
            raise FitError(
                f"ensemble: {len(members)} live statistical model(s), need {MIN_MEMBERS}"
            )
        names = sorted(name for name, _ in members)
        value = float(np.mean([v for _, v in members]))
        return Prediction(self.name, value, "normal", notes="members=" + "|".join(names))
