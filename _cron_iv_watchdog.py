"""IV alert watchdog quick scan (HV-from-EOD method) - cron morning briefing."""
import math, time
import numpy as np
from shared.thetadata import ThetaDataController
from datetime import datetime, timedelta

tickers = ["SPY", "QQQ", "AAPL", "NVDA", "MSFT", "AMD", "MU",
           "VST", "NKE", "NBIS", "LUNR", "CSCO", "DELL"]

td = ThetaDataController()
now = datetime.now()
end = now.strftime('%Y%m%d')
start = (now - timedelta(days=35)).strftime('%Y%m%d')

print(f"TICKER   SPOT      ATM IV   20d HV   RATIO   STATUS")
print("=" * 60)
for t in tickers:
    try:
        spot = td.fetch_spot_price(t)
        exps = td.list_expirations(t)
        if not spot or not exps:
            print(f"{t:8s}  no options / no spot — skip")
            continue
        rows = td.option_bulk_greeks(t, exps[0])
        window = max(5, float(spot) * 0.02)
        cur_iv = None
        for r in rows:
            k = float(r['strike']) / 1000.0
            iv = r.get('implied_vol')
            if abs(k - float(spot)) < window and r['right'] == 'C' and iv and float(iv) > 0:
                cur_iv = float(iv) * 100.0
                break
        if not cur_iv:
            print(f"{t:8s}  {float(spot):8.2f}  IV not found (snapshot)")
            continue

        eod_rows = td.hist_stock_eod(t, start, end)
        closes = [float(r['close']) for r in eod_rows if r.get('close') and float(r['close']) > 0]
        if len(closes) < 15:
            print(f"{t:8s}  {float(spot):8.2f}  {cur_iv:7.1f}%  n/a       —")
            continue
        log_rets = np.diff(np.log(closes[-25:]))
        hv_20d = float(np.std(log_rets) * np.sqrt(252) * 100)
        ratio = cur_iv / hv_20d if hv_20d > 0 else 1.0
        if ratio > 1.5:
            status = "🔴 ALERT"
        elif ratio > 1.3:
            status = "🟡 Elevated"
        elif cur_iv > 150:
            status = "🟡 Raw extreme"
        elif ratio < 0.7:
            status = "🟢 Vol cheap"
        else:
            status = "✓ normal"
        print(f"{t:8s}  {float(spot):8.2f}  {cur_iv:7.1f}%  {hv_20d:6.1f}%  {ratio:5.2f}x   {status}")
    except Exception as e:
        print(f"{t:8s}  ERROR: {str(e)[:60]}")
    time.sleep(0.5)

td.close()
print("DONE")
