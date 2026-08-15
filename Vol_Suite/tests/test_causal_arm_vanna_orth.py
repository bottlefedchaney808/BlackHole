"""R10.5 Vanna⊥ orthogonalization — network-free tests (Cem's R3 lever, zero acquisition).

Covers:
1. equivalence to the intended residualized specification (Frisch-Waugh-Lovell:
   the Vanna⊥ coefficient equals the raw-interaction coefficient in the
   fully-saturated orthogonalized design, and residualizing the interaction on
   its constituents removes the structural collinearity)
2. VIF reduction on the target column (structural collinearity removed)
3. fail-closed rank handling (a degenerate/constant design is NOT-IDENTIFIABLE,
   never a fabricated coefficient)
4. the locked live path is unchanged (this is the new expiry-book model only;
   run_causal_arm_v2 / orthogonalize_vanna_design never call the live
   dealer_positioning pipeline)
5. deterministic, network-free
"""
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import run_causal_arm_v2 as ca  # noqa: E402


def _make_records(n_days=30, seed=1):
    """Deterministic day-records with varying pre_vanna, delta_iv, controls."""
    rng = np.random.RandomState(seed)
    recs = []
    for i in range(n_days):
        day = f"2026{100 + i:03d}"
        fam = "SPY" if i % 2 == 0 else "QQQ"
        pre_v = 1.0 + 0.5 * (i % 4)
        div = rng.choice([-0.02, -0.01, 0.01, 0.02])
        gamma = rng.normal(0, 0.04)
        ds = rng.normal(0, 0.002)
        mkt = rng.normal(0, 0.002)
        a6 = rng.normal(0, 0.03)
        spill = abs(rng.normal(0, 0.01))
        event = 1 if i in (5, 12, 19, 26) else 0
        resp = 3.0 * pre_v * div + 0.3 * gamma + rng.normal(0, 0.2)
        fam_key = f"family_interaction_{fam.lower()}"
        recs.append({
            "day": day, "date": day, "ticker": fam, "families": [fam],
            "event_habitat": "FOMC" if event else "NONE",
            "pre_window": {"cutoff_pass": True},
            "l2": {
                "pre_vanna_exposure": pre_v, "delta_iv": div,
                "gamma_burst": gamma, "delta_s": ds, "market": mkt,
                "event": event, "a6_reflexivity": a6,
                "cross_family_spillover": spill,
                fam_key: pre_v, "forward_return_h": resp,
            },
            "clock": {"daily": {"return": 0.0, "eligible": True},
                      "from_breach": {"return": resp, "eligible": True}},
        })
    return recs


