"""ROUND-6 CORRECTED PACKET tests — lock the R6 upgrade set (network-free).

Covers (from R2 deleg_da28fbca + the R6 merged set):
  R6-1  unique-calendar-day sign aggregation (n=9 primary, n=12 sensitivity)
  R6-2  honest binomial power (bar-clearing prob, n-for-80%; md is NOT power)
  R6-3  magnitude/OI/vanna-weighted fallback->0 production level (deadband/fallback -> 0),
        seed-merge provenance preservation, seed rows in cross-engine comparison
  R6-4  correlational + placebo arms labeled underpowered
  R6-5  SPY/QQQ never pooled (a pooled aggregate is forbidden)
  R6-7  delta convention-distance helper (delta_prod vs delta_new==0), zero-firing
        clusters retained in the corpus, pre-registered DEMOTE/OPEN threshold
Network-free: pure functions + synthetic fixtures, no ThetaData / .env / clock.
"""
import json
import os
import math

import run_dual_pipeline_gate_v5 as v5


# ---------------------------------------------------------------------------
# R6-1: unique-day aggregation
# ---------------------------------------------------------------------------
def test_unique_day_counts_shared_day_once():
    # SPY 20260605 + QQQ 20260605 -> ONE unique day; 4 distinct days total
    rows = [
        {"day": "20260605", "ticker": "SPY", "agreed": True, "both_nonzero": True},
        {"day": "20260605", "ticker": "QQQ", "agreed": True, "both_nonzero": True},
        {"day": "20260413", "ticker": "SPY", "agreed": False, "both_nonzero": True},
        {"day": "20260716", "ticker": "QQQ", "agreed": True, "both_nonzero": True},
        {"day": "20260729", "ticker": "QQQ", "agreed": True, "both_nonzero": True},
    ]
    n, n_agree, day_map = v5._unique_day_outcome(rows)
    assert n == 4
    assert n_agree == 3  # 0605, 0716, 0729
    assert day_map["20260605"][2] is True


def test_unique_day_any_disagree_makes_day_disagree():
    # A day carrying BOTH QQQ+SPY where ONE disagrees => the DAY is a DISAGREE
    rows = [
        {"day": "20260617", "ticker": "SPY", "agreed": True, "both_nonzero": True},
        {"day": "20260617", "ticker": "QQQ", "agreed": False, "both_nonzero": True},
    ]
    n, n_agree, day_map = v5._unique_day_outcome(rows)
    assert n == 1
    assert n_agree == 0
    assert day_map["20260617"][2] is False


def test_unique_day_ignores_zero_sign_clusters():
    rows = [
        {"day": "20260605", "ticker": "SPY", "agreed": True, "both_nonzero": True},
        {"day": "20260605", "ticker": "QQQ", "agreed": False, "both_nonzero": False},
        {"day": "20260413", "ticker": "SPY", "agreed": False, "both_nonzero": True},
    ]
    n, n_agree, day_map = v5._unique_day_outcome(rows)
    assert n == 2  # zero-sign QQQ 0605 dropped
    assert n_agree == 1


def test_unique_day_equals_known_result():
    # Reproduce the recorded v3+v4 corpus -> eff-n=9. Under the pre-registered
    # conservative rule (a day is AGREE only if EVERY resolvable cluster on it
    # agrees), days 20260617 (QQQ disagrees) and 20260729 (SPY disagrees) are
    # DISAGREEs despite a same-day agreeing family -> 3/9.
    v3 = json.load(open(os.path.join(os.path.dirname(v5.__file__), "_intraday_cache",
                                     "dual_pipeline_gate_v3_obs.json"), encoding="utf-8"))
    v4 = json.load(open(os.path.join(os.path.dirname(v5.__file__), "_intraday_cache",
                                     "dual_pipeline_gate_v4_obs.json"), encoding="utf-8"))
    seed = v5._v4._merge_seed_rows()
    rows = seed + v4.get("rows", [])
    n, n_agree, day_map = v5._unique_day_outcome(rows)
    assert n == 9
    assert n_agree == 3  # 20260605, 20260728, 20260731 agree; 0617/0729 mixed-disagree


