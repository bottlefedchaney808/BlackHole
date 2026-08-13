#!/usr/bin/env python3
"""backtest_accumulation_falsifier.py

The pre-registered OOS falsifier + lead-lag test the WSL/Ubuntu-environment
"vanna-seed battery" (see Vol_Suite/docs/Dealer posistioning notes/) designed
but never ran: "does the accumulated (multi-day seed-plus-flow) dealer-gamma
read predict forward realized vol better than the plain same-day snapshot
read, and at what lag." Per the CARL audit + 5-person panel debate that
followed, this is the one test result that should gate any future
seed-vs-flow claim -- not another round of narrower comparisons.

Reuses backtest_stage3.py's already-tested machinery rather than
reimplementing it: `_build_day_records` for the same-day snapshot arm
(net_gamma_v1/oi_heuristic regime + forward realized vol per day) and
`_forward_realized_vol` for the RV label itself. The accumulated arm is new
here: for each usable trading day, walk the accumulated position as of that
day (replication_reference._accumulate_from_history, restricted to the
rolling lookback window ending on that day) and combine it with that day's
own gamma to get a net accumulated-gamma read, directly comparable to the
snapshot arm's net_gamma_v1.

Split into a pure function over already-fetched rows
(_run_falsifier_from_history) and a network/cache-touching orchestrator
(run_falsifier), same pattern as every other module in this file's family
(replication_reference.compute_accumulated_position,
backtest_stage3.run_backtest). run_falsifier defaults to reading the
12-ticker, 150-day dataset already pulled once and saved to disk
(seed_data_loader.py) rather than hitting ThetaData on every run -- the
proxy-overload problem already hit once during this investigation.
"""
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy import stats as _scipy_stats

from thetadata_client import ThetaDataController, strike_from_theta
import expiry_selector
import implied_vol as implied_vol_mod
import replication_reference
import dealer_positioning
import backtest_stage3
import seed_data_loader

# ---------------------------------------------------------------------------
# Result cache -- persist heavy computed falsifier results to JSON so re-runs
# load instead of recomputing the ~8-min per-day accumulated book across 12
# tickers. The raw seed_data_*.json market data is ALREADY saved to disk; this
# caches the DERIVED result (which doesn't change for the same (mode, fitter,
# seed_mode, lookback, ticker)). `_force_recompute()` bypasses.
# ---------------------------------------------------------------------------
import json
import os

_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "outputs", "falsifier_cache")


def _fitter_used() -> str:
    return os.environ.get("VOL_SURFACE_FITTER", "svi").strip().lower() or "svi"


def _cache_path(kind: str, **key) -> str:
    """Deterministic cache file for a result kind + key tuple. Include the
    sign-source fitter so SABR vs SVI results never collide."""
    parts = [kind, _fitter_used()]
    for k, v in key.items():
        parts.append(f"{k}={v}")
    fname = "_".join(str(p) for p in parts).replace("/", "_").replace("\\", "_") + ".json"
    return os.path.join(_CACHE_DIR, fname)


def _force_recompute() -> bool:
    return os.environ.get("FALSIFIER_FORCE", "0") == "1"


def _dataclass_to_dict(obj) -> dict:
    from dataclasses import asdict
    return asdict(obj)


def _save_result(kind: str, key: dict, obj) -> str:
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        path = _cache_path(kind, **key)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(_dataclass_to_dict(obj), fh, indent=2, default=str)
        return path
    except Exception as e:
        print(f"  [cache] failed to save {kind} result: {type(e).__name__} {e}")
        return ""


def _load_result(kind: str, key: dict):
    path = _cache_path(kind, **key)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def _hydrate(cls, data) -> object:
    """Reconstruct a dataclass (with nested dict/list defaults) from a loaded
    dict, tolerating missing optional fields."""
    import dataclasses
    fields = {f.name: f for f in dataclasses.fields(cls)}
    kwargs = {}
    for name, f in fields.items():
        if name in data:
            kwargs[name] = data[name]
        elif f.default is not dataclasses.MISSING:
            kwargs[name] = f.default
        elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            kwargs[name] = f.default_factory()  # type: ignore[misc]
    return cls(**kwargs)


TRADING_DAYS_PER_YEAR = 252

# Flat rate/dividend assumptions -- same values and same rationale as
# backtest_stage3.py's _BACKTEST_R/_BACKTEST_Q (gamma sign/magnitude isn't
# materially rate-sensitive; these only shift the OTM boundary slightly).
_R = 0.04
_Q = 0.012

# Pre-registered thresholds (TEST 6, battery-consolidated-20260811.md):
# incremental t>2.0 AND |corr(accumulated, snapshot)|<0.5 AND block-perm
# p<0.05 -> the accumulated read earns its keep. ANY single perturbation
# flipping the verdict, or fewer than this many usable days, -> INCONCLUSIVE
# rather than a false positive/negative from an underpowered sample.
_MIN_USABLE_DAYS = 20
_TSTAT_THRESHOLD = 2.0
_CORR_INDEPENDENCE_THRESHOLD = 0.5
_PERM_P_THRESHOLD = 0.05
_N_PERMUTATIONS = 500
_PERM_BLOCK_SIZE = 5


@dataclass
class FalsifierResult:
    ticker: str
    expiry: str
    n_days: int
    n_days_signals_disagree: int
    snapshot_corr: float
    accumulated_corr: float
    r2_snapshot_only: float
    r2_with_accumulated: float
    delta_r2: float
    accumulated_coef_tstat: float
    accumulated_coef_pvalue: float
    block_perm_pvalue: float
    lead_lag_corr: Dict[int, float] = field(default_factory=dict)
    best_lag: int = 0
    verdict: str = "INCONCLUSIVE"


def _build_gamma_and_oi_by_date(expiry: str, hist_greek_rows: List[dict],
                                 hist_oi_rows: List[dict],
                                 close_by_date: Dict[str, float],
                                 ) -> Tuple[Dict[str, Dict[Tuple[float, str], float]],
                                            Dict[str, Dict[Tuple[float, str], int]]]:
    """Same IV/gamma derivation as backtest_stage3._build_day_records (vendor
    gamma when present, else solved from bid/ask via implied_vol + BS gamma)
    -- duplicated rather than imported because backtest_stage3 doesn't
    expose its internal per-date maps, only the aggregated DayRecord list.
    Same tradeoff replication_reference.py's module docstring already
    documents: duplicating ~20 lines is cheaper than coupling this module to
    another module's private internals.
    """
    expiry_date = datetime.strptime(expiry, "%Y%m%d")
    gamma_by_date: Dict[str, Dict[Tuple[float, str], float]] = defaultdict(dict)
    for row in hist_greek_rows:
        d = replication_reference._parse_hist_date(row)
        if not d:
            continue
        try:
            k = float(row['strike']) if float(row['strike']) < 10000 else strike_from_theta(int(float(row['strike'])))
            right = str(row['right']).upper()[:1]
        except (KeyError, TypeError, ValueError):
            continue
        iv = float(row.get('implied_vol', 0) or 0)
        gamma = float(row.get('gamma', 0) or 0)
        if gamma <= 0:
            spot = close_by_date.get(d)
            if not spot or iv <= 0:
                continue
            T = max((expiry_date - datetime.strptime(d, "%Y%m%d")).days, 1) / 365.0
            gamma = dealer_positioning.bs_gamma(spot, k, T, _R, _Q, iv)
        if gamma > 0:
            gamma_by_date[d][(k, right)] = gamma

    oi_by_date: Dict[str, Dict[Tuple[float, str], int]] = defaultdict(dict)
    for row in hist_oi_rows:
        d = replication_reference._parse_hist_date(row)
        if not d:
            continue
        try:
            k = strike_from_theta(int(float(row['strike'])))
            right = row['right']
            oi = int(float(row.get('open_interest', 0) or 0))
        except (KeyError, TypeError, ValueError):
            continue
        oi_by_date[d][(k, right)] = oi

    return gamma_by_date, oi_by_date


