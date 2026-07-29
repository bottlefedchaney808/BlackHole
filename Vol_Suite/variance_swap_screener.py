#!/usr/bin/env python3
# variance_swap_screener.py
# Multi-ticker volatility screener ranking candidates by short-vol attractiveness.

import math
import warnings
import os
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Tuple, List, Dict

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from thetadata_client import ThetaDataController, strike_to_theta, strike_from_theta
from correlation_engine import fetch_price_history
import expiry_selector

warnings.filterwarnings("ignore", category=FutureWarning, module="pandas")

# DEFAULT_A (365, calendar days) resolves a target-years input to an expiry date,
# shared via expiry_selector so this, variance_swap_live.py, and
# dealer_positioning.py agree on one date. TRADING_DAYS (252) is the SEPARATE
# annualization factor for realized vol from trading-day EOD returns -- using 365
# there overstated RV ~20% and distorted VRP (same fix as variance_swap_live.py).
DEFAULT_A = expiry_selector.DEFAULT_A
TRADING_DAYS = 252
RISK_FREE_RATE = 0.05  # fallback only; live rate pulled from the yield curve


@dataclass
class ChainData:
    expiry: str
    strikes: np.ndarray
    call_mid: np.ndarray
    put_mid: np.ndarray
    r: float
    q: float
    call_iv: np.ndarray
    put_iv: np.ndarray

@dataclass
class ScreenResult:
    ticker: str
    expiry: str
    T_years: float
    S0: float
    F: float
    fair_vol_pct: float
    atm_iv_pct: float
    convexity_pct: float
    vrp_pct: float
    rv_30_pct: float
    rv_60_pct: float
    rv_90_pct: float
    rv_match_pct: float
    skew_bias: float
    tail_mass: float
    num_strikes: int
    score: float
    signal: str
    # "ok" or "insufficient_price_history". When not "ok", vrp_pct/rv_*_pct
    # are NaN (the underlying realized-vol read failed, not "0.0" of real
    # realized vol) and `signal` is forced to "INSUFFICIENT DATA" regardless
    # of the numeric score -- a missing input must not be able to produce a
    # confident BUY/SELL read. See FIX_PLAN_20260725.md, issue 3.
    data_quality: str = "ok"


def _to_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return np.nan

def compute_forward_price(S0: float, r: float, q: float, T: float) -> float:
    return S0 * math.exp((r - q) * T)

def _deltaK_half_widths(K: np.ndarray) -> np.ndarray:
    n = len(K)
    dK = np.empty(n)
    dK[0] = (K[1] - K[0]) / 2.0
    dK[-1] = (K[-1] - K[-2]) / 2.0
    if n > 2:
        dK[1:-1] = (K[2:] - K[:-2]) / 2.0
    return dK

def compute_realized_vol(prices: np.ndarray, lookback_days: int = 60) -> float:
    prices = np.asarray(prices).flatten()
    if len(prices) < 3:
        return np.nan
    if len(prices) < lookback_days:
        lookback_days = len(prices)
    prices = prices[-lookback_days:]
    log_returns = np.diff(np.log(prices))
    if len(log_returns) < 2:
        return np.nan
    return float(np.std(log_returns, ddof=1) * math.sqrt(TRADING_DAYS))

def find_nearest_expiry_thetadata(td, ticker: str, target_years: float) -> Tuple[str, float]:
    """Thin wrapper -- lookup now lives in expiry_selector.py, shared with
    variance_swap_live.py and dealer_positioning.py."""
    return expiry_selector.nearest_expiry(td, ticker, target_years)

