# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A quant-finance development monorepo: four independent analysis suites (options pricing, volatility,
VaR, sentiment) plus a DTCC equity-swaps ingestion pipeline, tied together by an orchestrator and a
local web dashboard. Everything shares one root `.venv` (Python 3.12) except `sentiment-scanner`,
which keeps its own project-local venv. There is no cloud deployment path in active use — this is
a single-machine, localhost-only setup (see `docs/guides/START_HERE.md` for the human-facing quick-start).

**Git structure (flattened 2026-08-12)**: the repo is a single flat git repo. `Options_Suite/` and
`tradingview-mcp/` are tracked as ordinary files inside the root repo (their nested `.git` dirs were
removed). `mcp-stockflow/` was purged (vendored-and-never-integrated third-party yfinance MCP server).
There are no submodules and no `.gitmodules`. Root `git` commands see everything.

**This repo already has `.claude/skills/`** with detailed, verified debugging knowledge for each
suite's launch conventions, quirks, and known bugs — read the relevant one before debugging a launch
failure instead of re-deriving it:
- `quant-suite-launch-conventions.md` — shared reference (venv/port/env-var checks, `suite_context.json` contract, `Tools/registry.py` pattern). Read first; the others link to it.
- `options-suite`, `vol-suite`, `var-tools`, `sentiment-scanner` — per-suite entry points, flags, and known bugs.
- `tool-launcher` — module-execution / widget-run layer: how a module's `ModuleResult` surfaces as status/error, plus `Tools/` framework notes.

## Setup and common commands

First time only:
```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt   # Windows
.venv/bin/python -m pip install -r requirements.txt           # Linux/Mac
python setup_db.py
git config core.hooksPath scripts/hooks   # enables the commit-msg subject-convention hook
```

Every entrypoint ships as a matched `.bat` (Windows) / `.sh` (Linux/Mac) pair that do the same thing —
see `docs/guides/CROSS_PLATFORM.md` for exactly how path/venv resolution works on each OS.

| Task | Windows | Linux/Mac |
|---|---|---|
| Dashboard (main control surface, `http://127.0.0.1:8787`) | `dashboard.bat` | `dashboard.sh` |
| Tools module (`/tools`, same uvicorn process as dashboard) | `tools.bat` | — |
| DTCC live poller (leave running, polls every 5 min) | `run_scheduler.bat` | `run_scheduler.sh` |
| Run registered modules as widgets (dashboard / Quant Console) | open `http://127.0.0.1:8787`, browse `GET /api/widgets/catalog`, run `POST /api/widgets/{slug}/run` | same (any browser) |
| Run registered modules in-process (scripted/agent) | `.venv\Scripts\python.exe -c "import shared.module_execution as me; print(me.run_selected_modules(['expiry_exposure'], {'ticker':'SPY'}))"` | same |
| Orchestrator module-CLI shell (interactive/`--modules` mode; the only surviving orchestrator CLI) | `orchestrator.py --modules slug,slug --ticker SPY` (`--modules-category`, `--all-modules`, `--list-modules`) | same |
| One-time full swap history backfill (resumable, hours-long) | `python backfill.py` | same |
| Query swap DB directly | `python swaps_query.py`, or `from swaps_query import SwapsQuery` | same |
| Run a suite standalone/interactively | `Options_Suite\options_suite.bat`, `Vol_Suite\vol_suite.bat`, `VaR_Tools_Simulations\var.bat`, `sentiment-scanner\sentiment.bat` | `.sh` equivalents |
| Backtests tournament harness | `.venv\Scripts\python.exe Backtests\main.py --harness all --ticker SPY,QQQ --lookback-days 1` | `python -m Backtests.main ...` |

Only run **one** `dashboard.bat`/`tools.bat` and **one** `run_scheduler.bat` at a time — both scripts
refuse to double-launch on their port, since a second instance racing the first against `swaps.db`
causes "database is locked" errors. Never stop or restart those processes while an orchestrator run
is in flight — wait for completion or ask Jason.

### Tests

```bash
pytest                          # whole monorepo: tests/, plus each suite's tests/ (see testpaths in pyproject.toml)
pytest -m unit                  # only tests needing no network/credentials
pytest tests/test_identifiers.py::test_some_case   # a single test
pytest Vol_Suite/tests/         # one suite only
```
`conftest.py` files add the repo root to `sys.path` so `shared.*` imports resolve inside suite tests.
Every new `@pytest.mark.skip` / `skipif` must carry a reason string naming the unblocking condition.

`Backtests/` is now a separate root-level package for pricing/greeks/signals evaluation. It complements, and does not replace, `Vol_Suite/backtest_stage3.py` or `Tools/tools/backtesting_tool.py`.


### Workflow helper scripts

