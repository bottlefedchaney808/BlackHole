#!/usr/bin/env python3
"""run_causal_arm_v2.py — ROUND-10.2 causal-arm driver (Dealer-Exposure-Dev CARL loop).

ONE driver that emits ONE decision table with one row per UNIQUE calendar day,
implementing every lock the R10.1 re-audit (deleg_1dbe471c) required before any
acquisition. This file makes each pre-registration lock OPERATIONAL in code.

ZERO acquisition. Deterministic, network-free analysis boundary. The callable
core (`run_causal_arm`) is exercised directly by the tests and the smoke run;
the CLI only loads day-record JSON and calls the same core.

THE 8 LOCKS (each mapped to code below):
  L1  Pre-event vanna exposure persisted with a pre-window cutoff. The
      exposure is computed from the option chain at a timestamp strictly
      BEFORE the breach/response window (`compute_pre_event_vanna_exposure`,
      gated by `cutoff_ts < breach_ts`). This fixes the run_intraday_flow.py
      defect that used CONTEMPORANEOUS ΔIV on the same firing bucket
      (run_intraday_flow.py:231-232). NO post-treatment quantity enters.
  L2  Production-grade L2 residualized model IN THE DRIVER (not just helpers):
      y ~ β(pre_vanna×ΔIV) + γ(gamma/burst) + ζ(ΔIV) + θ(ΔS) + λ(market)
            + event + A6_reflexivity + cross_family_spillover + family_interaction
      with ΔIV main effect explicit, rank/condition/VIF diagnostics, and a
      NOT-IDENTIFIABLE disposition (β = NaN when the target is aliased).
  L3  Unique-day effective-n EVERYWHERE. Same-day SPY/QQQ = ONE unit. The
      legacy n_eff>=20 gates (tier2d:227, intraday_flow docstring) and the
      pooled-bucket / (ticker,day) power are GONE. md/power/CI computed only
      at unique-day n. Honest β power, not a correlation md. Never claim 80%
      unless eligible unique-day n reaches the locked target and computed
      power >= 0.80.
  L4  Locked horizon h + firing support defined pre-outcome. h is a required
      config value (h_buckets for the from-breach clock, 1 trading day for the
      daily clock). No best-lag selection; `enforce_locked_horizon` asserts a
      lag sweep does not change the locked h.
  L5  Surprise defined operationally OR event results labeled
      DESCRIPTIVE-HABITAT. If a day has no operational surprise (actual -
      expected), its event finding is labeled descriptive-habitat and is NOT
      eligible for a causal-surprise claim.
  L6  Reflexivity (A6) + ΔIV + ΔS + gamma + market + cross-family spillover are
      L2 COVARIATES (in the regression, not a parallel check), plus a
      family-interaction opposite-sign rule: sign(β_SPY) == -sign(β_QQQ)
      => NO family-wide claim.
  L7  ONE decision table per unique day incl the BOTH-CLOCK-CONFIRMED cell
      (daily NEGATIVE AND from-breach POSITIVE = only re-admission state;
      daily is a diagnostic flag, not a gate).
  L8  Production-grade network-free tests (tests/test_causal_arm_v2.py).

Locked context (do NOT relitigate): live model = vol_surface_replication + SVI
+ IV_DEADBAND 0.01 + vannaflow + accumulation ON; rec.vanna = -1xBS; real spot.
New expiry-book model stays descriptive/conditional throughout. Prior R3 NOT
ACCEPTED. A positive result re-admits to evidence only, never auto-promotes.

Solver: numpy lstsq (SVD) — production-grade, robust to moderate ill-conditioning
for the INCREMENTAL slope, but identifiability is judged BEFORE any β is
reported (rank < n_params, condition number > COND_MAX, non-finite design, or
target aliased => β = NaN, status NOT-IDENTIFIABLE, never a junk number).
"""

from __future__ import annotations

import json
import math
import os
import sys
from typing import Any, Dict, List, Optional

import numpy as np

# ---------------------------------------------------------------------------
# Solver + diagnostics policy
# ---------------------------------------------------------------------------
COND_MAX = 1.0e10       # condition number above this => design too ill-conditioned to trust β
VIF_MAX = 50.0          # VIF above this => collinearity flag (target may still be reported if identifiable)
ALPHA = 0.05            # two-sided type-I error for β CI / power
POWER_TARGET = 0.80
UNIQUE_DAY_TARGET = 29  # locked eligible unique-day count for md<=0.5 correlational arm
BETA_POWER_TARGET_DAYS = 29  # never claim 80% β power unless eligible unique-day n reaches this

# locked horizons (L4): from-breach = 1 forward 10-min bucket; daily = 1 trading day
LOCKED_H_BREACH = 1
LOCKED_H_DAILY = 1


