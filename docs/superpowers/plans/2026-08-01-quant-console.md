# Quant Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/superpowers/specs/2026-08-01-quant-console-design.md` (CARL-converged after 3 rounds, commit `2699784`). Read it before starting — this plan doesn't repeat its rationale, only translates its decisions into file-level tasks.

**Goal:** Extend `dashboard/app.py` into a module-card "Quant Console": one structured `quant_summary.json` per run, a module-card view replacing the dead `vol-suite.py` print-to-stdout dashboard, headless-`claude -p` worker dispatch (interpret/investigate/explain) from a completed run's card, a live log panel for continuous processes, and a best-effort watchlist/alert layer.

**Architecture:** Three phases, in order — each phase's endpoints are usable on their own before the next starts. Phase 1 adds a read/render layer on top of the dashboard's *existing* job-tracking (`POST /run`, `GET /runs/{id}`) — no new job engine. Phase 2 adds a second, independently-authorized mutation surface (`dispatch`) that spawns tracked `claude -p` subprocesses. Phase 3 adds a WebSocket log-tail and a durable-but-best-effort alert layer that does not depend on any Claude Code session being active to *detect* conditions, only to *notify* about them promptly.

**Tech Stack:** Python 3.12 (shared root `.venv`), FastAPI/Jinja2 (existing), `websockets` (already a dependency — no new package for Phase 3's live panel), **`pywin32` — new dependency, add to `requirements.txt`** (Windows Job Object bindings for Phase 2's timeout-kill), pytest + `unittest.mock` (existing convention, see `Tools/tests/`).

## Global Constraints

- No new auth/broker system. Every new mutating endpoint uses the existing `verify_api_key`; dispatch additionally uses the new `require_dispatch_configured` (Task 7) — never invent a third auth mechanism.
- Worker subprocess `env=` is always built as an **explicit allowlist** (`PATH`, `HOME`/`USERPROFILE`, `TEMP` — nothing else), never the full inherited `os.environ` and never a blocklist. This is the single most load-bearing constraint in this plan (spec R2-F1/R3-F2) — get it wrong and `DASHBOARD_API_KEY` leaks to a subprocess that, for `explain`, has live network egress.
- `explain` gets `WebSearch`/`WebFetch` and *no* Bash/shell tool access. `interpret`/`investigate` get neither network tool.
- `investigate` never commits or pushes. Diff stays in its worktree for manual review.
- Nothing in this plan calls Claude's `PushNotification` tool from `dashboard/app.py`'s backend — it can't (no LLM in that process). Completion surfacing is: an in-page browser notification (Task 16) plus, separately and only best-effort, whatever an actively-watching Claude Code session chooses to do on its own.
- No autonomous re-running of billed suites anywhere in this plan, including Phase 3's detection script — it only *reads* `orchestrator_runs`/`quant_summary.json` history, never triggers a new run.
- Every new endpoint stays inside the existing loopback-bound FastAPI process — no new listening port, no new process, no new broker.
- Match existing test conventions: `pytest -m unit` requires no network/credentials; mock `subprocess.Popen` for dispatch tests; one explicitly-marked manual smoke test is allowed to actually shell out.

---

## Phase 1 — Foundation

### Task 1: Load `.env` in the dashboard process

**Files:**
- Modify: `dashboard/app.py` (top of file, before `dashboard.auth` import)
- Test: `dashboard/tests/test_env_loading.py` (new)

**Why first:** Task 7's `require_dispatch_configured` and this task both depend on `DASHBOARD_API_KEY` actually reaching `os.environ`. Currently nothing in `dashboard/app.py`'s import chain calls `shared.config.load_env_once()` (verified during CARL R2 — confirmed absent). Doing this now, standalone, means Phase 2 doesn't inherit a silent bug.

- [ ] **Step 1: Write the failing test.** Assert that importing `dashboard.app` (in a subprocess or with `os.environ` cleared/monkeypatched and a temp `.env` pointed at via `shared.config`) results in a custom `DASHBOARD_API_KEY` value being visible to `dashboard.auth.API_KEY`, not the hardcoded default.
- [ ] **Step 2: Implement.** Add `from shared.config import load_env_once; load_env_once()` immediately after the `sys.path` setup and before `import orchestrator`/`from dashboard.auth import ...` in `dashboard/app.py`. Confirm `shared/config.py::load_env_once()` is idempotent (safe to call once here even if a suite subprocess also calls it later) — read its current implementation before assuming.
- [ ] **Step 3: Run tests to verify they pass.**

