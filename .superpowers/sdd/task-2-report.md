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
