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
- Ruff was not available on this host (`python -m ruff`: module unavailable; `ruff` is not installed). The pre-existing legacy acquisition/universe tests still encode the pre-calendar contract and fail when run unchanged; the destructive Stage 2 regressions cover the new fail-closed contract.

## Stage 2 calendar-binding bypass closure (2026-08-15)

- `select_primary_schedule()` now requires the exact injected `CalendarSnapshot`; it recomputes and validates both probe and schedule bindings against that snapshot before admission. A self-consistent probe-contained binding without a matching snapshot cannot be admitted.
- `run_availability_probes()` now emits `status=HARD_GAP`, `validated=false`, and `comparison_status=COMPARISON_INVALID` when the exact snapshot or complete calendar evidence is absent/mismatched.
- `execute_sequential_acquisition()` threads the snapshot through probes/selection and revalidates the binding immediately before every injected heavy fetch. Missing/forged snapshot-backed identity produces no heavy call and reports `heavy_calls=0`, `network_flag=false`.
- Expansion execution passes the validated snapshot into the selector gate. Held-pair, dry-run, same-day, artifact identity, and strict hash behavior remain unchanged.

### Closure verification

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest Vol_Suite/tests/test_task1_opex_binding.py Vol_Suite/tests/test_opex_calendar.py -q`: **36 passed**.
- The focused regression file now covers forged selector admission, forged heavy-path admission, missing-calendar PASS probes, and a valid snapshot-backed control reaching only an injected executor.
- `python -m py_compile Vol_Suite/opex_calendar.py Vol_Suite/dealer_exposure_universe.py Vol_Suite/dealer_exposure_acquisition.py Vol_Suite/dealer_exposure_expansion.py`: passed.
- `git diff --check`: passed.
- Ruff unavailable: `python -m ruff` reported `No module named ruff`.
- The legacy root acquisition suite was run plugin-isolated; **15 failures** are expected stale pre-calendar-contract assertions (they build unbound schedules and expect heavy admission), while the Stage 2/core slice is green.

## Stage 2 status-integrity closure (2026-08-15)

- `run_availability_probes()` now downgrades a response that claims `PASS` without a complete response status, response-count, and source-count evidence set to `HARD_GAP`, with `validated=false`, `comparison_status=COMPARISON_INVALID`, an auditable `COMPARISON_INVALID: incomplete probe evidence` reason, and network/admission markers false.
- Valid snapshot-bound PASS behavior remains unchanged: complete probe evidence still requires exact calendar-binding validation against the injected `CalendarSnapshot` before selector admission or heavy acquisition.
- Added `tests/test_dealer_exposure_acquisition.py::test_pass_probe_with_incomplete_evidence_is_not_admitted`, covering empty and partial count/evidence responses; the regression passed 3/3 after the fix and was red 3/3 before it.

### Status-integrity verification

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest Vol_Suite/tests/test_task1_opex_binding.py Vol_Suite/tests/test_opex_calendar.py -q`: **36 passed**.
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_dealer_exposure_acquisition.py -q`: **26 passed, 15 pre-existing stale-contract failures**; failures are from unbound legacy schedules expecting heavy admission and are unrelated to this status-integrity change.
- `python -m py_compile Vol_Suite/opex_calendar.py Vol_Suite/dealer_exposure_universe.py Vol_Suite/dealer_exposure_acquisition.py Vol_Suite/dealer_exposure_expansion.py tests/test_dealer_exposure_acquisition.py Vol_Suite/tests/test_task1_opex_binding.py`: passed.
- `git diff --check`: passed.
- Ruff unavailable: `python -m ruff` reported `No module named ruff`.
