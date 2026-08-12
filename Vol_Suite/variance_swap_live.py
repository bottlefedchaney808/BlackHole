#!/usr/bin/env python3
# variance_swap_live.py
# Variance swap calculator using ThetaData (via api.potatohedge.com) for live data.

import math
import warnings
import os
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Tuple, List

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import textwrap

from thetadata_client import ThetaDataController, strike_to_theta, strike_from_theta
from correlation_engine import fetch_price_history
import expiry_selector
from vs_utils import timestamped_output_dir

warnings.filterwarnings("ignore", category=FutureWarning, module="pandas")

# DEFAULT_A (365, calendar days) is the CALENDAR-day count used ONLY to turn a
# target-years input into an expiry date / time-to-maturity -- shared via
# expiry_selector so this module, the screener, and dealer_positioning all
# resolve the same target to the same date.
DEFAULT_A = expiry_selector.DEFAULT_A
# TRADING_DAYS (252) is the annualization factor for realized vol. The price
# series is trading-day EOD closes (no weekends), so daily-return vol must be
# scaled by sqrt(252), NOT sqrt(365). Using DEFAULT_A here (as the code did)
# overstated realized vol by sqrt(365/252) ~ 1.20x and fed that ~20% inflation
# straight into every VRP (fair vol - RV) print. These are two genuinely
# different constants that were wrongly collapsed into one.
TRADING_DAYS = 252
RISK_FREE_RATE = 0.05  # fallback only; live rate now pulled from the yield curve


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


def _to_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return np.nan


def compute_forward_price(S0: float, r: float, q: float, T: float) -> float:
    return S0 * math.exp((r - q) * T)


def compute_vega_notional(spot: float, base_notional: float = 100_000.0,
                          reference_spot: float = 100.0) -> float:
    """Target dollar-vega exposure, scaled so a $2000 stock and a $20 stock
    aren't sized identically (the code previously hardcoded 100000 for every
    ticker on every run). `base_notional` is both the value at
    `reference_spot` and the floor for very cheap underlyings -- vega notional
    is a trade-size choice (see the variance-swap payoff convention
    N_var = N_vol / (2*sigma_strike)), not something that should shrink toward
    zero just because spot is small."""
    if not math.isfinite(spot) or spot <= 0:
        return base_notional
    return max(base_notional, base_notional * (spot / reference_spot))


def compute_variance_notional(vega_notional: float, strike_vol: float):
    """N_var = N_vol / (2 * sigma_strike). Returns None -- never a fabricated
    number -- when the fair strike vol isn't a usable positive value.

    That is a real, reachable case, not defensive padding:
    compute_fair_variance_strike returns fair_vol = 0.0 whenever the
    replication integral comes out non-positive (a degraded/illiquid chain),
    and 0.0 here would be a divide-by-zero. Callers must handle None by
    reporting "N/A", not by substituting a placeholder."""
    if strike_vol is None or not math.isfinite(strike_vol) or strike_vol <= 0:
        return None
    return vega_notional / (2.0 * strike_vol)


def _deltaK_half_widths(K: np.ndarray) -> np.ndarray:
    n = len(K)
    dK = np.empty(n)
    dK[0] = (K[1] - K[0]) / 2.0
    dK[-1] = (K[-1] - K[-2]) / 2.0
    if n > 2:
        dK[1:-1] = (K[2:] - K[:-2]) / 2.0
    return dK


