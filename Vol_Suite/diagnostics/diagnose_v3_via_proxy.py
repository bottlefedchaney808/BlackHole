#!/usr/bin/env python3
"""diagnose_v3_via_proxy.py

Tests whether api.potatohedge.com exposes ThetaData's v3 REST API (the
`history/greeks/eod` / `history/greeks/all` family confirmed real on
docs.thetadata.us) UNDER SOME PATH ON THE PROXY, rather than testing the
locally-run Theta Terminal directly (diagnose_v3_local_terminal.py already
established Jason has no Terminal running locally -- ConnectError on every
request, not a routing problem).

Jason's point (2026-07-23): the proxy already runs a real Theta Terminal and
successfully passes through other calls (confirmed working elsewhere --
Discord bot, etc, plus this project's own confirmed-working bulk_snapshot
and hist/option/trade_greeks calls). "bulk is v2" -- the `bulk_hist/...`
naming this project's code uses is the OLDER v2 API's route family and may
simply not be wired up / broken on the proxy's routing table, while the
NEWER v3 route names (`history/greeks/eod`, no "bulk" in the name at all,
whole-chain by default via strike=*/right=both) might be what the proxy
actually forwards correctly, under some path prefix.

This script does NOT know the proxy's exact prefix convention for v3 routes,
so it tries several plausible combinations of (path prefix) x (param naming
style, since v3 docs use symbol/expiration while this project's existing
confirmed-working v2-style calls use root/exp) against the same
single-day range that 502'd via the old bulk_hist path, so results are
directly comparable to diagnose_bulk_hist_endpoint.py's findings.

Usage:
    python diagnose_v3_via_proxy.py SPY [YYYYMMDD-expiry] [YYYYMMDD-date]
"""
import sys
from datetime import datetime, timedelta

from thetadata_client import ThetaDataController


def _pretty(label, resp):
    print(f"\n--- {label} ---")
    print(f"URL: {resp.request.url}")
    print(f"status: {resp.status_code}")
    body = resp.text
    if len(body) > 1200:
        print(f"body (first 1200 of {len(body)} chars, {len(body)} total):\n{body[:1200]}...")
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

    date = sys.argv[3] if len(sys.argv) > 3 else (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    print(f"root={root} exp={exp} date={date}\n"
          f"Probing api.potatohedge.com for a v3-style ('history', not "
          f"'bulk_hist') route to the same single-day range that 502'd via "
          f"the old bulk_hist path.\n")

    base = ctl.base_url
    client = ctl.client

    # v3-native param naming (symbol/expiration, per docs.thetadata.us) vs
    # this project's existing v2-style calls (root/exp) -- we don't know
    # which one the proxy's v3 wiring, if any, actually expects.
    v3_native = {"symbol": root, "expiration": exp, "start_date": date, "end_date": date}
    v2_style = {"root": root, "exp": exp, "start_date": date, "end_date": date}
    # /all and /second_order require `interval` (no EOD-only default) --
    # matches Jason's own sample URLs (interval=10m / interval=1h).
    v3_native_interval = {**v3_native, "interval": "1h"}
    v2_style_interval = {**v2_style, "interval": "1h"}

    # trade_greeks/first_order's sample URL uses a single `date` param
    # instead of start_date/end_date -- test both forms since it's unclear
    # from the one sample whether a range also works.
    v3_native_single_date = {"symbol": root, "expiration": exp, "date": date}
    v2_style_single_date = {"root": root, "exp": exp, "date": date}

    # open_interest's sample URL from Jason specifies an explicit
    # strike+right (220.000/call) rather than whole-chain defaults -- test
    # BOTH a whole-chain form (strike=*, right=both, mirroring greeks/eod's
    # documented defaults, since that's what this project's seed+accumulate
    # OI need actually wants) and a single-contract form matching the exact
    # sample shape, in case strike=*/right=both isn't actually supported
    # here. "right" also uses full words (call/put) in the v3 docs vs this
    # project's existing C/P convention -- test both spellings too.
    _range_start = (datetime.strptime(date, "%Y%m%d") - timedelta(days=3)).strftime("%Y%m%d")
    oi_whole_chain_v3 = {"symbol": root, "expiration": exp, "strike": "*", "right": "both",
                          "start_date": _range_start, "end_date": date}
    oi_whole_chain_v2 = {"root": root, "exp": exp, "strike": "*", "right": "both",
                          "start_date": _range_start, "end_date": date}
    oi_single_contract_full_word = {"symbol": root, "expiration": exp, "strike": 745.000,
                                     "right": "call", "start_date": _range_start, "end_date": date}
    oi_single_contract_letter = {"symbol": root, "expiration": exp, "strike": 745.000,
                                  "right": "C", "start_date": _range_start, "end_date": date}

    # (path suffix, param sets to try under it) -- same prefix candidates
    # applied to all four v3 history endpoints Jason's flagged: eod (whole
    # chain, EOD-bucketed), all (intraday, first-order), second_order
    # (vanna/charm/vomma/veta/vera history), and trade_greeks/first_order
    # (per-trade first-order greeks, single-date sample).
    endpoints = [
        ("history/greeks/eod", [("v3-native params", v3_native), ("root/exp params", v2_style)]),
        ("history/greeks/all", [("v3-native params", v3_native_interval), ("root/exp params", v2_style_interval)]),
        ("history/greeks/second_order", [("v3-native params", v3_native_interval), ("root/exp params", v2_style_interval)]),
        ("history/trade_greeks/first_order", [
            ("v3-native params, single date", v3_native_single_date),
            ("root/exp params, single date", v2_style_single_date),
            ("v3-native params, date range", v3_native),
            ("root/exp params, date range", v2_style),
        ]),
        ("history/open_interest", [
            ("whole chain (strike=*, right=both), v3-native", oi_whole_chain_v3),
            ("whole chain (strike=*, right=both), root/exp", oi_whole_chain_v2),
            ("single contract, right=call (matches sample)", oi_single_contract_full_word),
            ("single contract, right=C", oi_single_contract_letter),
        ]),
    ]
    prefixes = [
        ("existing /api/theta prefix", "/api/theta/{ep}"),
        ("existing /api/theta prefix + option/", "/api/theta/option/{ep}"),
        ("v3-prefixed under /api/theta", "/api/theta/v3/option/{ep}"),
        ("bare /v3 path through proxy host", "/v3/option/{ep}"),
    ]

    for ep, param_variants in endpoints:
        for prefix_label, prefix_tmpl in prefixes:
            path = prefix_tmpl.format(ep=ep)
            for param_label, params in param_variants:
                label = f"{ep} -- {prefix_label} -- {param_label}"
                try:
                    r = client.get(f"{base}{path}", params=params)
                    _pretty(label, r)
                except Exception as e:
                    print(f"\n--- {label} ---\nREQUEST FAILED: {type(e).__name__}: {e}")

    print("\n\nLooking for the first 200 with real per-strike rows above -- "
          "that's the v3-style path/param combo the proxy actually "
          "supports. Everything else 404ing just means that particular "
          "prefix/param guess is wrong, not that v3 isn't wired up at all. "
          "Once eod's working combo is found, all/second_order almost "
          "certainly use the identical prefix.")


if __name__ == "__main__":
    main()
