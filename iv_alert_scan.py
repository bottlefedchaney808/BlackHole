import sys, json, time, math
sys.path.insert(0, '.')
from shared.thetadata import ThetaDataController

DEFAULTS = ["SPY", "QQQ", "AAPL", "NVDA", "MSFT", "AMD", "MU"]

# 1. Extract unique tickers from the manifest
tickers = set(DEFAULTS)
try:
    with open('sentiment-scanner/data/exports/highlighted_ticker_packs/latest_manifest.json') as f:
        manifest = json.load(f)
    for p in manifest.get('packs', []):
        for t in p.get('tickers', []):
            tickers.add(t)
    manifest_src = 'manifest+defaults'
except Exception as e:
    manifest_src = f'manifest FAILED ({e}) -> defaults only'
print(f"TICKERS({len(tickers)}) from {manifest_src}: {sorted(tickers)}", flush=True)

# 2. Fetch spot + 30-DTE ATM IV + 20d RV baseline
from datetime import date, timedelta
today = date.today()
today_int = int(today.strftime('%Y%m%d'))
target = int((today + timedelta(days=30)).strftime('%Y%m%d'))

results = []
td = ThetaDataController()
try:
    for t in sorted(tickers):
        row = {'ticker': t}
        try:
            spot = td.fetch_spot_price(t); time.sleep(0.3)
            row['spot'] = round(float(spot), 2)
        except Exception as e:
            row['spot_err'] = str(e)[:80]; results.append(row); continue
        try:
            exps = td.list_expirations(t); time.sleep(0.3)
            good = sorted([(abs(int(e)-target), e) for e in exps if int(e) > today_int + 14])
            if not good:
                row['err'] = 'no expiry'; results.append(row); continue
            exp = good[0][1]
            row['exp'] = exp
            rows = td.option_bulk_greeks(t, exp); time.sleep(0.5)
            window = max(5, float(spot) * 0.02)
            iv = None
            for r in rows or []:
                k = float(r['strike']) / 1000.0
                if abs(k - float(spot)) < window and r['right'] == 'C' and r.get('implied_vol') and float(r['implied_vol']) > 0:
                    iv = float(r['implied_vol']) * 100
                    break
            if iv is None:
                row['err'] = 'no ATM IV'; results.append(row); continue
            row['iv'] = round(iv, 2)
        except Exception as e:
            row['err'] = str(e)[:120]; results.append(row); continue
        # 3. 20d realized vol baseline from EOD closes
        try:
            start_str = (today - timedelta(days=45)).strftime('%Y%m%d')
            end_str = today.strftime('%Y%m%d')
            eod = td.hist_stock_eod(t, start_str, end_str); time.sleep(0.3)
            closes = [float(x['close']) for x in (eod or []) if x.get('close')]
            closes = closes[-25:]
            if len(closes) >= 11:
                log_rets = [math.log(closes[i]/closes[i-1]) for i in range(1, len(closes))]
                # use last ~20 trading days for 20d avg
                rv20 = float(__import__('statistics').pstdev(log_rets[-20:]) * math.sqrt(252) * 100)
                row['rv20'] = round(rv20, 2)
                row['ratio'] = round(iv / rv20, 2) if rv20 > 0 else None
                row['n_closes'] = len(closes)
            else:
                row['err'] = 'insufficient eod data'
        except Exception as e:
            row['err'] = str(e)[:120]
        results.append(row)
        print(f"  {t}: spot={row.get('spot')} iv={row.get('iv')} rv20={row.get('rv20')} ratio={row.get('ratio')} err={row.get('err')}", flush=True)
finally:
    td.close()

with open('iv_alert_scan_results.json', 'w') as f:
    json.dump(results, f, indent=2)
print("DONE")
