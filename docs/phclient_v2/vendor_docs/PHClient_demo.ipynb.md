# PHClient_demo.ipynb — captured as text (source was JSON notebook)

Saved as `.md` (not `.ipynb`) since it was captured via page-text extraction, not a raw file
download — this is the CONTENT for reference, not a valid re-importable notebook. Original URL:
`https://install.potatohedge.com/releases/v2.0.1/examples/PHClient_demo.ipynb`

## IMPORTANT discrepancy found (2026-08-17)

Two cells in this notebook call `client.get_bulk_snapshot_option_quote(...)` /
`client.get_bulk_snapshot_option_all_greeks(...)` (old v1 flat-method names) with comments saying:

> RUNTIME-BLOCKED (guide §4.1/§7c): options.bulk_snapshot_option_quote is policy-resolved `ordinary`
> under `options.bulk.read` (PR #96) but not yet in the generated client pending the
> conformance-gated regeneration. Draft v2 mapping (do not merge/run until regeneration lands):
> opt_data = client.options.bulk_snapshot_option_quote(root=TICKER, exp=0, use_csv=True).data

**This comment is stale/wrong as shipped in 2.0.1** — `CLIENT_REFERENCE.md` (the generated, canonical
method catalog, same release) DOES list both `options.bulk_snapshot_option_all_greeks` and
`options.bulk_snapshot_option_quote` as real namespaced v2 methods (see
`vendor_docs/CLIENT_REFERENCE.md` lines ~85, 88). The notebook just wasn't refreshed after the
conformance regeneration landed. **Use the namespaced `client.options.bulk_snapshot_option_*(...)`
form (the "draft v2 mapping" comment), not the old flat `client.get_bulk_snapshot_option_*(...)` calls
literally present in the notebook's executable code** — those flat names don't exist on v2's client at
all (they were deleted at the v1-deletion gate per CHANGELOG 2.0.0).

---

## Cell 1 — imports + client construction

```python
import os
import logging
import sys
import datetime as dt
import pandas as pd
import numpy as np
from io import StringIO
from scipy.stats import norm

from potatohedge.client_v2 import PHClient
from potatohedge.config import ClientConfig, Credential

_cf_credential = Credential(headers={
    "CF-Access-Client-Id": os.getenv("CF_CLIENT_ID"),
    "CF-Access-Client-Secret": os.getenv("CF_CLIENT_SECRET"),
})
client = PHClient(ClientConfig(
    base_url="https://api.potatohedge.com",
    credentials={
        "market.read": _cf_credential,
        "options.bulk.read": _cf_credential,
        "volatility.compute": _cf_credential,
    },
    caller_id="phclient-demo-notebook",
    client_version="2",
))
```

## Cell 2 — config params (TICKER, TODAY, NEXT_MONTH, PREV_YEAR, TARGET_EXPIRY, HIST_LOOKBACK_DAYS)

Standard scaffolding, no client calls.

## Cell 3 — stock/option/greeks snapshot (contains the stale-comment methods above)

- `client.get_bulk_snapshot_option_quote(TICKER, 0, use_csv=True)` — **stale name, see discrepancy note above**
- `client.get_bulk_snapshot_option_all_greeks(TICKER, 0, use_csv=True)` — **stale name, see discrepancy note above**
- Real-time stock quote snapshot was **removed entirely in 2.0** (`market.stock_quote_snapshot` is
  policy `restricted`, no v2 replacement) — comment says "use option greeks for underlying price" instead
  (greeks response includes `underlying_price`).
- Strike values are in **cents×10, i.e. thousandths of a dollar** (divide by 1000 for dollars) — same
  convention `strike_from_theta`/`option_bulk_hist_oi_by_day` already assume in this repo's existing
  `shared/thetadata.py`.

## Cell 4 — yield curve

```python
yield_curve_data = client.market.yield_curve(start_date=TODAY, use_csv=True).data
```
Confirmed real/runtime-verifiable v2 method (not draft).

## Cell 5 — pure-Python utility functions (strike-cents→dollars, yield-curve tenor selection, Black-Scholes)

No client calls — pricing math, not API surface. Could be dropped/replaced with this repo's own
`american_binomial.py`/`SABRModel.py` etc.

## Cell 6 — implied-vol Newton-Raphson + bisection fallback

No client calls — pure numerical method, same shape as this repo's `NewtonRaphsonIV.py`.

## Cell 7 — full worked example: pick near-ATM option, compute implied vol, price via Black-Scholes,
compare to market — orchestrates cells 1-6's data + math together. No new API surface.

## Cell 8 — volatility term structure

```python
term_structure = client.volatility.term_structure(root=TICKER, use_csv=False).data
```
Confirmed real/runtime-verifiable v2 method (capability `volatility.compute`).

## Cell 9 — historical OHLC + EOD data

```python
ohlc_data = client.market.stock_ohlc(
    root=TICKER, start_date=start_date_int, end_date=end_date_int,
    ivl=3600000, rth=False, venue='utp_cta', use_csv=True,
).data

eod_data = client.market.stock_eod(root=TICKER, start_date=start_date_int, end_date=end_date_int, use_csv=True).data
```
Both confirmed real v2 methods (capability `market.read`). Interval-size limits noted in comments:
tick (ivl=0) max 30d, 1min max 1y, 5min max 2y, 15min max 3y, 1hr max 5y.
