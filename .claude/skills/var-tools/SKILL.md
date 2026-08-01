---
name: var-tools
description: Use when launching, running, or debugging VaR_Tools_Simulations — the correlated-simulation/Monte-Carlo/historical-sim VaR engine — whether interactively via var.bat or headlessly via --context, or when a --module invocation fails or behaves unexpectedly.
---

# VaR Tools Simulations

## Overview

Entry point is `VaR_Tools_Simulations/main.py`, a 10-module interactive CLI (correlated sim, Monte Carlo, historical sim, copula VaR, forex VaR, cash-flow mapping, stress testing, VaR aggregation, price distribution, hedge optimizer). It shares the root `.venv` with `Vol_Suite` and `Options_Suite` — see `../quant-suite-launch-conventions.md` for the venv/port/env-var preflight checks before launching anything below.

## Launch

Interactive (menu-driven, all 10 modules available):

```
VaR_Tools_Simulations\var.bat
VaR_Tools_Simulations\var.bat --module 3    # jumps straight to module 3, still interactive prompts
```

Headless / context mode:

```
VaR_Tools_Simulations\var.bat --context <path\to\suite_context.json> --context-out <path\to\out.json>
```

**Context mode only runs module 1 (corr_sim).** `run_context_mode` (`main.py:313`) hard-codes the corr_sim path — there is no dispatch table for the other nine modules in headless mode. Passing `--module` with any value other than 1 alongside `--context` does not run that module; it returns a `status: "error"` JSON payload with `"notes": "Context mode currently supports only module 1 (corr_sim)."` and exits 1. Omitting `--module` in context mode defaults to module 1 and works normally. Modules 2-10 (Monte Carlo, historical sim, copula, forex, cash-flow mapping, stress test, aggregation, price distribution, hedge optimizer) are interactive-only — reachable only through the menu or `--module N` *without* `--context`.

## Common Mistakes / Debug

Module-select confusion is the most common failure: seeing a clean `status: error` JSON (not a traceback) after `--context ... --module 2` is expected behavior, not a bug — don't chase it as a crash. Fix is to drop `--context`/`--context-out` and run that module interactively instead.

The `--help` text itself is stale — it says "Run module 1-9 directly" but `MODULES` has 10 entries, so `--module 10` (hedge optimizer) is valid despite the help string. Don't let the help text cap what you try.

`--module 0` silently falls through to the interactive menu instead of erroring, because the CLI checks `args.module` truthiness (`0` is falsy) before range-checking — an easy off-by-one trap if scripting module selection.

In context mode, a `status: error` with a message like `"Missing required field 'basket' (or fallback 'ticker')"` means the JSON at `--context` is malformed or incomplete for corr_sim, not a code bug — validate against the shared `suite_context.json` schema (`Vol_Suite/suite_context.py`) first.

## Quick Reference

| Flag | Interactive | Context mode |
|---|---|---|
| (none) | Full menu, modules 1-10 | N/A |
| `--module N` | Runs module N directly (1-10) | Only `N=1` succeeds; anything else returns `status: error`, exit 1 |
| `--context PATH` | N/A | Runs corr_sim from `suite_context.json`, prints JSON to stdout |
| `--context-out PATH` | N/A | Also writes the result JSON to this path |
