# OpEx Calendar Module — Task 1 Report

## Status

**PASS — pure, network-free calendar core implemented.** No live model, expiry-book, acquisition, secrets, or master changes were made by this task.

## Deliverables

- `Vol_Suite/opex_calendar.py`
  - Frozen `CalendarSnapshot`, `OpExRecord`, and `EventWindow` contracts.
  - Immutable source/session/holiday/monthly/event record collections represented as frozen snapshot mappings.
  - Strict snapshot canonicalization and SHA-256 identity using `Vol_Suite.provenance_contract.canonical_json_bytes` semantics.
  - America/New_York timezone-qualified timestamp validation, local-day checks, knowledge-cutoff and source-availability checks.
  - Arithmetic-only third-Friday `standard_monthly_candidate`.
  - Pure `resolve_opex`, `resolve_event_window`, and `calendar_for_probe` APIs with snapshot/binding hashes.
  - Fail-closed handling for unknown/conflicting sessions or facts, holiday-closed sessions without an approved shift, early-close records, unknown settlement, wrong DTE, unavailable sources, and unsupported windows/events.
  - No `datetime.now()`, network calls, current/latest fallback, or implicit acquisition.
- `Vol_Suite/tests/test_opex_calendar.py`
  - 16 network-free tests covering arithmetic, immutability/hash binding, permutation stability, exact OpEx/probe resolution, event windows, cutoff/source availability, timezone/local-day validation, holiday/early-close/AM settlement, conflicts, NaN/hash strictness, and DTE.

No fixture files were required; tests use a small in-test synthetic snapshot.

## Verification

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q Vol_Suite/tests/test_opex_calendar.py --disable-warnings
16 passed in 0.06s

python -m py_compile Vol_Suite/opex_calendar.py Vol_Suite/tests/test_opex_calendar.py
exit 0

git diff --check
exit 0
```

Ruff was not installed/available in the environment (`ruff unavailable`). The default pytest plugin environment was intentionally bypassed with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` per repository precedent.

## Commit

Implementation commit: `b60a4fc503cc889342d749d9a31f4da2d189b922` (`feat(vol): add deterministic opex calendar core`).

The report is committed separately as documentation only. Existing unrelated acquisition scratch files and `.superpowers/sdd/progress.md` changes were left untouched and excluded.

## Boundary / follow-up

This task does not integrate the calendar into Task 1 acquisition/probe contracts or Task 4 evaluation. Those remain later gated tasks. Calendar identity is available for those integrations through the pure binding API.

## Review-fix closure (2026-08-15)

- Enforced non-empty, fully described source records (identity, kind, publisher, version, availability, content hash, and selection reason). Monthly, listing, session, holiday, and event facts now require source references; resolution checks only referenced sources against `as_of` and requires listing evidence.
- Standard-monthly classification now requires agreement among arithmetic third-Friday, explicit venue-rule date, and listing date. Settlement timestamps are timezone-qualified and must fall on the observed session's local day. Session IDs and open/close facts are mandatory.
- `PRE_OPEX_SESSION`, `OPEX_DAY`, and `POST_OPEX_RESPONSE` derive distinct validated boundaries from adjacent/session and event facts; unknown or conflicting records fail closed. Event source references and surprise eligibility are validated.
- `calendar_for_probe` now requires `as_of` and binds observed/nominal dates, exact DTE, session facts, settlement, event IDs/windows, timezone, snapshot/calendar/binding hashes, source hashes, and policy/resolver identity. Snapshot records and nested probe bindings are deeply immutable.
- Added regressions for empty/incomplete provenance, fake standard-monthly labels, distinct policy boundaries, invalid settlement local day, incomplete probe binding, and nested mutation attempts.

## Review-fix verification

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q -p no:cacheprovider Vol_Suite/tests/test_opex_calendar.py tests/test_dealer_exposure_acquisition.py --disable-warnings
59 passed in 13.40s

C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m py_compile Vol_Suite/opex_calendar.py Vol_Suite/tests/test_opex_calendar.py
exit 0

C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m ruff check Vol_Suite/opex_calendar.py Vol_Suite/tests/test_opex_calendar.py
All checks passed!

git diff --check
exit 0
```

Only the calendar implementation, its tests, and this report are included in the review-fix commit. Existing acquisition scratch artifacts and progress changes remain untouched.

## Remaining review findings closure (2026-08-15)

- `calendar_for_probe` now resolves the exact requested supported policy (`PRE_OPEX_SESSION`, `OPEX_DAY`, or `POST_OPEX_RESPONSE`) and binds the resolved `window_id`, `window_start`, `window_end`, and `window_policy` at top level and per event.
- Probe binding now invokes `resolve_opex` before construction and invokes `resolve_event_window` for every event row. This prevents probe-only expiry, venue-rule, listing, settlement, session, event-boundary, and source-ref validation from diverging from the strict resolvers.
- Added fail-closed validation for empty required event context, unsupported event types/policies, reversed or session-inconsistent event windows, unknown/invalid surprise statuses and boolean combinations, unavailable or unknown holiday source references, unknown settlement styles, invalid/reversed session facts, missing listing evidence, wrong expiry dates, and duplicate adjacent session dates.
- Adjacent-session ordering uses explicit stable keys and detects duplicate dates before sorting, so conflicting sessions deterministically produce `HARD_GAP` rather than a Python comparison `TypeError`.
- Binding and event-window records remain deeply immutable; canonical snapshot/binding hashes, source hashes, exact local-date DTE, explicit `as_of`, and no-network/no-current-date behavior are preserved.
- Added adversarial and valid-control regressions covering the above cases and policy/hash identity.

### Final verification

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q -p no:cacheprovider Vol_Suite/tests/test_opex_calendar.py tests/test_dealer_exposure_acquisition.py --disable-warnings
66 passed in 14.59s

C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m py_compile Vol_Suite/opex_calendar.py Vol_Suite/tests/test_opex_calendar.py
exit 0

C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m ruff check Vol_Suite/opex_calendar.py Vol_Suite/tests/test_opex_calendar.py
All checks passed!

git diff --check
exit 0
```

Only `Vol_Suite/opex_calendar.py`, `Vol_Suite/tests/test_opex_calendar.py`, and this report are changed for this closure. Unrelated acquisition artifacts and `.superpowers/sdd/progress.md` remain untouched and uncommitted.
