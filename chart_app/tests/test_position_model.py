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


def test_an_old_pyramiding_profile_is_financed_not_free():
    """`unit_fraction=1.0` used to carry max_units x the book with no loan and
    no interest -- each 'unit' above the first was exposure nobody paid for.
    It now resolves to a margin account: the part above equity is borrowed,
    charged interest, and capped at what Reg T lends (2x on stock)."""
    bars = _ramp()
    cash = run_backtest(bars, interval="1d", ticker="SPY").metrics
    pyr = run_backtest(bars, interval="1d", config={"unit_fraction": 1.0}, ticker="SPY").metrics

    assert cash["capital_model"] == "cash"
    # Sized to 100% at each order; a hair of drift between orders is price.
    assert cash["max_gross_exposure_pct"] <= 101.0
    assert cash["interest_dollars"] == 0.0
    assert pyr["capital_model"].startswith("margin")
    assert pyr["interest_dollars"] > 0.0
    # 3x was asked for; a stock broker lends 50%, so orders are sized to 2x.
    # (Gross drifts a little past 2x BETWEEN orders as price moves -- the cap
    # is on what an order may buy, as at a real broker, not a rebalance.)
    assert pyr["margin_capped"] is True
    assert pyr["sizing"]["margin_pct"] == pytest.approx(0.5)
    assert pyr["max_gross_exposure_pct"] < 250.0


# The new sizing model: dollars, whole shares, real margin.
_SLICED = {
    "position_size": 0.3,          # $300k of a $1M pool
    "entry_style": "scale",
    "entry_slice": 1.0 / 3.0,      # bought $100k at a time
    "exit_style": "scale",
    "exit_slice": 1.0 / 8.0,       # sold in eighths
    "add_long": 20.0,
}


def test_orders_are_whole_shares_by_default():
    res = run_backtest(_ramp(), interval="1d", config=_SLICED, ticker="SPY")
    assert res.orders
    assert all(o["qty"] == int(o["qty"]) and o["qty"] >= 1 for o in res.orders)


def test_fractional_is_an_opt_in_for_expensive_shares():
    res = run_backtest(
        _ramp(), interval="1d", config={**_SLICED, "fractional": True}, ticker="SPY"
    )
    assert any(o["qty"] != int(o["qty"]) for o in res.orders)


def test_an_entry_order_is_a_dollar_slice_of_the_position():
    """$300k position in $100k orders: each buy is $100k, less at most one share."""
    res = run_backtest(_ramp(), interval="1d", config=_SLICED, ticker="SPY")
    buys = [o for o in res.orders if o["action"] in ("buy", "add")]
    assert buys
    first = buys[0]
    assert 100_000.0 - first["price"] < first["notional"] <= 100_000.0
    # Never more than the full $300k (plus compounding) on at once.
    assert res.metrics["max_gross_exposure_pct"] < 40.0


def test_a_scale_out_sells_in_the_chunks_it_was_set_to():
    """Exit in eighths of the full position -- not one 'unit' of an unrelated ladder."""
    res = run_backtest(_ramp(), interval="1d", config=_SLICED, ticker="SPY")
    trims = [o for o in res.orders if o["action"] == "trim"]
    assert trims
    full_qty = max(t["peak_qty"] for t in res.trades)
    # An eighth of a full position, rounded down to whole shares.
    assert any(abs(o["qty"] - full_qty / 8.0) <= 1.0 for o in trims)


