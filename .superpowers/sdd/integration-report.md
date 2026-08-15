# Dealer-Exposure-Dev Whole-Branch Integration Report

Date: 2026-08-15

## Outcome

- Restored `Vol_Suite/dealer_positioning.py` byte-for-byte (line-ending normalized) to the pre-branch base implementation. No live accumulation override remains.
- Added `Vol_Suite/run_universe_causal_comparison.py`, a network-free injected-adapter runner that validates persisted Task 3 registry/canonical units, requires 100% PRE_WINDOW causal eligibility, groups same-day units, runs the common-input comparison, optionally invokes Task 4 evaluation, and emits auditable verdict statuses.
- Hardened Task 5 `_execution_gate`: PASS probes are independently validated against the complete Task 1 check/evidence schema, request parameters bind to the exact candidate schedule key, detached/duplicate/malformed probes block admission.
- Consolidated Task 4 hash/canonical serialization through `Vol_Suite/provenance_contract.py`.
- Added runner/live immutability integration coverage and updated Task 5 fixtures to use complete probe contracts.

## Verification

- No-plugin Python 3.12 affected suites: **167 passed**.
- Python 3.12 `py_compile`: **passed** for affected modules/tests.
- Ruff: **All checks passed**.
- `git diff --check`: **passed** (only Git LF/CRLF conversion warnings).
- No network acquisition or secrets/master/live production modification was performed.

## Scope

Tracked changes are limited to the runner, integration gate/hash fixes, tests, and this report. Existing `.superpowers/sdd/progress.md`, `.hermes/`, and unrelated untracked acquisition/scratch artifacts were not staged.

## Verdict

The branch integration blockers are closed in the tested offline path. The current persisted acquisition corpus remains subject to its own PRE_WINDOW/causal gate and is not promoted by this integration work.

## Final runner integration follow-up

- Corrected the runner's denominator contract: `intended_unique_day_denominator` is checked against unique calendar days, while ticker×day evidence records remain explicitly clustered under `preserve_ticker_values_v1`.
- The current Task 3 adapter accepts one `CanonicalInput`, so multi-record clustered corpora now emit structured `CAUSAL_BLOCKED` (`TASK3_CLUSTER_ADAPTER`) instead of comparing ticker rows independently with `intended_units=1`.
- The default path imports and invokes `run_task4_evaluation.evaluate_task4` with the validated Task 3 record, provenance, and artifact registry; injected evaluators remain available for tests.
- CLI exit status is fail-closed: only completed `BETTER`/`WORSE` decisions return zero; `COMPARISON_INVALID`, `CAUSAL_BLOCKED`, `INDETERMINATE`, `HARD_GAP`, `FAILED_EXECUTION`, and unknown statuses return nonzero.
- Added integration coverage for two tickers on one day, default Task 4 composition, blocked exit codes, completed exit codes, and the valid runner path.

## Final verification

- Affected no-plugin Python 3.12 tests: **127 passed**.
- Python 3.12 `py_compile`: **passed** for runner/integration tests.
- Ruff: **All checks passed** for runner/integration tests.
- `git diff --check`: **passed** (Git reports only LF/CRLF conversion warnings).
- A broader causal-arm selection exposed 8 pre-existing Python 3.12 environment failures because this interpreter lacks SciPy; those failures are outside the runner diff and were not changed.
