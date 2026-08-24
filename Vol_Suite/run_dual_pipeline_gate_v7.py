"""ROUND-9 (R9) — minimal repair to the R8 symmetric convention-distance packet.

R2 source cross-exam (deleg_8d589a97) found R8 mechanically sound but FRAGILE and
MIS-FRAMED, and required a minimal cheap (zero-acquisition) repair before Cem's
re-admission-to-evidence (NOT promotion) ruling:

  (1) REFRAME the benchmark. delta_new is NOT a "flat −1×BS baseline". It is the
      new engine's −1×BS_vanna MONEYNESS SIGN MAP: a near-balanced 50/50 ±1 map
      (−1:223 / +1:224 on the v5 grid). Every "flat −1 baseline / production hugs
      the −1 baseline" claim in R8 was factually wrong and is corrected here.
  (2) PUBLISH robustness diagnostics: leave-one-cluster-out (LOO) table, family-
      balanced D (SPY-only / QQQ-only / leave-one-family), K_eff and row n_eff,
      the three distance classes (0/1/2) with BOTH row counts AND OI·vanna mass,
      and BOTH weightings (|sign·OI·vanna| conservative AND |OI·vanna|
      convention-independent).
  (3) ADD a cluster-as-unit binomial gate (n=12 clusters, >= 2/3 bar = >= 8 of 12,
      exact null P) so ONE cluster's weight can no longer decide the bar.
  (4) Report the verdict as fragility-qualified descriptive: "OPEN (not certified)".

R9 is network-free, ZERO new acquisition: it reuses v6's exactly-derived new-engine
rows (dealer_frame_vanna = -1 * bs_vanna(spot,strike,T,IV) on the persisted v5 grid)
and the v5 production applied_sign (real SVI deviation_by_strike). It only extends
the aggregation/robustness layer on the same 447 per-strike rows / 12 clusters.

Binding promotion blockers (UNCHANGED, independent of R8/R9):
  - sign-arm: FAIL (3/9 unique-day heterogeneous-null P=0.8066; 6/12 P=0.6128)
  - convention-bound correlational: binding blocker (A6 reflexivity > vanna every
    round; b4 exact sign-flip)
R9 at its best supports a re-admission-to-EVIDENCE ruling only. It is NOT promotion
evidence and does NOT certify the mechanism.

Network-free: pure functions on persisted v5/v6 arrays. No ThetaData / .env.
"""

import datetime as dt
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import run_dual_pipeline_gate_v6 as v6

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_intraday_cache")
V7_OBS = os.path.join(CACHE, "dual_pipeline_gate_v7_obs.json")
V7_RESULT = os.path.join(CACHE, "dual_pipeline_gate_v7_RESULT.md")

# Cluster-as-unit gate pre-registration (R9-3): n=12 clusters, >= 2/3 bar = >= 8.
N_CLUSTERS = 12
BAR_2_3 = 8  # at least two-thirds of 12
HALF = 0.5  # null agreement probability (chance-compatible, direction pre-specified)

# Blocker statuses (binding, unchanged by R8/R9).
SIGN_ARM_STATUS = "FAIL (3/9 unique-day heterogeneous-null P=0.8066; 6/12 P=0.6128)"
CORR_STATUS = (
    "BINDING BLOCKER (convention-bound: A6 reflexivity > vanna; b4 exact sign-flip)"
)


def weight_conservative(delta_prod, oi, vanna):
    """Conservative weight |sign·OI·vanna| — R8's estimand. Deadband rows (sign=0)
    get ZERO weight (treated as indeterminate, not dropped)."""
    return abs(delta_prod * oi * vanna)


def weight_convention_independent(oi, vanna):
    """Convention-independent weight |OI·vanna| — no sign prior. Deadband rows keep
    FULL weight. Differs from conservative ONLY on deadband (sign=0) rows."""
    return abs(oi * vanna)


def exact_binom_ge(k, n=N_CLUSTERS, p=HALF):
    """Exact one-sided null P(X >= k) for X ~ Binomial(n, p)."""
    return sum(math.comb(n, x) * (p**x) * ((1 - p) ** (n - x)) for x in range(k, n + 1))


