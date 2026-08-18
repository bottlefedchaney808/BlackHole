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


# ---------------------------------------------------------------------------
# Task 4 -- per-bar liquidity signal (_liquidity_signal)
# ---------------------------------------------------------------------------
# gamma_fn contract: gamma_fn(root, bar_dt, interval) -> bool (dealer-gamma
#   regime is bullish / negative-supply).  Called ONLY on the coarse grid:
#   the first call of a session, then every gamma_every_n_bars-th session
#   bar (session index derived from bar_ts).  Between grid points the last
#   computed value is reused; a call failure degrades to neutral and the
#   next grid point retries.
# oi_fn contract: oi_fn(root, as_of) -> {"max_pain": float, "price": float}
#   (EOD OI max-pain as of the bar's YYYYMMDD date).  Called on every bar
#   (its default path is data-layer-cached; OI settles EOD by nature).
# liquidity = gamma_bullish OR the v1 max-pain signal readable from
#   liquidity_map.get_liquidity (max pain within 2% of spot), NOT the
#   brief's literal "price above max-pain" phrasing -- deviation documented
#   in the Task 4 report.
#
# 15m session bar indexes for timestamps starting 09:30 ET:
#   09:30 -> 38, 09:45 -> 39, 10:00 -> 40, ..., 11:45 -> 47.

_LIQ_BAR0 = datetime(2026, 8, 14, 9, 30)


def _liq_bars(n=10, interval_min=15) -> list[datetime]:
    return [_LIQ_BAR0 + timedelta(minutes=interval_min * i) for i in range(n)]


def _oi_neutral():
    """OI payload far enough from price that the v1 2% gravity rule is off."""
    return {"max_pain": 100.0, "price": 150.0}


def _gamma_fn_with(values):
    """gamma_fn that returns the next value per call (list) or a fixed bool."""
    calls = []

    def _fn(root, bar_dt, interval):
        calls.append((root, bar_dt, interval))
        if isinstance(values, list):
            return values[min(len(calls) - 1, len(values) - 1)]
        return values

    _fn.calls = calls
    return _fn


def _oi_fn_with(payload):
    calls = []

    def _fn(root, as_of):
        calls.append((root, as_of))
        return payload

    _fn.calls = calls
    return _fn


@pytest.mark.unit
def test_liquidity_signal_returns_exact_bool_keys():
    result = indicator._liquidity_signal(
        "SPY", _LIQ_BAR0, gamma_fn=_gamma_fn_with(True),
        oi_fn=_oi_fn_with(_oi_neutral()), state={})
    assert set(result.keys()) == {"liquidity"}
    assert isinstance(result["liquidity"], bool)


@pytest.mark.unit
def test_liquidity_signal_grid_count_10_bars_every_4_three_gamma_calls():
    """10 bars, gamma every 4 -> exactly 3 gamma_fn calls (bars 1, 3, 7 of
    the sequence: first bar forced + session grid points), never 10.  oi_fn
    is the EOD side and is called on every bar."""
    gamma = _gamma_fn_with(True)
    oi = _oi_fn_with(_oi_neutral())
    state = {}  # shared grid state -- must persist across the bar sequence
    for ts in _liq_bars(10):
        indicator._liquidity_signal("SPY", ts, interval="15m",
                                    gamma_fn=gamma, oi_fn=oi, state=state)
    assert len(gamma.calls) == 3
    assert len(gamma.calls) != 10
    assert len(oi.calls) == 10


@pytest.mark.unit
def test_liquidity_signal_off_grid_reuses_last_gamma():
    """Bars 09:30 (grid), 09:45 (off-grid), 10:00 (grid), 10:15 (off-grid):
    the 10:00 recompute only takes effect at 10:00 -- 09:45 reuses the
    09:30 value.  gamma_fn is called exactly twice."""
    gamma = _gamma_fn_with([True, False])
    oi = _oi_fn_with(_oi_neutral())
    bars = _liq_bars(4)
    state = {}  # shared grid state across the bar sequence
    results = [indicator._liquidity_signal("SPY", ts, interval="15m",
                                           gamma_fn=gamma, oi_fn=oi,
                                           state=state)
               for ts in bars]
    assert results == [{"liquidity": True},   # 09:30 computed -> True
                       {"liquidity": True},   # 09:45 reuses True
                       {"liquidity": False},  # 10:00 recomputed -> False
                       {"liquidity": False}]  # 10:15 reuses False
    assert len(gamma.calls) == 2


