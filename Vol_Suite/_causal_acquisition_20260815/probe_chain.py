#!/usr/bin/env python3
"""Verify the historical (day, expiry) intraday chain endpoint works for an EXPIRED
expiry (critical assumption for the whole acquisition). Also test OI + EOD routes.
"""
import os, sys, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from shared.thetadata import ThetaDataController
_env = r"C:/Users/bottl/FinancialDevelopment/.env"
if os.path.exists(_env):
    for line in open(_env, encoding="utf-8").read().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        k, _, v = line.partition("="); k = k.strip(); v = v.strip().strip('"').strip("'")
        if k and k not in os.environ: os.environ[k] = v
os.environ["THETADATA_HIST_CONCURRENCY"] = "1"
IVL = 600000

def strike_to_theta(k):
    return int(round(k * 1000))

def main():
    day = sys.argv[1] if len(sys.argv) > 1 else "20260202"
    exp = sys.argv[2] if len(sys.argv) > 2 else "20260206"  # next Friday
    tk = sys.argv[3] if len(sys.argv) > 3 else "SPY"
    ctl = ThetaDataController()
    try:
        # stock intraday
        r = ctl._get_with_retry(f"/api/theta/hist/stock/ohlc/{tk}",
                                params={"start_date": day, "end_date": day})
        rows = ctl._parse_rows(r)
        print(f"[{day} {tk}] stock/ohlc status={r.status_code} rows={len(rows)}")
        if rows:
            spots = {int(float(x.get("ms_of_day",0) or 0)): float(x["close"]) for x in rows
                     if x.get("close") not in (None,"","0",0)}
            ms = sorted(spots)
            print(f"  ms range {ms[0]}..{ms[-1]} n={len(ms)} open~{spots[ms[0]]:.2f} close~{spots[ms[-1]]:.2f}")
            base = round(spots[ms[0]] / 5.0) * 5.0
            grid = [strike_to_theta(base + i*5.0) for i in range(-6,7)]
        else:
            grid = [strike_to_theta(600)]
        # chain all_greeks for a few strikes C+P
        k0 = grid[len(grid)//2]
        r = ctl._get_with_retry(f"/api/theta/hist/option/all_greeks/{tk}/{exp}/{k0}/C",
                                params={"start_date": day, "end_date": day, "ivl": IVL})
        crows = ctl._parse_rows(r)
        print(f"  all_greeks C {tk}/{exp}/{k0}: status={r.status_code} rows={len(crows)}")
        if crows:
            print(f"    sample keys: {list(crows[0].keys())}")
            print(f"    sample row: { {k:crows[0].get(k) for k in ('ms_of_day','implied_vol','gamma','delta','vanna','open_interest')} }")
        r = ctl._get_with_retry(f"/api/theta/hist/option/open_interest/{tk}/{exp}/{k0}/C",
                                params={"start_date": day, "end_date": day})
        orows = ctl._parse_rows(r)
        print(f"  OI {tk}/{exp}/{k0}/C: status={r.status_code} rows={len(orows)}")
        if orows: print(f"    OI last: {orows[-1]}")
        # EOD greeks
        r = ctl._get_with_retry(f"/api/theta/bulk_hist/option/eod_greeks/{tk}/{exp}",
                                params={"start_date": day, "end_date": day})
        erows = ctl._parse_rows(r)
        print(f"  eod_greeks {tk}/{exp}: status={r.status_code} rows={len(erows)}")
        if erows:
            print(f"    keys: {list(erows[0].keys())}")
    finally:
        ctl.close()

if __name__ == "__main__":
    main()
