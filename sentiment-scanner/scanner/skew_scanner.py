"""Skew Extreme Scanner — put skew via SABR reference curve.

Uses Vol_Suite's vol_surface_reference to fit SABR across the OTM strip
and flag extreme skew conditions.  Key outputs:

  - put_skew_pts: put IV - call IV at symmetric OTM points (25-delta proxies)
  - sabr_rho: SABR correlation parameter (negative = put-skewed)
  - sabr_nu: SABR vol-of-vol
  - deviation_rich: strikes trading rich vs the reference curve
  - deviation_cheap: strikes trading cheap vs the reference curve
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timezone
import math

from scanner.options_scanner_base import VolSuiteImporter, get_td

vsi = VolSuiteImporter()

# Thresholds for flagging extreme skew
PUT_SKEW_WARN = 3.0    # vol points — put skew above this is flagged
PUT_SKEW_EXTREME = 6.0  # vol points — extreme threshold
# Minimum number of deviation strikes to report a "rich/cheap cluster"
MIN_DEVIATION_STRIKES = 3


@dataclass
class SkewScan:
    ticker: str
    spot: float
    forward: float
    expiry: str
    T_years: float
    atm_iv_pct: float
    put_skew_pts: float          # 25-delta-ish put IV - call IV
    num_strikes_total: int
    num_otm_strikes: int
    sabr_fit_success: bool
    sabr_alpha: Optional[float]  # SABR alpha (ATM vol level)
    sabr_rho: Optional[float]    # SABR rho (negative = put skew)
    sabr_nu: Optional[float]     # SABR nu (vol of vol)
    sabr_rmse: Optional[float]   # Fit RMSE in vol points
    fitter: str                  # 'sabr' or 'quadratic'
    rich_strikes: List[Tuple[float, str, float]]  # (strike, right, deviation_pts)
    cheap_strikes: List[Tuple[float, str, float]]
    skew_signal: str             # "PUT_SKEW_EXTREME" | "PUT_SKEW_ELEVATED" | "CALL_SKEWED" | "FLAT"
    timestamp: str
    error: Optional[str] = None


def _find_otm_iv_at_delta(
    chain_iv: Dict[Tuple[float, str], float],
    forward: float,
    delta_target: float = 0.25,
) -> Tuple[float, float]:
    """Approximate 25-delta put and call IV from the OTM chain.

    Takes the strike nearest to forward*(1 - delta_target) for puts
    and forward*(1 + delta_target) for calls, using the OTM side only.
    Returns (put_iv, call_iv) in decimal.
    """
    put_strikes = [k for k, r in chain_iv if r == "P" and k < forward]
    call_strikes = [k for k, r in chain_iv if r == "C" and k > forward]

    def _nearest_iv(strikes, target):
        if not strikes:
            return float("nan")
        best = min(strikes, key=lambda k: abs(k - target))
        return chain_iv.get((best, "P" if best < forward else "C"), float("nan"))

    put_target = forward * (1.0 - delta_target)
    call_target = forward * (1.0 + delta_target)
    return _nearest_iv(put_strikes, put_target), _nearest_iv(call_strikes, call_target)


def scan_skew(ticker: str) -> SkewScan:
    """Run skew scan for one ticker.

    Parameters
    ----------
    ticker : str

    Returns
    -------
    SkewScan
    """
    td = get_td()
    ts = datetime.now(timezone.utc).isoformat()

    try:
        spot = td.fetch_spot_price(ticker)
    except Exception as e:
        return SkewScan(
            ticker=ticker, spot=0.0, forward=0.0, expiry="", T_years=0.0,
            atm_iv_pct=0.0, put_skew_pts=0.0,
            num_strikes_total=0, num_otm_strikes=0,
            sabr_fit_success=False, sabr_alpha=None, sabr_rho=None, sabr_nu=None,
            sabr_rmse=None, fitter="", rich_strikes=[], cheap_strikes=[],
            skew_signal="UNKNOWN", timestamp=ts, error=str(e),
        )
    if spot <= 0:
        return SkewScan(
            ticker=ticker, spot=0.0, forward=0.0, expiry="", T_years=0.0,
            atm_iv_pct=0.0, put_skew_pts=0.0,
            num_strikes_total=0, num_otm_strikes=0,
            sabr_fit_success=False, sabr_alpha=None, sabr_rho=None, sabr_nu=None,
            sabr_rmse=None, fitter="", rich_strikes=[], cheap_strikes=[],
            skew_signal="UNKNOWN", timestamp=ts, error="no_spot",
        )

    try:
        expiry, T_years = vsi.expiry_selector.nearest_expiry(
            td, ticker, target_years=0.25
        )
    except Exception as e:
        return SkewScan(
            ticker=ticker, spot=spot, forward=0.0, expiry="", T_years=0.0,
            atm_iv_pct=0.0, put_skew_pts=0.0,
            num_strikes_total=0, num_otm_strikes=0,
            sabr_fit_success=False, sabr_alpha=None, sabr_rho=None, sabr_nu=None,
            sabr_rmse=None, fitter="", rich_strikes=[], cheap_strikes=[],
            skew_signal="UNKNOWN", timestamp=ts, error=str(e),
        )

    if not expiry:
        return SkewScan(
            ticker=ticker, spot=spot, forward=0.0, expiry="", T_years=0.0,
            atm_iv_pct=0.0, put_skew_pts=0.0,
            num_strikes_total=0, num_otm_strikes=0,
            sabr_fit_success=False, sabr_alpha=None, sabr_rho=None, sabr_nu=None,
            sabr_rmse=None, fitter="", rich_strikes=[], cheap_strikes=[],
            skew_signal="UNKNOWN", timestamp=ts, error="no_expiry",
        )

    # --- Build chain_iv ---
    try:
        greeks = td.option_bulk_greeks(ticker, expiry)
    except Exception as e:
        return SkewScan(
            ticker=ticker, spot=spot, forward=0.0, expiry=expiry, T_years=T_years,
            atm_iv_pct=0.0, put_skew_pts=0.0,
            num_strikes_total=0, num_otm_strikes=0,
            sabr_fit_success=False, sabr_alpha=None, sabr_rho=None, sabr_nu=None,
            sabr_rmse=None, fitter="", rich_strikes=[], cheap_strikes=[],
            skew_signal="UNKNOWN", timestamp=ts, error=str(e),
        )

    chain_iv: Dict[Tuple[float, str], float] = {}
    for row in greeks:
        try:
            k = float(row["strike"]) / 1000.0  # theta format
            right = row["right"]
            iv = float(row.get("implied_vol", 0) or 0)
            if iv > 0:
                chain_iv[(k, right)] = iv
        except (ValueError, KeyError, TypeError):
            continue

    if not chain_iv:
        return SkewScan(
            ticker=ticker, spot=spot, forward=0.0, expiry=expiry, T_years=T_years,
            atm_iv_pct=0.0, put_skew_pts=0.0,
            num_strikes_total=0, num_otm_strikes=0,
            sabr_fit_success=False, sabr_alpha=None, sabr_rho=None, sabr_nu=None,
            sabr_rmse=None, fitter="", rich_strikes=[], cheap_strikes=[],
            skew_signal="UNKNOWN", timestamp=ts, error="no_chain_iv",
        )

    # --- Forward & dividend ---
    try:
        div_yield = td.fetch_dividend_yield(ticker)
        r_live = td.fetch_risk_free_rate(T_years)
        r = r_live if r_live is not None else 0.05
        forward = spot * math.exp((r - div_yield) * T_years)
    except Exception:
        forward = spot
        div_yield = 0.0

    # --- SABR reference curve ---
    ref = vsi.vol_surface_reference.compute_vol_surface_reference(
        ticker, chain_iv, spot, forward=forward, T=T_years
    )

    sabr_success = False
    sabr_alpha = sabr_rho = sabr_nu = sabr_rmse = None
    fitter = "quadratic"
    rich: List[Tuple[float, str, float]] = []
    cheap: List[Tuple[float, str, float]] = []

    if ref is not None:
        fitter = ref.fitter
        if ref.sabr_params:
            sabr_success = True
            sabr_alpha = ref.sabr_params["alpha"]
            sabr_rho = ref.sabr_params["rho"]
            sabr_nu = ref.sabr_params["nu"]
            sabr_rmse = ref.sabr_params.get("rmse")

        # Collect rich/cheap strikes
        for (k, right), dev in ref.deviation_by_strike.items():
            dev_pts = dev * 100.0
            if abs(dev_pts) < 1.0:
                continue
            if dev_pts > 0:
                rich.append((k, right, round(dev_pts, 2)))
            else:
                cheap.append((k, right, round(dev_pts, 2)))

    rich.sort(key=lambda x: abs(x[2]), reverse=True)
    cheap.sort(key=lambda x: abs(x[2]), reverse=True)

    # --- 25-delta-ish skew ---
    put_iv, call_iv = _find_otm_iv_at_delta(chain_iv, forward, 0.25)
    put_skew_pts = (put_iv - call_iv) * 100.0 if (not math.isnan(put_iv) and not math.isnan(call_iv)) else 0.0

    # --- ATM IV ---
    atm_iv = 0.0
    otm_strikes = [k for k, r in chain_iv if (r == "P" and k < forward) or (r == "C" and k > forward)]
    if otm_strikes:
        atm_k = min(otm_strikes, key=lambda k: abs(k - forward))
        atm_right = "P" if atm_k < forward else "C"
        atm_iv = chain_iv.get((atm_k, atm_right), 0.0)

    # --- Skew signal ---
    if put_skew_pts >= PUT_SKEW_EXTREME:
        skew_signal = "PUT_SKEW_EXTREME"
    elif put_skew_pts >= PUT_SKEW_WARN:
        skew_signal = "PUT_SKEW_ELEVATED"
    elif put_skew_pts <= -PUT_SKEW_WARN:
        skew_signal = "CALL_SKEWED"
    else:
        skew_signal = "FLAT"

    return SkewScan(
        ticker=ticker, spot=spot, forward=round(forward, 2),
        expiry=expiry, T_years=T_years,
        atm_iv_pct=round(atm_iv * 100.0, 2),
        put_skew_pts=round(put_skew_pts, 2),
        num_strikes_total=len(chain_iv),
        num_otm_strikes=len(otm_strikes),
        sabr_fit_success=sabr_success,
        sabr_alpha=sabr_alpha, sabr_rho=sabr_rho, sabr_nu=sabr_nu,
        sabr_rmse=sabr_rmse,
        fitter=fitter,
        rich_strikes=rich[:10], cheap_strikes=cheap[:10],
        skew_signal=skew_signal,
        timestamp=ts,
    )


def format_skew(scan: SkewScan) -> str:
    """One-line summary string."""
    if scan.error:
        return f"  {scan.ticker:6s} | SKEW: ERROR — {scan.error}"
    sabr_info = ""
    if scan.sabr_rho is not None:
        sabr_info = f" | SABR ρ={scan.sabr_rho:.2f} ν={scan.sabr_nu:.2f}"
    rich_n = len(scan.rich_strikes)
    cheap_n = len(scan.cheap_strikes)
    edge = ""
    if rich_n >= MIN_DEVIATION_STRIKES:
        edge += f" | RICH: {rich_n} strikes"
    if cheap_n >= MIN_DEVIATION_STRIKES:
        edge += f" | CHEAP: {cheap_n} strikes"
    return (
        f"  {scan.ticker:6s} | Skew: {scan.put_skew_pts:+.1f}pts "
        f"({scan.skew_signal}){sabr_info}{edge}"
    )


if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    scan = scan_skew(ticker)
    print(format_skew(scan))