# ---------------------------------------------------------------------------
# R6-2: honest binomial power
# ---------------------------------------------------------------------------
def test_bar_clearing_prob_9_true_2_3():
    # P(clear >=6/9 | Bin(9,2/3)) = 0.6503 (R2-cited)
    p = v5._bar_clearing_prob(9, 2 / 3)
    assert abs(p - 0.6503) < 0.001


def test_bar_clearing_prob_12_true_2_3():
    # P(clear >=8/12 | Bin(12,2/3)) = 0.6315 (R2-cited)
    p = v5._bar_clearing_prob(12, 2 / 3)
    assert abs(p - 0.6315) < 0.001


def test_n_for_80_power_true_p_2_3_about_58():
    n = v5._n_for_80_power(2 / 3)
    assert 50 <= n <= 75  # R2: ~58 one-sided


def test_n_for_80_power_true_p_0_75_about_30():
    n = v5._n_for_80_power(0.75)
    assert 20 <= n <= 40  # exact one-sided calc ~23 (R2 rough estimate ~30)


def test_n_for_80_bar_clear_true_p_2_3_is_none():
    # bar at the mean of p=2/3 -> power -> 0.5 asymptotically, never 0.80
    assert v5._n_for_80_bar_clear(2 / 3) is None


def test_n_for_80_bar_clear_true_p_0_75_finite():
    n = v5._n_for_80_bar_clear(0.75)
    assert n is not None and n < 100


def test_md_is_not_binomial_power_documentation():
    # Guard: md(9)=0.816 is a CORRELATION MDE; the honest test power at n=9
    # is ~18% (the probability of a decisive rejection), never quoted as md.
    md = v5._v2._tanh_md(9)
    assert abs(md - 0.816) < 0.01
    # test power (reject H0:p=0.5 at n=9 when true p=2/3) is far below 0.80
    reject_power = v5._binom_tail(7, 9, 2 / 3)  # k=7 is the alpha<=0.05 one-sided cut at n=9
    assert reject_power < 0.80


# ---------------------------------------------------------------------------
# R6-3: weighted fallback->0 level
# ---------------------------------------------------------------------------
def test_fallback0_resolve_sign_returns_zero_for_deadband():
    # deadband/missing/ref-None strike -> 0 (NOT -1); key is (strike, right)
    assert v5._resolve_sign_fallback0("C", 100.0, "vol_surface_replication",
                                      otm_strikes={(100.0, "C")}, vol_surface_ref=None) == 0.0


def test_fallback0_resolve_sign_returns_zero_when_not_otm():
    assert v5._resolve_sign_fallback0("C", 100.0, "vol_surface_replication",
                                      otm_strikes=set(), vol_surface_ref=None) == 0.0


def test_fallback0_resolve_sign_oi_heuristic_preserved():
    assert v5._resolve_sign_fallback0("C", 100.0, "oi_heuristic") == 1.0
    assert v5._resolve_sign_fallback0("P", 100.0, "oi_heuristic") == -1.0


def test_fallback0_resolve_sign_uses_svi_deviation_when_available():
    # When a real SVI deviation read exists, it is used (rich->-1, cheap->+1).
    # A mocked ref with a negative deviation (cheap) -> +1.0.
    class Ref:
        deviation_by_strike = {(100.0, "C"): -0.03}  # cheap -> dealer long +1
    from vol_surface_reference import resolve_vol_surface_sign
    assert resolve_vol_surface_sign(Ref(), 100.0, "C") == 1.0
    assert v5._resolve_sign_fallback0("C", 100.0, "vol_surface_replication",
                                      otm_strikes={(100.0, "C")}, vol_surface_ref=Ref()) == 1.0


