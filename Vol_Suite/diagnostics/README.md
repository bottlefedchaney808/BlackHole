# diagnostics/

One-off scripts that were used to work out how `api.potatohedge.com` actually
behaves. They are **not** tests and not part of any production path — they hit
the live proxy and print raw status codes and bodies. They're kept because the
conclusions they establish are load-bearing for `thetadata_client.py`, and
re-deriving them from scratch would mean repeating a long session of guesswork.

Run from the project root (not from inside this folder):

```
python -m diagnostics.diagnose_wide_range_chunks SPY 20261016 745 C 20260210 20260722
```

## What each one established

| Script | Conclusion |
|---|---|
| `diagnose_bulk_hist_endpoint.py` | Whole-chain `bulk_hist/option/all_greeks` is **502 on this proxy, always** — not a sizing or `ivl` issue. The route simply isn't wired up. |
| `diagnose_trade_greeks_endpoint.py` | `bulk_hist/option/all_trade_greeks` doesn't exist (clean 404); single-contract `hist/option/trade_greeks` works and returns tick-level data. |
| `diagnose_v3_local_terminal.py` | ThetaData v3 native endpoints need a Terminal on localhost. There isn't one on this machine — dead end, kept so nobody retries it. |
| `diagnose_v3_via_proxy.py` | The proxy exposes **no** v3 routes at all. Every v3-shaped path 404s. It is a v2-shaped, path-segment-only surface. |
| `diagnose_hist_all_greeks_single_contract.py` | **The one that found the working route.** Single-contract `hist/option/all_greeks` in path-segment style with `ivl` returns real data, including 2nd/3rd-order greeks and `implied_vol` in the same response. `greeks` needs `rth=true`; `greeks_second_order` and `implied_volatility` 502 regardless (and are redundant given the above). |
| `diagnose_wide_range_chunks.py` | **Proved the proxy returns false-negative 404s.** Identical contract and end date: 10-day range works, 12-day fails, 14- and 16-day work. A real size or date-boundary limit cannot produce a non-monotonic result like that. This is why `thetadata_client._get_with_retry` treats a 404 as retryable rather than authoritative. |
| `probe_eod_endpoint.py`, `probe_eod_range.py` | `hist/stock/eod` 502s on multi-month ranges; ~1 month is the safe request size. Hence the internal pagination in `hist_stock_eod`. |
| `probe_stockanalysis.py` | Scraping check for an index-constituents fallback source. |
| `_debug_spot.py`, `_test_potatohedge.py` | Scratch connectivity/spot-price checks. |

## Caveat about a wrong turn

`diagnose_wide_range_chunks.py`'s flakiness finding was real, but the *scale*
of failure seen later (442/442 contracts failing under concurrency) was
misattributed to the proxy rate-limiting. The actual cause was a VPN in front
of every request. Retry logic is still warranted; the concurrency=1 conclusion
that followed from it was not, and has been reverted.