- `bash scripts/burst_checkpoint.sh vol` prints `git diff --stat` and runs the current narrow Vol_Suite checkpoint slice via `.venv\Scripts\python.exe`. Commit (or run that script) **before** long test sweeps, mass ThetaData pulls, or worker dispatches.
- `git config core.hooksPath scripts/hooks` enables `scripts/hooks/commit-msg`. Allowed subjects: `feat|fix|test|docs|refactor|chore|security|improve|data` (optional `(scope):`) or `reconcile:`. `Revert`/`Merge`/`journal`/`port`/`rh_monitor` subjects are rejected — rewrite to an allowed type.
- `bash scripts/verify_tradingview_submodule.sh` checks `tradingview-mcp` gitlink state when the parent repo configures one, always verifies `tradingview-mcp/src/server.js`, and treats missing `hermes` or live CDP output as informational on this Windows repo.

### Lint

`pre-commit` runs `ruff` + `ruff-format` (and trailing-whitespace/EOF/JSON/YAML checks) — see
`.pre-commit-config.yaml`. Run `pre-commit run --all-files` or `ruff check .` / `ruff format .` directly.

## Architecture

### The spine: swaps DB → suites → dashboard (widget-native)

```
DTCC public API ──(dtcc_api_client.py, dtcc_parser.py)──> db_loader.py ──> swaps.db (SQLite, WAL)
                                                                              ^
scheduled_ingest.py (5-min poll, APScheduler) / backfill.py (full history) ─┘

dashboard/app.py (FastAPI, :8787)  ──>  runs registered modules as widgets, browses swaps.db, serves Tools/
     │   GET  /api/widgets/catalog      (browse every registered ModuleSpec)
     │   POST /api/widgets/{slug}/run   (run one module synchronously; writes cache + Context Store)
     │   GET  /api/widgets/{slug}/state / GET /api/context  (cached results / provenance)
     │
     └─> shared.module_execution.run_selected_modules(slugs, context)   (in-process; no subprocess)
             └─ resolves / expands `requires` / topo-sorts / runs each module's `run(context)`
```

**Run path (Phase 7, widget-native):** there is no cross-suite subprocess launcher anymore. The CLI
`--unified`/`--suite` and `run_suite`/`run_unified` were removed (removed 2026-09-04).
Each registered `ModuleSpec` runs in-process, either from the dashboard
(`POST /api/widgets/{slug}/run`) or scripted/agent code
(`shared.module_execution.run_selected_modules(slugs, context)`). `orchestrator.py` survives only
as: the module-CLI shell (`--modules`/`--interactive`), `build_context`, `run_market_signals_stage`,
`log_run`, DB helpers, and the re-export shim of the module-execution machinery.

**Suite dependency graph** (what a `run_selected_modules`/widget run of the full set honours):
`sentiment → vol → {options, var}` — modules declare their dependencies via `ModuleSpec.requires`,
expanded and topologically ordered by `shared.module_execution`. Sentiment is hard-skipped as a
registered default (replaced by an in-process market-signals stage after vol); check the actual
registry, not a docstring, if a run's behavior around sentiment looks surprising.

**Cross-suite handoff (`suite_context.json` contract)**: one run writes `suite_context.json` — ticker,
expiry, basket, sentiment data, recommended strategies — that other suites/tools consume without
redoing the work. Schema owned by `Vol_Suite/suite_context.py`; validated by `shared/schemas.py` and
`shared/suite_validation.py`. Producer/consumer roles differ:
- **sentiment-scanner** is the sentiment-block producer; it doesn't take `--context`/`--context-out`
  like the others — it exposes `--export-context <path>` instead.
- **Vol_Suite, Options_Suite, VaR_Tools_Simulations** are consumers via `run_context_mode`
  (`--context <path> --context-out <path>`). Two functions share the name `validate_vol_result` with
  different signatures (`shared/schemas.py` vs `shared/suite_validation.py`) — confirm which one a call
  site actually uses.
- **VaR_Tools_Simulations is a partial consumer**: `run_context_mode` only implements the corr_sim
  module (module 1); any other `--module` value returns a `status: error` payload in context mode.
- Each suite still validates its output artifact via `shared/suite_validation.py`
  / `shared/schemas.py`; in the widget-native path a failing module surfaces as
  `status: error`/`failed` on its `ModuleResult` (never a silent fallback), and
  result persistence no longer depends on the `orchestrator_runs` per-run log
  (the run trigger that wrote it was removed with `run_unified`).

