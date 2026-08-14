import sys, time, math
sys.path.insert(0, '.')
import numpy as np
from shared.thetadata import ThetaDataController
from datetime import datetime, timedelta

TICKERS = ['VST', 'MCD', 'REPL', 'CRMD', 'UCO']  # from latest_manifest.json top pack

td = ThetaDataController()
now = datetime.now()
end = now.strftime('%Y%m%d')
start = (now - timedelta(days=40)).strftime('%Y%m%d')

for t in TICKERS:
    try:
        spot_raw = td.fetch_spot_price(t)
        if not spot_raw:
            print(f'{t} ERROR: no spot')
            time.sleep(0.3)
            continue
        spot = float(spot_raw)
        exps = td.list_expirations(t)
        if not exps:
            print(f'{t} ERROR: no expirations')
            time.sleep(0.3)
            continue
        rows = td.option_bulk_greeks(t, exps[0])
        window = max(5.0, spot * 0.02)
        cur_iv = None
        for r in (rows or []):
            try:
                k = float(r.get('strike', 0)) / 1000.0
                iv = r.get('implied_vol')
                right = r.get('right', '')
                if abs(k - spot) < window and right == 'C' and iv and float(iv) > 0:
                    cur_iv = float(iv) * 100.0
                    break
            except Exception:
                continue
        if not cur_iv:
            print(f'{t} ERROR: no ATM IV (spot={spot:.2f})')
            time.sleep(0.3)
            continue
        # 20d HV baseline from EOD closes (fast, avoids 502-prone greeks history)
        hv_20d = None
        try:
            eod_rows = td.hist_stock_eod(t, start, end)
            closes = [float(r['close']) for r in eod_rows if r.get('close') and float(r['close']) > 0]
            if len(closes) >= 21:
                log_rets = np.diff(np.log(closes[-25:]))
                hv_20d = float(np.std(log_rets) * math.sqrt(252) * 100)
        except Exception as e:
            print(f'{t} WARN: hist_eod failed: {e}')
        if hv_20d and hv_20d > 0:
            ratio = cur_iv / hv_20d
            print(f'{t} spot={spot:.2f} iv={cur_iv:.1f}% hv20={hv_20d:.1f}% ratio={ratio:.2f}x')
        else:
            print(f'{t} spot={spot:.2f} iv={cur_iv:.1f}% hv20=NA')
    except Exception as e:
        print(f'{t} ERROR: {e}')
    time.sleep(0.4)

td.close()
print('DONE')
