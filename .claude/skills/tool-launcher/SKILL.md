---
name: tool-launcher
description: Use when launching, running, or debugging orchestrator.py (root) or the dashboard's Tools/ framework — the layer that drives Vol_Suite, Options_Suite, VaR_Tools_Simulations and sentiment-scanner as child processes over a shared suite_context.json handoff.
---

## Overview

`orchestrator.py` (repo root) re-implements Vol_Suite's `_run_child_suite` at the root level: it builds one `suite_context.json` via `Vol_Suite/suite_context.py`, launches each child suite with the consolidated root `.venv` interpreter (`SHARED_PYTHON`, never `sys.executable`), and validates each child's marker file (`shared/suite_validation.py`) before letting the next stage consume it. `Tools/` (dashboard-side, `Tools/registry.py`'s `ToolSpec` pattern) is a separate, lighter integration surface for one-off tools that read a `suite_context.json` — see the shared conventions file, not repeated here.

For venv/port/PYTHONPATH preflight, defer to `.claude/skills/quant-suite-launch-conventions.md`.

## Launch

Interactive: `python orchestrator.py --interactive` (requires a TTY; fails cleanly with a CLI-flag fallback message otherwise). Guides mode (unified/vol-only/options/var/custom), ticker, expiration, strike, index.

Unified (CLI): `python orchestrator.py --unified --ticker NVDA --expiry 2026-10-16 [--strike K] [--option-type call|put] [--index SPY] [--target-years 0.25] [--timeout SEC] [--json] [--fail-on-suite-error] [--no-validate]`. Runs vol -> {options, var} in dependency order.

Single suite: `python orchestrator.py --suite {vol,options,var,sentiment} --ticker AAPL --target-years 0.25`. Builds context and launches only that one suite — no dependency chain, no sentiment-first pass.

## Debug — orchestrator failures given each sub-suite's known limits

`--suite var`: orchestrator's flags lambda for var never passes `--module`, so `main.py`'s context mode always defaults to module 1 (corr_sim) — the one path var-tools' `run_context_mode` actually implements. Confirmed against real `var_result.json` output in `orchestrator_output/`. This is the one config that always exercises var-tools' working path; it does **not** inherit the "unsupported module" failure mode described in `var-tools/SKILL.md`, because the orchestrator never asks for another module.

`--suite options`: hits Options_Suite's context-mode stub, and today that means **FAIL, not PASS**. Options_Suite/main.py's current `_build_options_result` writes only `status`, `ticker`, `expiry`, `pricing_models: []` — no `method`/`sigma`/`price`/`greeks` at all. Verified directly: feeding that exact payload shape to `shared/schemas.py::validate_options_result` raises `"method must be a non-empty string"` immediately. So `--suite options` (and the options leg of `--unified`) should report a FAIL verdict from `shared/suite_validation.py` on any current run — that's expected given the stub, not an orchestrator bug. (A historical `options_result.json` exists under `orchestrator_output/20260729T055003Z/` with real CRR pricing populated, which current source cannot produce — if you see an orchestrator options PASS with real numbers, either `main.py` has since been un-stubbed or you're looking at stale output; check `options-suite/SKILL.md` and the file's own timestamp before trusting it.)

`--suite vol`: full consumer (see vol-suite skill); `VS_RUN_*` env vars still gate the same optional sub-steps in this path.

`--suite sentiment` alone: builds a context and calls `main.py` with `--export-context`/`--no-loop` — works, but `run_unified`'s own sentiment stage is **hardcoded skipped** (`sentiment_result = {'status': 'skipped'}`) with a dummy always-PASS audit stub, despite the module docstring describing a full audited fold. Don't expect `--unified` runs to exercise the sentiment producer or the real `context_audit:sentiment` transaction — currently a no-op in that flow.

`--no-validate` + `--fail-on-suite-error` together is a parser error (nothing to fail on).

## Output / Audit Trail

Each run writes `orchestrator_output/<run_id>/`: `suite_context_<suite>.json` per launched child, `<suite>_result.json` markers (`vol_result.json`, `options_result.json`, `var_result.json`, `sentiment_result.json`), plus `suite_context.json` (unified runs only) and any suite-specific artifacts (CSVs, PNGs). Every stage and validation verdict also lands in the `orchestrator_runs` SQLite table (`swaps.db`), including `context_audit:sentiment` rows.

## Quick Reference

| Task | Command |
|---|---|
| Interactive | `python orchestrator.py --interactive` |
| Unified run | `python orchestrator.py --unified --ticker NVDA --expiry YYYY-MM-DD` |
| Single suite | `python orchestrator.py --suite {vol,options,var,sentiment} --ticker T --target-years 0.25` |
| Abort on first bad stage | add `--fail-on-suite-error` |
| Full JSON output | add `--json` |
| Skip validation (debug) | `--no-validate` |
| Run output | `orchestrator_output/<run_id>/` |
