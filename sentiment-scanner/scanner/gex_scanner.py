"""Gamma Exposure (GEX) Scanner — dealer positioning, gamma flip, zero gamma.

Uses Vol_Suite's dealer_positioning.compute_dealer_positioning() which
aggregates gamma across all active expiries from ThetaData and returns
net gamma, gamma flip level, dollar gamma by strike, and the full record
set.

Key outputs fed into the correlation engine:
  - total_net_gamma -> GAMMA_SQUEEZE_RISK when negative + CNS > 50
  - gamma_flip_level -> the strike where net gamma crosses zero
  - gamma_by_strike / dollar_gamma_by_strike -> per-strike exposure
"""

from typing import Optional, Dict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from scanner.options_scanner_base import get_td
from expiry_book_production import ExpiryBookUnavailable, fetch_production_result
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
        expiry, _ = expiry_selector.resolve_expiration(td, ticker, None, target_years)
        result = fetch_production_result(td, ticker, expiry)
    except (ValueError, RuntimeError, ExpiryBookUnavailable) as e:
        return GexScan(
            ticker=ticker, spot=0.0, forward=0.0,
            total_net_gamma=0.0, total_net_dollar_gamma=0.0,
            gamma_flip_level=0.0, highest_gamma_strike=0.0,
            num_expiries=0, num_records=0, sign_model=sign_model,
            timestamp=datetime.now(timezone.utc).isoformat(),
            error=str(e),
        )

    return GexScan(
        ticker=ticker,
        spot=result.spot,
        # ProductionDealerExposure (expiry_book_production, the current
        # dealer model as of 2026-08-17) has no forward-price concept --
        # spot is the closest available anchor.
        forward=result.spot,
        total_net_gamma=result.snapshot.gex(),
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
