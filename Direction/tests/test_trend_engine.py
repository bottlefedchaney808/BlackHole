# Direction/tests/test_trend_engine.py
"""Tests for Direction/trend_engine.py -- network-free except
analyze_trend(), which monkeypatches Direction.data.get_ohlcv/resample_ohlcv.
"""
from datetime import datetime, timedelta

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


@pytest.mark.unit
def test_timeframe_weekly_downtrend_with_realistic_bar_count_is_not_falsely_bullish():
    """Regression for the MA20/MA50 convolve-kernel-longer-than-input bug:
    with ~30 realistic weekly bars (< the MA50 window), ma50 must come back
    empty (degrading ma_alignment to "mixed") rather than a garbage constant
    that lets ma20 > ma50 look bullish in a clear downtrend.
    """
    n = 30  # realistic weekly bar count from a 180-day daily lookback
    closes = np.linspace(160, 82, n)  # clear -50% downtrend
    weekly = {
        "date": np.array([f"2026-{(i % 12) + 1:02d}-01" for i in range(n)]),
        "high": closes + 1, "low": closes - 1, "close": closes,
        "volume": np.ones(n) * 1000,
    }
    result = te._timeframe(weekly)
    # MA50 has insufficient history (30 < 50) -> ma_alignment must degrade to
    # "mixed", never fabricate "bullish" out of a downtrend.
    assert result["ma"] == "mixed"
    assert result["ma"] != "bullish"


@pytest.mark.unit
def test_analyze_trend_weekly_and_monthly_are_not_stuck_mixed_with_real_resampling(monkeypatch):
    """Integration regression for the lookback_days=180 bug: resampling 180
    calendar days of daily bars to weekly (~26 bars) / monthly (~6 bars) left
    both legs permanently short of the 50-bar MA50 window, so ma_alignment
    could never report anything but "mixed" and `aligned`/`signal` were
    structurally always False. With a genuinely long (~4.5yr) daily pull and
    REAL resample_ohlcv (not monkeypatched away, unlike
    test_analyze_trend_aligned_true_when_daily_and_weekly_agree_and_adx_high),
    both legs must have enough real bars for MA50 and report a real
    bullish/bearish reading -- and aligned/signal must be able to go True.
    """
    n = 1650  # matches _LOOKBACK_DAYS=1600 with margin; >=50 real monthly bars
    start = datetime(2020, 1, 1)
    dates = np.array([(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)])
    closes = np.linspace(100, 500, n)  # clear, unambiguous multi-year uptrend
    daily = {
        "date": dates,
        "high": closes + 1, "low": closes - 1, "close": closes,
        "volume": np.ones(n) * 1000,
    }
    monkeypatch.setattr(data, "get_ohlcv", lambda ticker, lookback_days=180: daily)
    # resample_ohlcv is intentionally left un-mocked so real date-bucketing
    # runs -- that's the exact mechanism the original bug hid.
    result = te.analyze_trend("SPY")

    assert result["weekly"]["ma"] == "bullish"
    assert result["monthly"]["ma"] == "bullish"
    assert result["daily"]["ma"] == "bullish"
    assert result["adx_ok"] is True
    assert result["aligned"] is True
    assert result["signal"] is True


@pytest.mark.unit
def test_timeframe_monthly_with_realistic_bar_count_degrades_to_mixed():
    """~6 monthly bars is far short of both the MA20 and MA50 windows --
    both MAs must come back empty, degrading to "mixed" rather than
    producing a garbage constant MA."""
    n = 6
    closes = np.linspace(160, 82, n)
    monthly = {
        "date": np.array([f"2026-{i + 1:02d}-01" for i in range(n)]),
        "high": closes + 1, "low": closes - 1, "close": closes,
        "volume": np.ones(n) * 1000,
    }
    result = te._timeframe(monthly)
    assert result["ma"] == "mixed"