def _ols_r2_and_tstat(y: np.ndarray, X: np.ndarray) -> Tuple[float, np.ndarray, np.ndarray]:
    """Closed-form OLS with an intercept column already included in X.
    Returns (R^2, coefficients, t-stats per coefficient)."""
    n, p = X.shape
    beta, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    dof = max(n - p, 1)
    sigma2 = ss_res / dof
    try:
        xtx_inv = np.linalg.inv(X.T @ X)
        se = np.sqrt(np.clip(np.diag(xtx_inv) * sigma2, 0, None))
        tstats = np.divide(beta, se, out=np.zeros_like(beta), where=se > 0)
    except np.linalg.LinAlgError:
        tstats = np.zeros(p)
    return r2, beta, tstats


def _extract_paired_signals(ticker: str, expiry: str,
                             hist_greek_rows: List[dict], hist_oi_rows: List[dict],
                             hist_spot_rows: List[dict],
                             lookback_days: int = 150,
                             forward_window_days: int = 5,
                             seed_mode: str = 'replication',
                             ) -> Tuple[List[str], np.ndarray, np.ndarray, np.ndarray]:
    """Pure function: builds the day-aligned (snapshot sign, accumulated
    sign, forward realized vol) arrays for ONE ticker -- the shared core
    both the single-ticker falsifier (_run_falsifier_from_history) and the
    cross-sectional pooled falsifier (_run_pooled_falsifier_from_histories)
    are built from, so pooling never re-derives the per-ticker signal logic.
    Returns (paired_dates, snapshot_sign, accumulated_sign, forward_rv).
    """
    day_records = backtest_stage3._build_day_records(
        ticker, expiry, hist_greek_rows, hist_oi_rows, hist_spot_rows,
        forward_window_days=forward_window_days,
    )
    labeled = [r for r in day_records if r.fwd_realized_vol is not None]
    if len(labeled) < 2:
        raise ValueError(
            f"Not enough usable labeled days for {ticker} {expiry} to run the "
            f"falsifier (found {len(labeled)})."
        )

    # Sourced from the raw spot-price rows directly, NOT from day_records'
    # (necessarily narrower) gamma-AND-oi-AND-iv-AND-close intersection --
    # real hist_stock_eod data has a close price on every trading day
    # regardless of whether that day's option chain had usable greeks/OI,
    # and the accumulated arm's gamma-from-iv derivation only needs
    # (iv, spot), not the snapshot arm's full four-way intersection. Using
    # day_records' narrower set here was the second half of the real-data
    # bug found running this against the WSL handoff's cached seed_data
    # (see test_accumulated_arm_uses_full_spot_history_not_just_snapshot_intersection).
    close_by_date: Dict[str, float] = {}
    for row in hist_spot_rows:
        d = replication_reference._parse_hist_date(row)
        if not d:
            continue
        try:
            c = float(row.get('close', 0) or 0)
        except (TypeError, ValueError):
            continue
        if c > 0:
            close_by_date[d] = c

    gamma_by_date, oi_by_date = _build_gamma_and_oi_by_date(
        expiry, hist_greek_rows, hist_oi_rows, close_by_date)

    dates_sorted = sorted(gamma_by_date.keys())

    accumulated_by_date: Dict[str, float] = {}
    for i, d in enumerate(dates_sorted):
        window_dates = dates_sorted[max(0, i - lookback_days):i + 1]
        if len(window_dates) < 2:
            continue
        window_greek_rows = [row for row in hist_greek_rows
                              if replication_reference._parse_hist_date(row) in window_dates]
        window_oi_rows = [row for row in hist_oi_rows
                           if replication_reference._parse_hist_date(row) in window_dates]
        window_spot_rows = [row for row in hist_spot_rows
                             if replication_reference._parse_hist_date(row) in window_dates]
        try:
            acc = replication_reference._accumulate_from_history(
                ticker, expiry, len(window_dates) - 1, seed_mode,
                window_greek_rows, window_oi_rows, window_spot_rows,
            )
        except ValueError:
            continue
        gamma_map_today = gamma_by_date.get(d, {})
        net_acc_gamma = sum(
            gamma_map_today.get((k, right), 0.0) * pos
            for (k, right), pos in acc.position_by_strike.items()
        )
        accumulated_by_date[d] = net_acc_gamma

    paired_dates = [r.date for r in labeled if r.date in accumulated_by_date]
    if not paired_dates:
        raise ValueError(
            f"No overlap between the accumulated-signal dates and the "
            f"labeled snapshot dates for {ticker} {expiry}."
        )

    by_date_record = {r.date: r for r in labeled}
    snapshot_vals = np.array([by_date_record[d].net_gamma_v1 for d in paired_dates])
    accumulated_vals = np.array([accumulated_by_date[d] for d in paired_dates])
    fwd_rv = np.array([by_date_record[d].fwd_realized_vol for d in paired_dates])

    return paired_dates, np.sign(snapshot_vals), np.sign(accumulated_vals), fwd_rv


