# Bug report: `hist/option/all_greeks` ignores `end_date`

Reproduced 2026-07-24 against `api.potatohedge.com`. Two request IDs below for
log lookup.

## Summary

`hist/option/all_greeks` accepts a date range, validates it, and returns only
`start_date`. `hist/option/eod` — same contract, same window, same client —
returns the full range. The discrepancy is between two routes on the same
service, not between the client and the service.

## Reproduction

```
GET /api/theta/hist/option/all_greeks/SPY/20261030/745000/C
    ?start_date=20260706&end_date=20260723&ivl=900000
-> 200, 27 rows, ALL dated 20260706
   x-request-id: 9e598e0e-52bb-4b98-b219-0b64f6549d42
   x-process-time: 0.0790

GET /api/theta/hist/option/eod/SPY/20261030/745000/C
    ?start_date=20260706&end_date=20260723
-> 200, 15 rows, 14 distinct dates (20260706 .. 20260723)
   x-request-id: 1a4b628d-0f01-4a68-9657-ef4414fb5929
   x-process-time: 0.6249
```

27 rows at `ivl=900000` is one 6.5-hour session of 15-minute bars. So the
greeks route returns exactly one day and stops.

## What we ruled out, and how

**Not a wrong parameter name.** Omitting `end_date` returns 422, as do
`from`/`to`, `start`/`end`, and `date_from`/`date_to`. `end_date` is parsed
and required — a silently-ignored parameter cannot also be mandatory.

**Not truncation, a size cap, or an upstream timeout.** `x-process-time` is
0.079s versus 0.625s for the route that *does* return the full range. The
greeks handler is doing one day of work quickly, not straining against a
limit.

**Not pagination.** No `Next-Page`, `Link`, or any pagination header on the
response. (ThetaData v2 documents `Next-Page`; it isn't present here.)

**Not client-side parsing.** Verified against the raw JSON before any of our
code touches it — the `date` column holds a single value.

**Not `exp=*`.** Tried as a path segment (422) and as a query param on
`bulk_hist` and single-contract routes (404 in every form).

**Not a dead contract.** The same contract and window returns 14 dates via
`eod`, so the data exists.

## Related, possibly the same root cause

- `hist/option/open_interest` shows identical single-day behavior.
- `bulk_hist/option/all_greeks/{root}/{exp}` returns 502 for any range,
  with or without `ivl`.
- `bulk_hist/option/open_interest/{root}/{exp}` returns 200 with the whole
  chain (344 rows) but, again, only for `start_date`.
- `bulk_hist/option/eod` times out (ReadTimeout, not 502).

So: every `eod` route honors ranges, and every greeks/OI route returns one
day. That pattern looks like the range is reaching the eod handler and not the
others.

## What would settle it fastest

One working `curl` that returns multi-day greeks history. If the route is
intended to be single-day, that's fine and we'll keep the workaround — we just
need to know which it is.

## Impact / current workaround

Backfilling one expiry's greeks a day at a time is ~430 contracts × ~110
trading days ≈ 47,000 requests, which we're not going to do to a shared proxy.
So `backtest_stage3.py` now pulls prices from `hist/option/eod` (~430 requests,
full range per call) plus whole-chain OI one day at a time (~110 requests), and
reconstructs IV and gamma locally (`implied_vol.py`). ~550 requests total.

That works and is tested, but it substitutes our own European Black-Scholes IV
for the vendor's. If the greeks route can serve ranges, we'd rather use vendor
IV and demote the local inversion to a fallback.
