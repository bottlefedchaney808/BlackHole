"""
Regression test: options_chain_scanner MUST consume the SAME dealer engine
result as dealer_positioning (compute_dealer_positioning, max_days=150) --
the scanner performs NO independent vanna computation.

Jason's standing complaint: the scanner chart ("Net Dealer Vanna by Strike")
disagreed with the dealer 4-panel vanna panel. Root cause: even after the
sign resolver was shared (dealer_sign column), the scanner still computed the
vanna SERIES from its own single-expiry chain with today's OI, while the
dealer chart uses the 150d accumulated whole-surface book. Fix (2026-08-10):
scan_chain calls compute_dealer_positioning (the SAME solver, SAME window) and
consumes its vanna_shares_by_strike verbatim -- scan_chain attaches the
result to ScanResult.dealer_result, and BOTH compute_vanna_positioning and
plot_scanner_charts render from it. No inline sign heuristics, no second
vanna math.

These tests are network-free: the scanner's scan_chain() and the solver's
ThetaDataController are stubbed (FakeTD), the direction-bias source is
patched, and the 150d accumulation book is stubbed to empty, so they prove
the WIRING (scanner vanna == engine vanna, chart == engine series).
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dealer_positioning as dp
import options_chain_scanner as ocs
from options_chain_scanner import ScanResult


SPOT = 8.33
STRIKES = [5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0]
RIGHTS = ("C", "P")


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
        return ["20261120"]

    def option_bulk_greeks(self, root, exp):
        rows = []
        for k in STRIKES:
            for right in RIGHTS:
                rows.append({
                    "strike": _theta(k), "right": right,
                    "implied_vol": 0.6 - 0.03 * abs(k - SPOT) / SPOT,
                    "bid": 0.5, "ask": 0.6,
                    "delta": 0.5 if right == "C" else -0.5,
                    "gamma": 0.02, "vanna": 0.001, "charm": 0.0001,
                })
        return rows

    def option_bulk_greeks_second_order(self, root, exp):
        rows = []
        for k in STRIKES:
            for right in RIGHTS:
                rows.append({"strike": _theta(k), "right": right,
                             "vanna": 0.001, "charm": 0.0001})
        return rows

    def option_bulk_oi(self, root, exp):
        rows = []
        for k in STRIKES:
            for right in RIGHTS:
                rows.append({"strike": _theta(k), "right": right,
                             "open_interest": 1000})
        return rows

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _patch_deps(monkeypatch):
    monkeypatch.setattr(ocs, "ThetaDataController", _FakeTD)
    # The scanner now calls the SAME solver (compute_dealer_positioning) the
    # dealer 4-panel chart uses, which opens its OWN ThetaDataController from
    # dealer_positioning's namespace -- patch that too, and stub the 150d
    # accumulation book so the solver's engine call stays network-free.
    monkeypatch.setattr(dp, "ThetaDataController", _FakeTD)
    monkeypatch.setattr(dp.replication_reference, "get_accumulated_position",
                        lambda ticker, expiry=None, lookback_days=None: {})
    # Direction bias: bullish, so signs are deterministic and non-zero.
    monkeypatch.setattr(
        dp, "_fetch_direction_bias",
        lambda ticker, min_score=3: (1.0, {"score": 3, "conviction": "MEDIUM",
                                          "whale_direction": "bullish"}))
    # Pin per-expiry sabr_deviation to fallback (= direction_bias) so the
    # fake chain's SABR fit can't flip the bias in these wiring tests.
    monkeypatch.setattr(
        dp, "_compute_sabr_deviation_for_expiry",
        lambda vsr, fallback_bias, deadband=dp.SABR_DEVIATION_DEADBAND: fallback_bias)


def _run_scan(tmp_path):
    # run_chain_scanner writes charts; point output at tmp_path via the
    # scanner's VS_OUTPUT_DIR env var.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("VS_OUTPUT_DIR", str(tmp_path))
    try:
        files, interp, result = ocs.run_chain_scanner(
            "TGB", target_years=0.25, output_dir=str(tmp_path))
    finally:
        monkeypatch.undo()
    return files, interp, result


def test_scanner_df_has_dealer_sign_column(tmp_path):
    """scan_chain must attach the engine's sign column -- the wiring that
    killed the inline call=+/put=- heuristic."""
    _, _, result = _run_scan(tmp_path)
    assert "dealer_sign" in result.df.columns
    assert set(result.df["dealer_sign"].unique()) <= {-1.0, 0.0, 1.0}
    # With a bullish bias and OTM-gated legs, at least one ±1 must appear
    # (the OTM replicating set is non-empty for this synthetic chain).
    assert (result.df["dealer_sign"] != 0.0).any()


def test_scanner_signs_match_engine_resolver(tmp_path):
    """Every scanner sign must equal compute_expiry_sign_map's answer for
    the same (strike, right) -- the shared single source of truth."""
    _, _, result = _run_scan(tmp_path)
    chain_iv = {
        (float(row["strike"]), str(row["right"]).strip().upper()[:1]): float(row["iv"])
        for _, row in result.df.iterrows()
        if float(row["iv"]) > 0
    }
    engine_signs = dp.compute_expiry_sign_map(
        "TGB", chain_iv, SPOT, forward=SPOT, tte=60 / 365, expiration="20261120")
    for _, row in result.df.iterrows():
        key = (float(row["strike"]), str(row["right"]).strip().upper()[:1])
        assert row["dealer_sign"] == engine_signs.get(key, 0.0), key


def test_scanner_vanna_equals_dealer_engine_vanna(tmp_path):
    """The scanner's vanna positioning MUST be the dealer engine's series:
    net == sum(engine vanna_shares_by_strike), call/put split == engine
    fields. This is the parity invariant Jason keeps checking -- the scanner
    chart and the dealer 4-panel chart cannot disagree."""
    _, _, result = _run_scan(tmp_path)
    dr = result.dealer_result
    assert dr is not None
    vanna_info = ocs.compute_vanna_positioning(dr, SPOT)
    assert abs(vanna_info["net_vanna_shares"] - float(np.sum(dr.vanna_shares_by_strike))) < 1e-6
    assert abs(vanna_info["call_vanna_shares"] - float(dr.vanna_call_shares)) < 1e-6
    assert abs(vanna_info["put_vanna_shares"] - float(dr.vanna_put_shares)) < 1e-6
    # The ScanResult's own net vanna is the same series (computed by scan_chain
    # from the engine result, not from the single-expiry df).
    assert abs(result.net_vanna_shares - float(np.sum(dr.vanna_shares_by_strike))) < 1e-6
    # call + put must reconcile to net (same signed scaling).
    assert abs((dr.vanna_call_shares + dr.vanna_put_shares)
               - float(np.sum(dr.vanna_shares_by_strike))) < 1e-6


def test_plot_scanner_charts_uses_dealer_engine_result(tmp_path):
    """plot_scanner_charts must render the dealer engine's vanna series
    (result.dealer_result); without it the chart must raise rather than
    recompute vanna from the single-expiry chain."""
    _, _, result = _run_scan(tmp_path)
    # The charting path reads result.dealer_result; if missing it must raise
    # rather than silently fall back to a heuristic.
    from dataclasses import replace
    bad_result = replace(result, dealer_result=None)
    with pytest.raises(ValueError):
        ocs.plot_scanner_charts(bad_result, output_dir=str(tmp_path))
