#!/usr/bin/env python3
"""options_chain_scanner.py
Single-expiry options chain scanner for the Volatility Suite.

For a ticker + one chosen expiry (via expiry_selector.py -- closest weekly /
closest monthly-OPEX / closest overall to whatever target_years the user
gives), this pulls EVERY strike's full greek stack from ThetaData:

  - 1st order: delta, gamma, theta, vega, rho, bid/ask/mid, implied vol
    (bulk_snapshot/option/all_greeks -- same endpoint the rest of the suite
    already uses via thetadata_client.option_bulk_greeks)
  - 2nd order: vanna, charm, vomma/volga, veta, speed, zomma, color
    (bulk_snapshot/option/greeks_second_order -- a raw ThetaData passthrough
    catalogued in POTATOHEDGE_API_REFERENCE.md as recovered-but-never-used
    until this module; see thetadata_client.option_bulk_greeks_second_order)
  - open interest (bulk_snapshot/option/open_interest)

and prints the full strike-by-strike table, then does two reads on top of it:

  1. A LOCAL SMILE-FIT edge scan: fits a quadratic to OTM implied vol vs.
     log-moneyness and flags strikes whose live IV sits meaningfully off that
     fitted curve (with real OI behind it) -- a concrete, defensible
     "is this strike mispriced relative to its own smile" signal, not just a
     vague richness call.
  2. A VANNA-weighted positioning read: net dealer vanna by strike (same
     call=+/put=- sign convention as dealer_positioning.py's gamma), where it
     flips sign nearest spot, and whether calls or puts carry the exposure.
     Vanna gets the most weight of the group here on purpose -- it's the
     greek that ties a vol move directly to a hedging flow (dVega/dSpot,
     equivalently dDelta/dVol), which is where most of the signal in a chain
     scan like this actually comes from, more so than gamma or theta alone.

If neither read turns up anything, the scanner says so explicitly rather
than manufacturing a signal, and still reports the vol-regime read (rich /
cheap / fair vs. realized, skew direction) as the fallback "insight."
"""
import math
import os
import json
import warnings
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe

from thetadata_client import ThetaDataController, strike_from_theta
import expiry_selector
from variance_swap_screener import compute_realized_vol
from correlation_engine import fetch_price_history
from dealer_positioning import (
    DARK_BG, GRID_COLOR, TEXT_COLOR, ACCENT_BLUE, ACCENT_GREEN, ACCENT_RED,
    ACCENT_GOLD, ACCENT_PURPLE, ACCENT_CYAN, ACCENT_ORANGE,
    CONTRACT_MULTIPLIER, VANNA_PP_SCALE,
)
from strategy_recommender import StrategyRecommender, format_strategies_artifact

warnings.filterwarnings("ignore", category=FutureWarning, module="pandas")

RISK_FREE_RATE = 0.05
DEFAULT_A = expiry_selector.DEFAULT_A

# Minimum open interest for a strike to count as a real edge candidate rather
# than a stale/illiquid quote artifact.
MIN_OI_FOR_EDGE = 10
# Minimum |market IV - fitted smile IV|, in vol points, to flag a strike.
MIN_RESIDUAL_VOL_PTS = 1.5

# ---------- Field-name fallback (ThetaData's exact key names for the newer
# greeks_second_order endpoint haven't been confirmed against a live response
# in this environment -- same "best effort, NaN if missing" posture as
# dealer_positioning._extract_greek_field / _GREEK_FIELD_CANDIDATES). ----------
FIRST_ORDER_CANDIDATES = {
    'delta': ['delta', 'Delta', 'DELTA'],
    'gamma': ['gamma', 'Gamma', 'GAMMA'],
    'theta': ['theta', 'Theta', 'THETA'],
    'vega': ['vega', 'Vega', 'VEGA'],
    'rho': ['rho', 'Rho', 'RHO'],
    'iv': ['implied_vol', 'impliedVol', 'iv', 'IV'],
    'bid': ['bid', 'Bid', 'BID'],
    'ask': ['ask', 'Ask', 'ASK'],
}
SECOND_ORDER_CANDIDATES = {
    'vanna': ['vanna', 'Vanna', 'VANNA'],
    'charm': ['charm', 'Charm', 'CHARM', 'delta_decay', 'deltaDecay'],
    'vomma': ['vomma', 'Vomma', 'VOMMA', 'volga', 'Volga', 'VOLGA'],
    'veta': ['veta', 'Veta', 'VETA', 'vega_decay', 'vegaDecay'],
    'speed': ['speed', 'Speed', 'SPEED'],
    'zomma': ['zomma', 'Zomma', 'ZOMMA'],
    'color': ['color', 'Color', 'COLOR', 'gamma_decay', 'gammaDecay'],
}


def _to_float(x):
    try:
        v = float(x)
        return v
    except (TypeError, ValueError):
        return float('nan')


