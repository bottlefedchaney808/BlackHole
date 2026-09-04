# The Build

This page is history, not the lesson. It records the commit-level shape of the repo and the artifact discipline that keeps the method reproducible.

## Recent commit themes

- `ad87822` — chore: push-prep — commit working-tree code/docs, ignore run outputs and caches.
- `528ec5c` — fix(tests): unblock full tests/ collection — purge stale flat-module `sys.modules` entries so Options_Suite config resolves.
- `1f24d46` — chore(quant-console): remove quant bridge stack — `quant_bridge`/`worker_broker`/`synthesis`/`interpret` + 6 test modules + `worker_contracts`.

## Artifact discipline

- Every chart is a real artifact.
- Every run writes a `run_id` directory under `orchestrator_output/`.
- Every suite writes a validated `<suite>_result.json`.
- Every run is logged to `orchestrator_runs` in `swaps.db`.

## Where to read more

- For the method: [The Idea](./01-the-idea.md)
- For the command sequence: [Analysis Pipeline Runbook](../guides/FINANCIALDEVELOPMENT_ANALYSIS_PIPELINE_RUNBOOK.md)
- For the full repo inventory: `FINANCIAL_DEVELOPMENT_INVENTORY.md` (kanban attachment from task `t_fd2be247`)
- For the style template: `FINDEV_DEALER_BOOK_DOC_STYLE.md` (kanban attachment from task `t_087b25ad`)

## What this page is not

It is not a changelog. It is a record of the build context a new contributor needs to understand why the repo looks the way it does today.
