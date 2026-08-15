"""ROUND-8 (R8) — symmetric two-sided magnitude-weighted convention-distance rerun.

The ONLY door Cem Karsan left open to reconsider APPROVAL (R3, deleg_54ae4bc3):

  "I would revisit APPROVAL only on a symmetric, two-sided, magnitude-weighted
   convention-distance rerun — persist the new-engine per-strike rows alongside
   production, compute D = sum_s w_s |delta_prod,s - delta_new,s| / sum_s w_s
   on the real SVI deviation path."

Builds on run_dual_pipeline_gate_v5.py (the R6/R7 packet). v5 persisted the
PRODUCTION-side per-strike arrays (strike/right/oi/vanna/iv/applied_sign) for all
12 resolvable clusters under weighted.<ticker>_<day>.per_strike, and hard-coded
delta_new == 0 (new engine has no SVI branch). That made the v5/v7 delta a
ONE-SIDED production screen. R8 fixes the genuinely TWO-SIDED distance.

NEW-ENGINE SIDE — network-free derivation (no rerun, no new acquisition):
  The new engine's per-strike dealer-frame vanna is a PURE ANALYTIC function
    dealer_frame_vanna = -1 * bs_vanna(spot, strike, T, IV)
  of inputs that are ALL already persisted in the v5 production per-strike
  arrays (strike, right, iv), the v5 cluster close_spot, and day->expiry (T).
  So the new-side rows are derived EXACTLY (same engine formula) with ZERO
  network calls and ZERO approximation — they are not "re-derived guesses".
  new_engine_data_status = "network-free (derived exactly from persisted v5
  per-strike spot/strike/T/IV via the same dealer_frame_vanna(-1*BS) formula)".

  This is the aligned comparison Cem asked for: SAME grid, SAME OI, SAME IV,
  SAME close-spot anchor, SAME day/expiry (T). delta_new is a MEASURED nonzero
  per-strike value (the -1*BS vanna), NOT an assumed 0.

PRODUCTION SIDE — delta_prod is the real SVI deviation classification:
  The v5 persisted per-strike `applied_sign` was produced by
  run_production_vanna_fallback0 patching `_dp._resolve_sign` to
  `_resolve_sign_fallback0` with sign_model="vol_surface_replication", which
  calls `resolve_vol_surface_sign(vol_surface_ref, strike, right)` — the REAL
  SVI `deviation_by_strike` path (rich +1 / cheap -1 / |dev|<=0.01 deadband -> 0).
  It is NOT the v3 IV-vs-chain-median proxy. So delta_prod = applied_sign
  in {-1, 0, +1} per strike, magnitude-weighted by w_s = |applied_sign*OI*vanna|.

SYMMETRIC DISTANCE (Cem's formula):
  D_conv = sum_s w_s |delta_prod,s - delta_new,s| / sum_s w_s,  w_s = |applied_sign*OI*vanna|
  Per-strike |delta_prod - delta_new| in {0, 1, 2} (both in {-1,0,+1}):
    same sign            -> 0
    zero-vs-nonzero      -> 1
    opposite signs       -> 2
  Mean D_conv is reported UNWEIGHTED across the 12 clusters (each cluster one
  datum, matching prior rounds) AND as a pooled mass-weighted D_conv (Cem's raw
  formula over ALL per-strike rows). Per-strike weighted disagreement mass and
  the disagreement fraction are reported.

HONESTY (what R8 can/cannot establish):
  R8 measures how far production's REAL SVI deviation map is from a flat -1*BS
  benchmark, on both sides, magnitude-weighted. A material D_conv means
  production carries REAL INDEPENDENT SVI deviation (mechanism OPEN /
  re-admitted to evidence). D_conv ~ 0 means production hugs the -1 baseline
  (stays demoted). This measures deviation-MAGNITUDE independence, NOT
  causality. Even a material D_conv re-admits to EVIDENCE — it does NOT
  authorize promotion. Per Cem's boundary, a positive result never establishes
  causality; the mechanism stays descriptive/conditional until he APPROVES.

Network-free: pure functions on persisted v5 arrays. No ThetaData / .env.
"""
import datetime as dt
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import expiry_book_exposure as ebe  # noqa: E402  (bs_vanna / dealer_frame_vanna)

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_intraday_cache")
V5_OBS = os.path.join(CACHE, "dual_pipeline_gate_v5_obs.json")
V6_OBS = os.path.join(CACHE, "dual_pipeline_gate_v6_obs.json")
V6_RESULT = os.path.join(CACHE, "dual_pipeline_gate_v6_RESULT.md")

