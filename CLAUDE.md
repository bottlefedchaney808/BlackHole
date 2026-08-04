# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A quant-finance development monorepo: four independent analysis suites (options pricing, volatility,
VaR, sentiment) plus a DTCC equity-swaps ingestion pipeline, tied together by an orchestrator and a
local web dashboard. Everything shares one root `.venv` (Python 3.12) except `sentiment-scanner`,
which keeps its own project-local venv. There is no cloud deployment path in active use — this is
a single-machine, localhost-only setup (see `START_HERE.md` for the human-facing quick-start).

**Nested git repos**: `Options_Suite/` and `mcp-stockflow/` each contain their own independent `.git`
directory with their own remote (not submodules — no `.gitmodules`). Root-level `git` commands don't
see into them; treat them as separate repos when making changes inside those directories.

**This repo already has `.claude/skills/`** with detailed, verified debugging knowledge for each
suite's launch conventions, quirks, and known bugs — read the relevant one before debugging a launch
failure instead of re-deriving it:
- `quant-suite-launch-conventions.md` — shared reference (venv/port/env-var checks, `suite_context.json` contract, `Tools/registry.py` pattern). Read first; the others link to it.
- `options-suite`, `vol-suite`, `var-tools`, `sentiment-scanner` — per-suite entry points, flags, and known bugs.
- `tool-launcher` — orchestrator.py / dashboard `Tools/` layer, how child-suite failures surface as PASS/FAIL.

## Setup and common commands

First time only:
```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt   # Windows
.venv/bin/python -m pip install -r requirements.txt           # Linux/Mac
python setup_db.py
```

Every entrypoint ships as a matched `.bat` (Windows) / `.sh` (Linux/Mac) pair that do the same thing —
see `CROSS_PLATFORM.md` for exactly how path/venv resolution works on each OS.

| Task | Windows | Linux/Mac |
|---|---|---|
| Dashboard (main control surface, `http://127.0.0.1:8787`) | `dashboard.bat` | `dashboard.sh` |
| Tools module (`/tools`, same uvicorn process as dashboard) | `tools.bat` | — |
| DTCC live poller (leave running, polls every 5 min) | `run_scheduler.bat` | `run_scheduler.sh` |
| Orchestrator (cross-suite run) | `orchestrator.bat --unified --ticker NVDA --expiry 2026-10-16` | `orchestrator.sh ...` |
| Single suite via orchestrator | `orchestrator.bat --suite options\|vol\|var\|sentiment --ticker AAPL` | same |
| One-time full swap history backfill (resumable, hours-long) | `python backfill.py` | same |
| Query swap DB directly | `python swaps_query.py`, or `from swaps_query import SwapsQuery` | same |
| Run a suite standalone/interactively | `Options_Suite\options_suite.bat`, `Vol_Suite\vol_suite.bat`, `VaR_Tools_Simulations\var.bat`, `sentiment-scanner\sentiment.bat` | `.sh` equivalents |

Only run **one** `dashboard.bat`/`tools.bat` and **one** `run_scheduler.bat` at a time — both scripts
refuse to double-launch on their port, since a second instance racing the first against `swaps.db`
causes "database is locked" errors.

### Tests

```bash
pytest                          # whole monorepo: tests/, plus each suite's tests/ (see testpaths in pyproject.toml)
pytest -m unit                  # only tests needing no network/credentials
pytest tests/test_identifiers.py::test_some_case   # a single test
pytest Vol_Suite/tests/         # one suite only
```
`conftest.py` files add the repo root to `sys.path` so `shared.*` imports resolve inside suite tests.

### Lint

`pre-commit` runs `ruff` + `ruff-format` (and trailing-whitespace/EOF/JSON/YAML checks) — see
`.pre-commit-config.yaml`. Run `pre-commit run --all-files` or `ruff check .` / `ruff format .` directly.

## Architecture

### The spine: swaps DB → orchestrator → four suites → dashboard

```
DTCC public API ──(dtcc_api_client.py, dtcc_parser.py)──> db_loader.py ──> swaps.db (SQLite, WAL)
                                                                              ^
scheduled_ingest.py (5-min poll, APScheduler) / backfill.py (full history) ─┘

orchestrator.py --unified  ──>  sentiment-scanner ──> Vol_Suite ──> {Options_Suite, VaR_Tools_Simulations}
  (subprocess per suite, via SHARED_PYTHON = .venv/Scripts/python.exe, never sys.executable)

dashboard/app.py (FastAPI, :8787)  ──>  triggers orchestrator runs, browses swaps.db, serves Tools/
```

