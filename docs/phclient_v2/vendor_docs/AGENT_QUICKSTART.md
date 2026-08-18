# PHClient v2 — Agent Quickstart

Zero-context orientation for calling the PotatoHedge API (`PH_API`) from Python. Read this,
then the generated [Client reference](generated/CLIENT_REFERENCE.md) for the full method list.

## Install

Packaged with PEP 621 metadata (`pyproject.toml`, setuptools backend). Requires Python `>=3.10`.

```bash
pip install -e . # from a checkout of this repo
pip install -e '.[pandas]' # add pandas
pip install -e '.[models]' # add pydantic (optional response models)
pip install -e '.[dev]' # test/dev tooling
```

Extras: `pandas`, `models` (pydantic), `dev`, `conformance` (`pyproject.toml:38`).

## Import the v2 client

The v2 namespaced clients live in `potatohedge.client_v2`. (The top-level
`from potatohedge import PHClient` currently resolves to the legacy v1 client —
`src/potatohedge/__init__.py:73` — so import v2 explicitly.)

```python
from potatohedge.client_v2 import PHClient, AsyncPHClient
from potatohedge.config import ClientConfig, Credential
```

## Construct a client

Credentials are a per-capability mapping. Each endpoint declares an `auth_capability` of the
form `<namespace>.read`; configure a `Credential` (Cloudflare Access service-token headers) for
every namespace you intend to call.

```python
import os

cf_headers = {
"CF-Access-Client-Id": os.environ["CF_ACCESS_CLIENT_ID"],
"CF-Access-Client-Secret": os.environ["CF_ACCESS_CLIENT_SECRET"],
}
cred = Credential(headers=cf_headers)

config = ClientConfig(
base_url="https://api.potatohedge.com",
credentials={
"signals.read": cred,
"options.read": cred,
"market.read": cred,
},
)

client = PHClient(config) # sync
# async: client = AsyncPHClient(config) # supports `async with`
```

Notes:
- `base_url` must be an origin only (scheme + host, no path/query/user); HTTPS is required
except for explicit `localhost_test_mode=True` (`src/potatohedge/config.py:45`).
- A `"*"` catch-all capability is rejected (`config.py:71`). An unconfigured capability raises
`PHPermissionError` at call time (`src/potatohedge/auth.py:66`).
- `Credential` and `ClientConfig` reprs redact secret values.
- Runtime gate: with the current descriptor snapshot (all `unverified`), construction raises
`PHConfigurationError("generated v2 client is unavailable until runtime conformance is
verified")` (`src/potatohedge/client_v2.py:22`). The shapes below are the intended API.

## One worked call

`signals.strategies` lists registered strategies; both params are optional
(`GET /api/signals/strategies`, conformance-verified).

```python
# NOTE: raises PHConfigurationError until conformance artifacts land (see gate above)
with PHClient(config) as client:
env = client.signals.strategies(is_active=True) # sync
strategies = env.data # decoded response payload
print(env.status, env.endpoint_id, len(strategies) if hasattr(strategies, "__len__") else strategies)
```

Async equivalent:

```python
async with AsyncPHClient(config) as client:
env = await client.signals.strategies(is_active=True)
strategies = env.data
```

Every call returns a `ResponseEnvelope` (`src/potatohedge/envelopes.py:22`). Useful fields:
`env.data` (decoded body — dict/list, or tuple-of-dicts for CSV responses), `env.status`,
`env.endpoint_id`, `env.request_id`, `env.content_type`, `env.warnings`, `env.metadata`
(safe response headers), `env.provenance`.

## Error envelope

All errors subclass `PHClientError` (`src/potatohedge/errors.py:63`) and carry structured,
secret-redacted context: `.message`, `.endpoint_id`, `.status`, `.request_id`, `.retryable`,
`.context`. HTTP failures map by status (`src/potatohedge/transport.py:146`):

