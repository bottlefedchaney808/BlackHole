"""Max Pain / OI Pin Detector — finds the strike with highest option cost at expiry.

Theory: the market tends to pin near the max pain strike — the price where
option buyers (long calls + long puts) lose the most money collectively.
This is the strike that maximizes the total dollar cost of OI at expiry.

Key outputs:
  - max_pain_strike: the pinning level
  - pain_concentration: how much OI dollar-value is concentrated there
  - price_vs_pain: distance from spot to max pain
  - pin_action: whether price is near max pain (within 1-2 strikes)
"""

from typing import List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timezone
import numpy as np

from scanner.options_scanner_base import get_td, strike_from_theta

# Max pain proximity: within this many strikes of spot is "near pin"
PIN_STRIKE_RADIUS = 2
# Minimum OI for a strike to count
MIN_OI = 10
# Calendar days per year -- same convention as Vol_Suite's expiry_selector
# (DEFAULT_A = 365), so a caller-supplied expiry and a self-selected one
# report T_years on the same basis.
DAYS_PER_YEAR = 365.0
EXPIRY_DATE_FMT = "%Y%m%d"


@dataclass
class MaxPainStrike:
    strike: float
    pain_value: float  # total dollar cost at expiry
    call_oi: int
    put_oi: int


@dataclass
class MaxPainScan:
    ticker: str
    spot: float
    expiry: str
    T_years: float
    max_pain_strike: float
    max_pain_value: float
    second_pain_strike: float
    price_vs_pain_pct: float  # (spot - max_pain) / spot as %
    near_pin: bool             # spot within PIN_STRIKE_RADIUS of max pain
    pain_profile: List[MaxPainStrike]
    num_strikes: int
    timestamp: str
    error: Optional[str] = None


def _compute_pain_for_strike(K: float, strikes: np.ndarray,
                              call_oi: np.ndarray, put_oi: np.ndarray) -> float:
    """Total dollar cost of OI at expiry if spot settles at K.

    Sum over all strikes: call_oi * max(0, strike - K) + put_oi * max(0, K - strike)
    """
    call_payout = call_oi * np.maximum(strikes - K, 0.0)
    put_payout = put_oi * np.maximum(K - strikes, 0.0)
    return float(np.sum(call_payout + put_payout))


def _resolve_given_expiry(expiry: str) -> Tuple[str, float]:
    """Normalize a caller-supplied expiry to (YYYYMMDD, T_years).

    Accepts ``YYYYMMDD`` or ``YYYY-MM-DD``. Raises ValueError on anything
    else so the caller can surface it as a scan error rather than silently
    falling back to a different expiry than the run is analyzing.
    """
    exp_norm = str(expiry).strip().replace("-", "")
    exp_date = datetime.strptime(exp_norm, EXPIRY_DATE_FMT).date()
    today = datetime.now(timezone.utc).date()
    T_years = max((exp_date - today).days, 0) / DAYS_PER_YEAR
    return exp_norm, T_years


