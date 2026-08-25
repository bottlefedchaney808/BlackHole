"""Round-9 (R9) symmetric convention-distance REPAIR tests — network-free.

Covers the minimal repair R2 (deleg_8d589a97) required before Cem's
re-admission-to-evidence (NOT promotion) ruling:

  - corrected benchmark framing: "the new engine's −1×BS_vanna moneyness sign map",
    NOT a flat −1 baseline; delta_new ±1 split near-balanced (~50/50)
  - robustness diagnostics: leave-one-cluster-out (12 rows, one per cluster),
    family-balanced D (SPY-only / QQQ-only), row n_eff + cluster K_eff (with bounds),
    distance-class ledger (0/1/2) with row counts AND OI·vanna mass under BOTH
    weightings (|sign·OI·vanna| conservative and |OI·vanna| convention-independent)
  - cluster-as-unit binomial gate (n=12, >= 8/12 bar, exact null P)
  - fragility-qualified verdict "OPEN (not certified)"
  - binding promotion blockers unchanged (sign-arm FAIL, convention-bound correlational)

Network-free: pure functions + synthetic fixtures + the persisted v6/v5 obs where
present. No ThetaData / .env / clock / sockets.
"""

import inspect
import json
import os

import run_dual_pipeline_gate_v7 as v7


# ---------------------------------------------------------------------------
# Corrected benchmark framing: moneyness sign map, NOT flat −1
# ---------------------------------------------------------------------------
def test_delta_new_split_counts():
    rows, all_pairs = v7.build_all_pairs()
    split = v7.delta_new_split(all_pairs)
    assert split["n"] == 447
    assert split["n_plus"] + split["n_minus"] + split["n_zero"] == split["n"]
    # near-balanced ±1 map (R2: −1:223 / +1:224)
    assert abs(split["pct_plus"] - split["pct_minus"]) < 5.0
    assert split["n_plus"] >= 150 and split["n_minus"] >= 150
    assert split["n_zero"] == 0  # new-engine delta measured nonzero everywhere


def test_benchmark_framing_label_present():
    rows, all_pairs = v7.build_all_pairs()
    split = v7.delta_new_split(all_pairs)
    src = inspect.getsource(v7)
    # label must name the moneyness sign map
    assert (
        "−1×BS_vanna MONEYNESS SIGN MAP" in src or "moneyness sign map" in src.lower()
    )
    # must NOT claim a flat −1 baseline
    assert "flat −1×BS benchmark" not in src
    assert "flat -1×BS baseline" not in src
    assert "hug the −1 baseline" not in src
    assert "hugs the −1 baseline" not in src
    assert abs((split["pct_plus"] + split["pct_minus"]) - 100.0) < 1e-6


# ---------------------------------------------------------------------------
# Weighting helpers
# ---------------------------------------------------------------------------
def test_weight_conservative_zero_on_deadband():
    # deadband (sign=0) -> zero weight under the conservative estimand
    assert v7.weight_conservative(0, 100.0, 0.2) == 0.0


def test_weight_conservative_confident_sign():
    assert abs(v7.weight_conservative(-1, 100.0, 0.2) - 20.0) < 1e-12
    assert abs(v7.weight_conservative(1, 100.0, 0.2) - 20.0) < 1e-12


def test_weight_independent_keeps_deadband_mass():
    # convention-independent keeps deadband rows at full weight
    assert abs(v7.weight_convention_independent(100.0, 0.2) - 20.0) < 1e-12
    assert abs(v7.weight_convention_independent(50.0, 0.1) - 5.0) < 1e-12


def test_weightings_identical_for_confident_sign():
    # for sign=±1 rows the two weightings are mathematically identical
    for s in (-1, 1):
        assert (
            abs(
                v7.weight_conservative(s, 100.0, 0.2)
                - v7.weight_convention_independent(100.0, 0.2)
            )
            < 1e-12
        )


