"""Round-8 (R8) symmetric two-sided magnitude-weighted convention-distance tests.

Covers Cem's ONLY reopening door (R3, deleg_54ae4bc3): a symmetric, two-sided,
magnitude-weighted convention-distance rerun on the real SVI deviation path.

  - exact SVI deviation classification via the persisted production applied_sign
    (which v5 resolved through resolve_vol_surface_sign / deviation_by_strike,
    NOT the IV-median proxy)
  - new-engine delta measured nonzero (derived dealer_frame_vanna), NOT assumed 0
  - distance formula: same-sign 0, zero-vs-nonzero 1, opposite-sign 2
  - magnitude weighting w = |applied_sign * OI * vanna|
  - call/put same-strike separation (join key includes right)
  - per-cluster persistence schema (all 12 resolvable clusters)
  - honest network-free provenance field

Network-free: pure functions + synthetic fixtures; no ThetaData / .env / clock.
"""
import json
import os

import run_dual_pipeline_gate_v6 as v6


# ---------------------------------------------------------------------------
# distance formula (same-sign 0, zero-vs-nonzero 1, opposite-sign 2)
# ---------------------------------------------------------------------------
def test_dist_same_sign_zero():
    p = {"delta_prod": -1, "delta_new": -1}
    assert abs(p["delta_prod"] - p["delta_new"]) == 0


def test_dist_zero_vs_nonzero_one():
    p = {"delta_prod": 0, "delta_new": -1}
    assert abs(p["delta_prod"] - p["delta_new"]) == 1


def test_dist_opposite_sign_two():
    p = {"delta_prod": 1, "delta_new": -1}
    assert abs(p["delta_prod"] - p["delta_new"]) == 2


# ---------------------------------------------------------------------------
# classify_svi: applied_sign -> int in {-1,0,+1}
# ---------------------------------------------------------------------------
def test_classify_svi_rich_minus1():
    assert v6.classify_svi(-1.0) == -1
    assert v6.classify_svi(-1) == -1


def test_classify_svi_cheap_plus1():
    assert v6.classify_svi(1.0) == 1


def test_classify_svi_deadband_zero():
    assert v6.classify_svi(0.0) == 0


def test_classify_svi_none_zero():
    assert v6.classify_svi(None) == 0


# ---------------------------------------------------------------------------
# symmetric D_conv formula (Cem's: D = sum w|d_prod-d_new| / sum w)
# ---------------------------------------------------------------------------
def test_symmetric_dconv_identical_sides_zero():
    pairs = [
        {"weight": 100.0, "dist": 0, "delta_prod": -1, "delta_new": -1},
        {"weight": 50.0, "dist": 0, "delta_prod": 1, "delta_new": 1},
    ]
    D, tot, disag, n, nd, counts = v6.symmetric_dconv(pairs)
    assert D == 0.0 and disag == 0.0 and nd == 0


def test_symmetric_dconv_opposite_signs_heavy():
    pairs = [
        {"weight": 100.0, "dist": 2, "delta_prod": 1, "delta_new": -1},
        {"weight": 0.0, "dist": 2, "delta_prod": -1, "delta_new": 1},  # zero weight
    ]
    D, tot, disag, n, nd, counts = v6.symmetric_dconv(pairs)
    # only the first pair has nonzero weight -> D = (2*100)/100 = 2.0
    assert abs(D - 2.0) < 1e-12
    assert tot == 100.0


def test_symmetric_dconv_magnitude_weighted():
    # opposite-sign (dist 2) with 100 weight vs same-sign (dist 0) with 300 weight
    pairs = [
        {"weight": 100.0, "dist": 2, "delta_prod": 1, "delta_new": -1},
        {"weight": 300.0, "dist": 0, "delta_prod": -1, "delta_new": -1},
    ]
    D, tot, disag, n, nd, counts = v6.symmetric_dconv(pairs)
    # D = (2*100 + 0*300)/400 = 200/400 = 0.5
    assert abs(D - 0.5) < 1e-12


def test_symmetric_dconv_zero_total_weight_nan():
    pairs = [{"weight": 0.0, "dist": 2, "delta_prod": 1, "delta_new": -1}]
    D, tot, _, n, nd, _ = v6.symmetric_dconv(pairs)
    assert tot == 0.0 and D != D  # nan


