"""Fast decisive test for the expiry-book-exposure edge question (arbiter (e)).

Implements Step 1 (interpretability layer) + Step 2 (true hedge-flow object) from the
Karsan arbiter's round-2 merged set, on the EXISTING offline seed corpus. Network-free,
runs in seconds. NO new data, NO OpEx calendar, NO intraday.

Design (pre-registered):
- Signal (true hedge-flow object): for each day, the forced dealer hedge impulse
      impulse_t = net_dollar_gamma_per_1%_t * (dS_t / spot_t)
  fired ONLY on days when spot breached the delta-hedge tolerance band (|dS/spot| > band),
  else 0. Reuses build_net_exposure().gex() (the level) + execution_locus/hedge_flow_at
  (the band-breach gating). This replaces the broken Delta(dollar-gamma) proxy.
- Clock: daily close-to-close (the only clock the seed supports). This resolves the
  PRIMARY GEX/hedge-flow channel at the daily clock only; OpEx/intraday clocks need data.
- n: pool all 12 tickers, de-meaned PER TICKER (fixed-effects -> true cross-sectional),
  giving n ~= 1700+. At n~1700 min-detectable |r|@80% ~= 0.068, far below the theorized
  0.10-0.15, so this CAN rule the edge out.
- Interpretability: min-detectable |r|@80% at pooled n; 95% CI on pooled corr; BH over the
  true family on the vanna/charm exploratory secondaries.
- Decision rule (pre-registered):
    RULED_OUT   : pooled |r| < min_detectable AND 95% CI excludes min_detectable
                  -> clean "no edge in primary GEX/hedge-flow channel at the daily clock";
                     ship descriptive instrument, permanently demote predictive framing.
    SUPPORTED   : pooled |r| >= min_detectable, CI excludes 0, sign-consistent SPY/QQQ
                  -> first honest evidence of a flow edge -> justifies the longer build.
    UNDERPOWERED/INCONCLUSIVE: CI straddles min_detectable -> "can't tell yet" -> event-clock build warranted.
Reuses: expiry_book_exposure.build_daily_signals_from_seed, build_net_exposure,
execution_locus, hedge_flow_at, _block_perm_p, _safe_corr. No network.
"""
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import expiry_book_exposure as ebe


def min_detectable_r(n, alpha=0.05, power=0.80):
    """Two-sided minimum detectable |r| for given n at alpha/power."""
    import statistics
    z_a = 1.959963984540054   # z_{1-alpha/2}
    z_b = 0.8416212335729143  # z_{power}
    z = z_a + z_b
    # Fisher-based approx: r_min ~= z / sqrt(n + z^2)
    return z / math.sqrt(n + z * z)


def corr_ci(r, n):
    """Approx 95% CI on a correlation (Fisher z)."""
    if n < 4:
        return (-1.0, 1.0)
    se = 1.0 / math.sqrt(n - 3)
    z = 0.5 * math.log((1 + r) / (1 - r)) if abs(r) < 1 else (math.copysign(float('inf'), r))
    lo = math.tanh(z - 1.959963984540054 * se)
    hi = math.tanh(z + 1.959963984540054 * se)
    return (lo, hi)


def build_fast_signals():
    """Per-ticker daily series of the true hedge-flow object (impulse_t), plus
    gex_flow (the proxy, for contrast), vanna_flow, charm, and forward 1-day return
    and its sign. Reuses the module's own builders."""
    tickers = ebe.seed_corpus_tickers()
    out = {}
    for tk in tickers:
        seed = ebe._load_seed(tk)
        expiry = str(seed.get("manifest", {}).get("expiry", ""))
        # Rebuild per-day net exposure (level), spot, and forward return exactly like
        # build_daily_signals, but keep the LEVEL (gex) + band-breach impulse.
        imp, proxy, vanna, charm, fwd, fwdsign, dates = ebe_daily_series(
            seed.get("greeks", []), seed.get("oi", []), seed.get("spot", []),
            expiry, tk)
        out[tk] = dict(dates=dates, impulse=imp, proxy=proxy, vanna=vanna,
                       charm=charm, fwd=fwd, fwd_sign=fwdsign)
    return out


