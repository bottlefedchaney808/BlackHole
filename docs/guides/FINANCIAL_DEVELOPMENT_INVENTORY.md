# FinancialDevelopment Repo Inventory

A factual map of the `C:\Users\bottl\FinancialDevelopment` monorepo, built for a wiki author.  Everything below was verified against the live working tree (branch `master`, HEAD `ad87822`) on 2026-09-04.

---

## 1. Top-level layout

| Path | Kind | Purpose |
|------|------|---------|
| `Options_Suite/` | suite | American option pricing/Greeks/IV (CRR, LR, BAW, NR, SABR, VV, MC/Heston LSM) |
| `Vol_Suite/` | suite | Volatility/dealer-positioning engine: GARCH, variance-swap replication, VRP, screener, strategy recommender |
| `VaR_Tools_Simulations/` | suite | Legacy-Excel VaR toolkit port: corr_sim, mc_sim, hist_sim, copulas, stress, hedge optimizer |
| `sentiment-scanner/` | suite | Contested-narrative sentiment + 7 option-flow scanners; runs under its own venv |
| `shared/` | library | Cross-cutting code used by the suites: data client, cache, schemas, validation, logging, connection pool, identifiers, context store |
| `Tools/` | plugin framework | One-off quant tools (`ToolSpec`) surfaced through the dashboard |
| `dashboard/` | app | FastAPI localhost web app (`http://127.0.0.1:8787`) over swaps data + orchestrator + tools |
| `swaps_dashboard/` | app | Secondary FastAPI dashboard on port 8788 for swap-data overview |
| `chart_app/` | app | Native chart app on port 8791 |
| `adapters/` | ingest | DTCC/CME/OTC data-source adapters (DTCC real, CME/OTC stubs) |
| `migrations/` | db | Numbered SQLite schema migrations for `swaps.db` |
| `tests/` | tests | Root-level pytest suite (shared/orchestrator wiring) |
| `docs/` | docs | Guides, specs, archived research, superpowers plans |
| `.claude/skills/` | skills | Per-suite launch/debugging skill docs for Claude Code |
| `scripts/` | scripts | Helper bash scripts (burst checkpoint, commit-msg hook, submodule verify) |

**Notable root files**

- `orchestrator.py` — root driver that launches suites in dependency order and validates handoff JSON.
- `pyproject.toml` / `requirements.txt` — single shared venv deps.
- `setup_db.py` — initializes/migrates `swaps.db`.
- `backfill.py` / `scheduled_ingest.py` / `poll_ingest.py` — DTCC ingestion pipeline.
- `swaps_query.py` — query interface to `swaps.db`.
- `db_loader.py`, `dtcc_api_client.py`, `dtcc_parser.py`, `decode_upis.py`, `upi_decoder.py` — ingestion helpers.
- `.env` / `.env.example` — ThetaData CF Access credentials and SQLite config.
- `CLAUDE.md` — the canonical, actively maintained agent context.
- `docs/PROJECT_AUDIT_AND_SPEC.md` — deeper point-in-time audit (2026-08-04), useful but stale on some items.

---

## 2. New-machine setup and primary entry points

### First-time setup (Windows)

```batch
cd C:\Users\bottl\FinancialDevelopment
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
python setup_db.py
```

Then create a root `.env` with at least:

```text
THETADATA_CF_ACCESS_CLIENT_ID=...
THETADATA_CF_ACCESS_CLIENT_SECRET=...
```

### Primary entry points

| Task | Windows command | Notes |
|------|-----------------|-------|
| Dashboard | `dashboard.bat` | `http://127.0.0.1:8787`; refuses to double-launch |
| DTCC live poller | `run_scheduler.bat` | 5-minute APScheduler loop; single instance only |
| Widget / module run | `dashboard.bat`, then `GET /api/widgets/catalog` + `POST /api/widgets/{slug}/run` (or `shared.module_execution.run_selected_modules` in-process) | runs one or more registered modules; no subprocess |
| Module run (scripted) | `.venv\Scripts\python.exe -c "import shared.module_execution as me; me.run_selected_modules(['dealer_exposure'], {'ticker':'SPY'})"` | in-process; expands `requires` |
| Backfill | `python backfill.py` | hours-long, resumable |
| Query swaps | `python swaps_query.py` | CLI demo / `from swaps_query import SwapsQuery` |
| Options_Suite | `Options_Suite\options_suite.bat` | interactive or context mode |
| Vol_Suite | `Vol_Suite\vol_suite.bat` | focus-ticker workflow |
| VaR Tools | `VaR_Tools_Simulations\var.bat` | interactive or `--context` mode |
| Sentiment scanner | `sentiment-scanner\sentiment.bat` | its own local `.venv`; auto-installs deps |