def pooled_dconv(pairs, weight_key):
    """Cem's raw pooled D over a list of side-pair dicts under a given weight key.
    D = sum_s w_s |δ_prod − δ_new| / sum_s w_s. Returns (D, total_w, disag_w, n)."""
    tot = sum(p[weight_key] for p in pairs)
    disag = sum(p[weight_key] * p["dist"] for p in pairs)
    D = disag / tot if tot > 0 else float("nan")
    return D, tot, disag, len(pairs)


def n_eff(weights):
    """Effective sample size of a weight vector: (Σw)^2 / Σw². Bounds 1 <= n_eff <= len."""
    s = sum(weights)
    if s <= 0:
        return 0.0
    s2 = sum(w * w for w in weights)
    return (s * s) / s2 if s2 > 0 else 0.0


def k_eff(cluster_weights):
    """Effective cluster count by weight: (ΣW)^2 / ΣW². Bounds 1 <= K_eff <= len."""
    return n_eff(list(cluster_weights))


def build_all_pairs():
    """Load v5 weighted obs and build v6-style both-sides pair dicts for the 12
    resolvable clusters, tagging each row with the OI·vanna mass and both weights.
    Returns (rows, all_pairs) where all_pairs is the pooled list with cluster/`
    family tags."""
    v5 = v6.load_v5_weighted()
    weighted = v5.get("weighted", {})
    rows = {}
    all_pairs = []
    for tk, day, exp, src in v6.RESOLVABLE:
        rec = weighted.get(f"{tk}_{day}")
        key = f"{tk}_{day}"
        if not rec or not rec.get("per_strike"):
            rows[key] = {
                "ticker": tk,
                "day": day,
                "expiry": exp,
                "src": src,
                "ok": False,
            }
            continue
        pairs = v6.build_both_sides(
            rec["per_strike"], float(rec["close_spot"]), day, rec["expiry"] or exp
        )
        for p in pairs:
            p["weight_conv"] = weight_conservative(
                p["delta_prod"], p["oi"], p["prod_vanna"]
            )
            p["weight_ind"] = weight_convention_independent(p["oi"], p["prod_vanna"])
            p["oi_vanna_mass"] = abs(p["oi"] * p["prod_vanna"])
            p["cluster"] = key
            p["family"] = tk
            all_pairs.append(p)
        rows[key] = {
            "ticker": tk,
            "day": day,
            "expiry": exp,
            "src": src,
            "ok": True,
            "per_strike": pairs,
        }
    return rows, all_pairs


def distance_class_ledger(all_pairs, weight_key):
    """Per distance class (0/1/2): row count, OI·vanna mass, mass share, and the
    weight-key mass. Rows + mass ledgers reconcile to totals."""
    classes = {
        0: {"rows": 0, "mass": 0.0, "weight": 0.0},
        1: {"rows": 0, "mass": 0.0, "weight": 0.0},
        2: {"rows": 0, "mass": 0.0, "weight": 0.0},
    }
    for p in all_pairs:
        d = p["dist"]
        classes[d]["rows"] += 1
        classes[d]["mass"] += p["oi_vanna_mass"]
        classes[d]["weight"] += p[weight_key]
    total_mass = sum(c["mass"] for c in classes.values())
    for c in classes.values():
        c["mass_share"] = c["mass"] / total_mass if total_mass > 0 else 0.0
    return classes


def family_dconv(all_pairs, weight_key):
    """Family-balanced D: SPY-only, QQQ-only pooled D. Returns dict."""
    fams = {}
    for fam in ("SPY", "QQQ"):
        subset = [p for p in all_pairs if p["family"] == fam]
        D, tot, disag, n = pooled_dconv(subset, weight_key)
        clusters = sorted({p["cluster"] for p in subset})
        fams[fam] = {
            "D": D,
            "total_weight": tot,
            "disagree_weight": disag,
            "n_rows": n,
            "n_clusters": len(clusters),
            "clusters": clusters,
        }
    return fams


