#!/usr/bin/env python3
"""replication_reference.py

Layer 1b of the dealer-positioning v2 design (see
DEALER_POSITIONING_V2_DESIGN.md, §3/§5/§6) -- the Demeterfi/DDKZ (1999)
variance-swap static-replication recursion run on a REAL, live strike ladder
for one ticker/expiry, instead of the evenly-spaced synthetic grid used to
validate the math itself in tests/test_variance_swap_replication.py.

This is the Stage 2 "real-chain sanity test" input from that design doc's
§8: does the now-validated recursion + diagnostics produce sensible,
correctly-ordered numbers on a real, unevenly-spaced chain (dense near the
money, sparse in the wings) -- not yet whether the sign/positioning
assumption itself is correct (that's Stage 3), just whether the machinery
degrades the way it should once real strikes replace a synthetic grid.

IMPORTANT -- this module cannot be exercised end-to-end from this sandbox:
there's no network egress to api.potatohedge.com here (same limitation
dealer_positioning.py's header comments already flag for the vanna/charm
unit assumptions). It's been syntax-checked and dry-run against a mocked
chain shaped like a real bulk_snapshot/option/all_greeks + bulk OI response
(see the __main__ block / the accompanying smoke test), but the actual
Stage 2 numbers on SPY vs. a sparse name need to be run from an environment
that can reach the API -- i.e., run this locally.

Two things this module deliberately reuses from the already-validated
synthetic test rather than re-deriving:
  - The Appendix A recursion itself (`_build_weights`/`_f_payoff`) is pure
    strike-and-forward geometry -- no volatility input at all. It's the same
    formula as tests/test_variance_swap_replication.py's `_build_weights`,
    kept as an independent copy (not imported) since this one runs on a
    real, ragged strike ladder pulled live rather than a synthetic
    np.arange grid, and duplicating ~15 lines is cheaper than coupling this
    production module to the test file's import path.
  - `spacing_corrugation_score` is stored as the RELATIVE ratio (local std
    of successive differences / local mean absolute level), not an
    absolute standard deviation -- confirmed in Stage 1 to be the only
    version that shows the paper's claimed "worse near expiry" direction.
"""

import math
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import expiry_selector
import numpy as np
import vol_surface_reference
from thetadata_client import ThetaDataController, strike_from_theta

CONTRACT_MULTIPLIER = 100

# Minimum resting OI for a strike to be eligible as the synthetic delta-hedge
# instrument (§5: "pick among the liquid deep-ITM strikes, not a zero-OI one
# nobody actually trades"). Deliberately conservative/arbitrary starting
# point -- tune against real chains once Stage 2 is actually run.
MIN_HEDGE_OI = 50


