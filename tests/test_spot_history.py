from __future__ import annotations

from datetime import date as date_type

import pytest

from shared.chart_data import CandlePayload, ChartDataError
import shared.spot_history as spot_history
from shared.spot_history import fetch_daily_candles


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

    assert calls == [("SPY", "20260717", "20260815")]
    assert isinstance(payload, CandlePayload)
    assert payload.ticker == "SPY"
    assert payload.interval == "1d"
    assert payload.source == "thetadata"
    assert payload.observations[0].timestamp.isoformat() == "2026-08-15T12:00:00"
    assert payload.observations[0].close == 103.0


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

    assert calls == [("SPY", "20260217", "20260815")]
