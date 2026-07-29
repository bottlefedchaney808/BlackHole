"""Vol Dispersion Scanner — single-stock IV vs benchmark IV.

Detects when a stock's implied vol is expanding relative to its benchmark
(sector ETF / SPY).  This is a classic dispersion trade signal: when single-
stock vol outruns index vol, the correlation between the stock and the index
is likely falling, creating a dispersion opportunity.

Key outputs:
  - stock_iv_pct, benchmark_iv_pct
  - iv_spread_pts: stock IV - benchmark IV
  - iv_spread_z: how many standard deviations above/below the mean spread
  - beta: historical beta to benchmark
  - dispersion_signal: "DISPERSION_SETUP" (stock vol >> benchmark),
    "CONTRACTION" (stock vol << benchmark), or "NEUTRAL"
"""

from typing import Optional
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import numpy as np

from scanner.options_scanner_base import VolSuiteImporter, get_td

vsi = VolSuiteImporter()

# Default benchmark ticker
BENCHMARK = "SPY"
# Z-score threshold for flagging dispersion signal
DISPERSION_Z_THRESHOLD = 1.5
# Spread in vol points for override flag
DISPERSION_PTS_THRESHOLD = 10.0


@dataclass
class VolDispersionScan:
    ticker: str
    benchmark: str
    spot: float
    stock_iv_pct: float
    benchmark_iv_pct: float
    iv_spread_pts: float
    iv_spread_z: float
    beta: Optional[float]
    stock_atm_iv_pct: float
    benchmark_atm_iv_pct: float
    dispersion_signal: str  # "DISPERSION_SETUP" | "CONTRACTION" | "NEUTRAL"
    timestamp: str
    error: Optional[str] = None


def _get_atm_iv(ticker: str) -> float:
    """Quick ATM IV estimate for any ticker (nearest ~30DTE expiry)."""
    td = get_td()
    try:
        expiry, _ = vsi.expiry_selector.nearest_expiry(td, ticker, target_years=0.083)
    except Exception:
        return 0.0
    if not expiry:
        return 0.0
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
        return float(np.median(ivs)) * 100.0 if ivs else 0.0
    except Exception:
        return 0.0


def _estimate_beta(ticker: str, benchmark: str) -> Optional[float]:
    """Quick historical beta estimate via correlation_engine."""
    try:
        prices = vsi.correlation_engine.fetch_price_history(
            [ticker, benchmark], period="1y"
        )
        returns = np.log(prices / prices.shift(1)).dropna()
        if len(returns) < 20:
            return None
        t_ret = returns[ticker].values
        b_ret = returns[benchmark].values
        cov = np.cov(t_ret, b_ret, ddof=1)[0, 1]
        var = np.var(b_ret, ddof=1)
        return float(cov / var) if var > 0 else None
    except Exception:
        return None


def scan_vol_dispersion(
    ticker: str,
    benchmark: str = BENCHMARK,
) -> VolDispersionScan:
    """Run vol dispersion scan comparing ticker to benchmark.

    Parameters
    ----------
    ticker : str
    benchmark : str
        Benchmark ticker (default SPY).

    Returns
    -------
    VolDispersionScan
    """
    td = get_td()
    ts = datetime.now(timezone.utc).isoformat()

    try:
        spot = td.fetch_spot_price(ticker)
    except Exception as e:
        return VolDispersionScan(
            ticker=ticker, benchmark=benchmark, spot=0.0,
            stock_iv_pct=0.0, benchmark_iv_pct=0.0,
            iv_spread_pts=0.0, iv_spread_z=0.0, beta=None,
            stock_atm_iv_pct=0.0, benchmark_atm_iv_pct=0.0,
            dispersion_signal="UNKNOWN", timestamp=ts, error=str(e),
        )

    stock_iv = _get_atm_iv(ticker)
    benchmark_iv = _get_atm_iv(benchmark)
    spread = stock_iv - benchmark_iv

    # For a z-score, we need a history of the spread.  Since this is a
    # single-snapshot scanner, we use a simple heuristic: if stock vol is
    # > 2x benchmark vol or > 10 vol pts above, it's a dispersion setup.
    if stock_iv > 0 and benchmark_iv > 0:
        if spread >= DISPERSION_PTS_THRESHOLD or stock_iv >= benchmark_iv * 2.0:
            signal = "DISPERSION_SETUP"
        elif spread <= -DISPERSION_PTS_THRESHOLD or benchmark_iv >= stock_iv * 2.0:
            signal = "CONTRACTION"
        else:
            signal = "NEUTRAL"
    else:
        signal = "NEUTRAL"

    # Beta estimate
    beta = _estimate_beta(ticker, benchmark)

    # Z-score: use a rough estimate based on typical vol spread volatility
    # (about 3-5 vol points for liquid names).  We'll estimate from the
    # stock's own vol level.
    spread_vol_est = max(stock_iv * 0.15, 2.0)  # 15% of stock vol, floor 2pp
    z = spread / spread_vol_est if spread_vol_est > 0 else 0.0

    return VolDispersionScan(
        ticker=ticker, benchmark=benchmark, spot=spot,
        stock_iv_pct=round(stock_iv, 2),
        benchmark_iv_pct=round(benchmark_iv, 2),
        iv_spread_pts=round(spread, 2),
        iv_spread_z=round(z, 2),
        beta=round(beta, 3) if beta is not None else None,
        stock_atm_iv_pct=round(stock_iv, 2),
        benchmark_atm_iv_pct=round(benchmark_iv, 2),
        dispersion_signal=signal,
        timestamp=ts,
    )


def format_dispersion(scan: VolDispersionScan) -> str:
    """One-line summary string."""
    if scan.error:
        return f"  {scan.ticker:6s} | DISP: ERROR — {scan.error}"
    beta_str = f" β={scan.beta:.2f}" if scan.beta is not None else ""
    return (
        f"  {scan.ticker:6s} | Dispersion: IV {scan.stock_iv_pct:.1f}% vs "
        f"{scan.benchmark} {scan.benchmark_iv_pct:.1f}% "
        f"(spread {scan.iv_spread_pts:+.1f}pp, z={scan.iv_spread_z:+.1f}){beta_str} | "
        f"{scan.dispersion_signal}"
    )


if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    scan = scan_vol_dispersion(ticker)
    print(format_dispersion(scan))