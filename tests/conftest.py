from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from volforecast.calendar import TradingCalendar
from volforecast.config import TICKERS, Settings
from volforecast.estimators import daily_frame
from volforecast.forecast import build_model_data
from volforecast.models import ModelData
from volforecast.providers.budget import CallBudget
from volforecast.providers.recorded import FrameProvider
from volforecast.store import PrivateStore, PublicStore
from volforecast.synthetic import simulate, simulate_vix

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def calendar() -> TradingCalendar:
    return TradingCalendar()


@pytest.fixture(scope="session")
def sessions_2y(calendar: TradingCalendar) -> list[dt.date]:
    return calendar.sessions(dt.date(2024, 10, 1), dt.date(2026, 10, 16))


@pytest.fixture(scope="session")
def spy_bars(sessions_2y: list[dt.date]) -> tuple[pd.DataFrame, object]:
    return simulate(sessions_2y, seed=7)


@pytest.fixture(scope="session")
def spy_data(spy_bars, calendar: TradingCalendar) -> ModelData:  # type: ignore[no-untyped-def]
    bars, true_h = spy_bars
    vix = simulate_vix(bars, true_h, seed=8)
    return build_model_data(daily_frame(bars), vix=vix, calendar=calendar)


def utc(*args: int) -> dt.datetime:
    return dt.datetime(*args, tzinfo=dt.UTC)


class World:
    """A temporary public dir, private dir and synthetic provider for live-job tests."""

    def __init__(self, root: Path, calendar: TradingCalendar, sessions: list[dt.date]) -> None:
        self.root = root
        self.calendar = calendar
        self.settings = Settings(
            public_dir=root / "data",
            private_dir=root / "private",
            site_dir=root / "site",
            data_wait_minutes=0.0,
        )
        self.public = PublicStore(self.settings.public_dir)
        self.private = PrivateStore(self.settings.private_dir)
        self.clock = [utc(2026, 1, 1)]
        self.budget = CallBudget(
            self.private.budget_path,
            self.settings.budget_limit,
            today=lambda: self.clock[0].astimezone(ZoneInfo("America/New_York")).date(),
        )
        bars, vix = {}, None
        for k, ticker in enumerate(TICKERS):
            frame, true_h = simulate(sessions, seed=100 + k)
            bars[ticker] = frame
            if ticker == "SPY":
                vix = simulate_vix(frame, true_h, seed=99)
        self.bars = bars
        self.provider = FrameProvider(bars, vix=vix, budget=self.budget)

    def now(self) -> dt.datetime:
        return self.clock[0]


@pytest.fixture
def world(tmp_path: Path, calendar: TradingCalendar) -> World:
    sessions = calendar.sessions(dt.date(2026, 3, 1), dt.date(2026, 10, 30))
    return World(tmp_path, calendar, sessions)
