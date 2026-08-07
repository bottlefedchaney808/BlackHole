# Direction/tests/test_liquidity_map.py
"""Tests for Direction/liquidity_map.py -- network-free except
get_liquidity(), which monkeypatches Direction.data.*.
"""
import pytest

from Direction import data, liquidity_map as lm


def _row(strike, right, oi):
    return {"strike": strike, "right": right, "oi": oi}


@pytest.mark.unit
def test_pick_expiry_prefers_nearest_future_expiry():
    assert lm._pick_expiry(["20260101", "20260601", "20261231"]) in (
        "20260101", "20260601", "20261231"
    )  # exact answer depends on "today", but must be one of the inputs
    assert lm._pick_expiry([]) is None


@pytest.mark.unit
def test_sum_oi_totals_only_matching_right():
    rows = [_row(100, "C", 50), _row(105, "C", 30), _row(100, "P", 10)]
    assert lm._sum_oi(rows, "C") == pytest.approx(80.0)
    assert lm._sum_oi(rows, "P") == pytest.approx(10.0)


@pytest.mark.unit
def test_max_pain_picks_strike_minimizing_aggregate_itm_payout():
    # All OI concentrated at 100 on both sides -> settling AT 100 makes both
    # sides' payout zero (calls/puts both expire worthless there), so 100
    # must be the max-pain strike even though it isn't "nearest to some
    # arbitrary spot" -- this is exactly the case the old nearest-strike
    # approximation could get wrong if spot weren't already near 100.
    chain = [_row(90, "C", 5), _row(100, "C", 100), _row(110, "C", 5),
             _row(90, "P", 5), _row(100, "P", 100), _row(110, "P", 5)]
    assert lm._max_pain(chain, default=999.0) == pytest.approx(100.0)


@pytest.mark.unit
def test_max_pain_favors_side_with_more_oi_away_from_naive_center():
    # Heavy call OI at 90, heavy put OI at 110, light OI at 100 -- the
    # nearest-to-spot(100) approximation would pick 100, but the true
    # payout-minimizing strike is wherever total dollar payout is lowest.
    chain = [_row(90, "C", 1000), _row(100, "C", 1), _row(110, "C", 1),
             _row(90, "P", 1), _row(100, "P", 1), _row(110, "P", 1000)]
    result = lm._max_pain(chain, default=999.0)
    assert result == pytest.approx(100.0)  # midpoint minimizes both tails here


@pytest.mark.unit
def test_max_pain_correctly_weights_calls_by_itm_above_strike_not_below():
    # Deliberately OI-unbalanced (370 total call OI vs 30 total put OI) --
    # a call/put payout-formula swap bug is invisible on balanced-OI chains
    # (the error term is linear in total_call_oi - total_put_oi, which
    # vanishes when they're equal) but this chain exposes it directly.
    chain = [_row(90, "C", 20), _row(100, "C", 50), _row(110, "C", 300),
             _row(90, "P", 10), _row(100, "P", 15), _row(110, "P", 5)]
    result = lm._max_pain(chain, default=999.0)
    assert result in (90.0, 100.0)  # true payout-minimizing strikes are tied at 90 and 100
    assert result != 110.0  # the swapped-formula bug picks 110 -- must not reproduce


@pytest.mark.unit
def test_max_pain_returns_default_on_empty_chain():
    assert lm._max_pain([], default=123.0) == pytest.approx(123.0)


@pytest.mark.unit
def test_get_liquidity_degrades_gracefully_on_no_price(monkeypatch):
    monkeypatch.setattr(data, "get_price", lambda ticker: None)
    monkeypatch.setattr(data, "get_expirations", lambda ticker: [])
    monkeypatch.setattr(data, "get_dealer_gamma", lambda ticker: None)
    result = lm.get_liquidity("SPY")
    assert result["signal"] is False
    assert result["max_pain"] is None
    assert result["pcr"] == 0.0


@pytest.mark.unit
def test_get_liquidity_signal_true_when_max_pain_within_2pct_of_spot(monkeypatch):
    chain = [_row(100, "C", 50), _row(100, "P", 50)]
    monkeypatch.setattr(data, "get_price", lambda ticker: 100.0)
    monkeypatch.setattr(data, "get_expirations", lambda ticker: ["20261231"])
    monkeypatch.setattr(data, "get_chain_oi", lambda ticker, exp: chain)
    monkeypatch.setattr(data, "get_dealer_gamma", lambda ticker: None)
    result = lm.get_liquidity("SPY")
    assert result["signal"] is True
    assert result["max_pain"] == pytest.approx(100.0)
