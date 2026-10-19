"""Calendar: holidays, early closes, DST, unscheduled closures, latest completed session (§16)."""

from __future__ import annotations

import datetime as dt

from tests.conftest import utc
from volforecast.calendar import TradingCalendar


def test_holidays_and_early_closes(calendar: TradingCalendar) -> None:
    assert not calendar.is_session(dt.date(2026, 11, 26))  # Thanksgiving
    assert calendar.is_session(dt.date(2026, 11, 27))
    assert calendar.is_early_close(dt.date(2026, 11, 27))
    assert not calendar.is_early_close(dt.date(2026, 11, 30))
    assert calendar.next_session(dt.date(2026, 11, 25)) == dt.date(2026, 11, 27)
    assert calendar.previous_session(dt.date(2026, 11, 27)) == dt.date(2026, 11, 25)


def test_open_times_follow_daylight_saving(calendar: TradingCalendar) -> None:
    assert calendar.session_open_utc(dt.date(2026, 10, 30)) == utc(2026, 10, 30, 13, 30)
    assert calendar.session_open_utc(dt.date(2026, 11, 2)) == utc(2026, 11, 2, 14, 30)
    assert calendar.session_open_utc(dt.date(2026, 3, 9)) == utc(2026, 3, 9, 13, 30)


def test_latest_completed_session_for_primary_and_backup_runs(calendar: TradingCalendar) -> None:
    friday = dt.date(2026, 10, 16)
    assert calendar.latest_completed_session(utc(2026, 10, 16, 23, 37)) == friday
    # The 03:37 UTC backup run is on Saturday's UTC date but still processes Friday.
    assert calendar.latest_completed_session(utc(2026, 10, 17, 3, 37)) == friday
    # Before Monday's close the latest completed session is still Friday.
    assert calendar.latest_completed_session(utc(2026, 10, 19, 15, 0)) == friday
    # A run delayed into the next morning before the open.
    assert calendar.latest_completed_session(utc(2026, 10, 20, 12, 0)) == dt.date(2026, 10, 19)


def test_latest_completed_session_around_an_early_close(calendar: TradingCalendar) -> None:
    # The day after Thanksgiving closes at 13:00 ET (18:00 UTC).
    assert calendar.latest_completed_session(utc(2026, 11, 27, 18, 30)) == dt.date(2026, 11, 27)
    assert calendar.latest_completed_session(utc(2026, 11, 27, 17, 30)) == dt.date(2026, 11, 25)


def test_unscheduled_closure_is_skipped() -> None:
    closed = dt.date(2026, 10, 14)
    cal = TradingCalendar(extra_closures=[closed])
    assert not cal.is_session(closed)
    assert cal.next_session(dt.date(2026, 10, 13)) == dt.date(2026, 10, 15)
    assert closed not in cal.sessions(dt.date(2026, 10, 12), dt.date(2026, 10, 16))
    assert cal.latest_completed_session(utc(2026, 10, 14, 23, 37)) == dt.date(2026, 10, 13)


def test_first_session_of_week_and_vix_session_count(calendar: TradingCalendar) -> None:
    assert calendar.is_first_session_of_week(dt.date(2026, 10, 19))  # Monday
    assert not calendar.is_first_session_of_week(dt.date(2026, 10, 20))
    assert calendar.is_first_session_of_week(dt.date(2026, 9, 8))  # Tuesday after Labor Day
    assert 19 <= calendar.sessions_in_next_30_days(dt.date(2026, 10, 16)) <= 22
