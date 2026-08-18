# Direction/tests/test_bollinger_analyzer.py
"""Tests for Direction/bollinger_analyzer.py -- network-free except
analyze(), which monkeypatches Direction.data.get_ohlcv.
"""
import numpy as np
import pytest

from Direction import bollinger_analyzer as ba, data


@pytest.mark.unit
def test_get_bands_returns_empty_arrays_when_shorter_than_window():
    bands = ba.get_bands([1, 2, 3], window=20)
    assert len(bands["sma"]) == 0


@pytest.mark.unit
def test_get_bands_computes_sma_and_symmetric_upper_lower():
    prices = [100.0] * 25  # flat -> std=0 -> upper==lower==sma
    bands = ba.get_bands(prices, window=20)
    assert bands["sma"][-1] == pytest.approx(100.0)
    assert bands["upper"][-1] == pytest.approx(100.0)
    assert bands["lower"][-1] == pytest.approx(100.0)


@pytest.mark.unit
def test_detect_squeeze_true_when_width_below_threshold_pct_of_price():
    bands = {"sma": np.array([100.0]), "width": np.array([1.0])}  # 1% < 2%
    assert ba.detect_squeeze(bands) is True


@pytest.mark.unit
def test_detect_squeeze_false_when_width_above_threshold_pct():
    bands = {"sma": np.array([100.0]), "width": np.array([5.0])}  # 5% > 2%
    assert ba.detect_squeeze(bands) is False


@pytest.mark.unit
def test_detect_squeeze_false_on_empty_bands():
    assert ba.detect_squeeze({"sma": np.array([]), "width": np.array([])}) is False


@pytest.mark.unit
def test_regime_upper_thrust_bullish_near_upper_band():
    bands = {"upper": np.array([110.0]), "lower": np.array([100.0])}
    assert ba.regime(bands, price=109.5) == "upper_thrust_bullish"


@pytest.mark.unit
def test_regime_lower_thrust_bearish_near_lower_band():
    bands = {"upper": np.array([110.0]), "lower": np.array([100.0])}
    assert ba.regime(bands, price=100.5) == "lower_thrust_bearish"


@pytest.mark.unit
def test_regime_neutral_midband():
    bands = {"upper": np.array([110.0]), "lower": np.array([100.0])}
    assert ba.regime(bands, price=105.0) == "neutral"


@pytest.mark.unit
def test_analyze_degrades_to_neutral_signal_false_on_no_data(monkeypatch):
    monkeypatch.setattr(data, "get_ohlcv", lambda ticker, lookback_days=90, as_of=None: None)
    result = ba.analyze("SPY")
    assert result == {"squeeze": False, "regime": "neutral", "signal": False}


@pytest.mark.unit
def test_analyze_signal_true_on_squeeze(monkeypatch):
    closes = np.array([100.0] * 25)
    monkeypatch.setattr(
        data, "get_ohlcv",
        lambda ticker, lookback_days=90, as_of=None: {"close": closes},
    )
    result = ba.analyze("SPY")
    assert result["squeeze"] is True
    assert result["signal"] is True


def test_analyze_passes_as_of_to_ohlcv(monkeypatch):
    from Direction import bollinger_analyzer
    from Direction import data as d
    seen = {}

    def _fake_get_ohlcv(ticker, lookback_days=90, as_of=None):
        seen["as_of"] = as_of
        return None  # no data -> analyze degrades to its neutral result

    monkeypatch.setattr(d, "get_ohlcv", _fake_get_ohlcv)
    bollinger_analyzer.analyze("SPY", as_of="2026-08-14")
    assert seen["as_of"] == "2026-08-14"