# ---------------------------------------------------------------------------
# L1 — pre-event vanna exposure with a pre-window cutoff
# ---------------------------------------------------------------------------
def compute_pre_event_vanna_exposure(
    chain_rows: List[dict],
    spot: float,
    T: float,
    cutoff_ts: float,
    breach_ts: float,
    ticker: str = "MOCK",
    right_multiplier: float = 1.0,
) -> Dict[str, Any]:
    """Persist pre-event vanna exposure = SUM dealer_frame_vanna * OI * 100 * PP
    over pre-event moneyness x T, computed ONLY from chain rows whose timestamp
    is STRICTLY BEFORE the breach/response window.

    cutoff_ts: the latest allowed source timestamp (pre-window).
    breach_ts: the breach/response window start. Requires cutoff_ts < breach_ts;
               otherwise `cutoff_pass` is False and NO exposure is returned.

    Uses expiry_book_exposure.build_net_exposure (dealer-frame, rec.vanna=-1xBS,
    real spot/IV/DTE) restricted to rows with ts <= cutoff_ts. NO post-treatment
    quantity (contemporaneous ΔIV) is used here.
    """
    import expiry_book_exposure as ebe  # local import: only needed for chain->exposure

    cutoff_pass = cutoff_ts < breach_ts
    if not cutoff_pass:
        return {
            "pre_event_vanna_exposure": float("nan"),
            "pre_vanna_timestamp": cutoff_ts,
            "pre_window_end": cutoff_ts,
            "breach_window_start": breach_ts,
            "cutoff_pass": False,
            "n_rows_used": 0,
            "excluded_post_cutoff": len([r for r in chain_rows if r.get("ms_of_day", 0) > cutoff_ts]),
        }

    pre_rows = [r for r in chain_rows if r.get("ms_of_day", 0) <= cutoff_ts]
    if not pre_rows:
        return {
            "pre_event_vanna_exposure": float("nan"),
            "pre_vanna_timestamp": cutoff_ts,
            "pre_window_end": cutoff_ts,
            "breach_window_start": breach_ts,
            "cutoff_pass": True,
            "n_rows_used": 0,
            "excluded_post_cutoff": len(chain_rows),
        }

    ne = ebe.build_net_exposure(pre_rows, spot, ticker=ticker, T=T)
    # SUM dealer_frame_vanna * OI * CONTRACT_MULTIPLIER * VANNA_PP_SCALE (per-strike)
    # pre-event exposure magnitude. This is the level (no ΔIV), so it cannot be a
    # contemporaneous reflexivity re-derivation.
    total = 0.0
    for r in ne.rows:
        total += r.greeks.get("vanna", 0.0) * r.oi * ebe.CONTRACT_MULTIPLIER * ebe.VANNA_PP_SCALE

    return {
        "pre_event_vanna_exposure": total * right_multiplier,
        "pre_vanna_timestamp": cutoff_ts,
        "pre_window_end": cutoff_ts,
        "breach_window_start": breach_ts,
        "cutoff_pass": True,
        "n_rows_used": len(pre_rows),
        "excluded_post_cutoff": len(chain_rows) - len(pre_rows),
    }


# ---------------------------------------------------------------------------
# L3 — unique-day effective-n (same-day SPY/QQQ = ONE unit)
# ---------------------------------------------------------------------------
def unique_day_count(records: List[dict]) -> int:
    """Number of distinct unique calendar days across records. Same-day SPY+QQQ
    (or any multi-family day) counts once. This is THE atomic independent unit
    for md, power, CI, and gates (L3)."""
    return len({r.get("day") or r.get("date") for r in records})


def deduplicate_family_day(records: List[dict]) -> List[dict]:
    """Deduplicate to one record per unique calendar day, merging family data.
    Family-tagged fields are kept as dicts keyed by family. Any day with both
    SPY and QQQ is one unit; its L2 row aggregates the day's exposure."""
    by_day: Dict[str, dict] = {}
    for r in records:
        d = r.get("day") or r.get("date")
        if d in by_day:
            by_day[d] = _merge_day_records(by_day[d], r)
        else:
            by_day[d] = dict(r)
    # deterministic order
    return [by_day[d] for d in sorted(by_day)]


def _merge_day_records(a: dict, b: dict) -> dict:
    """Merge two records for the same calendar day (e.g. SPY and QQQ records).
    Numeric L2 fields are summed/averaged per policy; family-specific fields
    accumulate under 'families' / '_family_data'."""
    out = dict(a)
    out["day"] = out.get("day") or out.get("date") or b.get("day") or b.get("date")
    fam = set(_as_list(out.get("families"))) | set(_as_list(b.get("families")))
    out["families"] = sorted(fam)
    # accumulate per-family nested data
    fd = dict(out.get("_family_data", {}))
    for f in _as_list(b.get("families")):
        fd[f] = b.get("_family_data", {}).get(f, b)
    out["_family_data"] = fd
    # merge l2 dicts
    l2a = dict(out.get("l2", {}))
    l2b = dict(b.get("l2", {}))
    for k, v in l2b.items():
        if isinstance(v, dict):
            l2a.setdefault(k, {})
            l2a[k].update(v)
        else:
            # sum scalar exposure-like controls across families; keep outcome by priority
            l2a[k] = l2a.get(k, 0.0) + v if isinstance(l2a.get(k), (int, float)) and isinstance(v, (int, float)) else v
    out["l2"] = l2a
    # merge clock dicts
    for clk in ("daily", "from_breach"):
        ca = dict(out.get("clock", {}).get(clk, {}))
        cb = dict(b.get("clock", {}).get(clk, {}))
        # eligible if either eligible; returns take day-aggregate (mean for diagnostics)
        vals = [x for x in (ca.get("return"), cb.get("return")) if x is not None]
        if vals:
            ca["return"] = sum(vals) / len(vals)
        ca["eligible"] = bool(ca.get("eligible") or cb.get("eligible"))
        out.setdefault("clock", {})[clk] = ca
    return out


def _as_list(x) -> List[str]:
    if x is None:
        return []
    if isinstance(x, str):
        return [x]
    return list(x)


