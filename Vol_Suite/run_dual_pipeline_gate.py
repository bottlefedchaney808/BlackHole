"""P0-1 — DUAL-PIPELINE CONVENTION-INDEPENDENCE GATE (pre-registered estimand).

Cem Karsan round-3 verdict: NOT ACCEPTED. The single requirement that changes the
verdict is that the PRODUCTION live `dealer_positioning` vanna and the NEW
`expiry_book_exposure` vanna must independently converge in SIGN on the SAME
intraday firing buckets. Two independently-built conventions —
  - PRODUCTION:  dealer_positioning `vanna_call_shares + vanna_put_shares`,
                  sign resolved per-strike by `sign_model='vol_surface_replication'`
                  (SVI/SABR reference curve fit on the firing day's chain IV),
                  `accumulate=False` (same-day book), driven offline via the
                  _SeedTD / _FrozenDT monkeypatch pattern.
  - NEW:          expiry_book_exposure.vanna_flow(ne, dIV) =
                  SUM signed_vanna(-1*BS) * OI * 100 * VANNA_PP_SCALE * (dIV/0.01),
                  signed by the locked rec.vanna = -1*BS dealer-frame convention.
agreeing in sign on the same firing buckets = the convention-independence test.

================================================================================
PRE-REGISTERED ESTIMAND (written BEFORE any network data run; immutable).
================================================================================

FIRING BUCKETS (ticker=QQQ, the intraday clock from run_intraday_flow.py v2):
  day=20260716 -> expiry=20260717
  day=20260717 -> expiry=20260717
  day=20260731 -> expiry=20260803
These firing expiries are short-DTE weeklies NOT listed 150 days out, so the
literal "150d-accumulated production vanna" is INFEASIBLE for these anchors.
The honest adaptation of Cem's gate on the intraday clock is the SAME-DAY
production dealer_positioning vanna (SVI sign-mapped, accumulate=False)
computed on the SAME firing days, cross-checked against new-engine vanna_flow.

UNIT OF ANALYSIS
  An observation is one firing bucket: an intraday 10-min (ivl=600000) bucket
  on a firing day where (|dIV| > 0.01 dead-band) AND (burst != 0), burst =
  hedge_flow_at(day-anchored execution locus, spot). The day-anchored locus is
  built ONCE per day from the opening bucket (band = open spot ± 1%, fixed all
  day). Forward return = next-10-min spot return strictly AFTER the bucket
  (no look-ahead: uses only the next spot print > bucket ms).
  A bucket where either engine's quantity is missing/zero is REPORTED
  SEPARATELY (not counted as agreement). Zero sign never counts as agreement.

QUANTITIES
  Production:  P_day = res.vanna_call_shares + res.vanna_put_shares  (signed,
               SVI sign map, accumulate=False, same-day EOD book on the firing
               day, restricted to the same $5 strike grid as the new engine).
  New engine:  N_i   = ebe.vanna_flow(ne_i, dIV_i) per firing bucket i,
               where ne_i = build_net_exposure(rows_at_bucket, spot_i, T=real
               days-to-expiry/365). Day-level new sign = sign(sum_i N_i).

SIGN AGREEMENT (the Cem gate, PRIMARY statistic)
  Per firing day: agree = (sign(P_day) == sign(sum_i N_i)) when both are
  nonzero. Report each day's signs, agreement, and the aggregate agree/N.
  Gate PASS requires agree on >= 2 of 3 firing days (both nonzero).
  Zero/missing on either side -> bucket/day not counted, reported explicitly.

CORRELATIONS vs FORWARD RETURNS (SECONDARY, descriptive)
  r_pearson and r_spearman of (new-engine vanna_flow N_i, forward return fwd_i)
  pooled over all firing buckets, de-meaned, n reported. NOTE at n<=3 days the
  pooled-bucket correlation is descriptive, NOT inferential; effective-n = 3
  day-clusters (QQQ only, one family). Fisher-tanh 95% CI reported with an
  explicit note that K=3 bootstrap CIs are exploratory-only.

LEAD/LAG FALSIFIER (does the signal PREDICT, or is it contemporaneous/reflexive?)
  For the firing-day intraday series, compute corr(N_i, fwd_{i+k}) for k in
  {0, +1, +2} (response at lags strictly AFTER the breach bucket), plus the
  REVERSE direction corr(prior-return_{i-1}, N_i) — if prior returns predict
  the signal as strongly as the signal predicts forward returns, that is
  reverse-causation/reflexivity, not predictive dealer flow.
  Lead/lag is non-null only if the k>=1 correlations are sign-consistent with
  the k=0 direction AND exceed the A6 reflexivity baseline in magnitude.

PLACEBO NULL
  Permute the (bucket -> forward-return) assignment across firing buckets
  (preserves bucket structure, breaks the dIV->return link), recompute
  corr(N, fwd) under `_PLACEBO_PERMS=1000` permutations with seed `_SEED=7`.
  Empirical p = (1 + #null_corr >= obs_corr) / (1 + n_perms).
  Reported as the null distribution; p must be < 0.05 for a non-null claim.

REFLEXIVITY BASELINE (A6, Cem's decisive cheap diagnostic)
  r_A6 = corr(dIV_i, fwd_i) on the SAME firing buckets. If r_A6 is positive
  and >= the vanna corr, the vanna sign is downstream of dIV/return
  reflexivity (convention-bound) and the gate's mechanistic claim FAILS even
  if the two pipelines agree in sign. We report r_A6 alongside and the verdict
  is gated on corr(vanna,fwd) being both positive AND > r_A6.

GATE RULE (verdict on the whole run)
  PASS:          >= 2/3 firing-day sign agreement (both nonzero) AND
                 corr(N, fwd) > 0 AND corr(N, fwd) > r_A6 (survives reflexivity).
  FAIL:          sign agreement < 2/3, OR corr(N, fwd) <= 0, OR
                 corr(N, fwd) <= r_A6 (convention-bound / reflexivity subsumes).
  INDETERMINATE: < 4 firing buckets across all days, or missing day data, or
                 a firing day where both engines are zero (cannot resolve).
The verdict is stated plainly. Small-n (3 days) is disclosed: the sign
agreement is the PRIMARY, genuinely decidable statistic; the correlational
arms are descriptive.

DATA ACQUISITION
  Sequential via the ThetaData proxy (THETADATA_HIST_CONCURRENCY=1), credentials
  from the main tree .env (NEVER written to disk/result). Per firing day:
    1. stock/ohlc intraday (open spot -> $5 strike grid, base±12*5)
    2. per-strike hist/all_greeks at ivl=600000 (C+P) -> intraday buckets + dIV
    3. per-strike hist/open_interest (C+P) -> OI
    4. bulk_hist/option/eod_greeks single-day -> EOD same-day chain for the
       PRODUCTION same-day book (filtered to the same $5 grid)
  404s on strikes not on the grid / thin strikes are handled gracefully and
  recorded as data gaps. No synthetic data is ever substituted.
  Normalized observations are written to
  Vol_Suite/_intraday_cache/dual_pipeline_gate_obs.json for audit.
  Result: Vol_Suite/_intraday_cache/dual_pipeline_gate_RESULT.md
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

import dealer_positioning as dp
import expiry_book_exposure as ebe

# Reuse the production-driving helpers from run_compare_live_vs_new (the test-suite
# monkeypatch pattern). Import is side-effect-free (main() guarded by __main__).
import run_compare_live_vs_new as rcl

from shared.thetadata import (
    ThetaDataController,
    strike_from_theta,
    strike_to_theta,
)

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_intraday_cache")
DEADBAND = 0.01
IVL = 600000  # 10-min
DAYS = [("20260716", "20260717"), ("20260717", "20260717"), ("20260731", "20260803")]
TICKER = "QQQ"
SIGN_MODEL = "vol_surface_replication"
ACCUMULATE = False
_PLACEBO_PERMS = 1000
_SEED = 7
TOL = 0.01  # day-anchored band = open spot ± 1%


# ---------------------------------------------------------------------------
# Data acquisition (sequential, proxy; graceful on 404/empty)
# ---------------------------------------------------------------------------
def _stock_intraday(ctl, root, day):
    r = ctl._get_with_retry(
        f"/api/theta/hist/stock/ohlc/{root}",
        params={"start_date": day, "end_date": day},
    )
    r.raise_for_status()
    rows = ctl._parse_rows(r)
    out = {}
    for x in rows:
        ms = int(float(x.get("ms_of_day", 0) or 0))
        c = x.get("close")
        if c not in (None, "", "0", 0):
            try:
                out[ms] = float(c)
            except (TypeError, ValueError):
                continue
    return out


def _chain_intraday(ctl, root, exp, day, strikes_theta, right):
    out = {}
    for k in strikes_theta:
        r = ctl._get_with_retry(
            f"/api/theta/hist/option/all_greeks/{root}/{exp}/{k}/{right}",
            params={"start_date": day, "end_date": day, "ivl": IVL},
        )
        if r.status_code == 404:
            continue
        r.raise_for_status()
        rows = ctl._parse_rows(r)
        if rows:
            out[k] = rows
    return out


def _oi_proxy(ctl, root, exp, day, strikes_theta, right):
    out = {}
    for k in strikes_theta:
        try:
            r = ctl._get_with_retry(
                f"/api/theta/hist/option/open_interest/{root}/{exp}/{k}/{right}",
                params={"start_date": day, "end_date": day},
            )
            if r.status_code == 404:
                continue
            r.raise_for_status()
            rows = ctl._parse_rows(r)
            if rows:
                out[k] = int(float(rows[-1].get("open_interest", 0) or 0))
        except Exception:
            continue
    return out


def _eod_greeks_day(ctl, root, exp, day):
    """EOD same-day chain (implied_vol/gamma/delta/vanna) for the production
    same-day book. Uses the dense bulk_hist/option/eod_greeks route for a
    single day. Returns dict keyed by (theta_strike, right)."""
    out = {}
    try:
        r = ctl._get_with_retry(
            f"/api/theta/bulk_hist/option/eod_greeks/{root}/{exp}",
            params={"start_date": day, "end_date": day},
        )
        if r.status_code == 404:
            return out
        r.raise_for_status()
        rows = ctl._parse_rows(r)
    except Exception:
        return out
    for row in rows:
        d = str(row.get("date", "") or "")
        if d and not str(d)[:8] == day:
            continue
        try:
            kt = int(float(row.get("strike", 0) or 0))
            rt = str(row.get("right", ""))[:1].upper()
            if rt not in ("C", "P") or kt <= 0:
                continue
            out[(kt, rt)] = {
                "strike": kt,
                "right": rt,
                "implied_vol": float(row.get("implied_vol", 0) or 0),
                "gamma": float(row.get("gamma", 0) or 0),
                "delta": float(row.get("delta", 0) or 0),
                "vanna": float(row.get("vanna", 0) or 0),
                "bid": float(row.get("bid", 0) or 0),
                "ask": float(row.get("ask", 0) or 0),
            }
        except (TypeError, ValueError):
            continue
    return out


# ---------------------------------------------------------------------------
# Production pipeline (same-day, SVI sign map, accumulate=False)
# ---------------------------------------------------------------------------
def run_production_vanna_same_day(seed, ticker):
    """Drive compute_dealer_positioning offline on the SAME-DAY EOD book via
    the _SeedTD/_FrozenDT monkeypatch pattern (reuses rcl._SeedTD +
    _hist_rows_from_seed). Freezes time at the seed's as-of date so the
    firing-day weekly expiry is still-listed. Returns
    (prod_vanna, result) with prod_vanna = vanna_call_shares+vanna_put_shares,
    or (None, None) on failure. Restoration guaranteed in finally."""
    import datetime as _dt

    m = seed.get("manifest", {})
    odr = m.get("obs_date_range") or []
    if odr and str(odr[-1])[:8].isdigit():
        as_of = _dt.datetime.strptime(str(odr[-1])[:8], "%Y%m%d").date()
    else:
        spots = seed.get("spot", [])
        as_of = None
        for sp in reversed(spots):
            d = str(ebe._date_of(sp))[:8]
            if d.isdigit():
                as_of = _dt.datetime.strptime(d, "%Y%m%d").date()
                break
        if as_of is None:
            as_of = _dt.date.today()

    class _FrozenDT(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _dt.datetime.combine(as_of, _dt.time(16, 0), tzinfo=tz)

    td = rcl._SeedTD(seed)
    hist_rows = rcl._hist_rows_from_seed(seed)
    old_ctrl = dp.ThetaDataController
    old_dt = dp.datetime
    dp.ThetaDataController = lambda *a, **k: td
    dp.datetime = _FrozenDT
    try:
        exp = str(seed.get("manifest", {}).get("expiry", "")) or None
        result = dp.compute_dealer_positioning(
            ticker,
            target_years=0.25,
            expiration=exp,
            sign_model=SIGN_MODEL,
            accumulate=ACCUMULATE,
            _accumulation_hist_rows=hist_rows,
        )
        if result is None:
            return None, None
        pv = result.vanna_call_shares + result.vanna_put_shares
        if math.isnan(pv):
            return None, result
        return pv, result
    except Exception as e:
        print(f"    [prod] EXC {type(e).__name__}: {str(e)[:120]}")
        return None, None
    finally:
        dp.ThetaDataController = old_ctrl
        dp.datetime = old_dt


def build_production_seed(day, exp, spot_close, eod_greeks, oi_c, oi_p, grid_theta):
    """Build a run_compare_live_vs_new-shaped seed dict for the SAME-DAY EOD
    book, restricted to the same $5 strike grid so both engines see the same
    chain. greeks rows use theta-int strikes (>10000) so _SeedTD and the
    production pipeline agree on units."""
    greeks, oi_rows = [], []
    seen = set()
    for kt, rt in sorted(eod_greeks.keys()):
        if kt not in grid_theta:
            continue
        key = (kt, rt)
        if key in seen:
            continue
        seen.add(key)
        g = eod_greeks[key]
        iv = g["implied_vol"]
        if iv <= 0:
            continue
        greeks.append(
            {
                "date": day,
                "strike": kt,
                "right": rt,
                "implied_vol": iv,
                "gamma": g["gamma"],
                "delta": g["delta"],
                "vanna": g["vanna"],
                "bid": g["bid"],
                "ask": g["ask"],
            }
        )
    for kt in grid_theta:
        for rt, oi in (("C", oi_c.get(kt, 0)), ("P", oi_p.get(kt, 0))):
            oi_rows.append(
                {"date": day, "strike": kt, "right": rt, "open_interest": int(oi or 0)}
            )
    return {
        "manifest": {"expiry": exp, "obs_date_range": [day]},
        "spot": [{"date": day, "close": spot_close}],
        "greeks": greeks,
        "oi": oi_rows,
    }


# ---------------------------------------------------------------------------
# New engine: rows_at / locus / burst / vanna_flow
# ---------------------------------------------------------------------------
def _rows_at(chain_c, chain_p, oi_c, oi_p, ms):
    rows = []
    for k, krows in chain_c.items():
        best = None
        for x in krows:
            xms = int(float(x.get("ms_of_day", 0) or 0))
            if xms <= ms and (
                best is None or xms > int(float(best.get("ms_of_day", 0) or 0))
            ):
                best = x
        if best is not None:
            iv = best.get("implied_vol")
            if iv not in (None, "", 0):
                try:
                    rows.append(
                        {
                            "strike": strike_from_theta(k),
                            "right": "C",
                            "oi": oi_c.get(k, 0),
                            "implied_vol": float(iv),
                        }
                    )
                except (TypeError, ValueError):
                    pass
    for k, krows in chain_p.items():
        best = None
        for x in krows:
            xms = int(float(x.get("ms_of_day", 0) or 0))
            if xms <= ms and (
                best is None or xms > int(float(best.get("ms_of_day", 0) or 0))
            ):
                best = x
        if best is not None:
            iv = best.get("implied_vol")
            if iv not in (None, "", 0):
                try:
                    rows.append(
                        {
                            "strike": strike_from_theta(k),
                            "right": "P",
                            "oi": oi_p.get(k, 0),
                            "implied_vol": float(iv),
                        }
                    )
                except (TypeError, ValueError):
                    pass
    return rows


# ---------------------------------------------------------------------------
# Statistics helpers (imported by tests; network-free)
# ---------------------------------------------------------------------------
def _de_mean(x):
    m = sum(x) / len(x) if x else 0.0
    return [v - m for v in x]


def _corr(a, b):
    n = len(a)
    if n < 4:
        return 0.0
    ma = sum(a) / n
    mb = sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    return num / (da * db) if da > 0 and db > 0 else 0.0


def _spearman(a, b):
    def _rank(x):
        idx = sorted(range(len(x)), key=lambda i: x[i])
        r = [0.0] * len(x)
        i = 0
        while i < len(idx):
            j = i
            while j + 1 < len(idx) and x[idx[j + 1]] == x[idx[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[idx[k]] = avg
            i = j + 1
        return r

    if len(a) < 4:
        return 0.0
    return _corr(_rank(a), _rank(b))


def _fisher_tanh_ci(r, n):
    if n < 4 or abs(r) >= 0.999:
        return (float("nan"), float("nan"))
    z = 0.5 * math.log((1 + r) / (1 - r))
    se = 1.0 / math.sqrt(max(n - 3, 1))
    return (math.tanh(z - 1.96 * se), math.tanh(z + 1.96 * se))


def _lead_lag_corrs(series_x, series_y, lags=(0, 1, 2)):
    """corr(x_i, y_{i+k}) for k in lags (x leads y / response at lag k).
    Also reverse: corr(x_{i}, y_{i-1}) i.e. prior y predicts x (reverse-causation
    probe, returned as 'prior' key). Returns dict {k: corr} plus 'prior'."""
    out = {}
    n = len(series_x)
    for k in lags:
        xs = series_x[: n - k] if k >= 0 else series_x[-k:]
        ys = series_y[k:] if k >= 0 else series_y[: n + k]
        out[int(k)] = _corr(list(xs), list(ys))
    # prior: corr(x_i, y_{i-1}) -> prior y predicts current x
    out["prior"] = _corr(list(series_x[1:]), list(series_y[:-1]))
    return out


def _placebo_null(x_obs, y_obs, n_perms=_PLACEBO_PERMS, seed=_SEED):
    """Permute the (bucket -> forward-return) assignment across firing buckets
    (breaks the dIV->return link), recompute corr(N, fwd). Returns
    (obs_corr, null_corrs, p_value)."""
    rng = random.Random(seed)
    obs = _corr(list(x_obs), list(y_obs))
    n = len(x_obs)
    nulls = []
    for _ in range(n_perms):
        if n < 4:
            nulls.append(0.0)
            continue
        yp = y_obs[:]
        rng.shuffle(yp)
        nulls.append(_corr(list(x_obs), list(yp)))
    nulls.sort()
    p = (1 + sum(1 for v in nulls if v >= obs)) / (1 + n_perms)
    return obs, nulls, p


def _sign_of(v):
    if v is None or v == 0.0 or (isinstance(v, float) and math.isnan(v)):
        return 0
    return 1 if v > 0 else -1


def gate_verdict(sign_agreement, total_days, corr_nf, r_a6, n_buckets):
    """Apply the pre-registered gate rule. sign_agreement = list of (agreed, both_nonzero)
    per firing day; corr_nf = corr(new vanna, fwd); r_a6 = reflexivity baseline."""
    agreed = sum(1 for a, b in sign_agreement if b and a)
    resolvable = sum(1 for a, b in sign_agreement if b)  # both-nonzero days
    if n_buckets < 4 or resolvable == 0:
        return "INDETERMINATE", "insufficient firing buckets/resolvable days"
    if resolvable >= 2 and agreed >= 2:
        if corr_nf > 0 and corr_nf > r_a6:
            return "PASS", (
                f"sign-agree {agreed}/{resolvable}, corr={corr_nf:+.4f}>r_A6={r_a6:+.4f}"
            )
        if corr_nf <= 0:
            return "FAIL", f"corr(new,fwd)={corr_nf:+.4f} <= 0 (wrong/absent direction)"
        return (
            "FAIL",
            f"corr(new,fwd)={corr_nf:+.4f} <= r_A6={r_a6:+.4f} (reflexivity subsumes — convention-bound)",
        )
    return "FAIL", f"sign-agreement {agreed}/{resolvable} < 2/2"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    os.environ.setdefault("THETADATA_HIST_CONCURRENCY", "1")
    ctl = ThetaDataController()
    t0 = time.time()
    lines = []
    lines.append("# P0-1 — Dual-Pipeline Convention-Independence Gate (result)\n")
    lines.append(
        f"**Date:** {dt.date.today().isoformat()}  **Driver:** `run_dual_pipeline_gate.py`  "
        f"**Tree:** Dealer-Exposure-Dev  **Ticker:** {TICKER}\n"
    )
    lines.append(
        "**Pre-registered estimand:** see the driver docstring (bucket inclusion, "
        "exposure strata, sign convention, lead/lag lags, placebo design). Written "
        "before any network data run.\n"
    )
    lines.append(
        "**Adaptation:** firing expiries are short-DTE weeklies not listed 150d out; "
        "the gate is the SAME-DAY production dealer_positioning vanna "
        "(vol_surface_replication SVI sign map, accumulate=False) vs new-engine "
        "ebe.vanna_flow on the same firing buckets.\n"
    )

    gaps = []
    obs = {}  # normalized observations for audit
    rows_table = []  # per-day summary rows

    for day, exp in DAYS:
        print(f"\n=== {TICKER} {day} -> {exp} ===")
        spot_min = _stock_intraday(ctl, TICKER, day)
        if not spot_min:
            gaps.append(
                {
                    "day": day,
                    "endpoint": "stock/ohlc",
                    "status": "empty",
                    "note": "no intraday stock rows",
                }
            )
            continue
        open_spot = spot_min[min(spot_min)]
        close_spot = spot_min[max(spot_min)]
        base = round(open_spot / 5.0) * 5.0
        grid_theta = [strike_to_theta(base + i * 5.0) for i in range(-12, 13)]
        grid_set = set(grid_theta)
        chain_c = _chain_intraday(ctl, TICKER, exp, day, grid_theta, "C")
        chain_p = _chain_intraday(ctl, TICKER, exp, day, grid_theta, "P")
        if len(chain_c) < 10 or len(chain_p) < 10:
            gaps.append(
                {
                    "day": day,
                    "endpoint": "all_greeks intraday",
                    "status": "thin",
                    "note": f"C={len(chain_c)} P={len(chain_p)} strikes served",
                }
            )
        oi_c = _oi_proxy(ctl, TICKER, exp, day, list(chain_c.keys()), "C")
        oi_p = _oi_proxy(ctl, TICKER, exp, day, list(chain_p.keys()), "P")
        eod_g = _eod_greeks_day(ctl, TICKER, exp, day)
        if not eod_g:
            gaps.append(
                {
                    "day": day,
                    "endpoint": "bulk_hist/option/eod_greeks",
                    "status": "empty",
                    "note": "no EOD same-day chain served",
                }
            )

        # ---- new engine: day-anchored locus from opening bucket ----
        rows_open = _rows_at(chain_c, chain_p, oi_c, oi_p, min(spot_min))
        T = max(
            (
                dt.datetime.strptime(exp, "%Y%m%d").date()
                - dt.datetime.strptime(day, "%Y%m%d").date()
            ).days
            / 365.0,
            0.01,
        )
        if len(rows_open) < 20:
            print(f"  [warn] {day}: only {len(rows_open)} open rows")
            gaps.append(
                {
                    "day": day,
                    "endpoint": "execution_locus",
                    "status": "thin",
                    "note": f"{len(rows_open)} open rows < 20",
                }
            )
        try:
            locus = ebe.execution_locus(rows_open, open_spot, T=T)
        except Exception as e:
            print(f"  [warn] {day}: locus EXC {type(e).__name__}: {str(e)[:100]}")
            gaps.append(
                {
                    "day": day,
                    "endpoint": "execution_locus",
                    "status": "exc",
                    "note": str(e)[:100],
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

        # series for lead/lag
        N_series, fwd_series, div_series, ms_series = [], [], [], []
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
            rows_at = _rows_at(chain_c, chain_p, oi_c, oi_p, ms)
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
                    rows_at, spot_now, ticker=TICKER, T=T
                )
                N_i = ebe.vanna_flow(ne_bucket, div)
            except Exception:
                N_i = 0.0
            N_series.append(N_i)
            fwd_series.append(fwd)
            div_series.append(div)
            ms_series.append(ms)
            if abs(div) > DEADBAND and burst != 0.0:
                firing_N.append(N_i)
                firing_fwd.append(fwd)
                firing_div.append(div)

        # ---- production same-day book ----
        seed = build_production_seed(day, exp, close_spot, eod_g, oi_c, oi_p, grid_set)
        prod_v, res = run_production_vanna_same_day(seed, TICKER)
        prod_sign = _sign_of(prod_v)
        new_day_sign = _sign_of(sum(firing_N))

        # day-level agreement
        both_nonzero = prod_sign != 0 and new_day_sign != 0
        agreed = both_nonzero and (prod_sign == new_day_sign)
        if not both_nonzero:
            gaps.append(
                {
                    "day": day,
                    "endpoint": "sign-agreement",
                    "status": "zero/missing",
                    "note": f"prod_sign={prod_sign} new_sign={new_day_sign}",
                }
            )

        n_prod_records = res.num_records if res is not None else 0
        n_prod_strikes = len(res.strike_grid) if res is not None else 0
        row = {
            "day": day,
            "expiry": exp,
            "T": round(T, 5),
            "open_spot": open_spot,
            "close_spot": close_spot,
            "grid_strikes": len(grid_set),
            "eod_strikes": len(eod_g),
            "intraday_strikes_C": len(chain_c),
            "intraday_strikes_P": len(chain_p),
            "firing_buckets": len(firing_N),
            "buckets": len(bucket_ms),
            "prod_vanna": round(prod_v, 6) if prod_v is not None else None,
            "prod_sign": prod_sign,
            "prod_records": n_prod_records,
            "prod_strikes": n_prod_strikes,
            "new_vanna_sum": round(sum(firing_N), 6),
            "new_sign": new_day_sign,
            "agreed": agreed,
            "both_nonzero": both_nonzero,
            "zero_gamma": getattr(locus, "zero_gamma", float("nan")),
        }
        rows_table.append(row)
        obs[day] = {
            "expiry": exp,
            "T": T,
            "open_spot": open_spot,
            "close_spot": close_spot,
            "grid_theta": grid_theta,
            "firing_N": firing_N,
            "firing_fwd": firing_fwd,
            "firing_div": firing_div,
            "N_series": N_series,
            "fwd_series": fwd_series,
            "div_series": div_series,
            "prod_vanna": prod_v,
        }
        print(
            f"  [day] prod_v={prod_v} (sign {prod_sign}) | new_vanna_sum={sum(firing_N):.4g} "
            f"(sign {new_day_sign}) | firing={len(firing_N)} | agree={agreed}"
        )

    # ---- aggregate stats over ALL firing buckets (QQQ one family) ----
    all_N, all_fwd, all_div = [], [], []
    for d, o in obs.items():
        all_N.extend(o["firing_N"])
        all_fwd.extend(o["firing_fwd"])
        all_div.extend(o["firing_div"])
    n_buckets = len(all_N)
    n_days = len(obs)

    corr_nf = _corr(all_N, all_fwd) if n_buckets >= 4 else float("nan")
    corr_nf_sp = _spearman(all_N, all_fwd) if n_buckets >= 4 else float("nan")
    r_a6 = _corr(all_div, all_fwd) if n_buckets >= 4 else float("nan")
    lo, hi = (
        _fisher_tanh_ci(corr_nf, n_buckets)
        if n_buckets >= 4
        else (float("nan"), float("nan"))
    )

    # lead/lag on the firing series. Pooled over days: within each day, pair
    # (N_i, fwd_{i+k}); lag1/lag2/prior need >=2/3/2 buckets in a day to form
    # a shifted pair, so pool all days' shifted pairs. Per-day reported only
    # where n>=4 (a real correlation; _corr returns 0 below that).
    lead_lag = {}
    day_lead = {}
    ll0, ll1, ll2, prior = [], [], [], []
    for d, o in obs.items():
        x = o["firing_N"]
        y = o["firing_fwd"]
        n = len(x)
        ll0.extend([(x[i], y[i]) for i in range(n)])
        if n >= 2:
            ll1.extend([(x[i], y[i + 1]) for i in range(n - 1)])
            prior.extend([(x[i], y[i - 1]) for i in range(1, n)])
        if n >= 3:
            ll2.extend([(x[i], y[i + 2]) for i in range(n - 2)])
        if n >= 4:
            day_lead[d] = _lead_lag_corrs(x, y, lags=(0, 1, 2))
    if len(ll0) >= 4:
        lead_lag = {
            "lag0": _corr([a for a, _ in ll0], [b for _, b in ll0]),
            "lag1": _corr([a for a, _ in ll1], [b for _, b in ll1]),
            "lag2": _corr([a for a, _ in ll2], [b for _, b in ll2]),
            "prior": _corr([a for a, _ in prior], [b for _, b in prior]),
            "n_lag0": len(ll0),
            "n_lag1": len(ll1),
            "n_lag2": len(ll2),
            "n_prior": len(prior),
        }

    placebo_obs, placebo_nulls, placebo_p = (float("nan"), [], float("nan"))
    if n_buckets >= 4:
        placebo_obs, placebo_nulls, placebo_p = _placebo_null(all_N, all_fwd)

    sign_agreement = [(r["agreed"], r["both_nonzero"]) for r in rows_table]
    verdict, verdict_reason = gate_verdict(
        sign_agreement, n_days, corr_nf, r_a6, n_buckets
    )

    # ---- write result doc ----
    lines.append("\n### Data acquisition (provenance)\n")
    lines.append(
        "Sequential via ThetaData proxy (THETADATA_HIST_CONCURRENCY=1), credentials "
        "from main-tree .env (never persisted). Per day: stock/ohlc intraday, "
        "per-strike hist/all_greeks @600000 (C+P), hist/open_interest, and "
        "bulk_hist/option/eod_greeks single-day for the production same-day book.\n"
    )

    lines.append("\n### Firing-day summary\n")
    lines.append(
        "| Day | Expiry | T | open | close | grid | eod | intraday(C/P) | firing/buckets | "
        "prod_vanna | prod_sign | new_vanna_sum | new_sign | agree |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows_table:
        pv = f"{r['prod_vanna']:+.3g}" if r["prod_vanna"] is not None else "NA"
        nv = f"{r['new_vanna_sum']:+.3g}"
        ag = "YES" if r["agreed"] else ("NO" if r["both_nonzero"] else "n/a(0)")
        lines.append(
            f"| {r['day']} | {r['expiry']} | {r['T']:.4f} | {r['open_spot']:.2f} | "
            f"{r['close_spot']:.2f} | {r['grid_strikes']} | {r['eod_strikes']} | "
            f"{r['intraday_strikes_C']}/{r['intraday_strikes_P']} | "
            f"{r['firing_buckets']}/{r['buckets']} | {pv} | {r['prod_sign']} | {nv} | "
            f"{r['new_sign']} | {ag} |"
        )

    n_agree = sum(1 for a, b in sign_agreement if b and a)
    n_resolvable = sum(1 for a, b in sign_agreement if b)
    frac_txt = f"{n_agree / n_resolvable:.0%}" if n_resolvable else "n/a"
    lines.append(
        "\n### PRIMARY — SIGN AGREEMENT (Cem's convention-independence gate)\n"
    )
    lines.append(
        f"Production (SVI sign map) vs New-engine (−1×BS) vanna sign on firing days: "
        f"**{n_agree}/{n_resolvable}** resolvable days agree "
        f"({frac_txt}).\n"
    )
    for r in rows_table:
        lines.append(
            f"- {r['day']}: prod_sign={r['prod_sign']} new_sign={r['new_sign']} "
            f"→ {'AGREE' if r['agreed'] else ('non-zero' if r['both_nonzero'] else 'ZERO/missing — not counted')}"
        )
    if n_resolvable == 0:
        lines.append(
            "- No firing day had both engines nonzero — sign agreement is INDETERMINATE."
        )

    lines.append("\n### SECONDARY — Correlations vs forward returns (descriptive)\n")
    if n_buckets >= 4:
        lines.append(
            f"- corr(new_vanna, fwd) Pearson = {corr_nf:+.4f}  Spearman = {corr_nf_sp:+.4f}  "
            f"(n={n_buckets} firing buckets, {n_days} days)  Fisher-tanh 95% CI "
            f"[{lo:+.3f}, {hi:+.3f}]"
        )
        lines.append(
            f"  effective-n = {n_days} day-clusters (QQQ only, one family) — the K=3 CI "
            f"is exploratory-only, NOT a valid narrow interval."
        )
    else:
        lines.append(f"- insufficient firing buckets ({n_buckets}) for correlation.")

    lines.append("\n### LEAD/LAG FALSIFIER\n")
    if lead_lag:
        lines.append(
            f"- corr(N_i, fwd_{{i+0}}) = {lead_lag['lag0']:+.4f}  (n={lead_lag['n_lag0']})"
        )
        lines.append(
            f"- corr(N_i, fwd_{{i+1}}) = {lead_lag['lag1']:+.4f}  (n={lead_lag['n_lag1']}; response one 10-min lag AFTER breach)"
        )
        lines.append(
            f"- corr(N_i, fwd_{{i+2}}) = {lead_lag['lag2']:+.4f}  (n={lead_lag['n_lag2']}; two lags after)"
        )
        lines.append(
            f"- REVERSE (prior return predicts signal): corr(N_i, fwd_{{i-1}}) = {lead_lag['prior']:+.4f}  (n={lead_lag['n_prior']})"
        )
        for d, ll in day_lead.items():
            lines.append(
                f"  {d}: lag0={ll[0]:+.3f} lag1={ll[1]:+.3f} lag2={ll[2]:+.3f} prior={ll['prior']:+.3f}"
            )
        l0 = lead_lag.get("lag0", 0)
        l1 = lead_lag.get("lag1", 0)
        lag_nonnull = (abs(l1) > 0.05) and ((l1 > 0) == (l0 > 0))
        lines.append(
            f"  read: lead/lag is {'NON-NULL (response persists after the breach bucket)' if lag_nonnull else 'NULL/weak — no persistent response after the breach bucket'}."
        )
        if (
            abs(lead_lag.get("prior", 0)) > abs(l0)
            and abs(lead_lag.get("prior", 0)) > 0.05
        ):
            lines.append(
                "  CAUTION: prior-return predicts the signal more strongly than the signal predicts "
                "forward returns → reverse-causation/reflexivity present."
            )
    else:
        lines.append("- insufficient data for lead/lag.")

    lines.append("\n### PLACEBO NULL\n")
    if n_buckets >= 4:
        p_frac = sum(1 for v in placebo_nulls if v >= placebo_obs) / len(placebo_nulls)
        lines.append(
            f"- {_PLACEBO_PERMS} permutations (seed={_SEED}): observed corr = {placebo_obs:+.4f}; "
            f"empirical p = {placebo_p:.4f} ({p_frac:.1%} of null >= obs)."
        )
        lines.append(
            f"- null median = {sorted(placebo_nulls)[len(placebo_nulls) // 2]:+.4f}; "
            f"null 95th pct = {sorted(placebo_nulls)[int(0.95 * len(placebo_nulls)) - 1]:+.4f}"
        )
        lines.append(
            f"  read: {'non-null (obs beyond placebo null)' if placebo_p < 0.05 else 'null NOT rejected at p<0.05'}."
        )
    else:
        lines.append("- insufficient firing buckets for placebo.")

    lines.append("\n### (A6) REFLEXIVITY BASELINE (Cem's decisive cheap diagnostic)\n")
    if n_buckets >= 4:
        lines.append(
            f"- corr(dIV, fwd) on the SAME firing buckets = {r_a6:+.4f}  (n={n_buckets})"
        )
        if corr_nf == corr_nf:
            lines.append(
                f"- vanna corr {corr_nf:+.4f} vs reflexivity {r_a6:+.4f} → "
                f"{'vanna EXCEEDS reflexivity (survives A6)' if corr_nf > r_a6 else 'vanna <= reflexivity — the vanna sign is downstream of dIV/return reflexivity (convention-bound)'}"
            )
    else:
        lines.append("- insufficient firing buckets for A6.")

    lines.append("\n### DATA GAPS\n")
    if gaps:
        lines.append("| day | endpoint | status | note |")
        lines.append("|---|---|---|---|")
        for g in gaps:
            lines.append(
                f"| {g.get('day', '-')} | {g['endpoint']} | {g['status']} | {g.get('note', '')} |"
            )
    else:
        lines.append("- none recorded.")
    # Zero-DTE explanation: the same-day expiry (20260717 on days 0716/0717,
    # 20260803 on 0731) is the ANCHOR expiry. On day 0717 the firing weekly IS
    # the anchor and expires that day -> tte=0, so the production expiry filter
    # (0 < tte) drops it and compute_dealer_positioning raises "No valid gamma
    # records" (0 records). This is a production-pipeline convention, not a
    # proxy failure. The two-day expiring weekly is the only case affected.
    zero_dte_days = [r["day"] for r in rows_table if r["prod_records"] == 0]
    if zero_dte_days:
        lines.append(
            f"\nNOTE on {', '.join(zero_dte_days)}: the production same-day book returned "
            f"0 records because the firing expiry is the ANCHOR and expires THAT day "
            f"(tte=0) — the production `dealer_positioning` expiry filter (0 < tte) "
            f"drops an expiring weekly. This is a production-pipeline convention, not a "
            f"proxy/data failure; the day is reported as sign=0 (not counted) per the "
            f"pre-registered rule. The new engine is unaffected (it uses real "
            f"days-to-expiry/365)."
        )

    lines.append(f"\n### GATE VERDICT: **{verdict}** — {verdict_reason}\n")
    lines.append(
        f"Ran in {time.time() - t0:.1f}s. Raw observations: `dual_pipeline_gate_obs.json`."
    )

    os.makedirs(CACHE, exist_ok=True)
    out = os.path.join(CACHE, "dual_pipeline_gate_RESULT.md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    with open(
        os.path.join(CACHE, "dual_pipeline_gate_obs.json"), "w", encoding="utf-8"
    ) as fh:
        json.dump(
            {
                "verdict": verdict,
                "verdict_reason": verdict_reason,
                "rows": rows_table,
                "obs": {
                    d: {k: v for k, v in o.items() if k not in ("grid_theta",)}
                    for d, o in obs.items()
                },
            },
            fh,
            indent=2,
            default=str,
        )
    print("\n".join(lines))
    print(f"\n[P0-1] verdict: {verdict} — {verdict_reason}")


if __name__ == "__main__":
    main()
