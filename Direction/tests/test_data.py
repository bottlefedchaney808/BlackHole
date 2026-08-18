"""Tests for Direction/data.py's pure row/date transforms -- network-free.
The fetch functions themselves (get_price, get_ohlcv, ...) are thin
try/except wrappers around a ThetaDataController call; they're exercised
indirectly via each signal module's tests, which monkeypatch `data.*`
directly rather than mocking the controller.
"""
import numpy as np
import pytest

from Direction import data


@pytest.mark.unit
def test_date_key_handles_iso_timestamp_with_t_separator():
    assert data._date_key({"created": "2026-07-01T17:15:06.172"}) == "2026-07-01"


@pytest.mark.unit
def test_date_key_handles_yyyymmdd_date_field():
    assert data._date_key({"date": "20260701"}) == "2026-07-01"


@pytest.mark.unit
def test_date_key_returns_empty_string_on_missing_or_short_value():
    assert data._date_key({}) == ""
    assert data._date_key({"date": "2026"}) == ""


@pytest.mark.unit
def test_ohlcv_from_rows_dedupes_by_date_and_drops_missing_close():
    rows = [
        {"date": "20260701", "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000},
        {"date": "20260702", "high": 103.0, "low": 100.0, "close": None, "volume": 500},
    ]
    out = data._ohlcv_from_rows(rows)
    assert list(out["date"]) == ["2026-07-01"]
    assert out["close"][0] == pytest.approx(100.0)


@pytest.mark.unit
def test_ohlcv_from_rows_returns_none_on_empty_input():
    assert data._ohlcv_from_rows([]) is None


@pytest.mark.unit
def test_normalize_chain_rows_upscales_theta_strike_and_normalizes_right_word():
    rows = [{"strike": 450000, "right": "CALL", "oi": 12, "close": 3.5}]
    out = data._normalize_chain_rows(rows)
    assert out[0]["strike"] == pytest.approx(450.0)
    assert out[0]["right"] == "C"
    assert out[0]["oi"] == pytest.approx(12.0)


@pytest.mark.unit
def test_normalize_chain_rows_keeps_plain_dollar_strike_unscaled():
    rows = [{"strike": 450.0, "right": "PUT"}]
    out = data._normalize_chain_rows(rows)
    assert out[0]["strike"] == pytest.approx(450.0)
    assert out[0]["right"] == "P"


@pytest.mark.unit
def test_normalize_chain_rows_drops_rows_with_unresolvable_right():
    rows = [{"strike": 100.0, "right": "?"}]
    assert data._normalize_chain_rows(rows) == []


@pytest.mark.unit
def test_latest_day_rows_keeps_only_max_date():
    rows = [
        {"date": "20260701", "strike": 100.0},
        {"date": "20260702", "strike": 100.0},
        {"date": "20260702", "strike": 105.0},
    ]
    out = data._latest_day_rows(rows)
    assert len(out) == 2
    assert all(r["date"] == "20260702" for r in out)


@pytest.mark.unit
def test_resample_ohlcv_weekly_buckets_by_iso_week():
    daily = {
        "date": np.array(["2026-07-06", "2026-07-07", "2026-07-13"]),  # Mon, Tue, next Mon
        "high": np.array([10.0, 12.0, 9.0]),
        "low": np.array([8.0, 9.0, 7.0]),
        "close": np.array([9.0, 11.0, 8.0]),
        "volume": np.array([100.0, 200.0, 50.0]),
    }
    out = data.resample_ohlcv(daily, "W")
    assert len(out["date"]) == 2  # week of 7/6 (2 days), week of 7/13 (1 day)
    assert out["high"][0] == pytest.approx(12.0)
    assert out["volume"][0] == pytest.approx(300.0)


@pytest.mark.unit
def test_resample_ohlcv_returns_none_on_empty_daily():
    assert data.resample_ohlcv({"date": np.array([])}) is None


from datetime import date, timedelta

import pytest

from Direction import data


def _ohlcv_rows(days):
    rows = []
    for i, d in enumerate(days):
        ymd = d.strftime("%Y%m%d")
        rows.append({"date": ymd, "created": d.isoformat(), "high": "100", "low": "90",
                     "close": str(100 + i), "volume": "1000"})
    return rows


def test_normalize_date_accepts_both_forms():
    assert data._normalize_date("2026-08-14") == "2026-08-14"
    assert data._normalize_date("20260814") == "2026-08-14"
    assert data._normalize_date("") == ""
    assert data._normalize_date("garbage") == ""


def test_as_of_date_defaults_to_today():
    assert data._as_of_date(None) == date.today()


def test_get_ohlcv_ends_on_as_of(monkeypatch):
    days = [date(2026, 8, 12), date(2026, 8, 13), date(2026, 8, 14)]
    calls = {}

    class FakeC:
        def hist_stock_eod(self, root, start_date, end_date):
            calls["start"] = start_date
            calls["end"] = end_date
            return _ohlcv_rows(days)

    monkeypatch.setattr(data, "_get_controller", lambda: FakeC())
    out = data.get_ohlcv("SPY", lookback_days=90, as_of="2026-08-14")
    assert calls["end"] == "20260814"
    assert out is not None and str(out["date"][-1]) == "2026-08-14"


def test_get_chain_eod_volume_uses_as_of_window(monkeypatch):
    calls = {}

    class FakeC:
        def option_bulk_hist_eod(self, root, exp, start_date, end_date):
            calls["end"] = end_date
            return [
                {"date": "20260813", "strike": 100000, "right": "C", "volume": "5"},
                {"date": "20260814", "strike": 100000, "right": "C", "volume": "9"},
            ]

    monkeypatch.setattr(data, "_get_controller", lambda: FakeC())
    out = data.get_chain_eod_volume("SPY", "20260918", as_of="2026-08-14")
    assert calls["end"] == "20260814"
    # latest trading day <= as_of wins
    assert len(out) == 1 and out[0]["volume"] == 9.0


def test_get_chain_oi_as_of_uses_historical_probe(monkeypatch):
    calls = {}

    class FakeC:
        def option_bulk_oi(self, root, exp):
            raise AssertionError("snapshot must not be called when as_of is set")

        def option_bulk_oi_latest(self, root, exp, lookback_days=7, as_of=None):
            calls["as_of"] = as_of
            return [{"strike": 100000, "right": "C", "open_interest": "50"}]

    monkeypatch.setattr(data, "_get_controller", lambda: FakeC())
    out = data.get_chain_oi("SPY", "20260918", as_of="2026-08-14")
    assert calls["as_of"] == "2026-08-14"
    assert out and out[0]["oi"] == 50.0


def test_get_close_asof_returns_last_close(monkeypatch):
    days = [date(2026, 8, 12), date(2026, 8, 13), date(2026, 8, 14)]

    class FakeC:
        def hist_stock_eod(self, root, start_date, end_date):
            return _ohlcv_rows(days)

    monkeypatch.setattr(data, "_get_controller", lambda: FakeC())
    # fixture closes are 100+i -> [100.0, 101.0, 102.0]; last close <= as_of wins
    assert data.get_close_asof("SPY", as_of="2026-08-14") == 102.0