def scan_max_pain(ticker: str, expiry: Optional[str] = None, *,
                  garch_cond_vol_pct: Optional[float] = None,
                  fair_vol_pct: Optional[float] = None) -> MaxPainScan:
    """Run max pain detection for one ticker.

    Parameters
    ----------
    ticker : str
    expiry : str, optional
        Expiry to scan, as ``YYYYMMDD`` or ``YYYY-MM-DD``. When given (e.g.
        the expiry the rest of the orchestrator run is analyzing), it is used
        directly and the internal nearest-~30DTE selection is skipped.

    Returns
    -------
    MaxPainScan
    """
    td = get_td()
    ts = datetime.now(timezone.utc).isoformat()

    try:
        spot = td.fetch_spot_price(ticker)
    except Exception as e:
        return MaxPainScan(
            ticker=ticker, spot=0.0, expiry="", T_years=0.0,
            max_pain_strike=0.0, max_pain_value=0.0,
            second_pain_strike=0.0, price_vs_pain_pct=0.0,
            near_pin=False, pain_profile=[], num_strikes=0,
            timestamp=ts, error=str(e),
        )
    if spot <= 0:
        return MaxPainScan(
            ticker=ticker, spot=0.0, expiry="", T_years=0.0,
            max_pain_strike=0.0, max_pain_value=0.0,
            second_pain_strike=0.0, price_vs_pain_pct=0.0,
            near_pin=False, pain_profile=[], num_strikes=0,
            timestamp=ts, error="no_spot",
        )

    # Caller-supplied expiry wins; otherwise self-select the nearest ~30DTE.
    try:
        if expiry:
            expiry, T_years = _resolve_given_expiry(expiry)
        else:
            from scanner.options_scanner_base import VolSuiteImporter
            vsi = VolSuiteImporter()
            expiry, T_years = vsi.expiry_selector.nearest_expiry(
                td, ticker, target_years=0.083
            )
    except Exception as e:
        return MaxPainScan(
            ticker=ticker, spot=spot, expiry="", T_years=0.0,
            max_pain_strike=0.0, max_pain_value=0.0,
            second_pain_strike=0.0, price_vs_pain_pct=0.0,
            near_pin=False, pain_profile=[], num_strikes=0,
            timestamp=ts, error=str(e),
        )

    if not expiry:
        return MaxPainScan(
            ticker=ticker, spot=spot, expiry="", T_years=0.0,
            max_pain_strike=0.0, max_pain_value=0.0,
            second_pain_strike=0.0, price_vs_pain_pct=0.0,
            near_pin=False, pain_profile=[], num_strikes=0,
            timestamp=ts, error="no_expiry",
        )

    # --- Fetch OI ---
    # Try the snapshot endpoint first; if it 404s or comes back empty,
    # fall back to the historical endpoint (bulk_hist/option/open_interest)
    # which has OI for expiries that haven't settled into the snapshot yet.
    try:
        oi_rows = td.option_bulk_oi(ticker, expiry)
    except Exception:
        oi_rows = []
    if not oi_rows:
        try:
            oi_rows = td.option_bulk_oi_latest(ticker, expiry)
        except Exception as e:
            return MaxPainScan(
                ticker=ticker, spot=spot, expiry=expiry, T_years=T_years,
                max_pain_strike=0.0, max_pain_value=0.0,
                second_pain_strike=0.0, price_vs_pain_pct=0.0,
                near_pin=False, pain_profile=[], num_strikes=0,
                timestamp=ts, error=str(e),
            )

    # Build strike -> OI maps
    call_oi_map: dict = {}
    put_oi_map: dict = {}
    for row in oi_rows:
        try:
            k = strike_from_theta(int(row["strike"]))
            right = row["right"]
            oi = int(row["open_interest"])
            if oi < MIN_OI:
                continue
            if right == "C":
                call_oi_map[k] = call_oi_map.get(k, 0) + oi
            elif right == "P":
                put_oi_map[k] = put_oi_map.get(k, 0) + oi
        except (ValueError, KeyError, TypeError):
            continue

    all_strikes = sorted(set(call_oi_map.keys()) | set(put_oi_map.keys()))
    if len(all_strikes) < 3:
        return MaxPainScan(
            ticker=ticker, spot=spot, expiry=expiry, T_years=T_years,
            max_pain_strike=0.0, max_pain_value=0.0,
            second_pain_strike=0.0, price_vs_pain_pct=0.0,
            near_pin=False, pain_profile=[], num_strikes=len(all_strikes),
            timestamp=ts, error="too_few_strikes",
        )

    strikes_arr = np.array(all_strikes)
    call_oi_arr = np.array([call_oi_map.get(k, 0) for k in all_strikes])
    put_oi_arr = np.array([put_oi_map.get(k, 0) for k in all_strikes])

    # Pain at each strike
    pain_values = np.array([
        _compute_pain_for_strike(K, strikes_arr, call_oi_arr, put_oi_arr)
        for K in all_strikes
    ])

    # Max pain = strike with highest pain value
    max_idx = int(np.argmax(pain_values))
    max_pain = float(strikes_arr[max_idx])
    max_val = float(pain_values[max_idx])

    # Second highest
    pain_sorted = np.argsort(pain_values)[::-1]
    second_idx = pain_sorted[1] if len(pain_sorted) > 1 else max_idx
    second_pain = float(strikes_arr[second_idx])

    # Price vs pain
    price_vs_pain = ((spot - max_pain) / spot) * 100.0 if spot > 0 else 0.0

    # Near pin?
    spot_idx = int(np.argmin(np.abs(strikes_arr - spot)))
    near_pin = abs(spot_idx - max_idx) <= PIN_STRIKE_RADIUS

    # Profile
    profile = [
        MaxPainStrike(
            strike=float(strikes_arr[i]),
            pain_value=float(pain_values[i]),
            call_oi=int(call_oi_arr[i]),
            put_oi=int(put_oi_arr[i]),
        )
        for i in range(len(all_strikes))
    ]

    return MaxPainScan(
        ticker=ticker, spot=spot, expiry=expiry, T_years=T_years,
        max_pain_strike=max_pain, max_pain_value=max_val,
        second_pain_strike=second_pain,
        price_vs_pain_pct=round(price_vs_pain, 2),
        near_pin=near_pin,
        pain_profile=profile,
        num_strikes=len(all_strikes),
        timestamp=ts,
    )


def format_max_pain(scan: MaxPainScan) -> str:
    """One-line summary string."""
    if scan.error:
        return f"  {scan.ticker:6s} | PAIN: ERROR — {scan.error}"
    pin = " ◀ PIN" if scan.near_pin else ""
    return (
        f"  {scan.ticker:6s} | MaxPain: ${scan.max_pain_strike:.2f} "
        f"(val: ${scan.max_pain_value:,.0f}) | "
        f"Spot: ${scan.spot:.2f} ({scan.price_vs_pain_pct:+.1f}%){pin} | "
        f"2nd: ${scan.second_pain_strike:.2f}"
    )


if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    scan = scan_max_pain(ticker)
    print(format_max_pain(scan))