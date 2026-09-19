from __future__ import annotations

from datetime import date as date_type, datetime

import pytest

from shared.chart_data import CandlePayload, CandleRecord, ChartDataError
import shared.spot_history as spot_history
from shared.spot_history import fetch_daily_candles, fetch_intraday_candles


_ROWS = [
    {
        "timestamp": "2026-08-01",
        "open": 98,
        "high": 102,
        "low": 97,
        "close": 101,
        "volume": 20,
    }
]


def test_fetch_daily_candles_uses_injected_provider_and_normalizes_ticker():
    calls = []

    def provider(ticker, lookback):
        calls.append((ticker, lookback))
        return _ROWS

    payload = fetch_daily_candles(" spy ", provider=provider)

    assert calls == [("SPY", "6m")]
    assert payload.ticker == "SPY"
    assert payload.interval == "1d"
    assert payload.lookback == "6m"
    assert payload.source == "injected"
    assert payload.observations[0].close == 101.0


def test_fetch_daily_candles_forwards_explicit_lookback():
    calls = []

    def provider(ticker, lookback):
        calls.append((ticker, lookback))
        return _ROWS

    fetch_daily_candles("QQQ", lookback="3m", provider=provider)

    assert calls == [("QQQ", "3m")]


def test_fetch_daily_candles_wraps_provider_failure_without_details():
    def provider(ticker, lookback):
        raise RuntimeError("credential=do-not-leak")

    with pytest.raises(ChartDataError, match="daily spot-history provider failed") as exc_info:
        fetch_daily_candles("SPY", provider=provider)

    assert "do-not-leak" not in str(exc_info.value)
    assert "credential" not in str(exc_info.value)


def test_fetch_daily_candles_wraps_provider_chart_data_error_without_details():
    def provider(ticker, lookback):
        raise ChartDataError("credential=do-not-leak")

    with pytest.raises(ChartDataError, match="daily spot-history provider failed") as exc_info:
        fetch_daily_candles("SPY", provider=provider)

    assert "do-not-leak" not in str(exc_info.value)
    assert "credential" not in str(exc_info.value)


def test_fetch_daily_candles_uses_falsey_injected_provider():
    calls = []

    class FalseyProvider:
        def __bool__(self):
            return False

        def __call__(self, ticker, lookback):
            calls.append((ticker, lookback))
            return _ROWS

    payload = fetch_daily_candles("SPY", provider=FalseyProvider())

    assert calls == [("SPY", "6m")]
    assert payload.source == "injected"


def test_fetch_daily_candles_wraps_invalid_provider_rows():
    def provider(ticker, lookback):
        return [
            {
                "timestamp": "not-a-date",
                "open": 1,
                "high": 2,
                "low": 0,
                "close": 1,
            }
        ]

    with pytest.raises(ChartDataError, match="invalid timestamp"):
        fetch_daily_candles("SPY", provider=provider)


def test_fetch_daily_candles_rejects_empty_ticker_before_provider_call():
    called = False

    def provider(ticker, lookback):
        nonlocal called
        called = True
        return _ROWS

    with pytest.raises(ChartDataError, match="ticker"):
        fetch_daily_candles(" ", provider=provider)

    assert called is False


@pytest.mark.parametrize("ticker", ["SPY/../QQQ", "SPY?x=1", "SPY\n", "../SPY"])
def test_fetch_daily_candles_rejects_unsafe_ticker_before_provider_call(ticker):
    called = False

    def provider(ticker, lookback):
        nonlocal called
        called = True
        return _ROWS

    with pytest.raises(ChartDataError, match="ticker"):
        fetch_daily_candles(ticker, provider=provider)

    assert called is False


@pytest.mark.parametrize("ticker", ["SPY", "BRK.B", "BTC-USD"])
def test_fetch_daily_candles_accepts_normal_symbol_forms(ticker):
    calls = []

    def provider(ticker, lookback):
        calls.append((ticker, lookback))
        return _ROWS

    payload = fetch_daily_candles(ticker, provider=provider)

    assert payload.ticker == ticker
    assert calls == [(ticker, "6m")]


@pytest.mark.parametrize("lookback", [None, "", "0d", "-1d", 0, -1, 1.5, object()])
def test_fetch_daily_candles_rejects_invalid_lookback_before_injected_provider(lookback):
    called = False

    def provider(ticker, lookback):
        nonlocal called
        called = True
        return _ROWS

    with pytest.raises(ChartDataError, match="lookback"):
        fetch_daily_candles("SPY", lookback=lookback, provider=provider)

    assert called is False


