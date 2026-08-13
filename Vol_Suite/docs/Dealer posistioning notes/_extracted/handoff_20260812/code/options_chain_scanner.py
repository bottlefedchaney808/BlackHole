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
import warnings
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

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
    CANONICAL_SIGN_MODEL, compute_expiry_sign_map, SIGN_MODEL_LABEL,
    DealerPositioningResult, compute_dealer_positioning,
)

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
    sabr_params: Optional[dict] = None   # calibrated SABR (alpha/beta/rho/nu/rmse) when available
    # The shared dealer engine result (compute_dealer_positioning, 150d
    # window) this scan's vanna positioning was taken from. plot_scanner_charts
    # renders result.dealer_result.vanna_shares_by_strike verbatim, so the
    # scanner chart == the dealer 4-panel vanna panel by construction.
    dealer_result: Optional[DealerPositioningResult] = None


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


# ---------- SABR smile fit (reuses Hagan formula; no re-fetch) ----------
def fit_sabr_smile(df: pd.DataFrame, forward: float, T_years: float,
                   use_sabr: bool = True, r: float = 0.0, q: float = 0.0,
                   ) -> Tuple[pd.DataFrame, float, float, Optional[dict]]:
    """Fit SABR (Hagan 2002) to the OTM IV curve of one chain and flag edges.

    Reuses the options suite's `sabr_vol_hagan` closed form (pure math — no
    second ThetaData pull). The scanner has already fetched the full chain, so
    we calibrate against OUR OTM IVs rather than constructing SABRCalibrator
    (which would re-fetch its own smile). Calibration: scipy least_squares on
    (alpha, rho, nu) with beta pinned at 0.5 unless a free fit improves RMSE by
    >=8% (mirrors SABRCalibrator's guard). fit_iv / iv_residual_pts / edge flags
    keep the SAME contract as the quadratic path.

    Returns (df, smile_a, smile_b, sabr_params) where smile_a/b are kept for
    back-compat (filled with the quadratic result when the SABR fit is off) and
    sabr_params is None when SABR is disabled or fails (caller falls back).
    """
    try:
        from SABRModel import sabr_vol_hagan
        from scipy.optimize import least_squares
    except Exception:
        use_sabr = False
        sabr_vol_hagan = None
        least_squares = None

    df = df.copy()
    df['moneyness'] = np.log(df['strike'] / forward)
    df['is_otm'] = np.where(df['strike'] <= forward, df['right'] == 'P', df['right'] == 'C')
    otm = df[df['is_otm'] & df['iv'].notna() & (df['iv'] > 0)]
    df['fit_iv'] = np.nan
    df['iv_residual_pts'] = np.nan
    df['is_edge'] = False
    df['edge_kind'] = ''

    sabr_params = None
    if use_sabr and len(otm) >= 5 and forward > 0 and T_years > 0:
        K = otm['strike'].values.astype(float)
        mv = otm['iv'].values.astype(float)
        # drop wing outliers before calibration (>3x MAD from median)
        med = float(np.median(mv))
        mad = float(np.median(np.abs(mv - med))) * 1.4826 or 1e-6
        keep = np.abs(mv - med) <= 3.0 * mad
        Kc, mvc = K[keep], mv[keep]
        if len(Kc) >= 5:
            fwd = float(forward); T = float(T_years)
            def resid(theta):
                a, rho, nu = theta
                a = max(a, 1e-4); nu = max(nu, 1e-3)
                if not (-0.99 <= rho <= 0.99):
                    return np.full_like(Kc, 1e3, dtype=float)
                model = np.array([sabr_vol_hagan(fwd, kk, T, a, 0.5, rho, nu) for kk in Kc])
                return model - mvc
            try:
                sol = least_squares(resid, x0=[np.median(mvc), -0.3, 0.5],
                                    bounds=([1e-4, -0.99, 1e-3], [0.5, 0.99, 5.0]),
                                    max_nfev=400)
                a0, rho0, nu0 = sol.x
                rms_fixed = float(np.sqrt(np.mean(resid([a0, rho0, nu0])**2)))
                sabr_params = {'alpha': float(a0), 'beta': 0.5, 'rho': float(rho0),
                               'nu': float(nu0), 'rmse': rms_fixed}
            except Exception:
                sabr_params = None

    if sabr_params is not None:
        # SABR model IV at every strike
        p = sabr_params
        for idx in df.index:
            df.at[idx, 'fit_iv'] = sabr_vol_hagan(
                forward, float(df.at[idx, 'strike']), T_years,
                p['alpha'], p['beta'], p['rho'], p['nu'])
        # rich/cheap on the calibration-liquid OTM body only (wing fail-safe):
        # compute residuals, then derive the MAD threshold exactly as the
        # quadratic path does.
        df.loc[df['is_otm'] & df['iv'].notna(), 'iv_residual_pts'] = (
            (df.loc[df['is_otm'] & df['iv'].notna(), 'iv'] -
             df.loc[df['is_otm'] & df['iv'].notna(), 'fit_iv']) * 100.0)
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
        return df, float(sabr_params['alpha']), float(sabr_params['rho']), sabr_params

    # Fallback: quadratic (existing behavior). Returns sabr_params=None.
    coeffs = np.polyfit(otm['moneyness'].values, otm['iv'].values, 2) if len(otm) >= 5 else (0.0, 0.0, 0.0)
    a, b, c = coeffs
    df['fit_iv'] = np.polyval(coeffs, df['moneyness'].values)
    df.loc[df['is_otm'] & df['iv'].notna(), 'iv_residual_pts'] = (
        (df.loc[df['is_otm'] & df['iv'].notna(), 'iv'] -
         df.loc[df['is_otm'] & df['iv'].notna(), 'fit_iv']) * 100.0)
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
    return df, float(a), float(b), None