@pytest.mark.unit
def test_liquidity_signal_gamma_failure_neutral_then_retry_next_grid():
    """A gamma_fn failure at a grid point -> neutral (False), reused on the
    following off-grid bar, retried at the next grid point -- never raises."""
    calls = []

    def _flaky(root, bar_dt, interval):
        calls.append(bar_dt)
        if len(calls) == 1:
            raise RuntimeError("dealer gamma down")
        return True

    oi = _oi_fn_with(_oi_neutral())
    bars = _liq_bars(3)  # 09:30 (grid/fail), 09:45 (off-grid), 10:00 (grid/retry)
    state = {}
    results = [indicator._liquidity_signal("SPY", ts, interval="15m",
                                           gamma_fn=_flaky, oi_fn=oi,
                                           state=state)
               for ts in bars]
    assert results == [{"liquidity": False},  # failure -> neutral
                       {"liquidity": False},  # off-grid reuse of neutral
                       {"liquidity": True}]   # retry succeeded
    assert len(calls) == 2


@pytest.mark.unit
def test_liquidity_signal_oi_failure_degrades_to_false():
    """oi_fn failure must degrade the OI leg to neutral -- never raise."""

    def _boom(root, as_of):
        raise RuntimeError("oi provider down")

    result = indicator._liquidity_signal(
        "SPY", _LIQ_BAR0, gamma_fn=_gamma_fn_with(False), oi_fn=_boom, state={})
    assert result == {"liquidity": False}


@pytest.mark.unit
def test_liquidity_signal_both_providers_failing_degrades_to_false():
    def _boom_gamma(root, bar_dt, interval):
        raise RuntimeError("gamma down")

    def _boom_oi(root, as_of):
        raise RuntimeError("oi down")

    result = indicator._liquidity_signal(
        "SPY", _LIQ_BAR0, gamma_fn=_boom_gamma, oi_fn=_boom_oi, state={})
    assert result == {"liquidity": False}


@pytest.mark.unit
def test_liquidity_signal_matches_v1_max_pain_gravity_rule():
    """The liquidity rule reuses the v1 get_liquidity() signal readable from
    that module: max pain within 2% of spot.  'Price above max pain' alone
    is NOT the v1 rule -- a price above max pain but >2% away stays False
    (documented deviation from the brief's literal phrasing)."""
    gamma = _gamma_fn_with(False)
    near = indicator._liquidity_signal(
        "SPY", _LIQ_BAR0, gamma_fn=gamma,
        oi_fn=_oi_fn_with({"max_pain": 101.0, "price": 100.0}), state={})
    assert near == {"liquidity": True}  # 1% -> gravity proximity

    far_above = indicator._liquidity_signal(
        "SPY", _LIQ_BAR0, gamma_fn=gamma,
        oi_fn=_oi_fn_with({"max_pain": 100.0, "price": 110.0}), state={})
    assert far_above == {"liquidity": False}  # 9% away, even though price > max pain


@pytest.mark.unit
def test_liquidity_signal_oi_fn_receives_root_and_as_of_date():
    seen = {}

    def _capturing(root, as_of):
        seen.update(root=root, as_of=as_of)
        return _oi_neutral()

    indicator._liquidity_signal("SPY", _LIQ_BAR0,
                                gamma_fn=_gamma_fn_with(True),
                                oi_fn=_capturing, state={})
    assert seen == {"root": "SPY", "as_of": "20260814"}


