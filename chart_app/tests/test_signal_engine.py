"""The conviction line and the position state machine.

The single most important test in this file is `test_conviction_is_causal`.
Every other result in the repo -- the sweep, the walk-forward, the
permutation test -- is worthless if any component can see a future bar.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta

import numpy as np
import pytest

from chart_app.elmo import compute_elmo
from chart_app.signal_engine import (
    DEFAULTS,
    WEIGHTS,
    adx_series,
    atr_series,
    conviction_series,
    evaluate,
    rsi_series,
    run_state_machine,
)
from shared.chart_data import CandleRecord


def _bars(closes, volumes=None, minutes=15):
    t0 = datetime(2026, 1, 2, 9, 30)
    out = []
    for i, c in enumerate(closes):
        c = float(c)
        out.append(
            CandleRecord(
                timestamp=t0 + timedelta(minutes=minutes * i),
                open=float(closes[i - 1]) if i else c,
                high=c * 1.004,
                low=c * 0.996,
                close=c,
                volume=1_000_000.0 if volumes is None else volumes[i],
            )
        )
    return out


def _walk(n, drift, sigma, seed):
    rng = np.random.default_rng(seed)
    return list(100 * np.exp(np.cumsum(rng.normal(drift, sigma, n))))


# ------------------------------------------------------------- indicators


def test_atr_is_positive_and_warms_up():
    atr = atr_series(_bars(_walk(80, 0.0, 0.01, 1)), period=14)
    assert all(v is None for v in atr[:14])
    assert all(v > 0 for v in atr[14:])


def test_adx_is_bounded_and_di_split_is_sane():
    adx, pdi, mdi = adx_series(_bars(_walk(200, 0.002, 0.005, 2)), period=14)
    vals = [v for v in adx if v is not None]
    assert vals and all(0.0 <= v <= 100.0 for v in vals)
    # A persistent uptrend must leave +DI above -DI at the end.
    assert pdi[-1] > mdi[-1]


def test_rsi_is_bounded_and_pins_at_100_on_an_all_up_window():
    rsi = rsi_series([100.0 + i for i in range(40)], period=14)
    vals = [v for v in rsi if v is not None]
    assert all(0.0 <= v <= 100.0 for v in vals)
    assert vals[-1] == pytest.approx(100.0)


# ------------------------------------------------------------- conviction


def test_conviction_is_causal():
    """Truncating the input must not change any earlier value.

    This is the property that makes `backtest.py` mean anything. Any
    component that peeks ahead -- a centred moving average, a full-series
    percentile, a normalisation by the global max -- shows up here.
    """
    closes = _walk(400, 0.001, 0.008, 3)
    bars = _bars(closes)
    flow = [float(v) for v in np.random.default_rng(4).normal(0, 5000, 400)]

    full = conviction_series(
        bars, elmo=compute_elmo(bars), flow_net=flow
    ).score
    cut = 260
    prefix_bars = bars[:cut]
    prefix = conviction_series(
        prefix_bars, elmo=compute_elmo(prefix_bars), flow_net=flow[:cut]
    ).score

    assert len(prefix) == cut
    for i in range(cut):
        assert full[i] == pytest.approx(prefix[i], abs=1e-9), f"bar {i} moved"


def test_conviction_is_bounded():
    bars = _bars(_walk(300, 0.003, 0.01, 5))
    score = conviction_series(bars, elmo=compute_elmo(bars)).score
    assert all(-100.0 <= v <= 100.0 for v in score)


def test_conviction_is_positive_in_an_uptrend_and_negative_in_a_downtrend():
    up = _bars(_walk(400, 0.004, 0.004, 6))
    down = _bars(_walk(400, -0.004, 0.004, 6))
    up_score = float(np.median(conviction_series(up, elmo=compute_elmo(up)).score[220:]))
    dn_score = float(np.median(conviction_series(down, elmo=compute_elmo(down)).score[220:]))
    assert up_score > 15.0, up_score
    assert dn_score < -15.0, dn_score


def test_missing_flow_shrinks_the_denominator_rather_than_diluting_the_score():
    """The regression that made entry thresholds unreachable.

    With whale's 22 points left in the divisor and nothing in the numerator,
    the achievable score was capped near 44 and a +35 entry never fired. A
    component with NO INPUT must leave the denominator.
    """
    bars = _bars(_walk(400, 0.004, 0.004, 7))
    elmo = compute_elmo(bars)
    no_flow = conviction_series(bars, elmo=elmo, flow_net=None)
    assert no_flow.available["whale"] is False
    assert no_flow.available["breakout"] is True   # zero reading, not missing

    with_flow = conviction_series(bars, elmo=elmo, flow_net=[0.0] * 400)
    assert with_flow.available["whale"] is True
    # Identical components except whale, which is flat zero with the flow
    # supplied -- so the no-flow run must read STRONGER, not weaker.
    assert abs(no_flow.score[-1]) > abs(with_flow.score[-1])


def test_breakout_component_only_fires_on_a_squeeze_release():
    bars = _bars(_walk(400, 0.0, 0.006, 8))
    comp = conviction_series(bars, elmo=compute_elmo(bars)).components["breakout"]
    assert any(v != 0 for v in comp)
    assert sum(1 for v in comp if v != 0) < len(comp) * 0.5


def test_weights_are_the_only_scale_knob():
    assert sum(WEIGHTS.values()) == pytest.approx(100.0)


def test_trend_still_votes_on_a_1h_length_tape_that_cannot_seed_ema200():
    """SPY 1h with the 30d provider cap is ~162 bars. EMA200 never defines,
    and the trend stack used to drop its 0.3 close-vs-slow term invisibly,
    capping the stack at 0.7.

    A monotonic ramp (every EMA aligned, ADX pinned high) on that length
    must sit in the same range as a 250-bar ramp that *does* seed EMA200,
    not ~70% of it. Use a ramp rather than a random walk so the two last
    bars are comparable; a walk of 162 vs 250 with the same seed ends on
    different bars and can pass by luck.
    """
    short = _bars([100.0 + i * 0.4 for i in range(162)])
    long = _bars([100.0 + i * 0.4 for i in range(250)])
    short_trend = conviction_series(short, elmo=compute_elmo(short)).components["trend"][-1]
    long_trend = conviction_series(long, elmo=compute_elmo(long)).components["trend"][-1]
    assert short_trend > 0.5, short_trend
    assert long_trend > 0.5, long_trend
    assert short_trend == pytest.approx(long_trend, rel=0.15)


def test_trend_and_ready_work_on_a_4h_length_tape():
    """4h with the 30d cap is ~42 bars: EMA50 never defines, so the whole
    trend stack used to stay at 0 and `ready` stayed False -- the state
    machine could not fire on that timeframe at all.
    """
    bars = _bars(_walk(42, 0.004, 0.003, 21))
    conv = conviction_series(bars, elmo=compute_elmo(bars))
    assert conv.ready[-1] is True
    assert conv.components["trend"][-1] != 0.0


# ---------------------------------------------------------- state machine


def test_no_add_or_sell_without_a_position_first():
    bars = _bars(_walk(400, 0.003, 0.008, 9))
    conv = conviction_series(bars, elmo=compute_elmo(bars))
    run = run_state_machine(bars, conv)
    qty = 0
    for i, action in enumerate(run.actions):
        if action == "buy":
            assert qty == 0, f"buy while already long at {i}"
            qty = 1
        elif action in ("add", "trim"):
            assert qty > 0, f"{action} while flat at {i}"
        elif action == "sell":
            assert qty > 0, f"sell while flat at {i}"
            qty = 0


def test_actions_are_transitions_not_a_state_on_every_bar():
    """The old gate printed `hold` on every qualifying bar, which buried the
    two marks that mattered under a dotted line."""
    bars = _bars(_walk(500, 0.002, 0.008, 10))
    run = evaluate(bars, elmo=compute_elmo(bars))[1]
    acted = sum(1 for a in run.actions if a != "none")
    assert 0 < acted < len(bars) * 0.1


def test_atr_stop_closes_a_long_on_a_collapse():
    """Ramp up hard enough to trigger an entry, then fall off a cliff."""
    closes = [100.0 + i * 0.9 for i in range(260)] + [
        360.0 - i * 6.0 for i in range(40)
    ]
    bars = _bars(closes)
    conv = conviction_series(bars, elmo=compute_elmo(bars))
    run = run_state_machine(bars, conv)
    assert "buy" in run.actions
    assert "sell" in run.actions
    closed = [t for t in run.trades if t.closed]
    assert closed
    assert any(t.exit_reason in ("atr_stop", "score_exit") for t in closed)


def test_trailing_stop_only_ratchets_upward_while_long():
    closes = [100.0 + i * 0.8 for i in range(320)]
    bars = _bars(closes)
    run = run_state_machine(bars, conviction_series(bars, elmo=compute_elmo(bars)))
    prev = None
    for i, qty in enumerate(run.position):
        if qty > 0 and run.stop[i] is not None:
            if prev is not None:
                assert run.stop[i] >= prev - 1e-9, f"stop loosened at {i}"
            prev = run.stop[i]
        else:
            prev = None


def test_cooldown_blocks_back_to_back_entries():
    bars = _bars(_walk(500, 0.001, 0.02, 11))
    cfg = {**DEFAULTS, "cooldown_bars": 10}
    run = run_state_machine(bars, conviction_series(bars, elmo=compute_elmo(bars), config=cfg), config=cfg)
    entries = [i for i, a in enumerate(run.actions) if a == "buy"]
    # An entry may follow a stop immediately (the stop resets the clock), so
    # only assert the gap between two ENTRIES with no exit in between.
    for a, b in itertools.pairwise(entries):
        assert any(run.actions[j] in ("sell", "trim") for j in range(a, b))


def test_shorts_are_off_by_default():
    bars = _bars(_walk(400, -0.004, 0.006, 12))
    run = evaluate(bars, elmo=compute_elmo(bars))[1]
    assert "short" not in run.actions
    run_short = evaluate(
        bars, elmo=compute_elmo(bars), config={"allow_short": True}
    )[1]
    assert "short" in run_short.actions


def test_trade_pnl_sign_matches_direction():
    bars = _bars(_walk(500, 0.002, 0.01, 13))
    run = evaluate(bars, elmo=compute_elmo(bars))[1]
    for trade in run.trades:
        if not trade.closed:
            continue
        expected = trade.exit_price > trade.entry_price
        assert (trade.pnl_pct() > 0) == expected


def test_empty_input_is_handled():
    conv, run = evaluate([], elmo=None)
    assert conv.score == [] and run.actions == []


def test_flow_observed_shrinks_the_denominator_per_bar_not_per_series():
    """§3.4's rule, one granularity down -- the regression Grok's lookback
    fix exposed.

    `available` is series-wide, which was right only while the flow window and
    the chart window were the same length. Once 15m spanned 20d (14 sessions)
    with the flow pull still capped at 5 session dates, whale's 22 points sat
    in the divisor for 71% of bars that had no flow at all. Measured on SPY
    2026-09-18: 252 bars understated by 28.2%, 31 across an entry/exit level.
    """
    bars = _bars(_walk(400, 0.004, 0.004, 7))
    elmo = compute_elmo(bars)
    flow = [1.0] * 400

    # Flow present on the last 100 bars only, exactly like a 5-session pull
    # against a 14-session chart.
    mask = [False] * 300 + [True] * 100
    partial = conviction_series(
        bars, elmo=elmo, flow_net=flow, flow_observed=mask
    )
    # The series-wide summary still says flow arrived...
    assert partial.available["whale"] is True
    # ...but the per-bar provenance is honest about where.
    assert partial.flow_observed == mask

    # A dark bar must score as if whale were never a component: identical to a
    # run with no flow at all.
    no_flow = conviction_series(bars, elmo=elmo, flow_net=None)
    assert partial.score[200] == pytest.approx(no_flow.score[200], abs=1e-9)
    # And a covered bar must NOT: it has the whale weight in play.
    assert partial.score[-1] != pytest.approx(no_flow.score[-1], abs=1e-9)


def test_a_covered_bar_reading_zero_premium_stays_in_the_denominator():
    """The distinction the mask exists to preserve.

    `flow_observed` is keyed on session dates the provider answered for, never
    on `net_premium != 0`. A covered session with no qualifying print is a real
    measurement reading zero and must stay in the divisor -- dropping it would
    reintroduce §3.4 by rewarding quiet tape with a higher score.
    """
    bars = _bars(_walk(400, 0.004, 0.004, 7))
    elmo = compute_elmo(bars)
    zero_flow = [0.0] * 400

    covered = conviction_series(
        bars, elmo=elmo, flow_net=zero_flow, flow_observed=[True] * 400
    )
    dark = conviction_series(
        bars, elmo=elmo, flow_net=zero_flow, flow_observed=[False] * 400
    )
    # Same zero numerator, different divisor -> the dark run must read
    # STRONGER. If these are equal the mask is keying on premium, not coverage.
    assert abs(dark.score[-1]) > abs(covered.score[-1])


def test_omitting_flow_observed_keeps_the_series_wide_behaviour():
    """Backtests pass no mask and must be unaffected -- their calibration is
    the one the shipped defaults were measured against."""
    bars = _bars(_walk(400, 0.004, 0.004, 7))
    elmo = compute_elmo(bars)
    flow = [1.0] * 400
    legacy = conviction_series(bars, elmo=elmo, flow_net=flow)
    explicit = conviction_series(
        bars, elmo=elmo, flow_net=flow, flow_observed=[True] * 400
    )
    assert legacy.score == pytest.approx(explicit.score)
    assert legacy.flow_observed == [True] * 400


def test_weights_are_overridable_so_they_can_be_measured():
    """Weights are not a UI lever -- they are a seam for testing.

    They were hand-picked and unmeasurable for the life of the app. Sweeping
    `elmo` across 11 cached series (2026-09-19) is what established that 18 is
    right and that raising it is monotonically worse; that check is only
    possible because the weights can be overridden per run.
    """
    bars = _bars(_walk(400, 0.004, 0.004, 7))
    elmo = compute_elmo(bars)
    base = conviction_series(bars, elmo=elmo)
    heavy = conviction_series(bars, elmo=elmo, config={"weights": {"elmo": 45.0}})
    assert base.score != pytest.approx(heavy.score)


def test_a_typo_in_a_weight_name_is_refused_not_ignored():
    """Silently ignoring `elmoo` would evaluate the shipped weights and report
    the result as though the override had applied -- a sweep would then 'find'
    that the change did nothing."""
    from chart_app.signal_engine import resolve_weights

    with pytest.raises(ValueError):
        resolve_weights({"weights": {"elmoo": 30.0}})
    assert resolve_weights({"weights": {"elmo": 30.0}})["elmo"] == 30.0