**Module-execution path (Phase 7 widget-native):** `shared/module_registry.py`
defines `ModuleSpec` / `ModuleResult` / `ArtifactRef` / `ArchiveHint` /
`ParamSpec`. All four suites plus `Tools/` and `dashboard/cache_widgets.py`
export populated `MODULES` lists -- 56 registered modules as of 2026-09-10
(the "others still stubs" note here was stale). The reusable execution
machinery lives in
`shared/module_execution.py` (`run_selected_modules`, `_expand_module_requires`,
`_topo_sort_modules`); orchestrator.py re-exports it and drives it from the
surviving module-CLI shell (`orchestrator.py --modules slug,slug` /
`--modules-category` / `--all-modules` / `--list-modules`), and the dashboard
runs the same function per-widget via `POST /api/widgets/{slug}/run`.
`ModuleResult.context_patch` values are written to the Context Store
(`shared/context_store.py`, tables `context_entries` / `context_store_audit`)
by `run_selected_modules` itself, and merged into the in-flight context so a
later module in the same run sees them. `ContextStore.load(scope)` is the bulk
read the dashboard uses to seed a run from prior results.
The archiver hook is live: every `ModuleResult` from `run_selected_modules` records to
`module_archive.db` (`shared/module_archive.py`, dedicated DB, never-raises;
`triggered_by="orchestrator"` covers CLI and dashboard callers).

### `shared/` — the library every suite and the orchestrator import from

- **Data access**: `data_source.py` (`DataSourceAdapter` ABC + `TradeRecord` — the pluggable-source
  contract), `query_builder.py` (`CrossSourceQueryBuilder`, cross-source aggregation over
  `swap_trades`), `connection_pool.py` (thread-safe SQLite pool, WAL mode), `identifiers.py` (UPI/CME/OTC
  instrument identifier normalization and bidirectional resolvers).
- **Contracts**: `schemas.py` (validators for the six inter-suite JSON artifacts: `suite_context.json`,
  `options_result.json`, `var_result.json`, sentiment pack/context, `vol_result.json`),
  `suite_validation.py` (marker-file presence/schema checks used by the orchestrator).
- **Observability**: `context_audit.py` (audited, hashed, rollback-capable context mutations),
  `query_monitor.py` (slow-query detection, EXPLAIN analysis), `logging.py` (structured JSON logging).
- **Infra**: `cache.py` (disk cache in `.shared_cache/`), `config.py` (single root `.env` loader).
- **Market data client**: `thetadata.py` (~85KB) — `ThetaDataController`, the merged replacement for
  what used to be separate `Options_Suite`/`Vol_Suite` ThetaData clients (`api.potatohedge.com`). All
  four suites, plus `Options_Suite/thetadata_controller.py` and `Vol_Suite/thetadata_client.py` (both
  now thin re-export stubs), route through this one implementation — despite `Options_Suite` having
  had its own nested `.git`/`.venv`, its live data layer is fully merged into the parent repo's
  `shared/`, not independent. (`mcp-stockflow`, a never-integrated third-party yfinance MCP server,
  was purged 2026-08-12.)

Credentials live in exactly one place, the root `.env` (`THETADATA_CF_ACCESS_CLIENT_ID`/`_SECRET`) —
no suite needs its own `.env` for ThetaData anymore, even though some still have `.env.example` files
from before the merge.

### DTCC swaps ingestion → `swaps.db`

`dtcc_api_client.py` (client for `pddata.dtcc.com/ppd/api/{slice,cumulative}/{SEC,CFTC}/EQ`) →
`dtcc_parser.py` (ZIP/CSV → normalized dicts) → `db_loader.py` (`SwapsLoader`, upserts into
`swap_trades` via `shared/connection_pool.py`). Driven by `backfill.py` (one-time/resumable full
history — expect hours for the first run) and `scheduled_ingest.py`/`poll_ingest.py` (5-minute live
polling via APScheduler). Both long-running scripts use `shutdown_signal.py`'s `GracefulShutdown` for
clean SIGINT/SIGTERM handling.

`swaps.db` schema (see `migrations/001_initial.sql` through `003_add_instrument_identifiers.sql`):
core `swap_trades` fact table, `schema_version`, `ingestion_state`, `scrape_log`, `orchestrator_runs`
(audit trail), `upi_reference`/`upi_decode_state`, plus a `data_source` column (`002`, default `DTCC`)
and cross-source identifier columns (`003`: `source_identifier`, `normalized_instrument_id`,
`instrument_type`) added for multi-source support. `adapters/` (`dtcc_adapter.py`, `cme_adapter.py`,
`otc_adapter.py`) implement `shared/data_source.py`'s adapter pattern — DTCC is production, CME/OTC are
stubs — enabled via the `DATA_SOURCES` env var and discovered by `orchestrator.py::discover_adapters`.

`upi_decoder.py`/`decode_upis.py` decode DTCC UPI *names* into company names (falling back to
OpenFIGI) — a different concern from `shared/identifiers.py`, which normalizes identifier *type*
across sources (UPI/CME/OTC). Query the resulting data via `swaps_query.py`'s `SwapsQuery` class
(`get_database_stats()`, `top_notional_products()`, `query_by_upi(upi)`), not raw SQL, where possible.

`SWAPS_DATABASE_SPEC.md` is the original design doc and describes a PostgreSQL schema — that's
historical; the project is SQLite-only now (see the comment in `requirements.txt`). Trust
`migrations/*.sql` over that doc for current schema.