def _safe_corr(a, b):
    if len(a) < 2 or np.std(a) == 0 or np.std(b) == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _run_falsifier_from_history(ticker: str, expiry: str,
                                 hist_greek_rows: List[dict], hist_oi_rows: List[dict],
                                 hist_spot_rows: List[dict],
                                 lookback_days: int = 150,
                                 forward_window_days: int = 5,
                                 seed_mode: str = 'replication',
                                 n_perms: int = _N_PERMUTATIONS,
                                 ) -> FalsifierResult:
    """Pure function over already-fetched historical rows. See module
    docstring for the design; see tests/test_backtest_accumulation_falsifier.py
    for the network-free regression coverage.
    """
    paired_dates, snap_sign, acc_sign, fwd_rv = _extract_paired_signals(
        ticker, expiry, hist_greek_rows, hist_oi_rows, hist_spot_rows,
        lookback_days=lookback_days, forward_window_days=forward_window_days,
        seed_mode=seed_mode,
    )

    n_disagree = int(np.sum(snap_sign != acc_sign))
    snapshot_corr = _safe_corr(snap_sign, fwd_rv)
    accumulated_corr = _safe_corr(acc_sign, fwd_rv)

    n = len(paired_dates)
    ones = np.ones(n)

    X_snap = np.column_stack([ones, snap_sign])
    r2_snap, _, _ = _ols_r2_and_tstat(fwd_rv, X_snap)

    X_both = np.column_stack([ones, snap_sign, acc_sign])
    r2_both, beta_both, tstats_both = _ols_r2_and_tstat(fwd_rv, X_both)
    acc_tstat = float(tstats_both[2]) if len(tstats_both) > 2 else 0.0
    acc_pvalue = float(2 * (1 - _scipy_stats.t.cdf(abs(acc_tstat), max(n - 3, 1))))

    delta_r2 = r2_both - r2_snap

    # Block-permutation p-value on delta_r2: shuffle the accumulated series
    # in contiguous blocks (preserves short-range autocorrelation) and see
    # how often a random accumulated series produces as large a delta_r2 as
    # the real one.
    rng = np.random.RandomState(0)
    perm_hits = 0
    if n >= 4:
        block_size = max(1, min(_PERM_BLOCK_SIZE, n // 2))
        n_blocks = max(1, n // block_size)
        for _ in range(n_perms):
            block_order = rng.permutation(n_blocks)
            shuffled = np.concatenate([
                acc_sign[b * block_size:(b + 1) * block_size] for b in block_order
            ])[:n]
            if len(shuffled) < n:
                shuffled = np.concatenate([shuffled, acc_sign[len(shuffled):n]])
            X_perm = np.column_stack([ones, snap_sign, shuffled])
            r2_perm, _, _ = _ols_r2_and_tstat(fwd_rv, X_perm)
            if (r2_perm - r2_snap) >= delta_r2:
                perm_hits += 1
        block_perm_p = perm_hits / n_perms
    else:
        block_perm_p = 1.0

    # Lead-lag k in {0,1,2}: shift the accumulated sign series k days FORWARD
    # relative to fwd_rv (i.e. does yesterday's/2-days-ago's accumulated read
    # predict today's forward-vol label better than today's own read).
    lead_lag_corr: Dict[int, float] = {}
    for k in (0, 1, 2):
        if n - k < 2:
            lead_lag_corr[k] = 0.0
            continue
        lead_lag_corr[k] = _safe_corr(acc_sign[:n - k], fwd_rv[k:])
    best_lag = max(lead_lag_corr, key=lambda kk: abs(lead_lag_corr[kk]))

    corr_acc_snap = _safe_corr(acc_sign, snap_sign)
    if n < _MIN_USABLE_DAYS:
        verdict = "INCONCLUSIVE"
    elif (abs(acc_tstat) > _TSTAT_THRESHOLD and abs(corr_acc_snap) < _CORR_INDEPENDENCE_THRESHOLD
          and block_perm_p < _PERM_P_THRESHOLD):
        verdict = "ACCUMULATION_ADDS_SIGNAL"
    elif delta_r2 < 0.01:
        verdict = "REDUNDANT"
    else:
        verdict = "INCONCLUSIVE"

    return FalsifierResult(
        ticker=ticker, expiry=expiry, n_days=n,
        n_days_signals_disagree=n_disagree,
        snapshot_corr=snapshot_corr, accumulated_corr=accumulated_corr,
        r2_snapshot_only=r2_snap, r2_with_accumulated=r2_both, delta_r2=delta_r2,
        accumulated_coef_tstat=acc_tstat, accumulated_coef_pvalue=acc_pvalue,
        block_perm_pvalue=block_perm_p,
        lead_lag_corr=lead_lag_corr, best_lag=best_lag, verdict=verdict,
    )


# Pooled-cells floor for the cross-sectional falsifier -- distinct from
# _MIN_USABLE_DAYS (which gates the single-ticker path): a thin ticker
# (e.g. SPY/QQQ in the WSL handoff's cached dataset, 62/21 days vs the other
# 10 tickers' 117-163) can't clear _MIN_USABLE_DAYS alone, but pooling
# across enough tickers can reach a real decision-grade sample without a new
# ThetaData pull. 200 mirrors the pre-registered battery's own pooled-cells
# floor (battery-consolidated-20260811.md TEST 3: "Min N: >=30 distinct days
# AND >=200 pooled cells, else no verdict").
_MIN_POOLED_DAYS = 200
_MIN_POOLED_TICKERS = 2


@dataclass
class PooledFalsifierResult:
    tickers: List[str]
    n_tickers: int
    per_ticker_n_days: Dict[str, int]
    n_pooled_days: int
    skipped: List[Tuple[str, str]] = field(default_factory=list)
    delta_r2: float = 0.0
    accumulated_coef_tstat: float = 0.0
    accumulated_coef_pvalue: float = 1.0
    block_perm_pvalue: float = 1.0
    corr_acc_snap_pooled: float = 0.0
    verdict: str = "INCONCLUSIVE"
    # Tickers whose accumulated (or snapshot) sign never changed within
    # their own sample window -- after ticker-fixed-effects demeaning,
    # THOSE tickers contribute exactly zero within-ticker variance to the
    # pooled regression, regardless of whether real cross-sectional signal
    # exists. A pooled result where every ticker lands here (found running
    # this against the WSL handoff's real 12-ticker cached dataset,
    # 2026-08-13) means delta_r2=0 is a DESIGN LIMITATION of the
    # within-ticker pooling approach, not evidence the accumulated read
    # carries no information -- see HANDOFF.md for the recommended
    # cross-sectional follow-up test.
    constant_signal_tickers: List[str] = field(default_factory=list)


def _run_pooled_falsifier_from_histories(
        ticker_histories: Dict[str, Tuple[str, List[dict], List[dict], List[dict]]],
        lookback_days: int = 150, forward_window_days: int = 5,
        seed_mode: str = 'replication', n_perms: int = _N_PERMUTATIONS,
        ) -> PooledFalsifierResult:
    """Cross-sectional pooled falsifier over MULTIPLE tickers.

    `ticker_histories`: {ticker: (expiry, hist_greek_rows, hist_oi_rows, hist_spot_rows)}.

    Pools per-ticker (snapshot sign, accumulated sign, forward RV) via a
    ticker-fixed-effects WITHIN transformation -- demean each series by its
    OWN ticker's mean before pooling -- so a ticker that's just
    structurally higher-vol, or has a persistently different snapshot/
    accumulated sign split, doesn't get misread as accumulated-vs-snapshot
    signal. This is the pooled analogue of the pre-registered battery's
    "strike-FE + day-FE regression (common-shock separation)"; here the
    fixed effect is per-TICKER since each ticker contributes its own day
    axis, not a shared one.

    Reuses _extract_paired_signals per-ticker (the exact same per-ticker
    logic the single-ticker falsifier uses) -- pooling never re-derives
    signal extraction. A ticker whose extraction fails (ValueError, e.g.
    insufficient history) or yields <2 days is skipped and reported in
    `skipped`, not silently dropped.
    """
    per_ticker_dates: Dict[str, List[str]] = {}
    per_ticker_snap: Dict[str, np.ndarray] = {}
    per_ticker_acc: Dict[str, np.ndarray] = {}
    per_ticker_rv: Dict[str, np.ndarray] = {}
    skipped: List[Tuple[str, str]] = []

    for ticker, (expiry, greek_rows, oi_rows, spot_rows) in ticker_histories.items():
        try:
            dates, snap, acc, rv = _extract_paired_signals(
                ticker, expiry, greek_rows, oi_rows, spot_rows,
                lookback_days=lookback_days, forward_window_days=forward_window_days,
                seed_mode=seed_mode,
            )
        except ValueError as e:
            skipped.append((ticker, str(e)))
            continue
        if len(dates) < 2:
            skipped.append((ticker, f"only {len(dates)} usable day(s)"))
            continue
        per_ticker_dates[ticker] = dates
        per_ticker_snap[ticker] = snap
        per_ticker_acc[ticker] = acc
        per_ticker_rv[ticker] = rv

    if not per_ticker_snap:
        raise ValueError(
            "No ticker produced usable paired signals for the pooled falsifier "
            f"(skipped: {skipped})."
        )

    per_ticker_n_days = {t: len(v) for t, v in per_ticker_snap.items()}

    constant_signal_tickers = sorted(
        t for t in per_ticker_snap
        if np.std(per_ticker_acc[t]) == 0 or np.std(per_ticker_snap[t]) == 0
    )

    snap_parts, acc_parts, rv_parts = [], [], []
    for ticker in per_ticker_snap:
        snap, acc, rv = per_ticker_snap[ticker], per_ticker_acc[ticker], per_ticker_rv[ticker]
        snap_parts.append(snap - np.mean(snap))
        acc_parts.append(acc - np.mean(acc))
        rv_parts.append(rv - np.mean(rv))

    snap_pooled = np.concatenate(snap_parts)
    acc_pooled = np.concatenate(acc_parts)
    rv_pooled = np.concatenate(rv_parts)
    n_pooled = len(snap_pooled)

    ones = np.ones(n_pooled)
    X_snap = np.column_stack([ones, snap_pooled])
    r2_snap, _, _ = _ols_r2_and_tstat(rv_pooled, X_snap)

    X_both = np.column_stack([ones, snap_pooled, acc_pooled])
    r2_both, beta_both, tstats_both = _ols_r2_and_tstat(rv_pooled, X_both)
    acc_tstat = float(tstats_both[2]) if len(tstats_both) > 2 else 0.0
    acc_pvalue = float(2 * (1 - _scipy_stats.t.cdf(abs(acc_tstat), max(n_pooled - 3, 1))))
    delta_r2 = r2_both - r2_snap

    # Block-permutation p-value: shuffle the accumulated series WITHIN each
    # ticker's own block, never across tickers -- cross-ticker shuffling
    # would destroy the within-ticker day structure and let cross-sectional
    # mixing masquerade as signal.
    rng = np.random.RandomState(0)
    perm_hits = 0
    for _ in range(n_perms):
        shuffled_parts = []
        for ticker in per_ticker_acc:
            acc = per_ticker_acc[ticker] - np.mean(per_ticker_acc[ticker])
            nT = len(acc)
            block_size = max(1, min(_PERM_BLOCK_SIZE, nT // 2)) if nT >= 2 else 1
            n_blocks = max(1, nT // block_size)
            block_order = rng.permutation(n_blocks)
            shuffled = np.concatenate(
                [acc[b * block_size:(b + 1) * block_size] for b in block_order])[:nT]
            if len(shuffled) < nT:
                shuffled = np.concatenate([shuffled, acc[len(shuffled):nT]])
            shuffled_parts.append(shuffled)
        shuffled_pooled = np.concatenate(shuffled_parts)
        X_perm = np.column_stack([ones, snap_pooled, shuffled_pooled])
        r2_perm, _, _ = _ols_r2_and_tstat(rv_pooled, X_perm)
        if (r2_perm - r2_snap) >= delta_r2:
            perm_hits += 1
    block_perm_p = perm_hits / n_perms

    corr_acc_snap = _safe_corr(acc_pooled, snap_pooled)

    if n_pooled < _MIN_POOLED_DAYS or len(per_ticker_snap) < _MIN_POOLED_TICKERS:
        verdict = "INCONCLUSIVE"
    elif (abs(acc_tstat) > _TSTAT_THRESHOLD and abs(corr_acc_snap) < _CORR_INDEPENDENCE_THRESHOLD
          and block_perm_p < _PERM_P_THRESHOLD):
        verdict = "ACCUMULATION_ADDS_SIGNAL"
    elif delta_r2 < 0.01:
        verdict = "REDUNDANT"
    else:
        verdict = "INCONCLUSIVE"

    return PooledFalsifierResult(
        tickers=sorted(per_ticker_snap.keys()), n_tickers=len(per_ticker_snap),
        per_ticker_n_days=per_ticker_n_days, n_pooled_days=n_pooled, skipped=skipped,
        delta_r2=delta_r2, accumulated_coef_tstat=acc_tstat, accumulated_coef_pvalue=acc_pvalue,
        block_perm_pvalue=block_perm_p, corr_acc_snap_pooled=corr_acc_snap, verdict=verdict,
        constant_signal_tickers=constant_signal_tickers,
    )


def run_pooled_falsifier(tickers: Optional[List[str]] = None,
                          lookback_days: int = 150, forward_window_days: int = 5,
                          seed_mode: str = 'replication',
                          cached_dir: Optional[str] = None,
                          ) -> PooledFalsifierResult:
    """Orchestrator: loads every cached ticker (or the given subset) via
    seed_data_loader and runs the pooled falsifier -- no ThetaData load.
    Defaults to the handoff package's seed_data/ folder in this repo, same
    default as run_falsifier(use_cached=True).
    """
    import os
    search_dir = cached_dir or os.path.join(
        os.path.dirname(__file__), "docs", "Dealer posistioning notes",
        "_extracted", "handoff_20260812", "seed_data")
    all_data = seed_data_loader.load_all_seed_data(search_dir)
    if tickers:
        all_data = {t: v for t, v in all_data.items() if t in tickers}
    if not all_data:
        raise ValueError(f"No cached seed_data found in {search_dir} for tickers={tickers}")

    import glob
    ticker_histories = {}
    for ticker, (greeks, oi, spot) in all_data.items():
        matches = glob.glob(os.path.join(search_dir, f"seed_data_{ticker}_*.json"))
        expiry = seed_data_loader.manifest_of(matches[0])["expiry"] if matches else "20261120"
        ticker_histories[ticker] = (expiry, greeks, oi, spot)

    return _run_pooled_falsifier_from_histories(
        ticker_histories, lookback_days=lookback_days,
        forward_window_days=forward_window_days, seed_mode=seed_mode,
    )


def format_pooled_falsifier_report(r: PooledFalsifierResult) -> str:
    lines = [
        f"POOLED falsifier -- {r.n_tickers} tickers, {r.n_pooled_days} pooled ticker-days",
        f"  per-ticker n_days: {r.per_ticker_n_days}",
    ]
    if r.skipped:
        lines.append(f"  skipped: {r.skipped}")
    if r.constant_signal_tickers:
        lines.append(
            f"  WARNING: {len(r.constant_signal_tickers)}/{r.n_tickers} ticker(s) had a "
            f"CONSTANT accumulated or snapshot sign across their whole window "
            f"(never flipped): {r.constant_signal_tickers}. Ticker-FE demeaning zeroes "
            f"these tickers' within-ticker contribution to delta_r2/t-stat regardless of "
            f"whether real cross-sectional signal exists -- if this list covers ALL "
            f"tickers, delta_r2=0 is a design limitation of within-ticker pooling, NOT "
            f"evidence the accumulated read carries no information. See HANDOFF.md."
        )
    lines += [
        f"  delta R^2 (ticker-FE, pooled):     {r.delta_r2:+.4f}",
        f"  accumulated coef t-stat:           {r.accumulated_coef_tstat:+.3f}  "
        f"(p={r.accumulated_coef_pvalue:.4f})",
        f"  corr(accumulated, snapshot) pooled: {r.corr_acc_snap_pooled:+.4f}",
        f"  block-permutation p:               {r.block_perm_pvalue:.4f}",
        f"  VERDICT:                           {r.verdict}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Cross-sectional falsifier -- the follow-up test HANDOFF.md §5 recommends.
#
# The day-level pooled falsifier above is mechanically blind to BETWEEN-ticker
# signal because the 150d-accumulated dealer-short read is CONSTANT within every
# ticker's own window (verified on the real 12-ticker cached dataset: acc unique
# set is {-1.0} for every ticker), and ticker-fixed-effects demeaning zeroes a
# constant series regardless of whether real cross-sectional variation exists
# (i.e. which tickers ended up long vs short, and whether that predicts anything).
#
# This test answers that between-ticker question directly: one accumulated sign
# per ticker (the dominant/final read) against that ticker's own realized-vol
# LEVEL over the same window, pooled as 12 (n_tickers) cross-sectional points.
# Dealer-hedge hypothesis: dealers short gamma amplify realized vol, so tickers
# whose accumulated book reads SHORT should have higher realized-vol levels than
# tickers whose book reads LONG -- and the accumulated read should sort tickers'
# realized-vol level better than the same-day snapshot read does.
#
# 12 data points is thin (the panel's quant seat flagged this as a live power
# concern) -- the verdict is gated on n_tickers >= _MIN_CROSS_TICKERS and a
# permutation p-value; below the floor it is INCONCLUSIVE, not a claim.
# ---------------------------------------------------------------------------

# Fewer than this many tickers and the cross-sectional regression has no power.
_MIN_CROSS_TICKERS = 8


@dataclass
class CrossSectionalFalsifierResult:
    tickers: List[str]
    n_tickers: int
    per_ticker_acc_sign: Dict[str, float] = field(default_factory=dict)
    per_ticker_snap_sign: Dict[str, float] = field(default_factory=dict)
    per_ticker_rv_level: Dict[str, float] = field(default_factory=dict)
    rho_acc_rv: float = 0.0
    rho_snap_rv: float = 0.0
    r2_acc: float = 0.0
    r2_snap: float = 0.0
    delta_r2: float = 0.0
    tstat_acc: float = 0.0
    pvalue_acc: float = 1.0
    mean_rv_short: float = 0.0
    mean_rv_long: float = 0.0
    n_short: int = 0
    n_long: int = 0
    permutation_pvalue: float = 1.0
    verdict: str = "INCONCLUSIVE"
    skipped: List[Tuple[str, str]] = field(default_factory=list)


def _realized_vol_level(hist_spot_rows: List[dict]) -> Optional[float]:
    """Annualized realized-vol LEVEL over the whole sample window for one
    ticker, computed directly from its spot close series (std of log returns,
    ddof=1, x sqrt(252)). Returns None if there aren't enough closes.
    """
    closes = []
    for row in hist_spot_rows:
        d = replication_reference._parse_hist_date(row)
        if not d:
            continue
        try:
            c = float(row.get("close", 0) or 0)
        except (TypeError, ValueError):
            continue
        if c > 0:
            closes.append(c)
    if len(closes) < 3:
        return None
    log_rets = np.diff(np.log(closes))
    if len(log_rets) < 2:
        return None
    return float(np.std(log_rets, ddof=1) * math.sqrt(TRADING_DAYS_PER_YEAR))


def _dominant_sign(series: np.ndarray) -> float:
    """Reduce a per-day sign series to one dominant scalar sign. For the
    accumulated arm this is the constant regime read (sign of the mean); for
    the snapshot arm it's the net direction (fraction of days short/long).
    Returns 0.0 when the series is empty or exactly balanced."""
    if len(series) == 0:
        return 0.0
    m = float(np.mean(series))
    if abs(m) < 1e-12:
        return 0.0
    return float(np.sign(m))


def _run_cross_sectional_falsifier_from_histories(
        ticker_histories: Dict[str, Tuple[str, List[dict], List[dict], List[dict]]],
        lookback_days: int = 150, forward_window_days: int = 5,
        seed_mode: str = "replication", n_perms: int = _N_PERMUTATIONS,
        ) -> CrossSectionalFalsifierResult:
    """Pure function: one (accumulated sign, snapshot sign, realized-vol level)
    per ticker, pooled cross-sectionally. Uses _extract_paired_signals per
    ticker (the SAME per-ticker signal logic the day-level falsifiers use),
    reduces each to a dominant sign, and computes the ticker's own realized-vol
    level from its spot series. Returns a CrossSectionalFalsifierResult.
    """
    per_ticker_acc: Dict[str, float] = {}
    per_ticker_snap: Dict[str, float] = {}
    per_ticker_rv: Dict[str, float] = {}
    skipped: List[Tuple[str, str]] = []

    for ticker, (expiry, greek_rows, oi_rows, spot_rows) in ticker_histories.items():
        try:
            _, snap, acc, _ = _extract_paired_signals(
                ticker, expiry, greek_rows, oi_rows, spot_rows,
                lookback_days=lookback_days, forward_window_days=forward_window_days,
                seed_mode=seed_mode,
            )
        except ValueError as e:
            skipped.append((ticker, str(e)))
            continue
        acc_sign = _dominant_sign(acc)
        snap_sign = _dominant_sign(snap)
        rv_level = _realized_vol_level(spot_rows)
        if rv_level is None:
            skipped.append((ticker, "insufficient spot history for rv level"))
            continue
        per_ticker_acc[ticker] = acc_sign
        per_ticker_snap[ticker] = snap_sign
        per_ticker_rv[ticker] = rv_level

    if not per_ticker_rv:
        raise ValueError(
            "No ticker produced a usable cross-sectional signal "
            f"(skipped: {skipped})."
        )

    tickers = sorted(per_ticker_rv)
    n = len(tickers)
    accs = np.array([per_ticker_acc[t] for t in tickers])
    snaps = np.array([per_ticker_snap[t] for t in tickers])
    rvs = np.array([per_ticker_rv[t] for t in tickers])

    rho_acc = _safe_corr(accs, rvs)
    rho_snap = _safe_corr(snaps, rvs)

    ones = np.ones(n)
    r2_acc, _, tstats_acc = _ols_r2_and_tstat(rvs, np.column_stack([ones, accs]))
    r2_snap, _, _ = _ols_r2_and_tstat(rvs, np.column_stack([ones, snaps]))
    tstat_acc = float(tstats_acc[1]) if len(tstats_acc) > 1 else 0.0
    pvalue_acc = float(2 * (1 - _scipy_stats.t.cdf(abs(tstat_acc), max(n - 2, 1))))
    delta_r2 = r2_acc - r2_snap

    short_idx = accs < 0
    long_idx = accs > 0
    n_short = int(np.sum(short_idx))
    n_long = int(np.sum(long_idx))
    mean_rv_short = float(np.mean(rvs[short_idx])) if n_short else 0.0
    mean_rv_long = float(np.mean(rvs[long_idx])) if n_long else 0.0

    # Permutation p-value on |corr(accumulated sign, rv level)|: shuffle the
    # accumulated signs across tickers (breaks the ticker->sign pairing while
    # preserving the realized-vol levels) and see how often a random shuffle
    # produces as extreme a |rho| as the observed one.
    rng = np.random.RandomState(0)
    perm_hits = 0
    obs_abs_rho = abs(rho_acc)
    for _ in range(n_perms):
        perm = rng.permutation(accs)
        if abs(_safe_corr(perm, rvs)) >= obs_abs_rho:
            perm_hits += 1
    perm_p = perm_hits / n_perms

    if n < _MIN_CROSS_TICKERS:
        verdict = "INCONCLUSIVE"
    # All tickers the SAME accumulated sign => the sign axis is degenerate (the
    # cross-sectional test can't sort on sign -- every point is the same value).
    # This is the BASE / resting regime (verified on the real 12-ticker cached
    # dataset: every ticker accumulates SHORT), NOT evidence about magnitude.
    # Signal, if any, lives on the MAGNITUDE axis (net exposure / SVI cheap-rich
    # size), which the sign-only test is blind to -- see the SVI-magnitude axis.
    elif n_short == n or n_long == n:
        verdict = "BASE"
    elif abs(tstat_acc) > _TSTAT_THRESHOLD and perm_p < _PERM_P_THRESHOLD:
        verdict = "ACCUMULATION_CROSS_SECTIONAL_SIGNAL"
    elif delta_r2 < 0.01:
        verdict = "REDUNDANT"
    else:
        verdict = "INCONCLUSIVE"

    return CrossSectionalFalsifierResult(
        tickers=tickers, n_tickers=n,
        per_ticker_acc_sign=per_ticker_acc, per_ticker_snap_sign=per_ticker_snap,
        per_ticker_rv_level=per_ticker_rv,
        rho_acc_rv=rho_acc, rho_snap_rv=rho_snap,
        r2_acc=r2_acc, r2_snap=r2_snap, delta_r2=delta_r2,
        tstat_acc=tstat_acc, pvalue_acc=pvalue_acc,
        mean_rv_short=mean_rv_short, mean_rv_long=mean_rv_long,
        n_short=n_short, n_long=n_long,
        permutation_pvalue=perm_p, verdict=verdict, skipped=skipped,
    )


def run_cross_sectional_falsifier(tickers: Optional[List[str]] = None,
                                  lookback_days: int = 150,
                                  forward_window_days: int = 5,
                                  seed_mode: str = "replication",
                                  cached_dir: Optional[str] = None,
                                  ) -> CrossSectionalFalsifierResult:
    """Orchestrator: loads every cached ticker (or the given subset) via
    seed_data_loader and runs the cross-sectional falsifier -- no ThetaData
    load. Defaults to the handoff package's seed_data/ folder, same default as
    run_pooled_falsifier."""
    import os
    search_dir = cached_dir or os.path.join(
        os.path.dirname(__file__), "docs", "Dealer posistioning notes",
        "_extracted", "handoff_20260812", "seed_data")
    all_data = seed_data_loader.load_all_seed_data(search_dir)
    if tickers:
        all_data = {t: v for t, v in all_data.items() if t in tickers}
    if not all_data:
        raise ValueError(f"No cached seed_data found in {search_dir} for tickers={tickers}")

    import glob
    ticker_histories = {}
    for ticker, (greeks, oi, spot) in all_data.items():
        matches = glob.glob(os.path.join(search_dir, f"seed_data_{ticker}_*.json"))
        expiry = seed_data_loader.manifest_of(matches[0])["expiry"] if matches else "20261120"
        ticker_histories[ticker] = (expiry, greeks, oi, spot)

    return _run_cross_sectional_falsifier_from_histories(
        ticker_histories, lookback_days=lookback_days,
        forward_window_days=forward_window_days, seed_mode=seed_mode,
    )


def run_cross_sectional_falsifier_cached(
        tickers: Optional[List[str]] = None, lookback_days: int = 150,
        forward_window_days: int = 5, seed_mode: str = "replication",
        cached_dir: Optional[str] = None,
        ) -> CrossSectionalFalsifierResult:
    """Caching wrapper over run_cross_sectional_falsifier: loads a saved result
    if present for (mode, fitter, tickers, lookback, seed_mode), else computes
    and saves. FALSIFIER_FORCE=1 bypasses. Avoids the ~8-min recompute."""
    key = {"tickers": ",".join(sorted(tickers)) if tickers else "all",
           "lb": lookback_days, "sm": seed_mode}
    if not _force_recompute():
        cached = _load_result("cross", key)
        if cached is not None:
            print(f"  [cache] loaded cross-sectional result for fitter={_fitter_used()} "
                  f"(FALSIFIER_FORCE=1 to recompute)")
            return _hydrate(CrossSectionalFalsifierResult, cached)
    res = run_cross_sectional_falsifier(
        tickers=tickers, lookback_days=lookback_days,
        forward_window_days=forward_window_days, seed_mode=seed_mode,
        cached_dir=cached_dir)
    _save_result("cross", key, res)
    return res


def format_cross_sectional_falsifier_report(r: CrossSectionalFalsifierResult) -> str:
    lines = [
        f"CROSS-SECTIONAL falsifier -- {r.n_tickers} tickers, 1 (sign, rv-level) point each",
    ]
    if r.skipped:
        lines.append(f"  skipped: {r.skipped}")
    lines += [
        f"  per-ticker accumulated sign:   {r.per_ticker_acc_sign}",
        f"  per-ticker realized-vol level: {r.per_ticker_rv_level}",
        f"  corr(accum sign, rv level):    {r.rho_acc_rv:+.4f}",
        f"  corr(snapshot sign, rv level): {r.rho_snap_rv:+.4f}",
        f"  R^2(acc): {r.r2_acc:.4f}  R^2(snap): {r.r2_snap:.4f}  delta: {r.delta_r2:+.4f}",
        f"  accumulated coef t-stat:       {r.tstat_acc:+.3f}  (p={r.pvalue_acc:.4f})",
        f"  mean rv (accum SHORT): {r.mean_rv_short:.4f}  (accum LONG): {r.mean_rv_long:.4f}  "
        f"[n_short={r.n_short}, n_long={r.n_long}]",
        f"  permutation p:                 {r.permutation_pvalue:.4f}",
        f"  VERDICT:                       {r.verdict}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SVI-MAGNITUDE cross-sectional axis (Jason's idea, 2026-08-13).
#
# The sign axis is degenerate: every ticker accumulates the SAME sign (BASE,
# verified all-SHORT on the real 12-ticker dataset), so a sign-only cross-
# sectional test can't sort anything. The real between-ticker variation lives
# on the MAGNITUDE axis -- HOW far each ticker's smile is from the SSVI
# reference (cheap/rich), and how big the OI-weighted cheap/rich imbalance is.
#
# This axis uses the SVI cheap/rich marking (svi_rp.calibrate_ssvi ->
# SviRpReference.mark_chain) as a MAGNITUDE signal per ticker, tested against
# that ticker's own realized-vol level over the same window. Hypothesis:
# tickers whose smile is more "distorted" (larger |market_iv - ref|) or whose
# cheap/rich OI imbalance is larger carry more dealer positioning / realized
# vol. This is exactly the "bring SVI in for testing as a magnitude sign"
# directive -- nothing canonical, just a testable arm.
# ---------------------------------------------------------------------------

@dataclass
class SviMagnitudeFalsifierResult:
    tickers: List[str]
    n_tickers: int
    per_ticker_magnitude: Dict[str, float] = field(default_factory=dict)
    per_ticker_net_seed: Dict[str, float] = field(default_factory=dict)
    per_ticker_rv_level: Dict[str, float] = field(default_factory=dict)
    rho_mag_rv: float = 0.0
    r2_mag: float = 0.0
    tstat_mag: float = 0.0
    pvalue_mag: float = 1.0
    permutation_pvalue: float = 1.0
    verdict: str = "INCONCLUSIVE"
    skipped: List[Tuple[str, str]] = field(default_factory=list)


def _otm_chain_at_date(greek_rows: List[dict], oi_rows: List[dict],
                       spot: float, expiry: str, d: str,
                       ) -> Tuple[Dict[Tuple[float, str], float],
                                  Dict[Tuple[float, str], int]]:
    """Build the OTM chain_iv + oi_by dicts for ONE date from cached rows
    (calls above spot, puts below -- same OTM restriction svi_rp expects)."""
    expiry_date = datetime.strptime(expiry, "%Y%m%d")
    T = max((expiry_date - datetime.strptime(d, "%Y%m%d")).days, 1) / 365.0
    iv, oi = {}, {}
    for row in greek_rows:
        rd = replication_reference._parse_hist_date(row)
        if rd != d:
            continue
        try:
            k = float(row['strike']) if float(row['strike']) < 10000 else strike_from_theta(int(float(row['strike'])))
            right = str(row['right']).upper()[:1]
            v = float(row.get('implied_vol', 0) or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if v > 0 and ((right == 'C' and k > spot) or (right == 'P' and k < spot)):
            iv[(k, right)] = v
    for row in oi_rows:
        rd = replication_reference._parse_hist_date(row)
        if rd != d:
            continue
        try:
            k = strike_from_theta(int(float(row['strike'])))
            right = str(row['right']).upper()[:1]
            o = int(float(row.get('open_interest', 0) or 0))
        except (KeyError, TypeError, ValueError):
            continue
        oi[(k, right)] = o
    return iv, oi


def _run_svi_magnitude_cross_sectional_from_histories(
        ticker_histories: Dict[str, Tuple[str, List[dict], List[dict], List[dict]]],
        n_perms: int = _N_PERMUTATIONS,
        ) -> SviMagnitudeFalsifierResult:
    """Per-ticker SVI cheap/rich MAGNITUDE vs realized-vol level, pooled
    cross-sectionally. Uses the LAST available date's OTM chain in each ticker's
    cached history (no network), calibrates the SSVI reference, and takes two
    magnitude measures:
      - `magnitude`: OI-weighted mean |market_iv - svi_ref| over the OTM set
        (how far the market smile sits from the reference -- the distortion).
      - `net_seed`: long_oi - short_oi from SviRpReference.seed (the signed
        OI-weighted cheap/rich imbalance).
    Tests each against the ticker's realized-vol level (Spearman + OLS t-stat
    + permutation p)."""
    import svi_rp
    per_ticker_mag: Dict[str, float] = {}
    per_ticker_net: Dict[str, float] = {}
    per_ticker_rv: Dict[str, float] = {}
    skipped: List[Tuple[str, str]] = []

    for ticker, (expiry, greek_rows, oi_rows, spot_rows) in ticker_histories.items():
        rv_level = _realized_vol_level(spot_rows)
        if rv_level is None:
            skipped.append((ticker, "insufficient spot history for rv level"))
            continue
        # last date present in both greeks and spot
        g_dates = sorted({replication_reference._parse_hist_date(r) for r in greek_rows
                          if replication_reference._parse_hist_date(r)})
        if not g_dates:
            skipped.append((ticker, "no greek dates"))
            continue
        d = g_dates[-1]
        spot_map = {}
        for r in spot_rows:
            rd = replication_reference._parse_hist_date(r)
            if rd:
                try:
                    c = float(r.get('close', 0) or 0)
                except (TypeError, ValueError):
                    c = 0.0
                if c > 0:
                    spot_map[rd] = c
        if d not in spot_map:
            skipped.append((ticker, "last greek date missing spot close"))
            continue
        spot = spot_map[d]
        chain_iv, oi_by = _otm_chain_at_date(greek_rows, oi_rows, spot, expiry, d)
        if len(chain_iv) < 6:
            skipped.append((ticker, f"only {len(chain_iv)} OTM strikes on {d}"))
            continue
        try:
            ref = svi_rp.calibrate_ssvi(chain_iv, spot,
                                        max((datetime.strptime(expiry, "%Y%m%d")
                                             - datetime.strptime(d, "%Y%m%d")).days, 1) / 365.0,
                                        oi_by=oi_by)
        except Exception as e:
            skipped.append((ticker, f"svi calibrate failed: {type(e).__name__}: {str(e)[:60]}"))
            continue
        marks = ref.mark_chain(chain_iv, oi_by)
        total_oi = sum(oi_by.get((k, r), 0) for (k, r) in chain_iv)
        if total_oi <= 0:
            skipped.append((ticker, "no OI on OTM chain"))
            continue
        mag = sum(abs(m[4]) * oi_by.get((m[0], m[1]), 0) for m in marks) / total_oi
        _, _, net_seed = ref.seed(chain_iv, oi_by)
        per_ticker_mag[ticker] = mag
        per_ticker_net[ticker] = float(net_seed)
        per_ticker_rv[ticker] = rv_level

    if not per_ticker_mag:
        raise ValueError(f"No ticker produced a usable SVI-magnitude signal (skipped: {skipped})")

    tickers = sorted(per_ticker_mag)
    n = len(tickers)
    mags = np.array([per_ticker_mag[t] for t in tickers])
    nets = np.array([per_ticker_net[t] for t in tickers])
    rvs = np.array([per_ticker_rv[t] for t in tickers])

    rho_mag = _safe_corr(mags, rvs)
    rho_net = _safe_corr(nets, rvs)
    ones = np.ones(n)
    r2_mag, _, tstats_mag = _ols_r2_and_tstat(rvs, np.column_stack([ones, mags]))
    tstat_mag = float(tstats_mag[1]) if len(tstats_mag) > 1 else 0.0
    pvalue_mag = float(2 * (1 - _scipy_stats.t.cdf(abs(tstat_mag), max(n - 2, 1))))

    rng = np.random.RandomState(0)
    perm_hits = 0
    obs_abs = max(abs(rho_mag), abs(rho_net))
    for _ in range(n_perms):
        pm = rng.permutation(mags)
        pn = rng.permutation(nets)
        if max(abs(_safe_corr(pm, rvs)), abs(_safe_corr(pn, rvs))) >= obs_abs:
            perm_hits += 1
    perm_p = perm_hits / n_perms

    if n < _MIN_CROSS_TICKERS:
        verdict = "INCONCLUSIVE"
    elif abs(tstat_mag) > _TSTAT_THRESHOLD and perm_p < _PERM_P_THRESHOLD:
        verdict = "SVI_MAGNITUDE_CROSS_SECTIONAL_SIGNAL"
    else:
        verdict = "INCONCLUSIVE"

    return SviMagnitudeFalsifierResult(
        tickers=tickers, n_tickers=n,
        per_ticker_magnitude=per_ticker_mag, per_ticker_net_seed=per_ticker_net,
        per_ticker_rv_level=per_ticker_rv,
        rho_mag_rv=rho_mag, r2_mag=r2_mag, tstat_mag=tstat_mag, pvalue_mag=pvalue_mag,
        permutation_pvalue=perm_p, verdict=verdict, skipped=skipped,
    )


def run_svi_magnitude_cross_sectional_falsifier(
        tickers: Optional[List[str]] = None,
        cached_dir: Optional[str] = None,
        ) -> SviMagnitudeFalsifierResult:
    """Orchestrator (offline, no ThetaData): loads cached tickers via
    seed_data_loader and runs the SVI-magnitude cross-sectional axis."""
    import os
    search_dir = cached_dir or os.path.join(
        os.path.dirname(__file__), "docs", "Dealer posistioning notes",
        "_extracted", "handoff_20260812", "seed_data")
    all_data = seed_data_loader.load_all_seed_data(search_dir)
    if tickers:
        all_data = {t: v for t, v in all_data.items() if t in tickers}
    if not all_data:
        raise ValueError(f"No cached seed_data found in {search_dir} for tickers={tickers}")
    import glob
    ticker_histories = {}
    for ticker, (greeks, oi, spot) in all_data.items():
        matches = glob.glob(os.path.join(search_dir, f"seed_data_{ticker}_*.json"))
        expiry = seed_data_loader.manifest_of(matches[0])["expiry"] if matches else "20261120"
        ticker_histories[ticker] = (expiry, greeks, oi, spot)
    return _run_svi_magnitude_cross_sectional_from_histories(ticker_histories)


def run_svi_magnitude_cross_sectional_falsifier_cached(
        tickers: Optional[List[str]] = None, cached_dir: Optional[str] = None,
        ) -> SviMagnitudeFalsifierResult:
    """Caching wrapper over run_svi_magnitude_cross_sectional_falsifier.
    FALSIFIER_FORCE=1 bypasses."""
    key = {"tickers": ",".join(sorted(tickers)) if tickers else "all"}
    if not _force_recompute():
        cached = _load_result("svimag", key)
        if cached is not None:
            print(f"  [cache] loaded SVI-magnitude result for fitter={_fitter_used()} "
                  f"(FALSIFIER_FORCE=1 to recompute)")
            return _hydrate(SviMagnitudeFalsifierResult, cached)
    res = run_svi_magnitude_cross_sectional_falsifier(tickers=tickers, cached_dir=cached_dir)
    _save_result("svimag", key, res)
    return res


def format_svi_magnitude_falsifier_report(r: SviMagnitudeFalsifierResult) -> str:
    lines = [
        f"SVI-MAGNITUDE cross-sectional falsifier -- {r.n_tickers} tickers",
    ]
    if r.skipped:
        lines.append(f"  skipped: {r.skipped}")
    lines += [
        f"  per-ticker SVI magnitude (OI-wtd |IV-ref|): {r.per_ticker_magnitude}",
        f"  per-ticker SVI net seed (long-short OI):    {r.per_ticker_net_seed}",
        f"  per-ticker realized-vol level:              {r.per_ticker_rv_level}",
        f"  corr(SVI magnitude, rv level):              {r.rho_mag_rv:+.4f}",
        f"  R^2(SVI magnitude): {r.r2_mag:.4f}  t-stat: {r.tstat_mag:+.3f}  (p={r.pvalue_mag:.4f})",
        f"  permutation p:                              {r.permutation_pvalue:.4f}",
        f"  VERDICT:                                    {r.verdict}",
    ]
    return "\n".join(lines)


def run_falsifier(ticker: str, expiry: Optional[str] = None,
                   target_years: float = 0.25,
                   lookback_days: int = 150, forward_window_days: int = 5,
                   seed_mode: str = 'replication',
                   use_cached: bool = True,
                   cached_path: Optional[str] = None,
                   cached_dir: Optional[str] = None,
                   ) -> FalsifierResult:
    """Orchestrator. use_cached=True (default) loads the 12-ticker, 150-day
    dataset already pulled once and saved to disk via seed_data_loader.py --
    no ThetaData proxy load. Pass cached_path to a specific
    seed_data_<TICKER>_<expiry>_150d.json, or cached_dir to scan a directory
    for it; defaults to the handoff package's seed_data/ folder in this repo.
    Pass use_cached=False for a live pull (same cost caveats as
    backtest_stage3.run_backtest / replication_reference.compute_accumulated_position).
    """
    if use_cached:
        if cached_path:
            greeks, oi, spot = seed_data_loader.load_seed_data(cached_path)
            if expiry is None:
                manifest = seed_data_loader.manifest_of(cached_path)
                expiry = manifest["expiry"]
        else:
            import os
            search_dir = cached_dir or os.path.join(
                os.path.dirname(__file__), "docs", "Dealer posistioning notes",
                "_extracted", "handoff_20260812", "seed_data")
            all_data = seed_data_loader.load_all_seed_data(search_dir)
            if ticker not in all_data:
                raise ValueError(f"No cached seed_data for {ticker} in {search_dir}")
            greeks, oi, spot = all_data[ticker]
            if expiry is None:
                import glob
                # match the file load_all_seed_data actually used (it globs sorted
                # and overwrites by ticker -> last sorted match wins), so the
                # expiry label agrees with the loaded data.
                matches = sorted(glob.glob(os.path.join(search_dir, f"seed_data_{ticker}_*.json")))
                expiry = seed_data_loader.manifest_of(matches[-1])["expiry"] if matches else "20261120"
        return _run_falsifier_from_history(
            ticker, expiry, greeks, oi, spot,
            lookback_days=lookback_days, forward_window_days=forward_window_days,
            seed_mode=seed_mode,
        )

    td = ThetaDataController()
    try:
        resolved_expiry, _ = expiry_selector.resolve_expiration(td, ticker, expiry, target_years)
        end_date = datetime.now()
        start_date = end_date - __import__("datetime").timedelta(days=int(lookback_days * 2.2) + 5)
        start_str, end_str = start_date.strftime("%Y%m%d"), end_date.strftime("%Y%m%d")
        greeks = td.option_bulk_hist_greeks(ticker, resolved_expiry, start_str, end_str)
        oi = td.option_bulk_hist_oi(ticker, resolved_expiry, start_str, end_str)
        spot = td.hist_stock_eod(ticker, start_str, end_str)
    finally:
        td.close()
    return _run_falsifier_from_history(
        ticker, resolved_expiry, greeks, oi, spot,
        lookback_days=lookback_days, forward_window_days=forward_window_days,
        seed_mode=seed_mode,
    )


def run_falsifier_cached(ticker: str, expiry: Optional[str] = None,
                         lookback_days: int = 150, forward_window_days: int = 5,
                         seed_mode: str = "replication", use_cached: bool = True,
                         cached_path: Optional[str] = None,
                         cached_dir: Optional[str] = None,
                         ) -> FalsifierResult:
    """Caching wrapper over run_falsifier (per-ticker). FALSIFIER_FORCE=1
    bypasses. Keyed by (ticker, expiry, lookback, seed_mode, fitter)."""
    key = {"ticker": ticker, "exp": expiry or "auto", "lb": lookback_days,
           "fw": forward_window_days, "sm": seed_mode}
    if use_cached and not _force_recompute():
        cached = _load_result("falsifier", key)
        if cached is not None:
            print(f"  [cache] loaded falsifier result for {ticker} "
                  f"(fitter={_fitter_used()}, FALSIFIER_FORCE=1 to recompute)")
            return _hydrate(FalsifierResult, cached)
    res = run_falsifier(
        ticker, expiry=expiry, lookback_days=lookback_days,
        forward_window_days=forward_window_days, seed_mode=seed_mode,
        use_cached=use_cached, cached_path=cached_path, cached_dir=cached_dir)
    if use_cached:
        _save_result("falsifier", key, res)
    return res


def format_falsifier_report(r: FalsifierResult) -> str:
    lines = [
        f"{r.ticker} {r.expiry} -- OOS falsifier (n={r.n_days} usable days, "
        f"{r.n_days_signals_disagree} days snapshot/accumulated disagree)",
        f"  snapshot corr(sign, fwd_rv):     {r.snapshot_corr:+.4f}",
        f"  accumulated corr(sign, fwd_rv):  {r.accumulated_corr:+.4f}",
        f"  R^2 snapshot only:               {r.r2_snapshot_only:.4f}",
        f"  R^2 + accumulated:                {r.r2_with_accumulated:.4f}",
        f"  delta R^2:                        {r.delta_r2:+.4f}",
        f"  accumulated coef t-stat:          {r.accumulated_coef_tstat:+.3f}  (p={r.accumulated_coef_pvalue:.4f})",
        f"  block-permutation p:              {r.block_perm_pvalue:.4f}",
        f"  lead-lag corr by k:                {r.lead_lag_corr}",
        f"  best lag:                         k={r.best_lag}",
        f"  VERDICT:                          {r.verdict}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    arg = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    if arg == "--pooled":
        pooled_result = run_pooled_falsifier()
        print(format_pooled_falsifier_report(pooled_result))
    elif arg == "--cross":
        cross_result = run_cross_sectional_falsifier_cached()
        print(format_cross_sectional_falsifier_report(cross_result))
    elif arg == "--svimag":
        svimag_result = run_svi_magnitude_cross_sectional_falsifier_cached()
        print(format_svi_magnitude_falsifier_report(svimag_result))
    else:
        result = run_falsifier_cached(arg, use_cached=True)
        print(format_falsifier_report(result))
