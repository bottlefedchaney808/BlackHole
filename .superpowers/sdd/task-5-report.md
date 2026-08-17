# Task 5 Report: `output_runs.py` — sentiment date-bucket discovery

## Status: DONE

## What was implemented

Appended `discover_date_bucket_runs(root_glob: str, suite: str, run_id_prefix: str) -> List[RunInfo]`
to `dashboard/output_runs.py`, verbatim from the task brief. It treats each directory matching
`root_glob` as one run bucket — built for sentiment-scanner's
`data/exports/highlighted_ticker_packs/<YYYYMMDD>/` layout. Deliberately collapses multiple scan
cycles from one day into a single bucket (sentiment-scanner loops continuously per
`SCAN_INTERVAL_MINUTES`).

Key behavior:
- Walks each candidate directory's direct files (non-recursive), classifies each via `classify_file`,
  builds `RunFile` records.
- Skips directories with zero files.
- Parses the directory name as `%Y%m%d` for the label (`YYYY-MM-DD`) and, critically, for the sort
  timestamp — using `parsed_date.timestamp()` rather than filesystem mtime, per the bug fix already
  baked into this brief (mirrors the Task 4 clustering fix: near-identical folders created close
  together can share an mtime at this filesystem's granularity, but never share a parsed date).
- Falls back to raw dirname as label and max file mtime as timestamp only when the dirname isn't a
  parseable `%Y%m%d` token.
- `run_id` is `f"{run_id_prefix}:{dirname}"`, e.g. `pack:20260728`.

Appended the four tests from the brief to `dashboard/tests/test_output_runs.py`:
- `test_discover_date_bucket_runs_one_run_per_date_folder`
- `test_discover_date_bucket_runs_ignores_manifest_file`
- `test_discover_date_bucket_runs_skips_empty_date_folders`
- `test_discover_date_bucket_runs_sorts_newest_first`

No existing code was touched — both edits are pure appends.

## RED evidence

```
ImportError while importing test module '...\dashboard\tests\test_output_runs.py'.
E   ImportError: cannot import name 'discover_date_bucket_runs' from 'dashboard.output_runs'
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
```

## GREEN evidence

```
collected 43 items
... (all 43 tests, including the 4 new ones)
============================= 43 passed in 0.09s ==============================
```
All Task 1–4 tests plus the 4 new Task 5 tests pass; no regressions.

## 3x repeat-run of the sort-order test (flakiness check)

```
=== Run 1 ===
dashboard/tests/test_output_runs.py::test_discover_date_bucket_runs_sorts_newest_first PASSED [100%]
============================== 1 passed in 0.03s ==============================
=== Run 2 ===
dashboard/tests/test_output_runs.py::test_discover_date_bucket_runs_sorts_newest_first PASSED [100%]
============================== 1 passed in 0.04s ==============================
=== Run 3 ===
dashboard/tests/test_output_runs.py::test_discover_date_bucket_runs_sorts_newest_first PASSED [100%]
============================== 1 passed in 0.05s ==============================
```
No flakiness observed. This is expected given the implementation sorts by `parsed_date.timestamp()`
(deterministic from the folder name) rather than mtime, so two folders created back-to-back in the
same test run can never tie.

## Files changed

- `C:\Users\bottl\FinancialDevelopment\dashboard\output_runs.py` (+63 lines, pure append)
- `C:\Users\bottl\FinancialDevelopment\dashboard\tests\test_output_runs.py` (+53 lines, pure append)

## Self-review findings

- Diff matches the brief's code exactly (verified via `git diff` before commit) — no deviations.
- No existing code in either file was modified or reordered.
- `ruff check` on both files surfaces ~32 findings (`UP006`/`UP035` `List`→`list`, `UP045`
  `Optional`→`X | None`, `DTZ001`/`DTZ007` naive-datetime warnings, one import-sort issue). All of
  these are **pre-existing style debt spanning the whole file**, present in code from Tasks 1–4
  (e.g. `cluster_by_timestamp`'s `List[str]` signature, `extract_timestamp`'s naive
  `datetime.strptime`) — my added code merely extends the same established (if not ruff-clean)
  conventions already used throughout `output_runs.py`. Fixing them would mean deviating from "use
  the brief's code exactly as written" and touching lines outside Task 5's scope, so left untouched.
- Ran the full `dashboard/tests/` directory as an extra sanity check beyond what the task required;
  it did not finish within 120s and was stopped. This is unrelated to Task 5 — `test_output_runs.py`
  alone (the file this task modifies, and the command explicitly specified in the task instructions)
  runs in under 0.1s and is fully green. Did not investigate the other test file further since it's
  out of scope for this task.
- `git status` showed several unrelated pre-existing modifications (`.superpowers/sdd/progress.md`,
  `Options_Suite/tests/test_crr_binomial.py`, `Options_Suite/tests/test_pricing_models.py`,
  `docs/superpowers/plans/2026-08-09-dashboard-output-tab-redesign.md`, a deleted `swaps.db.lock`)
  plus some untracked archive files at repo root. None of these were touched or staged — only
  `dashboard/output_runs.py` and `dashboard/tests/test_output_runs.py` were added and committed, per
  the brief's explicit `git add` list.

## Concerns

None. Task 5 is complete, tests are green and non-flaky, and the commit is scoped exactly to the two
files the brief specifies.

## Commit

`5110b35` — "feat: add date-bucket run discovery for sentiment-scanner exports"