### The four suites

Each suite has its own `.claude/skills/<suite>/SKILL.md` with launch/debugging detail beyond this
summary.

- **Options_Suite** — American option pricing/Greeks/IV across CRR, Leisen-Reimer, Newton-Raphson,
  brute-force IV, SABR, Vanna-Volga, plain and Heston Monte Carlo LSM, and Barone-Adesi-Whaley, compared
  against live ThetaData quotes. `main.py` is ~926 lines: a real interactive terminal mode (`main()`,
  prompts for ticker/option type/strike/model choice) plus `--context`/`--context-out` headless mode
  (`run_context_mode`), which fetches live spot/rate/dividend-yield and prices via LeisenReimer, writing
  a schema-valid `options_result.json` (`method`/`sigma`/`price`/`greeks`) on success. (Corrected
  2026-08-17 — this used to describe `main.py` as a ~120-line stub with pricing code unwired; that was
  stale.) `chain_evaluation.py` → `reports.py` remains a genuinely separate multi-model reporting path
  `main.py` doesn't call into. Shares `shared/thetadata.py` for data. `module_registry.py` (the
  widget adapters) is a *third* entry point, and as of 2026-09-10 it fetches its own market data
  the same way `main.py` does rather than demanding a pre-filled context — see the sigma fragile
  surface below.
- **Vol_Suite** — the largest suite (`volatility_suite.py`, ~2913 lines): dealer positioning (three
  sign conventions — `oi_heuristic` v1, `replication` v2-1b, `vol_surface_replication` v2-1a+1b),
  variance-swap replication (Carr-Madan/Demeterfi) and VRP term structure, correlation/basket
  construction, GARCH(1,1), a multi-ticker screener, and a strategy recommender. Builds one shared
  context per focus ticker and runs it through `_run_core_analysis`. Owns the `suite_context.json`
  schema (`suite_context.py`) that the other suites consume. `instrument_resolver.py` bridges tickers
  to cross-source UPIs via `shared.identifiers`. The screener's two historical failure modes (missing
  realized vol silently becoming `0.0`; a blanket `except` dropping tickers unmarked) are **fixed**
  (NaN + `data_quality` + `INSUFFICIENT DATA` signal + `skipped` reporting) — see README Phase 11.
  **Live dealer model (merged 2026-08-20; charts 2026-08-21):** `volatility_suite._run_production_dealer_positioning`
  → `expiry_book_production.fetch_production_result`. Legacy `compute_dealer_positioning`
  is backtest/test only. 4-panel GEX/VEX/CEX = VannaCharm stock + today's `scanner_trades`
  (not SVI `gamma_book`). Charm is per-right C+P, no extra dealer −1. Prior-close OI from
  `option_bulk_hist_oi_by_day` (`eod_greeks` has no OI); `oi_sum>0` else keep live snapshot.
  7d vendor-ΔIV vanna flow stays adjacent, never summed with inventory. Contracts:
  `Vol_Suite/docs/LIVE_expiry_book_20260821.md` (charts), `LIVE_expiry_book_20260820.md` (scalars).
  Vanna convention: `expiry_book_exposure.py::dealer_frame_vanna`.
  **ITM-leg coverage (fixed 2026-08-31):** `expiry_book_production.py::normalize_snapshot_rows`
  used to silently drop any strike where the vendor left `implied_vol=0` (can't numerically solve
  IV for legs with little extrinsic value) — confirmed live on SPY: 124/642 rows (19%), 100% ITM,
  several with thousands of contracts of real OI, which made GEX/VEX/CEX (and the surface
  explorer, which shares this same row-fetch via `surface_grids.py::_fetch_expiry_rows`) look like
  it cut off artificially right at spot instead of decaying across the whole chain. Now mirrors the
  opposite-right leg's solved IV at the same strike (put-call parity) before dropping a row; only
  drops when neither side solved.
  **Jump-diffusion default path (2026-08-30/31):** `Vol_Suite/jump_diffusion/` calibrates Bates/Merton
  (Nelder-Mead in IV space, `MAX_CALIB_STRIKES=15`) early in `_run_core_analysis` and writes
  `suite_context.jump_diffusion`. Failures persist `{status: error, error: ...}` — never `None`.
  `rmse_iv` is scoped to the calibration mask, not the full smile. Results feed dealer
  positioning / VRP / strategy recommender. Comparison mode stays standalone.
- **VaR_Tools_Simulations** — a Python port of a legacy Excel VaR toolkit (`VaRtools Samples.xls` is
  the source spec), one `var_engine/` module per original sheet: `corr_sim.py` (correlated GBM Monte
  Carlo), `mc_sim.py`, `hist_sim.py` (basic/Hull-White/FHS-GARCH), `copulas.py` (Gaussian/Student-T/
  Clayton), `forex_var.py`, `cashflow_map.py`, `stress_test.py`, `var_agg.py` (EWMA + PCA + Euler
  allocation), plus `hedge_optimizer.py` and `price_dist.py`. `main.py`'s CLI is `--module N`
  (interactive) or `--context/--context-out` (context mode); canned demo runs go through
  `python -m var_engine.<module>` (there is NO `--demo` flag). Its only `shared/` touchpoint is
  `var_engine/data_loader.py` (via `shared.cache`/`shared.config`/`shared.thetadata`) — no swaps-DB
  integration yet.
