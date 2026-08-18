# Direction/tests/test_elliott_wave.py
"""Tests for Direction/elliott_wave.py -- network-free except analyze(),
which monkeypatches Direction.data.get_ohlcv.
"""
import numpy as np
import pytest

from Direction import data, elliott_wave as ew


@pytest.mark.unit
def test_count_waves_returns_unknown_on_too_few_prices():
    assert ew.count_waves([1, 2]) == {"wave_count": 0, "wave_number": 0, "wave_type": "unknown"}


@pytest.mark.unit
def test_count_waves_returns_unknown_with_fewer_than_two_highs():
    # Monotonic rise: no local maxima at all.
    result = ew.count_waves([1, 2, 3, 4, 5])
    assert result["wave_type"] == "unknown"


@pytest.mark.unit
def test_count_waves_classifies_wave3_when_second_upleg_spans_more_bars():
    # Local maxima at idx 1, 3, 5 (the value 2 at idx 3 is itself a local
    # max between two 1s); local minima at idx 2, 4. Constructed so
    # highs[1]-highs[0] > highs[0]-lows[0], i.e. the second upleg (idx1->idx3)
    # spans more bars than the first pullback (idx0->idx2 of highs/lows).
    prices = [1, 5, 1, 2, 1, 8, 1]
    result = ew.count_waves(prices)
    assert result["wave_count"] == 3
    assert result["wave_type"] in ("impulse_wave_3", "impulse_wave_1_or_5")


@pytest.mark.unit
def test_fib_levels_computes_all_five_retracements():
    levels = ew.fib_levels(high=110.0, low=100.0)
    assert levels["fib_236"] == pytest.approx(102.36)
    assert levels["fib_500"] == pytest.approx(105.0)
    assert levels["fib_786"] == pytest.approx(107.86)


@pytest.mark.unit
def test_validate_impulse_true_for_valid_five_point_structure():
    assert ew.validate_impulse(p1=1, p2=0.5, p3=2, p4=1.5, p5=1.2) is True


@pytest.mark.unit
def test_validate_impulse_false_when_wave2_exceeds_wave1_start():
    assert ew.validate_impulse(p1=1, p2=1.5, p3=2, p4=1.5, p5=1.2) is False


@pytest.mark.unit
def test_analyze_degrades_to_unknown_signal_false_on_no_data(monkeypatch):
    monkeypatch.setattr(data, "get_ohlcv", lambda ticker, lookback_days=90, as_of=None: None)
    result = ew.analyze("SPY")
    assert result == {"wave_count": 0, "wave_number": 0, "wave_type": "unknown", "signal": False}


@pytest.mark.unit
def test_analyze_sets_signal_true_when_wave_type_is_impulse_wave_3(monkeypatch):
    closes = np.array([1, 5, 1, 2, 1, 8, 1], dtype=float)
    monkeypatch.setattr(
        data, "get_ohlcv",
        lambda ticker, lookback_days=90, as_of=None: {"close": closes},
    )
    result = ew.analyze("SPY")
    assert result["signal"] == (result["wave_type"] == "impulse_wave_3")


def test_analyze_passes_as_of_to_ohlcv(monkeypatch):
    from Direction import elliott_wave
    from Direction import data as d
    seen = {}

    def _fake_get_ohlcv(ticker, lookback_days=90, as_of=None):
        seen["as_of"] = as_of
        return None  # no data -> analyze degrades to its neutral result

    monkeypatch.setattr(d, "get_ohlcv", _fake_get_ohlcv)
    elliott_wave.analyze("SPY", as_of="2026-08-14")
    assert seen["as_of"] == "2026-08-14"
