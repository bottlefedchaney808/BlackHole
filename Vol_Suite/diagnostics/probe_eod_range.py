#!/usr/bin/env python3
"""probe_eod_range.py

Follow-up to probe_eod_endpoint.py, which established that
/api/theta/hist/stock/eod/{root} is a valid route serving real data for a
~6-week window, while the suite's normal 2-year request 502s.

That points at request size, not path/auth/format. This walks the window
outward to find where it breaks, and times each call so a timeout signature
is visible (failures creeping up on latency, then dying) as opposed to a hard
server-side row cap (fast, clean rejection).

Run:  python probe_eod_range.py [TICKER]
"""
import sys
import time
from datetime import date, timedelta

from thetadata_client import ThetaDataController

TICKER = (sys.argv[1] if len(sys.argv) > 1 else "SPY").upper()

# End on a recent weekday, then walk the start date backwards.
END = date(2026, 7, 10)
WINDOWS = [
    ("1 week", 7),
    ("1 month", 30),
    ("3 months", 91),
    ("6 months", 182),
    ("1 year", 365),
    ("18 months", 548),
    ("2 years  [what the suite requests]", 730),
    ("5 years", 1826),
    ("11.5 years  [what GARCH requests]", 4200),
]


def main():
    print(f"Range-size probe for {TICKER} stock EOD history")
    print(f"End date fixed at {END:%Y%m%d}; start date walked backwards.\n")
    try:
        td = ThetaDataController()
    except RuntimeError as e:
        print(f"Could not build client: {e}")
        return

    print(f"  {'window':<38} {'status':<8} {'secs':<7} {'rows':<7} note")
    print("  " + "-" * 78)

    for label, days in WINDOWS:
        start = END - timedelta(days=days)
        params = {"start_date": start.strftime("%Y%m%d"),
                  "end_date": END.strftime("%Y%m%d")}
        t0 = time.time()
        try:
            r = td._get(f"/api/theta/hist/stock/eod/{TICKER}", params=params)
            elapsed = time.time() - t0
            rows = ""
            note = ""
            if r.status_code == 200:
                try:
                    data = r.json()
                    rows = str(max(len(data) - 1, 0))  # first element is the header row
                except Exception:
                    note = "200 but unparseable body"
            else:
                note = (r.text or "")[:60].replace("\n", " ")
            print(f"  {label:<38} {r.status_code:<8} {elapsed:<7.1f} {rows:<7} {note}")
        except Exception as e:
            elapsed = time.time() - t0
            print(f"  {label:<38} {'ERR':<8} {elapsed:<7.1f} {'':<7} {type(e).__name__}: {e}")

    td.close()

    print("\nHow to read this:")
    print("  Latency climbing, then 502 at some width  -> upstream timeout. Fix by")
    print("     chunking requests in hist_stock_eod and stitching the results.")
    print("  Clean fast failure at a specific width    -> server-side row/range cap.")
    print("     Same fix, but chunk size should sit just under the cap.")
    print("  All windows 200                           -> size isn't the trigger;")
    print("     suspect time-of-day (ThetaData regenerates its EOD report at 17:15")
    print("     ET, and the failing run started at 17:15) or a transient outage.")


if __name__ == "__main__":
    main()
