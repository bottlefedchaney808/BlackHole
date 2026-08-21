"""
Regression test for the "two-vanna" bug: options_chain_scanner.py used to
compute its own vanna series from its own independent chain fetch
(compute_vanna_positioning(df, spot)), completely disconnected from
dealer_positioning.py's DealerPositioningResult -- the 4-panel dealer chart
and the scanner's vanna panel could (and, per the CARL audit's screenshot
evidence, did) silently disagree for the same ticker/run.

The fix: compute_vanna_positioning now takes a DealerPositioningResult and
reads its already-computed vanna numbers verbatim; scan_chain/run_chain_scanner
accept an optional `dealer_result` so a caller that already ran
compute_dealer_positioning (e.g. volatility_suite.py) can share it instead of
triggering a second independent computation.

Network-free: builds a DealerPositioningResult directly via a fake
ThetaDataController (same pattern as test_dealer_positioning_sign_model.py),
then feeds it into the scanner's vanna computation.
"""
import math
import os

import dealer_positioning as dp
import expiry_book_exposure as ebe
import options_chain_scanner as ocs
import pytest

SPOT = 100.0
STRIKES = list(range(70, 131))


def _theta(k):
    return int(round(k * 1000))


class _FakeTD:
    def __init__(self, *a, **k):
        pass

    def fetch_spot_price(self, ticker):
        return SPOT

    def fetch_dividend_yield(self, ticker, spot=None):
        return 0.0

    def fetch_risk_free_rate(self, T):
        return 0.04

    def list_expirations(self, root):
        from datetime import datetime, timedelta
        return [(datetime.now() + timedelta(days=60)).strftime("%Y%m%d")]

    def option_bulk_greeks(self, root, exp):
        rows = []
        for k in STRIKES:
            moneyness = k / SPOT
            iv = max(0.15 + 0.10 * max(0, 1 - moneyness), 0.10)
            gamma = 0.02 * (1.0 / (1 + abs(k - SPOT) / 10))
            for right in ("C", "P"):
                # Deliberately asymmetric vanna/OI across strikes so net vanna
                # is nonzero and a flip strike exists to detect -- an all-flat
                # mock would make "scanner matches engine" trivially true even
                # with a real double-computation bug (both would be zero).
                vanna = 0.02 if k < SPOT else -0.01
                rows.append({
                    "strike": _theta(k), "right": right, "gamma": gamma, "implied_vol": iv,
                    "bid": 1.0, "ask": 1.1,
                    "delta": 0.5 if right == "C" else -0.5,
                    "vanna": vanna, "charm": -0.001,
                })
        return rows

    def option_bulk_oi(self, root, exp):
        return [{"strike": _theta(k), "right": right,
                  "open_interest": 200 + (5 * k if right == "C" else 0)}
                for k in STRIKES for right in ("C", "P")]

    def close(self):
        pass


@pytest.fixture(autouse=True)
def fake_client(monkeypatch):
    monkeypatch.setattr(dp, "ThetaDataController", _FakeTD)
    monkeypatch.setattr(ocs, "ThetaDataController", _FakeTD)


@pytest.mark.unit
def test_dealer_result_carries_call_put_vanna_split():
    result = dp.compute_dealer_positioning("MOCK", target_years=60 / 365, sign_model="oi_heuristic")
    assert not math.isnan(result.vanna_call_shares)
    assert not math.isnan(result.vanna_put_shares)
    # net vanna (sum of the per-strike array) should equal call+put split
    net = float(sum(result.vanna_shares_by_strike))
    assert net == pytest.approx(result.vanna_call_shares + result.vanna_put_shares, rel=1e-9)


