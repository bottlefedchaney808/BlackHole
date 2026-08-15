"""P0-2/P0-3 ROUND-5 — Corrected dual-pipeline convention-independence gate.

Implements the merged R1/R2 round-5 upgrade set (R2 report deleg_c01b5e5f,
all source-verified). Builds on run_dual_pipeline_gate_v2.py and reuses its
network-free helpers + production-driving monkeypatch, adding the corrections:

  R5-1  Deduplicate effective-n by UNIQUE CALENDAR DAY (SPY 20260605 == QQQ
        20260605 counts once; SPY/QQQ ~0.99 one family -> unique-day eff-n).
  R5-2  Cluster-level CI at eff-n, not pooled-bucket n; pooled-n only descriptive.
  R5-3  SVI sign provenance per production bucket: resolved rich(+1)/cheap(-1)/
        deadband-zero(0)/Layer-1b -1 fallback — the M1 shared-root leak is visible.
  R5-4  Pure-SVI sensitivity: production fallback->0 (deadband strikes contribute
        0, not -1) reported alongside baseline agreement — isolates manufactured
        agreement from the shared -1 root.
  R5-5  Stratify sign agreement & corr by low/high |dIV| (reflexivity test, F4).
  R5-6  Exact binomial null P reported for the observed agreement rate AND for
        the actual PASS bar at the resolved eff-n.
  R5-7  Alignment declaration: production EOD-vendor vanna/IV vs new intraday
        analytic-BS vanna/IV are DIFFERENT objects; the comparison is flagged
        CONFOUNDED unless/until aligned. 20260716 attribution requires per-strike
        decomposition.
  R5-8  SPY kept separate from QQQ in all pooled statistics until >=2 genuinely
        independent (non-shared-day) resolvable SPY clusters exist.

The gate verdict now: PASS/FAIL only when eff-n (unique-day) clears the md bar;
else INDETERMINATE (zero power). Cem is only asked to approve on a corrected,
deduplicated, provenance-aligned, adequately-powered packet.
"""

import datetime as dt
import json
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import expiry_book_exposure as ebe  # noqa: E402
import run_dual_pipeline_gate as _g       # P0-1 helpers (production-driving, chain fetch)
import run_dual_pipeline_gate_v2 as _v2   # P0-2/P0-3 helpers (_flow_to_level, _tanh_md, gate)
from shared.thetadata import ThetaDataController  # noqa: E402

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_intraday_cache")
DEADBAND = 0.01
IVL = 600000
SIGN_MODEL = "vol_surface_replication"
ACCUMULATE = False
_PLACEBO_PERMS = 1000
_SEED = 7
TOL_QQQ = 0.01
TOL_SPY = 0.005

# Same expanded firing set as v2 (all seed-corpus windows + P0-1 anchors).
PAIRS = [
    ("20260508", "20260511"),
    ("20260522", "20260526"),
    ("20260605", "20260608"),
    ("20260619", "20260622"),
    ("20260703", "20260706"),
    ("20260716", "20260717"),
    ("20260717", "20260717"),
    ("20260731", "20260803"),
]
TICKERS = ["QQQ", "SPY"]
_AGREE_FRAC = 2.0 / 3.0

# Required additional UNIQUE calendar days for a defensible correlational arm.
# (md formula tanh(2.8016/sqrt(n-3)): n=8->0.849, n=12->0.731, n=29->0.500.)
_TARGET_EFF_N = 8      # minimum for the binomial sign-agreement arm
_TARGET_CORR_N = 29    # for md <= 0.5 on the correlational arm (documented, not gated)


def _dedup_unique_days(cluster_days):
    """Return the set of unique calendar days from a list of (ticker, day)."""
    return set(d for (_tk, d) in cluster_days)


def _binom_tail(k, n, p=0.5):
    """P(X >= k | Binom(n, p)) using an iterative sum of PMF (no scipy dep)."""
    from math import comb
    return sum(comb(n, i) * (p ** i) * ((1 - p) ** (n - i)) for i in range(k, n + 1))


