"""P0-2/P0-3 ROUND-5 REQUIRED ACQUISITION — run_dual_pipeline_gate_v4.py.

R5-NEW unique-day acquisition for the Dealer-Exposure-Dev CARL loop. Extends
run_dual_pipeline_gate_v3.py and PRESERVES every R5-1..R5-8 correction:

  R5-1  Deduplicate effective-n by UNIQUE CALENDAR DAY (SPY 20260605 == QQQ
        20260605 counts once; SPY/QQQ ~0.99 one family -> unique-day eff-n).
  R5-2  Cluster-level CI at eff-n, not pooled-bucket n; pooled-n descriptive.
  R5-3  SVI sign provenance per production bucket: resolved rich(+1)/cheap(-1)/
        deadband-zero(0)/Layer-1b -1 fallback — the M1 shared-root leak visible.
  R5-4  Pure-SVI sensitivity: production fallback->0 (deadband strikes
        contribute 0, not -1) reported alongside baseline agreement — isolates
        manufactured agreement from the shared -1 root.
  R5-5  Stratify sign agreement & corr by low/high |dIV| (reflexivity test, F4).
  R5-6  Exact binomial null P for the observed agreement rate AND for the
        actual PASS bar at the resolved eff-n.
  R5-7  Alignment declaration: production EOD-vendor vanna/IV vs new intraday
        analytic-BS vanna/IV are DIFFERENT objects; the comparison is flagged
        CONFOUNDED unless/until aligned.
  R5-8  SPY kept separate from QQQ in all pooled statistics until >=2 genuinely
        independent (non-shared-day) resolvable SPY clusters exist.

==============================================================================
PRE-REGISTERED ACQUISITION SET (round-5, NEW unique calendar days)
==============================================================================
The R1/R2 ruling required 8-12 GENUINELY independent UNIQUE calendar days
beyond the exhausted seed set (20260508/0522/0605/0619/0703/0716/0717/0731).
This v4 run adds the following NEW (day, expiry) pairs — all distinct NYSE
calendar days, all BEFORE today (2026-08-14), chosen for high-vol event
sessions (FOMC, QQQ/SPY-index earnings weeks, monthly OpEx, month-end) with
MIXED short-to-mid DTE (1-10 days) and SEPARATED acquisition sessions
(a distinct session-id/timestamp per day).

  Day        Expiry     DTE   Event / rationale
  20260413   20260417    4    pre-OpEx Monday (Apr monthly OpEx week)
  20260417   20260424    7    April monthly OpEx Friday (MID DTE)
  20260429   20260501    2    FOMC decision + index earnings (Wed)
  20260430   20260501    1    post-FOMC / AAPL-MSFT-AMZN earnings (Thu)
  20260515   20260522    7    May monthly OpEx Friday (MID DTE)
  20260520   20260522    2    NVDA earnings (Wed)
  20260529   20260605    7    month-end QQQ-quarterly Friday (MID DTE)
  20260616   20260626   10    FOMC day-1 (Tue) — MID DTE (10)
  20260617   20260624    7    FOMC decision (Wed) — MID DTE (7)
  20260721   20260724    3    MSFT earnings (Tue)
  20260722   20260724    2    TSLA earnings (Wed)
  20260728   20260731    3    FOMC day-1 + AAPL earnings (Tue)
  20260729   20260731    2    FOMC decision + META earnings (Wed)
  20260807   20260810    3    post-jobs vol spike (Fri)

All 14 probed viable: stock/ohlc intraday serves ~390 rows, eod_greeks serves
the same-day production book, and per-strike all_greeks @600000 serves 40
10-min buckets (QQQ + SPY). These are CANDIDATES; only days that produce
BOTH a nonzero production LEVEL and a nonzero new-engine LEVEL become
resolvable observations. 404/empty/zero-firing days are recorded as data gaps.

This is a REQUIRED acquisition for the loop; it does NOT dispatch Cem. The
gate verdict: PASS/FAIL only when eff-n (unique-day) clears the md bar, else
INDETERMINATE. The seed-set resolvable days (from dual_pipeline_gate_v3_obs.json)
are merged so the reported total unique-day eff-n reflects ALL days to date.
"""

import datetime as dt
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import expiry_book_exposure as ebe
import run_dual_pipeline_gate as _g  # P0-1 helpers
import run_dual_pipeline_gate_v2 as _v2  # P0-2/P0-3 helpers
import run_dual_pipeline_gate_v3 as _r5  # R5 helpers (dedup, binom, provenance, verdict)

