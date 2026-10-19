"""Trading sessions for the NYSE (XNYS), early closes and the "latest completed session" (§10.1).

Unscheduled closures that the calendar package doesn't know about yet are passed in as
`extra_closures` (confirmed `no_session` dates, per the runbook) and treated as holidays.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

NEW_YORK = ZoneInfo("America/New_York")
UTC = dt.UTC


@lru_cache(maxsize=1)
def _xnys() -> xcals.ExchangeCalendar:
    return xcals.get_calendar("XNYS", start="2000-01-01")


def _ts(day: dt.date) -> pd.Timestamp:
    return pd.Timestamp(day)


class TradingCalendar:
    def __init__(self, extra_closures: Iterable[dt.date] = ()) -> None:
        self._cal = _xnys()
        self.extra_closures: frozenset[dt.date] = frozenset(extra_closures)
        self._early = frozenset(d.date() for d in self._cal.early_closes)

    # --- sessions -------------------------------------------------------------------------
    def is_session(self, day: dt.date) -> bool:
        if day in self.extra_closures:
            return False
        try:
            return bool(self._cal.is_session(_ts(day)))
        except (ValueError, xcals.errors.DateOutOfBounds):
            return False

    def sessions(self, start: dt.date, end: dt.date) -> list[dt.date]:
        """Sessions with start <= session <= end."""
        if end < start:
            return []
        days = self._cal.sessions_in_range(_ts(start), _ts(end))
        return [d.date() for d in days if d.date() not in self.extra_closures]

    def next_session(self, day: dt.date) -> dt.date:
        """The first session strictly after `day`."""
        candidate = day
        while True:
            candidate = self._cal.date_to_session(
                _ts(candidate + dt.timedelta(days=1)), direction="next"
            ).date()
            if candidate not in self.extra_closures:
                return candidate

    def previous_session(self, day: dt.date) -> dt.date:
        """The last session strictly before `day`."""
        candidate = day
        while True:
            candidate = self._cal.date_to_session(
                _ts(candidate - dt.timedelta(days=1)), direction="previous"
            ).date()
            if candidate not in self.extra_closures:
                return candidate

    def is_early_close(self, day: dt.date) -> bool:
        return day in self._early

    def session_open_utc(self, day: dt.date) -> dt.datetime:
        return self._cal.session_open(_ts(day)).to_pydatetime()

    def session_close_utc(self, day: dt.date) -> dt.datetime:
        return self._cal.session_close(_ts(day)).to_pydatetime()

    # --- run-time helpers ------------------------------------------------------------------
    def latest_completed_session(self, now_utc: dt.datetime) -> dt.date:
        """Most recent session whose close is at or before `now_utc`.

        Never trusts the runner's calendar date: a delayed run or the 03:37 UTC backup run
        still lands on the right session (§14).
        """
        if now_utc.tzinfo is None:
            raise ValueError("now_utc must be timezone-aware")
        today_ny = now_utc.astimezone(NEW_YORK).date()
        candidate = today_ny if self.is_session(today_ny) else self.previous_session(today_ny)
        if self.session_close_utc(candidate) > now_utc:
            candidate = self.previous_session(candidate)
        return candidate

    def is_first_session_of_week(self, day: dt.date) -> bool:
        return self.previous_session(day).isocalendar()[:2] != day.isocalendar()[:2]

    def sessions_in_next_30_days(self, day: dt.date) -> int:
        """Nₜ in the VIX conversion: sessions in the 30 calendar days after `day` (§3)."""
        return len(self.sessions(day + dt.timedelta(days=1), day + dt.timedelta(days=30)))
