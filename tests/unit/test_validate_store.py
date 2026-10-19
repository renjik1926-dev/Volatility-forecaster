"""Validation rules, revision logging and the append-only store (§10.3, §16)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from tests.conftest import utc
from volforecast.store import (
    FORECAST_COLUMNS,
    AppendOnlyCSV,
    StoreError,
    format_value,
    input_hash,
)
from volforecast.validate import check_bars, check_dates, large_returns, merge_bars


def bars(rows: list[tuple[str, float, float, float, float, int]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    frame["date"] = pd.to_datetime(frame["date"]).astype("datetime64[ns]")
    return frame


def test_bad_bars_are_flagged() -> None:
    frame = bars(
        [
            ("2026-10-12", 100, 101, 99, 100.5, 10),
            ("2026-10-13", 100, 99.5, 99, 100.5, 10),  # high below close
            ("2026-10-14", 100, 101, 100.2, 100.5, 10),  # low above open
            ("2026-10-15", 100, 101, 99, 100.5, 0),  # zero volume
            ("2026-10-16", -1, 101, 99, 100.5, 10),  # non-positive price
            ("2026-10-16", 100, 101, 99, 100.5, 10),  # duplicate date
        ]
    )
    rules = {i.rule for i in check_bars("SPY", frame)}
    assert rules == {
        "high_below_open_close",
        "low_above_open_close",
        "zero_volume",
        "non_positive_price",
        "duplicate_date",
    }


def test_missing_and_extra_dates() -> None:
    frame = bars([("2026-10-12", 100, 101, 99, 100, 1), ("2026-10-17", 100, 101, 99, 100, 1)])
    expected = [dt.date(2026, 10, 12), dt.date(2026, 10, 13)]
    issues = check_dates("SPY", frame, expected)
    assert {(i.rule, i.date) for i in issues} == {
        ("missing_session", dt.date(2026, 10, 13)),
        ("not_a_session", dt.date(2026, 10, 17)),
    }


def test_large_return_rule() -> None:
    frame = pd.DataFrame({"date": pd.to_datetime(["2026-10-13", "2026-10-14"]), "r": [0.01, -0.7]})
    assert [i.date for i in large_returns("SPY", frame, 0.25)] == [dt.date(2026, 10, 14)]


def test_merge_appends_new_dates_and_logs_revisions() -> None:
    stored = bars([("2026-10-12", 100, 101, 99, 100, 1), ("2026-10-13", 100, 101, 99, 100, 1)])
    fetched = bars([("2026-10-13", 100, 101, 99, 100.25, 1), ("2026-10-14", 100, 101, 99, 100, 1)])
    merged, revisions = merge_bars("SPY", stored, fetched, utc(2026, 10, 14, 23, 40))
    assert len(merged) == 3
    assert merged.loc[merged["date"] == "2026-10-13", "close"].iloc[0] == 100.25
    assert revisions[["date", "field"]].values.tolist() == [["2026-10-13", "close"]]


def test_append_only_csv_refuses_duplicates(tmp_path: Path) -> None:
    store = AppendOnlyCSV(tmp_path / "f.csv", ["id", "value"], key="id")
    store.append([{"id": "a", "value": 1.0}])
    with pytest.raises(StoreError):
        store.append([{"id": "a", "value": 2.0}])
    with pytest.raises(StoreError):
        store.append([{"id": "b", "value": 1.0}, {"id": "b", "value": 2.0}])
    with pytest.raises(StoreError):
        store.append([{"id": "c"}])  # missing column
    assert store.read()["id"].tolist() == ["a"]


def test_header_must_match_schema(tmp_path: Path) -> None:
    path = tmp_path / "forecasts.csv"
    path.write_text("forecast_id,ticker\n", encoding="utf-8")
    with pytest.raises(StoreError):
        AppendOnlyCSV(path, FORECAST_COLUMNS, "forecast_id").read()


def test_stable_formatting() -> None:
    assert format_value(0.1 + 0.2) == "0.3"
    assert format_value(None) == "" and format_value(float("nan")) == ""
    assert format_value(True) == "true"
    assert format_value(dt.date(2026, 10, 16)) == "2026-10-16"


def test_input_hash_is_deterministic_and_sensitive() -> None:
    a = bars([("2026-10-12", 100.0, 101.0, 99.0, 100.0, 1)])
    b = a.copy()
    assert input_hash(a) == input_hash(b)
    b.loc[0, "close"] = 100.0001
    assert input_hash(a) != input_hash(b)
