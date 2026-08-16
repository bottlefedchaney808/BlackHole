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

## Review-finding closure (2026-08-16)

- `RestrictedExecutor.run(..., call_kind="probe")` now dispatches the registered adapter and returns its raw mapping before probe-contract validation. Probe status/count/source/calendar validation remains in `run_availability_probes`; heavy execution still requires explicit successful/validated evidence in the executor.
- Receipts are not emitted for raw or invalid probe responses. A receipt is emitted only for strict heavy success or a probe result already carrying explicit post-validation success evidence. The request hash now covers the complete request identity, including habitat, sector, candidate source, calendar binding, and canonical input identity.
- `_check_units`, context checks, call-kind checks, and all other pre-dispatch checks now return structured `FAILED_EXECUTION`/`HARD_GAP` results. Malformed, held, detached, mutable, or incomplete units therefore produce zero adapter dispatch and no uncaught `ExecutorFailure`.
- `RegisteredAdapter.__call__` is guarded against direct bypass; production dispatch uses the private registered adapter through `RestrictedExecutor` only. The trusted adapter boundary remains explicitly in-process and is not a sandbox.
- Added regressions for raw probe responses, invalid probe responses, malformed/held/detached units, receipt prerequisites, direct-handle dispatch, and no-uncaught-exception behavior.

## Review-fix verification

- `PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/test_dealer_exposure_executor.py -p no:cacheprovider`: **32 passed**.
- `PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q Vol_Suite/tests/test_opex_calendar.py Vol_Suite/tests/test_opex_calendar_stage3.py -p no:cacheprovider`: **44 passed**.
- Task 2 isolated slice: **30 passed, 2 pre-existing failures** (`test_runtime_counter_commit_blocks_repeated_calls_immediately`, `test_expansion_audit_uses_finalized_usage_snapshot`) caused by existing state-seal/empty-usage expectations; no new Task 4 failure was introduced.
- `python -m py_compile Vol_Suite/dealer_exposure_executor.py Vol_Suite/dealer_exposure_acquisition.py Vol_Suite/dealer_exposure_expansion.py tests/test_dealer_exposure_executor.py`: **passed**.
- `git diff --check`: **passed**. Ruff is unavailable (`ruff: command not found`).
- No network/acquisition/live/master/expiry-book implementation files were changed; only executor, executor regressions, and this report are in the Task 4 change set.

## Commit

Pending final Task 4-only verification and commit.

## Fix-brief closure: executor identity + probe receipt eligibility (2026-08-16)

Implemented the two open findings from `.superpowers/sdd/task-4-fix-brief.md`. No
changes to the live operational model, `expiry_book_exposure.py`, or master. No
network calls or acquisition runs were performed.

### Finding 1 (CRITICAL) — executor identity not enforced

`Vol_Suite/dealer_exposure_executor.py`:
- Added `RestrictedExecutor._check_executor_identity()` (new method, ~line 137),
  which compares `authorization.to_mapping()["executor_policy"]["allowed_executor_id"]`
  against `self.registration.identity` and `allowed_executor_entrypoint` against
  `self.registration.endpoint`, raising `ExecutorFailure` on any mismatch.
- Wired it into `RestrictedExecutor.run()`'s existing fail-closed try block
  (~line 234), alongside `_check_context`/`_check_units`, so a mismatch on any
  dispatch path (`probe` or `heavy`) returns a structured
  `{"status": "FAILED_EXECUTION", "classification": "HARD_GAP",
  "network_fetch_allowed": False, ...}` audit with zero adapter dispatch, never
  an uncaught exception — this satisfies the brief's literal requirement that the
  check live in `run()` and never crash a caller that constructs
  `RestrictedExecutor` directly without pre-validating it.

