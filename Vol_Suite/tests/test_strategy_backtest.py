"""
Regression test for backtest_stage3.py's general strategy P&L backtester
(run_strategy_backtest / _run_strategy_backtest_from_history), added
alongside (and independent of) the Stage 3 dealer-gamma-sign validation
backtest that tests/test_backtest_stage3.py covers.

Network-free throughout: builds synthetic entry_prices/exit_prices maps by
hand (no ThetaDataController) and calls _run_strategy_backtest_from_history
directly, same pattern test_backtest_stage3.py uses for
_run_backtest_from_history.
"""
import math

import pytest

import backtest_stage3 as bt3


def _strategy(strategy_type, legs):
    """A minimal strategy dict matching the _strategy_to_dict /
    suite_context.json "strategies" schema shape: strategy_type + legs,
    where each leg carries instrument_type/strike/quantity.
    """
    return {
        "strategy_type": strategy_type,
        "legs": legs,
        "vol_regime": "FAIR",
        "rationale": "synthetic test strategy",
        "edge_strikes_used": [],
        "greeks_summary": {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "vanna": 0.0},
        "rank_score": 0.0,
    }


def _leg(instrument_type, strike, quantity):
    return {"instrument_type": instrument_type, "strike": strike, "quantity": quantity}


# ---------------------------------------------------------------------------
# Long single leg, exit before expiration off a market quote
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_long_call_pnl_before_expiration_uses_market_price():
    strategy = _strategy("long_call", [_leg("call", 100.0, 1)])
    entry_prices = {(100.0, "call"): 5.0}
    exit_prices = {(100.0, "call"): 8.0}

    result = bt3._run_strategy_backtest_from_history(
        strategy, "20260101", "20260115", "20260201",
        entry_prices, exit_prices,
    )

    assert result.entry_cost == pytest.approx(500.0)   # 1 * 5.0 * 100
    assert result.exit_value == pytest.approx(800.0)    # 1 * 8.0 * 100
    assert result.pnl == pytest.approx(300.0)
    assert result.pnl_pct == pytest.approx(60.0)
    assert result.strategy_type == "long_call"
    assert result.entry_date == "20260101"
    assert result.exit_date == "20260115"


@pytest.mark.unit
def test_short_call_credit_and_loss_sign():
    """Selling a call is a credit at entry (negative entry_cost); if the
    option is worth MORE at exit, the short loses money -- pnl must come
    out negative, not just "smaller".
    """
    strategy = _strategy("short_call", [_leg("call", 100.0, -1)])
    entry_prices = {(100.0, "call"): 5.0}
    exit_prices = {(100.0, "call"): 8.0}

    result = bt3._run_strategy_backtest_from_history(
        strategy, "20260101", "20260115", "20260201",
        entry_prices, exit_prices,
    )

    assert result.entry_cost == pytest.approx(-500.0)  # credit received
    assert result.exit_value == pytest.approx(-800.0)   # cost to buy back
    assert result.pnl == pytest.approx(-300.0)          # loss
    assert result.pnl_pct == pytest.approx(-60.0)


# ---------------------------------------------------------------------------
# Multi-leg: call spread, both legs priced, net debit/credit nets correctly
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_call_spread_nets_both_legs():
    # Long the 100 call, short the 110 call -- classic debit call spread.
    strategy = _strategy("call_spread", [
        _leg("call", 100.0, 1),
        _leg("call", 110.0, -1),
    ])
    entry_prices = {(100.0, "call"): 6.0, (110.0, "call"): 2.0}
    exit_prices = {(100.0, "call"): 9.0, (110.0, "call"): 3.0}

    result = bt3._run_strategy_backtest_from_history(
        strategy, "20260101", "20260115", "20260201",
        entry_prices, exit_prices,
    )

    expected_entry_cost = (1 * 6.0 + (-1) * 2.0) * 100.0   # net debit 400
    expected_exit_value = (1 * 9.0 + (-1) * 3.0) * 100.0    # net value 600
    assert result.entry_cost == pytest.approx(expected_entry_cost)
    assert result.exit_value == pytest.approx(expected_exit_value)
    assert result.pnl == pytest.approx(expected_exit_value - expected_entry_cost)


# ---------------------------------------------------------------------------
# Held to expiration -- intrinsic value payoff off exit_spot, not a market
# quote (there is none for a dead contract).
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_expired_call_uses_intrinsic_value_itm():
    strategy = _strategy("long_call", [_leg("call", 100.0, 1)])
    entry_prices = {(100.0, "call"): 5.0}
    # exit_date == expiry -> expired; no exit_prices needed, only exit_spot.
    result = bt3._run_strategy_backtest_from_history(
        strategy, "20260101", "20260201", "20260201",
        entry_prices, exit_prices={}, exit_spot=112.0,
    )
    # Intrinsic = max(112 - 100, 0) = 12
    assert result.exit_value == pytest.approx(1200.0)
    assert result.pnl == pytest.approx(1200.0 - 500.0)


