"""Gamma Exposure (GEX) Scanner — dealer positioning, gamma flip, zero gamma.

Uses Vol_Suite's expiry_book_production.fetch_production_result(), the live
dealer-frame greeks engine (see expiry_book_exposure.py's module docstring
for the 2026-08-17 promotion history). dealer_positioning.py's
compute_dealer_positioning() is now locked to backtest/test callers only.

Key outputs fed into the correlation engine:
  - total_net_dollar_gamma -> GAMMA_SQUEEZE_RISK when negative + CNS > 50
  - gamma_flip_level -> the spot level where dealer gamma crosses zero
    (execution_locus.local_gamma_boundary)
"""

from typing import Optional, Dict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from scanner.options_scanner_base import get_td
from expiry_book_production import ExpiryBookUnavailable, fetch_production_result
from dealer_positioning import compute_forward_price, RISK_FREE_RATE
import expiry_selector

# How many calendar days of expiries to include (passed as max_days
# to dealer_positioning.compute_dealer_positioning).
DEFAULT_MAX_DAYS = 150
# Default target years for the anchoring forward price.
DEFAULT_TARGET_YEARS = 0.25

@dataclass
class GexScan:
    ticker: str
    spot: float
    forward: float
    total_net_gamma: float
    total_net_dollar_gamma: float
    gamma_flip_level: float
    highest_gamma_strike: float
    num_expiries: int
    num_records: int
    sign_model: str
    timestamp: str
    error: Optional[str] = None


def scan_gex(
    ticker: str,
    max_days: int = DEFAULT_MAX_DAYS,
    target_years: float = DEFAULT_TARGET_YEARS,
    sign_model: str = "expiry_book",
) -> GexScan:
    """Run dealer-positioning GEX scan for one ticker.

    Parameters
    ----------
    ticker : str
        Equity symbol.
    max_days : int
        Maximum calendar-day window for active expiries (passed as
        ``max_days`` to ``compute_dealer_positioning``).
    target_years : float
        Target years for the forward-price anchor.
    sign_model : str
        One of 'oi_heuristic' (call=+/put=- flat), 'replication' (Layer 1b),
        or 'vol_surface_replication' (Layer 1a+1b with per-strike flip).

    Returns
    -------
    GexScan
    """
    td = get_td()
    try:
        expiry, actual_T = expiry_selector.resolve_expiration(td, ticker, None, target_years)
        result = fetch_production_result(td, ticker, expiry)
        # expiry_book_production's ProductionDealerExposure has no `forward`
        # field (its greeks are spot-based, not forward-based) -- computed
        # here instead, same convention every other suite uses
        # (compute_forward_price(spot, r, q, T)), so GexScan's forward field
        # stays meaningful. Was previously `result.forward`, an attribute
        # that doesn't exist -- crashed every successful scan (CARL review,
        # 2026-08-17).
        r_live = td.fetch_risk_free_rate(actual_T)
        r_use = r_live if r_live is not None else RISK_FREE_RATE
        dividend_yield = td.fetch_dividend_yield(ticker)
        forward = compute_forward_price(result.spot, r_use, dividend_yield, actual_T)
    except (ValueError, RuntimeError, ExpiryBookUnavailable) as e:
        return GexScan(
            ticker=ticker, spot=0.0, forward=0.0,
            total_net_gamma=0.0, total_net_dollar_gamma=0.0,
            gamma_flip_level=0.0, highest_gamma_strike=0.0,
            num_expiries=0, num_records=0, sign_model=sign_model,
            timestamp=datetime.now(timezone.utc).isoformat(),
            error=str(e),
        )
    finally:
        td.close()

    return GexScan(
        ticker=ticker,
        spot=result.spot,
        forward=forward,
        # net("gamma") = raw share-denominated signed gamma exposure
        # (sign*gamma*OI*multiplier); gex() = the same exposure re-expressed
        # as dollar-gamma-per-1%-move. These were previously both set to
        # gex() (a copy-paste from the single-field legacy model), silently
        # mislabeling total_net_gamma as a dollar figure (CARL review,
        # 2026-08-17). Only the sign of either matters to the one live
        # consumer (correlation/engine.py's `< 0` check), so this was inert,
        # not a live bug -- fixed for correctness/future consumers anyway.
        total_net_gamma=result.snapshot.net("gamma"),
        total_net_dollar_gamma=result.snapshot.gex(),
        gamma_flip_level=result.execution_locus.local_gamma_boundary,
        highest_gamma_strike=result.execution_locus.call_gamma_wall,
        num_expiries=1,
        num_records=len(result.snapshot.rows),
        sign_model="expiry_book",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def format_gex(gex: GexScan) -> str:
    """One-line summary string for console output."""
    if gex.error:
        return f"  {gex.ticker:6s} | GEX: ERROR — {gex.error}"
    regime = "AMPLIFYING" if gex.total_net_dollar_gamma < 0 else "DAMPENING"
    return (
        f"  {gex.ticker:6s} | GEX: ${gex.total_net_dollar_gamma:+,.0f} "
        f"({regime}) | Flip: ${gex.gamma_flip_level:.2f} | "
        f"Hi-Gamma: ${gex.highest_gamma_strike:.2f} | "
        f"{gex.num_expiries} expiries"
    )


if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    gex = scan_gex(ticker)
    print(format_gex(gex))