def fetch_chain_thetadata(td, ticker: str, expiration: str, r: float, q: float) -> ChainData:
    strikes = td.list_strikes(ticker, expiration)
    if not strikes:
        raise ValueError(f"No strikes found for {ticker} {expiration}")
    greeks = td.option_bulk_greeks(ticker, expiration)
    call_map = {}
    put_map = {}
    call_iv_map = {}
    put_iv_map = {}
    for row in greeks:
        k = strike_from_theta(int(float(row['strike'])))
        right = row.get('right', '')
        bid = _to_float(row.get('bid'))
        ask = _to_float(row.get('ask'))
        iv = _to_float(row.get('implied_vol'))
        mid = (bid + ask) / 2.0 if (not math.isnan(bid) and not math.isnan(ask) and bid > 0 and ask > 0) else 0.0
        if mid == 0.0:
            last = _to_float(row.get('last'))
            mid = last if not math.isnan(last) else 0.0
        if right == 'C':
            call_map[k] = mid
            call_iv_map[k] = iv
        elif right == 'P':
            put_map[k] = mid
            put_iv_map[k] = iv
    all_strikes = sorted(set(call_map.keys()) | set(put_map.keys()))
    K_grid = np.array(all_strikes, dtype=float)
    call_mid = np.array([call_map.get(k, 0.0) for k in K_grid])
    put_mid = np.array([put_map.get(k, 0.0) for k in K_grid])
    call_iv = np.array([call_iv_map.get(k, np.nan) for k in K_grid])
    put_iv = np.array([put_iv_map.get(k, np.nan) for k in K_grid])
    return ChainData(expiry=expiration, strikes=K_grid, call_mid=call_mid, put_mid=put_mid,
                     r=r, q=q, call_iv=call_iv, put_iv=put_iv)

def compute_fair_variance_strike(chain: ChainData, S0: float, T_years: float) -> dict:
    F = compute_forward_price(S0, chain.r, chain.q, T_years)
    OTM = np.where(chain.strikes <= F, chain.put_mid, chain.call_mid)
    mask = np.isfinite(OTM) & np.isfinite(chain.strikes)
    K = chain.strikes[mask]
    OTM2 = OTM[mask]
    if len(K) < 5:
        raise ValueError(f"Too few strikes ({len(K)}).")
    dK = _deltaK_half_widths(K)
    weights = dK / (K ** 2)
    weighted_sum = np.sum(weights * OTM2)
    # Demeterfi-Derman-Kamal-Zou (1999) discrete fair-variance strike:
    #   sigma^2 = (2/T) e^{rT} Sum_i (dK_i/K_i^2) Q(K_i)  -  (1/T)(F/K0 - 1)^2
    # The second term is the discretization correction for using the strike K0
    # just below the forward as the boundary between the put and call legs; it
    # was omitted before, which slightly overstated the fair variance. K0 = the
    # largest strike <= F.
    replication_term = (2.0 * math.exp(chain.r * T_years) / T_years) * weighted_sum
    below = K[K <= F]
    K0 = float(below.max()) if below.size else float(K.min())
    boundary_correction = (1.0 / T_years) * ((F / K0 - 1.0) ** 2) if K0 > 0 else 0.0
    fair_variance = replication_term - boundary_correction
    fair_vol = math.sqrt(fair_variance) if fair_variance > 0 else 0.0
    contributions = weights * OTM2
    idx = np.argmin(np.abs(chain.strikes - F))
    atm_strike = chain.strikes[idx]
    atm_iv = chain.call_iv[idx] if atm_strike >= F else chain.put_iv[idx]
    if math.isnan(atm_iv):
        atm_iv = chain.put_iv[idx] if atm_strike >= F else chain.call_iv[idx]
    # If both call and put ATM IV are NaN, leave atm_iv as NaN (do not fall back
    # to 0.0, which fabricates a real-looking value from missing data). The result
    # will naturally have NaN for atm_iv and convexity_premium, which will be
    # handled as insufficient data downstream.
    convexity_premium = fair_vol - atm_iv
    put_contrib = float(np.sum(contributions[K <= F]))
    call_contrib = float(np.sum(contributions[K > F]))
    total_contrib = put_contrib + call_contrib
    skew_bias = put_contrib / total_contrib if total_contrib > 0 else 0.5
    tail_mask = (K < 0.5 * F) | (K > 2.0 * F)
    tail_mass = float(np.sum(contributions[tail_mask])) / total_contrib if total_contrib > 0 else 0.0
    return {
        "expiry": chain.expiry, "S0": S0, "F": F, "T_years": T_years,
        "fair_variance_annualized": fair_variance,
        "fair_variance_swap_strike_vol": fair_vol,
        "fair_variance_swap_strike_vol_pct": 100.0 * fair_vol,
        "atm_strike": float(atm_strike),
        "atm_implied_vol": float(atm_iv),
        "atm_implied_vol_pct": 100.0 * atm_iv,
        "convexity_premium_vol_pct": 100.0 * convexity_premium,
        "num_strikes_used": len(K),
        "K_min": float(K.min()), "K_max": float(K.max()),
        "skew_bias": skew_bias,
        "tail_mass": tail_mass,
        "strike_table": {"strikes": K, "deltaK": dK, "weights": weights, "otm_prices": OTM2, "contributions": contributions}
    }

