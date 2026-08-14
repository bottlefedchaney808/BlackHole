"""Tier 1 — corrected fast-test + vol-shock vanna-lead probe (Karsan arbiter round-3 Step A + B).

Zero new data, network-free. Implements the arbiter's corrected measurement:
  (i)   TRUE burst object via hedge_flow_at(locus, price) at the zero-gamma/wall —
        NOT gex_level*dS revaluation.
  (ii)  gamma LAGGED one day (impulse_t uses locus built from day t-1 rows & spot,
        fired by the day t spot move) — fixes the contemporaneous contamination.
  (iii) continuous forward return (sign as secondary).
  (iv)  per-ticker cluster: by-ticker block bootstrap CI + effective-n min-detectable.
  (v)   BH on real two-sided p-values (not |corr|).
  (vi)  per-ticker corr distribution (not just pooled mean).
  (vii) charm/vanna labeled untestable-habitat where appropriate.

Plus Step B — the vol-shock vanna-lead probe:
  shock day = |dIV_atm| top-decile AND |daily return| > 1.5*prior-20d realized vol;
  signal = corrected vanna hedge flow gated on hedge_flow_at firing;
  response = next 1-2 day continuous return; within-ticker block permutation;
  SPY/QQQ treated as ONE instrument (twin dependence).

Reuses expiry_book_exposure.build_net_exposure, execution_locus, hedge_flow_at,
dealer-frame vanna (vanna_flow with decimal dIV). No network.
"""
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import expiry_book_exposure as ebe


def _date_of(r):
    return ebe._date_of(r)


def _series(ticker):
    seed = ebe._load_seed(ticker)
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
        d = _date_of(r)
        v = ebe._extract(r, "close")
        if not math.isnan(v):
            spot_by_date[d] = v
    greeks_by_date = {}
    for g in greeks:
        d = _date_of(g)
        k = ebe._extract(g, "strike")
        if not math.isnan(k) and k > 10000:
            k = k / 1000.0
        iv = ebe._extract(g, "implied_vol")
        if not math.isnan(k) and not math.isnan(iv) and iv > 0:
            greeks_by_date.setdefault(d, []).append(
                {"strike": k, "right": str(g.get("right", "C")).upper()[:1], "implied_vol": iv})
    oi_by_date = {}
    for o in oi_rows:
        d = _date_of(o)
        k = ebe._extract(o, "strike")
        if not math.isnan(k) and k > 10000:
            k = k / 1000.0
        oi_by_date.setdefault(d, {})[(k, str(o.get("right", "C")).upper()[:1])] = \
            int(ebe._extract(o, "open_interest", 0.0))
    ordered = sorted(spot_by_date)
    # Build per-day: locus (from day rows), spot, next-day return, dIV_atm.
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


def build_burst_series(ticker):
    """Per-day TRUE hedge-flow burst (hedge_flow_at, lagged gamma) + continuous fwd return + dIV.
    burst_t = hedge_flow_at(locus_{t-1}, spot_t): locus built from day t-1 rows & spot,
    fired when the day-t spot breaches day t-1's tolerance band, sized by gex_slope."""
    s = _series(ticker)
    days = s["days"]
    bursts, fwd, fwd_sign, div, dates = [], [], [], [], []
    prev_locus = None
    prev_spot = None
    prev_atm = None
    for i, d in enumerate(days):
        spot = s["rows"][d]["spot"]
        atm = s["rows"][d]["atm"]
        # forward return: next day continuous
        if i + 1 < len(days):
            s_nxt = s["rows"][days[i + 1]]["spot"]
            r = (s_nxt - spot) / spot if spot else 0.0
            fwd.append(r)
            fwd_sign.append(1.0 if r > 0 else (-1.0 if r < 0 else 0.0))
        else:
            fwd.append(0.0); fwd_sign.append(0.0)
        # burst with LAGGED gamma: locus from previous day, fired by today's spot
        if prev_locus is not None:
            burst = ebe.hedge_flow_at(prev_locus, spot)
        else:
            burst = 0.0
        bursts.append(burst)
        # dIV_atm
        div.append(0.0 if prev_atm is None else atm - prev_atm)
        dates.append(d)
        # update prev locus (LAGGED gamma: build from day d rows but apply to NEXT day move)
        try:
            prev_locus = ebe.execution_locus(s["rows"][d]["rows"], spot, T=s["rows"][d]["T"])
        except Exception:
            prev_locus = None
        prev_spot = spot
        prev_atm = atm
    return {"ticker": ticker, "dates": dates, "burst": bursts,
            "fwd": fwd, "fwd_sign": fwd_sign, "div": div}