def _extract(row: dict, candidates: List[str]) -> float:
    for key in candidates:
        if key in row and row[key] not in (None, ''):
            v = _to_float(row[key])
            if not math.isnan(v):
                return v
    return float('nan')


def compute_forward_price(S0: float, r: float, q: float, T: float) -> float:
    return S0 * math.exp((r - q) * T)


def _json_safe(obj):
    """
    JSON serializer for objects not serializable by default json code.
    Handles NaN, Infinity, numpy types. Used for cross-process handoffs.
    Addresses R7: Ensures NaN/Infinity are converted to None for JSON compatibility.
    """
    if isinstance(obj, (np.integer, np.floating)):
        v = float(obj)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, (float,)):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    elif hasattr(obj, '__dict__'):
        return obj.__dict__
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _sanitize_for_json(obj):
    """
    Recursively convert NaN and Infinity to None in nested dicts/lists.
    Used to pre-process data before json.dump to ensure valid JSON output.
    """
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_sanitize_for_json(item) for item in obj]
    elif isinstance(obj, (float, np.floating)):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    return obj


def _extract_chain_data_from_df(scan_result_df: pd.DataFrame, option_type: str = 'call') -> Dict[str, List[float]]:
    """
    Extract chain_data dict from options_chain_scanner DataFrame.

    Filters to one option type (call or put) and creates dict with parallel arrays.
    Addresses R1: Complete DataFrame extraction logic for strategy recommender.

    Args:
        scan_result_df: DataFrame from scan_result.df with columns:
                        strike, right, delta, gamma, theta, vega, vanna, bid_ask_spread, oi
        option_type: 'call' or 'put'

    Returns:
        Dict with keys: strikes, delta, gamma, theta, vega, vanna, bid_ask_spread, open_interest
    """
    right_code = 'C' if option_type.lower() == 'call' else 'P'
    filtered = scan_result_df[scan_result_df['right'] == right_code].sort_values('strike').reset_index(drop=True)

    if len(filtered) == 0:
        raise ValueError(f"No {option_type} options found in scan result")

    return {
        'strikes': filtered['strike'].tolist(),
        'delta': filtered['delta'].tolist(),
        'gamma': filtered['gamma'].tolist(),
        'theta': filtered['theta'].tolist(),
        'vega': filtered['vega'].tolist(),
        'vanna': filtered['vanna'].tolist(),
        'bid_ask_spread': filtered['bid_ask_spread'].tolist(),
        'open_interest': filtered['oi'].tolist(),
    }


def _transform_edge_strikes(edge_candidates: List[dict]) -> List[dict]:
    """
    Transform edge_candidates from chain scanner to strategy recommender format.

    Addresses R2: Maps edge_kind ('rich'/'cheap') → edge_type ('SELL'/'BUY').

    Args:
        edge_candidates: List of dicts from scan_result.edge_candidates with keys:
                        strike, right, edge_kind ('rich'/'cheap'), iv_residual_pts, oi

    Returns:
        List of dicts with keys: strike, edge_type ('SELL'/'BUY'), iv_deviation, oi
    """
    transformed = []
    for candidate in edge_candidates:
        edge_type = 'SELL' if candidate['edge_kind'] == 'rich' else 'BUY'
        transformed.append({
            'strike': candidate['strike'],
            'edge_type': edge_type,
            'iv_deviation': candidate['iv_residual_pts'],  # in basis points
            'oi': candidate['oi'],
        })
    return transformed


@dataclass
class ScanResult:
    ticker: str
    expiry: str
    T_years: float
    spot: float
    forward: float
    dividend_yield: float
    df: pd.DataFrame
    atm_iv_pct: float
    rv_30_pct: float
    rv_60_pct: float
    rv_90_pct: float
    rv_match_pct: float
    smile_a: float          # quadratic curvature coefficient (convexity of the smile)
    smile_b: float          # linear coefficient (skew slope in log-moneyness)
    net_vanna_shares: float
    call_vanna_shares: float
    put_vanna_shares: float
    vanna_flip_strike: Optional[float]
    top_vanna_strikes: List[Tuple[float, float]]
    edge_candidates: List[dict]
    regime: str
    verdict: str
    insight: str
    strategies: List[dict] = field(default_factory=list)  # Recommended strategies from chain scan


