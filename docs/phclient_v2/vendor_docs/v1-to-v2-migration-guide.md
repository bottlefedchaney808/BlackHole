# PHClient v1 -> v2 Migration Guide

Status: authored for the `phclient-2-release-readiness-program` gate "Publish
migration guide and complete caller ledger." This guide does **not**
authorize caller migration, v1 deletion, or production deploy — those remain
separate operator gates per
`docs/superpowers/plans/2026-07-13-phclient-v2-initial-release-scope-amendment.md:27`.

This is an authored reference. It does not hand-edit anything under
`docs/generated/` (single-generator rule) — it cites those files instead of
duplicating them. The authoritative, generator-produced v2 method catalog is
`docs/generated/CLIENT_REFERENCE.md`; the authoritative raw route/schema
catalog is `docs/generated/API_REFERENCE.md`. When in doubt, those files win.

Every v1 line number and v2 name in this guide was verified by direct read/grep
against `src/potatohedge/client.py` (v1) and
`docs/generated/CLIENT_REFERENCE.md` (v2). Provenance is two-snapshot:
the original caller scan ran at base `c919e08`; the §4.1 capability table
and the §6/§9 post-flip dispositions were verified at base `2d2187b`
(post-#95/#96, the branch's current rebase base). Where the two differ,
the `2d2187b` verification wins.

## 1. Package identity — read this first

`potatohedge/__init__.py:__getattr__` currently binds the public name
`PHClient` (and `AsyncPHClient`) to **`src/potatohedge/client.py`** — the v1,
`requests`-based, flat-method client — not to `src/potatohedge/client_v2.py`.
So today, `from potatohedge import PHClient` still returns the v1 client.
This is confirmed by reading `src/potatohedge/__init__.py:73-80`.

- **v1 (current default import):** `from potatohedge import PHClient` or
`from potatohedge.client import PHClient`.
- **v2 (must import explicitly today):** `from potatohedge.client_v2 import
PHClient` (or `AsyncPHClient`).
- The release-repin gate (plan Task 10,
`docs/superpowers/plans/2026-07-11-phclient-v2-api-alignment-and-quant-migration.md:653-736`)
is what will eventually flip the default `potatohedge` import to v2 and add
the typed `upgrade-required` response for v1 identities. Until that gate
runs, **v1 and v2 coexist under the same package** and callers must
explicitly opt into `potatohedge.client_v2` to test/adopt v2.
- A second, non-package name — `from phclient import PHClient` — appears in
several docstring code samples (not executable imports; see §6). There is
no installed `phclient` package; do not follow those examples literally.

## 2. Construction: positional args -> `ClientConfig` + capability credentials

**v1** (`src/potatohedge/client.py:26-53`):

```python
from potatohedge.client import PHClient

client = PHClient(
cf_client_id="...",
cf_client_secret="...",
base_url="https://api.potatohedge.com",
timeout=180,
)
```

One shared Cloudflare Access credential authorizes every v1 method call.

**v2** (`src/potatohedge/client_v2.py:47-58`, `src/potatohedge/config.py:36-75`,
`src/potatohedge/auth.py:56-84`):

```python
from potatohedge.client_v2 import PHClient
from potatohedge.config import ClientConfig, Credential

config = ClientConfig(
base_url="https://api.potatohedge.com",
credentials={
"dealer.read": Credential(headers={
"CF-Access-Client-Id": "...",
"CF-Access-Client-Secret": "...",
}),
"market.read": Credential(headers={...}),
# one entry per capability the caller actually needs — see §4
},
caller_id="trading-research", # stable per-caller identity, not "phclient" default
client_version="2",
)
client = PHClient(config)
```

Key differences, verified in `config.py`:

- `ClientConfig.__post_init__` (`config.py:42-75`) **rejects** an
all-routes/default credential: `"*" in self.credentials` raises
`PHConfigurationError` (`config.py:59-61`). Every v2 caller must configure
credentials per capability, not once for the whole client.
`AuthProvider.headers_for` (`auth.py:56-84`) looks up the credential by the
descriptor's `auth_capability` and raises `PHPermissionError` if that
capability has no configured credential (`auth.py:65-69`).
`PHConfigurationError`/`PHPermissionError` are typed errors — see §3.
- `base_url` must be HTTPS-origin-only: no path, query, fragment, or
userinfo (`config.py:47-55`); `http://` is allowed only with
`localhost_test_mode=True` and host `localhost` (`config.py:56-58`).
- `caller_id` defaults to the literal string `"phclient"`
(`config.py:39`) — every caller should set an explicit, stable
`caller_id` (this is what feeds PH_API's caller-attribution middleware
described in the plan, `docs/superpowers/plans/2026-07-11-phclient-v2-api-alignment-and-quant-migration.md:201`).
- `AuthProvider.headers_for` also rejects a credential that tries to
override `PH-Client-Version` / `PH-Caller-Id` (`auth.py:70-75`) — those
identity headers are always client-controlled, never caller-supplied.

## 3. Error shape: `requests` exceptions -> typed `PHClientError` hierarchy

**v1** (`src/potatohedge/client.py:78-156`, docstring at `:97-98`) raises
whatever `requests` raises: `response.raise_for_status()` produces
`requests.exceptions.HTTPError`; the retry loop explicitly catches
`ChunkedEncodingError`, `ConnectionError`, `Timeout`
(`client.py:146-153`), and anything else propagates as
`requests.exceptions.RequestException` (`client.py:155-156`). A failed JSON
decode with no CSV-like fallback re-raises the raw `ValueError`/
`JSONDecodeError` (`client.py:138-144`).

**v2** (`src/potatohedge/errors.py`) raises a closed hierarchy rooted at
`PHClientError`, which carries only *redacted*, structured context
(`errors.py:64-96`, `_redact_context`/`_is_sensitive_key` at `:31-58`):

| v2 exception | When (from `errors.py`) |
|---|---|
| `PHConfigurationError` | Bad `ClientConfig`/`Credential` construction |
| `PHAuthError` | Origin mismatch, or credential tried to override identity headers |
| `PHPermissionError` | No credential configured for the endpoint's capability |
| `PHRequestValidationError` | Client-side request shape invalid |
| `PHTransportError` (base) / `PHTimeoutError` (subclass) | Network/connection failures, timeouts |
| `PHRateLimitError` | 429-class responses |
| `PHAPIError` | Other non-2xx API responses |
| `PHDecodeError` | Response body could not be decoded |
| `PHContractDriftError` | Response violated the pinned contract |

Every instance exposes `.endpoint_id`, `.status`, `.request_id`,
`.retryable`, and a `.context` mapping with all `authorization` /
`credential` / `secret` / `token` / `password` / `cookie` /
`cf-access-client-*` / `*api-key*` values replaced with `"[REDACTED]"`
(`errors.py:9-19, 33-58`).

**Caller-side impact:** any `except requests.exceptions.HTTPError` /
`except requests.exceptions.RequestException` block must become
`except PHClientError` (or a specific subclass) when a caller switches to
v2. Callers that currently do broad `except Exception` (e.g.
`trading_research/core/phclient_wrapper.py:198` inside
`_rate_limited_call`, evidence for that pattern below) are unaffected in
shape but should confirm their retry heuristics still make sense against the
new `.retryable` flag instead of exception type alone.

## 4. Capability model: what "credentials" you need per namespace

Every v2 descriptor declares an `auth_capability` string of the form
`<namespace>.read`. Verified by grepping every `boundary_summary` `"auth"`
value in `docs/generated/CLIENT_REFERENCE.md` — the complete, currently
exposed set is:

`dealer.read`, `flow.read`, `market.read`, `news.read`, `options.read`,
`recipes.read`, `regsho.read`, `signals.read`,
`support_resistance.read`, `volatility.read`.

A caller that only uses dealer + market + options methods (the common
research-repo pattern; see §7) needs exactly those three capability keys in
`ClientConfig.credentials`, not one key per method.

### 4.1 New capabilities beyond `<namespace>.read` (post-#95/#96 surface unrestrict)

PRs #95 (`dbe7f157`) and #96 (`2d2187b8`, merged 2026-08-07) flipped **16
policy rows** from `restricted` to `ordinary` under six capabilities that do
not follow the `<namespace>.read` pattern:

`dealer.bulk.read`, `options.bulk.read`, `market.bulk.read`,
`market.refresh`, `volatility.compute`, `flow.bulk.read`.

**Authority caveat (load-bearing for wave-migration PRs):** the source of
truth for this table is `policy/endpoint-policy.yaml` on `main`, NOT the
generated `docs/generated/CLIENT_REFERENCE.md`. The generator deliberately
fails closed (`contract_drift`: policy hash) until the live conformance
re-run at final re-pin covers the expanded ordinary set, so the 16 methods
below do **not** yet appear in the generated reference and are
policy-authoritative / runtime-pending (`runtime_verified` flips at the
gate-opening conformance PR). **Use this table for mapping and planning
only.** Do not merge caller code against these 16 names yet: none of them
exists in the generated client today, client construction fails until every
descriptor is runtime-verified (`src/potatohedge/client_v2.py:23-29,51`), and
the migration plan consumes the canary-approved regenerated artifact — not
policy-only names — as its input
(`docs/superpowers/plans/2026-07-11-phclient-v2-api-alignment-and-quant-migration.md:663`).
Wave PRs targeting §4.1 methods stay draft/unmerged until the regeneration
lands and exposes the real method signatures.

| Capability | Newly-ordinary v2 endpoint | v1 method it unblocks |
|---|---|---|
| `dealer.bulk.read` | `dealer.positioning` | `get_dealer_positioning` |
| `dealer.bulk.read` | `dealer.weighted_greeks` | `get_dealer_weighted_greeks` |
| `dealer.bulk.read` | `dealer.weighted_greeks_summary` | `get_dealer_weighted_greeks_summary` |
| `flow.bulk.read` | `flow.timeseries` | `get_flow_timeseries` |
| `market.bulk.read` | `market.bulk_snapshot_stock_ohlc` | `get_bulk_snapshot_stock_ohlc` |
| `market.bulk.read` | `market.bulk_snapshot_stock_quote` | `get_bulk_snapshot_stock_quote` |
| `market.refresh` | `market.last_index_price` | `get_last_index_price` |
| `market.refresh` | `market.snapshot_stock_ohlc` | `get_snapshot_stock_ohlc` |
| `options.bulk.read` | `options.bulk_hist_option_eod` | `get_bulk_option_eod` |
| `options.bulk.read` | `options.bulk_hist_option_eod_greeks` | `get_bulk_option_eod_greeks` |
| `options.bulk.read` | `options.bulk_hist_option_open_interest` | `get_bulk_hist_option_open_interest` |
| `options.bulk.read` | `options.bulk_snapshot_option_all_greeks` | `get_bulk_snapshot_option_all_greeks` |
| `options.bulk.read` | `options.bulk_snapshot_option_greeks` | `get_bulk_snapshot_option_greeks` |
| `options.bulk.read` | `options.bulk_snapshot_option_open_interest` | `get_bulk_snapshot_option_open_interest` |
| `options.bulk.read` | `options.bulk_snapshot_option_quote` | `get_bulk_snapshot_option_quote` |
| `volatility.compute` | `volatility.term_structure` | `get_volatility_term_structure` |

A caller that needs any method above must add the corresponding capability
key to `ClientConfig.credentials` in addition to its `<namespace>.read`
keys — e.g. the research-repo pattern (§7) that also calls
`get_dealer_weighted_greeks` needs `dealer.read` + `market.read` +
`options.read` **+ `dealer.bulk.read`**.

**Not everything under these capabilities was unrestricted.** Remaining
rows in `policy/endpoint-policy.yaml` under the same six capabilities (44
rows total: 16 ordinary / 26 restricted / 2 excluded):

- `dealer.bulk.read`: `dealer.batch_positions`, `dealer.batch_summary`,
`dealer.interval_positions` stay `restricted`.
- `flow.bulk.read`: `flow.aggregate_legacy`, `flow.timeseries_legacy`,
`flow.aggregate`, `flow.contracts` stay `restricted`.
- `market.refresh`: `market.snapshot_index_ohlc` and
`market.stock_quote_snapshot` stay `restricted`. Note the #96 alias fix:
`get_snapshot_stock_quote` (v1 `client.py:383`) is the alias of the
**restricted** `market.stock_quote_snapshot` row — it is NOT unblocked,
despite its siblings flipping.
- `options.bulk.read`: 13 bulk rows stay `restricted` (at-time, all-greeks,
second/third-order greeks, hist ohlc/quotes/trade variants);
`options.list_expirations` and `options.list_strikes` are `excluded`
(explicit policy decision — see §6).
- `volatility.compute`: `volatility.intraday`, `volatility.skew`,
`volatility.surface`, `volatility.surface_metrics` stay `restricted`.

The generator continues to gate restricted descriptors out of the runtime
client (`src/potatohedge/catalog/compiler.py:99`, error code
`restricted_descriptor_leak`); the paragraph above describes the policy
layer, which the next regeneration consumes.

## 5. Namespace/method mapping (confirmed, for methods callers actually use)

This table only lists v1 methods this session's scan found in real call
sites (see §8/caller ledger). It is **not** a full v1<->v2 dictionary — v1
`client.py` defines 216 methods; the generated v2 catalog currently exposes
124 namespaced methods total (counted via
`grep -n "^ def " src/potatohedge/client.py | wc -l` = 216, and the
`## client.<ns>.<name>` heading count in `docs/generated/CLIENT_REFERENCE.md`
= 124). For anything not listed here, check
`docs/generated/CLIENT_REFERENCE.md` directly before assuming a 1:1 rename —
several high-traffic v1 methods have **no v2 equivalent yet** (§6).

| v1 method (`client.py` line) | v2 name | Namespace capability |
|---|---|---|
| `list_roots` (:165) | `options.list_roots` | `options.read` |
| `get_hist_stock_eod` (:306) | `market.stock_eod` | `market.read` |
| `get_hist_stock_ohlc` (:261) | `market.stock_ohlc` (route retains `/hist/`, public name drops the `hist_` prefix — confirmed via the method's `temporal_summary.path` = `/api/theta/hist/stock/ohlc/{root}`) | `market.read` |
| `get_index_eod` (:614) | `market.index_eod` | `market.read` |
| `get_index_price` (:592) | `market.index_price` | `market.read` |
| `get_hist_index_ohlc` (:622) | `market.hist_index_ohlc` | `market.read` |
| `get_last_trading_day` (:1586) | `market.last_trading_day` | `market.read` |
| `get_trading_days` (:1638) | `market.trading_days` | `market.read` |
| `get_market_schedule` (:1676) | `market.market_schedule` | `market.read` |
| `is_market_open` (:1925) | `market.is_market_open` | `market.read` |
| `get_yield_curve` (:1419) | `market.yield_curve` | `market.read` |
| `get_stock_dividends` (:322) | `market.stock_dividends` | `market.read` |
| `get_stock_splits` (:314) | `market.stock_splits` | `market.read` |
| `get_dealer_positions` (:2153) | `dealer.positions` | `dealer.read` |
| `get_hist_option_quote` (:408) | `options.hist_option_quote` | `options.read` |
| `get_hist_option_trade` (:441) | `options.hist_option_trade` | `options.read` |
| `get_hist_option_ohlc` (:745) | `options.hist_option_ohlc` | `options.read` |
| `get_hist_option_open_interest` (:753) | `options.hist_option_open_interest` | `options.read` |
| `get_hist_option_implied_volatility` (:761) | `options.hist_option_implied_volatility` | `options.read` |
| `get_snapshot_option_quote` (:1122) | `options.snapshot_option_quote` | `options.read` |

Worked-example methods for the two currently-mapped, highest-traffic
patterns are in §7.

## 6. Known gaps — v1 methods actually used by callers with NO current v2 equivalent

> **Status update (2026-08-07, post-#95/#96):** most rows below were
> resolved by the surface-unrestrict flip — see §4.1 for the authoritative
> capability table and the runtime-pending caveat. Each row's Status cell
> now records the post-flip disposition. Rows marked **RESOLVED** have an
> ordinary policy row awaiting the conformance-gated regeneration; rows
> marked **EXCLUDED** are explicit policy decisions to drop the surface
> (callers need a replacement plan, not a wait); remaining **OPEN** rows
> still need disposition.

| v1 method | Used by (highest-traffic evidence) | Status |
|---|---|---|
| `get_dealer_weighted_greeks` (:1717) / `get_dealer_weighted_greeks_summary` (:1799) | `trading_research` (237 call-site hits across the repo), `dealer_positioning` (96), `ph_videos/fetch_dealer_book.py:46`, `dealer_positioning/scripts/diagnose_gamma_sign.py:54` | **RESOLVED (#96)** — `dealer.weighted_greeks{,_summary}` are `ordinary` under `dealer.bulk.read` (§4.1); runtime-pending until the conformance-gated regeneration. |
| `get_dealer_positioning` (:2498) | `PH_API/ph_data_loader.py:95,170` (PH_API is itself a caller — see caller ledger) | **RESOLVED (#96)** — maps to `dealer.positioning` (`ordinary`, `dealer.bulk.read`); runtime-pending. Still confusable-by-name with `get_dealer_positions` (:2153 → `dealer.positions`, `dealer.read`) — the two are DISTINCT v2 endpoints; verify which one each caller intends before migrating. |
| `get_dealer_positions_optimized` (:2319) | not directly hit in this session's targeted greps, but defined adjacent to the two above; flagged for completeness | **RESOLVED** — now a policy alias of `dealer.positions` (`ordinary`, `dealer.read`) per `policy/endpoint-policy.yaml` `aliases: [get_dealer_positions, get_dealer_positions_optimized]`. Callers migrate to `dealer.positions`. |
| `list_expirations` (:184) | 30 call-site hits (trading_research + dealer_positioning combined) | **EXCLUDED** — `options.list_expirations` carries `exposure: excluded` in `policy/endpoint-policy.yaml` (explicit decision, no v2 method will exist). Callers need a replacement plan (candidate: derive from `options.list_option_contracts`). |
| `list_strikes` (:204) | 19 call-site hits | **EXCLUDED** — `options.list_strikes` `exposure: excluded`; same replacement path as `list_expirations`. |
| Entire `get_bulk_*` family (`get_bulk_option_eod`, `get_bulk_option_eod_greeks`, `get_bulk_hist_option_open_interest`, `get_bulk_snapshot_option_quote`, `get_bulk_snapshot_option_open_interest`, `get_bulk_snapshot_option_all_greeks`, `get_bulk_snapshot_option_greeks`, `get_bulk_snapshot_stock_ohlc`, and siblings) | Individually 17-126 call-site hits each; collectively the single largest gap by call-site volume | **PARTIALLY RESOLVED (#96)** — 7 `options.bulk.read` rows + `market.bulk_snapshot_stock_{ohlc,quote}` are now `ordinary` (full list §4.1), covering every bulk method named in the evidence column. 13 further `options.bulk.read` rows (at-time, all-greeks-hist, second/third-order greeks, hist ohlc/quotes/trade variants) stay `restricted` — callers relying on those specific variants remain blocked and must be flagged per-caller in the wave PRs. |
| `get_ctb_timeseries` (:5692) and the whole `get_ctb_*` family (cost-to-borrow: `get_ctb_distribution`, `get_ctb_drop_events`, `get_ctb_spike_events`, `get_ctb_term_structure`, `get_ctb_velocity`, `get_ctb_forward_returns_matrix`, `get_ctb_health`) | 52 call-site hits for `get_ctb_timeseries` alone | **EXCLUDED** — a `ctb` namespace DOES exist in `policy/endpoint-policy.yaml` (9 `ctb.*` rows under `ctb.read`/`ctb.stream`; the related health probe lives separately as `system.ctb_health` under `system.health`), and every one of those rows is `exposure: excluded` — an explicit policy decision to drop the CTB surface from v2, not an oversight. CTB-dependent callers need a replacement plan or the call sites removed. |
| `get_daily_summary` (:1483) | 17 call-site hits | **OPEN** — no policy row carries this alias. A possibly-related `system.db_daily_option_summary` row exists (`exposure: excluded`, `system.infra.read`) but with an empty alias list, so the binding is unconfirmed — do not treat as resolved without an explicit policy disposition. |
| `get_flow_timeseries` (:2590) | 17 call-site hits | **RESOLVED (#96)** — `flow.timeseries` (`ordinary`, `flow.bulk.read`); runtime-pending. Note `flow.timeseries_legacy` / `get_flow_timeseries_legacy` remains `restricted`. |
| `get_volatility_term_structure` (:1938) | 14 call-site hits | **RESOLVED (#96)** — `volatility.term_structure` (`ordinary`, `volatility.compute`); runtime-pending. |
| `get_last_index_price` (:655), `get_snapshot_stock_ohlc` (:391), `get_snapshot_stock_quote` (:383) | 28, 28, 49 call-site hits respectively | **MIXED (#96)** — `market.last_index_price` and `market.snapshot_stock_ohlc` are `ordinary` under `market.refresh` (runtime-pending). `get_snapshot_stock_quote` maps (post-#96 alias fix) to `market.stock_quote_snapshot`, which stays **`restricted`** — the 49 call sites using it remain blocked and must be flagged per-caller in the wave PRs. |

**Recommendation for the release-readiness program (updated 2026-08-07):**
the #95/#96 flip resolves the highest-traffic rows (weighted-greeks,
positioning, the evidenced bulk set, flow timeseries, volatility term
structure) pending the conformance-gated regeneration. Still requiring
per-caller handling in the migration waves: (a) `get_snapshot_stock_quote`
(restricted, 49 hits), (b) the 13 still-restricted `options.bulk.read`
variants where callers use them, (c) EXCLUDED surfaces
(`list_expirations`/`list_strikes`/CTB family) which need caller-side
replacement plans, and (d) `get_daily_summary` (OPEN — needs policy
disposition). Wave PRs must not silently drop call sites in these four
buckets; each needs an explicit per-caller disposition in the PR body.

### 6.1 Certified-surface narrowings — released methods with an uncertified parameterization

A method being `ordinary` (§4.1) says the ROUTE is in the release. It does
not promise that every *parameterization* of that route was covered by the
conformance run. Where the two differ, the policy row carries a
`certification_exclusions` entry and the generator projects it to
**`policy/release-scope/certification-exclusions.yaml`** — read that file,
not this table, as the machine-readable source. The conformance runner
enforces coherence: if a recipe ever sends an excluded parameter, the run
aborts with `certification_exclusion_violated` rather than recording an
observation the policy says does not exist.

| Method | Uncertified parameterization | Why | Review by |
|---|---|---|---|
| `dealer.positioning` | any call passing **`root`** | At the pinned PH_API revision, `root` combined with an `end_date` earlier than today raises `asyncpg.InterfaceError: the server expects 3 arguments for this query, 4 were passed` and the route answers 500 (`PH_API routes/postgres/postgres_data_router.py:293-299`). The conformance anchor therefore exercises the route **without** `root`, so the root-filtered form carries no observation. | 2026-11-07 |

**What this means for a v2 caller.** `dealer.positioning` is callable and
its unfiltered form is certified. You may still pass `root` — the client
does not block it — but you are outside the certified surface and, at this
revision, a historical `end_date` plus `root` will fail. Until the PH_API
fix lands and a re-pinned conformance run covers it, filter by root
client-side, or send `root` only with a current-day `end_date`.

## 7. Worked examples

### 7a. `dealer.positions` (confirmed mapping)

v1, `dealer_positioning/core/data_loader/positions.py:407`:

```python
raw_data = self.client.get_dealer_positions(
root=root, use_csv=True, ...
)
```

v2 equivalent shape (namespaced method call; params per
`docs/generated/CLIENT_REFERENCE.md:157-190` — `root` is still required,
`use_csv` still exists, plus optional `expiration`, `max_dte`, `strike`,
`trade_right`, `start_date`, `end_date`, `latest_only`, `level`,
`positioning_days`):

```python
from potatohedge.client_v2 import PHClient
from potatohedge.config import ClientConfig, Credential

client = PHClient(ClientConfig(
base_url="https://api.potatohedge.com",
credentials={"dealer.read": Credential(headers={...})},
caller_id="dealer-positioning",
))
envelope = client.dealer.positions(root=root, use_csv=True)
raw_data = envelope.data
```

**Return shape (applies to every generated v2 method):** methods return a
`ResponseEnvelope[T]` (`src/potatohedge/envelopes.py:22`), not the bare
payload — the caller's data lives under `.data` (with `metadata` and
`endpoint_id` alongside). The migration plan requires unwrapping it
(`docs/superpowers/plans/2026-07-11-phclient-v2-api-alignment-and-quant-migration.md:639`).
A wave PR that assigns the method result directly where v1 returned a
list/dict silently replaces data with an envelope object.

### 7b. `market.stock_eod` (confirmed mapping)

v1, `dealer_positioning/scripts/create_sr_viz_enhanced.py:115` (async
caller, via the wrapper's rate-limited proxy):

```python
data = await client.get_hist_stock_eod(root=root, start_date=..., end_date=...)
```

v2:

```python
data = client.market.stock_eod(root=root, start_date=..., end_date=...).data
# or, on AsyncPHClient: (await client.market.stock_eod(...)).data
```

### 7c. `dealer.weighted_greeks` — policy-resolved, runtime-blocked (do not merge yet)

v1, `ph_videos/fetch_dealer_book.py:46`:

```python
result = client.get_dealer_weighted_greeks(root=root, ...)
```

Post-#96 this is **no longer a catalog gap**: `dealer.weighted_greeks` is
`ordinary` under `dealer.bulk.read` (§4.1). But there is still **no v2 call
to write here today** — the method does not exist in the generated client
until the conformance-gated regeneration lands. Callers on this path
(`trading_research`, `dealer_positioning`, `ph_videos`, and `PH_API`'s own
`get_dealer_positioning`/weighted-greeks readers) stay on v1 for this
surface until regeneration exposes the method; wave PRs may draft the
mapping but not merge it (§4.1 authority caveat). Do not route around this
by calling PH_API's `/api/dealer/weighted-greeks/{root}` HTTP route
directly — that bypasses the v2 catalog/policy gate.

## 8. Non-SDK (direct HTTP) callers

Some inventoried and newly-discovered callers do not import the
`potatohedge` Python package at all — they call PH_API HTTP routes directly.
This guide's import/error/config mapping does not apply to them; their
migration path is route/version alignment plus adopting the
`PH-Client-Version` / `PH-Caller-Id` attribution headers the plan describes
(`docs/superpowers/plans/2026-07-11-phclient-v2-api-alignment-and-quant-migration.md:199-205`),
not an import rewrite:

- **`ph-discord`** and the newly-discovered **`ph-react-shared`** /
**`ph-ui`** (§ caller ledger) — JavaScript, `axios`-based `PHApi` class in
`ph-react-shared/src/services/phApi.js`, base URL
`https://api.potatohedge.com` (verified `phApi.js:12,211`). No Python
import surface exists for these.
- **`thetadata-api-direct-client`** — `requests`-based hand-rolled client in
`thetadata-api/thetadata/theta_client.py`, independent of the
`potatohedge` package.
- **`ph-api-cron`** (`PH_API/cron/warm_ticker_cache.py`) and
**`ph-api-service`** (`PH_API/ph_mcp/server.py`) — both use `httpx`
directly against PH_API's own routes, not the `potatohedge` SDK.

## 9. Open questions for the operator / program

1. ~~Bulk-family and CTB-family v2 coverage (§6) — expand catalog or accept
loss of that surface for the affected callers?~~ **RESOLVED 2026-08-07 by
#95/#96 + policy:** evidenced bulk set flipped `ordinary` (§4.1); CTB
family is `excluded` by explicit policy decision. Remaining per-caller
work: replacement plans for EXCLUDED surfaces and dispositions for
still-restricted variants (§6 recommendation buckets a-c).
2. `get_dealer_positioning` vs `get_dealer_positions` — **PARTIALLY
RESOLVED:** both now have ordinary v2 targets (`dealer.positioning`
under `dealer.bulk.read` vs `dealer.positions` under `dealer.read`).
The remaining per-call-site question (which one each PH_API call site
intends) still needs answering in the PH_API self-caller wave PR.
3. ~~`dealer.weighted_greeks{,_summary}` restricted-exposure status~~
**RESOLVED 2026-08-07 by #96:** both `ordinary` under
`dealer.bulk.read`; runtime-pending until the conformance-gated
regeneration (§4.1).
4. `ph-react-shared` / `ph-ui` were not in the original 11-caller inventory
(`inventory/caller-universe.yaml`). Adding them (schema-conformant
caller rows) is a **wave-merge gate**, not a recommendation: the caller
ledger's exhaustion gate blocks every wave merge until they are added
and the two uninspected surfaces are itemized (ledger, Stats block).
This guide does not edit the inventory file itself (single-generator /
scope discipline); the addition lands as its own small PR. **Still
open.**
5. `get_daily_summary` (§6, OPEN) — needs an explicit policy disposition:
bind to `system.db_daily_option_summary` (currently `excluded`, empty
alias list) or classify as a distinct dropped surface.