#!/usr/bin/env python3
"""R10.3 causal-arm acquisition helper probes — check proxy health + expiry listing.

Quick, low-request probe before the full ≥29-day sequential run.
"""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from shared.thetadata import ThetaDataController  # noqa: E402

# Load main-tree .env creds if not already set
_env = r"C:/Users/bottl/FinancialDevelopment/.env"
if os.path.exists(_env):
    for line in open(_env, encoding="utf-8").read().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip(); v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v

os.environ["THETADATA_HIST_CONCURRENCY"] = "1"


def probe(day: str, ticker: str):
    ctl = ThetaDataController()
    try:
        # stock intraday
        r = ctl._get_with_retry(f"/api/theta/hist/stock/ohlc/{ticker}",
                                params={"start_date": day, "end_date": day})
        rows = ctl._parse_rows(r)
        print(f"{day} {ticker} stock/ohlc: status={r.status_code} rows={len(rows)}")
        # list expirations
        exps = ctl.list_expirations(ticker)
        print(f"  list_expirations: {len(exps)} total; first 6={exps[:6]}")
        # nearest expiries 1-10 dte
        as_of = dt.datetime.strptime(day, "%Y%m%d").date()
        cand = []
        for e in exps:
            try:
                dd = (dt.datetime.strptime(str(e), "%Y%m%d").date() - as_of).days
            except Exception:
                continue
            if 1 <= dd <= 10:
                cand.append((dd, e))
        cand.sort()
        print(f"  expiries 1-10 DTE: {cand[:8]}")
    finally:
        ctl.close()


if __name__ == "__main__":
    day = sys.argv[1] if len(sys.argv) > 1 else "20260202"
    for tk in ("SPY", "QQQ"):
        probe(day, tk)
