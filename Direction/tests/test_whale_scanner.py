"""Tests for Direction/whale_scanner.py -- the LIVE sibling of
Vol_Suite/whale_scanner.py's backtest-only pure classifier. Network-free:
monkeypatches Direction.data.* and asserts the classification is delegated
to Vol_Suite.whale_scanner.classify_whale_bias rather than reimplemented.
"""
import sys
from pathlib import Path

import pytest

_VOL_SUITE_ROOT = Path(__file__).resolve().parent.parent.parent / "Vol_Suite"
if str(_VOL_SUITE_ROOT) not in sys.path:
    sys.path.append(str(_VOL_SUITE_ROOT))

from Direction import data, whale_scanner as dws
import whale_scanner as vs_whale_scanner  # Vol_Suite's pure classifier


def _row(strike, right, volume, close):
    return {"strike": strike, "right": right, "volume": volume, "close": close}


@pytest.mark.unit
def test_scan_degrades_to_neutral_on_no_expirations(monkeypatch):
    monkeypatch.setattr(data, "get_expirations", lambda ticker: [])
    result = dws.scan("SPY")
    assert result["direction"] == "neutral"
    assert result["signal"] is False


@pytest.mark.unit
def test_scan_degrades_to_neutral_on_no_chain_rows(monkeypatch):
    monkeypatch.setattr(data, "get_expirations", lambda ticker: ["20261231"])
    monkeypatch.setattr(data, "get_chain_eod_volume", lambda ticker, exp: None)
    monkeypatch.setattr(data, "get_price", lambda ticker: 100.0)
    result = dws.scan("SPY")
    assert result["direction"] == "neutral"
    assert result["signal"] is False


@pytest.mark.unit
def test_scan_bullish_matches_vol_suite_classifier_directly(monkeypatch):
    rows = [
        _row(110, "C", 20, 15.0),   # $30,000 premium
        _row(90, "P", 5, 10.0),     # $5,000 -- filtered out
    ]
    monkeypatch.setattr(data, "get_expirations", lambda ticker: ["20261231"])
    monkeypatch.setattr(data, "get_chain_eod_volume", lambda ticker, exp: rows)
    monkeypatch.setattr(data, "get_price", lambda ticker: 100.0)

    result = dws.scan("SPY")
    expected_direction, expected_stats = vs_whale_scanner.classify_whale_bias(rows)

    assert result["direction"] == expected_direction == "bullish"
    assert result["whale_calls"] == expected_stats["whale_calls"]
    assert result["call_premium"] == pytest.approx(expected_stats["call_premium"])
    assert result["signal"] is True
    assert result["expiry"] == "20261231"
    assert result["price"] == pytest.approx(100.0)


@pytest.mark.unit
def test_scan_picks_nearest_future_expiry(monkeypatch):
    captured = {}

    def _fake_chain(ticker, exp):
        captured["exp"] = exp
        return [_row(100, "C", 1, 1.0)]

    monkeypatch.setattr(data, "get_expirations", lambda ticker: ["20200101", "20990101"])
    monkeypatch.setattr(data, "get_chain_eod_volume", _fake_chain)
    monkeypatch.setattr(data, "get_price", lambda ticker: 100.0)
    dws.scan("SPY")
    assert captured["exp"] == "20990101"