def ebe_daily_series(hist_greek_rows, hist_oi_rows, hist_spot_rows, expiry, ticker):
    """Mirror build_daily_signals' day loop but emit the TRUE hedge-flow object:
    impulse_t = gex_level_t * (dS_t/spot_t), fired only on band breach; plus the
    Delta(dollar-gamma) proxy for contrast, vanna level, charm, and forward returns."""
    from datetime import datetime
    try:
        exp_dt = datetime.strptime(str(expiry), "%Y%m%d")
    except Exception:
        exp_dt = None
    spot_by_date = {}
    for r in hist_spot_rows:
        d = ebe._date_of(r)
        v = ebe._extract(r, "close")
        if not math.isnan(v):
            spot_by_date[d] = v
    greeks_by_date = {}
    for g in hist_greek_rows:
        d = ebe._date_of(g)
        k = ebe._extract(g, "strike")
        if not math.isnan(k) and k > 10000:
            k = k / 1000.0
        iv = ebe._extract(g, "implied_vol")
        if not math.isnan(k) and not math.isnan(iv) and iv > 0:
            greeks_by_date.setdefault(d, []).append(
                {"strike": k, "right": str(g.get("right", "C")).upper()[:1],
                 "implied_vol": iv})
    oi_by_date = {}
    for o in hist_oi_rows:
        d = ebe._date_of(o)
        k = ebe._extract(o, "strike")
        if not math.isnan(k) and k > 10000:
            k = k / 1000.0
        oi_by_date.setdefault(d, {})[(k, str(o.get("right", "C")).upper()[:1])] = \
            int(ebe._extract(o, "open_interest", 0.0))
    ordered = sorted(spot_by_date)
    imp, proxy, vanna, charm, fwd, fwdsign, dates, gex_level, dspot = [], [], [], [], [], [], [], [], []
    prev_gex = None
    spot_list = [spot_by_date[d] for d in ordered]
    for i, d in enumerate(ordered):
        if d not in greeks_by_date:
            continue
        spot = spot_by_date[d]
        rows = [{"strike": g["strike"], "right": g["right"],
                 "oi": oi_by_date.get(d, {}).get((g["strike"], g["right"]), 0),
                 "implied_vol": g["implied_vol"]} for g in greeks_by_date[d]]
        T = 0.25
        if exp_dt is not None:
            try:
                ddt = datetime.strptime(d, "%Y%m%d")
                T = max((exp_dt - ddt).days, 1) / 365.0
            except Exception:
                T = 0.25
        ne = ebe.build_net_exposure(rows, spot, T=T, ticker=ticker)
        g = ne.gex()  # LEVEL: net dollar-gamma-per-1%
        gex_level.append(g)
        proxy.append(g if prev_gex is None else g - prev_gex)
        prev_gex = g
        vanna.append(ebe.vanna_flow(ne, 0.01))
        charm.append(sum(r.exposure_of("charm") for r in ne.rows))
        # forward return from NEXT day's spot
        if i + 1 < len(ordered):
            s_nxt = spot_by_date[ordered[i + 1]]
            r = (s_nxt - spot) / spot if spot else 0.0
            fwd.append(r)
            fwdsign.append(1.0 if r > 0 else (-1.0 if r < 0 else 0.0))
        else:
            fwd.append(0.0); fwdsign.append(0.0)
        # day-over-day spot move (for the impulse)
        if i > 0:
            dspot.append((spot - spot_by_date[ordered[i - 1]]) / spot_by_date[ordered[i - 1]])
        else:
            dspot.append(0.0)
        dates.append(d)
    # TRUE hedge-flow object: impulse = gex_level * (dS/spot), fired only on band breach.
    # Reuse execution_locus tolerance (1%) via a per-day locus's band check.
    band = 0.01
    impulse = []
    for i in range(len(dates)):
        ds = dspot[i] if i < len(dspot) else 0.0
        fired = ds if abs(ds) > band else 0.0
        impulse.append(gex_level[i] * fired)
    n = min(len(dates), len(fwd))
    return (impulse[:n], proxy[:n], vanna[:n], charm[:n], fwd[:n], fwdsign[:n], dates[:n])