# ---------- SVI smile fit (reuses the reusable svi_rp module) ----------
def fit_svi_smile(df: pd.DataFrame, forward: float, T_years: float,
                  oi_by=None, use_svi: bool = True,
                  ) -> Tuple[pd.DataFrame, float, float, Optional[dict]]:
    """Fit the SSVI reference smile (Gatheral-Jacquier) via the svi_rp module
    and flag cheap/rich edges. Keeps the SAME contract as fit_sabr_smile /
    fit_smile_and_flag_edges (fit_iv / iv_residual_pts / is_edge / edge_kind),
    so existing consumers are unaffected. smile_a/b are the quadratic
    coefficients (kept for back-compat); svi_params is None when SVI is off or
    fails (caller falls back to quadratic).
    """
    df = df.copy()
    df['moneyness'] = np.log(df['strike'] / forward)
    df['is_otm'] = np.where(df['strike'] <= forward, df['right'] == 'P', df['right'] == 'C')
    otm = df[df['is_otm'] & df['iv'].notna() & (df['iv'] > 0)]
    df['fit_iv'] = np.nan
    df['iv_residual_pts'] = np.nan
    df['is_edge'] = False
    df['edge_kind'] = ''

    svi_params = None
    if use_svi and len(otm) >= 5 and forward > 0 and T_years > 0:
        try:
            import svi_rp
            # Build the OTM chain_iv + oi maps the module expects.
            chain_iv = {}
            oi_map = {}
            for _, r in otm.iterrows():
                k = float(r['strike'])
                rt = str(r['right']).strip().upper()[:1]
                chain_iv[(k, rt)] = float(r['iv'])
                oi_map[(k, rt)] = int(r.get('oi', 0) or 0)
            ref = svi_rp.calibrate_ssvi(chain_iv, float(forward), float(T_years),
                                        oi_by=oi_map)
            # reference IV at every strike
            for idx in df.index:
                k = float(df.at[idx, 'strike'])
                df.at[idx, 'fit_iv'] = ref.sigma_ref(k)
            df.loc[df['is_otm'] & df['iv'].notna(), 'iv_residual_pts'] = (
                (df.loc[df['is_otm'] & df['iv'].notna(), 'iv'] -
                 df.loc[df['is_otm'] & df['iv'].notna(), 'fit_iv']) * 100.0)
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
            svi_params = {
                'theta_t': ref.theta_t, 'sigma_atm': ref.sigma_atm,
                'psi_t': ref.psi_t, 'p_t': ref.p_t,
                'phi': ref.phi, 'rho': ref.rho,
                'sigma_swap': ref.sigma_swap, 'K_var': ref.K_var,
                'butterfly_clamped': ref.butterfly_clamped,
            }
            return df, float(ref.sigma_atm), float(ref.rho), svi_params
        except Exception:
            svi_params = None

    # Fallback: quadratic (existing behavior). Returns svi_params=None.
    coeffs = np.polyfit(otm['moneyness'].values, otm['iv'].values, 2) if len(otm) >= 5 else (0.0, 0.0, 0.0)
    a, b, c = coeffs
    df['fit_iv'] = np.polyval(coeffs, df['moneyness'].values)
    df.loc[df['is_otm'] & df['iv'].notna(), 'iv_residual_pts'] = (
        (df.loc[df['is_otm'] & df['iv'].notna(), 'iv'] -
         df.loc[df['is_otm'] & df['iv'].notna(), 'fit_iv']) * 100.0)
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
    return df, float(a), float(b), None