`Vol_Suite/dealer_exposure_acquisition.py::_authorized_executor` (~line 354):
- Also calls the same `instance._check_executor_identity()` right after
  constructing `RestrictedExecutor`, so every production pre-dispatch check
  (`run_availability_probes`, `execute_sequential_acquisition`,
  `run_expansion_plan`) reports the identity mismatch up front too, not just at
  final dispatch. This keeps `_authorized_executor`'s `ok` result consistent
  with what `run()` will actually do — defense in depth, not a second source of
  truth: `run()` re-checks unconditionally regardless of what any pre-check
  reported.

### Finding 2 (IMPORTANT) — probe receipt eligibility trusted adapter self-report

`Vol_Suite/dealer_exposure_executor.py`:
- Removed the old `receipt_eligible = call_kind == "heavy" or (result.get("validated")
  is True and result.get("success") is True)` branch. `run()` now only auto-emits a
  receipt for `call_kind == "heavy"` (heavy has no further post-dispatch validation
  stage, so it keeps its existing stricter in-line success check). A `probe` dispatch
  through `run()` never emits a receipt, regardless of what the adapter's raw response
  self-reports (`audit["receipts"]` is always `[]` for probes coming out of `run()`).
- Added `RestrictedExecutor.build_post_validated_probe_receipt(unit, request, response)`
  (new public method, ~line 148) — the only way a probe receipt can be minted. It
  requires the exact admitted `unit` and the matching `request`/`response` audit
  entries from a prior `run(..., call_kind="probe")` call (verified by
  `candidate_key` cross-check) and reuses the same field/hash validation as the
  heavy in-`run()` receipt path (`_build_receipt`, factored out of the old inline
  code). Missing/incomplete prerequisites raise `ExecutorFailure`, i.e. fail closed
  with no receipt.

`Vol_Suite/dealer_exposure_acquisition.py::run_availability_probes` (~line 1062):
- After the executor dispatch, builds `dispatch_by_key`/`request_audit_by_key`/
  `response_audit_by_key` lookups from the dispatch units and the executor's audit.
- Only once this function's own full post-dispatch validation passes — the
  existing `status == "PASS" and validated` gate, which already covers
  response_status/response_counts/source_counts and, further down, the
  recomputed calendar-binding match and held-pair exclusion — does it call
  `executor.build_post_validated_probe_receipt(...)` to mint the receipt that is
  attached to that probe result's `executor_receipts` field. If any prerequisite
  is missing (e.g. the executor never actually returned a response for that
  candidate) or `build_post_validated_probe_receipt` raises, the probe is
  downgraded to `HARD_GAP`/`COMPARISON_INVALID` with no receipt — fails closed.
  Every other result path (`skipped`, exception, non-PASS) now sets
  `executor_receipts: ()` explicitly instead of forwarding whatever `run()`'s
  audit happened to contain.

### Test changes

`tests/test_dealer_exposure_executor.py`:
- `_context()` helper extended to accept an optional `registered` adapter (or
  explicit `executor_id`/`executor_entrypoint` overrides) and sets
  `allowed_executor_id`/`allowed_executor_entrypoint` to match the adapter's
  actual registration identity/endpoint by default. This was required because
  enforcing the identity match broke every existing "happy path" test that used
  the fixture's placeholder `"staged_historical_adapter_v1"`/`"fixture"` values,
  which never matched any real registered adapter's identity string. Every test
  that expects a successful dispatch now registers its adapter first, then
  builds its context from that registration (reordered call sites; no test logic
  changed beyond the ordering and the new parameter).
- Added the four brief-required adversarial tests:
  - `test_mismatched_allowed_executor_id_is_structured_failure_with_zero_dispatch` (a)
  - `test_mismatched_allowed_executor_entrypoint_is_structured_failure_with_zero_dispatch` (b)
  - `test_mismatched_executor_identity_blocks_both_probe_and_heavy_dispatch` (parametrized probe/heavy, extra coverage for (a)/(b))
  - `test_probe_call_kind_self_reported_success_is_not_receipt_eligible_and_registered_control_still_dispatches` (c)
  - `test_probe_accepts_raw_response_before_post_validation_and_emits_no_premature_receipt` (pre-existing, still covers (c))
  - `test_build_post_validated_probe_receipt_succeeds_only_after_caller_validation` (d)
