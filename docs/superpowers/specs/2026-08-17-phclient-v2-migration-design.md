# PHClient v2 Migration — Design Spec

**Status:** Draft for review. No code changes made.
**Date:** 2026-08-17
**Scope:** Replace the hand-rolled REST transport in `shared/thetadata.py::ThetaDataController` with the `potatohedge` v2 package, keeping the existing 29-method public facade stable so the 4 suites and orchestrator require zero caller-visible changes.

---

## 1. Recommended Approach

**Facade replacement with a v2-backed transport.**

- Keep `ThetaDataController`'s public method names, signatures, and return types exactly as they are.
- Replace `self.client = httpx.Client(...)` + `self._get()` / `self._get_with_retry()` + `self._parse_rows()` with a `PHClient` instance.
- Each facade method translates its arguments into the matching v2 call, unwraps `env.data`, normalizes shape, and returns the same Python types the suites already consume.
- `close()` becomes `client.close()` on the `PHClient` context manager.

This is the lowest-risk path: suites never see a new import or a new method name. The migration is a single-file rewrite of the transport layer plus a new dependency in `requirements.txt`.

### Why not the alternatives

| Alternative | Verdict | Reason |
|---|---|---|
| Parallel v2 client, migrate suites method-by-method | Rejected | Touches every suite, orchestrator, and dashboard call site. High blast radius, no benefit over a single-file facade swap. |
| Narrow fix only (kill MOCK loop, fix dead diagnostic scripts) | Deferred | Worth doing *after* the migration so the dead-endpoint warnings are gone and the MOCK 404s stop polluting logs. The migration itself does not require the MOCK loop to be found first. |
| Full v2 surface exposure (suites call v2 directly) | Rejected | Suites rely on hand-rolled retry, chunking, `_last_bar_per_date`, column-name normalization, and fallback logic that the v2 client does not provide. That logic stays in the facade. |

---

## 2. Wheel / Packaging Facts

| Item | Value |
|---|---|
| Wheel | `potatohedge-2.0.1-py3-none-any.whl` |
| SHA256 | Matches manifest `artifact_sha256` (`b74ecb9e...9f2b4b`) — verified at download time |
| Location | `C:\Users\bottl\FinancialDevelopment\potatohedge-2.0.1-py3-none-any.whl` |
| Requires-Python | `>=3.10` (repo uses 3.12 — compatible) |
| Core deps | `httpx>=0.27`, `aiohttp>=3.8`, `pyyaml>=6.0`, `requests>=2.28`, `typing-extensions>=4`, `urllib3>=1.26`, `python-dotenv>=0.19` |
| Conflicts | None expected: repo already has `httpx` (used by current client) and `requests` (used elsewhere). `pyyaml` is new but safe. |
| Install target | Root `.venv` (`C:\Users\bottl\FinancialDevelopment\.venv\Scripts\python.exe`) — affects all 4 suites + orchestrator + dashboard. This is correct because `shared/` is imported by everything. |

**Note:** The wheel's `METADATA` does **not** list `pydantic` in `Requires-Dist` (it is only an extra: `models`). The beta note's reference to pydantic is misleading; it is **not** a transitive install requirement unless the caller uses the optional models layer. We do not need it.

---

## 3. Credential / Client Construction

Current code reads `THETADATA_CF_ACCESS_CLIENT_ID` and `THETADATA_CF_ACCESS_CLIENT_SECRET` from env / `.env`, builds a single headers dict, and passes it to every request.

v2 requires a per-capability `Credential` mapping. In practice the beta note and vendor examples reuse the **same** CF Access token dict across every capability — the shape just requires one dict entry per capability key.

**Planned construction:**

