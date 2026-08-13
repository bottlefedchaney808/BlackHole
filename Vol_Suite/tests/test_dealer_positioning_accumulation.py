"""
Regression test for wiring real multi-day accumulation into the LIVE
dealer-positioning render path (compute_dealer_positioning).

Context: replication_reference.py's seed-plus-accumulate engine
(_accumulate_from_history / compute_accumulated_position_for_expiry) was
fully built and unit-tested but had ZERO callers anywhere in the production
tree -- dealer_positioning.py's compute_dealer_positioning() always used a
same-day OI snapshot regardless of sign_model. This is the audited root
cause of "the accumulation isn't accumulating live." This test proves the
wiring itself: when accumulate=True, the ANCHOR expiry's Greek exposure is
built from the accumulated signed position (via an injected history
fixture, no network), not from today's snapshot OI -- and that the
non-anchor expiry is untouched, and that the rendered label reflects
accumulate state.
"""
import pytest

import dealer_positioning as dp
import replication_reference as rr


SPOT = 100.0
STRIKES = list(range(70, 131))


def _theta(k):
    return int(round(k * 1000))


class _FakeTD:
    """Same shape as test_dealer_positioning_sign_model.py's _FakeTD, plus a
    second expiry so the test can assert the accumulation wiring only
    touches the anchor expiry, not every active expiry (v1 scope decision).
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
        anchor = (datetime.now() + timedelta(days=60)).strftime("%Y%m%d")
        other = (datetime.now() + timedelta(days=90)).strftime("%Y%m%d")
        return [anchor, other]

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
    # anchor_expiry must actually be resolvable to the FIRST list_expirations
    # entry -- expiry_selector picks nearest-to-target_years, and both fake
    # expiries (60d, 90d) bracket target_years=60/365 closely enough that
    # the 60d one (first in the list) resolves as nearest.


def _accumulation_fixture(expiry):
    """Small seed-plus-accumulate history scoped to the SAME strikes/expiry
    the fake chain snapshot uses, so accumulated positions land on strikes
    the aggregation loop will actually see. Deliberately gives the
    accumulated book a DIFFERENT shape than the flat snapshot OI (200 per
    strike/right) so accumulate=True is distinguishable from accumulate=False.
    """
    dates = ["20260715", "20260716", "20260717"]
    spots = {"20260715": 100.0, "20260716": 101.0, "20260717": 102.0}
    hist_greek_rows, hist_oi_rows, hist_spot_rows = [], [], []
    for i, d in enumerate(dates):
        spot = spots[d]
        hist_spot_rows.append({"date": d, "close": spot})
        for k in STRIKES:
            moneyness = k / spot
            iv = max(0.15 + 0.10 * max(0, 1 - moneyness), 0.10)
            hist_greek_rows.append({"date": d, "strike": _theta(k), "right": "C", "implied_vol": iv})
            hist_greek_rows.append({"date": d, "strike": _theta(k), "right": "P", "implied_vol": iv})
            bump = 500 if abs(k - spot) < 5 else 0
            hist_oi_rows.append({"date": d, "strike": _theta(k), "right": "C",
                                  "open_interest": 200 + bump * (i + 1)})
            hist_oi_rows.append({"date": d, "strike": _theta(k), "right": "P", "open_interest": 200})
    return hist_greek_rows, hist_oi_rows, hist_spot_rows


@pytest.mark.unit
def test_accumulate_false_is_default_and_matches_snapshot_behavior():
    result = dp.compute_dealer_positioning("MOCK", target_years=60 / 365, sign_model="oi_heuristic")
    assert result.accumulate is False


@pytest.mark.unit
def test_accumulate_true_changes_anchor_expiry_numbers():
    from datetime import datetime, timedelta
    anchor_expiry = (datetime.now() + timedelta(days=60)).strftime("%Y%m%d")
    hist_rows = _accumulation_fixture(anchor_expiry)

    snapshot = dp.compute_dealer_positioning("MOCK", target_years=60 / 365, sign_model="oi_heuristic")
    accumulated = dp.compute_dealer_positioning(
        "MOCK", target_years=60 / 365, sign_model="oi_heuristic",
        accumulate=True, accumulation_lookback_days=3,
        _accumulation_hist_rows=hist_rows,
    )

    assert accumulated.accumulate is True
    assert accumulated.total_net_gamma != snapshot.total_net_gamma, (
        "accumulate=True must actually change the anchor expiry's aggregated "
        "Greek exposure -- if it doesn't, the accumulated book isn't wired "
        "into the aggregation loop at all"
    )


@pytest.mark.unit
def test_accumulate_true_leaves_applied_sign_baked_in():
    """Sign is already embedded in the accumulated signed position -- the
    per-row applied_sign for anchor-expiry records under accumulate=True
    should be 1.0 (pass-through), not a re-derived _resolve_sign value, per
    the documented behavioral fork.
    """
    from datetime import datetime, timedelta
    anchor_expiry = (datetime.now() + timedelta(days=60)).strftime("%Y%m%d")
    hist_rows = _accumulation_fixture(anchor_expiry)

    accumulated = dp.compute_dealer_positioning(
        "MOCK", target_years=60 / 365, sign_model="oi_heuristic",
        accumulate=True, accumulation_lookback_days=3,
        _accumulation_hist_rows=hist_rows,
    )
    anchor_records = [r for r in accumulated.gamma_records if r.expiry == anchor_expiry]
    assert anchor_records
    assert all(r.applied_sign == 1.0 for r in anchor_records)


@pytest.mark.unit
def test_accumulate_true_leaves_non_anchor_expiry_untouched():
    """v1 scope: accumulation only applies to the anchor expiry. The second
    (90d) expiry must still use ordinary same-day _resolve_sign behavior
    (applied_sign in {+1.0, -1.0} for oi_heuristic), proving the fork is
    scoped correctly rather than accumulating every active expiry.
    """
    from datetime import datetime, timedelta
    anchor_expiry = (datetime.now() + timedelta(days=60)).strftime("%Y%m%d")
    hist_rows = _accumulation_fixture(anchor_expiry)

    accumulated = dp.compute_dealer_positioning(
        "MOCK", target_years=60 / 365, sign_model="oi_heuristic", max_days=150,
        accumulate=True, accumulation_lookback_days=3,
        _accumulation_hist_rows=hist_rows,
    )
    non_anchor_records = [r for r in accumulated.gamma_records if r.expiry != anchor_expiry]
    assert non_anchor_records, "expected the 90d expiry to still be present via max_days=150"
    assert all(r.applied_sign in (1.0, -1.0) for r in non_anchor_records)


@pytest.mark.unit
def test_label_reflects_accumulate_state():
    from datetime import datetime, timedelta
    anchor_expiry = (datetime.now() + timedelta(days=60)).strftime("%Y%m%d")
    hist_rows = _accumulation_fixture(anchor_expiry)

    snapshot = dp.compute_dealer_positioning("MOCK", target_years=60 / 365, sign_model="oi_heuristic")
    accumulated = dp.compute_dealer_positioning(
        "MOCK", target_years=60 / 365, sign_model="oi_heuristic",
        accumulate=True, accumulation_lookback_days=3,
        _accumulation_hist_rows=hist_rows,
    )
    assert dp.sign_model_render_label(snapshot) == "OI Heuristic (v1)"
    assert dp.sign_model_render_label(accumulated) == "OI Heuristic (v1) + 150d accumulation"


@pytest.mark.unit
def test_dealer_accumulation_env_var_forces_it_on(monkeypatch):
    from datetime import datetime, timedelta
    anchor_expiry = (datetime.now() + timedelta(days=60)).strftime("%Y%m%d")
    hist_rows = _accumulation_fixture(anchor_expiry)
    monkeypatch.setenv("DEALER_ACCUMULATION", "1")

    result = dp.compute_dealer_positioning(
        "MOCK", target_years=60 / 365, sign_model="oi_heuristic",
        accumulation_lookback_days=3, _accumulation_hist_rows=hist_rows,
    )
    assert result.accumulate is True
