"""IV Rank / IV Percentile Scanner — where ATM IV sits vs GARCH & RV.

Compares current ATM implied vol to:
  1. GARCH(1,1) conditional vol forecast (30-day)
  2. Recent realized vol (30, 60, 90 day)
  3. Fair vol from variance replication

Gives a vol-regime read: RICH (IV >> RV), CHEAP (IV << RV), or FAIR.
The correlation engine uses this as IV_RANK_EXTREME when IV is in the
tails of its distribution vs GARCH/RV.
"""

from typing import Optional
from dataclasses import dataclass
from datetime import datetime, timezone
import numpy as np

from scanner.options_scanner_base import VolSuiteImporter, get_td

vsi = VolSuiteImporter()


@dataclass
class IvRankScan:
    ticker: str
    spot: float
    atm_iv_pct: float
    garch_cond_vol_pct: float  # GARCH conditional vol (annualized %)
    rv_30_pct: float
    rv_60_pct: float
    rv_90_pct: float
    fair_vol_pct: float         # Fair variance-strike vol
    vrp_pct: float              # IV - fair vol (vol points)
    regime: str                 # "RICH" | "CHEAP" | "FAIR" | "UNKNOWN"
    timestamp: str
    error: Optional[str] = None


def scan_iv_rank(ticker: str, *, garch_cond_vol_pct: Optional[float] = None,
                 fair_vol_pct: Optional[float] = None) -> IvRankScan:
    """IV rank / percentile scan for one ticker.

    Fetches:
      - ATM IV from the nearest ~30DTE expiry (via options_chain_scanner
        / variance_swap_screener path)
      - GARCH conditional vol via garch_analysis.run_garch_analysis
      - Realized vol at 30/60/90 day lookbacks
      - Fair variance vol

    Parameters
    ----------
    ticker : str

    Returns
    -------
    IvRankScan
    """
    td = get_td()
    ts = datetime.now(timezone.utc).isoformat()

    # --- 1. Spot ---
    try:
        spot = td.fetch_spot_price(ticker)
    except Exception as e:
        return IvRankScan(
            ticker=ticker, spot=0.0, atm_iv_pct=0.0,
            garch_cond_vol_pct=0.0, rv_30_pct=0.0, rv_60_pct=0.0,
            rv_90_pct=0.0, fair_vol_pct=0.0, vrp_pct=0.0,
            regime="UNKNOWN", timestamp=ts, error=str(e),
        )
    if spot <= 0:
        return IvRankScan(
            ticker=ticker, spot=0.0, atm_iv_pct=0.0,
            garch_cond_vol_pct=0.0, rv_30_pct=0.0, rv_60_pct=0.0,
            rv_90_pct=0.0, fair_vol_pct=0.0, vrp_pct=0.0,
            regime="UNKNOWN", timestamp=ts, error="no_spot",
        )

    # --- 2. Nearest expiry & ATM IV ---
    try:
        expiry, T_years = vsi.expiry_selector.nearest_expiry(
            td, ticker, target_years=0.083
        )
    except Exception:
        expiry, T_years = None, 0.0

    atm_iv = 0.0
    if expiry:
        try:
            greeks = td.option_bulk_greeks(ticker, expiry)
            ivs = []
            for row in greeks:
                try:
                    iv = float(row.get("implied_vol", 0) or 0)
                    if iv > 0:
                        ivs.append(iv)
                except (ValueError, TypeError):
                    continue
            if ivs:
                atm_iv = float(np.median(ivs))
        except Exception:
            pass

    atm_iv_pct = atm_iv * 100.0

    # --- 3. Realized vol ---
    rv_30 = rv_60 = rv_90 = 0.0
    try:
        hist_df = vsi.correlation_engine.fetch_price_history([ticker], period="2y")
        prices = hist_df[ticker].values.flatten()
        if len(prices) >= 5:
            rv_30 = vsi.variance_swap_screener.compute_realized_vol(prices, min(30, len(prices))) * 100.0
            rv_60 = vsi.variance_swap_screener.compute_realized_vol(prices, min(60, len(prices))) * 100.0
            rv_90 = vsi.variance_swap_screener.compute_realized_vol(prices, min(90, len(prices))) * 100.0
    except Exception:
        pass

    # --- 4. GARCH conditional vol ---
    # A caller (the orchestrator's market-signals stage) may supply the
    # Vol_Suite-computed GARCH conditional vol from context -- prefer it over
    # re-fitting here, which can silently degrade to 0.0 when the fit fails.
    garch_vol = float(garch_cond_vol_pct) if garch_cond_vol_pct else 0.0
    if not garch_vol:
        try:
            garch_result = vsi.garch_analysis.run_garch_analysis(ticker)
            # garch_result has .conditional_volatility (last value) and .forecast
            # Extract the final conditional vol
            if hasattr(garch_result, "conditional_volatility") and len(garch_result.conditional_volatility) > 0:
                garch_vol = float(garch_result.conditional_volatility[-1] * 100.0)
        except Exception:
            pass

    # --- 5. Fair variance vol ---
    # Prefer a context-supplied fair vol (from Vol_Suite); only recompute here
    # (which can silently degrade to 0.0 on failure) when none was given.
    fair_vol_rv = float(fair_vol_pct) if fair_vol_pct else 0.0
    if not fair_vol_rv:
        try:
            # Use the nearest ~60DTE expiry for the fair-vol calculation
            fair_expiry, fair_T = vsi.expiry_selector.nearest_expiry(
                td, ticker, target_years=0.167
            )
            if fair_expiry:
                div_yield = td.fetch_dividend_yield(ticker)
                r_live = td.fetch_risk_free_rate(fair_T)
                r = r_live if r_live is not None else 0.05
                chain = vsi.variance_swap_screener.fetch_chain_thetadata(
                    td, ticker, fair_expiry, r, div_yield
                )
                fair = vsi.variance_swap_screener.compute_fair_variance_strike(
                    chain, spot, fair_T
                )
                fair_vol_rv = fair.get("fair_vol_pct", 0.0)
                if fair_vol_rv == 0.0:
                    fair_vol_rv = fair.get("fair_vol", 0.0) * 100.0
        except Exception:
            pass

    # --- 6. Vol regime ---
    vrp = atm_iv_pct - fair_vol_rv if fair_vol_rv > 0 else 0.0
    if atm_iv_pct > 0 and fair_vol_rv > 0:
        if vrp >= 4.0:
            regime = "RICH"
        elif vrp <= -2.0:
            regime = "CHEAP"
        else:
            regime = "FAIR"
    elif atm_iv_pct > 0 and rv_60 > 0:
        vrp_rv = atm_iv_pct - rv_60
        regime = "RICH" if vrp_rv >= 4.0 else ("CHEAP" if vrp_rv <= -2.0 else "FAIR")
        vrp = vrp_rv
    else:
        regime = "UNKNOWN"

    return IvRankScan(
        ticker=ticker, spot=spot, atm_iv_pct=round(atm_iv_pct, 2),
        garch_cond_vol_pct=round(garch_vol, 2),
        rv_30_pct=round(rv_30, 2), rv_60_pct=round(rv_60, 2),
        rv_90_pct=round(rv_90, 2),
        fair_vol_pct=round(fair_vol_rv, 2),
        vrp_pct=round(vrp, 2), regime=regime, timestamp=ts,
    )


def format_iv_rank(scan: IvRankScan) -> str:
    """One-line summary string."""
    if scan.error:
        return f"  {scan.ticker:6s} | IV: ERROR — {scan.error}"
    return (
        f"  {scan.ticker:6s} | IV: {scan.atm_iv_pct:.1f}% "
        f"| Regime: {scan.regime} "
        f"| RV(30/60/90): {scan.rv_30_pct:.1f}/{scan.rv_60_pct:.1f}/{scan.rv_90_pct:.1f}%"
        + (f" | GARCH: {scan.garch_cond_vol_pct:.1f}%" if scan.garch_cond_vol_pct > 0 else "")
        + (f" | VRP: {scan.vrp_pct:+.1f}pp" if scan.vrp_pct != 0 else "")
    )


if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    scan = scan_iv_rank(ticker)
    print(format_iv_rank(scan))