```python
from potatohedge.client_v2 import PHClient
from potatohedge.config import ClientConfig, Credential

_CRED = Credential(headers={
    "CF-Access-Client-Id": os.environ["THETADATA_CF_ACCESS_CLIENT_ID"],
    "CF-Access-Client-Secret": os.environ["THETADATA_CF_ACCESS_CLIENT_SECRET"],
})

_CONFIG = ClientConfig(
    base_url="https://api.potatohedge.com",
    credentials={
        "market.read": _CRED,
        "market.bulk.read": _CRED,
        "options.read": _CRED,
        "options.bulk.read": _CRED,
        "dealer.read": _CRED,
        "dealer.bulk.read": _CRED,
        "volatility.read": _CRED,
        "volatility.compute": _CRED,
        "flow.read": _CRED,
        "flow.bulk.read": _CRED,
        "signals.read": _CRED,
        "regsho.read": _CRED,
        "support_resistance.read": _CRED,
        "recipes.read": _CRED,
        "news.read": _CRED,
    },
    caller_id="zinko",   # per beta note + existing caller_id in inventory
    client_version="2",
)

# Inside ThetaDataController.__init__:
#   self._v2 = PHClient(_CONFIG)
#   self._as_v2 = self._v2.__enter__()   # or use context-manager in close()
```

All 140 endpoints in the wheel's descriptor catalog are `runtime_verified` — the conformance gate will not block construction.

---

## 4. Method Mapping Table

Current facade methods → v2 endpoint calls. "Unwrap" means `env.data`; "Normalize" means any shape conversion the suite code expects.

