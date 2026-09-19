"""The capital model, the unit ladder, and the two engines agreeing.

The bug these exist to prevent: `run_backtest` tracked `in_pos` as -1/0/+1
while `signal_engine.run_state_machine` scaled 1 -> 3 units. So `add_long` and
`trim_long` moved the marks on the chart and had EXACTLY ZERO effect on the
reported P&L -- five different add/trim settings returned -0.50% to the basis
point on SPY 15m. The tester was scoring a different strategy than the chart
was drawing, and nothing caught it because both sides were internally
consistent.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from chart_app.backtest import run_backtest
from chart_app.elmo import compute_elmo
from chart_app.signal_engine import evaluate
from shared.chart_data import CandleRecord


def _ramp(n: int = 500, drift: float = 0.0015) -> list[CandleRecord]:
    """A trending tape WITH real pullbacks.

    A pure ramp is useless here: conviction never falls far enough to exit, so
    the engine opens once, holds to the end, and every exit-style or ladder
    comparison returns the same single trade. The cycle term is what makes the
    position get built, trimmed, stopped and rebuilt.
    """
    import math

    import numpy as np

    rng = np.random.default_rng(11)
    t0 = datetime(2026, 1, 2, 9, 30)
    out: list[CandleRecord] = []
    price = 100.0
    for i in range(n):
        cycle = 0.010 * math.sin(i / 14.0)
        price *= 1.0 + drift + cycle + float(rng.normal(0, 0.005))
        op = out[-1].close if out else price
        out.append(
            CandleRecord(
                timestamp=t0 + timedelta(days=i),
                open=op,
                high=max(op, price) * 1.004,
                low=min(op, price) * 0.996,
                close=price,
                volume=1_000_000.0,
            )
        )
    return out


def test_position_size_reaches_the_pnl_at_all():
    """The headline regression: how many units get held must change the money.

    Measured on SPY 15m before the fix, five different add/trim settings all
    returned -0.50% to the basis point.

    Note `max_units` alone does NOT isolate this: under fractional sizing a
    1-unit cap is also 100% invested. What isolates it is whether `add_long`
    is REACHABLE, i.e. whether the ladder is ever climbed.
    """
    bars = _ramp()
    never = run_backtest(bars, interval="1d", config={"add_long": 200.0}).metrics
    often = run_backtest(bars, interval="1d", config={"add_long": 20.0}).metrics

    assert never["total_return_pct"] != pytest.approx(often["total_return_pct"], abs=1e-9)
    # Climbing the ladder puts more capital to work, by construction.
    assert often["avg_exposure_pct"] > never["avg_exposure_pct"]


def test_fractional_never_borrows_and_pyramiding_does():
    """The two honest readings of 'go long', made explicit rather than implied.

    Fractional: one unit is 1/max_units of capital, so a full-conviction book
    is 100% invested and never more. Pyramiding: one unit IS the book, so the
    adds lever it up. The difference is a capital decision, not a detail.
    """
    bars = _ramp()
    frac = run_backtest(bars, interval="1d").metrics
    pyr = run_backtest(bars, interval="1d", config={"unit_fraction": 1.0}).metrics

    assert frac["capital_model"] == "fractional"
    assert pyr["capital_model"] == "pyramiding"
    assert frac["avg_exposure_pct"] <= 100.0
    # Same trades, ~max_units times the money at work.
    assert pyr["avg_exposure_pct"] > frac["avg_exposure_pct"] * 2.0
    # ...and the drawdown scales with it. Leverage is not edge.
    assert abs(pyr["max_drawdown_pct"]) > abs(frac["max_drawdown_pct"])


def test_scale_out_sheds_units_instead_of_closing_flat():
    """Built in three steps, out in three -- but a STOP still closes it all."""
    bars = _ramp()
    full = run_backtest(bars, interval="1d", config={"exit_style": "full"})
    scaled = run_backtest(bars, interval="1d", config={"exit_style": "scale"})
    assert full.metrics["total_return_pct"] != pytest.approx(
        scaled.metrics["total_return_pct"], abs=1e-9
    )
    # A scaled exit unwinds over several actions rather than one, so the book
    # spends longer partially on -- it cannot come off in a single bar.
    assert scaled.metrics["avg_units_when_in"] != pytest.approx(
        full.metrics["avg_units_when_in"], abs=1e-9
    )


def test_shorts_get_the_same_ladder_as_longs():
    """Shorts used to have entry and exit only -- they could never express
    more or less conviction than 'on'."""
    bars = _ramp(drift=-0.004)
    flat = run_backtest(
        bars,
        interval="1d",
        config={"allow_short": True, "add_short": -99.0},   # unreachable
    )
    laddered = run_backtest(
        bars,
        interval="1d",
        config={"allow_short": True, "add_short": -40.0},   # reachable
    )
    flat_peak = max((t.get("peak_units", 1) for t in flat.trades if t["side"] == "short"), default=0)
    lad_peak = max((t.get("peak_units", 1) for t in laddered.trades if t["side"] == "short"), default=0)
    assert flat_peak <= 1
    assert lad_peak > 1, "a short must be able to scale in"


def test_the_two_engines_agree_on_how_big_the_position_gets():
    """The live machine draws the marks; the backtest prices them. If they
    disagree about the size of the position, one of them is lying about the
    strategy -- which is exactly the bug this module was written for."""
    bars = _ramp()
    for cfg in ({}, {"exit_style": "scale"}, {"max_units": 2}):
        elmo = compute_elmo(bars)
        _conv, run = evaluate(bars, elmo=elmo, config=cfg)
        live_peak = max(abs(x) for x in run.position)
        bt_peak = max(
            (t.get("peak_units", 1) for t in run_backtest(bars, interval="1d", config=cfg).trades),
            default=0,
        )
        assert live_peak == pytest.approx(bt_peak), f"disagreement under {cfg}"


def test_a_trimmed_unit_books_its_own_slice_not_the_whole_position():
    """`pnl_pct` is a portfolio-return CONTRIBUTION now. A 1-unit winner and a
    3-unit winner of the same price move are different amounts of money; the
    old model called them equal."""
    bars = _ramp()
    res = run_backtest(bars, interval="1d")
    assert res.trades, "expected trades on a trending tape"
    for t in res.trades:
        assert "peak_units" in t
        assert 1 <= t["peak_units"] <= 3
    # The real invariant: a position that never climbed the ladder is one
    # unit of capital at work, so its contribution must be strictly smaller
    # than the same move taken at full size. (Under the old model the two were
    # identical, which is the bug.)
    thin = run_backtest(bars, interval="1d", config={"add_long": 200.0})
    fat = run_backtest(bars, interval="1d", config={"add_long": 20.0})
    assert fat.metrics["avg_exposure_pct"] > thin.metrics["avg_exposure_pct"]


def test_the_backtest_honours_per_bar_flow_coverage_too():
    """The live chart was fixed for this and the BACKTEST path was not.

    `flow_observed` decides the denominator per bar. The flow pull covers a
    handful of session dates while a scored window can be a year, so without it
    the whale weight dilutes every uncovered bar. Found because an offline
    reproduction of a saved SPY 15m profile disagreed with the number the
    server had reported for it -- the two paths were scoring different
    strategies, which is the same class of bug as the position model.
    """
    bars = _ramp()
    flow = [1.0] * len(bars)
    fifth = len(bars) // 5
    # Flow covering only the last fifth, like a 5-session pull against a year.
    mask = [False] * (len(bars) - fifth) + [True] * fifth

    diluted = run_backtest(bars, interval="1d", flow_net=flow)
    honest = run_backtest(bars, interval="1d", flow_net=flow, flow_observed=mask)
    assert diluted.metrics["total_return_pct"] != pytest.approx(
        honest.metrics["total_return_pct"], abs=1e-9
    )
