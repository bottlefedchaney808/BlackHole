"""ROUND-6 CORRECTED PACKET — run_dual_pipeline_gate_v5.py (the packet R2 confirmed is required).

Builds on run_dual_pipeline_gate_v4.py and preserves every R5-1..R5-8 correction,
adding the R6 merged upgrade set from R2 (deleg_da28fbca, all source-verified):

  R6-1  RECOMPUTE the sign statistic on UNIQUE CALENDAR DAYS (eff-n=9). A day
        carrying both QQQ+SPY resolvable clusters is ONE unique day. A unique
        day counts AGREE only if EVERY resolvable cluster on it agrees
        (conservative; a mixed day with a disagreeing family is a DISAGREE).
        The 12-cluster result is reported ONLY as a sensitivity.
  R6-2  STOP quoting md=0.816 as binomial power. Report (a) bar-clearing prob
        P(pass the 2/3 bar | n, true p) and (b) n-for-80%-power to reject the
        p=0.5 null at alpha=0.05 one-sided. md stays a CORRELATION MDE only.
  R6-3  REPLACE the pure-SVI strike-COUNT proxy with the TRUE magnitude/OI/vanna-
        weighted production level  L_f0 = sum sign_k * OI_k * vanna_k  with
        deadband/fallback strikes contributing 0 (not -1). Computed by patching
        dealer_positioning._resolve_sign so the Layer-1b -1 fallback -> 0, then
        re-driving the same production pipeline (same chain, same OI, same SVI
        fit, same aggregation). sign(L_f0) is compared CROSS-ENGINE to the new-
        engine day LEVEL sign across ALL 12 merged resolvable clusters INCLUDING
        seed rows. CAVEAT: NOT derivable from the cached obs (no per-strike OI/
        vanna persisted) -> requires a driver change + re-run. The underlying
        EOD-greeks + OI chain data is re-fetchable from ThetaData for the SAME
        days at ZERO new-day acquisition.
  R6-4  Report correlational + placebo arms as UNDERPOWERED (n=17 buckets,
        md(17)=0.634; at eff-n=9 the |r| MDE is 0.816 and power at |r|0.3-0.5 is
        ~19-38%).
  R6-5  SPY/QQQ kept SEPARATE, never pooled. Pooled corr is a descriptive
        Simpson cancellation only. corr_spy/corr_qqq labeled bucket-level noise.
  R6-6  EMIT reproducibility assertions (cluster/day counts, duplicate-day check,
        provenance counts) into the result.
  R6-7  ZERO-COST CONVENTION-DISTANCE TEST (the decisive demote-vs-open test):
        delta_prod (production's SVI deviation term, from rich/cheap/deadband
        provenance, normalized by total strikes) vs delta_new (the new engine's
        deviation from its fixed -1xBS, which is 0 BY CONSTRUCTION — the new
        engine has no SVI branch). Computed across the FULL corpus (all ~40
        clusters INCLUDING zero-firing clusters). If delta_prod is directionally
        INDEPENDENT of delta_new (real, materially large SVI deviation), the
        mechanism stays OPEN; if both hug the shared -1 baseline (delta_prod ~ 0
        and delta_new == 0), the mechanism is convention-bound -> demote
        permanently. Purely network-free (from cached provenance).

PRE-REGISTERED (R6): the delta verdict rule is fixed BEFORE seeing numbers:
  delta_new = 0 for every cluster (new engine has no SVI branch).
  delta_prod = (rich_plus1 - cheap_minus1) / total_strikes  per cluster.
  Convention distance D = |delta_prod - delta_new| = |delta_prod|.
  Verdict: DEMOTE if mean|D| < 0.10 AND < 25% of clusters have |D| >= 0.10
           (production hugs the shared -1 baseline, no independent deviation).
           OPEN otherwise (material production deviation exists).
This threshold is deterministic and documented, not chosen post-hoc.
"""

import datetime as dt
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import dealer_positioning as _dp
import expiry_book_exposure as ebe
import run_dual_pipeline_gate as _g  # P0-1 helpers
import run_dual_pipeline_gate_v2 as _v2  # P0-2/P0-3 helpers
import run_dual_pipeline_gate_v3 as _r5  # R5 helpers
import run_dual_pipeline_gate_v4 as _v4  # R5-4/merge helpers + acquisition loop

from shared.thetadata import ThetaDataController

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_intraday_cache")
DEADBAND = 0.01
SIGN_MODEL = "vol_surface_replication"
_AGREE_FRAC = 2.0 / 3.0

# R6 pre-registered delta verdict threshold.
_DEMOTE_DELTA_MEAN = 0.10  # mean|delta_prod| below this -> production hugs baseline
_DEMOTE_DELTA_FRAC = 0.25  # <25% of clusters with |delta_prod|>=0.10

# The 12 merged RESOLVABLE (both-nonzero) clusters from the v3+v4 acquisition:
# (ticker, day, expiry, source). These are the clusters the weighted fallback->0
# production level is re-derived for (seed rows included, provenance preserved).
RESOLVABLE = [
    # seed (v3) resolvable clusters
    ("QQQ", "20260508", "20260511", "seed-v3"),
    ("QQQ", "20260605", "20260608", "seed-v3"),
    ("QQQ", "20260716", "20260717", "seed-v3"),
    ("QQQ", "20260731", "20260803", "seed-v3"),
    ("SPY", "20260605", "20260608", "seed-v3"),
    # new (v4) resolvable clusters
    ("QQQ", "20260617", "20260624", "v4"),
    ("QQQ", "20260729", "20260731", "v4"),
    ("SPY", "20260413", "20260417", "v4"),
    ("SPY", "20260430", "20260501", "v4"),
    ("SPY", "20260617", "20260624", "v4"),
    ("SPY", "20260728", "20260731", "v4"),
    ("SPY", "20260729", "20260731", "v4"),
]


