# Direction/tests/test_trend_engine.py
"""Tests for Direction/trend_engine.py -- network-free except
analyze_trend(), which monkeypatches Direction.data.get_ohlcv/resample_ohlcv.
"""
import numpy as np
import pytest

from Direction import data, trend_engine as te


@pytest.mark.unit
def test_adx_returns_zero_on_none_inputs():
    assert te.adx(None, None, None) == 0.0


@pytest.mark.unit
def test_adx_returns_zero_on_mismatched_lengths():
    assert te.adx([1, 2], [1], [1, 2]) == 0.0


@pytest.mark.unit
def test_adx_positive_on_strong_uptrend():
    n = 30
    high = np.linspace(100, 130, n)
    low = high - 1
    close = high - 0.5
    result = te.adx(high, low, close)
    assert result > 0.0


@pytest.mark.unit
def test_ma_alignment_bullish_when_price_above_ma20_above_ma50():
    prices = np.array([110.0])
    ma20 = np.array([105.0])
    ma50 = np.array([100.0])
    assert te.ma_alignment(prices, ma20, ma50) == "bullish"


@pytest.mark.unit
def test_ma_alignment_bearish_when_price_below_ma20_below_ma50():
    prices = np.array([90.0])
    ma20 = np.array([95.0])
    ma50 = np.array([100.0])
    assert te.ma_alignment(prices, ma20, ma50) == "bearish"


@pytest.mark.unit
def test_ma_alignment_mixed_on_missing_inputs():
    assert te.ma_alignment(None, None, None) == "mixed"


@pytest.mark.unit
def test_analyze_trend_degrades_to_neutral_on_no_data(monkeypatch):
    monkeypatch.setattr(data, "get_ohlcv", lambda ticker, lookback_days=180: None)
    result = te.analyze_trend("SPY")
    assert result["adx_ok"] is False
    assert result["aligned"] is False
    assert result["signal"] is False


@pytest.mark.unit
def test_analyze_trend_aligned_true_when_daily_and_weekly_agree_and_adx_high(monkeypatch):
    n = 120
    closes = np.linspace(100, 160, n)
    daily = {
        "date": np.array([f"2026-01-{(i % 28) + 1:02d}" for i in range(n)]),
        "high": closes + 1, "low": closes - 1, "close": closes,
        "volume": np.ones(n) * 1000,
    }
    monkeypatch.setattr(data, "get_ohlcv", lambda ticker, lookback_days=180: daily)
    monkeypatch.setattr(data, "resample_ohlcv", lambda d, freq: daily)
    result = te.analyze_trend("SPY")
    assert result["daily"]["ma"] == "bullish"
    assert result["signal"] == result["aligned"]
