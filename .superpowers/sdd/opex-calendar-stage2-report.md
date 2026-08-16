# OpEx Calendar Stage 2 — Task 1 Binding Report

## Scope

Implemented fixture-backed binding at the Task 1 schedule/probe/manifest boundary. No network acquisition, live/master/expiry-book, dealer model, or Task 4 changes were made.

## Contract changes

- Candidate schedules can be built from an explicitly supplied `CalendarSnapshot` and point-in-time `as_of`; the resolver is called only through that injected snapshot and remains network-free.
- Schedule rows carry the immutable calendar binding, including snapshot/calendar/policy/resolver/binding hashes, as-of, nominal/observed dates, session identity/status, settlement, event/window identity, timezone, and exact DTE.
- PASS probe evidence now requires a complete binding and the execution contract requires byte/structure-equivalent binding equality between the probe and exact candidate schedule key.
- Manifest units project the validated binding; missing, conflicting, unknown-status, wrong-day/timezone, shifted-date, DTE, zero-DTE, and missing-event identity cases fail closed.
- Existing held ticker×day extraction and same-day semantics are unchanged. Legacy schedule calls remain network-free and continue to preserve their keys; binding-enabled calls require the explicit snapshot/as-of contract.

## Tests

Added `Vol_Suite/tests/test_task1_opex_binding.py` with focused network-free fixture tests for schedule projection, missing binding, conflicting identity, wrong day/timezone, and zero-DTE rejection. Existing OpEx core and Task 4 tests remain green.

## Verification

- `python -m pytest Vol_Suite/tests/test_opex_calendar.py Vol_Suite/tests/test_task4_evaluation.py -q`: passed (63 tests, Python 3.11 environment)
- Python 3.12 plugin-isolated collection of the Task 4 module was blocked by the environment's incompatible NumPy cp311 binary; the Stage 2/core slice itself passed under Python 3.12.
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 py -3.12 -m pytest Vol_Suite/tests/test_task1_opex_binding.py Vol_Suite/tests/test_opex_calendar.py -q`: passed (32 tests)
- `python -m py_compile Vol_Suite/dealer_exposure_universe.py Vol_Suite/dealer_exposure_acquisition.py Vol_Suite/dealer_exposure_expansion.py`: passed
- `git diff --check`: passed
- Ruff and plugin-isolated Python 3.12 full verification are reported with the commit result.

## Out of scope

Authorization, Task 4 calendar consumption, source adapters, live acquisition, and expiry-selector compatibility were not implemented.

## Stage 2 review remediation (2026-08-15)

- Calendar-bound acquisition candidates now resolve from an injected validated `CalendarSnapshot`; caller-supplied bindings are evidence only and are rejected unless they exactly match a recomputed OpEx binding.
- OpEx bindings now include and hash `resolver_code_hash`, observed-expiry/session/window aliases, settlement identity, source hashes, exact DTE, and point-in-time `as_of`; `calendar_binding_hash` is recomputed with the shared canonical SHA-256 primitive.
- PASS probe selection requires complete calendar evidence, strict binding equality to the candidate schedule, and validated/invoked markers. Unbound PASS probes cannot bypass the selector, and the heavy executor gate validates every non-held unit before invoking an injected fetcher.
- Artifact manifests and registry entries persist the complete calendar binding and request identity alongside exact expiry/DTE and source/raw hashes.
- Legacy held-pair extraction and dry-run/probe-only paths remain network-free; unbound legacy census rows are never eligible for non-dry-run acquisition.

### Verification

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest Vol_Suite/tests/test_task1_opex_binding.py Vol_Suite/tests/test_opex_calendar.py -q`: **34 passed**.
- `python -m py_compile Vol_Suite/opex_calendar.py Vol_Suite/dealer_exposure_universe.py Vol_Suite/dealer_exposure_acquisition.py Vol_Suite/dealer_exposure_expansion.py`: passed.
- `git diff --check`: passed.
- Ruff was not available on this host (`ruff: command not found`). The pre-existing legacy acquisition/universe tests still encode the pre-calendar contract and fail when run unchanged; the destructive Stage 2 regressions cover the new fail-closed contract.
