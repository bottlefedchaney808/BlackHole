#!/usr/bin/env python3
# dealer_positioning.py
# Dealer Positioning / Hedging Heatmap Module
# Live ThetaData: gamma from bulk greeks, OI from separate endpoint.

import math
import inspect
import warnings
import os
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Tuple, List, Dict, Optional
from collections import defaultdict

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import FancyBboxPatch
from matplotlib.ticker import FuncFormatter
import matplotlib.patheffects as pe

from thetadata_client import ThetaDataController, strike_from_theta
import expiry_selector
import replication_reference
import vol_surface_reference
from vs_utils import timestamped_output_dir

warnings.filterwarnings("ignore", category=FutureWarning, module="pandas")

# Was already 365 here (unlike variance_swap_live.py/variance_swap_screener.py,
# which used 252) -- now delegates to expiry_selector.DEFAULT_A so all three
# modules are provably the same constant instead of independently agreeing by
# coincidence. See expiry_selector.py's module docstring.
DEFAULT_A = expiry_selector.DEFAULT_A
RISK_FREE_RATE = 0.05
CONTRACT_MULTIPLIER = 100

# ---- Greek exposure scaling (Gamma/Delta/Vanna/Charm comparison chart) ----
# "Shares" exposure per strike = sign * raw_greek * OI * CONTRACT_MULTIPLIER,
# i.e. how many shares dealers must trade to stay hedged for a 1-unit move in
# the relevant variable, with the same call=+/put=- convention as gamma (see
# _dealer_sign). Vanna and charm need an extra unit conversion on top of that
# because ThetaData's exact convention for those two fields hasn't been
# confirmed against a live response in this environment (no network egress to
# api.potatohedge.com from here) -- gamma/delta units are already established
# elsewhere in this file and in thetadata_controller usage, so those two are
# trusted as-is.
#
# ASSUMPTIONS below -- verify against a real bulk_snapshot/option/all_greeks
# response (run debug_print_greek_fields() once) and adjust if wrong:
#   - VANNA_PP_SCALE: assumes raw vanna is dDelta/dVol with vol in decimal
#     (e.g. 0.20 for 20%), so exposure "per 1 vol point" = vanna * 0.01.
#   - CHARM_ANNUALIZED: assumes raw charm is dDelta/dT with T in years (the
#     ThetaData convention used elsewhere for time inputs in this codebase),
#     so a *daily* charm exposure needs /365. If ThetaData's charm field is
#     already per-day, set this to False.
VANNA_PP_SCALE = 0.01
CHARM_ANNUALIZED = True

# Candidate key names to try (in order) when pulling a greek out of a
# ThetaData all_greeks bulk row, mirroring the case-variant fallback pattern
# already used in main.py's model-comparison path.
_GREEK_FIELD_CANDIDATES = {
    'delta': ['delta', 'Delta', 'DELTA'],
    'vanna': ['vanna', 'Vanna', 'VANNA'],
    'charm': ['charm', 'Charm', 'CHARM', 'delta_decay', 'deltaDecay'],
}

# ---------- Data structures ----------
@dataclass
class GammaRecord:
    strike: float
    expiry: str
    right: str
    gamma: float
    dollar_gamma: float
    iv: float
    oi: int
    bid: float
    ask: float
    tte: float
    # Delta/vanna/charm are "best effort" -- pulled from the same ThetaData
    # all_greeks bulk row as gamma, but that field's exact key names/units
    # haven't been verified against a live response (see _extract_greek_field
    # below), so these default to NaN when the row doesn't have a usable value
    # instead of raising. Downstream aggregation skips NaNs per-greek
    # independently, so a missing vanna field doesn't take down delta/gamma.
    delta: float = float('nan')
    vanna: float = float('nan')
    charm: float = float('nan')
    # The sign actually applied to this record when it was aggregated into
    # gamma_by_strike/delta_by_strike/etc -- either the flat _dealer_sign(right)
    # (sign_model='oi_heuristic') or a replication-derived sign/gate
    # (sign_model='replication', see _resolve_sign). Stored per-record so the
    # gamma-flip surface calc downstream can reuse the SAME sign each record
    # was aggregated with, instead of re-deriving it (and potentially
    # disagreeing with the by-strike sums above it in the same result).
    applied_sign: float = float('nan')

@dataclass
class DealerPositioningResult:
    ticker: str
    spot: float
    forward: float
    dividend_yield: float
    total_net_gamma: float
    total_net_dollar_gamma: float
    hedge_requirement: float
    gamma_flip_level: float
    highest_gamma_strike: float
    total_gamma_exposure: float
    gamma_records: List[GammaRecord]
    strike_grid: np.ndarray
    gamma_by_strike: np.ndarray
    dollar_gamma_by_strike: np.ndarray
    oi_by_strike: np.ndarray
    num_expiries: int
    num_records: int
    surface_spot: np.ndarray
    surface_iv: np.ndarray
    surface_gamma: np.ndarray
    gamma_by_spot: np.ndarray
    # ---- Greek exposure comparison (Gamma/Delta/Vanna/Charm by strike) ----
    # Share-denominated (not dollar-denominated) signed dealer exposure per
    # strike, aligned to strike_grid, for the 4-panel comparison chart. Any
    # strike where a given greek was entirely unavailable across all records
    # is left at 0.0 -- see plot_greek_exposure_comparison.
    gamma_shares_by_strike: np.ndarray = None
    delta_shares_by_strike: np.ndarray = None
    vanna_shares_by_strike: np.ndarray = None
    charm_shares_by_strike: np.ndarray = None
    greek_days_window: Optional[int] = None
    has_delta_data: bool = False
    has_vanna_data: bool = False
    has_charm_data: bool = False
    sign_model: str = 'oi_heuristic'
    # Option-contract equivalent of hedge_requirement -- see the note where
    # this is computed in compute_dealer_positioning for why it's shown
    # alongside the share-based number, not instead of it.
    hedge_equiv_option_strike: Optional[float] = None
    hedge_equiv_option_right: Optional[str] = None
    hedge_equiv_option_contracts: float = float('nan')
    # Whether the ANCHOR expiry's Greek exposure was built from real
    # multi-day accumulation (replication_reference.compute_accumulated_position_for_expiry)
    # rather than a same-day OI snapshot -- see compute_dealer_positioning's
    # `accumulate` param. Sourced onto the result object (not inferred from
    # a docstring or hardcoded chart label) so every render can honestly
    # state which model actually executed -- see sign_model_render_label().
    accumulate: bool = False
    # Call/put split of net vanna exposure (same CONTRACT_MULTIPLIER *
    # VANNA_PP_SCALE scaling as vanna_shares_by_strike -- their sum equals
    # sum(vanna_shares_by_strike) exactly). Computed here, once, so
    # options_chain_scanner.py's vanna panel can read it verbatim instead of
    # re-deriving its own call/put split from a second independent chain
    # fetch (the "two-vanna" bug the CARL audit found still live).
    vanna_call_shares: float = float('nan')
    vanna_put_shares: float = float('nan')

# ---------- Helpers ----------
def _to_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return np.nan

def _extract_greek_field(row: dict, greek: str) -> float:
    """Best-effort pull of a greek value out of a ThetaData all_greeks bulk
    row. Returns NaN (not an exception) if none of the candidate keys are
    present or the value doesn't parse -- callers should treat NaN as "not
    available for this record" rather than "this record is broken"."""
    for key in _GREEK_FIELD_CANDIDATES.get(greek, [greek]):
        if key in row and row[key] not in (None, ''):
            val = _to_float(row[key])
            if not math.isnan(val):
                return val
    return float('nan')


def debug_print_greek_fields(ticker: str, expiry: Optional[str] = None) -> None:
    """One-shot helper: fetch a single bulk_snapshot/option/all_greeks row and
    print every field name/value ThetaData actually returned. Run this once
    against a live account to confirm the delta/vanna/charm key names and
    units the VANNA_PP_SCALE / CHARM_ANNUALIZED assumptions above rely on --
    this sandbox has no network path to api.potatohedge.com to verify it in
    advance."""
    td = ThetaDataController()
    try:
        exp = expiry or td.list_expirations(ticker)[0]
        rows = td.option_bulk_greeks(ticker, exp)
        if not rows:
            print(f"No rows returned for {ticker} {exp}")
            return
        print(f"Fields in {ticker} {exp} all_greeks row:")
        for k, v in rows[0].items():
            print(f"  {k!r}: {v!r}")
    finally:
        td.close()


def norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)

