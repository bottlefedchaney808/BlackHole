#!/usr/bin/env python3
"""Probe: does ThetaData serve historical greeks/OI/spot for a PAST expiry at a PRIOR as-of?"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from thetadata_client import ThetaDataController

# Load .env
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
_env = os.path.join(_root, ".env")
if os.path.exists(_env):
    for line in open(_env, encoding="utf-8").read().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            k = k.strip(); v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v

td = ThetaDataController()
# Prior as_of 2026-07-31 (Fri). Candidate short expiries: next Mon 20260803(dte3), Wed 20260805, Fri 20260807.
# Lookback 20260717..20260731 (14 days).
tests = [
    ("AAPL", "20260803", "20260717", "20260731"),
    ("AAPL", "20260805", "20260717", "20260731"),
    ("AAPL", "20260807", "20260717", "20260731"),
    ("SPY",  "20260803", "20260717", "20260731"),
    ("SPY",  "20260805", "20260717", "20260731"),
    ("SPY",  "20260807", "20260717", "20260731"),
    ("AMD",  "20260803", "20260717", "20260731"),
]
for tk, exp, s, e in tests:
    try:
        gr = td.option_bulk_hist_eod_greeks(tk, exp, s, e)
        print(f"{tk} {exp} lookback[{s}..{e}] greeks_rows={len(gr)}", flush=True)
    except Exception as ex:
        print(f"{tk} {exp} greeks ERROR: {type(ex).__name__}: {ex}", flush=True)
# stock spot
try:
    sp = td.hist_stock_eod("AAPL", "20260717", "20260731")
    print("AAPL spot rows:", len(sp), [r.get("date") or r.get("created") for r in sp][:15])
except Exception as ex:
    print("spot ERROR:", type(ex).__name__, ex)
# OI
try:
    oi = td.option_bulk_hist_oi_by_day("AAPL", "20260803", "20260717", "20260731")
    print("AAPL OI rows:", len(oi), [r.get("date") for r in oi][:15])
except Exception as ex:
    print("OI ERROR:", type(ex).__name__, ex)
td.close()
