# Authorization Hardening Task 2 Report

## Status

Implemented the atomic acquisition admission boundary. The implementation is network-free until an explicitly injected executor is reached, and boolean `approval`/`approve_network` is no longer accepted as authorization for non-dry-run entry points.

## Changes

- Added `admit_acquisition(authorization, manifest, probes, evidence, registry)` in `Vol_Suite/dealer_exposure_acquisition.py`.
- Admission is fail-closed and ordered as:
  1. calendar-enriched manifest projection;
  2. exact scope, manifest hash, quota, cost, and serial-concurrency checks;
  3. complete probe identity/status/validation/invocation/check checks;
  4. evidence and registry/source/artifact closure;
  5. authorization self-hash, scope, expiry, and restricted permissions;
  6. explicit restricted-executor handoff.
- Admission rejects duplicate/unknown/missing probes and evidence units, detached calendar/source/artifact identities, held or unsafe permissions, imputation, incomplete PRE_WINDOW evidence, and boolean approval without an authorization object.
- Returned admitted units and the audit record are immutable nested mappings/tuples.
- Routed expansion and acquisition execution entry points through the admission boundary; dry-run/probe-only remains network-free.
- Added fake-handoff tests for boolean approval rejection and a valid control with exact admitted key list and immutability.

## Verification

- `python -m py_compile Vol_Suite/dealer_exposure_acquisition.py Vol_Suite/dealer_exposure_expansion.py tests/test_dealer_exposure_acquisition.py` — passed.
- `python -m pytest -q tests/test_dealer_exposure_authorization.py tests/test_dealer_exposure_acquisition.py::test_boolean_approval_without_authorization_cannot_handoff tests/test_dealer_exposure_acquisition.py::test_admission_valid_control_returns_immutable_units_and_exact_keys` — **23 passed**.
- `git diff --check` — passed.
- Ruff was not installed (`ruff: command not found`).
- Repository `.venv/Scripts/python.exe` (plugin-isolated Python 3.12) was not present; available interpreter was Python 3.11. The full legacy acquisition/expansion suites still contain pre-Task-2 calls using boolean approval and therefore report expected contract-transition failures; those calls were not used to validate the new admission control.

## Scope

Only Task 2 implementation/tests/report should be committed. Existing unrelated `.superpowers/sdd/progress.md` and untracked acquisition artifacts remain untouched.

## Authorization Hardening Task 2 re-review fixes (2026-08-15)

- Replaced role-only PRE_WINDOW acceptance with strict exactly-two observation validation: timezone-qualified ordered/distinct timestamps, both strictly before the breach on the declared local day, finite IVs, non-empty source identities, registry-present SHA-256 source hashes, explicit no-imputation, exact `iv_source_minus_iv_before` delta, and aggregation version `1`.
- Probe admission now requires exact snapshot/calendar/policy/resolver/session/settlement/window/binding/source identities, `as_of`, candidate request identity, and authorization probe-code hash; detached or forged identities are hard gaps.
- Admission requires all usage counters (`units`, probe/heavy/total endpoint calls, payload bytes, wall seconds, concurrency); absent counters are not zero and every configured ceiling is enforced.
- Registry closure now binds source hashes, calendar/request/raw-payload identities, artifact hash/manifest, and evidence, rejecting shallow or cross-unit reuse.
- `run_expansion_plan` compares the constructed manifest projection and canonical hash against authorization before calling admission or any executor.
- Added adversarial Task 2 tests for incomplete PRE_WINDOW, forged probe identity, each missing cost counter, shallow registry, manifest mismatch, and a valid fully-evidenced PRE_WINDOW control.

## Re-review verification

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/test_dealer_exposure_authorization.py tests/test_dealer_exposure_authorization_task2.py tests/test_dealer_exposure_acquisition.py::test_boolean_approval_without_authorization_cannot_handoff tests/test_dealer_exposure_acquisition.py::test_output_dir_writes_auditable_artifact` — passed (33 tests).
- `python -m py_compile Vol_Suite/dealer_exposure_authorization.py Vol_Suite/dealer_exposure_acquisition.py Vol_Suite/dealer_exposure_expansion.py` — passed.
- `git diff --check` — passed.
- Ruff availability and the full legacy suite remain environment/contract-transition constrained; no network acquisition was executed.

## Authorization Hardening Task 2 execution-boundary closure (2026-08-15)

- Moved non-dry-run acquisition behind calendar-enriched authorization preflight; missing/forged authorization, evidence, or registry stops before `run_availability_probes`, with no injected probe call.
- `run_availability_probes` now requires the opaque preflight authorization context for network-capable operation; `approval=True` alone is a hard gap and invokes zero calls.
- Added an atomic runtime accounting context around probe, heavy, and executor calls. It records probe/heavy/total calls, payload bytes, wall seconds, and concurrency; ceilings stop execution at the first overrun and report a hard gap.
- Expansion executor handoff now requires a named adapter with authorized executor ID, entrypoint, endpoint/path, and method; arbitrary caller callables and wrong adapters are rejected and identity is recorded.
- Removed malformed annotation tokens and added regressions for boolean bypass, zero-call failed probe admission, runtime ceilings, and authorization-bound execution.

## Closure verification

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/test_dealer_exposure_authorization.py tests/test_dealer_exposure_authorization_task2.py` — **38 passed**.
- `python -m py_compile Vol_Suite/dealer_exposure_authorization.py Vol_Suite/dealer_exposure_acquisition.py Vol_Suite/dealer_exposure_expansion.py` — passed.
- `git diff --check` — passed.
- Ruff was unavailable in the environment; no live/network acquisition was run.

