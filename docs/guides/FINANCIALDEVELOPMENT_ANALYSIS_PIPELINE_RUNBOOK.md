# FinancialDevelopment Analysis Pipeline Runbook

> How the FinDev analysis pipeline is run, what it consumes, what it produces, and how to tell a healthy run from a broken one.

## 1. What this runbook covers

This repo (`C:\Users\bottl\FinancialDevelopment`) turns live option-chain / swap / sentiment data into cross-suite quantitative analysis. Since Phase 7 (2026-09-04) each registered module runs **in-process** via `shared/module_execution.run_selected_modules(slugs, context)` (scripted/agent) or the dashboard's `POST /api/widgets/{slug}/run` route; a module's `context_patch` is persisted to the Context Store so later modules read it without recomputing. There is no cross-suite subprocess orchestrator anymore (`run_suite`/`run_unified`/`--unified`/`--suite`, removed 2026-09-04).

This runbook focuses on the **analysis pipeline** — the module run that produces `vol_result.json`, `options_result.json`, `var_result.json`, and `sentiment_result.json` for a given ticker. It does not cover trading, order placement, or the DTCC ingestion back-end in depth; those are documented in `DTCC_LOCAL_SETUP.md` and `START_HERE.md`.

## 2. Pipeline stages (in order)

A full analysis run executes three stages. Each maps to a group of registered modules; a stage's output context feeds the next via the Context Store.

| Phase | Modules | What it answers | Where it runs |
|---|---|---|---|
| 1 | **Vol_Suite** (`dealer_exposure`, surface/vol-stats modules) | What is the volatility surface, dealer gamma exposure, variance-swap fair strike, GARCH conditional vol, and peer basket correlation for this ticker? | In-process module run |
| 2 | **Market Signals** | What do option-chain scanners (IV rank, max pain, skew, unusual OI) plus 1-year simulations and the Direction 5-tool suite say about the ticker? | In-process (`run_market_signals_stage`) |
| 3 | **Options + VaR** | What is the option price/Greeks and the 1-day/99% portfolio VaR/CVaR, using the vol/correlation context Vol_Suite produced? | In-process module runs |

Historical note: the old `sentiment-scanner` subprocess (social-media scraping) is hard-skipped by default and replaced by the in-process Market Signals stage. The scanner slugs still live under `sentiment-scanner/module_registry.py`, but a full run reaches them through the in-process registry call, not a subprocess.

### 2.1 Phase detail

#### Phase 1 — Vol_Suite

- Loads the focus ticker and resolves a basket (defaults to index constituents vs. `SPY`; falls back to a single-name basket if resolution fails).
- Fits a volatility surface, computes variance-swap fair strikes, and runs a GARCH(1,1) fit.
- Computes dealer positioning using the configured sign model (`vol_surface_replication` by default).
- Produces correlation matrix / pair CSVs for the basket.
- Writes `vol_result.json` and side artifacts (CSVs, PNGs).

#### Phase 2 — Market Signals

- Reuses Vol_Suite's GARCH fit (`focus.garch_conditional_vol`) instead of re-fitting.
- Runs option-chain scanners: `iv_rank`, `max_pain`, `skew`, `unusual_oi`.
- Runs 1-year-out simulations via VaR engine builders (MC, copula, corr_sim).
- Runs the `Direction` 5-tool signal suite.
- Writes `sentiment_result.json` and `suite_context_sentiment.json`.

#### Phase 3 — Options + VaR