# ---------- Vanna positioning read ----------
def compute_vanna_positioning(dealer_result: DealerPositioningResult, spot: float) -> dict:
    """Net dealer vanna exposure by strike, taken DIRECTLY from the shared
    dealer engine's DealerPositioningResult -- the SAME object the dealer
    4-panel chart plots (compute_dealer_positioning, max_days=150 window,
    accumulated book when available). The scanner never re-derives vanna
    from its own chain; it consumes the engine's vanna_shares_by_strike
    series verbatim, so the two charts cannot disagree.
    """
    strikes = np.asarray(dealer_result.strike_grid, dtype=float)
    values = np.asarray(dealer_result.vanna_shares_by_strike, dtype=float)
    net_vanna = float(values.sum()) if len(values) else 0.0
    call_vanna = float(dealer_result.vanna_call_shares or 0.0)
    put_vanna = float(dealer_result.vanna_put_shares or 0.0)

    # Flip strike: adjacent-strike sign change in the per-strike net vanna
    # profile, nearest to spot (same "nearest crossing to the reference point"
    # logic dealer_positioning.py uses for its gamma flip level).
    flip_strike = None
    if len(strikes) > 1:
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
        'has_vanna_data': bool(dealer_result.has_vanna_data),
    }


# ---------- Main scan ----------
_DEALER_UNAVAILABLE = object()  # sentinel: engine already failed, do not retry


