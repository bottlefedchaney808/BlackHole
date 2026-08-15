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

## Review-fix closure (2026-08-15)

- Captured mapping now binds every selected IV observation to the producing successful call's endpoint and exact `payload_sha256`; spot and chain timestamps carry the producing endpoint/hash too. The mapped `source_hashes` registry identity is the exact selected-call set, and the existing registry validator checks unit/manifest/registry equality.
- Spot, chain, and IV candidates are filtered strictly before `metrics.breach_window_start_prov` after conversion through the candidate's declared timezone. Latest post-breach rows cannot be selected.
- Successful required spot/chain table payloads and every required IV row are decoded strictly. Malformed mixed rows are HARD_GAPs; absent optional/non-captured rows are not invented.
- Mapping requires explicit raw `metrics.breach_eligible is True` and rejects raw `decision=HARD_GAP`/`status=HARD_GAP`; missing or false eligibility is HARD_GAP and cannot be promoted to PASS. Eligibility/decision are persisted in the unit artifact.

## Review-fix regression coverage

Using the captured XLE artifact (without network or acquisition):

- raw `breach_eligible=false` remains HARD_GAP;
- eligible valid-row mapping preserves exact per-call endpoint/hash bindings;
- post-breach-only selection is rejected and produces no mapped observations;
- malformed mixed IV rows fail closed;
- existing valid strict aggregation, timezone, census, and no-imputation tests remain covered.

## Review-fix verification

- `python -m pytest tests/test_dealer_exposure_acquisition.py -q -p no:plugins` — **36 passed**
- `python -m pytest tests/test_dealer_exposure_acquisition.py tests/test_dealer_exposure_expansion.py -q -p no:plugins` — **114 passed**
- `python -m py_compile Vol_Suite/dealer_exposure_acquisition.py tests/test_dealer_exposure_acquisition.py` — **passed**
- `git diff --check` — **passed**
- Ruff unavailable (`ruff` and `python -m ruff` not installed).

No network call or acquisition was performed. Unrelated worktree artifacts remain untouched.