### Task 2: `quant_summary` schema

**Files:**
- Modify: `shared/schemas.py`
- Test: `tests/test_schemas.py` (extend existing file if present, else `shared/tests/test_schemas.py` matching existing convention)

**Interfaces:**
- `validate_quant_summary(data: dict) -> None` — raises `ValueError` on violation, mirroring the existing five validators' style.
- Schema per the spec's Phase 1 table: `schema_version` (int, `1`), `run_id` (str), `ticker` (str), `created_at_utc` (ISO str), `modules` (list of `{module, status, headline, metrics, warnings, source_result}`), `status ∈ {ok, error, degraded, unsupported}`.

- [ ] **Step 1: Write failing tests** — valid payload passes; missing `schema_version` fails; `status` outside the four-value enum fails; a `modules` entry missing `module`/`status` fails.
- [ ] **Step 2: Implement `validate_quant_summary`** following the existing five validators' error-message style in the same file.
- [ ] **Step 3: Run tests to verify they pass.**

### Task 3: Extractors + `build_run_summary()`

**Files:**
- Create: `shared/summary.py`
- Test: `shared/tests/test_summary.py` (new)

**Interfaces:**
- `build_run_summary(run_dir: Path, run_id: str, ticker: str) -> dict` — scans `run_dir` for `*_result.json` files, calls the matching extractor per module, assembles and returns a `quant_summary`-shaped dict (does not write the file — caller does, so this function is easy to unit-test against fixtures).
- One extractor per suite, each `(result: dict) -> ModuleSummary` (a small `TypedDict` or dataclass matching the schema's `modules[]` entry shape):
  - `_extract_vol(result: dict) -> dict` — **ports** `.claude/skills/vol-suite.py::extract_metrics_from_run`'s gamma/correlation/variance-swap logic (read that file first; this is a port, not a rewrite).
  - `_extract_options(result: dict) -> dict`, `_extract_var(result: dict) -> dict`, `_extract_sentiment(result: dict) -> dict` — new, small, built directly against each result schema already in `shared/schemas.py`.
  - Every extractor: missing/malformed input → `status: "degraded"`, never raises.
- `runnable` gating: `build_run_summary` takes an optional `module_registry` (Task 5) so a module with `runnable=False` (Options today) short-circuits straight to `status: "unsupported"` without attempting extraction — implements the spec's Open Question 2 resolution.

- [ ] **Step 1: Write failing tests** against canned fixture `*_result.json` files (valid, missing-field, malformed-JSON, and one fixture per suite) — no network.
- [ ] **Step 2: Implement extractors**, porting Vol_Suite's logic from `vol-suite.py` per above.
- [ ] **Step 3: Implement `build_run_summary()`** wiring extractors + the `runnable`-gate short-circuit.
- [ ] **Step 4: Run tests to verify they pass.**

### Task 4: Wire the summary writer into the dashboard + `GET /runs/{run_id}/summary`

**Files:**
- Modify: `dashboard/app.py` (`_execute_run`, new route)
- Test: `dashboard/tests/test_quant_summary_route.py` (new)

**Interfaces:**
- At the end of `_execute_run()` (after the suite/orchestrator subprocess exits, existing result-writing code), call `shared.summary.build_run_summary(...)` and write `quant_summary.json` into that run's output directory via temp-file + atomic rename (same durability pattern Phase 2's worker reports use — establish it here once).
- `GET /runs/{run_id}/summary` — reads and returns `quant_summary.json` if present, `404` if the run isn't done yet or the file doesn't exist, validated through `validate_quant_summary` before returning (a corrupt file should 500 loudly, not silently serve garbage to the frontend).

- [ ] **Step 1: Write failing tests** — a completed run produces a fetchable, schema-valid summary; a run still `running` returns 404 from the summary endpoint (not confused with "no such run"); a run whose suite subprocess failed still gets a summary (with `error`/`degraded` module entries), not a missing file.
- [ ] **Step 2: Implement.**
- [ ] **Step 3: Run tests to verify they pass.**

### Task 5: Module registry

