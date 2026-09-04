# FinancialDevelopment Analysis Pipeline Runbook

> How the FinDev analysis pipeline is run, what it consumes, what it produces, and how to tell a healthy run from a broken one.

## 1. What this runbook covers

This repo (`C:\Users\bottl\FinancialDevelopment`) turns live option-chain / swap / sentiment data into cross-suite quantitative analysis. The entry point for most analysis work is the orchestrator: a headless dependency runner that feeds a single `suite_context.json` through the suites in the correct order and records what happened.

This runbook focuses on the **analysis pipeline** — the orchestrator-driven path that produces `vol_result.json`, `options_result.json`, `var_result.json`, and `sentiment_result.json` for a given ticker. It does not cover trading, order placement, or the DTCC ingestion back-end in depth; those are documented in `DTCC_LOCAL_SETUP.md` and `START_HERE.md`.

## 2. Pipeline stages (in order)

A standard unified run executes three phases. Each phase is gated: the orchestrator validates the marker file from a stage before the next stage is allowed to consume it.

| Phase | Stage | What it answers | Runs in |
|---|---|---|---|
| 1 | **Vol_Suite** | What is the volatility surface, dealer gamma exposure, variance-swap fair strike, GARCH conditional vol, and peer basket correlation for this ticker? | Subprocess (`Vol_Suite/volatility_suite.py`) |
| 2 | **Market Signals** | What do option-chain scanners (IV rank, max pain, skew, unusual OI) plus 1-year simulations and the Direction 5-tool suite say about the ticker? | In-process (`run_market_signals_stage`) |
| 3 | **Options + VaR** | What is the option price/Greeks and the 1-day/99% portfolio VaR/CVaR, using the vol/correlation context Vol_Suite produced? | Subprocesses in parallel (`Options_Suite/main.py`, `VaR_Tools_Simulations/main.py`) |

Historical note: the old `sentiment-scanner` subprocess (social-media scraping) is hard-skipped by default and replaced by the in-process Market Signals stage. The scanner slugs still live under `sentiment-scanner/module_registry.py`, but the unified pipeline reaches them through the in-process registry call, not a subprocess.

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

| Knob | Default | Effect |
|---|---|---|
| `--target-years` | `0.25` | Horizon for vol/GARCH/variance-swap analysis when no explicit expiry is given |
| `--expiry` | none | Explicit expiration in `YYYY-MM-DD` |
| `--strike` | ATM | Optional strike for the options pricer |
| `--option-type` | `call` | `call` or `put` |
| `--index` | `SPY` | Basket benchmark index |
| `--timeout` | `1800` | Per-suite timeout in seconds |
| `--fail-on-suite-error` | off | Abort the chain at the first failing suite instead of continuing degraded |
| `--no-validate` | off | Skip explicit output validation (debug only) |
| `SUITE_VALIDATION_STRICT=0` | `1` | Downgrade missing side-artifact checks from FAIL to WARN |
| `VS_RUN_CHAIN_SCANNER=1` | `1` in orchestrator runs | Ensures `chain_strategies.json` is produced by Vol_Suite |

### 3.3 Output artifacts

All outputs for a run land under `orchestrator_output/<run_id>/`.

| Artifact | Producer | Contains |
|---|---|---|
| `suite_context.json` | Orchestrator | The canonical shared context: focus, basket, var settings, swap_activity, controls |
| `suite_context_<suite>.json` | Orchestrator | Per-suite copy of the context used to launch that suite |
| `vol_result.json` | Vol_Suite | Vol surface, dealer positioning, gamma records, correlation matrix metadata, produced files list |
| `options_result.json` | Options_Suite | Pricing method, IV, price, Greeks, and any error detail |
| `var_result.json` | VaR_Tools_Simulations | VaR/CVaR, confidence, horizon, notes on fallback assumptions |
| `sentiment_result.json` | Market-signals stage | Scanner metrics, 1-year simulations, Direction conviction |
| `swaps_result.json` | Orchestrator | Recent DTCC top-notional rows (optional enrichment) |
| `correlation_matrix_*.csv` / `correlation_pairs_*.csv` | Vol_Suite | Basket correlation data |
| `*_garch_*.png`, `*_hedging_heatmap_*.png`, `*_greek_exposure_comparison_*.png` | Vol_Suite | Visual summaries |
| `NVDA_*_variance_swap_*.csv/png` | Vol_Suite | Variance-swap strike tables and plots |
| `*_gamma_records_*.csv` | Vol_Suite | Per-strike gamma records |

## 4. Minimal end-to-end example

### 4.1 Run the pipeline from the command line

Open a terminal at the repo root and run:

```bash
orchestrator.bat --unified --ticker NVDA --expiry 2026-10-16
```

Linux/Mac equivalent:

```bash
./orchestrator.sh --unified --ticker NVDA --expiry 2026-10-16
```

The orchestrator prints phase headers and a final summary block similar to:

```text
[unified] run_id=20260730T085905Z output_dir=...\orchestrator_output\20260730T085905Z

[1/3] VOL SUITE
       Dealer positioning / vol surface / gamma exposure

[2/3] MARKET SIGNALS
       IV Rank / Max Pain / Skew / Unusual OI + MC/copula/corr sims + Direction suite

[3/3] OPTIONS & VAR SUITES
       Option pricing + value-at-risk analysis (parallel)

============================================================
  Suites completed: 2/3
  Context audit: PASSED
============================================================
```