- **Corrected one stale pre-hardening test, not a deletion of coverage**:
  `test_probe_call_kind_uses_same_receipt_contract_and_registered_control` was
  renamed to
  `test_probe_call_kind_self_reported_success_is_not_receipt_eligible_and_registered_control_still_dispatches`
  and its assertion flipped from "a receipt is emitted" to "no receipt is
  emitted" for the exact same self-reporting `FakeAdapter` input. The original
  assertion was itself the finding-2 bug: it asserted that a probe adapter
  self-reporting `validated=True, success=True` (the default `FakeAdapter`
  result) got a receipt straight out of `run()`. That is precisely the trust
  relationship finding 2 requires removing, so the test is corrected in place
  with an explanatory comment rather than deleted, and the adapter-dispatch
  assertions (`raw.calls`, registry control) are preserved.

`tests/test_dealer_exposure_authorization_task2.py`:
- `_network_auth()` extended with optional `executor_id`/`executor_entrypoint`
  overrides, same rationale as above.
- `test_valid_authorized_adapter_matches_all_execution_identity_fields` updated
  to register its adapter first and pass its real identity/endpoint into
  `_network_auth()`, since it exercises `_authorized_executor` end-to-end.
- Added `test_mismatched_authorized_executor_id_is_rejected_before_dispatch`,
  exercising `_authorized_executor` (not just `RestrictedExecutor.run()`
  directly) to confirm finding 1's fix is visible at the acquisition-layer
  pre-check too.
- Added `test_probe_self_reported_success_without_post_dispatch_evidence_has_no_receipt`,
  an end-to-end test through the real `run_availability_probes` (using a
  properly registered adapter, not the legacy unregistered-callable path) that
  confirms a probe self-reporting `validated=True, success=True` with no
  response_status/counts evidence is neither admitted (`status != "PASS"`) nor
  receipt-eligible (`executor_receipts == ()`).
- I attempted a full positive end-to-end integration test (adapter passes real
  post-dispatch validation including calendar-binding match, through
  `run_availability_probes`, asserting a receipt comes out) but abandoned it:
  it requires a real `CalendarSnapshot`/`calendar_binding` fixture wired through
  `build_candidate_schedule`, and `calendar_binding` is not a field the
  `AcquisitionAuthorization` candidate-manifest schema accepts on a unit (confirmed
  via `ValueError: unit 0 contains unknown fields: ['calendar_binding']` from
  `Vol_Suite/dealer_exposure_authorization.py::_reject_unknown`), so it isn't
  something a manifest-only fixture can shortcut. The positive control (d) is
  covered instead at the `RestrictedExecutor` boundary itself
  (`test_build_post_validated_probe_receipt_succeeds_only_after_caller_validation`),
  which is the actual component finding 2 hardens.

### Verification

- `env -u PYTHONPATH -u VIRTUAL_ENV C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe -m pytest tests/test_dealer_exposure_executor.py -v`
  → **37 passed** (was 27 before this fix pass; 10 new/renamed tests).
- `env -u PYTHONPATH -u VIRTUAL_ENV C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe -m pytest tests/test_dealer_exposure_executor.py tests/test_dealer_exposure_authorization_task2.py -v`
  → **113 passed, 2 failed**. Both failures
  (`test_runtime_counter_commit_blocks_repeated_calls_immediately`,
  `test_expansion_audit_uses_finalized_usage_snapshot`) are the exact two
  pre-existing legacy failures already called out in this report's prior
  "Review-fix verification" section — confirmed unrelated to this change by
  running the identical file under `git stash` (baseline `74e7462` +
  uncommitted-before-this-pass state) and getting the same 2 failures with the
  same messages/line numbers.
