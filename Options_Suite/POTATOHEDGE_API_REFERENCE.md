# PotatoHedge API Reference

Durable reference for the `potatohedge` client (ThetaData, proxied through Cloudflare
Access) that this project's `thetadata_controller.py` talks to. Written because the
actual `potatohedge` package source was deleted from the working tree at
`../PHClient_MonteCarloAmericanPricer/src/potatohedge/` but is still recoverable from
that repo's git history -- this doc captures what was recovered so it doesn't need to
be re-excavated from git every time.

To re-pull the raw source if this doc ever needs updating:
```bash
cd ../PHClient_MonteCarloAmericanPricer
git show HEAD:src/potatohedge/client.py        # sync client, ~1637 lines
git show HEAD:src/potatohedge/async_client.py  # async client, ~1323 lines
```
(`../PHClient_MonteCarloAmericanPricer/PHClient_MonteCarloAmericanPricer/` is a stray
nested empty `.git` dir, not the real source -- work from the outer repo.)

## What PotatoHedge is

A Cloudflare-Access-gated proxy in front of ThetaData at `https://api.potatohedge.com`.
Two distinct layers live behind it:

1. **`/api/theta/...`** -- raw ThetaData passthroughs. Confirmed (endpoint-path and
   response-shape level) to match this project's own hand-rolled `ThetaDataController`
   exactly for everything it already wraps.
2. **`/api/db/...`, `/api/market/...`, `/api/stability/...`** -- PotatoHedge's *own*
   value-add analytics layer, computed on top of ThetaData rather than passed through
   from it. None of these were used anywhere in this project before this pass; two
   (`dealer_positioning`, `yield_curve`) now have wrappers in `thetadata_controller.py`,
   unverified against a live response -- see below.

## Client setup

```python
from potatohedge import PHClient          # sync, requests-based
from potatohedge import AsyncPHClient      # async, aiohttp-based

client = PHClient(
    cf_client_id="...",       # from THETADATA_CF_ACCESS_CLIENT_ID
    cf_client_secret="...",   # from THETADATA_CF_ACCESS_CLIENT_SECRET
    base_url="https://api.potatohedge.com",
    timeout=180,
    max_retries=3,
)
```

Auth is two Cloudflare Access headers on every request:
```
CF-Access-Client-Id: ...
CF-Access-Client-Secret: ...
```
`PHClient` pools connections via `requests.Session` + `HTTPAdapter`/`urllib3.Retry`
(retries on 500/502/503/504, `backoff_factor=0.5`). `AsyncPHClient` is an async
context manager (`async with AsyncPHClient(...) as client:`) built on `aiohttp`.

This project doesn't depend on the `potatohedge` package directly -- `thetadata_controller.py`
is a lean, purpose-built `httpx`-based reimplementation of the same auth pattern and
the specific `/api/theta/` endpoints this project needs. Use the info above only if
you need to call the real package directly (e.g. quick one-off scripts) or need an
endpoint this project's controller doesn't wrap yet.

Every endpoint accepts `use_csv: bool` -- `True` (the official client's default)
returns raw CSV text, `False` returns JSON. This project's controller always requests
JSON. `/api/theta/` responses in JSON mode come back as `[headers_row, data_row, ...]`
(a list of lists, headers first) -- that's ThetaData's own convention, which is why
`thetadata_controller.py._parse_rows` zips `data[0]` against each subsequent row.
**`/api/db/` responses do NOT necessarily follow that convention** (different backend
layer) -- treat their shape as unverified until exercised live.

## Endpoints this project already wraps (`/api/theta/`, confirmed matching)

| `thetadata_controller.py` method | Endpoint |
|---|---|
| `list_expirations` | `GET /api/theta/list/expirations/{root}` |
| `list_strikes` | `GET /api/theta/list/strikes/{root}/{exp}` |
| `stock_snapshot_quote` | `GET /api/theta/snapshot/stock/quote/{root}` |
| `option_snapshot_quote` | `GET /api/theta/snapshot/option/quote/{root}/{exp}/{strike*1000}/{C\|P}` |
| `option_bulk_greeks` | `GET /api/theta/bulk_snapshot/option/all_greeks/{root}/{exp}` |

## Endpoints newly wrapped this pass (`/api/db/`, unverified live)

| Method | Endpoint | Notes |
|---|---|---|
| `get_dealer_positioning(root, start_date, end_date, latest_only)` | `GET /api/db/dealer_positioning` | Vendor-computed dealer positioning/GEX. Cross-check target for this project's from-scratch GEX (see `Variance_Swap_Module/dealer_positioning.py`). |
| `get_yield_curve(start_date, end_date=None, target_date=None)` | `GET /api/db/yield_curve` | Treasury curve; `target_date` returns the closest forward rate for that tenor. Candidate replacement for hardcoded `RISK_FREE_RATE` constants -- not yet wired into any call site. |

## Major undiscovered-until-now capability: native 2nd/3rd-order Greeks

While cataloguing the recovered client, found these **raw ThetaData passthrough**
endpoints (i.e. `/api/theta/`, not the `/db/` layer) that this project has never used:

| Method | Endpoint |
|---|---|
| `get_hist_option_greeks_second_order` | `GET /api/theta/hist/option/greeks_second_order/{root}/{exp}/{strike}/{right}` |
| `get_hist_option_greeks_third_order` | `GET /api/theta/hist/option/greeks_third_order/{root}/{exp}/{strike}/{right}` |
| `get_bulk_snapshot_option_greeks_second_order` | `GET /api/theta/bulk_snapshot/option/greeks_second_order/{root}/{exp}` |
| `get_bulk_snapshot_option_greeks_third_order` | `GET /api/theta/bulk_snapshot/option/greeks_third_order/{root}/{exp}` |

**This is directly relevant to the open Color/Charm-vs-market question** (the one
explicitly deferred rather than investigated in this pass -- "just fix the
computation method for now"). If ThetaData computes and exposes its own second/
third-order Greeks natively, that's a stronger long-term answer than either the old
bump-and-revalue-on-LSM approach or the new closed-form Black-Scholes approach: it
would let this project compare against (or adopt outright) the *exact* vendor
convention/units the "Market" row in the comparison report is presumably already
reading from elsewhere. Flagging this now so it isn't lost; not wired up or
investigated further, per the explicit decision to scope this pass to the computation
method only.

## Other undiscovered/unused endpoints (catalogued, not wrapped)

Recovered from the same client but not evaluated in depth -- listed for future
reference:

- **Flow**: `get_daily_flow`, `get_flow_timeseries`, `get_aggregated_flow`, `get_recent_flow`
- **Market metadata**: `get_market_metrics_breakdown`, `get_daily_summary`, `get_last_trading_day`, `is_market_open`, `get_trading_days`
- **Stability (MSI/LSS)**: `get_realtime_stability`, `calculate_realtime_stability`, `get_historical_stability`, `get_stability_history`, `get_lss_matrix`, `get_msi_timeseries`, `process_stability_csv`, `generate_stability_metrics`
- **Volatility**: `get_volatility_term_structure`
- **Index**: `get_index_price`, `get_index_eod`, `get_last_index_price`
- **Bulk historical/at-time option & stock data**: `get_bulk_option_*`, `get_at_time_option_*`, `get_at_time_stock_*`, `get_bulk_snapshot_stock_*` (mostly covered indirectly by what this project already wraps; a few variants like OHLC/open-interest bulk aren't)

None of these have been checked against a live response -- treat any future use the
same way `get_dealer_positioning`/`get_yield_curve` are flagged above.
