#!/usr/bin/env python3
"""diagnose_v3_local_terminal.py

Tests ThetaData's own v3 REST API, served directly by a locally-running
Theta Terminal v3 process (http://127.0.0.1:25503) -- NOT the
api.potatohedge.com proxy this project's thetadata_client.py normally talks
to. Relevant because the Theta Terminal v3 MCP-server setup instructions
Jason pasted reference this exact port -- if that Terminal process is
already running, this endpoint is reachable right now with zero extra setup.

Why this matters: docs.thetadata.us (the CURRENT v3 doc site, distinct from
the older http-docs.thetadata.us pages checked earlier in this
investigation) shows `/v3/option/history/greeks/eod` as a whole-chain-
capable, EOD-bucketed historical greeks endpoint -- exactly the data shape
backtest_stage3.py and replication_reference.py need (one row per
strike/right per day, incl. gamma) -- using QUERY params (symbol,
expiration, strike=* default, right=both default, start_date, end_date),
not path segments. This is a completely different, native surface from
api.potatohedge.com's broken bulk_hist/option/all_greeks proxy route
(confirmed 502 in diagnose_bulk_hist_endpoint.py) -- if Theta Terminal v3 is
running locally, this sidesteps that proxy bug entirely rather than needing
someone else to fix it.

IMPORTANT: this only works if Theta Terminal v3 is actually running ON THE
SAME MACHINE this script runs on (it binds to localhost, no Cloudflare
Access credentials needed, unlike the proxy). If Theta Terminal isn't
running, every request below will fail to CONNECT (not 404/502) -- start
Theta Terminal first if so.

Usage:
    python diagnose_v3_local_terminal.py SPY [YYYYMMDD-expiry] [YYYYMMDD-date]
"""
import sys
from datetime import datetime, timedelta

import httpx

V3_BASE = "http://127.0.0.1:25503"


def _pretty(label, resp_or_exc):
    print(f"\n--- {label} ---")
    if isinstance(resp_or_exc, Exception):
        print(f"REQUEST FAILED: {type(resp_or_exc).__name__}: {resp_or_exc}")
        print("(if this is a connection error, Theta Terminal v3 likely "
              "isn't running on this machine right now)")
        return
    resp = resp_or_exc
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
                shown = list(first.keys()) if isinstance(first, dict) else first
                print(f"parsed: list, len={len(data)}" + (f", first row={shown}" if data else ""))
            elif isinstance(data, dict):
                print(f"parsed: dict, keys={list(data.keys())}")
        except Exception as e:
            print(f"  (JSON parse failed: {e})")


def _get(client, path, params):
    try:
        return client.get(f"{V3_BASE}{path}", params=params, timeout=30.0)
    except Exception as e:
        return e


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    exp = sys.argv[2] if len(sys.argv) > 2 else "20260723"
    date = sys.argv[3] if len(sys.argv) > 3 else (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    print(f"root={root} exp={exp} date={date}\n"
          f"Testing local Theta Terminal v3 REST API at {V3_BASE} directly "
          f"(bypassing api.potatohedge.com entirely).\n")

    client = httpx.Client()

    # 1. Whole-chain EOD greeks history (strike=*, right=both are the
    #    documented defaults) for the same single date that 502'd through
    #    the proxy's bulk_hist/option/all_greeks route.
    r1 = _get(client, "/v3/option/history/greeks/eod",
              {"symbol": root, "expiration": exp, "start_date": date, "end_date": date})
    _pretty("1. v3 option/history/greeks/eod -- whole chain, single day", r1)

    # 2. Same endpoint, JSON format explicit (docs default is csv).
    r2 = _get(client, "/v3/option/history/greeks/eod",
              {"symbol": root, "expiration": exp, "start_date": date, "end_date": date, "format": "json"})
    _pretty("2. same, format=json explicit", r2)

    # 3. The finer-grained, Pro-tier /all endpoint at a coarse interval, for
    #    comparison -- NOT what we need for EOD-style backtesting, but worth
    #    confirming it also resolves in case entitlement (not shape) turns
    #    out to be the deciding factor.
    r3 = _get(client, "/v3/option/history/greeks/all",
              {"symbol": root, "expiration": exp, "date": date, "interval": "1h"})
    _pretty("3. v3 option/history/greeks/all -- intraday interval, for comparison", r3)

    # 4. Same /all endpoint, multi-day range + 10m interval -- matches
    #    Jason's exact sample shape (docs.thetadata.us sample URL uses
    #    interval=10m over a multi-day start_date/end_date range, not the
    #    single `date` param used in #3). Confirms the multi-day path works
    #    the way the docs show it, not just the single-day form.
    week_start = (datetime.strptime(date, "%Y%m%d") - timedelta(days=3)).strftime("%Y%m%d")
    r4 = _get(client, "/v3/option/history/greeks/all",
              {"symbol": root, "expiration": exp, "start_date": week_start,
               "end_date": date, "interval": "10m"})
    _pretty("4. v3 option/history/greeks/all -- multi-day range, interval=10m", r4)

    # 5. Second-order greeks history (vanna, charm, vomma, veta, vera) --
    #    this project's design doc has wanted 2nd-order greeks history for a
    #    while (options_chain_scanner.py already has a snapshot-only 2nd-
    #    order path); if this v3 endpoint works it's a direct historical
    #    counterpart, no more guessing at bulk_snapshot analogies.
    r5 = _get(client, "/v3/option/history/greeks/second_order",
              {"symbol": root, "expiration": exp, "start_date": week_start,
               "end_date": date, "interval": "1h"})
    _pretty("5. v3 option/history/greeks/second_order -- multi-day, interval=1h", r5)

    print("\n\nIf #1/#2 return 200 with real per-strike rows, this is a "
          "clean replacement for the broken bulk_hist/option/all_greeks "
          "proxy route -- point the historical pulls' "
          "get_or_fetch_greeks_history() at this instead, bypassing "
          "api.potatohedge.com entirely for this data need. #4 confirms the "
          "multi-day form (what a real 90-day lookback will actually use) "
          "works the same way. #5 tells us whether 2nd-order greeks history "
          "is available the same way, a separate but related want. If every "
          "request fails to CONNECT, Theta Terminal v3 isn't running on "
          "this machine right now -- start it first, then rerun.")


if __name__ == "__main__":
    main()