# ---------------------------------------------------------------------------
# Effective sample sizes
# ---------------------------------------------------------------------------
def test_n_eff_formula_and_bounds():
    # uniform weights -> n_eff == n
    assert abs(v7.n_eff([1.0] * 10) - 10.0) < 1e-9
    # single nonzero weight -> n_eff == 1
    assert abs(v7.n_eff([0.0] * 9 + [5.0]) - 1.0) < 1e-9
    assert abs(v7.n_eff([]) - 0.0) < 1e-9
    # bounds always hold: 1 <= n_eff <= len
    import random

    random.seed(0)
    for _ in range(50):
        w = [random.random() * 10 for _ in range(20)]
        ne = v7.n_eff(w)
        assert 1.0 <= ne <= 20.0


def test_k_eff_formula():
    assert abs(v7.k_eff([1.0] * 12) - 12.0) < 1e-9
    assert v7.k_eff([100.0, 1.0, 1.0, 1.0]) > 1.0
    assert v7.k_eff([100.0, 1.0, 1.0, 1.0]) < 4.0  # concentrated -> well under 12


def test_real_row_neff_and_keff_bounds():
    rows, all_pairs = v7.build_all_pairs()
    row_w = [p["weight_conv"] for p in all_pairs]
    ne = v7.n_eff(row_w)
    assert 1.0 <= ne <= len(all_pairs)
    clw = {}
    for p in all_pairs:
        clw[p["cluster"]] = clw.get(p["cluster"], 0.0) + p["weight_conv"]
    ke = v7.k_eff(list(clw.values()))
    assert 1.0 <= ke <= 12.0
    # R2 anchor: K_eff ~5, row n_eff ~35
    assert 3.0 < ke < 9.0
    assert 15.0 < ne < 60.0


# ---------------------------------------------------------------------------
# Distance-class ledger
# ---------------------------------------------------------------------------
def test_distance_class_ledger_counts_and_masses():
    rows, all_pairs = v7.build_all_pairs()
    for wk in ("weight_conv", "weight_ind"):
        classes = v7.distance_class_ledger(all_pairs, wk)
        assert set(classes.keys()) == {0, 1, 2}
        tot_rows = sum(c["rows"] for c in classes.values())
        assert tot_rows == len(all_pairs)
        tot_mass = sum(c["mass"] for c in classes.values())
        assert abs(tot_mass - sum(p["oi_vanna_mass"] for p in all_pairs)) < 1e-6
        for d in (0, 1, 2):
            assert classes[d]["rows"] >= 0
            assert classes[d]["mass"] >= 0.0
            assert 0.0 <= classes[d]["mass_share"] <= 1.0
        # mass shares sum to 1
        assert abs(sum(c["mass_share"] for c in classes.values()) - 1.0) < 1e-9


def test_real_distance_ledger_matches_r2_anchors():
    rows, all_pairs = v7.build_all_pairs()
    cls = v7.distance_class_ledger(all_pairs, "weight_conv")
    # R2: dist0=95 rows (majority confident mass agrees), dist1=297 deadband,
    # dist2=55 confident opposite-sign rows
    assert cls[1]["rows"] == 297
    assert cls[0]["rows"] == 95
    assert cls[2]["rows"] == 55
    assert cls[2]["mass"] > 0.0 and cls[0]["mass"] > cls[2]["mass"]
    # confident-disagreement rate (dist2 / confident rows) ~36.7%
    conf = cls[0]["rows"] + cls[2]["rows"]
    rate = cls[2]["rows"] / conf
    assert 0.25 < rate < 0.50


# ---------------------------------------------------------------------------
# Family-balanced D
# ---------------------------------------------------------------------------
def test_family_dconv_both_families_present():
    rows, all_pairs = v7.build_all_pairs()
    fam = v7.family_dconv(all_pairs, "weight_conv")
    assert set(fam.keys()) == {"SPY", "QQQ"}
    for fk, f in fam.items():
        assert f["n_clusters"] > 0 and f["n_rows"] > 0
        assert 0.0 <= f["D"] <= 2.0
    # each family has 6 clusters (12 total split SPY/QQQ)
    assert fam["SPY"]["n_clusters"] == 6 and fam["QQQ"]["n_clusters"] == 6
    # R2 anchors: SPY-only 0.6783, QQQ-only 0.8816 — both clear 0.50
    assert fam["SPY"]["D"] > 0.50
    assert fam["QQQ"]["D"] > 0.50
    assert fam["SPY"]["D"] < fam["QQQ"]["D"]


