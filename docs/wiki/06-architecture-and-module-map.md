# Architecture and Module Map

This page maps the repo to the questions it answers. File paths are grouped by function, not by folder name.

## The spine

```
DTCC public API ──(dtcc_api_client.py, dtcc_parser.py)──> db_loader.py ──> swaps.db (SQLite, WAL)
                                                                              ^
scheduled_ingest.py (5-min poll, APScheduler) / backfill.py (full history) ──┘

orchestrator.py --unified  ──>  Vol_Suite ──> Market Signals ──> {Options_Suite, VaR_Tools_Simulations}
  (subprocess per suite, via SHARED_PYTHON = .venv/Scripts/python.exe)

dashboard/app.py (FastAPI, :8787)  ──>  triggers orchestrator runs, browses swaps.db, serves Tools/
```

## Entry points and control

| Question | File | Why it matters |
|---|---|---|
| How do I run the whole pipeline? | `orchestrator.py` | `run_unified`, `run_suite`, `run_selected_modules` |
| How do I launch the UI? | `dashboard.bat` / `dashboard/app.py` | FastAPI on `127.0.0.1:8787` |
| How do I keep swap data current? | `run_scheduler.bat` / `scheduled_ingest.py` | 5-minute DTCC poll |
| How do I populate history? | `python backfill.py` | Resumable full-history backfill |
| How do I query swaps? | `python swaps_query.py` | `SwapsQuery` API over `swaps.db` |

## Cross-cutting library

Everything in `shared/` is imported by more than one suite.

| Question | File | Why it matters |
|---|---|---|
| What is the market-data contract? | `shared/thetadata.py` | `ThetaDataController`; merged client for all suites |
| What is the inter-suite JSON contract? | `shared/schemas.py` | Validators for `suite_context.json` and all `*_result.json` files |
| What checks a marker file? | `shared/suite_validation.py` | `validate_suite_output`, `SUITE_REQUIREMENTS` |
| What registers launchable units? | `shared/module_registry.py` | `ModuleSpec`, `ModuleResult`, `all_modules()` |
| How are contexts persisted? | `shared/context_store.py` | SQLite-backed scoped key/value store |
| How does the DB stay safe under concurrency? | `shared/connection_pool.py` | Thread-safe SQLite pool, WAL mode |
| Where do env vars load? | `shared/config.py` | Single root `.env` loader |

## Suites

### Options_Suite

| Question | File |
|---|---|
| How do I price an American option headlessly? | `Options_Suite/main.py` |
| What pricing models are registered? | `Options_Suite/module_registry.py` |
| Where are the pricing engines? | `Options_Suite/american_binomial.py`, `barone_adesi_whaley.py`, `MC.py`, `MCHestonLSM.py`, `SABRModel.py`, `VannaVolga.py`, `NewtonRaphsonIV.py` |
| Where is the multi-model comparison path? | `Options_Suite/chain_evaluation.py`, `reports.py` |

### Vol_Suite

| Question | File |
|---|---|
| Where is the main focus-ticker pipeline? | `Vol_Suite/volatility_suite.py` |
| Where are the sign conventions? | `Vol_Suite/dealer_positioning.py` |
| Where is variance-swap analytics? | `Vol_Suite/variance_swap_screener.py`, `variance_swap_live.py`, `vrp_term_structure.py` |
| Where is GARCH? | `Vol_Suite/garch_analysis.py` |
| Where is the suite context schema defined? | `Vol_Suite/suite_context.py` |
| Where are vol modules registered? | `Vol_Suite/module_registry.py` |

### VaR_Tools_Simulations

| Question | File |
|---|---|
| Where is the entry point? | `VaR_Tools_Simulations/main.py` |
| Where are the VaR modules? | `VaR_Tools_Simulations/var_engine/` |
| Which module runs in context mode? | `corr_sim` (module 1) only; others return `status: error` in context mode |

### sentiment-scanner

| Question | File |
|---|---|
| Where is the main scanner loop? | `sentiment-scanner/main.py` |
| Where are the option-flow scanners? | `sentiment-scanner/scanner/` |
| Where is the signal engine? | `sentiment-scanner/correlation/engine.py` |

## Tools plugin surface

| Question | File |
|---|---|
| Where are tools registered? | `Tools/registry.py` |
| Where are tool implementations? | `Tools/tools/` |
| How does a tool load a suite context? | `Tools/context_loader.py` |

## What this page is not

It is not a folder-by-folder tour. It is a question-first map. If you need the exact command to run a suite, see the [Analysis Pipeline Runbook](../guides/FINANCIALDEVELOPMENT_ANALYSIS_PIPELINE_RUNBOOK.md).

Next: the workflows that string these files together.