## Authorization Hardening Task 2 execution-boundary re-closure (2026-08-15)

- `_AdmissionContext.call()` now atomically commits projected units plus probe/heavy/total endpoint reservations before dispatch; failed or repeated adapters cannot bypass a ceiling. Payload and wall-time accounting remains fail-closed after the call.
- Added zero-unit and repeated-call regressions proving immediate stop and no second adapter invocation.
- `run_expansion_plan()` now requires `approve_network is True`; false or omitted approval returns a blocked audit with zero executor invocations before admission/executor dispatch.
- Consolidated the strict named-executor validator in acquisition and reused it from expansion. Arbitrary callables, wrong executor ID/entrypoint, non-enumerated endpoint paths, methods, or `scope_binding` are rejected; exact executor identity and scope binding are recorded in acquisition/expansion audits.
- Authorization validation now requires non-empty executor identity, endpoint/method enumerations, absolute exact paths, and explicit scope binding. Endpoint-family strings are descriptive only and never authorize substring matches.
- Added adversarial regressions for approval false, arbitrary fetchers, wrong scope/path/method, missing scope binding, and a valid fully identified adapter.

## Re-closure verification

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/test_dealer_exposure_authorization_task2.py` — **26 passed**.
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/test_dealer_exposure_authorization.py tests/test_dealer_exposure_authorization_task2.py` — **47 passed**.
- Full legacy acquisition/expansion suites remain contract-transition failures because they intentionally call non-dry-run entry points with boolean approval/arbitrary lambdas and no typed authorization; these failures confirm the new boundary is enforced.
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q Vol_Suite/tests/test_opex_calendar.py Vol_Suite/tests/test_opex_calendar_stage3.py` — **44 passed**.
- `Backtests/tests/test_core.py` collection was blocked by the environment because `scipy` is not installed; no code failure was inferred from that dependency error.
- `python -m py_compile Vol_Suite/dealer_exposure_authorization.py Vol_Suite/dealer_exposure_acquisition.py Vol_Suite/dealer_exposure_expansion.py tests/test_dealer_exposure_authorization_task2.py` — passed.
- `git diff --check` — passed; Ruff unavailable.
- No network/live/master/expiry-book acquisition was run.

## Authorization Hardening Task 2 execution-boundary final closure (2026-08-15)

- `_AdmissionContext.call()` now reserves units/call counters and `concurrency=1` atomically before dispatch, rejects a contending second thread without poisoning a live context, releases the slot in `finally`, and returns the finalized payload/wall-time usage snapshot after accounting.
- `run_availability_probes()` now applies the shared strict `_authorized_executor()` validator before any probe dispatch; arbitrary callables are rejected with zero calls and exact executor ID/entrypoint/path/method/scope binding are required.
- Acquisition and probe entry points require `approval is True`; integer, string, list, mapping, and other truthy values are rejected.
- Expansion audit runtime usage is covered by a regression proving the stored snapshot includes finalized payload accounting.
- Added regressions for two-thread concurrency, arbitrary probe executors, non-boolean approval, and finalized expansion usage.

## Final closure verification

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/test_dealer_exposure_authorization.py tests/test_dealer_exposure_authorization_task2.py` — **54 passed**.
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q Vol_Suite/tests/test_opex_calendar.py Vol_Suite/tests/test_opex_calendar_stage3.py` — **44 passed**.
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/test_dealer_exposure_authorization_task2.py` — **33 passed**.
- `python -m py_compile Vol_Suite/dealer_exposure_authorization.py Vol_Suite/dealer_exposure_acquisition.py Vol_Suite/dealer_exposure_expansion.py tests/test_dealer_exposure_authorization_task2.py` — passed.
- `git diff --check` — passed.
- Ruff unavailable (`ruff: command not found`).
- Legacy acquisition/expansion tests were run and remain expected contract-transition failures because they invoke non-dry-run paths with boolean approval/arbitrary lambdas and no typed authorization; no network/live/master/expiry-book acquisition was run.
