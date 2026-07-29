#!/usr/bin/env python3
"""diagnose_trade_greeks_endpoint.py

Standalone diagnostic (real network, real credentials -- not a pytest test)
evaluating Jason's 2026-07-23 question: is `hist/option/trade_greeks` (and/or
the whole-chain `bulk_hist/option/all_trade_greeks`) a viable data source for
the seed+accumulate historical construction (replication_reference.py) and
backtest_stage3.py, as an alternative/supplement to the still-blocked
`bulk_hist/option/all_greeks`?

Jason's framing: "this is trade greeks which kind of applies to us building
like we do from a point forward, this is gives the greeks for all trades" --
i.e. a per-TRADE (tick-level) greeks feed rather than a per-day EOD greeks
feed. This script checks:
  1. Whether bulk_hist/option/all_trade_greeks (whole chain -- flagged as
     "confirmed real, Pro tier" in earlier DEALER_POSITIONING_V2_DESIGN.md
     research) actually returns for a single day/single expiry without a 502.
  2. Whether hist/option/trade_greeks (single contract, per the doc page
     Jason linked) works, and what shape it actually returns -- since if it's
     tick-level trade-by-trade, it may be far larger per contract per day
     than one EOD greeks row, which matters for whether it's genuinely
     *simpler* to build from or just differently-shaped and heavier.
  3. Both path-segment and query-param request conventions for each, same
     open question as diagnose_bulk_hist_endpoint.py.

Usage:
    python diagnose_trade_greeks_endpoint.py SPY [YYYYMMDD-expiry] [strike] [C|P] [YYYYMMDD-date]
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
                print(f"parsed: list, len={len(data)}" + (f", first row={data[0]}" if data else ""))
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
        if not exp:
            print(f"No expirations found for {root}; pass one explicitly.")
            return
        print(f"Using nearest expiration for {root}: {exp}")

    if len(sys.argv) > 3:
        strike = float(sys.argv[3])
    else:
        spot = ctl.fetch_spot_price(root)
        strikes = ctl.list_strikes(root, exp)
        strike = min(strikes, key=lambda k: abs(k - spot)) if strikes else spot
        print(f"Using nearest-ATM strike for {root}: {strike} (spot~{spot})")

    right = sys.argv[4] if len(sys.argv) > 4 else "C"
    date = sys.argv[5] if len(sys.argv) > 5 else (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")

    k = strike_to_theta(strike)
    base = ctl.base_url
    client = ctl.client
    print(f"root={root} exp={exp} strike={strike} right={right} date={date}\n")

    # 1. Whole-chain bulk historical TRADE greeks -- path-segment convention,
    #    matching this project's existing style, single day.
    r1 = client.get(f"{base}/api/theta/bulk_hist/option/all_trade_greeks/{root}/{exp}",
                     params={"start_date": date, "end_date": date})
    _pretty("1. bulk_hist/option/all_trade_greeks (whole chain, path-segment)", r1)

    # 2. Same, query-param convention (matches ThetaData's raw docs pattern
    #    seen on the other bulk_hist pages) in case path-segment is again
    #    the wrong shape for this route too.
    r2 = client.get(f"{base}/api/theta/bulk_hist/option/all_trade_greeks",
                     params={"root": root, "exp": exp, "start_date": date, "end_date": date})
    _pretty("2. bulk_hist/option/all_trade_greeks (query-param convention)", r2)

    # 3. Single-contract hist TRADE greeks (the page Jason just linked) --
    #    needs strike+right, path-segment style matching this project's
    #    existing single-contract call (option_snapshot_quote).
    r3 = client.get(f"{base}/api/theta/hist/option/trade_greeks/{root}/{exp}/{k}/{right}",
                     params={"start_date": date, "end_date": date})
    _pretty("3. hist/option/trade_greeks (single contract, path-segment)", r3)

    # 4. Single-contract, query-param convention.
    r4 = client.get(f"{base}/api/theta/hist/option/trade_greeks",
                     params={"root": root, "exp": exp, "strike": k, "right": right,
                             "start_date": date, "end_date": date})
    _pretty("4. hist/option/trade_greeks (query-param convention)", r4)

    print("\n\nSummary: #1/#2 tell us whether the whole-chain trade-greeks "
          "route is any healthier than all_greeks was. #3/#4 tell us "
          "whether the single-contract route works and, if so, whether its "
          "response is tick-level (many rows per contract per day -- heavier "
          "than EOD greeks) or already time-bucketed. That distinction is "
          "what decides whether this is actually simpler for seed+accumulate "
          "or just a different shape needing its own aggregation step.")


if __name__ == "__main__":
    main()
