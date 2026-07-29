#!/usr/bin/env python3
"""Bare connectivity test against api.potatohedge.com — run this locally.

Not part of the app; just isolates whether the proxy/Terminal path is up
right now, independent of any of the variance-swap or dealer-positioning
code paths.
"""
import os
import httpx

from thetadata_client import _load_dotenv_once

_load_dotenv_once()

client_id = os.environ.get("THETADATA_CF_ACCESS_CLIENT_ID")
client_secret = os.environ.get("THETADATA_CF_ACCESS_CLIENT_SECRET")

if not client_id or not client_secret:
    raise SystemExit("Missing THETADATA_CF_ACCESS_CLIENT_ID / THETADATA_CF_ACCESS_CLIENT_SECRET (check .env)")

headers = {
    "CF-Access-Client-Id": client_id,
    "CF-Access-Client-Secret": client_secret,
}

urls = [
    "https://api.potatohedge.com/api/theta/list/expirations/MSFT",
    "https://api.potatohedge.com/api/theta/bulk_snapshot/option/all_greeks/MSFT/20260821",
    "https://api.potatohedge.com/api/theta/hist/stock/eod/MSFT?start_date=20260701&end_date=20260719",
]

for url in urls:
    print(f"GET {url}")
    try:
        r = httpx.get(url, headers=headers, timeout=20.0)
        print(f"status: {r.status_code}")
        print(f"body  : {r.text[:500]}")
    except Exception as e:
        print(f"request failed: {type(e).__name__}: {e}")
    print()

# Now test the actual 2-year range that was failing, through the real
# (now-paginated) client method rather than a raw single call.
print("--- testing hist_stock_eod('NVDA', 20240719, 20260719) via ThetaDataController (paginated) ---")
from thetadata_client import ThetaDataController
import time

td = ThetaDataController()
t0 = time.time()
try:
    rows = td.hist_stock_eod("NVDA", "20240719", "20260719")
    print(f"OK: {len(rows)} rows in {time.time() - t0:.1f}s")
    if rows:
        print(f"first: {rows[0]}")
        print(f"last : {rows[-1]}")
except Exception as e:
    print(f"FAILED after {time.time() - t0:.1f}s: {type(e).__name__}: {e}")
td.close()