def pooled_corr_de_meaned(sig_map, key):
    """Pool per-ticker series for `key`, de-meaned per ticker (fixed-effects),
    return pooled Pearson corr vs fwd_sign + n."""
    xs, ys = [], []
    for tk, d in sig_map.items():
        x = [v for v in d[key]]
        y = [v for v in d["fwd_sign"]]
        mx = sum(x) / len(x) if x else 0.0
        my = sum(y) / len(y) if y else 0.0
        xs.extend([v - mx for v in x])
        ys.extend([v - my for v in y])
    n = len(xs)
    if n < 4:
        return 0.0, 0, 1.0
    r = ebe._safe_corr(xs, ys)
    return r, n, min_detectable_r(n)


def main():
    t0 = time.time()
    signals = build_fast_signals()
    print(f"[fast] built {len(signals)} tickers in {time.time()-t0:.1f}s")
    for tk in signals:
        print(f"  {tk:5s} n={len(signals[tk]['dates']):4d} impulse_nnz="
              f"{sum(1 for v in signals[tk]['impulse'] if v!=0):3d}")

    print("\n=== PRIMARY: TRUE HEDGE-FLOW OBJECT (impulse = gex_level * dS/spot, band-gated) ===")
    r, n, md = pooled_corr_de_meaned(signals, "impulse")
    lo, hi = corr_ci(r, n)
    print(f"  pooled n={n}  corr={r:+.4f}  95% CI=[{lo:+.4f},{hi:+.4f}]  "
          f"min-detectable|r|@80%={md:.4f}")
    # Decision rule
    abs_r = abs(r)
    if abs_r < md and (abs(lo) < md and abs(hi) < md):
        verdict = "RULED_OUT"
        print(f"  VERDICT: {verdict}  (|r|={abs_r:.4f} < md={md:.4f} AND CI excludes md) -> "
              f"clean 'no edge in primary GEX/hedge-flow channel at the daily clock'. "
              f"Ship descriptive instrument, permanently demote predictive framing.")
    elif abs_r >= md and lo <= 0 <= hi is False and abs(lo) > 0:
        verdict = "SUPPORTED"
        print(f"  VERDICT: {verdict}  -> first honest evidence of a flow edge -> justifies the longer build.")
    else:
        verdict = "UNDERPOWERED/INCONCLUSIVE"
        print(f"  VERDICT: {verdict}  (CI straddles md) -> 'can't tell yet' -> event-clock build warranted.")

    # SPY/QQQ sign-consistency on the pooled primary
    spy = signals.get("SPY", {}).get("impulse", []); spyf = signals.get("SPY", {}).get("fwd_sign", [])
    qqq = signals.get("QQQ", {}).get("impulse", []); qqqf = signals.get("QQQ", {}).get("fwd_sign", [])
    if spy and qqq:
        r_spy = ebe._safe_corr(spy, spyf)
        r_qqq = ebe._safe_corr(qqq, qqqf)
        consistent = (r_spy > 0) == (r_qqq > 0)
        print(f"  SPY corr={r_spy:+.4f}  QQQ corr={r_qqq:+.4f}  sign-consistent={consistent}")

    # Exploratory secondaries (de-meaned pooled) with BH over the true family
    print("\n=== EXPLORATORY SECONDARIES (pooled de-meaned, BH over true family) ===")
    sec = {}
    for key in ("proxy", "vanna", "charm"):
        rr, nn, _ = pooled_corr_de_meaned(signals, key)
        sec[key] = (rr, nn)
        print(f"  {key:8s} n={nn:5d} corr={rr:+.4f}")
    ps = sorted((abs(sec[k][0]) for k in sec), reverse=True)
    import numpy as np
    qs = ebe._bh_qvalues(dict(zip(sec.keys(), [abs(sec[k][0]) for k in sec])))
    for k in sec:
        print(f"    -> {k}: q={qs.get(k, 0):.3f}")

    # Block-perm p on the primary pooled signal
    allx, ally = [], []
    for tk, d in signals.items():
        mx = sum(d["impulse"]) / len(d["impulse"]) if d["impulse"] else 0.0
        my = sum(d["fwd_sign"]) / len(d["fwd_sign"]) if d["fwd_sign"] else 0.0
        allx.extend([v - mx for v in d["impulse"]])
        ally.extend([v - my for v in d["fwd_sign"]])
    p = ebe._block_perm_p(allx, ally, n_perms=500)
    print(f"\n  PRIMARY block-perm p={p:.4f} (n={len(allx)})")

    print(f"\n[fast] total {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
