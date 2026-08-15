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

## Critical finding fixes (2026-08-15)
- `execute_sequential_acquisition` now computes the validated PASS-only `primary_schedule` from probe evidence first and invokes the injected heavy fetcher only for those keys. INELIGIBLE, HARD_GAP, held exclusions, and missing-probe units are retained in the census without heavy fetch calls.
- `network_heavy_acquisition_executed` is now a local execution flag set immediately before the fetcher call, so a fetcher exception is reported as an attempted/failed execution rather than as `False`. The immutable import sentinel remains untouched and is not used as runtime evidence.
- `output_dir` now has explicit behavior: when supplied, the result is persisted as `dealer_exposure_acquisition.json` and the returned result includes `artifact_path`. The artifact retains probe, schedule, unit, hash, no-imputation, and census provenance fields.
- Added focused regressions for PASS-only fetch-call lists, raising fetchers, and output artifacts. Existing dry-run/probe-only, no-fetcher HARD_GAP, concurrency=1, no-imputation, and provenance gates remain covered.
- Ruff/compile sequencing was rerun as compile first, then Ruff, then pytest, followed by `git diff --check`; Ruff findings in the touched module were cleaned up rather than reported as an unverified pass.

### Fix verification
```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m py_compile Vol_Suite/dealer_exposure_acquisition.py tests/test_dealer_exposure_acquisition.py
exit 0

C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe -m ruff check Vol_Suite/dealer_exposure_acquisition.py tests/test_dealer_exposure_acquisition.py
All checks passed!

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q tests/test_dealer_exposure_acquisition.py tests/test_dealer_exposure_universe.py --disable-warnings
48 passed in 0.10s

git diff --check
exit 0
```

No heavy or live acquisition was executed; no dealer_positioning.py, live config, master, secrets, or unrelated untracked corpora were modified.

## Latest re-review fixes (2026-08-15)
- Ordinary injected `Exception` failures from both the probe adapter and heavy fetcher are now retained as auditable `HARD_GAP` records. Adapter invocation remains `True` once the call is entered; `Exception` is caught intentionally without catching `BaseException` subclasses such as `SystemExit` or `KeyboardInterrupt`.
- Candidate schedules now deduplicate identical contract keys `(calendar_day, ticker, expiry, dte, habitat, sector, candidate_source)` before probe/fetch, selecting duplicate representations deterministically.
- `artifact_path` is inserted into the result before output serialization, so the persisted JSON is self-describing.
- Added regressions for ordinary probe/fetch exceptions, duplicate schedules, and persisted artifact self-reference.

### Latest verification
```text
C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m py_compile Vol_Suite/dealer_exposure_acquisition.py tests/test_dealer_exposure_acquisition.py
exit 0

C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe -m ruff check Vol_Suite/dealer_exposure_acquisition.py tests/test_dealer_exposure_acquisition.py
All checks passed!

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q tests/test_dealer_exposure_acquisition.py tests/test_dealer_exposure_universe.py --disable-warnings
51 passed in 0.09s

git diff --check
exit 0
```

No network acquisition was executed.