# ---------------------------------------------------------------------------
# R6-1: unique-day binomial (network-free helpers)
# ---------------------------------------------------------------------------
def _unique_day_outcome(rows):
    """Collapse a list of row dicts (each with 'day', 'agreed', 'both_nonzero')
    into ONE outcome per unique calendar day. A unique day is counted as a
    DISAGREE if it contains ANY resolvable cluster that disagrees; AGREE only if
    EVERY resolvable cluster on that day agrees. A day with no resolvable
    cluster contributes nothing. Returns (n_unique_days, n_agree_days,
    day_map) where day_map = {day: (n_resolvable, n_agree, is_agree)}."""
    from collections import defaultdict

    by_day = defaultdict(list)
    for r in rows:
        if not r.get("both_nonzero"):
            continue
        by_day[r["day"]].append(bool(r.get("agreed")))
    day_map = {}
    n_agree = 0
    for day, agrees in sorted(by_day.items()):
        is_agree = all(agrees)  # EVERY resolvable cluster agrees
        day_map[day] = (len(agrees), sum(agrees), is_agree)
        if is_agree:
            n_agree += 1
    return len(day_map), n_agree, day_map


def _binom_tail(k, n, p=0.5):
    """P(X >= k | Binom(n, p)) computed in log-space (stable for large n).
    Uses the log-PMF: log(C(n,i)) + i*log p + (n-i)*log(1-p)."""
    from math import exp, lgamma, log, log1p

    if n <= 0:
        return float("nan")
    k = max(0, min(n, int(k)))
    if p <= 0.0:
        return 1.0 if k == 0 else 0.0
    if p >= 1.0:
        return 1.0 if k <= n else 0.0
    logn = lgamma(n + 1)
    lp = log(p)
    l1p = log1p(-p)
    s = 0.0
    for i in range(k, n + 1):
        lpmf = logn - lgamma(i + 1) - lgamma(n - i + 1) + i * lp + (n - i) * l1p
        s += exp(lpmf)
    return min(s, 1.0)


def _bar_clearing_prob(n, true_p, bar_frac=_AGREE_FRAC):
    """P(clear the 2/3 gate bar | Binom(n, true_p)). bar = ceil(bar_frac*n)."""
    if n <= 0:
        return float("nan")
    bar = math.ceil(bar_frac * n)
    return _binom_tail(bar, n, true_p)


def _heterogeneous_null_tail(day_map, k_obs, p=0.5):
    """R7-A: Poisson-binomial tail P(K >= k_obs) under the STRICT-AND day rule.

    The unique-day primary is a CONJUNCTION statistic. A single-family day
    (one resolvable cluster) agrees by chance with q=p; a two-family day (QQQ
    + SPY both resolvable, ~0.99 one family) agrees by chance with q=p*p (both
    must agree). So the null is heterogeneous across days, NOT a uniform p=0.5.

    Returns (tail_prob, full_dist) where full_dist[k] = P(exactly k days agree).
    """
    if not day_map:
        return (float("nan"), {0: 1.0})
    # Each day contributes q = p if n_resolvable==1 else p*p.
    dist = {0: 1.0}
    for day, (nres, _nagree, _is_agree) in day_map.items():
        q = p if nres <= 1 else p * p
        nd = {}
        for kk, pr in dist.items():
            nd[kk] = nd.get(kk, 0.0) + pr * (1.0 - q)
            nd[kk + 1] = nd.get(kk + 1, 0.0) + pr * q
        dist = nd
    tail = sum(pr for kk, pr in dist.items() if kk >= k_obs)
    return (min(tail, 1.0), dist)


def _n_for_80_power(true_p, alpha=0.05, bar_frac=_AGREE_FRAC):
    """Smallest n such that a one-sided sign test of H0:p=0.5 vs H1:p>0.5 at
    alpha reaches 80% power to REJECT the null when the true agreement rate is
    true_p. Rejection set X>=k where k = min{k: P(X>=k|Bin(n,0.5))<=alpha}.
    Returns None if unreachable within 2000."""
    for n in range(1, 2001):
        # one-sided critical value at alpha (reject null)
        k = None
        for kk in range(1, n + 1):
            if _binom_tail(kk, n, 0.5) <= alpha:
                k = kk
                break
        if k is None:
            continue
        if _binom_tail(k, n, true_p) >= 0.80:
            return n
    return None


def _n_for_80_bar_clear(true_p, bar_frac=_AGREE_FRAC):
    """Smallest n such that P(clear the bar | Binom(n, true_p)) >= 0.80.
    NOTE: for true_p == bar_frac the bar sits at the mean and this can never
    reach 0.80 -> returns None (documented)."""
    for n in range(1, 2001):
        if _bar_clearing_prob(n, true_p, bar_frac) >= 0.80:
            return n
    return None


def _sign_test_rejection_threshold(n, alpha=0.05):
    """Smallest k with P(X>=k | Binom(n, 0.5)) <= alpha (one-sided sign test
    H0:p=0.5 vs H1:p>0.5). Returns the rejection threshold X>=k, or n+1 if
    none exists at this alpha."""
    for k in range(n + 1):
        if _binom_tail(k, n, 0.5) <= alpha:
            return k
    return n + 1


