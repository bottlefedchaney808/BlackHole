#!/usr/bin/env python3
"""diagnose_bulk_hist_endpoint.py

Standalone diagnostic script (NOT a pytest test -- hits the real network,
needs real credentials) to isolate why ThetaDataController.option_bulk_hist_greeks()
502s against api.potatohedge.com's `/api/theta/bulk_hist/option/all_greeks/{root}/{exp}`
route, even for a single day's range (already ruled out request-sizing as the
sole cause).

Background (2026-07-23 investigation):
  - ThetaData's own docs for the bulk historical greeks endpoint show ROOT and
    EXP as QUERY params (not path segments), plus a required `ivl` param
    (default 0 = tick-level, likely huge/slow if omitted), returning a nested
    {"header":..., "response":[{"ticks":[[...]], "contract":{...}}]} shape.
  - The CURRENT code (thetadata_client.py's option_bulk_hist_greeks) uses
    ROOT/EXP as PATH segments (matching the confirmed-working *snapshot*
    bulk greeks route's convention) and doesn't pass `ivl` at all, expecting
    a flat list-of-lists response.
  - Jason's hypothesis (2026-07-23): the proxy (api.potatohedge.com) probably
    isn't "reshaping" anything on purpose -- ThetaData Terminal exposes a
    REST API and a separate streaming API, and the working snapshot call and
    the broken historical call may simply be hitting two different backends
    with two different native conventions, not one proxy doing inconsistent
    translation. This script tests that directly rather than guessing again.

This script fires several request SHAPES at the same root/exp/single-day
range and prints status code + raw body (truncated) for each, so the actual
failure mode (which shape 502s, which 4xx's with a real message, which
succeeds) is visible directly.

Usage:
    python diagnose_bulk_hist_endpoint.py SPY [YYYYMMDD-expiry] [YYYYMMDD-date]

Needs THETADATA_CF_ACCESS_CLIENT_ID / THETADATA_CF_ACCESS_CLIENT_SECRET set
(env var or .env), same as the rest of the suite.
"""
import sys
from datetime import datetime, timedelta

from thetadata_client import ThetaDataController


def _pretty(label, resp):
    print(f"\n--- {label} ---")
    print(f"URL: {resp.request.url}")
    print(f"status: {resp.status_code}")
    print(f"content-type: {resp.headers.get('content-type', '')}")
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

    date = sys.argv[3] if len(sys.argv) > 3 else (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    print(f"root={root} exp={exp} date={date} (single-day range, isolates "
          f"convention from request-sizing)\n")

    base = ctl.base_url
    client = ctl.client

    # 0. Control: the CONFIRMED-working snapshot bulk greeks call, for
    #    side-by-side comparison of what a healthy response looks like.
    r0 = client.get(f"{base}/api/theta/bulk_snapshot/option/all_greeks/{root}/{exp}")
    _pretty("0. CONTROL: bulk_snapshot/option/all_greeks (known-working)", r0)

    # 1. Current code's exact call: path-segment root/exp, no ivl.
    r1 = client.get(f"{base}/api/theta/bulk_hist/option/all_greeks/{root}/{exp}",
                     params={"start_date": date, "end_date": date})
    _pretty("1. CURRENT CODE: path-segment root/exp, no ivl", r1)

    # 2. Same path-segment convention, but with ivl explicitly set to a
    #    coarse interval (15 min) instead of omitted (which may default to
    #    tick-level and time out server-side -- ThetaData's docs sample URL
    #    uses ivl=900000).
    r2 = client.get(f"{base}/api/theta/bulk_hist/option/all_greeks/{root}/{exp}",
                     params={"start_date": date, "end_date": date, "ivl": 900000})
    _pretty("2. path-segment root/exp, ivl=900000 (15-min bars)", r2)

    # 3. ThetaData's OWN documented convention: root/exp as QUERY params, not
    #    path segments, still with ivl. This is Jason's hypothesis test -- if
    #    THIS is what actually works, the proxy isn't doing uniform
    #    path-segment translation for bulk_hist the way it does for
    #    bulk_snapshot, and the two routes may be backed differently.
    r3 = client.get(f"{base}/api/theta/bulk_hist/option/all_greeks",
                     params={"root": root, "exp": exp, "start_date": date,
                             "end_date": date, "ivl": 900000})
    _pretty("3. QUERY-PARAM convention (root/exp/ivl all as params) -- "
            "matches ThetaData's raw docs", r3)

    # 4. Query-param convention, tick-level (ivl omitted), matching the docs'
    #    literal default in case ivl itself isn't the issue and the
    #    path/query convention is.
    r4 = client.get(f"{base}/api/theta/bulk_hist/option/all_greeks",
                     params={"root": root, "exp": exp, "start_date": date, "end_date": date})
    _pretty("4. QUERY-PARAM convention, no ivl (tick-level default)", r4)

    print("\n\nSummary: compare status codes above. Whichever variant "
          "returns 200 (or a clean 4xx with a real error message instead of "
          "a bare 502) tells us which convention this specific route family "
          "actually expects.")


if __name__ == "__main__":
    main()