- **sentiment-scanner** — contested-narrative sentiment (StockTwits, Reddit, YouTube transcripts)
  cross-referenced with options flow (7 scanners: GEX, Unusual OI, IV Rank, Skew, Max Pain, Vol
  Dispersion, Earnings-Vol Premium) and CME swap-data-repository activity, combined by
  `correlation/engine.py`. Long-running by default (loops on `config.SCAN_INTERVAL_MINUTES`; pass
  `--no-loop` for one shot). Has its own project-local `.venv` (auto-installs deps on every launch,
  unlike the other three suites' shared venv) and depends on a separate local `bgutil-ytdlp-pot-provider`
  server on port 4416 for YouTube caption PO-tokens — if that server isn't running, YouTube captions
  are silently skipped rather than erroring, which shows up later as thin sentiment data.
  `verify_pot_token.py` is a standalone diagnostic for that token pipeline, unrelated to Reddit.
  `sector_rotation_launcher.py` (ranks 15 sector ETFs) is a separate, independently-run CLI.

### Dashboard and Tools

`dashboard/app.py` (FastAPI, `:8787`, localhost-only) serves the swap-data browser, the widget
surface (run any registered module via `POST /api/widgets/{slug}/run`, browse
`GET /api/widgets/catalog`, read cached results / Context-Store provenance via
`GET /api/widgets/{slug}/state` and `GET /api/context`), cross-source analytics, and query-monitor
metrics; `tools.bat` opens the same uvicorn process at `/tools` instead of `/`. `Tools/` is a
lighter-weight plugin surface for one-off tools (distinct from adding a whole new suite): implement
`run(context: dict) -> dict` in `Tools/tools/`, wrap it in a `ToolSpec`, register it in
`Tools/registry.py::TOOLS`. `Tools/context_loader.py` discovers/loads/re-validates
`suite_context.json` files from any suite's output directory.

**Tabs (`docs/guides/TABS.md`).** Six: Desk (`/`), Chart, Volatility, Models, Dealer Book,
Sentiment. The Suite-output tab was dropped from the nav 2026-09-11 (the `/suites/*` routes still
resolve for direct artifact links). **Volatility / Models / Sentiment are `dashboard/panels.py`
pages**, not widget-card rails: a `PanelSpec` is a server-side spec whose `run()` returns the same
ModuleResult shape the widget API does, so the browser reuses `widget-renderers.js::renderResult`.
They exist because several modules render nothing on a generic card — the four `surface_*` modules
return `artifacts=[]` (grid on `context_patch`, no PNG ever drawn), and `vrp_term_structure` in
Vol_Suite is a `runnable=False` marker whose `run()` raises. Panels route those to
`Tools/tools/surface_explorer_tool.py` and `Tools/tools/vrp_term_structure_tool.py`, which do
render. Those same four `surface_*` slugs now carry `superseded_by="surface-explorer"` so the desk
picker offers the renderer instead of the bare grid (see the selection contract below).
No quant logic lives in `panels.py`; every panel delegates to `run_selected_modules` or a
`Tools/` entry point. `dashboard/static/js/panel-tab.js` is the shared scope-bar + card + runner
engine; scope now persists across tabs via `localStorage` in `sync-bus.js`. **Panel batches run
sequentially with a 400ms gap** — every panel is a billed ThetaData pull and the repo rule is never
to fan out.

**The desk page (`GET /`, rebuilt 2026-09-10) — see `docs/guides/DESK.md`.** Overview and Quant
Console were merged into one book-driven surface; `/quant` and `/tools` now 307-redirect to `/`.
Post-2026-09-11 the desk is specifically the *post-suite* tools (hedge optimizer, sims, VaR,
screeners, options tools) plus the book and status panels.
The rule the page enforces: **scope (ticker/expiry/basket) is entered once, in a page-level scope
bar, and normally comes from the position book, not typed** — tool cards render with
`scope-ui="hidden"` and follow `static/js/sync-bus.js`. Cache-backed widgets (`category="cache"`:
positions/signals/position_analysis/surfaces) render as status panels with a Refresh and no scope
inputs, because their `run()` ignores context by design. `ModuleSpec.params` (a tuple of
`ParamSpec`) declares module-specific knobs, which the card renders as controls and the caller
sends under the run body's `params`.

**The selection contract (2026-09-12): everything the picker offers, runs and shows you
something.** `ModuleSpec.is_pickable()` is the single verdict — false when either
`runnable=False` (a selection-only pipeline step whose `run()` raises: `group_screener`,
`vol_surface_2d`, `vrp_term_structure`, `sentiment_backtest`) or `superseded_by` names another
registered slug that does the job properly (the four `surface_*` modules → `surface-explorer`,
which computes the same grid *and* draws it; `surface-explorer` now declares `mode`/`greek`/
`min_dte`/`option_type` params so one card reaches all five surfaces). The catalog publishes
`pickable`/`superseded_by`, `index.html` hides unpickable entries and prunes them out of a saved
layout, and `POST /api/widgets/{slug}/run` refuses one with **409** naming the replacement rather
than letting `run()` raise a paragraph at the card. Adding a module to either hidden set is a
UI-visible decision: `tests/test_module_pickability.py` pins that every unpickable slug declares a
reason and that a `superseded_by` target is itself registered and pickable.

**Dependencies are declared, expanded and skipped when already satisfied.** `ModuleSpec.requires`
auto-pulls (`_expand_module_requires`) — the five equity VaR modules (`corr_sim`, `mc_sim`,
`copulas`, `var_agg`, `hedge_optimizer`) require `correlation_matrix`, since without it they
simulated a flat 0.25 vol and an identity matrix and reported it as a risk number. `forex_var` is
deliberately excluded (currency pairs, not equities). To keep that affordable, `ModuleSpec.provides`
declares the `context_patch` keys a module writes, and an *auto-added* dependency whose provides are
all already in context is skipped (`_dependency_already_satisfied`) — the dashboard seeds the
Context Store into every run first, so otherwise each click of a VaR card would re-run a 2-year
whole-basket EOD pull. An explicitly selected module always runs. Pinned by
`tests/test_module_requires_expansion.py`.

Three things the run route does that it did NOT before, and which every widget depends on:
`POST /api/widgets/{slug}/run` executes through `shared.module_execution.run_selected_modules`
(so `requires` expands and `run_id`/`output_dir` are minted — it used to call `spec.run(context)`
directly, which is why `dealer_flow` returned `status: skipped` forever); it **seeds the context
from the Context Store first** via `_seed_context_from_store` (the store was write-only from the
dashboard's side — the only reader was `describe()` for the provenance list, so nothing ever
consumed a `context_patch`); and it runs in a threadpool, since a module run can take minutes.
The response carries `ran` (modules actually executed) and `context_seeded` (keys the run was fed),
which the card renders as its "fed:" line.

There is no auth on the dashboard — it's a deliberate, permanent decision (`dashboard/auth.py` only has
a `get_client_ip` helper left; the API-key gate was removed since this is a single-user, localhost-only
tool that already keeps its real secrets (ThetaData credentials) in plaintext in `.env`).
**localhost-only discipline is now load-bearing: `/api/quant-console/agent` turns free text into
potentially billed ThetaData-backed runs — never expose the dashboard beyond `127.0.0.1`.** Sharing
it publicly (e.g. via `cloudflared tunnel`) would expose all swap data and let anyone trigger billed
widget runs from arbitrary text. Don't expose it beyond localhost without adding real auth back first.

