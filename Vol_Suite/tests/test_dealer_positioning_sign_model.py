"""
Regression test for the sign_model wiring in dealer_positioning.py
(DEALER_POSITIONING_V2_DESIGN.md §5: "add a sign_model parameter to
compute_dealer_positioning()").

This is what lets the existing 4-panel Gamma/Delta/Vanna/Charm exposure
chart actually render the Layer 1b replication-implied sign convention
instead of just the flat v1 OI heuristic -- the whole point of building
replication_reference.py in the first place. This test proves the wiring
itself works (both sign models run end to end without exceptions, and
they actually produce DIFFERENT results, and 'replication' correctly
restricts to OTM legs only) using a fake ThetaDataController stand-in, not
live data -- same network-free approach as every other test in this suite.
"""
import pytest

import dealer_positioning as dp


SPOT = 100.0
STRIKES = list(range(70, 131))


def _theta(k):
    return int(round(k * 1000))


class _FakeTD:
    """Stands in for ThetaDataController -- deliberately symmetric gamma/IV/OI
    across calls and puts at every strike, so any observed difference between
    sign models is attributable to the SIGN CONVENTION itself, not to
    incidental asymmetry in the mock inputs.
    """
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
                rows.append({
                    "strike": _theta(k), "right": right, "gamma": gamma, "implied_vol": iv,
                    "bid": 1.0, "ask": 1.1,
                    "delta": 0.5 if right == "C" else -0.5,
                    "vanna": 0.01, "charm": -0.001,
                })
        return rows

    def option_bulk_oi(self, root, exp):
        return [{"strike": _theta(k), "right": right, "open_interest": 200}
                for k in STRIKES for right in ("C", "P")]

    def close(self):
        pass


@pytest.fixture(autouse=True)
def fake_client(monkeypatch):
    monkeypatch.setattr(dp, "ThetaDataController", _FakeTD)


@pytest.mark.unit
def test_oi_heuristic_is_default_and_runs():
    result = dp.compute_dealer_positioning("MOCK", target_years=60 / 365)
    assert result.sign_model == "oi_heuristic"
    assert len(result.gamma_records) > 0


@pytest.mark.unit
def test_replication_sign_model_runs():
    result = dp.compute_dealer_positioning("MOCK", target_years=60 / 365, sign_model="replication")
    assert result.sign_model == "replication"
    assert len(result.gamma_records) > 0


@pytest.mark.unit
def test_sign_models_produce_different_results():
    v1 = dp.compute_dealer_positioning("MOCK", target_years=60 / 365, sign_model="oi_heuristic")
    v2 = dp.compute_dealer_positioning("MOCK", target_years=60 / 365, sign_model="replication")
    assert v1.total_net_gamma != v2.total_net_gamma


@pytest.mark.unit
def test_replication_excludes_itm_legs():
    """The replication sign model should zero out strikes that aren't part
    of that expiry's OTM replicating set -- unlike v1, which applies a flat
    sign to every strike regardless of moneyness.
    """
    v2 = dp.compute_dealer_positioning("MOCK", target_years=60 / 365, sign_model="replication")
    nonzero_strikes = sum(1 for v in v2.gamma_by_strike if v != 0)
    assert 0 < nonzero_strikes < len(v2.strike_grid), (
        "expected replication sign model to zero out at least some (ITM) "
        "strikes while still leaving others (OTM) nonzero"
    )


@pytest.mark.unit
def test_applied_sign_is_recorded_per_record():
    v2 = dp.compute_dealer_positioning("MOCK", target_years=60 / 365, sign_model="replication")
    signs = {r.applied_sign for r in v2.gamma_records}
    assert signs <= {0.0, -1.0}, f"replication sign model should only ever apply 0.0 or -1.0, got {signs}"
    assert 0.0 in signs and -1.0 in signs, "expected both excluded (ITM) and included (OTM) legs in the mock chain"


@pytest.mark.unit
def test_invalid_sign_model_raises():
    with pytest.raises(ValueError):
        dp.compute_dealer_positioning("MOCK", target_years=60 / 365, sign_model="nonsense")
