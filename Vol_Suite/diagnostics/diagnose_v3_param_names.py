#!/usr/bin/env python3
"""diagnose_v3_param_names.py

Testing the proxy operator's correction rather than arguing with it.

HIS CLAIM (2026-07-24): use `exp=*`, and our client predates ThetaData v3 --
"a lot of params got shuffled".

WHY THAT FITS BETTER THAN MY EXPLANATION: we send v2 names
(root/exp/start_date/end_date/ivl). The v3 docs use different ones
(symbol/expiration/interval). If the server is v3 underneath and silently
IGNORES query params it doesn't recognize, then `end_date` gets dropped and
the response covers only `start_date` -- which is exactly the symptom
diagnose_date_collapse.py found and which I attributed to the route being
single-day by design. A silently-ignored parameter and a route that only
ever returns one day look identical from outside. That is on me, not the
proxy.

Worth being precise about what was and wasn't established earlier:
diagnose_v3_via_proxy.py tested v3 PATHS (/v3/option/history/...) and got
404s. From that I concluded "the proxy is v2-shaped, path-segment only."
That conclusion does not follow: the proxy can perfectly well expose its own
path scheme while forwarding v3-NAMED query params underneath. We never
tested v3 param names on the proxy's own working path. This does.

The test is unambiguous because we already know the ground truth: for
SPY/20261030/745000/C, the window 20260706-20260723 contains ~14 trading
days, and `hist/option/eod` returns all 14. Any greeks variant that returns
more than 1 date has found the right parameter name.

Usage (from the project root):
    python -m diagnostics.diagnose_v3_param_names
"""
import sys

from thetadata_client import ThetaDataController, strike_to_theta

ROOT, EXP, STRIKE, RIGHT = "SPY", "20261030", 745.0, "C"
START, END = "20260706", "20260723"
EXPECTED = 14          # what hist/option/eod returns for this window


def probe(ctl, label, path, params):
    try:
        r = ctl._get(path, params=params)
    except Exception as e:
        print(f"  {label:56s} EXC {type(e).__name__}")
        return 0
    if r.status_code != 200:
        print(f"  {label:56s} {r.status_code}")
        return 0
    try:
        rows = ctl._rows_from_any(r.json())
    except Exception:
        print(f"  {label:56s} 200 non-JSON")
        return 0
    if not rows:
        print(f"  {label:56s} 200 empty")
        return 0
    dates = {str(ctl._normalize_date(row)) for row in rows}
    dates.discard("None")
    n = len(dates)
    mark = ""
    if n > 1:
        mark = f"  <<< RANGE HONORED ({n}/{EXPECTED})"
    print(f"  {label:56s} 200  rows={len(rows):5d}  dates={n:3d}{mark}")
    if n > 1:
        o = sorted(dates)
        print(f"       {o[0]} .. {o[-1]}")
        print(f"       URL: {r.request.url}")
    return n