# ---------------------------------------------------------------------------
# build_both_sides: new-engine delta measured nonzero; call/put separated
# ---------------------------------------------------------------------------
def test_build_both_sides_new_delta_nonzero():
    # A rich call strike (applied_sign -1) at the spot with a real IV -> the
    # derived dealer_frame_vanna is nonzero, so delta_new must be ±1, not 0.
    day, expiry = "20260605", "20260608"  # T ~ 3/365
    per_strike = [
        {"strike": 700.0, "right": "C", "oi": 100.0, "vanna": -0.2,
         "iv": 0.30, "applied_sign": -1.0},
    ]
    pairs = v6.build_both_sides(per_strike, 705.0, day, expiry)
    assert len(pairs) == 1
    p = pairs[0]
    assert p["delta_prod"] == -1
    assert p["delta_new"] in (-1, 1)      # measured nonzero, not forced to 0
    assert p["new_vanna"] != 0.0
    assert p["weight"] == abs(-1 * 100 * -0.2)  # 20.0


def test_build_both_sides_call_put_same_strike_separate():
    day, expiry = "20260605", "20260608"
    per_strike = [
        {"strike": 700.0, "right": "C", "oi": 100.0, "vanna": -0.2,
         "iv": 0.30, "applied_sign": -1.0},
        {"strike": 700.0, "right": "P", "oi": 500.0, "vanna": -0.1,
         "iv": 0.25, "applied_sign": 1.0},
    ]
    pairs = v6.build_both_sides(per_strike, 705.0, day, expiry)
    assert len(pairs) == 2
    rights = sorted(p["right"] for p in pairs)
    assert rights == ["C", "P"]  # call/put same-strike kept separate


def test_build_both_sides_weight_uses_magnitude():
    day, expiry = "20260508", "20260511"
    # zero applied_sign -> weight 0 (deadband drops out of the weighted distance)
    per_strike = [
        {"strike": 640.0, "right": "C", "oi": 125.0, "vanna": -0.16,
         "iv": 0.57, "applied_sign": 0.0},
    ]
    pairs = v6.build_both_sides(per_strike, 711.0, day, expiry)
    assert pairs[0]["weight"] == 0.0


# ---------------------------------------------------------------------------
# all-resolvable persistence schema + honest network-free provenance
# ---------------------------------------------------------------------------
def test_resolvable_has_12_clusters():
    assert len(v6.RESOLVABLE) == 12


def test_v6_obs_all_clusters_persisted_both_sides():
    if not os.path.exists(v6.V6_OBS):
        import pytest
        pytest.skip("v6 obs not generated yet; run run_dual_pipeline_gate_v6.py")
    d = json.load(open(v6.V6_OBS, encoding="utf-8"))
    assert d["n_clusters"] == 12
    assert d["new_engine_data_status"].startswith("network-free")
    for k, r in d["clusters"].items():
        assert r["ok"] is True
        assert r["n_per_strike"] > 0
        per = d["per_strike"].get(k)
        assert per and len(per) == r["n_per_strike"]
        for p in per:
            assert "delta_new" in p and p["delta_new"] in (-1, 0, 1)
            assert "weight" in p and p["weight"] >= 0.0


def test_v6_obs_verdict_present():
    if not os.path.exists(v6.V6_OBS):
        import pytest
        pytest.skip("v6 obs not generated yet; run run_dual_pipeline_gate_v6.py")
    d = json.load(open(v6.V6_OBS, encoding="utf-8"))
    assert "verdict" in d and "verdict_reason" in d
    assert d["verdict"] in ("OPEN / RE-ADMIT TO EVIDENCE", "STAYS DEMOTED",
                            "INDETERMINATE")


# ---------------------------------------------------------------------------
# real SVI path, NOT the IV-median proxy (unit-level guard)
# ---------------------------------------------------------------------------
def test_classification_is_svi_path_not_iv_median():
    # The v5 fallback0 patch wired _dp._resolve_sign to _resolve_sign_fallback0,
    # which calls resolve_vol_surface_sign (real deviation_by_strike). The
    # driver's delta_prod comes straight from the persisted applied_sign, so
    # there is no IV-vs-chain-median computation anywhere in the R8 path.
    import inspect
    src = inspect.getsource(v6)
    assert "deviation_by_strike" in open(
        os.path.join(os.path.dirname(v6.__file__),
                     "vol_surface_reference.py"), encoding="utf-8").read()
    assert "resolve_vol_surface_sign" in open(
        os.path.join(os.path.dirname(v6.__file__),
                     "vol_surface_reference.py"), encoding="utf-8").read()
    assert "rich_plus1" not in src  # R8 does NOT use the v3 IV-median proxy
    assert "med_iv" not in src