# ---------- Data pull / merge ----------
def build_chain_dataframe(td, ticker: str, expiration: str) -> pd.DataFrame:
    """Merge all_greeks (1st order), greeks_second_order (2nd order), and
    open_interest into one row per (strike, right). Missing 2nd-order fields
    are left NaN rather than dropping the row -- a chain with partial 2nd-order
    coverage should still print the strikes it does have, same posture as
    dealer_positioning.py's GammaRecord.vanna/charm defaults."""
    first_rows = td.option_bulk_greeks(ticker, expiration)
    if not first_rows:
        raise ValueError(f"No option chain data for {ticker} {expiration}")
    try:
        second_rows = td.option_bulk_greeks_second_order(ticker, expiration)
    except Exception as e:
        print(f"  [warn] 2nd-order greeks fetch failed ({e}); vanna/charm/vomma/veta will be N/A.")
        second_rows = []
    try:
        oi_rows = td.option_bulk_oi(ticker, expiration)
    except Exception as e:
        print(f"  [warn] open interest fetch failed ({e}); OI will be 0.")
        oi_rows = []

    def _key(row):
        try:
            return (int(float(row['strike'])), str(row.get('right', '')).strip().upper()[:1])
        except (KeyError, ValueError, TypeError):
            return None

    second_map: Dict[tuple, dict] = {}
    for row in second_rows:
        k = _key(row)
        if k:
            second_map[k] = row

    oi_map: Dict[tuple, int] = {}
    for row in oi_rows:
        k = _key(row)
        if not k:
            continue
        try:
            oi_map[k] = int(float(row.get('open_interest', 0)))
        except (ValueError, TypeError):
            pass

    records = []
    for row in first_rows:
        k = _key(row)
        if not k:
            continue
        strike_theta, right = k
        strike = strike_from_theta(strike_theta)
        bid = _extract(row, FIRST_ORDER_CANDIDATES['bid'])
        ask = _extract(row, FIRST_ORDER_CANDIDATES['ask'])
        mid = (bid + ask) / 2.0 if (not math.isnan(bid) and not math.isnan(ask) and bid > 0 and ask > 0) else float('nan')
        iv = _extract(row, FIRST_ORDER_CANDIDATES['iv'])
        second_row = second_map.get(k, {})
        records.append({
            'strike': strike, 'right': right,
            'bid': bid, 'ask': ask, 'mid': mid, 'iv': iv,
            'delta': _extract(row, FIRST_ORDER_CANDIDATES['delta']),
            'gamma': _extract(row, FIRST_ORDER_CANDIDATES['gamma']),
            'theta': _extract(row, FIRST_ORDER_CANDIDATES['theta']),
            'vega': _extract(row, FIRST_ORDER_CANDIDATES['vega']),
            'rho': _extract(row, FIRST_ORDER_CANDIDATES['rho']),
            'vanna': _extract(second_row, SECOND_ORDER_CANDIDATES['vanna']),
            'charm': _extract(second_row, SECOND_ORDER_CANDIDATES['charm']),
            'vomma': _extract(second_row, SECOND_ORDER_CANDIDATES['vomma']),
            'veta': _extract(second_row, SECOND_ORDER_CANDIDATES['veta']),
            'speed': _extract(second_row, SECOND_ORDER_CANDIDATES['speed']),
            'zomma': _extract(second_row, SECOND_ORDER_CANDIDATES['zomma']),
            'color': _extract(second_row, SECOND_ORDER_CANDIDATES['color']),
            'oi': oi_map.get(k, 0),
        })

    df = pd.DataFrame.from_records(records)
    if df.empty:
        raise ValueError(f"No usable rows for {ticker} {expiration}")
    df = df.sort_values(['strike', 'right']).reset_index(drop=True)
    return df


# ---------- Smile fit / edge scan ----------
def fit_smile_and_flag_edges(df: pd.DataFrame, forward: float) -> Tuple[pd.DataFrame, float, float]:
    """Fit a quadratic to OTM IV vs. log-moneyness (put IV for K<=F, call IV
    for K>F -- same OTM convention variance_swap_screener/variance_swap_live
    already use for the fair-variance replication) and flag strikes whose
    live IV deviates meaningfully from that fit. Returns (df with fit_iv /
    iv_residual_pts / is_edge columns added, smile_a, smile_b)."""
    df = df.copy()
    df['moneyness'] = np.log(df['strike'] / forward)
    df['is_otm'] = np.where(df['strike'] <= forward, df['right'] == 'P', df['right'] == 'C')

    otm = df[df['is_otm'] & df['iv'].notna() & (df['iv'] > 0)]
    df['fit_iv'] = np.nan
    df['iv_residual_pts'] = np.nan
    df['is_edge'] = False
    df['edge_kind'] = ''

    if len(otm) < 5:
        return df, float('nan'), float('nan')

    x = otm['moneyness'].values
    y = otm['iv'].values
    coeffs = np.polyfit(x, y, 2)
    a, b, c = coeffs
    fit_all = np.polyval(coeffs, df['moneyness'].values)
    df['fit_iv'] = fit_all
    df.loc[df['is_otm'] & df['iv'].notna(), 'iv_residual_pts'] = (
        (df.loc[df['is_otm'] & df['iv'].notna(), 'iv'] - df.loc[df['is_otm'] & df['iv'].notna(), 'fit_iv']) * 100.0
    )

    # Robust dispersion of residuals (median absolute deviation, scaled) so the
    # threshold adapts to how noisy this particular chain's smile is instead
    # of a single hardcoded vol-point cutoff being too tight or too loose
    # across very different tickers.
    otm_resid = df.loc[df['is_otm'] & df['iv_residual_pts'].notna(), 'iv_residual_pts']
    if len(otm_resid) >= 5:
        mad = float(np.median(np.abs(otm_resid - np.median(otm_resid)))) * 1.4826
        threshold = max(MIN_RESIDUAL_VOL_PTS, 1.5 * mad)
    else:
        threshold = MIN_RESIDUAL_VOL_PTS

    edge_mask = df['is_otm'] & (df['iv_residual_pts'].abs() >= threshold) & (df['oi'] >= MIN_OI_FOR_EDGE)
    df.loc[edge_mask, 'is_edge'] = True
    df.loc[edge_mask & (df['iv_residual_pts'] > 0), 'edge_kind'] = 'rich'
    df.loc[edge_mask & (df['iv_residual_pts'] < 0), 'edge_kind'] = 'cheap'
    return df, float(a), float(b)