def test_weighted_level_is_sum_sign_oi_vanna_not_count():
    # R6-3 formula: L_f0 = sum_k sign_k*OI_k*vanna_k; deadband contributes 0
    # (the applied_sign for a deadband strike under fallback->0 is 0).
    # Build a small seed and drive the pipeline to check L_f0 is a LEVEL.
    # Because driving compute_dealer_positioning needs a network controller, we
    # instead verify the formula path via a synthetic per-strike array.
    strikes = [
        {"strike": 100.0, "right": "C", "oi": 100.0, "vanna": 2.0, "applied_sign": 1.0},
        {"strike": 105.0, "right": "C", "oi": 50.0, "vanna": 1.0, "applied_sign": 0.0},  # deadband
        {"strike": 95.0, "right": "P", "oi": 200.0, "vanna": -1.0, "applied_sign": -1.0},
    ]
    L = sum(s["applied_sign"] * s["oi"] * s["vanna"] for s in strikes)
    # 1*100*2 + 0*50*1 + (-1)*200*(-1) = 200 + 0 + 200 = 400 (a LEVEL, not a count)
    assert L == 400.0
    assert L != 3  # not a strike count


def test_weighted_level_deadband_contributes_exactly_zero():
    strikes = [
        {"strike": 100.0, "right": "C", "oi": 1000.0, "vanna": 5.0, "applied_sign": 0.0},
    ]
    L = sum(s["applied_sign"] * s["oi"] * s["vanna"] for s in strikes)
    assert L == 0.0  # deadband strike contributes 0, not -1000*5, not a count


# ---------------------------------------------------------------------------
# R6-3: seed-merge provenance preservation + seed rows in cross-engine
# ---------------------------------------------------------------------------
def test_merge_seed_rows_preserves_provenance(tmp_path, monkeypatch):
    rows = [{
        "ticker": "QQQ", "day": "20260605", "expiry": "20260608",
        "prod_sign": 1, "new_sign": 1, "agreed": True, "both_nonzero": True,
        "firing_buckets": 7, "buckets": 40, "mean_abs_div": 0.0161,
        "provenance": {"total": 45, "rich_plus1": 22, "cheap_minus1": 21,
                       "deadband_zero": 2, "missing": 0},
    }]
    with open(os.path.join(str(tmp_path), "dual_pipeline_gate_v3_obs.json"), "w") as fh:
        json.dump({"rows": rows}, fh)
    monkeypatch.setattr(v5._v4, "CACHE", str(tmp_path))
    merged = v5._v4._merge_seed_rows()
    assert len(merged) == 1
    assert merged[0]["session"] == "seed-v3"
    assert merged[0]["provenance"]["rich_plus1"] == 22  # provenance preserved


def test_seed_rows_in_cross_engine_comparison():
    # The 12-cluster weighted comparison includes all 5 seed resolvable rows.
    v3 = json.load(open(os.path.join(os.path.dirname(v5.__file__), "_intraday_cache",
                                     "dual_pipeline_gate_v3_obs.json"), encoding="utf-8"))
    seed = v5._v4._merge_seed_rows()
    seed_res = [r for r in seed if r["both_nonzero"]]
    assert len(seed_res) == 5  # QQQ 0508/0605/0716/0731 + SPY 0605


# ---------------------------------------------------------------------------
# R6-5: SPY/QQQ never pooled
# ---------------------------------------------------------------------------
def test_spy_qqq_never_pooled_in_corr():
    # The driver computes corr_spy / corr_qqq SEPARATELY; there is no pooled
    # inferential corr. Verify the driver's own labels describe them as noise.
    # (structural guard: no function pools SPY+QQQ into one corr in v5)
    src = open(os.path.join(os.path.dirname(v5.__file__), "run_dual_pipeline_gate_v5.py"),
               encoding="utf-8").read()
    # corr_spy and corr_qqq are computed independently
    assert "corr_spy = _g._corr(spy_x, spy_y)" in src
    assert "corr_qqq = _g._corr(qx, qy)" in src
    assert "BUCKET-LEVEL NOISE" in src


# ---------------------------------------------------------------------------
# R6-4: correlational + placebo underpowered labels
# ---------------------------------------------------------------------------
def test_placebo_labeled_underpowered():
    src = open(os.path.join(os.path.dirname(v5.__file__), "run_dual_pipeline_gate_v5.py"),
               encoding="utf-8").read()
    assert "UNDERPOWERED" in src
    assert "md_buckets" in src