def find_nearest_expiry_thetadata(td, ticker: str, target_years: float) -> Tuple[str, float]:
    """Kept as a thin wrapper (same name/signature) so nothing else importing
    this needs to change -- the actual lookup now lives in expiry_selector.py,
    shared with variance_swap_screener.py and dealer_positioning.py so all
    three agree on which expiry a given target_years resolves to."""
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
        # ThetaData returns strikes in cents (multiplied by 1000) — convert to dollars
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
    # Trading-day annualization (sqrt(252)) -- see TRADING_DAYS note above.
    return float(np.std(log_returns, ddof=1) * math.sqrt(TRADING_DAYS))


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
    call_iv_plot = np.where(K <= F, np.nan, chain.call_iv[mask])
    put_iv_plot = np.where(K <= F, chain.put_iv[mask], np.nan)

    return {
        "expiry": chain.expiry,
        "S0": S0,
        "F": F,
        "T_years": T_years,
        "r": chain.r, "q": chain.q,
        "fair_variance_annualized": fair_variance,
        "fair_variance_swap_strike_vol": fair_vol,
        "fair_variance_swap_strike_vol_pct": 100.0 * fair_vol,
        "atm_strike": float(atm_strike),
        "atm_implied_vol": float(atm_iv),
        "atm_implied_vol_pct": 100.0 * atm_iv,
        "convexity_premium_vol_pct": 100.0 * convexity_premium,
        "num_strikes_used": len(K),
        "K_min": float(K.min()), "K_max": float(K.max()),
        "strike_table": {
            "strikes": K, "deltaK": dK, "weights": weights,
            "otm_prices": OTM2, "contributions": contributions,
            "call_iv": call_iv_plot, "put_iv": put_iv_plot
        }
    }


def generate_plots(result, chain, S0, F, ticker, expiration, rv_30, rv_60, rv_90, rv_match, match_lookback, interpretation: str = None) -> str:
    t = result["strike_table"]
    K = t["strikes"]
    contrib = t["contributions"]
    call_iv = t["call_iv"]
    put_iv = t["put_iv"]
    otm = t["otm_prices"]
    fair_vol_pct = result["fair_variance_swap_strike_vol_pct"]
    atm_iv_pct = result["atm_implied_vol_pct"]

    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    fig.suptitle(f"{ticker} Variance Swap Analysis -- Expiry {expiration} (ThetaData)", fontsize=16, fontweight='bold')

    ax1 = axes[0, 0]
    ax1.bar(K, otm, width=np.diff(K, append=K[-1]+1)*0.8, color='steelblue', alpha=0.7)
    ax1.axvline(x=F, color='red', linestyle='--', linewidth=1.5, label=f'F = {F:.2f}')
    ax1.set_xlabel("Strike"); ax1.set_ylabel("Option Mid Price ($)")
    ax1.set_title("OTM Option Prices Used in Replication")
    ax1.legend(); ax1.grid(axis='y', alpha=0.3)

    ax2 = axes[0, 1]
    colors = ['#e74c3c' if k <= F else '#2ecc71' for k in K]
    ax2.bar(K, contrib, width=np.diff(K, append=K[-1]+1)*0.8, color=colors, alpha=0.7)
    ax2.axvline(x=F, color='red', linestyle='--', linewidth=1.5, label=f'F = {F:.2f}')
    ax2.set_xlabel("Strike"); ax2.set_ylabel("Contribution")
    ax2.set_title("Replication Weights by Strike")
    ax2.legend(); ax2.grid(axis='y', alpha=0.3)

    ax3 = axes[1, 0]
    ax3.scatter(K[~np.isnan(put_iv)], put_iv[~np.isnan(put_iv)]*100, color='red', label='Put IV', s=40, alpha=0.7)
    ax3.scatter(K[~np.isnan(call_iv)], call_iv[~np.isnan(call_iv)]*100, color='green', label='Call IV', s=40, alpha=0.7)
    ax3.axhline(y=fair_vol_pct, color='blue', linewidth=2, label=f'Fair Vol = {fair_vol_pct:.2f}%')
    if not math.isnan(atm_iv_pct):
        ax3.axhline(y=atm_iv_pct, color='orange', linestyle='--', linewidth=1.5, label=f'ATM IV = {atm_iv_pct:.2f}%')
    else:
        ax3.axhline(y=0, color='orange', linestyle='--', linewidth=1.5, label='ATM IV = N/A')
    ax3.axvline(x=F, color='purple', linestyle=':', linewidth=1, label=f'F = {F:.2f}')
    ax3.set_xlabel("Strike"); ax3.set_ylabel("Implied Volatility (%)")
    ax3.set_title("Volatility Smile vs Fair Variance Swap Strike")
    ax3.legend(fontsize=9); ax3.grid(alpha=0.3)

    ax4 = axes[1, 1]
    lookbacks = [30, 60, 90, match_lookback]
    labels = ['30d', '60d', '90d', f'{match_lookback}d (match)']
    rvs = [rv_30, rv_60, rv_90, rv_match]
    x_pos = np.arange(len(lookbacks))
    ax4.bar(x_pos, [rv*100 for rv in rvs], width=0.5, color=['#3498db', '#3498db', '#3498db', '#e67e22'], alpha=0.8)
    ax4.axhline(y=fair_vol_pct, color='red', linewidth=2, label=f'Fair Vol = {fair_vol_pct:.2f}%')
    ax4.set_xticks(x_pos); ax4.set_xticklabels(labels)
    ax4.set_ylabel("Annualized Volatility (%)")
    ax4.set_title("Realized Vol vs Fair Variance Swap Strike")
    ax4.legend(); ax4.grid(axis='y', alpha=0.3)

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    # Add interpretation text box if provided
    if interpretation:
        try:
            # Draw background box and multi-line wrapped text at bottom
            wrapped = textwrap.fill(interpretation, width=120)
            bbox_props = dict(boxstyle="round,pad=0.6", facecolor="#ffffff", alpha=0.9, edgecolor="#bbbbbb")
            fig.text(0.5, 0.02, wrapped, ha='center', va='bottom', fontsize=9, color="#222222", bbox=bbox_props)
        except Exception:
            pass

    out_dir = os.getenv("VS_OUTPUT_DIR") or timestamped_output_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(out_dir, f"{ticker}_variance_swap_plots_{timestamp}.png")
    plt.savefig(filename, dpi=150, bbox_inches='tight')
    plt.close()
    return filename


