#!/usr/bin/env python3
"""diagnose_date_collapse.py

Run this first when you're back. One contract, one run, answers the question.

THE SYMPTOM (2026-07-24, SPY 90):
    [backtest_stage3] SPY 20261030 date coverage:
        gamma=1 iv=1 oi=1 close=112 -> 1 usable days

430 contracts requested over 20260211-20260723. 344 of them returned rows.
Zero errors. Price history came back with 112 distinct dates, so the range and
the calendar are fine. Yet the greeks and OI histories between them yielded
exactly ONE distinct date.

So rows are arriving and dates are being lost. There are only two ways that
happens, and they need completely different fixes:

  (A) The API is only returning one day per contract -- the start_date/end_date
      range is being ignored or overridden (an `ivl` interaction, a silently
      capped response, a param the proxy drops). Fix would be in the request.

  (B) The API is returning many days, and OUR parsing is collapsing them --
      the date column isn't where _normalize_date looks, so every row falls
      back to the same value or gets dropped, and _last_bar_per_date keys them
      all into one bucket. Fix would be in the parsing.

This prints the raw truth for a single contract so you can tell which. It does
NOT use _parse_rows or _last_bar_per_date for the raw dump -- the whole point
is to see what arrives BEFORE our code touches it.

Usage (from the project root):
    python -m diagnostics.diagnose_date_collapse
    python -m diagnostics.diagnose_date_collapse SPY 20261030 745 C 20260211 20260723
"""
import sys
from collections import Counter

from thetadata_client import ThetaDataController, strike_to_theta


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    exp = sys.argv[2] if len(sys.argv) > 2 else "20261030"
    strike = float(sys.argv[3]) if len(sys.argv) > 3 else 745.0
    right = sys.argv[4] if len(sys.argv) > 4 else "C"
    start = sys.argv[5] if len(sys.argv) > 5 else "20260211"
    end = sys.argv[6] if len(sys.argv) > 6 else "20260723"

    ctl = ThetaDataController()
    k = strike_to_theta(strike)
    print(f"{root} {exp} {strike}{right}   range {start}-{end}\n")

    try:
        # ---- 1. ONE raw request, no chunking, no parsing ------------------
        # A single 28-day window, which is exactly what one production chunk
        # asks for. If this comes back with one date, it's cause (A).
        for label, params in [
            ("greeks, 28d window, ivl=900000 (production settings)",
             {"start_date": "20260211", "end_date": "20260310", "ivl": 900000}),
            ("greeks, same window, NO ivl",
             {"start_date": "20260211", "end_date": "20260310"}),
            ("greeks, same window, ivl=0 (EOD-only bucket?)",
             {"start_date": "20260211", "end_date": "20260310", "ivl": 0}),
        ]:
            r = ctl._get(f"/api/theta/hist/option/all_greeks/{root}/{exp}/{k}/{right}",
                         params=params)
            print(f"--- {label}")
            print(f"    {r.request.url}")
            print(f"    status {r.status_code}")
            if r.status_code != 200:
                print(f"    body: {r.text[:200]}\n")
                continue
            data = r.json()
            if not isinstance(data, list) or len(data) < 2:
                print(f"    unexpected payload shape: {str(data)[:300]}\n")
                continue
            headers = data[0]
            rows = data[1:]
            print(f"    COLUMNS: {headers}")
            print(f"    rows returned: {len(rows)}")
            # Which column actually holds the date?
            date_cols = [h for h in headers
                         if any(t in str(h).lower() for t in ("date", "time", "created"))]
            print(f"    date-ish columns: {date_cols or 'NONE -- this is cause (B)'}")
            for col in date_cols:
                idx = headers.index(col)
                vals = [row[idx] for row in rows]
                distinct = sorted({str(v) for v in vals})
                print(f"      '{col}': {len(distinct)} distinct value(s)"
                      f"{' -> ' + str(distinct[:6]) if len(distinct) <= 6 else ''}")
                if len(distinct) > 6:
                    print(f"        first: {distinct[0]}   last: {distinct[-1]}")
            print(f"    first row: {dict(zip(headers, rows[0]))}")
            if len(rows) > 1:
                print(f"    last  row: {dict(zip(headers, rows[-1]))}")
            print()

        # ---- 2. What OUR pipeline makes of the same contract --------------
        # If step 1 shows many distinct dates and this shows one, the bug is
        # ours, and it's between _parse_rows and _last_bar_per_date.
        print("=" * 66)
        print("Now through the production path (chunking + parsing + collapse):")
        rows = ctl.option_hist_all_greeks_single(root, exp, k, right, start, end)
        print(f"  option_hist_all_greeks_single -> {len(rows)} rows")
        if rows:
            print(f"  keys on a row: {sorted(rows[0].keys())}")
            dates = Counter(str(r.get('date')) for r in rows)
            print(f"  distinct 'date' values: {len(dates)}")
            print(f"  most common: {dates.most_common(5)}")
            collapsed = ctl._last_bar_per_date(rows)
            print(f"  after _last_bar_per_date -> {len(collapsed)} rows "
                  f"({len({r['date'] for r in collapsed})} distinct dates)")

        print("\n  Same for open interest:")
        oi = ctl.option_hist_open_interest_single(root, exp, k, right, start, end)
        print(f"  option_hist_open_interest_single -> {len(oi)} rows")
        if oi:
            print(f"  keys on a row: {sorted(oi[0].keys())}")
            print(f"  distinct 'date' values: {len({str(r.get('date')) for r in oi})}")

        print("""
READING THIS:
  * Step 1 shows MANY distinct dates, step 2 shows one  -> cause (B), our
    parsing. Look at which column step 1 named vs what _normalize_date checks
    ('date', 'Date', 'created', 'datetime').
  * Step 1 shows ONE distinct date too                  -> cause (A), the
    request. Compare the three ivl variants above: if the no-ivl or ivl=0
    variant returns more dates, ivl is collapsing the range to a single day.
  * Step 1 returns ~28 rows all on the SAME date        -> the route is
    returning one day of intraday bars regardless of the range, and the
    production chunking needs to iterate DAYS, not 28-day windows.
""")
    finally:
        ctl.close()


if __name__ == "__main__":
    main()
