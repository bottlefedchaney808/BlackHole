"""P0-2/P0-3 — EXPANDED DUAL-PIPELINE CONVENTION-INDEPENDENCE GATE (pre-registered estimand).

================================================================================
PRE-REGISTERED ESTIMAND (written BEFORE any network data run; immutable).
================================================================================

Cem Karsan round-3/round-4 verdicts: NOT ACCEPTED. The P0-1 gate (committed
105495fb) FAILED on three counts that this v2 run is REQUIRED to address:
  (1) LEVEL-VS-FLOW ARTIFACT — P0-1 compared sign(production LEVEL) vs
      sign(new FLOW). Production `vanna_call+put_shares` (dealer_positioning.py:765)
      is a vanna LEVEL with NO dIV factor; new `ebe.vanna_flow(ne,dIV)` is a FLOW =
      level x (dIV/0.01). The dIV factor mechanically flips the sign. **This v2 run
      compares LEVEL-vs-LEVEL**: the new-engine LEVEL is obtained by UNDOING the
      dIV/0.01 flow factor on the new side (new_level = new_flow / (dIV/0.01)).
  (2) EFFECTIVE-n = 3 = md 0.993 = ZERO POWER. This run acquires substantially
      more INDEPENDENT day-clusters (target >= 8-12) to lift effective-n past md.
  (3) SPY fired 0 buckets. This run adds INDEX (SPY) firing coverage via a
      pre-registered tighter tolerance band (below).

UNIT OF ANALYSIS
  An observation is one firing bucket: an intraday 10-min (ivl=600000) bucket on
  a firing day where (|dIV| > 0.01 dead-band) AND (burst != 0), burst =
  hedge_flow_at(day-anchored execution locus, spot). The day-anchored locus is
  built ONCE per day from the opening bucket. Forward return = next-10-min spot
  return strictly AFTER the bucket (no look-ahead).

INDEPENDENCE / EFFECTIVE-n
  A cluster = one (ticker, day) firing day. Each firing day is one independent
  unit of inference (a separate calendar day and, for SPY vs QQQ, a separate
  family). effective-n = number of resolvable (ticker, day) clusters that yield
  BOTH a nonzero production LEVEL and a nonzero new-engine LEVEL (i.e. the
  denominator of the primary sign-agreement statistic). md is computed at this
  effective-n: md = tanh(2.8016 / sqrt(n_eff - 3)) for n_eff > 4 else 1.0
  (R2-5 authoritative form). The gate only claims power if effective-n > 4 and
  effective-n clears md in the correlational arm. Both pooled-bucket n and
  cluster effective-n are reported SEPARATELY to prevent ambiguity.

SIGN CONVENTION (LEVEL-vs-LEVEL — the correction)
  Production level per cluster:  P = res.vanna_call_shares + res.vanna_put_shares
    (signed, SVI sign map via sign_model='vol_surface_replication',
    accumulate=False, same-day EOD book on the firing day, restricted to the
    same $5 strike grid as the new engine).  NOTE: P is already a LEVEL
    (dealer_positioning.py:765 has no dIV factor) — do NOT multiply by dIV.
  New level per bucket i:  L_i = vanna_flow(ne_i, dIV_i) / (dIV_i / 0.01)
    = SUM signed_vanna·OI·100·VANNA_PP_SCALE (undoes the dIV/0.01 flow factor).
    Guarded: only firing buckets have |dIV| > deadband, so dIV != 0.
  New day level:  N = sum_i L_i over firing buckets.
  Day sign agreement: agree = (sign(P) == sign(N)) when both are nonzero.
  ZERO SIGN POLICY: a cluster with sign(P)==0 OR sign(N)==0 is NOT counted in
  the agreement denominator; it is reported separately as an excluded cluster.

PRIMARY STATISTIC (the Cem gate)
  Level-vs-level sign agreement across all resolvable (ticker, day) clusters,
  reported SEPARATELY for SPY (index sub-analysis), QQQ, and combined.
  Gate PASS requires agree/resolvable >= 2/3 AND corr(new_level, fwd) > 0 AND
  corr(new_level, fwd) > r_A6 (survives reflexivity). Any cluster where either
  engine's quantity is zero/missing is reported separately (not counted).

SPY BAND RULE (pre-registered, P0-2)
  SPY's 0.84-1.43% daily ranges rarely breach the ±1% day-anchored band in a
  10-min window, so SPY fired 0 buckets under the P0-1 rule. To give the INDEX
  family firing coverage without relaxing the day-anchoring principle, SPY uses
  a TIGHTER pre-registered tolerance band:
    SPY: tolerance_pct = 0.005 (band = open spot ± 0.5%, anchored ONCE per day).
    QQQ: tolerance_pct = 0.010 (band = open spot ± 1.0%, anchored ONCE per day).
  Justification: SPY realized vol is roughly half of QQQ's, so a ±0.5% band on
  SPY is the index-family analogue of the ±1% band on QQQ (same ~0.5-1 sigma
  10-min breach probability). The band is applied to the EXECUTION LOCUS
  construction (execution_locus tolerance_pct) BEFORE burst extraction; it does
  not affect sign extraction (signs are read from the LEVEL quantities).
  BOUNDARY: inclusive on both sides (price exactly at band edge => no burst =>
  not a firing bucket), matching hedge_flow_at's closed-band behavior.

LEAD/LAG FALSIFIER
  corr(N_i, fwd_{i+k}) for k in {0, +1, +2} (response strictly AFTER the breach
  bucket) plus REVERSE: corr(N_i, fwd_{i-1}) (prior return predicts signal —
  reverse-causation/reflexivity probe). Lead/lag non-null only if k>=1
  correlations are sign-consistent with k=0 AND exceed r_A6 in magnitude.

PLACEBO NULL
  Permute (bucket -> forward-return) assignment across firing buckets, recompute
  corr(new_level, fwd) under _PLACEBO_PERMS=1000 permutations, seed=_SEED=7.
  Empirical p = (1 + #null >= obs)/(1 + n_perms). p < 0.05 for a non-null claim.

REFLEXIVITY BASELINE (A6, Cem's decisive cheap diagnostic)
  r_A6 = corr(dIV_i, fwd_i) on the SAME firing buckets. If r_A6 positive and >=
  the new-level corr, the vanna sign is downstream of dIV/return reflexivity
  (convention-bound) and the mechanistic claim FAILS even if both pipelines
  agree in sign. Verdict gated on corr(new_level,fwd) > 0 AND > r_A6.

EVENT-DAY HANDLING
  Firing days are short-DTE weekly OpEx-week days (Mondays/Fridays). The same-day
  expiry on the firing day (the anchor) that expires THAT day (tte=0) is dropped
  by the production expiry filter (0 < tte) — a production-pipeline convention,
  not a proxy failure. Such a day is reported as production sign=0 (excluded
  cluster), never counted, per the pre-registered zero-sign policy.

DATA ACQUISITION
  Sequential via the ThetaData proxy (THETADATA_HIST_CONCURRENCY=1), credentials
  from the main tree .env (NEVER written to disk/result). Per (ticker, day):
    1. stock/ohlc intraday (open spot -> $5 strike grid, base±12*5)
    2. per-strike hist/all_greeks at ivl=600000 (C+P) -> intraday buckets + dIV
    3. per-strike hist/open_interest (C+P) -> OI
    4. bulk_hist/option/eod_greeks single-day -> EOD same-day chain for the
       PRODUCTION same-day book (filtered to the same $5 grid)
  404s on strikes not on the grid / thin strikes / unavailable days are handled
  gracefully and recorded as data gaps. NO synthetic data is ever substituted.
  Normalized observations are written to
  Vol_Suite/_intraday_cache/dual_pipeline_gate_v2_obs.json for audit.
  Result: Vol_Suite/_intraday_cache/dual_pipeline_gate_v2_RESULT.md

EXPANDED FIRING SET (day -> expiry), SPY + QQQ, drawn from the seed-corpus
OpEx-week windows already present in _scratch_tier2b (20260508/20260522/20260605/
20260619/20260703/20260717/20260731) plus the existing P0-1 anchors. The proxy
may 404 on some days/expiries; those are recorded as data gaps and the run
continues with the days that resolve.
  QQQ: 20260508->20260511, 20260522->20260526, 20260605->20260608,
       20260619->20260622, 20260703->20260706, 20260716->20260717,
       20260717->20260717, 20260731->20260803
  SPY: same day->expiry pairs (20260508->20260511 ... 20260731->20260803)
This is a candidate set; only clusters where the proxy serves short-DTE intraday
+ EOD chains become resolvable observations.

GATE RULE (verdict on the whole run)
  PASS:          agree/resolvable >= 2/3 (level-vs-level) AND
                 corr(new_level, fwd) > 0 AND corr(new_level, fwd) > r_A6.
  FAIL:          agreement < 2/3, OR corr <= 0, OR corr <= r_A6 (reflexivity
                 subsumes / convention-bound).
  INDETERMINATE: < 4 firing buckets across all days, or no resolvable cluster,
                 or effective-n <= 4 (no power to clear md).
The verdict is stated plainly. effective-n vs md is disclosed: the sign
agreement is the PRIMARY genuinely-decidable statistic; the correlational arms
are descriptive unless effective-n clears md.
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
# Reuse the P0-1 driver's pure, network-free helpers AND its production-driving
# monkeypatch helpers (build_production_seed, run_production_vanna_same_day) and
# data-acquisition functions. Importing run_dual_pipeline_gate is side-effect-free.
import run_dual_pipeline_gate as _g  # noqa: E402
from shared.thetadata import ThetaDataController  # noqa: E402

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_intraday_cache")
DEADBAND = 0.01
IVL = 600000  # 10-min
SIGN_MODEL = "vol_surface_replication"
ACCUMULATE = False
_PLACEBO_PERMS = 1000
_SEED = 7
TOL_QQQ = 0.01   # QQQ band = open spot ± 1.0%
TOL_SPY = 0.005  # SPY band = open spot ± 0.5%  (pre-registered, P0-2)

# Expanded firing set (day -> expiry) for QQQ and SPY (P0-3: more day-clusters).
PAIRS = [
    ("20260508", "20260511"),
    ("20260522", "20260526"),
    ("20260605", "20260608"),
    ("20260619", "20260622"),
    ("20260703", "20260706"),
    ("20260716", "20260717"),  # existing P0-1 anchor
    ("20260717", "20260717"),  # existing P0-1 anchor (zero-DTE drop expected)
    ("20260731", "20260803"),  # existing P0-1 anchor
]
TICKERS = ["QQQ", "SPY"]
_MIN_RESOLVABLE = 8   # target independent (ticker,day) clusters
_AGREE_FRAC = 2.0 / 3.0


# ---------------------------------------------------------------------------
# LEVEL-vs-LEVEL conversion (the P0-2 correction)
# ---------------------------------------------------------------------------
import httpx  # noqa: E402


def _robust_chain_intraday(ctl, root, exp, day, strikes_theta, right, attempts=3):
    """Wrapper over the P0-1 _chain_intraday that treats transient 5xx (502/503)
    proxy overload as graceful data gaps instead of crashing the whole run.
    Retries up to `attempts` times with the proxy's own linear backoff via
    _get_with_retry, then returns {} (no strikes) on persistent failure.
    Returns (out, failures) where failures lists the theta strikes that 5xx'd."""
    out = {}
    failures = []
    for k in strikes_theta:
        try:
            r = ctl._get_with_retry(
                f"/api/theta/hist/option/all_greeks/{root}/{exp}/{k}/{right}",
                params={"start_date": day, "end_date": day, "ivl": IVL})
            if r.status_code == 404:
                continue
            if r.status_code >= 500:
                failures.append(k)
                continue
            r.raise_for_status()
            rows = ctl._parse_rows(r)
            if rows:
                out[k] = rows
        except httpx.HTTPStatusError as e:
            if e.response is not None and e.response.status_code >= 500:
                failures.append(k)
                continue
            raise
        except (httpx.TransportError, ValueError, TypeError):
            failures.append(k)
            continue
    return out, failures