def screen_ticker(ticker: str, target_years: float, expiration: str = None) -> ScreenResult:
    """Screen a single ticker and return a ScreenResult.

    `expiration`: if given (a "YYYYMMDD" string), used directly instead of
    re-deriving a nearest-expiry from target_years -- lets volatility_suite.py
    force this leg onto the same expiry it resolved interactively for the
    rest of the run.

    NOTE: an earlier version of this function accepted a `vrp_history` param
    and computed a `vrp_zscore` field from it -- but nothing in the codebase
    ever populated `vrp_history` (no caller passed anything but the default
    None), so that branch always fell to the else case (`vrp_zscore = 0.0`)
    and the field was never surfaced in any report or CSV export. Removed as
    dead code rather than left half-wired; a real VRP z-score would need a
    persisted rolling history of past screener runs, which doesn't exist yet.
    """
    try:
        td = ThetaDataController()
        dividend_yield = td.fetch_dividend_yield(ticker)
        expiration, actual_T = expiry_selector.resolve_expiration(td, ticker, expiration, target_years)
        r_live = td.fetch_risk_free_rate(actual_T)
        r_use = r_live if r_live is not None else RISK_FREE_RATE
        chain = fetch_chain_thetadata(td, ticker, expiration, r_use, dividend_yield)
        td.close()
        td2 = ThetaDataController()
        S0 = td2.fetch_spot_price(ticker)
        td2.close()
        if S0 <= 0:
            return None
        F = compute_forward_price(S0, r_use, dividend_yield, actual_T)
        result = compute_fair_variance_strike(chain, S0, actual_T)
        fair_vol_pct = result["fair_variance_swap_strike_vol_pct"]
        atm_iv_pct = result["atm_implied_vol_pct"]
        convexity_pct = result["convexity_premium_vol_pct"]
        try:
            hist_df = fetch_price_history([ticker], period="2y")
            prices = hist_df[ticker].values.flatten()
        except Exception:
            prices = np.array([])
        if len(prices) >= 5:
            rv_30 = compute_realized_vol(prices, min(30, len(prices)))
            rv_60 = compute_realized_vol(prices, min(60, len(prices)))
            rv_90 = compute_realized_vol(prices, min(90, len(prices)))
            match_lookback = int(actual_T * TRADING_DAYS)  # trading-day price points ~ option life
            rv_match = compute_realized_vol(prices, min(match_lookback, len(prices)))
        else:
            rv_30 = rv_60 = rv_90 = rv_match = np.nan

        # A ticker with no usable price history has an UNKNOWN VRP, not a
        # VRP of exactly zero -- those are different things and the old
        # `else 0.0` conflated them, letting a data gap masquerade as a
        # measured "fair vol == realized vol" reading. `insufficient_data`
        # tracks that gap; `vrp_for_score` is a same-as-before 0.0 stand-in
        # used ONLY inside the score math below (so score/ranking behavior
        # is unchanged), while the ticker's actual `signal` gets overridden
        # to INSUFFICIENT DATA further down regardless of what the score
        # comes out to -- a missing input can no longer produce a confident
        # STRONG BUY/SELL. The outward-facing `vrp_pct` field is NaN, not
        # 0.0, so it can't silently pass as a real reading downstream either.
        insufficient_data = math.isnan(rv_match)
        vrp_for_score = 0.0 if insufficient_data else (fair_vol_pct - (rv_match * 100))
        vrp_pct = float('nan') if insufficient_data else vrp_for_score
        skew_bias = result["skew_bias"]
        tail_mass = result["tail_mass"]

        # Same pattern as realized vol: if ATM IV is missing, it's not "0.0 convexity"
        # but rather "unknown convexity". Use a stand-in for scoring while keeping
        # the actual field as NaN so it can't masquerade as a real reading.
        insufficient_atm_iv = math.isnan(atm_iv_pct)
        insufficient_data = insufficient_data or insufficient_atm_iv
        convexity_for_score = 0.0 if insufficient_atm_iv else convexity_pct

        # Composite score (0-100, higher = better for short vol)
        score = 0.0
        score += 30.0 * min(max((vrp_for_score - 2.0) / 8.0, 0.0), 1.0)  # VRP magnitude
        score += 20.0 * min(max((5.0 - convexity_for_score) / 5.0, 0.0), 1.0)  # Low convexity = good
        score += 15.0 * min(max((0.65 - skew_bias) / 0.35, 0.0), 1.0)  # Not too put-heavy
        score += 15.0 * min(max((0.20 - tail_mass) / 0.20, 0.0), 1.0)  # Low tail mass = good
        score += 10.0 * (1.0 if not math.isnan(rv_30) and not math.isnan(rv_60) and not math.isnan(rv_90) else 0.0)
        score = min(max(score, 0.0), 100.0)

        if insufficient_data:
            signal = "INSUFFICIENT DATA"
        elif score >= 80:
            signal = "STRONG SELL"
        elif score >= 60:
            signal = "MODERATE SELL"
        elif score >= 40:
            signal = "NEUTRAL"
        elif score >= 20:
            signal = "MODERATE BUY"
        else:
            signal = "STRONG BUY"

        return ScreenResult(
            ticker=ticker, expiry=expiration, T_years=actual_T,
            S0=S0, F=F, fair_vol_pct=fair_vol_pct, atm_iv_pct=atm_iv_pct,
            convexity_pct=convexity_pct, vrp_pct=vrp_pct,
            rv_30_pct=rv_30*100 if not math.isnan(rv_30) else float('nan'),
            rv_60_pct=rv_60*100 if not math.isnan(rv_60) else float('nan'),
            rv_90_pct=rv_90*100 if not math.isnan(rv_90) else float('nan'),
            rv_match_pct=rv_match*100 if not math.isnan(rv_match) else float('nan'),
            skew_bias=skew_bias, tail_mass=tail_mass,
            num_strikes=result["num_strikes_used"], score=score, signal=signal,
            data_quality="insufficient_price_history" if insufficient_data else "ok",
        )
    except Exception as e:
        print(f"  [SKIP] {ticker}: {e}")
        return None


