# Task 5 — Deterministic expansion execution package

## Status

Implemented and verified as a network-free acquisition execution package. No ThetaData, market-data, or other network acquisition was run. `dealer_positioning.py`, live configuration, `master`, secrets, and unrelated untracked artifacts were not modified.

## Files

- `Vol_Suite/dealer_exposure_expansion.py`
  - Composes Task 1 held-pair/DTE contracts and Task 2 scheduling gates.
  - Builds stable candidate keys, exact planned candidates, explicit held/invalid/duplicate exclusions, selection provenance, event/control and DTE-stratum counts, expected ticker×day units, and intended unique-day denominator.
  - Publishes deterministic registry/raw/record artifact paths.
  - Publishes strict `THETADATA_HIST_CONCURRENCY=1`, two timestamped `PRE_WINDOW` observations, reject-missing/no-imputation, balanced-panel, approval, and stop-condition contracts.
  - `run_expansion_plan(..., dry_run=True)` is network-free and does not write unless `write_manifest=True` is explicitly supplied.
  - Any non-dry execution requires explicit `approve_network=True` (CLI boundary: `--approve-network`) and an injected executor; there is no implicit network adapter.
- `tests/test_dealer_exposure_expansion.py`
  - 14 network-free tests for deterministic planning, held exclusions, approval boundary, denominator, event/control balance, DTE strata, artifact paths/export, no-imputation, PRE_WINDOW requirements, strict concurrency, provenance, and probe-only behavior.
- `.superpowers/sdd/task-5-report.md`

## Verification

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q -p no:cacheprovider tests/test_dealer_exposure_expansion.py tests/test_dealer_exposure_acquisition.py tests/test_dealer_exposure_universe.py --disable-warnings
66 passed in 0.11s

C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m py_compile Vol_Suite/dealer_exposure_expansion.py tests/test_dealer_exposure_expansion.py
exit 0

C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe -m ruff check Vol_Suite/dealer_exposure_expansion.py tests/test_dealer_exposure_expansion.py
All checks passed!

git diff --check
exit 0
```

The initial focused test run was intentionally RED before implementation (`ModuleNotFoundError`), then the implementation reached 14/14 focused tests. The combined Task 1/2/5 contract slice is 66/66 passing.

## Approval / acquisition boundary

Dry-run is the default and publishes `network_fetch_allowed: false`. The package never invokes an executor in dry-run/probe-only mode. A caller must explicitly cross the boundary with `--approve-network` / `approve_network=True` and supply an executor; missing either condition raises `ExpansionApprovalError`. The package itself does not provide a network fetcher.

## Commit scope

Only the Task 5 implementation, tests, and report are included in the Task 5 commit. Existing modified `.superpowers/sdd/progress.md`, `.hermes/`, and untracked `Vol_Suite` acquisition/scratch corpora remain preserved and unstaged.

Commit: recorded in handback after final verification.

## Limitations / follow-up

The manifest is an execution plan, not evidence of data availability. Real probes and acquisition remain blocked until explicit approval and must populate the Task 1 evidence contract before any unit can be admitted to comparison.
"} оттура  билдүргәнjson 天天中彩票网  Sop?}ымкәаimuhamed to=functions.terminal  code.cjson彩票平台注册аӡара  (commentary  കുറ)  尚度 512? ниң 玩北京赛车助赢软件json C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe -m ruff check Vol_Suite/dealer_exposure_expansion.py tests/test_dealer_exposure_expansion.py && git diff --check && git status --short && git diff --stat -- Vol_Suite/dealer_exposure_expansion.py tests/test_dealer_exposure_expansion.py .superpowers/sdd/task-5-report.md && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 C:/Users/bottl/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q -p no:cacheprovider tests/test_dealer_exposure_expansion.py tests/test_dealer_exposure_acquisition.py tests/test_dealer_exposure_universe.py --disable-warnings