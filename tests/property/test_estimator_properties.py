"""Property tests (Hypothesis): non-negativity and scale invariance of the estimators (§16)."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from volforecast import estimators as est

price = st.floats(min_value=1.0, max_value=1e4, allow_nan=False, allow_infinity=False)
fraction = st.floats(min_value=0.0, max_value=1.0)


@st.composite
def bars(draw: st.DrawFn) -> tuple[float, float, float, float]:
    low = draw(price)
    high = low * (1 + draw(st.floats(min_value=0.0, max_value=0.2)))
    o = low + draw(fraction) * (high - low)
    c = low + draw(fraction) * (high - low)
    return o, high, low, c


@settings(max_examples=300, deadline=None)
@given(bars())
def test_range_estimators_are_non_negative(bar: tuple[float, float, float, float]) -> None:
    o, h, lo, c = bar
    assert est.parkinson(h, lo) >= 0
    assert est.garman_klass(o, h, lo, c) >= -1e-15
    assert est.rogers_satchell(o, h, lo, c) >= 0


@settings(max_examples=300, deadline=None)
@given(bars(), st.floats(min_value=0.01, max_value=100.0))
def test_scaling_prices_leaves_estimators_unchanged(
    bar: tuple[float, float, float, float], k: float
) -> None:
    o, h, lo, c = bar
    for f in (est.garman_klass, est.rogers_satchell):
        a, b = f(o, h, lo, c), f(o * k, h * k, lo * k, c * k)
        assert abs(a - b) <= 1e-9 * max(abs(a), 1e-12) + 1e-15
    assert abs(est.parkinson(h, lo) - est.parkinson(h * k, lo * k)) <= 1e-12


@settings(max_examples=200, deadline=None)
@given(price, price, st.floats(min_value=0.01, max_value=100.0))
def test_returns_are_scale_free(close: float, prev: float, k: float) -> None:
    a = est.close_to_close_return(close, prev)
    b = est.close_to_close_return(close * k, prev * k)
    assert abs(a - b) <= 1e-12
