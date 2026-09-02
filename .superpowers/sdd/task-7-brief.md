# Task 7 — Automatic Archiving System (Phase 6 of the Modularization Overhaul)

Source plan: `C:\Users\bottl\.claude\plans\i-want-to-do-cosmic-turing.md` (CARL-reviewed, converged
SHIP; read Phase 6's full section, including the "Post-Approval Update 2, 2026-09-02" correction at the
top of its first bullet — it resolves a real contradiction the original plan text had between "dedicated
archive DB" and "add a migrations/*.sql file", verified by reading `setup_db.py` before writing this
brief). This is Phase 6, sequenced ahead of Phase 4/5 per the user's explicit re-prioritization
(2026-09-02): Phase 3 (Vol_Suite split) is complete; Phase 6 (this task) and Phase 7 (Dealer Book tab)
are next, before circling back to Phase 4 (sentiment-scanner split) and Phase 5 (Options_Suite split).

## Why this matters now

Phase 7 (the Dealer Book dashboard tab, the user's most-wanted visible feature) needs this phase's
archive index to power its history-browsing feature ("a history of previous snapshots or module runs").
Phase 2 already stubbed the call site (`orchestrator.py::_archive_module_result`, currently `pass`) so
this phase is a pure implementation drop-in, not a call-site hunt.

## Read before writing code

- `orchestrator.py:1220-1229` — the existing no-op `_archive_module_result(module_slug, result,
  context)` stub and its call site inside `run_selected_modules` (`orchestrator.py:1340`-ish, confirm
  current line). This is where the real archiver plugs in.
- `shared/module_registry.py` — `ModuleSpec`/`ModuleResult`/`ArtifactRef`/`ArchiveHint` shapes (ground
  truth for what `ArchiveIndex.record()` receives).
- `migrations/004_add_quant_alerts.sql` and `dashboard/app.py::_summary_output_dir` (`:1104-1132`) —
  the `run_id`-as-TEXT precedent this phase's schema follows (see plan text for why).
- `setup_db.py` — confirms `migrations/*.sql` is exclusively `swaps.db`'s mechanism
  (`MIGRATIONS_DIR`/`migrate()` hardcode `swaps.db` as the target). **Do not add a file there for this
  phase** — the plan's Post-Approval Update 2 correction explains why and what to do instead.
- `shared/connection_pool.py` — `ConnectionPool(db_path, pool_size=5, ...)` already takes an arbitrary
  `db_path`, so it can be pointed at a new dedicated archive DB file. `init_pool()`/`get_pool()`
  (`:359-370`ish) manage a single module-level `_default_pool` singleton, already used for `swaps.db` —
  **do not call `init_pool()` for the archive DB** (that would clobber or conflict with the swaps.db
  singleton depending on call order); construct your own separate `ConnectionPool` instance for the
  archive DB path instead, owned by `shared/module_archive.py` itself.
- `dashboard/app.py`'s `WidgetCache`/`BarCache` (grep for them) — the existing precedent for a SQLite
  table that runs its own `CREATE TABLE IF NOT EXISTS` fresh-per-call rather than going through the
  swaps.db migration pipeline; this is the pattern to match, not `migrations/`.
- `dashboard/app.py::_execute_run` (`:739-774`ish) — the second trigger point this phase must wire.
- CLAUDE.md's documented `swaps.db` single-writer / cross-process locking convention, and why the
  dedicated archive DB sidesteps it (a standalone `cli_entry` module run is a separate OS process from
  a running dashboard — this is why WAL mode + a genuinely separate DB file matter here, not a
  same-process-only concern).

## This task's scope

1. **New file `shared/module_archive.py`**:
   - `ArchiveIndex` class (or a small set of module-level functions — your call, document which and
     why) backed by a **dedicated SQLite file** (e.g. `module_archive.db` at repo root, matching
     `swaps.db`'s own placement convention — confirm a sensible path via `shared/config.py` if that's
     where `SWAPS_DB_PATH`-style config lives, or hardcode a sibling path next to it, your call).
   - Owns its own schema: `CREATE TABLE IF NOT EXISTS module_archive (id INTEGER PRIMARY KEY
     AUTOINCREMENT, module_slug TEXT NOT NULL, suite TEXT NOT NULL, ticker TEXT, expiry TEXT, run_id
     TEXT, triggered_by TEXT NOT NULL, timestamp TIMESTAMP NOT NULL, artifact_paths_json TEXT NOT NULL,
     metrics_json TEXT NOT NULL)` plus sensible indexes for the two query patterns below (e.g. one on
     `(ticker, expiry, module_slug)`, one on `(module_slug, timestamp)`). `triggered_by` is one of
     `"cli"`, `"orchestrator"`, `"dashboard"` (a plain TEXT column with app-level validation, not a SQL
     CHECK constraint, matching this repo's general schema style — confirm against `migrations/*.sql`
     for the actual house style before deciding).
   - `record(module_result: ModuleResult, module_spec: ModuleSpec, context: dict, *, triggered_by: str)
     -> None`: reads `module_result.artifacts`/`.metrics`, extracts `ticker`/`expiry` from `context`
     (module registry `run()` calls already receive `context` with these — check the actual shape
     Task 3/4/5's `_run_*` functions use via `_resolve_ticker`/`_resolve_expiry` helpers in
     `Vol_Suite/module_registry.py` for the established convention), serializes artifacts/metrics to
     JSON, writes one row. **Must never raise** — per the plan's explicit requirement, a failure to
     archive must log a warning (use this repo's `shared/logging.py` structured logger if that's the
     established convention — check) and return, never propagate an exception that could fail the
     module run itself. Wrap the whole body in `try/except Exception`.
   - `query(*, ticker: str | None = None, expiry: str | None = None, module_slug: str | None = None,
     since: datetime | None = None, until: datetime | None = None, limit: int = 50) -> list[dict]` (or
     a small dataclass row type, your call) — supports both documented query patterns: by
     `(ticker, expiry, module_slug)` and by `(module_slug, date range)`. Rows ordered newest-first.
   - Connection management via a dedicated `ConnectionPool` instance (see "Read before writing code"
     above) — not the swaps.db singleton, not a bare `sqlite3.connect()` per call unless you have a
     specific reason to deviate (document it if so). WAL mode enabled (check how `connection_pool.py`
     or `swaps.db`'s own setup enables it, mirror that).
2. **Wire the archiver hook into all three trigger points**, per the plan's explicit requirement that
   every module run archives automatically regardless of launch path:
   - `orchestrator.py::_archive_module_result` (`:1220-1229`): replace the `pass` stub with a real call
     to `module_archive.record(...)`, `triggered_by="orchestrator"` (or `"cli"` if you can distinguish —
     check how `run_selected_modules` is invoked from `orchestrator.py`'s own `--modules` CLI flag vs.
     from the dashboard's `POST /run/{kind}` path calling into the same function; if you can't cleanly
     distinguish CLI-direct from dashboard-triggered at this call site, `"orchestrator"` covering both
     is fine — document the choice).
   - `dashboard/app.py::_execute_run`: if it already routes through `orchestrator.run_selected_modules`
     when a `modules` field is present (Task 2's work), the hook above may already cover the dashboard
     path — verify this by tracing the actual call chain rather than assuming, and only add a second,
     separate call site here if the dashboard path genuinely bypasses `run_selected_modules`'s archiver
     hook somehow. Don't double-archive the same module run.
   - Each suite's standalone `cli_entry` `__main__` block (e.g. `Vol_Suite/dealer_exposure_module.py`,
     `dealer_flow_module.py` from Task 3 — check if others exist by now) — these currently run a module
     directly, bypassing `run_selected_modules` entirely, so they need their own explicit
     `module_archive.record(..., triggered_by="cli")` call after a successful run. Read each existing
     `cli_entry` file's `__main__` block before editing to match its existing structure/error handling.
3. **Tests** (`tests/test_module_archive.py` — new file):
   - Recording a `ModuleResult` produces a queryable row (round-trip: record, then query by
     ticker/expiry/module_slug, confirm the row matches).
   - Querying by `(module_slug, date range)` works (insert rows with different timestamps, query a
     sub-range, confirm correct subset).
   - A `record()` call that would fail (e.g. an unwritable DB path, or a metrics dict that can't
     JSON-serialize) logs a warning and does NOT raise — assert this explicitly with a test that would
     fail loudly if `record()` ever started propagating exceptions.
   - **Genuine multi-process concurrency test**: spawn a second Python `subprocess` that opens its own
     connection to the same archive DB file and writes a row, concurrently with (or immediately
     interleaved against) the main test process writing its own rows — confirm no "database is locked"
     error and both rows land. This is explicitly required per the plan (same-process thread-based
     concurrency doesn't reproduce the documented cross-process failure mode) — don't substitute a
     threading-based test for this.
   - A test confirming the archiver hook is actually wired at each of the three trigger points (mock
     `module_archive.record` and assert it's called with the right `triggered_by` value from each path)
     — at minimum for `orchestrator.py::run_selected_modules`; cover the dashboard/CLI-entry paths too
     if you added real wiring there (per point 2's "verify, don't double-archive" guidance).
   - All ThetaData-dependent calls mocked where relevant (module `run()` calls under test should be
     stubbed/faked, not hitting live network) — this test file is about the archiver, not about
     re-testing module correctness.

## Fragile surfaces

- **`swaps.db` single-writer convention**: this phase must not touch `swaps.db` or its migration
  pipeline at all — confirmed via the Post-Approval Update 2 correction above. If you find yourself
  about to add a `migrations/*.sql` file, stop — that's the wrong mechanism for this dedicated DB.
- **Never let archiving fail a module run**: `record()`'s try/except-and-log-never-raise contract is
  the single most important correctness property in this task. Test it explicitly (see above).
- **Don't double-archive**: trace the actual dashboard → orchestrator call chain before adding a second
  hook in `dashboard/app.py` — Task 2 already wired `_execute_run` to call `run_selected_modules` when
  `modules` is present, which already carries the archiver hook via `orchestrator.py`. A redundant
  second `record()` call for the same module run would pollute the archive with duplicate rows.
- **`ArchiveHint.key_shape`** (`"ticker_expiry"` | `"ticker_only"` | `"global"`, from
  `shared/module_registry.py`, Phase 1): use this to decide what to store in the `ticker`/`expiry`
  columns for a module whose `archive.key_shape` isn't `"ticker_expiry"` (e.g. a future `"global"`
  module with no per-ticker scope) — don't assume every module has a real ticker/expiry to record;
  `NULL` is fine for the columns that don't apply, per the schema above already allowing it.

## What NOT to do in this task
- Do not add a `migrations/*.sql` file — the dedicated archive DB owns its own schema creation.
- Do not touch `swaps.db`, `shared/connection_pool.py`'s existing `init_pool()`/`get_pool()` singleton
  usage for `swaps.db`, or any existing `orchestrator_runs`/`quant_alerts` table logic.
- Do not replace `dashboard/output_runs.py` — it stays as the fallback path for pre-archiver-era
  artifacts, per the plan. This phase only adds `ArchiveIndex.query()` as a new, additional option.
- Do not modify `run_unified`, `_thread_vol_stats_into_context`, or `_SUITE_SPECS` in `orchestrator.py`
  — this task only touches `_archive_module_result`'s body (already a stub, already a designated
  drop-in point) and, if genuinely needed after tracing the call chain, `dashboard/app.py::_execute_run`.
- Do not build the Dealer Book dashboard tab itself (Phase 7) or any dashboard UI for browsing the
  archive — this task is the write/query backend only.

## Testing

Run `tests/test_module_archive.py`, then a full repo `pytest -q` pass — run these synchronously in the
foreground and wait for them to actually finish before reporting; do not start background/async test
monitoring (this has repeatedly caused problems in prior tasks this session — the specific failure mode
to avoid is reporting back with something like "I'll wait for the background test run to complete"
instead of literally waiting for pytest to exit in the same tool call). Report exact commands and
pass/fail counts. Also run the three-suite Vol_Suite baseline gate
(`pytest Vol_Suite/tests/ -q`, current baseline **1142 passed, 7 pre-existing failures in
test_dual_pipeline_gate_v2.py/test_dual_pipeline_gate_v7.py, 10 skipped**) since this task touches
Vol_Suite's `cli_entry` `__main__` blocks — confirm no regression there.

## Report contract
Follow the standard implementer report contract (status DONE/DONE_WITH_CONCERNS/NEEDS_CONTEXT/BLOCKED,
commits made, one-line test summary, concerns). Write your full report to
`.superpowers/sdd/task-7-brief-report.md` (note the distinct filename — this repo has a history of
`task-N-report.md` filename collisions with unrelated prior plan runs; `task-7-report.md` may or may
not already exist, check first, but use the `-brief-report.md` suffix regardless to stay consistent
with Tasks 4-6's naming).