def scan_chain(ticker: str, expiration: str, target_years: float, td,
               dealer_result: Optional[DealerPositioningResult] = None) -> ScanResult:
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
    # Smile fit: SVI (SSVI, Gatheral-Jacquier) is the default (Jason 2026-08-12:
    # "make all things smile use SVI"). Falls back to quadratic automatically if
    # the SVI calibration can't run on a thin/illiquid chain.
    df, smile_a, smile_b, _svi = fit_svi_smile(df, forward, actual_T, use_svi=True)

    # ---- Dealer sign engine (shared with dealer_positioning) ----
    # Resolve every (strike, right) sign through the SAME machinery the dealer
    # 4-panel chart uses (V5 Direction bias + OTM replicating-set gate +
    # per-expiry sabr_deviation). No independent call/put heuristic here.
    chain_iv: Dict[Tuple[float, str], float] = {}
    for _, row in df.iterrows():
        iv_val = row.get('iv')
        try:
            iv_f = float(iv_val)
        except (TypeError, ValueError):
            continue
        if math.isnan(iv_f) or iv_f <= 0:
            continue
        chain_iv[(float(row['strike']), str(row['right']).strip().upper()[:1])] = iv_f
    try:
        expiry_sign_map = compute_expiry_sign_map(
            ticker, chain_iv, spot, forward, actual_T, expiration)
    except Exception as e:
        print(f"  [warn] dealer sign map unavailable ({e}); vanna signs default to 0")
        expiry_sign_map = {}
    df['dealer_sign'] = df.apply(
        lambda r: expiry_sign_map.get(
            (float(r['strike']), str(r['right']).strip().upper()[:1]), 0.0),
        axis=1,
    )

    # ---- Dealer vanna: the SAME solver the dealer 4-panel chart uses ----
    # Jason's requirement (2026-08-10): the chain scanner's vanna MUST be the
    # dealer engine's vanna. No second computation: call compute_dealer_positioning
    # with the same window the 4-panel chart uses (max_days=150 ==
    # run_dealer_positioning's greek_days_window default) and consume its
    # vanna_shares_by_strike series verbatim. When a caller already holds the
    # result (scan_all_expiries computes it once), pass it in instead of
    # refetching.
    if dealer_result is _DEALER_UNAVAILABLE:
        dealer_result = None
    if dealer_result is None:
        try:
            dealer_result = compute_dealer_positioning(ticker, target_years, max_days=150)
        except Exception as e:
            print(f"  [warn] dealer engine unavailable ({e}); vanna section empty")
            dealer_result = None
    if dealer_result is not None:
        vanna_info = compute_vanna_positioning(dealer_result, spot)
    else:
        vanna_info = {
            'net_vanna_shares': 0.0, 'call_vanna_shares': 0.0, 'put_vanna_shares': 0.0,
            'vanna_flip_strike': None, 'top_vanna_strikes': [], 'has_vanna_data': False,
        }

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
        dealer_result=dealer_result,
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
                         "no strong directional vanna-hedging bias either way in the dealer book.")
        if flip is not None:
            side = "above" if flip > spot else "below"
            lines.append(f"  Net vanna flips sign near strike {flip:.2f} ({side} spot ${spot:.2f}) -- "
                         "that's the level where the vanna-hedging flow direction itself would reverse.")
        top = vanna_info['top_vanna_strikes'][:3]
        if top:
            desc = ", ".join(f"{k:.2f} ({v:+,.0f})" for k, v in top)
            lines.append(f"  Largest vanna concentrations: {desc}")
    else:
        lines.append("Dealer vanna/2nd-order data wasn't available (the shared dealer engine "
                     "returned no usable vanna) -- positioning read above is skipped rather than guessed.")

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
    # Rendered from the shared dealer engine result (dealer 4-panel series),
    # NOT recomputed from this expiry's chain -- the two charts must agree.
    ax2.set_facecolor('#161b22')
    dr = result.dealer_result
    if dr is None or dr.strike_grid is None or dr.vanna_shares_by_strike is None:
        raise ValueError("plot_scanner_charts requires the shared dealer engine "
                         "result (scan_chain attaches it via compute_dealer_positioning) -- "
                         "refusing to recompute vanna from the single-expiry chain")
    strikes = np.asarray(dr.strike_grid, dtype=float)
    values = np.asarray(dr.vanna_shares_by_strike, dtype=float)
    if len(strikes):
        colors = [ACCENT_BLUE if v >= 0 else ACCENT_RED for v in values]
        diffs = np.diff(strikes)
        bar_width = (float(np.median(diffs)) if len(diffs) else 1.0) * 0.7
        ax2.bar(strikes, values, width=bar_width, color=colors, alpha=0.9)
    ax2.axhline(0, color='#8b949e', linewidth=0.8, alpha=0.6)
    ax2.axvline(result.spot, color=ACCENT_CYAN, linestyle='--', linewidth=1.5, alpha=0.8)
    if result.vanna_flip_strike is not None:
        ax2.axvline(result.vanna_flip_strike, color=ACCENT_ORANGE, linestyle=':', linewidth=1.5, alpha=0.8)
    ax2.set_title(f'Net Dealer Vanna by Strike ({SIGN_MODEL_LABEL})', color=TEXT_COLOR, fontsize=13, fontweight='bold')
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
CHAIN_STRATEGIES_FILENAME = "chain_strategies.json"


def _chain_strategies_artifact(result: ScanResult) -> dict:
    """Build the chain_strategies v1 artifact dict for one ScanResult."""
    strategies = []
    for c in result.edge_candidates or []:
        strategies.append({
            "side": "sell" if c.get("edge_kind") == "rich" else "buy",
            "edge_kind": c.get("edge_kind"),
            "right": c.get("right"),
            "strike": c.get("strike"),
            "iv": c.get("iv"),
            "fit_iv": c.get("fit_iv"),
            "iv_residual_pts": c.get("iv_residual_pts"),
            "oi": c.get("oi"),
        })
    return {
        "version": 1,
        "ticker": result.ticker,
        "expiry": result.expiry,
        "run_created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "spot": result.spot,
        "forward": result.forward,
        "atm_iv_pct": result.atm_iv_pct,
        "regime": result.regime,
        "verdict": result.verdict,
        "strategies": strategies,
    }


