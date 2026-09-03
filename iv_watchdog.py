"""IV alert watchdog: spot + ATM IV vs 20d ATM IV baseline.

Uses ThetaData bulk_hist/option/eod_greeks (verified live route) for the
20-day baseline: daily median implied_vol of contracts near spot on the
~30d expiry. Current ATM IV from the same expiry snapshot.
"""
import sys, json, statistics
from datetime import datetime, timedelta

sys.path.insert(0, '.')
from shared.thetadata import ThetaDataController

TICKERS = sys.argv[1:] if len(sys.argv) > 1 else ['SPY', 'QQQ', 'AAPL', 'NVDA', 'MSFT', 'AMD', 'MU']
HIST_DAYS = 28      # ~20 trading days
MIN_BARS = 5        # min daily baseline observations
ATM_BAND = 0.10     # strikes within +-10% of spot count as ATM-ish
RATIO_ALERT = 1.5   # IV > 1.5x 20d avg -> ALERT
RATIO_NOTIFY = 0.67 # IV < 0.67x 20d avg -> NOTIFY (crush)

def pick_expiry(exps, days=30):
    target = datetime.now() + timedelta(days=days)
    return min(exps, key=lambda e: abs((datetime.strptime(e, '%Y%m%d') - target).days))

def main():
    td = ThetaDataController()
    today = datetime.now()
    start = (today - timedelta(days=HIST_DAYS)).strftime('%Y%m%d')
    end = today.strftime('%Y%m%d')
    results = []
    for t in TICKERS:
        rec = {'ticker': t}
        try:
            spot = td.fetch_spot_price(t)
            rec['spot'] = spot
            if not spot:
                rec['error'] = 'no spot'
                results.append(rec); continue
            exps = td.list_expirations(t)
            if not exps:
                rec['error'] = 'no expirations'
                results.append(rec); continue
            exp = pick_expiry(exps)
            rec['exp'] = exp

            # current ATM IV from ~30d expiry snapshot
            rows = td.option_bulk_greeks(t, exp) or []
            best = None
            for r in rows:
                try:
                    k = float(r.get('strike', 0)) / 1000.0
                    iv = float(r.get('implied_vol') or r.get('impliedVolatility') or 0)
                except (TypeError, ValueError):
                    continue
                if iv <= 0:
                    continue
                d = abs(k - float(spot))
                if best is None or d < best[0]:
                    best = (d, k, iv, str(r.get('right', ''))[:1].upper())
            if not best:
                rec['error'] = 'no ATM row'
                results.append(rec); continue
            _, atm_k, atm_iv, atm_right = best
            rec['atm_strike'] = atm_k
            rec['atm_iv'] = atm_iv
            rec['atm_right'] = atm_right

            # 20d baseline: per-day ATM IV (strikes near THAT day's underlying),
            # same right as the current ATM reading, same expiry
            hist = td.option_bulk_hist_eod_greeks(t, exp, start, end)
            bydate = {}
            for r in hist:
                try:
                    k = float(r.get('strike', 0)) / 1000.0
                    iv = float(r.get('implied_vol') or 0)
                    up = float(r.get('underlying_price') or 0)
                    d = str(r.get('date') or '')
                    # date comes back as int YYYYMMDD -> '20260901'
                    if len(d) == 8:
                        d = f'{d[:4]}-{d[4:6]}-{d[6:8]}'
                except (TypeError, ValueError):
                    continue
                if iv <= 0 or not d or len(d) != 10 or up <= 0:
                    continue
                if str(r.get('right', '')).upper() != atm_right:
                    continue
                if abs(k - up) / up <= ATM_BAND:
                    bydate.setdefault(d, []).append(iv)
            daily_med = {d: statistics.median(v) for d, v in bydate.items()}
            rec['hist_bars'] = len(daily_med)
            if len(daily_med) >= MIN_BARS:
                rec['baseline_ok'] = True
                vals = [daily_med[d] for d in sorted(daily_med)]
                rec['avg20'] = statistics.mean(vals)
                rec['pct'] = sum(1 for v in vals if v <= atm_iv) / len(vals) * 100.0
            else:
                rec['baseline_ok'] = False
                rec['hist_dates'] = sorted(daily_med)[-5:]
            results.append(rec)
        except Exception as e:
            rec['error'] = f'{type(e).__name__}: {e}'
            results.append(rec)
        print(f"[{t}] " + json.dumps(rec, default=str), flush=True)
    td.close()
    print("__DONE__")
    print(json.dumps(results, default=str))

if __name__ == '__main__':
    main()