## Known fragile surfaces (read before changing these)

- **Context Store threading of vol stats.** Vol_Suite computes real per-ticker realized vol, a pairwise
  correlation matrix, and GARCH conditional vol; in the widget-native path these `context_patch` values
  are persisted by the run route into the Context Store (`shared/context_store.py`, table
  `context_entries`/`context_store_audit`) keyed by scope, and VaR's `_resolve_vol_and_quality` /
  `_resolve_drift_and_quality` read them back via `context_store.get(...)` before falling back to an
  identity correlation matrix + flat 0.25 vol. (This replaces the removed `run_unified`'s
  `_thread_vol_stats_into_context`, which mutated the shared suite_context object in place.) Any change
  to the Context-Store scope-key normalization, the `suite_context` focus/basket schema, or VaR's
  `_resolve_vol_and_quality` / `_resolve_drift_and_quality` can silently re-break this. Run
  `tests/test_orchestrator_market_signals.py` + `VaR_Tools_Simulations/tests/test_context_builders.py`
  after touching it.
- **Context-Store key names must match at BOTH ends, and nothing checks that for you.** Two live
  breakages of exactly this shape, both fixed 2026-09-11, both silent: (a) `garch` wrote
  `garch_vol` while VaR's `_resolve_vol` read `garch_conditional_vol`, so every VaR run took the
  flat-0.25 fallback no matter how many times GARCH had run on the scope — `_run_garch` now writes
  both names; (b) **nothing anywhere wrote a correlation matrix**, so `_resolve_corr` always fell
  back to identity and every basket VaR was a zero-correlation simulation presented as a risk
  number — the new `Vol_Suite` `correlation_matrix` module (wraps `correlation_engine.
  run_correlation_engine`) writes `correlation_matrix`/`covariance_matrix`/`volatilities`/
  `correlation_tickers`. A stored per-name vector or matrix is re-indexed onto the requested basket
  **by label** via `correlation_tickers`, never positionally — a matrix stored for `[QQQ, SPY]`
  applied to `[SPY, QQQ]` is wrong with no error — and is refused outright if it does not cover
  every requested name. Every VaR module reports `vol_source`/`corr_source`/`position_source`; a
  `fallback:` prefix means the number is not a measurement. Pinned by
  `Vol_Suite/tests/test_module_registry_vol_context.py` and
  `VaR_Tools_Simulations/tests/test_context_pull_from_vol.py`.