def print_screener_table(results: List[ScreenResult]):
    """Print a colour-coded ranking table."""
    results = [r for r in results if r is not None]
    results.sort(key=lambda r: r.score, reverse=True)
    
    print("\n" + "=" * 130)
    print(f"{'RANK':<5} {'MKR':<7} {'TICKER':<8} {'SIGNAL':<15} {'SCORE':<7} {'FAIR VOL':<10} {'ATM IV':<10} {'CONVEX':<10} {'VRP':<10} {'RV(95d)':<10} {'SKEW':<8} {'TAIL':<8} {'STRK':<6}")
    print("=" * 130)
    # Marker derived from r.signal itself, not a second re-derivation of the
    # score thresholds -- two independent copies of the same threshold logic
    # is exactly how this table could show "[BUY]" on a row whose Signal
    # column says INSUFFICIENT DATA (score-only re-derivation has no way to
    # know the ticker was flagged). One source of truth: the signal field.
    _SIGNAL_MARKERS = {
        "STRONG SELL": "[SELL]",
        "MODERATE SELL": "[sell]",
        "NEUTRAL": "[hold]",
        "MODERATE BUY": "[ buy]",
        "STRONG BUY": "[BUY]",
        "INSUFFICIENT DATA": "[ ?? ]",
    }
    for i, r in enumerate(results, 1):
        signal_marker = _SIGNAL_MARKERS.get(r.signal, "[ ?? ]")
        flag = "  (!) missing RV history" if r.data_quality != "ok" else ""
        print(f"{i:<5} {signal_marker:<7} {r.ticker:<8} {r.signal:<15} {r.score:<7.1f} {r.fair_vol_pct:<10.2f} {r.atm_iv_pct:<10.2f} {r.convexity_pct:<10.2f} {r.vrp_pct:<10.2f} {r.rv_match_pct:<10.2f} {r.skew_bias:<8.3f} {r.tail_mass:<8.3f} {r.num_strikes:<6}{flag}")
    print("=" * 130)