# ---------- Vanna positioning read ----------
def compute_vanna_positioning(df: pd.DataFrame, spot: float) -> dict:
    """Net dealer vanna exposure by strike, using the same call=+/put=-
    convention dealer_positioning.py uses for gamma (see its _dealer_sign
    docstring for the rationale/caveats -- this is a modeling assumption
    about which side of OI dealers sit on, not a measured fact)."""
    d = df.copy()
    d['sign'] = np.where(d['right'] == 'C', 1.0, -1.0)
    has_vanna = d['vanna'].notna()
    d['vanna_shares'] = 0.0
    d.loc[has_vanna, 'vanna_shares'] = (
        d.loc[has_vanna, 'sign'] * d.loc[has_vanna, 'vanna'] * d.loc[has_vanna, 'oi']
        * CONTRACT_MULTIPLIER * VANNA_PP_SCALE
    )

    by_strike = d.groupby('strike')['vanna_shares'].sum().sort_index()
    net_vanna = float(by_strike.sum())
    call_vanna = float(d.loc[d['right'] == 'C', 'vanna_shares'].sum())
    put_vanna = float(d.loc[d['right'] == 'P', 'vanna_shares'].sum())

    # Flip strike: adjacent-strike sign change in the per-strike net vanna
    # profile, nearest to spot (same "nearest crossing to the reference point"
    # logic dealer_positioning.py uses for its gamma flip level).
    strikes = by_strike.index.values
    values = by_strike.values
    flip_strike = None
    if len(strikes) > 1 and has_vanna.any():
        signs = np.sign(values)
        crossings = np.where(np.diff(signs) != 0)[0]
        if len(crossings) > 0:
            spot_idx = int(np.argmin(np.abs(strikes - spot)))
            nearest = crossings[np.argmin(np.abs(crossings - spot_idx))]
            flip_strike = float((strikes[nearest] + strikes[nearest + 1]) / 2.0)

    top_vanna = sorted(zip(strikes.tolist(), values.tolist()), key=lambda t: abs(t[1]), reverse=True)[:5]

    return {
        'net_vanna_shares': net_vanna,
        'call_vanna_shares': call_vanna,
        'put_vanna_shares': put_vanna,
        'vanna_flip_strike': flip_strike,
        'top_vanna_strikes': top_vanna,
        'has_vanna_data': bool(has_vanna.any()),
    }


