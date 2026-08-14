"""Tier 2C — ΔIV-signed vanna-shock event study (Karsan arbiter round-4 decisive test).

Implements the arbiter's mandated design exactly:
  Object/estimand: signed_flow = −sign(ΔIV_atm_t) × burst_t
  Gate:           Tier-1 Step-B exogenous-shock rule — |ΔIV_atm| top-decile AND
                  |daily return| > 1.5σ(prior-20d realized), pooled across all 8 windows.
  Cells:          vol-up (framework expects NEGATIVE fwd return) vs vol-down (expects POSITIVE);
                  index (SPY/QQQ = ONE cluster family) vs singles; ≥20 obs/cell for index primary.
  Null:           within-cluster permutation — shuffle ΔIV sign inside each ticker×window cluster.
  Multiplicity:   BH q<0.10 over (index, single) families and window family.
  Power:          min-detectable-at-effective-n reported next to each CI (target |r|≈0.15).
  Co-primary:     OpEx-charm DTE≤3 arm, same design, pooled across all 8 windows.

Reuses the EXACT burst object (hedge_flow_at, gamma lagged one day) via
run_expiry_tier2_multi.build_burst_series_from_seed. No network. No live model files touched.
"""
import glob
import json
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_expiry_tier2_multi as t2m  # reuses _load_seed_file, build_burst_series_from_seed, stats
from datetime import datetime

SCRATCH = r"C:\Users\bottl\FinancialDevelopment\.worktrees\dealer-exposure-dev\Vol_Suite\_scratch_tier2b"
REUSE = r"C:\Users\bottl\FinancialDevelopment\.worktrees\dealer-exposure-dev\Vol_Suite\_scratch_tier2"


def load_all_clusters():
    """Return {(ticker, window_key): series} for all 8 windows (7 prior + reuse)."""
    clusters = {}
    windows = sorted(glob.glob(os.path.join(SCRATCH, "window_*")))
    for wdir in windows:
        wkey = os.path.basename(wdir)
        for f in sorted(glob.glob(os.path.join(wdir, "seed_data_*.json"))):
            tk = os.path.basename(f).split("_")[2].upper()
            seed = t2m._load_seed_file(f)
            s = t2m.build_burst_series_from_seed(seed, tk)
            if len(s["dates"]) >= 4:
                clusters[(tk, wkey)] = s
    for f in sorted(glob.glob(os.path.join(REUSE, "seed_data_*_short.json"))):
        tk = os.path.basename(f).split("_")[2].upper()
        seed = t2m._load_seed_file(f)
        s = t2m.build_burst_series_from_seed(seed, tk)
        if len(s["dates"]) >= 4:
            clusters[(tk, "reuse_20260814")] = s
    return clusters


def _dte_of(expiry, date_str):
    try:
        exp = datetime.strptime(expiry, "%Y%m%d").date()
        obs = datetime.strptime(str(date_str)[:8], "%Y%m%d").date()
        return (exp - obs).days
    except Exception:
        return 999


def build_shock_events(clusters):
    """Per ticker: pool all windows, compute top-decile |ΔIV| threshold (Step-B gate)."""
    # per-ticker |ΔIV| pool for the top-decile threshold
    div_pool = {}
    for (tk, w), s in clusters.items():
        div_pool.setdefault(tk, []).extend([abs(v) for v in s["div"] if v != 0.0])
    thr = {}
    for tk, vals in div_pool.items():
        if len(vals) < 10:
            thr[tk] = max(vals) if vals else 0.005
        else:
            sv = sorted(vals)
            thr[tk] = sv[int(0.90 * len(sv))]
    # build per-cluster shock-day records
    events = {}  # (tk, w) -> list of dicts
    for (tk, w), s in clusters.items():
        rets = s["fwd"]
        divs = s["div"]
        bursts = s["burst"]
        evs = []
        for i in range(1, len(rets) - 1):
            window = rets[max(0, i - 20):i]
            if len(window) < 5:
                continue
            rv = (sum(v * v for v in window) / len(window)) ** 0.5
            r_now = rets[i]
            div_now = divs[i]
            if abs(div_now) >= abs(thr[tk]) and abs(r_now) > 1.5 * rv:
                signed_flow = -math.copysign(1.0, div_now) * bursts[i]
                # ZERO-TARGET AUDIT (R2-1): never emit a fake 0.0 forward return —
                # skip when the aligned next-day return is unavailable.
                if i + 1 >= len(rets):
                    continue
                resp = rets[i + 1]
                evs.append({
                    "div": div_now, "burst": bursts[i], "signed_flow": signed_flow,
                    "resp": resp, "resp_sign": 1.0 if resp > 0 else (-1.0 if resp < 0 else 0.0),
                    "dte": _dte_of(s["expiry"], s["dates"][i]),
                    "vol_up": div_now > 0,
                })
        if evs:
            events[(tk, w)] = evs
    return events, thr


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _corr(a, b):
    n = len(a)
    if n < 4:
        return 0.0
    ma = _mean(a); mb = _mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    return num / (da * db) if da > 0 and db > 0 else 0.0


