"""Offline providers for tests and local development: no network, no API key (§16).

- `RecordedProvider` replays saved response bodies (synthetic values) through the same parsers
  as the live clients, so message detection and parsing are tested end to end.
- `FrameProvider` serves in-memory synthetic bars up to a movable "available through" date,
  which lets tests walk the live job forward one evening at a time.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pandas as pd

from volforecast.providers.alphavantage import parse_daily, parse_dividends, parse_splits
from volforecast.providers.base import (
    ProviderError,
    empty_dividends,
    empty_splits,
    filter_dates,
)
from volforecast.providers.budget import CallBudget
from volforecast.providers.fred import parse_observations


class RecordedProvider:
    name = "recorded"

    def __init__(self, root: Path, budget: CallBudget | None = None) -> None:
        self.root = root
        self.budget = budget
        self.calls_made = 0

    def _load(self, *parts: str, charge: bool = True) -> Any:
        path = self.root.joinpath(*parts)
        if not path.is_file():
            raise ProviderError(f"no recorded response at {path.name}")
        if charge and self.budget is not None:  # the budget is for Alpha Vantage calls only
            self.budget.charge(path.stem)
        self.calls_made += 1
        return json.loads(path.read_text(encoding="utf-8"))

    def daily_bars(
        self, ticker: str, start: dt.date | None = None, end: dt.date | None = None
    ) -> pd.DataFrame:
        payload = self._load("alphavantage", f"TIME_SERIES_DAILY_{ticker}.json")
        return filter_dates(parse_daily(payload), start, end)

    def dividends(self, ticker: str) -> pd.DataFrame:
        return parse_dividends(self._load("alphavantage", f"DIVIDENDS_{ticker}.json"))

    def splits(self, ticker: str) -> pd.DataFrame:
        return parse_splits(self._load("alphavantage", f"SPLITS_{ticker}.json"))

    def series(self, series_id: str, start: dt.date | None = None) -> pd.Series:
        observations = parse_observations(self._load("fred", f"{series_id}.json", charge=False))
        if start is not None:
            observations = observations[observations.index >= pd.Timestamp(start)]
        return observations


class FrameProvider:
    """Synthetic in-memory provider. `available_through` mimics the provider's latest bar."""

    name = "frames"

    def __init__(
        self,
        bars: dict[str, pd.DataFrame],
        *,
        dividends: dict[str, pd.DataFrame] | None = None,
        splits: dict[str, pd.DataFrame] | None = None,
        vix: pd.Series | None = None,
        budget: CallBudget | None = None,
        compact_size: int = 100,
    ) -> None:
        self._bars = {k: v.sort_values("date").reset_index(drop=True) for k, v in bars.items()}
        self._dividends = dividends or {}
        self._splits = splits or {}
        self._vix = vix
        self.budget = budget
        self.compact_size = compact_size
        self.available_through: dt.date | None = None
        self.vix_available_through: dt.date | None = None
        self.missing_tickers: set[str] = set()  # tickers whose latest bar is "late"
        self.calls: list[str] = []

    def _charge(self, label: str) -> None:
        if self.budget is not None:
            self.budget.charge(label)
        self.calls.append(label)

    def daily_bars(
        self, ticker: str, start: dt.date | None = None, end: dt.date | None = None
    ) -> pd.DataFrame:
        self._charge(f"TIME_SERIES_DAILY {ticker}")
        frame = self._bars[ticker]
        if self.available_through is not None:
            limit = pd.Timestamp(self.available_through)
            if ticker in self.missing_tickers:
                frame = frame[frame["date"] < limit]
            else:
                frame = frame[frame["date"] <= limit]
        frame = frame.tail(self.compact_size)
        return filter_dates(frame, start, end)

    def dividends(self, ticker: str) -> pd.DataFrame:
        self._charge(f"DIVIDENDS {ticker}")
        return self._dividends.get(ticker, empty_dividends()).copy()

    def splits(self, ticker: str) -> pd.DataFrame:
        self._charge(f"SPLITS {ticker}")
        return self._splits.get(ticker, empty_splits()).copy()

    def series(self, series_id: str, start: dt.date | None = None) -> pd.Series:
        self.calls.append(f"FRED {series_id}")
        if self._vix is None:
            raise ProviderError(f"no synthetic series {series_id}")
        values = self._vix
        through = self.vix_available_through or self.available_through
        if through is not None:
            values = values[values.index <= pd.Timestamp(through)]
        if start is not None:
            values = values[values.index >= pd.Timestamp(start)]
        return values.copy()
