# Workflows

These are the recurring ways the repo is used. Each is written as a trigger, inputs, stages, outputs, and a "good means" check.

## Morning scan

**Trigger**: you want a quick cross-suite read on one ticker before the open or after a macro move.

**Inputs**:
- Focus ticker (e.g. `SPY`, `NVDA`)
- Expiry or target horizon (e.g. `--expiry 2026-10-16` or `--target-years 0.25`)
- ThetaData credentials in root `.env`

**Stages**:
1. `orchestrator.bat --unified --ticker <TICKER> --expiry <YYYY-MM-DD>`
2. Wait for the orchestrator summary.
3. Inspect `vol_result.json`, `options_result.json`, `var_result.json`, `sentiment_result.json` in `orchestrator_output/<run_id>/`.

**Outputs**:
- Vol surface and dealer positioning
- Option price/Greeks
- VaR/CVaR
- Scanner metrics and Direction conviction

**Good means**:
- `vol_result.json` has `"status": "ok"` and a non-empty `produced_files` list.
- `options_result.json` has numeric `price`, `sigma`, and a `greeks` block.
- `var_result.json` has numeric `var` and `cvar`.
- Failures are named, not quiet.

## Dealer-book load

**Trigger**: you opened the dashboard and selected a ticker/expiry on the dealer-book tab.

**Stages**:
1. Dashboard form posts to `/run/unified` with `ticker` and optional `expiration_date`.
2. Orchestrator runs the pipeline.
3. Dashboard polls `GET /runs/{run_id}` until `done` is true.
4. Dealer-book tab loads modules via `ModuleResult.get` on the returned context.

**Outputs**:
- `run_id` for tracking
- Aggregated dealer-book panels

**Good means**:
- The `run_id` returns `done: true` within the suite timeout.
- `ModuleResult.get` returns real values; legacy callers that treated results as dicts still work.

## Unified run from the dashboard

**Trigger**: you want to trigger a full run from the UI.

**Stages**:
1. Start the dashboard: `dashboard.bat`.
2. Open `http://127.0.0.1:8787`.
3. Use the Quant Console cards or POST to `/run/unified` with JSON body `{ "ticker": "NVDA", "expiration_date": "2026-10-16" }`.
4. Poll `GET /runs/{run_id}` until `done`.

**Outputs**:
- `run_id` and final orchestrator summary
- All `*_result.json` artifacts in the run directory

**Good means**:
- Only one dashboard instance is running.
- The run does not overlap with a `run_scheduler.bat` database-lock race.

## Single-suite run

**Trigger**: you only need one kind of output.

**Stages**:
```bash
orchestrator.bat --suite vol --ticker AAPL --target-years 0.25
```

Valid suite names: `vol`, `options`, `var`, `sentiment`.

**Good means**:
- The suite's `<suite>_result.json` validates.
- Single-name runs may intentionally lack a correlation matrix; set `SUITE_VALIDATION_STRICT=0` if the run is expected to be single-name.

## Modular run

**Trigger**: you want additive modules, not the full unified path.

**Stages**:
```bash
orchestrator.bat --modules dealer_exposure,dealer_flow --ticker SPY --target-years 0.25
```

List every module:
```bash
orchestrator.bat --list-modules
```

**Good means**:
- Every slug resolves via `shared.module_registry.resolve_modules`.
- Dependencies declared in `ModuleSpec.requires` are respected by the runner.

## Archive / rerun

**Trigger**: you want to compare today's run to a prior run.

**Stages**:
1. Note the `run_id` from `orchestrator_output/`.
2. Re-run with the same ticker and expiry.
3. Compare `*_result.json` files side by side.

**Good means**:
- The two runs used the same sign convention and expiry.
- Any divergence in outputs can be tied to a change in inputs, not a change in method.

## What this page is not

It is not a tutorial that narrates every click. It assumes the reader has read [The Idea](./01-the-idea.md) and [The Engine](./04-the-engine.md).

Next: the knobs that control each workflow.
