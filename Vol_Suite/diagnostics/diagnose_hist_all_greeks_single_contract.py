#!/usr/bin/env python3
"""diagnose_hist_all_greeks_single_contract.py

Tests the SINGLE-CONTRACT historical all-greeks endpoint (root/exp/strike/
right, not the whole-chain "bulk" version) against api.potatohedge.com.

Background: ThetaData's own v2 docs show `hist/option/all_greeks` as a
single-contract endpoint with root/exp/strike/right/start_date/end_date/ivl
all as QUERY params (Jason's sample, on the local v2 Terminal port 25510).
But this project already confirmed the proxy doesn't use query-param style
anywhere -- `hist/option/trade_greeks` (same family: single-contract,
historical) works on the proxy ONLY in PATH-SEGMENT style:
    /api/theta/hist/option/trade_greeks/{root}/{exp}/{strike}/{right}?start_date=...&end_date=...
(confirmed 200, real tick data, in diagnose_trade_greeks_endpoint.py).

Hypothesis: `all_greeks`'s single-contract form follows the identical
path-segment pattern on this proxy -- just swap `trade_greeks` for
`all_greeks`, plus add `ivl` since all_greeks buckets into intervals rather
than returning raw per-trade ticks. If true, this is actually BETTER than
the trade_greeks fallback already confirmed working: pre-aggregated greeks
bars (e.g. one row per 15-min interval, ~26 rows/day) instead of raw ticks
(~1000+ rows/day) -- same per-contract call volume for a whole chain, much
less data to move/parse per call.

Usage:
    python diagnose_hist_all_greeks_single_contract.py SPY [YYYYMMDD-expiry] [strike] [C|P] [YYYYMMDD-date]
"""
import sys
from datetime import datetime, timedelta

from thetadata_client import ThetaDataController, strike_to_theta


def _pretty(label, resp):
    print(f"\n--- {label} ---")
    print(f"URL: {resp.request.url}")
    print(f"status: {resp.status_code}")
    body = resp.text
    if len(body) > 1500:
        print(f"body (first 1500 of {len(body)} chars, {len(body)} total):\n{body[:1500]}...")
    else:
        print(f"body:\n{body}")
    if resp.status_code == 200:
        try:
            data = resp.json()
            if isinstance(data, list):
                first = data[0] if data else None
                print(f"parsed: list, len={len(data)}" + (f", first row={first}" if data else ""))
            elif isinstance(data, dict):
                print(f"parsed: dict, keys={list(data.keys())}")
        except Exception as e:
            print(f"  (JSON parse failed: {e})")


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    ctl = ThetaDataController()

    if len(sys.argv) > 2:
        exp = sys.argv[2]
    else:
        exps = ctl.list_expirations(root)
        today = datetime.now().strftime("%Y%m%d")
        future = sorted(e for e in exps if e >= today)
        exp = future[0] if future else (exps[0] if exps else None)
        print(f"Using nearest expiration for {root}: {exp}")

    # NOTE: list_strikes() has a known double-scaling bug (divides an
    # already-dollar value by 1000 again) -- pass a real strike explicitly
    # (e.g. 745 for SPY) rather than relying on it, same caveat as
    # diagnose_trade_greeks_endpoint.py.
    strike = float(sys.argv[3]) if len(sys.argv) > 3 else 745.0
    right = sys.argv[4] if len(sys.argv) > 4 else "C"
    date = sys.argv[5] if len(sys.argv) > 5 else (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")

    k = strike_to_theta(strike)
    base = ctl.base_url
    client = ctl.client
    print(f"root={root} exp={exp} strike={strike} right={right} date={date}\n")

    # Same family of single-contract historical endpoints, per Jason's v2
    # doc samples (all on port 25510, root/exp/strike/right/start_date/
    # end_date/ivl as query params): all_greeks, greeks (a narrower/first-
    # order-only variant?), greeks_second_order, implied_volatility. Testing
    # all of them the same way -- path-segment style matching the
    # CONFIRMED-working hist/option/trade_greeks pattern -- since query-param
    # style has 404'd on every route tried on this proxy so far.
    endpoint_names = ["all_greeks", "greeks", "greeks_second_order", "implied_volatility"]

    for ep in endpoint_names:
        # 1. THE HYPOTHESIS: path-segment style, with ivl added (these are
        #    interval-bucketed, unlike trade_greeks which is inherently
        #    per-trade and needed no ivl).
        r1 = client.get(f"{base}/api/theta/hist/option/{ep}/{root}/{exp}/{k}/{right}",
                         params={"start_date": date, "end_date": date, "ivl": 900000})
        _pretty(f"{ep} -- path-segment, ivl=900000 (15-min bars)", r1)

        # 2. Same, no ivl (tick-level default per docs) -- in case ivl
        #    itself causes a problem on this specific route.
        r2 = client.get(f"{base}/api/theta/hist/option/{ep}/{root}/{exp}/{k}/{right}",
                         params={"start_date": date, "end_date": date})
        _pretty(f"{ep} -- path-segment, no ivl", r2)

        # 3. Query-param convention matching Jason's exact sample shape
        #    (root/exp/strike/right all as params) -- already expected to
        #    404 given everything else tested so far on this proxy, but
        #    confirming each specific route behaves the same, rules out a
        #    route-specific exception.
        r3 = client.get(f"{base}/api/theta/hist/option/{ep}",
                         params={"root": root, "exp": exp, "strike": k, "right": right,
                                 "start_date": date, "end_date": date, "ivl": 900000})
        _pretty(f"{ep} -- query-param convention (Jason's sample shape)", r3)

        # 4. First run of this script found greeks/greeks_second_order/
        #    implied_volatility all return a REAL, structured 400 (not a
        #    404) -- "OPRA extended-hours requests are dormant before
        #    2026-08-17 ET", flagging field "rth". That means the route
        #    IS live, it's just rejecting the implicit non-RTH-scoped
        #    request. Test with rth=true explicitly to see if that's the
        #    missing param (all_greeks needed no such flag and worked
        #    outright, so this may be specific to this narrower endpoint
        #    family).
        r4 = client.get(f"{base}/api/theta/hist/option/{ep}/{root}/{exp}/{k}/{right}",
                         params={"start_date": date, "end_date": date, "ivl": 900000, "rth": "true"})
        _pretty(f"{ep} -- path-segment, ivl=900000, rth=true", r4)

    print("\n\nIf any path-segment variant above returns 200 with real "
          "rows, we have a working single-contract source for that greek "
          "family -- same call-volume cost as the trade_greeks fallback "
          "(one call per strike/right per day) but pre-bucketed, not raw "
          "ticks, so much lighter to aggregate.")


if __name__ == "__main__":
    main()