def main():
    print("=" * 60)
    print("VOLATILITY SCREENER -- Multi-Ticker Ranking")
    print("=" * 60)
    
    # Default ticker list
    default_tickers = "GME,SPY,AAPL,TSLA,QQQ,AMZN,MSFT,NVDA,GOOGL,META"
    ticker_input = input(f"Enter tickers (comma-separated, default: {default_tickers}): ").strip()
    if ticker_input:
        tickers = [t.strip().upper() for t in ticker_input.split(",")]
    else:
        tickers = [t.strip() for t in default_tickers.split(",")]
    
    ty_input = input("Target time-to-expiry in years (e.g., 0.25): ").strip()
    target_years = float(ty_input) if ty_input else 0.25
    
    print(f"\nScreening {len(tickers)} tickers (T={target_years} yr)...")
    print("-" * 60)
    
    results = []
    for ticker in tickers:
        print(f"  Processing {ticker}...")
        result = screen_ticker(ticker, target_years)
        if result:
            results.append(result)
            print(f"    Score: {result.score:.1f} | Signal: {result.signal} | Fair Vol: {result.fair_vol_pct:.1f}% | VRP: {result.vrp_pct:+.1f}%")
    
    if results:
        print_screener_table(results)
        
        # Show top picks -- excluding anything flagged INSUFFICIENT DATA, same
        # reasoning as run_variance_screener: a missing RV read must not be
        # able to land a ticker on a recommended-candidates list.
        flagged = [r for r in results if r.data_quality != "ok"]
        ok_results = [r for r in results if r.data_quality == "ok"]
        if flagged:
            print(f"\n(!) {len(flagged)} ticker(s) flagged INSUFFICIENT DATA, excluded "
                  f"from candidates below: {', '.join(r.ticker for r in flagged)}")
        top_sell = [r for r in ok_results if r.score >= 60]
        top_buy = [r for r in ok_results if r.score <= 20]

        if top_sell:
            print(f"\n--- TOP SHORT VARIANCE CANDIDATES (score >= 60) ---")
            for r in top_sell:
                print(f"  {r.ticker}: Score={r.score:.1f}, VRP={r.vrp_pct:+.1f}pp, Conv={r.convexity_pct:.1f}pp, Skew={r.skew_bias:.2f}, Tail={r.tail_mass:.1%}")
            print(f"  >> Short variance recommended on these tickers")
        
        if top_buy:
            print(f"\n--- TOP LONG VARIANCE CANDIDATES (score <= 20) ---")
            for r in top_buy:
                print(f"  {r.ticker}: Score={r.score:.1f}, VRP={r.vrp_pct:+.1f}pp, Conv={r.convexity_pct:.1f}pp")
            print(f"  >> Long variance / tail hedge recommended on these tickers")
        
        # Export option
        choice = input("\nExport screener results to CSV? (y/n): ").strip().lower()
        if choice == 'y':
            out_dir = os.getenv("VS_OUTPUT_DIR", os.path.dirname(os.path.abspath(__file__)))
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            data = []
            for r in results:
                data.append({
                    "Ticker": r.ticker, "Signal": r.signal, "Score": round(r.score, 1),
                    "FairVol%": round(r.fair_vol_pct, 2), "ATM_IV%": round(r.atm_iv_pct, 2),
                    "Convexity%": round(r.convexity_pct, 2), "VRP%": round(r.vrp_pct, 2),
                    "RV_30d%": round(r.rv_30_pct, 2), "RV_60d%": round(r.rv_60_pct, 2),
                    "RV_90d%": round(r.rv_90_pct, 2), "RV_match%": round(r.rv_match_pct, 2),
                    "Skew_Bias": round(r.skew_bias, 3), "Tail_Mass": round(r.tail_mass, 3),
                    "Strikes": r.num_strikes
                })
            df = pd.DataFrame(data)
            filename = os.path.join(out_dir, f"screener_{timestamp}.csv")
            df.to_csv(filename, index=False)
            print(f"  Saved to: {filename}")
    else:
        print("\nNo valid results. Check ticker symbols or API connection.")
    
    print("\nDone.")


if __name__ == "__main__":
    main()