# ---------------------------------------------------------------------------
# Core replication math (Demeterfi, Derman, Kamal, Zou 1999, Appendix A) --
# pure strike/forward geometry, no vol input. Validated against the paper's
# own Figure 3 in tests/test_variance_swap_replication.py.
# ---------------------------------------------------------------------------


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _bs_vega(S: float, K: float, T: float, r: float, q: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0 or K <= 0 or S <= 0:
        return 0.0
    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    return S * math.exp(-q * T) * _norm_pdf(d1) * sqrtT


def _f_payoff(ST: float, Sstar: float, T: float) -> float:
    return (2.0 / T) * ((ST - Sstar) / Sstar - math.log(ST / Sstar))


def _build_weights(
    strikes: list[float], Sstar: float, T: float, side: str
) -> tuple[np.ndarray, np.ndarray]:
    """Appendix A discrete recursion for one side of the chain (calls above
    Sstar or puts below it). Every weight is guaranteed >= 0 -- the
    replicating strip is long-only by construction (convexity of the
    log-payoff, minimum exactly 0 at Sstar). See the module docstring for
    why this is a deliberate copy of the synthetic test's version, not an
    import of it.
    """
    nodes = [Sstar] + list(strikes)
    fvals = [_f_payoff(k, Sstar, T) for k in nodes]
    out_strikes, weights = [], []
    cum = 0.0
    for i in range(1, len(nodes)):
        Ki, Kim1 = nodes[i], nodes[i - 1]
        denom = (Ki - Kim1) if side == "call" else (Kim1 - Ki)
        slope = (fvals[i] - fvals[i - 1]) / denom
        w = slope - cum
        out_strikes.append(Ki)
        weights.append(w)
        cum += w
    return np.array(out_strikes), np.array(weights)


@dataclass
class ChainLeg:
    strike: float
    right: str
    weight: float  # from the recursion -- signed by construction (>= 0 here)
    iv: float
    oi: int
    delta: float  # native ThetaData delta for this strike/right


@dataclass
class ReplicationDiagnostics:
    ticker: str
    expiry: str
    T_years: float
    spot: float
    legs: list[ChainLeg] = field(default_factory=list)
    range_truncation_score: float = float("nan")
    spacing_corrugation_score: float = float("nan")  # RELATIVE ratio
    net_delta_before_hedge: float = float("nan")
    ideal_delta_target: float = float("nan")  # -(2/T)/S continuum ideal
    delta_hedge_drift: float = float("nan")  # net_delta - ideal target, per-share units
    delta_hedge_drift_normalized: float = float(
        "nan"
    )  # drift * spot -- see note in compute_replication_reference
    delta_hedge_strike: float | None = None
    delta_hedge_right: str | None = None
    delta_hedge_contracts: float = float("nan")
    n_calls: int = 0
    n_puts: int = 0


# ---------------------------------------------------------------------------
# Live data fetch (real bulk_snapshot/option/all_greeks + bulk OI, same
# field-name convention already confirmed live in dealer_positioning.py:
# lowercase 'strike'/'right'/'implied_vol'/'delta').
# ---------------------------------------------------------------------------


def _fetch_chain(
    td: ThetaDataController, ticker: str, expiry: str
) -> tuple[float, dict[tuple[float, str], dict]]:
    spot = td.fetch_spot_price(ticker)
    if not spot:
        raise ValueError(f"Could not fetch spot price for {ticker}")

    greek_rows = td.option_bulk_greeks(ticker, expiry)
    oi_rows = td.option_bulk_oi(ticker, expiry)

    oi_lookup: dict[tuple[int, str], int] = {}
    for row in oi_rows:
        try:
            k_theta = int(float(row["strike"]))
            right = row["right"]
            oi_lookup[(k_theta, right)] = int(float(row.get("open_interest", 0) or 0))
        except (KeyError, TypeError, ValueError):
            continue

    chain: dict[tuple[float, str], dict] = {}
    for row in greek_rows:
        try:
            k_theta = int(float(row["strike"]))
            right = row["right"]
            k = strike_from_theta(k_theta)
            iv = float(row.get("implied_vol", row.get("iv", 0)) or 0)
            delta = float(row.get("delta", 0) or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if iv <= 0:
            continue
        oi = oi_lookup.get((k_theta, right), 0)
        chain[(k, right)] = {"iv": iv, "delta": delta, "oi": oi}

    return spot, chain


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def compute_replication_reference(
    ticker: str,
    expiration: str | None = None,
    target_years: float = 0.25,
    r: float = 0.0,
    q: float = 0.0,
    min_hedge_oi: int = MIN_HEDGE_OI,
    _td: ThetaDataController | None = None,
    _spot: float | None = None,
    _chain: dict[tuple[float, str], dict] | None = None,
    _expiry: str | None = None,
    _T: float | None = None,
) -> ReplicationDiagnostics:
    """Build the Layer 1b replication diagnostics for one ticker/expiry.

    The `_td`/`_spot`/`_chain`/`_expiry`/`_T` parameters exist only so the
    smoke test at the bottom of this file (and any future pytest
    `integration`-marked test) can inject a mocked chain shaped like a real
    response, without needing live network access -- normal callers should
    never pass them.
    """
    owns_td = _td is None
    td = _td or ThetaDataController()
    try:
        if _expiry is not None and _T is not None:
            expiry, T = _expiry, _T
        else:
            expiry, T = expiry_selector.resolve_expiration(
                td, ticker, expiration, target_years
            )

        if _spot is not None and _chain is not None:
            spot, chain = _spot, _chain
        else:
            spot, chain = _fetch_chain(td, ticker, expiry)
    finally:
        if owns_td:
            td.close()

    call_strikes = sorted(k for (k, right) in chain if right == "C" and k > spot)
    put_strikes = sorted(
        (k for (k, right) in chain if right == "P" and k < spot), reverse=True
    )

    if len(call_strikes) < 2 or len(put_strikes) < 2:
        raise ValueError(
            f"Not enough OTM strikes on both sides for {ticker} {expiry} "
            f"(calls={len(call_strikes)}, puts={len(put_strikes)}) -- "
            f"can't run the replication recursion."
        )

    Kc, Wc = _build_weights(call_strikes, spot, T, "call")
    Kp, Wp = _build_weights(put_strikes, spot, T, "put")

    legs: list[ChainLeg] = []
    for k, w in zip(Kc, Wc):
        info = chain.get((k, "C"), {"iv": 0.0, "delta": 0.0, "oi": 0})
        legs.append(
            ChainLeg(
                strike=k,
                right="C",
                weight=float(w),
                iv=info["iv"],
                oi=info["oi"],
                delta=info["delta"],
            )
        )
    for k, w in zip(Kp, Wp):
        info = chain.get((k, "P"), {"iv": 0.0, "delta": 0.0, "oi": 0})
        legs.append(
            ChainLeg(
                strike=k,
                right="P",
                weight=float(w),
                iv=info["iv"],
                oi=info["oi"],
                delta=info["delta"],
            )
        )

    result = ReplicationDiagnostics(
        ticker=ticker,
        expiry=expiry,
        T_years=T,
        spot=spot,
        legs=legs,
        n_calls=len(Kc),
        n_puts=len(Kp),
    )

    # ---- variance-vega vs. spot, using each leg's OWN market IV (real
    # smile, not a flat sigma -- this is the actual Stage-2 upgrade over the
    # synthetic test) ----
    S_grid = np.linspace(spot * 0.7, spot * 1.3, 61)
    ivs_with_data = [leg.iv for leg in legs if leg.iv > 0]
    if not ivs_with_data:
        raise ValueError(f"No usable IV data in chain for {ticker} {expiry}")
    mean_iv = float(np.mean(ivs_with_data))

    vega_by_S = np.zeros_like(S_grid)
    for leg in legs:
        if leg.iv <= 0:
            continue
        for i, S in enumerate(S_grid):
            vega_by_S[i] += leg.weight * _bs_vega(S, leg.strike, T, r, q, leg.iv)
    # Chain-rule factor to convert summed BS vega into "variance vega"
    # d(Price)/d(sigma^2). There's no single flat sigma on a real smile, so
    # this uses the chain's own mean IV as the reference -- an
    # approximation, fine for a RELATIVE diagnostic (comparing shape across
    # spot / across tickers), not precise enough to use as a priced
    # variance-swap fair value.
    variance_vega_by_S = vega_by_S / (2.0 * mean_iv)

    # ---- range truncation, take 2 --------------------------------------
    # FIRST VERSION OF THIS DIAGNOSTIC WAS CONFOUNDED WITH VOL LEVEL, caught
    # by actually running it on live SPY vs. GME: it measured the absolute
    # vega gap between spot and the ±30%-of-spot window, which is really
    # just measuring "how fast does BS vega decay with moneyness" -- a
    # function of IV level and T, not of whether the chain has run out of
    # real strikes. High-IV names (GME) have a WIDE, slowly-decaying vega
    # bell curve in strike space, so they scored as "less truncated" than
    # low-IV SPY even though SPY's chain is objectively deeper (221 legs vs.
    # 34) and more liquid -- backwards from what the diagnostic is supposed
    # to catch.
    #
    # Fixed version: measure the ACTUAL listed strike range in
    # implied-vol-standardized (d1-style) units -- ln(K/S) / (mean_iv *
    # sqrt(T)) -- i.e. "how many implied-vol standard deviations out does
    # this ticker's real chain actually extend, on its own vol scale."
    # This is dimensionless and comparable across any two tickers regardless
    # of price level or vol regime, and it's a direct, literal measure of
    # truncation risk: if the chain only reaches ~1-2 sigma-equivalent
    # before running out of strikes, that's a real hedging gap; reaching
    # 5-6 sigma-equivalent means truncation basically doesn't matter for any
    # move short of a genuine tail event.
    #
    # NOTE the sign convention is the OPPOSITE of the old version:
    # LOWER range_truncation_score now means MORE truncated / riskier
    # (a narrower safety margin in vol-standardized terms), not higher.
    lowest_strike = min(put_strikes[-1], call_strikes[0])
    highest_strike = max(put_strikes[0], call_strikes[-1])
    sigma_scale = mean_iv * math.sqrt(T)
    z_lo = math.log(spot / lowest_strike) / sigma_scale
    z_hi = math.log(highest_strike / spot) / sigma_scale
    result.range_truncation_score = min(z_lo, z_hi)

    mid_mask = (S_grid > spot * 0.85) & (S_grid < spot * 1.15)
    mid_vals = variance_vega_by_S[mid_mask]
    if len(mid_vals) > 2:
        result.spacing_corrugation_score = float(
            np.std(np.diff(mid_vals)) / (np.mean(np.abs(mid_vals)) + 1e-9)
        )

    # ---- delta-hedge leg: neutralize the strip's net delta with a
    # synthetic high-delta option position at a real, liquid strike (§5) ----
    net_delta = float(sum(leg.weight * leg.delta for leg in legs))
    ideal_delta_target = (
        -(2.0 / T) / spot
    )  # continuum log-contract ideal, Demeterfi eq. 8-9 region
    result.net_delta_before_hedge = net_delta
    result.ideal_delta_target = ideal_delta_target
    result.delta_hedge_drift = net_delta - ideal_delta_target
    # ideal_delta_target * spot = -(2/T), a UNIVERSAL constant that depends
    # only on T, not on spot -- so raw delta_hedge_drift isn't directly
    # comparable across tickers at very different price levels (a $21 stock
    # and a $747 index both "should" have ideal_delta_target*spot = -2/T,
    # just at wildly different per-share deltas). Multiplying drift by spot
    # removes that 1/S scaling and puts every ticker's drift on the same
    # -(2/T)-scaled footing, which is what should actually be compared
    # ticker-to-ticker (confirmed against the live SPY/GME run: raw drift
    # looked ~36x worse for GME than SPY, purely from GME's much lower share
    # price; normalized, they came out within ~4% of each other).
    result.delta_hedge_drift_normalized = result.delta_hedge_drift * spot

    # Need a hedge delta of opposite sign to net_delta, sized to zero it.
    # Prefer whichever available strike has the largest |delta| among
    # strikes with real resting OI, on the correct side to reduce net_delta.
    candidates = [
        leg for leg in legs if leg.oi >= min_hedge_oi and abs(leg.delta) > 1e-6
    ]
    if net_delta > 0:
        # need negative delta to offset -> short call OR long put; either
        # works, so just take the largest |delta| among eligible legs whose
        # delta sign would reduce |net_delta| if added with a positive size.
        candidates = [leg for leg in candidates if leg.delta < 0] or [
            leg for leg in candidates if leg.right == "C"
        ]
    else:
        candidates = [leg for leg in candidates if leg.delta > 0] or [
            leg for leg in candidates if leg.right == "P"
        ]

    if candidates:
        hedge_leg = max(candidates, key=lambda leg: abs(leg.delta))
        result.delta_hedge_strike = hedge_leg.strike
        result.delta_hedge_right = hedge_leg.right
        result.delta_hedge_contracts = -net_delta / (
            hedge_leg.delta * CONTRACT_MULTIPLIER
        )
    # else: no liquid deep-ITM strike found -- leave hedge fields as None/NaN
    # and let the caller flag "no viable hedge instrument" rather than
    # silently picking an illiquid one.

    return result


def format_report(d: ReplicationDiagnostics) -> str:
    lines = [
        f"{d.ticker} {d.expiry} (T={d.T_years:.4f}yr, spot={d.spot:.2f})",
        f"  legs: {d.n_calls} calls, {d.n_puts} puts, {len(d.legs)} total",
        f"  range_truncation_score:      {d.range_truncation_score:.2f}  "
        f"(sigma-equivalent chain extent; LOWER = more truncated/riskier)",
        f"  spacing_corrugation_score:   {d.spacing_corrugation_score:.4f}  (relative ratio)",
        f"  net_delta_before_hedge:      {d.net_delta_before_hedge:.6f}",
        f"  ideal_delta_target:          {d.ideal_delta_target:.6f}",
        f"  delta_hedge_drift:           {d.delta_hedge_drift:.6f}  (per-share, NOT comparable across tickers)",
        f"  delta_hedge_drift_normalized:{d.delta_hedge_drift_normalized:8.4f}  (drift*spot -- compare this across tickers)",
    ]
    if d.delta_hedge_strike is not None:
        lines.append(
            f"  delta_hedge:                 {d.delta_hedge_contracts:.2f} contracts of "
            f"{d.delta_hedge_right} {d.delta_hedge_strike:.1f}"
        )
    else:
        lines.append(
            "  delta_hedge:                 NO LIQUID STRIKE FOUND (min_hedge_oi not met)"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Seed + accumulate: a real cumulative position, not a repeated snapshot
# ---------------------------------------------------------------------------
#
# Resolved 2026-07-22 from a back-and-forth with Jason about what a
# multi-day version of this should actually mean. The single-day
# compute_replication_reference() above implicitly assumes the ENTIRE
# assumed hedge position was built TODAY, anchored at today's spot as S* --
# but real dealer positions accumulate over many days at many different
# reference spots, from whenever each trade actually happened. Re-running
# the snapshot recursion on several different past days and summing the
# results would NOT fix that -- each day's recursion already represents a
# FULL variance-hedge notional on its own, so adding several together
# double/triple/N-counts the same thing. The only genuinely additive
# quantity across days is a real observable: day-over-day OPEN INTEREST
# CHANGE. So the construction here is seed-plus-accumulate, not
# repeat-plus-sum:
#   1. Seed an initial position at the start of the lookback window.
#   2. Walk forward one day at a time. Each day, pull that day's real OI
#      change per strike, and sign it using THAT DAY's own
#      replication-implied direction (that day's own spot as S*, that
#      day's own smile) -- not today's.
#   3. Add each day's signed OI change to a running total.
# What comes out the other end is an estimate of what's actually resting
# right now, built from real flow, not a hypothetical "if built today"
# re-derivation.


@dataclass
class AccumulatedPositionResult:
    ticker: str
    expiry: str
    seed_date: str
    end_date: str
    seed_mode: str
    lookback_days: int
    position_by_strike: dict[tuple[float, str], float] = field(default_factory=dict)
    daily_trace: list[dict] = field(default_factory=list)
    dates_used: list[str] = field(default_factory=list)
    weighting_convention: str = "raw_oi_seed+mean_abs_vanna_normalized_flow"


def _parse_hist_date(row: dict) -> str | None:
    """Same fallback-key pattern as correlation_engine._rows_to_close_series:
    confirmed live field for hist/stock/eod is 'created' (a full timestamp,
    e.g. "2026-07-01T17:15:06.172"), not 'date' -- bulk historical option
    endpoints are unconfirmed and may differ, so try 'date' first (the more
    likely shape for a per-row date on a bulk chain-history pull) and fall
    back to 'created'/'Date'/'datetime' truncated to YYYYMMDD.
    """
    for key in ("date", "Date", "created", "datetime"):
        if row.get(key):
            raw = str(row[key])
            digits = raw[:10].replace("-", "")
            if len(digits) == 8 and digits.isdigit():
                return digits
    return None


def _otm_leg_weights(
    chain_iv: dict[tuple[float, str], float], spot: float, T: float
) -> dict[tuple[float, str], float]:
    """One day's replication weights, keyed by (strike, right), restricted
    to that day's own OTM sides (calls above that day's spot, puts below).
    Returns {} if there aren't enough strikes on both sides that day --
    callers should skip that day's attribution rather than guess.
    """
    call_strikes = sorted(k for (k, right) in chain_iv if right == "C" and k > spot)
    put_strikes = sorted(
        (k for (k, right) in chain_iv if right == "P" and k < spot), reverse=True
    )
    if len(call_strikes) < 2 or len(put_strikes) < 2:
        return {}
    Kc, Wc = _build_weights(call_strikes, spot, T, "call")
    Kp, Wp = _build_weights(put_strikes, spot, T, "put")
    out: dict[tuple[float, str], float] = {}
    for k, w in zip(Kc, Wc):
        out[(k, "C")] = float(w)
    for k, w in zip(Kp, Wp):
        out[(k, "P")] = float(w)
    return out


def compute_accumulated_position(
    ticker: str,
    expiration: str | None = None,
    target_years: float = 0.25,
    lookback_days: int = 5,
    seed_mode: str = "replication",
) -> AccumulatedPositionResult:
    """Seed-plus-accumulate cumulative dealer position over `lookback_days`
    trading days. See the module-level note above this function for why
    this is NOT the same as re-running compute_replication_reference() on
    several past days and summing the results.

    seed_mode:
      'replication' -- seed the first day's position as -OI(K) for every
          strike classified as part of THAT day's own OTM replicating set,
          0 elsewhere (assumes the replication-style logic already applied
          on day 1).
      'oi_heuristic' -- seed using the existing v1 flat call=+/put=-
          convention across ALL strikes (dealer_positioning.py's
          _dealer_sign), for when you don't want to assume day 1 itself
          was a clean replication-built book.

    Sign convention: the recursion's own weights are long-only (the strip a
    hedger would hold). The assumed DEALER position is the other side of
    that trade -- a dealer who sold a customer this convexity is short what
    the replication is long -- so every attributed OI change gets a flat
    -1 multiplier on top of being restricted to that day's correctly-
    classified OTM side. This does NOT yet scale by each strike's relative
    weight within the strip (a central strike and a tail-edge strike are
    currently trusted equally once both are "in" the OTM set) -- a
    deliberate simplification to get a first version that's actually
    checkable, not a claim that weight-scaling wouldn't help.

    Needs `option_bulk_hist_oi` / `option_bulk_hist_greeks`
    (thetadata_client.py) -- both new, inferred-path, not yet live-smoke-
    tested. Run from an environment with real network access; this dev
    sandbox has none (see module docstring).
    """
    if seed_mode not in ("replication", "oi_heuristic", "vanna", "svi_rp"):
        raise ValueError(
            f"seed_mode must be 'replication', 'oi_heuristic', 'vanna', or 'svi_rp', got {seed_mode!r}"
        )

    td = ThetaDataController()
    try:
        expiry, _ = expiry_selector.resolve_expiration(
            td, ticker, expiration, target_years
        )
    finally:
        td.close()

    return compute_accumulated_position_for_expiry(
        ticker,
        expiry,
        lookback_days=lookback_days,
        seed_mode=seed_mode,
    )


def compute_accumulated_position_for_expiry(
    ticker: str,
    expiry: str,
    lookback_days: int = 150,
    seed_mode: str = "replication",
    _hist_rows: tuple[list[dict], list[dict], list[dict]] | None = None,
) -> AccumulatedPositionResult:
    """Same seed-plus-accumulate construction as compute_accumulated_position,
    but takes an ALREADY-RESOLVED `expiry` -- so a caller that resolved one
    via expiry_selector once (e.g. dealer_positioning.compute_dealer_positioning's
    anchor expiry) doesn't re-resolve/re-fetch a possibly-different one --
    and accepts pre-fetched history rows via `_hist_rows` so tests and
    offline `seed_data_*.json` consumers can bypass the network fetch
    entirely. Same injection pattern as compute_replication_reference's
    _td/_spot/_chain parameters.
    """
    if seed_mode not in ("replication", "oi_heuristic", "vanna", "svi_rp"):
        raise ValueError(
            f"seed_mode must be 'replication', 'oi_heuristic', 'vanna', or 'svi_rp', got {seed_mode!r}"
        )

    if _hist_rows is not None:
        hist_greek_rows, hist_oi_rows, hist_spot_rows = _hist_rows
    else:
        td = ThetaDataController()
        try:
            end_date = datetime.now()
            # generous calendar-day pad so `lookback_days` TRADING days survive
            # weekends/holidays after filtering to dates actually present in
            # both the OI and greeks history.
            start_date = end_date - timedelta(days=int(lookback_days * 2.2) + 5)
            start_str, end_str = (
                start_date.strftime("%Y%m%d"),
                end_date.strftime("%Y%m%d"),
            )

            # Direct historical bulk pulls. These went through a SQLite cache
            # until 2026-07-24; see backtest_stage3.run_backtest's docstring for
            # why it was removed (a cache that can't tell a failed fetch from an
            # empty one will happily serve a permanent hole, and this proxy
            # fails transiently often enough for that to be a when, not an if).
            # Dense routes so a far-dated expiry still gets a full overlapping
            # history to accumulate from (Jason 2026-08-16). The sparse
            # option_bulk_hist_greeks / option_bulk_hist_oi routes return only a
            # SINGLE date for far-dated expiries (measured SPY 20261120 -> 1
            # usable day), which made the accumulation raise "found 1 usable
            # trading day(s)" instead of computing. Use the dense whole-chain
            # routes -- option_bulk_hist_eod_greeks (one request per expiry,
            # OHLC + implied_vol + full greeks over the range, same convention
            # as option_bulk_hist_oi_by_day: string YYYYMMDD 'date', right
            # 'C'/'P', strike cents-int) -- so the seed + daily-flow
            # accumulation can actually run for any expiry.
            hist_greek_rows = td.option_bulk_hist_eod_greeks(
                ticker, expiry, start_str, end_str
            )
            hist_oi_rows = td.option_bulk_hist_oi_by_day(
                ticker, expiry, start_str, end_str
            )
            hist_spot_rows = td.hist_stock_eod(ticker, start_str, end_str)
        finally:
            td.close()

    result = _accumulate_from_history(
        ticker,
        expiry,
        lookback_days,
        seed_mode,
        hist_greek_rows,
        hist_oi_rows,
        hist_spot_rows,
    )
    # A far-dated expiry can leave the seed position empty: the oldest date's
    # OTM chain may yield no legs, so `_otm_leg_weights` returns nothing and
    # position_by_strike stays {}. Shorten the lookback to the nearest usable
    # date(s) -- `lookback_days` only trims the already-fetched trading_dates
    # (no re-fetch) -- and keep the first non-empty result. Falls through to
    # the original (possibly empty) result if none qualifies.
    if not result.position_by_strike:
        for _lb in (60, 30, 15, 10, 5):
            if _lb >= lookback_days:
                continue
            try:
                result = _accumulate_from_history(
                    ticker,
                    expiry,
                    _lb,
                    seed_mode,
                    hist_greek_rows,
                    hist_oi_rows,
                    hist_spot_rows,
                )
            except Exception:
                continue
            if result.position_by_strike:
                break
    return result


def _accumulate_from_history(
    ticker: str,
    expiry: str,
    lookback_days: int,
    seed_mode: str,
    hist_greek_rows: list[dict],
    hist_oi_rows: list[dict],
    hist_spot_rows: list[dict],
) -> AccumulatedPositionResult:
    """Pure function over already-fetched historical rows -- split out from
    compute_accumulated_position() so the aggregation logic itself (the part
    fully testable without live network access) can be exercised directly
    against mocked multi-day data. See the smoke test in
    tests/test_replication_reference_accumulation.py.
    """
    expiry_date = datetime.strptime(expiry, "%Y%m%d")

    iv_by_date: dict[str, dict[tuple[float, str], float]] = defaultdict(dict)
    for row in hist_greek_rows:
        d = _parse_hist_date(row)
        if not d:
            continue
        try:
            k = strike_from_theta(int(float(row["strike"])))
            right = row["right"]
            iv = float(row.get("implied_vol", row.get("iv", 0)) or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if iv > 0:
            iv_by_date[d][(k, right)] = iv

    vanna_by_date: dict[str, dict[tuple[float, str], float]] = defaultdict(dict)
    for row in hist_greek_rows:
        d = _parse_hist_date(row)
        if not d:
            continue
        try:
            k = strike_from_theta(int(float(row["strike"])))
            right = row["right"]
            vanna = float(row.get("vanna", row.get("Vanna", row.get("VANNA", 0))) or 0)
        except (KeyError, TypeError, ValueError):
            continue
        # Key decision is presence, not magnitude -- vanna can be legitimately
        # ~0 at the money, so store on successful parse (no >0 gate, no scale).
        vanna_by_date[d][(k, right)] = vanna

    oi_by_date: dict[str, dict[tuple[float, str], int]] = defaultdict(dict)
    for row in hist_oi_rows:
        d = _parse_hist_date(row)
        if not d:
            continue
        try:
            k = strike_from_theta(int(float(row["strike"])))
            right = row["right"]
            oi = int(float(row.get("open_interest", 0) or 0))
        except (KeyError, TypeError, ValueError):
            continue
        oi_by_date[d][(k, right)] = oi

    spot_by_date: dict[str, float] = {}
    for row in hist_spot_rows:
        d = _parse_hist_date(row)
        if not d:
            continue
        try:
            close = float(row.get("close", 0) or 0)
        except (TypeError, ValueError):
            continue
        if close > 0:
            spot_by_date[d] = close

    trading_dates = sorted(
        d for d in oi_by_date if d in iv_by_date and d in spot_by_date
    )
    if len(trading_dates) < 2:
        raise ValueError(
            f"Not enough overlapping OI/greeks/spot history for {ticker} {expiry} "
            f"to seed + accumulate (found {len(trading_dates)} usable trading day(s))."
        )
    trading_dates = (
        trading_dates[-(lookback_days + 1) :]
        if len(trading_dates) > lookback_days + 1
        else trading_dates
    )

    seed_sign = -1.0 if os.environ.get("DEALER_SEED_SIGN") != "1" else 1.0
    # Seed on the NEAREST trading date whose OTM chain yields a non-empty
    # position (Jason 2026-08-16): far-dated expiries can have a sparse/partial
    # OTM chain on the oldest overlapping date, which would leave the seed empty
    # and the accumulated book flat. Advance the seed date forward until it lands
    # on a usable chain instead of failing on the first date. This is the single
    # accumulation engine both dealer_positioning.py (live) and the backtest's
    # v2_live consume, so the fix makes the live model work for far-dated too.
    seed_idx = 0
    position: dict[tuple[float, str], float] = defaultdict(float)
    while seed_idx < len(trading_dates):
        seed_date = trading_dates[seed_idx]
        position = defaultdict(float)
        seed_spot = spot_by_date[seed_date]
        seed_T = (
            max((expiry_date - datetime.strptime(seed_date, "%Y%m%d")).days, 1) / 365.0
        )
        if seed_mode == "replication":
            seed_weights = _otm_leg_weights(iv_by_date[seed_date], seed_spot, seed_T)
            for k, right in seed_weights:
                position[(k, right)] += seed_sign * oi_by_date[seed_date].get(
                    (k, right), 0
                )
        elif seed_mode == "vanna":
            # Jason's brainstorming seed (LARP Round 1, 2026-08-11): mark rich
            # vanna OI as SHORT and cheap vanna OI as LONG. sign(vanna) at the seed
            # date picks the sign per strike. This is the "vanna-smile seed" arm.
            seed_vanna = vanna_by_date.get(seed_date, {})
            for (k, right), oi in oi_by_date[seed_date].items():
                v = seed_vanna.get((k, right), 0.0)
                if abs(v) < 1e-12:
                    continue
                sign = seed_sign * math.copysign(1.0, v)
                position[(k, right)] += sign * oi
        elif seed_mode == "svi_rp":
            # RP-native cheap/rich seed (2026-08-11): calibrate the SSVI reference
            # smile on the day-1 OTM chain (Gatheral-Jacquier 3-observable), then
            # mark each strike rich/SHORT (market_IV > ref) or cheap/LONG. The seed
            # is the OI at each strike signed by its cheap/rich marking -- scaling
            # is intrinsic to the chain's OI, no artificial anchor.
            try:
                import svi_rp

                otm_w = _otm_leg_weights(iv_by_date[seed_date], seed_spot, seed_T)
                if otm_w:
                    chain_iv = {kv: iv_by_date[seed_date][kv] for kv in otm_w}
                    ref = svi_rp.calibrate_ssvi(
                        chain_iv,
                        seed_spot,
                        seed_T,
                        oi_by=oi_by_date[seed_date],
                        otm_weights=otm_w,
                    )
                    marks = ref.mark_chain(chain_iv, oi_by_date[seed_date])
                    for k, right, sig, refv, diff, mark, oi in marks:
                        sign = seed_sign * (1.0 if mark == "SHORT" else -1.0)
                        position[(k, right)] += sign * oi
                else:
                    # fall back to replication if the SSVI calibration can't run
                    seed_weights = otm_w or _otm_leg_weights(
                        iv_by_date[seed_date], seed_spot, seed_T
                    )
                    for k, right in seed_weights:
                        position[(k, right)] += seed_sign * oi_by_date[seed_date].get(
                            (k, right), 0
                        )
            except Exception as e:
                print(
                    f"  [acc] svi_rp seed failed for {ticker}: {type(e).__name__} {str(e)[:80]}, "
                    f"falling back to replication seed",
                    flush=True,
                )
                seed_weights = _otm_leg_weights(
                    iv_by_date[seed_date], seed_spot, seed_T
                )
                for k, right in seed_weights:
                    position[(k, right)] += seed_sign * oi_by_date[seed_date].get(
                        (k, right), 0
                    )
        else:  # 'oi_heuristic'
            for (k, right), oi in oi_by_date[seed_date].items():
                sign = seed_sign * (1.0 if right == "C" else -1.0)
                position[(k, right)] += sign * oi
        if position:
            break
        seed_idx += 1

    daily_trace = [
        {
            "date": seed_date,
            "kind": "seed",
            "net_change": sum(position.values()),
            "n_strikes_included": len(position),
        }
    ]

    for prev_d, d in zip(trading_dates[seed_idx:], trading_dates[seed_idx + 1 :]):
        spot_t = spot_by_date[d]
        T_t = max((expiry_date - datetime.strptime(d, "%Y%m%d")).days, 1) / 365.0
        weights_t = _otm_leg_weights(iv_by_date[d], spot_t, T_t)

        # Build per-strike sign map for this day using the vol surface
        # reference (SABR fit), so each strike gets its own sign based on
        # whether its IV trades rich or cheap vs the fitted curve -- instead
        # of the old flat -1.0. Falls back to flat -1.0 if the SABR fit
        # fails for this day (graceful degradation per-day, not per-strike).
        day_chain_iv = iv_by_date[d]
        day_sign_map: dict[tuple[float, str], float] | None = None
        try:
            # Forward ≈ spot for near-term equity options (r,q small);
            # the sign-resolution logic only needs the fit shape, not a
            # precise forward, so spot-as-forward is a safe approximation.
            day_vs_ref = vol_surface_reference.compute_vol_surface_reference(
                ticker, day_chain_iv, spot_t, forward=spot_t, T=T_t
            )
            if day_vs_ref is not None:
                day_sign_map = {}
                for k, right in weights_t:
                    s = vol_surface_reference.resolve_vol_surface_sign(
                        day_vs_ref, k, right
                    )
                    # 0.0 means "no confident read" (inside dead-band) --
                    # fall back to the Layer 1b default (-1.0) for those,
                    # same as _resolve_sign does in dealer_positioning.py.
                    day_sign_map[(k, right)] = s if s != 0.0 else -1.0
        except Exception:
            day_sign_map = None  # fall through to flat sign below

        day_change = 0.0
        n_included = 0
        n_new_strikes = (
            0  # strikes present in day d but missing in prev_d (sparse endpoint gap)
        )

        # Cache OI dicts for this day pair
        oi_today_dict = oi_by_date[d]
        oi_prev_dict = oi_by_date.get(prev_d, {})

        # Normalize this day's vanna weights by the day's own mean absolute
        # vanna so vanna-weighted flow lands on a comparable unit scale to
        # the unweighted (raw-OI) seed contribution, instead of mixing
        # OI-sized seed numbers with vanna-sized (~0.01-1) flow numbers.
        day_vanna_map = vanna_by_date.get(d, {})
        day_abs_vanna_vals = [abs(v) for v in day_vanna_map.values() if v is not None]
        day_mean_abs_vanna = (
            sum(day_abs_vanna_vals) / len(day_abs_vanna_vals)
            if day_abs_vanna_vals
            else None
        )

        for k, right in weights_t:
            # Only compute delta_oi for strikes present in BOTH days.
            # If sparse endpoint: absence in prev_d is ambiguous (could be 0 or endpoint gap),
            # so we skip it to avoid misattributing yesterday's existing position as "new flow today."
            if (k, right) not in oi_today_dict:
                # Strike not in today's snapshot (rolled off) -- skip
                continue
            if (k, right) not in oi_prev_dict:
                # Strike missing from yesterday's snapshot -- sparse endpoint gap
                n_new_strikes += 1
                continue

            # Strike present in BOTH days: safe to compute delta
            oi_today = oi_today_dict[(k, right)]
            oi_prev = oi_prev_dict[(k, right)]
            delta_oi = oi_today - oi_prev
            if delta_oi == 0:
                continue
            if day_sign_map is not None:
                sign = day_sign_map.get((k, right), -1.0)
            else:
                sign = -1.0  # fallback: flat Layer 1b default
            signed_change = sign * delta_oi
            # Vanna-weighted flow (Jason, 2026-08-11): weight the daily OI flow
            # by rec.vanna (× sabr_deviation sign). DEALER_VANNA_FLOW is ON by
            # default; set DEALER_VANNA_FLOW=0 to disable (the arm comparison
            # passes "0"/"1" explicitly). SAFETY: if a day carries no vanna data
            # (sparse greeks route), fall back to the plain signed flow rather
            # than multiply by 0.0 (which would silently zero the book).
            vanna_weight = day_vanna_map.get((k, right), None)
            if os.environ.get("DEALER_VANNA_FLOW", "1") != "0":
                if vanna_weight is not None and day_mean_abs_vanna:
                    signed_change = (
                        sign * delta_oi * (vanna_weight / day_mean_abs_vanna)
                    )
            position[(k, right)] += signed_change
            day_change += signed_change
            n_included += 1

        if n_new_strikes > 0:
            print(
                f"  [{d}] {n_new_strikes} strike(s) seen today but missing yesterday "
                f"(possibly sparse OI endpoint or rolled off)"
            )

        daily_trace.append(
            {
                "date": d,
                "kind": "accumulate",
                "net_change": day_change,
                "n_strikes_included": n_included,
            }
        )

    return AccumulatedPositionResult(
        ticker=ticker,
        expiry=expiry,
        seed_date=seed_date,
        end_date=trading_dates[-1],
        seed_mode=seed_mode,
        lookback_days=lookback_days,
        position_by_strike=dict(position),
        daily_trace=daily_trace,
        dates_used=trading_dates,
    )


def format_accumulated_report(r: AccumulatedPositionResult) -> str:
    lines = [
        f"{r.ticker} {r.expiry} accumulated position, seed={r.seed_mode}, "
        f"{r.seed_date} -> {r.end_date} ({len(r.dates_used)} trading days)",
        "  daily trace:",
    ]
    for row in r.daily_trace:
        lines.append(
            f"    {row['date']} [{row['kind']:<10}] net_change={row['net_change']:>12.2f}  "
            f"strikes_included={row['n_strikes_included']}"
        )
    total = sum(r.position_by_strike.values())
    lines.append(f"  final net accumulated position (all strikes): {total:.2f}")
    top5 = sorted(
        r.position_by_strike.items(), key=lambda kv: abs(kv[1]), reverse=True
    )[:5]
    lines.append("  largest 5 strike-level positions:")
    for (k, right), pos in top5:
        lines.append(f"    {right} {k:.1f}: {pos:.2f}")
    return "\n".join(lines)


if __name__ == "__main__":
    # Live CLI entry point -- needs real network access to
    # api.potatohedge.com, which this dev sandbox does not have (see module
    # docstring). Run this locally to actually get Stage 2 numbers.
    import sys

    ticker = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    target_years = float(sys.argv[2]) if len(sys.argv) > 2 else 0.25
    mode = sys.argv[3] if len(sys.argv) > 3 else "snapshot"

    if mode == "accumulate":
        lookback = int(sys.argv[4]) if len(sys.argv) > 4 else 5
        seed_mode = sys.argv[5] if len(sys.argv) > 5 else "replication"
        acc = compute_accumulated_position(
            ticker,
            target_years=target_years,
            lookback_days=lookback,
            seed_mode=seed_mode,
        )
        print(format_accumulated_report(acc))
    else:
        result = compute_replication_reference(ticker, target_years=target_years)
        print(format_report(result))