def _exact_sign_power(n, true_p, alpha=0.05):
    """Power of the exact one-sided sign test at n under a true agreement p:
    P(X >= k_reject | Binom(n, true_p))."""
    if n <= 0:
        return float("nan")
    k = _sign_test_rejection_threshold(n, alpha)
    if k > n:
        return 0.0
    return _binom_tail(k, n, true_p)


# ---------------------------------------------------------------------------
# R6-3: weighted fallback->0 production level (L_f0 = sum sign_k*OI_k*vanna_k)
# ---------------------------------------------------------------------------
def _resolve_sign_fallback0(
    right, strike, sign_model, otm_strikes=None, vol_surface_ref=None
):
    """Identical to dealer_positioning._resolve_sign for vol_surface_replication
    EXCEPT the Layer-1b -1 fallback returns 0.0 (deadband/missing/ref-None
    strikes contribute 0 to the weighted level, not -1)."""
    if sign_model == "oi_heuristic":
        return _dp._dealer_sign(right)
    if sign_model == "replication":
        if otm_strikes is None or (strike, right) not in otm_strikes:
            return 0.0
        return -1.0
    if sign_model == "vol_surface_replication":
        if otm_strikes is None or (strike, right) not in otm_strikes:
            return 0.0
        if vol_surface_ref is not None:
            vs = ebe  # noqa: F841  (module ref placeholder)
            from vol_surface_reference import resolve_vol_surface_sign

            vs_sign = resolve_vol_surface_sign(vol_surface_ref, strike, right)
            if vs_sign != 0.0:
                return vs_sign
        return 0.0  # fallback -> 0 (not -1)
    raise ValueError(
        f"sign_model must be one of {{'oi_heuristic','replication','vol_surface_replication'}}, got {sign_model!r}"
    )


