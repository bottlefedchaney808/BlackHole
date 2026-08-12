---
name: vol-suite
description: Use when launching, running headlessly, or debugging Vol_Suite (volatility_suite.py) — dealer positioning, correlation/basket analysis, variance-swap replication, GARCH, and screener workflows in FinancialDevelopment/Vol_Suite.
---

## Overview

Vol_Suite is a focus-ticker-driven options/volatility analysis CLI (`Vol_Suite/volatility_suite.py`, ~1850 lines). It builds one shared context (focus ticker -> index/basket -> correlation stats -> variance-swap replication -> dealer positioning/GARCH/screener) and runs it through `_run_core_analysis`, shared by both interactive run modes. ThetaData is the primary data source; yfinance is a per-ticker fallback only.

For venv location, port checks, and `PYTHONPATH`/`PYTHONHOME` stripping, defer to `.claude/skills/quant-suite-launch-conventions.md` — do not re-derive those here.

## Launch

Interactive: `Vol_Suite\vol_suite.bat` (or `vol_suite.sh`), prompts for run mode (1 = focus workflow, 2 = unified cross-suite run with optional Options_Suite/VaR launch).

Headless/context mode: `python volatility_suite.py --context PATH --context-out PATH`. Unlike VaR_Tools' context mode (one of ten modes only), Vol_Suite's is a full consumer — same `_run_core_analysis` pipeline as interactive. Gotcha: several wall-clock-expensive sub-steps (group screener, chain scanner, vol-surface-2D, VRP term structure, sentiment backtest) are interactive prompts in menu mode but **env-var gated in context mode**, defaulting off: `VS_RUN_CHAIN_SCANNER` (the orchestrator sets this to 1 itself), `VS_RUN_GROUP_SCREENER`, `VS_RUN_VOL_SURFACE_2D`, `VS_RUN_SENTIMENT_BACKTEST`. The one exception is `VS_RUN_VRP_TERM_STRUCTURE`, which defaults **on** (set it to `0` to skip) — it costs about one chain fetch per tenor and its table now prints in the vol-suite console block as well as landing in `vol_result.json`. If a headless run skips a section you expected, check these before suspecting a bug. Context mode never launches Options_Suite/VaR as children, even if the context's `controls` say to — that's the interactive unified flow's job only.

## Debug / Common Mistakes

FIX_PLAN items re-checked against current source (not trusted from the docs) — already fixed, don't re-flag:
- `dealer_positioning.py:537` — `hedge_requirement = abs(net_dollar_gamma * 0.01)`, no spurious `/spot`.
- `dealer_positioning.py:198,207` — `bs_gamma`/`bs_gamma_vec` both apply `math.exp(-q * T)`.
- `correlation_engine.py:224-260` — filters to `valid_tickers`/`valid_weights`; a bad/delisted basket ticker is dropped with a note, not a `KeyError`.
- `backtest_stage3.py:69,313` — `TRADING_DAYS_PER_YEAR = 252`, forward price includes the dividend leg.
- `variance_swap_live.py:164-172` / `variance_swap_screener.py:169-176` — ATM IV stays `NaN` (not fabricated `0.0`); screener forces `INSUFFICIENT DATA`.
- `variance_swap_screener.py:163-164` — has the DDKZ `boundary_correction`, matching `variance_swap_live.py`.
- `replication_reference.py` OI accumulation — distinguishes "strike absent" from "strike present, OI=0" before computing `delta_oi`.

Still genuinely live: **no OI magnitude/outlier filtering in `dealer_positioning.py`'s gamma aggregation** (`oi > 0` only, line 449) — one large stale/illiquid strike can dominate the gamma/vanna read. Deliberately deferred (FIX_PLAN_20260725.md issue 4); confirmed still absent, no z-score/percentile/concentration logic exists in the file. If dealer-positioning output looks strike-dominated, this is why.

`requirements.txt` is runtime-only; `pytest` lives in `requirements-dev.txt`. The shared root `.venv` is usually built from `requirements.txt` alone, so `pytest` will fail with `ModuleNotFoundError` unless `requirements-dev.txt` was also installed.

## Quick Reference

| Task | Command |
|---|---|
| Interactive launch | `Vol_Suite\vol_suite.bat` |
| Headless run | `python volatility_suite.py --context ctx.json --context-out result.json` |
| Enable optional headless steps | set `VS_RUN_*` env vars before headless run |
| Run tests | `pip install -r requirements-dev.txt` then `pytest tests/ -q` |
