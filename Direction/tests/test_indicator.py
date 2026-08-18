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


# ---------------------------------------------------------------------------
# Task 3 -- per-bar intraday whale-flow signal (_flow_signal)
# ---------------------------------------------------------------------------
# flow_fn contract: flow_fn(root, start_datetime, end_datetime, min_premium)
# -> list[dict] of scanner flow rows ({"premium": $, "timestamp": ISO, ...}).
# Window is [bar_ts - interval, bar_ts], closed at bar_ts; whale = any
# in-window row with premium >= WHALE_PREMIUM.

_BAR_TS = datetime(2026, 8, 14, 10, 0)          # 15m bar closed 10:00
_WINDOW_START = datetime(2026, 8, 14, 9, 45)    # bar_ts - 15m


def _flow_fn_with(rows):
    """Injectable flow_fn stand-in returning the given rows."""

    def _fn(root, start_datetime, end_datetime, min_premium):
        return rows

    return _fn


def _row(premium: float, ts: str) -> dict:
    return {"premium": premium, "timestamp": ts}


@pytest.mark.unit
def test_flow_signal_returns_exact_bool_keys():
    rows = [_row(indicator.WHALE_PREMIUM, _BAR_TS.isoformat())]
    result = indicator._flow_signal("SPY", _BAR_TS, interval="15m",
                                    flow_fn=_flow_fn_with(rows))
    assert set(result.keys()) == {"whale"}
    assert all(isinstance(value, bool) for value in result.values())


@pytest.mark.unit
def test_flow_signal_whale_true_for_in_window_premium_row():
    """A row inside the bar's window with premium >= WHALE_PREMIUM is a whale."""
    rows = [_row(indicator.WHALE_PREMIUM, "2026-08-14T09:50:00")]
    result = indicator._flow_signal("SPY", _BAR_TS, interval="15m",
                                    flow_fn=_flow_fn_with(rows))
    assert result == {"whale": True}


@pytest.mark.unit
def test_flow_signal_closed_bar_semantics_trade_at_bar_ts_counts():
    """A trade exactly at bar_ts is inside the window (closed-bar)."""
    rows = [_row(indicator.WHALE_PREMIUM, _BAR_TS.isoformat())]
    result = indicator._flow_signal("SPY", _BAR_TS, interval="15m",
                                    flow_fn=_flow_fn_with(rows))
    assert result == {"whale": True}


@pytest.mark.unit
def test_flow_signal_window_start_is_inclusive():
    """A trade exactly at bar_ts - interval is inside the window."""
    rows = [_row(indicator.WHALE_PREMIUM, _WINDOW_START.isoformat())]
    result = indicator._flow_signal("SPY", _BAR_TS, interval="15m",
                                    flow_fn=_flow_fn_with(rows))
    assert result == {"whale": True}


@pytest.mark.unit
def test_flow_signal_out_of_window_row_is_not_whale():
    """A high-premium trade BEFORE bar_ts - interval is NOT in this bar's
    window -- it belongs to an earlier bar."""
    rows = [_row(indicator.WHALE_PREMIUM, "2026-08-14T09:30:00")]
    result = indicator._flow_signal("SPY", _BAR_TS, interval="15m",
                                    flow_fn=_flow_fn_with(rows))
    assert result == {"whale": False}


@pytest.mark.unit
def test_flow_signal_after_bar_ts_row_is_not_whale():
    """A high-premium trade AFTER bar_ts is in a later bar's window."""
    rows = [_row(indicator.WHALE_PREMIUM, "2026-08-14T10:15:00")]
    result = indicator._flow_signal("SPY", _BAR_TS, interval="15m",
                                    flow_fn=_flow_fn_with(rows))
    assert result == {"whale": False}


@pytest.mark.unit
def test_flow_signal_below_threshold_row_is_not_whale():
    """In-window but premium < WHALE_PREMIUM is not a whale."""
    rows = [_row(indicator.WHALE_PREMIUM - 1.0, "2026-08-14T09:50:00")]
    result = indicator._flow_signal("SPY", _BAR_TS, interval="15m",
                                    flow_fn=_flow_fn_with(rows))
    assert result == {"whale": False}


