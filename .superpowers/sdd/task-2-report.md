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