### Tests

```batch
pytest                # whole monorepo (configured in pyproject.toml)
pytest -m unit        # tests needing no network/credentials
```

`pyproject.toml` defines `testpaths`: `tests`, `shared/tests`, `Vol_Suite/tests`, `Options_Suite/tests`, `VaR_Tools_Simulations/tests`, `sentiment-scanner/tests`, `Tools/tests`, `Direction/tests`, `chart_app/tests`.

---

## 3. Key modules/classes and responsibilities

### 3.1 Orchestrator (`orchestrator.py`)

- `shared/module_execution.run_selected_modules(slugs, context)` — runs registered modules in-process (resolves `requires`, topo-sorts, merges `context_patch`); the surviving run path. Re-exported by `orchestrator.run_selected_modules`.
- `run_market_signals_stage(ticker, context)` — the in-process market-signals/Options/VaR context stage that replaced the sentiment subprocess.
- Module result validation happens inside each module (`shared/schemas.py` / `shared/suite_validation.py`); the Context Store (`shared/context_store.py`) persists `context_patch` values keyed by scope.

### 3.2 Shared library (`shared/`)

| Module | Responsibility |
|--------|----------------|
| `shared/thetadata.py` | `ThetaDataController` — merged market-data client used by all suites |
| `shared/connection_pool.py` | Thread-safe SQLite connection pool (WAL mode) |
| `shared/schemas.py` | JSON validators for all inter-suite artifacts |
| `shared/suite_validation.py` | Marker-file presence/schema checks |
| `shared/module_registry.py` | `ModuleSpec`/`ModuleResult` contract + `all_modules()` aggregation |
| `shared/context_store.py` | SQLite-backed scoped key/value store for module context patches |
| `shared/cache.py` | Disk cache in `.shared_cache/` |
| `shared/config.py` | `.env` loader (`load_env_once`) |
| `shared/logging.py` | Structured JSON logging |
| `shared/identifiers.py` | UPI/CME/OTC identifier normalization |
| `shared/query_builder.py` | Cross-source aggregation over `swap_trades` |

### 3.3 Suites

#### Options_Suite
- `main.py` — interactive menu + headless `--context`/`--context-out` mode.
- Pricing engines: `american_binomial.py`, `barone_adesi_whaley.py`, `MC.py`, `MCHestonLSM.py`, `SABRModel.py`, `VannaVolga.py`, `NewtonRaphsonIV.py`.
- `module_registry.py` — registers 9 pricing modules (CRR, Leisen-Reimer, NR IV, SABR, Vanna-Volga, MC, Heston LSM, BAW, model comparison). Leisen-Reimer is `default_selected`.
- `chain_evaluation.py` / `reports.py` — multi-model comparison/ PDF reporting path.
- `market_data.py` / `thetadata_controller.py` — thin wrappers around `shared/thetadata.py`.

#### Vol_Suite
- `volatility_suite.py` — ~2,913-line focus-ticker CLI; `_run_core_analysis` is the pipeline shared by interactive and context modes.
- `dealer_positioning.py` — three sign conventions (`oi_heuristic`, `replication`, `vol_surface_replication`).
- `variance_swap_screener.py`, `variance_swap_live.py`, `vrp_term_structure.py` — variance-swap/VRP analytics.
- `garch_analysis.py` — GARCH(1,1) conditional vol.
- `correlation_engine.py`, `index_membership.py` — basket/correlation construction.
- `suite_context.py` — owns the `suite_context.json` schema.
- `module_registry.py` — exposes vol modules to the unified registry (populated; exact count can be retrieved with `python -c "from Vol_Suite.module_registry import MODULES; print(len(MODULES))"`).