**Suite dependency graph**, per `orchestrator.py::run_unified`: `sentiment → vol → {options, var}`
(options and var are parallel siblings; neither reads the other). Note: sentiment is currently
hard-skipped by default in `run_unified` ("unreliable network dependency", stubbed with `_DummyAudit`)
even though the module docstring describes it running first — check the actual code, not just the doc,
if a run's behavior around sentiment looks surprising.

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
- Each stage's output marker file is validated (`shared/suite_validation.py::validate_suite_output`)
  before the next stage trusts it; `--fail-on-suite-error` turns a bad stage into a hard abort.
  `orchestrator_runs` in `swaps.db` logs every run.

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
- **Market data client**: `thetadata.py` (43KB) — `ThetaDataController`, the merged replacement for
  what used to be separate `Options_Suite`/`Vol_Suite` ThetaData clients (`api.potatohedge.com`). All
  four suites, plus `Options_Suite/thetadata_controller.py` and `Vol_Suite/thetadata_client.py` (both
  now thin re-export stubs), route through this one implementation — despite `Options_Suite` and
  `mcp-stockflow` having their own nested `.git`/`.venv`, their live data layer is fully merged into
  the parent repo's `shared/`, not independent.

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
  against live ThetaData quotes. `main.py` is the entry point but is deliberately small (~120 lines,
  two flags: `--context`/`--context-out`) — the heavier pricing/reporting code
  (`vol_manager.py`, `chain_evaluation.py`, `reports.py`, `VannaVolga.py`, `bruteforceimpliedvol.py`) is
  **not currently wired into `main.py`**; it's reachable via a separate `chain_evaluation.py` →
  `reports.py` path. Has its own nested git repo + venv but shares `shared/thetadata.py` for data.
- **Vol_Suite** — the largest suite (`volatility_suite.py`, ~1850 lines): dealer positioning (three
  sign conventions — `oi_heuristic` v1, `replication` v2-1b, `vol_surface_replication` v2-1a+1b),
  variance-swap replication (Carr-Madan/Demeterfi) and VRP term structure, correlation/basket
  construction, GARCH(1,1), a multi-ticker screener, and a strategy recommender. Builds one shared
  context per focus ticker and runs it through `_run_core_analysis`. Owns the `suite_context.json`
  schema (`suite_context.py`) that the other suites consume. `instrument_resolver.py` bridges tickers
  to cross-source UPIs via `shared.identifiers`. `README.md` flags `variance_swap_screener.py` as
  needing a rewrite: missing realized vol silently becomes `0.0`, and a blanket `except Exception` drops
  failing tickers from output without marking them failed.
- **VaR_Tools_Simulations** — a Python port of a legacy Excel VaR toolkit (`VaRtools Samples.xls` is
  the source spec), one `var_engine/` module per original sheet: `corr_sim.py` (correlated GBM Monte
  Carlo), `mc_sim.py`, `hist_sim.py` (basic/Hull-White/FHS-GARCH), `copulas.py` (Gaussian/Student-T/
  Clayton), `forex_var.py`, `cashflow_map.py`, `stress_test.py`, `var_agg.py` (EWMA + PCA + Euler
  allocation), plus `hedge_optimizer.py` and `price_dist.py`. `main.py` supports `--demo all` /
  `--demo <module>` for network-free canned runs. Its only `shared/` touchpoint is
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

`dashboard/app.py` (FastAPI, `:8787`, localhost-only) serves the swap-data browser, orchestrator
trigger UI (`POST /run/{suite_or_unified}`), run status, cross-source analytics, and query-monitor
metrics; `tools.bat` opens the same uvicorn process at `/tools` instead of `/`. `Tools/` is a
lighter-weight plugin surface for one-off tools (distinct from adding a whole new suite): implement
`run(context: dict) -> dict` in `Tools/tools/`, wrap it in a `ToolSpec`, register it in
`Tools/registry.py::TOOLS`. `Tools/context_loader.py` discovers/loads/re-validates
`suite_context.json` files from any suite's output directory.

There is no auth on the dashboard — it's a deliberate, permanent decision (`dashboard/auth.py` only has
a `get_client_ip` helper left; the API-key gate on `POST /run/*` and the worker-dispatch routes was
removed since this is a single-user, localhost-only tool that already keeps its real secrets
(ThetaData credentials) in plaintext in `.env`). Sharing it publicly (e.g. via `cloudflared tunnel`)
exposes all swap data and lets anyone trigger billed ThetaData-backed orchestrator runs, including
worker-dispatch (an LLM-driven subprocess with filesystem write access). Don't expose it beyond
localhost without adding real auth back first.

## Notable env vars

All in the single root `.env` (see `.env.example`): `THETADATA_CF_ACCESS_CLIENT_ID`/`_SECRET`
(required — ThetaData/PotatoHedge proxy credentials), `SWAPS_DB_PATH` (defaults to `./swaps.db`),
`LOG_LEVEL`, `DATA_SOURCES` (which `adapters/` are active).
