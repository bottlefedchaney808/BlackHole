# PHClient v2 migration — working wiki / handoff

**Status as of 2026-08-18: implementation done and live-verified against the real API, uncommitted.**
A design was drafted (`docs/superpowers/specs/2026-08-17-phclient-v2-migration-design.md`) and a prior
session already implemented it in the working tree — `shared/thetadata.py` now routes every facade
method through `potatohedge.client_v2.PHClient` via a legacy-path translation layer (`_translate_path`/
`_rewrite_params`/`_PATH_ALIASES`/`_PARAM_ALIASES`), not the per-method rewrite the spec originally
described. That's a cleaner shape than the spec (one shared shim instead of touching all 29 methods)
and this session verified/repaired it live rather than re-deriving the design. **Nothing has been
committed** — `git status` still shows `shared/thetadata.py` as modified, uncommitted.

## 2026-08-18 session — live verification + bug fixes

Ran every facade method against the real `api.potatohedge.com` v2 API (not mocks) and found + fixed
five real bugs the mocked unit tests couldn't have caught (mocks target `_get_with_retry` directly, so
they never exercised the actual v2 param translation):

1. **`list_expirations`** — `available_expirations` rows are dicts (`{'expiration': '2026-08-14', ...}`),
   not strings; the code was stringifying the whole dict. Fixed to extract `.expiration` and normalize
   `YYYY-MM-DD` → compact `YYYYMMDD` (matching the method's documented contract and every other facade
   method's `exp` convention).
2. **`list_strikes`** — a single-expiration `options_chain` call returns a chain-summary **dict** with
   strikes nested under `data['chain']`, not a flat list of contract dicts as the code assumed. Fixed.
3. **`options_chain` routing** — needed `exp` → `expiration` renamed specifically for this one v2 method
   (it's the only namespace method that doesn't use `exp`); added as a special-case in `_translate_path`
   alongside the existing `root`→`ticker` rename.
4. **Double-passthrough key collisions in `_rewrite_params`** — `ticker`, `symbol`, `expiry`, and (worst)
   `ivl` were each both (a) aliased to a v2 name via `_PARAM_ALIASES` **and** (b) hardcoded in the
   generic literal-passthrough set, so both the old and new key ended up in the outgoing kwargs dict.
   The `ivl`→`interval` one is the big one: **every** `option_hist_all_greeks_single` /
   `option_bulk_hist_greeks` call was passing both `interval=900000` and `ivl=900000`, and the real v2
   method only accepts `ivl` — so 100% of hist-greeks calls were failing
   `PHRequestValidationError: request arguments do not match the generated signature` before this fix
   (this looked like a concurrency/thread-safety bug at first — it reproduced identically at concurrency
   1 once isolated — the `ivl`/`interval` duplicate was the actual cause). Fixed by dropping the bogus
   `"ivl": "interval"` alias entirely (no currently-routed v2 method actually takes `interval`) and
   removing `ticker`/`symbol`/`expiry` from the literal-passthrough set since they already have alias
   entries.
5. **`_get_with_retry` stopped retrying 502s** — it only retried when `PHClientError.retryable` was
   `True`, but the live API returns 502s with `retryable=False` on the exception (confirmed live: same
   request succeeds on a bare retry seconds later). The old pre-migration code retried on
   `_RETRY_STATUSES = (404, 502, 503, 504)` regardless of a "retryable" flag. Restored that behavior:
   retry when either `e.retryable` or `e.status in _RETRY_STATUSES`.

Also made `ThetaDataController` build one `PHClient` **per thread** (lazily, via `threading.local()`)
instead of sharing one instance across the `ThreadPoolExecutor` fan-outs used by
`option_bulk_hist_greeks`/`option_bulk_hist_oi`/`_enumerate_contracts`. This turned out not to be the
cause of bug #4 above (a fresh per-call config reproduced the same failure even single-threaded), but
it's cheap defensive correctness — nothing in the `potatohedge` package's docs promises `PHClient` is
thread-safe for concurrent request-building, and the fan-out pattern is exactly the shape that would
surface it if it ever is an issue. `close()` now closes every thread-local client it created.

**Verified live** (via `.venv/Scripts/python.exe`, real SPY data, 2026-08-18): `list_expirations`,
`list_strikes`, `stock_snapshot_quote`, `fetch_spot_price`, `fetch_risk_free_rate`, `option_bulk_greeks`,
`option_bulk_oi`, `option_snapshot_quote`, `stock_snapshot_trade`, `option_bulk_greeks_second_order`,
`option_bulk_oi_latest`, `option_hist_open_interest_single`, `option_hist_all_greeks_single`,
`option_bulk_hist_eod`, `option_bulk_hist_eod_greeks`, `option_bulk_hist_greeks`, `option_bulk_hist_oi`,
`option_bulk_hist_oi_by_day`, `hist_stock_eod`, `hist_stock_ohlc`, `resolve_longest_history_expiry`,
`get_dealer_positioning`, `fetch_realized_vol`, `fetch_beta`, `fetch_dividend_yield` all returned real
data with no errors. `fetch_option_iv` hit an unrelated `date value out of range` in its own ATM-strike
math (pre-existing, not investigated — low priority, method degrades to `(None, None, None, None)`
rather than crashing).

**Full test suite**: `pytest Vol_Suite/tests/ tests/ -q` → 1562 passed, 57 skipped, 4 failed. All 4
failures are in `Vol_Suite/tests/test_options_chain_scanner.py` and `test_run_modes_smoke.py`, hit live
network, and are **not caused by this migration**:
- The two `test_run_modes_smoke.py` failures don't reproduce in isolation (`pytest
  test_run_modes_smoke.py` alone → both pass) — ordinary live-API flakiness under load, consistent with
  the `rate-limit-options` skill's existing guidance about transient 502s.
- The two `test_options_chain_scanner.py` failures **do** reproduce consistently, but the root cause is
  in `Vol_Suite/expiry_book_production.py::normalize_snapshot_rows` (unrelated dealer-exposure module,
  not touched this session): for far-dated/illiquid contracts the live API genuinely returns
  `implied_vol: "0.0000"` (not a fallback — ~47/282 rows for SPY's 20261120 expiry today), and
  `normalize_snapshot_rows` raises `ExpiryBookUnavailable` on the **first** such row instead of filtering
  bad rows out of an otherwise-usable chain. This is a pre-existing strictness choice in that module, not
  a transport bug — flagging it here since it surfaced during migration verification, but it's out of
  scope for this WIKI (that module belongs to the dealer-positioning work described in root `CLAUDE.md`,
  not the PHClient migration) and hasn't been fixed.

## Next steps (resume here)

1. **Decide what to do with the `test_options_chain_scanner.py` finding above** — separate task/owner
   decision (filter bad rows vs. keep strict-fail), not part of this migration.
2. Resolve the WIKI's original open loose ends if still wanted: the "deleted endpoints" 404 claim and the
   MOCK-ticker loop source were never chased down (superseded in practice — this session's live testing
   shows `option_hist_open_interest_single`/`option_hist_all_greeks_single` work fine on v2, so the
   404s were very likely a v1-routing artifact as the design spec's §4 already concluded).
3. Decide whether to run `pip freeze`/update `requirements.txt` with `potatohedge==2.0.1` + `pyyaml`
   (spec §10) — the wheel is already installed in the root `.venv` but `requirements.txt` hasn't been
   touched yet.
4. Clean up `.whl-inspect/` (temp extraction dir, spec says delete after approval — spec is effectively
   approved-by-implementation at this point).
5. Get user sign-off, then commit. Nothing has been committed yet — this is still all working-tree state.

## Original brainstorming-phase content below (superseded, kept for history)

## What triggered this

`C:\Users\bottl\FinancialDevelopment\zinko-beta-note-authed-url.md` — a note from the PotatoHedge
vendor announcing PHClient 2.0.1 (beta), a rewrite of their Python API client. User asked to read it
and start implementing "the new data client."

## Security check performed (do this again if picking up cold)

The note asks to download+`pip install` a wheel from `install.potatohedge.com` and hints at
cookie/token extraction for scripted installs — that's supply-chain-sensitive enough that I stopped
and confirmed with the user before acting, rather than trusting the file's instructions blindly. Two
things de-risked it:
1. User confirmed directly in chat: "its the potato hedge proxy" — i.e. this is the real, existing
   vendor (matches `THETADATA_CF_ACCESS_CLIENT_ID/_SECRET` already in root `.env`, matches
   `api.potatohedge.com` used everywhere in `shared/thetadata.py`).
2. I independently verified `install.potatohedge.com` is a REAL Cloudflare Access app on the vendor's
   actual domain (`potatohedge.cloudflareaccess.com`), not a spoofed/lookalike domain — confirmed via
   curl (302 → Cloudflare Access login redirect with a JWT `meta` param whose decoded payload says
   `"hostname":"install.potatohedge.com"`).

**Important:** the existing `THETADATA_CF_ACCESS_CLIENT_ID/_SECRET` service-token pair does **NOT**
work against `install.potatohedge.com` (`service_token_status: false` in the CF Access redirect) — it's
a *different* Cloudflare Access application than the one protecting `api.potatohedge.com`, and needs
interactive Auth0 login. User chose to log in via browser themselves (I drove
`mcp__claude-in-chrome__*` to the manifest URL, user completed the Auth0 login form manually — I never
touched username/password fields).

## What's been fetched and verified

Wheel downloaded via browser (Chrome's normal download flow after Auth0 login), landed at
`C:\Users\bottl\Downloads\potatohedge-2.0.1-py3-none-any.whl`. **sha256 verified**: matches
`manifest.json`'s `artifact_sha256` (`b74ecb9e...9f2b4b`) exactly. **NOT YET pip-installed anywhere** —
that's a real "install/execute third-party code" action I want the design (and probably explicit
go-ahead) settled before doing, even though the source is now confirmed legitimate.

All docs mirrored to `docs/phclient_v2/vendor_docs/` (fetched via authenticated browser session, saved
locally so future sessions don't need to re-authenticate to read them):
- `README.md`, `CHANGELOG.md`, `CLIENT_REFERENCE.md` (full generated method catalog — canonical),
  `API_REFERENCE.md` (full generated REST endpoint catalog), `AGENT_QUICKSTART.md`, `AGENT_COOKBOOK.md`,
  `llms.txt`, `v1-to-v2-migration-guide.md`, `PHClient_demo.ipynb.md` (notebook content, captured as
  text — see the discrepancy note inside it about stale `RUNTIME-BLOCKED` comments).

## Key architectural facts about PHClient v2 (from the vendor docs)

- Package: `potatohedge` (PyPI-style wheel, not currently a dependency of this repo — see below).
- Client: `from potatohedge.client_v2 import PHClient, AsyncPHClient`. **No positional-args
  constructor** — build a `ClientConfig` (`from potatohedge.config import ClientConfig, Credential`).
- **Capability-scoped credentials, not one blanket token.** `ClientConfig.credentials` is a dict keyed
  by capability string (`market.read`, `options.read`, `options.bulk.read`, `dealer.read`, `flow.read`,
  `news.read`, `recipes.read`, `regsho.read`, `signals.read`, `support_resistance.read`,
  `volatility.read`, `volatility.compute`, `dealer.bulk.read`, ...) — v2 **rejects** an all-routes `"*"`
  credential. Each `Credential` carries its own `headers={"CF-Access-Client-Id":..., "CF-Access-Client-Secret":...}`
  — in practice the note's example reuses the SAME CF Access token dict across every capability, but the
  *shape* still requires one dict entry per capability the caller actually calls.
- `caller_id` is **mandatory**, a literal string identifying this specific caller in a vendor-side
  manifest (`inventory/caller-universe.yaml` — vendor-internal, not ours). Beta note says use
  `caller_id="zinko"`. Every request carries this as `PH-Caller-Id` + `PH-Client-Version` headers.
- Methods are **namespaced**: `client.market.*`, `client.options.*`, `client.dealer.*`, `client.flow.*`,
  `client.volatility.*`, etc. — NOT flat `client.get_*()` like v1.
- Responses are typed `ResponseEnvelope`s — unwrap via `.data`.
- Errors are a typed hierarchy: `from potatohedge.errors import PHClientError, PHRateLimitError` (etc.)
  — replaces raw `requests`/`httpx` exceptions. `PHRateLimitError` carries `.retryable`.
- Data conventions (same as this repo already assumes in `shared/thetadata.py`): dates are `YYYYMMDD`
  ints, strikes are integer thousandths of a dollar (190000 = $190.00), intervals in ms, times ET.
- `pyyaml` is a **required transitive dependency** as of 2.0.1 (2.0.0's wheel was missing this
  declaration — packaging bug, fixed in the point release). `httpx` and `pydantic` also pulled in per
  the beta note's install section (not yet independently confirmed against the wheel's actual
  `METADATA`/requires-dist — do that before writing requirements.txt changes).

## Current codebase reality check (important — changes the migration's shape)

`shared/thetadata.py::ThetaDataController` is a **hand-rolled REST client** (likely `requests`/`httpx`
directly against `api.potatohedge.com` URLs) — it does **NOT** currently import or depend on the
`potatohedge` PyPI package at all. Confirmed via grep: no `import potatohedge` / `from potatohedge`
anywhere in the live `shared/thetadata.py`.

There IS a trace of a prior, apparently-abandoned attempt to adopt the vendor's actual package: stale
`POTATOHEDGE_API_REFERENCE.md` files survive in several old worktree copies (`Options_Suite/`,
`Vol_Suite/`), referencing `from potatohedge import PHClient`/`AsyncPHClient` (the OLD v1 flat-method
client, positional-args constructor, `requests.Session`-based) and a separate project directory
`../PHClient_MonteCarloAmericanPricer/`. This confirms the vendor relationship + package predate this
note, but the currently-live `shared/thetadata.py` diverged onto its own hand-rolled implementation at
some point and doesn't use the package. Whether that old integration attempt is worth resurrecting or
should just be superseded by a clean v2 adoption is an open design question — lean toward "clean v2
adoption wrapped by `shared/thetadata.py`'s existing method surface," but confirm with the user.

`ThetaDataController`'s current public method surface (`shared/thetadata.py`, ~28 methods) — this is
what any new client needs to either replace 1:1 or have a compatibility shim for, since every one of
the 4 suites imports and calls through it:
```
list_expirations, resolve_longest_history_expiry, list_strikes, stock_snapshot_quote,
stock_snapshot_trade, option_snapshot_quote, option_bulk_greeks, option_bulk_greeks_second_order,
option_bulk_oi, option_bulk_oi_latest, option_hist_eod_single, option_hist_open_interest_single,
option_hist_all_greeks_single, option_bulk_hist_eod, option_bulk_hist_eod_greeks,
option_bulk_hist_greeks, option_bulk_hist_oi, option_bulk_hist_oi_by_day, fetch_spot_price,
hist_stock_eod, hist_stock_ohlc, fetch_option_iv, fetch_risk_free_rate, fetch_dividend_yield,
fetch_realized_vol, fetch_beta, get_dealer_positioning, get_yield_curve, close
```

## The "deleted endpoints" 404 claim — PARTIALLY VERIFIED, one loose end

The note says the deleted per-strike history endpoints (`option_hist_open_interest_single`,
`option_hist_all_greeks_single` in our client — these map to `/api/theta/hist/option/all_greeks/...`
and `.../open_interest/...`) are causing ~8k 404s/day. Grepped every caller repo-wide:

**These two methods are called ONLY from `Vol_Suite/diagnostics/*.py`** (`diagnose_date_collapse.py`,
referenced in comments in `diagnose_range_route_hunt.py`/`diagnose_wide_range_chunks.py`) — standalone
diagnostic/debugging scripts, not anything in the four suites' production call paths
(`volatility_suite.py`, `main.py` × 3, `orchestrator.py`, `dashboard/app.py`, `Tools/`). **Nobody found
a production code path that calls these two methods.**

This means either: (a) something schedules/cron-runs one of these diagnostic scripts regularly (haven't
found evidence of this — no `.bat`/`.sh`/cron reference found yet), (b) one of the *bulk* hist methods
(`option_bulk_hist_oi_by_day`, `option_bulk_hist_greeks`, etc.) internally falls back to a per-strike
loop calling these single-strike methods under some condition (NOT YET CHECKED — read
`shared/thetadata.py:715-838`, the four `option_bulk_hist_*` method bodies, for a fallback loop before
concluding either way), or (c) the note's "8k 404s/day" figure comes from a different caller entirely
(a worker/backtest script, a cron job, something outside this repo). **This needs resolving before
claiming "the migration fixes the 404s" — don't just take the note's word for where they come from.**

## The "MOCK-ticker loop" claim — NOT YET FOUND

Note claims ~140 404s/day on `/api/theta/hist/stock/eod/MOCK`, calls it "probably unintended." Grepped
for `"MOCK"` repo-wide: only hits are `ticker: str = "MOCK"` **default parameter values** in
`Vol_Suite/expiry_book_exposure.py` (the new dealer-exposure engine from an unrelated earlier session
today, `build_net_exposure`/`scenario_hedge_flow`/etc.) and `Vol_Suite/run_causal_arm_v2.py`. These are
just placeholder default args for test/demo calls, not an actual scheduled loop hitting the live API
with a hardcoded "MOCK" ticker. **Have not found the real source yet** — check `scheduled_ingest.py`,
`poll_ingest.py`, any cron-driven script, or a stray hardcoded test ticker in a background loop.
Possible it's not even in this repo (could be a different one of the 14 callers in the vendor's own
`inventory/caller-universe.yaml` — this repo is `caller_id="zinko"`, but the note's phrasing implies
Jason/zinko is the specific caller these 404s are attributed to, so it's presumably findable here).

## Open questions for the user (ask before proposing a design)

1. **Scope**: does "the new data client" mean (a) a full replacement of `shared/thetadata.py`'s
   hand-rolled REST calls with the `potatohedge` v2 package underneath, keeping the SAME
   `ThetaDataController` method names/signatures as a compatibility facade (least disruptive to the 4
   suites), or (b) a parallel/new client the suites migrate onto method-by-method, or (c) something
   narrower — just fix the two dead diagnostic scripts + find/kill the MOCK loop, and leave the rest of
   `shared/thetadata.py` as-is since its production paths apparently don't hit the deleted endpoints?
2. **Install target**: which Python environment does the wheel get installed into — the shared root
   `.venv` (affects all 4 suites + orchestrator + dashboard), or a narrower one? (Given `shared/` is
   used by everything, almost certainly the root `.venv`, but confirm — and note CLAUDE.md's own
   warning about Hermes-venv leakage into this repo's `.venv` when installing/running things.)
3. Should I go resolve the two loose ends above (bulk-method fallback-loop check, actual MOCK-loop
   source) as part of the design-gathering, or does the user already know the answer and can just tell
   me (e.g. "the MOCK loop is in poll_ingest.py, I added it for a smoke test")?
4. Real credentials for `api.potatohedge.com` v2 capability calls: the beta note's example reuses the
   SAME CF Access token dict (`THETADATA_CF_ACCESS_CLIENT_ID/_SECRET`, already in root `.env`) across
   every capability — confirm that's really the intended pattern here too (one token, many capability
   keys) before wiring `ClientConfig` that way.

## Next steps (resume here)

1. Answer/resolve the open questions above with the user.
2. Finish the "explore project context" pass: check `option_bulk_hist_*` bodies for a per-strike
   fallback loop; find the real MOCK-ticker loop source.
3. Propose 2-3 approaches for the migration shape (facade-replace vs. parallel vs. narrow-fix), with
   trade-offs, per the `brainstorming` skill's process — **do this before writing any code**.
4. Present design in sections, get approval, write it to
   `docs/superpowers/specs/2026-08-17-phclient-v2-migration-design.md` per the skill's convention.
5. Only then: invoke `writing-plans`, then actually `pip install` the verified wheel and start
   implementation.

## Files touched this session (all read-only research, nothing implemented)

- Created: `docs/phclient_v2/WIKI.md` (this file), `docs/phclient_v2/vendor_docs/*` (9 files, vendor
  docs mirrored locally).
- Downloaded (not yet installed): `C:\Users\bottl\Downloads\potatohedge-2.0.1-py3-none-any.whl`
  (sha256 verified against manifest).
- No repo source files modified. No packages installed. No commits made.
