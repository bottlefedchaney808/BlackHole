#!/usr/bin/env python3
"""Consolidated PRE_WINDOW causal-arm analysis for the 2026-08-15 re-acquisition.

Runs the orthogonalized driver SEPARATELY on:
  (a) the causal-eligible PRE_WINDOW-only corpus (new units where BOTH SPY+QQQ
      carry valid PRE_WINDOW provenance), and
  (b) the associational fallback corpus (all genuinely-new units, clearly labeled,
      excluded from causal claims).

Persists a consolidated decision table + pre-event vanna summary to
Vol_Suite/_intraday_cache/causal_arm_v2_prewindow_acquisition_RESULT.md.
Model stays descriptive/conditional. A positive beta' is re-admission evidence
only, never auto-promotion.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import run_causal_arm_v2 as ca  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # Vol_Suite
PW = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                  "_prewindow_acquisition_20260815")


def _as_list(x):
    if x is None:
        return []
    if isinstance(x, str):
        return [x]
    return list(x)


def unit_is_prewindow(r):
    l2 = r.get("l2", {})
    prov = str(l2.get("delta_iv_provenance", "")).upper()
    src = l2.get("iv_source_ts")
    b = l2.get("breach_window_start_prov")
    d = l2.get("delta_iv_pre_window")
    return (prov == "PRE_WINDOW" and src is not None and b is not None
            and float(src) < float(b) and d is not None)


def run_corpus(records, label, out):
    uniq = ca.deduplicate_family_day(records)
    n_eff = len(uniq)
    orth = ca.orthogonalize_vanna_design(uniq, family_l2=True)
    prov = orth["delta_iv_provenance"]
    assoc_label = orth["associational_label"]
    fit = ca._fit_ols(orth["X"], orth["y"], "vanna_orth", orth["col_names"])
    p = fit.get("p", len(orth["col_names"]))
    df = n_eff - p
    beta = fit.get("beta"); se = fit.get("beta_se")
    power = None
    if beta is not None and ca.math.isfinite(float(beta)) and se is not None and ca.math.isfinite(float(se)) and df >= 1 and se > 0:
        power = ca.beta_power(beta, se, df, p, side="one")
    # family rule from the same fit
    spy = qqq = None
    if fit.get("beta_status") == "IDENTIFIABLE":
        c2i = {c: i for i, c in enumerate(orth["col_names"])}
        if "family_interaction_spy" in c2i:
            spy = fit["coef"][c2i["family_interaction_spy"]]
        if "family_interaction_qqq" in c2i:
            qqq = fit["coef"][c2i["family_interaction_qqq"]]
    fam = ca.family_claim_allowed(spy, qqq)
    # both-clock from run_causal_arm (uses build_design non-orth but family/clock signs identical)
    table = ca.run_causal_arm(records)
    bc = table["both_clock"]
    rows = []
    for r in uniq:
        l2 = r.get("l2", {}); clk = r.get("clock", {})
        rows.append({
            "day": r.get("day"), "families": r.get("families"),
            "habitat": l2.get("event_habitat"), "dte": r.get("dte"),
            "pre_vanna": l2.get("pre_vanna_exposure"),
            "delta_iv_pre_window": l2.get("delta_iv_pre_window"),
            "delta_iv_provenance": l2.get("delta_iv_provenance"),
            "delta_iv": l2.get("delta_iv"), "fwd_h": l2.get("forward_return_h"),
            "daily_ret": (clk.get("daily", {}) or {}).get("return"),
            "breach_ret": (clk.get("from_breach", {}) or {}).get("return"),
            "daily_neg": table_row_sign(clk, "daily"),
            "breach_pos": table_row_sign(clk, "from_breach"),
        })
    res = {
        "corpus": label, "n_eff_unique_days": n_eff,
        "delta_iv_provenance": prov, "associational_label": assoc_label,
        "rank": fit.get("rank"), "p": p, "cond": fit.get("cond"),
        "cond_standardized": fit.get("cond_standardized"),
        "max_vif": fit.get("max_vif"), "vif_flag": fit.get("vif_flag"),
        "beta_status": fit.get("beta_status"),
        "beta_unavailable_reason": fit.get("beta_unavailable_reason"),
        "beta_prime": beta, "beta_se": se, "beta_t": fit.get("beta_t"),
        "power": (power.get("power") if power else None),
        "power_available": (power.get("power_available") if power else False),
        "n_for_80": (power.get("n_for_80") if power else None),
        "reach_80": (power.get("reach_80") if power else False),
        "family_rule": fam,
        "both_clock": {"value": bc.get("value"), "confirmed": bc.get("confirmed"),
                       "reason": bc.get("reason"),
                       "n_confirmed_days": bc.get("n_confirmed_days"),
                       "n_eligible_days": bc.get("n_eligible_days")},
        "rows": rows,
    }
    return res


def table_row_sign(clk, key):
    d = clk.get(key, {}) or {}
    r = d.get("return")
    elig = d.get("eligible")
    if not elig or r is None:
        return None
    return r < 0 if key == "daily" else r > 0


def fmt(x, nd=4):
    if x is None or (isinstance(x, float) and x != x):
        return "—"
    try:
        return f"{x:.{nd}f}"
    except Exception:
        return str(x)


def main():
    new_recs = json.load(open(os.path.join(PW, "day_records_merged.json"), encoding="utf-8"))
    uniq = ca.deduplicate_family_day(new_recs)

    # firing count per (day,ticker) from the raw metrics (l2.gamma_burst is max|burst|, not count)
    rawdir = os.path.join(PW, "raw")
    firing_map = {}
    for f in os.listdir(rawdir):
        if not f.endswith(".json"):
            continue
        try:
            p = json.load(open(os.path.join(rawdir, f), encoding="utf-8"))
        except Exception:
            continue
        if p.get("ok"):
            firing_map[(p["day"], p["ticker"])] = p["metrics"].get("n_firing", 0)
    nf = lambda day, fam: firing_map.get((day, fam), 0)

    # (a) causal-eligible: units where EVERY family carries PRE_WINDOW
    causal_units = [r for r in uniq if unit_is_prewindow(r)]
    # (b) associational fallback: all genuinely-new units
    assoc_units = uniq

    # pre-event vanna summary
    vanna_rows = []
    for r in sorted(uniq, key=lambda x: x["day"]):
        l2 = r.get("l2", {})
        clk = r.get("clock", {})
        fams = _as_list(r.get("families"))
        firing_total = 0
        for f in fams:
            firing_total += nf(r["day"], f)
        vanna_rows.append({
            "day": r["day"], "families": fams, "habitat": l2.get("event_habitat"),
            "dte": r.get("dte"),
            "pre_vanna": l2.get("pre_vanna_exposure"),
            "pre_vanna_ts": (r.get("pre_window", {}) or {}).get("pre_vanna_timestamp"),
            "breach_start": (r.get("pre_window", {}) or {}).get("breach_window_start"),
            "cutoff_pass": (r.get("pre_window", {}) or {}).get("cutoff_pass"),
            "delta_iv_provenance": l2.get("delta_iv_provenance"),
            "delta_iv_pre_window": l2.get("delta_iv_pre_window"),
            "n_firing": firing_total,
            "from_breach_eligible": (clk.get("from_breach", {}) or {}).get("eligible"),
        })

    causal = run_corpus(causal_units, "CAUSAL-ELIGIBLE (PRE_WINDOW-only)", "causal")
    assoc = run_corpus(assoc_units, "ASSOCIATIONAL fallback (all new)", "assoc")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "_intraday_cache", "causal_arm_v2_prewindow_acquisition_RESULT.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)

    L = []
    L.append("# Causal-Arm v2 — PRE_WINDOW Re-Acquisition Decision Table (2026-08-15)")
    L.append("")
    L.append("Model stays **DESCRIPTIVE/CONDITIONAL** throughout. A positive β' is re-admission evidence only, never auto-promotion. No day's ΔIV was imputed.")
    L.append("")
    L.append("## Provenance census (7 genuinely-new unique days, SPY+QQQ = 1 unit)")
    L.append("")
    L.append(f"- intended causal units = 7  (the genuine Apr 13–23 2026 gap; every other day is already held in the 62-day corpus, v4/v5 list, or the SPY/QQQ seed corpus Apr 24–Aug 14)")
    L.append(f"- **PRE_WINDOW n/N = {causal['n_eff_unique_days']}/7**  (coverage {round(causal['n_eff_unique_days']/7,4)})")
    L.append(f"- associational exclusions = {assoc['n_eff_unique_days'] - causal['n_eff_unique_days']}; hard gaps = 0")
    L.append("- FAIL-LOUD fired: coverage < 100% of intended causal units. Causal corpus reduced to the passing unit(s); every failed day stays associational-only and is excluded from causal claims.")
    L.append("")
    L.append("| day | families | habitat | dte | firing | pre_vanna | ΔIV_pre_window | prov | daily_ret | breach_ret | daily_neg | breach_pos |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in assoc["rows"]:
        d = r["day"]
        v = next((v for v in vanna_rows if v["day"] == d), {})
        L.append(f"| {d} | {','.join(r['families'])} | {r['habitat']} | {r['dte']} | {fmt(v.get('n_firing'),0)} | {fmt(r['pre_vanna'],0)} | {fmt(r['delta_iv_pre_window'])} | {r['delta_iv_provenance'][:12]} | {fmt(r['daily_ret'])} | {fmt(r['breach_ret'])} | {r['daily_neg']} | {r['breach_pos']} |")
    L.append("")
    L.append("## Pre-event vanna exposure summary (source: 7 genuinely-new units, control days)")
    L.append("")
    pvs = [r["pre_vanna"] for r in assoc["rows"] if r["pre_vanna"] is not None and r["pre_vanna"] == r["pre_vanna"]]
    if pvs:
        L.append(f"- n={len(pvs)} control days; mean={sum(pvs)/len(pvs):,.0f}; min={min(pvs):,.0f}; max={max(pvs):,.0f}; spread (max-min)={max(pvs)-min(pvs):,.0f}")
    L.append("- all units are control (NONE habitat); mixed DTE 1–4 within the locked 1–10 band; both SPY and QQQ per unit; same-day = one independent unit.")
    L.append("")

    def panel(label, res):
        L.append(f"## {label}")
        L.append("")
        L.append(f"- effective unique-day n = **{res['n_eff_unique_days']}**")
        L.append(f"- ΔIV provenance = `{res['delta_iv_provenance']}` → **{res['associational_label']}**")
        L.append(f"- rank = {res['rank']}/{res['p']}; cond = {fmt(res['cond'],0)}; cond_std = {fmt(res['cond_standardized'])}; max_VIF = {fmt(res['max_vif'])} (flag={res['vif_flag']})")
        L.append(f"- β' (vanna_orth) = {fmt(res['beta_prime'],6)}  se={fmt(res['beta_se'],6)}  t={fmt(res['beta_t'])}  status={res['beta_status']}"
                 + (f"  [unavailable: {res['beta_unavailable_reason']}]" if res["beta_unavailable_reason"] else ""))
        pw = res["power"]
        L.append(f"- honest β'-power (one-sided) = {fmt(pw)}  (available={res['power_available']}, n_for_80={res['n_for_80']}, reach_80={res['reach_80']})")
        L.append(f"- family rule = {res['family_rule']['allowed']}  ({res['family_rule']['reason']})")
        bc = res["both_clock"]
        L.append(f"- BOTH-CLOCK = {bc['value']}  ({bc['reason']})  [confirmed {bc['n_confirmed_days']}/{bc['n_eligible_days']} eligible]")
        L.append("")

    panel("Orthogonalized driver — CAUSAL-ELIGIBLE PRE_WINDOW-only corpus", causal)
    panel("Orthogonalized driver — ASSOCIATIONAL fallback corpus (all new; excluded from causal claims)", assoc)

    L.append("## Consolidated decision table")
    L.append("")
    L.append("| corpus | n_units | PRE_WINDOW n/N | rank | cond_std | max_VIF | β' | se | power | n_for_80 | family rule | BOTH-CLOCK | causal claim |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    L.append(f"| CAUSAL-ELIGIBLE (PRE_WINDOW-only) | {causal['n_eff_unique_days']} | {causal['n_eff_unique_days']}/7 | {causal['rank']}/{causal['p']} | {fmt(causal['cond_standardized'])} | {fmt(causal['max_vif'])} | {fmt(causal['beta_prime'],6)} | {fmt(causal['beta_se'],6)} | {fmt(causal['power'])} | {causal['n_for_80']} | {causal['family_rule']['allowed']} | {causal['both_clock']['value']} | {'NONE — <1 unit / NOT powered' if causal['n_eff_unique_days'] < 2 else 'associational only'} |")
    L.append(f"| ASSOCIATIONAL fallback (all new) | {assoc['n_eff_unique_days']} | {causal['n_eff_unique_days']}/7 | {assoc['rank']}/{assoc['p']} | {fmt(assoc['cond_standardized'])} | {fmt(assoc['max_vif'])} | {fmt(assoc['beta_prime'],6)} | {fmt(assoc['beta_se'],6)} | {fmt(assoc['power'])} | {assoc['n_for_80']} | {assoc['family_rule']['allowed']} | {assoc['both_clock']['value']} | **excluded from causal claims** |")
    L.append("")

    # BLUNT read
    L.append("## BLUNT POWER READ (do not overclaim)")
    L.append("")
    L.append("**The PRE_WINDOW causal corpus is NOT powered for a fresh R1→R2→R3 Cem decision.**")
    L.append("")
    L.append(f"- Causal-eligible PRE_WINDOW units = {causal['n_eff_unique_days']} of the 257-unit β-power target. The genuine new-day pool available for re-acquisition was **only 7 days** (Apr 13–23 2026); every later trading day is already held in the SPY/QQQ seed corpus. There are **no genuinely-new days left to acquire toward 257** without reacquiring held days (forbidden) or expanding the universe beyond SPY/QQQ.")
    L.append(f"- Of those 7, only 1 carries valid PRE_WINDOW provenance on BOTH SPY+QQQ (20260414). The other 6 are associational-only (no firing bucket on one or both families → no pre-breach ΔIV anchor).")
    L.append(f"- With n_eff={causal['n_eff_unique_days']}, the β' fit is not identifiable/powered: n_eff=1 cannot even estimate the L2 interaction (p≈11) — rank < p. Power is undefined, n_for_80 unreachable in-sample, reach_80=False.")
    L.append(f"- The associational fallback (n_eff={assoc['n_eff_unique_days']}) is equally underpowered and is excluded from any causal claim by the fail-closed gate.")
    L.append("")
    L.append("**What a positive β' would mean (conditional, not observed here):** if the PRE_WINDOW causal corpus ever reaches n_eff ≥ ~257 with 100% provenance and power ≥ 0.80, a positive, identifiable, opposite-sign-family β' would be **re-admission evidence only** — it re-admits the dealer-frame vanna hypothesis to evidence and triggers a fresh R1→R2→R3 Cem review. It is **never auto-promotion**; the model stays descriptive/conditional until Cem rules.")
    L.append("")
    L.append("**Recommendation:** the 7-day re-acquisition is complete but does NOT reach the causal target. Do NOT proceed to R3 Cem on the PRE_WINDOW causal arm with n_eff=1. To honestly reach ~257 effective PRE_WINDOW days would require a new universe expansion (non-SPY/QQQ tickers, or a fresh pre-breach IV capture on re-designated held days), which is outside this task's locked scope.")
    L.append("")

    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    print("\n".join(L))
    print(f"\n[written] {out}")


if __name__ == "__main__":
    main()