@pytest.mark.unit
def test_flow_signal_mixed_rows_whale_if_any_row_qualifies():
    """whale is ANY qualifying row -- a below-threshold row must not mask a
    qualifying one, and rows without a parseable timestamp still count on
    premium alone (the provider was asked for the window)."""
    rows = [
        _row(indicator.WHALE_PREMIUM - 1.0, "2026-08-14T09:40:00"),
        {"premium": indicator.WHALE_PREMIUM},  # no timestamp -> in-window
    ]
    result = indicator._flow_signal("SPY", _BAR_TS, interval="15m",
                                    flow_fn=_flow_fn_with(rows))
    assert result == {"whale": True}


@pytest.mark.unit
def test_flow_signal_empty_rows_degrades_to_false():
    result = indicator._flow_signal("SPY", _BAR_TS, interval="15m",
                                    flow_fn=_flow_fn_with([]))
    assert result == {"whale": False}


@pytest.mark.unit
def test_flow_signal_raising_flow_fn_degrades_to_false():
    """A provider hiccup must not crash per-bar evaluation; degrade to
    {"whale": False} -- never raise, never fabricate."""

    def _boom(root, start_datetime, end_datetime, min_premium):
        raise RuntimeError("flow provider down")

    result = indicator._flow_signal("SPY", _BAR_TS, interval="15m",
                                    flow_fn=_boom)
    assert result == {"whale": False}


@pytest.mark.unit
def test_flow_signal_passes_closed_bar_window_to_flow_fn():
    """flow_fn receives [bar_ts - interval, bar_ts] as start/end datetimes,
    the ticker as root, and the WHALE_PREMIUM filter."""
    seen = {}

    def _capturing(root, start_datetime, end_datetime, min_premium):
        seen.update(root=root, start=start_datetime, end=end_datetime,
                    min_premium=min_premium)
        return [_row(indicator.WHALE_PREMIUM, "2026-08-14T09:50:00")]

    result = indicator._flow_signal("SPY", _BAR_TS, interval="15m",
                                    flow_fn=_capturing)
    assert result == {"whale": True}
    assert seen == {
        "root": "SPY",
        "start": _WINDOW_START,
        "end": _BAR_TS,
        "min_premium": indicator.WHALE_PREMIUM,
    }


@pytest.mark.unit
def test_flow_signal_accepts_iso_string_bar_ts():
    rows = [_row(indicator.WHALE_PREMIUM, "2026-08-14T09:50:00")]
    result = indicator._flow_signal("SPY", _BAR_TS.isoformat(), interval="15m",
                                    flow_fn=_flow_fn_with(rows))
    assert result == {"whale": True}


@pytest.mark.unit
def test_flow_signal_default_flow_fn_uses_ph_flow_namespace(monkeypatch):
    """With flow_fn=None the production default must call the PH v2 client's
    flow.scanner_trades_in_time_range with root/start/end/min_premium and
    unwrap the ResponseEnvelope's .data."""
    seen = {}

    class _FakeEnv:
        def __init__(self, data):
            self.data = data

    class _FakeFlow:
        def scanner_trades_in_time_range(self, **kwargs):
            seen.update(kwargs)
            return _FakeEnv([{"premium": indicator.WHALE_PREMIUM,
                              "timestamp": "2026-08-14T09:50:00"}])

    class _FakeClient:
        flow = _FakeFlow()

    monkeypatch.setattr(indicator, "_thread_client",
                        lambda: _FakeClient())
    result = indicator._flow_signal("SPY", _BAR_TS, interval="15m")
    assert result == {"whale": True}
    assert seen["root"] == "SPY"
    assert seen["start_datetime"] == _WINDOW_START.isoformat()
    assert seen["end_datetime"] == _BAR_TS.isoformat()
    assert seen["min_premium"] == indicator.WHALE_PREMIUM


@pytest.mark.unit
def test_flow_signal_default_flow_fn_failure_degrades_to_false(monkeypatch):
    """A failure building/calling the PH client (e.g. missing credentials)
    must degrade to {"whale": False}, never raise."""

    def _no_client():
        raise RuntimeError("ThetaData credentials not found")

    monkeypatch.setattr(indicator, "_thread_client", _no_client)
    result = indicator._flow_signal("SPY", _BAR_TS, interval="15m")
    assert result == {"whale": False}


@pytest.mark.unit
def test_whale_premium_threshold_reused_from_v1(monkeypatch):
    """WHALE_PREMIUM must reuse the v1 threshold constant from
    whale_scanner (WHALE_THRESHOLD), not a locally invented value."""
    import importlib
    importlib.reload(indicator)
    from Direction.whale_scanner import WHALE_THRESHOLD
    assert indicator.WHALE_PREMIUM == WHALE_THRESHOLD

