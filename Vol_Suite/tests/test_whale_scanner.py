"""Tests for whale_scanner.py -- pure classification of large single-contract
options premium into a bullish/bearish/neutral bias for one day's chain.

Network-free: works entirely off synthetic row dicts, matching the rest of
Vol_Suite/tests/'s convention (see test_backtest_stage3.py's module
docstring for why this matters).
"""
import pytest

import whale_scanner as ws


def _row(strike, right, volume, close):
    return {"strike": strike, "right": right, "volume": volume, "close": close}


@pytest.mark.unit
def test_classify_whale_bias_bullish_on_call_heavy_premium():
    rows = [
        _row(110, 'C', 20, 15.0),   # premium = 20*15*100 = $30,000 >= $25K bar
        _row(90, 'P', 5, 10.0),     # premium = 5*10*100 = $5,000 -- filtered out
    ]
    bias, stats = ws.classify_whale_bias(rows)
    assert bias == 'bullish'
    assert stats['whale_calls'] == 1
    assert stats['whale_puts'] == 0
    assert stats['call_premium'] == pytest.approx(30_000.0)
    assert stats['put_premium'] == 0.0


@pytest.mark.unit
def test_classify_whale_bias_bearish_on_put_heavy_premium():
    rows = [
        _row(90, 'P', 20, 15.0),    # $30,000
        _row(110, 'C', 5, 10.0),    # $5,000 -- filtered out
    ]
    bias, stats = ws.classify_whale_bias(rows)
    assert bias == 'bearish'
    assert stats['whale_puts'] == 1
    assert stats['whale_calls'] == 0


@pytest.mark.unit
def test_classify_whale_bias_neutral_when_balanced():
    """Both sides clear the threshold but neither exceeds the other by the
    1.2x ratio -- neither side 'wins', so the day is neutral."""
    rows = [
        _row(110, 'C', 20, 15.0),   # $30,000
        _row(90, 'P', 20, 15.0),    # $30,000
    ]
    bias, stats = ws.classify_whale_bias(rows)
    assert bias == 'neutral'


@pytest.mark.unit
def test_classify_whale_bias_neutral_when_nothing_clears_threshold():
    rows = [_row(110, 'C', 1, 1.0)]  # premium = $100
    bias, stats = ws.classify_whale_bias(rows)
    assert bias == 'neutral'
    assert stats['whale_calls'] == 0
    assert stats['call_premium'] == 0.0


@pytest.mark.unit
def test_classify_whale_bias_neutral_on_empty_rows():
    bias, stats = ws.classify_whale_bias([])
    assert bias == 'neutral'
    assert stats['call_premium'] == 0.0
    assert stats['put_premium'] == 0.0


@pytest.mark.unit
def test_classify_whale_bias_skips_malformed_rows():
    """A row with a non-numeric volume/close must be skipped, not crash the
    whole classification."""
    rows = [
        {"strike": 110, "right": "C", "volume": "not-a-number", "close": 15.0},
        _row(110, 'C', 20, 15.0),   # the one valid row -- still $30,000, bullish
    ]
    bias, stats = ws.classify_whale_bias(rows)
    assert bias == 'bullish'
    assert stats['whale_calls'] == 1


@pytest.mark.unit
def test_effective_threshold_uses_bps_when_set_and_price_available():
    # 3000 bps = 30% of one ATM contract's notional (price * 100)
    bar = ws._effective_threshold(min_premium=25_000.0, threshold_bps=3000, price=100.0)
    assert bar == pytest.approx(3000 / 10_000.0 * 100.0 * 100.0)  # $30,000


@pytest.mark.unit
def test_effective_threshold_falls_back_to_absolute_without_price():
    bar = ws._effective_threshold(min_premium=25_000.0, threshold_bps=3000, price=None)
    assert bar == 25_000.0


@pytest.mark.unit
def test_effective_threshold_absolute_when_bps_param_none_and_env_unset(monkeypatch):
    monkeypatch.delenv("WHALE_THRESHOLD_BPS", raising=False)
    bar = ws._effective_threshold(min_premium=25_000.0, threshold_bps=None, price=100.0)
    assert bar == 25_000.0


@pytest.mark.unit
def test_effective_threshold_reads_env_when_param_is_none(monkeypatch):
    # bar = bps/10_000 * price * 100; at price=100 that's bps/10_000 * 10_000
    # = bps numerically, so 3000 bps -> $3,000 (30% of one $10,000 ATM
    # contract's notional).
    monkeypatch.setenv("WHALE_THRESHOLD_BPS", "3000")
    bar = ws._effective_threshold(min_premium=25_000.0, threshold_bps=None, price=100.0)
    assert bar == pytest.approx(3_000.0)


@pytest.mark.unit
def test_classify_whale_bias_applies_bps_threshold_when_passed():
    # price=1000 -> bps=3000 bar is 3000/10_000 * 1000 * 100 = $30,000; a
    # $28,000 call premium clears the legacy $25K bar but NOT the
    # bps-relative bar, so it must be filtered out and the day must read
    # neutral.
    rows = [_row(1100, 'C', 20, 14.0)]  # premium = 20*14*100 = $28,000
    bias, _ = ws.classify_whale_bias(rows, threshold_bps=3000, price=1000.0)
    assert bias == 'neutral'