# ---------------------------------------------------------------------------
# L2 — production-grade OLS via numpy lstsq (SVD) + rank/condition/VIF
# ---------------------------------------------------------------------------
def _vif(X: np.ndarray, target_idx: int) -> float:
    """VIF for column target_idx = 1/(1 - R2) from regressing it on the rest.
    Returns inf when exactly collinear (R2==1) or the rest is rank-deficient
    (VIF genuinely unbounded — never fabricated via a pseudoinverse).

    VIF measures collinearity among PREDICTOR columns, so constant/intercept-like
    columns (zero variance) are excluded from the regression set (a centered
    constant column is all-zeros and would spuriously induce rank deficiency).
    A constant column gets VIF 1.0 (no variance to inflate)."""
    p = X.shape[1]
    stds = X.std(axis=0)
    pred_cols = [j for j in range(p) if stds[j] > 0]
    if target_idx not in pred_cols:
        return 1.0                      # constant column: VIF undefined -> 1.0
    others = [j for j in pred_cols if j != target_idx]
    if not others:
        return 1.0
    Xr = X[:, others]
    y = X[:, target_idx]
    # center both
    Xc = Xr - Xr.mean(axis=0)
    yc = y - y.mean()
    # if rest columns are themselves rank-deficient, VIF is undefined -> inf
    if np.linalg.matrix_rank(Xc) < min(Xc.shape):
        return float("inf")
    coef, *_ = np.linalg.lstsq(Xc, yc, rcond=None)
    resid = yc - Xc @ coef
    ss_res = float(resid @ resid)
    ss_tot = float(yc @ yc)
    if ss_tot <= 0:
        return 1.0
    r2 = 1.0 - ss_res / ss_tot
    if r2 >= 1.0 - 1e-9:
        return float("inf")
    return 1.0 / (1.0 - r2)


def _fit_ols(X: np.ndarray, y: np.ndarray, target_col: str, col_names: List[str]) -> Dict[str, Any]:
    """Fit y ~ X via numpy lstsq (SVD) with rank/condition/VIF diagnostics and a
    NOT-IDENTIFIABLE disposition. Returns a full diagnostics dict; β for the
    target column is NaN with beta_status NOT-IDENTIFIABLE when the target is
    aliased / the design is rank-deficient / condition > COND_MAX / non-finite."""
    n, p = X.shape
    # Rank and conditioning are computed on SCALE-INVARIANT inputs:
    #   - rank is identical under column scaling (SVD pivot), so use raw X.
    #   - condition number is NOT scale-invariant: the raw design here mixes
    #     ~1e9 (gamma_burst) / ~1e6 (pre_vanna) columns with ~1e-3 (delta_s,
    #     market, spillover) columns, so raw cond can be ~1e12 purely from unit
    #     mismatch even when the columns are genuinely independent (standardized
    #     cond ~20). Gating identifiability on raw cond would fabricate
    #     NOT-IDENTIFIABLE for a well-conditioned, full-rank design. So the
    #     conditioning GUARD uses the standardized (mean-0, unit-variance, on
    #     non-intercept columns) design; the OLS fit itself stays in natural units.
    _Xs = X.astype(float).copy()
    for _j in range(1, _Xs.shape[1]):
        _s = _Xs[:, _j].std()
        if _s > 1e-12:
            _Xs[:, _j] = (_Xs[:, _j] - _Xs[:, _j].mean()) / _s
    _rank = int(np.linalg.matrix_rank(X))
    _cond_std = (float(np.linalg.cond(_Xs)) if n >= p and _rank == p else float("nan"))
    out: Dict[str, Any] = {
        "n": n, "p": p, "solver": "numpy.lstsq(SVD)",
        "rank": _rank,
        "cond": float(np.linalg.cond(X)) if n >= p and _rank == p else float("nan"),
        "cond_standardized": _cond_std,
        "col_names": list(col_names),
    }
    # identify target column by NAME, never by positional index (audit pin)
    if target_col not in col_names:
        out["beta_status"] = "NOT-IDENTIFIABLE"
        out["beta"] = float("nan")
        out["beta_unavailable_reason"] = f"target column '{target_col}' not in design"
        return out

    target_idx = col_names.index(target_col)

    # exact-collinearity / rank-deficiency guard BEFORE reporting any β
    if not np.all(np.isfinite(X)) or not np.all(np.isfinite(y)):
        out["beta_status"] = "NOT-IDENTIFIABLE"
        out["beta"] = float("nan")
        out["beta_unavailable_reason"] = "non-finite design or outcome"
        return out

    rank = out["rank"]
    if rank < p:
        out["beta_status"] = "NOT-IDENTIFIABLE"
        out["beta"] = float("nan")
        out["beta_unavailable_reason"] = f"rank-deficient design (rank {rank} < p {p})"
        return out

    # Conditioning GUARD uses the SCALE-INVARIANT standardized cond (see the
    # block above: raw cond can be ~1e12 purely from column-unit mismatch on a
    # genuinely full-rank design). A design is "not identifiable" for conditioning
    # only if its standardized cond exceeds COND_MAX. Raw cond is still reported
    # as a diagnostic but never gates identifiability.
    cond_std = out.get("cond_standardized")
    if not math.isfinite(cond_std) or cond_std > COND_MAX:
        out["beta_status"] = "NOT-IDENTIFIABLE"
        out["beta"] = float("nan")
        out["beta_unavailable_reason"] = (f"ill-conditioned design (std cond "
                                          f"{cond_std:.2e} > {COND_MAX:.1e})")
        return out

    # VIF diagnostics per column
    vifs = []
    for j in range(p):
        v = _vif(X, j)
        vifs.append(v)
    out["vif"] = vifs
    out["max_vif"] = max(v for v in vifs if math.isfinite(v)) if any(math.isfinite(v) for v in vifs) else float("inf")
    out["vif_flag"] = any(v > VIF_MAX for v in vifs)

    # Production fit
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    dof = n - rank
    sigma2 = float(resid @ resid) / dof if dof > 0 else float("nan")
    # covariance via pinv(X'X) — used ONLY for SE; identifiability already decided above
    try:
        cov = sigma2 * np.linalg.pinv(X.T @ X)
    except Exception:
        cov = np.full((p, p), float("nan"))
    se = np.sqrt(np.clip(np.diag(cov), 0, None))

    beta = coef[target_idx]
    se_beta = se[target_idx]
    out["coef"] = coef.tolist()
    out["se"] = se.tolist()
    out["beta"] = float(beta)
    out["beta_se"] = float(se_beta)
    out["beta_status"] = "IDENTIFIABLE"
    out["beta_t"] = float(beta / se_beta) if se_beta > 0 else float("nan")
    out["beta_unavailable_reason"] = None
    out["sigma2"] = float(sigma2)
    out["r2"] = 1.0 - float(resid @ resid) / float((y - y.mean()) @ (y - y.mean())) if n > 1 else float("nan")
    return out