def export_chain_strategies(result: ScanResult, output_dir: Optional[str] = None) -> str:
    """Write the run's chain-strategy artifact consumed by the Tools module's
    options-strategy tool.

    The scanner's `edge_candidates` are exactly the "chain strategies": strikes
    whose live IV sits meaningfully off the fitted smile (with real OI), mapped
    to a tradable side (rich -> sell candidate, cheap -> buy candidate). This
    serializes them (plus the run header the tool needs) into a stable
    `chain_strategies.json` in the Vol_Suite output dir so a later launch of the
    options-strategy tool can read the cached artifact without re-hitting
    ThetaData. Returns the written path.
    """
    import json as _json
    out_dir = output_dir or os.getenv("VS_OUTPUT_DIR", os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(out_dir, exist_ok=True)

    artifact = _chain_strategies_artifact(result)
    path = os.path.join(out_dir, CHAIN_STRATEGIES_FILENAME)
    with open(path, "w", encoding="utf-8") as handle:
        _json.dump(artifact, handle, indent=2)
        handle.write("\n")
    return path


def export_chain_strategies_per_expiry(result: ScanResult,
                                       output_dir: Optional[str] = None) -> str:
    """Write chain_strategies_<expiry>.json (v1 schema) for one expiry.

    This is the per-expiry variant used by scan_all_expiries, so a later
    tool/reader can load each expiry's full edge set independently.
    """
    import json as _json
    out_dir = output_dir or os.getenv("VS_OUTPUT_DIR", os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(out_dir, exist_ok=True)
    artifact = _chain_strategies_artifact(result)
    path = os.path.join(out_dir, f"chain_strategies_{result.expiry}.json")
    with open(path, "w", encoding="utf-8") as handle:
        _json.dump(artifact, handle, indent=2)
        handle.write("\n")
    return path


CHAIN_STRATEGIES_INDEX_FILENAME = "chain_strategies_index.json"


def export_chain_index(results: List[ScanResult],
                       skipped: List[dict],
                       output_dir: Optional[str] = None) -> str:
    """Write the every-expiry summary index (v2, additive).

    Rows: one per expiry with T_years, atm_iv_pct, regime, verdict, sell/rich and
    buy/cheap edge counts, n_strikes, and (when SABR params are stashed on the
    result) the calibrated params. `top_expiries` ranks expiries by edge strength
    (total |residual| × edge count), and `aggregate_verdict` is the mode regime.
    The v1 consumers keep reading chain_strategies.json (the primary expiry), so
    this file is purely additive.
    """
    import json as _json
    out_dir = output_dir or os.getenv("VS_OUTPUT_DIR", os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(out_dir, exist_ok=True)

    rows = []
    for r in results:
        edges = r.edge_candidates or []
        rich = sum(1 for e in edges if e.get("edge_kind") == "rich")
        cheap = sum(1 for e in edges if e.get("edge_kind") == "cheap")
        strength = sum(abs(e.get("iv_residual_pts") or 0.0) for e in edges)
        rows.append({
            "expiry": r.expiry,
            "T_years": r.T_years,
            "spot": r.spot,
            "forward": r.forward,
            "atm_iv_pct": r.atm_iv_pct,
            "regime": r.regime,
            "verdict": r.verdict,
            "rich_count": rich,
            "cheap_count": cheap,
            "edge_count": len(edges),
            "edge_strength": round(strength, 2),
            "n_strikes": int(len(r.df)) if r.df is not None else 0,
            "sabr_params": getattr(r, "sabr_params", None),
        })

    regimes = [row["regime"] for row in rows if row["regime"] in ("RICH", "CHEAP", "FAIR")]
    aggregate_verdict = max(set(regimes), key=regimes.count) if regimes else "UNKNOWN"
    top = sorted(rows, key=lambda x: (x["edge_count"], x["edge_strength"]), reverse=True)

    index = {
        "version": 2,
        "ticker": results[0].ticker if results else (skipped[0].get("ticker") if skipped else None),
        "scan_created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "aggregate_verdict": aggregate_verdict,
        "n_expiries_scanned": len(results) + len(skipped),
        "n_expiries_ok": len(results),
        "n_expiries_skipped": len(skipped),
        "top_expiries": [row["expiry"] for row in top],
        "expiries": rows,
        "skipped": skipped,
    }
    path = os.path.join(out_dir, CHAIN_STRATEGIES_INDEX_FILENAME)
    with open(path, "w", encoding="utf-8") as handle:
        _json.dump(index, handle, indent=2, default=str)
        handle.write("\n")
    return path


VOL_RESULT_FILENAME = "vol_result.json"


def export_vol_result(result: ScanResult, output_dir: Optional[str] = None) -> str:
    """Write the run's canonical `vol_result.json` artifact.

    This is what the Quant synthesis worker (`quant_synthesis._extract` for
    module="vol") reads. It must live in the same output dir as
    `chain_strategies.json` (the orchestrator/bridge vol run writes both there).
    Without it, "Ask Quant to interpret" on a vol run finds no recognized
    `vol_result.json` and fails with `missing_data / missing vol input`.

    Fields map 1:1 to the synthesizer's vol schema:
      - fair_vol -> realized vol (RV 30d), the natural "fair" reference
      - atm_iv   -> ATM implied vol (percent)
      - vrp      -> ATM IV minus RV 30d (variance risk premium)
      - signal   -> the scanner verdict
      - score    -> edge-detection count (0 when the scan found no edge)
    """
    import json as _json
    out_dir = output_dir or os.getenv("VS_OUTPUT_DIR", os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(out_dir, exist_ok=True)

    fair_vol = result.rv_30_pct
    atm_iv = result.atm_iv_pct
    vrp = None
    if fair_vol is not None and atm_iv is not None:
        vrp = round(atm_iv - fair_vol, 4)
    edges = result.edge_candidates or []
    edge_detected = str(result.verdict or "").upper().find("EDGE") >= 0

    artifact = {
        "suite": "vol",
        "schema_version": 1,
        "ticker": result.ticker,
        "expiry": result.expiry,
        "run_created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "spot": result.spot,
        "forward": result.forward,
        "atm_iv": atm_iv,
        "fair_vol": fair_vol,
        "vrp": vrp,
        "signal": result.verdict,
        "score": len(edges) if edge_detected else 0,
        "regime": result.regime,
        "edge_count": len(edges),
    }

    path = os.path.join(out_dir, VOL_RESULT_FILENAME)
    with open(path, "w", encoding="utf-8") as handle:
        _json.dump(artifact, handle, indent=2)
        handle.write("\n")
    return path


def run_chain_scanner(ticker: str, target_years: float = 0.25, expiration: Optional[str] = None,
                      output_dir: Optional[str] = None,
                      dealer_result: Optional[DealerPositioningResult] = None) -> tuple:
    """Programmatic, non-interactive runner (mirrors run_variance_swap_live /
    screen_ticker / run_dealer_positioning conventions) for volatility_suite.py.
    `expiration`, if given, pins this to the exact date the suite resolved
    interactively for the rest of the run; otherwise falls back to a plain
    nearest-expiry lookup (no prompting -- this is meant to be called from
    inside an already-orchestrated run).

    `dealer_result`: optional precomputed DealerPositioningResult from the
    suite's dealer-positioning step. When passed, the scanner consumes its
    vanna_shares_by_strike verbatim -- no refetch, guaranteed identical to the
    dealer 4-panel chart. When omitted (standalone run), scan_chain calls the
    same compute_dealer_positioning solver itself (max_days=150)."""
    out_dir = output_dir or os.getenv("VS_OUTPUT_DIR", os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(out_dir, exist_ok=True)
    files = []
    td = ThetaDataController()
    try:
        exp, actual_T = expiry_selector.resolve_expiration(td, ticker, expiration, target_years)
        result = scan_chain(ticker, exp, actual_T, td, dealer_result=dealer_result)
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
    try:
        files.append(export_chain_strategies(result, out_dir))
    except Exception as e:
        print(f"  Chain strategies export failed: {e}")
    try:
        files.append(export_vol_result(result, out_dir))
    except Exception as e:
        print(f"  Vol result export failed: {e}")
    interp = f"{result.verdict} -- {result.regime} vol regime.\n{result.insight}"
    return files, interp, result


MIN_LIQUID_STRIKES_FOR_EXPIRY = 8
MAX_EXPIRIES = None   # no cap: skip degenerate expiries, scan the rest


def scan_all_expiries(ticker: str, target_years: float = 0.25,
                      output_dir: Optional[str] = None,
                      max_expiries: Optional[int] = MAX_EXPIRIES,
                      min_liquid_strikes: int = MIN_LIQUID_STRIKES_FOR_EXPIRY,
                      use_sabr: bool = True) -> dict:
    """Scan EVERY expiry in a ticker's chain and present per-expiry plays.

    Jason's core requirement: the scanner must scan, use, and present plays on
    all options chains for every expiry, not just the one nearest to
    `target_years`. This resolves the expiry list via ThetaData, scans each
    expiry with SABR-or-quadratic fit, writes per-expiry
    `chain_strategies_<expiry>.json`, a `chain_strategies_index.json` summary,
    and keeps the v1 `chain_strategies.json` for the primary (nearest) expiry so
    existing Tools / synthesis consumers keep working.

    Returns a dict {result: ScanResult, artifacts: [paths], skipped: [str]}.
    """
    out_dir = output_dir or os.getenv("VS_OUTPUT_DIR", os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(out_dir, exist_ok=True)

    td = ThetaDataController()
    results: List[ScanResult] = []
    skipped: List[dict] = []
    files: List[str] = []
    try:
        expiries = td.list_expirations(ticker)
        if not expiries:
            return {"primary": None, "results": [], "artifacts": files, "skipped": skipped}
        # Primary expiry: the one nearest to target_years, for v1 back-compat.
        primary_exp = expiry_selector.resolve_expiration(td, ticker, None, target_years)[0]

        # Dealer vanna comes from the SAME solver the dealer 4-panel chart uses,
        # computed ONCE for the ticker (150d window) and shared across every
        # expiry scan -- the surface is ticker-level, and per-expiry refetching
        # would fan out the whole 150d book N times (proxy saturation).
        dealer_result: Optional[DealerPositioningResult] = None
        try:
            dealer_result = compute_dealer_positioning(ticker, target_years, max_days=150)
        except Exception as exc:
            print(f"  [chain] dealer engine unavailable ({exc}); vanna section skipped per-expiry")
            dealer_result = _DEALER_UNAVAILABLE  # type: ignore[assignment]

        if max_expiries:
            # Prefer nearest-to-target first so the primary is scanned regardless
            # of cap, then spread over the calendar to capture weekly crush.
            expiries = sorted(expiries, key=lambda e: abs(
                (datetime.strptime(e, "%Y%m%d") - datetime.strptime(primary_exp, "%Y%m%d")).days))
            expiries = expiries[:max_expiries]

        for exp in sorted(expiries):
            try:
                result = scan_chain(ticker, exp, target_years, td, dealer_result=dealer_result)
            except Exception as exc:
                skipped.append({"expiry": exp, "reason": f"{type(exc).__name__}: {exc}"})
                continue
            # Guard against degenerate / illiquid expiries with too few usable
            # OTM strikes to trust a smile fit.
            n_otm = len(result.df[result.df['is_otm']]) if result.df is not None else 0
            if n_otm < min_liquid_strikes:
                skipped.append({"expiry": exp,
                                "reason": f"only {n_otm} usable OTM strikes (< {min_liquid_strikes})"})
                continue
            results.append(result)
            try:
                files.append(export_chain_strategies_per_expiry(result, out_dir))
            except Exception as exc:
                print(f"  [chain] {exp} per-expiry export failed: {exc}")

        # Primary back-compat artifact + summary index.
        if results:
            primary = results[0] if results[0].expiry == primary_exp else next(
                (r for r in results if r.expiry == primary_exp), results[0])
            try:
                files.append(export_chain_strategies(primary, out_dir))
                files.append(export_vol_result(primary, out_dir))
            except Exception as exc:
                print(f"  [chain] primary export failed: {exc}")
        try:
            files.append(export_chain_index(results, skipped, out_dir))
        except Exception as exc:
            print(f"  [chain] index export failed: {exc}")
    finally:
        td.close()

    return {"primary": results[0] if results else None,
            "results": results, "artifacts": files, "skipped": skipped}


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
