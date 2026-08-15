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