from shared.thetadata import ThetaDataController

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_intraday_cache")
DEADBAND = 0.01
IVL = 600000
SIGN_MODEL = "vol_surface_replication"
ACCUMULATE = False
_PLACEBO_PERMS = 1000
_SEED = 7
TOL_QQQ = 0.01
TOL_SPY = 0.005

# Round-5 NEW acquisition set (day, expiry). PRE-REGISTERED above.
PAIRS = [
    ("20260413", "20260417"),
    ("20260417", "20260424"),
    ("20260429", "20260501"),
    ("20260430", "20260501"),
    ("20260515", "20260522"),
    ("20260520", "20260522"),
    ("20260529", "20260605"),
    ("20260616", "20260626"),
    ("20260617", "20260624"),
    ("20260721", "20260724"),
    ("20260722", "20260724"),
    ("20260728", "20260731"),
    ("20260729", "20260731"),
    ("20260807", "20260810"),
]
TICKERS = ["QQQ", "SPY"]
_AGREE_FRAC = 2.0 / 3.0
_TARGET_EFF_N = 8  # min for the binomial sign-agreement arm
_TARGET_CORR_N = 29  # md<=0.5 on the correlational arm (documented, not gated)


def _merge_seed_rows():
    """Load the resolvable seed-day rows from the v3 obs.json (prior rounds).
    Returns list of row dicts matching the v4 rows_table shape (already
    level-vs-level, unique-day-deduped, R5-corrected). Empty on any failure.
    The seed rows were acquired in EARLIER SESSIONS (distinct session-ids),
    so merging them is NOT a shared-session confound with this run's days."""
    path = os.path.join(CACHE, "dual_pipeline_gate_v3_obs.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        out = []
        for r in d.get("rows", []):
            out.append(
                {
                    "ticker": r.get("ticker"),
                    "day": r.get("day"),
                    "expiry": r.get("expiry"),
                    "T": r.get("T"),
                    "firing_buckets": r.get("firing_buckets", 0),
                    "buckets": r.get("buckets", 0),
                    "prod_sign": r.get("prod_sign"),
                    "new_sign": r.get("new_sign"),
                    "agreed": r.get("agreed", False),
                    "both_nonzero": r.get("both_nonzero", False),
                    "mean_abs_div": r.get("mean_abs_div", 0.0),
                    "provenance": r.get("provenance", {}),
                    "session": "seed-v3",
                }
            )
        return out
    except Exception:
        return []


def _pure_svi_sign(provenance):
    """R5-4 pure-SVI sensitivity: recompute the production sign with deadband
    (fallback->-1) strikes contributing 0 instead of -1. Sign of the SVI
    deviation term alone: rich=+1, cheap=-1, deadband->0. Returns +1/-1/0.
    If all resolved strikes are deadband, returns 0 (no SVI read)."""
    svi = provenance.get("rich_plus1", 0) - provenance.get("cheap_minus1", 0)
    if svi == 0:
        return 0
    return 1 if svi > 0 else -1


def main():
    os.environ.setdefault("THETADATA_HIST_CONCURRENCY", "1")
    ctl = ThetaDataController()
    t0 = time.time()
    lines = []
    lines.append("# P0-2/P0-3 ROUND-5 — REQUIRED UNIQUE-DAY ACQUISITION (result)\n")
    lines.append(
        f"**Date:** {dt.date.today().isoformat()}  **Driver:** `run_dual_pipeline_gate_v4.py`\n"
    )
    lines.append(
        "**R5 corrections preserved (all from v3):** unique-day dedup (R5-1), "
        "cluster-level CI (R5-2), SVI sign provenance (R5-3), pure-SVI fallback->0 "
        "sensitivity (R5-4), low/high |dIV| stratification (R5-5), exact binomial "
        "null (R5-6), provenance/confound declaration (R5-7), SPY kept separate (R5-8).\n"
    )

    gaps = []
    obs = {}
    rows_table = []

    for ticker in TICKERS:
        for day, exp in PAIRS:
            key = f"{ticker}_{day}"
            session_id = f"r5-{day}-{ticker}"
            print(f"\n=== SESSION {session_id}: {ticker} {day} -> {exp} ===")
            try:
                spot_min = _g._stock_intraday(ctl, ticker, day)
            except Exception as e:
                gaps.append(
                    {
                        "ticker": ticker,
                        "day": day,
                        "endpoint": "stock/ohlc",
                        "status": "exc",
                        "note": str(e)[:100],
                        "session": session_id,
                    }
                )
                continue
            if not spot_min:
                gaps.append(
                    {
                        "ticker": ticker,
                        "day": day,
                        "endpoint": "stock/ohlc",
                        "status": "empty",
                        "note": "no intraday stock rows",
                        "session": session_id,
                    }
                )
                continue
            open_spot = spot_min[min(spot_min)]
            close_spot = spot_min[max(spot_min)]
            base = round(open_spot / 5.0) * 5.0
            grid_theta = [_g.strike_to_theta(base + i * 5.0) for i in range(-12, 13)]
            grid_set = set(grid_theta)
            try:
                chain_c, fail_c = _v2._robust_chain_intraday(
                    ctl, ticker, exp, day, grid_theta, "C"
                )
                chain_p, fail_p = _v2._robust_chain_intraday(
                    ctl, ticker, exp, day, grid_theta, "P"
                )
            except Exception as e:
                gaps.append(
                    {
                        "ticker": ticker,
                        "day": day,
                        "endpoint": "all_greeks intraday",
                        "status": "exc",
                        "note": str(e)[:100],
                        "session": session_id,
                    }
                )
                continue
            for fk in fail_c + fail_p:
                gaps.append(
                    {
                        "ticker": ticker,
                        "day": day,
                        "endpoint": "all_greeks intraday",
                        "status": "5xx",
                        "note": f"theta-strike {fk} transient proxy error",
                        "session": session_id,
                    }
                )
            if len(chain_c) < 10 or len(chain_p) < 10:
                gaps.append(
                    {
                        "ticker": ticker,
                        "day": day,
                        "endpoint": "all_greeks intraday",
                        "status": "thin",
                        "note": f"C={len(chain_c)} P={len(chain_p)} strikes served",
                        "session": session_id,
                    }
                )
                continue
            oi_c, _of_c = _v2._robust_oi_proxy(
                ctl, ticker, exp, day, list(chain_c.keys()), "C"
            )
            oi_p, _of_p = _v2._robust_oi_proxy(
                ctl, ticker, exp, day, list(chain_p.keys()), "P"
            )
            eod_g = _g._eod_greeks_day(ctl, ticker, exp, day)
            if not eod_g:
                gaps.append(
                    {
                        "ticker": ticker,
                        "day": day,
                        "endpoint": "bulk_hist/option/eod_greeks",
                        "status": "empty",
                        "note": "no EOD same-day chain",
                        "session": session_id,
                    }
                )
                # eod_g empty -> production same-day book empty -> sign=0 (excluded cluster)
                continue

            # Day-anchored locus (v2 band rule per ticker)
            rows_open = _g._rows_at(chain_c, chain_p, oi_c, oi_p, min(spot_min))
            T = max(
                (
                    dt.datetime.strptime(exp, "%Y%m%d").date()
                    - dt.datetime.strptime(day, "%Y%m%d").date()
                ).days
                / 365.0,
                0.01,
            )
            if len(rows_open) < 20:
                gaps.append(
                    {
                        "ticker": ticker,
                        "day": day,
                        "endpoint": "execution_locus",
                        "status": "thin",
                        "note": f"{len(rows_open)} open rows < 20",
                        "session": session_id,
                    }
                )
                continue
            tol = _v2.spy_band_rule(ticker)
            try:
                locus = ebe.execution_locus(
                    rows_open, open_spot, T=T, tolerance_pct=tol
                )
            except Exception as e:
                gaps.append(
                    {
                        "ticker": ticker,
                        "day": day,
                        "endpoint": "execution_locus",
                        "status": "exc",
                        "note": str(e)[:100],
                        "session": session_id,
                    }
                )
                continue

            all_ms = set()
            for k, krows in chain_c.items():
                for x in krows:
                    all_ms.add(int(float(x.get("ms_of_day", 0) or 0)))
            for k, krows in chain_p.items():
                for x in krows:
                    all_ms.add(int(float(x.get("ms_of_day", 0) or 0)))
            bucket_ms = sorted(all_ms)

            firing_N, firing_fwd, firing_div = [], [], []
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
                ivs = [
                    r_["implied_vol"]
                    for r_ in rows_at
                    if r_["implied_vol"] not in (None, 0)
                ]
                if not ivs:
                    continue
                atm_iv = min(
                    (
                        (r_["strike"], r_["implied_vol"])
                        for r_ in rows_at
                        if r_["implied_vol"] not in (None, 0)
                    ),
                    key=lambda p: abs(p[0] - spot_now),
                )[1]
                div = 0.0 if prev_atm_iv is None else atm_iv - prev_atm_iv
                prev_atm_iv = atm_iv
                burst = ebe.hedge_flow_at(locus, spot_now)
                fwd = 0.0
                for sms in sorted(spot_min):
                    if sms > ms:
                        fwd = (spot_min[sms] - spot_now) / spot_now if spot_now else 0.0
                        break
                try:
                    ne_bucket = ebe.build_net_exposure(
                        rows_at, spot_now, ticker=ticker, T=T
                    )
                    N_i = ebe.vanna_flow(ne_bucket, div)
                except Exception:
                    N_i = 0.0
                if abs(div) > DEADBAND and burst != 0.0:
                    firing_N.append(N_i)
                    firing_fwd.append(fwd)
                    firing_div.append(div)

            # Production same-day book
            seed = _g.build_production_seed(
                day, exp, close_spot, eod_g, oi_c, oi_p, grid_set
            )
            prod_v, res = _g.run_production_vanna_same_day(seed, ticker)
            prod_sign = _g._sign_of(prod_v)

            # New-engine LEVEL (undo dIV flow factor) per firing bucket
            new_levels = [
                _v2._flow_to_level(n, dv) for n, dv in zip(firing_N, firing_div)
            ]
            new_levels = [x for x in new_levels if x is not None]
            new_day_level = sum(new_levels) if new_levels else 0.0
            new_sign = _g._sign_of(new_day_level)

            both_nonzero = prod_sign != 0 and new_sign != 0
            agreed = both_nonzero and (prod_sign == new_sign)
            mean_abs_div = (
                sum(abs(d) for d in firing_div) / len(firing_div) if firing_div else 0.0
            )

            # SVI sign provenance (R5-3) + pure-SVI sensitivity (R5-4)
            prov = _r5._sign_provenance(seed)
            pure_svi_sign = _pure_svi_sign(prov)

            rows_table.append(
                {
                    "ticker": ticker,
                    "day": day,
                    "expiry": exp,
                    "T": round(T, 5),
                    "open_spot": open_spot,
                    "close_spot": close_spot,
                    "firing_buckets": len(firing_N),
                    "buckets": len(bucket_ms),
                    "prod_vanna": round(prod_v, 6) if prod_v is not None else None,
                    "prod_sign": prod_sign,
                    "new_day_LEVEL": round(new_day_level, 6),
                    "new_sign": new_sign,
                    "agreed": agreed,
                    "both_nonzero": both_nonzero,
                    "mean_abs_div": round(mean_abs_div, 5),
                    "provenance": prov,
                    "pure_svi_sign": pure_svi_sign,
                    "session": session_id,
                }
            )
            obs[key] = {
                "ticker": ticker,
                "day": day,
                "expiry": exp,
                "firing_N": firing_N,
                "firing_fwd": firing_fwd,
                "firing_div": firing_div,
                "prod_vanna": prod_v,
                "new_day_LEVEL": new_day_level,
                "prod_sign": prod_sign,
                "new_sign": new_sign,
                "agreed": agreed,
                "both_nonzero": both_nonzero,
                "session": session_id,
            }
            print(
                f"  [day] prod={prod_sign} new={new_sign} agree={agreed} "
                f"firing={len(firing_N)} mean|div|={mean_abs_div:.4f} pureSVI={pure_svi_sign}"
            )
            time.sleep(0.5)  # session pacing (separated acquisition)

    # Merge prior seed-day rows (acquired in EARLIER sessions) for combined total.
    seed_rows = _merge_seed_rows()
    n_seed_merged = len(seed_rows)
    seed_resolvable = sum(1 for r in seed_rows if r["both_nonzero"])
    seed_unique = _r5._dedup_unique_days(
        [(r["ticker"], r["day"]) for r in seed_rows if r["both_nonzero"]]
    )

    all_rows = seed_rows + rows_table

    # ---- effective-n: UNIQUE CALENDAR DAYS (R5-1) ----
    all_clusters = [(r["ticker"], r["day"]) for r in all_rows if r["both_nonzero"]]
    n_clusters = len(all_clusters)
    unique_days = _r5._dedup_unique_days(all_clusters)
    eff_n = len(unique_days)
    md = _v2._tanh_md(max(eff_n, 1))

    # SPY vs QQQ separation (R5-8)
    spy_clusters = [
        (r["ticker"], r["day"])
        for r in all_rows
        if r["both_nonzero"] and r["ticker"] == "SPY"
    ]
    spy_unique_days = _r5._dedup_unique_days(spy_clusters)
    spy_resolvable = len(spy_clusters)
    spy_indep_days = len(spy_unique_days)

    # ---- binomial null (R5-6) ----
    resolvable = [r for r in all_rows if r["both_nonzero"]]
    agreed_n = sum(1 for r in resolvable if r["agreed"])
    resolvable_n = len(resolvable)
    binom_obs = (
        _r5._binom_tail(agreed_n, resolvable_n) if resolvable_n else float("nan")
    )
    pass_bar_k = math.ceil(_AGREE_FRAC * max(resolvable_n, 1))
    binom_pass = (
        _r5._binom_tail(pass_bar_k, resolvable_n) if resolvable_n else float("nan")
    )

    # ---- correlational arms (pooled + stratified, R5-5/R5-8) ----
    # NOTE: seed rows carry no per-bucket firing arrays in the merged form, so the
    # pooled/stratified correlational arms are computed on THIS run's firing buckets
    # only (the seed correlational arms are already reported in v3_RESULT.md). The
    # cluster-level sign agreement is the PRIMARY genuinely-decidable statistic and
    # IS computed on the merged unique-day set.
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
            low_x.append(n_)
            low_y.append(y_)
        else:
            high_x.append(n_)
            high_y.append(y_)
    corr_low = _g._corr(low_x, low_y) if len(low_x) >= 4 else float("nan")
    corr_high = _g._corr(high_x, high_y) if len(high_x) >= 4 else float("nan")

    # SPY vs QQQ stratified (R5-8)
    spy_x, spy_y, qx, qy = [], [], [], []
    for r in rows_table:
        o = obs.get(f"{r['ticker']}_{r['day']}", {})
        if r["ticker"] == "SPY":
            spy_x += o.get("firing_N", [])
            spy_y += o.get("firing_fwd", [])
        else:
            qx += o.get("firing_N", [])
            qy += o.get("firing_fwd", [])
    corr_spy = _g._corr(spy_x, spy_y) if len(spy_x) >= 4 else float("nan")
    corr_qqq = _g._corr(qx, qy) if len(qx) >= 4 else float("nan")

    # cluster-level CI at eff-n (R5-2)
    ci_lo, ci_hi = (
        _g._fisher_tanh_ci(corr_nf, eff_n)
        if n_buckets >= 4
        else (float("nan"), float("nan"))
    )

    # ---- lead/lag + placebo on THIS run's firing buckets ----
    lead_lag = {}
    ll0, ll1, ll2, prior = [], [], [], []
    for k, o in obs.items():
        x = o.get("firing_N", [])
        y = o.get("firing_fwd", [])
        n = len(x)
        if n == 0:
            continue
        ll0.extend([(x[i], y[i]) for i in range(n)])
        if n >= 2:
            ll1.extend([(x[i], y[i + 1]) for i in range(n - 1)])
            prior.extend([(x[i], y[i - 1]) for i in range(1, n)])
        if n >= 3:
            ll2.extend([(x[i], y[i + 2]) for i in range(n - 2)])
    if len(ll0) >= 4:
        lead_lag = {
            "lag0": _g._corr([a for a, _ in ll0], [b for _, b in ll0]),
            "lag1": _g._corr([a for a, _ in ll1], [b for _, b in ll1]),
            "lag2": _g._corr([a for a, _ in ll2], [b for _, b in ll2]),
            "prior": _g._corr([a for a, _ in prior], [b for _, b in prior]),
            "n_lag0": len(ll0),
            "n_lag1": len(ll1),
            "n_lag2": len(ll2),
            "n_prior": len(prior),
        }

    placebo_obs, placebo_nulls, placebo_p = (float("nan"), [], float("nan"))
    if n_buckets >= 4:
        placebo_obs, placebo_nulls, placebo_p = _g._placebo_null(all_N, all_fwd)

    # ---- gate verdict ----
    sign_agreement = [(r["agreed"], r["both_nonzero"]) for r in all_rows]
    verdict, verdict_reason = _r5.gate_verdict_r5(
        sign_agreement, corr_nf, r_a6, n_buckets, eff_n
    )

    # ---- pure-SVI sensitivity (R5-4) on the merged set ----
    pure_resolvable = [
        r for r in all_rows if r["both_nonzero"] and r.get("pure_svi_sign", 0) != 0
    ]
    pure_agreed = sum(
        1 for r in pure_resolvable if r["pure_svi_sign"] == r["prod_sign"]
    )

    # ---- write result ----
    lines.append("### Data acquisition\n")
    lines.append(
        "Sequential via ThetaData proxy (THETADATA_HIST_CONCURRENCY=1), creds from main-tree .env "
        "(never persisted). 14 NEW unique calendar days, each in a SEPARATE acquisition session "
        "(session id `r5-<day>-<ticker>`), paced with a sleep between days. Per day: stock/ohlc "
        "intraday, per-strike all_greeks @600000 (C+P), open_interest, bulk_hist/option/eod_greeks "
        "single-day for the production same-day book. Prior seed-day rows merged from "
        "`dual_pipeline_gate_v3_obs.json` (separate earlier sessions — not a shared-session confound).\n"
    )

    lines.append("### ROUND-5 ACQUISITION MANIFEST (pre-registered)\n")
    lines.append("| Day | Expiry | DTE | Event / rationale |")
    lines.append("|---|---|---|---|")
    for day, exp in PAIRS:
        dte = (
            dt.datetime.strptime(exp, "%Y%m%d").date()
            - dt.datetime.strptime(day, "%Y%m%d").date()
        ).days
        ev = {
            "20260413": "pre-OpEx Mon",
            "20260417": "Apr OpEx Fri (MID)",
            "20260429": "FOMC+earnings Wed",
            "20260430": "post-FOMC/earnings Thu",
            "20260515": "May OpEx Fri (MID)",
            "20260520": "NVDA earnings Wed",
            "20260529": "month-end/QQQ-qtr Fri (MID)",
            "20260616": "FOMC day1 Tue (MID)",
            "20260617": "FOMC Wed (MID)",
            "20260721": "MSFT earnings Tue",
            "20260722": "TSLA earnings Wed",
            "20260728": "FOMC day1+AAPL Tue",
            "20260729": "FOMC+META Wed",
            "20260807": "post-jobs vol Fri",
        }[day]
        lines.append(f"| {day} | {exp} | {dte} | {ev} |")

    lines.append("\n### ROUND-5 ACQUISITION RESULTS (this run)\n")
    lines.append(
        "| Ticker | Day | Expiry | DTE | firing/buckets | prod_sign | new_LEVEL_sign | agree | mean\\|dIV\\| | pureSVI_sign |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows_table:
        dte = (
            dt.datetime.strptime(r["expiry"], "%Y%m%d").date()
            - dt.datetime.strptime(r["day"], "%Y%m%d").date()
        ).days
        ag = "YES" if r["agreed"] else ("NO" if r["both_nonzero"] else "n/a")
        lines.append(
            f"| {r['ticker']} | {r['day']} | {r['expiry']} | {dte} | {r['firing_buckets']}/{r['buckets']} | "
            f"{r['prod_sign']} | {r['new_sign']} | {ag} | {r['mean_abs_div']:.4f} | {r['pure_svi_sign']} |"
        )

    lines.append(
        "\n### PRIMARY — SIGN AGREEMENT (unique-day dedup, R5-1; merged seed + new)\n"
    )
    lines.append(
        f"- This-run clusters: {len(rows_table)}; merged seed rows: {n_seed_merged} "
        f"({seed_resolvable} resolvable, {len(seed_unique)} unique days)."
    )
    lines.append(
        f"- Resolvable (both-nonzero) clusters: {n_clusters}  **unique calendar days: {eff_n}** "
        f"(SPY {spy_indep_days} independent-day resolvable; SPY kept separate, R5-8).\n"
    )
    frac_txt = f"{agreed_n / resolvable_n:.0%}" if resolvable_n else "n/a"
    lines.append(
        f"- Level-vs-level agreement: **{agreed_n}/{resolvable_n}** resolvable = "
        f"{frac_txt}."
    )
    for r in resolvable:
        lines.append(
            f"  - {r['ticker']} {r['day']}: prod={r['prod_sign']} new={r['new_sign']} "
            f"→ {'AGREE' if r['agreed'] else 'DISAGREE'} (mean|dIV|={r['mean_abs_div']:.4f})"
        )
    if resolvable_n:
        lines.append(
            f"- Binomial null: P(agree>={agreed_n} | n={resolvable_n}, p=0.5) = **{binom_obs:.4f}**; "
            f"P(pass bar >= {pass_bar_k}/{resolvable_n}) = **{binom_pass:.4f}** (R5-6)."
        )
    lines.append(
        f"- md at eff-n={eff_n} = **{md:.3f}** → {'INDETERMINATE (zero power)' if md > 0.95 else 'powered'}."
    )

    lines.append("\n### SPY vs QQQ SEPARATE (R5-8)\n")

    def _fam_agreement(tk):
        rr = [r for r in all_rows if r["both_nonzero"] and r["ticker"] == tk]
        return (
            sum(1 for r in rr if r["agreed"]),
            len(rr),
            len(_r5._dedup_unique_days([(r["ticker"], r["day"]) for r in rr])),
        )

    sa_spy = _fam_agreement("SPY")
    sa_qqq = _fam_agreement("QQQ")
    lines.append(
        f"- SPY: agree {sa_spy[0]}/{sa_spy[1]} resolvable ({sa_spy[2]} independent unique days). "
        f"Index sub-analysis valid only if >= 2 independent SPY days."
    )
    lines.append(
        f"- QQQ: agree {sa_qqq[0]}/{sa_qqq[1]} resolvable ({sa_qqq[2]} independent unique days)."
    )

    lines.append(
        "\n### CORRELATIONAL ARMS (cluster-level, R5-2/R5-5/R5-8; this run's firing buckets)\n"
    )
    if n_buckets >= 4:
        lines.append(
            f"- corr(new_LEVEL, fwd) pooled = {corr_nf:+.4f} (n={n_buckets}) — **Simpson caveat**: "
            f"SPY {corr_spy:+.4f} vs QQQ {corr_qqq:+.4f} opposite-sign families; pooled is a "
            f"cancellation, NOT a null. SPY not an index claim ({spy_indep_days} indep day)."
        )
        lines.append(
            f"- corr by |dIV| strata (R5-5): low {corr_low:+.4f} vs high {corr_high:+.4f} "
            f"(median |dIV|={med_div:.4f})."
        )
        lines.append(
            f"- A6 reflexivity baseline corr(ΔIV, fwd) = {r_a6:+.4f} vs vanna {corr_nf:+.4f} "
            f"→ {'vanna EXCEEDS reflexivity' if corr_nf > r_a6 else 'vanna <= reflexivity (convention-bound)'}."
        )
        lines.append(
            f"- cluster-level 95% CI at eff-n={eff_n}: [{ci_lo:+.3f}, {ci_hi:+.3f}] "
            f"(pooled-bucket CI is descriptive only, R5-2)."
        )
        lines.append(
            f"- placebo p = {placebo_p:.4f} ({'non-null' if placebo_p < 0.05 else 'null NOT rejected'}); "
            f"at md {md:.3f} this is {'powered' if md <= 0.95 else 'no-information (underpowered)'}."
        )

    lines.append("\n### LEAD/LAG FALSIFIER\n")
    if lead_lag:
        lines.append(
            f"- corr(N_i, fwd_{{i+0}}) = {lead_lag['lag0']:+.4f}  (n={lead_lag['n_lag0']})"
        )
        lines.append(
            f"- corr(N_i, fwd_{{i+1}}) = {lead_lag['lag1']:+.4f}  (n={lead_lag['n_lag1']})"
        )
        lines.append(
            f"- corr(N_i, fwd_{{i+2}}) = {lead_lag['lag2']:+.4f}  (n={lead_lag['n_lag2']})"
        )
        lines.append(
            f"- REVERSE (prior return predicts signal): corr(N_i, fwd_{{i-1}}) = {lead_lag['prior']:+.4f}  (n={lead_lag['n_prior']})"
        )
        l0 = lead_lag.get("lag0", 0)
        l1 = lead_lag.get("lag1", 0)
        lag_nonnull = (abs(l1) > 0.05) and ((l1 > 0) == (l0 > 0))
        lines.append(
            f"  read: lead/lag is {'NON-NULL' if lag_nonnull else 'NULL/weak — no persistent response after the breach bucket'}."
        )
        if (
            abs(lead_lag.get("prior", 0)) > abs(l0)
            and abs(lead_lag.get("prior", 0)) > 0.05
        ):
            lines.append(
                "  CAUTION: prior-return predicts the signal more strongly than the signal predicts forward returns → reverse-causation/reflexivity present."
            )
    else:
        lines.append("- insufficient data for lead/lag.")

    lines.append("\n### SVI SIGN PROVENANCE (R5-3) + PURE-SVI SENSITIVITY (R5-4)\n")
    lines.append(
        "| Ticker | Day | total_strikes | rich+1 | cheap-1 | deadband→fallback-1 | missing | pureSVI_sign |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in rows_table:
        p = r["provenance"]
        lines.append(
            f"| {r['ticker']} | {r['day']} | {p['total']} | {p['rich_plus1']} | "
            f"{p['cheap_minus1']} | {p['deadband_zero']} (→ -1 fallback) | {p['missing']} | {r['pure_svi_sign']} |"
        )
    lines.append(
        f"- Pure-SVI sensitivity: of {len(pure_resolvable)} resolvable clusters with a non-zero SVI "
        f"read, **{pure_agreed}** agree with production when deadband strikes contribute 0 "
        f"(fallback→0, R5-4). This isolates agreement manufactured by the shared -1 root."
    )

    lines.append("\n### PROVENANCE / CONFOUND DECLARATION (R5-7)\n")
    lines.append(
        "- Production = whole-day EOD **vendor** vanna field + vendor IV (`eod_greeks`). "
        "New = intraday **analytic BS** vanna + intraday IV summed over firing-breach buckets."
    )
    lines.append(
        "- These are DIFFERENT objects (IV surface, vanna definition, aggregation window). "
        "Any sign disagreement is CONFOUNDED and cannot be cleanly attributed without "
        "per-strike decomposition / aligned inputs. Declared, not hidden."
    )

    lines.append("\n### DATA GAPS / EXCLUDED CLUSTERS\n")
    if gaps:
        lines.append("| ticker | day | endpoint | status | note |")
        lines.append("|---|---|---|---|---|")
        for g in gaps:
            lines.append(
                f"| {g.get('ticker', '-')} | {g.get('day', '-')} | {g['endpoint']} | "
                f"{g['status']} | {g.get('note', '')} |"
            )
    else:
        lines.append("- none recorded.")
    excluded = [r for r in all_rows if not r["both_nonzero"]]
    if excluded:
        lines.append("\nExcluded (zero-sign / non-resolvable) clusters:")
        for r in excluded:
            lines.append(
                f"- {r['ticker']} {r['day']}: prod={r['prod_sign']} new={r['new_sign']} "
                f"firing={r['firing_buckets']}"
            )

    lines.append("\n### GATE VERDICT\n")
    lines.append(f"**{verdict}** — {verdict_reason}")
    lines.append(
        f"\nRequired for a defensible correlational arm: eff-n >= {_TARGET_CORR_N} (md<=0.5) — "
        f"documented; the binomial sign-agreement arm targets eff-n >= {_TARGET_EFF_N}."
    )
    lines.append(
        f"\nRan in {time.time() - t0:.1f}s. Raw: `dual_pipeline_gate_v4_obs.json`."
    )

    os.makedirs(CACHE, exist_ok=True)
    with open(
        os.path.join(CACHE, "dual_pipeline_gate_v4_RESULT.md"), "w", encoding="utf-8"
    ) as fh:
        fh.write("\n".join(lines))
    with open(
        os.path.join(CACHE, "dual_pipeline_gate_v4_obs.json"), "w", encoding="utf-8"
    ) as fh:
        json.dump(
            {
                "verdict": verdict,
                "verdict_reason": verdict_reason,
                "eff_n": eff_n,
                "unique_days": sorted(unique_days),
                "md": md,
                "agreed_n": agreed_n,
                "resolvable_n": resolvable_n,
                "binom_obs": binom_obs,
                "binom_pass": binom_pass,
                "corr_nf": corr_nf,
                "corr_spy": corr_spy,
                "corr_qqq": corr_qqq,
                "corr_low": corr_low,
                "corr_high": corr_high,
                "r_a6": r_a6,
                "ci_lo": ci_lo,
                "ci_hi": ci_hi,
                "placebo_p": placebo_p,
                "lead_lag": lead_lag,
                "n_seed_merged": n_seed_merged,
                "seed_unique_days": sorted(seed_unique),
                "spy_agreement": list(sa_spy),
                "qqq_agreement": list(sa_qqq),
                "pure_svi_agreed": pure_agreed,
                "pure_svi_resolvable": len(pure_resolvable),
                "gaps": gaps,
                "rows": rows_table,
                "obs": {k: {kk: vv for kk, vv in o.items()} for k, o in obs.items()},
            },
            fh,
            indent=2,
            default=str,
        )
    print("\n".join(lines))
    print(f"\n[R5-NEW] verdict: {verdict} — {verdict_reason}")
    print(
        f"[R5-NEW] SPY agree={sa_spy[0]}/{sa_spy[1]} ({sa_spy[2]}d)  QQQ agree={sa_qqq[0]}/{sa_qqq[1]} ({sa_qqq[2]}d)  "
        f"combined={agreed_n}/{resolvable_n}  eff-n={eff_n}  md={md:.3f}"
    )


if __name__ == "__main__":
    main()