@pytest.mark.parametrize(
    ("frozen_today", "expected_end"),
    [
        ((2026, 8, 15), "20260814"),  # Saturday -> Friday
        ((2026, 8, 16), "20260814"),  # Sunday -> Friday
    ],
)
def test_lookback_without_explicit_end_anchors_to_latest_weekday(
    monkeypatch, frozen_today, expected_end
):
    class FixedDate(date_type):
        @classmethod
        def today(cls):
            return cls(*frozen_today)

    monkeypatch.setattr(spot_history, "date", FixedDate)

    assert spot_history._lookback_start_end("1m") == ("20260716", expected_end)


def test_lookback_with_explicit_end_preserves_requested_date():
    assert spot_history._lookback_start_end("1m", end=date_type(2026, 8, 15)) == (
        "20260717",
        "20260815",
    )


def test_default_provider_adapts_created_rows_and_converts_dates(monkeypatch):
    calls = []

    class FixedDate(date_type):
        @classmethod
        def today(cls):
            return cls(2026, 8, 15)

    class FakeThetaDataController:
        def hist_stock_eod(self, ticker, start_date, end_date):
            calls.append((ticker, start_date, end_date))
            return [
                {
                    "created": "2026-08-15T12:00:00",
                    "open": 101,
                    "high": 105,
                    "low": 99,
                    "close": 103,
                    "volume": 40,
                    "extra": "preserved",
                }
            ]

    monkeypatch.setattr(spot_history, "date", FixedDate)
    monkeypatch.setattr(spot_history, "ThetaDataController", FakeThetaDataController)

    payload = fetch_daily_candles(" spy ", lookback="30d")

    assert calls == [("SPY", "20260716", "20260814")]
    assert isinstance(payload, CandlePayload)
    assert payload.ticker == "SPY"
    assert payload.interval == "1d"
    assert payload.source == "thetadata"
    assert payload.observations[0].timestamp.isoformat() == "2026-08-15T12:00:00"
    assert payload.observations[0].close == 103.0
    assert payload.as_of.isoformat() == "2026-08-15T12:00:00"


def test_default_provider_skips_empty_theta_pagination_rows(monkeypatch):
    class FixedDate(date_type):
        @classmethod
        def today(cls):
            return cls(2026, 8, 15)

    class FakeThetaDataController:
        def hist_stock_eod(self, ticker, start_date, end_date):
            return [None, {}, [], {"date": "2026-08-15", "open": 101, "high": 105,
                                   "low": 99, "close": 103, "volume": 40}]

    monkeypatch.setattr(spot_history, "date", FixedDate)
    monkeypatch.setattr(spot_history, "ThetaDataController", FakeThetaDataController)

    payload = fetch_daily_candles("SPY", lookback="1m")

    assert len(payload.observations) == 1
    assert payload.observations[0].timestamp.isoformat() == "2026-08-15T00:00:00"


def test_injected_timestamp_rows_provide_latest_as_of():
    rows = [
        {**_ROWS[0], "timestamp": "2026-08-01T09:30:00"},
        {**_ROWS[0], "timestamp": "2026-08-02T15:45:00"},
    ]

    payload = fetch_daily_candles("SPY", provider=lambda ticker, lookback: rows)

    assert payload.as_of.isoformat() == "2026-08-02T15:45:00"


def test_default_provider_converts_public_six_month_lookback_deterministically(monkeypatch):
    calls = []

    class FixedDate(date_type):
        @classmethod
        def today(cls):
            return cls(2026, 8, 15)

    class FakeThetaDataController:
        def hist_stock_eod(self, ticker, start_date, end_date):
            calls.append((ticker, start_date, end_date))
            return [
                {
                    "created": "2026-08-15T12:00:00",
                    "open": 101,
                    "high": 105,
                    "low": 99,
                    "close": 103,
                }
            ]

    monkeypatch.setattr(spot_history, "date", FixedDate)
    monkeypatch.setattr(spot_history, "ThetaDataController", FakeThetaDataController)

    fetch_daily_candles("SPY")

    assert calls == [("SPY", "20260216", "20260814")]


def _intraday_rows():
    return [
        {"date": "20260814", "ms_of_day": 34200000, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 10},
        {"date": "20260814", "ms_of_day": 34260000, "open": 100.5, "high": 102, "low": 100, "close": 101.5, "volume": 20},
        {"date": "20260814", "ms_of_day": 35100000, "open": 101.5, "high": 103, "low": 101, "close": 102, "volume": 30},
    ]


