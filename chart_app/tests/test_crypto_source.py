"""Crypto/perp routing. No network: every venue call is stubbed.

The one thing these tests exist to prevent is the failure mode that made this
module necessary -- ThetaData answers for the ticker `BTC` with the Grayscale
Bitcoin Mini Trust ETF ($33.82 on 2026-09-17), which is a real instrument at a
real price that is simply not bitcoin.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from chart_app import crypto_source
from chart_app.crypto_source import (
    _aggregate,
    _bars_wanted,
    fetch_crypto_candles,
    is_crypto,
    known_symbols,
    resolve,
)
from shared.chart_data import ChartDataError

# --------------------------------------------------------------- routing


@pytest.mark.parametrize(
    "ticker,expected",
    [
        ("BTC-PERP", True),
        ("ETH-PERP", True),
        ("BTC-USDT-SWAP", True),
        ("BTC-USD", True),
        ("SOL-USDC", True),
        ("SPY", False),
        ("NVDA", False),
        ("BRK.B", False),
        ("", False),
        # The trap: a bare coin ticker must NOT route to crypto, because it is
        # a real (and different) US-listed equity on the options feed.
        ("BTC", False),
        ("ETH", False),
    ],
)
def test_is_crypto_routing(ticker, expected):
    assert is_crypto(ticker) is expected


def test_resolve_maps_perp_to_an_okx_swap():
    r = resolve("BTC-PERP")
    assert r["kind"] == "perp"
    assert r["okx"] == "BTC-USDT-SWAP"
    assert r["coinbase"] == "BTC-USD"


def test_resolve_passes_through_a_full_instrument_id():
    assert resolve("ETH-USDT-SWAP")["okx"] == "ETH-USDT-SWAP"


def test_resolve_handles_an_unlisted_coin():
    assert resolve("PEPE-PERP")["okx"] == "PEPE-USDT-SWAP"


def test_known_symbols_are_all_crypto_routable():
    symbols = known_symbols()
    assert "BTC-PERP" in symbols
    assert all(is_crypto(s) for s in symbols)


# --------------------------------------------------------------- helpers


def test_bars_wanted_uses_a_24_7_day():
    """1440 minutes a day, not the 390 of an equity session."""
    assert _bars_wanted("1h", "10d") == 240
    assert _bars_wanted("1d", "1y") == 365
    assert _bars_wanted("15m", "1d") == 96


def test_bars_wanted_is_bounded():
    assert _bars_wanted("3m", "5y") <= 6000
    assert _bars_wanted("1d", "1d") >= 60


def test_aggregate_folds_bars_correctly():
    t0 = datetime(2026, 1, 1)
    rows = [
        {"timestamp": t0 + timedelta(minutes=5 * i),
         "open": 10.0 + i, "high": 20.0 + i, "low": 1.0 + i,
         "close": 15.0 + i, "volume": 100.0}
        for i in range(4)
    ]
    out = _aggregate(rows, 2)
    assert len(out) == 2
    assert out[0]["open"] == 10.0          # first open of the pair
    assert out[0]["close"] == 16.0         # last close of the pair
    assert out[0]["high"] == 21.0          # max
    assert out[0]["low"] == 1.0            # min
    assert out[0]["volume"] == 200.0       # summed
    assert out[0]["timestamp"] == rows[1]["timestamp"]


def test_aggregate_is_a_noop_at_factor_one():
    rows = [{"timestamp": datetime(2026, 1, 1), "open": 1.0, "high": 2.0,
             "low": 0.5, "close": 1.5, "volume": 10.0}]
    assert _aggregate(rows, 1) == rows


def test_aggregate_refuses_to_invent_volume():
    t0 = datetime(2026, 1, 1)
    rows = [
        {"timestamp": t0 + timedelta(minutes=5 * i), "open": 1.0, "high": 2.0,
         "low": 0.5, "close": 1.5, "volume": None if i else 10.0}
        for i in range(2)
    ]
    assert _aggregate(rows, 2)[0]["volume"] is None


# ----------------------------------------------------------------- fetch


def _fake_rows(n, start=datetime(2026, 1, 1)):
    return [
        {"timestamp": start + timedelta(hours=i), "open": 100.0 + i,
         "high": 101.0 + i, "low": 99.0 + i, "close": 100.5 + i,
         "volume": 50.0 + i}
        for i in range(n)
    ]


def test_fetch_uses_okx_first_and_records_the_venue(monkeypatch):
    monkeypatch.setattr(crypto_source, "_okx_rows", lambda i, iv, w: _fake_rows(80))
    monkeypatch.setattr(
        crypto_source, "_coinbase_rows",
        lambda *a: pytest.fail("coinbase must not be called when okx answers"),
    )
    payload = fetch_crypto_candles("BTC-PERP", interval="1h", lookback="3d")
    assert payload.source.startswith("crypto:okx:BTC-USDT-SWAP")
    assert len(payload.observations) == 72   # 3d * 24h
    assert payload.observations[0].timestamp < payload.observations[-1].timestamp


def test_fetch_falls_back_to_coinbase_when_okx_fails(monkeypatch):
    def boom(*_a):
        raise ChartDataError("okx down")

    monkeypatch.setattr(crypto_source, "_okx_rows", boom)
    monkeypatch.setattr(crypto_source, "_coinbase_rows", lambda i, iv, w: _fake_rows(50))
    payload = fetch_crypto_candles("BTC-PERP", interval="1h", lookback="2d")
    # The venue is NAMED: a perp priced off spot is a different instrument.
    assert payload.source.startswith("crypto:coinbase:")


def test_fetch_raises_with_every_venue_error_when_all_fail(monkeypatch):
    def boom(*_a):
        raise ChartDataError("nope")

    monkeypatch.setattr(crypto_source, "_okx_rows", boom)
    monkeypatch.setattr(crypto_source, "_coinbase_rows", boom)
    with pytest.raises(ChartDataError) as excinfo:
        fetch_crypto_candles("BTC-PERP", interval="1h", lookback="2d")
    assert "okx" in str(excinfo.value) and "coinbase" in str(excinfo.value)


def test_fetch_rejects_an_unsupported_interval():
    with pytest.raises(ChartDataError):
        fetch_crypto_candles("BTC-PERP", interval="2h", lookback="2d")


def test_ten_minute_interval_is_aggregated_from_five(monkeypatch):
    seen = {}

    def fake_okx(inst, interval, want):
        seen["interval"] = interval
        seen["want"] = want
        return _fake_rows(200)

    monkeypatch.setattr(crypto_source, "_okx_rows", fake_okx)
    payload = fetch_crypto_candles("BTC-PERP", interval="10m", lookback="1d")
    assert seen["interval"] == "5m"          # fetched at 5m
    assert payload.interval == "10m"         # published at 10m
    assert len(payload.observations) <= 144  # 1d of 10m bars


def test_ingest_routes_crypto_away_from_thetadata(monkeypatch, tmp_path):
    """The wiring, not just the module: a crypto ticker must never reach the
    injected equity fetchers."""
    from chart_app.bar_cache import BarCache
    from chart_app.ingest import refresh_cache

    monkeypatch.setattr(
        crypto_source, "_okx_rows", lambda i, iv, w: _fake_rows(100)
    )

    def must_not_call(*_a, **_k):
        pytest.fail("ThetaData fetcher called for a crypto ticker")

    cache = BarCache(tmp_path / "c.db")
    count = refresh_cache(
        cache, "BTC-PERP", "1h", "2d",
        daily_fn=must_not_call, intrad_fn=must_not_call,
    )
    assert count > 0
    assert cache.load("BTC-PERP", "1h")