def run_variance_screener(tickers: List[str] = None, target_years: float = 0.25,
                          output_dir: str = None) -> list:
    """Programmatic runner for the variance screener. Returns list of generated files."""
    out_dir = output_dir or os.getenv("VS_OUTPUT_DIR", os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(out_dir, exist_ok=True)
    if tickers is None:
        default_list = "GME,SPY,AAPL,TSLA,QQQ,AMZN,MSFT,NVDA,GOOGL,META"
        tickers = [t.strip() for t in default_list.split(',')]
    requested = list(dict.fromkeys(tickers))
    results = []
    skipped: List[str] = []
    for t in requested:
        r = screen_ticker(t, target_years)
        if r:
            results.append(r)
        else:
            skipped.append(t)
    print_screener_table(results)
    if skipped:
        print(f"  [SKIPPED] {len(skipped)}/{len(requested)} requested tickers returned no "
              f"result (see [SKIP] lines above for reasons): {', '.join(skipped)}")
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    data = []
    for r in results:
        data.append({
            "Ticker": r.ticker, "Signal": r.signal, "Score": round(r.score, 1),
            "DataQuality": r.data_quality,
            "FairVol%": round(r.fair_vol_pct, 2), "ATM_IV%": round(r.atm_iv_pct, 2),
            "Convexity%": round(r.convexity_pct, 2), "VRP%": round(r.vrp_pct, 2),
            "RV_30d%": round(r.rv_30_pct, 2), "RV_60d%": round(r.rv_60_pct, 2),
            "RV_90d%": round(r.rv_90_pct, 2), "RV_match%": round(r.rv_match_pct, 2),
            "Skew_Bias": round(r.skew_bias, 3), "Tail_Mass": round(r.tail_mass, 3),
            "Strikes": r.num_strikes
        })
    df = pd.DataFrame(data)
    filename = os.path.join(out_dir, f"screener_{timestamp}.csv")
    df.to_csv(filename, index=False)
    files = [filename]
    # Also render a PNG table for inclusion in PDFs and a ranking chart
    try:
        fig, ax = plt.subplots(figsize=(10, max(2, len(df) * 0.3)))
        ax.axis('off')
        tbl = ax.table(cellText=df.values, colLabels=df.columns, loc='center', cellLoc='left')
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(8)
        tbl.scale(1, 1.25)
        png_file = os.path.join(out_dir, f"screener_table_{timestamp}.png")
        plt.tight_layout()
        fig.savefig(png_file, dpi=150, bbox_inches='tight')
        plt.close(fig)
        files.append(png_file)
    except Exception as e:
        print(f"  Screener table image failed: {e}")
    # Ranking chart
    try:
        # Top 10 by score, among tickers with real data only -- a ticker
        # flagged INSUFFICIENT DATA sitting in this chart next to genuine
        # scores invites reading a data gap as a real ranking.
        rankable = [r for r in results if r.data_quality == "ok"] or results
        top = sorted(rankable, key=lambda r: r.score, reverse=True)[:10]
        labels = [r.ticker for r in top]
        scores = [r.score for r in top]
        fig, ax = plt.subplots(figsize=(8, 4))
        bars = ax.barh(labels[::-1], scores[::-1], color='#4c72b0')
        ax.set_xlabel('Composite Score')
        ax.set_title('Top 10 Screener Scores')
        for i, b in enumerate(bars):
            ax.text(b.get_width() + 1, b.get_y() + b.get_height() / 2, f"{scores[::-1][i]:.1f}", va='center')
        chart_file = os.path.join(out_dir, f"screener_ranking_{timestamp}.png")
        plt.tight_layout()
        fig.savefig(chart_file, dpi=150, bbox_inches='tight')
        plt.close(fig)
        files.append(chart_file)
    except Exception as e:
        print(f"  Screener ranking chart failed: {e}")

    # Build verbose interpretation text
    try:
        # Buy/sell candidates are drawn ONLY from tickers with a real RV
        # read -- an INSUFFICIENT DATA ticker's score is still computed (so
        # it still sorts sensibly in the table/chart) but a missing input
        # must never be able to land a ticker in "Top long/short candidates".
        flagged = [r for r in results if r.data_quality != "ok"]
        ok_results = [r for r in results if r.data_quality == "ok"]
        top_sell = [r for r in ok_results if r.score >= 60]
        top_buy = [r for r in ok_results if r.score <= 20]
        interp_lines = [f"Screened {len(results)} of {len(requested)} requested tickers (T={target_years} yr)"]
        if skipped:
            interp_lines.append(f"Skipped (no result, see log): {', '.join(skipped)}")
        if flagged:
            interp_lines.append(
                f"Flagged INSUFFICIENT DATA (excluded from candidate lists below, "
                f"missing realized-vol history): {', '.join(r.ticker for r in flagged)}"
            )
        interp_lines.append(f"Top short candidates: {', '.join([r.ticker for r in top_sell[:5]]) or 'None'}")
        interp_lines.append(f"Top long candidates: {', '.join([r.ticker for r in top_buy[:5]]) or 'None'}")
        interp_lines.append(f"Median score: {df['Score'].median():.1f}")
    except Exception:
        interp_lines = ["Screener completed."]
    interp = "\n".join(interp_lines)
    return files, interp