# ---------- Main scan ----------
def scan_chain(ticker: str, expiration: str, target_years: float, td) -> ScanResult:
    spot = td.fetch_spot_price(ticker)
    if spot <= 0:
        raise ValueError(f"Could not fetch spot for {ticker}")
    dividend_yield = td.fetch_dividend_yield(ticker)

    exp_date = datetime.strptime(expiration, "%Y%m%d").date()
    today = datetime.now(timezone.utc).date()
    actual_T = max((exp_date - today).days, 0) / DEFAULT_A
    r_live = td.fetch_risk_free_rate(actual_T)
    r_use = r_live if r_live is not None else RISK_FREE_RATE
    forward = compute_forward_price(spot, r_use, dividend_yield, actual_T)

    df = build_chain_dataframe(td, ticker, expiration)
    df, smile_a, smile_b = fit_smile_and_flag_edges(df, forward)
    vanna_info = compute_vanna_positioning(df, spot)

    # ATM IV via the OTM-side convention (put IV below forward, call IV above --
    # same convention variance_swap_screener/variance_swap_live use), taking
    # whichever listed strike sits closest to the forward.
    otm_df = df[df['is_otm'] & df['iv'].notna() & (df['iv'] > 0)]
    if len(otm_df):
        atm_idx = (otm_df['strike'] - forward).abs().idxmin()
        atm_iv_pct = float(otm_df.loc[atm_idx, 'iv'] * 100.0)
    else:
        atm_iv_pct = float('nan')

    try:
        hist_df = fetch_price_history([ticker], period="2y")
        prices = hist_df[ticker].values.flatten()
    except Exception:
        prices = np.array([])
    if len(prices) >= 5:
        rv_30 = compute_realized_vol(prices, min(30, len(prices)))
        rv_60 = compute_realized_vol(prices, min(60, len(prices)))
        rv_90 = compute_realized_vol(prices, min(90, len(prices)))
        match_lookback = max(int(actual_T * 252), 5)  # trading-day price points ~ option life
        rv_match = compute_realized_vol(prices, min(match_lookback, len(prices)))
    else:
        rv_30 = rv_60 = rv_90 = rv_match = float('nan')

    rv_match_pct = rv_match * 100 if not math.isnan(rv_match) else float('nan')
    vrp_pts = (atm_iv_pct - rv_match_pct) if (not math.isnan(atm_iv_pct) and not math.isnan(rv_match_pct)) else float('nan')

    if not math.isnan(vrp_pts):
        if vrp_pts >= 4.0:
            regime = "RICH"
        elif vrp_pts <= -2.0:
            regime = "CHEAP"
        else:
            regime = "FAIR"
    else:
        regime = "UNKNOWN"

    edge_rows = df[df['is_edge']].sort_values('iv_residual_pts', key=lambda s: s.abs(), ascending=False)
    edge_candidates = edge_rows[['strike', 'right', 'iv', 'fit_iv', 'iv_residual_pts', 'oi', 'edge_kind']].to_dict('records')

    verdict, insight = _build_insight(
        ticker, expiration, actual_T, spot, forward, atm_iv_pct,
        rv_30, rv_60, rv_90, rv_match_pct, vrp_pts, regime,
        smile_a, smile_b, vanna_info, edge_candidates,
    )

    return ScanResult(
        ticker=ticker, expiry=expiration, T_years=actual_T, spot=spot, forward=forward,
        dividend_yield=dividend_yield, df=df, atm_iv_pct=atm_iv_pct,
        rv_30_pct=rv_30 * 100 if not math.isnan(rv_30) else float('nan'),
        rv_60_pct=rv_60 * 100 if not math.isnan(rv_60) else float('nan'),
        rv_90_pct=rv_90 * 100 if not math.isnan(rv_90) else float('nan'),
        rv_match_pct=rv_match_pct, smile_a=smile_a, smile_b=smile_b,
        net_vanna_shares=vanna_info['net_vanna_shares'],
        call_vanna_shares=vanna_info['call_vanna_shares'],
        put_vanna_shares=vanna_info['put_vanna_shares'],
        vanna_flip_strike=vanna_info['vanna_flip_strike'],
        top_vanna_strikes=vanna_info['top_vanna_strikes'],
        edge_candidates=edge_candidates, regime=regime, verdict=verdict, insight=insight,
    )


