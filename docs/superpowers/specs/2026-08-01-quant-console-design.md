# Quant Console — Design

**Status:** CARL R1-reviewed, revised (see Revision Notes)

## Context

This repo's dashboard (`dashboard/app.py`, FastAPI, `:8787`, localhost-only) already
triggers suite/orchestrator runs (`POST /run/{suite_or_unified}`) and polls their
status (`GET /runs/{run_id}`), backed by an in-memory registry plus the
`orchestrator_runs` table in `swaps.db`. Run-triggering already requires
`DASHBOARD_API_KEY` via `verify_api_key`, and is rate-limited to 1/60s per IP.

Separately, `.claude/skills/vol-suite.py` is dead-end scaffolding: it scrapes a
Vol_Suite run's CSV/PNG output directory, builds a metric-card/tab HTML
dashboard, and `print()`s it to stdout — it never renders anywhere.
`.claude/skills/vol-suite-viewer.py` is a related but separate plain-text CLI
(`outputs`/`show`/`images` subcommands) that formats the same kind of run data
as terminal tables, not HTML — it scrapes the same output directories but has
no dashboard-reusable markup of its own.

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
| `modules[].status` | str | `ok` \| `error` \| `degraded` \| `unsupported` |
| `modules[].headline` | str | One-line summary, e.g. "IV rich vs. fair vol by 4.2pts" |
| `modules[].metrics` | object | Small flat key/value set for card display |
| `modules[].warnings` | string[] | e.g. "basket weights defaulted to 1.0" |
| `modules[].source_result` | str | Filename of the `*_result.json` this was built from |

