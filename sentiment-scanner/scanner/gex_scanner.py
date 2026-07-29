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

from scanner.options_scanner_base import VolSuiteImporter, get_td

# How many calendar days of expiries to include (passed as max_days
# to dealer_positioning.compute_dealer_positioning).
DEFAULT_MAX_DAYS = 150
# Default target years for the anchoring forward price.
DEFAULT_TARGET_YEARS = 0.25

vsi = VolSuiteImporter()


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
    sign_model: str = "oi_heuristic",
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
        result = vsi.dealer_positioning.compute_dealer_positioning(
            ticker,
            target_years=target_years,
            max_days=max_days,
            sign_model=sign_model,
        )
    except (ValueError, RuntimeError) as e:
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
        forward=result.forward,
        total_net_gamma=result.total_net_gamma,
        total_net_dollar_gamma=result.total_net_dollar_gamma,
        gamma_flip_level=result.gamma_flip_level,
        highest_gamma_strike=result.highest_gamma_strike,
        num_expiries=result.num_expiries,
        num_records=result.num_records,
        sign_model=result.sign_model,
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
