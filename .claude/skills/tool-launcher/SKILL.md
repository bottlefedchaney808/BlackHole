---
name: tool-launcher
description: Use when launching, running, or debugging orchestrator.py (root) or the dashboard's Tools/ framework — the layer that drives Vol_Suite, Options_Suite, VaR_Tools_Simulations and sentiment-scanner as child processes over a shared suite_context.json handoff.
---

## Overview

`orchestrator.py` (repo root) re-implements Vol_Suite's `_run_child_suite` at the root level: it builds one `suite_context.json` via `Vol_Suite/suite_context.py`, launches each child suite with the consolidated root `.venv` interpreter (`SHARED_PYTHON`, never `sys.executable`), and validates each child's marker file (`shared/suite_validation.py`) before letting the next stage consume it. `Tools/` (dashboard-side, `Tools/registry.py`'s `ToolSpec` pattern) is a separate, lighter integration surface for one-off tools that read a `suite_context.json` — see the shared conventions file, not repeated here.

For venv/port/PYTHONPATH preflight, defer to `.claude/skills/quant-suite-launch-conventions.md`.

## Launch

**After a code fix, do not start** orchestrator / unified / dashboard unless the user explicitly asked to run. Default to a handoff package.

Interactive: `python orchestrator.py --interactive` (requires a TTY; fails cleanly with a CLI-flag fallback message otherwise). Guides mode (unified/vol-only/options/var/custom), ticker, expiration, strike, index.

Unified (CLI): `python orchestrator.py --unified --ticker NVDA --expiry 2026-10-16 [--strike K] [--option-type call|put] [--index SPY] [--target-years 0.25] [--timeout SEC] [--json] [--fail-on-suite-error] [--no-validate]`. Runs vol -> {options, var} in dependency order.

Selectable modules (additive; does not change `run_unified`): `python orchestrator.py --modules dealer_exposure,chain_scanner --ticker SPY` (also `--modules-category`, `--all-modules`, `--list-modules`). Resolves via `shared.module_registry`. Default with no `--modules` is unchanged.

Single suite: `python orchestrator.py --suite {vol,options,var,sentiment} --ticker AAPL --target-years 0.25`. Builds context and launches only that one suite — no dependency chain, no sentiment-first pass.

## Debug — orchestrator failures given each sub-suite's known limits

`--suite var`: orchestrator's flags lambda for var never passes `--module`, so `main.py`'s context mode always defaults to module 1 (corr_sim) — the one path var-tools' `run_context_mode` actually implements. Confirmed against real `var_result.json` output in `orchestrator_output/`. This is the one config that always exercises var-tools' working path; it does **not** inherit the "unsupported module" failure mode described in `var-tools/SKILL.md`, because the orchestrator never asks for another module.

`--suite options`: runs Options_Suite's real context mode (`run_context_mode`, `main.py:144`), which fetches live spot/rate/dividend-yield and solves LeisenReimer pricing + Greeks — on a normal run this **passes** `shared/schemas.py::validate_options_result` (`method`/`sigma`/`price`/`greeks` all populated). (Corrected 2026-08-17 — this skill previously described a `_build_options_result` stub producing an empty `pricing_models: []` payload that always FAILs validation; that function doesn't exist in current source. See `options-suite/SKILL.md` for what context mode actually produces.) A FAIL here now means a real problem — missing context fields, a ThetaData error, or an IV-solve failure — not expected stub behavior; investigate it as you would any other suite's FAIL.

`--suite vol`: full consumer (see vol-suite skill); `VS_RUN_*` env vars still gate the same optional sub-steps in this path.

**suite_context threading (do not re-debug as a scanner crash):** after vol, `_thread_vol_stats_into_context` (`orchestrator.py` ~1167–1302) must copy GARCH / fair-vol onto `context.focus` even when vol FAIL (via payload). Scanner `0.0` / MC vol hardcoded `0.25` / empty max-pain expiry means the thread was missed.

