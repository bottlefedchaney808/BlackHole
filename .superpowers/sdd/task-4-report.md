# Authorization Hardening Task 4 Report

## Scope

Implemented the Task 4 restricted executor boundary only. No network acquisition, live model, scheduler, master, or expiry-book code was changed. The executor accepts a deeply immutable admitted-unit tuple and the authenticated runtime context minted by the existing authorization/admission path.

## Implementation

- Added `Vol_Suite/dealer_exposure_executor.py`.
  - Requires a typed `AcquisitionAuthorization` and the matching authenticated `_AdmissionContext`.
  - Rejects plain arbitrary functions/methods and incomplete adapter identity.
  - Enforces exact executor ID, entrypoint, endpoint path, HTTP method, and scope binding.
  - Rejects query/fragment/dynamic endpoints and unsafe live/scheduler/out-of-root/new/held permissions.
  - Requires a deeply immutable tuple of units, exact authorized candidate scope, and calendar-bound identity fields.
  - Dispatches through the existing atomic `context.call("heavy", ...)` reservation, preserving units/heavy/total-call, payload-byte, wall-time, and concurrency ceilings with finalized usage.
  - Stops on the first adapter exception, malformed response, unauthorized request, non-success evidence, or ceiling breach.
  - Audits invocations, requests, request hashes, response status, payload hashes, and finalized counters.
  - Returns `FAILED_EXECUTION` / `HARD_GAP` with `network_fetch_allowed: false` for execution failures.
- Modified `Vol_Suite/dealer_exposure_expansion.py` to hand admitted units to `RestrictedExecutor` instead of directly invoking a caller executor. Legacy boolean approval/arbitrary-callable paths remain fail-closed.
- Added `tests/test_dealer_exposure_executor.py` covering immutable handoff, authenticated context, exact endpoint policy, candidate scope, exception hard-stop, cost ceilings, audit hashes, and arbitrary callable rejection.

## Verification

- `pytest -q tests/test_dealer_exposure_executor.py`
  - **10 passed**.
- `python -m py_compile Vol_Suite/dealer_exposure_executor.py Vol_Suite/dealer_exposure_expansion.py tests/test_dealer_exposure_executor.py`
  - **passed**.
- `git diff --check`
  - **passed**.
- `ruff check ...`
  - Not run successfully: `ruff` is not installed/on PATH in this plugin-isolated environment.
- Project `.venv/Scripts/python.exe` (requested Python 3.12 plugin environment) was absent. The available isolated Hermes Python 3.11 runner executed the focused tests successfully.
- The combined legacy authorization/expansion slice was run. Existing pre-Task-4 tests that directly pass unbound manifests, boolean approval, plain lambdas, or stale mutable adapter APIs fail at the new fail-closed transition boundary; these are expected transition failures per the Task 4 brief and were not weakened.

## Files in Task 4 commit

- `Vol_Suite/dealer_exposure_executor.py`
- `Vol_Suite/dealer_exposure_expansion.py`
- `tests/test_dealer_exposure_executor.py`
- `.superpowers/sdd/task-4-report.md`

Unrelated pre-existing worktree artifacts were not staged.

## Review-fix closure (2026-08-15)

- Replaced metadata-only adapter admission with `AdapterRegistry`/opaque `RegisteredAdapter` handles. Registration attests the exact `type(adapter).__call__` identity and marshalled code hash; unregistered callables, functions, methods, and malicious self-declared metadata fail closed. The controlled adapter remains in-process and is not represented as a sandbox against side effects inside trusted code.
- RestrictedExecutor now accepts only the exact sealed `_AdmissionContext` class, verifies ownership, seal, atomic `call` implementation, lock, counters, and ceilings. Duck-typed/fabricated contexts return `FAILED_EXECUTION`/`HARD_GAP` before dispatch.
- Exact family-to-path mapping, method, and scope binding are enforced. Adapter-returned `calls` are not authorization evidence; the executor emits a receipt for each successful dispatch.
- Successful receipts require source hashes, artifact hash, registry key, authorization hash, manifest hash, PRE_WINDOW evidence hashes, request/response hashes, and the registered entrypoint code hash. Missing prerequisites fail closed. Exceptions and ceiling overruns remain hard stops.
- Added adversarial coverage for forged contexts, unregistered callables, metadata mismatch, missing receipt prerequisites, family/path/method/scope mismatches, hash audit, malicious self-reporting, and a valid registered fake adapter.

## Status

Review-fix focused executor tests are green: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/test_dealer_exposure_executor.py` = **17 passed**. The requested combined executor/Task 2/calendar/stage2 slice produced **69 passed, 4 failed**: two Task 2 assertions still expect metadata-only adapters to dispatch, and two calendar tests exercise the pre-existing authorization-required gate without an authorization object. These are fail-closed transition failures, not weakened. `py_compile` and `git diff --check` passed; Ruff is unavailable on PATH.


## Commit

The final Task 4-only commit is the commit carrying this report; its short hash is returned with the task status.
