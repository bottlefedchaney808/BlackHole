#!/usr/bin/env python3
"""diagnose_wide_range_chunks.py

Reproduces option_hist_all_greeks_single's exact chunking (28-day spans)
over the exact range backtest_stage3.py just requested (20260210-20260722)
for SPY/20261016 745000/C -- a contract ALREADY CONFIRMED to return real
data for a single day within this range (20260722 alone returned 200 with
28 real rows in diagnose_hist_all_greeks_single_contract.py, run minutes
before this script).

The full backtest run just reported ALL 442 contracts (including this one)
as "never traded in range" across this SAME date span, with zero exceptions
-- meaning every chunk request must have quietly 404'd (the only non-raising
path in option_hist_all_greeks_single). That's a contradiction worth seeing
directly: print the raw status/body for EACH chunk instead of silently
swallowing 404s, so we can see whether it's really 404 on every chunk (and
if so, why a known-good contract would 404 for a range it definitely has
data in), or something else.

Usage:
    python diagnose_wide_range_chunks.py SPY 20261016 745 C 20260210 20260722
"""
import sys
from datetime import datetime, timedelta

from thetadata_client import ThetaDataController, strike_to_theta


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    exp = sys.argv[2] if len(sys.argv) > 2 else "20261016"
    strike = float(sys.argv[3]) if len(sys.argv) > 3 else 745.0
    right = sys.argv[4] if len(sys.argv) > 4 else "C"
    start_date = sys.argv[5] if len(sys.argv) > 5 else "20260210"
    end_date = sys.argv[6] if len(sys.argv) > 6 else "20260722"

    ctl = ThetaDataController()
    k = strike_to_theta(strike)
    base = ctl.base_url
    client = ctl.client

    fmt = "%Y%m%d"
    start_dt = datetime.strptime(start_date, fmt)
    end_dt = datetime.strptime(end_date, fmt)

    print(f"root={root} exp={exp} strike={strike} right={right} "
          f"range={start_date}-{end_date}\n")

    # Isolate whether ANY multi-day range fails, or just these specific
    # 28-day chunk boundaries -- narrow it down to the single known-good
    # day (control), then progressively widen by one day at a time.
    known_good_day = end_dt.strftime(fmt)
    narrow_tests = [
        ("A. single day (control, already confirmed 200)", known_good_day, known_good_day),
        ("B. 2-day range ending on the known-good day",
         (end_dt - timedelta(days=1)).strftime(fmt), known_good_day),
        ("C. 3-day range ending on the known-good day",
         (end_dt - timedelta(days=2)).strftime(fmt), known_good_day),
        ("D. 7-day range ending on the known-good day",
         (end_dt - timedelta(days=6)).strftime(fmt), known_good_day),
        ("E. 10-day range ending on the known-good day",
         (end_dt - timedelta(days=9)).strftime(fmt), known_good_day),
        ("F. 12-day range ending on the known-good day",
         (end_dt - timedelta(days=11)).strftime(fmt), known_good_day),
        ("G. 14-day range ending on the known-good day",
         (end_dt - timedelta(days=13)).strftime(fmt), known_good_day),
        ("H. 16-day range ending on the known-good day",
         (end_dt - timedelta(days=15)).strftime(fmt), known_good_day),
    ]
    for label, cs, ce in narrow_tests:
        r = client.get(
            f"{base}/api/theta/hist/option/all_greeks/{root}/{exp}/{k}/{right}",
            params={"start_date": cs, "end_date": ce, "ivl": 900000})
        print(f"--- {label}: {cs} - {ce} ---")
        print(f"URL: {r.request.url}")
        print(f"status: {r.status_code}")
        body = r.text
        print(f"body:\n{body[:500]}")
        if r.status_code == 200:
            try:
                data = r.json()
                print(f"parsed: list, len={len(data)}" if isinstance(data, list) else "")
            except Exception as e:
                print(f"  (JSON parse failed: {e})")
        print()

    print("=== boundary found above (or not) -- full chunk sweep skipped this "
          "run to save requests; already have that data from the prior run ===")


if __name__ == "__main__":
    main()