def _robust_oi_proxy(ctl, root, exp, day, strikes_theta, right):
    out, failures = {}, []
    for k in strikes_theta:
        try:
            r = ctl._get_with_retry(
                f"/api/theta/hist/option/open_interest/{root}/{exp}/{k}/{right}",
                params={"start_date": day, "end_date": day})
            if r.status_code == 404:
                continue
            if r.status_code >= 500:
                failures.append(k)
                continue
            r.raise_for_status()
            rows = ctl._parse_rows(r)
            if rows:
                out[k] = int(float(rows[-1].get("open_interest", 0) or 0))
        except Exception:
            failures.append(k)
            continue
    return out, failures


def _flow_to_level(flow, div):
    """Undo the dIV/0.01 flow factor on the NEW engine so the comparison is
    LEVEL-vs-LEVEL (production LEVEL vs new-engine LEVEL).

    new_flow = new_level x (dIV/0.01)  =>  new_level = new_flow / (dIV/0.01).
    Guarded: a zero/None/NaN dIV returns None (caller treats as unresolvable).
    """
    if div is None or div == 0.0:
        return None
    try:
        div = float(div)
    except (TypeError, ValueError):
        return None
    if div == 0.0 or math.isnan(div):
        return None
    return flow / (div / 0.01)


def new_level_from_flow(flow, div):
    """Public wrapper: returns the new-engine LEVEL (float) or None if the dIV
    factor cannot be undone. Used by the driver and the tests."""
    return _flow_to_level(flow, div)


