# Task 6 — PRE_WINDOW mapping fix

## Outcome

Implemented the smallest adapter-side mapping fix in `Vol_Suite/dealer_exposure_acquisition.py`.
Captured Theta table responses are now mapped, when their declared timezone is supplied, into the existing strict provenance fields:

- timezone-qualified UTC `spot_timestamp` and `chain_timestamp` from `date`/`ms_of_day`;
- breach timestamp from the captured metrics `breach_window_start_prov`;
- two timestamped IV observations selected strictly before breach;
- exact `iv_source_minus_iv_before` aggregation and version `1`;
- endpoint/request evidence and validated source payload hashes.

Malformed rows, absent timezone, absent endpoint data, or fewer than two usable IV observations remain fail-closed through the existing validator. No timestamps or values are imputed.

The candidate schedule preserves an explicitly supplied `declared_timezone` so the mapping can use the strict timezone contract without inventing a default.

## Regression coverage

Added network-free tests using the captured authoritative fixture `Vol_Suite/_bounded_heavy_acquisition_20260815/raw/2026-07-06_XLE.json`:

- successful timestamp/spot/chain mapping and exact two-observation IV aggregation;
- fail-closed behavior when timezone is missing;
- fail-closed behavior for malformed/missing timestamp rows.

## Verification

- `python -m pytest tests/test_dealer_exposure_acquisition.py -q -p no:plugins` — **33 passed**
- `python -m pytest tests/test_dealer_exposure_acquisition.py tests/test_dealer_exposure_expansion.py -q -p no:plugins` — **111 passed**
- `python -m py_compile Vol_Suite/dealer_exposure_acquisition.py tests/test_dealer_exposure_acquisition.py` — **passed**
- `git diff --check` — **passed**
- Ruff could not be run: neither `ruff` nor `python -m ruff` is installed in this environment.

No acquisition or network call was performed. Existing unrelated worktree changes were not included.

## Commit

Implementation/tests/report-only commit created on branch `Dealer-Exposure-Dev`.

