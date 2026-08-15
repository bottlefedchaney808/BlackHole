# Task 5 — Universe-expansion execution package

## Status

Implemented and verified a fail-closed, network-free execution package. No ThetaData, market-data, or other network acquisition was run. `dealer_positioning.py`, live configuration, `master`, secrets, and unrelated untracked artifacts were not modified.

## Changes

- `Vol_Suite/dealer_exposure_expansion.py`
  - Composes the Task 3 `run_live_vs_expiry_book_common_input` harness through `compare_expansion_common_input`; canonical payload consumption, engine identities, SVI/deadband/accumulation settings, exact strike/right coverage, exclusions, pairwise levels/units/sign provenance, metrics, and 100% coverage remain owned by the common harness.
  - Adds an execution admission gate requiring balanced event/control schedule, validated PASS probes for every primary unit, two PRE_WINDOW observations per unit, complete canonical Task 1 evidence, verified registry integrity, no-imputation evidence, and exact primary-schedule coverage.
  - Approval is necessary but insufficient. Blocked attempts return an auditable `execution_audit`; executors receive only admitted evidence units and failures are recorded.
  - Preserves dry-run/probe-only behavior, no imputation, strict `THETADATA_HIST_CONCURRENCY=1`, and no implicit network adapter.
- `tests/test_dealer_exposure_expansion.py`
  - Adds regressions for missing probes/evidence, missing PRE_WINDOW observations, incomplete coverage, balance failure, and a valid gated executor call list.
- `.superpowers/sdd/task-5-report.md`
  - Repaired the malformed report tail/newline and appended this review-fix report.

## Verification

Commands run with no plugin autoload:

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q -p no:cacheprovider tests/test_dealer_exposure_expansion.py --disable-warnings
19 passed

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q -p no:cacheprovider tests/test_dealer_exposure_expansion.py tests/test_dealer_exposure_acquisition.py tests/test_dealer_exposure_universe.py --disable-warnings
71 passed in 0.11s

C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m py_compile Vol_Suite/dealer_exposure_expansion.py tests/test_dealer_exposure_expansion.py

C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe -m ruff check Vol_Suite/dealer_exposure_expansion.py tests/test_dealer_exposure_expansion.py

git diff --check
```

Real acquisition was not authorized and was not attempted.

## Commit scope

Only the Task 5 implementation, tests, and report are to be committed. Existing modified `.superpowers/sdd/progress.md`, `.hermes/`, and unrelated untracked acquisition/scratch artifacts remain preserved and unstaged.

## Limitations

The package still requires caller-supplied canonical acquisition evidence and an injected executor. The manifest alone can never authorize network execution or produce headline comparison eligibility.

## Review-blocker closure

- Enforced an exact `deadband=0.01` input contract; live invocation now receives explicit `sign_model=vol_surface_replication`, `accumulate=True`, `route=SVI`, `deadband=0.01`, and `dealer_vanna_flow=1`.
- Live results must attest every requested configuration; missing or mismatched attestation is structured invalid rather than a hardcoded report label.
- Both engine results must attest spot, selected expiry, DTE/T, exact record count, and exact canonical strike/right coverage. Every pair carries OI, IV, spot, T/DTE, per-strike source/config hashes, and resolved sign provenance.
- Structured executor failure returns are classified as auditable `HARD_GAP` / `FAILED_EXECUTION` and cannot produce a successful execution status.
- Added regressions for wrong deadband, missing configuration attestation, omitted result identity/rows, missing per-strike provenance, and structured executor failure.

## Review-blocker closure (2026-08-15)

- `_require_result_identity` now validates every live/new row, not only the container: exact expiry, spot, DTE/T, strike/right, IV, OI, canonical source hash, and non-empty config identity are required; optional canonical `config_hash` attributes are matched exactly.
- Pair metadata is populated from the verified live/new rows for spot, T, DTE, IV, OI, and provenance; it no longer copies canonical values into the output.
- Live config output is the actual validated attestation, including `sign_model`, `accumulate`, route, deadband, vanna-flow, and explicit `attested`; omitted or mutated attestations block comparison.
- Executor admission is now fail-closed: only an explicit `SUCCESS`/`SUCCEEDED`/`PASS`/`OK` mapping with `validated=True` and `success=True` or `ok=True` proceeds. `HARD_GAP`, `FAILED_EXECUTION`, `BLOCKED`, `ERROR`, `FAIL`, false success/ok, missing status, and non-mapping returns are auditable `HARD_GAP`/`FAILED_EXECUTION` with `network_fetch_allowed=False`.
- Added regressions for per-strike identity/IV/OI/source mutations and omissions, config mutations/omitted explicit validation, and every structured executor failure class.

## Verification (review-blocker closure)

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q -p no:cacheprovider tests/test_dealer_exposure_expansion.py tests/test_dealer_exposure_acquisition.py tests/test_dealer_exposure_universe.py --disable-warnings
104 passed in 0.15s

C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m py_compile Vol_Suite/dealer_exposure_expansion.py tests/test_dealer_exposure_expansion.py
C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe -m ruff check Vol_Suite/dealer_exposure_expansion.py tests/test_dealer_exposure_expansion.py
# All checks passed

git diff --check
```

## Review-fix closure (2026-08-15)

