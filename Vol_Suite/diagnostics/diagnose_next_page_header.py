#!/usr/bin/env python3
"""diagnose_next_page_header.py

The hypothesis I should have tested before any parameter guessing.

WHAT diagnose_v3_param_names.py ACTUALLY ESTABLISHED (2026-07-24):
omitting `end_date` returns 422, and so do from/to, start/end and
date_from/date_to. So `start_date`/`end_date` are the correct names AND
`end_date` is REQUIRED -- the server parses and validates our range. A
parameter that is silently ignored cannot also be mandatory. That rules out
"the proxy ignores end_date", which was my explanation, and it doesn't
support "the param names shuffled" either, at least for this route.

So the server accepts a 14-day range and returns 27 rows covering one day.
There is a well-documented ThetaData v2 behavior that produces exactly that:

    RESPONSES ARE PAGINATED VIA A `Next-Page` RESPONSE HEADER.

The client is expected to read that header and follow the URL until it comes
back `null`. We have never looked at a single response header. If that's what
this is, then every conclusion about "which routes honor ranges" has really
been "which routes happen to fit in one page":

  * hist/option/all_greeks at ivl=900000 -> ~27 rows per DAY -> one page per day
  * hist/option/eod                      -> 1 row per day -> 14 days fits easily
  * hist/stock/eod                       -> same, and we already chunk it by
                                            month by hand, which is why price
                                            history looked fine all along

That would also retire the 502s: a whole-chain bulk_hist request isn't
"broken", it's enormous, and asking for it in one shot times out upstream --
which is precisely what pagination exists to prevent.

This prints every response header for a request we know returns partial data.
No guessing: either `Next-Page` is there or it isn't.

Usage (from the project root):
    python -m diagnostics.diagnose_next_page_header
"""
import sys

from thetadata_client import ThetaDataController, strike_to_theta

ROOT, EXP, STRIKE, RIGHT = "SPY", "20261030", 745.0, "C"
START, END = "20260706", "20260723"


def show_headers(r, label):
    print(f"--- {label}")
    print(f"    {r.request.url}")
    print(f"    status {r.status_code}")
    print("    response headers:")
    for k, v in sorted(r.headers.items()):
        # Cloudflare adds a lot of noise; keep it but mark the interesting ones.
        star = " <<<" if any(t in k.lower() for t in
                             ("page", "next", "count", "format", "total", "link")) else ""
        print(f"      {k}: {v}{star}")
    print()


def main():
    ctl = ThetaDataController()
    k = strike_to_theta(STRIKE)
    base = "/api/theta"

    print(f"{ROOT} {EXP} {STRIKE}{RIGHT}  {START}-{END}")
    print("Looking for a Next-Page (or Link / pagination) response header.\n")

    # 1. The request that returns 27 rows for a 14-day window.
    r = ctl._get(f"{base}/hist/option/all_greeks/{ROOT}/{EXP}/{k}/{RIGHT}",
                 params={"start_date": START, "end_date": END, "ivl": 900000})
    show_headers(r, "all_greeks, 14-day range (returns ~1 day of bars)")

    # 2. The route that looked like it honored ranges -- if pagination is the
    #    mechanism, this one simply fits in a single page, and its headers
    #    should show either no Next-Page or a null one.
    r_eod = ctl._get(f"{base}/hist/option/eod/{ROOT}/{EXP}/{k}/{RIGHT}",
                     params={"start_date": START, "end_date": END})
    show_headers(r_eod, "eod, same range (returns all 14 dates)")

    # 3. Follow the chain if it's there.
    print("=" * 66)
    header_names = {k.lower() for k in r.headers}
    page_header = next((h for h in ("next-page", "next_page", "x-next-page", "link")
                        if h in header_names), None)

    if not page_header:
        print("No pagination header on the greeks response.")
        print("\nThen the single-day behavior is real, and the next step is")
        print("not more guessing: ask him for one working curl that returns")
        print("multi-day greeks history. Everything else about the current")
        print("build already works around this correctly.")
        ctl.close()
        return

    print(f"FOUND: '{page_header}' = {r.headers.get(page_header)!r}\n")
    print("Following the chain...")
    seen_dates = {str(ctl._normalize_date(row)) for row in ctl._parse_rows(r)}
    seen_dates.discard("None")
    url = r.headers.get(page_header)
    pages = 1
    while url and str(url).lower() not in ("null", "none", "") and pages < 40:
        nxt = ctl.client.get(url, timeout=60.0)
        if nxt.status_code != 200:
            print(f"  page {pages + 1}: status {nxt.status_code} -- stopping")
            break
        rows = ctl._parse_rows(nxt)
        new = {str(ctl._normalize_date(row)) for row in rows}
        new.discard("None")
        seen_dates |= new
        pages += 1
        print(f"  page {pages}: {len(rows):4d} rows, dates so far: {len(seen_dates)}")
        url = nxt.headers.get(page_header)

    print(f"\nTotal: {pages} pages, {len(seen_dates)} distinct dates "
          f"({min(seen_dates)} .. {max(seen_dates)})" if seen_dates else "")
    if len(seen_dates) > 1:
        print("""
CONFIRMED -- pagination was the whole problem.

Fix: teach ThetaDataController._get_with_retry (or a wrapper above it) to
follow this header and concatenate pages. Then:
  * hist/option/all_greeks honors ranges after all -> vendor IV and gamma
    become affordable again, and implied_vol.py demotes from the primary
    source to a fallback (keep it -- it's tested, and it's what makes the
    eod route usable if greeks ever regress);
  * the whole-chain bulk_hist 502s are worth retrying, since "too big for
    one response" is exactly what pagination solves;
  * the 28-day manual chunking in this client becomes redundant.

And it means the proxy was never the problem on this one. Our client just
never implemented half of the protocol.""")
    ctl.close()


if __name__ == "__main__":
    main()
