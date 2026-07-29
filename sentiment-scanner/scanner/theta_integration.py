"""ThetaData integration: connects narrative scores with options chain data."""
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional
from scanner.theta_controller import ThetaDataController, strike_from_theta, strike_to_theta

def get_nearest_expiry(td: ThetaDataController, ticker: str, target_days: int = 30):
    try:
        exps = td.list_expirations(ticker)
        if not exps: return None, 0.0
        today = datetime.now()
        target_date = today + timedelta(days=target_days)
        best = min(exps, key=lambda e: abs((datetime.strptime(e, "%Y%m%d") - target_date).days))
        actual_days = max((datetime.strptime(best, "%Y%m%d") - today).days, 1)
        return best, actual_days / 365.0
    except: return None, 0.0

def build_oi_snapshot(ticker: str, td: Optional[ThetaDataController] = None) -> Dict:
    close_td = False
    if td is None: td = ThetaDataController(); close_td = True
    try:
        spot = td.fetch_spot_price(ticker)
        if spot == 0.0: return {"error": "no_spot_price", "ticker": ticker}
        expiry, T = get_nearest_expiry(td, ticker, 30)
        if not expiry: return {"error": "no_expiry", "ticker": ticker, "spot": spot}
        greeks = td.option_bulk_greeks(ticker, expiry)
        calls, puts = {}, {}
        for row in greeks:
            k = int(row['strike'])  # theta format (cents*1000)
            right = row['right']
            iv = row.get('implied_vol')
            bid, ask = row.get('bid'), row.get('ask')
            mid = (float(bid)+float(ask))/2.0 if bid and ask and float(bid)>0 else 0.0
            entry = {"strike": strike_from_theta(k), "iv": float(iv) if iv else None, "mid": mid}
            if right == "C": calls[k] = entry
            elif right == "P": puts[k] = entry
        all_strikes = list(calls.keys()) + list(puts.keys())
        if not all_strikes: return {"error": "no_strikes", "ticker": ticker, "spot": spot}
        atm_k = min(all_strikes, key=lambda k: abs(strike_from_theta(k) - spot))
        atm_iv = calls.get(atm_k,{}).get("iv") or puts.get(atm_k,{}).get("iv") or 0.0
        # Calculate skew (OTM put IV vs OTM call IV)
        otm_puts = [k for k in puts.keys() if strike_from_theta(k) < spot]
        otm_calls = [k for k in calls.keys() if strike_from_theta(k) > spot]
        skew = 0.0
        if otm_puts and otm_calls:
            p_k = max(otm_puts, key=lambda k: strike_from_theta(k))
            c_k = min(otm_calls, key=lambda k: strike_from_theta(k))
            p_iv = puts[p_k].get("iv",0) or 0
            c_iv = calls[c_k].get("iv",0) or 0
            skew = float(p_iv) - float(c_iv)
        return {"ticker": ticker, "spot": spot, "expiry": expiry, "T_years": round(T,4),
                "atm_iv": float(atm_iv) if atm_iv else 0.0,
                "skew_vol_pts": round(skew*100, 2),
                "convexity_premium": abs(round(skew*100, 2)),
                "num_strikes": len(all_strikes),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "oi_change_pct": 0.0, "call_oi": 0, "put_oi": 0, "total_oi": 0,
                "otm_oi_ratio": 0.0, "put_call_oi_ratio": 0.0}
    finally:
        if close_td: td.close()