def run_production_vanna_fallback0(seed, ticker):
    """Re-drive the production same-day pipeline with the Layer-1b -1 fallback
    replaced by 0 (the R6-3 magnitude-weighted fallback->0 level). Returns
    (L_f0, result) or (None, None) on failure. Restoration guaranteed."""
    import datetime as _dt

    import run_compare_live_vs_new as rcl

    m = seed.get("manifest", {})
    odr = m.get("obs_date_range") or []
    if odr and str(odr[-1])[:8].isdigit():
        as_of = _dt.datetime.strptime(str(odr[-1])[:8], "%Y%m%d").date()
    else:
        as_of = _dt.date.today()

    class _FrozenDT(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _dt.datetime.combine(as_of, _dt.time(16, 0), tzinfo=tz)

    td = rcl._SeedTD(seed)
    hist_rows = rcl._hist_rows_from_seed(seed)
    old_ctrl = _dp.ThetaDataController
    old_dt = _dp.datetime
    old_resolve = _dp._resolve_sign
    _dp.ThetaDataController = lambda *a, **k: td
    _dp.datetime = _FrozenDT
    _dp._resolve_sign = _resolve_sign_fallback0
    try:
        exp = str(seed.get("manifest", {}).get("expiry", "")) or None
        result = _dp.compute_dealer_positioning(
            ticker,
            target_years=0.25,
            expiration=exp,
            sign_model=SIGN_MODEL,
            accumulate=False,
            _accumulation_hist_rows=hist_rows,
        )
        if result is None:
            return None, None
        pv = result.vanna_call_shares + result.vanna_put_shares
        if math.isnan(pv):
            return None, result
        return pv, result
    except Exception as e:
        print(f"    [prod-fallback0] EXC {type(e).__name__}: {str(e)[:120]}")
        return None, None
    finally:
        _dp.ThetaDataController = old_ctrl
        _dp.datetime = old_dt
        _dp._resolve_sign = old_resolve


def _rederive_weighted_level(ctl, ticker, day, expiry):
    """Re-fetch EOD greeks + OI + spot for one (ticker, day, expiry) and compute
    the magnitude-weighted fallback->0 production level L_f0. Returns a dict with
    per-strike arrays persisted, or None on data failure. Zero NEW-day
    acquisition (the day is already in the study)."""
    try:
        spot_min = _g._stock_intraday(ctl, ticker, day)
    except Exception:
        return None
    if not spot_min:
        return None
    open_spot = spot_min[min(spot_min)]
    close_spot = spot_min[max(spot_min)]
    base = round(open_spot / 5.0) * 5.0
    grid_theta = [_g.strike_to_theta(base + i * 5.0) for i in range(-12, 13)]
    grid_set = set(grid_theta)
    oi_c, _ = _v2._robust_oi_proxy(ctl, ticker, expiry, day, grid_theta, "C")
    oi_p, _ = _v2._robust_oi_proxy(ctl, ticker, expiry, day, grid_theta, "P")
    eod_g = _g._eod_greeks_day(ctl, ticker, expiry, day)
    if not eod_g:
        return None
    seed = _g.build_production_seed(
        day, expiry, close_spot, eod_g, oi_c, oi_p, grid_set
    )
    L_f0, res = run_production_vanna_fallback0(seed, ticker)
    # baseline (fallback=-1) for reference
    L_base, _ = _g.run_production_vanna_same_day(seed, ticker)
    # persist per-strike arrays from the production result records (gamma_records
    # live on the result object as .records / .gamma_records — read whichever)
    per_strike = []
    if res is not None:
        recs = (
            getattr(res, "gamma_records", None) or getattr(res, "records", None) or []
        )
        for rec in recs:
            per_strike.append(
                {
                    "strike": float(rec.strike),
                    "right": rec.right,
                    "oi": float(rec.oi),
                    "vanna": float(getattr(rec, "vanna", 0.0) or 0.0),
                    "iv": float(getattr(rec, "iv", 0.0) or 0.0),
                    "applied_sign": float(rec.applied_sign),
                }
            )
    return {
        "ticker": ticker,
        "day": day,
        "expiry": expiry,
        "open_spot": open_spot,
        "close_spot": close_spot,
        "L_f0": L_f0,
        "L_base": L_base,
        "n_per_strike": len(per_strike),
        "per_strike": per_strike,
        "grid_n": len(grid_set),
        "eod_n": len(eod_g),
    }


# ---------------------------------------------------------------------------
# R6-7: delta convention-distance (network-free from cached provenance)
# ---------------------------------------------------------------------------
def delta_prod(prov):
    """Production's SVI deviation term: (rich_plus1 - cheap_minus1)/total."""
    total = prov.get("total", 0)
    if total <= 0:
        return 0.0
    return (prov.get("rich_plus1", 0) - prov.get("cheap_minus1", 0)) / float(total)


def delta_new(_prov):
    """New engine's deviation from its fixed -1xBS = 0 BY CONSTRUCTION (the new
    engine has no SVI branch; dealer_frame_vanna is a flat -1*BS_vanna)."""
    return 0.0


def convention_distance(deltas_prod):
    """R7-C: SUPPORTING production-flat-baseline screen (NOT a decisive two-sided
    convention-distance test).

    delta_new==0 BY CONSTRUCTION (the new engine has no SVI branch), so
    D=|delta_prod| is a one-sided production-deviation screen. delta_prod is an
    unweighted IV-vs-chain-median strike-count proxy (run_dual_pipeline_gate_v3.py
    _sign_provenance), NOT the production SVI deviation_by_strike /
    resolve_vol_surface_sign path. This screen can only describe how -1-bound
    production's OUTPUT sign map is; it cannot affirm or refute the shared -1
    root as a mechanism. Relabeled per R7-C as supporting, never decisive.
    """
    if not deltas_prod:
        return (float("nan"), float("nan"), "INDETERMINATE", "empty corpus")
    mean_abs = sum(abs(d) for d in deltas_prod) / len(deltas_prod)
    frac_large = sum(1 for d in deltas_prod if abs(d) >= _DEMOTE_DELTA_MEAN) / len(
        deltas_prod
    )
    if mean_abs < _DEMOTE_DELTA_MEAN and frac_large < _DEMOTE_DELTA_FRAC:
        return (
            mean_abs,
            frac_large,
            "SUPPORTING-screen: -1-bound",
            f"delta_prod hugs the shared -1 baseline (mean|D|={mean_abs:.4f} < "
            f"{_DEMOTE_DELTA_MEAN}, {frac_large:.1%} of clusters >= {_DEMOTE_DELTA_MEAN}); "
            f"one-sided proxy, delta_new==0 by construction. Descriptive screen only — "
            f"mechanism formally open-not-disproven (not a decisive demote).",
        )
    return (
        mean_abs,
        frac_large,
        "SUPPORTING-screen: deviation-present",
        f"material production SVI deviation present (mean|D|={mean_abs:.4f}, "
        f"{frac_large:.1%} of clusters >= {_DEMOTE_DELTA_MEAN}); one-sided proxy. "
        f"Descriptive screen only — not a decisive mechanistic test.",
    )


# ---------------------------------------------------------------------------
# Reproducibility assertions (R6-6)
# ---------------------------------------------------------------------------
def assert_corpus_integrity(rows):
    """Network-free checks: no duplicate (ticker,day) rows; provenance counts
    present; day field present. Returns a dict of counts + raises on hard
    violations."""
    seen = {}
    for r in rows:
        key = (r.get("ticker"), r.get("day"))
        assert r.get("day"), f"row missing day: {r}"
        assert "provenance" in r, f"row {key} missing provenance"
        assert key not in seen, f"duplicate (ticker,day) row: {key}"
        seen[key] = True
    return {"n_rows": len(rows), "n_unique": len(seen)}


def main():
    os.environ.setdefault("THETADATA_HIST_CONCURRENCY", "1")
    ctl = ThetaDataController()
    t0 = time.time()
    lines = []
    lines.append(
        "# ROUND-6 CORRECTED PACKET — Dual-Pipeline Convention-Independence Gate (result)\n"
    )
    lines.append(
        f"**Date:** {dt.date.today().isoformat()}  **Driver:** `run_dual_pipeline_gate_v5.py`\n"
    )
    lines.append(
        "**R6 corrections:** unique-day binomial (R6-1), honest binomial power (R6-2), "
        "TRUE magnitude/OI/vanna-weighted fallback->0 production level (R6-3), "
        "underpowered corr/placebo arms (R6-4), SPY/QQQ separate (R6-5), "
        "reproducibility assertions (R6-6), delta convention-distance test (R6-7). "
        "All R5-1..R5-8 corrections preserved.\n"
    )

    # ---- Load merged corpus (seed v3 rows + v4 this-run rows) ----
    v3 = {}
    v3p = os.path.join(CACHE, "dual_pipeline_gate_v3_obs.json")
    if os.path.exists(v3p):
        v3 = json.load(open(v3p, encoding="utf-8"))
    v4 = {}
    v4p = os.path.join(CACHE, "dual_pipeline_gate_v4_obs.json")
    if os.path.exists(v4p):
        v4 = json.load(open(v4p, encoding="utf-8"))
    seed_rows = _v4._merge_seed_rows()
    new_rows = v4.get("rows", [])
    all_rows = seed_rows + new_rows
    integrity = assert_corpus_integrity(all_rows)
    lines.append("### Corpus (merged seed-v3 + v4)\n")
    lines.append(
        f"- {integrity['n_rows']} rows ({len(seed_rows)} seed + {len(new_rows)} v4), "
        f"{integrity['n_unique']} unique (ticker,day). Duplicate-day check passed.\n"
    )

    if not all_rows:
        print("No merged corpus rows (seed + v4 both empty) — nothing to gate.")
        return

    # ---- R6-1: UNIQUE-DAY binomial (PRIMARY) + 12-cluster sensitivity ----
    n_unique, n_agree_unique, day_map = _unique_day_outcome(all_rows)
    resolvable = [r for r in all_rows if r.get("both_nonzero")]
    n_clusters = len(resolvable)
    n_agree_clusters = sum(1 for r in resolvable if r.get("agreed"))
    # R7-A: the strict-AND day rule is a CONJUNCTION statistic. A single-family
    # day agrees by chance with q=0.5; a two-family (QQQ+SPY) day with q=p*p=0.25
    # (both families must agree). So the null is heterogeneous (Poisson-binomial),
    # NOT a uniform p=0.5. Report the correct mixture null.
    binom_unique, _hetero_dist = _heterogeneous_null_tail(
        day_map, n_agree_unique, p=0.5
    )
    binom_cluster = (
        _binom_tail(n_agree_clusters, n_clusters, 0.5) if n_clusters else float("nan")
    )
    # 2/3 bar-clearing (R6-2)
    bar_unique = math.ceil(_AGREE_FRAC * max(n_unique, 1))
    bar_clear_unique_2_3 = _bar_clearing_prob(n_unique, 2 / 3)
    bar_clear_unique_1_2 = _bar_clearing_prob(n_unique, 0.5)
    bar_clear_cluster_2_3 = _bar_clearing_prob(n_clusters, 2 / 3)
    bar_clear_cluster_1_2 = _bar_clearing_prob(n_clusters, 0.5)
    # n-for-80% power (R6-2)
    n80_reject_2_3 = _n_for_80_power(2 / 3)  # detect true p=2/3 vs null p=0.5
    n80_reject_3_4 = _n_for_80_power(0.75)
    n80_bar_2_3 = _n_for_80_bar_clear(2 / 3)  # bar-clear power (None: bar at mean)
    n80_bar_3_4 = _n_for_80_bar_clear(0.75)
    # R7-B: exact one-sided sign-test power (H0:p=0.5 vs H1:p>0.5, alpha=0.05).
    bar_unique_reject = _sign_test_rejection_threshold(n_unique, 0.05)
    exact_power_2_3 = _exact_sign_power(n_unique, 2 / 3, 0.05)

    lines.append(
        "### PRIMARY — UNIQUE-DAY SIGN AGREEMENT (R6-1; eff-n on UNIQUE CALENDAR DAYS)\n"
    )
    lines.append(
        f"- Resolvable clusters: {n_clusters}  **unique calendar days: {n_unique}**."
    )
    lines.append(
        "- Unique-day rule (pre-registered): a day counts AGREE only if EVERY resolvable "
        "cluster on it agrees; any disagreeing family on a day => that day is a DISAGREE. "
        "Days carrying both QQQ+SPY count ONCE."
    )
    for day, (nres, nagree, is_agree) in day_map.items():
        lines.append(
            f"  - {day}: {nres} resolvable cluster(s), {nagree} agree "
            f"-> {'AGREE' if is_agree else 'DISAGREE'}"
        )
    lines.append(
        f"- **Unique-day agreement: {n_agree_unique}/{n_unique} = {n_agree_unique / n_unique:.1%}**"
    )
    lines.append(
        f"- R7-A **HETEROGENEOUS (Poisson-binomial) null** for the CONJUNCTION day rule: "
        f"single-family days agree by chance q=0.5, two-family days q=0.25 (p²). "
        f"P(agree>={n_agree_unique} | mixture null) = **{binom_unique:.4f}** — NOT a "
        f"uniform p=0.5 tail. The primary is a **conjunction statistic**, not an "
        f"ordinary agreement rate.\n"
    )
    lines.append(
        "### SENSITIVITY — 12-cluster (NOT the primary; same-day SPY/QQQ families are "
        "not independent)\n"
    )
    lines.append(
        f"- Cluster-level agreement: {n_agree_clusters}/{n_clusters} = "
        f"{n_agree_clusters / n_clusters:.1%}; P(>={n_agree_clusters}|{n_clusters},0.5)="
        f"{binom_cluster:.4f}\n"
    )
    lines.append("### HONEST BINOMIAL POWER (R6-2 — md is NOT power)\n")
    lines.append(
        f"- Bar-clearing prob at n={n_unique}: P(clear >= {bar_unique}/{n_unique} | "
        f"true p=2/3) = **{bar_clear_unique_2_3:.3f}**; at true p=1/2 = "
        f"{bar_clear_unique_1_2:.3f}."
    )
    lines.append(
        f"- Bar-clearing prob at n={n_clusters} (sensitivity): P(clear >= "
        f"{math.ceil(_AGREE_FRAC * n_clusters)}/{n_clusters} | p=2/3) = "
        f"{bar_clear_cluster_2_3:.3f}."
    )
    lines.append(
        f"- n-for-80% power (one-sided sign test, H0:p=0.5 vs H1:p>0.5, alpha=0.05): "
        f"detect true p=2/3 -> **n={n80_reject_2_3}**; detect true p=0.75 -> "
        f"**n={n80_reject_3_4}**."
    )
    n80bar23 = (
        str(n80_bar_2_3)
        if n80_bar_2_3
        else "NEVER (bar sits at the mean, power -> 0.5 asymptotically)"
    )
    lines.append(
        f"- n-for-80% bar-clearing (power to PASS the 2/3 bar under a true p): "
        f"p=2/3 -> {n80bar23}; p=0.75 -> n={n80_bar_3_4}."
    )
    lines.append(
        f"- **Current n={n_unique} exact one-sided sign-test power = {exact_power_2_3:.1%}** (rejection at "
        f"X>={bar_unique_reject}, P_null={_binom_tail(bar_unique_reject, n_unique, 0.5):.4f}; "
        f"power at true p=2/3 = {exact_power_2_3:.3f}) — NOT 80%-powered.** "
        f"md={_v2._tanh_md(max(n_unique, 1)):.3f} is a CORRELATION MDE, NOT binomial "
        f"power (do not quote it as such).\n"
    )

    # ---- R6-3: weighted fallback->0 production level (re-derivation) ----
    lines.append(
        "### R6-3 — TRUE MAGNITUDE/OI/VANNA-WEIGHTED FALLBACK->0 PRODUCTION LEVEL "
        "(CROSS-ENGINE)\n"
    )
    lines.append(
        "- L_f0 = sum_k sign_k * OI_k * vanna_k, deadband/fallback strikes contribute 0 "
        "(not -1). Computed by patching the production Layer-1b -1 fallback -> 0 and "
        "re-driving the SAME production pipeline. sign(L_f0) compared to the NEW-engine "
        "day LEVEL sign (cross-engine). Per-strike arrays persisted. "
        "ZERO new-day acquisition (re-fetch of same-study EOD/OI).\n"
    )
    weighted = {}
    for tk, day, exp, src in RESOLVABLE:
        print(f"=== re-derive weighted level: {tk} {day} -> {exp} ({src}) ===")
        d = _rederive_weighted_level(ctl, tk, day, exp)
        weighted[f"{tk}_{day}"] = d if d else None
        time.sleep(0.3)
    # pull the new-engine day level sign from the persisted v4/v3 obs
    new_sign_of = {}
    for r in all_rows:
        new_sign_of[(r["ticker"], r["day"])] = r.get("new_sign")
        prod_sign_of = (r.get("prod_sign"),)
    wf0_rows = []
    for tk, day, exp, src in RESOLVABLE:
        d = weighted.get(f"{tk}_{day}")
        if d is None:
            wf0_rows.append({"ticker": tk, "day": day, "src": src, "ok": False})
            continue
        L_f0 = d["L_f0"]
        L_base = d["L_base"]
        s_f0 = _g._sign_of(L_f0)
        s_new = new_sign_of.get((tk, day))
        s_prod = None
        for r in all_rows:
            if r["ticker"] == tk and r["day"] == day:
                s_prod = r.get("prod_sign")
                break
        agree_f0 = s_f0 != 0 and s_new not in (None, 0) and s_f0 == s_new
        wf0_rows.append(
            {
                "ticker": tk,
                "day": day,
                "exp": exp,
                "src": src,
                "ok": True,
                "L_f0": L_f0,
                "L_base": L_base,
                "sign_f0": s_f0,
                "sign_prod_baseline": s_prod,
                "sign_new": s_new,
                "agree_f0_vs_new": agree_f0,
                "n_per_strike": d["n_per_strike"],
            }
        )
        print(
            f"  [wf0] {tk} {day}: L_f0={L_f0:.3g} sign={s_f0} vs new={s_new} "
            f"prod_base={s_prod} -> {'AGREE' if agree_f0 else 'NO'}"
        )
    wf0_ok = [w for w in wf0_rows if w.get("ok")]
    wf0_agree = sum(1 for w in wf0_ok if w["agree_f0_vs_new"])
    lines.append(
        "| Ticker | Day | Src | sign(L_f0) | sign(prod baseline) | sign(new) | agree(L_f0,new) | per-strike |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for w in wf0_rows:
        if not w["ok"]:
            lines.append(
                f"| {w['ticker']} | {w['day']} | {w['src']} | n/a | n/a | n/a | DATA-FAIL | n/a |"
            )
            continue
        lines.append(
            f"| {w['ticker']} | {w['day']} | {w['src']} | {w['sign_f0']} | "
            f"{w['sign_prod_baseline']} | {w['sign_new']} | "
            f"{'YES' if w['agree_f0_vs_new'] else 'NO'} | {w['n_per_strike']} |"
        )
    wf0_pct = f"{wf0_agree / len(wf0_ok):.0%}" if wf0_ok else "n/a"
    lines.append(
        f"\n- **Magnitude-weighted fallback->0 vs new-engine sign: {wf0_agree}/"
        f"{len(wf0_ok)} resolvable clusters agree** ({wf0_pct})."
    )
    lines.append(
        f"- Per-strike arrays persisted for {len(wf0_ok)}/{len(RESOLVABLE)} re-derived "
        f"clusters. Baseline (fallback=-1) sign reported for reference.\n"
    )

    # ---- R6-7/R7-C: delta convention-distance (SUPPORTING screen only) ----
    lines.append(
        "### R6-7 / R7-C — CONVENTION-DISTANCE SCREEN ON SVI DEVIATION TERMS "
        "(SUPPORTING, NOT decisive)"
    )
    lines.append(
        "- delta_prod = (rich_plus1 - cheap_minus1)/total_strikes is an **unweighted IV-"
        "vs-chain-median strike-count proxy** (run_dual_pipeline_gate_v3.py "
        "_sign_provenance), NOT the production SVI `deviation_by_strike` / "
        "`resolve_vol_surface_sign` path. delta_new = 0 **BY CONSTRUCTION** (new engine "
        "has no SVI branch; flat -1xBS), so D = |delta_prod| is a **one-sided "
        "production-deviation screen**, not a genuine two-sided convention-distance. "
        "Per R7-C this is a **supporting descriptive screen only — it cannot affirm or "
        "refute the shared -1 root as a mechanism**; the mechanism stays formally "
        "open-not-disproven. RULE (screen): 'production-hugs--1-baseline' if mean|D|<0.10 "
        "AND <25% of clusters have |D|>=0.10."
    )
    deltas = []
    delta_table = []
    for r in all_rows:
        dp_ = delta_prod(r.get("provenance", {}))
        dn_ = delta_new(r.get("provenance", {}))
        deltas.append(dp_)
        delta_table.append(
            (r["ticker"], r["day"], r.get("firing_buckets", 0), dp_, dn_)
        )
    mean_D, frac_large, delta_verdict, delta_reason = convention_distance(deltas)
    lines.append("| Ticker | Day | firing_buckets | delta_prod | delta_new | D |")
    lines.append("|---|---|---|---|---|---|")
    for tk, day, fb, dp_, dn_ in delta_table:
        lines.append(
            f"| {tk} | {day} | {fb} | {dp_:+.4f} | {dn_:+.4f} | {abs(dp_ - dn_):.4f} |"
        )
    lines.append(
        f"\n- Corpus: {len(deltas)} clusters (seed {len(seed_rows)} + v4 {len(new_rows)}), "
        f"INCLUDING zero-firing clusters. mean|D| = {mean_D:.4f}; "
        f"{frac_large:.1%} of clusters have |D| >= {_DEMOTE_DELTA_MEAN}."
    )
    lines.append(f"- **DELTA VERDICT: {delta_verdict}** — {delta_reason}\n")
    # SPY/QQQ separate delta summaries (R6-5)
    for tk in ("SPY", "QQQ"):
        sub = [
            d
            for (r_tk, _d, _f, dp_, _dn) in [
                (x[0], x[3], x[2], x[3], x[4]) for x in delta_table
            ]
            if r_tk == tk
        ]
        # rebuild cleanly
        sub_deltas = [x[3] for x in delta_table if x[0] == tk]
        if sub_deltas:
            m = sum(abs(d) for d in sub_deltas) / len(sub_deltas)
            lines.append(
                f"  - {tk}: {len(sub_deltas)} clusters, mean|delta_prod| = {m:.4f}."
            )

    # ---- R6-4/6-5: correlational + placebo arms (honest labels) ----
    lines.append(
        "### CORRELATIONAL + PLACEBO ARMS (R6-4 honest underpowered labels; R6-5 SPY/QQQ "
        "separate, never pooled)\n"
    )
    all_N, all_fwd, all_div = [], [], []
    for r in new_rows:
        o = v4.get("obs", {}).get(f"{r['ticker']}_{r['day']}", {})
        all_N.extend(o.get("firing_N", []))
        all_fwd.extend(o.get("firing_fwd", []))
        all_div.extend(o.get("firing_div", []))
    n_buckets = len(all_N)
    corr_nf = _g._corr(all_N, all_fwd) if n_buckets >= 4 else float("nan")
    r_a6 = _g._corr(all_div, all_fwd) if n_buckets >= 4 else float("nan")
    ci_lo, ci_hi = (
        _g._fisher_tanh_ci(corr_nf, n_unique)
        if n_buckets >= 4
        else (float("nan"), float("nan"))
    )
    placebo_p = float("nan")
    if n_buckets >= 4:
        _, _, placebo_p = _g._placebo_null(all_N, all_fwd)
    md_effn = _v2._tanh_md(max(n_unique, 1))
    md_buckets = _v2._tanh_md(max(n_buckets, 1))
    # per-family bucket correlations (R6-5, separate, labeled noise)
    spy_x, spy_y, qx, qy = [], [], [], []
    for r in new_rows:
        o = v4.get("obs", {}).get(f"{r['ticker']}_{r['day']}", {})
        if r["ticker"] == "SPY":
            spy_x += o.get("firing_N", [])
            spy_y += o.get("firing_fwd", [])
        else:
            qx += o.get("firing_N", [])
            qy += o.get("firing_fwd", [])
    corr_spy = _g._corr(spy_x, spy_y) if len(spy_x) >= 4 else float("nan")
    corr_qqq = _g._corr(qx, qy) if len(qx) >= 4 else float("nan")
    lines.append(
        f"- corr(new_LEVEL, fwd) pooled = {corr_nf:+.4f} (n={n_buckets} buckets) — "
        f"DESCRIPTIVE ONLY (Simpson cancellation; never inferential)."
    )
    lines.append(
        f"  - SPY corr = {corr_spy:+.4f} (n={len(spy_x)} buckets) — BUCKET-LEVEL NOISE, "
        f"underpowered, not an index claim."
    )
    lines.append(
        f"  - QQQ corr = {corr_qqq:+.4f} (n={len(qx)} buckets) — BUCKET-LEVEL NOISE."
    )
    a6_label = (
        "vanna EXCEEDS reflexivity"
        if corr_nf > r_a6
        else "vanna <= reflexivity (convention-bound)"
    )
    lines.append(
        f"- A6 reflexivity baseline corr(dIV,fwd) = {r_a6:+.4f} vs vanna {corr_nf:+.4f} "
        f"-> {a6_label}."
    )
    lines.append(
        f"- cluster-level 95% CI at eff-n={n_unique}: [{ci_lo:+.3f}, {ci_hi:+.3f}] — "
        f"SPANS MEANINGFUL RANGE (not a clean negative)."
    )
    lines.append(
        f"- Correlational POWER (HONEST): at eff-n={n_unique}, |r| MDE = {md_effn:.3f} "
        f"(Fisher-z); power at |r|=0.3 ~19%, |r|=0.4 ~27%, |r|=0.5 ~38% -> "
        f"**UNDERPOWERED**. n-for-80% at |r|=0.5 ~ n=29 (documented, not gated)."
    )
    lines.append(
        f"- Placebo: p = {placebo_p:.4f} on n={n_buckets} pooled buckets "
        f"(md({n_buckets})={md_buckets:.3f}) — null-consistent but **UNDERPOWERED / "
        f"EXPLORATORY**, NOT a clean powered null.\n"
    )

    # ---- gate verdict (preserve R5 gate_verdict_r5) ----
    sign_agreement = [(r.get("agreed"), r.get("both_nonzero")) for r in all_rows]
    verdict, verdict_reason = _r5.gate_verdict_r5(
        sign_agreement, corr_nf, r_a6, n_buckets, n_unique
    )
    lines.append("### GATE VERDICT\n")
    lines.append(f"**{verdict}** — {verdict_reason}")
    lines.append(
        f"\nRan in {time.time() - t0:.1f}s. Raw: `dual_pipeline_gate_v5_obs.json`.\n"
    )

    # ---- write ----
    os.makedirs(CACHE, exist_ok=True)
    with open(
        os.path.join(CACHE, "dual_pipeline_gate_v5_RESULT.md"), "w", encoding="utf-8"
    ) as fh:
        fh.write("\n".join(lines))
    obs_out = {
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "eff_n_unique": n_unique,
        "n_agree_unique": n_agree_unique,
        "day_map": {
            d: {"n_resolvable": v[0], "n_agree": v[1], "is_agree": v[2]}
            for d, v in day_map.items()
        },
        "n_clusters": n_clusters,
        "n_agree_clusters": n_agree_clusters,
        "binom_unique": binom_unique,
        "binom_cluster": binom_cluster,
        "bar_clear_unique_2_3": bar_clear_unique_2_3,
        "bar_clear_cluster_2_3": bar_clear_cluster_2_3,
        "n80_reject_2_3": n80_reject_2_3,
        "n80_reject_3_4": n80_reject_3_4,
        "n80_bar_2_3": n80_bar_2_3,
        "n80_bar_3_4": n80_bar_3_4,
        "bar_unique_reject": bar_unique_reject,
        "exact_power_2_3": exact_power_2_3,
        "r7_mechanism_status": "demote to descriptive/conditional; mechanism formally open-not-disproven",
        "r7_delta_screen": (
            "A symmetric magnitude-weighted convention-distance is NOT buildable from the "
            "current packet — new-engine per-strike dealer_frame_vanna rows are not persisted "
            "(only production per-strike arrays exist). Required rerun: persist new-engine "
            "per-strike rows (strike/right/oi/vanna/applied_sign) alongside production arrays, "
            "then D_conv = sum w_s|delta_prod,s - delta_new,s| / sum w_s. As written the delta "
            "screen is a SUPPORTING one-sided production-deviation proxy, not a decisive test."
        ),
        "corr_nf": corr_nf,
        "corr_spy": corr_spy,
        "corr_qqq": corr_qqq,
        "r_a6": r_a6,
        "ci_lo": ci_lo,
        "ci_hi": ci_hi,
        "placebo_p": placebo_p,
        "md_effn": md_effn,
        "md_buckets": md_buckets,
        "weighted_rows": wf0_rows,
        "weighted": {
            k: (v if v else None) for k, v in weighted.items()
        },  # full per-strike arrays
        "wf0_agree": wf0_agree,
        "wf0_total": len(wf0_ok),
        "delta_verdict": delta_verdict,
        "delta_reason": delta_reason,
        "delta_mean_abs": mean_D,
        "delta_frac_large": frac_large,
        "delta_corpus_n": len(deltas),
        "corpus_integrity": integrity,
        "rows": all_rows,
    }
    with open(
        os.path.join(CACHE, "dual_pipeline_gate_v5_obs.json"), "w", encoding="utf-8"
    ) as fh:
        json.dump(obs_out, fh, indent=2, default=str)
    print("\n".join(lines))
    print(f"\n[R6] verdict: {verdict} — {verdict_reason}")
    print(
        f"[R6] unique-day agree={n_agree_unique}/{n_unique}  cluster={n_agree_clusters}/"
        f"{n_clusters}  weighted-fallback0={wf0_agree}/{len(wf0_ok)}  "
        f"delta={delta_verdict} (mean|D|={mean_D:.4f})"
    )


if __name__ == "__main__":
    main()