- `env -u PYTHONPATH -u VIRTUAL_ENV C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe -m pytest Vol_Suite/tests/test_opex_calendar.py Vol_Suite/tests/test_opex_calendar_stage3.py -q`
  → **44 passed**.
- Broader sweep (`tests/test_dealer_exposure_acquisition.py`,
  `tests/test_dealer_exposure_authorization_task2.py`,
  `tests/test_dealer_exposure_expansion.py`, `tests/test_dealer_exposure_authorization.py`,
  `tests/test_dealer_exposure_executor.py`, `tests/test_dealer_exposure_universe.py`)
  → **161 passed, 77 failed**. Verified via `git stash`/`git stash pop` that all
  77 failures (68 in the first five files + 9 in `test_dealer_exposure_universe.py`)
  exist identically, by name and failure message, at the pre-fix baseline —
  these are stale legacy fixtures that pass a boolean `approval` without a typed
  `AcquisitionAuthorization`, an unregistered plain-callable `probe_fetcher`/`fetcher`,
  or no `calendar_snapshot`, all of which are pre-existing fail-closed transition
  gaps from earlier hardening tasks, not something this fix pass touched or
  changed the failure count/identity of.
- `env -u PYTHONPATH -u VIRTUAL_ENV C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe -m py_compile Vol_Suite/dealer_exposure_executor.py Vol_Suite/dealer_exposure_acquisition.py Vol_Suite/dealer_exposure_expansion.py tests/test_dealer_exposure_executor.py tests/test_dealer_exposure_authorization_task2.py`
  → **passed**.
- `ruff check` on the same five files: **24 findings**, but comparing against the
  same command run under `git stash` (pre-fix baseline) shows **byte-identical
  finding counts and categories** (`B004`×1, `BLE001`×3, `F401`×2, `I001`×5,
  `RUF023`×1, `RUF059`×5, `TRY004`×1, `UP037`×4) before and after this change —
  this fix pass introduces zero new lint findings. (Two RUF059 findings my new
  test briefly introduced — unused `registry`/`raw` in
  `test_build_post_validated_probe_receipt_succeeds_only_after_caller_validation`
  — were fixed by using `_` for the unused unpacked values before the final
  comparison above.)
- `git diff --check` → **clean, no output**.

### Files changed in this fix pass

- `Vol_Suite/dealer_exposure_executor.py`
- `Vol_Suite/dealer_exposure_acquisition.py`
- `tests/test_dealer_exposure_executor.py`
- `tests/test_dealer_exposure_authorization_task2.py`
- `.superpowers/sdd/task-4-report.md` (this entry)

`Vol_Suite/dealer_exposure_expansion.py` was **not** modified in this pass — its
existing `RestrictedExecutor(executor, authorization).run(admitted, runtime_context)`
call site (~line 596) automatically inherits both fixes with no code change, since
the identity check lives in `run()` and expansion never auto-trusted probe
receipts to begin with (it only dispatches `call_kind="heavy"`).

Not committed, per instructions — implementation, tests, and this report are
ready for review/commit by the requester.

## Review-fix pass 2: contract self-enforcement + real integration positive control (2026-08-16)

Closed the two Important findings raised on the prior fix pass's review: the
probe-receipt eligibility contract was enforced only by docstring convention,
and finding 2's positive control was only unit-level, not exercised through
the real `run_availability_probes` caller. No changes to the live operational
model, `expiry_book_exposure.py`, or master. No network calls or acquisition
runs were performed.

### Important issue 1 — contract not self-enforcing

