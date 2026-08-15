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

<!-- report ends with a newline -->