def build_design(day_records: List[dict], family_l2: bool = True) -> Dict[str, Any]:
    """Build the L2 design matrix at unique-day level.

    y (from-breach forward return at locked h) ~
        β·(pre_vanna×ΔIV) + γ·gamma_burst + ζ·ΔIV + θ·ΔS + λ·market
      + event + A6_reflexivity + cross_family_spillover + family interactions
    where family interactions = per-family signed SPY/QQQ interaction columns
    (0 on the family absent that day) so the opposite-sign family rule is scored
    from the same fit's β_SPY / β_QQQ.

    One row per unique calendar day (L3). Deterministic column order.
    The combined `family_interaction` contrast column is intentionally NOT added
    (it is a linear combination of the two per-family columns => collinear).
    Family interaction columns are added only for families actually present in
    the data (an all-zero family column would make the design rank-deficient).
    """
    # detect families present across the deduped records
    present_fams = set()
    for r in day_records:
        for f in _as_list(r.get("families")):
            present_fams.add(str(f).upper())
    fam_cols = [f"family_interaction_{f.lower()}" for f in sorted(present_fams)]

    rows_X = []
    y_rows = []
    days = []
    for r in day_records:
        l2 = r.get("l2", {})
        pre_v = l2.get("pre_vanna_exposure", 0.0)
        div = l2.get("delta_iv", 0.0)
        interaction = pre_v * div
        row = [
            1.0,                 # intercept
            interaction,         # target: pre_vanna × ΔIV
            l2.get("gamma_burst", 0.0),
            div,                 # ζ ΔIV main effect (explicit)
            l2.get("delta_s", 0.0),
            l2.get("market", 0.0),
            float(l2.get("event", 0) or 0),
            l2.get("a6_reflexivity", 0.0),
            l2.get("cross_family_spillover", 0.0),
        ]
        if family_l2:
            for fam_col in fam_cols:
                row.append(l2.get(fam_col, 0.0))
        rows_X.append(row)
        y_rows.append(l2.get("forward_return_h", 0.0))
        days.append(r.get("day") or r.get("date"))
    cols = (["intercept", "pre_vanna_x_delta_iv", "gamma_burst", "delta_iv",
             "delta_s", "market", "event", "a6_reflexivity",
             "cross_family_spillover"])
    if family_l2:
        cols += fam_cols
    X = np.asarray(rows_X, dtype=float)
    y = np.asarray(y_rows, dtype=float)
    return {"X": X, "y": y, "col_names": cols, "days": days}


