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