@pytest.mark.unit
def test_compute_vanna_positioning_reads_dealer_result_verbatim():
    dealer_result = dp.compute_dealer_positioning("MOCK", target_years=60 / 365, sign_model="oi_heuristic")
    vanna_info = ocs.compute_vanna_positioning(dealer_result)

    expected_net = float(sum(dealer_result.vanna_shares_by_strike))
    assert vanna_info['net_vanna_shares'] == pytest.approx(expected_net, rel=1e-9)
    assert vanna_info['call_vanna_shares'] == pytest.approx(dealer_result.vanna_call_shares, rel=1e-9)
    assert vanna_info['put_vanna_shares'] == pytest.approx(dealer_result.vanna_put_shares, rel=1e-9)


@pytest.mark.unit
def test_scan_chain_with_shared_dealer_result_matches_engine_vanna_exactly():
    """The actual regression test for the bug: run compute_dealer_positioning
    ONCE, pass it into scan_chain, and assert the scanner's net/call/put
    vanna numbers are EXACTLY the dealer engine's numbers -- not just
    close, not independently re-derived.
    """
    from datetime import datetime, timedelta
    td = _FakeTD()
    expiration = (datetime.now() + timedelta(days=60)).strftime("%Y%m%d")

    dealer_result = dp.compute_dealer_positioning("MOCK", target_years=60 / 365,
                                                    expiration=expiration, sign_model="oi_heuristic")
    scan_result = ocs.scan_chain("MOCK", expiration, 60 / 365, td, dealer_result=dealer_result)

    expected_net = float(sum(dealer_result.vanna_shares_by_strike))
    assert scan_result.net_vanna_shares == pytest.approx(expected_net, rel=1e-9)
    assert scan_result.call_vanna_shares == pytest.approx(dealer_result.vanna_call_shares, rel=1e-9)
    assert scan_result.put_vanna_shares == pytest.approx(dealer_result.vanna_put_shares, rel=1e-9)


@pytest.mark.unit
def test_scan_chain_without_dealer_result_computes_one_itself_not_twice():
    """Standalone scanner use (no caller-supplied dealer_result): scan_chain
    must still end up with only ONE vanna computation -- it should compute
    a DealerPositioningResult internally and read vanna from it, not fall
    back to an independent df-based vanna calculation.
    """
    from datetime import datetime, timedelta
    td = _FakeTD()
    expiration = (datetime.now() + timedelta(days=60)).strftime("%Y%m%d")

    scan_result = ocs.scan_chain("MOCK", expiration, 60 / 365, td)

    # Independently compute what the dealer engine WOULD have produced, and
    # assert the scanner's standalone numbers match it exactly -- proving
    # scan_chain's internal fallback path is calling compute_dealer_positioning
    # itself, not running its own separate vanna arithmetic.
    from expiry_book_production import fetch_production_result
    reference = fetch_production_result(td, "MOCK", expiration)
    expected_net = float(sum(ebe.vannacharm_row(r, reference.spot, "vanna")
                            for r in reference.snapshot.rows))
    assert scan_result.net_vanna_shares == pytest.approx(expected_net, rel=1e-9)


@pytest.mark.unit
def test_plot_scanner_charts_renders_dealer_engine_series(tmp_path):
    """plot_scanner_charts must render the DEALER ENGINE's vanna series
    (result.dealer_result), not recompute a second series from the scanner's
    own single-expiry chain -- the two-vanna fix that makes the scanner chart
    agree with the dealer engine's exposure charts.
    """
    from datetime import datetime, timedelta
    td = _FakeTD()
    expiration = (datetime.now() + timedelta(days=60)).strftime("%Y%m%d")

    scan_result = ocs.scan_chain("MOCK", expiration, 60 / 365, td)
    assert scan_result.dealer_result is not None

    # A ScanResult without the shared dealer result must be REFUSED (raise),
    # not silently recomputed from the single-expiry chain.
    from dataclasses import replace
    bad_result = replace(scan_result, dealer_result=None)
    with pytest.raises(ValueError):
        ocs.plot_scanner_charts(bad_result, output_dir=str(tmp_path))

    # With the shared result, the chart renders (network-free via fakes).
    path = ocs.plot_scanner_charts(scan_result, output_dir=str(tmp_path))
    assert path.endswith(".png")
    assert os.path.exists(path)