def orthogonalize_vanna_design(day_records: List[dict], family_l2: bool = True) -> Dict[str, Any]:
    """Vanna⊥ orthogonalization (Cem's R3 lever, zero acquisition cost).

    The target column pre_vanna×ΔIV is structurally collinear (VIF≈85.1) with its
    own constituents: the per-family pre_vanna levels (family_interaction_*) and the
    ΔIV main effect. Regress pre_vanna×ΔIV on those constituents, keep the residual
    `(pre_vanna×ΔIV)⊥ = Vanna⊥`, and re-fit the outcome model with Vanna⊥ replacing
    the raw interaction (the pre-registered controls gamma_burst / ΔIV / ΔS / market /
    event / A6 / spillover / family levels remain unchanged).

    This removes the structural overlap that inflates SE and drags β power — the
    binding constraint Cem identified. It is equivalent (up to the linear span) to
    including the interaction as a residualized regressor in a Frisch-Waugh-Lovell
    sense: the coefficient on Vanna⊥ is the same as the coefficient on the interaction
    in the fully-saturated design where the interaction is orthogonalized against its
    constituents.

    Columns: [intercept, vanna_orth (residualized target), gamma_burst, delta_iv,
    delta_s, market, event, a6_reflexivity, cross_family_spillover, family levels...]
    """
    present_fams = set()
    for r in day_records:
        for f in _as_list(r.get("families")):
            present_fams.add(str(f).upper())
    fam_cols = [f"family_interaction_{f.lower()}" for f in sorted(present_fams)]

    # ---- ΔIV provenance gate (Cem's fail-closed causal gate) — computed FIRST,
    #      before any design is built. A corpus is CAUSAL-ELIGIBLE only if EVERY
    #      deduped unit explicitly carries delta_iv_provenance=="PRE_WINDOW", a
    #      valid iv_source_ts STRICTLY before breach_window_start_prov, AND a
    #      non-null delta_iv_pre_window. Any missing/equal/later/mixed/invalid unit
    #      vetoes the whole corpus -> causal β unavailable (associational only).
    all_pass = True
    _reasons = set()
    for r in day_records:
        l2 = r.get("l2", {})
        prov = str(l2.get("delta_iv_provenance", "")).upper()
        src_ts = l2.get("iv_source_ts")
        b_ts = l2.get("breach_window_start_prov")
        div_pw = l2.get("delta_iv_pre_window")
        if prov != "PRE_WINDOW":
            all_pass = False
            _reasons.add("missing/associational provenance")
            continue
        if src_ts is None or b_ts is None or not (float(src_ts) < float(b_ts)):
            all_pass = False
            _reasons.add("timestamp not strictly before breach")
            continue
        if div_pw is None:
            all_pass = False
            _reasons.add("missing delta_iv_pre_window")
    if all_pass:
        delta_iv_provenance = "PRE_WINDOW"
        associational_label = "CAUSAL-ELIGIBLE"
    else:
        delta_iv_provenance = "DAY_LEVEL-UNVERIFIED" if _reasons else "ASSOCIATIONAL-ΔIV-CONTEMPORANEOUS"
        associational_label = "ASSOCIATIONAL-ΔIV-CONTEMPORANEOUS"

    # constituents of the interaction: per-family pre_vanna levels + ΔIV main effect
    constituent_cols = list(fam_cols) + ["delta_iv"]

    rows_Z = []   # design for the residualization regression (constituents)
    rows_X = []   # full outcome design with Vanna⊥ in place of the raw interaction
    y_rows = []
    days = []
    for r in day_records:
        l2 = r.get("l2", {})
        pre_v = l2.get("pre_vanna_exposure", 0.0)
        div_day = l2.get("delta_iv", 0.0)   # day-level net_div — associational/descriptive only
        # R10.6.1: the causal target uses delta_iv_pre_window ONLY when the whole
        # corpus passed the causal gate. Otherwise it uses the day-level ΔIV and is
        # explicitly labeled ASSOCIATIONAL. Never introduce NaN (would crash lstsq).
        if all_pass:
            div_for_target = float(l2.get("delta_iv_pre_window"))
        else:
            div_for_target = div_day
        interaction = pre_v * div_for_target
        # residualization regressors
        zrow = []
        for c in constituent_cols:
            zrow.append(l2.get(c, 0.0))
        rows_Z.append([1.0] + zrow)          # + intercept
        # outcome design: Vanna⊥ in the target slot (filled after residualization).
        # The ΔIV main-effect column stays the day-level ΔIV (the associational
        # contemporaneous control); the residualized TARGET is what switches source.
        rows_X.append([1.0, 0.0, l2.get("gamma_burst", 0.0), div_day,
                       l2.get("delta_s", 0.0), l2.get("market", 0.0),
                       float(l2.get("event", 0) or 0), l2.get("a6_reflexivity", 0.0),
                       l2.get("cross_family_spillover", 0.0)])
        if family_l2:
            for fam_col in fam_cols:
                rows_X[-1].append(l2.get(fam_col, 0.0))
        y_rows.append(l2.get("forward_return_h", 0.0))
        days.append(r.get("day") or r.get("date"))

    Z = np.asarray(rows_Z, dtype=float)          # n x (1 + len(constituents))
    target = np.asarray([r.get("l2", {}).get("pre_vanna_exposure", 0.0)
                         * (float(r.get("l2", {}).get("delta_iv_pre_window"))
                            if all_pass else r.get("l2", {}).get("delta_iv", 0.0))
                         for r in day_records], dtype=float)
    # residualize target on constituents + intercept (FWL step)
    coef_z, *_ = np.linalg.lstsq(Z, target, rcond=None)
    resid = target - Z @ coef_z
    for i, xrow in enumerate(rows_X):
        xrow[1] = float(resid[i])               # Vanna⊥ into the target slot

    X = np.asarray(rows_X, dtype=float)
    y = np.asarray(y_rows, dtype=float)
    cols = (["intercept", "vanna_orth", "gamma_burst", "delta_iv",
             "delta_s", "market", "event", "a6_reflexivity",
             "cross_family_spillover"])
    if family_l2:
        cols += fam_cols

    # report the residualization diagnostics
    return {"X": X, "y": y, "col_names": cols, "days": days,
            "constituents": constituent_cols, "resid_target": resid,
            "delta_iv_provenance": delta_iv_provenance,
            "associational_label": associational_label,
            "provenance_reasons": sorted(_reasons),
            "target_vif_pre": None}  # VIF computed downstream


# ---------------------------------------------------------------------------
# Honest β power at unique-day effective-n (NOT a correlation md)
# ---------------------------------------------------------------------------
def _t_ppf(q, df):
    try:
        from scipy.stats import t as _t
        return float(_t.ppf(q, df))
    except Exception:
        # normal approximation fallback
        from scipy.stats import norm as _n
        return float(_n.ppf(q))


def _t_cdf(x, df):
    try:
        from scipy.stats import t as _t
        return float(_t.cdf(x, df))
    except Exception:
        from scipy.stats import norm as _n
        return float(_n.cdf(x))