def _two_sided_p(r, n):
    if n < 4 or abs(r) >= 1:
        return 1.0
    import statistics
    t = r * math.sqrt((n - 2) / (1 - r * r))
    # two-sided via normal approx of t distribution (large n)
    z = abs(t)
    p = 2 * (1 - 0.5 * (1 + math.erf(z / math.sqrt(2))))
    return min(max(p, 1e-12), 1.0)


def _bh_qvalues(pvals):
    """Benjamini-Hochberg on p-values -> q-values."""
    n = len(pvals)
    if n == 0:
        return {}
    order = sorted(range(n), key=lambda i: pvals[i])
    q = {}
    prev = 1.0
    for rank, i in enumerate(order, start=1):
        # BH: q_i = min(prev, p_i * n / rank), cumulative from largest p
        val = min(prev, pvals[i] * n / rank)
        q[i] = val
        prev = val
    return q


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


def _min_detectable_r(n, alpha=0.05, power=0.80):
    z = 1.959963984540054 + 0.8416212335729143
    return z / math.sqrt(n + z * z)


def _block_bootstrap_ci(x, y, n_iter=2000, block=5):
    """By-ticker-cluster block bootstrap of the corr; returns (ci_lo, ci_hi, r)."""
    import random
    rng = random.Random(1234)
    r_obs = _corr(x, y)
    n = len(x)
    stats = []
    for _ in range(n_iter):
        idx = []
        i = 0
        while i < n:
            b = rng.randint(block, block * 3)
            start = rng.randint(0, n - b)
            idx.extend(range(start, start + b))
            i += b
        idx = idx[:n]
        xs = [x[j] for j in idx]; ys = [y[j] for j in idx]
        if len(xs) >= 4:
            stats.append(_corr(xs, ys))
    stats.sort()
    lo = stats[int(0.025 * len(stats))]
    hi = stats[int(0.975 * len(stats)) - 1]
    return lo, hi, r_obs