def test_fetch_intraday_aggregates_interval_boundaries_and_metadata():
    payload = fetch_intraday_candles("SPY", interval="15m", provider=lambda ticker, lookback: _intraday_rows())
    assert payload.interval == "15m"
    assert len(payload.observations) == 2
    first, second = payload.observations
    assert first.timestamp.isoformat() == "2026-08-14T09:30:00"
    assert (first.open, first.high, first.low, first.close, first.volume) == (100.0, 102.0, 99.0, 101.5, 30.0)
    assert second.timestamp.isoformat() == "2026-08-14T09:45:00"
    assert second.close == 102.0
    assert payload.as_of.isoformat() == "2026-08-14T09:45:00"


@pytest.mark.parametrize("interval", ["3m", "5m", "10m", "15m", "30m", "1h", "4h"])
def test_fetch_intraday_supports_requested_intervals(interval):
    payload = fetch_intraday_candles("SPY", interval=interval, provider=lambda ticker, lookback: _intraday_rows())
    assert payload.interval == interval


def test_fetch_intraday_rejects_malformed_source_rows():
    with pytest.raises(ChartDataError, match="intraday"):
        fetch_intraday_candles("SPY", interval="15m", provider=lambda ticker, lookback: [{"date": "20260814", "ms_of_day": 1}])


def test_fetch_intraday_defaults_to_one_day_and_validates_lookback():
    """Defaults to 1d; rejects nonsense; no longer rejects long windows.

    This used to assert that `lookback="6m"` RAISED, because intraday was
    capped at 30 days and only `<N>d`/`<N>w` parsed. That cap was never the
    provider's -- it was httpx's default 5.0s timeout inside PHClient, which
    made anything dense fail and get filed as vendor flakiness (see CLAUDE.md,
    "the five-second lie"). With the timeout raised and `hist_stock_ohlc`
    chunking at 21d, a long intraday window is a legitimate request.
    """
    calls = []
    def provider(ticker, lookback):
        calls.append(lookback)
        return _intraday_rows()
    fetch_intraday_candles("SPY", provider=provider)
    assert calls == ["1d"]

    # Months and years now parse and are fetched, not refused.
    fetch_intraday_candles("SPY", lookback="6m", provider=provider)
    fetch_intraday_candles("SPY", lookback="2y", provider=provider)
    assert calls == ["1d", "6m", "2y"]

    # What must STILL be refused is input that cannot mean a window at all,
    # and a non-positive one -- those fail identically every time, so retrying
    # or fetching them just delays the real error.
    for bad in ("0d", "-5d", "abc", "1x"):
        with pytest.raises(ChartDataError, match="intraday lookback"):
            fetch_intraday_candles("SPY", lookback=bad, provider=provider)


def _as_of_payload() -> CandlePayload:
    """SPY 15m fixture: four RTH bars plus one after-hours (16:00) bar."""
    return CandlePayload(
        ticker="SPY",
        interval="15m",
        lookback="1d",
        source="test",
        observations=(
            CandleRecord(timestamp=datetime(2026, 8, 14, 9, 30), open=100.0, high=101.0, low=99.0, close=100.5, volume=10.0),
            CandleRecord(timestamp=datetime(2026, 8, 14, 9, 45), open=100.5, high=102.0, low=100.0, close=101.5, volume=20.0),
            CandleRecord(timestamp=datetime(2026, 8, 14, 10, 0), open=101.5, high=103.0, low=101.0, close=102.0, volume=30.0),
            CandleRecord(timestamp=datetime(2026, 8, 14, 10, 15), open=102.0, high=103.5, low=101.5, close=103.0, volume=40.0),
            CandleRecord(timestamp=datetime(2026, 8, 14, 16, 0), open=103.0, high=104.0, low=102.5, close=103.5, volume=50.0),
        ),
    )


def _stub_intraday_fetch(monkeypatch, payload):
    monkeypatch.setattr(spot_history, "fetch_intraday_candles", lambda *args, **kwargs: payload)


def test_intraday_bars_as_of_returns_bars_up_to_ts_ascending(monkeypatch):
    _stub_intraday_fetch(monkeypatch, _as_of_payload())

    bars = spot_history.intraday_bars_as_of("SPY", "15m", datetime(2026, 8, 14, 9, 45))

    assert [bar.timestamp for bar in bars] == [
        datetime(2026, 8, 14, 9, 30),
        datetime(2026, 8, 14, 9, 45),
    ]
    assert all(isinstance(bar, CandleRecord) for bar in bars)


