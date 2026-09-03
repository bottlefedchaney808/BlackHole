import sys, json, time, math, statistics
sys.path.insert(0, '.')
from shared.thetadata import ThetaDataController
from datetime import date, timedelta

today = date.today()
CRUSH_CANDIDATES = ['ANF','DKS','FN','KLAR','MRNA','MRK','NBIS','OMER','TEM','WMT']

td = ThetaDataController()
try:
    for t in CRUSH_CANDIDATES:
        try:
            start_str = (today - timedelta(days=45)).strftime('%Y%m%d')
            end_str = today.strftime('%Y%m%d')
            eod = td.hist_stock_eod(t, start_str, end_str); time.sleep(0.3)
            closes = [float(x['close']) for x in (eod or []) if x.get('close')]
            # recent 10-15 closes RV to confirm whether low vol is real
            if len(closes) >= 12:
                rec = closes[-11:]
                log_rets = [math.log(rec[i]/rec[i-1]) for i in range(1, len(rec))]
                rv_rec = statistics.pstdev(log_rets) * math.sqrt(252) * 100
                print(f"{t}: n={len(closes)} last_close={closes[-1]:.2f} RV_recent10d={rv_rec:.2f}%")
            else:
                print(f"{t}: insufficient ({len(closes)})")
        except Exception as e:
            print(f"{t}: ERR {str(e)[:100]}")
finally:
    td.close()
