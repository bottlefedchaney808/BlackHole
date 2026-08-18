# PotatoHedge API Client

A Python client for the PotatoHedge API providing access to financial market
data including options, stocks, indices, dealer positioning, and flow
analytics.

PHClient 2.0 ships a **generated, namespaced, capability-scoped client**
(`potatohedge.client_v2.PHClient` / `AsyncPHClient`). Every method is
generated from a runtime-conformance-verified catalog; responses are typed
`ResponseEnvelope`s and errors form the typed `PHClientError` hierarchy.

> **2.0 breaking change:** the legacy flat-method clients
> (`potatohedge.PHClient` / `potatohedge.AsyncPHClient`) were removed at the
> v1-deletion gate. See `docs/migration/v1-to-v2-migration-guide.md` for the
> complete migration reference.

## Documentation

- **[CLIENT_REFERENCE.md](./docs/generated/CLIENT_REFERENCE.md)** — generated
SDK method reference (canonical)
- **[API_REFERENCE.md](./docs/generated/API_REFERENCE.md)** — generated
endpoint reference
- **[v1 → v2 migration guide](./docs/migration/v1-to-v2-migration-guide.md)**

## Installation

```bash
git clone https://github.com/schismsaints/PHClient.git
cd PHClient
pip install poetry
poetry install
```

## Authentication and construction

The API uses Cloudflare Access. v2 construction takes a `ClientConfig` with
one credential per capability your caller actually needs (an all-routes `"*"`
credential is rejected), plus your caller's manifest identity:

```python
import os
from potatohedge.client_v2 import PHClient
from potatohedge.config import ClientConfig, Credential

credential = Credential(headers={
"CF-Access-Client-Id": os.environ["CF_CLIENT_ID"],
"CF-Access-Client-Secret": os.environ["CF_CLIENT_SECRET"],
})

client = PHClient(ClientConfig(
base_url="https://api.potatohedge.com",
credentials={
"market.read": credential,
"options.read": credential,
},
# Your caller's stable identity from inventory/caller-universe.yaml —
# a literal, sent as PH-Caller-Id on every request.
caller_id="phclient-demo-notebook",
client_version="2",
))
```

## Usage

Methods are namespaced (`client.market.*`, `client.options.*`,
`client.dealer.*`, `client.flow.*`, `client.volatility.*`, ...) and return
`ResponseEnvelope`s — unwrap the payload via `.data`:

```python
ohlc = client.market.stock_ohlc(
root="AAPL",
start_date=20240101,
end_date=20240110,
ivl=3600000, # 1-hour bars
).data

curve = client.market.yield_curve(start_date=20240110, use_csv=True).data
```

### Async

```python
import asyncio
from potatohedge.client_v2 import AsyncPHClient

async def main(config):
client = AsyncPHClient(config)
envelope = await client.market.stock_ohlc(
root="AAPL", start_date=20240101, end_date=20240110, ivl=3600000
)
return envelope.data

asyncio.run(main(config))
```

### Error handling

```python
from potatohedge.errors import PHClientError, PHRateLimitError

try:
quote = client.options.snapshot_option_quote(
root="AAPL", exp=20240119, strike=190000, right="C"
).data
except PHRateLimitError:
... # carries .retryable
except PHClientError as exc:
print(exc)
```

## Data conventions

- Dates are `YYYYMMDD` integers; strikes are integer thousandths
(190000 = $190.00); intervals are milliseconds.
- CSV/JSON handling is explicit per method (`use_csv`).
- Times are ET for market context.

## Release provenance

2.0.0's surface is generated from a 140-target runtime-conformance-verified
catalog; see `migration/release-evidence.yaml` for artifact hashes and gate
locators.