@pytest.mark.unit
def test_liquidity_signal_gamma_fn_receives_root_bar_ts_and_interval():
    gamma = _gamma_fn_with(True)
    indicator._liquidity_signal("SPY", _LIQ_BAR0, interval="15m",
                                gamma_fn=gamma, oi_fn=_oi_fn_with(_oi_neutral()),
                                state={})
    assert gamma.calls == [("SPY", _LIQ_BAR0, "15m")]


@pytest.mark.unit
def test_liquidity_signal_gamma_every_n_bars_configurable():
    """N=2, 5 bars -> gamma_fn called on bars 0, 2, 4 -> 3 calls."""
    gamma = _gamma_fn_with(True)
    oi = _oi_fn_with(_oi_neutral())
    state = {}
    for ts in _liq_bars(5):
        indicator._liquidity_signal("SPY", ts, interval="15m",
                                    gamma_fn=gamma, oi_fn=oi,
                                    gamma_every_n_bars=2, state=state)
    assert len(gamma.calls) == 3


@pytest.mark.unit
def test_liquidity_signal_accepts_iso_string_bar_ts():
    result = indicator._liquidity_signal(
        "SPY", _LIQ_BAR0.isoformat(), gamma_fn=_gamma_fn_with(True),
        oi_fn=_oi_fn_with(_oi_neutral()), state={})
    assert result == {"liquidity": True}


@pytest.mark.unit
def test_liquidity_signal_module_state_isolated_by_ticker_and_date():
    """With state=None the module-level store keys on (ticker, date): the
    first call of a session forces a computation, an off-grid bar reuses it,
    and a NEW date starts a fresh session (no cross-day leak)."""
    gamma = _gamma_fn_with(True)
    oi = _oi_fn_with(_oi_neutral())
    ticker = "SPY-T4MOD"
    first = indicator._liquidity_signal(ticker, _LIQ_BAR0, interval="15m",
                                        gamma_fn=gamma, oi_fn=oi)
    second = indicator._liquidity_signal(ticker, _LIQ_BAR0 + timedelta(minutes=15),
                                         interval="15m", gamma_fn=gamma, oi_fn=oi)
    next_day = datetime(2026, 8, 17, 9, 30)
    third = indicator._liquidity_signal(ticker, next_day, interval="15m",
                                        gamma_fn=gamma, oi_fn=oi)
    assert [first, second, third] == [{"liquidity": True}] * 3
    assert len(gamma.calls) == 2  # session 1 grid + new-date first bar


@pytest.mark.unit
def test_liquidity_signal_docstring_mentions_coarse_grid_sampling():
    """The gamma is sampled on a coarse grid -- the docstring must say so
    (never fabricate between grid points)."""
    assert "sampled on a coarse grid" in indicator._liquidity_signal.__doc__


@pytest.mark.unit
def test_liquidity_signal_default_gamma_fn_uses_ph_dealer_namespace(monkeypatch):
    """With gamma_fn=None the production default must call the PH v2 client's
    dealer.weighted_greeks with root/start_date/end_date/interval/ms_of_day
    and extract the net-gamma sign from the CSV (list-of-lists) payload."""
    seen = {}

    class _FakeEnv:
        def __init__(self, data):
            self.data = data

    class _FakeDealer:
        def weighted_greeks(self, **kwargs):
            seen.update(kwargs)
            return _FakeEnv([
                ["strike", "expiration", "right", "date", "root", "ms_of_day",
                 "weighted_gamma", "net_positioning"],
                ["1000000", "20260821", "C", "20260814", "SPY", "34200000",
                 "0.05", "0.01"],
                ["1010000", "20260821", "C", "20260814", "SPY", "34200000",
                 "-0.02", "-0.005"],
            ])

    class _FakeClient:
        dealer = _FakeDealer()

    monkeypatch.setattr(indicator, "_thread_client", lambda: _FakeClient())
    result = indicator._liquidity_signal(
        "SPY", _LIQ_BAR0, interval="15m",
        oi_fn=_oi_fn_with(_oi_neutral()), state={})
    assert result == {"liquidity": True}  # sum weighted_gamma = 0.03 > 0
    assert seen["root"] == "SPY"
    assert seen["start_date"] == "20260814"
    assert seen["end_date"] == "20260814"
    assert seen["interval"] == "MINUTE15"
    assert seen["ms_of_day"] == 34_200_000  # 09:30 ET in ms