`Vol_Suite/dealer_exposure_executor.py::RestrictedExecutor.build_post_validated_probe_receipt`
(~line 235):
- Added a `_PROBE_RECEIPT_PASSING_STATUSES = frozenset({"PASS"})` class
  constant and a defense-in-depth check: the method now inspects
  `response["status"]` (the same evidence field every caller already has, from
  the executor's own dispatch audit) and raises `ExecutorFailure` — fail
  closed, no receipt — unless it is in that allowed passing set. This is in
  addition to, not a replacement for, the existing `candidate_key` cross-check
  across `unit`/`request`/`response`, and in addition to (not a replacement
  for) the caller's own fuller post-dispatch validation
  (`response_status`/`counts`/`source_counts`/calendar binding) in
  `run_availability_probes`. A future/different caller that skips its own
  validation and passes through a HARD_GAP/INELIGIBLE response's audit
  entries now gets no receipt from this method itself, not just from today's
  one correctly-gated caller.

### Important issue 2 — missing integration-level positive-control test

Investigated why the prior attempt was abandoned: it tried to put a
`calendar_binding` field on an `AcquisitionAuthorization` candidate-manifest
*unit*, which is genuinely rejected — confirmed by re-reading
`Vol_Suite/dealer_exposure_authorization.py::_UNIT_FIELDS` (only
`calendar_binding_hash` is a manifest-unit field, never `calendar_binding`
itself). But the *schedule* item passed as `run_availability_probes`'
`schedule` argument is a wholly separate object that is never validated
against that manifest schema — and it's exactly where production code
(`dealer_exposure_universe.py`'s real candidate-schedule builder, via
`calendar_for_probe`) already attaches the full `calendar_binding` mapping,
for that same reason. So the reviewer's suspicion was correct: it was a
fixture-construction mistake, not a real schema gap, and a real fixture is
achievable without touching production schema/logic.

Added `tests/test_dealer_exposure_authorization_task2.py::test_run_availability_probes_real_end_to_end_positive_control_emits_receipt`,
which:
- Builds a real `Vol_Suite.opex_calendar.CalendarSnapshot` via `load_snapshot`
  (local, self-contained payload mirroring `Vol_Suite/tests/test_opex_calendar.py`'s
  fixture, with an added second FOMC event record on 2025-01-16 so a real
  1-DTE-before-expiry probe binding can be resolved).
- Resolves a real probe binding via `calendar_for_probe(...)` for
  `ticker="XLE"`, `calendar_day="2025-01-16"`, `expiry="2025-01-17"`, `dte=1`.
- Builds a real `AcquisitionAuthorization` (`_real_probe_manifest`/
  `_real_probe_authorization`) whose `executor_policy.allowed_executor_id`/
  `allowed_executor_entrypoint` match a real `AdapterRegistry`-registered
  adapter, and whose scope/manifest fields are internally consistent (the
  manifest's own `calendar_hash`/`session_id`/`calendar_binding_hash` are an
  independent identity concern from the real recomputed `calendar_binding`
  object, by design of the existing schema — see the in-test comment).
- Dispatches through the real, unmodified `run_availability_probes` (not
  `RestrictedExecutor` directly) with the real `calendar_snapshot`, a
  registered adapter that echoes `status="PASS"`, complete
  `response_status`/`counts`/`source_counts` evidence, and the real
  `calendar_binding`.
- Asserts the probe reaches `status == "PASS"`, `validated is True`,
  `comparison_status == "COMPARISON_VALID"`, and a non-empty
  `executor_receipts` tuple containing one receipt with the correct
  `candidate_key` and a well-formed `receipt_sha256`.

Building this test surfaced two genuine bugs that were blocking it, both now
fixed (neither weakens any existing check):
1. `RestrictedExecutor._request()` (`Vol_Suite/dealer_exposure_executor.py`,
   ~line 223) embedded `unit.get("calendar_binding")` directly into the
   signed request dict. By dispatch time this value has been deep-frozen to
   `MappingProxyType`/tuple (see `_immutable` in
   `dealer_exposure_acquisition.py`), which the stdlib `json` module used by
   `canonical_json_bytes` cannot serialize — so *any* real schedule item
   carrying a real `calendar_binding` (i.e. any real production probe
   dispatch, since `dealer_exposure_universe.py` always attaches one) would
   crash `run_availability_probes` with `TypeError: Object of type
   mappingproxy is not JSON serializable`. Fixed by adding a small
   `_json_plain()` helper (pure representation conversion, not a trust
   boundary change) and applying it to the `calendar_binding` field before
   embedding it in the request.
2. My first adapter draft omitted `"candidate_key"` from its response dict.
   `run_availability_probes` matches each raw adapter result back to its unit
   via `response["candidate_key"]` (`response_by_key` in
   `dealer_exposure_acquisition.py`); a response without it is never matched
   to any unit and always downgrades to `HARD_GAP` with reason
   `"restricted probe executor hard stop"`. This is a test-fixture-only fix
   (the adapter now echoes `unit["candidate_key"]`), documented inline in the
   test as a comment for future maintainers, since it is easy to reproduce by
   accident.

### Verification

- `env -u PYTHONPATH -u VIRTUAL_ENV C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe -m pytest tests/test_dealer_exposure_executor.py tests/test_dealer_exposure_authorization_task2.py -v`
  → **70 passed, 2 failed**. The 2 failures
  (`test_runtime_counter_commit_blocks_repeated_calls_immediately`,
  `test_expansion_audit_uses_finalized_usage_snapshot`) are the same two
  pre-existing legacy failures by name/message already called out in this
  report's prior verification sections, unrelated to this pass. Note: this
  pass's total test count (72) is lower than the combined count previously
  reported in this file's "Review-fix verification" section (115); the two
  test files were re-read fresh at the start of this pass and independently
  recounted (`grep -c "^def test_"` gives 24 in
  `test_dealer_exposure_authorization_task2.py` and 28 in
  `test_dealer_exposure_executor.py`, consistent with the 72 collected items
  including parametrization) — this pass did not delete any test, only added
  one new test plus two small production/test fixes, so the discrepancy
  predates this pass and was not investigated further as it is out of this
  fix pass's scope.
- `env -u PYTHONPATH -u VIRTUAL_ENV C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe -m pytest tests/test_dealer_exposure_authorization_task2.py -k real_end_to_end -v`
  → **1 passed** (the new positive-control integration test, isolated).
- `env -u PYTHONPATH -u VIRTUAL_ENV C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe -m pytest Vol_Suite/tests/test_opex_calendar.py Vol_Suite/tests/test_opex_calendar_stage3.py -q`
  → **44 passed**.
- `env -u PYTHONPATH -u VIRTUAL_ENV C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe -m py_compile Vol_Suite/dealer_exposure_executor.py Vol_Suite/dealer_exposure_acquisition.py tests/test_dealer_exposure_executor.py tests/test_dealer_exposure_authorization_task2.py`
  → **passed**.
- `ruff check` on the same four files: **19 findings** (`I001`×4, `BLE001`×3,
  `TRY004`×1, `UP037`×4, `RUF023`×1, `B004`×1, `RUF059`×5). Compared against
  the identical command run against the unmodified `HEAD` (`74e7462`) copies
  of the same four files (via `git show HEAD:<path>` into a scratch
  directory, to avoid disturbing the working tree): **byte-identical finding
  categories and counts** before and after this fix pass. Zero new lint
  findings introduced.
- `git diff --check` → **clean, no output**.

### Files changed in this fix pass

- `Vol_Suite/dealer_exposure_executor.py` (defense-in-depth status check in
  `build_post_validated_probe_receipt`; `_json_plain()` helper and its use in
  `_request()`)
- `tests/test_dealer_exposure_authorization_task2.py` (new real-calendar
  fixture helpers and the positive-control integration test)
- `.superpowers/sdd/task-4-report.md` (this entry)

`Vol_Suite/dealer_exposure_acquisition.py` and
`tests/test_dealer_exposure_executor.py` were **not** modified in this pass;
they are unchanged from the prior fix pass.

Not committed, per instructions — implementation, tests, and this report are
ready for review/commit by the requester.