@pytest.mark.unit
def test_expired_put_otm_expires_worthless():
    strategy = _strategy("long_put", [_leg("put", 100.0, 1)])
    entry_prices = {(100.0, "put"): 4.0}
    result = bt3._run_strategy_backtest_from_history(
        strategy, "20260101", "20260201", "20260201",
        entry_prices, exit_prices={}, exit_spot=110.0,   # spot above strike -> put OTM
    )
    assert result.exit_value == pytest.approx(0.0)
    assert result.pnl == pytest.approx(-400.0)


@pytest.mark.unit
def test_expired_iron_condor_all_legs_intrinsic():
    strategy = _strategy("iron_condor", [
        _leg("call", 110.0, -1),
        _leg("call", 115.0, 1),
        _leg("put", 90.0, -1),
        _leg("put", 85.0, 1),
    ])
    entry_prices = {
        (110.0, "call"): 2.0, (115.0, "call"): 0.5,
        (90.0, "put"): 2.0, (85.0, "put"): 0.5,
    }
    # Spot settles at 100 -- squarely between the short strikes, every leg
    # expires worthless, so exit_value should be exactly 0 and pnl should
    # equal the net credit collected at entry.
    result = bt3._run_strategy_backtest_from_history(
        strategy, "20260101", "20260201", "20260201",
        entry_prices, exit_prices={}, exit_spot=100.0,
    )
    net_credit = (-1 * 2.0 + 1 * 0.5 + -1 * 2.0 + 1 * 0.5) * 100.0
    assert result.exit_value == pytest.approx(0.0)
    assert result.entry_cost == pytest.approx(net_credit)
    assert result.pnl == pytest.approx(0.0 - net_credit)


# ---------------------------------------------------------------------------
# Exit date past expiry (e.g. caller passed a date after the option died)
# should still be treated as "expired" and priced off intrinsic value.
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_exit_date_after_expiry_still_uses_intrinsic():
    strategy = _strategy("long_call", [_leg("call", 100.0, 1)])
    entry_prices = {(100.0, "call"): 5.0}
    result = bt3._run_strategy_backtest_from_history(
        strategy, "20260101", "20260210", "20260201",  # exit_date > expiry
        entry_prices, exit_prices={}, exit_spot=105.0,
    )
    assert result.exit_value == pytest.approx(500.0)  # max(105-100,0)*100


# ---------------------------------------------------------------------------
# Error handling: missing data must raise, never silently default/zero-fill
# (same discipline as _build_day_records' "unlabeled, not zero-filled" rule).
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_missing_entry_price_raises():
    strategy = _strategy("long_call", [_leg("call", 100.0, 1)])
    with pytest.raises(ValueError):
        bt3._run_strategy_backtest_from_history(
            strategy, "20260101", "20260115", "20260201",
            entry_prices={}, exit_prices={(100.0, "call"): 8.0},
        )


@pytest.mark.unit
def test_missing_exit_price_before_expiration_raises():
    strategy = _strategy("long_call", [_leg("call", 100.0, 1)])
    with pytest.raises(ValueError):
        bt3._run_strategy_backtest_from_history(
            strategy, "20260101", "20260115", "20260201",
            entry_prices={(100.0, "call"): 5.0}, exit_prices={},
        )


@pytest.mark.unit
def test_expired_leg_missing_exit_spot_raises():
    strategy = _strategy("long_call", [_leg("call", 100.0, 1)])
    with pytest.raises(ValueError):
        bt3._run_strategy_backtest_from_history(
            strategy, "20260101", "20260201", "20260201",
            entry_prices={(100.0, "call"): 5.0}, exit_prices={},
            exit_spot=None,
        )


@pytest.mark.unit
def test_empty_legs_raises():
    strategy = _strategy("empty", [])
    with pytest.raises(ValueError):
        bt3._run_strategy_backtest_from_history(
            strategy, "20260101", "20260115", "20260201", {}, {},
        )


# ---------------------------------------------------------------------------
# Report formatting sanity check
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_format_strategy_backtest_report_contains_key_fields():
    strategy = _strategy("long_call", [_leg("call", 100.0, 1)])
    result = bt3._run_strategy_backtest_from_history(
        strategy, "20260101", "20260115", "20260201",
        entry_prices={(100.0, "call"): 5.0},
        exit_prices={(100.0, "call"): 8.0},
    )
    report = bt3.format_strategy_backtest_report(result)
    assert "long_call" in report
    assert "20260101" in report and "20260115" in report
    assert "long" in report  # leg direction label
    assert not math.isnan(result.pnl_pct)
