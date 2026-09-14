# The Engine

The engine is the analysis pipeline. It is a sequence of stages, each allowed to answer only its own question, and each validated before the next stage trusts it.

## Pipeline stages

A standard unified run executes three phases.

| # | Stage | Mechanism | What it answers |
|---|---|---|---|
| 1 | **Vol_Suite** | Subprocess: `Vol_Suite/volatility_suite.py` | What is the volatility surface, the dealer positioning, the variance-swap fair strike, the GARCH conditional vol, and the peer-basket correlation for this ticker? |
| 2 | **Market Signals** | In-process: `run_market_signals_stage` inside `orchestrator.py` | What do IV rank, max pain, skew, unusual OI, 1-year simulations, and the Direction 5-tool suite say? |
| 3 | **Options + VaR** | Parallel subprocesses: `Options_Suite/main.py`, `VaR_Tools_Simulations/main.py` | What is the option price/Greeks, and what is the 1-day/99% VaR/CVaR, using the context Vol_Suite produced? |

The historical `sentiment-scanner` subprocess is hard-skipped by default. Its option-chain scanners are replaced by the in-process Market Signals stage, which runs after Vol_Suite.

## Stage detail

### Phase 1 — Vol_Suite

- Resolves the focus ticker and a peer basket (defaults to index constituents vs. SPY).
- Fits a volatility surface, computes variance-swap fair strikes, and runs a GARCH(1,1) fit.
- Computes dealer positioning using the configured sign model (`vol_surface_replication` by default).
- Produces a correlation matrix and pair CSVs for the basket.
- Writes `vol_result.json` and side artifacts (CSVs, PNGs).

### Phase 2 — Market Signals

- Reuses Vol_Suite's GARCH fit instead of re-fitting.
- Runs option-chain scanners: `iv_rank`, `max_pain`, `skew`, `unusual_oi`.
- Runs 1-year-out simulations via VaR engine builders.
- Runs the Direction 5-tool signal suite.
- Writes `sentiment_result.json` and `suite_context_sentiment.json`.

### Phase 3 — Options + VaR

Both suites read the same `suite_context.json`. They run in parallel; neither reads the other's output.

- **Options_Suite** prices the focus option with the default Leisen-Reimer tree, fetches live spot/rate/dividend-yield, and writes `options_result.json`.
- **VaR_Tools_Simulations** runs `corr_sim` (module 1) using the threaded correlation matrix and individual vols when they match; otherwise falls back to an identity correlation matrix and a flat 0.25 annual vol. Writes `var_result.json`.

## The context contract

`Vol_Suite/suite_context.py` owns the schema for `suite_context.json`. The orchestrator writes it once per run, and consumer suites receive it via `--context`. Each producer suite writes its own marker file (`<suite>_result.json`), validated by `shared/suite_validation.py` and `shared/schemas.py`.

## Key output artifacts

| Artifact | Producer | Contains |
|---|---|---|
| `suite_context.json` | Orchestrator | Shared context: focus, basket, VaR settings, swap activity, controls |
| `vol_result.json` | Vol_Suite | Vol surface, dealer positioning, gamma records, correlation metadata |
| `options_result.json` | Options_Suite | Pricing method, IV, price, greeks, error detail |
| `var_result.json` | VaR_Tools_Simulations | VaR/CVaR, confidence, horizon, fallback notes |
| `sentiment_result.json` | Market-signals stage | Scanner metrics, 1-year simulations, Direction conviction |

All outputs land under `orchestrator_output/<run_id>/`.

## What this page is not

It is not the command reference. For copy-pasteable commands and a failure-mode table, see the [Analysis Pipeline Runbook](../guides/FINANCIALDEVELOPMENT_ANALYSIS_PIPELINE_RUNBOOK.md).

Next: how to read the outputs once the engine has produced them.