| Facade method | v2 call | Auth | Unwrap / Normalize |
|---|---|---|---|
| `list_expirations` | `client.options.list_roots(data_type="option")` | `options.read` | Unwrap list; return as `List[str]` |
| `resolve_longest_history_expiry` | iterate `list_expirations` + `client.options.options_chain(ticker=..., date=...)` history depth probe | `options.read` | Same logic as today, just v2 calls |
| `list_strikes` | `client.options.options_chain(ticker=..., expiration=..., date=...)` → extract strikes | `options.read` | **Route changed**: old `/api/theta/list/strikes/{root}/{exp}` no longer exists in v2/v3; strikes come from `options_chain` response |
| `stock_snapshot_quote` | `client.market.bulk_snapshot_stock_quote(root=...)` | `market.bulk.read` | **Route changed**: old `/api/theta/snapshot/stock/quote/{root}` renamed to `bulk_snapshot_stock_quote` |
| `stock_snapshot_trade` | `client.market.snapshot_stock_trade(root=...)` | `market.read` | Unwrap last trade |
| `option_snapshot_quote` | `client.options.snapshot_option_quote(root=..., exp=..., strike=..., right=...)` | `options.read` | Unwrap |
| `option_bulk_greeks` | `client.options.bulk_snapshot_option_greeks(root=..., exp=...)` | `options.bulk.read` | Unwrap rows |
| `option_bulk_greeks_second_order` | `client.options.bulk_snapshot_option_all_greeks(root=..., exp=..., ...)` | `options.bulk.read` | **Route changed**: old `/api/theta/bulk_snapshot/option/greeks_second_order/{root}/{exp}` dropped in v2/v3; second-order greeks come from `bulk_snapshot_option_all_greeks` and are filtered to vanna/charm/vomma/veta columns |
| `option_bulk_oi` | `client.options.bulk_snapshot_option_open_interest(root=..., exp=...)` | `options.bulk.read` | Unwrap |
| `option_bulk_oi_latest` | same endpoint with `force_refresh=True` or fallback to `dealer.positioning(level="strike", latest_only=True)` | `options.bulk.read` | Keep existing fallback logic |
| `option_hist_eod_single` | `client.options.hist_option_eod(root=..., exp=..., strike=..., right=..., start_date=..., end_date=...)` | `options.read` | Unwrap; `_last_bar_per_date` |
| `option_hist_open_interest_single` | `client.options.hist_option_open_interest(root=..., exp=..., strike=..., right=..., start_date=..., end_date=...)` | `options.read` | **This replaces the deleted per-strike endpoint.** Param name changed from `strike_theta` → `strike`. v2 has it live — 404 concern resolved. |
| `option_hist_all_greeks_single` | `client.options.hist_option_all_greeks(root=..., exp=..., strike=..., right=..., start_date=..., end_date=..., ivl=...)` | `options.read` | Same as above — v2 endpoint exists; param `strike_theta` → `strike` |
| `option_bulk_hist_eod` | `client.options.bulk_hist_option_eod(root=..., exp=..., start_date=..., end_date=...)` | `options.bulk.read` | Unwrap; dedupe by date |
| `option_bulk_hist_eod_greeks` | `client.options.bulk_hist_option_eod_greeks(root=..., exp=..., start_date=..., end_date=..., ...)` | `options.bulk.read` | Unwrap; normalize right/date |
| `option_bulk_hist_greeks` | `client.options.bulk_snapshot_option_all_greeks(...)` — or keep thread-pool fan-out to `hist_option_all_greeks` for single-contract history | `options.bulk.read` or `options.read` | Keep chunking logic |
| `option_bulk_hist_oi` | `client.options.bulk_hist_option_open_interest(root=..., exp=..., start_date=..., end_date=...)` | `options.bulk.read` | Unwrap; dedupe by date |
| `option_bulk_hist_oi_by_day` | same endpoint — it returns whole chain per day already | `options.bulk.read` | Unwrap; stamp date |
| `fetch_spot_price` | `client.market.stock_eod(root=..., start_date=..., end_date=...)` → latest close, or `bulk_snapshot_stock_quote` | `market.read` / `market.bulk.read` | Three-layer fallback preserved |
| `hist_stock_eod` | `client.market.stock_eod(root=..., start_date=..., end_date=..., adjusted=...)` | `market.read` | Paginate into ≤28-day chunks (existing logic) |
| `hist_stock_ohlc` | `client.market.stock_ohlc(root=..., start_date=..., end_date=..., ivl=..., rth=...)` | `market.read` | Same chunking |
| `fetch_option_iv` | `client.options.hist_option_implied_volatility(...)` or `options.snapshot_option_quote` for ATM | `options.read` | Keep existing ATM-strike selection logic |
| `fetch_risk_free_rate` | `client.market.yield_curve(start_date=..., end_date=..., target_date=...)` | `market.read` | Interpolate to tenor T |
| `fetch_dividend_yield` | `client.market.stock_dividends(root=..., start_date=..., end_date=...)` | `market.read` | TTM calculation |
| `fetch_realized_vol` | `client.market.stock_eod(...)` → close-to-close log returns | `market.read` | Existing annualization logic |
| `fetch_beta` | `client.market.stock_eod(...)` for stock + SPY → cov/var | `market.read` | Same as today |
| `get_dealer_positioning` | `client.dealer.positioning(root=..., expiration=..., latest_only=True, level="contract")` | `dealer.bulk.read` | Normalize CSV/JSON; keep GEX derivation |
| `get_yield_curve` | `client.market.yield_curve(start_date=..., end_date=..., target_date=..., use_csv=...)` | `market.read` | Same as `fetch_risk_free_rate` but full curve |
| `close` | `self._v2.close()` | — | — |

**Key insight:** The two "deleted endpoint" methods (`option_hist_open_interest_single`, `option_hist_all_greeks_single`) **do exist in v2** under the `options.hist_option_*` namespace. The vendor note's claim that they were deleted appears to refer to v1 naming / old routing, not to v2. The migration actually *fixes* the 404s by moving to the live v2 endpoints.

---

## 5. Architecture

