"""Providers on recorded synthetic responses, including rate-limit and premium messages (§16)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import httpx
import pytest

from tests.conftest import FIXTURES
from volforecast.providers.alphavantage import AlphaVantageClient, check_messages, parse_daily
from volforecast.providers.base import (
    AuthError,
    BudgetExceededError,
    PremiumEndpointError,
    ProviderError,
    RateLimitError,
)
from volforecast.providers.budget import CallBudget
from volforecast.providers.fred import FredClient, parse_observations
from volforecast.providers.recorded import RecordedProvider

FAKE_KEY = "TESTKEY-not-real"
AV = FIXTURES / "alphavantage"


def load(name: str) -> dict:  # type: ignore[type-arg]
    return json.loads((AV / name).read_text(encoding="utf-8"))


def budget(tmp_path: Path, limit: int = 20) -> CallBudget:
    return CallBudget(tmp_path / "budget.json", limit, today=lambda: dt.date(2026, 10, 16))


def test_recorded_daily_series_parses(tmp_path: Path) -> None:
    provider = RecordedProvider(FIXTURES)
    bars = provider.daily_bars("SPY")
    assert len(bars) == 100
    assert list(bars.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert bars["date"].is_monotonic_increasing
    assert bars["date"].iloc[-1] == dt.datetime(2026, 10, 16)
    dividends = provider.dividends("SPY")
    assert len(dividends) == 1 and dividends["amount"].iloc[0] == 0.25
    assert provider.splits("SPY").empty


@pytest.mark.parametrize(
    ("name", "error"),
    [
        ("RATE_LIMIT.json", RateLimitError),
        ("PREMIUM.json", PremiumEndpointError),
        ("ERROR.json", ProviderError),
        ("BAD_KEY.json", AuthError),
    ],
)
def test_message_bodies_raise_typed_errors(name: str, error: type[Exception]) -> None:
    with pytest.raises(error):
        check_messages(load(name))
    with pytest.raises(error):
        parse_daily(load(name))


def _client(tmp_path: Path, handler, limit: int = 20) -> tuple[AlphaVantageClient, list[float]]:  # type: ignore[no-untyped-def]
    sleeps: list[float] = []
    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = AlphaVantageClient(
        FAKE_KEY, budget(tmp_path, limit), http=http, sleep=sleeps.append, clock=lambda: 0.0
    )
    return client, sleeps


def test_retries_count_toward_the_budget(tmp_path: Path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=load("TIME_SERIES_DAILY_SPY.json"))

    client, sleeps = _client(tmp_path, handler)
    bars = client.daily_bars("SPY")
    assert len(bars) == 100
    assert client.budget.used_today() == 2
    assert calls[0].url.params["outputsize"] == "compact"
    assert any(s >= 13.0 for s in sleeps)  # second call waited for the 13-second spacing


def test_rate_limit_message_is_not_retried(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=load("RATE_LIMIT.json"))

    client, _ = _client(tmp_path, handler)
    with pytest.raises(RateLimitError):
        client.daily_bars("SPY")
    assert client.budget.used_today() == 1


def test_budget_guard_stops_before_the_call(tmp_path: Path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=load("TIME_SERIES_DAILY_SPY.json"))

    client, _ = _client(tmp_path, handler, limit=1)
    client.daily_bars("SPY")
    with pytest.raises(BudgetExceededError):
        client.daily_bars("QQQ")
    assert len(calls) == 1


def test_errors_never_contain_the_api_key(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed {request.url}")

    client, _ = _client(tmp_path, handler)
    with pytest.raises(ProviderError) as info:
        client.daily_bars("SPY")
    assert FAKE_KEY not in str(info.value)
    assert FAKE_KEY not in repr(client)


def test_missing_key_is_an_auth_error(tmp_path: Path) -> None:
    with pytest.raises(AuthError):
        AlphaVantageClient(None, budget(tmp_path))


def test_fred_parses_missing_values_and_errors() -> None:
    series = parse_observations(json.loads((FIXTURES / "fred" / "VIXCLS.json").read_text()))
    assert series.isna().sum() == 1
    with pytest.raises(AuthError):
        parse_observations({"error_code": 400, "error_message": "api_key is not registered"})


def test_fred_client_over_mock_transport() -> None:
    body = {
        "observations": [
            {"date": "2026-10-15", "value": "18.2"},
            {"date": "2026-10-16", "value": "."},
        ]
    }
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    series = FredClient(FAKE_KEY, http=http).series("VIXCLS")
    assert series.iloc[0] == 18.2 and series.isna().iloc[1]


def test_budget_persists_and_resets_daily(tmp_path: Path) -> None:
    day = [dt.date(2026, 10, 16)]
    b1 = CallBudget(tmp_path / "b.json", 3, today=lambda: day[0])
    b1.charge()
    b1.charge()
    b2 = CallBudget(tmp_path / "b.json", 3, today=lambda: day[0])  # a later run
    assert b2.used_today() == 2
    b2.charge()
    with pytest.raises(BudgetExceededError):
        b2.charge()
    day[0] = dt.date(2026, 10, 19)
    assert b2.used_today() == 0
