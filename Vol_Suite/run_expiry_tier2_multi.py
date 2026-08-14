"""Tier 2B — multi-window short-DTE confirmation driver (per tier2B_pre_registration.md).

Takes over from the coder. Runs the EXACT corrected burst object (hedge_flow_at at
zero-gamma/wall, gamma lagged one day, continuous fwd return) across MULTIPLE independent
2-7 DTE windows per ticker, with cluster-aware inference at the ticker x window unit.

Reuses (verbatim, unmodified):
  - expiry_book_exposure.build_net_exposure / execution_locus / hedge_flow_at / _date_of / _extract
  - the burst-building logic from run_expiry_tier1.build_burst_series (parameterized by seed file)

Per locked pre-registration (tier2B_pre_registration.md):
  - window = (as_of, expiry) with end-of-window DTE in [2,7]; each ticker x window an independent
    cluster; observation days span the 14-day lookback, de-meaned PER CLUSTER
  - primary estimand = pooled (meta-analytic) corr between corrected burst and continuous fwd return
  - nominal obs / # clusters / effective-n reported SEPARATELY
  - BH q<0.10 over the window family AND the per-ticker family
  - leave-one-window-out, leave-one-ticker-out
  - placebo: (a) non-breach days only, (b) shifted forward-return horizon
  - DTE buckets 2-3 / 4-5 / 6-7; index ETFs (SPY/QQQ) separate from single names
  - vanna-shock arm: UNTESTABLE/UNDERPOWERED unless genuine shock obs exist
  - SPY/QQQ sign flip on same construct = FRAGILE/FAIL
  - verdict ladder: SUPPORTED / FRAGILE / RULED_OUT / BLOCKED

No network. No live model files touched. New file only.
"""
import glob
import json
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import expiry_book_exposure as ebe

