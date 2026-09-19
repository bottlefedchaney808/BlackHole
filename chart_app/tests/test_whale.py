"""Whale binning, magnitude, adaptive threshold and coverage reporting.

These cover the three changes made after "whale symbols hardly ever show up":
bisect binning, per-bar magnitude instead of a bit, and a threshold that
adapts to the tape instead of a flat $25k.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from chart_app.flow_stamp import (
    MIN_ADAPTIVE_MARKS,
    WHALE_PREMIUM,
    bar_bounds,
    resolve_min_premium,
    stamp_whale,
    whale_bars,
    whale_coverage,
)
from shared.chart_data import CandleRecord


def _bars(n, start=datetime(2026, 8, 18, 9, 30), minutes=15):
    return [
        CandleRecord(start + timedelta(minutes=minutes * i), 1, 1, 1, 1, 1)
        for i in range(n)
    ]


def _trade(ts, premium, right="C"):
    return {"datetime": ts, "premium": premium, "trade_right": right}


# ---------------------------------------------------------------- binning


def test_bar_bounds_marks_the_first_bar_of_each_session():
    day1 = _bars(3)
    day2 = _bars(2, start=datetime(2026, 8, 19, 9, 30))
    bounds = bar_bounds(day1 + day2)
    assert [b[2] for b in bounds] == [True, False, False, True, False]


def test_a_print_lands_on_the_bar_that_closes_after_it():
    bars = _bars(3)
    out = whale_bars(bars, [_trade("2026-08-18T09:40:00", 30_000)], min_premium=25_000)
    assert [b["count"] for b in out] == [0, 1, 0]


def test_a_print_exactly_on_a_bar_timestamp_lands_in_that_bar():
    bars = _bars(3)
    out = whale_bars(bars, [_trade("2026-08-18T09:45:00", 30_000)], min_premium=25_000)
    assert [b["count"] for b in out] == [0, 1, 0]


def test_an_overnight_print_belongs_to_the_session_that_follows():
    """`flow_pane` was session-gap aware and `flow_stamp` was not, so a print
    in the gap counted on two different bars for the dot and the pane."""
    bars = _bars(2) + _bars(2, start=datetime(2026, 8, 19, 9, 30))
    out = whale_bars(
        bars, [_trade("2026-08-18T20:00:00", 30_000)], min_premium=25_000
    )
    assert [b["count"] for b in out] == [0, 0, 0, 0]  # in the gap, before 09:30


def test_a_print_outside_the_window_is_dropped():
    bars = _bars(3)
    out = whale_bars(bars, [_trade("2030-01-01T10:00:00", 99_999)], min_premium=25_000)
    assert sum(b["count"] for b in out) == 0


def test_an_untimestamped_print_is_attributed_to_the_first_bar():
    """The provider was asked for exactly this window, so an unparsable
    timestamp is still a real print inside it."""
    bars = _bars(3)
    out = whale_bars(bars, [{"premium": 50_000}], min_premium=25_000)
    assert out[0]["count"] == 1


def test_binning_matches_a_brute_force_scan():
    """The bisect rewrite must be behaviour-identical to the O(T x B) loop."""
    bars = _bars(40)
    trades = [
        _trade((datetime(2026, 8, 18, 9, 30) + timedelta(minutes=7 * k)).isoformat(),
               30_000 + k)
        for k in range(60)
    ]
    fast = [b["count"] for b in whale_bars(bars, trades, min_premium=25_000)]

    bounds = bar_bounds(bars)
    slow = [0] * len(bars)
    for t in trades:
        ts = datetime.fromisoformat(t["datetime"])
        for i, (start, end, first) in enumerate(bounds):
            inside = start <= ts <= end if first else start < ts <= end
            if inside:
                slow[i] += 1
                break
    assert fast == slow


# -------------------------------------------------------------- magnitude


def test_whale_bars_carry_size_and_the_call_put_split():
    bars = _bars(2)
    trades = [
        _trade("2026-08-18T09:40:00", 100_000, "C"),
        _trade("2026-08-18T09:41:00", 400_000, "C"),
        _trade("2026-08-18T09:42:00", 250_000, "P"),
    ]
    out = whale_bars(bars, trades, min_premium=25_000)
    bar = out[1]
    assert bar["count"] == 3
    assert bar["max_premium"] == 400_000
    assert bar["total_premium"] == 750_000
    assert bar["call_premium"] == 500_000
    assert bar["put_premium"] == 250_000
    assert bar["net_premium"] == 250_000


def test_stamp_whale_is_the_boolean_view_of_whale_bars():
    bars = _bars(3)
    trades = [_trade("2026-08-18T09:40:00", 30_000)]
    assert stamp_whale(bars, trades, min_premium=25_000) == [
        b["count"] > 0 for b in whale_bars(bars, trades, min_premium=25_000)
    ]


def test_a_bar_with_no_qualifying_print_is_zeros_not_a_gap():
    out = whale_bars(_bars(2), [], min_premium=25_000)
    assert all(b["count"] == 0 and b["max_premium"] == 0.0 for b in out)


# -------------------------------------------------------------- threshold


def test_fixed_mode_returns_the_floor():
    trades = [_trade("2026-08-18T09:40:00", 10_000_000)]
    assert resolve_min_premium(trades, mode="fixed") == WHALE_PREMIUM


def test_adaptive_never_returns_below_the_floor():
    """A dead tape must not manufacture a whale out of a $900 print."""
    trades = [_trade("2026-08-18T09:40:00", 900) for _ in range(500)]
    assert resolve_min_premium(trades, mode="adaptive", bars=100) == WHALE_PREMIUM


def test_adaptive_targets_a_mark_density_on_a_busy_tape():
    """On SPY, a flat $25k clears ~9,500 prints a day -- a dot on every bar.
    The adaptive threshold must cut that to roughly one mark per two bars."""
    trades = [
        _trade("2026-08-18T09:40:00", float(p))
        for p in range(30_000, 30_000 + 4000)
    ]
    bars = 100
    threshold = resolve_min_premium(trades, mode="adaptive", bars=bars)
    marked = sum(1 for t in trades if t["premium"] >= threshold)
    assert marked == pytest.approx(bars * 0.5, abs=2)
    assert threshold > WHALE_PREMIUM


def test_adaptive_falls_back_to_the_floor_on_a_thin_tape():
    trades = [_trade("2026-08-18T09:40:00", 50_000) for _ in range(3)]
    assert resolve_min_premium(trades, mode="adaptive", bars=200) == WHALE_PREMIUM


def test_adaptive_has_a_minimum_mark_count():
    trades = [_trade("2026-08-18T09:40:00", float(30_000 + p)) for p in range(200)]
    threshold = resolve_min_premium(trades, mode="adaptive", bars=2)
    marked = sum(1 for t in trades if t["premium"] >= threshold)
    assert marked >= MIN_ADAPTIVE_MARKS


def test_empty_tape_returns_the_floor():
    assert resolve_min_premium([], mode="adaptive", bars=50) == WHALE_PREMIUM
    assert resolve_min_premium(None, mode="adaptive", bars=50) == WHALE_PREMIUM


# --------------------------------------------------------------- coverage


def test_coverage_reports_a_partly_stamped_chart():
    """The readout that makes a dark WH leg explainable: the flow provider is
    capped at a few session dates while the chart shows many more."""
    bars = []
    for day in range(5):
        bars += _bars(3, start=datetime(2026, 8, 17 + day, 9, 30))
    trades = [_trade("2026-08-21T09:40:00", 50_000)]
    cov = whale_coverage(bars, trades)
    assert cov["sessions"] == 5
    assert cov["covered"] == 1
    assert cov["partial"] is True
    assert cov["last_covered"] == "2026-08-21"


def test_coverage_is_not_partial_when_every_session_is_stamped():
    bars = _bars(3) + _bars(3, start=datetime(2026, 8, 19, 9, 30))
    trades = [
        _trade("2026-08-18T09:40:00", 50_000),
        _trade("2026-08-19T09:40:00", 50_000),
    ]
    cov = whale_coverage(bars, trades)
    assert cov["covered"] == cov["sessions"] == 2
    assert cov["partial"] is False


def test_coverage_on_an_empty_chart_is_not_partial():
    cov = whale_coverage([], [])
    assert cov["sessions"] == 0 and cov["partial"] is False