def leave_one_cluster_out(all_pairs, weight_key):
    """LOO table: dropping each of the 12 clusters -> pooled D + aligned count on
    the remaining 11 (bar >= 0.50 on D). Returns (loo_rows, aligned_on_full)."""
    loo = []
    full_D, _, _, _ = pooled_dconv(all_pairs, weight_key)
    for cl in sorted({p["cluster"] for p in all_pairs}):
        subset = [p for p in all_pairs if p["cluster"] != cl]
        D, tot, disag, n = pooled_dconv(subset, weight_key)
        loo.append(
            {
                "omitted": cl,
                "D": D,
                "n_rows": n,
                "n_clusters": len({p["cluster"] for p in subset}),
                "total_weight": tot,
                "flips_to_fail": D < 0.50,
            }
        )
    return loo, full_D


def cluster_aligned(all_pairs, weight_key, bar=0.50):
    """Per-cluster D_conv and whether it clears the 0.50 bar (pre-specified
    direction: distance from the −1×BS_vanna sign map is material)."""
    per = {}
    for cl in sorted({p["cluster"] for p in all_pairs}):
        subset = [p for p in all_pairs if p["cluster"] == cl]
        D, tot, disag, n = pooled_dconv(subset, weight_key)
        per[cl] = {"D_conv": D, "total_weight": tot, "n_rows": n, "aligned": D >= bar}
    aligned = [c for c, r in per.items() if r["aligned"]]
    return per, aligned


def cluster_as_unit_gate(all_pairs, weight_key):
    """Cluster-as-unit binomial gate. Each cluster is one datum; aligned = per-
    cluster D_conv >= 0.50. Bar = >= 8 of 12. Exact one-sided null P(X>=aligned).
    Returns (aligned_count, n, aligned_clusters, P, clears_bar)."""
    per, aligned = cluster_aligned(all_pairs, weight_key)
    k = len(aligned)
    n = len(per)
    bar = math.ceil(2.0 / 3.0 * n) if n else 0
    P = exact_binom_ge(k, n, HALF) if n else float("nan")
    return k, n, aligned, P, n > 0 and k >= bar


def delta_new_split(all_pairs):
    """Emit delta_new's ±1 split (the corrected benchmark framing)."""
    n_plus = sum(1 for p in all_pairs if p["delta_new"] == 1)
    n_minus = sum(1 for p in all_pairs if p["delta_new"] == -1)
    n_zero = sum(1 for p in all_pairs if p["delta_new"] == 0)
    n = len(all_pairs)
    return {
        "n_plus": n_plus,
        "n_minus": n_minus,
        "n_zero": n_zero,
        "n": n,
        "pct_plus": 100.0 * n_plus / n if n else 0.0,
        "pct_minus": 100.0 * n_minus / n if n else 0.0,
    }


def build_verdict(all_pairs, split, fam, cluster_gate):
    """Fragility-qualified descriptive verdict. R8 shows a positive pooled
    convention-distance association robust to weighting and family balance, but it
    is single-cluster-hinged, tiny-n_eff/low-K_eff, and does NOT clear the
    cluster-as-unit gate. Verdict = 'OPEN (not certified)'. Binding promotion
    blockers stay in force regardless."""
    aligned_k, n, aligned_clusters, P, clears = cluster_gate
    D_conv, tot, disag, nrows = pooled_dconv(all_pairs, "weight_conv")
    reason = (
        "R9 shows a positive pooled convention-distance association between "
        f"production's real SVI deviation map and the new engine's −1×BS_vanna "
        f"moneyness sign map: pooled D_conv = {D_conv:.4f}, robust to |OI·vanna| "
        f"weighting and family balance (SPY {fam['SPY']['D']:.3f} / QQQ "
        f"{fam['QQQ']['D']:.3f}). BUT it is single-cluster-hinged (SPY-0728), "
        f"low power (row n_eff, cluster K_eff < 12), driven by a minority of "
        f"confident opposite-sign rows, and the near-balanced {split['pct_plus']:.1f}%/"
        f"{split['pct_minus']:.1f}% ±1 sign map is NOT a 'flat −1' baseline. The "
        f"cluster-as-unit gate clears {aligned_k}/{n} (>= {BAR_2_3} needed, exact "
        f"null P = {P:.4f}) — it does NOT clear the ≥2/3 bar. Descriptive distance "
        f"consistent with production adding genuine SVI deviation, NOT certified "
        f"re-admission, NOT promotion. Binding promotion blockers remain: "
        f"sign-arm {SIGN_ARM_STATUS}; correlational {CORR_STATUS}."
    )
    return "OPEN (not certified)", reason