`degraded` (not `error`) is used when the result file exists but couldn't be
fully parsed into headline/metrics — matches the sibling project's own
already-proven vocabulary rather than inventing a new one. `unsupported` is
distinct from both: it's set deterministically, without attempting
extraction, whenever the module list's `runnable` flag (below) is `False` for
that module — e.g. Options today, per the `tool-launcher` skill's documented
context-mode FAIL. This keeps a known, pre-existing limitation visually and
semantically distinct from a genuine regression in a module that used to
work — a card showing `unsupported` means "not wired up yet," a card showing
`error`/`degraded` means "this broke." Resolves Open Question 2 below.

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
documented as currently FAIL in context mode; `runnable=False` short-circuits
that module's summary straight to `status: "unsupported"` rather than
attempting a run that's known to fail — see the schema note above). Each card: ticker input, Run button (→ existing
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
   This matters concretely for `explain`: sentiment-scanner's inputs
   (Reddit/StockTwits/YouTube, per this repo's own `CLAUDE.md`) can end up
   embedded in `*_result.json` text that becomes part of that "evidence" —
   i.e. attacker-influenceable public text reaching a worker prompt, not a
   hypothetical.
2. Per-action tool/network scoping (resolves Open Question 3 below):
   `interpret` and `investigate` run with no network-capable tools —
   local-repo-only. `explain` ("research-style, external context" is its own
   stated purpose, which has no other source of external context) is the one
   action granted `WebSearch`/`WebFetch`. Because `explain`'s prompt is
   necessarily built from the same untrusted evidence described above, its
   dispatch additionally enforces a hard cap on outbound requests per job
   (e.g. 10) so manipulated input can't be used for bulk exfiltration or
   runaway cost — see Security.
3. `interpret` / `explain`: read-only w.r.t. the repo (network access differs
   per above). Spawns
   `claude -p "<prompt>" --output-format json` as a subprocess with `cwd` set
   to the repo root, tracked via the same `_RUNS`-style registry used for
   orchestrator jobs (tag `run_type='dashboard:worker:{action}'` in
   `orchestrator_runs`, reusing the existing free-form `run_type` column
   rather than a new table).
4. `investigate`: same, but `cwd` is a freshly created `git worktree add`
   checkout (this repo's `using-git-worktrees` pattern), and the prompt
   explicitly instructs no commit/push — diff is left in the worktree for the
   user to review/apply manually. Matches the sibling project's own
   already-reviewed default for code-touching dispatch.
5. Worker process writes its own structured report,
   `orchestrator_output/<run_id>/quant_worker_<action>_<job_id>.json`
   (`{schema_version, worker: "claude", action, job_id, status, headline,
   detail, worktree_path (investigate only), created_at_utc}`), via temp-file +
   atomic rename (avoids a half-written file being read mid-poll).
6. Idempotency: dispatch accepts an optional client-supplied `idempotency_key`;
   a repeat with the same key against the same `run_id`+`action` returns the
   existing job rather than launching a duplicate — same rationale as the
   sibling spec (double-click safety), implemented as a small in-memory dict
   keyed on `(run_id, action, idempotency_key)`.

**`GET /runs/{run_id}/dispatch/{job_id}`** polls status the same shape as
`GET /runs/{run_id}` already does (`queued`/`running`/`completed`/`failed`,
`done` boolean, stdout tail, result once written).

Concurrency/timeout: one explicit timeout per action type (`interpret`/`explain`
short, e.g. 5 min; `investigate` longer, e.g. 20 min), a max-concurrent-workers
cap (e.g. 2) enforced in the dispatch endpoint. **This is new infrastructure,
not reuse**: this repo's only existing subprocess-timeout precedent
(`orchestrator.py`'s `subprocess.run(..., timeout=timeout)`) kills only the
direct child, and this repo runs on Windows, where POSIX process-group kill
(`os.killpg`) doesn't exist. `claude -p` is itself agentic and can spawn its
own tool-call subprocesses, so a naive child-only kill would leave live
descendants (and a locked `investigate` worktree) behind on timeout — exactly
the failure this control exists to prevent. Concretely: launch with
`subprocess.Popen(..., creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)`,
and on timeout kill the whole tree via `taskkill /F /T /PID <pid>` (or a
Windows Job Object for a stronger guarantee).

## Phase 3 — Live Panel + Proactive Layer

**Live panel.** `WS /suites/{suite}/live` tails that suite's current log file
(sentiment-scanner's loop-mode stdout, redirected to a file the same way
dashboard job logs already are) and streams new lines to connected clients.
Genuinely new — the dashboard has no websocket route today.

**Proactive layer.** The sibling project's "send to personal-bot watchlist"
maps to: periodically inspect `orchestrator_runs` / recent `quant_summary.json`
history for a condition worth surfacing (e.g. a module's `warnings` growing
across consecutive runs, or a status flip from `ok` to `degraded`), and
surface it. Resolves Open Question 1 below: `CronCreate` was considered and
rejected as the execution substrate — verified against its own tool contract,
scheduled jobs are session-only (in-memory, gone when the originating Claude
Code session ends) and hard-auto-expire after 7 days regardless of
recurrence, so a watchlist built on it would silently stop working with no
error surfaced anywhere. Detection and notification are split instead:

- **Detection** (durable, no LLM involved): a plain OS-level scheduled task
  (Windows Task Scheduler, not `CronCreate`) runs a small dependency-free
  Python script against `orchestrator_runs`/`quant_summary.json` history and,
  on a match, writes a local alert record (a new small `quant_alerts` table
  or a flag file) — no active session required, nothing expires.
- **Notification is inherently best-effort, not real-time**: the dashboard
  reads pending alerts on page load/poll and renders a banner/badge (works
  with zero Claude Code involvement); a `PushNotification` additionally fires
  the next time an interactive Claude Code session in this repo checks
  pending alerts. Nothing in this spec should treat watchlist alerting as
  time-sensitive — that constraint is stated here explicitly rather than left
  implicit.

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
- Worker dispatch timeout → full process tree killed via `taskkill /F /T`
  (see Phase 2), job marked `timed_out`, partial stdout retained as
  diagnostic evidence (not discarded).
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
- Detection-side failure in Phase 3's watchlist (scheduled task doesn't run,
  can't write its alert record) has no session to report to by definition —
  the only mitigation is the alert table/file's own last-run timestamp being
  visible on the dashboard, so a stalled watcher is discoverable on inspection
  rather than failing silently forever.

## Security

- All new endpoints bind to the existing loopback-only FastAPI process — no
  new listening port, no new process. Loopback binding is a deployment detail,
  **not a security control**: `dashboard/app.py`'s existing `get_client_ip`
  already trusts `X-Forwarded-For`, and `CLAUDE.md` documents this dashboard
  being exposed via `cloudflared tunnel` in practice — in that scenario all
  forwarded traffic legitimately arrives from `127.0.0.1`, so a peer-IP check
  cannot distinguish a local user from a tunneled stranger. The only real
  control for every new mutating endpoint in this spec is possession of
  `DASHBOARD_API_KEY`; nothing here should be built or tested as if network
  origin adds protection.
- All new *mutating* endpoints (`dispatch`) require the existing
  `DASHBOARD_API_KEY` via `verify_api_key`, same as run-triggering already
  does — **with one tightening**: `dashboard/auth.py` today silently falls
  back to a public, hardcoded default (`"dev-key-change-in-production"`) if
  `DASHBOARD_API_KEY` is unset, which run-triggering tolerates. Dispatch's
  blast radius is materially worse than triggering a billed data pull — it's
  an LLM-driven subprocess with filesystem write access and, for `explain`,
  network egress — so the dispatch router refuses to start at process
  startup (not per-request) if the configured key is unset or still equals
  that default string, rather than silently accepting it.
- `investigate` dispatches never commit or push by default; applying its diff
  to the main working tree is a separate, manual, human action.
- `explain`'s network access (see Phase 2) is a real prompt-injection-to-
  exfiltration surface, not just a capability grant: its prompt is built from
  evidence that can include public, attacker-influenceable text (sentiment
  data). Mitigations: a hard structural boundary in the prompt template
  between ingested evidence text and instructions, and the per-job outbound-
  request cap already named in Phase 2.
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
  authorized/unauthorized (missing/wrong API key), the fail-closed startup
  check (default/unset key refuses to start), duplicate idempotency key, and
  timeout-kills-full-process-tree (`CREATE_NEW_PROCESS_GROUP` + `taskkill`).
  No network-origin test — per Security, origin is not a control this design
  relies on, so there's nothing meaningful to assert there.
- One manual smoke test: a real `interpret` dispatch against a fixture run
  directory, confirming the round trip (prompt → subprocess → structured
  report → rendered card).
- `pytest -m unit` continues to require no network/credentials for all of the
  above; the smoke test is explicitly excluded from that marker.

## Resolved Design Decisions (CARL R1)

The three questions this spec originally deferred were resolved during CARL
review rather than left open — each verified against actual tool contracts
or source, not opinion:

1. **Phase 3's execution substrate.** `CronCreate` is not viable for durable
   unattended watching (session-only, 7-day hard expiry, verified against its
   own tool schema). Resolved to a split design: an OS-level scheduled task
   (not `CronCreate`) does detection durably with no LLM involved;
   notification is explicitly best-effort (dashboard banner on next
   view, `PushNotification` on next active session) — see Phase 3.
2. **Options_Suite's day-one broken card.** Resolved by adding a fourth
   `quant_summary` status, `unsupported`, driven deterministically by the
   module list's existing `runnable` flag — distinguishes "not wired up yet"
   from "this broke" instead of showing a generic error — see Phase 1.
3. **`explain`'s network access.** Resolved to yes — `WebSearch`/`WebFetch`
   granted to `explain` only (`interpret`/`investigate` get none), with a
   per-job outbound-request cap and an explicit prompt-injection surface
   named in Security, since `explain`'s evidence can include public,
   attacker-influenceable text — see Phase 2 and Security.

## Revision Notes (CARL R1)

One round, single reviewer family (fresh subagent contexts only —
`diversity: reduced`, no alternate model family available in this
environment). All 7 findings were independently verified against source
before being applied; none required relitigating a locked decision.

| ID | Severity | Finding | Resolution |
|---|---|---|---|
| R1-F1 | critical | `CronCreate` doesn't support durable unattended watching | Split detection (OS scheduled task)/notification (best-effort) design in Phase 3 |
| R1-F2 | major | Non-loopback-origin test had nothing to test against; misleading given documented tunnel exposure | Removed from Testing; Security now states API key is the only real control |
| R1-F3 | major | "Process-group kill on timeout" asserted as reuse; no such precedent exists and this repo is Windows | Named as new infrastructure; concrete Windows mechanism specified (`CREATE_NEW_PROCESS_GROUP` + `taskkill /F /T`) |
| R1-F4 | major | Dispatch reuses run-triggering's auth despite materially larger blast radius, under a key documented to default-fallback | Dispatch router fails closed at startup on unset/default key |
| R1-F5 | major | `explain`'s network access left unresolved despite being answerable from the spec's own stated purpose and untrusted-input posture | Resolved to yes, scoped to `explain` only, with request cap + named injection surface |
| R1-F6 | minor | No status value distinguishes a known limitation from a regression | Added `unsupported` status, driven by `runnable` flag |
| R1-F7 | minor | Context section overstated `vol-suite-viewer.py`'s role (attributed HTML generation it doesn't have) | Reworded to describe it accurately as a separate plain-text CLI |

## Self-Review

- No unresolved placeholder markers; all three original Open Questions carry
  concrete resolutions, not restatements.
- Phase boundaries, endpoint list, and data-flow diagram agree with each
  other (checked field-by-field against the schema table above, including
  the new `unsupported` status and per-action network scoping).
- Explicitly distinguishes what already exists in `dashboard/app.py` (run
  trigger, polling, API-key auth, rate limiting) from what's net-new — and,
  after R1, explicitly flags where a "reuse existing" claim understated the
  actual lift (process-tree kill, auth strictness for dispatch specifically).
- Non-Goals section directly addresses the two biggest risks raised during
  ideation: no unattended re-running of billed suites, no new auth model
  where the existing one already covers the threat — R1 sharpened, but did
  not overturn, that reasoning.
- Security section now names loopback binding as a non-control explicitly,
  rather than implying it contributes protection it doesn't.