def _build_insight(ticker, expiration, T, spot, forward, atm_iv_pct, rv_30, rv_60, rv_90,
                   rv_match_pct, vrp_pts, regime, smile_a, smile_b, vanna_info, edge_candidates) -> Tuple[str, str]:
    lines = []
    has_edge = len(edge_candidates) > 0
    has_vanna = vanna_info['has_vanna_data']

    if has_edge:
        verdict = "EDGE DETECTED"
        lines.append(f"{len(edge_candidates)} strike(s) sit off the fitted smile with real OI behind them:")
        for c in edge_candidates[:8]:
            tag = "RICH (sell candidate)" if c['edge_kind'] == 'rich' else "CHEAP (buy candidate)"
            lines.append(
                f"  {c['strike']:.2f}{c['right']}: IV {c['iv']*100:.2f}% vs fit {c['fit_iv']*100:.2f}% "
                f"({c['iv_residual_pts']:+.2f} vol pts, OI={c['oi']:.0f}) -> {tag}"
            )
    else:
        verdict = "NO CLEAR EDGE"
        lines.append("No strike deviates meaningfully from its own fitted smile once OI is filtered for -- "
                     "this chain is internally consistent; there's no local mispricing to trade on relative "
                     "to its own neighbors at this expiry.")

    lines.append("")
    if has_vanna:
        net = vanna_info['net_vanna_shares']
        call_v = vanna_info['call_vanna_shares']
        put_v = vanna_info['put_vanna_shares']
        flip = vanna_info['vanna_flip_strike']
        lines.append(f"Vanna positioning (dealer convention, calls=+/puts=-; net = {net:+,.0f} shares/1pp IV):")
        if abs(call_v) > abs(put_v) * 1.3:
            lines.append(f"  Call side dominates vanna ({call_v:+,.0f} vs put {put_v:+,.0f}). "
                         "A vol pop pushes dealers toward MORE positive delta on this side -- "
                         "if spot is also rallying when vol rises here, expect vanna-driven dealer "
                         "buying to amplify the move; if vol rises on a selloff instead, this side's "
                         "vanna works against the selloff (dealers buying into weakness).")
        elif abs(put_v) > abs(call_v) * 1.3:
            lines.append(f"  Put side dominates vanna ({put_v:+,.0f} vs call {call_v:+,.0f}). "
                         "Classic negative spot/vol correlation setup: as spot falls and IV rises, "
                         "dealer vanna hedging on the put side tends to add SELLING pressure into "
                         "the move -- the textbook 'vanna feeds the selloff' dynamic.")
        else:
            lines.append(f"  Call ({call_v:+,.0f}) and put ({put_v:+,.0f}) vanna are roughly balanced -- "
                         "no strong directional vanna-hedging bias either way from this expiry alone.")
        if flip is not None:
            side = "above" if flip > spot else "below"
            lines.append(f"  Net vanna flips sign near strike {flip:.2f} ({side} spot ${spot:.2f}) -- "
                         "that's the level where the vanna-hedging flow direction itself would reverse.")
        top = vanna_info['top_vanna_strikes'][:3]
        if top:
            desc = ", ".join(f"{k:.2f} ({v:+,.0f})" for k, v in top)
            lines.append(f"  Largest vanna concentrations: {desc}")
    else:
        lines.append("Vanna/2nd-order data wasn't available in ThetaData's response for this chain "
                     "(bulk_snapshot/option/greeks_second_order returned nothing usable) -- "
                     "positioning read above is skipped rather than guessed.")

    lines.append("")
    regime_txt = {
        "RICH": f"Vol looks RICH here: ATM IV {atm_iv_pct:.2f}% vs matched-tenor RV {rv_match_pct:.2f}% "
                f"({vrp_pts:+.2f} vol pts). Favors premium selling if the vanna/edge picture above doesn't argue otherwise.",
        "CHEAP": f"Vol looks CHEAP here: ATM IV {atm_iv_pct:.2f}% vs matched-tenor RV {rv_match_pct:.2f}% "
                f"({vrp_pts:+.2f} vol pts). Favors buying premium / owning gamma over selling it.",
        "FAIR": f"Vol looks FAIRLY PRICED: ATM IV {atm_iv_pct:.2f}% vs matched-tenor RV {rv_match_pct:.2f}% "
                f"({vrp_pts:+.2f} vol pts) is inside a normal range -- no strong environment-level edge either way.",
        "UNKNOWN": "Couldn't compare IV to realized vol (insufficient price history) -- no environment read available.",
    }[regime]
    lines.append(regime_txt)
    if not math.isnan(smile_b):
        skew_txt = "put-skewed (downside richer)" if smile_b < -0.02 else ("call-skewed (upside richer)" if smile_b > 0.02 else "roughly flat")
        lines.append(f"Smile skew slope is {skew_txt} (b={smile_b:.4f}); curvature a={smile_a:.4f}.")

    insight = "\n".join(lines)
    return verdict, insight


# ---------- Printing / export ----------
def print_report(result: ScanResult):
    df = result.df
    print("\n" + "=" * 130)
    print(f"OPTIONS CHAIN SCANNER -- {result.ticker}  Expiry {result.expiry}  "
          f"(T={result.T_years:.4f}yr, DTE={int(round(result.T_years*DEFAULT_A))})")
    print("=" * 130)
    print(f"Spot: ${result.spot:.2f}   Forward: ${result.forward:.2f}   "
          f"ATM IV: {result.atm_iv_pct:.2f}%   Regime: {result.regime}")
    print("-" * 130)
    header = (f"{'Strike':>9} {'R':>1} {'Bid':>8} {'Ask':>8} {'IV%':>7} {'Delta':>8} {'Gamma':>9} "
              f"{'Vega':>8} {'Theta':>8} {'Vanna':>10} {'Charm':>10} {'Vomma':>10} {'Veta':>10} {'OI':>8} {'Edge':>6}")
    print(header)
    print("-" * 130)
    for _, row in df.iterrows():
        def f(v, fmt="{:.4f}"):
            return "n/a" if pd.isna(v) else fmt.format(v)
        edge_tag = row['edge_kind'].upper() if row.get('is_edge') else ""
        print(f"{row['strike']:9.2f} {row['right']:>1} {f(row['bid'],'{:.2f}'):>8} {f(row['ask'],'{:.2f}'):>8} "
              f"{f(row['iv']*100 if not pd.isna(row['iv']) else float('nan'),'{:.2f}'):>7} "
              f"{f(row['delta']):>8} {f(row['gamma'],'{:.5f}'):>9} {f(row['vega']):>8} {f(row['theta']):>8} "
              f"{f(row['vanna']):>10} {f(row['charm']):>10} {f(row['vomma']):>10} {f(row['veta']):>10} "
              f"{int(row['oi']) if not pd.isna(row['oi']) else 0:>8} {edge_tag:>6}")
    print("-" * 130)
    print(f"\nVERDICT: {result.verdict}\n")
    print(result.insight)
    print("=" * 130)