IV_DEADBAND_VOL = 0.01

# The 12 merged RESOLVABLE (both-nonzero) clusters from the v3+v4 acquisition.
RESOLVABLE = [
    ("QQQ", "20260508", "20260511", "seed-v3"),
    ("QQQ", "20260605", "20260608", "seed-v3"),
    ("QQQ", "20260716", "20260717", "seed-v3"),
    ("QQQ", "20260731", "20260803", "seed-v3"),
    ("SPY", "20260605", "20260608", "seed-v3"),
    ("QQQ", "20260617", "20260624", "v4"),
    ("QQQ", "20260729", "20260731", "v4"),
    ("SPY", "20260413", "20260417", "v4"),
    ("SPY", "20260430", "20260501", "v4"),
    ("SPY", "20260617", "20260624", "v4"),
    ("SPY", "20260728", "20260731", "v4"),
    ("SPY", "20260729", "20260731", "v4"),
]


def _days_between(day, expiry):
    d1 = dt.datetime.strptime(day, "%Y%m%d").date()
    d2 = dt.datetime.strptime(expiry, "%Y%m%d").date()
    return (d2 - d1).days


def _sign_of(x):
    """Return +1/-1/0 from a float, tolerating NaN and near-zero."""
    if x is None:
        return 0
    if isinstance(x, float) and math.isnan(x):
        return 0
    if x > 1e-12:
        return 1
    if x < -1e-12:
        return -1
    return 0


def derive_new_vanna(spot, strike, right, iv, day, expiry):
    """New-engine per-strike dealer_frame_vanna and its sign, derived EXACTLY
    from the same analytic formula the engine uses (ebe.dealer_frame_vanna =
    -1 * bs_vanna), on the same grid/OI/IV/spot/expiry. Network-free.

    bs_vanna has the SAME sign for call & put at the same OTM strike (it is a
    magnitude-only reference; the directional object is -1*BS), so `right` is
    carried only as the join key, not passed to bs_vanna.

    Returns (dfv, sign, T)."""
    T = max(_days_between(day, expiry) / 365.0, 0.01)
    raw = ebe.bs_vanna(spot, strike, T, iv)
    dfv = ebe.dealer_frame_vanna(raw)
    return dfv, _sign_of(dfv), T


def classify_svi(applied_sign):
    """delta_prod per strike = the persisted production applied_sign, which the
    v5 fallback0 path already resolved through the REAL SVI deviation_by_strike
    / resolve_vol_surface_sign (rich +1, cheap -1, |dev|<=0.01 deadband -> 0).
    Returned as an int in {-1, 0, +1}."""
    return int(round(float(applied_sign))) if applied_sign is not None else 0


def build_both_sides(per_strike, close_spot, day, expiry):
    """Join production per-strike rows with derived new-engine rows on
    (strike, right). Returns a list of side-pair dicts.

    delta_prod = classify_svi(applied_sign)          (real SVI path)
    delta_new  = sign of derived dealer_frame_vanna   (measured, nonzero)
    w          = |applied_sign * oi * vanna|          (magnitude weight)
    dist       = |delta_prod - delta_new| in {0,1,2}
    """
    pairs = []
    for pr in per_strike:
        dp = classify_svi(pr.get("applied_sign"))
        dfv, dn, T = derive_new_vanna(
            close_spot, float(pr["strike"]), pr["right"], float(pr["iv"]),
            day, expiry)
        w = abs(dp * float(pr["oi"]) * float(pr["vanna"]))
        pairs.append({
            "strike": float(pr["strike"]), "right": pr["right"],
            "oi": float(pr["oi"]), "iv": float(pr["iv"]),
            "prod_vanna": float(pr["vanna"]),
            "prod_applied_sign": dp, "delta_prod": dp,
            "new_vanna": dfv, "delta_new": dn, "T": T,
            "weight": w,
            "dist": abs(dp - dn),
        })
    return pairs