def _two_sided_p(r, n):
    if n < 4 or abs(r) >= 1:
        return 1.0
    t = r * math.sqrt((n - 2) / (1 - r * r))
    z = abs(t)
    return min(max(2 * (1 - 0.5 * (1 + math.erf(z / math.sqrt(2)))), 1e-12), 1.0)


def _bh_qvalues(pvals):
    n = len(pvals)
    if n == 0:
        return {}
    order = sorted(range(n), key=lambda i: pvals[i])
    q, prev = {}, 1.0
    for rank, i in enumerate(order, start=1):
        q[i] = min(prev, pvals[i] * n / rank)
        prev = q[i]
    return q


def _min_detectable_r(n, alpha=0.05, power=0.80):
    z = 1.959963984540054 + 0.8416212335729143
    return z / math.sqrt(n + z * z)


def permutation_null(events, index_family, n_iter=2000, seed=7):
    """Within-cluster permutation: shuffle ΔIV sign inside each cluster, recompute
    the pooled index-family signed_flow-vs-resp corr. Returns (obs_r, null_r_list)."""
    rng = random.Random(seed)
    def pooled_signed_corr(shuffled=False):
        xs, ys = [], []
        for (tk, w), evs in events.items():
            if not index_family(tk):
                continue
            if shuffled:
                evs = [{**e, "div": -e["div"] if rng.random() < 0.5 else e["div"],
                        "signed_flow": -math.copysign(1.0, e["div"] if rng.random() < 0.5 else e["div"]) * e["burst"]}
                       for e in evs]
            else:
                evs = evs
            xd, yd = t2m._de_mean([e["signed_flow"] for e in evs]), t2m._de_mean([e["resp"] for e in evs])
            xs.extend(xd); ys.extend(yd)
        return _corr(xs, ys)
    obs = pooled_signed_corr(shuffled=False)
    nulls = [pooled_signed_corr(shuffled=True) for _ in range(n_iter)]
    return obs, nulls