# ---------------------------------------------------------------------------
# Leave-one-cluster-out
# ---------------------------------------------------------------------------
def test_loo_12_rows_one_per_cluster():
    rows, all_pairs = v7.build_all_pairs()
    loo, full_D = v7.leave_one_cluster_out(all_pairs, "weight_conv")
    clusters = sorted({p["cluster"] for p in all_pairs})
    assert len(clusters) == 12
    assert len(loo) == 12
    omitted = [r["omitted"] for r in loo]
    assert sorted(omitted) == clusters
    for r in loo:
        assert r["n_clusters"] == 11
        assert r["n_rows"] == 447 - sum(
            1 for p in all_pairs if p["cluster"] == r["omitted"]
        )


def test_loo_spy0728_is_single_hinge():
    rows, all_pairs = v7.build_all_pairs()
    loo, full_D = v7.leave_one_cluster_out(all_pairs, "weight_conv")
    flips = [r for r in loo if r["flips_to_fail"]]
    # R2: SPY-0728 is the single cluster whose drop flips pooled D below 0.50
    assert [r["omitted"] for r in flips] == ["SPY_20260728"]
    spy0728 = [r for r in loo if r["omitted"] == "SPY_20260728"][0]
    assert spy0728["D"] < 0.50


# ---------------------------------------------------------------------------
# Cluster-as-unit binomial gate
# ---------------------------------------------------------------------------
def test_exact_binomial_reference_values():
    # exact P(X>=k | Binomial(12,0.5)) reference values
    refs = {8: 0.193848, 9: 0.072998, 10: 0.019287, 11: 0.003174, 12: 0.000244}
    for k, expected in refs.items():
        assert abs(v7.exact_binom_ge(k, 12, 0.5) - expected) < 1e-4


def test_cluster_gate_n12_bar8_exact_null():
    rows, all_pairs = v7.build_all_pairs()
    k, n, aligned_clusters, P, clears = v7.cluster_as_unit_gate(
        all_pairs, "weight_conv"
    )
    assert n == 12
    assert v7.BAR_2_3 == 8
    assert k == len(aligned_clusters)
    assert (
        abs(P - sum(__import__("math").comb(12, x) for x in range(k, 13)) / 2**12)
        < 1e-9
    )
    # R2/R9: 6/12 aligned, chance-median -> does NOT clear the 2/3 bar
    assert k == 6
    assert clears is False
    assert P > 0.5  # chance-compatible


def test_cluster_gate_independent_weights_deadband():
    rows, all_pairs = v7.build_all_pairs()
    kc, nc, ac, Pc, clr = v7.cluster_as_unit_gate(all_pairs, "weight_conv")
    ki, ni, ai, Pi, clri = v7.cluster_as_unit_gate(all_pairs, "weight_ind")
    # n and bar are weighting-independent
    assert nc == ni == 12 and v7.BAR_2_3 == 8
    # alignment is NOT weighting-independent: under the convention-independent
    # weighting deadband rows sit at full weight (dist 1), inflating every
    # cluster's D_conv to >= 0.50 -> all 12 align. This is exactly the R2
    # caution: |OI·vanna| is more favorable, conservative |sign·OI·vanna| is
    # the honest bar.
    assert kc == 6  # conservative: chance-median, fails the 2/3 bar
    assert ki == 12  # convention-independent: all align
    assert Pc > 0.5 and Pi < 0.01
    assert clr is False and clri is True
    # conservative is the binding/fragility-honest gate
    assert (
        v7.build_verdict(
            all_pairs,
            v7.delta_new_split(all_pairs),
            v7.family_dconv(all_pairs, "weight_conv"),
            v7.cluster_as_unit_gate(all_pairs, "weight_conv"),
        )[0]
        == "OPEN (not certified)"
    )


