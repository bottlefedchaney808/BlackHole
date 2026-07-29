#!/usr/bin/env python3
"""probe_eod_endpoint.py

Diagnostic for the api.potatohedge.com proxy. Answers two questions:

  1. Are the Cloudflare Access credentials still valid? (control request against
     an endpoint the suite is known to use successfully)
  2. Which path/parameter shape, if any, serves stock EOD history?

Only `hist_stock_eod` fails in a normal suite run -- every other ThetaData call
goes through fine with the same credentials against the same host -- so this
probes the one broken route rather than assuming the whole proxy is down.

Run:  python probe_eod_endpoint.py [TICKER]
Prints status codes only; no credentials are echoed.
"""
import sys

from thetadata_client import ThetaDataController

TICKER = (sys.argv[1] if len(sys.argv) > 1 else "SPY").upper()
START_COMPACT, END_COMPACT = "20260601", "20260710"
START_DASHED, END_DASHED = "2026-06-01", "2026-07-10"

# (label, path, params). The current client uses the first history variant.
CANDIDATES = [
    ("CONTROL  snapshot/stock/quote (known-good route)",
     f"/api/theta/snapshot/stock/quote/{TICKER}", None),

    ("CONTROL  list/expirations (known-good route)",
     f"/api/theta/list/expirations/{TICKER}", None),

    ("v1  hist/stock/eod/{root}, compact dates  [CURRENT CLIENT]",
     f"/api/theta/hist/stock/eod/{TICKER}",
     {"start_date": START_COMPACT, "end_date": END_COMPACT}),

    ("v2  hist/stock/eod/{root}, dashed dates",
     f"/api/theta/hist/stock/eod/{TICKER}",
     {"start_date": START_DASHED, "end_date": END_DASHED}),

    ("v3  hist/stock/eod, root as query param",
     "/api/theta/hist/stock/eod",
     {"root": TICKER, "start_date": START_COMPACT, "end_date": END_COMPACT}),

    ("v4  hist/stock/eod, symbol as query param, dashed dates",
     "/api/theta/hist/stock/eod",
     {"symbol": TICKER, "start_date": START_DASHED, "end_date": END_DASHED}),

    ("v5  history/stock/eod/{root}",
     f"/api/theta/history/stock/eod/{TICKER}",
     {"start_date": START_COMPACT, "end_date": END_COMPACT}),

    ("v6  stock/history/eod (grpc method name order)",
     "/api/theta/stock/history/eod",
     {"symbol": TICKER, "start_date": START_DASHED, "end_date": END_DASHED}),
]


def main():
    print(f"Probing api.potatohedge.com for {TICKER} stock EOD history\n")
    try:
        td = ThetaDataController()
    except RuntimeError as e:
        print(f"Could not build client: {e}")
        return

    for label, path, params in CANDIDATES:
        try:
            r = td._get(path, params=params)
            body = (r.text or "")[:120].replace("\n", " ")
            print(f"  [{r.status_code}] {label}")
            print(f"          {path}")
            if params:
                print(f"          params={params}")
            print(f"          body: {body!r}\n")
        except Exception as e:
            print(f"  [ERR] {label}\n          {type(e).__name__}: {e}\n")

    td.close()

    print("How to read this:")
    print("  Controls 200 + current client route 502  -> EXPECTED. Credentials are")
    print("     fine and the route is registered (an unknown path returns 404, not")
    print("     502). The backend behind that route is failing. Nothing to fix here;")
    print("     this output is what to send the proxy operator.")
    print("  Controls 401/403                         -> credentials rejected after")
    print("     all. Then, and only then, ask for a new key.")
    print("  Current route 404                        -> path really is unregistered;")
    print("     check whether any other variant below returns 200.")
    print("  Everything 5xx / connection errors       -> whole proxy or terminal down.")


if __name__ == "__main__":
    main()