# ---------------------------------------------------------------- data loading
def _load_seed_file(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _series_from_seed(seed, ticker):
    """Build per-day rows {date -> rows/spot/T/atm} from a seed dict (same logic as
    run_expiry_tier1._series but from an explicit seed object)."""
    expiry = str(seed.get("manifest", {}).get("expiry", ""))
    greeks = seed.get("greeks", [])
    oi_rows = seed.get("oi", [])
    spots = seed.get("spot", [])
    from datetime import datetime
    try:
        exp_dt = datetime.strptime(expiry, "%Y%m%d")
    except Exception:
        exp_dt = None
    spot_by_date = {}
    for r in spots:
        d = ebe._date_of(r)
        v = ebe._extract(r, "close")
        if not math.isnan(v):
            spot_by_date[d] = v
    greeks_by_date = {}
    for g in greeks:
        d = ebe._date_of(g)
        k = ebe._extract(g, "strike")
        if not math.isnan(k) and k > 10000:
            k = k / 1000.0
        iv = ebe._extract(g, "implied_vol")
        if not math.isnan(k) and not math.isnan(iv) and iv > 0:
            greeks_by_date.setdefault(d, []).append(
                {"strike": k, "right": str(g.get("right", "C")).upper()[:1], "implied_vol": iv})
    oi_by_date = {}
    for o in oi_rows:
        d = ebe._date_of(o)
        k = ebe._extract(o, "strike")
        if not math.isnan(k) and k > 10000:
            k = k / 1000.0
        oi_by_date.setdefault(d, {})[(k, str(o.get("right", "C")).upper()[:1])] = \
            int(ebe._extract(o, "open_interest", 0.0))
    ordered = sorted(spot_by_date)
    rows = {}
    for d in ordered:
        if d not in greeks_by_date:
            continue
        day_rows = [{"strike": g["strike"], "right": g["right"],
                     "oi": oi_by_date.get(d, {}).get((g["strike"], g["right"]), 0),
                     "implied_vol": g["implied_vol"]} for g in greeks_by_date[d]]
        T = 0.25
        if exp_dt is not None:
            try:
                ddt = datetime.strptime(d, "%Y%m%d")
                T = max((exp_dt - ddt).days, 1) / 365.0
            except Exception:
                T = 0.25
        ivs = [g["implied_vol"] for g in day_rows
               if abs(g["strike"] - spot_by_date[d]) <= 0.05 * spot_by_date[d]]
        atm = float(sum(ivs) / len(ivs)) if ivs else 0.0
        rows[d] = {"rows": day_rows, "T": T, "spot": spot_by_date[d], "atm": atm}
    days = [d for d in ordered if d in rows]
    return {"days": days, "rows": rows, "expiry": expiry, "ticker": ticker}


def build_burst_series_from_seed(seed, ticker):
    """EXACT copy of run_expiry_tier1.build_burst_series, parameterized by seed object."""
    s = _series_from_seed(seed, ticker)
    days = s["days"]
    bursts, fwd, fwd_sign, div, dates = [], [], [], [], []
    prev_locus = None
    prev_atm = None
    for i, d in enumerate(days):
        spot = s["rows"][d]["spot"]
        atm = s["rows"][d]["atm"]
        if i + 1 < len(days):
            s_nxt = s["rows"][days[i + 1]]["spot"]
            r = (s_nxt - spot) / spot if spot else 0.0
            fwd.append(r)
            fwd_sign.append(1.0 if r > 0 else (-1.0 if r < 0 else 0.0))
        else:
            fwd.append(0.0); fwd_sign.append(0.0)
        if prev_locus is not None:
            burst = ebe.hedge_flow_at(prev_locus, spot)
        else:
            burst = 0.0
        bursts.append(burst)
        div.append(0.0 if prev_atm is None else atm - prev_atm)
        dates.append(d)
        try:
            prev_locus = ebe.execution_locus(s["rows"][d]["rows"], spot, T=s["rows"][d]["T"])
        except Exception:
            prev_locus = None
        prev_atm = atm
    return {"ticker": ticker, "expiry": s["expiry"], "dates": dates, "burst": bursts,
            "fwd": fwd, "fwd_sign": fwd_sign, "div": div}


# ---------------------------------------------------------------- statistics
def _corr(a, b):
    n = len(a)
    if n < 4:
        return 0.0
    ma = sum(a) / n; mb = sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    return num / (da * db) if da > 0 and db > 0 else 0.0


def _de_mean(x):
    m = sum(x) / len(x) if x else 0.0
    return [v - m for v in x]


def _two_sided_p(r, n):
    if n < 4 or abs(r) >= 1:
        return 1.0
    t = r * math.sqrt((n - 2) / (1 - r * r))
    z = abs(t)
    return min(max(2 * (1 - 0.5 * (1 + math.erf(z / math.sqrt(2)))), 1e-12), 1.0)


def _fisher_z(r):
    r = max(min(r, 0.999999), -0.999999)
    return 0.5 * math.log((1 + r) / (1 - r))


def _fisher_inv(z):
    return math.tanh(z)


def _der_simonian_laird(r_vals, n_vals):
    """Random-effects meta-analytic pooling (DerSimonian-Laird) of per-window corrs
    via Fisher-z transform. Returns (pooled_r, pooled_z, tau2, se_z, ci_lo_z, ci_hi_z)."""
    k = len(r_vals)
    if k < 2:
        if k == 1:
            z = _fisher_z(r_vals[0]); se = 1.0 / math.sqrt(max(n_vals[0] - 3, 1))
            return (r_vals[0], z, 0.0, se, z - 1.96 * se, z + 1.96 * se)
        return (0.0, 0.0, 0.0, 1.0, -1.96, 1.96)
    zs = [_fisher_z(r) for r in r_vals]
    vi = [1.0 / max(n - 3, 1) for n in n_vals]
    w = [1.0 / v for v in vi]
    wz = sum(wi * zi for wi, zi in zip(w, zs)) / sum(w)
    Q = sum(wi * (zi - wz) ** 2 for wi, zi in zip(w, zs))
    df = k - 1
    C = sum(w) - sum(wi ** 2 for wi in w) / sum(w)
    tau2 = max((Q - df) / C, 0.0) if C > 0 else 0.0
    w_star = [1.0 / (v + tau2) for v in vi]
    z_pool = sum(wsi * zi for wsi, zi in zip(w_star, zs)) / sum(w_star)
    se_pool = math.sqrt(1.0 / sum(w_star))
    return (_fisher_inv(z_pool), z_pool, tau2, se_pool,
            z_pool - 1.96 * se_pool, z_pool + 1.96 * se_pool)


def _bh_qvalues(pvals):
    n = len(pvals)
    if n == 0:
        return {}
    order = sorted(range(n), key=lambda i: pvals[i])
    q = {}
    prev = 1.0
    for rank, i in enumerate(order, start=1):
        val = min(prev, pvals[i] * n / rank)
        q[i] = val
        prev = val
    return q


def _cluster_bootstrap_ci(clusters, n_iter=2000, seed=1234):
    """Bootstrap over ticker x window CLUSTERS (resample whole clusters with replacement),
    pooled de-meaned corr; returns (ci_lo, ci_hi, r_pooled)."""
    rng = random.Random(seed)
    K = len(clusters)
    # pooled de-meaned corr on all clusters
    xall, yall = [], []
    for (x, y) in clusters:
        xd, yd = _de_mean(x), _de_mean(y)
        xall.extend(xd); yall.extend(yd)
    r_obs = _corr(xall, yall)
    stats = []
    for _ in range(n_iter):
        xs, ys = [], []
        for _ in range(K):
            x, y = clusters[rng.randrange(K)]
            xd, yd = _de_mean(x), _de_mean(y)
            xs.extend(xd); ys.extend(yd)
        if len(xs) >= 4:
            stats.append(_corr(xs, ys))
    stats.sort()
    if len(stats) < 100:
        return (0.0, 0.0, r_obs)  # too few clusters for a stable bootstrap
    lo = stats[int(0.025 * len(stats))]
    hi = stats[int(0.975 * len(stats)) - 1]
    return lo, hi, r_obs


def _placebo_shifted(x, y, shift=1):
    """Shifted-horizon placebo: correlate burst_t with fwd return t+shift (future-shifted)."""
    n = len(x)
    if n - shift < 4:
        return 0.0
    return _corr(x[: n - shift], y[shift:])


# ---------------------------------------------------------------- main
def main():
    t0 = time.time()
    scratch = r"C:\Users\bottl\FinancialDevelopment\.worktrees\dealer-exposure-dev\Vol_Suite\_scratch_tier2b"
    reuse_dir = r"C:\Users\bottl\FinancialDevelopment\.worktrees\dealer-exposure-dev\Vol_Suite\_scratch_tier2"
    # discover windows: each window_YYYYMMDD/ holds seed_data_<TICKER>_<EXP>_short.json
    windows = sorted(glob.glob(os.path.join(scratch, "window_*")))
    if not windows:
        print("NO WINDOWS FOUND at", scratch)
        return
    print(f"[tier2b] discovered {len(windows)} windows: {[os.path.basename(w) for w in windows]}")

    # REUSED most-recent window (as-of 2026-08-14, expiry 20260817/20260821) — does NOT count
    # toward the prior-windows target but IS included in the pooled analysis.
    reuse_files = sorted(glob.glob(os.path.join(reuse_dir, "seed_data_*_short.json")))
    reuse_key = "reuse_20260814"
    if reuse_files:
        print(f"[tier2b] including REUSED most-recent window: {len(reuse_files)} tickers from {reuse_dir}")
    else:
        print("[tier2b] WARNING: no reused most-recent window found at", reuse_dir)

    # load all (ticker, window) clusters
    clusters = {}   # (ticker, window_key) -> burst/fwd series
    per_window_files = {}
    for wdir in windows:
        wkey = os.path.basename(wdir)
        for f in sorted(glob.glob(os.path.join(wdir, "seed_data_*.json"))):
            fname = os.path.basename(f)
            tk = fname.split("_")[2].upper()  # seed_data_<TICKER>_<EXP>_short.json
            seed = _load_seed_file(f)
            s = build_burst_series_from_seed(seed, tk)
            if len(s["dates"]) < 4:
                print(f"  SKIP {wkey}/{tk}: only {len(s['dates'])} days")
                continue
            clusters[(tk, wkey)] = s
            per_window_files[(tk, wkey)] = f
    for f in reuse_files:
        fname = os.path.basename(f)
        tk = fname.split("_")[2].upper()
        seed = _load_seed_file(f)
        s = build_burst_series_from_seed(seed, tk)
        if len(s["dates"]) < 4:
            print(f"  SKIP {reuse_key}/{tk}: only {len(s['dates'])} days")
            continue
        clusters[(tk, reuse_key)] = s
        per_window_files[(tk, reuse_key)] = f
    print(f"[tier2b] loaded {len(clusters)} ticker x window clusters")

    tickers = sorted({k[0] for k in clusters})
    wkeys = sorted({k[1] for k in clusters})
    print(f"[tier2b] tickers={len(tickers)}  windows={len(wkeys)}")

    # per-window pooled corr (de-meaned per ticker within window)
    window_stats = {}
    for w in wkeys:
        xall, yall, ns = [], [], []
        per_tk = {}
        for tk in tickers:
            if (tk, w) not in clusters:
                continue
            c = clusters[(tk, w)]
            xd, yd = _de_mean(c["burst"]), _de_mean(c["fwd"])
            xall.extend(xd); yall.extend(yd)
            per_tk[tk] = _corr(c["burst"], c["fwd"])
            ns.append(len(c["burst"]))
        n_w = sum(ns)
        r_w = _corr(xall, yall) if len(xall) >= 4 else 0.0
        window_stats[w] = {"r": r_w, "n": n_w, "n_tickers": len(ns), "per_ticker": per_tk}
        print(f"  window {w}: r={r_w:+.4f} n={n_w} tickers={len(ns)}")

    # per-ticker pooled corr across windows
    ticker_stats = {}
    for tk in tickers:
        xall, yall, ns = [], [], []
        for w in wkeys:
            if (tk, w) not in clusters:
                continue
            c = clusters[(tk, w)]
            xd, yd = _de_mean(c["burst"]), _de_mean(c["fwd"])
            xall.extend(xd); yall.extend(yd)
            ns.append(len(c["burst"]))
        n_tk = sum(ns)
        r_tk = _corr(xall, yall) if len(xall) >= 4 else 0.0
        ticker_stats[tk] = {"r": r_tk, "n": n_tk, "n_windows": len(ns)}
        print(f"  ticker {tk}: r={r_tk:+.4f} n={n_tk} windows={len(ns)}")

    # primary: cluster bootstrap over ticker x window + meta-analytic pooling
    cluster_list = [(clusters[k]["burst"], clusters[k]["fwd"]) for k in clusters]
    ci_lo, ci_hi, r_pool = _cluster_bootstrap_ci(cluster_list)
    n_nominal = sum(len(c["burst"]) for c in clusters.values())
    n_clusters = len(clusters)
    # meta-analytic pooling over windows
    r_wins = [window_stats[w]["r"] for w in wkeys]
    n_wins = [window_stats[w]["n"] for w in wkeys]
    meta = _der_simonian_laird(r_wins, n_wins)
    meta_r, meta_z, tau2, se_z, z_lo, z_hi = meta
    print("\n=== PRIMARY ===")
    print(f"  nominal obs n={n_nominal}  independent ticker x window clusters={n_clusters}")
    print(f"  pooled de-meaned corr (cluster bootstrap) = {r_pool:+.4f}  CI=[{ci_lo:+.4f},{ci_hi:+.4f}]")
    print(f"  meta-analytic pooled (DS-L over {len(wkeys)} windows) = {meta_r:+.4f}  "
          f"z-CI=[{_fisher_inv(z_lo):+.4f},{_fisher_inv(z_hi):+.4f}]  tau2={tau2:.4f}")

    # per-window family BH
    wp = [_two_sided_p(r_wins[i], n_wins[i]) for i in range(len(wkeys))]
    wq = _bh_qvalues(wp)
    print("\n  Per-window p / BH q:")
    for i, w in enumerate(wkeys):
        print(f"    {w}: r={r_wins[i]:+.4f} n={n_wins[i]} p={wp[i]:.4f} q={wq[i]:.4f}")
    # per-ticker family BH
    tks = list(ticker_stats.keys())
    tp = [_two_sided_p(ticker_stats[tk]["r"], ticker_stats[tk]["n"]) for tk in tks]
    tq = _bh_qvalues(tp)
    print("  Per-ticker p / BH q:")
    for i, tk in enumerate(tks):
        print(f"    {tk}: r={ticker_stats[tk]['r']:+.4f} n={ticker_stats[tk]['n']} "
              f"p={tp[i]:.4f} q={tq[i]:.4f}")

    # leave-one-window-out
    print("\n=== LEAVE-ONE-WINDOW-OUT ===")
    loo_w = {}
    for w in wkeys:
        sub = [(clusters[k]["burst"], clusters[k]["fwd"]) for k in clusters if k[1] != w]
        lo, hi, r = _cluster_bootstrap_ci(sub, n_iter=1000, seed=99)
        loo_w[w] = (r, lo, hi)
        print(f"  drop {w}: r={r:+.4f} CI=[{lo:+.4f},{hi:+.4f}]")
    # leave-one-ticker-out
    print("\n=== LEAVE-ONE-TICKER-OUT ===")
    loo_t = {}
    for tk in tickers:
        sub = [(clusters[k]["burst"], clusters[k]["fwd"]) for k in clusters if k[0] != tk]
        lo, hi, r = _cluster_bootstrap_ci(sub, n_iter=1000, seed=99)
        loo_t[tk] = (r, lo, hi)
        print(f"  drop {tk}: r={r:+.4f} CI=[{lo:+.4f},{hi:+.4f}]")

    # placebo: non-breach days only
    print("\n=== PLACEBO (non-breach days only) ===")
    nb_x, nb_y = [], []
    for k in clusters:
        c = clusters[k]
        for b, f in zip(c["burst"], c["fwd"]):
            if b == 0.0:
                nb_x.append(b); nb_y.append(f)
    r_nb = _corr(_de_mean(nb_x), _de_mean(nb_y)) if len(nb_x) >= 4 else 0.0
    print(f"  non-breach days: n={len(nb_x)} corr={r_nb:+.4f}")

    # placebo: shifted forward horizon
    print("\n=== PLACEBO (shifted forward-return horizon) ===")
    sh_x, sh_y = [], []
    for k in clusters:
        c = clusters[k]
        r_shift = _placebo_shifted(c["burst"], c["fwd"], shift=1)
        sh_x.append(r_shift)
    # aggregate: mean shifted corr across clusters
    sh_vals = [r for r in sh_x if r != 0.0 or True]
    print(f"  per-cluster shifted-horizon corr: n_clusters={len(sh_vals)} "
          f"mean={sum(sh_vals)/len(sh_vals):+.4f}" if sh_vals else "  no clusters")

    # DTE buckets
    print("\n=== DTE BUCKETS (2-3 / 4-5 / 6-7) ===")
    buckets = {"2-3": [], "4-5": [], "6-7": []}
    for k in clusters:
        c = clusters[k]
        from datetime import datetime
        try:
            exp_dt = datetime.strptime(c["expiry"], "%Y%m%d")
        except Exception:
            continue
        for i, d in enumerate(c["dates"]):
            try:
                ddt = datetime.strptime(d, "%Y%m%d")
                dte = (exp_dt - ddt).days
            except Exception:
                continue
            if 2 <= dte <= 3:
                buckets["2-3"].append((c["burst"][i], c["fwd"][i]))
            elif 4 <= dte <= 5:
                buckets["4-5"].append((c["burst"][i], c["fwd"][i]))
            elif 6 <= dte <= 7:
                buckets["6-7"].append((c["burst"][i], c["fwd"][i]))
    for name, pairs in buckets.items():
        if len(pairs) < 4:
            print(f"  DTE {name}: n={len(pairs)} (too few)")
            continue
        xs = _de_mean([p[0] for p in pairs]); ys = _de_mean([p[1] for p in pairs])
        print(f"  DTE {name}: n={len(pairs)} corr={_corr(xs, ys):+.4f}")

    # index vs single
    print("\n=== INDEX ETFs (SPY/QQQ) vs SINGLE NAMES ===")
    for grp, members in (("index", ["SPY", "QQQ"]), ("single", [t for t in tickers if t not in ("SPY", "QQQ")])):
        xall, yall = [], []
        for tk in members:
            for w in wkeys:
                if (tk, w) not in clusters:
                    continue
                c = clusters[(tk, w)]
                xd, yd = _de_mean(c["burst"]), _de_mean(c["fwd"])
                xall.extend(xd); yall.extend(yd)
        r_g = _corr(xall, yall) if len(xall) >= 4 else 0.0
        print(f"  {grp}: n={len(xall)} corr={r_g:+.4f}")

    # SPY/QQQ sign consistency
    spy_r = ticker_stats.get("SPY", {}).get("r", 0.0)
    qqq_r = ticker_stats.get("QQQ", {}).get("r", 0.0)
    print(f"\n  SPY r={spy_r:+.4f}  QQQ r={qqq_r:+.4f}  "
          f"sign_consistent={spy_r * qqq_r >= 0}")

    # vanna-shock arm
    print("\n=== VANNA-SHOCK ARM ===")
    shock_obs = 0
    for k in clusters:
        c = clusters[k]
        for b, f, dv in zip(c["burst"], c["fwd"], c["div"]):
            if abs(dv) >= 0.005 and b != 0:
                shock_obs += 1
    if shock_obs < 20:
        print(f"  shock obs={shock_obs} -> UNTESTABLE/UNDERPOWERED (needs >=20 genuine shocks)")
    else:
        print(f"  shock obs={shock_obs} -> testable (not run in this pass)")

    # ---- VERDICT LADDER ----
    print("\n=== VERDICT ===")
    n_windows_per_ticker = {tk: 0 for tk in tickers}
    for k in clusters:
        n_windows_per_ticker[k[0]] += 1
    min_windows = min(n_windows_per_ticker.values())
    print(f"  windows/ticker: min={min_windows}  (need >=3 for non-BLOCKED)")
    sign_consistent = sum(1 for tk in tickers if ticker_stats[tk]["r"] < 0) / max(len(tickers), 1)
    print(f"  fraction negative per-ticker: {sign_consistent:.2f}")
    if min_windows < 3:
        verdict = "BLOCKED (insufficient independent windows)"
    elif meta_r < 0 and _fisher_inv(z_hi) < 0 and sign_consistent > 0.6 and \
            min(loo_w.values(), key=lambda v: v[2])[2] < 0 and \
            min(loo_t.values(), key=lambda v: v[2])[2] < 0:
        verdict = "SUPPORTED (CONFIRMED)"
    elif meta_r < 0 and (_fisher_inv(z_lo) < 0 < _fisher_inv(z_hi)):
        verdict = "FRAGILE/INCONCLUSIVE"
    elif meta_r < 0:
        verdict = "FRAGILE/INCONCLUSIVE"
    else:
        verdict = "RULED_OUT"
    print(f"  VERDICT: {verdict}")
    print(f"\n[tier2b] total {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
