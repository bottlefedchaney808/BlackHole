# OpEx Calendar Stage 3 — Task 3 registry and Task 4 consumption

## Status

Implemented Stage 3 on `Dealer-Exposure-Dev`. The Task 3 artifact/provenance boundary now carries flattened and nested calendar identity, and Task 4 consumes only supplied, registry-bound calendar-enriched records. Task 4 does not import or call the OpEx resolver.

## Changes

- `Vol_Suite/dealer_exposure_acquisition.py`
  - Persists calendar fields into Task 3 artifacts/manifests and duplicated registry entries.
  - Carries snapshot/source/policy/resolver/as-of/binding hashes, event/window identity, nominal/observed expiry, session/open/close/early-close, settlement, and timezone fields.
  - Missing calendar binding produces an auditable `HARD_GAP` rather than an unbound artifact.
- `Vol_Suite/run_live_vs_expiry_book_common_input.py`
  - Extends strict provenance requirements and registry cross-field validation with calendar identity.
- `Vol_Suite/opex_calendar.py`
  - Event-window payloads now retain `event_type` for deterministic downstream unions.
- `Vol_Suite/run_task4_evaluation.py`
  - Validates supplied calendar binding, canonical binding hash, snapshot/calendar/source/policy/resolver identity, event/session/window fields, and registry attachment before fitting.
  - Same-day collapse preserves sorted nested per-ticker metadata and deterministically unions event IDs/types with an explicit overlap label.
  - Existing all-eligible/event/control/pooled/no-firing strata, fixed clocks, CI/power/falsifier gates, and no-promotion ladder remain intact.
- Tests
  - Updated Task 4 fixtures to use complete hash-bound calendar records.
  - Added `Vol_Suite/tests/test_opex_calendar_stage3.py` covering missing/forged/mutated registry evidence, binding/source hash invalidation, same-day overlap union, nested metadata, early-close/shift mutation rejection, no-firing retention, and resolver non-invocation.

## Verification

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH= C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q -p no:cacheprovider Vol_Suite/tests/test_task4_evaluation.py Vol_Suite/tests/test_opex_calendar_stage3.py Vol_Suite/tests/test_opex_calendar.py`
  - **72 passed**
- Python 3.12 `py_compile` on all touched implementation/tests
  - passed
- Python 3.12 Ruff on all touched implementation/tests
  - **All checks passed**
- `git diff --check`
  - passed; only normal Windows LF-to-CRLF warnings

## Scope and concerns

No network/acquisition endpoint was used by Stage 3 tests. No live/master/expiry-book/model files were changed. Existing legacy acquisition/common-input tests that construct unbound schedules/provenance are expected contract-transition failures under the mandatory calendar requirement; they were not weakened.

Pre-existing `.superpowers/sdd/progress.md` modification and untracked acquisition/corpus artifacts were preserved and excluded from the Stage 3 commit.

## Commit

`bbd20ae51364ae30786c02b4a9ec472d26753e27` (`feat(vol): implement opex calendar stage3 consumption`).

## Stage 3 review remediation (2026-08-15)

- `_validate_calendar_metadata()` now requires calendar binding `ticker`, `calendar_day`, `expiry`, and `dte`, and compares each exactly to the evaluation row or supplied canonical-input identity before fitting. A fully rehashed binding from another ticker/day/expiry/DTE now hard-gaps even when registry, artifact, and binding hashes are internally consistent.
- Every nested `event_windows` entry is now required to carry event/window identity and is checked against top-level event metadata. Nested bounds and policy must agree with the top-level selected window; selected-window identity is checked explicitly. Optional event ID/type/source/status metadata is checked when supplied.
- Added regressions for fully rehashed cross-ticker/cross-day/expiry/DTE detachment and rehashed nested-window bounds/policy inconsistency. Existing same-day nested metadata, event unions, no-firing/strata/clocks/CI/power/falsifier/no-promotion behavior, and resolver non-invocation remain covered.

### Remediation verification

- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH= C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q -p no:cacheprovider Vol_Suite/tests/test_opex_calendar_stage3.py Vol_Suite/tests/test_task4_evaluation.py Vol_Suite/tests/test_opex_calendar.py`: **74 passed**.
- The broader Stage 3/core/acquisition-adjacent slice produced **172 passed, 7 pre-existing legacy contract-transition failures** in `test_common_input_live_vs_expiry_book.py`; those failures are outside Stage 3 and were not weakened.