def bs_gamma(S: float, K: float, T: float, r: float, q: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    sigma_sqrt_T = sigma * math.sqrt(T)
    if sigma_sqrt_T <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / sigma_sqrt_T
    return math.exp(-q * T) * norm_pdf(d1) / (S * sigma_sqrt_T)

def bs_gamma_vec(S: float, K: np.ndarray, T: np.ndarray, r: float, q: float, sigma: float) -> np.ndarray:
    """Vectorized Black-Scholes gamma over arrays of strikes/TTEs at a single
    (spot, iv) point -- used to build the dealer-gamma-vs-spot surface without
    an O(spots * ivs * records) pure-Python triple loop."""
    with np.errstate(divide='ignore', invalid='ignore'):
        sigma_sqrt_T = sigma * np.sqrt(T)
        d1 = (np.log(S / K) + (r + 0.5 * sigma * sigma) * T) / sigma_sqrt_T
        gamma = np.exp(-q * T) * np.exp(-0.5 * d1 * d1) / np.sqrt(2.0 * np.pi) / (S * sigma_sqrt_T)
    valid = (T > 0) & (sigma_sqrt_T > 0) & (S > 0) & (K > 0)
    return np.where(valid, gamma, 0.0)

def compute_forward_price(S0: float, r: float, q: float, T: float) -> float:
    return S0 * math.exp((r - q) * T)

def find_nearest_expiry(td, ticker: str, target_years: float) -> Tuple[str, float]:
    """Thin wrapper -- lookup now lives in expiry_selector.py, shared with
    variance_swap_live.py and variance_swap_screener.py."""
    return expiry_selector.nearest_expiry(td, ticker, target_years)

# ---------- Sign convention ----------
def _dealer_sign(right: str) -> float:
    """Standard GEX-style dealer positioning convention (as popularized by
    SqueezeMetrics/SpotGamma-style public gamma-exposure trackers): call open
    interest is assumed to contribute POSITIVELY to dealer gamma exposure,
    put open interest NEGATIVELY. Net gamma = sum(call_gamma*call_OI) -
    sum(put_gamma*put_OI).

    This is a MODELING ASSUMPTION, not a measured fact -- open interest alone
    cannot tell you which side of a given contract the dealer community is
    actually on. It's the standard convention used across most public GEX
    tools, and it's what makes "positive net gamma -> dealers dampen moves"
    and "negative net gamma -> dealers amplify moves" (see print_report
    below) a coherent story: positive net gamma corresponds to a
    call-dominated book, consistent with dealers behaving long-gamma-like in
    aggregate.

    Previously this code summed raw (always-positive) Black-Scholes gamma
    across calls AND puts with NO sign distinction at all -- the `right`
    field was captured on every GammaRecord but never actually used in any
    aggregation. That made every downstream quantity (net gamma,
    gamma-by-strike, the gamma-flip level, the hedging-heatmap surface)
    degenerately one-signed: net gamma was always the negative of the sum of
    positive numbers, the per-strike bar chart could never show the
    alternating support/resistance pattern real GEX charts show, and the
    gamma-flip zero-crossing search was looking for a sign change in a
    cumulative sum that could mathematically never have one -- which is why
    it silently fell back to the forward price on effectively every run.
    """
    return 1.0 if right == 'C' else -1.0


VALID_SIGN_MODELS = ('oi_heuristic', 'replication', 'vol_surface_replication')

_LEGACY_BACKTEST_CALLERS = {
    "backtest_stage3.py", "backtesting_tool.py", "broker_book.py",
    "run_dual_pipeline_gate.py", "run_dual_pipeline_gate_v2.py",
    "run_dual_pipeline_gate_v5.py", "run_compare_live_vs_new.py",
    "run_live_vs_expiry_book_common_input.py",
}


def _assert_legacy_backtest_access() -> None:
    """Prevent the retired live model from being called by production code."""
    frames = inspect.stack()[1:]
    callers = {os.path.basename(frame.filename).lower() for frame in frames}
    if not callers.intersection(_LEGACY_BACKTEST_CALLERS) and not any(
        "tests" in frame.filename.lower() for frame in frames
    ):
        raise RuntimeError("dealer_positioning legacy live model is locked; use expiry_book_production")

_SIGN_MODEL_LABELS = {
    'oi_heuristic': "OI Heuristic (v1)",
    'replication': "Replication (v2)",
    'vol_surface_replication': "Vol-Surface + Replication (v2.1)",
}


def sign_model_render_label(result: DealerPositioningResult) -> str:
    """The ONE place a chart/report/scanner asks "which model actually
    produced this result" -- sourced from the DealerPositioningResult
    object itself (result.sign_model / result.accumulate), never a
    hardcoded string or docstring. This is the render-time model-identity
    assertion from the CARL audit: two sign models were once rendered
    side-by-side for the same ticker with opposite conclusions, and neither
    was traceable back to the code that actually ran. Every place that
    displays a sign-model label (plot_heatmap, CLI prints, JSON summaries)
    should call this instead of re-deriving its own string.
    """
    base = _SIGN_MODEL_LABELS.get(result.sign_model, result.sign_model)
    if result.accumulate:
        return f"{base} + 150d accumulation"
    return base


def _resolve_sign(right: str, strike: float, sign_model: str,
                   otm_strikes: Optional[set] = None,
                   vol_surface_ref=None) -> float:
    """Sign/gate actually applied to a record's greek*OI contribution.

    'oi_heuristic' (v1, default): flat _dealer_sign(right) -- every strike
    counts, call=+1/put=-1 regardless of moneyness.

    'replication' (Layer 1b, DEALER_POSITIONING_V2_DESIGN.md §3/§5): the
    dealer is assumed short whatever the Demeterfi replication strip is
    long, and the replication strip only assigns a position to OTM legs
    (calls above spot, puts below it) -- ITM legs get 0, not a lesser
    weight, because the recursion never includes them at all. `otm_strikes`
    is the set of (strike, right) pairs replication_reference.py's
    _otm_leg_weights() classified as part of that expiry's OTM replicating
    set; this deliberately reuses just the SIGN/GATE from that
    classification, not the weight magnitude itself, so gamma/delta/vanna/
    charm stay in the same units as the v1 chart (a fair like-for-like
    comparison per §8's validation plan) rather than being rescaled by the
    tiny natural units the recursion's own weights carry.

    'vol_surface_replication' (Layer 1a+1b, resolved 2026-07-22): fixes
    'replication' alone's structural blind spot -- its flat -1 can NEVER
    produce a positive (dealer-long) gamma bar anywhere, on any ticker,
    because raw gamma has no call/put sign asymmetry and the recursion's
    own weights are non-negative by construction (confirmed live on both
    SPY and QQQ: 100% negative gamma panels). Real OTM open interest isn't
    all "customer bought convexity" flow, though -- some of it is covered-
    call/cash-secured-put OVERWRITING flow (customer sells premium TO the
    dealer, dealer ends up LONG that specific strike). Layer 1a
    (vol_surface_reference.py) reads which kind of flow a given strike
    plausibly saw by checking whether its IV trades rich (net buying,
    matches Layer 1b's short default) or cheap (net selling, flips to
    long) versus a smooth near-ATM reference curve. Still gated by Layer
    1b's OTM classification (`otm_strikes`) -- only WHICH sign applies to
    an included leg changes, not whether it's included at all. Falls back
    to plain 'replication' behavior (flat -1) for any strike where
    `vol_surface_ref` has no usable deviation (e.g. missing/zero IV).
    """
    if sign_model == 'oi_heuristic':
        return _dealer_sign(right)
    if sign_model == 'replication':
        if otm_strikes is None or (strike, right) not in otm_strikes:
            return 0.0
        return -1.0
    if sign_model == 'vol_surface_replication':
        if otm_strikes is None or (strike, right) not in otm_strikes:
            return 0.0
        if vol_surface_ref is not None:
            vs_sign = vol_surface_reference.resolve_vol_surface_sign(vol_surface_ref, strike, right)
            if vs_sign != 0.0:
                return vs_sign
        return -1.0  # fallback: no usable deviation for this leg, use Layer 1b's default
    raise ValueError(f"sign_model must be one of {VALID_SIGN_MODELS}, got {sign_model!r}")

# ---------- Main computation ----------
def compute_accumulated_position(ticker: str, expiry: str,
                                 lookback_days: int = 150,
                                 seed_mode: str = 'replication',
                                 hist_rows: Optional[Tuple[List[dict], List[dict], List[dict]]] = None,
                                 ) -> Optional[Dict[Tuple[float, str], float]]:
    """THE single entry point for the live dealer-accumulation model.

    dealer_positioning.py is the one live model; this is the ONLY place the
    model layer calls into replication_reference for the accumulated dealer
    book. Both the live render (compute_dealer_positioning) and the backtest's
    v2_live consume this, so there is no second, separate accumulation path.

    Returns the signed accumulated position (dict keyed by (strike, right)), or
    None if it cannot be built (thin chain / insufficient history / proxy
    hiccup). Callers decide the failure policy -- the live render falls back to
    a same-day snapshot; the backtest's v2_live fails loudly.
    """
    try:
        acc = replication_reference.compute_accumulated_position_for_expiry(
            ticker, expiry, lookback_days=lookback_days, seed_mode=seed_mode,
            _hist_rows=hist_rows)
        return dict(acc.position_by_strike) if acc.position_by_strike else None
    except Exception as exc:
        print(f"  [dealer_positioning] accumulation failed for {ticker} {expiry}: "
              f"{type(exc).__name__}: {exc}", flush=True)
        return None


def compute_dealer_positioning(ticker: str, target_years: float = 0.25,
                                max_days: Optional[int] = None,
                                expiration: Optional[str] = None,
                                sign_model: str = 'oi_heuristic',
                                accumulate: bool = False,
                                accumulation_lookback_days: int = 150,
                                accumulation_seed_mode: str = 'replication',
                                _accumulation_hist_rows: Optional[
                                    Tuple[List[dict], List[dict], List[dict]]] = None,
                                ) -> DealerPositioningResult:
    """max_days: if given, restricts the expiry window used for the greek
    exposure comparison (Gamma/Delta/Vanna/Charm-by-strike) to expiries within
    this many calendar days, overriding the default 2-year max_tte cutoff
    used for the existing hedging-heatmap surface. Pass e.g. 150 to match a
    '150 days' comparison chart.

    `expiration`: if given (a "YYYYMMDD" string), used only to anchor the
    forward-price T (`actual_T` below) to the same date volatility_suite.py
    resolved for the rest of the suite. The Gamma/Delta/Vanna/Charm surface
    itself still aggregates across the active_expiries window (that's the
    whole point of a dealer-positioning surface, not a single-expiry view) --
    only the reported forward/T pins to the shared expiry.

    `sign_model`: 'oi_heuristic' (default, v1 -- flat call=+1/put=-1 on
    every strike, see _dealer_sign) or 'replication' (Layer 1b -- dealer
    assumed short whatever that expiry's Demeterfi replication strip is
    long, restricted to the OTM legs the recursion actually covers; ITM
    strikes contribute 0 rather than a flat sign). See _resolve_sign for
    the exact convention and DEALER_POSITIONING_V2_DESIGN.md §3/§5 for the
    reasoning. Adding this is what lets the SAME chart type actually render
    v1 vs. v2's sign convention side by side, rather than the two only ever
    being compared as separate numbers.

    `accumulate`: opt-in (also forceable via the DEALER_ACCUMULATION=1 env
    var, mirroring the DEALER_VANNA_FLOW pattern) real multi-day seed-plus-
    accumulate positioning (replication_reference.compute_accumulated_position_for_expiry)
    for the ANCHOR expiry only (the one `expiration`/`target_years` resolves
    to) -- NOT every active expiry, to avoid multiplying the expensive
    per-contract historical-greeks fetch by the expiry count. When True, the
    anchor expiry's per-strike aggregation uses the accumulated SIGNED
    position in place of `_resolve_sign(...) * today's OI`; the accumulated
    position already carries its own sign, so `applied_sign` is recorded as
    1.0 (pass-through) for those records, not a `sign_model`-derived value.
    Every other active expiry is untouched -- still same-day snapshot via
    `_resolve_sign`, per `sign_model`. See DealerPositioningResult.accumulate
    and the render label in sign_model_render_label()."""
    _assert_legacy_backtest_access()
    effective_accumulate = accumulate or os.environ.get("DEALER_ACCUMULATION", "0") == "1"

    td = ThetaDataController()

    spot = td.fetch_spot_price(ticker)
    if spot <= 0:
        td.close()
        raise ValueError(f"Could not fetch spot for {ticker}")

    dividend_yield = td.fetch_dividend_yield(ticker)

    anchor_expiry, actual_T = expiry_selector.resolve_expiration(td, ticker, expiration, target_years)
    anchor_expiry = anchor_expiry.strip() if anchor_expiry else anchor_expiry
    # Live risk-free rate from the yield curve (RISK_FREE_RATE=0.05 kept only as a
    # fallback). Gamma is nearly r-insensitive, but the forward and the whole
    # suite now share one live rate rather than a stale 0.05 constant.
    _r_live = td.fetch_risk_free_rate(actual_T)
    r_use = _r_live if _r_live is not None else RISK_FREE_RATE
    forward = compute_forward_price(spot, r_use, dividend_yield, actual_T)

    all_expiries = td.list_expirations(ticker)
    if not all_expiries:
        td.close()
        raise ValueError(f"No expiries found for {ticker}")

    today = datetime.now(timezone.utc).date()
    max_tte = (max_days / DEFAULT_A) if max_days else 2.0
    active_expiries = []
    for exp_str in all_expiries:
        try:
            exp_date = datetime.strptime(exp_str.strip(), "%Y%m%d").date()
        except ValueError:
            continue
        tte = (exp_date - today).days / DEFAULT_A
        if 0 < tte <= max_tte:
            active_expiries.append((exp_str.strip(), tte))

    print(f"  Processing {len(active_expiries)} expiries for {ticker}...")

    gamma_records: List[GammaRecord] = []
    # Signed (call=+, put=-) per-strike dealer gamma exposure -- see _dealer_sign.
    gamma_by_strike: Dict[float, float] = defaultdict(float)
    dollar_gamma_by_strike: Dict[float, float] = defaultdict(float)
    oi_by_strike: Dict[float, int] = defaultdict(int)
    # Raw sign*greek*OI sums (unscaled by CONTRACT_MULTIPLIER/pp/day factors --
    # that scaling is applied once, in one place, after the loop). NaN greek
    # values are skipped per-record so one missing field doesn't zero out a
    # strike that had other valid records.
    delta_by_strike: Dict[float, float] = defaultdict(float)
    vanna_by_strike: Dict[float, float] = defaultdict(float)
    charm_by_strike: Dict[float, float] = defaultdict(float)
    delta_seen = vanna_seen = charm_seen = 0
    vanna_call_total = 0.0
    vanna_put_total = 0.0

    if sign_model not in VALID_SIGN_MODELS:
        td.close()
        raise ValueError(f"sign_model must be one of {VALID_SIGN_MODELS}, got {sign_model!r}")

    # Real multi-day accumulation for the ANCHOR expiry only (see docstring
    # above) -- computed once, before the per-expiry loop, not per-iteration.
    # Falls back to ordinary same-day snapshot behavior (accumulated_position
    # stays None) rather than aborting the whole render if the historical
    # fetch fails (thin chain, insufficient history, proxy hiccup, etc.).
    accumulated_position: Optional[Dict[Tuple[float, str], float]] = None
    if effective_accumulate and anchor_expiry:
        accumulated_position = compute_accumulated_position(
            ticker, anchor_expiry, lookback_days=accumulation_lookback_days,
            seed_mode=accumulation_seed_mode, hist_rows=_accumulation_hist_rows)
        if accumulated_position is None:
            print(f"  [accumulate] falling back to same-day snapshot for {ticker} "
                  f"{anchor_expiry}")

    for exp_str, tte in active_expiries:
        try:
            greeks = td.option_bulk_greeks(ticker, exp_str)
            oi_data = td.option_bulk_oi(ticker, exp_str)
        except Exception:
            continue

        # For sign_model in ('replication', 'vol_surface_replication'):
        # classify this expiry's chain into its OTM replicating set ONCE
        # per expiry (not per row) using that expiry's own IVs and today's
        # spot, exactly the same _otm_leg_weights() classification
        # replication_reference.py's single-day snapshot uses. Only the
        # (strike, right) KEYS are used below (via _resolve_sign) -- see
        # that function's docstring for why the weight magnitudes
        # themselves aren't reused here.
        #
        # For sign_model='vol_surface_replication' specifically, ALSO fit
        # Layer 1a's reference curve on this same expiry's chain_iv, so
        # _resolve_sign can flip sign per-strike where a strike is trading
        # cheap (net selling / overwriting flow) instead of applying
        # Layer 1b's flat -1 everywhere.
        otm_strikes = None
        vol_surface_ref = None
        if sign_model in ('replication', 'vol_surface_replication'):
            chain_iv: Dict[Tuple[float, str], float] = {}
            for row in greeks:
                try:
                    k_iv = strike_from_theta(int(row['strike']))
                    right_iv = row['right']
                    iv_val = _to_float(row['implied_vol'])
                except (KeyError, ValueError):
                    continue
                if not math.isnan(iv_val) and iv_val > 0:
                    chain_iv[(k_iv, right_iv)] = iv_val
            weights_this_expiry = replication_reference._otm_leg_weights(chain_iv, spot, tte)
            otm_strikes = set(weights_this_expiry.keys())
            if sign_model == 'vol_surface_replication':
                # Pass this expiry's own forward/T so Layer 1a fits SABR
                # (well-behaved across the whole OTM strip) instead of
                # falling back to the near-ATM quadratic, which was caught
                # via live SPY data producing a smooth, non-economic
                # deviation artifact when extrapolated to far strikes --
                # see vol_surface_reference.py's module docstring
                # ("UPDATE 2026-07-22") for the full diagnosis.
                expiry_forward = compute_forward_price(spot, r_use, dividend_yield, tte)
                vol_surface_ref = vol_surface_reference.compute_vol_surface_reference(
                    ticker, chain_iv, spot, forward=expiry_forward, T=tte)
                # compute_vol_surface_reference returns None if this expiry
                # doesn't have enough OTM/near-ATM points to trust either
                # fitter -- _resolve_sign already falls back to Layer 1b's
                # flat -1 when vol_surface_ref is None, so no special-casing
                # needed here beyond just passing it through.

        oi_lookup = {}
        for row in oi_data:
            try:
                k_theta = int(row['strike'])
                right = row['right']
                oi = int(row['open_interest'])
                if oi > 0:
                    oi_lookup[(k_theta, right)] = oi
            except (ValueError, KeyError):
                continue

        # v1 scope: accumulation only ever applies to the resolved ANCHOR
        # expiry, never to every active expiry (see the accumulate docstring
        # above -- avoids multiplying the expensive historical-greeks fetch
        # by the expiry count). Every other expiry keeps the ordinary
        # same-day _resolve_sign path below unchanged.
        use_accumulation_here = (
            effective_accumulate and accumulated_position is not None and exp_str == anchor_expiry
        )

        for row in greeks:
            try:
                k_theta = int(row['strike'])
                right = row['right']
                gamma_val = _to_float(row['gamma'])
                iv = _to_float(row['implied_vol'])
                bid = _to_float(row['bid'])
                ask = _to_float(row['ask'])
            except (KeyError, ValueError):
                continue

            if math.isnan(gamma_val) or gamma_val <= 0:
                continue
            if math.isnan(iv) or iv <= 0:
                continue

            k = strike_from_theta(k_theta)

            if use_accumulation_here:
                # Sign is already baked into the accumulated position (it's
                # a signed dealer position, not raw OI) -- applied_sign is
                # recorded as 1.0 (pass-through), NOT a _resolve_sign value,
                # per the accumulate docstring's documented behavioral fork.
                oi = accumulated_position.get((k, right), 0.0)
                if oi == 0.0:
                    continue
                sign = 1.0
            else:
                oi = oi_lookup.get((k_theta, right), 0)
                if oi <= 0:
                    continue
                sign = _resolve_sign(right, k, sign_model, otm_strikes, vol_surface_ref)

            dollar_gamma = gamma_val * spot * CONTRACT_MULTIPLIER * oi

            delta_val = _extract_greek_field(row, 'delta')
            vanna_val = _extract_greek_field(row, 'vanna')
            charm_val = _extract_greek_field(row, 'charm')

            gamma_records.append(GammaRecord(
                strike=k, expiry=exp_str, right=right,
                gamma=gamma_val, dollar_gamma=dollar_gamma,
                iv=iv, oi=oi, bid=bid, ask=ask, tte=tte,
                delta=delta_val, vanna=vanna_val, charm=charm_val,
                applied_sign=sign,
            ))

            gamma_by_strike[k] += sign * gamma_val * oi
            dollar_gamma_by_strike[k] += sign * dollar_gamma
            oi_by_strike[k] += oi

            if not math.isnan(delta_val):
                delta_by_strike[k] += sign * delta_val * oi
                delta_seen += 1
            if not math.isnan(vanna_val):
                vanna_by_strike[k] += sign * vanna_val * oi
                vanna_seen += 1
                if right == 'C':
                    vanna_call_total += sign * vanna_val * oi
                else:
                    vanna_put_total += sign * vanna_val * oi
            if not math.isnan(charm_val):
                charm_by_strike[k] += sign * charm_val * oi
                charm_seen += 1

    td.close()

    if not gamma_records:
        raise ValueError(f"No valid gamma records found for {ticker}")

    strikes_sorted = sorted(gamma_by_strike.keys())
    K_grid = np.array(strikes_sorted, dtype=float)
    gamma_arr = np.array([gamma_by_strike[k] for k in strikes_sorted], dtype=float)
    dollar_gamma_arr = np.array([dollar_gamma_by_strike[k] for k in strikes_sorted], dtype=float)
    oi_arr = np.array([oi_by_strike[k] for k in strikes_sorted], dtype=float)

    # ---- Greek exposure comparison arrays (Gamma/Delta/Vanna/Charm, shares units) ----
    # Gamma "shares/$1" exposure = sign*gamma*OI*CONTRACT_MULTIPLIER, which is
    # exactly dollar_gamma_arr / spot (dollar_gamma already carries the
    # CONTRACT_MULTIPLIER and a spot factor -- dividing it back out avoids
    # summing the same thing twice under a different name).
    gamma_shares_arr = dollar_gamma_arr / spot if spot > 0 else np.zeros_like(dollar_gamma_arr)
    delta_shares_arr = np.array(
        [delta_by_strike.get(k, 0.0) for k in strikes_sorted], dtype=float
    ) * CONTRACT_MULTIPLIER
    vanna_shares_arr = np.array(
        [vanna_by_strike.get(k, 0.0) for k in strikes_sorted], dtype=float
    ) * CONTRACT_MULTIPLIER * VANNA_PP_SCALE
    charm_shares_arr = np.array(
        [charm_by_strike.get(k, 0.0) for k in strikes_sorted], dtype=float
    ) * CONTRACT_MULTIPLIER * (1.0 / DEFAULT_A if CHARM_ANNUALIZED else 1.0)

    # Net gamma is now genuinely signed via the call/put split above -- no
    # separate blanket negation needed (that was the old code's substitute
    # for a real sign convention, and would now double-flip the result).
    net_gamma = float(np.sum(gamma_arr))
    net_dollar_gamma = float(np.sum(dollar_gamma_arr))

    hedge_requirement = abs(net_dollar_gamma * 0.01)

    # Option-contract equivalent of the same hedge_requirement, using the
    # highest-|delta|, real-OI strike actually in the chain -- NOT a claim
    # that this replaces hedge_requirement, but a genuinely different
    # question worth answering side by side (see Jason's question: the
    # share number above is the standard GEX-style "if hedged continuously
    # via the underlying" convention, real and still valid on its own
    # terms, since continuous minute-by-minute delta-rehedging is
    # standardly done via stock/futures for liquidity reasons even in
    # desks that otherwise carry big structural option positions -- but it
    # was never reconciled with replication_reference.py's option-native
    # delta-hedge concept, which lives in a completely separate function
    # and was never wired into this chart at all. This gives an honest
    # side-by-side instead of silently picking one story.
    hedge_candidates = [r for r in gamma_records if r.oi >= replication_reference.MIN_HEDGE_OI
                        and not math.isnan(r.delta) and abs(r.delta) > 1e-6]
    if hedge_candidates:
        hedge_leg = max(hedge_candidates, key=lambda r: abs(r.delta))
        hedge_equiv_option_strike = hedge_leg.strike
        hedge_equiv_option_right = hedge_leg.right
        hedge_equiv_option_contracts = hedge_requirement / (abs(hedge_leg.delta) * CONTRACT_MULTIPLIER)
    else:
        hedge_equiv_option_strike = None
        hedge_equiv_option_right = None
        hedge_equiv_option_contracts = float('nan')

    max_idx = np.argmax(np.abs(dollar_gamma_arr))
    highest_gamma_strike = float(K_grid[max_idx])
    total_gex = float(np.sum(np.abs(dollar_gamma_arr)))

    # Surface: dealer gamma as a function of hypothetical (spot, iv), built
    # from the same signed convention. Vectorized over records per grid point
    # instead of a full triple-nested Python loop.
    spot_range = np.linspace(spot * 0.7, spot * 1.3, 50)
    all_ivs = np.array([r.iv for r in gamma_records if r.iv > 0])
    if len(all_ivs) > 0:
        avg_iv = np.mean(all_ivs)
        iv_range = np.linspace(max(0.05, avg_iv * 0.5), min(2.0, avg_iv * 1.5), 50)
    else:
        iv_range = np.linspace(0.1, 1.0, 50)

    K_arr = np.array([r.strike for r in gamma_records])
    T_arr = np.array([r.tte for r in gamma_records])
    # Reuses each record's OWN applied_sign (set above per sign_model),
    # rather than re-deriving a flat _dealer_sign(right) here -- so the
    # gamma-flip level and hedging surface agree with the by-strike bars
    # above them under sign_model='replication' too, instead of silently
    # staying on the v1 convention for this one derived quantity.
    weight_arr = np.array([r.applied_sign * r.oi for r in gamma_records])

    surface_gamma = np.zeros((len(spot_range), len(iv_range)))
    for i, s in enumerate(spot_range):
        for j, iv in enumerate(iv_range):
            g = bs_gamma_vec(s, K_arr, T_arr, r_use, dividend_yield, iv)
            surface_gamma[i, j] = float(np.sum(g * weight_arr))

    # Gamma-flip level: the SPOT PRICE at which total dealer gamma changes
    # sign -- derived from the spot-indexed surface (averaged across the IV
    # axis), not from a cumulative sum over strikes at the current spot. The
    # old code's `cumsum over strikes, find sign change` approach was looking
    # for a flip in strike-space, which isn't the same concept as "the spot
    # level where dealer gamma flips" even independent of the sign bug that
    # made it never fire at all; this also matches what Plot 4 (gamma profile
    # vs spot) already visualizes, so the reported level and the chart now
    # agree by construction.
    gamma_by_spot = np.sum(surface_gamma, axis=1) / len(iv_range)
    sign_changes = np.where(np.diff(np.sign(gamma_by_spot)))[0]
    if len(sign_changes) > 0:
        # pick the crossing nearest current spot (index closest to spot_range == spot)
        spot_idx = int(np.argmin(np.abs(spot_range - spot)))
        nearest = sign_changes[np.argmin(np.abs(sign_changes - spot_idx))]
        # linear interpolation between the two straddling grid points
        x0, x1 = spot_range[nearest], spot_range[nearest + 1]
        y0, y1 = gamma_by_spot[nearest], gamma_by_spot[nearest + 1]
        gamma_flip = float(x0 - y0 * (x1 - x0) / (y1 - y0)) if (y1 - y0) != 0 else float(x0)
    else:
        gamma_flip = forward

    print(f"  Records: {len(gamma_records)}, Strikes: {len(K_grid)}")
    print(f"  Net dealer gamma: {net_gamma:.2e}, Hedge req: {hedge_requirement:,.0f} shares")
    print(f"  Delta field seen: {delta_seen}/{len(gamma_records)}  "
          f"Vanna field seen: {vanna_seen}/{len(gamma_records)}  "
          f"Charm field seen: {charm_seen}/{len(gamma_records)}")
    if delta_seen == 0 or vanna_seen == 0 or charm_seen == 0:
        print("  NOTE: one or more of delta/vanna/charm wasn't found in the ThetaData "
              "all_greeks response under any of the known field-name candidates -- run "
              "debug_print_greek_fields(ticker) to see the raw field names and extend "
              "_GREEK_FIELD_CANDIDATES if needed. The corresponding panel(s) in "
              "plot_greek_exposure_comparison will be flagged as unavailable rather than "
              "silently showing zeros.")

    return DealerPositioningResult(
        ticker=ticker, spot=spot, forward=forward, dividend_yield=dividend_yield,
        total_net_gamma=net_gamma,
        total_net_dollar_gamma=net_dollar_gamma,
        hedge_requirement=hedge_requirement,
        gamma_flip_level=gamma_flip,
        highest_gamma_strike=highest_gamma_strike,
        total_gamma_exposure=total_gex,
        gamma_records=gamma_records,
        strike_grid=K_grid,
        gamma_by_strike=gamma_arr,
        dollar_gamma_by_strike=dollar_gamma_arr,
        oi_by_strike=oi_arr,
        num_expiries=len(active_expiries),
        num_records=len(gamma_records),
        surface_spot=spot_range,
        surface_iv=iv_range,
        surface_gamma=surface_gamma,
        gamma_by_spot=gamma_by_spot,
        gamma_shares_by_strike=gamma_shares_arr,
        delta_shares_by_strike=delta_shares_arr,
        vanna_shares_by_strike=vanna_shares_arr,
        charm_shares_by_strike=charm_shares_arr,
        greek_days_window=max_days,
        has_delta_data=delta_seen > 0,
        has_vanna_data=vanna_seen > 0,
        has_charm_data=charm_seen > 0,
        sign_model=sign_model,
        hedge_equiv_option_strike=hedge_equiv_option_strike,
        hedge_equiv_option_right=hedge_equiv_option_right,
        hedge_equiv_option_contracts=hedge_equiv_option_contracts,
        accumulate=accumulated_position is not None,
        vanna_call_shares=vanna_call_total * CONTRACT_MULTIPLIER * VANNA_PP_SCALE,
        vanna_put_shares=vanna_put_total * CONTRACT_MULTIPLIER * VANNA_PP_SCALE,
    )

# ---------- Plotting — Professional Edition ----------

# Color palette -- matches shared/candlestick_chart.py's navy-to-purple dark
# theme (the renderer behind trading_journal's SPY/NKE candlestick charts) so
# every Vol_Suite chart reads as one consistent visual system instead of two
# unrelated dark themes living side by side.
DARK_BG = '#081326'
PANEL_BG = '#101d34'
GRID_COLOR = '#64748B'
TEXT_COLOR = '#F4F7FF'
ACCENT_BLUE = '#38BDF8'    # == candlestick_chart._UP (bullish/positive)
ACCENT_GREEN = '#34D399'
ACCENT_RED = '#C084FC'     # == candlestick_chart._DOWN (bearish/negative -- violet, not red, by design)
ACCENT_GOLD = '#FBBF24'
ACCENT_PURPLE = '#A78BFA'
ACCENT_CYAN = '#22D3EE'
ACCENT_ORANGE = '#FB923C'

# Custom colormaps
# Diverging colormaps -- violet (negative/short) through the navy background
# to cyan (positive/long), matching candlestick_chart.py's _DOWN/_UP pair
# instead of the old purple->orange->green "heatmap_pro" gradient.
HEATMAP_CMAP = LinearSegmentedColormap.from_list('heatmap_pro',
    ['#2e1065', ACCENT_RED, '#4c2f73', DARK_BG, '#134a63', ACCENT_BLUE, '#a5e6fb'], N=256)

GAMMA_BAR_CMAP = LinearSegmentedColormap.from_list('gamma_bar',
    [ACCENT_RED, '#4c2f73', PANEL_BG, '#134a63', ACCENT_BLUE], N=256)


def _style_axis(ax, title='', xlabel='', ylabel=''):
    """Apply consistent professional styling."""
    ax.set_facecolor(PANEL_BG)
    ax.set_title(title, color=TEXT_COLOR, fontsize=13, fontweight='bold', pad=12)
    ax.set_xlabel(xlabel, color=TEXT_COLOR, fontsize=10)
    ax.set_ylabel(ylabel, color=TEXT_COLOR, fontsize=10)
    ax.tick_params(colors=TEXT_COLOR, labelsize=8)
    ax.grid(True, color=GRID_COLOR, alpha=0.5, linewidth=0.5)
    ax.spines['bottom'].set_color(GRID_COLOR)
    ax.spines['top'].set_color(GRID_COLOR)
    ax.spines['left'].set_color(GRID_COLOR)
    ax.spines['right'].set_color(GRID_COLOR)


def _add_annotation_box(ax, x, y, text, color, fontsize=9, ha='left', va='bottom'):
    """Add a styled annotation box."""
    ax.annotate(
        text, xy=(x, y), xytext=(x, y),
        fontsize=fontsize, color=color, fontweight='bold',
        ha=ha, va=va,
        path_effects=[pe.withStroke(linewidth=3, foreground=PANEL_BG)],
        bbox=dict(boxstyle='round,pad=0.3', facecolor=PANEL_BG,
                  edgecolor=color, alpha=0.9)
    )


def plot_heatmap(result: DealerPositioningResult, interpretation: str = None) -> str:
    """Generate the professional-grade hedging heatmap."""
    fig = plt.figure(figsize=(20, 14), facecolor=DARK_BG)
    # top=0.83 (was 0.88): opens up real clearance between the header text
    # and the top row of subplots. Second attempt at this header layout --
    # the first tried a slightly taller header_ax with the sign-model tag on
    # its own line *inside* that axes, but its bottom edge sat only 0.005
    # above the gridspec top, which wasn't enough once matplotlib's own
    # subplot-title padding stacked on top -- the tag visibly bled into
    # "DEALER GAMMA BY STRIKE" underneath it (confirmed live). Plain
    # fig.text at well-separated figure-fraction y-values, plus a real
    # gridspec gap, fixes both the original collision AND that follow-up one.
    gs = fig.add_gridspec(2, 2, hspace=0.35, wspace=0.30,
                          left=0.08, right=0.92, top=0.83, bottom=0.08)

    gamma_sign = "NEGATIVE" if result.total_net_gamma < 0 else "POSITIVE"
    gamma_tag = "AMPLIFYING" if result.total_net_gamma < 0 else "DAMPENING"
    gamma_color = ACCENT_RED if result.total_net_gamma < 0 else ACCENT_GREEN

    # Row 1 (y=0.965): main title -- unchanged length, same as every prior run.
    fig.text(0.08, 0.965, f"{result.ticker}  DEALER POSITIONING",
             fontsize=22, fontweight='bold', color=TEXT_COLOR, va='center')
    # Row 2 (y=0.935): sign-model tag, its own row -- clear of the title
    # above, the stats row below, AND (thanks to gridspec top=0.83) the
    # subplot titles beneath.
    sign_model_tag = sign_model_render_label(result)
    sign_model_color = ACCENT_ORANGE if result.sign_model == 'replication' else '#8b949e'
    fig.text(0.08, 0.935, f"sign model: {sign_model_tag}",
             fontsize=12, fontweight='bold', color=sign_model_color, va='center')
    # Row 3 (y=0.90): key metrics, spread across the width.
    fig.text(0.36, 0.90, f"Spot: ${result.spot:.2f}",
             fontsize=14, color=ACCENT_BLUE, va='center',
             path_effects=[pe.withStroke(linewidth=1, foreground=DARK_BG)])
    fig.text(0.48, 0.90, f"Flip: ${result.gamma_flip_level:.2f}",
             fontsize=14, color=ACCENT_PURPLE, va='center',
             path_effects=[pe.withStroke(linewidth=1, foreground=DARK_BG)])
    fig.text(0.60, 0.90, f"Hedge: {result.hedge_requirement:,.0f} sh/1%",
             fontsize=14, color=ACCENT_GOLD, va='center',
             path_effects=[pe.withStroke(linewidth=1, foreground=DARK_BG)])
    fig.text(0.76, 0.90, f"Gamma: {gamma_tag}",
             fontsize=14, color=gamma_color, va='center', fontweight='bold',
             path_effects=[pe.withStroke(linewidth=1, foreground=DARK_BG)])
    fig.text(0.90, 0.90, f"{result.num_expiries} exp·{result.num_records} rec",
             fontsize=10, color='#8b949e', va='center')
    # Row 4 (y=0.865): option-contract equivalent of the same hedge
    # requirement -- see the note in compute_dealer_positioning for why
    # this sits ALONGSIDE the share-based number rather than replacing it.
    if result.hedge_equiv_option_strike is not None:
        fig.text(0.36, 0.865,
                 f"Option-equiv hedge: {result.hedge_equiv_option_contracts:,.1f} contracts of "
                 f"{result.hedge_equiv_option_right} {result.hedge_equiv_option_strike:.1f} "
                 f"(vs. {result.hedge_requirement:,.0f} sh/1% above)",
                 fontsize=10, color='#8b949e', va='center', style='italic')

    # ====== PLOT 1: Gamma by Strike (top-left) ======
    ax1 = fig.add_subplot(gs[0, 0])
    # Subtitle is sign_model-aware: "(calls +, puts -)" is v1's convention
    # specifically and is actively wrong under sign_model='replication',
    # where gamma is uniformly negative everywhere there's OTM OI (both
    # calls and puts assumed short) -- see DEALER_POSITIONING_V2_DESIGN.md
    # §5 and _resolve_sign's docstring for why that's a deliberate
    # consequence of the replication model, not a bug.
    _gamma_subtitles = {
        'oi_heuristic': 'DEALER GAMMA BY STRIKE (calls +, puts -)',
        'replication': 'DEALER GAMMA BY STRIKE (replication: OTM legs short, both sides)',
        'vol_surface_replication': 'DEALER GAMMA BY STRIKE (vol-surface sign: rich=short, cheap=long)',
    }
    gamma_subtitle = _gamma_subtitles.get(result.sign_model, 'DEALER GAMMA BY STRIKE')
    _style_axis(ax1, gamma_subtitle, 'Strike', 'Dollar Gamma ($M)')

    K = result.strike_grid
    gamma = result.dollar_gamma_by_strike / 1e6  # $M, signed (call=+, put=-)
    bar_width = np.diff(K, append=K[-1] + (K[-1] - K[-2])*0.5) * 0.7

    # Color gradient by magnitude
    norm_bar = plt.Normalize(vmin=-max(abs(gamma)), vmax=max(abs(gamma)))
    bar_colors = [GAMMA_BAR_CMAP(norm_bar(g)) for g in gamma]

    bars = ax1.bar(K, gamma, width=bar_width, color=bar_colors, alpha=0.85, edgecolor='none')

    # Key level lines with glow
    ax1.axvline(x=result.spot, color=ACCENT_BLUE, linestyle='--', linewidth=2.5,
                alpha=0.9, zorder=5)
    ax1.axvline(x=result.spot, color=ACCENT_BLUE, linestyle='--', linewidth=5,
                alpha=0.2, zorder=4)
    ax1.axvline(x=result.gamma_flip_level, color=ACCENT_GOLD, linestyle=':', linewidth=2.5,
                alpha=0.9, zorder=5)
    ax1.axvline(x=result.highest_gamma_strike, color=ACCENT_PURPLE, linestyle='-.', linewidth=2,
                alpha=0.7, zorder=5)
    ax1.axhline(y=0, color='#8b949e', linewidth=0.8, alpha=0.5)

    # Annotations
    _add_annotation_box(ax1, result.spot, ax1.get_ylim()[1]*0.95,
                        f'Spot ${result.spot:.2f}', ACCENT_BLUE, ha='center')
    _add_annotation_box(ax1, result.gamma_flip_level, ax1.get_ylim()[1]*0.85,
                        f'Γ-Flip ${result.gamma_flip_level:.2f}', ACCENT_GOLD, ha='center')
    _add_annotation_box(ax1, result.highest_gamma_strike, ax1.get_ylim()[1]*0.75,
                        f'Max Γ ${result.highest_gamma_strike:.2f}', ACCENT_PURPLE, ha='center')

    # ====== PLOT 2: OI by Strike (top-right) ======
    ax2 = fig.add_subplot(gs[0, 1])
    _style_axis(ax2, 'OPEN INTEREST BY STRIKE', 'Strike', 'Open Interest')

    oi_norm = result.oi_by_strike / max(result.oi_by_strike)
    oi_colors = plt.cm.Blues(oi_norm * 0.6 + 0.4)
    ax2.bar(K, result.oi_by_strike, width=bar_width, color=oi_colors, alpha=0.85, edgecolor='none')

    ax2.axvline(x=result.spot, color=ACCENT_BLUE, linestyle='--', linewidth=2.5, alpha=0.9, zorder=5)
    ax2.axvline(x=result.spot, color=ACCENT_BLUE, linestyle='--', linewidth=5, alpha=0.2, zorder=4)
    ax2.axvline(x=result.highest_gamma_strike, color=ACCENT_PURPLE, linestyle='-.', linewidth=2, alpha=0.7, zorder=5)

    _add_annotation_box(ax2, result.spot, max(result.oi_by_strike)*0.95,
                        f'Spot ${result.spot:.2f}', ACCENT_BLUE, ha='center')
    _add_annotation_box(ax2, result.highest_gamma_strike, max(result.oi_by_strike)*0.85,
                        f'Max Γ ${result.highest_gamma_strike:.2f}', ACCENT_PURPLE, ha='center')

    # ====== PLOT 3: Hedging Heatmap (bottom-left) ======
    ax3 = fig.add_subplot(gs[1, 0])
    _style_axis(ax3, 'HEDGING HEATMAP — DEALER GAMMA SURFACE',
                'Spot Price (% of Current)', 'Implied Volatility (%)')

    spot_norm = result.surface_spot / result.spot
    iv_pct = result.surface_iv * 100

    max_abs = np.max(np.abs(result.surface_gamma))
    if max_abs > 0:
        norm = mcolors.TwoSlopeNorm(vmin=-max_abs, vcenter=0, vmax=max_abs)
        im = ax3.pcolormesh(spot_norm, iv_pct, result.surface_gamma.T,
                            cmap=HEATMAP_CMAP, norm=norm, shading='auto', rasterized=True)
    else:
        im = ax3.pcolormesh(spot_norm, iv_pct, result.surface_gamma.T,
                            cmap=HEATMAP_CMAP, shading='auto', rasterized=True)

    # Colorbar
    cbar = plt.colorbar(im, ax=ax3, shrink=0.8, pad=0.02)
    cbar.set_label('Dealer Gamma', color=TEXT_COLOR, fontsize=10, fontweight='bold')
    cbar.ax.yaxis.set_tick_params(color=TEXT_COLOR)
    plt.setp(plt.getp(cbar.ax.axes, 'yticklabels'), color=TEXT_COLOR)

    # Overlay lines with glow
    ax3.axvline(x=1.0, color=ACCENT_BLUE, linestyle='--', linewidth=3, alpha=0.9, zorder=5)
    ax3.axvline(x=1.0, color=ACCENT_BLUE, linestyle='--', linewidth=7, alpha=0.15, zorder=4)
    flip_pct = result.gamma_flip_level / result.spot
    ax3.axvline(x=flip_pct, color=ACCENT_GOLD, linestyle=':', linewidth=3, alpha=0.9, zorder=5)
    ax3.axvline(x=flip_pct, color=ACCENT_GOLD, linestyle=':', linewidth=7, alpha=0.15, zorder=4)

    # Annotations on heatmap
    _add_annotation_box(ax3, 1.0, iv_pct[-1]*0.95,
                        f'Current Spot ${result.spot:.2f}', ACCENT_BLUE, ha='center')
    _add_annotation_box(ax3, flip_pct, iv_pct[-1]*0.85,
                        f'Γ-Flip ${result.gamma_flip_level:.2f}', ACCENT_GOLD, ha='center')

    # Add gamma regime labels -- same conditional gating as PLOT 4 below:
    # only label a regime that's actually present in surface_gamma. See the
    # note at PLOT 4's regime labels for why this can't be unconditional
    # once sign_model='replication' is in play.
    surf_has_long = bool(np.any(result.surface_gamma > 0))
    surf_has_short = bool(np.any(result.surface_gamma < 0))
    if surf_has_long:
        ax3.text(0.75, 0.05, 'LONG Γ', fontsize=11, color=ACCENT_GREEN, fontweight='bold',
                 transform=ax3.transAxes, alpha=0.7,
                 path_effects=[pe.withStroke(linewidth=2, foreground=DARK_BG)])
    if surf_has_short:
        ax3.text(1.08, 0.05, 'SHORT Γ', fontsize=11, color=ACCENT_RED, fontweight='bold',
                 transform=ax3.transAxes, alpha=0.7,
                 path_effects=[pe.withStroke(linewidth=2, foreground=DARK_BG)])

    # ====== PLOT 4: Gamma Profile (bottom-right) ======
    ax4 = fig.add_subplot(gs[1, 1])
    _style_axis(ax4, 'GAMMA PROFILE vs SPOT', 'Spot Price (% of Current)', 'Avg Dealer Gamma ($M)')

    gamma_by_spot = result.gamma_by_spot / 1e6
    spot_pct = result.surface_spot / result.spot

    # Fill under curve
    ax4.fill_between(spot_pct, gamma_by_spot, 0,
                      where=(gamma_by_spot > 0), color=ACCENT_GREEN, alpha=0.25, interpolate=True)
    ax4.fill_between(spot_pct, gamma_by_spot, 0,
                      where=(gamma_by_spot < 0), color=ACCENT_RED, alpha=0.25, interpolate=True)

    # Main line
    ax4.plot(spot_pct, gamma_by_spot, color=TEXT_COLOR, linewidth=2.5, alpha=0.9, zorder=3)
    ax4.plot(spot_pct, gamma_by_spot, color=ACCENT_BLUE, linewidth=1.5, alpha=0.5, zorder=4)

    ax4.axhline(y=0, color='#8b949e', linewidth=0.8, alpha=0.5)
    ax4.axvline(x=1.0, color=ACCENT_BLUE, linestyle='--', linewidth=2.5, alpha=0.9, zorder=5)
    ax4.axvline(x=flip_pct, color=ACCENT_GOLD, linestyle=':', linewidth=2.5, alpha=0.9, zorder=5)

    # Zero crossings (this is now the same computation that produced
    # result.gamma_flip_level, so the marked crossing and the reported flip
    # level agree by construction)
    zero_mask = np.where(np.diff(np.sign(gamma_by_spot)))[0]
    for idx in zero_mask:
        cross_pct = spot_pct[idx]
        ax4.axvline(x=cross_pct, color=ACCENT_ORANGE, linestyle='--', linewidth=1.5, alpha=0.6, zorder=5)
        _add_annotation_box(ax4, cross_pct, 0, f'Γ=0', ACCENT_ORANGE, ha='center', fontsize=8)

    # Annotations
    _add_annotation_box(ax4, 1.0, max(gamma_by_spot)*0.65,
                        f'Spot ${result.spot:.2f}', ACCENT_BLUE, ha='center')
    _add_annotation_box(ax4, flip_pct, max(gamma_by_spot)*0.50,
                        f'Γ-Flip ${result.gamma_flip_level:.2f}', ACCENT_GOLD, ha='center')

    # Regime labels -- only drawn if that regime is actually PRESENT
    # anywhere in gamma_by_spot. Under sign_model='replication', gamma can
    # (and, in the first live SPY run, did) come out negative across the
    # ENTIRE spot range with no dampening region at all -- see _resolve_sign
    # and DEALER_POSITIONING_V2_DESIGN.md §5: the replication model assumes
    # dealers are short the whole OTM strip, both wings, so there's no
    # guarantee a "LONG Γ / DAMPEN" region exists the way there always is
    # under v1's mixed call/put convention. Stamping both labels
    # unconditionally (the old behavior) actively lied about the chart
    # whenever one regime was entirely absent.
    has_long_regime = bool(np.any(gamma_by_spot > 0))
    has_short_regime = bool(np.any(gamma_by_spot < 0))
    y_mid = max(abs(gamma_by_spot)) * 0.5
    if has_long_regime:
        ax4.text(0.72, y_mid, 'LONG Γ\nDAMPEN', fontsize=10, color=ACCENT_GREEN, fontweight='bold',
                 transform=ax4.transData, ha='center', alpha=0.8,
                 path_effects=[pe.withStroke(linewidth=2, foreground=DARK_BG)])
    if has_short_regime:
        ax4.text(1.12, -y_mid, 'SHORT Γ\nAMPLIFY', fontsize=10, color=ACCENT_RED, fontweight='bold',
                 transform=ax4.transData, ha='center', alpha=0.8,
                 path_effects=[pe.withStroke(linewidth=2, foreground=DARK_BG)])
    if not has_long_regime:
        ax4.text(0.5, 0.92, 'NO DAMPENING REGION IN THIS WINDOW', fontsize=9, color=ACCENT_GOLD,
                 fontweight='bold', transform=ax4.transAxes, ha='center', alpha=0.85,
                 path_effects=[pe.withStroke(linewidth=2, foreground=DARK_BG)])

    # ---- Footer ----
    footer_ax = fig.add_axes([0.08, 0.02, 0.84, 0.02], facecolor=DARK_BG)
    footer_ax.axis('off')
    footer_ax.text(0, 0.5, f'Generated: {datetime.now().strftime("%Y-%m-%d %H:%M UTC")}',
                   fontsize=8, color='#8b949e', va='center', transform=footer_ax.transAxes)
    footer_ax.text(1, 0.5, 'Data: ThetaData',
                   fontsize=8, color='#8b949e', va='center', ha='right',
                   transform=footer_ax.transAxes)

    # Save
    out_dir = os.getenv("VS_OUTPUT_DIR") or timestamped_output_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(out_dir, f"{result.ticker}_hedging_heatmap_{timestamp}.png")
    # Add interpretation text overlay if provided (multi-line with background)
    if interpretation:
        try:
            import textwrap
            wrapped = textwrap.fill(interpretation, width=100)
            bbox_props = dict(boxstyle="round,pad=0.5", facecolor="#0b1220", alpha=0.85, edgecolor="#274056")
            fig.text(0.5, 0.02, wrapped, ha='center', va='bottom', fontsize=10, color=TEXT_COLOR, bbox=bbox_props)
        except Exception:
            pass
    plt.savefig(filename, dpi=200, bbox_inches='tight', facecolor=DARK_BG, edgecolor='none')
    plt.close()
    return filename


def _greek_panel(ax, K, values, title, ylabel, spot, available, missing_note=None):
    """Draw one Gamma/Delta/Vanna/Charm-by-strike bar panel in the style of
    the reference comparison chart: dark bg, bars colored by the sign of the
    value at each strike (blue = positive/dampening-like, red =
    negative/amplifying-like -- chosen to stay consistent with this file's
    existing ACCENT_BLUE/ACCENT_RED usage elsewhere; flip PANEL_POS_COLOR/
    PANEL_NEG_COLOR below if a live render shows this doesn't match
    convention), gold dashed spot line, strike prices on the x-axis."""
    ax.set_facecolor(PANEL_BG)
    ax.set_title(title, color=TEXT_COLOR, fontsize=12, fontweight='bold', pad=18)
    ax.text(0.5, 1.02, f"${spot:.2f}", color=ACCENT_GOLD, fontsize=8,
            ha='center', va='bottom', transform=ax.transAxes)
    ax.set_xlabel('Strike Price', color=TEXT_COLOR, fontsize=10)
    ax.set_ylabel(ylabel, color=TEXT_COLOR, fontsize=10)
    ax.tick_params(colors=TEXT_COLOR, labelsize=8)
    ax.grid(True, color=GRID_COLOR, alpha=0.4, linewidth=0.5)
    for spine in ax.spines.values():
        spine.set_color(GRID_COLOR)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, pos: f"${x:.0f}"))

    if not available or len(K) == 0:
        ax.text(0.5, 0.5, missing_note or "No data available",
                color='#8b949e', fontsize=11, ha='center', va='center',
                transform=ax.transAxes)
        ax.axvline(x=spot, color=ACCENT_GOLD, linestyle=':', linewidth=2, alpha=0.9, zorder=5)
        return

    PANEL_POS_COLOR = ACCENT_BLUE
    PANEL_NEG_COLOR = ACCENT_RED
    bar_colors = [PANEL_POS_COLOR if v >= 0 else PANEL_NEG_COLOR for v in values]
    if len(K) > 1:
        diffs = np.diff(K)
        bar_width = np.append(diffs, diffs[-1]) * 0.7
    else:
        bar_width = np.array([1.0])

    ax.bar(K, values, width=bar_width, color=bar_colors, alpha=0.9, edgecolor='none')
    ax.axhline(y=0, color='#8b949e', linewidth=0.8, alpha=0.5)
    ax.axvline(x=spot, color=ACCENT_GOLD, linestyle=':', linewidth=2, alpha=0.9, zorder=5)

    from matplotlib.patches import Patch
    legend_handles = [
        Patch(facecolor=PANEL_POS_COLOR, label='Positive'),
        Patch(facecolor=PANEL_NEG_COLOR, label='Negative'),
    ]
    ax.legend(handles=legend_handles, loc='upper right', fontsize=8,
              facecolor=PANEL_BG, edgecolor=GRID_COLOR, labelcolor=TEXT_COLOR,
              framealpha=0.8)