```
shared/thetadata.py
├── ThetaDataController
│   ├── __init__()
│   │   ├── load_env_once()
│   │   ├── build Credential + ClientConfig
│   │   ├── self._v2 = PHClient(_CONFIG).__enter__()
│   │   └── self._caller_id = "zinko"
│   │
│   ├── _v2_call(namespace_method, **kwargs) → Any
│   │   ├── getattr(self._v2, namespace).method(**kwargs)
│   │   ├── unwrap ResponseEnvelope → env.data
│   │   ├── raise typed PHClientError on failure
│   │   └── return raw data
│   │
│   ├── [29 public methods — signatures unchanged]
│   │   ├── translate args → v2 kwargs
│   │   ├── call _v2_call()
│   │   ├── normalize shape (list-of-lists → list-of-dicts, etc.)
│   │   ├── apply existing chunking / retry / fallback logic
│   │   └── return same types as today
│   │
│   └── close()
│       └── self._v2.close()
│
├── _RETRY_ATTEMPTS, _RETRY_STATUSES (retained)
├── _HIST_GREEKS_CONCURRENCY (retained)
├── _last_bar_per_date() (retained)
└── _normalize_date(), _coerce_number() (retained)
```

**What moves to v2:**
- HTTP transport (`httpx.Client` → `SyncTransport`)
- Base URL, headers, auth (CF Access token → `Credential`)
- Retry on 429 / 5xx (v2 transport has its own retry — we can disable it and keep our linear backoff, or adopt v2's exponential backoff)
- `_parse_rows` for list-of-lists responses (v2 already decodes CSV to tuple-of-dicts via `_decode`)

**What stays in the facade:**
- Chunking logic for long date ranges (≤28-day windows)
- `_last_bar_per_date` deduplication
- Fallback chains (`stock_snapshot_quote` → trade → EOD)
- Column-name normalization (`right` → single char, date stamping)
- Dealer GEX derivation from raw positioning data
- Beta / realized vol / dividend yield math

---

## 6. Error Handling

v2 maps HTTP failures to typed exceptions:

| v2 exception | When | Facade behavior |
|---|---|---|
| `PHRateLimitError` (.retryable=True) | HTTP 429 | Retry with our linear backoff, then surface to caller |
| `PHAuthError` | HTTP 401 | Log + raise `RuntimeError` with env-var hint (same as today) |
| `PHPermissionError` | HTTP 403 | Raise — should never happen if credentials are correct |
| `PHAPIError` | Other non-2xx | Raise with `.status` and `.endpoint_id` |
| `PHContractDriftError` | Response schema drift | Raise — unexpected, needs vendor alert |
| `PHDecodeError` | Body parse failure | Raise — indicates a real problem |

**Important:** v2's own retry logic (`_max_attempts`, `_backoff`) is configurable per-descriptor but not user-overridable per call. Since our current code uses **linear** backoff for per-contract fan-outs (to avoid amplifying latency), we should either:
1. Set v2 retry to 1 attempt for bulk/hist methods and keep our own retry wrapper, OR
2. Accept v2's exponential backoff and remove our retry loop for those methods.

**Recommendation:** Option 1 — keep our `_get_with_retry` semantics for fan-out methods, set v2 transport `max_attempts=1` by passing no retry config (the default is descriptor-driven, and we can't override it). Actually, v2 does not expose a `max_attempts` override on `ClientConfig`. So we either rely on v2's built-in retry (which is exponential, not linear) or wrap the `send` function ourselves. The simpler path: **accept v2's retry policy for now**; if latency becomes an issue in fan-outs, we can revisit with a custom transport wrapper.

---

## 7. Testing Strategy

1. **Wheel smoke test first.** Before editing `thetadata.py`, run a standalone script that constructs `PHClient`, calls 3-4 key endpoints with the existing CF token, and prints `env.status` + `env.data` shapes. This validates the install + credential + network path independently of the suite.

2. **Unit tests (existing).** `Vol_Suite/tests/test_thetadata_client.py` already mocks `_get_with_retry` and tests `option_hist_all_greeks_single`, `option_hist_open_interest_single`, etc. After migration, those mocks need to target the v2 methods instead. The test logic stays the same; only the mock target changes.

3. **Contract test (new).** Add `shared/tests/test_phclient_v2_contract.py` that:
   - Constructs `ThetaDataController` and verifies all 29 public methods exist with the right signatures.
   - Calls each method once against live API with a 30s timeout, checks return type, and asserts no `PHClientError` is raised.
   - This is the "did the migration break anything?" smoke test.

4. **Suite integration.** Run `pytest tests/ -q` and `pytest Vol_Suite/tests/ -q` after migration. Fix any mocks that break.

---

## 8. Implementation Phases

| Phase | Task | Risk |
|---|---|---|
| **0. Install** | `pip install C:\Users\bottl\FinancialDevelopment\potatohedge-2.0.1-py3-none-any.whl` into root `.venv`. Verify `import potatohedge.client_v2` works. | Low |
| **1. Smoke test** | Standalone script: build `PHClient`, call `market.is_market_open`, `options.list_roots`, `market.yield_curve`. Confirm live. | Low |
| **2. Facade rewrite** | Replace `httpx.Client` + `_get` / `_get_with_retry` / `_parse_rows` with `PHClient` transport. Keep all 29 method bodies; replace internals. | Medium — this is the bulk edit |
| **3. Error mapping** | Add `except PHClientError as exc` blocks in each method that currently catches `httpx.HTTPStatusError` / `httpx.TransportError`. | Low |
| **4. Update mocks** | Fix `Vol_Suite/tests/test_thetadata_client.py` mocks to target v2 namespace methods. | Low |
| **5. Run full test suite** | `pytest tests/ Vol_Suite/tests/ -q`. Fix breakage. | Medium |
| **6. Live sanity check** | Run orchestrator on `--ticker SPY --expiry <near>` for one full suite run. Confirm no 404s in deleted-endpoint category. | Low |
| **7. MOCK loop + dead diag scripts** | Post-migration: find/kill the MOCK 404 loop, update or delete the dead diagnostic scripts that call the old per-strike endpoints. | Low (deferred) |

**Explicit non-goals for this phase:**
- Do not expose v2 namespace methods directly to suites.
- Do not change any suite call sites.
- Do not touch the MOCK loop or diagnostic scripts until the migration is live and verified.
- Do not change `caller_id` without confirming with the vendor that "zinko" is the correct identity for this caller universe.

---

## 9. Open Decisions (user input needed)

1. **Caller ID:** Confirm `caller_id="zinko"` is correct. The beta note says "use `zinko`". Our root `.env` has `THETADATA_CF_ACCESS_CLIENT_ID/_SECRET` which implies the existing caller is `zinko` in the vendor's `caller-universe.yaml`. But this is the vendor's internal manifest — confirm before shipping.

2. **v2 retry policy adoption:** Should we accept v2's built-in exponential retry for bulk fan-outs, or do you want me to wrap the transport to enforce linear backoff for per-contract pools? The current linear backoff (0.5s, 1.0s, 1.5s) was tuned for fan-out latency — changing it without measurement could slow things down.

3. **`pyyaml` in `requirements.txt`:** The beta note says it's required transitively as of 2.0.1. The wheel `METADATA` confirms it. Add it explicitly? (It will be pulled in anyway, but explicit is better for reproducibility.)

---

## 10. Files Changed

| File | Change |
|---|---|
| `requirements.txt` | Add `potatohedge==2.0.1` (or wheel path) + `pyyaml>=6.0` |
| `shared/thetadata.py` | Rewrite transport layer; keep 29-method facade |
| `Vol_Suite/tests/test_thetadata_client.py` | Update mock targets from `_get_with_retry` to v2 namespace methods |
| `shared/tests/test_phclient_v2_contract.py` | New — contract smoke test |
| `.whl-inspect/` | Temporary extraction dir — delete after spec approved |

No suite files, no orchestrator, no dashboard changes.
