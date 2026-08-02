# Quant Console — Design

## Context

This repo's dashboard (`dashboard/app.py`, FastAPI, `:8787`, localhost-only) already
triggers suite/orchestrator runs (`POST /run/{suite_or_unified}`) and polls their
status (`GET /runs/{run_id}`), backed by an in-memory registry plus the
`orchestrator_runs` table in `swaps.db`. Run-triggering already requires
`DASHBOARD_API_KEY` via `verify_api_key`, and is rate-limited to 1/60s per IP.

Separately, `.claude/skills/vol-suite.py` / `vol-suite-viewer.py` are dead-end
scaffolding: they scrape a Vol_Suite run's CSV/PNG output directory, build a
metric-card/tab HTML dashboard, and `print()` it to stdout — it never renders
anywhere.

A sibling project (`Financial_Development`, WSL, driven by a separate agent CLI
called Hermes, not Claude Code) has already built and iterated on a working
version of the thing this spec is chasing: a "Quant" plugin in a desktop app,
with per-suite module cards (run + job status + structured result), worker-
action buttons ("ask coder to investigate", "ask research to explain", "ask
Quant to interpret", "send to personal-bot watchlist"), and a live-scrolling
terminal panel for continuous processes. Its own design doc
(`docs/superpowers/specs/2026-08-01-quant-profile-orchestrator-design.md` in
that repo) is a useful reference for problems it already solved — worker
dispatch safety, error taxonomy, job idempotency — but its broker/bearer-token
auth model exists to secure a *separate untrusted desktop-app process* calling
into a bridge. That threat model doesn't apply here: everything in this spec
runs inside the one already-loopback-bound, already-API-key-gated FastAPI
process this repo already has.

Also relevant: that sibling project's own iteration moved *away* from
"pop open a browser tab per run" toward embedding structured results directly
in the persistent plugin UI. This spec follows that lesson rather than
resurrecting the browser-popup pattern.

## Goals

1. Every suite/orchestrator run produces one structured, schema-validated
   summary report (`quant_summary.json`) in its `orchestrator_output/<run_id>/`
   directory, in addition to the existing per-suite result JSONs.
2. The dashboard gains a module-card view (one card per suite + orchestrator
   unified) that triggers runs via the *existing* run/poll endpoints and
   renders `quant_summary.json` inline once a run completes — retiring the
   dead `vol-suite.py`/`vol-suite-viewer.py` print-to-stdout path.
3. From a completed run's card, the user can dispatch a headless Claude Code
   worker ("interpret" / "investigate" / "explain") whose output is written
   back as another structured report and rendered with provenance (which
   worker, when) in the same card.
4. Continuous processes (sentiment-scanner's loop mode) get a live-updating
   log panel in the dashboard.
5. A scoped `PushNotification` fires only for orchestrator `--unified` runs
   and worker-dispatch completions — not per-suite, not per-click.

## Non-Goals

- No new auth/broker system. Reuse `verify_api_key` and the existing rate
  limiter for every new endpoint in this spec.
- No replacement of `orchestrator.py`'s dependency graph, `suite_context.json`
  contract, or the five existing inter-suite JSON schemas — `quant_summary.json`
  is additive, not a replacement.
- No autonomous/unattended re-running of suites to "confirm a hypothesis."
  Worker dispatch is always a direct result of a user clicking a button;
  nothing in this spec schedules new billed ThetaData-backed runs on its own.
- No changes to DTCC/backtesting modules' runnability — they stay whatever
  their current verified state is (see `Tools/registry.py`, `tool-launcher`
  skill) until a verified runnable command exists for them.
- Phase 3's proactive/"watchlist" piece is deliberately left under-specified
  in this document (see Open Questions) rather than force-fitted into a
  design that doesn't actually resolve its core constraint.

## Architecture Overview

```
Browser (dashboard UI)
   │
   ├─ GET  /quant                      module-card list (new)
   ├─ POST /run/{suite_or_unified}     existing — triggers a run
   ├─ GET  /runs/{run_id}              existing — poll status/result
   ├─ GET  /runs/{run_id}/summary      new — quant_summary.json once written
   ├─ POST /runs/{run_id}/dispatch/{action}   new — worker action
   ├─ GET  /runs/{run_id}/dispatch/{job_id}   new — poll worker job
   └─ WS   /suites/{suite}/live        new — log-tail for loop-mode processes
```

`quant_summary.json` is produced by a new `shared/summary.py::build_run_summary()`,
called at the end of `_execute_run()` (dashboard/app.py) once the suite/
orchestrator subprocess exits — reading whichever `*_result.json` files exist
in that run's output directory and reducing them to the per-module headline/
metrics/warnings shape below, plus a raw-passthrough fallback when a result is
missing or malformed.

## Phase 1 — Foundation

**`shared/schemas.py` addition: `quant_summary` schema, `schema_version: 1`.**

| Field | Type | Notes |
|---|---|---|
| `schema_version` | int | `1` |
| `run_id` | str | Matches `orchestrator_runs.id` / in-memory run key |
| `ticker` | str | From the run's focus |
| `created_at_utc` | str (ISO) | |
| `modules` | object[] | One entry per suite that ran |
| `modules[].module` | str | `vol` \| `options` \| `var` \| `sentiment` |
| `modules[].status` | str | `ok` \| `error` \| `degraded` |
| `modules[].headline` | str | One-line summary, e.g. "IV rich vs. fair vol by 4.2pts" |
| `modules[].metrics` | object | Small flat key/value set for card display |
| `modules[].warnings` | string[] | e.g. "basket weights defaulted to 1.0" |
| `modules[].source_result` | str | Filename of the `*_result.json` this was built from |

`degraded` (not `error`) is used when the result file exists but couldn't be
fully parsed into headline/metrics — matches the sibling project's own
already-proven vocabulary rather than inventing a new one.

**Extractors.** One small function per suite in `shared/summary.py`, each
`(result_dict) -> ModuleSummary`. Vol_Suite's extractor ports the existing
metric logic from `.claude/skills/vol-suite.py::extract_metrics_from_run` /
`generate_dashboard_html` (gamma records, correlation pairs, variance-swap
summary) instead of writing new extraction from scratch; Options/VaR/Sentiment
extractors are new but small, given their result schemas are already
documented in `shared/schemas.py`.

**Dashboard module-card view (`GET /quant`, new template).** Lists modules the
same way the sibling project's `MODULE_REGISTRY` does — id, name, which
`suite`/`focus` it maps to, and a `runnable` flag — reusing the *known* current
state from this repo's own `tool-launcher` skill (e.g. `options` is
documented as currently FAIL in context mode; the card shows that state
rather than hiding it). Each card: ticker input, Run button (→ existing
`POST /run/{suite_or_unified}`), status (→ existing `GET /runs/{run_id}`
polled client-side), and once `done`, a fetch of `GET /runs/{run_id}/summary`
rendered as headline + metric grid + warnings — reusing the tab/metric-card
CSS already written (and currently wasted) in `vol-suite.py`'s
`generate_dashboard_html`.

No new job-tracking backend. This phase adds a summary-writer and a
read/render layer on top of infrastructure that already exists.

## Phase 2 — Worker Actions

**`POST /runs/{run_id}/dispatch/{action}`**, `action ∈ {interpret, investigate, explain}`,
gated by the existing `verify_api_key` dependency and a new
`@limiter.limit` bucket (separate from the run-trigger limiter, since these
are cheaper/faster operations that shouldn't share the 1/60s budget).

Request validates `run_id` exists and is `done`. Dispatch:

1. Builds a prompt referencing `quant_summary.json` + the run's `*_result.json`
   files by path (not inlined — the worker reads them itself), labeled
   explicitly as evidence/data, not instructions (artifact text is untrusted
   input to the worker prompt, same posture as the sibling project's spec).
2. `interpret` / `explain`: read-only. Spawns
   `claude -p "<prompt>" --output-format json` as a subprocess with `cwd` set
   to the repo root, tracked via the same `_RUNS`-style registry used for
   orchestrator jobs (tag `run_type='dashboard:worker:{action}'` in
   `orchestrator_runs`, reusing the existing free-form `run_type` column
   rather than a new table).
3. `investigate`: same, but `cwd` is a freshly created `git worktree add`
   checkout (this repo's `using-git-worktrees` pattern), and the prompt
   explicitly instructs no commit/push — diff is left in the worktree for the
   user to review/apply manually. Matches the sibling project's own
   already-reviewed default for code-touching dispatch.
4. Worker process writes its own structured report,
   `orchestrator_output/<run_id>/quant_worker_<action>_<job_id>.json`
   (`{schema_version, worker: "claude", action, job_id, status, headline,
   detail, worktree_path (investigate only), created_at_utc}`), via temp-file +
   atomic rename (avoids a half-written file being read mid-poll).
5. Idempotency: dispatch accepts an optional client-supplied `idempotency_key`;
   a repeat with the same key against the same `run_id`+`action` returns the
   existing job rather than launching a duplicate — same rationale as the
   sibling spec (double-click safety), implemented as a small in-memory dict
   keyed on `(run_id, action, idempotency_key)`.

**`GET /runs/{run_id}/dispatch/{job_id}`** polls status the same shape as
`GET /runs/{run_id}` already does (`queued`/`running`/`completed`/`failed`,
`done` boolean, stdout tail, result once written).

Concurrency/timeout: one explicit timeout per action type (`interpret`/`explain`
short, e.g. 5 min; `investigate` longer, e.g. 20 min), a max-concurrent-workers
cap (e.g. 2) enforced in the dispatch endpoint, and process-group kill on
timeout so a hung `claude -p` subprocess can't linger.

## Phase 3 — Live Panel + Proactive Layer

**Live panel.** `WS /suites/{suite}/live` tails that suite's current log file
(sentiment-scanner's loop-mode stdout, redirected to a file the same way
dashboard job logs already are) and streams new lines to connected clients.
Genuinely new — the dashboard has no websocket route today.

**Proactive layer (open, not locked in this pass).** The sibling project's
"send to personal-bot watchlist" maps to: periodically inspect
`orchestrator_runs` / recent `quant_summary.json` history for a condition
worth surfacing (e.g. a module's `warnings` growing across consecutive runs,
or a status flip from `ok` to `degraded`), and notify only then. The
unresolved piece: this requires *something* running unattended to do the
inspecting, and `PushNotification` is only callable from an active (or
`CronCreate`-scheduled) Claude Code session — there's no bare-cron path to it
the way there is for the rest of this spec's Python-only pieces. This phase
is intentionally left as a follow-up design decision (see Open Questions)
rather than speced against an assumption that might be wrong.

## Data Flow

```
User clicks Run on a module card
  → POST /run/{suite_or_unified}          [existing]
  → orchestrator/suite subprocess runs, writes *_result.json  [existing]
  → _execute_run() calls build_run_summary() → quant_summary.json  [new]
  → card polls GET /runs/{run_id}, then GET /runs/{run_id}/summary  [new]
  → card renders headline/metrics/warnings inline
  → (long/unified runs only) PushNotification fires once done
User clicks a worker action on a completed card
  → POST /runs/{run_id}/dispatch/{action}  [new]
  → claude -p subprocess (worktree-isolated if investigate)  [new]
  → writes quant_worker_<action>_<job_id>.json  [new]
  → card polls GET /runs/{run_id}/dispatch/{job_id}, renders with provenance
  → PushNotification fires once done
```

## Error Handling

- Missing/malformed `*_result.json` → that module's summary entry gets
  `status: degraded`, `headline` names the missing source; the card falls
  back to showing the raw result file link rather than crashing the whole
  summary render.
- Worker dispatch timeout → full process group killed, job marked
  `timed_out`, partial stdout retained as diagnostic evidence (not discarded).
- Worker dispatch failure (non-zero exit) → job marked `failed`; the run's
  `quant_summary.json` and existing suite results are untouched — a failed
  worker never invalidates the underlying analysis.
- Duplicate idempotency key → returns the existing job, never launches a
  second one.
- `investigate` worktree left behind after a crash → orphan worktrees are
  identifiable by a `quant-worker-` branch/dir prefix and can be swept by a
  manual `git worktree list` / `git worktree remove`; no automatic cleanup in
  this phase (explicit non-goal — automatic deletion of a worktree containing
  an uncommitted diff is exactly the kind of destructive default this repo's
  own operating conventions avoid).

## Security

- All new endpoints bind to the existing loopback-only FastAPI process — no
  new listening port, no new process.
- All new *mutating* endpoints (`dispatch`) require the existing
  `DASHBOARD_API_KEY` via `verify_api_key`, same as run-triggering already
  does. Read endpoints (`summary`, dispatch status poll) stay open to the
  same degree the rest of the dashboard's read surface already is.
- `investigate` dispatches never commit or push by default; applying its diff
  to the main working tree is a separate, manual, human action.
- Worker subprocess environment: same `child_env` scrubbing pattern
  `_start_job`-equivalent code already needs (strip `PYTHONPATH`/`VIRTUAL_ENV`
  contamination — this repo's `.bat` launchers already do this defensively
  for the Hermes-venv case; the same discipline applies to worker subprocess
  env construction).
- Worker prompts must not have `.env` contents read into them; report files
  must not contain secrets. No new secret-scanning infrastructure is being
  built in this phase — this is a stated constraint on prompt construction,
  not a new automated check, since the source data (suite result JSON) has no
  legitimate reason to ever contain credentials in the first place.

## Testing

- `shared/summary.py` extractors: unit tests against canned `*_result.json`
  fixtures (including malformed/missing-field cases) — no network required.
- `quant_summary` schema validator: unit tests mirroring the existing pattern
  for the other five schemas in `shared/schemas.py`.
- Dispatch endpoint: unit tests with `subprocess.Popen` mocked — cover
  authorized/unauthorized (missing API key), duplicate idempotency key,
  timeout-kills-process-group, and non-loopback-origin rejection.
- One manual smoke test: a real `interpret` dispatch against a fixture run
  directory, confirming the round trip (prompt → subprocess → structured
  report → rendered card).
- `pytest -m unit` continues to require no network/credentials for all of the
  above; the smoke test is explicitly excluded from that marker.

## Open Questions / Deferred Decisions

1. **Phase 3 proactive layer's execution substrate** — cron-invoked plain
   Python (can inspect data, can't call `PushNotification`) vs.
   `CronCreate`-scheduled Claude Code agent (can notify, has real cost/rate-
   limit exposure if it ever decides to act rather than just observe). Needs
   its own decision before Phase 3 is implementable, not assumed here.
2. **Options_Suite's context-mode stub** (`tool-launcher` skill: currently
   FAIL, not PASS, per `_build_options_result`) means the Options module
   card will show `degraded`/`error` summaries until that's fixed
   independently of this spec — not this spec's problem to solve, but worth
   the user knowing it'll be visibly broken in the UI on day one.
3. **Whether `explain` (research-style, external context) needs network
   access this subprocess model doesn't currently scope one way or the
   other** — deferred to Phase 2 implementation planning.

## Self-Review

- No unresolved placeholder markers.
- Phase boundaries, endpoint list, and data-flow diagram agree with each
  other (checked field-by-field against the schema table above).
- Explicitly distinguishes what already exists in `dashboard/app.py` (run
  trigger, polling, API-key auth, rate limiting) from what's net-new, so
  implementation planning doesn't re-build existing infrastructure.
- Non-Goals section directly addresses the two biggest risks raised during
  ideation: no unattended re-running of billed suites, no new auth model
  where the existing one already covers the threat.
- Phase 3's proactive layer is left genuinely open rather than speced against
  guessed constraints — flagged in Open Questions, not buried.