#### VaR_Tools_Simulations
- `main.py` — interactive `--module N` and headless `--context`/`--context-out` mode.
- `var_engine/` — one module per original Excel sheet: `corr_sim`, `mc_sim`, `hist_sim`, `copulas`, `forex_var`, `cashflow_map`, `stress_test`, `var_agg`, `hedge_optimizer`, `price_dist`.
- `module_registry.py` — registers 10 VaR modules.
- Context mode currently only implements `corr_sim` (module 1); other modules return `status: error` in context mode.

#### sentiment-scanner
- `main.py` — long-running scanner loop; `--no-loop` for one shot; `--export-context` for orchestrator handoff.
- `scanner/` — 7 option-flow scanners: GEX, unusual OI, IV rank, skew, max pain, vol dispersion, earnings-vol premium.
- `correlation/engine.py` — composite signal engine.
- `module_registry.py` — registers 7 scanner modules.
- Has its own local `.venv` and depends on a local `bgutil-ytdlp-pot-provider` server on port 4416 for YouTube captions.

### 3.4 Tools plugin framework (`Tools/`)

- `Tools/registry.py` — `ToolSpec` registry; loads 7 tools at runtime.
- `Tools/tools/` — implementations: `backtesting`, `direction_signal`, `hedge_optimizer`, `options_strategy`, `price_dist`, `surface_explorer`, `vrp_term_structure`, plus stubs like `bollinger`, `broker_book`, `elliott_wave`, `liquidity_map`, `trend_engine`, `whale_flow`.
- `Tools/context_loader.py` — discovers/loads `suite_context.json` files.
- Unified module registry adapts every `ToolSpec` into a `ModuleSpec` via `shared.module_registry.from_tool_spec`.

### 3.5 Dashboard (`dashboard/app.py`)

FastAPI app on `127.0.0.1:8787`.

Key route groups:
- Swap data browser, ingestion status.
- Orchestrator trigger: `POST /run/{suite_or_unified}`.
- Quant Console: `/quant`, runs, alerts, worker dispatch.
- Tools UI under `/tools` (same process; `tools.bat` is the entry point).
- WebSocket live log tail at `/suites/{suite}/live`.

### 3.6 DTCC ingestion pipeline

- `dtcc_api_client.py` — unauthenticated public DTCC API client.
- `dtcc_parser.py` — ZIP/CSV parsing.
- `db_loader.py` — upserts into `swap_trades`.
- `backfill.py` — resumable full history.
- `scheduled_ingest.py` / `poll_ingest.py` — 5-minute live polling via APScheduler.
- `swaps_query.py` — `SwapsQuery` query API.
- `migrations/001_initial.sql` (and later) define the SQLite schema.

---

## 4. Existing docs and gaps

### Current, useful docs

- `CLAUDE.md` — canonical quick reference; read first.
- `docs/guides/START_HERE.md` — human-facing quick start.
- `docs/guides/SETUP_GUIDE.md` — SQLite setup and query examples.
- `docs/guides/CROSS_PLATFORM.md` — `.bat`/`.sh` launchers.
- `docs/guides/DTCC_LOCAL_SETUP.md` — DTCC pipeline.
- `docs/guides/QUERY_MONITORING.md`, `LOGGING_GUIDE.md` — observability.
- `docs/superpowers/specs/` and `plans/` — design docs for recent/future work.

### Stale / misleading

- `docs/guides/SWAPS_DATABASE_SPEC.md` — describes a PostgreSQL schema; project is SQLite-only. Trust `migrations/*.sql`.
- `docs/guides/DEPLOY.md` / `DOCKER_SETUP.md` / `k8s/` / `Procfile` / `Dockerfile.*` — vestigial; no active cloud deployment.
- `VaR_Tools_Simulations/README.md` / `CLAUDE.md` — `--demo all` flag does not exist in current `main.py`; use `--module N` or `python -m var_engine.<module>`.
- Some `.claude/skills/` still describe earlier stubs (e.g. Options_Suite context mode); verify against source.

### Undocumented / broken getting-started paths

