"""Data quality rules (§10.3) and merging fetched bars into the stored history (§10.2)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd

from volforecast.providers.base import BAR_COLUMNS

REVISION_COLUMNS = ["ticker", "date", "field", "old", "new", "detected_utc"]


@dataclass(frozen=True)
class Issue:
    ticker: str
    date: dt.date | None
    rule: str
    detail: str
    severity: str = "error"  # "error" stops the ticker; "warning" is logged

    def __str__(self) -> str:
        day = self.date.isoformat() if self.date else "-"
        return f"{self.ticker} {day} {self.rule}: {self.detail}"


def check_bars(ticker: str, bars: pd.DataFrame) -> list[Issue]:
    """Per-bar rules: price consistency, positive prices, non-zero volume, duplicate dates."""
    issues: list[Issue] = []
    if list(bars.columns[: len(BAR_COLUMNS)]) != BAR_COLUMNS:
        return [Issue(ticker, None, "schema", f"columns {list(bars.columns)}")]
    dup = bars["date"].duplicated(keep=False)
    for day in bars.loc[dup, "date"].drop_duplicates():
        issues.append(Issue(ticker, pd.Timestamp(day).date(), "duplicate_date", "date repeats"))
    for row in bars.itertuples(index=False):
        day = pd.Timestamp(row.date).date()
        prices = np.array([row.open, row.high, row.low, row.close], dtype=np.float64)
        if not np.all(np.isfinite(prices)) or np.any(prices <= 0):
            issues.append(Issue(ticker, day, "non_positive_price", "a price is <= 0 or missing"))
            continue
        if row.high < max(row.open, row.close):
            issues.append(Issue(ticker, day, "high_below_open_close", f"high {row.high}"))
        if row.low > min(row.open, row.close):
            issues.append(Issue(ticker, day, "low_above_open_close", f"low {row.low}"))
        if row.volume <= 0:
            issues.append(Issue(ticker, day, "zero_volume", "volume is 0 on a session day"))
    return issues


def check_dates(ticker: str, bars: pd.DataFrame, expected: list[dt.date]) -> list[Issue]:
    """Dates missing from, or not in, the expected session list."""
    have = {pd.Timestamp(d).date() for d in bars["date"]}
    want = set(expected)
    issues = [Issue(ticker, d, "missing_session", "no bar") for d in sorted(want - have)]
    issues += [
        Issue(ticker, d, "not_a_session", "bar on a non-session date", "warning")
        for d in sorted(have - want)
    ]
    return issues


def large_returns(ticker: str, frame: pd.DataFrame, limit: float) -> list[Issue]:
    """|rₜ| > limit: likely a data error or an unrecorded split (§10.3)."""
    mask = frame["r"].abs() > limit
    return [
        Issue(ticker, pd.Timestamp(day).date(), "large_return", f"|r| = {abs(r):.3f}")
        for day, r in zip(frame.loc[mask, "date"], frame.loc[mask, "r"], strict=True)
    ]


def merge_bars(
    ticker: str, stored: pd.DataFrame, fetched: pd.DataFrame, now_utc: dt.datetime
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Append new dates; compare overlapping dates and log any change as a revision.

    The latest value wins (§10.3); the revision log records what changed and when.
    """
    stamp = now_utc.isoformat(timespec="seconds")
    if stored.empty:
        merged = fetched.copy()
        return merged.sort_values("date").reset_index(drop=True), pd.DataFrame(
            columns=REVISION_COLUMNS
        )
    old = stored.set_index("date")
    new = fetched.set_index("date")
    overlap = old.index.intersection(new.index)
    revisions = []
    for day in overlap:
        for field in BAR_COLUMNS[1:]:
            before, after = old.at[day, field], new.at[day, field]
            if not np.isclose(float(before), float(after), rtol=0, atol=1e-9):
                revisions.append(
                    {
                        "ticker": ticker,
                        "date": pd.Timestamp(day).date().isoformat(),
                        "field": field,
                        "old": before,
                        "new": after,
                        "detected_utc": stamp,
                    }
                )
    combined = pd.concat([old.drop(index=overlap), new]).sort_index()
    merged = combined.reset_index()[BAR_COLUMNS]
    merged["date"] = merged["date"].astype("datetime64[ns]")
    return merged, pd.DataFrame(revisions, columns=REVISION_COLUMNS)
