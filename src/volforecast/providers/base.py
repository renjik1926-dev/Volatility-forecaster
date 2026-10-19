"""Provider protocol and errors (§10.2). Swapping provider is a config change (D9)."""

from __future__ import annotations

import datetime as dt
from typing import Protocol

import pandas as pd

BAR_COLUMNS = ["date", "open", "high", "low", "close", "volume"]
DIVIDEND_COLUMNS = ["date", "amount"]  # ex-dividend date, cash amount per share
SPLIT_COLUMNS = ["date", "factor"]  # effective date, new shares per old share


class ProviderError(RuntimeError):
    """A provider call failed. Messages never contain the request URL or the API key."""


class RateLimitError(ProviderError):
    """The provider says the call quota or call frequency limit was reached."""


class PremiumEndpointError(ProviderError):
    """The endpoint or parameter needs a paid plan."""


class AuthError(ProviderError):
    """The API key is missing, invalid or revoked."""


class BudgetExceededError(ProviderError):
    """Our own daily call budget guard stopped the call before it was made (§14)."""


class PriceProvider(Protocol):
    name: str

    def daily_bars(
        self, ticker: str, start: dt.date | None = None, end: dt.date | None = None
    ) -> pd.DataFrame:
        """Unadjusted daily OHLCV with columns BAR_COLUMNS, sorted by date."""
        ...

    def dividends(self, ticker: str) -> pd.DataFrame:
        """Columns DIVIDEND_COLUMNS, sorted by date."""
        ...

    def splits(self, ticker: str) -> pd.DataFrame:
        """Columns SPLIT_COLUMNS, sorted by date."""
        ...


class SeriesProvider(Protocol):
    name: str

    def series(self, series_id: str, start: dt.date | None = None) -> pd.Series:
        """Daily observations indexed by date (Timestamp); missing values are NaN."""
        ...


def empty_bars() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.Series(dtype="datetime64[ns]"),
            "open": pd.Series(dtype="float64"),
            "high": pd.Series(dtype="float64"),
            "low": pd.Series(dtype="float64"),
            "close": pd.Series(dtype="float64"),
            "volume": pd.Series(dtype="int64"),
        }
    )


def empty_dividends() -> pd.DataFrame:
    return pd.DataFrame(
        {"date": pd.Series(dtype="datetime64[ns]"), "amount": pd.Series(dtype="float64")}
    )


def empty_splits() -> pd.DataFrame:
    return pd.DataFrame(
        {"date": pd.Series(dtype="datetime64[ns]"), "factor": pd.Series(dtype="float64")}
    )


def filter_dates(frame: pd.DataFrame, start: dt.date | None, end: dt.date | None) -> pd.DataFrame:
    mask = pd.Series(True, index=frame.index)
    if start is not None:
        mask &= frame["date"] >= pd.Timestamp(start)
    if end is not None:
        mask &= frame["date"] <= pd.Timestamp(end)
    return frame.loc[mask].reset_index(drop=True)