def main():
    ctl = ThetaDataController()
    k = strike_to_theta(STRIKE)
    base = "/api/theta"
    contract = f"{ROOT}/{EXP}/{k}/{RIGHT}"
    hits = []

    print(f"{ROOT} {EXP} {STRIKE}{RIGHT}   {START}-{END}")
    print(f"Ground truth: hist/option/eod returns {EXPECTED} dates for this window.")
    print("Anything with dates>1 means the parameter name was the problem.\n")

    # -- 1. v3 param names on the path we KNOW returns data ----------------
    # Every combination of what the range endpoints might be called. `ivl`
    # is kept where the v2 name is used and swapped for `interval` where the
    # v3 name is, since mixing conventions is exactly what a shuffled API
    # would reject or ignore.
    print("[1] v3-style parameter names on hist/option/all_greeks")
    variants = [
        ("v2 baseline (what we send today)",
         {"start_date": START, "end_date": END, "ivl": 900000}),
        ("interval instead of ivl",
         {"start_date": START, "end_date": END, "interval": "1d"}),
        ("interval=1d + no ivl",
         {"start_date": START, "end_date": END, "interval": "1d"}),
        ("expiration= as a param too",
         {"expiration": EXP, "start_date": START, "end_date": END, "interval": "1d"}),
        ("symbol= + expiration= (full v3 naming)",
         {"symbol": ROOT, "expiration": EXP, "start_date": START,
          "end_date": END, "interval": "1d"}),
        ("from/to instead of start_date/end_date",
         {"from": START, "to": END, "ivl": 900000}),
        ("start/end",
         {"start": START, "end": END, "ivl": 900000}),
        ("date_from/date_to",
         {"date_from": START, "date_to": END, "ivl": 900000}),
        ("start_date only (control -- proves 1 date is the default)",
         {"start_date": START, "ivl": 900000}),
    ]
    for label, params in variants:
        if probe(ctl, label, f"{base}/hist/option/all_greeks/{contract}", params) > 1:
            hits.append(("hist/option/all_greeks", label, params))
    print()

    # -- 2. exp=* -- the operator's actual instruction ----------------------
    # Read two ways, because "use exp=*" could mean either. Both are cheap.
    print("[2] exp=* -- as a path segment and as a query param")
    for label, path, params in [
        ("bulk_hist all_greeks, '*' as the exp path segment",
         f"{base}/bulk_hist/option/all_greeks/{ROOT}/*",
         {"start_date": START, "end_date": END, "ivl": 900000}),
        ("bulk_hist all_greeks, exp=* as a query param",
         f"{base}/bulk_hist/option/all_greeks/{ROOT}",
         {"exp": "*", "start_date": START, "end_date": END, "ivl": 900000}),
        ("bulk_hist all_greeks, root+exp=* both as params",
         f"{base}/bulk_hist/option/all_greeks",
         {"root": ROOT, "exp": "*", "start_date": START, "end_date": END, "ivl": 900000}),
        ("bulk_hist all_greeks, v3 naming + expiration=*",
         f"{base}/bulk_hist/option/all_greeks",
         {"symbol": ROOT, "expiration": "*", "start_date": START,
          "end_date": END, "interval": "1d"}),
        ("bulk_hist open_interest, exp=* query param",
         f"{base}/bulk_hist/option/open_interest/{ROOT}",
         {"exp": "*", "start_date": START, "end_date": END}),
        ("bulk_hist eod, exp=* query param",
         f"{base}/bulk_hist/option/eod/{ROOT}",
         {"exp": "*", "start_date": START, "end_date": END}),
        ("exp=* on the SINGLE-contract greeks route",
         f"{base}/hist/option/all_greeks/{ROOT}",
         {"exp": "*", "strike": k, "right": RIGHT,
          "start_date": START, "end_date": END, "ivl": 900000}),
    ]:
        if probe(ctl, label, path, params) > 1:
            hits.append((path, label, params))
    print()

    # -- 3. Does the whole-chain greeks route work with exp=* + a range? ----
    # This is the real prize: one call for every strike AND every day. If it
    # returns >1 date AND hundreds of rows, the entire enumerate-then-fan-out
    # design in thetadata_client.py becomes unnecessary.
    print("[3] Whole-chain + full-range in one call (the prize)")
    for label, params in [
        ("bulk_hist all_greeks {root}/{exp}, v2 params",
         {"start_date": START, "end_date": END, "ivl": 900000}),
        ("bulk_hist all_greeks {root}/{exp}, interval=1d",
         {"start_date": START, "end_date": END, "interval": "1d"}),
        ("bulk_hist all_greeks {root}/{exp}, v3 naming",
         {"symbol": ROOT, "expiration": EXP, "start_date": START,
          "end_date": END, "interval": "1d"}),
    ]:
        if probe(ctl, label, f"{base}/bulk_hist/option/all_greeks/{ROOT}/{EXP}",
                 params) > 1:
            hits.append(("bulk_hist/option/all_greeks", label, params))

    print("\n" + "=" * 70)
    if hits:
        print("FOUND IT. These honored the range:\n")
        for path, label, params in hits:
            print(f"  {path}")
            print(f"    {label}")
            print(f"    {params}\n")
        print("Next: switch thetadata_client.py to this form. If a whole-chain")
        print("variant works, option_bulk_hist_* collapses to a single call and")
        print("the derived-IV path in implied_vol.py becomes optional rather")
        print("than necessary -- vendor greeks would be affordable again.")
    else:
        print("Nothing here honored the range. Before concluding anything:")
        print("ask him for ONE working curl for greeks history over a multi-day")
        print("range. A single concrete example settles this faster than any")
        print("amount of guessing at parameter names -- and given I've already")
        print("misread this proxy once, guessing is the wrong tool.")
    ctl.close()


if __name__ == "__main__":
    main()