@pytest.mark.unit
def test_liquidity_signal_default_gamma_fn_dict_rows(monkeypatch):
    """use_csv=False style dict rows: weighted_gamma is preferred, and a
    net_positioning-only payload still yields a sign."""
    class _FakeEnv:
        def __init__(self, data):
            self.data = data

    class _FakeDealer:
        def __init__(self, env):
            self._env = env

        def weighted_greeks(self, **kwargs):
            return self._env

    class _FakeClient:
        def __init__(self, dealer):
            self.dealer = dealer

    # weighted_gamma sums to -0.15 -> bearish regime
    neg = _FakeEnv([{"weighted_gamma": 0.1}, {"weighted_gamma": -0.25}])
    monkeypatch.setattr(indicator, "_thread_client",
                        lambda: _FakeClient(_FakeDealer(neg)))
    out = indicator._liquidity_signal(
        "SPY", _LIQ_BAR0, interval="15m",
        oi_fn=_oi_fn_with(_oi_neutral()), state={})
    assert out == {"liquidity": False}

    # net_positioning-only rows, positive sum -> bullish
    pos = _FakeEnv([{"net_positioning": 0.4}, {"net_positioning": 0.1}])
    monkeypatch.setattr(indicator, "_thread_client",
                        lambda: _FakeClient(_FakeDealer(pos)))
    out = indicator._liquidity_signal(
        "SPY", _LIQ_BAR0, interval="15m",
        oi_fn=_oi_fn_with(_oi_neutral()), state={})
    assert out == {"liquidity": True}


@pytest.mark.unit
def test_liquidity_signal_default_gamma_fn_failure_degrades_to_false(monkeypatch):
    """A failure building/calling the PH client (e.g. missing credentials)
    must degrade to neutral liquidity, never raise."""

    def _no_client():
        raise RuntimeError("ThetaData credentials not found")

    monkeypatch.setattr(indicator, "_thread_client", _no_client)
    result = indicator._liquidity_signal(
        "SPY", _LIQ_BAR0, oi_fn=_oi_fn_with(_oi_neutral()), state={})
    assert result == {"liquidity": False}


@pytest.mark.unit
def test_liquidity_signal_default_oi_fn_uses_liquidity_map_helpers(monkeypatch):
    """The default oi_fn must reuse liquidity_map._pick_expiry/_max_pain with
    the Direction.data EOD OI/close paths as of the bar's date."""
    from Direction import data as ddata

    monkeypatch.setattr(ddata, "get_expirations",
                        lambda root: ["20260821", "20260814"])
    monkeypatch.setattr(ddata, "get_close_asof",
                        lambda root, as_of=None: 100.0)
    monkeypatch.setattr(ddata, "get_chain_oi",
                        lambda root, exp, as_of=None: [
                            {"strike": 100.0, "right": "C", "oi": 1000.0},
                            {"strike": 100.0, "right": "P", "oi": 1000.0},
                            {"strike": 105.0, "right": "C", "oi": 10.0},
                            {"strike": 95.0, "right": "P", "oi": 10.0},
                        ])
    out = indicator._default_oi_fn("SPY", "20260814")
    assert out == {"max_pain": 100.0, "price": 100.0}


@pytest.mark.unit
def test_liquidity_signal_default_oi_fn_failure_degrades(monkeypatch):
    """Missing chain/price/expirations -> neutral OI payload, never raise."""
    from Direction import data as ddata

    monkeypatch.setattr(ddata, "get_expirations", lambda root: [])
    monkeypatch.setattr(ddata, "get_close_asof",
                        lambda root, as_of=None: None)
    assert indicator._default_oi_fn("SPY", "20260814") == {}


