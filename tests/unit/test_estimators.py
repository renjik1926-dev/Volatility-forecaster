"""Known-value tests for returns and range estimators (§16).

Example bar: previous close 99, open 100, high 105, low 98, close 102. Expected values were
computed by hand from the formulas in §3 (to full double precision with a calculator script).
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from volforecast import estimators as est

OPEN, HIGH, LOW, CLOSE, PREV = 100.0, 105.0, 98.0, 102.0, 99.0


def test_close_to_close_and_overnight_returns() -> None:
    assert est.close_to_close_return(CLOSE, PREV) == pytest.approx(0.02985296314968113, rel=1e-14)
    assert est.overnight_return(OPEN, PREV) == pytest.approx(0.010050335853501506, rel=1e-14)


def test_dividend_and_split_adjustment() -> None:
    # A 0.50 dividend goes ex on the day: r = ln((102 + 0.5) / 99).
    assert est.close_to_close_return(CLOSE, PREV, dividend=0.5) == pytest.approx(
        0.03474294844387284, rel=1e-14
    )
    # A 2-for-1 split takes effect: previous close 200 becomes 100; close 101 → ln(1.01).
    assert est.close_to_close_return(101.0, 200.0, split=2.0) == pytest.approx(
        0.009950330853168092, rel=1e-14
    )


def test_parkinson_garman_klass_rogers_satchell() -> None:
    assert est.parkinson(HIGH, LOW) == pytest.approx(0.0017168129834163463, rel=1e-14)
    assert est.garman_klass(OPEN, HIGH, LOW, CLOSE) == pytest.approx(
        0.002228525123583498, rel=1e-14
    )
    assert est.rogers_satchell(OPEN, HIGH, LOW, CLOSE) == pytest.approx(
        0.0022225227492534334, rel=1e-14
    )


def test_yang_zhang_three_day_window() -> None:
    o = np.array([0.01, -0.02, 0.005])
    c = np.array([0.0, 0.01, -0.015])
    rs = np.array([1e-4, 2e-4, 1.5e-4])
    assert est.yang_zhang(o, c, rs) == pytest.approx(0.0004091816367265469, rel=1e-12)


@pytest.mark.parametrize(
    ("o", "h", "lo", "c"),
    [(100, 99, 98, 100), (100, 105, 101, 102), (0, 105, 98, 102), (100, 105, 98, -1)],
)
def test_invalid_bars_are_rejected(o: float, h: float, lo: float, c: float) -> None:
    with pytest.raises(ValueError):
        est.rogers_satchell(o, h, lo, c)


def test_flat_bar_gives_zero_variance() -> None:
    assert est.rogers_satchell(100, 100, 100, 100) == 0.0
    assert est.garman_klass(100, 100, 100, 100) == 0.0


def test_daily_frame_lines_up_corporate_actions() -> None:
    bars = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"]),
            "open": [99.0, 100.0, 51.0],
            "high": [100.0, 105.0, 52.0],
            "low": [98.0, 98.0, 50.0],
            "close": [99.0, 102.0, 51.5],
            "volume": [1, 1, 1],
        }
    )
    dividends = pd.DataFrame({"date": pd.to_datetime(["2026-01-06"]), "amount": [0.5]})
    splits = pd.DataFrame({"date": pd.to_datetime(["2026-01-07"]), "factor": [2.0]})
    frame = est.daily_frame(bars, dividends, splits)
    assert list(frame["date"].dt.date.astype(str)) == ["2026-01-06", "2026-01-07"]
    assert frame["r"].iloc[0] == pytest.approx(math.log(102.5 / 99.0))
    assert frame["r"].iloc[1] == pytest.approx(math.log(51.5 / 51.0))
    assert frame["overnight"].iloc[1] == pytest.approx(0.0)
    assert frame["proxy_rs"].iloc[0] == pytest.approx(
        math.log(100.5 / 99) ** 2 + 0.0022225227492534334
    )


def test_close_only_frame_has_no_range_columns() -> None:
    closes = pd.Series(
        [100.0, 101.0, 99.0], index=pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])
    )
    frame = est.close_only_frame(closes)
    assert frame["rs"].isna().all()
    assert frame["proxy_r2"].iloc[0] == pytest.approx(math.log(1.01) ** 2)