def export_csv(result, ticker, expiration, out_dir: str = None):
    t = result["strike_table"]
    df = pd.DataFrame({
        "Strike": t["strikes"], "DeltaK": t["deltaK"],
        "Weight": t["weights"], "OTM_Price": t["otm_prices"],
        "Contribution": t["contributions"]
    })
    out_dir = out_dir or os.getenv("VS_OUTPUT_DIR") or timestamped_output_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(out_dir, f"{ticker}_{expiration}_variance_swap_strikes_{timestamp}.csv")
    df.to_csv(filename, index=False)
    return filename


def export_summary_csv(result, ticker, expiration, rv_30, rv_60, rv_90, rv_match, match_lookback, out_dir: str = None):
    rv_30_str = f"{rv_30*100:.2f}" if not math.isnan(rv_30) else "N/A"
    rv_60_str = f"{rv_60*100:.2f}" if not math.isnan(rv_60) else "N/A"
    rv_90_str = f"{rv_90*100:.2f}" if not math.isnan(rv_90) else "N/A"
    rv_match_str = f"{rv_match*100:.2f}" if not math.isnan(rv_match) else "N/A"
    vrp_str = f"{result['fair_variance_swap_strike_vol_pct'] - rv_match*100:.2f}" if not math.isnan(rv_match) else "N/A"
    atm_iv_str = f"{result['atm_implied_vol_pct']:.2f}" if not math.isnan(result['atm_implied_vol_pct']) else "N/A"
    convexity_str = f"{result['convexity_premium_vol_pct']:.2f}" if not math.isnan(result['convexity_premium_vol_pct']) else "N/A"
    # Same spot-scaled sizing as the two callers -- writing the old flat
    # 100000 here would have made the exported CSV disagree with the result
    # dict and the console print.
    vega_notional = compute_vega_notional(result["S0"])
    variance_notional = compute_variance_notional(vega_notional, result["fair_variance_swap_strike_vol"])
    variance_notional_str = "N/A" if variance_notional is None else variance_notional

    data = {
        "Metric": [
            "Ticker", "Expiry", "Spot", "Forward", "T_years",
            "Fair_Variance_Annualized", "Fair_Vol_%", "ATM_Strike",
            "ATM_IV_%", "Convexity_Premium_vol_pts",
            "Num_Strikes_Used", "K_min", "K_max",
            "RV_30d_%", "RV_60d_%", "RV_90d_%",
            f"RV_{match_lookback}d_%", "VRP_vol_pts",
            "Vega_Notional", "Variance_Notional"
        ],
        "Value": [
            ticker, expiration, result["S0"], result["F"], result["T_years"],
            result["fair_variance_annualized"],
            result["fair_variance_swap_strike_vol_pct"],
            result["atm_strike"],
            atm_iv_str,
            convexity_str,
            result["num_strikes_used"],
            result["K_min"], result["K_max"],
            rv_30_str, rv_60_str, rv_90_str,
            rv_match_str, vrp_str,
            vega_notional,
            variance_notional_str
        ]
    }
    df = pd.DataFrame(data)
    out_dir = out_dir or os.getenv("VS_OUTPUT_DIR") or timestamped_output_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(out_dir, f"{ticker}_{expiration}_variance_swap_summary_{timestamp}.csv")
    df.to_csv(filename, index=False)
    return filename