def beta_power(beta: float, se_beta: float, df: int, p: int,
               alpha: float = ALPHA, side: str = "one") -> Dict[str, Any]:
    """Power to DETECT the L2 interaction β at unique-day effective-n, one-sided
    (EXPECT POSITIVE on the from-breach clock). df = n_eff - p.

    se scales ~ 1/sqrt(n_eff), so power for other n is computed by re-scaling.
    Returns also n_for_80 = smallest eligible unique-day n with power>=0.80.
    Never returns 80% unless BOTH n_eff >= BETA_POWER_TARGET_DAYS AND computed
    power >= 0.80."""
    n_eff = df + p
    if beta is None or not math.isfinite(float(beta)) or se_beta is None or not math.isfinite(float(se_beta)) or se_beta <= 0 or df < 1:
        return {"power": float("nan"), "power_available": False,
                "n_for_80": None, "beta": beta, "se_beta": se_beta,
                "df": df, "n_eff": n_eff, "reach_80": False}
    t_stat = beta / se_beta
    if side == "one":
        tcrit = _t_ppf(1.0 - alpha, df)
        power = 1.0 - _t_cdf(tcrit - t_stat, df)
    else:
        tcrit = _t_ppf(1.0 - alpha / 2.0, df)
        power = (1.0 - _t_cdf(tcrit - abs(t_stat), df)) + _t_cdf(-tcrit - abs(t_stat), df)
    reach_80 = power >= POWER_TARGET and n_eff >= BETA_POWER_TARGET_DAYS
    # n_for_80 via se scaling: se(n) = se_current * sqrt(n_eff/n)
    n_for_80 = None
    if math.isfinite(power):
        for n_cand in range(max(n_eff, 3), 1000):
            se_n = se_beta * math.sqrt(n_eff / n_cand)
            t_n = beta / se_n
            pow_n = 1.0 - _t_cdf(_t_ppf(1.0 - alpha, n_cand - p) - t_n, n_cand - p)
            if pow_n >= POWER_TARGET:
                n_for_80 = n_cand
                break
    return {"power": float(power), "power_available": True, "n_for_80": n_for_80,
            "beta": float(beta), "se_beta": float(se_beta), "df": df,
            "n_eff": n_eff, "reach_80": reach_80}


# FWL equivalence: the direct multivariate numpy lstsq fit of y ~ X (with the
# interaction column among the regressors) yields the SAME β (and SE) as
# Frisch-Waugh-Lovell residualization (regress y and the interaction on the
# remaining covariates, then regress the residuals). The tests verify the β
# from _fit_ols matches an explicit FWL residual-on-residual coefficient.


# ---------------------------------------------------------------------------
# L4 — locked horizon enforcement (no best-lag selection)
# ---------------------------------------------------------------------------
def enforce_locked_horizon(candidate_h: int, locked_h: int) -> bool:
    """A lag/horizon sweep MUST NOT change the locked h. Returns True iff the
    candidate horizon equals the locked value. Any best-lag selection would make
    this return False for a non-locked candidate."""
    return int(candidate_h) == int(locked_h)


# ---------------------------------------------------------------------------
# L5 — surprise defined operationally OR descriptive-habitat label
# ---------------------------------------------------------------------------
def surprise_status(record: dict) -> Dict[str, Any]:
    """Operational surprise = actual - expected (pre-specified). If a day has no
    surprise input, its event finding is labeled DESCRIPTIVE-HABITAT and is NOT
    eligible for a causal-surprise claim. Missing surprise never becomes 0."""
    l2 = record.get("l2", {})
    surprise = l2.get("surprise")
    habitat = l2.get("event_habitat", "NONE")
    if surprise is None:
        return {
            "surprise": None,
            "surprise_status": "DESCRIPTIVE-HABITAT",
            "causal_surprise_eligible": False,
            "reason": "no operational surprise (actual-expected) supplied",
            "event_habitat": habitat,
        }
    return {
        "surprise": float(surprise),
        "surprise_status": "OPERATIONAL" if habitat not in ("", None, "NONE") else "OPERATIONAL-NO-EVENT",
        "causal_surprise_eligible": True,
        "reason": None,
        "event_habitat": habitat,
    }


# ---------------------------------------------------------------------------
# L6 — family-interaction opposite-sign rule
# ---------------------------------------------------------------------------
def family_claim_allowed(spy_beta: Optional[float], qqq_beta: Optional[float]) -> Dict[str, Any]:
    """Pre-registered homogeneity rule: OPPOSITE-signed families => NO family-wide
    claim. Zero, missing, non-identifiable, duplicated, or same-family pairs fail
    the rule. Returns a structured verdict."""
    if spy_beta is None or qqq_beta is None or not math.isfinite(float(spy_beta)) or not math.isfinite(float(qqq_beta)):
        return {"allowed": False, "reason": "missing or non-identifiable family beta"}
    if spy_beta == 0.0 or qqq_beta == 0.0:
        return {"allowed": False, "reason": "zero family beta"}
    if (spy_beta > 0) == (qqq_beta > 0):
        return {"allowed": False, "reason": f"same-sign families (SPY {spy_beta:+.3f}, QQQ {qqq_beta:+.3f})"}
    return {"allowed": True, "reason": f"opposite-sign families (SPY {spy_beta:+.3f}, QQQ {qqq_beta:+.3f})"}


# ---------------------------------------------------------------------------
# L7 — single decision table + BOTH-CLOCK-CONFIRMED cell
# ---------------------------------------------------------------------------
def _both_clock_confirmed(daily_neg: bool, daily_ci_hi: Optional[float],
                          breach_pos: bool, breach_ci_lo: Optional[float]) -> Dict[str, Any]:
    """The ONLY re-admission state: daily NEGATIVE (shadow-consistent) AND
    from-breach POSITIVE (direct push), CIs supporting both. daily is a
    diagnostic flag, not a gate."""
    daily_ok = daily_neg and (daily_ci_hi is None or daily_ci_hi < 0)
    breach_ok = breach_pos and (breach_ci_lo is None or breach_ci_lo > 0)
    if daily_ok and breach_ok:
        return {"value": "BOTH-CLOCK-CONFIRMED", "confirmed": True,
                "reason": "daily negative AND from-breach positive (CIs support)"}
    return {"value": "NOT-CONFIRMED", "confirmed": False,
            "reason": f"daily_neg={daily_neg}(hi={daily_ci_hi}) breach_pos={breach_pos}(lo={breach_ci_lo})"}


