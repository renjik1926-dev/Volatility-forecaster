"""Alpha Vantage client: TIME_SERIES_DAILY (compact), DIVIDENDS, SPLITS (§6, §10.2).

Alpha Vantage reports rate limits and premium-only requests as JSON messages inside a normal
HTTP 200 response, so every body is checked for those messages before it is parsed as data.

Response formats are from the public documentation; the week-1 spike (Q1) confirms them,
including how dividend dates line up with ex-dates and what `split_factor` means.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from typing import Any

import httpx
import pandas as pd

from volforecast.providers.base import (
    AuthError,
    PremiumEndpointError,
    ProviderError,
    RateLimitError,
    empty_bars,
    empty_dividends,
    empty_splits,
    filter_dates,
)
from volforecast.providers.budget import CallBudget

BASE_URL = "https://www.alphavantage.co/query"
DAILY_KEY = "Time Series (Daily)"
FIELD_MAP = {
    "1. open": "open",
    "2. high": "high",
    "3. low": "low",
    "4. close": "close",
    "5. volume": "volume",
}


def check_messages(payload: Any) -> None:
    """Raise a typed error if the body is an Alpha Vantage message instead of data."""
    if not isinstance(payload, dict):
        raise ProviderError("unexpected response: body is not a JSON object")
    for key in ("Error Message", "Information", "Note"):
        if key not in payload:
            continue
        text = str(payload[key])
        lower = text.lower()
        # The message text can quote the key back; never include it in our error.
        summary = text.split(".")[0][:160]
        # Rate-limit messages also mention premium plans, so check them first.
        if "rate limit" in lower or "requests per day" in lower or "call frequency" in lower:
            raise RateLimitError(f"Alpha Vantage: rate limit reached ({summary})")
        if "premium" in lower:
            raise PremiumEndpointError(f"Alpha Vantage: premium-only request ({summary})")
        if "apikey" in lower or "api key" in lower:
            raise AuthError("Alpha Vantage: the API key was rejected")
        raise ProviderError(f"Alpha Vantage {key.lower()}: {summary}")


def parse_daily(payload: dict[str, Any]) -> pd.DataFrame:
    check_messages(payload)
    series = payload.get(DAILY_KEY)
    if not isinstance(series, dict):
        raise ProviderError(f"unexpected response: no '{DAILY_KEY}' block")
    if not series:
        return empty_bars()
    rows = []
    for day, values in series.items():
        row: dict[str, Any] = {"date": pd.Timestamp(day)}
        for source, target in FIELD_MAP.items():
            if source not in values:
                raise ProviderError(f"unexpected response: bar {day} has no '{source}'")
            row[target] = values[source]
        rows.append(row)
    frame = pd.DataFrame(rows)
    for col in ("open", "high", "low", "close"):
        frame[col] = pd.to_numeric(frame[col], errors="raise").astype("float64")
    frame["volume"] = pd.to_numeric(frame["volume"], errors="raise").astype("int64")
    frame["date"] = pd.to_datetime(frame["date"]).astype("datetime64[ns]")
    return frame.sort_values("date").reset_index(drop=True)


def _records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    check_messages(payload)
    data = payload.get("data")
    if not isinstance(data, list):
        raise ProviderError("unexpected response: no 'data' list")
    return [r for r in data if isinstance(r, dict)]


def _valid_date(value: Any) -> pd.Timestamp | None:
    try:
        ts = pd.Timestamp(str(value))
    except (ValueError, TypeError):
        return None
    return None if pd.isna(ts) else ts


def parse_dividends(payload: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for rec in _records(payload):
        day = _valid_date(rec.get("ex_dividend_date"))
        try:
            amount = float(rec.get("amount", "nan"))
        except (TypeError, ValueError):
            continue
        if day is not None and amount == amount and amount > 0:
            rows.append({"date": day, "amount": amount})
    if not rows:
        return empty_dividends()
    frame = pd.DataFrame(rows)
    frame["date"] = frame["date"].astype("datetime64[ns]")
    return frame.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def parse_splits(payload: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for rec in _records(payload):
        day = _valid_date(rec.get("effective_date"))
        try:
            factor = float(rec.get("split_factor", "nan"))
        except (TypeError, ValueError):
            continue
        if day is not None and factor == factor and factor > 0:
            rows.append({"date": day, "factor": factor})
    if not rows:
        return empty_splits()
    frame = pd.DataFrame(rows)
    frame["date"] = frame["date"].astype("datetime64[ns]")
    return frame.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


class AlphaVantageClient:
    name = "alphavantage"

    def __init__(
        self,
        api_key: str | None,
        budget: CallBudget,
        *,
        http: httpx.Client | None = None,
        spacing_seconds: float = 13.0,
        max_retries: int = 3,
        backoff_seconds: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not api_key:
            raise AuthError("ALPHAVANTAGE_API_KEY is not set")
        self._key = api_key
        self.budget = budget
        self._http = http or httpx.Client(timeout=30.0)
        self.spacing_seconds = spacing_seconds
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self._sleep = sleep
        self._clock = clock
        self._last_call: float | None = None
        self.calls_made = 0

    def __repr__(self) -> str:  # never show the key
        return f"AlphaVantageClient(calls_made={self.calls_made})"

    def _wait_for_spacing(self) -> None:
        if self._last_call is None:
            return
        wait = self.spacing_seconds - (self._clock() - self._last_call)
        if wait > 0:
            self._sleep(wait)

    def _get(self, params: dict[str, str]) -> dict[str, Any]:
        label = f"{params.get('function')} {params.get('symbol', '')}".strip()
        last_error = "no attempt made"
        for attempt in range(self.max_retries + 1):
            if attempt:
                self._sleep(self.backoff_seconds * 2 ** (attempt - 1))
            self._wait_for_spacing()
            self.budget.charge(label)  # retries count toward the budget (§14)
            self._last_call = self._clock()
            self.calls_made += 1
            try:
                response = self._http.get(BASE_URL, params={**params, "apikey": self._key})
            except httpx.TransportError as exc:
                last_error = f"network error ({type(exc).__name__})"
                continue
            if response.status_code >= 500:
                last_error = f"HTTP {response.status_code}"
                continue
            if response.status_code in (401, 403):
                raise AuthError(f"Alpha Vantage: HTTP {response.status_code} for {label}")
            if response.status_code != 200:
                raise ProviderError(f"Alpha Vantage: HTTP {response.status_code} for {label}")
            try:
                payload = response.json()
            except ValueError as exc:
                raise ProviderError(f"Alpha Vantage: body is not JSON for {label}") from exc
            check_messages(payload)
            if not isinstance(payload, dict):
                raise ProviderError(f"Alpha Vantage: unexpected body for {label}")
            return payload
        raise ProviderError(f"Alpha Vantage: {label} failed after retries ({last_error})")

    def daily_bars(
        self, ticker: str, start: dt.date | None = None, end: dt.date | None = None
    ) -> pd.DataFrame:
        payload = self._get(
            {"function": "TIME_SERIES_DAILY", "symbol": ticker, "outputsize": "compact"}
        )
        return filter_dates(parse_daily(payload), start, end)

    def dividends(self, ticker: str) -> pd.DataFrame:
        return parse_dividends(self._get({"function": "DIVIDENDS", "symbol": ticker}))

    def splits(self, ticker: str) -> pd.DataFrame:
        return parse_splits(self._get({"function": "SPLITS", "symbol": ticker}))