| Field | Source | If missing |
|---|---|---|
| `focus.expiration_date` | user / context | max-pain and OI snapshot have nothing to pin |
| `IV_RANK_GARCH_COND_VOL_PCT` | vol `garch_conditional_vol` | GARCH prints 0 |
| `IV_RANK_FAIR_VOL_PCT` | vol `fair_vol_pct` | fair vol prints 0 |
| MC sim vol | same thread, not a constant | hardcoded 0.25 |
| dealer stage | expiry_book live path | skipped / wrong file |

`--suite sentiment` alone: builds a context and calls `main.py` with `--export-context`/`--no-loop` — works, but `run_unified`'s own sentiment stage is **hardcoded skipped** (`sentiment_result = {'status': 'skipped'}`) with a dummy always-PASS audit stub, despite the module docstring describing a full audited fold. Don't expect `--unified` runs to exercise the sentiment producer or the real `context_audit:sentiment` transaction — currently a no-op in that flow.

`--no-validate` + `--fail-on-suite-error` together is a parser error (nothing to fail on).

## Dashboard quant-summary gotchas (`shared/summary.py`)

Added 2026-08-17 — this was the repo's single highest fix-ratio area (dashboard commits are 60-75% `fix` type across every worktree sampled) and the recurring bug class wasn't written down anywhere. Both fixes below live in `shared/summary.py`, not `dashboard/app.py` itself:
- **`_extract_sentiment` must understand BOTH bundle shapes.** `sentiment_result.json` has two producers — the real sentiment-scanner's `--export-context` (`sentiment.ranked_tickers`) and, on every `--unified` run, `orchestrator.py::run_market_signals_stage`'s scanners/simulations/direction bundle. Recognizing only the sentiment-scanner shape makes the quant summary's market-signals module fall through to `degraded` and hides a full run's real numbers behind a fake failure (fixed `8f284b9`).
- **Bundle-level `errors` must be surfaced even when per-item scanner/sim dicts are empty.** `run_market_signals_stage` can put scanner import failures, GARCH fit failures, or direction-suite crashes into a bundle-level `errors` list while leaving `scanners`/`simulations` empty — a per-key `.get("error") is None` check over an empty dict is vacuously true, so the summary silently reported "0/0 scanners, 0/0 sims" with zero warnings and dropped the actual reason. Append un-represented `errors` entries to `warnings` on the non-error path (fixed `61667bb`).
- General rule from both fixes: a bundle/import/fit failure anywhere in this pipeline should surface as an explicit warning in the dashboard summary, never silently degrade to a status that hides what actually happened.

(The other classic dashboard-launch failure — a stale shared `.venv` missing a required package — is already covered above under "Which venv, and is it complete": `dashboard.bat`/`tools.bat` pre-check `import fastapi, uvicorn, jinja2, slowapi` so that fails fast with one clear message, not a mid-startup traceback.)

## Output / Audit Trail

Each run writes `orchestrator_output/<run_id>/`: `suite_context_<suite>.json` per launched child, `<suite>_result.json` markers (`vol_result.json`, `options_result.json`, `var_result.json`, `sentiment_result.json`), plus `suite_context.json` (unified runs only) and any suite-specific artifacts (CSVs, PNGs). Every stage and validation verdict also lands in the `orchestrator_runs` SQLite table (`swaps.db`), including `context_audit:sentiment` rows.

## Quick Reference

| Task | Command |
|---|---|
| Interactive | `python orchestrator.py --interactive` |
| Unified run | `python orchestrator.py --unified --ticker NVDA --expiry YYYY-MM-DD` |
| Selectable modules | `python orchestrator.py --modules slug,slug --ticker T` (`--modules-category`, `--all-modules`, `--list-modules`) |
| Single suite | `python orchestrator.py --suite {vol,options,var,sentiment} --ticker T --target-years 0.25` |
| Abort on first bad stage | add `--fail-on-suite-error` |
| Full JSON output | add `--json` |
| Skip validation (debug) | `--no-validate` |
| Run output | `orchestrator_output/<run_id>/` |