def test_cluster_gate_synthetic_clear():
    # synthetic: 9 clusters with D>=0.5 (weighting-dependent alignment) -> clears 8/12
    pairs = []
    from run_dual_pipeline_gate_v6 import RESOLVABLE

    for i, (tk, day, exp, src) in enumerate(RESOLVABLE):
        aligned = i < 9
        dp = 1 if aligned else -1
        pairs.append(
            {
                "cluster": f"{tk}_{day}",
                "family": tk,
                "delta_prod": dp,
                "delta_new": -dp,
                "dist": 2 if aligned else 0,
                "weight_conv": 10.0 if aligned else 10.0,
                "weight_ind": 10.0,
                "oi_vanna_mass": 10.0,
                "oi": 1.0,
                "prod_vanna": 1.0,
            }
        )
    k, n, a, P, clears = v7.cluster_as_unit_gate(pairs, "weight_conv")
    assert k == 9 and n == 12 and clears is True
    assert abs(P - v7.exact_binom_ge(9, 12, 0.5)) < 1e-9


# ---------------------------------------------------------------------------
# Pooled both-weightings
# ---------------------------------------------------------------------------
def test_pooled_both_weightings():
    rows, all_pairs = v7.build_all_pairs()
    Dc, totc, disagc, n = v7.pooled_dconv(all_pairs, "weight_conv")
    Di, toti, disagi, _ = v7.pooled_dconv(all_pairs, "weight_ind")
    # R2 anchors: conservative 0.7445, convention-independent 0.8948
    assert abs(Dc - 0.7445) < 0.01
    assert abs(Di - 0.8948) < 0.01
    assert Di > Dc  # convention-independent (deadband at full weight) is higher
    assert n == 447


# ---------------------------------------------------------------------------
# Verdict + binding blockers
# ---------------------------------------------------------------------------
def test_verdict_open_not_certified():
    rows, all_pairs = v7.build_all_pairs()
    split = v7.delta_new_split(all_pairs)
    fam = v7.family_dconv(all_pairs, "weight_conv")
    gate = v7.cluster_as_unit_gate(all_pairs, "weight_conv")
    verdict, reason = v7.build_verdict(all_pairs, split, fam, gate)
    assert verdict == "OPEN (not certified)"
    assert "single-cluster-hinged" in reason
    assert "SPY-0728" in reason
    assert "NOT certified" in reason
    assert "NOT promotion" in reason


def test_binding_blockers_unchanged():
    # sign-arm FAIL and convention-bound correlational remain binding blockers
    assert "FAIL" in v7.SIGN_ARM_STATUS and "3/9" in v7.SIGN_ARM_STATUS
    assert "BINDING BLOCKER" in v7.CORR_STATUS
    assert "reflexivity" in v7.CORR_STATUS


def test_verdict_present_in_obs():
    if not os.path.exists(v7.V7_OBS):
        import pytest

        pytest.skip("v7 obs not generated yet; run run_dual_pipeline_gate_v7.py")
    d = json.load(open(v7.V7_OBS, encoding="utf-8"))
    assert d["verdict"] == "OPEN (not certified)"
    assert "moneyness sign map" in d["benchmark_framing"].lower()
    assert d["delta_new_split"]["n"] == 447
    assert d["cluster_gate_conservative"]["bar_2_3"] == 8
    assert d["cluster_gate_conservative"]["n"] == 12
    assert "leave_one_cluster_out_conservative" in d
    assert "family" in d
    assert "distance_classes_conservative" in d


# ---------------------------------------------------------------------------
# Network-free guarantee
# ---------------------------------------------------------------------------
def test_v7_module_is_network_free():
    # Scan executable code only (strip the docstring, which legitimately talks
    # about .env/thetadata but does not use them).
    code = inspect.getsource(v7)
    # drop module docstring
    if code.startswith('"""'):
        code = code[code.find('"""', 3) + 3 :]
    for bad in (
        "import socket",
        "import urllib",
        "import requests",
        "from requests",
        "import httpx",
        "import subprocess",
        "os.environ",
    ):
        assert bad not in code, bad
    # v6 import must not pull network on import (it is pure functions)
    assert v6_import_clean()


def v6_import_clean():
    import run_dual_pipeline_gate_v6 as v6

    s = inspect.getsource(v6)
    for bad in ("socket", "urllib", "requests.", "httpx", "subprocess"):
        assert bad not in s.lower()
    return True