def main():
    print("=" * 60)
    print("VARIANCE SWAP CALCULATOR -- ThetaData Live")
    print("=" * 60)

    ticker = input("Enter ticker: ").strip().upper() or "GME"

    print(f"\n[1/5] Connecting to ThetaData...")
    td = ThetaDataController()
    dividend_yield = td.fetch_dividend_yield(ticker)
    expiration, actual_T = expiry_selector.choose_expiry_interactive(td, ticker)
    print(f"  Chosen expiry: {expiration}  (T={actual_T:.4f} yr)")

    print("\n[2/5] Fetching option chain...")
    chain = fetch_chain_thetadata(td, ticker, expiration, RISK_FREE_RATE, dividend_yield)
    print(f"  Strikes: {len(chain.strikes)}")
    td.close()

    print("\n[3/5] Getting spot...")
    td2 = ThetaDataController()
    S0 = td2.fetch_spot_price(ticker)
    td2.close()
    print(f"  S0 = {S0:.4f}")

    print("\n[4/5] Computing forward...")
    F = compute_forward_price(S0, RISK_FREE_RATE, dividend_yield, actual_T)
    print(f"  F = {F:.4f}")

    print("\n[5/5] Computing fair variance...")
    result = compute_fair_variance_strike(chain, S0, actual_T)

    t = result["strike_table"]
    K, dK, w, otm, cnt = t["strikes"], t["deltaK"], t["weights"], t["otm_prices"], t["contributions"]
    print("\n" + "-" * 80)
    print(f"{'Strike':>8} | {'DeltaK':>8} | {'Weight':>10} | {'OTM Price':>10} | {'Contrib':>10}")
    print("-" * 80)
    for i in range(len(K)):
        print(f"{K[i]:8.2f} | {dK[i]:8.3f} | {w[i]:10.6f} | {otm[i]:10.4f} | {cnt[i]:10.6f}")
    print("-" * 80)
    print(f"{'TOTAL':>8} | {'':>8} | {'':>10} | {'':>10} | {np.sum(cnt):10.6f}")

    print("\n--- Fetching historical prices for realized vol (ThetaData first, yfinance fallback)...")
    try:
        hist_df = fetch_price_history([ticker], period="2y")
        prices = hist_df[ticker].values.flatten()
    except Exception as e:
        print(f"  History fetch failed: {e}")
        prices = np.array([])
    print(f"  Price points: {len(prices)}")

    if len(prices) < 5:
        rv_30 = rv_60 = rv_90 = rv_match = np.nan
    else:
        rv_30 = compute_realized_vol(prices, min(30, len(prices)))
        rv_60 = compute_realized_vol(prices, min(60, len(prices)))
        rv_90 = compute_realized_vol(prices, min(90, len(prices)))
        match_lookback = int(actual_T * TRADING_DAYS)  # trading-day price points ~ option life
        rv_match = compute_realized_vol(prices, min(match_lookback, len(prices)))

    print(f"RV (30d):  {rv_30*100:.2f}%" if not math.isnan(rv_30) else "RV (30d):  N/A")
    print(f"RV (60d):  {rv_60*100:.2f}%" if not math.isnan(rv_60) else "RV (60d):  N/A")
    print(f"RV (90d):  {rv_90*100:.2f}%" if not math.isnan(rv_90) else "RV (90d):  N/A")
    if not math.isnan(rv_match):
        print(f"RV ({match_lookback}d): {rv_match*100:.2f}%")

    print("\n" + "=" * 60)
    print("FINAL RESULT")
    print("=" * 60)
    for k, v in result.items():
        if k == "strike_table":
            continue
        if isinstance(v, float):
            print(f"{k:35}: {v:.6f}")
        else:
            print(f"{k:35}: {v}")
    print("=" * 60)

    fair_vol_pct = result["fair_variance_swap_strike_vol_pct"]
    if not math.isnan(rv_match):
        vrp = fair_vol_pct - rv_match * 100
        print(f"\n--- VRP ---")
        print(f"Fair vol: {fair_vol_pct:.2f}% | RV: {rv_match*100:.2f}% | VRP: {vrp:+.2f} vol pts")
        print("---")

    vega_notional = compute_vega_notional(S0)
    variance_notional = compute_variance_notional(vega_notional, result["fair_variance_swap_strike_vol"])
    if variance_notional is None:
        print(f"\nVega notional: ${vega_notional:,.0f} -> Variance notional: N/A "
              f"(fair strike vol is {result['fair_variance_swap_strike_vol']}, not usable)")
    else:
        print(f"\nVega notional: ${vega_notional:,.0f} -> Variance notional: ${variance_notional:,.2f}")

    print("\n--- Generating plots...")
    try:
        plot_file = generate_plots(result, chain, S0, F, ticker, expiration, rv_30, rv_60, rv_90, rv_match, match_lookback)
        print(f"  Plots: {plot_file}")
    except Exception as e:
        print(f"  Plot error: {e}")

    choice = input("\nExport CSV? (y/n): ").strip().lower()
    if choice == 'y':
        out_dir = os.getenv("VS_OUTPUT_DIR") or timestamped_output_dir()
        print(f"  Strikes CSV: {export_csv(result, ticker, expiration, out_dir=out_dir)}")
        sum_file = export_summary_csv(result, ticker, expiration, rv_30, rv_60, rv_90, rv_match, match_lookback, out_dir=out_dir)
        print(f"  Summary CSV: {sum_file}")
    print("\nDone.")