- Executor success admission now fails closed on either explicit `success=False` or `ok=False`, regardless of a contradictory success status or validation flag. It also rejects explicit error/failure markers; admission requires an allowed success status, `validated=True`, at least one explicit true success flag, and no failure marker.
- `_execution_gate` now inspects the original evidence-unit list for duplicate `candidate_key` identities before constructing the lookup mapping. Duplicates produce structured blocking reasons and return no admitted units, so evidence cannot be silently overwritten or deduplicated.
- Added regressions for contradictory executor results (`SUCCESS`/`OK` with false flags and error markers), duplicate evidence units, and the valid unique-evidence control.
- No acquisition, live/master, or secrets changes were made.

## Verification (review-fix closure)

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q -p no:cacheprovider tests/test_dealer_exposure_expansion.py tests/test_dealer_exposure_acquisition.py tests/test_dealer_exposure_universe.py --disable-warnings
110 passed in 0.16s

C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m py_compile Vol_Suite/dealer_exposure_expansion.py tests/test_dealer_exposure_expansion.py
# exit 0

C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe -m ruff check Vol_Suite/dealer_exposure_expansion.py tests/test_dealer_exposure_expansion.py
All checks passed!

git diff --check
# exit 0 (Git emitted only LF/CRLF conversion warnings)
```

<!-- report ends with a newline -->

## Review-fix closure (2026-08-15, typed admission and malformed identities)

- Executor admission now requires `status` in the allowed success set, `validated is True`, required `success is True`, and optional `ok is True`; every present boolean gate field must have exact `bool` type. String, integer, `None`, omitted, and contradictory values fail closed, while failure markers remain blocking.
- Evidence candidate identities are validated as non-empty canonical strings and counted in one pass. Duplicate, unhashable, or malformed identities return structured blocking evidence before mapping/coverage operations; no `TypeError` or silent overwrite is possible.
- Added regressions for string false, integer 0/1, `None`, omitted fields, contradictory/invalid `ok`, unhashable identity, and retained valid-control coverage.

## Verification (typed admission and malformed identity closure)

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q -p no:cacheprovider tests/test_dealer_exposure_expansion.py tests/test_dealer_exposure_acquisition.py tests/test_dealer_exposure_universe.py --disable-warnings
120 passed in 0.16s

C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m py_compile Vol_Suite/dealer_exposure_expansion.py tests/test_dealer_exposure_expansion.py
# exit 0

C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe -m ruff check Vol_Suite/dealer_exposure_expansion.py tests/test_dealer_exposure_expansion.py
All checks passed!

git diff --check
# exit 0 (Git emitted only LF/CRLF conversion warnings)
```

No network acquisition, live/master, secrets, or unrelated files were changed.

## Review-fix closure (2026-08-15, structural PRE_WINDOW and canonical config identities)

- Replaced the length-only PRE_WINDOW admission check with structural validation: every observation must be a mapping with explicit `role=PRE_WINDOW`, timezone-qualified timestamp, finite IV, non-empty source identity, valid SHA-256 source hash, and timestamp strictly before the breach/cutoff in the declared timezone and calendar day. Observations must be distinct and ordered; when delta fields are supplied, the registered aggregation is recomputed and must bind the ordered values.
- Added separate canonical live/new configuration identities to `CanonicalInput`, derived from explicit locked configuration fields. Per-strike `config_hash` must match the exact engine-specific canonical hash; arbitrary non-empty labels now block.
- Added regressions for `None`, scalar, malformed, post-cutoff, and arbitrary config evidence without monkeypatch bypass of the structural gate.

## Verification (structural evidence-contract closure)

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q -p no:cacheprovider tests/test_dealer_exposure_expansion.py tests/test_dealer_exposure_acquisition.py tests/test_dealer_exposure_universe.py --disable-warnings
126 passed in 0.14s

C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m py_compile Vol_Suite/dealer_exposure_expansion.py Vol_Suite/run_live_vs_expiry_book_common_input.py tests/test_dealer_exposure_expansion.py
# exit 0

C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe -m ruff check Vol_Suite/dealer_exposure_expansion.py Vol_Suite/run_live_vs_expiry_book_common_input.py tests/test_dealer_exposure_expansion.py
All checks passed!

git diff --check
# exit 0 (Git emitted only LF/CRLF conversion warnings)
```

No acquisition, network, live/master, secrets, or unrelated files were changed.

## Review-fix closure (2026-08-15, timezone validation)

- `_validate_pre_window_observations` now catches `ZoneInfoNotFoundError`, `KeyError`, `TypeError`, `ValueError`, `OverflowError`, and `OSError` from malformed declared timezone and PRE_WINDOW values, returning an auditable reason instead of leaking an exception.
- `_execution_gate` classifies every malformed PRE_WINDOW observation as structured `classification=HARD_GAP` / `status=COMPARISON_INVALID`, preserving fail-closed admission and preventing executor invocation.
- Added real-path regressions for `No/Such timezone`, non-string timezone values, and a valid `America/New_York` control; existing malformed PRE_WINDOW regressions remain covered.
- No acquisition, live/master, secrets, or unrelated files were changed.

## Verification (timezone validation closure)

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q -p no:cacheprovider tests/test_dealer_exposure_expansion.py tests/test_dealer_exposure_acquisition.py tests/test_dealer_exposure_universe.py --disable-warnings
130 passed in 0.16s

C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m py_compile Vol_Suite/dealer_exposure_expansion.py Vol_Suite/run_live_vs_expiry_book_common_input.py tests/test_dealer_exposure_expansion.py
# exit 0

C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe -m ruff check Vol_Suite/dealer_exposure_expansion.py Vol_Suite/run_live_vs_expiry_book_common_input.py tests/test_dealer_exposure_expansion.py
All checks passed!

git diff --check
# exit 0 (Git emitted only LF/CRLF conversion warnings)
```