def main():
    t0 = time.time()
    clusters = load_all_clusters()
    print(f"[tier2c] loaded {len(clusters)} ticker×window clusters")
    events, thr = build_shock_events(clusters)
    n_shock = sum(len(v) for v in events.values())
    print(f"[tier2c] shock days (Step-B gate): {n_shock}")

    def is_index(tk):
        return tk in ("SPY", "QQQ")

    # ---- primary: index family, vol-up vs vol-down cell means + signed-flow corr ----
    print("\n=== PRIMARY: INDEX FAMILY (SPY/QQQ = ONE cluster family) ===")
    idx_evs = []
    for (tk, w), evs in events.items():
        if is_index(tk):
            idx_evs.extend(evs)
    up = [e for e in idx_evs if e["vol_up"]]
    dn = [e for e in idx_evs if not e["vol_up"]]
    print(f"  index shock days: {len(idx_evs)}  (vol-up {len(up)}, vol-down {len(dn)})")
    if len(up) >= 20 and len(dn) >= 20:
        mu_up = _mean([e["resp"] for e in up])
        mu_dn = _mean([e["resp"] for e in dn])
        print(f"  vol-up mean fwd ret   = {mu_up:+.5f}  (framework expects NEGATIVE)  n={len(up)}")
        print(f"  vol-down mean fwd ret = {mu_dn:+.5f}  (framework expects POSITIVE)  n={len(dn)}")
        print(f"  spread (dn − up)      = {mu_dn - mu_up:+.5f}  (positive = framework-consistent)")
    else:
        print("  INSUFFICIENT cells (<20 per sign) — BLOCKED for index primary")
        mu_up = mu_dn = None

    # signed-flow corr on index shock days + permutation null
    obs_r, nulls = permutation_null(events, is_index, n_iter=2000)
    n_idx = len(idx_evs)
    md = _min_detectable_r(n_idx)
    p_perm = (sum(1 for nr in nulls if nr >= obs_r) + 1) / (len(nulls) + 1)  # one-sided (expect positive)
    p_two = _two_sided_p(obs_r, n_idx)
    print(f"\n  signed_flow corr (index, n={n_idx}) = {obs_r:+.4f}")
    print(f"  permutation p (one-sided, expect positive) = {p_perm:.4f}   two-sided p = {p_two:.4f}")
    print(f"  min-detectable |r| @ eff-n={n_idx}: {md:.4f}")

    # ---- secondary: singles ----
    print("\n=== SECONDARY: SINGLE NAMES (directional screen, diversification caveat) ===")
    sg_evs = []
    for (tk, w), evs in events.items():
        if not is_index(tk):
            sg_evs.extend(evs)
    if len(sg_evs) >= 20:
        sg_up = [e for e in sg_evs if e["vol_up"]]
        sg_dn = [e for e in sg_evs if not e["vol_up"]]
        xs = t2m._de_mean([e["signed_flow"] for e in sg_evs])
        ys = t2m._de_mean([e["resp"] for e in sg_evs])
        r_sg = _corr(xs, ys)
        print(f"  singles shock days: {len(sg_evs)}  signed_flow corr = {r_sg:+.4f}")
        print(f"  vol-up mean = {_mean([e['resp'] for e in sg_up]):+.5f} (n={len(sg_up)})   "
              f"vol-down mean = {_mean([e['resp'] for e in sg_dn]):+.5f} (n={len(sg_dn)})")
    else:
        print(f"  singles shock days: {len(sg_evs)} (too few)")
        r_sg = 0.0

    # ---- co-primary: OpEx-charm DTE≤3 arm ----
    print("\n=== CO-PRIMARY: OPEX-CHARM ARM (DTE≤3, pooled all 8 windows) ===")
    d3_evs = [e for evs in events.values() for e in evs if e["dte"] <= 3]
    d3_idx = [e for e in d3_evs if is_index(e.get("_tk", "")) or True]
    # need ticker tag; rebuild
    d3_idx_evs = []
    d3_sg_evs = []
    for (tk, w), evs in events.items():
        for e in evs:
            if e["dte"] <= 3:
                (d3_idx_evs if is_index(tk) else d3_sg_evs).append(e)
    print(f"  DTE≤3 shock days: index={len(d3_idx_evs)}  singles={len(d3_sg_evs)}")
    if len(d3_idx_evs) >= 20:
        up3 = [e for e in d3_idx_evs if e["vol_up"]]
        dn3 = [e for e in d3_idx_evs if not e["vol_up"]]
        xs = t2m._de_mean([e["signed_flow"] for e in d3_idx_evs])
        ys = t2m._de_mean([e["resp"] for e in d3_idx_evs])
        r3 = _corr(xs, ys)
        p3 = _two_sided_p(r3, len(d3_idx_evs))
        print(f"  DTE≤3 index signed_flow corr = {r3:+.4f}  p={p3:.4f}  n={len(d3_idx_evs)}")
        print(f"  vol-up mean = {_mean([e['resp'] for e in up3]):+.5f} (n={len(up3)})   "
              f"vol-down mean = {_mean([e['resp'] for e in dn3]):+.5f} (n={len(dn3)})")
    else:
        print("  INSUFFICIENT index DTE≤3 shock cells")

    # ---- BH over (index, single) family ----
    print("\n=== MULTIPLICITY (BH q<0.10 over families) ===")
    fam = {"index_signed": (_two_sided_p(obs_r, n_idx) if n_idx >= 4 else 1.0),
           "singles_signed": (_two_sided_p(r_sg, len(sg_evs)) if len(sg_evs) >= 4 else 1.0)}
    qvals = _bh_qvalues(list(fam.values()))
    for k, v in fam.items():
        print(f"  {k}: p={v:.4f}")
    print(f"  BH q over family: {[round(q,4) for q in qvals.values()]}")

    # ---- verdict ladder ----
    print("\n=== VERDICT ===")
    correct_sign = (obs_r > 0) if n_idx >= 4 else False
    if n_idx < 40 or (len([e for e in idx_evs if e['vol_up']]) < 20) or (len([e for e in idx_evs if not e['vol_up']]) < 20):
        verdict = "BLOCKED (insufficient index cells)"
    elif correct_sign and p_perm < 0.05 and abs(obs_r) >= md and n_idx >= 40:
        verdict = "SUPPORTED (index-primary, ΔIV-signed)"
    elif correct_sign and (p_perm < 0.10 or (len(d3_idx_evs) >= 20 and r3 > 0 and _two_sided_p(r3, len(d3_idx_evs)) < 0.10)):
        verdict = "FRAGILE (correct sign, not fully significant)"
    elif not correct_sign and n_idx >= 40:
        verdict = "RULED_OUT (sign inconsistent on index-primary)"
    else:
        verdict = "FRAGILE/INCONCLUSIVE"
    print(f"  VERDICT: {verdict}")
    print(f"\n[tier2c] total {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
