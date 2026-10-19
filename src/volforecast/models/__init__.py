"""Model registry (§11). The order here is the order forecasts are written."""

from __future__ import annotations

from collections.abc import Iterable

from volforecast.models.base import FitError, Model, ModelData, Params, Prediction
from volforecast.models.baselines import Ewma094, Hist21, RandomWalk, YangZhang21
from volforecast.models.ensemble import Ensemble
from volforecast.models.garch import egarch_t, garch_n, garch_t, gjr_t
from volforecast.models.garch_sh import GarchTShortHistory
from volforecast.models.har import HarLog
from volforecast.models.vix import Vix, VixMZ


def all_models(prior_sessions: float | None = None) -> list[Model]:
    sh = GarchTShortHistory() if prior_sessions is None else GarchTShortHistory(prior_sessions)
    return [
        RandomWalk(),
        Hist21(),
        YangZhang21(),
        Ewma094(),
        sh,
        garch_n(),
        garch_t(),
        gjr_t(),
        egarch_t(),
        HarLog(),
        Vix(),
        VixMZ(),
        Ensemble(),
    ]


def select(names: Iterable[str], prior_sessions: float | None = None) -> list[Model]:
    wanted = list(names)
    by_name = {m.name: m for m in all_models(prior_sessions)}
    unknown = [n for n in wanted if n not in by_name]
    if unknown:
        raise KeyError(f"unknown models: {unknown}")
    return [by_name[n] for n in wanted]


__all__ = [
    "FitError",
    "Model",
    "ModelData",
    "Params",
    "Prediction",
    "all_models",
    "select",
]
