"""Unadjusted-split detection and back-adjustment.

Grounded in a real failure: `shared/spot_history.fetch_daily_candles` serves
unadjusted prices, and NVDA's 2024 10-for-1 shows up on a 3y window as a
genuine -89.9% session (1208.88 -> 121.79, measured 2026-09-18).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from chart_app.split_guard import back_adjust, detect_splits
from shared.chart_data import CandleRecord


def _series(closes, volumes=None):
    t0 = datetime(2024, 1, 2)
    out = []
    for i, c in enumerate(closes):
        c = float(c)
        out.append(
            CandleRecord(
                timestamp=t0 + timedelta(days=i),
                open=c * 0.999,
                high=c * 1.01,
                low=c * 0.99,
                close=c,
                volume=1000.0 if volumes is None else volumes[i],
            )
        )
    return out


def test_detects_a_ten_for_one_split():
    bars = _series([1200.0, 1208.88, 121.79, 123.0, 125.0])
    events = detect_splits(bars)
    assert len(events) == 1
    assert events[0].direction == "forward"
    assert events[0].factor == 10.0
    assert events[0].index == 2


def test_detects_a_reverse_split():
    bars = _series([2.0, 2.05, 10.25, 10.4, 10.1])
    events = detect_splits(bars)
    assert len(events) == 1
    assert events[0].direction == "reverse"
    assert events[0].factor == 5.0


def test_ignores_an_ordinary_bad_day():
    """A -20% earnings gap is not a split and must not be adjusted away."""
    bars = _series([100.0, 101.0, 81.0, 80.0, 82.0])
    assert detect_splits(bars) == []


def test_ignores_a_large_move_that_is_not_a_round_factor():
    """-45% is extreme but 1.82 is not a ratio any exchange uses."""
    bars = _series([100.0, 100.0, 55.0, 54.0, 56.0])
    assert detect_splits(bars) == []


def test_back_adjust_restates_history_in_todays_share_terms():
    bars = _series([1200.0, 1208.88, 121.79, 123.0])
    adjusted, events = back_adjust(bars)
    assert len(events) == 1
    # Pre-split bars divided by 10; post-split untouched.
    assert adjusted[0].close == pytest.approx(120.0)
    assert adjusted[1].close == pytest.approx(120.888)
    assert adjusted[2].close == pytest.approx(121.79)
    assert adjusted[3].close == pytest.approx(123.0)


def test_back_adjust_scales_volume_so_dollar_volume_stays_continuous():
    """The Hui-Heubel liquidity ratio divides by share volume, so leaving
    volume unscaled across a 10-for-1 would report a 10x liquidity cliff."""
    bars = _series([1200.0, 1208.88, 121.79], volumes=[100.0, 100.0, 1000.0])
    adjusted, _ = back_adjust(bars)
    assert adjusted[0].volume == pytest.approx(1000.0)
    assert adjusted[2].volume == pytest.approx(1000.0)
    # price * volume preserved across the boundary
    assert adjusted[0].close * adjusted[0].volume == pytest.approx(
        bars[0].close * bars[0].volume
    )


def test_back_adjust_removes_the_artificial_return():
    bars = _series([1200.0, 1208.88, 121.79, 123.0])
    adjusted, _ = back_adjust(bars)
    moves = [
        abs(adjusted[i].close / adjusted[i - 1].close - 1.0)
        for i in range(1, len(adjusted))
    ]
    assert max(moves) < 0.05


def test_two_splits_both_apply_to_the_oldest_section():
    # 10-for-1, then later 2-for-1.
    bars = _series([1000.0, 1000.0, 100.0, 100.0, 50.0, 50.0])
    adjusted, events = back_adjust(bars)
    assert len(events) == 2
    # Oldest bars carry BOTH factors: /10 then /2.
    assert adjusted[0].close == pytest.approx(50.0)
    assert adjusted[2].close == pytest.approx(50.0)
    assert adjusted[4].close == pytest.approx(50.0)


def test_no_split_returns_the_input_unchanged():
    bars = _series([100.0 + i for i in range(20)])
    adjusted, events = back_adjust(bars)
    assert events == []
    assert [b.close for b in adjusted] == [b.close for b in bars]


def test_handles_empty_and_single_bar():
    assert detect_splits([]) == []
    assert back_adjust([]) == ([], [])
    one = _series([100.0])
    assert back_adjust(one)[1] == []