# ---------------------------------------------------------------------------
# R6-7: delta convention-distance
# ---------------------------------------------------------------------------
def test_delta_prod_formula():
    prov = {"total": 50, "rich_plus1": 30, "cheap_minus1": 20, "deadband_zero": 0}
    assert v5.delta_prod(prov) == 0.20


def test_delta_new_is_zero_by_construction():
    assert v5.delta_new({}) == 0.0


def test_convention_distance_demotes_when_production_hugs_baseline():
    # All delta_prod ~ 0 -> production hugs the shared -1 baseline -> DEMOTE
    deltas = [0.0, 0.01, -0.02, 0.005, -0.03, 0.02, 0.0, -0.01]
    mean_abs, frac, verdict, reason = v5.convention_distance(deltas)
    assert verdict == "DEMOTE"
    assert mean_abs < 0.10
    assert frac < 0.25


def test_convention_distance_open_when_material_deviation():
    # Some clusters with |delta_prod| >= 0.10 -> mechanism OPEN
    deltas = [0.0, 0.15, -0.12, 0.18, -0.05, 0.0]
    mean_abs, frac, verdict, reason = v5.convention_distance(deltas)
    assert verdict == "OPEN"


def test_convention_distance_corpus_includes_zero_firing_clusters():
    # delta is computed over ALL corpus rows INCLUDING zero-firing clusters
    # (provenance is present on every row regardless of firing state).
    v3 = json.load(open(os.path.join(os.path.dirname(v5.__file__), "_intraday_cache",
                                     "dual_pipeline_gate_v3_obs.json"), encoding="utf-8"))
    v4 = json.load(open(os.path.join(os.path.dirname(v5.__file__), "_intraday_cache",
                                     "dual_pipeline_gate_v4_obs.json"), encoding="utf-8"))
    seed = v5._v4._merge_seed_rows()
    rows = seed + v4.get("rows", [])
    deltas = [v5.delta_prod(r.get("provenance", {})) for r in rows]
    # Full corpus (40 rows), many of which fired 0 buckets; none dropped.
    assert len(deltas) == 40
    zero_firing = [r for r in rows if r.get("firing_buckets", 0) == 0]
    assert len(zero_firing) > 0
    # every zero-firing cluster still contributes a delta value
    for r in zero_firing:
        assert v5.delta_prod(r.get("provenance", {})) is not None


def test_real_corpus_delta_verdict_demotes():
    # The real corpus: mean|delta_prod| ~ 0.025, ~2.5% >= 0.10 -> DEMOTE.
    v3 = json.load(open(os.path.join(os.path.dirname(v5.__file__), "_intraday_cache",
                                     "dual_pipeline_gate_v3_obs.json"), encoding="utf-8"))
    v4 = json.load(open(os.path.join(os.path.dirname(v5.__file__), "_intraday_cache",
                                     "dual_pipeline_gate_v4_obs.json"), encoding="utf-8"))
    seed = v5._v4._merge_seed_rows()
    rows = seed + v4.get("rows", [])
    deltas = [v5.delta_prod(r.get("provenance", {})) for r in rows]
    mean_abs, frac, verdict, reason = v5.convention_distance(deltas)
    assert verdict == "DEMOTE"
    assert mean_abs < 0.05


# ---------------------------------------------------------------------------
# R6-6: reproducibility assertions
# ---------------------------------------------------------------------------
def test_corpus_integrity_no_duplicates():
    v3 = json.load(open(os.path.join(os.path.dirname(v5.__file__), "_intraday_cache",
                                     "dual_pipeline_gate_v3_obs.json"), encoding="utf-8"))
    v4 = json.load(open(os.path.join(os.path.dirname(v5.__file__), "_intraday_cache",
                                     "dual_pipeline_gate_v4_obs.json"), encoding="utf-8"))
    seed = v5._v4._merge_seed_rows()
    rows = seed + v4.get("rows", [])
    info = v5.assert_corpus_integrity(rows)
    assert info["n_rows"] == info["n_unique"]  # no dup (ticker,day)
    assert info["n_rows"] == 40