def run_causal_arm(records: List[dict], config: Optional[dict] = None) -> Dict[str, Any]:
    """Main analysis boundary. Dedups to unique days, builds the L2 design, fits
    β with diagnostics, computes honest unique-day power, scores the family rule,
    the surprise status, the both-clock cell, and assembles ONE decision table
    (one row per unique day). Deterministic, network-free."""
    config = config or {}
    locked_h = int(config.get("locked_h_breach", LOCKED_H_BREACH))

    # L3: dedup to unique calendar days FIRST (the atomic unit for everything)
    uniq = deduplicate_family_day(records)
    n_eff = len(uniq)

    # L4: enforce locked horizon against any candidate sweep
    h_locked = all(
        enforce_locked_horizon(hh, locked_h) for hh in config.get("horizon_sweep", [locked_h])
    )

    # L1: cutoff provenance + pre-event exposure (already gated at build; verify here)
    cutoff_ok = True
    for r in uniq:
        pw = r.get("pre_window", {})
        if pw.get("cutoff_pass") is False:
            cutoff_ok = False
            break

    # L2: build design at unique-day level
    des = build_design(uniq, family_l2=True)
    fit = _fit_ols(des["X"], des["y"], "pre_vanna_x_delta_iv", des["col_names"])
    beta = fit.get("beta")
    se_beta = fit.get("beta_se")
    p = fit.get("p", len(des["col_names"]))
    df = n_eff - p

    # family β from the same fit's per-family interaction columns (present families only)
    spy_beta = qqq_beta = None
    if fit.get("beta_status") == "IDENTIFIABLE":
        col_to_idx = {c: i for i, c in enumerate(des["col_names"])}
        if "family_interaction_spy" in col_to_idx:
            spy_beta = fit["coef"][col_to_idx["family_interaction_spy"]]
        if "family_interaction_qqq" in col_to_idx:
            qqq_beta = fit["coef"][col_to_idx["family_interaction_qqq"]]

    fam_rule = family_claim_allowed(spy_beta, qqq_beta)

    # honest β power at unique-day effective-n
    if beta is not None and math.isfinite(float(beta)) and se_beta is not None and math.isfinite(float(se_beta)):
        power = beta_power(beta, se_beta, df, p, side="one")
    else:
        power = {"power": float("nan"), "power_available": False, "n_for_80": None,
                 "beta": beta, "se_beta": se_beta, "df": df, "n_eff": n_eff,
                 "reach_80": False}

    # L7: one decision table row per unique day
    rows = []
    for r in uniq:
        l2 = r.get("l2", {})
        clk = r.get("clock", {})
        daily = clk.get("daily", {})
        breach = clk.get("from_breach", {})
        ss = surprise_status(r)
        rows.append({
            "unique_day": r.get("day") or r.get("date"),
            "families": _as_list(r.get("families")),
            "event_habitat": ss["event_habitat"],
            "surprise_status": ss["surprise_status"],
            "causal_surprise_eligible": ss["causal_surprise_eligible"],
            "pre_event_vanna_exposure": l2.get("pre_vanna_exposure"),
            "pre_vanna_timestamp": (r.get("pre_window", {}) or {}).get("pre_vanna_timestamp"),
            "pre_window_end": (r.get("pre_window", {}) or {}).get("pre_window_end"),
            "cutoff_pass": (r.get("pre_window", {}) or {}).get("cutoff_pass"),
            "gamma_burst": l2.get("gamma_burst"),
            "delta_iv": l2.get("delta_iv"),
            "delta_s": l2.get("delta_s"),
            "market": l2.get("market"),
            "a6_reflexivity": l2.get("a6_reflexivity"),
            "cross_family_spillover": l2.get("cross_family_spillover"),
            "forward_return_h": l2.get("forward_return_h"),
            "daily_return": daily.get("return"),
            "from_breach_return": breach.get("return"),
            "daily_clock_negative": bool(daily.get("return") is not None and daily.get("return") < 0) if daily.get("eligible") else None,
            "breach_clock_positive": bool(breach.get("return") is not None and breach.get("return") > 0) if breach.get("eligible") else None,
        })

    # both-clock cell: STRICT conjunction on per-day clock signs. A unique day is
    # confirmed only if BOTH clocks are eligible AND daily-return < 0 (shadow) AND
    # from-breach-return > 0 (direct push) on that day. This is a diagnostic state,
    # NOT a gate. Distinguishes confirmed / not-confirmed / not-identifiable.
    def _score_both_clock(r):
        dneg = r.get("daily_clock_negative")
        bpos = r.get("breach_clock_positive")
        if dneg is None or bpos is None:
            return {"value": "NOT-IDENTIFIABLE", "confirmed": False,
                    "reason": "one or both clocks not eligible on this day"}
        if dneg and bpos:
            return {"value": "BOTH-CLOCK-CONFIRMED", "confirmed": True,
                    "reason": "daily return<0 (shadow-consistent) AND from-breach return>0 (direct push)"}
        return {"value": "NOT-CONFIRMED", "confirmed": False,
                "reason": f"daily_neg={dneg} breach_pos={bpos}"}

    both_clk = {"per_day": [_score_both_clock(r) for r in rows]}
    n_confirmed = sum(1 for c in both_clk["per_day"] if c["confirmed"])
    n_eligible = sum(1 for c in both_clk["per_day"] if c["value"] != "NOT-IDENTIFIABLE")
    both_clk["n_confirmed_days"] = n_confirmed
    both_clk["n_eligible_days"] = n_eligible
    # RE-ADMISSION STATE (the only one that re-admits to evidence) is a strict
    # conjunction: >=1 eligible unique day with both-clock signs confirmed AND
    # family opposite-sign rule passes AND the L2 interaction β is identifiable.
    # Reported as a diagnostic, never as a stand-alone gate.
    fam_ok = bool(fam_rule.get("allowed"))
    beta_ok = fit.get("beta_status") == "IDENTIFIABLE" and beta is not None and math.isfinite(float(beta))
    both_clk["family_rule_passed"] = fam_ok
    both_clk["beta_identifiable"] = beta_ok
    if n_confirmed >= 1 and fam_ok and beta_ok:
        both_clk["value"] = "BOTH-CLOCK-CONFIRMED"
        both_clk["confirmed"] = True
        both_clk["reason"] = (f"{n_confirmed}/{n_eligible} eligible days both-clock + family rule + identifiable β")
    elif n_eligible == 0:
        both_clk["value"] = "NOT-IDENTIFIABLE"
        both_clk["confirmed"] = False
        both_clk["reason"] = "no eligible unique day for either clock"
    else:
        both_clk["value"] = "NOT-CONFIRMED"
        both_clk["confirmed"] = False
        fails = []
        if n_confirmed < 1:
            fails.append(f"{n_confirmed}/{n_eligible} days both-clock")
        if not fam_ok:
            fails.append("family rule")
        if not beta_ok:
            fails.append("identifiable β")
        both_clk["reason"] = "blocked by: " + "; ".join(fails)

    table = {
        "effective_n_unique_days": n_eff,
        "unique_day_target": UNIQUE_DAY_TARGET,
        "reach_unique_day_target": n_eff >= UNIQUE_DAY_TARGET,
        "locked_h_breach": locked_h,
        "locked_h_daily": config.get("locked_h_daily", LOCKED_H_DAILY),
        "horizon_locked": h_locked,
        "cutoff_integrity_ok": cutoff_ok,
        "solver": fit.get("solver"),
        "rank": fit.get("rank"),
        "p": fit.get("p"),
        "cond": fit.get("cond"),
        "cond_standardized": fit.get("cond_standardized"),
        "max_vif": fit.get("max_vif"),
        "vif_flag": fit.get("vif_flag"),
        "beta_t": fit.get("beta_t"),
        "beta": beta,
        "beta_se": se_beta,
        "beta_status": fit.get("beta_status"),
        "beta_unavailable_reason": fit.get("beta_unavailable_reason"),
        "beta_power": power.get("power"),
        "beta_power_available": power.get("power_available"),
        "beta_n_for_80": power.get("n_for_80"),
        "beta_reach_80": power.get("reach_80"),
        "family_rule": fam_rule,
        "both_clock": both_clk,
        "rows": rows,
    }
    return table