def symmetric_dconv(pairs):
    """Cem's symmetric two-sided magnitude-weighted convention distance.

    D_conv = sum_s w_s |delta_prod,s - delta_new,s| / sum_s w_s.
    Returns (D_conv, total_weight, disagreement_weight, n_rows, n_disagree,
    pair_counts)."""
    total_w = sum(p["weight"] for p in pairs)
    disag_w = sum(p["weight"] * p["dist"] for p in pairs)
    n_disagree = sum(1 for p in pairs if p["dist"] != 0)
    counts = {}
    for p in pairs:
        counts[(p["delta_prod"], p["delta_new"])] = \
            counts.get((p["delta_prod"], p["delta_new"]), 0) + 1
    D = disag_w / total_w if total_w > 0 else float("nan")
    return (D, total_w, disag_w, len(pairs), n_disagree, counts)


def load_v5_weighted():
    if not os.path.exists(V5_OBS):
        raise FileNotFoundError(f"missing v5 obs: {V5_OBS}")
    return json.load(open(V5_OBS, encoding="utf-8"))


def main():
    v5 = load_v5_weighted()
    weighted = v5.get("weighted", {})
    rows = {}
    for tk, day, exp, src in RESOLVABLE:
        rec = weighted.get(f"{tk}_{day}")
        if not rec or not rec.get("per_strike"):
            rows[f"{tk}_{day}"] = {"ticker": tk, "day": day, "expiry": exp,
                                   "src": src, "ok": False}
            continue
        pairs = build_both_sides(rec["per_strike"], float(rec["close_spot"]),
                                 day, rec["expiry"] or exp)
        D, tot_w, disag_w, n, nd, counts = symmetric_dconv(pairs)
        rows[f"{tk}_{day}"] = {
            "ticker": tk, "day": day, "expiry": exp, "src": src, "ok": True,
            "n_per_strike": n, "n_disagree": nd,
            "total_weight": tot_w, "disagree_weight": disag_w,
            "D_conv": D, "pair_counts": counts,
            "per_strike": pairs,
        }

    ok = [r for r in rows.values() if r.get("ok")]
    # mean D_conv UNWEIGHTED across clusters (each cluster one datum)
    Ds = [r["D_conv"] for r in ok]
    mean_D = sum(Ds) / len(Ds) if Ds else float("nan")
    # pooled mass-weighted D_conv (Cem's raw formula over all per-strike rows)
    tot_all = sum(r["total_weight"] for r in ok)
    disag_all = sum(r["disagree_weight"] for r in ok)
    mass_D = disag_all / tot_all if tot_all else float("nan")
    # per-strike disagreement fraction (unweighted rows)
    n_all = sum(r["n_per_strike"] for r in ok)
    nd_all = sum(r["n_disagree"] for r in ok)
    frac_disagree = nd_all / n_all if n_all else float("nan")
    # pooled pair-count histogram
    pair_tot = {}
    for r in ok:
        for k, c in r["pair_counts"].items():
            pair_tot[k] = pair_tot.get(k, 0) + c

    verdict, verdict_reason = _verdict(mean_D, mass_D, Ds)

    # ---- write v6 obs ----
    out = {
        "round": "R8",
        "new_engine_data_status":
            "network-free (derived exactly from persisted v5 per-strike "
            "spot/strike/T/IV via the same dealer_frame_vanna(-1*BS) formula; "
            "no rerun, no new acquisition)",
        "production_delta_source": "v5 persisted applied_sign via "
            "resolve_vol_surface_sign (real SVI deviation_by_strike; rich +1/"
            "cheap -1/deadband 0), NOT the IV-median proxy",
        "n_clusters": len(ok),
        "clusters": {k: {kk: r[kk] for kk in ("ticker", "day", "expiry", "src",
                                              "ok", "n_per_strike", "n_disagree",
                                              "D_conv", "total_weight",
                                              "disagree_weight")}
                     | {"pair_counts": {f"{p[0]},{p[1]}": c
                                        for p, c in r["pair_counts"].items()}}
                     for k, r in rows.items()},
        "per_strike": {k: r.get("per_strike") for k, r in rows.items()},
        "mean_D_conv_cluster": mean_D,
        "D_conv_distribution": Ds,
        "pooled_mass_weighted_D_conv": mass_D,
        "per_strike_disagree_frac": frac_disagree,
        "per_strike_n": n_all, "per_strike_disagree_n": nd_all,
        "pair_count_histogram": {f"{k[0]},{k[1]}": c for k, c in pair_tot.items()},
        "verdict": verdict, "verdict_reason": verdict_reason,
    }
    os.makedirs(CACHE, exist_ok=True)
    with open(V6_OBS, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)

    # ---- write v6 result md ----
    lines = []
    lines.append("# ROUND-8 — Symmetric Two-Sided Magnitude-Weighted Convention-Distance (result)\n")
    lines.append(f"**Date:** {dt.date.today().isoformat()}  **Driver:** `run_dual_pipeline_gate_v6.py`\n")
    lines.append("**Cem's boundary (R3, deleg_54ae4bc3):** the ONLY door to reconsider APPROVAL is a "
                 "symmetric, two-sided, magnitude-weighted convention-distance rerun: persist new-engine "
                 "per-strike rows alongside production, compute D = Σ_s w_s|δ_prod,s − δ_new,s| / Σ_s w_s "
                 "on the real SVI deviation path. A positive result would still NOT establish causality — "
                 "it licenses at most re-admission to evidence, never automatic promotion.\n")
    lines.append("### Network provenance\n")
    lines.append(f"- **new_engine_data_status = `network-free`** — the new-engine per-strike "
                 "`dealer_frame_vanna` is a pure analytic function `-1×BS_vanna(spot,strike,T,IV)` of "
                 "inputs ALL persisted in the v5 production per-strike arrays (strike/right/iv) + the "
                 "cluster `close_spot` + day→expiry (T). Derived EXACTLY with the same engine formula "
                 "(`ebe.dealer_frame_vanna(ebe.bs_vanna(...))`). ZERO network calls, ZERO new-day "
                 "acquisition, ZERO approximation. **No rerun was required.**\n")
    lines.append(f"- Production δ side: the v5 persisted `applied_sign` was produced by the fallback0 "
                 "patch calling `resolve_vol_surface_sign` → the REAL SVI `deviation_by_strike` path "
                 "(rich +1 / cheap −1 / |dev|≤0.01 deadband → 0) — **NOT** the v3 IV-vs-chain-median "
                 "proxy. δ_new is a MEASURED nonzero value (−1×BS), **not** an assumed 0.\n")
    lines.append("### Per-cluster symmetric D_conv (12 resolvable clusters)\n")
    lines.append("| Ticker | Day | Src | n_strikes | D_conv | weight | disagree/strike |")
    lines.append("|---|---|---|---|---|---|---|")
    for tk, day, exp, src in RESOLVABLE:
        r = rows.get(f"{tk}_{day}")
        if not r or not r.get("ok"):
            lines.append(f"| {tk} | {day} | {src} | n/a | DATA-FAIL | n/a | n/a |")
            continue
        lines.append(f"| {r['ticker']} | {r['day']} | {r['src']} | {r['n_per_strike']} | "
                     f"{r['D_conv']:.4f} | {r['total_weight']:.4g} | "
                     f"{r['n_disagree']}/{r['n_per_strike']} |")
    lines.append(f"\n- **Mean D_conv across clusters (unweighted, each cluster one datum): "
                 f"`{mean_D:.4f}`**")
    lines.append(f"- D_conv distribution across 12 clusters: "
                 f"[{', '.join(f'{d:.4f}' for d in Ds)}]")
    lines.append(f"- **Pooled mass-weighted D_conv (Cem's raw formula over all per-strike rows): "
                 f"`{mass_D:.4f}`**")
    lines.append(f"- Per-strike disagreement fraction: **{nd_all}/{n_all} = {frac_disagree:.1%}** "
                 f"(unweighted rows); pooled weighted disagreement mass = {disag_all:.4g} / "
                 f"{tot_all:.4g} total weight.")
    lines.append("- (δ_prod, δ_new) pair-count histogram (pooled, all clusters): "
                 + ", ".join(f"({k[0]},{k[1]})={c}" for k, c in sorted(pair_tot.items()))
                 + " — distance 0 = same sign, 1 = zero-vs-nonzero, 2 = opposite signs.")
    lines.append("\n### VERDICT\n")
    lines.append(f"**{verdict}** — {verdict_reason}\n")
    lines.append("### What this can and cannot establish\n")
    lines.append("- **CAN:** R8 measures how far production's REAL SVI deviation map is from a flat "
                 "−1×BS benchmark, on BOTH sides, magnitude-weighted per strike. Material D_conv ⇒ "
                 "production carries real independent SVI deviation (mechanism OPEN → re-admitted to "
                 "evidence). D_conv ≈ 0 ⇒ production hugs the −1 baseline (stays demoted).")
    lines.append("- **CANNOT:** R8 measures deviation-**magnitude independence**, NOT causality. Even a "
                 "material D_conv does **not** establish that the deviation is mechanically causal, does "
                 "**not** license promotion, and does **not** overturn the sign-arm / correlational "
                 "evidence. Per Cem's boundary, a positive result re-admits to evidence only; the "
                 "mechanism stays descriptive/conditional until Cem APPROVES.\n")
    with open(V6_RESULT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print("\n".join(lines))
    print(f"\n[R8] mean_D_conv={mean_D:.4f}  pooled_mass_D={mass_D:.4f}  "
          f"disagree_frac={frac_disagree:.1%}  verdict={verdict}")
    return out


def _verdict(mean_D, mass_D, Ds):
    """Deterministic pre-registered R8 verdict rule (fixed before running).

    Production carries REAL INDEPENDENT SVI deviation if the distance from the
    flat −1×BS benchmark is material. Given |δ_prod − δ_new| ∈ {0,1,2} with
    magnitude weighting, a flat−1-bound production would produce D_conv ≈ 0
    (every strike where production hugs −1×BS, δ_new ≈ δ_prod → dist 0, and
    deadband strikes where δ_prod=0 vs δ_new=±1 would contribute dist 1 with
    ZERO weight w=|0·OI·vanna|=0 — so they drop out entirely).

    Pre-registered bar: OPEN/re-admit if the pooled mass-weighted D_conv >= 0.50
    (production and new-engine deviations disagree in the majority of
    magnitude-mass); else stays demoted. Mean cluster D_conv reported as a
    sensitivity (unweighted over the 12 clusters).
    """
    if math.isnan(mass_D):
        return ("INDETERMINATE", "no matched per-strike mass")
    if mass_D >= 0.50:
        return ("OPEN / RE-ADMIT TO EVIDENCE",
                f"pooled mass-weighted D_conv = {mass_D:.4f} >= 0.50 ⇒ production's real SVI "
                f"deviation map is materially INDEPENDENT of the flat −1×BS benchmark "
                f"(mean cluster D_conv = {mean_D:.4f}). Mechanism OPENS — re-admitted to "
                f"evidence. NOT a promotion: this measures deviation-magnitude independence, "
                f"NOT causality; mechanism stays descriptive/conditional until Cem APPROVES.")
    return ("STAYS DEMOTED",
            f"pooled mass-weighted D_conv = {mass_D:.4f} < 0.50 ⇒ production's real SVI deviation "
            f"hugs the −1×BS baseline (mean cluster D_conv = {mean_D:.4f}). Mechanism stays "
            f"demoted to descriptive/conditional; formally open-not-disproven but no independent "
            f"deviation evidence.")


if __name__ == "__main__":
    main()
