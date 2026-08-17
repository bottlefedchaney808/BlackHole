# Task 6 Report: `output_runs.py` public dispatch, unified-run detection, file views

## What was implemented

Appended to `dashboard/output_runs.py` (verbatim from the brief), capping off the
module Tasks 1-5 built:

- `SUITE_LABELS` (dict of the 5 suite keys -> display labels)
- `MAX_TABLE_ROWS` / `MAX_TABLE_COLS` constants
- `discover_runs(suite_key) -> List[RunInfo]` — the single public dispatch that
  combines `discover_rundir_runs` (shared `orchestrator_output/`), the vol-specific
  `outputs/*` rundir source, `discover_clustered_runs` (Options_Suite
  `comparison_*.csv/.pdf`), `discover_loose_bucket` (Vol_Suite's legacy
  `vs_output/`), and `discover_date_bucket_runs` (sentiment date folders) —
  then computes `sibling_suites` for every returned run via `_discover_unified_runs`
  and re-sorts newest-first.
- `_discover_unified_runs()` — scans `orchestrator_output/*` directories, uses
  `claim_files_for_suite` (from Task 2) across all four suites, and reports a
  `suite='unified'` `RunInfo` for any directory claimed by 2+ suites.
- `get_run(suite_key, run_id)` — trivial lookup into `discover_runs`'s own
  (already-correct) result list.
- `read_json_view(path)`, `read_csv_table(path)`, `build_file_view(run_file)` —
  file-content view builders (JSON -> pairs/table/raw; CSV -> table; png/pdf/other
  -> bare kind markers for template-side rendering).

Appended to `dashboard/tests/test_output_runs.py`: the 6 tests specified in the
brief verbatim (`test_suite_labels_covers_all_five_keys`,
`test_discover_runs_combines_sources_and_detects_unified_siblings`,
`test_get_run_finds_a_run_by_id`, `test_read_json_view_pairs_kind`,
`test_read_csv_table_table_kind`, `test_build_file_view_dispatches_by_kind`).

Nothing in `dashboard/app.py` was touched (Task 7's scope).

## RED/GREEN evidence

**RED** (before implementation, tests appended, only imports added):
```
ImportError while importing test module '...\dashboard\tests\test_output_runs.py'.
E   ImportError: cannot import name 'discover_runs' from 'dashboard.output_runs'
```

**GREEN** (after implementation appended):
```
dashboard/tests/test_output_runs.py ... (49 items)
============================== 49 passed in 0.12s ==============================
```
43 pre-existing (Tasks 1-5) tests + 6 new Task 6 tests = 49, no regressions.
(Note: the brief's own "expected e.g. `32 passed`" example text is stale/inconsistent
with the actual accumulated test count from Tasks 1-5 already in the repo; 49 is the
correct total given what was already present before this task started.)

Also ran the full `dashboard/tests/` directory for a broader regression check:
```
179 passed, 1 warning in 27.88s
```
(the 1 warning is a pre-existing, unrelated schema-version backward-compat warning
in `test_quant_summary_route.py`, not something this task touched.)

## Files changed

- `C:\Users\bottl\FinancialDevelopment\dashboard\output_runs.py` (+207 lines, appended)
- `C:\Users\bottl\FinancialDevelopment\dashboard\tests\test_output_runs.py` (+94 lines, appended)

Commit: `db9cf4f` — "feat: add output_runs public dispatch, unified detection, file views"

Only these two files were staged/committed. The working tree also had pre-existing,
unrelated local modifications (`.superpowers/sdd/progress.md`,
`Options_Suite/tests/test_crr_binomial.py`, `Options_Suite/tests/test_pricing_models.py`,
`docs/superpowers/plans/2026-08-09-dashboard-output-tab-redesign.md`, a deleted
`swaps.db.lock`) that predate this session — left untouched and unstaged, not part of
this commit.

## Self-review

**Sibling-suites logic traced by hand** against
`test_discover_runs_combines_sources_and_detects_unified_siblings`:
- `discover_rundir_runs(ORCH_OUTPUT/*, 'options', 'orch')` finds the one run dir,
  `claim_files_for_suite` claims only `options_result.json` for suite `'options'`
  → one `RunInfo` with `run_id='orch:20260729T055306Z'`, `sibling_suites=[]` (default).
- `_discover_unified_runs()` independently re-scans the same directory: `per_suite`
  claims `options_result.json` for `'options'` and `var_result.json` for `'var'`
  (vol's catch-all claim is correctly excluded since both filenames are in
  `_NON_VOL_MARKERS`), `suites_present = ['options', 'var']` (>= 2) → one unified
  `RunInfo` with `sibling_suites=['options', 'var']`.
- Back in `discover_runs('options')`: `unified_siblings = {'orch:...': ['var']}`
  (self-suite `'options'` filtered out of the sibling list) → the options run gets
  rebuilt with `sibling_suites=['var']`. Matches the test assertion exactly.

This confirms the design point called out in the task context: sibling detection
is computed for *every* run `discover_runs` returns, not bolted onto `get_run()` —
traced and verified, not just assumed from the brief's docstring.

**Sort-key mtime concern** (flagged given the Task 4/5 history of a timestamp-sort
bug): all sorts added in this task (`discover_runs`'s two `runs.sort(key=lambda r:
r.timestamp, ...)` calls) sort on `RunInfo.timestamp`, the computed field — never on
raw filesystem mtime — consistent with the fixed pattern from Tasks 4/5. The one
place raw mtime does feed into `.timestamp` is `_discover_unified_runs`
(`timestamp=max(f.modified for f in files)`), but that's the same pattern
`discover_rundir_runs` already established in Task 2 for ordinary per-suite rundir
runs (not a new risk this task introduces) — unified runs don't have a
directory-name-embedded date the way sentiment's date-bucket runs do, so there's no
more-reliable value available here to prefer over mtime.

**Verbatim-code check**: implementation and tests were pasted directly from the
brief with no modifications. One slip during editing: I briefly added a stray
`# noqa: E301` comment to the `discover_date_bucket_runs` signature line while
positioning an edit anchor — caught and reverted before the implementation append,
confirmed the final file has no trace of it.

**Lint**: `ruff check` reports pre-existing style findings (`UP035`/`UP006` typing.List
vs `list`, `UP045` Optional vs `X | None`, `DTZ001`/`DTZ007` naive datetimes, import
sorting) — these are consistent with the exact style already used throughout the file
by Tasks 1-5 (e.g. `List[RunInfo]`, `Optional[RunInfo]` are used identically in
pre-existing functions like `discover_rundir_runs`), not something this task's new
code does differently. No `.git/hooks/pre-commit` is installed locally and
`pre-commit` isn't importable in the venv, so these findings don't block the commit
gate as configured. Left as-is rather than "fixing" since the task brief calls for
verbatim code and fixing only the new additions would leave the file internally
inconsistent.

## Concerns

None blocking. Two minor notes for whoever picks up Task 7:
- The brief's own "expected passed count" comment in Step 4 is out of date (says
  "32 passed" as an example) — actual expected count from Tasks 1-5 plus this task
  is 49; worth keeping in mind if a similar stale-count comment shows up in later
  task briefs.
- Pre-existing unrelated modified files sitting in the working tree (listed above)
  were left alone; someone should account for them before this branch is finished,
  since they aren't part of this task's diff.