- **A module's charts must be served, and must be only its own.** Two traps behind "the chart is
  blank / the card shows twenty PNGs": (a) `run_selected_modules` mints
  `output_dir=<repo>/outputs/<run_id>/`, but `GET /files` used to allow only `artifacts/` and
  returned **403** for everything else — a broken `<img>` with no error anywhere; the allowed roots
  are now `FILE_SERVE_ROOTS` in `dashboard/app.py` (realpath containment still enforced). (b)
  Several suite entry points collect output by **listing** `output_dir` and matching a filename
  prefix rather than tracking what they wrote — `garch_analysis.run_garch_module` globs
  `{ticker}_garch_*.png` — so two runs sharing a directory make the second return the first's
  charts (confirmed live on SPY: six artifacts, three from 11s earlier). Panel runs therefore get a
  fresh `artifacts/panels/<tab>/<panel>_<stamp>_<hex>/` per run (`panels.panel_run_dir`); don't
  "tidy" that back to a shared directory.
- **Options_Suite default pricing method is Leisen-Reimer, not CRR.** `main.py` resolves sigma via
  `method="LeisenReimer"` and derives Greeks from LR's tree (better strike/step convergence for
  American options). A regression to plain CRR for the default path is a bug Jason has reported
  more than once. `test_crr_binomial.py` covers CRR as a *separate* model; don't let it become the
  default. **This extends to the IV solver (2026-09-11):**
  `NewtonRaphsonIV.implied_volatility_nr_american` now inverts against Leisen-Reimer too. It
  defaulted to Barone-Adesi-Whaley while `vol_manager.py`'s own NewtonRaphson branch documented
  the opposite ("same tree the Leisen-Reimer method uses") — code and caller disagreed about
  which model a solved IV came from. BAW is a closed-form *approximation*, so inverting it
  against LR-generated prices left a residual that looked like solver error: worst-case
  round-trip across the strike/vol/dividend grid was ~1.3e-2 for puts, and is **4e-05** now that
  one model is used end to end. BAW is still reachable via `pricer=baw_american_price`; don't
  let it drift back to being the default.
- **A solved IV is only real if the price could have identified it (2026-09-11).** Both solvers in
  `Options_Suite/NewtonRaphsonIV.py` declare success on `|price - C| < tol`, and there are option
  shapes where price is FLAT in sigma across a whole interval — so that test is satisfied by every
  vol in the band and the loop reports whichever one it drifted to, with `converged=True`. Two
  causes, one shared guard (`_vol_is_identifiable`, which probes one vol point DOWN and compares
  against `tol` itself rather than a new magic number):
  `implied_volatility_nr_american` hits it on the **early-exercise boundary** (price exactly
  intrinsic, bit for bit, for every sigma below the boundary — measured S=550/K=605 put: 55.00000 at
  sigma 0.10, 0.12 and 0.15, all answered 0.15590); `implied_volatility_nr` has no boundary but hits
  it by **saturation** (far-OTM price pinned near 0, deep-ITM near `K·e^(-rT)`/`S·e^(-qT)` — measured
  K=300 put: 7e-136 at sigma 0.05 vs 7e-25 at 0.12, both answered 0.28481). The American case is
  bimodal (>1000x separation); the European one degrades **continuously** as vega decays, so no
  threshold partitions it cleanly — what `tol=1e-4` guarantees is the safe direction: never flags a
  solve accurate to 1%, always flags one worse than 10%. The guard must stay conservative because
  `vol_manager.py` RAISES on `converged=False`. Callers must honour the flag —
  `sentiment-scanner/scripts/iv_watchdog.py` discarded it into an IV *percentile rank*, where a
  fabricated wing IV reads as a slightly different rank rather than an error. Same family as
  `MCHestonLSM._bs_iv_batch`'s `min_vega_frac`→NaN floor. Pinned by
  `Options_Suite/tests/test_iv_solvers.py` (`TestAmericanIVIdentifiability`,
  `TestEuropeanIVIdentifiability`).