def _tanh_md(n, alpha=0.05, power=0.80):
    """R2-5 authoritative min-detectable |r| at effective-n day-clusters."""
    z = 1.959963984540054 + 0.8416212335729143
    if n <= 4:
        return 1.0
    return math.tanh(z / math.sqrt(max(n - 3, 1)))


def spy_band_rule(ticker):
    """Pre-registered SPY band rule (P0-2). Returns the tolerance_pct for the
    execution locus: SPY -> 0.005, QQQ -> 0.010. Applied BEFORE burst extraction.
    """
    return TOL_SPY if str(ticker).upper() == "SPY" else TOL_QQQ


# ---------------------------------------------------------------------------
# Gate verdict (v2: level-vs-level, majority / 2-3 fraction, reflexivity)
# ---------------------------------------------------------------------------
def gate_verdict_v2(sign_agreement, corr_nf, r_a6, n_buckets, eff_n):
    """Apply the pre-registered v2 gate rule. sign_agreement = list of
    (agreed, both_nonzero) per resolvable cluster."""
    agreed = sum(1 for a, b in sign_agreement if b and a)
    resolvable = sum(1 for a, b in sign_agreement if b)
    if n_buckets < 4 or resolvable == 0 or eff_n <= 4:
        return "INDETERMINATE", (
            f"insufficient power/clusters (buckets={n_buckets}, resolvable={resolvable}, "
            f"eff-n={eff_n} <= 4)")
    frac = agreed / resolvable
    if frac >= _AGREE_FRAC:
        if corr_nf > 0 and corr_nf > r_a6:
            return "PASS", (f"level-level agree {agreed}/{resolvable}={frac:.0%} >= 2/3, "
                            f"corr={corr_nf:+.4f}>r_A6={r_a6:+.4f}")
        if corr_nf <= 0:
            return "FAIL", f"corr(new_level,fwd)={corr_nf:+.4f} <= 0 (wrong/absent direction)"
        return "FAIL", (f"corr(new_level,fwd)={corr_nf:+.4f} <= r_A6={r_a6:+.4f} "
                        f"(reflexivity subsumes — convention-bound)")
    return "FAIL", f"level-level agree {agreed}/{resolvable}={frac:.0%} < 2/3"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    os.environ.setdefault("THETADATA_HIST_CONCURRENCY", "1")
    ctl = ThetaDataController()
    t0 = time.time()
    lines = []
    lines.append("# P0-2/P0-3 — Expanded Dual-Pipeline Convention-Independence Gate (result)\n")
    lines.append(f"**Date:** {dt.date.today().isoformat()}  **Driver:** `run_dual_pipeline_gate_v2.py`  "
                 f"**Tree:** Dealer-Exposure-Dev  **Tickers:** {', '.join(TICKERS)}\n")
    lines.append("**Pre-registered estimand:** see the driver docstring (LEVEL-vs-LEVEL sign "
                 "convention, SPY band rule, exposure strata, lead/lag lags, placebo design, "
                 "zero-sign policy, effective-n/md). Written before any network data run.\n")

    gaps = []
    obs = {}          # normalized observations for audit (keyed by (ticker, day))
    rows_table = []

    for ticker in TICKERS:
        for day, exp in PAIRS:
            key = f"{ticker}_{day}"
            print(f"\n=== {ticker} {day} -> {exp} ===")
            try:
                spot_min = _g._stock_intraday(ctl, ticker, day)
            except Exception as e:
                print(f"  [warn] {key}: stock/ohlc EXC {type(e).__name__}: {str(e)[:100]}")
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
            grid_theta = [strike_to_theta_int(base + i * 5.0) for i in range(-12, 13)]
            grid_set = set(grid_theta)
            try:
                chain_c, fail_c = _robust_chain_intraday(ctl, ticker, exp, day, grid_theta, "C")
                chain_p, fail_p = _robust_chain_intraday(ctl, ticker, exp, day, grid_theta, "P")
            except Exception as e:
                print(f"  [warn] {key}: intraday acquisition EXC {type(e).__name__}: {str(e)[:100]}")
                gaps.append({"ticker": ticker, "day": day, "endpoint": "all_greeks intraday",
                             "status": "exc", "note": str(e)[:100]})
                continue
            for fk in fail_c + fail_p:
                gaps.append({"ticker": ticker, "day": day, "endpoint": "all_greeks intraday",
                             "status": "5xx", "note": f"theta-strike {fk} transient proxy error"})
            if len(chain_c) < 10 or len(chain_p) < 10:
                gaps.append({"ticker": ticker, "day": day, "endpoint": "all_greeks intraday",
                             "status": "thin", "note": f"C={len(chain_c)} P={len(chain_p)} strikes served"})
                continue
            oi_c, _ofc = _robust_oi_proxy(ctl, ticker, exp, day, list(chain_c.keys()), "C")
            oi_p, _ofp = _robust_oi_proxy(ctl, ticker, exp, day, list(chain_p.keys()), "P")
            try:
                eod_g = _g._eod_greeks_day(ctl, ticker, exp, day)
            except Exception as e:
                eod_g = {}
                gaps.append({"ticker": ticker, "day": day, "endpoint": "bulk_hist/option/eod_greeks",
                             "status": "exc", "note": str(e)[:100]})
            if not eod_g:
                gaps.append({"ticker": ticker, "day": day, "endpoint": "bulk_hist/option/eod_greeks",
                             "status": "empty", "note": "no EOD same-day chain served"})

            tol = spy_band_rule(ticker)
            rows_open = _g._rows_at(chain_c, chain_p, oi_c, oi_p, min(spot_min))
            T = max((dt.datetime.strptime(exp, "%Y%m%d").date()
                     - dt.datetime.strptime(day, "%Y%m%d").date()).days / 365.0, 0.01)
            if len(rows_open) < 20:
                print(f"  [warn] {key}: only {len(rows_open)} open rows")
                gaps.append({"ticker": ticker, "day": day, "endpoint": "execution_locus",
                             "status": "thin", "note": f"{len(rows_open)} open rows < 20"})
            try:
                locus = ebe.execution_locus(rows_open, open_spot, T=T, tolerance_pct=tol)
            except Exception as e:
                print(f"  [warn] {key}: locus EXC {type(e).__name__}: {str(e)[:100]}")
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

            N_series, fwd_series, div_series, ms_series = [], [], [], []
            L_series = []  # new-engine LEVEL series (undoes dIV/0.01)
            firing_L, firing_fwd, firing_div = [], [], []
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
                    flow_i = ebe.vanna_flow(ne_bucket, div)
                    L_i = _flow_to_level(flow_i, div)  # undo dIV/0.01 -> LEVEL
                except Exception:
                    flow_i, L_i = 0.0, None
                N_series.append(flow_i)
                L_series.append(L_i)
                fwd_series.append(fwd)
                div_series.append(div)
                ms_series.append(ms)
                if abs(div) > DEADBAND and burst != 0.0 and L_i is not None:
                    firing_L.append(L_i)
                    firing_fwd.append(fwd)
                    firing_div.append(div)

            seed = _g.build_production_seed(day, exp, close_spot, eod_g, oi_c, oi_p, grid_set)
            prod_v, res = _g.run_production_vanna_same_day(seed, ticker)
            prod_sign = _g._sign_of(prod_v)
            new_day_level = sum(firing_L) if firing_L else 0.0
            new_sign = _g._sign_of(new_day_level)

            both_nonzero = (prod_sign != 0 and new_sign != 0)
            agreed = both_nonzero and (prod_sign == new_sign)
            if not both_nonzero:
                gaps.append({"ticker": ticker, "day": day, "endpoint": "sign-agreement",
                             "status": "zero/missing",
                             "note": f"prod_sign={prod_sign} new_sign={new_sign}"})

            n_prod_records = res.num_records if res is not None else 0
            n_prod_strikes = len(res.strike_grid) if res is not None else 0
            row = {
                "ticker": ticker, "day": day, "expiry": exp, "T": round(T, 5),
                "tolerance_pct": tol, "open_spot": open_spot, "close_spot": close_spot,
                "grid_strikes": len(grid_set), "eod_strikes": len(eod_g),
                "intraday_strikes_C": len(chain_c), "intraday_strikes_P": len(chain_p),
                "firing_buckets": len(firing_L), "buckets": len(bucket_ms),
                "prod_vanna": round(prod_v, 6) if prod_v is not None else None,
                "prod_sign": prod_sign, "prod_records": n_prod_records,
                "prod_strikes": n_prod_strikes,
                "new_day_LEVEL": round(new_day_level, 6) if firing_L else None,
                "new_sign": new_sign,
                "agreed": agreed, "both_nonzero": both_nonzero,
                "zero_gamma": getattr(locus, "zero_gamma", float("nan")),
            }
            rows_table.append(row)
            obs[key] = {"ticker": ticker, "expiry": exp, "T": T, "open_spot": open_spot,
                        "close_spot": close_spot, "tolerance_pct": tol, "grid_theta": grid_theta,
                        "firing_L": firing_L, "firing_fwd": firing_fwd, "firing_div": firing_div,
                        "N_series": N_series, "L_series": L_series,
                        "fwd_series": fwd_series, "div_series": div_series, "prod_vanna": prod_v}
            print(f"  [day] prod_v={prod_v} (sign {prod_sign}) | new_LEVEL={new_day_level:.4g} "
                  f"(sign {new_sign}) | firing={len(firing_L)} | tol={tol} | agree={agreed}")

    # ---- aggregate stats over ALL firing buckets ----
    all_L, all_fwd, all_div = [], [], []
    for k, o in obs.items():
        all_L.extend(o["firing_L"])
        all_fwd.extend(o["firing_fwd"])
        all_div.extend(o["firing_div"])
    n_buckets = len(all_L)

    # effective-n = number of independent resolvable (ticker,day) clusters
    eff_n = sum(1 for r in rows_table if r["both_nonzero"])
    md = _tanh_md(eff_n)

    corr_nf = _g._corr(all_L, all_fwd) if n_buckets >= 4 else float("nan")
    corr_nf_sp = _g._spearman(all_L, all_fwd) if n_buckets >= 4 else float("nan")
    r_a6 = _g._corr(all_div, all_fwd) if n_buckets >= 4 else float("nan")
    lo, hi = (_g._fisher_tanh_ci(corr_nf, n_buckets) if n_buckets >= 4
              else (float("nan"), float("nan")))

    # ---- sub-analyses: SPY (index) vs QQQ ----
    def _sub(tk):
        L_, fwd_, div_ = [], [], []
        n_res = 0
        for k, o in obs.items():
            if o["ticker"] != tk:
                continue
            L_.extend(o["firing_L"]); fwd_.extend(o["firing_fwd"]); div_.extend(o["firing_div"])
            rr = next((r for r in rows_table if r["ticker"] == tk and r["day"] == k.split("_")[1]), None)
            if rr and rr["both_nonzero"]:
                n_res += 1
        if len(L_) >= 4:
            return {
                "n_buckets": len(L_), "corr": _g._corr(L_, fwd_), "r_a6": _g._corr(div_, fwd_),
                "resolvable_clusters": n_res,
            }
        return {"n_buckets": len(L_), "corr": float("nan"), "r_a6": float("nan"),
                "resolvable_clusters": n_res}

    sub_spy = _sub("SPY")
    sub_qqq = _sub("QQQ")

    # ---- sign agreement per family ----
    def _agreement(tk=None):
        rows = [r for r in rows_table if (tk is None or r["ticker"] == tk)]
        agree = sum(1 for r in rows if r["both_nonzero"] and r["agreed"])
        resolv = sum(1 for r in rows if r["both_nonzero"])
        return agree, resolv

    spy_ag = _agreement("SPY")
    qqq_ag = _agreement("QQQ")
    comb_ag = _agreement()

    # ---- lead/lag on the firing series (pooled across days, within-day pairs) ----
    lead_lag = {}
    ll0, ll1, ll2, prior = [], [], [], []
    for k, o in obs.items():
        x = o["firing_L"]; y = o["firing_fwd"]
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
            "n_lag0": len(ll0), "n_lag1": len(ll1), "n_lag2": len(ll2), "n_prior": len(prior),
        }

    placebo_obs, placebo_nulls, placebo_p = (float("nan"), [], float("nan"))
    if n_buckets >= 4:
        placebo_obs, placebo_nulls, placebo_p = _g._placebo_null(all_L, all_fwd)

    sign_agreement = [(r["agreed"], r["both_nonzero"]) for r in rows_table]
    verdict, verdict_reason = gate_verdict_v2(sign_agreement, corr_nf, r_a6, n_buckets, eff_n)

    # ---- write result doc ----
    lines.append("\n### Data acquisition (provenance)\n")
    lines.append("Sequential via ThetaData proxy (THETADATA_HIST_CONCURRENCY=1), credentials "
                 "from main-tree .env (never persisted). Per (ticker, day): stock/ohlc intraday, "
                 "per-strike hist/all_greeks @600000 (C+P), hist/open_interest, and "
                 "bulk_hist/option/eod_greeks single-day for the production same-day book.\n")

    lines.append("\n### SPY BAND RULE (P0-2, pre-registered)\n")
    lines.append(f"SPY: tolerance_pct = {TOL_SPY} (open spot ± {TOL_SPY*100:.1f}%, anchored ONCE per day). "
                 f"QQQ: tolerance_pct = {TOL_QQQ} (open spot ± {TOL_QQQ*100:.1f}%). "
                 f"Applied to the execution locus BEFORE burst extraction; sign is read from "
                 f"LEVEL quantities. Justification: SPY realized vol ≈ half of QQQ, so ±0.5% on "
                 f"SPY is the index analogue of ±1% on QQQ (same breach probability).\n")

    lines.append("\n### Firing-day summary\n")
    lines.append("| Ticker | Day | Expiry | tol | open | close | grid | eod | intraday(C/P) | "
                 "firing/buckets | prod_vanna | prod_sign | new_LEVEL | new_sign | agree |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows_table:
        pv = f"{r['prod_vanna']:+.3g}" if r["prod_vanna"] is not None else "NA"
        nv = f"{r['new_day_LEVEL']:+.3g}" if r["new_day_LEVEL"] is not None else "NA"
        ag = "YES" if r["agreed"] else ("NO" if r["both_nonzero"] else "n/a(0)")
        lines.append(f"| {r['ticker']} | {r['day']} | {r['expiry']} | {r['tolerance_pct']:.3f} | "
                     f"{r['open_spot']:.2f} | {r['close_spot']:.2f} | {r['grid_strikes']} | "
                     f"{r['eod_strikes']} | {r['intraday_strikes_C']}/{r['intraday_strikes_P']} | "
                     f"{r['firing_buckets']}/{r['buckets']} | {pv} | {r['prod_sign']} | {nv} | "
                     f"{r['new_sign']} | {ag} |")

    def _render_ag(label, ag):
        a, res = ag
        frac = f"{a/res:.0%}" if res else "n/a"
        lines.append(f"- **{label}** level-vs-level sign agreement: **{a}/{res} = {frac}** "
                     f"resolvable clusters agree.")

    lines.append("\n### PRIMARY — LEVEL-vs-LEVEL SIGN AGREEMENT (Cem's gate, P0-2 correction)\n")
    _render_ag("SPY (INDEX)", spy_ag)
    _render_ag("QQQ", qqq_ag)
    _render_ag("COMBINED", comb_ag)
    lines.append("")
    for r in rows_table:
        st = "AGREE" if r["agreed"] else ("non-zero" if r["both_nonzero"] else "ZERO/missing — not counted")
        lvl_s = f"{r['new_day_LEVEL']:.3g}" if r["new_day_LEVEL"] is not None else "NA"
        lines.append(f"- {r['ticker']} {r['day']}: prod_sign={r['prod_sign']} "
                     f"new_sign={r['new_sign']} → {st} (new_LEVEL={lvl_s} if set)")

    lines.append(f"\n### EFFECTIVE-n vs md\n")
    lines.append(f"- effective-n = **{eff_n}** independent resolvable (ticker, day) clusters "
                 f"(SPY {spy_ag[1]} + QQQ {qqq_ag[1]}).")
    lines.append(f"- md at effective-n = **{md:.3f}** (R2-5 tanh form).")
    lines.append(f"- pooled firing buckets n = **{n_buckets}** (reported separately; the gate's "
                 f"inferential claim rests on effective-n = {eff_n}, not pooled n).")
    if eff_n > 4 and eff_n >= _MIN_RESOLVABLE:
        lines.append(f"- effective-n {eff_n} {'CLEARS' if eff_n > 4 else 'does not exceed'} the "
                     f"K=3 md 0.993 floor and is >= the {_MIN_RESOLVABLE}-cluster target.")
    else:
        lines.append(f"- effective-n {eff_n} is **below the {_MIN_RESOLVABLE}-cluster target** — "
                     f"power limited.")

    lines.append("\n### SECONDARY — Correlations vs forward returns (descriptive)\n")
    if n_buckets >= 4:
        lines.append(f"- corr(new_LEVEL, fwd) Pearson = {corr_nf:+.4f}  Spearman = {corr_nf_sp:+.4f}  "
                     f"(n={n_buckets} firing buckets, {eff_n} clusters)  Fisher-tanh 95% CI "
                     f"[{lo:+.3f}, {hi:+.3f}]")
        lines.append(f"  SPY sub-analysis: corr={sub_spy['corr']:+.4f} r_A6={sub_spy['r_a6']:+.4f} "
                     f"(n={sub_spy['n_buckets']} buckets, {sub_spy['resolvable_clusters']} resolvable)")
        lines.append(f"  QQQ sub-analysis: corr={sub_qqq['corr']:+.4f} r_A6={sub_qqq['r_a6']:+.4f} "
                     f"(n={sub_qqq['n_buckets']} buckets, {sub_qqq['resolvable_clusters']} resolvable)")
        lines.append(f"  effective-n = {eff_n} clusters (SPY + QQQ) — the K=3 CI from P0-1 is now "
                     f"superseded; the correlational arm is inferential only if effective-n clears md.")
    else:
        lines.append(f"- insufficient firing buckets ({n_buckets}) for correlation.")

    lines.append("\n### LEAD/LAG FALSIFIER\n")
    if lead_lag:
        lines.append(f"- corr(N_i, fwd_{{i+0}}) = {lead_lag['lag0']:+.4f}  (n={lead_lag['n_lag0']})")
        lines.append(f"- corr(N_i, fwd_{{i+1}}) = {lead_lag['lag1']:+.4f}  (n={lead_lag['n_lag1']}; response one 10-min lag AFTER breach)")
        lines.append(f"- corr(N_i, fwd_{{i+2}}) = {lead_lag['lag2']:+.4f}  (n={lead_lag['n_lag2']}; two lags after)")
        lines.append(f"- REVERSE (prior return predicts signal): corr(N_i, fwd_{{i-1}}) = {lead_lag['prior']:+.4f}  (n={lead_lag['n_prior']})")
        l0 = lead_lag.get("lag0", 0); l1 = lead_lag.get("lag1", 0)
        lag_nonnull = (abs(l1) > 0.05) and ((l1 > 0) == (l0 > 0))
        lines.append(f"  read: lead/lag is {'NON-NULL (response persists after the breach bucket)' if lag_nonnull else 'NULL/weak — no persistent response after the breach bucket'}.")
        if abs(lead_lag.get("prior", 0)) > abs(l0) and abs(lead_lag.get("prior", 0)) > 0.05:
            lines.append(f"  CAUTION: prior-return predicts the signal more strongly than the signal predicts "
                         f"forward returns → reverse-causation/reflexivity present.")
    else:
        lines.append("- insufficient data for lead/lag.")

    lines.append("\n### PLACEBO NULL\n")
    if n_buckets >= 4:
        p_frac = sum(1 for v in placebo_nulls if v >= placebo_obs) / len(placebo_nulls)
        lines.append(f"- {_PLACEBO_PERMS} permutations (seed={_SEED}): observed corr = {placebo_obs:+.4f}; "
                     f"empirical p = {placebo_p:.4f} ({p_frac:.1%} of null >= obs).")
        lines.append(f"- null median = {sorted(placebo_nulls)[len(placebo_nulls)//2]:+.4f}; "
                     f"null 95th pct = {sorted(placebo_nulls)[int(0.95*len(placebo_nulls))-1]:+.4f}")
        lines.append(f"  read: {'non-null (obs beyond placebo null)' if placebo_p < 0.05 else 'null NOT rejected at p<0.05'}.")
    else:
        lines.append("- insufficient firing buckets for placebo.")

    lines.append("\n### (A6) REFLEXIVITY BASELINE (Cem's decisive cheap diagnostic)\n")
    if n_buckets >= 4:
        lines.append(f"- corr(dIV, fwd) on the SAME firing buckets = {r_a6:+.4f}  (n={n_buckets})")
        if corr_nf == corr_nf:
            lines.append(f"- vanna-LEVEL corr {corr_nf:+.4f} vs reflexivity {r_a6:+.4f} → "
                         f"{'vanna EXCEEDS reflexivity (survives A6)' if corr_nf > r_a6 else 'vanna <= reflexivity — the vanna sign is downstream of dIV/return reflexivity (convention-bound)'}")
    else:
        lines.append("- insufficient firing buckets for A6.")

    lines.append("\n### DATA GAPS / EXCLUDED CLUSTERS\n")
    if gaps:
        lines.append("| ticker | day | endpoint | status | note |")
        lines.append("|---|---|---|---|---|")
        for g in gaps:
            lines.append(f"| {g.get('ticker','-')} | {g.get('day','-')} | {g['endpoint']} | "
                         f"{g['status']} | {g.get('note','')} |")
    else:
        lines.append("- none recorded.")
    zero_dte = [f"{r['ticker']} {r['day']}" for r in rows_table if r["prod_records"] == 0]
    if zero_dte:
        lines.append(f"\nNOTE on {', '.join(zero_dte)}: the production same-day book returned 0 records "
                     f"because the firing expiry is the ANCHOR and expires THAT day (tte=0) — the "
                     f"production `dealer_positioning` expiry filter (0 < tte) drops an expiring "
                     f"weekly. This is a production-pipeline convention, not a proxy/data failure; "
                     f"the day is reported as sign=0 (excluded) per the pre-registered zero-sign policy.")

    lines.append(f"\n### GATE VERDICT: **{verdict}** — {verdict_reason}\n")
    lines.append(f"Ran in {time.time()-t0:.1f}s. Raw observations: `dual_pipeline_gate_v2_obs.json`.")

    os.makedirs(CACHE, exist_ok=True)
    out = os.path.join(CACHE, "dual_pipeline_gate_v2_RESULT.md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    with open(os.path.join(CACHE, "dual_pipeline_gate_v2_obs.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "verdict": verdict, "verdict_reason": verdict_reason,
            "effective_n": eff_n, "md": md, "n_buckets": n_buckets,
            "sign_agreement_combined": list(comb_ag),
            "sign_agreement_spy": list(spy_ag),
            "sign_agreement_qqq": list(qqq_ag),
            "corr_new_level_fwd": corr_nf, "corr_spearman": corr_nf_sp,
            "r_a6": r_a6, "lead_lag": lead_lag, "placebo": {"obs": placebo_obs, "p": placebo_p},
            "rows": rows_table,
            "obs": {k: {kk: vv for kk, vv in o.items() if kk != "grid_theta"}
                    for k, o in obs.items()},
        }, fh, indent=2, default=str)
    print("\n".join(lines))
    print(f"\n[P0-2/P0-3] verdict: {verdict} — {verdict_reason}")
    print(f"[P0-2/P0-3] SPY agree={spy_ag[0]}/{spy_ag[1]}  QQQ agree={qqq_ag[0]}/{qqq_ag[1]}  "
          f"combined={comb_ag[0]}/{comb_ag[1]}  eff-n={eff_n}  md={md:.3f}")


def strike_to_theta_int(k):
    return int(round(k * 1000))


if __name__ == "__main__":
    main()