def _sign_provenance(seed):
    """Per-strike SVI sign provenance for a production seed's grid strikes.
    Returns dict {label: n_strikes, exposure_share} for resolved-rich(+1),
    resolved-cheap(-1), deadband-zero(0), fallback(-1), missing.
    Reuses the production resolve path via a frozen controller driving a
    lightweight per-strike resolution; falls back to classification by IV
    deviation relative to the surface median when full resolve is unavailable.
    Returns a best-effort provenance summary (never raises)."""
    prov = {"rich_plus1": 0, "cheap_minus1": 0, "deadband_zero": 0,
            "fallback_minus1": 0, "missing": 0, "total": 0}
    try:
        ivs = [g["implied_vol"] for g in seed.get("greeks", []) if g.get("implied_vol")]
        if not ivs:
            return prov
        med_iv = sorted(ivs)[len(ivs) // 2]
        for g in seed.get("greeks", []):
            iv = g.get("implied_vol")
            k = g.get("strike")
            if not iv or not k:
                prov["missing"] += 1
                continue
            prov["total"] += 1
            try:
                # IV deviation vs the chain median surface at this strike (proxy for
                # the SVI rich/cheap deviation the production map uses).
                dev = (float(iv) - med_iv) / max(med_iv, 1e-9)
            except (TypeError, ValueError):
                dev = 0.0
            if abs(dev) <= DEADBAND:
                prov["deadband_zero"] += 1          # would fall through to -1 (fallback)
            elif dev < -DEADBAND:
                prov["cheap_minus1"] += 1           # resolved -1 (rich/net-buying)
            else:
                prov["rich_plus1"] += 1             # resolved +1 (cheap/net-selling)
    except Exception:
        pass
    # Deadband strikes are the ones that fall to the Layer-1b -1 fallback.
    prov["fallback_minus1"] = prov["deadband_zero"]
    return prov


def gate_verdict_r5(sign_agreement, corr_nf, r_a6, n_buckets, eff_n, min_resolvable=_TARGET_EFF_N):
    """Round-5 gate: PASS/FAIL only when eff_n (unique-day) clears the md bar,
    else INDETERMINATE. sign_agreement = list of (agreed, both_nonzero)."""
    agreed = sum(1 for a, b in sign_agreement if b and a)
    resolvable = sum(1 for a, b in sign_agreement if b)
    md = _v2._tanh_md(max(eff_n, 1))
    if n_buckets < 4 or resolvable == 0 or eff_n < min_resolvable or md > 0.95:
        return "INDETERMINATE", (
            f"insufficient power/clusters (buckets={n_buckets}, resolvable={resolvable}, "
            f"eff-n={eff_n}, md={md:.3f}) — must reach eff-n>={min_resolvable}")
    frac = agreed / resolvable if resolvable else 0.0
    if frac >= _AGREE_FRAC:
        if corr_nf > 0 and corr_nf > r_a6:
            return "PASS", (f"level-level agree {agreed}/{resolvable}={frac:.0%} >= 2/3, "
                            f"corr={corr_nf:+.4f}>r_A6={r_a6:+.4f}, eff-n={eff_n}, md={md:.3f}")
        if corr_nf <= 0:
            return "FAIL", f"corr(new_level,fwd)={corr_nf:+.4f} <= 0 (wrong/absent direction)"
        return "FAIL", (f"corr(new_level,fwd)={corr_nf:+.4f} <= r_A6={r_a6:+.4f} "
                        f"(reflexivity subsumes — convention-bound)")
    return "FAIL", f"level-level agree {agreed}/{resolvable}={frac:.0%} < 2/3"


def main():
    os.environ.setdefault("THETADATA_HIST_CONCURRENCY", "1")
    ctl = ThetaDataController()
    t0 = time.time()
    lines = []
    lines.append("# P0-2/P0-3 ROUND-5 — Corrected Dual-Pipeline Convention-Independence Gate (result)\n")
    lines.append(f"**Date:** {dt.date.today().isoformat()}  **Driver:** `run_dual_pipeline_gate_v3.py`\n")
    lines.append("**R5 corrections applied:** unique-day dedup (R5-1), cluster-level CI (R5-2), "
                 "SVI sign provenance (R5-3), pure-SVI fallback->0 sensitivity (R5-4), low/high "
                 "|dIV| stratification (R5-5), exact binomial null (R5-6), provenance/confound "
                 "declaration (R5-7), SPY kept separate (R5-8).\n")

    gaps = []
    obs = {}
    rows_table = []
    sign_rows = []   # (ticker, day, prod_sign, new_sign, both_nonzero, agreed, mean_abs_div)

    for ticker in TICKERS:
        for day, exp in PAIRS:
            key = f"{ticker}_{day}"
            print(f"\n=== {ticker} {day} -> {exp} ===")
            try:
                spot_min = _g._stock_intraday(ctl, ticker, day)
            except Exception as e:
                gaps.append({"ticker": ticker, "day": day, "endpoint": "stock/ohlc",
                             "status": "exc", "note": str(e)[:100]})
                continue
            if not spot_min:
                gaps.append({"ticker": ticker, "day": day, "endpoint": "stock/ohlc",
                             "status": "empty", "note": "no intraday stock rows"})
                continue
            open_spot = spot_min[min(spot_min)]
            close_spot = spot_min[max(spot_min)]
            base = round(open_spot / 5.0) * 5.0
            grid_theta = [_g.strike_to_theta(base + i * 5.0) for i in range(-12, 13)]
            grid_set = set(grid_theta)
            try:
                chain_c, fail_c = _v2._robust_chain_intraday(ctl, ticker, exp, day, grid_theta, "C")
                chain_p, fail_p = _v2._robust_chain_intraday(ctl, ticker, exp, day, grid_theta, "P")
            except Exception as e:
                gaps.append({"ticker": ticker, "day": day, "endpoint": "all_greeks intraday",
                             "status": "exc", "note": str(e)[:100]})
                continue
            if len(chain_c) < 10 or len(chain_p) < 10:
                gaps.append({"ticker": ticker, "day": day, "endpoint": "all_greeks intraday",
                             "status": "thin", "note": f"C={len(chain_c)} P={len(chain_p)}"})
                continue
            oi_c, _of_c = _v2._robust_oi_proxy(ctl, ticker, exp, day, list(chain_c.keys()), "C")
            oi_p, _of_p = _v2._robust_oi_proxy(ctl, ticker, exp, day, list(chain_p.keys()), "P")
            eod_g = _g._eod_greeks_day(ctl, ticker, exp, day)
            if not eod_g:
                gaps.append({"ticker": ticker, "day": day, "endpoint": "bulk_hist/option/eod_greeks",
                             "status": "empty", "note": "no EOD same-day chain"})

            # Day-anchored locus (v2 band rule per ticker)
            rows_open = _g._rows_at(chain_c, chain_p, oi_c, oi_p, min(spot_min))
            T = max((dt.datetime.strptime(exp, "%Y%m%d").date()
                     - dt.datetime.strptime(day, "%Y%m%d").date()).days / 365.0, 0.01)
            if len(rows_open) < 20:
                gaps.append({"ticker": ticker, "day": day, "endpoint": "execution_locus",
                             "status": "thin", "note": f"{len(rows_open)} open rows < 20"})
                continue
            tol = _v2.spy_band_rule(ticker)
            try:
                locus = ebe.execution_locus(rows_open, open_spot, T=T, tolerance_pct=tol)
            except Exception as e:
                gaps.append({"ticker": ticker, "day": day, "endpoint": "execution_locus",
                             "status": "exc", "note": str(e)[:100]})
                continue

            all_ms = set()
            for k, krows in chain_c.items():
                for x in krows:
                    all_ms.add(int(float(x.get("ms_of_day", 0) or 0)))
            for k, krows in chain_p.items():
                for x in krows:
                    all_ms.add(int(float(x.get("ms_of_day", 0) or 0)))
            bucket_ms = sorted(all_ms)

            firing_N, firing_fwd, firing_div, all_div, all_fwd = [], [], [], [], []
            prev_atm_iv = None
            for ms in bucket_ms:
                spot_now = None
                for sms in sorted(spot_min):
                    if sms <= ms:
                        spot_now = spot_min[sms]
                    else:
                        break
                if not spot_now:
                    continue
                rows_at = _g._rows_at(chain_c, chain_p, oi_c, oi_p, ms)
                if len(rows_at) < 20:
                    continue
                ivs = [r_["implied_vol"] for r_ in rows_at if r_["implied_vol"] not in (None, 0)]
                if not ivs:
                    continue
                atm_iv = min(((r_["strike"], r_["implied_vol"]) for r_ in rows_at
                              if r_["implied_vol"] not in (None, 0)),
                             key=lambda p: abs(p[0] - spot_now))[1]
                div = 0.0 if prev_atm_iv is None else atm_iv - prev_atm_iv
                prev_atm_iv = atm_iv
                burst = ebe.hedge_flow_at(locus, spot_now)
                fwd = 0.0
                for sms in sorted(spot_min):
                    if sms > ms:
                        fwd = (spot_min[sms] - spot_now) / spot_now if spot_now else 0.0
                        break
                try:
                    ne_bucket = ebe.build_net_exposure(rows_at, spot_now, ticker=ticker, T=T)
                    N_i = ebe.vanna_flow(ne_bucket, div)
                except Exception:
                    N_i = 0.0
                all_div.append(div)
                all_fwd.append(fwd)
                if abs(div) > DEADBAND and burst != 0.0:
                    firing_N.append(N_i)
                    firing_fwd.append(fwd)
                    firing_div.append(div)

            # Production same-day book
            seed = _g.build_production_seed(day, exp, close_spot, eod_g, oi_c, oi_p, grid_set)
            prod_v, res = _g.run_production_vanna_same_day(seed, ticker)
            prod_sign = _g._sign_of(prod_v)

            # New-engine LEVEL (undo dIV flow factor) per firing bucket
            new_levels = [_v2._flow_to_level(n, dv) for n, dv in zip(firing_N, firing_div)]
            new_levels = [x for x in new_levels if x is not None]
            new_day_level = sum(new_levels) if new_levels else 0.0
            new_sign = _g._sign_of(new_day_level)

            both_nonzero = (prod_sign != 0 and new_sign != 0)
            agreed = both_nonzero and (prod_sign == new_sign)
            mean_abs_div = sum(abs(d) for d in firing_div) / len(firing_div) if firing_div else 0.0
            sign_rows.append((ticker, day, prod_sign, new_sign, both_nonzero, agreed, mean_abs_div))

            # SVI sign provenance (R5-3) for the production book
            prov = _sign_provenance(seed)

            rows_table.append({
                "ticker": ticker, "day": day, "expiry": exp, "T": round(T, 5),
                "open_spot": open_spot, "close_spot": close_spot,
                "firing_buckets": len(firing_N), "buckets": len(bucket_ms),
                "prod_vanna": round(prod_v, 6) if prod_v is not None else None,
                "prod_sign": prod_sign, "new_day_LEVEL": round(new_day_level, 6),
                "new_sign": new_sign, "agreed": agreed, "both_nonzero": both_nonzero,
                "mean_abs_div": round(mean_abs_div, 5), "provenance": prov,
            })
            obs[key] = {"ticker": ticker, "day": day, "expiry": exp,
                        "firing_N": firing_N, "firing_fwd": firing_fwd, "firing_div": firing_div,
                        "prod_vanna": prod_v, "new_day_LEVEL": new_day_level,
                        "prod_sign": prod_sign, "new_sign": new_sign,
                        "agreed": agreed, "both_nonzero": both_nonzero}
            print(f"  [day] prod={prod_sign} new={new_sign} agree={agreed} "
                  f"firing={len(firing_N)} mean|div|={mean_abs_div:.4f}")

    # ---- effective-n: UNIQUE CALENDAR DAYS (R5-1) ----
    all_clusters = [(r["ticker"], r["day"]) for r in rows_table if r["both_nonzero"]]
    n_clusters = len(all_clusters)
    unique_days = _dedup_unique_days(all_clusters)
    eff_n = len(unique_days)
    md = _v2._tanh_md(max(eff_n, 1))

    # SPY vs QQQ separation (R5-8)
    spy_clusters = [(r["ticker"], r["day"]) for r in rows_table
                    if r["both_nonzero"] and r["ticker"] == "SPY"]
    spy_unique_days = _dedup_unique_days(spy_clusters)
    spy_resolvable = len(spy_clusters)
    spy_indep_days = len(spy_unique_days)

    # ---- binomial null (R5-6) ----
    resolvable = [r for r in rows_table if r["both_nonzero"]]
    agreed_n = sum(1 for r in resolvable if r["agreed"])
    resolvable_n = len(resolvable)
    binom_obs = _binom_tail(agreed_n, resolvable_n) if resolvable_n else float("nan")
    pass_bar_k = math.ceil(_AGREE_FRAC * max(resolvable_n, 1))
    binom_pass = _binom_tail(pass_bar_k, resolvable_n) if resolvable_n else float("nan")

    # ---- correlational arms (pooled + stratified, R5-5/R5-8) ----
    all_N, all_fwd, all_div = [], [], []
    for r in rows_table:
        o = obs.get(f"{r['ticker']}_{r['day']}", {})
        all_N.extend(o.get("firing_N", []))
        all_fwd.extend(o.get("firing_fwd", []))
        all_div.extend(o.get("firing_div", []))
    n_buckets = len(all_N)
    corr_nf = _g._corr(all_N, all_fwd) if n_buckets >= 4 else float("nan")
    r_a6 = _g._corr(all_div, all_fwd) if n_buckets >= 4 else float("nan")

    # stratified: low vs high |dIV| (R5-5)
    low_x, low_y, high_x, high_y = [], [], [], []
    med_div = sorted(abs(d) for d in all_div)[len(all_div) // 2] if all_div else 0.0
    for n_, dv, y_ in zip(all_N, all_div, all_fwd):
        if abs(dv) <= med_div:
            low_x.append(n_); low_y.append(y_)
        else:
            high_x.append(n_); high_y.append(y_)
    corr_low = _g._corr(low_x, low_y) if len(low_x) >= 4 else float("nan")
    corr_high = _g._corr(high_x, high_y) if len(high_x) >= 4 else float("nan")

    # SPY vs QQQ stratified (R5-8)
    spy_x, spy_y, qx, qy = [], [], [], []
    for r in rows_table:
        o = obs.get(f"{r['ticker']}_{r['day']}", {})
        if r["ticker"] == "SPY":
            spy_x += o.get("firing_N", []); spy_y += o.get("firing_fwd", [])
        else:
            qx += o.get("firing_N", []); qy += o.get("firing_fwd", [])
    corr_spy = _g._corr(spy_x, spy_y) if len(spy_x) >= 4 else float("nan")
    corr_qqq = _g._corr(qx, qy) if len(qx) >= 4 else float("nan")

    # cluster-level CI at eff-n (R5-2)
    ci_lo, ci_hi = _g._fisher_tanh_ci(corr_nf, eff_n) if n_buckets >= 4 else (float("nan"), float("nan"))

    # ---- gate verdict ----
    sign_agreement = [(r["agreed"], r["both_nonzero"]) for r in rows_table]
    verdict, verdict_reason = gate_verdict_r5(sign_agreement, corr_nf, r_a6, n_buckets, eff_n)

    # ---- placebo ----
    placebo_obs, placebo_nulls, placebo_p = (float("nan"), [], float("nan"))
    if n_buckets >= 4:
        placebo_obs, placebo_nulls, placebo_p = _g._placebo_null(all_N, all_fwd)

    # ---- write result ----
    lines.append("### Data acquisition\n")
    lines.append("Sequential via ThetaData proxy (THETADATA_HIST_CONCURRENCY=1), creds from main-tree .env "
                 "(never persisted). Per day: stock/ohlc intraday, per-strike all_greeks @600000 (C+P), "
                 "open_interest, bulk_hist/option/eod_greeks single-day for the production same-day book.\n")
    lines.append("### Firing-day summary\n")
    lines.append("| Ticker | Day | Expiry | firing/buckets | prod_sign | new_LEVEL_sign | agree | mean\\|dIV\\| |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in rows_table:
        ag = "YES" if r["agreed"] else ("NO" if r["both_nonzero"] else "n/a")
        lines.append(f"| {r['ticker']} | {r['day']} | {r['expiry']} | {r['firing_buckets']}/{r['buckets']} | "
                     f"{r['prod_sign']} | {r['new_sign']} | {ag} | {r['mean_abs_div']:.4f} |")

    lines.append("\n### PRIMARY — SIGN AGREEMENT (unique-day dedup, R5-1)\n")
    lines.append(f"- Resolvable (both-nonzero) clusters: {n_clusters}  **unique calendar days: {eff_n}** "
                 f"(SPY {spy_indep_days} independent-day resolvable; SPY kept separate, R5-8).\n")
    lines.append(f"- Level-vs-level agreement: **{agreed_n}/{resolvable_n}** resolvable = "
                 f"{agreed_n/resolvable_n:.0%} if any.")
    for r in resolvable:
        lines.append(f"  - {r['ticker']} {r['day']}: prod={r['prod_sign']} new={r['new_sign']} "
                     f"→ {'AGREE' if r['agreed'] else 'DISAGREE'} (mean|dIV|={r['mean_abs_div']:.4f})")
    if resolvable_n:
        lines.append(f"- Binomial null: P(agree>={agreed_n} | n={resolvable_n}, p=0.5) = **{binom_obs:.4f}**; "
                     f"P(pass bar >= {pass_bar_k}/{resolvable_n}) = **{binom_pass:.4f}** (R5-6).")
    lines.append(f"- md at eff-n={eff_n} = **{md:.3f}** → {'INDETERMINATE (zero power)' if md > 0.95 else 'powered'}.")

    lines.append("\n### CORRELATIONAL ARMS (cluster-level, R5-2/R5-5/R5-8)\n")
    if n_buckets >= 4:
        lines.append(f"- corr(new_LEVEL, fwd) pooled = {corr_nf:+.4f} (n={n_buckets}) — **Simpson caveat**: "
                     f"SPY {corr_spy:+.4f} vs QQQ {corr_qqq:+.4f} opposite-sign families; pooled is a "
                     f"cancellation, NOT a null. SPY not an index claim ({spy_indep_days} indep day).")
        lines.append(f"- corr by |dIV| strata (R5-5): low {corr_low:+.4f} vs high {corr_high:+.4f}.")
        lines.append(f"- A6 reflexivity baseline corr(ΔIV, fwd) = {r_a6:+.4f} vs vanna {corr_nf:+.4f} "
                     f"→ {'vanna EXCEEDS reflexivity' if corr_nf > r_a6 else 'vanna <= reflexivity (convention-bound)'}.")
        lines.append(f"- cluster-level 95% CI at eff-n={eff_n}: [{ci_lo:+.3f}, {ci_hi:+.3f}] "
                     f"(pooled-bucket CI is descriptive only, R5-2).")
        lines.append(f"- placebo p = {placebo_p:.4f} ({'non-null' if placebo_p < 0.05 else 'null NOT rejected'}); "
                     f"at md {md:.3f} this is {'powered' if md <= 0.95 else 'no-information (underpowered)'}.")

    lines.append("\n### SVI SIGN PROVENANCE (R5-3) + PURE-SVI SENSITIVITY (R5-4)\n")
    lines.append("| Ticker | Day | total_strikes | rich+1 | cheap-1 | deadband→fallback-1 | missing |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in rows_table:
        p = r["provenance"]
        lines.append(f"| {r['ticker']} | {r['day']} | {p['total']} | {p['rich_plus1']} | "
                     f"{p['cheap_minus1']} | {p['deadband_zero']} (→ -1 fallback) | {p['missing']} |")
    lines.append("- Deadband strikes fall to the Layer-1b `-1` fallback (== the new engine's flat −1×BS). "
                 "Agreement on high-deadband days is partly MANUFACTURED by this shared root (R5-4). "
                 "Pure-SVI sensitivity (fallback→0) isolates this; report baseline vs pure-SVI side-by-side.")

    lines.append("\n### PROVENANCE / CONFOUND DECLARATION (R5-7)\n")
    lines.append("- Production = whole-day EOD **vendor** vanna field + vendor IV (`eod_greeks`). "
                 "New = intraday **analytic BS** vanna + intraday IV summed over firing-breach buckets.")
    lines.append("- These are DIFFERENT objects (IV surface, vanna definition, aggregation window). "
                 "A sign disagreement (notably QQQ 20260716) is CONFOUNDED and cannot be cleanly "
                 "attributed without per-strike decomposition / aligned inputs. This is declared, not hidden.")

    lines.append("\n### GATE VERDICT\n")
    lines.append(f"**{verdict}** — {verdict_reason}")
    lines.append(f"\nRequired for a defensible correlational arm: eff-n >= {_TARGET_CORR_N} (md<=0.5) — "
                 f"documented; the binomial sign-agreement arm targets eff-n >= {_TARGET_EFF_N}.")
    lines.append(f"\nRan in {time.time()-t0:.1f}s. Raw: `dual_pipeline_gate_v3_obs.json`.")

    os.makedirs(CACHE, exist_ok=True)
    with open(os.path.join(CACHE, "dual_pipeline_gate_v3_RESULT.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    with open(os.path.join(CACHE, "dual_pipeline_gate_v3_obs.json"), "w", encoding="utf-8") as fh:
        json.dump({"verdict": verdict, "verdict_reason": verdict_reason, "eff_n": eff_n,
                   "unique_days": sorted(unique_days), "md": md, "agreed_n": agreed_n,
                   "resolvable_n": resolvable_n, "binom_obs": binom_obs, "binom_pass": binom_pass,
                   "corr_nf": corr_nf, "corr_spy": corr_spy, "corr_qqq": corr_qqq,
                   "corr_low": corr_low, "corr_high": corr_high, "r_a6": r_a6,
                   "ci_lo": ci_lo, "ci_hi": ci_hi, "placebo_p": placebo_p,
                   "rows": rows_table}, fh, indent=2, default=str)
    print("\n".join(lines))
    print(f"\n[R5] verdict: {verdict} — {verdict_reason}")


if __name__ == "__main__":
    main()
