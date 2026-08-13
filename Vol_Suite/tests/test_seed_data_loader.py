"""
Regression test for seed_data_loader.py -- ported from the WSL handoff
package's docs/.../code/seed_data_loader.py into Vol_Suite/ proper so it's
real, tracked, importable code (not a docs attachment), per the wiring
plan's Part D: the OOS falsifier + lead-lag test harness loads the 12-ticker
cached `seed_data_*.json` payloads through this module instead of hitting
ThetaData on every test run.
"""
import json
import os

import pytest

import seed_data_loader as sdl


def _write_payload(tmp_path, ticker, expiry="20261120", lookback=150):
    payload = {
        "manifest": {"ticker": ticker, "expiry": expiry, "lookback_days": lookback,
                     "n_greeks": 2, "n_oi": 2, "n_spot": 1},
        "greeks": [{"date": "20260715", "strike": 100000, "right": "C", "implied_vol": 0.2},
                   {"date": "20260715", "strike": 100000, "right": "P", "implied_vol": 0.2}],
        "oi": [{"date": "20260715", "strike": 100000, "right": "C", "open_interest": 200},
               {"date": "20260715", "strike": 100000, "right": "P", "open_interest": 200}],
        "spot": [{"date": "20260715", "close": 100.0}],
    }
    path = tmp_path / f"seed_data_{ticker}_{expiry}_{lookback}d.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


@pytest.mark.unit
def test_load_seed_data_returns_greeks_oi_spot_verbatim(tmp_path):
    path = _write_payload(tmp_path, "SPY")
    greeks, oi, spot = sdl.load_seed_data(path)
    assert len(greeks) == 2
    assert len(oi) == 2
    assert len(spot) == 1
    assert greeks[0]["strike"] == 100000


@pytest.mark.unit
def test_load_all_seed_data_keys_by_ticker_parsed_from_filename(tmp_path):
    _write_payload(tmp_path, "SPY")
    _write_payload(tmp_path, "QQQ")
    all_data = sdl.load_all_seed_data(str(tmp_path))
    assert set(all_data.keys()) == {"SPY", "QQQ"}
    greeks, oi, spot = all_data["SPY"]
    assert len(greeks) == 2


@pytest.mark.unit
def test_manifest_of_returns_manifest_dict(tmp_path):
    path = _write_payload(tmp_path, "SPY", lookback=150)
    manifest = sdl.manifest_of(path)
    assert manifest["ticker"] == "SPY"
    assert manifest["lookback_days"] == 150


@pytest.mark.unit
@pytest.mark.skipif(
    not os.path.isdir(os.path.join(
        os.path.dirname(__file__), "..", "docs", "Dealer posistioning notes",
        "_extracted", "handoff_20260812", "seed_data")),
    reason="cached 150d seed_data/*.json not present in this checkout",
)
def test_real_cached_seed_data_loads_for_all_twelve_tickers():
    """Smoke check against the real, already-pulled 12-ticker 150d dataset
    (this is the 'pre-pulled chunk of data' the falsifier harness runs
    against instead of hitting ThetaData) -- skipped, not failed, when the
    large cached files aren't present in a given checkout.
    """
    seed_dir = os.path.join(
        os.path.dirname(__file__), "..", "docs", "Dealer posistioning notes",
        "_extracted", "handoff_20260812", "seed_data")
    all_data = sdl.load_all_seed_data(seed_dir)
    assert len(all_data) == 12
    for ticker, (greeks, oi, spot) in all_data.items():
        assert greeks and oi and spot, f"{ticker}: empty payload"