if __name__ == "__main__":
    main()


def run_variance_swap_live(ticker: str, target_years: float = 0.25, output_dir: str = None,
                           expiration: str = None) -> tuple:
    """Programmatic runner for the variance swap live module.
    Returns (file_paths, interpretation_text, result_dict) -- the raw result
    dict lets callers (e.g. the volatility_suite orchestrator's opportunities
    section) compare fair vol / VRP across legs without re-parsing text.

    `expiration`: if given (a "YYYYMMDD" string), use it directly instead of
    re-deriving a nearest-expiry from target_years. This is what lets
    volatility_suite.py resolve ONE expiry interactively and force every
    downstream leg (index + focus ticker here, screener, dealer positioning)
    onto that same date instead of each one independently re-picking."""
    out_dir = output_dir or os.getenv("VS_OUTPUT_DIR") or timestamped_output_dir()
    os.makedirs(out_dir, exist_ok=True)
    files = []
    td = ThetaDataController()
    dividend_yield = td.fetch_dividend_yield(ticker)
    expiration, actual_T = expiry_selector.resolve_expiration(td, ticker, expiration, target_years)
    r_live = td.fetch_risk_free_rate(actual_T)
    r_use = r_live if r_live is not None else RISK_FREE_RATE
    if r_live is None:
        print(f"  [rate] yield curve unavailable; using fallback r={RISK_FREE_RATE:.4f}")
    chain = fetch_chain_thetadata(td, ticker, expiration, r_use, dividend_yield)
    td.close()
    td2 = ThetaDataController()
    S0 = td2.fetch_spot_price(ticker)
    td2.close()
    F = compute_forward_price(S0, r_use, dividend_yield, actual_T)
    result = compute_fair_variance_strike(chain, S0, actual_T)
    # Compute realized vols for plotting and summary (match main flow).
    # ThetaData first, yfinance fallback only (see correlation_engine.fetch_price_history).
    try:
        hist_df = fetch_price_history([ticker], period="2y")
        prices = hist_df[ticker].values.flatten()
    except Exception as e:
        print(f"  History fetch failed: {e}")
        prices = []

    if len(prices) < 5:
        rv_30 = rv_60 = rv_90 = rv_match = float('nan')
        match_lookback = 0
    else:
        from math import isnan
        rv_30 = compute_realized_vol(prices, min(30, len(prices)))
        rv_60 = compute_realized_vol(prices, min(60, len(prices)))
        rv_90 = compute_realized_vol(prices, min(90, len(prices)))
        match_lookback = int(actual_T * TRADING_DAYS)  # trading-day price points ~ option life
        rv_match = compute_realized_vol(prices, min(match_lookback, len(prices))) if match_lookback > 0 else float('nan')

    # plots
    try:
        # generate_plots uses VS_OUTPUT_DIR env var; ensure it is set
        os.environ['VS_OUTPUT_DIR'] = out_dir
        interp = (
            f"Fair vol: {result.get('fair_variance_swap_strike_vol_pct', 'N/A'):.2f}% | "
            f"ATM IV: {result.get('atm_implied_vol_pct', float('nan')):.2f}% | "
            f"Convexity: {result.get('convexity_premium_vol_pct', float('nan')):.2f}pp"
        )
        plot_file = generate_plots(result, chain, S0, F, ticker, expiration, rv_30, rv_60, rv_90, rv_match, match_lookback, interpretation=interp)
        files.append(plot_file)
    except Exception as e:
        print(f"  Plot error: {e}")
    # csv exports
    try:
        strikes_csv = export_csv(result, ticker, expiration, out_dir=out_dir)
        summary_csv = export_summary_csv(result, ticker, expiration, rv_30, rv_60, rv_90, rv_match, match_lookback, out_dir=out_dir)
        files.extend([strikes_csv, summary_csv])
    except Exception as e:
        print(f"  CSV export failed: {e}")
    # Build interpretation text
    try:
        fair_vol = result.get('fair_variance_swap_strike_vol_pct', None)
        atm_iv = result.get('atm_implied_vol_pct', None)
        convexity = result.get('convexity_premium_vol_pct', None)
        interp_lines = [
            f"Ticker: {ticker}",
            f"Fair Vol (ann.): {fair_vol:.2f}%" if fair_vol is not None else "Fair Vol: N/A",
            f"ATM IV: {atm_iv:.2f}%" if atm_iv is not None else "ATM IV: N/A",
            f"Convexity (vol pts): {convexity:.2f}" if convexity is not None else "Convexity: N/A",
        ]
    except Exception:
        interp_lines = [f"Ticker: {ticker}"]
    interp = "\n".join(interp_lines)
    # Surface realized vol / VRP on the result dict too, since they're computed
    # here but weren't previously part of the dict compute_fair_variance_strike returns.
    try:
        result['rv_match'] = rv_match
        if not math.isnan(rv_match):
            result['vrp_vol_pts'] = result.get('fair_variance_swap_strike_vol_pct', float('nan')) - rv_match * 100
        else:
            result['vrp_vol_pts'] = float('nan')
    except Exception:
        pass
    # Trade sizing. Neither value was computed on this path before, so callers
    # of run_variance_swap_live saw no notional at all. Both are pure
    # arithmetic over values already in hand -- the only genuine failure mode
    # (a non-positive fair strike vol from a degraded chain) is handled
    # explicitly by compute_variance_notional returning None, so there is
    # nothing here worth wrapping in a blanket except.
    result['vega_notional'] = compute_vega_notional(S0)
    result['variance_notional'] = compute_variance_notional(
        result['vega_notional'], result.get('fair_variance_swap_strike_vol'))
    if result['variance_notional'] is None:
        print(f"  [sizing] variance notional unavailable: fair strike vol = "
              f"{result.get('fair_variance_swap_strike_vol')}")
    return files, interp, result