**Files:**
- Create: `dashboard/quant_modules.py` (small, mirrors the sibling project's `MODULE_REGISTRY` shape, adapted)
- Test: `dashboard/tests/test_quant_modules.py` (new)

**Interfaces:**
- `MODULE_REGISTRY: tuple[dict, ...]` — one entry per suite (`vol`, `options`, `var`, `sentiment`) + orchestrator `unified`, each `{id, name, suite, focus, runnable}`. Source `runnable=False` for `options` from the current documented state (`tool-launcher` skill — Options context-mode FAIL) — **do not silently flip this to `True`**; it changes only when that underlying bug is fixed, independently of this plan.
- `get_module(module_id: str) -> dict` — same "raise `KeyError` with valid options listed" pattern as `Tools/registry.py::get_tool`.

- [ ] **Step 1: Write failing tests** — registry has exactly 5 entries; `options`'s `runnable` is `False`; `get_module` raises with a helpful message on a bad id.
- [ ] **Step 2: Implement.**
- [ ] **Step 3: Run tests to verify they pass.**

### Task 6: `GET /quant` module-card view

**Files:**
- Modify: `dashboard/app.py` (new route)
- Create: `dashboard/templates/quant.html` (Jinja2, matches existing `dashboard/templates/` conventions — read an existing template first, e.g. whatever backs `/suites/{suite}`)
- Reuse: metric-card/tab CSS from `.claude/skills/vol-suite.py::generate_dashboard_html` (port the `<style>` block, don't reinvent it)

**Behavior:** one card per `MODULE_REGISTRY` entry — ticker input + Run button (client-side `fetch` to the *existing* `POST /run/{suite_or_unified}`), status polling (existing `GET /runs/{run_id}`), and once `done`, a `fetch` to `GET /runs/{run_id}/summary` (Task 4) rendered as headline + metric grid + warnings, with a distinct visual treatment for `unsupported` (neutral/grey) vs. `degraded` (amber) vs. `error` (red) vs. `ok` (green) — this distinction is the whole point of Task 2's schema addition, don't collapse it back to a generic error state in the template.

- [ ] **Step 1:** Build the template + route, manually verify against a real completed run's `quant_summary.json` (from Task 4) in a browser — this is UI, not unit-testable in the usual sense; note in the PR/commit that manual verification was done, per this repo's own "test UI changes in a real browser" convention.
- [ ] **Step 2:** Confirm the Options card renders `unsupported` distinctly, not as a generic failure — this is the concrete, checkable form of the spec's Open Question 2 resolution.

**Phase 1 checkpoint:** at this point, `.claude/skills/vol-suite.py`/`vol-suite-viewer.py` can be deleted (their function is now fully subsumed by `GET /quant`) — confirm with the user before deleting rather than doing it silently as part of this task.

---

## Phase 2 — Worker Actions

### Task 7: `require_dispatch_configured` + `pywin32` dependency

**Files:**
- Modify: `requirements.txt` (add `pywin32==<pin the version already resolved as a transitive dep in .venv today, for continuity>`)
- Modify: `dashboard/auth.py` (new dependency function)
- Test: `dashboard/tests/test_auth.py` (extend, or new)

**Interfaces:**
- `require_dispatch_configured(request: Request) -> None` in `dashboard/auth.py`, same shape as `verify_api_key` — raises `HTTPException(503, ...)` if `API_KEY == DEFAULT_API_KEY` (i.e., unset or left at default). **Per-request, not process-startup** — must not be able to take down the whole app (spec R2-F3/R3-F1 — this was gotten wrong twice already, be careful).
- Applied *only* to the two routes built in Tasks 9 and 12 — not to `/run`, `/runs/{id}`, `/quant`, or anything else pre-existing.

- [ ] **Step 1: Write failing tests** — dispatch-decorated endpoint with default/unset key returns 503 with the documented message; a pre-existing endpoint (`GET /runs/{id}`) is unaffected by a default/unset key in the same test run; a properly-configured key passes through.
- [ ] **Step 2: Implement `require_dispatch_configured`.**
- [ ] **Step 3: Add `pywin32` to `requirements.txt`; document the install step for anyone rebuilding the venv** (this doesn't get exercised by this task's own tests — Task 10 is where it's actually used).
- [ ] **Step 4: Run tests to verify they pass.**

### Task 8: Worker subprocess environment allowlist

**Files:**
- Create: `dashboard/worker_env.py`
- Test: `dashboard/tests/test_worker_env.py` (new)

**Interfaces:**
- `build_worker_env() -> dict` — returns a fresh dict containing only `PATH`, `HOME`/`USERPROFILE` (whichever is set on this OS), `TEMP`, sourced from the *current* `os.environ` (post-`load_env_once()`, but this function must not itself pull in anything `load_env_once()` added — assert this in the test, not just by convention).

- [ ] **Step 1: Write failing tests** — given a fake `os.environ` containing `DASHBOARD_API_KEY`, `THETADATA_CF_ACCESS_CLIENT_ID`, `PATH`, `TEMP`, assert the returned dict contains only `PATH`/`TEMP` (and `HOME`/`USERPROFILE` if present) and explicitly does **not** contain the credential keys — this is the single highest-value test in this plan (spec R2-F1, critical).
- [ ] **Step 2: Implement.**
- [ ] **Step 3: Run tests to verify they pass.**

### Task 9: `POST /runs/{run_id}/dispatch/{action}`

**Files:**
- Modify: `dashboard/app.py` (new route)
- Test: `dashboard/tests/test_dispatch.py` (new)

**Behavior (see spec Phase 2 for full detail — this is the checklist form):**
- Validates `run_id` exists and is `done` (reuse the existing `_RUNS`/DB lookup from `GET /runs/{run_id}`, don't duplicate the lookup logic — extract a shared helper if it isn't already one).
- Gated by `verify_api_key` **and** `require_dispatch_configured` (Task 7).
- `action ∈ {interpret, investigate, explain}`; anything else → 400.
- Builds a prompt referencing `quant_summary.json` + `*_result.json` **by path**, not inlined, explicitly labeled as evidence/data.
- Per-action tool scoping (see Global Constraints — this is where it's enforced, via whatever `claude -p` flag/config controls tool availability; confirm the actual flag during implementation rather than assuming one).
- Spawns `claude -p "<prompt>" --output-format json` via `subprocess.Popen(..., env=build_worker_env())` (Task 8) with `cwd` = repo root (`interpret`/`explain`) or a fresh worktree (`investigate`, Task 11).
- Tracked via the same `_RUNS`-style registry as orchestrator jobs, tagged `run_type='dashboard:worker:{action}'` in `orchestrator_runs` (existing free-form column, no migration).
- Idempotency: optional `idempotency_key` in the request body; repeat → return existing job, don't relaunch. In-memory dict keyed `(run_id, action, idempotency_key)`.
- Max-concurrent-workers cap (e.g. 2) enforced before spawning — over-cap request → 429, not silently queued indefinitely.

- [ ] **Step 1: Write failing tests** (all with `subprocess.Popen` mocked) — happy path returns 202 + job id; unknown `run_id` → 404; not-yet-`done` run → 409/400; unauthorized (missing key, and separately, default-key via Task 7) → 401/503; duplicate idempotency key → same job id, `Popen` called only once; over-concurrency-cap → 429; `env=` passed to `Popen` is exactly `build_worker_env()`'s output (assert the mock call args, not just that it doesn't crash).
- [ ] **Step 2: Implement**, reusing existing job-tracking plumbing rather than duplicating it.
- [ ] **Step 3: Run tests to verify they pass.**

### Task 10: Windows Job Object timeout enforcement

**Files:**
- Create: `dashboard/job_object.py` (isolates the `pywin32` usage so it's easy to test/mock and easy to find if it ever needs replacing)
- Modify: `dashboard/app.py` or wherever Task 9's dispatch launches the subprocess, to use this module
- Test: `dashboard/tests/test_job_object.py` (new — mock the `win32job`/`win32process` calls, this is not something to exercise against a real OS-level Job Object in unit tests)

**Interfaces:**
- `run_with_job_object(command: list[str], cwd: str, env: dict, timeout_sec: int) -> subprocess.Popen` (or a small wrapper class) — creates a Job Object, assigns the spawned process to it at launch, and exposes a `terminate()` that kills the whole job (all descendants) rather than just the tracked PID.
- Per-action timeouts: `interpret`/`explain` short (e.g. 5 min), `investigate` longer (e.g. 20 min) — read from a small constant dict, not hardcoded inline per call site.
- `taskkill /F /T /PID <pid>` implemented as an explicit fallback path, used only if Job Object creation/assignment itself fails (log clearly when this fallback triggers — it's a degraded guarantee, not equivalent).

- [ ] **Step 1: Write failing tests** — mock `win32job`/`win32process`, assert a spawned process is assigned to a job at launch; assert `terminate()` calls the job-kill API, not a plain `Popen.kill()`; assert the fallback path is only reached when job creation raises.
- [ ] **Step 2: Implement**, isolating all `pywin32` calls in this one module.
- [ ] **Step 3: Wire the per-action timeout into Task 9's dispatch — on timeout, mark job `timed_out`, retain partial stdout (don't discard).**
- [ ] **Step 4: Run tests to verify they pass.**
- [ ] **Step 5 (manual, not automated): one real timeout test** — dispatch something guaranteed to exceed its timeout against a throwaway fixture, confirm no orphaned `claude -p` process or descendant survives (check via Task Manager / `Get-Process`), confirm the `investigate` worktree (Task 11) isn't left locked.

### Task 11: Git worktree isolation for `investigate`

**Files:**
- Create: `dashboard/worker_worktree.py`
- Test: `dashboard/tests/test_worker_worktree.py` (new)

**Interfaces:**
- `create_worker_worktree(run_id: str, job_id: str) -> Path` — `git worktree add` into a fresh path (e.g. `.worker_worktrees/quant-worker-{job_id}`), returns the path for use as the subprocess's `cwd`.
- No automatic cleanup function in this task (explicit non-goal per spec Error Handling — a worktree can hold an uncommitted diff the user still needs). Document the manual cleanup command (`git worktree remove`) in this module's docstring instead of automating deletion.
- The `investigate` prompt built in Task 9 must explicitly instruct no commit/push — verify this instruction is actually present in the constructed prompt string in a test, don't just trust it's there by convention.

- [ ] **Step 1: Write failing tests** — worktree gets created at the expected path; branch/dir name carries the `quant-worker-` prefix (spec's orphan-identification convention); prompt-building includes the no-commit/no-push instruction verbatim.
- [ ] **Step 2: Implement.**
- [ ] **Step 3: Run tests to verify they pass.**

### Task 12: `GET /runs/{run_id}/dispatch/{job_id}` + worker report rendering

**Files:**
- Modify: `dashboard/app.py` (new route)
- Modify: `dashboard/templates/quant.html` (render worker results with provenance)
- Test: `dashboard/tests/test_dispatch_poll.py` (new)

**Interfaces:**
- Poll endpoint: same response shape as `GET /runs/{run_id}` (`queued`/`running`/`completed`/`failed`/`timed_out`, `done`, stdout tail, result once written). Gated by `verify_api_key` + `require_dispatch_configured`, same as Task 9.
- Worker writes `orchestrator_output/<run_id>/quant_worker_<action>_<job_id>.json` via temp-file + atomic rename (same pattern established in Task 4).
- Card UI: once a dispatch job is `done`, fetch and render its report inline under the module's summary, tagged with worker name + action + timestamp ("provenance") — visually distinct from the run's own `quant_summary.json` content so it's clear which came from the automated suite vs. which came from a dispatched worker's interpretation.

- [ ] **Step 1: Write failing tests** — poll returns correct shape at each state; a malformed/missing worker report file degrades gracefully (matches the "degraded" pattern from Task 3, don't crash the poll).
- [ ] **Step 2: Implement route + template changes.**
- [ ] **Step 3: Run tests to verify they pass; manually verify one real dispatch round-trip in a browser** (this is the plan's designated manual smoke test from the spec's Testing section).

---

## Phase 3 — Live Panel + Proactive Layer

### Task 13: `WS /suites/{suite}/live` log-tail

**Files:**
- Modify: `dashboard/app.py` (new websocket route)
- Test: `dashboard/tests/test_live_ws.py` (new — use `TestClient`'s websocket support)

**Interfaces:**
- Tails a suite's current log file (sentiment-scanner's loop-mode stdout, redirected to a file the same way job logs already are elsewhere in this codebase — reuse that redirection convention, don't invent a second one).
- Streams new lines to connected clients as they're written; closes cleanly when the underlying process exits or the client disconnects.

- [ ] **Step 1: Write failing tests** — connecting and receiving lines appended to a fixture log file; clean disconnect on client close; clean disconnect when the tailed file's writer process is no longer running.
- [ ] **Step 2: Implement**, matching this repo's existing async patterns in `dashboard/app.py`.
- [ ] **Step 3: Run tests to verify they pass.**

### Task 14: Alert detection (durable, no LLM)

**Files:**
- Create: `dashboard/quant_alerts.py` (detection logic, importable and independently testable)
- Create: `scripts/quant_alert_check.py` (thin CLI entry point for the OS-level scheduled task to invoke)
- Migration: add a `quant_alerts` table (small — `id`, `run_id`, `ticker`, `condition`, `detail`, `created_at_utc`, `acknowledged` bool) — follow this repo's existing `migrations/00N_*.sql` numbering convention, don't hand-edit `swaps.db`'s schema outside that mechanism.
- Test: `dashboard/tests/test_quant_alerts.py` (new)

**Interfaces:**
- `check_for_alerts(db_path: str) -> list[dict]` — inspects `orchestrator_runs`/recent `quant_summary.json` history for the two named conditions (a module's `warnings` growing across consecutive runs for the same ticker; a status flip `ok` → `degraded`/`error`), writes new rows to `quant_alerts`, returns what it found. Pure detection — **must not trigger a new suite/orchestrator run under any circumstance** (Global Constraints).
- `scripts/quant_alert_check.py` — `if __name__ == "__main__": check_for_alerts(default_db_path())`, nothing else; this is what a Windows Task Scheduler entry invokes, not `CronCreate` (spec-mandated, verified in CARL R1).
- Document (in this module's docstring, not automated by this plan) the `schtasks`/Task Scheduler setup command an operator runs once to install the recurring check — installing the actual scheduled task on the user's machine is an out-of-band manual step, not something this plan's code does for them.

- [ ] **Step 1: Write failing tests** — fixture `orchestrator_runs` rows/summary files trigger the two named conditions correctly; a run with no `warnings` growth and no status flip produces zero new alert rows; running twice against the same underlying condition doesn't duplicate rows (idempotent detection).
- [ ] **Step 2: Add the migration.**
- [ ] **Step 3: Implement `check_for_alerts` + the CLI entry point.**
- [ ] **Step 4: Run tests to verify they pass.**
- [ ] **Step 5 (manual): run `scripts/quant_alert_check.py` once by hand against real data, confirm a row lands in `quant_alerts` when expected.**

### Task 15: Alerts in `GET /quant` + banner UI

**Files:**
- Modify: `dashboard/app.py` (`GET /quant`'s response, from Task 6)
- Modify: `dashboard/templates/quant.html`
- Test: extend `dashboard/tests/test_quant_modules.py` or a new file

**Interfaces:**
- `GET /quant`'s response gains a top-level `alerts` field — unacknowledged rows from `quant_alerts`, most recent first.
- Template renders a dismissible banner/badge when `alerts` is non-empty; dismissing calls a small new endpoint (`POST /alerts/{id}/ack`) that sets `acknowledged=1` — don't delete the row, keep it for history.

- [ ] **Step 1: Write failing tests** — `GET /quant` includes pending alerts; acknowledging one removes it from subsequent responses but leaves the DB row intact.
- [ ] **Step 2: Implement.**
- [ ] **Step 3: Run tests to verify they pass; manually confirm the banner renders and the last-check timestamp (Error Handling's "stalled watcher is discoverable" requirement) is visible somewhere in the UI.**

### Task 16: In-page browser notification on completion

**Files:**
- Modify: `dashboard/templates/quant.html` (frontend JS only — no backend change)

**Interfaces:**
- When a polled run or dispatch job transitions to `done` while its card is on screen, request `Notification` permission (once, on first use) and fire a browser notification with the run's headline. This is the one channel `dashboard/app.py`'s backend doesn't need to be involved in at all — pure frontend, fires only while the tab is open, which is the accepted limitation the spec names explicitly.

- [ ] **Step 1: Implement**, gated behind a permission check (don't spam a permission prompt if the user already denied it).
- [ ] **Step 2: Manually verify** in a real browser — trigger a run, background the tab, confirm the notification fires on completion.

---

## Deferred / Not In This Plan

- `explain`'s outbound-request-cap **enforcement mechanism** (spec's one accepted open item) — needs its own short investigation (does `claude -p` expose a tool-call-count limit, or does this need an egress proxy) before it can become a task. Do this before shipping Phase 2's `explain` action to real use, not necessarily before Phase 1/most of Phase 2 lands.
- Fixing Options_Suite's context-mode stub itself — out of scope; Task 5/6 just render its current state correctly, they don't fix it.
- Deleting `.claude/skills/vol-suite.py`/`vol-suite-viewer.py` — flagged at the Phase 1 checkpoint (Task 6) as ready to delete once `/quant` subsumes them, but left as a human decision, not silently automated by this plan.
