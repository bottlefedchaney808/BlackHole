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
- RestrictedExecutor now accepts only the exact privately owned `_AdmissionContext` class, verifies ownership, atomic `call` implementation, lock, counters, and ceilings. Duck-typed/fabricated contexts return `FAILED_EXECUTION`/`HARD_GAP` before dispatch.
- Exact family-to-path mapping, method, and scope binding are enforced. Adapter-returned `calls` are not authorization evidence; the executor emits a receipt for each successful dispatch.
- Successful receipts require source hashes, artifact hash, registry key, authorization hash, manifest hash, PRE_WINDOW evidence hashes, request/response hashes, and the registered entrypoint code hash. Missing prerequisites fail closed. Exceptions and ceiling overruns remain hard stops.
- Added adversarial coverage for forged contexts, unregistered callables, metadata mismatch, missing receipt prerequisites, family/path/method/scope mismatches, hash audit, malicious self-reporting, and a valid registered fake adapter.

## Status

Review-fix focused executor tests are green: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/test_dealer_exposure_executor.py` = **17 passed**. The requested combined executor/Task 2/calendar/stage2 slice produced **69 passed, 4 failed**: two Task 2 assertions still expect metadata-only adapters to dispatch, and two calendar tests exercise the pre-existing authorization-required gate without an authorization object. These are fail-closed transition failures, not weakened. `py_compile` and `git diff --check` passed; Ruff is unavailable on PATH.

## Final trust-boundary closure (2026-08-15)

- Removed the public `_AdmissionContext._SEAL` marker. Context provenance is now recorded in a private module owner registry keyed to object identity; the exact initialized lock, manifest, cost ceilings, usage counters, finalized snapshot, and blocking state are revalidated against the authorization before dispatch. An `object.__new__(_AdmissionContext)` context has no owner entry and hard-fails before `context.call`, producing zero adapter dispatch.
- `_Registration` construction requires a private module token and an `AdapterRegistry` owner. `RegisteredAdapter` handles are recorded in a private owner registry and `RestrictedExecutor` requires both handle membership and exact registration membership in the owning registry. Directly fabricated handles therefore fail during executor construction with zero dispatch.
- Immediately before every adapter dispatch, the executor recomputes the current `type(adapter).__call__.__code__` hash and exact entrypoint identity and compares them with registration. Post-registration callable mutation hard-fails before reservation/dispatch.
- The trusted adapter boundary remains in-process. These controls authenticate the registered boundary and its receipts; they do not claim to sandbox malicious behavior inside a trusted adapter.
- New adversarial/control regressions cover object construction forgery, direct handle fabrication, post-registration entrypoint mutation, and valid registry/context dispatch.

## Final verification

- `PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/test_dealer_exposure_executor.py -p no:cacheprovider` — **21 passed**.
- Task 2/calendar slice: **63 passed, 2 expected legacy transition failures** (both stale tests expect direct metadata-only adapter dispatch).
- The broader executor/Task 2/calendar/expansion slice remains fail-closed with legacy expansion tests that omit required authorization; those failures are not authorization bypasses.
- `py_compile`, Ruff availability, and `git diff --check` status are reported with the final task status.

## Commit

The final Task 4-only commit is the commit carrying this report; its short hash is returned with the task status.

## Trust-boundary bypass closure (2026-08-16)

- Legacy `run_availability_probes` and `execute_sequential_acquisition` now reject every caller-supplied callable unless it is an `AdapterRegistry`-created opaque `RegisteredAdapter`; self-declared executor metadata is no longer inspected or accepted.
- Those paths use the same RestrictedExecutor registration/policy validation and dispatch-time entrypoint code-hash attestation immediately before their atomic runtime-context dispatch. Expansion continues to use RestrictedExecutor directly, so all three paths share the boundary.
- `_AdmissionContext` counters, finalized snapshots, limits, lock, and blocked state are held in module-private state and exposed only as immutable snapshots. Public reset/mutation attempts cannot alter dispatch state; ownership/provenance and authorization consistency are checked before probes or executor dispatch.
- Added/updated regressions cover metadata-only probe/acquisition rejection, immutable counter/limit exposure, forged context rejection, post-registration code mutation, and valid registered control. Trusted adapters remain explicitly in-process and are not claimed to be sandboxed.

## Closure verification (2026-08-16)

- Plugin-isolated Task 4 executor + Task 2 authorization + calendar tests: **97 passed**.
- Stage-2/backtest collection was attempted but blocked by the isolated runner's missing `scipy` dependency; no project `.venv` was present.
- `py_compile` and `git diff --check`: **passed**. Ruff was attempted and is unavailable on PATH (`ruff: command not found`).

## Critical trust-boundary remediation (2026-08-16)

- Refactored probe and sequential-acquisition dispatch to use the same `RestrictedExecutor.run` boundary as expansion. Registered adapter handles are passed into the wrapper; no direct probe/fetcher callable invocation remains after validation. The shared path performs immutable admitted-unit checks, runtime reservation, dispatch-time entrypoint attestation, success validation, request/response/source/artifact/registry/authorization/manifest/PRE_WINDOW receipt construction, audit accounting, and hard-stop handling.
- Extended the shared boundary with an explicit `call_kind` (`probe` or `heavy`) and returned validated adapter results only after receipt creation, preserving the existing expansion path and trusted in-process adapter limitation.
- Replaced unauthenticated module-state continuity with keyed state seals, object/container identity attestation, and append-only seal history. Backing state replacement/reset, counter rollback, lock replacement, limits mutation, and clearing a blocked state after usage now fail before adapter dispatch; this is not a shape-only or `MappingProxyType` control.
- Added regressions for probe receipt parity/registered control and backing-state reset, rollback, lock, limits, and blocked-state tampering. Existing forged-context and valid registered controls remain covered.

## Remediation verification

- `PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/test_dealer_exposure_executor.py -p no:cacheprovider`: **27 passed**.
- `python -m py_compile Vol_Suite/dealer_exposure_acquisition.py Vol_Suite/dealer_exposure_executor.py Vol_Suite/dealer_exposure_expansion.py tests/test_dealer_exposure_executor.py`: **passed**.
- `git diff --check`: **passed**.
- Ruff: unavailable on PATH (`ruff: command not found`).
- The combined legacy Task 2/calendar/expansion collection remains fail-closed with pre-existing transition tests that directly supply unbound lambdas or omit the required typed authorization; these are expected legacy failures under the frozen authorization boundary and were not weakened.