A successful run returns exit code 0; a degraded run (some suite failed) returns exit code 1.

### 4.2 Read the report

Navigate to the output directory and inspect the marker files:

```bash
cd orchestrator_output/20260730T085905Z
cat vol_result.json | python -m json.tool | head -40
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

If `options_result.json` shows `"status": "error"`, read the `error` field and the suite stderr tail in the orchestrator log.

## 5. Common failure modes and what they mean

| Symptom | Likely cause | What to do |
|---|---|---|
| `options_result.json`: "Could not fetch spot for ..." | Invalid ticker, bad ThetaData credentials, or network/API issue | Check ticker spelling; verify `.env` credentials; check ThetaData connectivity |
| `var_result.json` notes say "correlation matrix missing; used identity matrix" and/or "volatilities missing; used default annual volatility 0.25" | Vol_Suite failed or basket resolution returned no peers | Check Vol_Suite logs; verify ticker has option-chain data; the run continues with conservative defaults |
| `vol_result.json`: validation FAIL on `required_file[correlation_matrix_*.csv]` | Basket was single-name or correlation step failed | Set `SUITE_VALIDATION_STRICT=0` if a single-name run is expected, or investigate the vol failure |
| Orchestrator summary says `options=FAILED: ... validation=FAIL` | Options_Suite exited 0 but wrote a marker that failed `shared/schemas.py` validation | Check the validation errors in the orchestrator log; the most common cause is a missing `method`/`sigma`/`price`/`greeks` block |
| Suite timeout (returncode -1) | Slow network, heavy chain pull, or the suite is genuinely hanging | Increase `--timeout`; check ThetaData rate limits; avoid running multiple heavy vol runs in parallel |
| `sentiment_result.json` status `error` or `partial` | One or more market-signals scanners failed | Check the `errors` list inside `sentiment_result.json`; Direction or VaR-engine import failures are logged but non-fatal |
| `swaps_result.json` status `no_data` | `swaps.db` is cold or DTCC has no recent activity for the ticker | Run `python backfill.py` and/or leave `run_scheduler.bat` running |
| Orchestrator exits with "Shared interpreter not found" | Root `.venv` is missing or corrupt | Re-run `python -m venv .venv` and `pip install -r requirements.txt` |

## 6. Alternative ways to run

### 6.1 Single suite

Run only Vol_Suite for a ticker with an explicit horizon:

```bash
orchestrator.bat --suite vol --ticker AAPL --target-years 0.25
```

Valid suite names: `vol`, `options`, `var`, `sentiment`.

### 6.2 Modular runs

Run individual registered modules (additive path, does not use `run_unified`):

```bash
orchestrator.bat --modules dealer_exposure,dealer_flow --ticker SPY --target-years 0.25
```

List every available module:

```bash
orchestrator.bat --list-modules
```

### 6.3 From the dashboard

1. Start the dashboard: `dashboard.bat`
2. Open `http://127.0.0.1:8787`
3. Use the **Quant Console** (`/quant`) cards or `POST /run/unified` with a JSON body:

```json
{
  "ticker": "NVDA",
  "expiration_date": "2026-10-16"
}
```

The dashboard returns a `run_id`; poll `GET /runs/{run_id}` until `done` is true.

## 7. Key files to know

| File | Role |
|---|---|
| `orchestrator.py` | Main driver; `run_unified`, `run_suite`, `run_selected_modules` |
| `shared/suite_validation.py` | Marker-file validation rules (`SUITE_REQUIREMENTS`) |
| `shared/schemas.py` | JSON validators for each suite result |
| `shared/module_registry.py` | `ModuleSpec` / `ModuleResult` contract and `all_modules()` aggregator |
| `Vol_Suite/module_registry.py` | Vol_Suite modules: `dealer_exposure`, `dealer_flow`, `chain_scanner`, surface modules, etc. |
| `Options_Suite/module_registry.py` | Pricing modules: `crr`, `leisen_reimer`, `sabr`, `vanna_volga`, `mc`, etc. |
| `VaR_Tools_Simulations/module_registry.py` | VaR modules: `corr_sim`, `mc_sim`, `hist_sim`, `copulas`, etc. |
| `sentiment-scanner/module_registry.py` | Scanner modules: `gex`, `iv_rank`, `skew`, `max_pain`, etc. |

## 8. Typical workflow

1. **Prepare data**: ensure `swaps.db` is populated (`backfill.py` once, `run_scheduler.bat` ongoing).
2. **Run Vol_Suite first** if you only need volatility/dealer metrics: `--suite vol` is the fastest path.
3. **Run unified** when you want cross-suite consistency: `orchestrator.bat --unified --ticker TICKER --expiry YYYY-MM-DD`.
4. **Inspect outputs**: read `*_result.json` in `orchestrator_output/<run_id>/`.
5. **Iterate**: if a suite fails, fix the input/credentials and re-run only that suite, or use `--fail-on-suite-error` to fail fast during debugging.
