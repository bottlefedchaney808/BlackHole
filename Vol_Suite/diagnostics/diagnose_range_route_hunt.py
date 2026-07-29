#!/usr/bin/env python3
"""diagnose_range_route_hunt.py

FINDING SO FAR (diagnose_date_collapse.py, 2026-07-24):
`hist/option/all_greeks` returns exactly ONE day per call -- the `start_date`
-- and silently ignores `end_date`. Proof: a request for 20260706-20260723
returned 27 rows (one full session of 15-min bars) all dated 20260706.

That makes the current design unaffordable, not merely slow. One day per call
means 430 contracts x ~110 trading days = ~47,000 requests to backfill a
single expiry. At any polite concurrency that's hours, and it re-fetches the
same intraday bars we immediately throw away in _last_bar_per_date.

BUT: `hist/stock/eod` DOES honor a date range -- that's exactly why the price
history came back with 112 distinct dates in the same run that got 1 date of
greeks. So a range-honoring OPTION route very likely exists too; we just
haven't found its name. That's what this hunts for.

Two questions, in order of payoff:

  1. Is there a per-contract route returning ONE ROW PER DAY over a range?
     That's 430 calls per expiry instead of 47,000 -- and it's the shape
     _last_bar_per_date already wants, so nothing downstream changes.

  2. Is there a WHOLE-CHAIN route (bulk_hist) that works? That would be ~1
     call per expiry. Previously found broken for all_greeks, but it has
     never been tried for an `eod`-style route, and eod is a much cheaper
     thing for the upstream Terminal to serve than reconstructed greeks --
     which is the exact reason the whole-chain greeks route times out into a
     502 (see diagnose_bulk_hist_endpoint.py).

Anything returning 200 with >1 distinct date is the answer. Print it and stop.

Usage (from the project root):
    python -m diagnostics.diagnose_range_route_hunt
    python -m diagnostics.diagnose_range_route_hunt SPY 20261030 745 C 20260706 20260723
"""
import sys

from thetadata_client import ThetaDataController, strike_to_theta


def _summarize(ctl, url, params, label):
    """Fire one request and report how many DISTINCT DATES came back, which
    is the only number that matters here."""
    try:
        r = ctl._get(url, params=params)
    except Exception as e:
        print(f"  {label:52s} EXC {type(e).__name__}")
        return 0
    if r.status_code != 200:
        detail = ""
        if r.status_code == 404:
            detail = " (route exists? or just no data)"
        print(f"  {label:52s} {r.status_code}{detail}")
        return 0
    try:
        data = r.json()
    except Exception:
        print(f"  {label:52s} 200 but non-JSON")
        return 0
    rows = ctl._rows_from_any(data)
    if not rows:
        print(f"  {label:52s} 200 but no rows")
        return 0
    dates = {str(ctl._normalize_date(row)) for row in rows}
    dates.discard("None")
    flag = "  <<< RANGE HONORED" if len(dates) > 1 else ""
    print(f"  {label:52s} 200  rows={len(rows):5d}  dates={len(dates):3d}{flag}")
    if len(dates) > 1:
        ordered = sorted(dates)
        print(f"       {ordered[0]} .. {ordered[-1]}")
        print(f"       columns: {sorted(rows[0].keys())}")
    return len(dates)


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    exp = sys.argv[2] if len(sys.argv) > 2 else "20261030"
    strike = float(sys.argv[3]) if len(sys.argv) > 3 else 745.0
    right = sys.argv[4] if len(sys.argv) > 4 else "C"
    # Default to a window we KNOW has data: the contract was live from at
    # least 20260706, and 20260723 was the last closed day in the failing run.
    start = sys.argv[5] if len(sys.argv) > 5 else "20260706"
    end = sys.argv[6] if len(sys.argv) > 6 else "20260723"

    ctl = ThetaDataController()
    k = strike_to_theta(strike)
    base = f"/api/theta"
    contract = f"{root}/{exp}/{k}/{right}"
    rng = {"start_date": start, "end_date": end}

    print(f"{root} {exp} {strike}{right}   range {start}-{end}")
    print("Anything with dates>1 honors the range. That's the whole test.\n")

    # -- 0. Re-confirm the finding, and rule out "it's just this contract" --
    print("[0] CONFIRM the single-day behavior")
    _summarize(ctl, f"{base}/hist/option/all_greeks/{contract}",
               dict(rng, ivl=900000), "all_greeks 0706-0723 (expect 1 date)")
    _summarize(ctl, f"{base}/hist/option/all_greeks/{contract}",
               {"start_date": "20260710", "end_date": end, "ivl": 900000},
               "all_greeks 0710-0723 (expect only 0710)")
    print()

    # -- 1. Per-contract routes that might return one row per day -----------
    # `eod` is the shape we want and the shape hist/stock/eod already proves
    # the proxy can serve over a range.
    print("[1] PER-CONTRACT range routes (430 calls/expiry if one works)")
    for name, extra in [
        ("eod", {}),
        ("eod", {"ivl": 0}),
        ("ohlc", {}),
        ("quote", {}),
        ("trade", {}),
        ("open_interest", {}),
        ("all_greeks", {"ivl": 0}),
        ("all_greeks", {"ivl": 86400000}),      # one-day bucket in ms
        ("all_greeks", {"rth": "true", "ivl": 86400000}),
        ("greeks", {"ivl": 86400000, "rth": "true"}),
        ("trade_greeks", {}),
    ]:
        label = f"hist/option/{name}" + (f" {extra}" if extra else "")
        _summarize(ctl, f"{base}/hist/option/{name}/{contract}",
                   dict(rng, **extra), label)
    print()

    # -- 2. Whole-chain routes: ~1 call per expiry if any of these work -----
    print("[2] WHOLE-CHAIN routes (1 call/expiry -- the jackpot)")
    chain = f"{root}/{exp}"
    for name, extra in [
        ("eod", {}),
        ("ohlc", {}),
        ("open_interest", {}),
        ("all_greeks", {"ivl": 86400000}),
    ]:
        label = f"bulk_hist/option/{name}" + (f" {extra}" if extra else "")
        _summarize(ctl, f"{base}/bulk_hist/option/{name}/{chain}",
                   dict(rng, **extra), label)
    print()

    # -- 3. Sanity: the route we KNOW honors ranges -------------------------
    print("[3] CONTROL -- hist/stock/eod, known to honor ranges")
    _summarize(ctl, f"{base}/hist/stock/eod/{root}", rng, "hist/stock/eod")

    print("""
WHAT TO DO WITH THIS:
  * A per-contract route with dates>1  -> swap it into
    option_hist_all_greeks_single / _open_interest_single. Cost drops from
    ~47,000 requests per expiry to ~430, and _last_bar_per_date becomes a
    no-op rather than the thing discarding 26 of every 27 rows.
  * A whole-chain route with dates>1   -> even better; option_bulk_hist_*
    collapses back to a single call and the enumerate-then-fan-out design
    goes away entirely.
  * NOTHING honors a range             -> the per-contract-per-day loop is
    the only option, and Stage 3 should be re-scoped: fewer contracts (near
    the money only, where the gamma actually lives) rather than fewer days.
    A ~40-strike ATM window over 110 days is ~4,400 calls, which is
    affordable and loses very little, since far-OTM strikes contribute
    almost nothing to net gamma.
""")
    ctl.close()


if __name__ == "__main__":
    main()