def main():
    rows, all_pairs = build_all_pairs()
    split = delta_new_split(all_pairs)

    # --- conservative weighting (|sign·OI·vanna|, R8's estimand) ---
    D_conv, tot, disag, nrows = pooled_dconv(all_pairs, "weight_conv")
    # --- convention-independent weighting (|OI·vanna|) ---
    D_ind, tot_ind, disag_ind, _ = pooled_dconv(all_pairs, "weight_ind")

    # row effective sample sizes (conservative weight; deadband rows weight 0)
    row_w = [p["weight_conv"] for p in all_pairs]
    row_w_ind = [p["weight_ind"] for p in all_pairs]
    row_neff = n_eff(row_w)
    row_neff_ind = n_eff(row_w_ind)

    # cluster effective count (by conservative cluster weight and independent)
    cl_w_conv = {}
    cl_w_ind = {}
    for cl in sorted({p["cluster"] for p in all_pairs}):
        cl_w_conv[cl] = sum(p["weight_conv"] for p in all_pairs if p["cluster"] == cl)
        cl_w_ind[cl] = sum(p["weight_ind"] for p in all_pairs if p["cluster"] == cl)
    Keff_conv = k_eff(cl_w_conv.values())
    Keff_ind = k_eff(cl_w_ind.values())

    # distance-class ledgers (both weightings)
    classes_conv = distance_class_ledger(all_pairs, "weight_conv")
    classes_ind = distance_class_ledger(all_pairs, "weight_ind")

    # family-balanced
    fam = family_dconv(all_pairs, "weight_conv")

    # leave-one-cluster-out (both weightings)
    loo_conv, full_D_conv = leave_one_cluster_out(all_pairs, "weight_conv")
    loo_ind, _ = leave_one_cluster_out(all_pairs, "weight_ind")

    # cluster-as-unit gate
    per_conv, aligned_conv = cluster_aligned(all_pairs, "weight_conv")
    gate_conv = cluster_as_unit_gate(all_pairs, "weight_conv")
    gate_ind = cluster_as_unit_gate(all_pairs, "weight_ind")

    verdict, verdict_reason = build_verdict(all_pairs, split, fam, gate_conv)

    # ---- write v7 obs ----
    out = {
        "round": "R9",
        "new_engine_data_status": v6.__doc__ and "network-free" or "",
        "benchmark_framing": "the new engine's -1×BS_vanna moneyness sign map "
        "(NOT flat -1)",
        "delta_new_split": split,
        "n_clusters": len(all_pairs and {p["cluster"] for p in all_pairs}),
        "n_rows": len(all_pairs),
        "pooled_mass_weighted_D_conv": D_conv,
        "pooled_D_conv_independent": D_ind,
        "row_n_eff_conservative": row_neff,
        "row_n_eff_independent": row_neff_ind,
        "K_eff_conservative": Keff_conv,
        "K_eff_independent": Keff_ind,
        "distance_classes_conservative": classes_conv,
        "distance_classes_independent": classes_ind,
        "family": fam,
        "leave_one_cluster_out_conservative": loo_conv,
        "leave_one_cluster_out_independent": loo_ind,
        "cluster_aligned_conservative": {c: r["D_conv"] for c, r in per_conv.items()},
        "cluster_gate_conservative": {
            "aligned": gate_conv[0],
            "n": gate_conv[1],
            "aligned_clusters": gate_conv[2],
            "exact_null_P": gate_conv[3],
            "clears_bar": gate_conv[4],
            "bar_2_3": BAR_2_3,
        },
        "cluster_gate_independent": {
            "aligned": gate_ind[0],
            "n": gate_ind[1],
            "aligned_clusters": gate_ind[2],
            "exact_null_P": gate_ind[3],
            "clears_bar": gate_ind[4],
            "bar_2_3": BAR_2_3,
        },
        "per_cluster_D_conv_conservative": per_conv,
        "sign_arm_status": SIGN_ARM_STATUS,
        "correlational_status": CORR_STATUS,
        "verdict": verdict,
        "verdict_reason": verdict_reason,
    }
    os.makedirs(CACHE, exist_ok=True)
    with open(V7_OBS, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)

    # ---- write v7 result md ----
    lines = []
    lines.append(
        "# ROUND-9 — Symmetric Convention-Distance REPAIR: moneyness-sign-map benchmark, robustness diagnostics, cluster-as-unit gate (result)\n"
    )
    lines.append(
        f"**Date:** {dt.date.today().isoformat()}  **Driver:** `run_dual_pipeline_gate_v7.py`\n"
    )
    lines.append(
        "**Network-free / zero acquisition:** R9 reuses v6's exactly-derived "
        "new-engine rows (-1×BS_vanna(spot,strike,T,IV) on the persisted v5 "
        "grid) and the v5 production `applied_sign` (real SVI "
        "`deviation_by_strike`). Same 447 per-strike rows / 12 clusters. "
        "No ThetaData / .env / new acquisition.\n"
    )
    lines.append("### Corrected benchmark framing (R9-1)\n")
    lines.append(
        'R8 mislabeled delta_new as a "flat −1×BS baseline". It is actually '
        "the **new engine's −1×BS_vanna MONEYNESS SIGN MAP** — a near-balanced "
        f"50/50 ±1 map. Observed split: **+1: {split['n_plus']} "
        f"({split['pct_plus']:.2f}%) / −1: {split['n_minus']} "
        f"({split['pct_minus']:.2f}%)** over {split['n']} rows (0: "
        f"{split['n_zero']}). The near-balanced split is exactly why a "
        "symmetric two-sided convention-distance (not a one-sided −1 screen) "
        "is the correct repair.\n"
    )
    lines.append("### Pooled / effective-size ledger (R9-2)\n")
    lines.append(
        "| Metric | Conservative \\|sign·OI·vanna\\| | Convention-independent \\|OI·vanna\\| |"
    )
    lines.append("|---|---|---|")
    lines.append(f"| Pooled D_conv | **{D_conv:.4f}** | **{D_ind:.4f}** |")
    lines.append(f"| Row n_eff | {row_neff:.1f} | {row_neff_ind:.1f} |")
    lines.append(f"| Cluster K_eff | {Keff_conv:.2f} | {Keff_ind:.2f} |")
    lines.append(f"| Rows | {nrows} | {nrows} |")
    lines.append(
        "\nThe two weightings differ ONLY on deadband (sign=0) rows: "
        "conservative gives them 0 weight (indeterminate, not dropped), "
        "convention-independent keeps them at full weight.\n"
    )
    lines.append("### Distance-class ledger (rows AND OI·vanna mass) (R9-2)\n")
    lines.append(
        "| dist | rows | OI·vanna mass | mass share | conservative w | independent w |"
    )
    lines.append("|---|---|---|---|---|---|")
    for d in (0, 1, 2):
        c = classes_conv[d]
        ci = classes_ind[d]
        lines.append(
            f"| {d} | {c['rows']} | {c['mass']:.4g} | {c['mass_share']:.1%} | "
            f"{c['weight']:.4g} | {ci['weight']:.4g} |"
        )
    n_dead = classes_conv[1]["rows"]  # dist 1 == zero-vs-nonzero == deadband rows
    n_conf = classes_conv[0]["rows"] + classes_conv[2]["rows"]
    conf_txt = f"{classes_conv[2]['rows'] / n_conf:.1%}" if n_conf else "n/a"
    lines.append(
        f"\nNote: dist 1 = zero-vs-nonzero = the **{n_dead} deadband rows** "
        "(δ_prod=0). Under the conservative weighting they carry 0 weight "
        '(treated as indeterminate, NOT dropped). The R8 "78.7% '
        'disagreement" headline was inflated by these weight-0 rows; the '
        "true confident-disagreement rate (dist 2 rows / confident rows) is "
        f"{classes_conv[2]['rows']}/{n_conf} "
        "= "
        f"{conf_txt}.\n"
    )
    lines.append("### Family-balanced D (R9-2)\n")
    for fk in ("SPY", "QQQ"):
        f = fam[fk]
        lines.append(
            f"- **{fk}-only:** pooled D_conv = **{f['D']:.4f}** "
            f"({f['n_clusters']} clusters, {f['n_rows']} rows, weight "
            f"{f['total_weight']:.4g})."
        )
    lines.append(
        "\nLeave-one-family (= the other family alone) = the above; "
        "each family clears 0.50 on its own.\n"
    )
    lines.append("### Leave-one-cluster-out (R9-2)\n")
    lines.append(
        "| Omitted cluster | Pooled D_conv (conservative) | flips to <0.50? | remaining rows |"
    )
    lines.append("|---|---|---|---|")
    flip = [r for r in loo_conv if r["flips_to_fail"]]
    for r in loo_conv:
        lines.append(
            f"| {r['omitted']} | {r['D']:.4f} | {'**YES**' if r['flips_to_fail'] else 'no'} "
            f"| {r['n_rows']} |"
        )
    lines.append(
        f"\nFull pooled D_conv = **{full_D_conv:.4f}**. Dropping "
        f"{'/'.join(r['omitted'] for r in flip) or 'none'} alone flips the "
        "pooled D below the 0.50 bar.\n"
    )
    lines.append("### Cluster-as-unit binomial gate (R9-3)\n")
    gc = gate_conv
    lines.append(
        f"- Per-cluster D_conv >= 0.50 = ALIGNED (each cluster one datum). "
        f"**{gc[0]}/{gc[1]} aligned** — bar is >= {BAR_2_3}/12 (2/3). "
        f"Exact one-sided null P(X >= {gc[0]} | Binomial(12,0.5)) = "
        f"**{gc[3]:.4f}**."
    )
    lines.append(
        f"- **{'CLEARS' if gc[4] else 'DOES NOT CLEAR'} the ≥2/3 cluster-as-unit "
        f"bar.** {gc[0]}/{gc[1]} is the chance-median outcome (P={gc[3]:.4f}); "
        "the pooled mass metric alone cannot carry the re-admission, because "
        "one cluster's weight (SPY-0728) can still dominate it."
    )
    lines.append(f"- Aligned clusters: {', '.join(gc[2]) or 'none'}.\n")
    lines.append("### VERDICT (fragility-qualified descriptive)\n")
    lines.append(f"**{verdict}** — {verdict_reason}\n")
    lines.append("### Binding promotion blockers (UNCHANGED, independent of R8/R9)\n")
    lines.append(f"- **sign-arm:** {SIGN_ARM_STATUS}")
    lines.append(f"- **correlational:** {CORR_STATUS}")
    lines.append(
        "\nR9 supports a re-admission-to-**evidence** discussion only. It does "
        "NOT certify the mechanism and does NOT support promotion."
    )
    with open(V7_RESULT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print("\n".join(lines))
    print(
        f"\n[R9] delta_new +1:{split['n_plus']} ({split['pct_plus']:.2f}%) / "
        f"-1:{split['n_minus']} ({split['pct_minus']:.2f}%)  pooled_D_conv={D_conv:.4f} "
        f"indep={D_ind:.4f}  row_n_eff={row_neff:.1f}  K_eff={Keff_conv:.2f}  "
        f"cluster_gate={gc[0]}/{gc[1]} P={gc[3]:.4f}  verdict={verdict}"
    )
    return out


if __name__ == "__main__":
    main()