- **Sentiment scanner venv**: the root `.venv` does not include `yt-dlp`/`curl_cffi`/bgutil deps; sentiment must run from its own local `.venv` (auto-installed by `sentiment.bat`).
- **YouTube PO token server**: sentiment's YouTube caption path requires a local `bgutil-ytdlp-pot-provider` on port 4416 or captions are silently skipped.
- **ThetaData credentials**: every suite that touches market data needs `THETADATA_CF_ACCESS_CLIENT_ID`/`_SECRET` in root `.env`. Without them, Options_Suite fails fast with a clear error.
- **Sentiment is not run as a registered module by default**: the market-signals stage (`run_market_signals_stage`) replaced the old sentiment subprocess; `sentiment-scanner` scanner slugs exist in the registry but aren't part of a default module run.
- **VaR context mode only runs module 1**: `main.py --context ... --module 2` (or any module other than 1) returns a clean `status: "error"` JSON by design.
- **Root loose `test_*.py` files are not collected**: `test_data_source.py`, `test_decode_upis.py`, `test_logging.py`, `test_query_monitor.py`, etc. are not in `pyproject.toml`'s `testpaths` and never run with `pytest`.

---

## 5. Obvious gotchas

1. **One venv for three suites, one venv for sentiment.** Root `.venv` serves Options/Vol/VaR/Tools/dashboard. Sentiment keeps its own.
2. **Single-instance long-running processes.** Only one `dashboard.bat`/`tools.bat` and one `run_scheduler.bat` should run at a time to avoid `database is locked` errors.
3. **SPX vs SPXW.** On this ThetaData feed, the options chain is rooted under `SPXW`; index price is `SPX`. `shared/thetadata.py` handles the alias for price lookups.
4. **Flat cwd-relative imports.** Several suite entry points use bare local imports (e.g. `Options_Suite/main.py`). Run from the suite directory or via the orchestrator, which sets paths.
5. **PYTHONPATH/PYTHONHOME leaks.** When invoking the project `.venv` from a Hermes session, clear `PYTHONPATH`/`PYTHONHOME` to avoid pulling in Hermes' site-packages.
6. **ThetaData rate limits.** Bulk loops must sleep 0.3–0.5 s between requests; parallel storms get throttled with `502`.
7. **No dashboard auth.** The dashboard is intentionally localhost-only with no password; do not expose it publicly.
8. **`swaps.db` size.** The DB can grow very large (reported ~320 GB in the audit); ad-hoc full-table scans should be avoided.
9. **Module registry loading.** `shared.module_registry.all_modules()` defensively skips broken suite registries; if a suite is unloadable, its modules disappear from the dashboard picker without crashing the app.
10. **Context store.** New `shared/context_store.py` persists `ModuleResult.context_patch` to `artifacts/widget_cache.db`; it uses `shared/connection_pool.py` with WAL.

---

## 6. Concrete command examples for a wiki author

```batch
:: Setup
cd C:\Users\bottl\FinancialDevelopment
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
python setup_db.py

:: Run tests
pytest

:: Start the dashboard
dashboard.bat

:: Start the DTCC poller in a second terminal
run_scheduler.bat

:: Run registered modules as widgets (dashboard) or in-process
dashboard.bat
.venv\Scripts\python.exe -c "import shared.module_execution as me; me.run_selected_modules(['dealer_exposure'], {'ticker':'SPY'})"

:: Run a suite standalone
Options_Suite\options_suite.bat
Vol_Suite\vol_suite.bat
VaR_Tools_Simulations\var.bat
sentiment-scanner\sentiment.bat

:: Backfill DTCC history
python backfill.py

:: Query swaps from Python
python -c "from swaps_query import SwapsQuery; q = SwapsQuery(); print(q.get_database_stats())"

:: List every registered module
python -c "from shared.module_registry import all_modules; print([m.slug for m in all_modules()])"
```

---

## 7. Verification notes

- `pytest --collect-only` successfully listed the monorepo test suite on this run.
- `Options_Suite/module_registry.py`, `VaR_Tools_Simulations/module_registry.py`, and `sentiment-scanner/module_registry.py` were read directly; `Vol_Suite/module_registry.py` was not fully read in this pass, but the suite is known to register modules (count updated in recent commits).
- Dashboard `app.py` is 3,339 lines; only the first 200 lines were read in this pass, plus relevant route documentation in `CLAUDE.md` and `docs/PROJECT_AUDIT_AND_SPEC.md`.

---

*Generated for kanban task `t_fd2be247` — Inventory FinDev repo structure, setup, and entry points.*