Both suites read the same `suite_context.json` produced at the start of the run (with Vol_Suite's stats threaded in). They run in parallel; neither reads the other's output.

- **Options_Suite**: prices the focus option using the default Leisen-Reimer tree (or CRR/SABR/Vanna-Volga/etc. depending on the path), fetches live spot/rate/dividend-yield, and writes `options_result.json`.
- **VaR_Tools_Simulations**: in unified context mode this currently runs only `corr_sim` (module 1). It uses the threaded correlation matrix and individual vols when they match the basket; otherwise it falls back to an identity correlation matrix and a flat 0.25 annual vol. Writes `var_result.json`.

## 3. Inputs and outputs

### 3.1 Required inputs

| Input | Where it comes from | How to set it |
|---|---|---|
| Focus ticker | CLI / dashboard form | `--ticker NVDA` or `ticker` in POST body |
| Expiration or horizon | CLI / dashboard form | `--expiry 2026-10-16` or `--target-years 0.25` |
| ThetaData credentials | Root `.env` | `THETADATA_CF_ACCESS_CLIENT_ID`, `THETADATA_CF_ACCESS_CLIENT_SECRET` |
| Root venv | `C:\Users\bottl\FinancialDevelopment\.venv` | Created by `python -m venv .venv` + `pip install -r requirements.txt` |
| Swaps DB (optional enrichment) | `swaps.db` | Built by `python backfill.py` and/or `run_scheduler.bat` |

Optional inputs include strike, option type (`call`/`put`, default `call`), basket index (default `SPY`), and VaR horizon/confidence overrides.

### 3.2 Configuration knobs

Modules take their inputs from the run context dict (not CLI flags). Common
keys:

| Key | Default | Effect |
|---|---|---|
| `target_years` | `0.25` | Horizon for vol/GARCH/variance-swap analysis when no explicit expiry is given |
| `expiration_date` | none | Explicit expiration in `YYYY-MM-DD` |
| `strike` | ATM | Optional strike for the options pricer |
| `option_type` | `call` | `call` or `put` |
| `index_ticker` | `SPY` | Basket benchmark index |
| `SUITE_VALIDATION_STRICT=0` (env) | `1` | Downgrade missing side-artifact checks from FAIL to WARN |
| `VS_RUN_CHAIN_SCANNER=1` (env) | `1` in orchestrator runs | Ensures `chain_strategies.json` is produced by Vol_Suite |

### 3.3 Output artifacts

A module returns a `ModuleResult` (status/artifacts/metrics) and may also write
suite result files (e.g. `vol_result.json`, `options_result.json`) for the
suites that produce them. Every module's `context_patch` is persisted to the
Context Store (`artifacts/widget_cache.db`), keyed by run scope.

| Artifact | Producer | Contains |
|---|---|---|
| `ModuleResult` | Any registered module | status, artifacts, metrics, context_patch |
| Context-Store entries | module run route | `context_patch` values keyed by scope (tables `context_entries`, `context_store_audit`) |
| `vol_result.json` | Vol_Suite | Vol surface, dealer positioning, gamma records, correlation matrix metadata, produced files list |
| `options_result.json` | Options_Suite | Pricing method, IV, price, Greeks, and any error detail |
| `var_result.json` | VaR_Tools_Simulations | VaR/CVaR, confidence, horizon, notes on fallback assumptions |
| `sentiment_result.json` | Market-signals stage | Scanner metrics, 1-year simulations, Direction conviction |
| `correlation_matrix_*.csv` / `correlation_pairs_*.csv` | Vol_Suite | Basket correlation data |
| `*_garch_*.png`, `*_hedging_heatmap_*.png`, `*_greek_exposure_comparison_*.png` | Vol_Suite | Visual summaries |
| `NVDA_*_variance_swap_*.csv/png` | Vol_Suite | Variance-swap strike tables and plots |
| `*_gamma_records_*.csv` | Vol_Suite | Per-strike gamma records |

## 4. Minimal end-to-end example

### 4.1 Run the pipeline

Open a terminal at the repo root and run registered modules in-process (no
subprocess, no interpreter lookup):

```bash
.venv\Scripts\python.exe -c "import shared.module_execution as me; r = me.run_selected_modules(['chain_scanner','dealer_exposure'], {'ticker':'NVDA','expiration_date':'2026-10-16'}); print(r['status'], r['order'])"
```

Or drive the same modules as widgets from the dashboard (`dashboard.bat`, then
`POST /api/widgets/{slug}/run` / the Quant Console at `http://127.0.0.1:8787`).

The run prints a summary similar to:

```text
[module run] status=ok  order=['dealer_exposure', 'chain_scanner', ...]
```

A module run returns status `ok`; a degraded run (some module failed) returns
`partial`/`error` on the affected `ModuleResult`.

### 4.2 Read the report

A module run's `ModuleResult` carries its `status`, `artifacts`, and `metrics`
(the widget-run route also caches them for later `GET /api/widgets/{slug}/state`
reads). The suites that write result files still produce them for inspection:

```bash
# e.g. after running Vol_Suite / pricing / VaR modules:
cat vol_result.json | python -m json.tool | head -40    # where a suite wrote it
cat options_result.json
cat var_result.json
```

A healthy `vol_result.json` has `"status": "ok"`, a non-empty `produced_files` list, and numeric vol-surface/dealer-positioning blocks.

A healthy `options_result.json` looks like:

```json
{
  "suite": "options",
  "status": "ok",
  "ticker": "NVDA",
  "method": "LeisenReimer",
  "sigma": 0.4878,
  "price": 12.34,
  "greeks": { "delta": 0.52, "gamma": 0.03, "theta": -0.15, "vega": 0.21, "rho": 0.08 }
}
```

A healthy `var_result.json` looks like:

```json
{
  "suite": "var",
  "status": "ok",
  "module": "corr_sim",
  "var": 36693.86,
  "cvar": 41994.83,
  "confidence": 0.99,
  "horizon_days": 1,
  "timestamp": "2026-07-30T09:00:07.745572Z",
  "notes": "..."
}
```

If `options_result.json` shows `"status": "error"`, read the `error` field; a
failing module also surfaces `status: error`/`failed` with a `metrics.error` on
its `ModuleResult`.

## 5. Common failure modes and what they mean

| Symptom | Likely cause | What to do |
|---|---|---|
| `options_result.json`: "Could not fetch spot for ..." | Invalid ticker, bad ThetaData credentials, or network/API issue | Check ticker spelling; verify `.env` credentials; check ThetaData connectivity |
| `var_result.json` notes say "correlation matrix missing; used identity matrix" and/or "volatilities missing; used default annual volatility 0.25" | Context-Store lookup for the vol/correlation context failed or the producing module didn't run | Run the Vol_Suite context producer first (or confirm the Context-Store entries under the same scope); the run continues with conservative defaults |
| `vol_result.json`: validation FAIL on `required_file[correlation_matrix_*.csv]` | Basket was single-name or correlation step failed | Set `SUITE_VALIDATION_STRICT=0` if a single-name run is expected, or investigate the vol failure |
| A widget/module returns `status: error` with `metrics.error` naming a suite result | The module's underlying analysis failed | Read `metrics.error`; check the failing module's own logs; fix input/credentials and re-run that module |
| Module run hangs on a slow chain pull | Heavy chain fetch under ThetaData rate limits | Check ThetaData rate limits; avoid running multiple heavy vol modules in parallel |
| `sentiment_result.json` status `error` or `partial` | One or more market-signals scanners failed | Check the `errors` list inside `sentiment_result.json`; Direction or VaR-engine import failures are logged but non-fatal |
| `swaps_result.json` status `no_data` | `swaps.db` is cold or DTCC has no recent activity for the ticker | Run `python backfill.py` and/or leave `run_scheduler.bat` running |
| `ImportError` when running modules | Root `.venv` is missing or corrupt | Re-run `python -m venv .venv` and `pip install -r requirements.txt` |

## 6. Alternative ways to run

### 6.1 Run only Vol_Suite's modules

Vol_Suite's modules (dealer positioning, vol surface, variance-swap, GARCH)
are registered under slugs like `dealer_exposure` / `dealer_flow`. Run just
them for a ticker with an explicit horizon:

```bash
.venv\Scripts\python.exe -c "import shared.module_execution as me; me.run_selected_modules(['dealer_exposure'], {'ticker':'AAPL','target_years':0.25})"
```

### 6.2 Choose which registered modules run

`run_selected_modules` takes an explicit slug list and expands every
`requires` dependency transitively:

```bash
.venv\Scripts\python.exe -c "import shared.module_execution as me; me.run_selected_modules(['dealer_flow'], {'ticker':'SPY','target_years':0.25})"
```

List every available module from the shell:

```bash
.venv\Scripts\python.exe -c "from shared.module_registry import all_modules; [print(m.slug, '::', m.suite) for m in all_modules()]"
```

Or browse them on the dashboard: `GET /api/widgets/catalog`.

### 6.3 From the dashboard

1. Start the dashboard: `dashboard.bat`
2. Open `http://127.0.0.1:8787`
3. Use the **Quant Console** (`/quant`) to add a widget and run it, or call
   `POST /api/widgets/{slug}/run` directly:

```bash
curl -s -X POST http://127.0.0.1:8787/api/widgets/dealer_exposure/run \
  -H "Content-Type: application/json" -d '{"ticker":"NVDA","expiration_date":"2026-10-16"}'
```

The run returns the `ModuleResult` synchronously (status/artifacts/metrics);
fetch the last cached result later with `GET /api/widgets/{slug}/state`.

## 7. Key files to know

| File | Role |
|---|---|
| `orchestrator.py` | Surviving shell: re-exports `run_selected_modules`; `build_context`, `run_market_signals_stage`, `log_run`, DB helpers, `--modules`/`--interactive` CLI |
| `shared/module_execution.py` | `run_selected_modules`, `_expand_module_requires`, `_topo_sort_modules` — the in-process module runner |
| `shared/module_registry.py` | `ModuleSpec` / `ModuleResult` contract and `all_modules()` aggregator |
| `shared/context_store.py` | Context Store: persists module `context_patch` values keyed by scope (audit table `context_store_audit`) |
| `shared/schemas.py` | JSON validators for each suite result |
| `Vol_Suite/module_registry.py` | Vol_Suite modules: `dealer_exposure`, `dealer_flow`, `chain_scanner`, surface modules, etc. |
| `Options_Suite/module_registry.py` | Pricing modules: `crr`, `leisen_reimer`, `sabr`, `vanna_volga`, `mc`, etc. |
| `VaR_Tools_Simulations/module_registry.py` | VaR modules: `corr_sim`, `mc_sim`, `hist_sim`, `copulas`, etc. |
| `sentiment-scanner/module_registry.py` | Scanner modules: `gex`, `iv_rank`, `skew`, `max_pain`, etc. |

## 8. Typical workflow

1. **Prepare data**: ensure `swaps.db` is populated (`backfill.py` once, `run_scheduler.bat` ongoing).
2. **Run Vol_Suite's modules first** if you only need volatility/dealer metrics: `dealer_exposure` / `dealer_flow` are the fastest path.
3. **Run a fuller module set** when you want cross-suite consistency: pass the pricing + VaR slugs to `run_selected_modules` in dependency order.
4. **Inspect outputs**: read each module's `ModuleResult` (dashboard `GET /api/widgets/{slug}/state`, or `run_selected_modules`'s returned `results` dict).
5. **Iterate**: if a module fails, fix the input/credentials and re-run only that module.
