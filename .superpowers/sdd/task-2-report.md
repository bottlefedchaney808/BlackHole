# Task 2 Report — Probe and schedule without acquisition

## Status
Implemented and verified. The module is network-capable through an injected fetcher, but default dry-run/probe-only operation performs no network-heavy acquisition. No live acquisition was executed.

## Files
- `Vol_Suite/dealer_exposure_acquisition.py`
  - deterministic `(calendar_day, ticker, expiry, dte, habitat, sector, candidate_source)` schedule and contract key
  - held-pair/day reference integration using held manifests and seed paths
  - sequential ticker-by-day entrypoint with fail-closed `THETADATA_HIST_CONCURRENCY=1`
  - explicit approval gate; dry-run/probe-only path does not invoke fetcher
  - PRE_WINDOW validation from block-7b fields (`delta_iv_provenance`, `delta_iv_pre_window`, source/breach timestamps)
  - no-imputation artifacts, raw payload/artifact SHA-256 hashes
  - PASS / INELIGIBLE / HARD_GAP / ASSOCIATIONAL per-unit status
  - provenance census with 100% PRE_WINDOW gate and optional fail-loud behavior
  - deterministic same-day ticker clustering
- `tests/test_dealer_exposure_acquisition.py`
  - network-free tests for scheduling, held exclusions, ordering, approval/concurrency gates, dry-run behavior, provenance census, fail-loud behavior, clustering, hashes, and no-imputation semantics
- `.superpowers/sdd/task-2-report.md`

## Tests / output
Command (repository Python 3.12 environment):

```text
C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe -m pytest -q tests/test_dealer_exposure_acquisition.py tests/test_dealer_exposure_universe.py
41 passed in 0.10s
```

Lint:

```text
C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe -m ruff check Vol_Suite/dealer_exposure_acquisition.py tests/test_dealer_exposure_acquisition.py
All checks passed!
```

## Concerns / boundaries
- No ThetaData endpoint calls were made. Actual network use remains an explicit later approval decision and requires a caller-supplied fetcher.
- Existing live model/config files were not modified. Existing unrelated acquisition corpora and worktree artifacts were left untouched.
- The module records raw payload hashes in memory/result artifacts; persistence to an output directory is intentionally not automatic in dry-run mode.
- Task 1 contracts remain unchanged.

## Reviewer fix report (2026-08-15)
- Replaced lexicographic PRE_WINDOW timestamp comparison with strict ISO-8601 parsing, UTC normalization, strict source-before-breach comparison, and calendar-day consistency checks. Malformed values, equality, later timestamps, and cross-day windows downgrade to non-PASS.
- Acquisition without an injected heavy fetcher now remains a non-executed `HARD_GAP`; the execution flag is set only after the injected fetcher is actually invoked.
- Added sequential lightweight availability-probe orchestration with deterministic request parameters, response status/counts, source counts, probe code version/hash, invocation/validation fields, and PASS-only primary schedule selection. Dry-run/probe-only paths never invoke probes or heavy fetchers.
- Added explicit `generated_at` injection support for deterministic artifacts, removed the mutable recursive visitor default, and narrowed injected-adapter exception handling.
- Added regression coverage for malformed/equal/later/cross-day/timezone timestamps, no-fetcher acquisition, probe schema, and PASS-only schedule selection.

### Fix verification
```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q tests/test_dealer_exposure_acquisition.py tests/test_dealer_exposure_universe.py --disable-warnings
45 passed in 0.08s

C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m py_compile Vol_Suite/dealer_exposure_acquisition.py tests/test_dealer_exposure_acquisition.py
exit 0

git diff --check
exit 0
```

No acquisition was executed; live model/config and unrelated untracked corpora were not modified.