def test_borrowing_changes_the_financing_never_the_share_count():
    """At 50% borrowed you get the SAME shares for half the cash."""
    bars = _ramp()
    cfg = {**_SLICED, "position_size": 1.0}
    cash = run_backtest(bars, interval="1d", config=cfg, ticker="SPY")
    marg = run_backtest(bars, interval="1d", config={**cfg, "margin_pct": 0.5}, ticker="SPY")
    # The first order is placed at identical equity, so it is identical shares.
    # Later ones differ only by the interest already paid (orders size off
    # equity), which is a couple of shares, never a multiple.
    assert cash.orders[0]["qty"] == marg.orders[0]["qty"]
    for a, b in zip(cash.orders[:10], marg.orders[:10], strict=False):
        assert a["action"] == b["action"]
        assert b["qty"] == pytest.approx(a["qty"], rel=0.01)
    assert marg.orders[0]["borrowed"] == pytest.approx(marg.orders[0]["notional"] * 0.5)
    # ...and the loan costs money, so the same trades return less.
    assert marg.metrics["interest_dollars"] > 0
    assert marg.metrics["total_return_pct"] < cash.metrics["total_return_pct"]


def test_nobody_lends_more_than_80_percent():
    bars = _ramp()
    greedy = {"position_size": 1.0, "margin_pct": 0.95, "entry_style": "all"}
    perp = run_backtest(bars, interval="1d", config=greedy, ticker="BTC-PERP").metrics
    stock = run_backtest(bars, interval="1d", config=greedy, ticker="SPY").metrics
    spot = run_backtest(bars, interval="1d", config=greedy, ticker="BTC-USD").metrics
    assert perp["sizing"]["margin_pct"] == pytest.approx(0.80)
    assert stock["sizing"]["margin_pct"] == pytest.approx(0.50)   # Reg T
    assert spot["sizing"]["margin_pct"] == 0.0                    # spot crypto
    assert perp["margin_capped"] and stock["margin_capped"] and spot["margin_capped"]


def test_take_profit_banks_a_chunk_at_each_level_once():
    """'Take profit at 5% and 10%': a winner used to have no way out except the
    score turning or the trail giving back 4 ATR. Each level now sells one
    exit chunk at its price, at most once per trade."""
    bars = _ramp()
    cfg = {**_SLICED, "take_profit": "5, 10"}
    res = run_backtest(bars, interval="1d", config=cfg, ticker="SPY")
    tps = [o for o in res.orders if o["reason"] == "take_profit"]
    assert tps, "a trending tape should reach +5% at least once"
    # Never more than one fill per level per trade.
    for t in res.trades:
        in_trade = [o for o in tps if t["entry_index"] <= o["fill_index"] <= t["exit_index"]]
        assert len(in_trade) <= 2
    # It changes the result: profit taken is a different strategy.
    plain = run_backtest(bars, interval="1d", config=_SLICED, ticker="SPY")
    assert res.metrics["total_return_pct"] != pytest.approx(
        plain.metrics["total_return_pct"], abs=1e-9
    )
    # And the chart's state machine marks it too.
    _conv, run = evaluate(bars, elmo=compute_elmo(bars), config=cfg)
    assert "trim" in run.actions or any(
        t.exit_reason == "take_profit" for t in run.trades
    )


def test_a_margin_call_sells_the_book():
    """A levered long through a crash is liquidated at the maintenance line."""
    t0 = datetime(2026, 1, 2)
    bars: list[CandleRecord] = []
    price = 100.0
    for i in range(400):
        # Drift up long enough to enter, then fall well through the entry.
        price *= 1.001 if i < 250 else 0.99
        op = bars[-1].close if bars else price
        bars.append(
            CandleRecord(
                timestamp=t0 + timedelta(days=i),
                open=op,
                high=max(op, price) * 1.001,
                low=min(op, price) * 0.999,
                close=price,
                volume=1.0,
            )
        )
    cfg = {
        "position_size": 5.0,
        "margin_pct": 0.8,
        "entry_style": "all",
        "atr_stop_mult": 1000.0,   # no stop: only the margin line can save it
        "exit_long": -1000.0,      # no score exit either
        "trim_long": -1000.0,      # ...and no trims
        "entry_long": -100.0,
    }
    res = run_backtest(bars, interval="1d", config=cfg, ticker="BTC-PERP")
    assert res.metrics["margin_calls"] >= 1
    assert any(t["exit_reason"] == "margin_call" for t in res.trades)


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