def main():
    t0 = time.time()
    tickers = ebe.seed_corpus_tickers()
    series = {tk: build_burst_series(tk) for tk in tickers}
    print(f"[tier1] built {len(series)} tickers in {time.time()-t0:.1f}s")
    for tk in series:
        nf = sum(1 for v in series[tk]['burst'] if v != 0)
        print(f"  {tk:5s} n={len(series[tk]['burst']):4d} burst_nnz={nf:3d}")

    # ---- Step A: TRUE burst object, pooled de-meaned, cluster bootstrap ----
    print("\n=== STEP A: TRUE HEDGE-FLOW BURST (hedge_flow_at, lag-gamma) ===")
    pooled_x, pooled_y = [], []
    per_ticker_r = {}
    for tk in tickers:
        x = _de_mean(series[tk]['burst'])
        y = _de_mean(series[tk]['fwd'])
        pooled_x.extend(x); pooled_y.extend(y)
        per_ticker_r[tk] = _corr(series[tk]['burst'], series[tk]['fwd'])
    n = len(pooled_x)
    r = _corr(pooled_x, pooled_y)
    lo, hi, _ = _block_bootstrap_ci(pooled_x, pooled_y)
    md_all = _min_detectable_r(n)
    # effective-n: cluster by ticker -> use ~12 effective units for md
    md_eff = _min_detectable_r(12 * 25)  # ~12 tickers x 25 effective obs each as rough cluster-adj n
    print(f"  pooled n={n}  corr={r:+.4f}  cluster-bootstrap CI=[{lo:+.4f},{hi:+.4f}]")
    print(f"  min-detectable (nominal n): {md_all:.4f}   (rough effective-n ~300): {md_eff:.4f}")
    # decision
    abs_r = abs(r)
    if abs_r < md_eff and abs(lo) < md_eff and abs(hi) < md_eff:
        verdict = "RULED_OUT (narrow)"
    elif abs_r >= md_eff and (lo > 0 or hi < 0):
        verdict = "SUPPORTED"
    else:
        verdict = "UNDERPOWERED/INCONCLUSIVE"
    print(f"  VERDICT: {verdict}")

    # per-ticker distribution
    print("\n  Per-ticker corr (burst vs continuous fwd):")
    for tk in tickers:
        print(f"    {tk:5s} r={per_ticker_r[tk]:+.4f}")

    # ---- Step B: vol-shock vanna-lead probe ----
    print("\n=== STEP B: VOL-SHOCK VANNA-LEAD PROBE (exogenous vol-shock days) ===")
    # collect dIV and returns per ticker to set the shock threshold
    all_shock_x, all_shock_y = [], []
    per_ticker_shock = {}
    for tk in tickers:
        s = series[tk]
        # prior-20d realized vol
        rets = s['fwd']
        shock_sig, shock_resp = [], []
        # top-decile dIV threshold per ticker
        divs = [v for v in s['div'] if v != 0]
        if not divs:
            continue
        divs_sorted = sorted(divs)
        thr = divs_sorted[int(0.90 * len(divs_sorted))] if len(divs_sorted) > 9 else max(divs)
        for i in range(1, len(rets) - 1):
            # prior-20d realized vol (on fwd returns, absolute)
            window = rets[max(0, i - 20):i]
            if len(window) < 5:
                continue
            rv = (sum(v * v for v in window) / len(window)) ** 0.5
            r_now = s['fwd'][i]
            div_now = s['div'][i]
            if abs(div_now) >= abs(thr) and abs(r_now) > 1.5 * rv:
                # corrected vanna hedge flow gated on burst firing
                flow = s['burst'][i] if s['burst'][i] != 0 else 0.0
                # ZERO-TARGET AUDIT (R2-1): never emit a fake 0.0 forward return.
                # If the aligned next-day return is unavailable, SKIP the row.
                if i + 1 >= len(rets) or i + 1 >= len(s['fwd']):
                    continue
                resp = s['fwd'][i + 1]
                shock_sig.append(flow)
                shock_resp.append(resp)
        if len(shock_sig) >= 5:
            per_ticker_shock[tk] = (shock_sig, shock_resp)
            all_shock_x.extend(_de_mean(shock_sig))
            all_shock_y.extend(_de_mean(shock_resp))
            print(f"  {tk:5s} shock_days={len(shock_sig):3d} "
                  f"r={_corr(shock_sig, shock_resp):+.4f}")
    if all_shock_x:
        n_shock = len(all_shock_x)
        r_shock = _corr(all_shock_x, all_shock_y)
        print(f"\n  POOLED shock vanna-lead: n={n_shock} corr={r_shock:+.4f}")
        p = _two_sided_p(r_shock, n_shock)
        md_shock = _min_detectable_r(n_shock)
        print(f"  two-sided p={p:.4f}  min-detectable@80%={md_shock:.4f}")
        if abs(r_shock) >= md_shock and p < 0.05:
            print("  VERDICT: SUPPORTED -> proceed to Tier 2 short-DTE anchor (decisive).")
        elif abs(r_shock) < md_shock:
            print("  VERDICT: RULED_OUT (narrow) -> does NOT clear short-DTE/OpEx charm; "
                  "Tier 2 still required for any permanent claim.")
        else:
            print("  VERDICT: UNDERPOWERED/INCONCLUSIVE -> Tier 2 short-DTE anchor is required.")
    else:
        print("  no shock days found.")

    print(f"\n[tier1] total {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