| Condition | Exception |
|---|---|
| Bad/missing config, unverified runtime, base-URL invalid | `PHConfigurationError` |
| HTTP 401, credential/origin mismatch | `PHAuthError` |
| HTTP 403, unconfigured capability | `PHPermissionError` |
| HTTP 429 | `PHRateLimitError` |
| Other non-2xx (incl. 422 validation) | `PHAPIError` (`.status` holds the code) |
| Client-side arg/signature mismatch | `PHRequestValidationError` |
| Timeout / connection failure | `PHTimeoutError` / `PHTransportError` |
| Undecodable body / unknown-field or model drift | `PHDecodeError` / `PHContractDriftError` |

```python
from potatohedge.errors import PHClientError

try:
env = client.dealer.regime_history(ticker="SPY", start_date="20260701", end_date="20260724")
except PHClientError as exc:
print(exc.status, exc.endpoint_id, exc.retryable, dict(exc.context))
```

## Routing — which namespace/method for which question

All method names below exist in the generated client (`generated/CLIENT_REFERENCE.md`).

| Question | Call |
|---|---|
| Latest EOD options chain for a ticker | `options.options_chain(ticker=...)` |
| One contract by OCC symbol | `options.options_contract(occ=...)` |
| Historical option Greeks (all orders) | `options.hist_option_all_greeks(...)` |
| Dealer gamma/vanna exposures by strike | `dealer.greek_exposures(...)` |
| Dealer positioning regime over time | `dealer.regime_history(ticker=..., start_date=..., end_date=...)` |
| Zero-DTE gamma wall | `support_resistance.zero_dte_gamma_wall(ticker=...)` |
| Support/resistance levels snapshot | `support_resistance.snapshot(ticker=...)` |
| IV surface snapshot | `volatility.iv_surface_snapshot(ticker=...)` |
| Probabilistic price envelope | `volatility.probabilistic_envelope(ticker=...)` |
| Unusual options flow | `flow.unusual(...)` |
| Swap-flow validated signals | `flow.validated_signals(...)` |
| Active signal strategies | `signals.strategies(is_active=True)` |
| Signal validity for a ticker | `signals.validity(ticker=...)` |
| Cross-ticker correlation matrix | `market.correlation_matrix(tickers=..., metric=..., window=...)` |
| Is market open / next trading day | `market.is_market_open(...)` / `market.next_trading_day(...)` |
| Company / market news | `news.company_news(...)` / `news.market_news(...)` |
| One-shot ticker bundle | `recipes.ticker_bundle(...)` |
| RegSHO threshold/FTD screener | `regsho.current(...)` / `regsho.ftd_history(...)` |

## Format gotchas

1. **Dates are `YYYYMMDD`** (8 digits, e.g. `"20260724"`; some endpoints also accept
`YYYY-MM-DD`). Not epoch, not `MM/DD/YYYY`.
2. **Strikes are thousandths** — a $200.00 strike is `200000` (`CLAUDE.md` invariant). Applies
to option endpoints that take a strike; the OCC-symbol path (`options.options_contract`)
already encodes it.
3. **Optional params:** omit them or pass `None`; `None` query values are dropped before the
request (`transport.py:47`). Don't pass empty strings expecting "unset". Booleans go on the
wire lowercase (`true`/`false`).

## Analytic safety gotchas (do not misuse the data)

These govern how you *interpret* responses; the generated references do not carry them. Sources
are `PH_API` MCP tool descriptions.

4. **Dealer Greeks are position-weighted — NEVER multiply by open interest.** `dealer.*`
exposure values (gamma/delta/vanna/charm) are already weighted by dealer position size;
OI-weighting double-counts (`PH_API/ph_mcp/server.py (tool-description text, verbatim-verified 2026-07-24; line numbers drift — grep the quoted text)`). This is the most common agent
error against this API.
5. **Vanna polarity is ticker-specific.** `-vanna = bullish`, `+vanna = bearish` — **except
IWM, where `+vanna = bullish`** (`PH_API/ph_mcp/server.py (tool-description text, verbatim-verified 2026-07-24; line numbers drift — grep the quoted text)`). Check the ticker before
reading a vanna sign.
6. **`volatility.probabilistic_envelope` is a state descriptor, not a forecast.** Its bands and
anchors are reaction zones — never present them as targets, magnets, floors, ceilings, or
levels price will reach; it does not forecast direction (`PH_API/ph_mcp/server_public.py (tool-description text, verbatim-verified 2026-07-24; line numbers drift — grep the quoted text)`).