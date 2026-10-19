"""FRED client for VIXCLS (daily) and the one-time SP500 snapshot (§6, §10.2).

VIXCLS: copyright Cboe; SP500: copyright S&P Dow Jones Indices. Both are used only to compute
results. Raw values stay in the private data repo and are never published.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from typing import Any

import httpx
import numpy as np
import pandas as pd

from volforecast.providers.base import AuthError, ProviderError

BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

CITATIONS = {
    "VIXCLS": (
        "Chicago Board Options Exchange, CBOE Volatility Index: VIX [VIXCLS], retrieved from "
        "FRED, Federal Reserve Bank of St. Louis; https://fred.stlouisfed.org/series/VIXCLS"
    ),
    "SP500": (
        "S&P Dow Jones Indices LLC, S&P 500 [SP500], retrieved from FRED, Federal Reserve Bank "
        "of St. Louis; https://fred.stlouisfed.org/series/SP500"
    ),
}


def parse_observations(payload: Any) -> pd.Series:
    if not isinstance(payload, dict):
        raise ProviderError("FRED: body is not a JSON object")
    if "error_message" in payload:
        message = str(payload["error_message"])
        if "api_key" in message.lower():
            raise AuthError("FRED: the API key was rejected")
        raise ProviderError(f"FRED: {message[:160]}")
    observations = payload.get("observations")
    if not isinstance(observations, list):
        raise ProviderError("FRED: no 'observations' list")
    dates, values = [], []
    for obs in observations:
        day = pd.Timestamp(obs["date"])
        raw = obs.get("value", ".")
        values.append(np.nan if raw in (".", "", None) else float(raw))
        dates.append(day)
    index = pd.DatetimeIndex(dates, name="date").astype("datetime64[ns]")
    return pd.Series(values, index=index, dtype="float64").sort_index()


class FredClient:
    name = "fred"

    def __init__(
        self,
        api_key: str | None,
        *,
        http: httpx.Client | None = None,
        max_retries: int = 3,
        backoff_seconds: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise AuthError("FRED_API_KEY is not set")
        self._key = api_key
        self._http = http or httpx.Client(timeout=30.0)
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self._sleep = sleep
        self.calls_made = 0

    def __repr__(self) -> str:
        return f"FredClient(calls_made={self.calls_made})"

    def series(self, series_id: str, start: dt.date | None = None) -> pd.Series:
        params = {"series_id": series_id, "api_key": self._key, "file_type": "json"}
        if start is not None:
            params["observation_start"] = start.isoformat()
        last_error = "no attempt made"
        for attempt in range(self.max_retries + 1):
            if attempt:
                self._sleep(self.backoff_seconds * 2 ** (attempt - 1))
            self.calls_made += 1
            try:
                response = self._http.get(BASE_URL, params=params)
            except httpx.TransportError as exc:
                last_error = f"network error ({type(exc).__name__})"
                continue
            if response.status_code >= 500:
                last_error = f"HTTP {response.status_code}"
                continue
            try:
                payload = response.json()
            except ValueError as exc:
                raise ProviderError(f"FRED: HTTP {response.status_code}, body is not JSON") from exc
            if response.status_code != 200 and "error_message" not in payload:
                raise ProviderError(f"FRED: HTTP {response.status_code} for {series_id}")
            return parse_observations(payload)
        raise ProviderError(f"FRED: {series_id} failed after retries ({last_error})")