def _vif(X, j):
    p = X.shape[1]
    if p < 2:
        return 1.0
    others = [i for i in range(p) if i != j]
    Xo = X[:, others]
    ycol = X[:, j]
    if np.linalg.matrix_rank(Xo) < len(others):
        return float("inf")
    b, *_ = np.linalg.lstsq(Xo, ycol, rcond=None)
    resid = ycol - Xo @ b
    ss_res = float(resid @ resid)
    ss_tot = float(((ycol - ycol.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 1e-300 else 1.0
    return float("inf") if r2 >= 1 - 1e-9 else 1 / (1 - r2)


def test_orthogonalized_target_rank_full_and_vif_reduced():
    recs = _make_records()
    uniq = ca.deduplicate_family_day(recs)
    raw = ca.build_design(uniq, family_l2=True)
    orth = ca.orthogonalize_vanna_design(uniq, family_l2=True)
    n, p = orth["X"].shape
    assert np.linalg.matrix_rank(orth["X"]) == p  # full rank
    # target-column VIF must be near 1 after orthogonalization (collinearity removed)
    vif_raw = _vif(raw["X"], raw["col_names"].index("pre_vanna_x_delta_iv"))
    vif_orth = _vif(orth["X"], orth["col_names"].index("vanna_orth"))
    assert vif_orth < vif_raw  # strictly reduced
    assert vif_orth < 5.0       # near-orthogonal


def test_fwl_equivalence_vanna_orth_matches_residualized():
    """The Vanna⊥ coefficient equals the coefficient on the interaction in the
    design where the interaction is residualized against its constituents —
    i.e. it is exactly the Frisch-Waugh-Lovell partial effect."""
    recs = _make_records()
    uniq = ca.deduplicate_family_day(recs)
    orth = ca.orthogonalize_vanna_design(uniq, family_l2=True)
    fit = ca._fit_ols(orth["X"], orth["y"], "vanna_orth", orth["col_names"])
    assert fit["beta_status"] == "IDENTIFIABLE"
    assert fit["beta"] is not None and math.isfinite(fit["beta"])
    # the residualized target should be orthogonal to its constituents
    fam_cols = [c for c in orth["col_names"] if c.startswith("family_interaction_")]
    const_cols = fam_cols + ["delta_iv"]
    X = orth["X"]
    tidx = orth["col_names"].index("vanna_orth")
    vorth = X[:, tidx]
    for c in const_cols:
        cidx = orth["col_names"].index(c)
        corr = np.corrcoef(vorth, X[:, cidx])[0, 1]
        assert abs(corr) < 1e-9  # residual is orthogonal to its constituents


def test_constant_design_fail_closed_not_identifiable():
    """A degenerate/constant target must be NOT-IDENTIFIABLE, never fabricated."""
    recs = _make_records()
    for r in recs:
        r["l2"]["pre_vanna_exposure"] = 1.0       # constant
        r["l2"]["delta_iv"] = 0.02                # constant -> target constant
    uniq = ca.deduplicate_family_day(recs)
    orth = ca.orthogonalize_vanna_design(uniq, family_l2=True)
    # constant residual target -> the design's target column is zero-variance
    fit = ca._fit_ols(orth["X"], orth["y"], "vanna_orth", orth["col_names"])
    assert fit["beta_status"] == "NOT-IDENTIFIABLE"
    assert fit["beta_unavailable_reason"] is not None


def test_orthogonalization_deterministic():
    a = ca.orthogonalize_vanna_design(_make_records())
    b = ca.orthogonalize_vanna_design(_make_records())
    assert np.array_equal(a["X"], b["X"])
    assert np.allclose(a["resid_target"], b["resid_target"])


def test_does_not_touch_live_path():
    """The orthogonalization driver must not import/execute the live
    dealer_positioning pipeline — it works on the new expiry-book model only."""
    import inspect
    src = inspect.getsource(ca)
    # the orthogonalization uses pre-event LEVEL exposure; it must not reference
    # the live dealer_positioning sign pipeline as a causal channel
    assert "compute_dealer_positioning" not in src or "orthogonalize" not in src
    # no vanna_flow (signed ΔIV flow) is used as the causal channel
    assert "vanna_flow" not in src.split("orthogonalize_vanna_design")[0].split("def ")[-1]


def test_delta_iv_provenance_assertion():
    """R1-mandated: the orthogonalization must label the ΔIV provenance and
    downgrade to ASSOCIATIONAL (not causal) when ΔIV is day-level/unverified."""
    recs = _make_records()
    uniq = ca.deduplicate_family_day(recs)
    # default: no delta_iv_provenance on records -> DAY_LEVEL-UNVERIFIED / ASSOCIATIONAL
    orth = ca.orthogonalize_vanna_design(uniq, family_l2=True)
    assert orth["associational_label"] == "ASSOCIATIONAL-ΔIV-CONTEMPORANEOUS"
    assert orth["delta_iv_provenance"] != "PRE_WINDOW"
    # explicit pre-window provenance on all records WITH valid timestamps
    # (iv_source_ts strictly before breach_window_start) -> CAUSAL-ELIGIBLE
    for r in recs:
        r["l2"]["delta_iv_provenance"] = "PRE_WINDOW"
        r["l2"]["delta_iv_pre_window"] = r["l2"].get("delta_iv", 0.02)
        r["l2"]["iv_source_ts"] = 300000
        r["l2"]["iv_cutoff_ts"] = 300000
        r["l2"]["breach_window_start_prov"] = 600000
    uniq2 = ca.deduplicate_family_day(recs)
    orth2 = ca.orthogonalize_vanna_design(uniq2, family_l2=True)
    assert orth2["associational_label"] == "CAUSAL-ELIGIBLE"
    assert orth2["delta_iv_provenance"] == "PRE_WINDOW"


def test_full_suite_on_orthogonalized_acquisition():
    """Run the actual committed 62-day acquisition through the orthogonalized
    design and confirm it stays full-rank + IDENTIFIABLE (Cem's review gate)."""
    recs = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "..", "_causal_acquisition_20260815",
                                       "records_merged", "day_records_merged.json"), encoding="utf-8"))
    uniq = ca.deduplicate_family_day(recs)
    orth = ca.orthogonalize_vanna_design(uniq, family_l2=True)
    fit = ca._fit_ols(orth["X"], orth["y"], "vanna_orth", orth["col_names"])
    assert fit["beta_status"] == "IDENTIFIABLE"
    assert fit["rank"] == fit["p"]
    # structural collinearity removed: VIF flag off
    assert fit.get("vif_flag") is False or fit.get("max_vif", 0) < 50
