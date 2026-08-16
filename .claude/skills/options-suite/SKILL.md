---
name: options-suite
description: Use when launching, running headlessly, or debugging Options_Suite (main.py) — options pricing (CRR, Leisen-Reimer, SABR, Vanna-Volga, MC, Heston, BAW) and Greeks in FinancialDevelopment/Options_Suite.
---

## Overview

Options_Suite is the smallest of the three suites: `main.py` is a ~120-line entry point, not a full pipeline runner. It exposes only two flags, `--context`/`--context-out`, plus a bare no-arg mode. The heavier pricing code — `vol_manager.py`, `bruteforceimpliedvol.py`, `VannaVolga.py`, `chain_evaluation.py`/`reports.py` — exists in the same directory but is **not currently imported by `main.py`** (confirmed by reading `main.py`'s import block and grepping for `chain_evaluation`/`vol_manager`/`VannaVolga` usage across the repo: those modules are wired together via `chain_evaluation.py` -> `reports.py`, a separate path `main.py` never touches).

`trade_journal.py` is a separate CLI for position journaling in this same directory — unrelated to launching/debugging the pricing engine, out of scope here.

For venv location, port checks, and `PYTHONPATH`/`PYTHONHOME` stripping, defer to `.claude/skills/quant-suite-launch-conventions.md`.

## Launch

Interactive: `Options_Suite\options_suite.bat` (no args). No-arg mode prints a version banner and returns 0 — it does **not** open a menu; the code literally says `"Interactive mode not yet implemented."`

Headless/context mode: `python main.py --context PATH --context-out PATH`. Verified by reading `run_context_mode`/`_build_options_result`: this is a **placeholder**, and the result is not actually schema-valid despite the docstring's claim. It reads `suite_context.json` and writes `options_result.json` with `status: "ok"`, `pricing_models: []`, but **no `method`, `sigma`, `price`, or `greeks` keys at all** — no CRR/SABR/Vanna-Volga/Heston pricing runs in context mode today. Confirmed by running the payload through `shared/schemas.py::validate_options_result` directly: it fails with `"method must be a non-empty string"` (and would also fail on missing sigma/price/greeks if method were added). This means a bare `main.py --context/--context-out` run reports success on stdout but **produces a payload that fails orchestrator-level validation** — see `tool-launcher/SKILL.md` for what that looks like from the orchestrator side. Note: an older result file exists at `orchestrator_output/20260729T055003Z/options_result.json` with real CRR pricing (`method`, `sigma`, `price`, `greeks` all populated) — current source can't produce that shape, so either an earlier version of `main.py` could and was later reverted/stubbed, or that file came from elsewhere. Worth asking about, not something this skill can resolve from static analysis alone.

Gotcha (verified by running `python3 main.py --help` — it fails, does not print help): the ThetaData credential check (`THETADATA_CF_ACCESS_CLIENT_ID`/`THETADATA_CF_ACCESS_CLIENT_SECRET`) runs at **module import time**, before argparse even executes. Even `--help` exits with the credentials error if those env vars aren't set. Set them (or populate `.env`) before invoking `main.py` at all.

Minor inconsistency (resolved): `options_suite.bat` calls `..\.venv\Scripts\python.exe` (repo-root venv); `options_suite.sh` previously called a suite-local `Financial_Dev_Env/bin/python3` (WSL-era venv name) — both now use the shared root `.venv` (`../.venv/bin/python3` on POSIX).

## Dashboard / Tools relationship

The dashboard's "Options Strategy Tool" (`dashboard/templates/tools_options_strategy.html`, backed by `Tools/tools/options_strategy_tool.py`) is a **genuinely separate code path**, not a UI wrapper around `Options_Suite`. It reads/re-runs `Vol_Suite/options_chain_scanner.py` + `strategy_recommender.py` output (`chain_strategies.json`), and never imports anything from `Options_Suite/`. Don't assume debugging the dashboard's options tool touches this suite's code.

## Debug / Common Mistakes

- `--help` failing isn't a broken parser — it's the credential check above; check env vars first.
- Empty `pricing_models` in a context-mode result is expected current behavior, not a bug to chase in `main.py`.
- If you need actual pricing output, call `vol_manager.py`/`chain_evaluation.py` directly — they aren't reachable through `main.py`'s CLI surface yet.
- **Pricing-model default: use Leisen-Reimer (LR), NOT CRR.** CRR's price oscillation at coarse steps makes LR the preferred default for the IV solve and the non-interactive/context path. When a run "tries to use CRR again", switch the model to LR — see `american_binomial.py` docstring and the `main.py` context-mode path that already uses LR sigma.

## Quick Reference

| Task | Command |
|---|---|
| Interactive launch | `Options_Suite\options_suite.bat` |
| Headless run | `python main.py --context ctx.json --context-out result.json` |
| Required env vars | `THETADATA_CF_ACCESS_CLIENT_ID`, `THETADATA_CF_ACCESS_CLIENT_SECRET` |
| Real pricing code | `vol_manager.py`, `chain_evaluation.py` (not via `main.py`) |