def test_intraday_bars_as_of_includes_bar_at_ts(monkeypatch):
    _stub_intraday_fetch(monkeypatch, _as_of_payload())

    bars = spot_history.intraday_bars_as_of("SPY", "15m", datetime(2026, 8, 14, 10, 0))

    assert [bar.timestamp for bar in bars] == [
        datetime(2026, 8, 14, 9, 30),
        datetime(2026, 8, 14, 9, 45),
        datetime(2026, 8, 14, 10, 0),
    ]


def test_intraday_bars_as_of_before_first_bar_returns_empty(monkeypatch):
    _stub_intraday_fetch(monkeypatch, _as_of_payload())

    assert spot_history.intraday_bars_as_of("SPY", "15m", datetime(2026, 8, 14, 9, 15)) == []


def test_intraday_bars_as_of_excludes_after_hours_bars(monkeypatch):
    _stub_intraday_fetch(monkeypatch, _as_of_payload())

    bars = spot_history.intraday_bars_as_of("SPY", "15m", datetime(2026, 8, 14, 16, 30))

    assert [bar.timestamp for bar in bars] == [
        datetime(2026, 8, 14, 9, 30),
        datetime(2026, 8, 14, 9, 45),
        datetime(2026, 8, 14, 10, 0),
        datetime(2026, 8, 14, 10, 15),
    ]
    assert all(bar.timestamp.hour < 16 for bar in bars)


def test_intraday_bars_as_of_accepts_iso_string_ts(monkeypatch):
    _stub_intraday_fetch(monkeypatch, _as_of_payload())

    bars = spot_history.intraday_bars_as_of("SPY", "15m", "2026-08-14T09:45:00")

    assert [bar.timestamp for bar in bars] == [
        datetime(2026, 8, 14, 9, 30),
        datetime(2026, 8, 14, 9, 45),
    ]


def test_intraday_bars_as_of_forwards_ticker_interval_and_default_lookback(monkeypatch):
    calls = []

    def fake_fetch(ticker, *, interval="15m", lookback="1d", provider=None):
        calls.append((ticker, interval, lookback))
        return _as_of_payload()

    monkeypatch.setattr(spot_history, "fetch_intraday_candles", fake_fetch)

    spot_history.intraday_bars_as_of("SPY", "5m", datetime(2026, 8, 14, 9, 45))

    assert calls == [("SPY", "5m", "1d")]


def test_intraday_bars_as_of_sorts_unsorted_payload(monkeypatch):
    payload = CandlePayload(
        ticker="SPY",
        interval="15m",
        lookback="1d",
        source="test",
        observations=(
            CandleRecord(timestamp=datetime(2026, 8, 14, 10, 15), open=102.0, high=103.5, low=101.5, close=103.0, volume=40.0),
            CandleRecord(timestamp=datetime(2026, 8, 14, 9, 30), open=100.0, high=101.0, low=99.0, close=100.5, volume=10.0),
        ),
    )
    _stub_intraday_fetch(monkeypatch, payload)

    bars = spot_history.intraday_bars_as_of("SPY", "15m", datetime(2026, 8, 14, 16, 0))

    assert [bar.timestamp for bar in bars] == [
        datetime(2026, 8, 14, 9, 30),
        datetime(2026, 8, 14, 10, 15),
    ]


def test_intraday_bars_as_of_degrades_to_empty_on_fetch_failure(monkeypatch):
    def fake_fetch(ticker, *, interval="15m", lookback="1d", provider=None):
        raise ChartDataError("provider failed")

    monkeypatch.setattr(spot_history, "fetch_intraday_candles", fake_fetch)

    assert spot_history.intraday_bars_as_of("SPY", "15m", datetime(2026, 8, 14, 9, 45)) == []


def test_intraday_bars_as_of_degrades_to_empty_on_empty_payload(monkeypatch):
    payload = CandlePayload(ticker="SPY", interval="15m", lookback="1d", source="test", observations=())
    _stub_intraday_fetch(monkeypatch, payload)

    assert spot_history.intraday_bars_as_of("SPY", "15m", datetime(2026, 8, 14, 9, 45)) == []


def test_intraday_bars_as_of_degrades_to_empty_on_invalid_ts(monkeypatch):
    _stub_intraday_fetch(monkeypatch, _as_of_payload())

    assert spot_history.intraday_bars_as_of("SPY", "15m", "not-a-timestamp") == []