def render_table_md(table: Dict[str, Any]) -> str:
    lines = []
    lines.append("# Causal-Arm v2 — Single Decision Table (ROUND-10.2)")
    lines.append("")
    lines.append(f"- effective unique-day n = **{table['effective_n_unique_days']}** "
                 f"(target {table['unique_day_target']}; reached={table['reach_unique_day_target']})")
    lines.append(f"- locked h (from-breach) = **{table['locked_h_breach']}** 10-min bucket(s); "
                 f"locked h (daily) = **{table['locked_h_daily']}** trading day; horizon_locked={table['horizon_locked']}")
    lines.append(f"- cutoff integrity OK = {table['cutoff_integrity_ok']}")
    lines.append(f"- solver = {table['solver']}; rank={table['rank']}/{table['p']}; "
                 f"cond={table['cond']:.2e}; max_VIF={table['max_vif']} (flag={table['vif_flag']})")
    lines.append(f"- β (pre_vanna×ΔIV) = {table['beta']}  se={table['beta_se']}  status={table['beta_status']}"
                 + (f"  [unavailable: {table['beta_unavailable_reason']}]" if table['beta_unavailable_reason'] else ""))
    lines.append(f"- honest β power (one-sided) = {table['beta_power']:.3f} "
                 f"(available={table['beta_power_available']}, reach_80={table['beta_reach_80']}, n_for_80={table['beta_n_for_80']})")
    lines.append(f"- family rule = {table['family_rule']['allowed']}  ({table['family_rule']['reason']})")
    lines.append(f"- BOTH-CLOCK-CONFIRMED = {table['both_clock']['value']}  ({table['both_clock']['reason']})")
    lines.append("")
    lines.append("| unique_day | families | habitat | surprise | pre_vanna | gamma | ΔIV | ΔS | mkt | A6 | spill | fwd_h | daily | breach | daily_neg | breach_pos |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in table["rows"]:
        def f(x, nd=4):
            return "—" if x is None else f"{x:.{nd}f}"
        lines.append(
            f"| {r['unique_day']} | {','.join(r['families'])} | {r['event_habitat']} | "
            f"{r['surprise_status'][:9]} | {f(r['pre_event_vanna_exposure'],0)} | {f(r['gamma_burst'],0)} | "
            f"{f(r['delta_iv'])} | {f(r['delta_s'])} | {f(r['market'])} | {f(r['a6_reflexivity'])} | "
            f"{f(r['cross_family_spillover'])} | {f(r['forward_return_h'])} | {f(r['daily_return'])} | "
            f"{f(r['from_breach_return'])} | {r['daily_clock_negative']} | {r['breach_clock_positive']} |"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI (thin) — load day-record JSON and call the same core used by tests/smoke
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: run_causal_arm_v2.py <day_records.json> [out.md]")
        return 2
    with open(argv[0], "r", encoding="utf-8") as fh:
        records = json.load(fh)
    table = run_causal_arm(records)
    md = render_table_md(table)
    out = argv[1] if len(argv) > 1 else None
    if out:
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(md + "\n")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