def plot_greek_exposure_comparison(result: DealerPositioningResult,
                                    output_dir: Optional[str] = None) -> str:
    """Render the 2x2 Gamma/Delta/Vanna/Charm dealer exposure-by-strike
    comparison chart. Requires result to have been computed via
    compute_dealer_positioning (delta/vanna/charm are best-effort -- panels
    for any greek ThetaData didn't return are flagged rather than plotted as
    misleading zeros)."""
    days_label = f"{result.greek_days_window} days" if result.greek_days_window else "all expiries"
    fig = plt.figure(figsize=(16, 11), facecolor=DARK_BG)
    gs = fig.add_gridspec(2, 2, hspace=0.45, wspace=0.28,
                          left=0.07, right=0.96, top=0.90, bottom=0.07)

    _SIGN_MODEL_LABELS = {
        'oi_heuristic': "OI Heuristic (v1)",
        'replication': "Replication (v2)",
        'vol_surface_replication': "Vol-Surface + Replication (v2.1)",
    }
    sign_model_label = _SIGN_MODEL_LABELS.get(result.sign_model, result.sign_model)
    fig.text(0.5, 0.96,
              f"{result.ticker} Dealer Greek Exposure Comparison ({days_label}) — {sign_model_label}",
              color=TEXT_COLOR, fontsize=18, fontweight='bold', ha='center')

    K = result.strike_grid
    spot = result.spot

    ax1 = fig.add_subplot(gs[0, 0])
    _greek_panel(ax1, K, result.gamma_shares_by_strike, 'Gamma Exposure',
                 'Gamma (shares/$1)', spot, True)

    ax2 = fig.add_subplot(gs[0, 1])
    _greek_panel(ax2, K, result.delta_shares_by_strike, 'Delta Exposure',
                 'Delta (shares)', spot, result.has_delta_data,
                 missing_note="Delta not found in ThetaData response")

    ax3 = fig.add_subplot(gs[1, 0])
    _greek_panel(ax3, K, result.vanna_shares_by_strike, 'Vanna Exposure',
                 'Vanna (shares / 1pp IV)', spot, result.has_vanna_data,
                 missing_note="Vanna not found in ThetaData response")

    ax4 = fig.add_subplot(gs[1, 1])
    _greek_panel(ax4, K, result.charm_shares_by_strike, 'Charm Exposure',
                 'Charm (shares/day)', spot, result.has_charm_data,
                 missing_note="Charm not found in ThetaData response")

    out_dir = output_dir or os.getenv("VS_OUTPUT_DIR") or timestamped_output_dir()
    os.makedirs(out_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(out_dir, f"{result.ticker}_greek_exposure_comparison_{timestamp}.png")
    plt.savefig(filename, dpi=200, bbox_inches='tight', facecolor=DARK_BG, edgecolor='none')
    plt.close(fig)
    return filename


# ---------- Plotting -- Production expiry-book engine ----------
# The engine that replaced compute_dealer_positioning/compute_accumulated_position
# on the live path (2026-08-17 promotion; see expiry_book_production.py) never
# gained a plotting layer of its own, so a live run stopped producing the
# {ticker}_greek_exposure_comparison_*.png / {ticker}_hedging_heatmap_*.png
# pair above even though volatility_suite.py's dealer-positioning step still
# runs (and succeeds) every time. These functions restore that pair for
# ProductionDealerExposure, reusing this file's palette/_style_axis/_greek_panel
# so they render identically in style (and, for the heatmap, panel layout) to
# the legacy-engine versions above.


def plot_expiry_book_greek_exposure(result, output_dir: Optional[str] = None) -> str:
    """4-panel Gamma/Delta/Vanna/Charm dealer exposure-by-strike chart for a
    ProductionDealerExposure (expiry_book_production.fetch_production_result).
    Mirrors plot_greek_exposure_comparison's layout for the legacy engine,
    built from NetExposure.rows using VannaCharm GEX/VEX/CEX (not SVI)."""
    import expiry_book_exposure as ebe
    rows = result.snapshot.rows
    strikes = sorted({r.strike for r in rows})
    K = np.asarray(strikes, dtype=float)
    spot = result.spot
    base_spot = float(getattr(result, "prior_spot", None) or spot)
    d_attr = {"gamma": "d_gex", "vanna": "d_vex", "charm": "d_cex"}

    def _by_strike(greek: str) -> np.ndarray:
        """Current = prior-close stock + intraday dGEX/dVEX/dCEX."""
        agg: Dict[float, float] = defaultdict(float)
        for r in rows:
            if greek == "delta":
                val = r.exposure_of(greek)
            else:
                val = ebe.vannacharm_row(r, base_spot, greek)
                val = val + float(getattr(r, d_attr[greek], 0.0) or 0.0)
            agg[r.strike] += val
        return np.asarray([agg.get(k, 0.0) for k in strikes], dtype=float)

    fig = plt.figure(figsize=(16, 11), facecolor=DARK_BG)
    gs = fig.add_gridspec(2, 2, hspace=0.45, wspace=0.28,
                          left=0.07, right=0.96, top=0.90, bottom=0.07)
    fig.text(0.5, 0.96,
              f"{result.ticker} Dealer Greek Exposure Comparison (expiry {result.expiry}) "
              f"— prior close + intraday flow",
              color=TEXT_COLOR, fontsize=18, fontweight='bold', ha='center')

    have_data = len(K) > 0
    gamma_ylabel = 'GEX prior+dGEX ($ / 1%)'
    vanna_ylabel = 'VEX prior+dVEX'
    ax1 = fig.add_subplot(gs[0, 0])
    _greek_panel(ax1, K, _by_strike('gamma'), 'Gamma Exposure',
                 gamma_ylabel, spot, have_data)
    ax2 = fig.add_subplot(gs[0, 1])
    _greek_panel(ax2, K, _by_strike('delta'), 'Delta Exposure',
                 'Delta (shares)', spot, have_data)
    ax3 = fig.add_subplot(gs[1, 0])
    _greek_panel(ax3, K, _by_strike('vanna'), 'Vanna Exposure',
                 vanna_ylabel, spot, have_data)
    ax4 = fig.add_subplot(gs[1, 1])
    _greek_panel(ax4, K, _by_strike('charm'), 'Charm Exposure',
                 'CEX prior+dCEX / day', spot, have_data)

    out_dir = output_dir or os.getenv("VS_OUTPUT_DIR") or timestamped_output_dir()
    os.makedirs(out_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(out_dir, f"{result.ticker}_greek_exposure_comparison_{timestamp}.png")
    plt.savefig(filename, dpi=200, bbox_inches='tight', facecolor=DARK_BG, edgecolor='none')
    plt.close(fig)
    return filename


def _expiry_book_gamma_surface(result, spot_pct_range=(0.85, 1.15), n_spot=61,
                                iv_shift_range=(-0.15, 0.15), n_iv=31):
    """Re-price this snapshot's actual strikes/OI/IV across a (spot%, IV
    shift) grid via the same BS-gamma + dealer-frame sign convention
    (expiry_book_exposure.bs_gamma / _right_sign) the live snapshot itself
    uses -- a real repricing of this expiry's book, not a decorative fill.
    Returns (spot_pct_axis, iv_pct_axis, dollar_gamma_surface[$M])."""
    import expiry_book_exposure as ebe
    rows = result.snapshot.rows
    K = np.array([r.strike for r in rows])
    right_sign = np.array([1.0 if str(r.right).upper().startswith('C') else -1.0 for r in rows])
    oi = np.array([r.oi for r in rows])
    iv0 = np.array([r.iv for r in rows])
    T = rows[0].T
    spot0 = result.spot

    spot_pct = np.linspace(spot_pct_range[0], spot_pct_range[1], n_spot)
    iv_shift = np.linspace(iv_shift_range[0], iv_shift_range[1], n_iv)
    surface = np.zeros((n_spot, n_iv))
    sqrtT = math.sqrt(T)
    for i, pct in enumerate(spot_pct):
        s = spot0 * pct
        for j, dshift in enumerate(iv_shift):
            iv = np.clip(iv0 + dshift, 0.01, None)
            d1 = (np.log(s / K) + (ebe.RISK_FREE_RATE + 0.5 * iv ** 2) * T) / (iv * sqrtT)
            phi = np.exp(-0.5 * d1 ** 2) / math.sqrt(2.0 * math.pi)
            gamma = phi / (s * iv * sqrtT)
            signed_dollar_gamma = right_sign * gamma * oi * CONTRACT_MULTIPLIER * s ** 2 * 0.01
            surface[i, j] = float(np.sum(signed_dollar_gamma)) / 1e6  # $M
    iv_pct_axis = (iv0.mean() + iv_shift) * 100.0
    return spot_pct, iv_pct_axis, surface


def plot_expiry_book_heatmap(result, output_dir: Optional[str] = None) -> str:
    """4-panel hedging heatmap for a ProductionDealerExposure, matching
    plot_heatmap's legacy panel layout (gamma-by-strike, OI-by-strike, a
    real spot% x IV% dealer-gamma surface, gamma profile vs spot) so the
    production engine's chart reads the same as the legacy engine's --
    just this file's updated palette, not a different chart shape."""
    rows = result.snapshot.rows
    strikes = sorted({r.strike for r in rows})
    K = np.asarray(strikes, dtype=float)
    gamma_by_strike: Dict[float, float] = defaultdict(float)
    oi_by_strike: Dict[float, float] = defaultdict(float)
    for r in rows:
        gamma_by_strike[r.strike] += (
            float(r.greeks.get("gamma", 0.0)) * r.oi * CONTRACT_MULTIPLIER
            * result.spot ** 2 * 0.01
        )
        oi_by_strike[r.strike] += r.oi
    gamma_M = np.array([gamma_by_strike.get(k, 0.0) for k in strikes]) / 1e6
    oi_arr = np.array([oi_by_strike.get(k, 0.0) for k in strikes])
    spot = result.spot
    flip_level = result.execution_locus.local_gamma_boundary
    highest_gamma_strike = K[int(np.argmax(np.abs(gamma_M)))] if len(K) else spot

    fig = plt.figure(figsize=(20, 14), facecolor=DARK_BG)
    gs = fig.add_gridspec(2, 2, hspace=0.35, wspace=0.30,
                          left=0.08, right=0.92, top=0.83, bottom=0.08)

    total_net_dollar_gamma = result.snapshot.gex()
    gamma_tag = "AMPLIFYING" if total_net_dollar_gamma < 0 else "DAMPENING"
    gamma_color = ACCENT_RED if total_net_dollar_gamma < 0 else ACCENT_GREEN

    fig.text(0.08, 0.965, f"{result.ticker}  DEALER POSITIONING",
             fontsize=22, fontweight='bold', color=TEXT_COLOR, va='center')
    fig.text(0.08, 0.935, "sign model: expiry-book engine",
             fontsize=12, fontweight='bold', color='#8b949e', va='center')
    fig.text(0.36, 0.90, f"Spot: ${spot:.2f}",
             fontsize=14, color=ACCENT_BLUE, va='center',
             path_effects=[pe.withStroke(linewidth=1, foreground=DARK_BG)])
    fig.text(0.48, 0.90, f"Flip: ${flip_level:.2f}",
             fontsize=14, color=ACCENT_PURPLE, va='center',
             path_effects=[pe.withStroke(linewidth=1, foreground=DARK_BG)])
    fig.text(0.60, 0.90, f"Hedge: {abs(total_net_dollar_gamma * 0.01):,.0f} sh/1%",
             fontsize=14, color=ACCENT_GOLD, va='center',
             path_effects=[pe.withStroke(linewidth=1, foreground=DARK_BG)])
    fig.text(0.76, 0.90, f"Gamma: {gamma_tag}",
             fontsize=14, color=gamma_color, va='center', fontweight='bold',
             path_effects=[pe.withStroke(linewidth=1, foreground=DARK_BG)])
    fig.text(0.90, 0.90, f"expiry {result.expiry} · {len(rows)} rec",
             fontsize=10, color='#8b949e', va='center')

    # ====== PLOT 1: Gamma by Strike (top-left) ======
    ax1 = fig.add_subplot(gs[0, 0])
    _style_axis(ax1, 'DEALER GAMMA BY STRIKE (expiry-book engine)', 'Strike', 'Dollar Gamma ($M)')
    bar_width = (np.diff(K, append=K[-1] + (K[-1] - K[-2] if len(K) > 1 else 1.0) * 0.5)
                 * 0.7) if len(K) else np.array([])
    norm_bar = plt.Normalize(vmin=-max(abs(gamma_M).max(), 1e-9), vmax=max(abs(gamma_M).max(), 1e-9))
    bar_colors = [GAMMA_BAR_CMAP(norm_bar(g)) for g in gamma_M]
    ax1.bar(K, gamma_M, width=bar_width, color=bar_colors, alpha=0.85, edgecolor='none')
    ax1.axvline(x=spot, color=ACCENT_BLUE, linestyle='--', linewidth=2.5, alpha=0.9, zorder=5)
    ax1.axvline(x=flip_level, color=ACCENT_GOLD, linestyle=':', linewidth=2.5, alpha=0.9, zorder=5)
    ax1.axvline(x=highest_gamma_strike, color=ACCENT_PURPLE, linestyle='-.', linewidth=2, alpha=0.7, zorder=5)
    ax1.axhline(y=0, color='#8b949e', linewidth=0.8, alpha=0.5)
    _add_annotation_box(ax1, spot, ax1.get_ylim()[1] * 0.95, f'Spot ${spot:.2f}', ACCENT_BLUE, ha='center')
    _add_annotation_box(ax1, flip_level, ax1.get_ylim()[1] * 0.85, f'Γ-Flip ${flip_level:.2f}', ACCENT_GOLD, ha='center')
    _add_annotation_box(ax1, highest_gamma_strike, ax1.get_ylim()[1] * 0.75,
                        f'Max Γ ${highest_gamma_strike:.2f}', ACCENT_PURPLE, ha='center')

    # ====== PLOT 2: OI by Strike (top-right) ======
    ax2 = fig.add_subplot(gs[0, 1])
    _style_axis(ax2, 'OPEN INTEREST BY STRIKE', 'Strike', 'Open Interest')
    oi_max = oi_arr.max() if len(oi_arr) and oi_arr.max() > 0 else 1.0
    oi_colors = plt.cm.Blues(oi_arr / oi_max * 0.6 + 0.4)
    ax2.bar(K, oi_arr, width=bar_width, color=oi_colors, alpha=0.85, edgecolor='none')
    ax2.axvline(x=spot, color=ACCENT_BLUE, linestyle='--', linewidth=2.5, alpha=0.9, zorder=5)
    ax2.axvline(x=highest_gamma_strike, color=ACCENT_PURPLE, linestyle='-.', linewidth=2, alpha=0.7, zorder=5)
    _add_annotation_box(ax2, spot, oi_max * 0.95, f'Spot ${spot:.2f}', ACCENT_BLUE, ha='center')
    _add_annotation_box(ax2, highest_gamma_strike, oi_max * 0.85,
                        f'Max Γ ${highest_gamma_strike:.2f}', ACCENT_PURPLE, ha='center')

    # ====== PLOT 3: Hedging Heatmap -- real spot% x IV% gamma surface ======
    ax3 = fig.add_subplot(gs[1, 0])
    _style_axis(ax3, 'HEDGING HEATMAP — DEALER GAMMA SURFACE (expiry-book engine)',
                'Spot Price (% of Current)', 'Implied Volatility (%)')
    spot_pct, iv_pct, surface_gamma = _expiry_book_gamma_surface(result)
    max_abs = np.max(np.abs(surface_gamma))
    if max_abs > 0:
        norm = mcolors.TwoSlopeNorm(vmin=-max_abs, vcenter=0, vmax=max_abs)
        im = ax3.pcolormesh(spot_pct, iv_pct, surface_gamma.T,
                            cmap=HEATMAP_CMAP, norm=norm, shading='auto', rasterized=True)
    else:
        im = ax3.pcolormesh(spot_pct, iv_pct, surface_gamma.T,
                            cmap=HEATMAP_CMAP, shading='auto', rasterized=True)
    cbar = plt.colorbar(im, ax=ax3, shrink=0.8, pad=0.02)
    cbar.set_label('Dealer Gamma', color=TEXT_COLOR, fontsize=10, fontweight='bold')
    cbar.ax.yaxis.set_tick_params(color=TEXT_COLOR)
    plt.setp(plt.getp(cbar.ax.axes, 'yticklabels'), color=TEXT_COLOR)
    ax3.axvline(x=1.0, color=ACCENT_BLUE, linestyle='--', linewidth=3, alpha=0.9, zorder=5)
    flip_pct = flip_level / spot
    ax3.axvline(x=flip_pct, color=ACCENT_GOLD, linestyle=':', linewidth=3, alpha=0.9, zorder=5)
    _add_annotation_box(ax3, 1.0, iv_pct[-1] * 0.95, f'Current Spot ${spot:.2f}', ACCENT_BLUE, ha='center')
    _add_annotation_box(ax3, flip_pct, iv_pct[-1] * 0.85, f'Γ-Flip ${flip_level:.2f}', ACCENT_GOLD, ha='center')
    if np.any(surface_gamma > 0):
        ax3.text(0.75, 0.05, 'LONG Γ', fontsize=11, color=ACCENT_GREEN, fontweight='bold',
                 transform=ax3.transAxes, alpha=0.7,
                 path_effects=[pe.withStroke(linewidth=2, foreground=DARK_BG)])
    if np.any(surface_gamma < 0):
        ax3.text(1.08, 0.05, 'SHORT Γ', fontsize=11, color=ACCENT_RED, fontweight='bold',
                 transform=ax3.transAxes, alpha=0.7,
                 path_effects=[pe.withStroke(linewidth=2, foreground=DARK_BG)])

    # ====== PLOT 4: Gamma Profile vs Spot (bottom-right) ======
    # IV held at each row's own current level (iv_shift=0 slice of the same
    # surface) -- the standard "what does gamma do as only spot moves" read.
    ax4 = fig.add_subplot(gs[1, 1])
    _style_axis(ax4, 'GAMMA PROFILE vs SPOT (expiry-book engine)', 'Spot Price (% of Current)', 'Dealer Gamma ($M)')
    zero_shift_idx = int(np.argmin(np.abs(np.linspace(-0.15, 0.15, surface_gamma.shape[1]))))
    gamma_profile = surface_gamma[:, zero_shift_idx]
    ax4.fill_between(spot_pct, gamma_profile, 0, where=(gamma_profile > 0), color=ACCENT_GREEN, alpha=0.25, interpolate=True)
    ax4.fill_between(spot_pct, gamma_profile, 0, where=(gamma_profile < 0), color=ACCENT_RED, alpha=0.25, interpolate=True)
    ax4.plot(spot_pct, gamma_profile, color=TEXT_COLOR, linewidth=2.5, alpha=0.9, zorder=3)
    ax4.plot(spot_pct, gamma_profile, color=ACCENT_BLUE, linewidth=1.5, alpha=0.5, zorder=4)
    ax4.axhline(y=0, color='#8b949e', linewidth=0.8, alpha=0.5)
    ax4.axvline(x=1.0, color=ACCENT_BLUE, linestyle='--', linewidth=2.5, alpha=0.9, zorder=5)
    ax4.axvline(x=flip_pct, color=ACCENT_GOLD, linestyle=':', linewidth=2.5, alpha=0.9, zorder=5)
    zero_mask = np.where(np.diff(np.sign(gamma_profile)))[0]
    for idx in zero_mask:
        cross_pct = spot_pct[idx]
        ax4.axvline(x=cross_pct, color=ACCENT_ORANGE, linestyle='--', linewidth=1.5, alpha=0.6, zorder=5)
        _add_annotation_box(ax4, cross_pct, 0, 'Γ=0', ACCENT_ORANGE, ha='center', fontsize=8)
    has_long_regime = bool(np.any(gamma_profile > 0))
    has_short_regime = bool(np.any(gamma_profile < 0))
    y_mid = max(abs(gamma_profile).max(), 1e-9) * 0.5
    if has_long_regime:
        ax4.text(0.72, y_mid, 'LONG Γ\nDAMPEN', fontsize=10, color=ACCENT_GREEN, fontweight='bold',
                 transform=ax4.transData, ha='center', alpha=0.8,
                 path_effects=[pe.withStroke(linewidth=2, foreground=DARK_BG)])
    if has_short_regime:
        ax4.text(1.12, -y_mid, 'SHORT Γ\nAMPLIFY', fontsize=10, color=ACCENT_RED, fontweight='bold',
                 transform=ax4.transData, ha='center', alpha=0.8,
                 path_effects=[pe.withStroke(linewidth=2, foreground=DARK_BG)])
    if not has_long_regime:
        ax4.text(0.5, 0.92, 'NO DAMPENING REGION IN THIS WINDOW', fontsize=9, color=ACCENT_GOLD,
                 fontweight='bold', transform=ax4.transAxes, ha='center', alpha=0.85,
                 path_effects=[pe.withStroke(linewidth=2, foreground=DARK_BG)])

    footer_ax = fig.add_axes([0.08, 0.02, 0.84, 0.02], facecolor=DARK_BG)
    footer_ax.axis('off')
    footer_ax.text(0, 0.5, f'Generated: {datetime.now().strftime("%Y-%m-%d %H:%M UTC")}',
                   fontsize=8, color='#8b949e', va='center', transform=footer_ax.transAxes)
    footer_ax.text(1, 0.5, 'Data: ThetaData', fontsize=8, color='#8b949e', va='center', ha='right',
                   transform=footer_ax.transAxes)

    out_dir = output_dir or os.getenv("VS_OUTPUT_DIR") or timestamped_output_dir()
    os.makedirs(out_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(out_dir, f"{result.ticker}_hedging_heatmap_{timestamp}.png")
    plt.savefig(filename, dpi=200, bbox_inches='tight', facecolor=DARK_BG, edgecolor='none')
    plt.close(fig)
    return filename


# ---------- Report ----------
def print_report(result: DealerPositioningResult):
    gamma_sign = "NEGATIVE" if result.total_net_gamma < 0 else "POSITIVE"
    gamma_desc = "AMPLIFYING" if result.total_net_gamma < 0 else "DAMPENING"

    print("\n" + "=" * 90)
    print(f"DEALER POSITIONING REPORT — {result.ticker}")
    print("=" * 90)
    print(f"\nSpot:               ${result.spot:.2f}")
    print(f"Forward:            ${result.forward:.2f}  (q={result.dividend_yield:.4f})")
    print(f"Expiries Processed: {result.num_expiries}")
    print(f"Option Records:     {result.num_records:,}")
    print(f"Unique Strikes:     {len(result.strike_grid)}")

    print(f"\n--- Gamma Exposure (calls contribute +, puts contribute -; see _dealer_sign) ---")
    print(f"Net Dealer Gamma:    {result.total_net_gamma:.2e} ({gamma_sign}) -> {gamma_desc}")
    print(f"Net Dollar Gamma:    ${result.total_net_dollar_gamma:,.0f}")
    print(f"Total Gamma Exp.:    ${result.total_gamma_exposure:,.0f}")
    print(f"Hedge Requirement:   {result.hedge_requirement:,.0f} shares per 1% move")
    hedge_notional = result.hedge_requirement * result.spot
    print(f"  (approx ${hedge_notional:,.0f} notional)")

    print(f"\n--- Key Levels ---")
    print(f"Gamma Flip Level:    ${result.gamma_flip_level:.2f}  (spot level where dealer gamma changes sign)")
    print(f"Highest Gamma Strike: ${result.highest_gamma_strike:.2f}")

    print(f"\n--- Market Impact Assessment ---")
    print(f"  NOTE: this reflects a modeling assumption (calls=dealer-long-like,")
    print(f"  puts=dealer-short-like), not a measured fact about actual dealer books.")
    if result.total_net_gamma > 0:
        print(f"  POSITIVE GAMMA — Dealers net long gamma (assumed)")
        print(f"     -> Dealers buy dips, sell rips -> VOLATILITY DAMPENING")
    else:
        print(f"  NEGATIVE GAMMA — Dealers net short gamma (assumed)")
        print(f"     -> Dealers sell into weakness, buy into strength -> VOLATILITY AMPLIFYING")
        print(f"     -> Critical level at ${result.gamma_flip_level:.2f} (gamma flip)")
    print("=" * 90)


# ---------- Main ----------
def main():
    print("=" * 60)
    print("DEALER POSITIONING — Hedging Heatmap")
    print("=" * 60)

    ticker = input("Enter ticker: ").strip().upper() or "GME"
    td_for_expiry = ThetaDataController()
    expiration, target_years = expiry_selector.choose_expiry_interactive(
        td_for_expiry, ticker,
        prompt_prefix="Target time-to-expiry for forward (years, e.g. 0.25): ")
    td_for_expiry.close()

    sign_choice = input(
        "Sign model -- (1) OI heuristic [default], (2) Replication (Layer 1b), "
        "(3) Vol-Surface + Replication (Layer 1a+1b): "
    ).strip()
    sign_model = {'2': 'replication', '3': 'vol_surface_replication'}.get(sign_choice, 'oi_heuristic')

    print(f"\nComputing dealer positioning for {ticker} (sign_model={sign_model})...")

    try:
        result = compute_dealer_positioning(ticker, target_years, expiration=expiration, sign_model=sign_model)
        print_report(result)

        print("\n--- Generating plots...")
        try:
            filename = plot_heatmap(result)
            print(f"  Heatmap saved to: {filename}")
        except Exception as e:
            print(f"  Plot error: {e}")
            import traceback
            traceback.print_exc()

        try:
            days_input = input("Days window for Greek exposure comparison chart [150]: ").strip()
            greek_days = int(days_input) if days_input else 150
            greek_result = compute_dealer_positioning(ticker, target_years, max_days=greek_days,
                                                       sign_model=sign_model)
            greek_filename = plot_greek_exposure_comparison(greek_result)
            print(f"  Greek exposure comparison saved to: {greek_filename}")
        except Exception as e:
            print(f"  Greek exposure comparison plot error: {e}")
            import traceback
            traceback.print_exc()

        choice = input("\nExport gamma records to CSV? (y/n): ").strip().lower()
        if choice == 'y':
            import csv
            out_dir = os.getenv("VS_OUTPUT_DIR") or timestamped_output_dir()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = os.path.join(out_dir, f"{ticker}_gamma_records_{timestamp}.csv")
            with open(filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["Strike", "Expiry", "Right", "OI", "Gamma", "DollarGamma", "IV", "TTE"])
                for rec in result.gamma_records:
                    writer.writerow([rec.strike, rec.expiry, rec.right, rec.oi,
                                     f"{rec.gamma:.6e}", f"{rec.dollar_gamma:.2f}",
                                     f"{rec.iv:.4f}", f"{rec.tte:.4f}"])
            print(f"  Gamma records: {filename}")
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()

    print("\nDone.")


if __name__ == "__main__":
    main()


def run_dealer_positioning(ticker: str, target_years: float = 0.25,
                           output_dir: Optional[str] = None, save_csv: bool = True,
                           greek_days_window: Optional[int] = 150,
                           expiration: Optional[str] = None,
                           sign_model: str = 'oi_heuristic') -> List[str]:
    """Programmatic runner for dealer positioning. Returns list of saved files.

    greek_days_window: expiry window (calendar days) used for the new
    Gamma/Delta/Vanna/Charm exposure comparison chart -- pass None to reuse
    the same ~2-year window as the existing hedging heatmap (one ThetaData
    fetch instead of two) rather than a separate near-term-focused fetch.

    `expiration`: if given, pins the reported forward/T to this exact
    "YYYYMMDD" date (see compute_dealer_positioning docstring) so this leg
    agrees with the rest of a volatility_suite.py run on which expiry
    "target_years" meant.

    `sign_model`: 'oi_heuristic' (v1, default) or 'replication' (Layer 1b).
    Applied to BOTH the hedging heatmap and the Greek exposure comparison
    chart, so a full run gives an apples-to-apples v1-vs-v2 view rather
    than mixing conventions across the two charts. See
    compute_dealer_positioning's docstring / DEALER_POSITIONING_V2_DESIGN.md
    §3/§5 for what 'replication' actually changes.
    """
    out_dir = output_dir or os.getenv("VS_OUTPUT_DIR") or timestamped_output_dir()
    os.makedirs(out_dir, exist_ok=True)
    files: List[str] = []
    # Accumulation ON by default for the live runner: the accumulated seed-plus-
    # flow book (replication_reference) is the intended live model per Jason's
    # directive ("if accumulation isn't turned on turn it on"). DEALER_ACCUMULATION=0 disables.
    accumulate_live = os.environ.get("DEALER_ACCUMULATION", "1") == "1"
    result = compute_dealer_positioning(ticker, target_years, expiration=expiration,
                                        sign_model=sign_model, accumulate=accumulate_live)
    print_report(result)
    try:
        interp = None
        try:
            if result.total_net_gamma > 0:
                interp = f"Dealers net LONG gamma (assumed; dampening). Hedge requirement: {result.hedge_requirement:,.0f} sh/1%."
            else:
                interp = f"Dealers net SHORT gamma (assumed; amplifying). Gamma flip at ${result.gamma_flip_level:.2f}."
        except Exception:
            interp = None
        filename = plot_heatmap(result, interpretation=interp)
        files.append(filename)
    except Exception as e:
        print(f"  Plot error: {e}")

    try:
        # Separate fetch scoped to greek_days_window so the comparison chart's
        # title/window match what's actually plotted, without changing the
        # hedging heatmap's existing 2-year default above. If
        # greek_days_window is falsy, just reuse `result` (one fetch total).
        greek_result = (compute_dealer_positioning(ticker, target_years, max_days=greek_days_window,
                                                    sign_model=sign_model)
                         if greek_days_window else result)
        greek_filename = plot_greek_exposure_comparison(greek_result, output_dir=out_dir)
        files.append(greek_filename)
    except Exception as e:
        print(f"  Greek exposure comparison plot error: {e}")
    if save_csv:
        import csv as _csv
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = os.path.join(out_dir, f"{ticker}_gamma_records_{timestamp}.csv")
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = _csv.writer(f)
            writer.writerow(["Strike", "Expiry", "Right", "OI", "Gamma", "DollarGamma", "IV", "TTE"])
            for rec in result.gamma_records:
                writer.writerow([rec.strike, rec.expiry, rec.right, rec.oi,
                                 f"{rec.gamma:.6e}", f"{rec.dollar_gamma:.2f}",
                                 f"{rec.iv:.4f}", f"{rec.tte:.4f}"])
        files.append(filename)
    interp_lines = [
        f"Ticker: {ticker}",
        f"Spot: ${result.spot:.2f}",
        f"Forward: ${result.forward:.2f}",
        f"Net Dealer Gamma: {result.total_net_gamma:.2e}",
        f"Net Dollar Gamma: ${result.total_net_dollar_gamma:,.0f}",
        f"Hedge Req (sh/1%): {result.hedge_requirement:,.0f}",
        f"Gamma Flip Level: ${result.gamma_flip_level:.2f}",
    ]
    interp = "\n".join(interp_lines)
    return files, interp, result