def export_csv(result: ScanResult, output_dir: Optional[str] = None) -> str:
    out_dir = output_dir or os.getenv("VS_OUTPUT_DIR", os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(out_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(out_dir, f"{result.ticker}_{result.expiry}_chain_scan_{timestamp}.csv")
    result.df.to_csv(filename, index=False)
    return filename


def plot_scanner_charts(result: ScanResult, output_dir: Optional[str] = None) -> str:
    df = result.df
    out_dir = output_dir or os.getenv("VS_OUTPUT_DIR", os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(out_dir, exist_ok=True)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 12), facecolor=DARK_BG)

    # ---- Panel 1: IV smile with fit + edge flags ----
    ax1.set_facecolor('#161b22')
    otm = df[df['is_otm'] & df['iv'].notna()]
    ax1.scatter(otm['strike'], otm['iv'] * 100, s=28, color=ACCENT_BLUE, alpha=0.85, label='Market IV (OTM)', zorder=3)
    fit_sorted = df.sort_values('strike')
    ax1.plot(fit_sorted['strike'], fit_sorted['fit_iv'] * 100, color=ACCENT_GOLD, linewidth=2, alpha=0.9, label='Fitted smile', zorder=2)
    edges = df[df['is_edge']]
    if len(edges):
        rich = edges[edges['edge_kind'] == 'rich']
        cheap = edges[edges['edge_kind'] == 'cheap']
        if len(rich):
            ax1.scatter(rich['strike'], rich['iv'] * 100, s=90, facecolors='none', edgecolors=ACCENT_RED, linewidths=2, label='Rich (edge)', zorder=4)
        if len(cheap):
            ax1.scatter(cheap['strike'], cheap['iv'] * 100, s=90, facecolors='none', edgecolors=ACCENT_GREEN, linewidths=2, label='Cheap (edge)', zorder=4)
    ax1.axvline(result.spot, color=ACCENT_CYAN, linestyle='--', linewidth=1.5, alpha=0.8)
    ax1.axvline(result.forward, color=ACCENT_PURPLE, linestyle=':', linewidth=1.5, alpha=0.8)
    ax1.set_title(f"{result.ticker} {result.expiry} -- IV Smile vs. Fit", color=TEXT_COLOR, fontsize=13, fontweight='bold')
    ax1.set_xlabel('Strike', color=TEXT_COLOR)
    ax1.set_ylabel('Implied Vol (%)', color=TEXT_COLOR)
    ax1.tick_params(colors=TEXT_COLOR)
    ax1.grid(True, color=GRID_COLOR, alpha=0.4)
    for spine in ax1.spines.values():
        spine.set_color(GRID_COLOR)
    ax1.legend(facecolor='#161b22', edgecolor=GRID_COLOR, labelcolor=TEXT_COLOR, fontsize=8)

    # ---- Panel 2: net vanna by strike ----
    ax2.set_facecolor('#161b22')
    by_strike = df.copy()
    by_strike['sign'] = np.where(by_strike['right'] == 'C', 1.0, -1.0)
    by_strike['vanna_shares'] = np.where(
        by_strike['vanna'].notna(),
        by_strike['sign'] * by_strike['vanna'].fillna(0.0) * by_strike['oi'] * CONTRACT_MULTIPLIER * VANNA_PP_SCALE,
        0.0,
    )
    grouped = by_strike.groupby('strike')['vanna_shares'].sum().sort_index()
    if len(grouped):
        colors = [ACCENT_BLUE if v >= 0 else ACCENT_RED for v in grouped.values]
        diffs = np.diff(grouped.index.values)
        bar_width = (float(np.median(diffs)) if len(diffs) else 1.0) * 0.7
        ax2.bar(grouped.index, grouped.values, width=bar_width, color=colors, alpha=0.9)
    ax2.axhline(0, color='#8b949e', linewidth=0.8, alpha=0.6)
    ax2.axvline(result.spot, color=ACCENT_CYAN, linestyle='--', linewidth=1.5, alpha=0.8)
    if result.vanna_flip_strike is not None:
        ax2.axvline(result.vanna_flip_strike, color=ACCENT_ORANGE, linestyle=':', linewidth=1.5, alpha=0.8)
    ax2.set_title('Net Dealer Vanna by Strike (calls +, puts -)', color=TEXT_COLOR, fontsize=13, fontweight='bold')
    ax2.set_xlabel('Strike', color=TEXT_COLOR)
    ax2.set_ylabel('Vanna (shares / 1pp IV)', color=TEXT_COLOR)
    ax2.tick_params(colors=TEXT_COLOR)
    ax2.grid(True, color=GRID_COLOR, alpha=0.4)
    for spine in ax2.spines.values():
        spine.set_color(GRID_COLOR)

    plt.tight_layout()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(out_dir, f"{result.ticker}_{result.expiry}_chain_scan_{timestamp}.png")
    plt.savefig(filename, dpi=180, bbox_inches='tight', facecolor=DARK_BG, edgecolor='none')
    plt.close(fig)
    return filename


# ---------- Suite-integration entry point ----------
def run_chain_scanner(ticker: str, target_years: float = 0.25, expiration: Optional[str] = None,
                      output_dir: Optional[str] = None) -> tuple:
    """Programmatic, non-interactive runner (mirrors run_variance_swap_live /
    screen_ticker / run_dealer_positioning conventions) for volatility_suite.py.
    `expiration`, if given, pins this to the exact date the suite resolved
    interactively for the rest of the run; otherwise falls back to a plain
    nearest-expiry lookup (no prompting -- this is meant to be called from
    inside an already-orchestrated run)."""
    out_dir = output_dir or os.getenv("VS_OUTPUT_DIR", os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(out_dir, exist_ok=True)
    files = []
    td = ThetaDataController()
    try:
        exp, actual_T = expiry_selector.resolve_expiration(td, ticker, expiration, target_years)
        result = scan_chain(ticker, exp, actual_T, td)
    finally:
        td.close()
    print_report(result)
    try:
        files.append(export_csv(result, out_dir))
    except Exception as e:
        print(f"  CSV export failed: {e}")
    try:
        files.append(plot_scanner_charts(result, out_dir))
    except Exception as e:
        print(f"  Chart export failed: {e}")

    # Generate strategy recommendations based on detected edges and vol regime (Task 3 integration)
    strategies_artifact = None
    edge_candidates = result.edge_candidates or []

    if len(edge_candidates) > 0:
        try:
            # Extract chain_data from DataFrame (call-only for now, can be parameterized later)
            # Addresses R1: Complete DataFrame extraction logic
            chain_data = _extract_chain_data_from_df(result.df, option_type='call')

            # Transform edge_candidates to strategy recommender format
            # Addresses R2: Maps edge_kind ('rich'/'cheap') → edge_type ('SELL'/'BUY')
            edge_strikes = _transform_edge_strikes(edge_candidates)

            # Initialize and run recommender
            # Addresses R3: Complete integration code with all imports
            recommender = StrategyRecommender(
                chain_data=chain_data,
                edge_strikes=edge_strikes,
                vol_regime=result.regime,
                current_price=result.spot,
                expiry_days=(np.datetime64(result.expiry) - np.datetime64('today')).astype('timedelta64[D]').astype(float),
            )

            strategies = recommender.recommend()
            strategies_artifact = format_strategies_artifact(
                strategies=strategies,
                chain_verdict=result.verdict,
                vol_regime=result.regime,
                current_price=result.spot,
                expiration_date=result.expiry,
            )

            # Export strategies JSON to output directory
            strategies_file = Path(out_dir) / 'chain_strategies.json'
            with open(strategies_file, 'w') as f:
                json.dump(_sanitize_for_json(strategies_artifact), f, default=_json_safe, indent=2)

            # Store strategies in result for return to volatility_suite
            result.strategies = strategies_artifact.get('strategies', [])

        except Exception as e:
            # Log error but don't fail entire scan if strategies fail
            print(f"[Warning] Strategy recommendation failed: {e}")
            strategies_artifact = {'strategies': [], 'error': str(e)}
            result.strategies = []
    else:
        # No edges detected, write empty strategies artifact (Addresses R6: Always write)
        strategies_artifact = {
            'version': '1.0',
            'timestamp': pd.Timestamp.utcnow().isoformat(),
            'chain_verdict': result.verdict,
            'vol_regime': result.regime,
            'current_price': float(result.spot),
            'expiration_date': result.expiry,
            'strategies': [],
            'summary': {'total_recommendations': 0, 'by_type': {}, 'by_regime': result.regime}
        }

        # Write empty artifact for consistency (R6 fix - always write)
        strategies_file = Path(out_dir) / 'chain_strategies.json'
        with open(strategies_file, 'w') as f:
            json.dump(_sanitize_for_json(strategies_artifact), f, default=_json_safe, indent=2)

        result.strategies = []

    interp = f"{result.verdict} -- {result.regime} vol regime.\n{result.insight}"
    return files, interp, result


def main():
    print("=" * 60)
    print("OPTIONS CHAIN SCANNER")
    print("=" * 60)
    ticker = input("Enter ticker: ").strip().upper() or "SPY"
    td = ThetaDataController()
    try:
        expiration, target_years = expiry_selector.choose_expiry_interactive(td, ticker)
        result = scan_chain(ticker, expiration, target_years, td)
    finally:
        td.close()

    print_report(result)

    out_dir = os.getenv("VS_OUTPUT_DIR", os.path.dirname(os.path.abspath(__file__)))
    try:
        csv_path = export_csv(result, out_dir)
        print(f"\nSaved CSV: {csv_path}")
    except Exception as e:
        print(f"CSV export failed: {e}")
    try:
        plot_path = plot_scanner_charts(result, out_dir)
        print(f"Saved chart: {plot_path}")
    except Exception as e:
        print(f"Chart export failed: {e}")
    print("\nDone.")


if __name__ == "__main__":
    main()