- **Options_Suite sigma must be SOLVED, never defaulted (fixed 2026-09-10).** Every model adapter
  in `Options_Suite/module_registry.py` used to do `sigma = float(context.get("sigma") or 0.25)`,
  and nothing in the widget path supplies sigma — so CRR/LR/BAW/MC each priced at a flat 25% vol
  and reported the result as a price (SPY: $41.66 vs $25.52 at the real 14.33% IV). They now go
  through `_resolve_sigma` -> `VolManager.get_sigma`, matching `main.py`, and record provenance in
  `metrics["sigma_source"]`. `_extract_pricing_args` likewise fetches spot/rate/dividend and
  defaults the strike to ATM instead of raising `No usable spot price` — a card knows a ticker,
  not a strike. If a pricer's `sigma_source` ever reads `fallback:0.25`, the number is not a
  market price; read the reason it carries.
- **A `print` must never be able to fail a module run.** The above was *caused* by one:
  `VolManager.get_sigma` prints a banner containing `U+2500`, which raises `UnicodeEncodeError`
  on a cp1252 console mid-computation, and the caller silently took the sigma fallback.
  `shared/module_execution.py::_make_console_lossy` reconfigures stdout/stderr to
  `errors="replace"` at the top of every `run_selected_modules` call. Note `surrogateescape`
  (Python's Windows default) does NOT prevent this — it handles undecodable input bytes, not an
  unencodable output character. Don't "tidy" that guard away.
- **Flat cwd-relative imports.** `Options_Suite/main.py` (`from market_data import ...`) and several
  suite entry points import from the suite directory, not as packages. `python -c "import orchestrator"`
  from the wrong cwd, or a bare `import main` where Options/VaR/sentiment each ship a `main.py`, fails
  with `ModuleNotFoundError`. Import-check from the suite's own directory, or load a suite `main.py`
  under a unique module name via `importlib.util.spec_from_file_location` (the pattern
  `Tools/tools/price_dist_tool.py` and `hedge_optimizer_tool.py` use).
- **Hermes-venv leaks into the project venv.** Running the project venv python with `PYTHONPATH`/
  `PYTHONHOME` inherited from a Hermes session can pull Hermes' site-packages (e.g. PIL lacking the
  `_imaging` C extension, or a pydantic_core mismatch). Clear both with `env -u PYTHONPATH -u PYTHONHOME`
  before invoking the project `.venv`.
- **Don't resubmit a fail-loudly-on-missing-data change for dealer-exposure/backtest code without
  reading the lesson first.** The same "fail loud instead of silent fallback" change was proposed and
  reverted twice in dealer-exposure code (same day) — a live-render fail-loud check is correct, but the
  identical check inside `backtest_stage3.py`'s multi-day loop aborts the whole run on one bad day.
  Read `Vol_Suite/docs/Dealer posistioning notes/HANDOFF_dealer_exposure_dev_20260814.md` and
  distinguish the live-render case from the backtest-loop case before resubmitting this pattern.
- **SPX must be queried under two different root symbols depending on what you're asking for.** On
  this ThetaData feed, SPX's real listed *options chain* (`option_bulk_greeks`, `option_bulk_oi`, etc.)
  is rooted under `"SPXW"` — `option_bulk_greeks("SPX", ...)` returns `"v2 payload is None"` for every
  expiry. SPX's *index price* (`fetch_spot_price`, `index_snapshot_quote`) stays correctly rooted under
  plain `"SPX"` — `shared/thetadata.py::_INDEX_PRICE_ROOT_ALIASES` maps `"SPXW"` back to `"SPX"` for
  price lookups at the source (`63382cf`, `43b7c6a`), but a new caller that resolves its own ticker
  string instead of going through `fetch_spot_price` can still reintroduce this. Stock-EOD is
  fixed (2026-08-31): `shared/thetadata.py::hist_stock_eod` applies the same alias, so
  `Vol_Suite/correlation_engine.py::fetch_price_history` is no longer an open gap
  (`tests/test_thetadata_spxw_alias.py`). Keep chain-root SPXW vs price-root SPX.
- **ThetaData mass pulls.** Any loop over `shared/thetadata.py` must rate-limit (sleep 0.3–0.5s,
  never 6-way parallel) per the `theta-data` skill. A no-delay ~8000-request pull gets throttled.
- **Hermes tool rules (this host).** `search_files` patterns must not start with `-` (rg treats
  them as flags — `--modules` fails as `unrecognized flag`); escape regex metacharacters.
  Re-read a file immediately before `patch`; unique surrounding context. Foreground `terminal`
  timeout stays ≤600s — long builds/backtests/mass pulls use `background=true` + `notify`.

## Notable env vars

All in the single root `.env` (see `.env.example`): `THETADATA_CF_ACCESS_CLIENT_ID`/`_SECRET`
(required — ThetaData/PotatoHedge proxy credentials), `SWAPS_DB_PATH` (unset = repo-root recent-window `./swaps.db` ~941 rows; launchers set it to the OneDrive live book — see `docs/SWAPS_DB_LAYOUT.md`),
`LOG_LEVEL`, `DATA_SOURCES` (which `adapters/` are active).
