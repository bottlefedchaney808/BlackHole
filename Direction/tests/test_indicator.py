# Direction/tests/test_indicator.py
"""Tests for Direction/indicator.py -- per-bar price signals.

The v2 point is that a verdict CHANGES as bars are added. The crafted
fixture is 80 intraday bars with three regimes:
  1. 40 flat bars @ 100           -> flat: squeeze on, no trend, no wave3
  2. rally leg 1: 100 -> 110      -> swing high at bar 49
  3. pullback:     110 -> 105     -> swing low at bar 54
  4. rally leg 2:  105 -> 130     -> bigger second leg -> impulse_wave_3
  5. small decline 130 -> 129     -> confirms the bar-74 swing high

Expected verdicts (computed from the Direction pure functions directly):
  as-of bar 40 (flat only):    {"wave3": False, "squeeze": True,  "trend": False}
  as-of bar 80 (full series):  {"wave3": True,  "squeeze": False, "trend": True}
so every signal flips as bars are added -- that is the whole point of v2.
"""
from datetime import datetime, timedelta

import numpy as np
import pytest

from shared.chart_data import CandleRecord

from Direction import indicator


def _build_crafted_bars() -> list[CandleRecord]:
    """80 crafted 3m bars, all timestamps inside RTH (09:30-13:27)."""
    closes = [100.0] * 40
    closes += list(np.linspace(100, 110, 11)[1:])  # bars 40..49, 101..110
    closes += list(np.linspace(110, 105, 6)[1:])   # bars 50..54, 109..105
    closes += list(np.linspace(105, 130, 21)[1:])  # bars 55..74, 106.25..130
    closes += list(np.linspace(130, 129, 6)[1:])   # bars 75..79, 129.8..129
    start = datetime(2026, 8, 14, 9, 30)
    return [
        CandleRecord(
            timestamp=start + timedelta(minutes=3 * i),
            open=float(close),
            high=float(close) + 0.5,
            low=float(close) - 0.5,
            close=float(close),
            volume=1000.0,
        )
        for i, close in enumerate(closes)
    ]


def _ohlcv_fn_for(bars: list[CandleRecord]):
    """Injectable stand-in for intraday_bars_as_of: bars with ts <= cutoff."""

    def _fn(ticker, interval, ts):
        return [bar for bar in bars if bar.timestamp <= ts]

    return _fn


@pytest.fixture
def crafted_bars() -> list[CandleRecord]:
    return _build_crafted_bars()


@pytest.mark.unit
def test_price_signals_returns_exact_bool_keys(crafted_bars):
    ohlcv = _ohlcv_fn_for(crafted_bars)
    result = indicator._price_signals("SPY", crafted_bars[-1].timestamp,
                                      interval="3m", ohlcv_fn=ohlcv)
    assert set(result.keys()) == {"wave3", "squeeze", "trend"}
    assert all(isinstance(value, bool) for value in result.values())


@pytest.mark.unit
def test_verdict_changes_as_bars_are_added(crafted_bars):
    """The v2 core: the same ticker yields a DIFFERENT verdict at a later bar."""
    ohlcv = _ohlcv_fn_for(crafted_bars)
    ts1 = crafted_bars[39].timestamp   # 40 flat bars only
    ts2 = crafted_bars[-1].timestamp   # full 80-bar series
    early = indicator._price_signals("SPY", ts1, interval="3m", ohlcv_fn=ohlcv)
    late = indicator._price_signals("SPY", ts2, interval="3m", ohlcv_fn=ohlcv)
    assert early != late
    assert early == {"wave3": False, "squeeze": True, "trend": False}
    assert late == {"wave3": True, "squeeze": False, "trend": True}


@pytest.mark.unit
def test_flat_series_is_squeeze_with_no_trend_or_wave3(crafted_bars):
    """40 flat bars: band width is 0 (< 2% threshold) -> squeeze; a flat
    series has no swings (wave3 False) and no directional strength (trend
    False)."""
    ohlcv = _ohlcv_fn_for(crafted_bars)
    ts1 = crafted_bars[39].timestamp
    result = indicator._price_signals("SPY", ts1, interval="3m", ohlcv_fn=ohlcv)
    assert result == {"wave3": False, "squeeze": True, "trend": False}


@pytest.mark.unit
def test_rally_series_flips_to_wave3_and_trend(crafted_bars):
    """Full 80-bar series: the bigger second leg makes wave3 the strongest
    swing (impulse_wave_3) and the up-leg pushes ADX > 25 with price >
    ma20 > ma50 (bullish) -- squeeze has expanded away."""
    ohlcv = _ohlcv_fn_for(crafted_bars)
    ts2 = crafted_bars[-1].timestamp
    result = indicator._price_signals("SPY", ts2, interval="3m", ohlcv_fn=ohlcv)
    assert result == {"wave3": True, "squeeze": False, "trend": True}


@pytest.mark.unit
def test_short_series_degrades_to_all_false(crafted_bars):
    """10 bars (< 20 for bands, < 50 for MA50, too few for any swing
    structure): every signal must be False -- never raise, never fabricate."""
    ohlcv = _ohlcv_fn_for(crafted_bars[:10])
    result = indicator._price_signals("SPY", crafted_bars[9].timestamp,
                                      interval="3m", ohlcv_fn=ohlcv)
    assert result == {"wave3": False, "squeeze": False, "trend": False}


@pytest.mark.unit
def test_empty_series_degrades_to_all_false():
    def _empty(ticker, interval, ts):
        return []

    result = indicator._price_signals("SPY", datetime(2026, 8, 14, 9, 30),
                                      interval="3m", ohlcv_fn=_empty)
    assert result == {"wave3": False, "squeeze": False, "trend": False}


@pytest.mark.unit
def test_raising_ohlcv_fn_degrades_to_all_false():
    """A provider hiccup must not crash per-bar evaluation (the chart loop
    calls this on every bar); degrade to neutral instead."""

    def _boom(ticker, interval, ts):
        raise RuntimeError("provider down")

    result = indicator._price_signals("SPY", datetime(2026, 8, 14, 9, 30),
                                      interval="3m", ohlcv_fn=_boom)
    assert result == {"wave3": False, "squeeze": False, "trend": False}


@pytest.mark.unit
def test_ohlcv_fn_receives_ticker_interval_and_ts(crafted_bars):
    seen = {}

    def _capturing(ticker, interval, ts):
        seen.update(ticker=ticker, interval=interval, ts=ts)
        return crafted_bars

    ts = crafted_bars[-1].timestamp
    indicator._price_signals("SPY", ts, interval="3m", ohlcv_fn=_capturing)
    assert seen == {"ticker": "SPY", "interval": "3m", "ts": ts}


@pytest.mark.unit
def test_defaults_to_intraday_bars_as_of(crafted_bars, monkeypatch):
    """With ohlcv_fn=None the production default must be
    shared.spot_history.intraday_bars_as_of, called with the same args."""
    seen = {}

    def _fake(ticker, interval, ts):
        seen.update(ticker=ticker, interval=interval, ts=ts)
        return crafted_bars

    monkeypatch.setattr(indicator, "intraday_bars_as_of", _fake)
    ts = crafted_bars[-1].timestamp
    result = indicator._price_signals("SPY", ts, interval="3m")
    assert seen == {"ticker": "SPY", "interval": "3m", "ts": ts}
    assert result == {"wave3": True, "squeeze": False, "trend": True}
