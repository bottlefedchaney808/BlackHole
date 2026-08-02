# Quant Console — Design

**Status:** CARL-converged after R1+R2+R3 (1 minor item accepted as open debt — see Self-Review)

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
5. Completion of orchestrator `--unified` runs and worker dispatches is
   surfaced two ways, neither of which is "automatic" in the sense of firing
   with zero Claude Code or browser involvement — see the self-caught
   correction below: an in-page browser notification (works with the tab
   open, zero Claude Code involvement) and, best-effort, a `PushNotification`
   from an active Claude Code session that happens to be watching (same
   pattern as Phase 3's watchlist alerts, not a separate mechanism).

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

Pending Phase 3 watchlist alerts do not get their own endpoint — they ride
inside `GET /quant`'s existing response as an additional top-level field
(e.g. `alerts: [...]`), read from the `quant_alerts` table/flag file on
every module-card-list request. Stated explicitly here so there's no gap
between Phase 3's prose description and the endpoint contract.

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
gated by the existing `verify_api_key` dependency **and** the new
`require_dispatch_configured` dependency (see Security). Both dependencies
apply to this route and to `GET /runs/{run_id}/dispatch/{job_id}` below —
these are the two routes "require_dispatch_configured" protects; earlier
references to "three" routes in Security/Testing were miscounted and are
corrected there to two. Plus a new `@limiter.limit` bucket (separate from
the run-trigger limiter, since these are cheaper/faster operations that
shouldn't share the 1/60s budget).

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
   runaway cost — see Security. **Enforcement mechanism is an implementation-
   time decision, not asserted here**: this spec does not claim a specific
   `claude -p` CLI flag for capping tool-call counts exists, since that
   hasn't been verified. Candidate approaches to evaluate during Phase 2
   implementation: a CLI flag if one is confirmed to exist, or routing
   `explain`'s subprocess through a local HTTP proxy that enforces an
   allowlist/request-count limit on egress. Whichever is chosen must get its
   own Testing bullet — see Testing.
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

**`GET /runs/{run_id}/dispatch/{job_id}`** (also gated by `verify_api_key` +
`require_dispatch_configured`) polls status the same shape as
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
the failure this control exists to prevent. Concretely: assign the worker
subprocess to a **Windows Job Object** at launch (`CREATE_BREAKAWAY_FROM_JOB`
disabled so descendants stay bound to the job) and terminate the job on
timeout — this is the primary mechanism, chosen over `taskkill /F /T`'s
PID/PPID tree-walk because that walk has a known gap (an intermediate
process exiting before spawning its own child can orphan/reparent that child
out of the tree taskkill would otherwise kill), which matters here precisely
because `claude -p` is agentic and spawning its own subprocesses
unpredictably. `taskkill /F /T /PID <pid>` remains a documented fallback only
if Job Object plumbing proves impractical during implementation, not a
co-equal alternative. **New dependency, named explicitly**: Job Object
lifecycle management needs `pywin32`'s `win32job`/`win32process`/`win32api`
bindings (or equivalent raw `ctypes` calls into `kernel32.dll`, which is more
boilerplate for the same result). `pywin32` is not in root `requirements.txt`
today — it's present in this repo's `.venv` only as an undeclared transitive
dependency of an unrelated package (`mcp`), so this spec adds it as a real,
pinned, direct dependency rather than relying on that incidental presence.

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
- **Notification is inherently best-effort, not real-time**: `GET /quant`
  (Architecture Overview) includes pending alerts in its response, so the
  dashboard renders a banner/badge on every module-card-list load with zero
  Claude Code involvement; a `PushNotification` additionally fires the next
  time an interactive Claude Code session in this repo checks pending
  alerts. Nothing in this spec should treat watchlist alerting as
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
  → (long/unified runs only) browser Notification API fires if tab is open;
    separately, best-effort, if a Claude Code session is actively watching
    (see self-caught correction below Goals) it may PushNotification
User clicks a worker action on a completed card
  → POST /runs/{run_id}/dispatch/{action}  [new]
  → claude -p subprocess (worktree-isolated if investigate)  [new]
  → writes quant_worker_<action>_<job_id>.json  [new]
  → card polls GET /runs/{run_id}/dispatch/{job_id}, renders with provenance
  → same completion-surfacing as above (browser notification + best-effort
    PushNotification, not a guaranteed push)
```

**Self-caught correction (during plan formation, not from a CARL round):**
`PushNotification` is a tool only a live Claude Code agent session can
invoke — `dashboard/app.py`'s backend is a plain uvicorn process with no LLM
in the loop, so it cannot call it directly, no matter how the completion
handler is wired. Every "PushNotification fires" reference in this document
means *best-effort, if an active Claude Code session happens to be watching*
(the same posture Phase 3 already correctly used for watchlist alerts) —
never a guaranteed push from the backend itself. The one channel the backend
*can* drive directly is an in-page browser notification (the standard
`Notification` Web API, or simpler, an audible/visual cue) fired from
frontend JS once a polled job transitions to `done` — that only reaches the
user if the dashboard tab is open, which is an accepted limitation, not a
bug to solve here.

## Error Handling

- Missing/malformed `*_result.json` → that module's summary entry gets
  `status: degraded`, `headline` names the missing source; the card falls
  back to showing the raw result file link rather than crashing the whole
  summary render.
- Worker dispatch timeout → Job Object terminated (see Phase 2, primary
  mechanism; `taskkill /F /T` only as documented fallback), job marked
  `timed_out`, partial stdout retained as diagnostic evidence (not
  discarded).
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
  does — **with one tightening, precisely scoped**: `dashboard/auth.py` today
  silently falls back to a public, hardcoded default
  (`"dev-key-change-in-production"`) if `DASHBOARD_API_KEY` is unset, which
  run-triggering tolerates. Dispatch's blast radius is materially worse — an
  LLM-driven subprocess with filesystem write access and, for `explain`,
  network egress — so a new `require_dispatch_configured` dependency (same
  shape as `verify_api_key`, evaluated per-request, added *only* to the two
  new dispatch/poll routes) returns 503 with a clear "dispatch disabled:
  DASHBOARD_API_KEY not configured" message when the key is unset or equals
  the known default. This must not crash or block startup of the FastAPI
  process as a whole — `dashboard/app.py` has no router/sub-app split today
  (every route is registered directly on `app`), so a process-wide startup
  check would take down the already-working run-trigger/poll/swap-browser
  endpoints over a misconfiguration that only matters for the new dispatch
  surface. Scoping the check to a per-route dependency avoids that.
- **This check only works if `DASHBOARD_API_KEY` actually reaches the
  process's environment**, which it currently does not: `dashboard/app.py`'s
  import chain never calls `shared/config.py::load_env_once()` (verified —
  no `.env`-loading call exists anywhere in `dashboard/app.py` or
  `orchestrator.py` today, unlike `shared/thetadata.py` and the VaR/sentiment
  data loaders, which do call it), and `dashboard.bat` never sets it either.
  Under this repo's own documented convention ("all in the single root
  `.env`"), an operator who sets `DASHBOARD_API_KEY` in `.env` exactly as
  `.env.example` instructs would still see the process read the hardcoded
  default. This spec therefore includes adding `shared.config.load_env_once()`
  near the top of `dashboard/app.py`'s import chain (before `dashboard.auth`
  is imported) as an explicit, required change — not an assumption.
- `investigate` dispatches never commit or push by default; applying its diff
  to the main working tree is a separate, manual, human action.
- `explain`'s network access (see Phase 2) is a real prompt-injection-to-
  exfiltration surface, not just a capability grant: its prompt is built from
  evidence that can include public, attacker-influenceable text (sentiment
  data). Mitigations: a hard structural boundary in the prompt template
  between ingested evidence text and instructions, and the per-job outbound-
  request cap already named in Phase 2.
- Worker subprocess environment: **allowlist, not blocklist**, for all three
  actions. `subprocess.Popen` inherits the full parent `os.environ` unless
  `env=` is explicitly filtered — this repo's `.bat` launchers only strip
  `PYTHONPATH`/`PYTHONHOME` (confirmed: `dashboard.bat` does nothing else to
  the environment), which is not sufficient here. `DASHBOARD_API_KEY` is
  structurally guaranteed to be in the dashboard process's environment
  (`dashboard/auth.py` reads it for every auth check), so every worker
  subprocess's `env=` is built explicitly from a small allowlist (`PATH`,
  `HOME`/`USERPROFILE`, `TEMP`) — `DASHBOARD_API_KEY`, `THETADATA_*`,
  `SWAPS_DB_PATH`, and everything else from root `.env` are omitted by
  construction, not filtered after the fact. `claude -p` is expected to
  authenticate via the operator's own existing Claude Code
  session/credentials (however that's already configured on this machine
  outside this repo), not via a variable this repo's `.env` defines — no
  Anthropic credential is added to the allowlist, since none of this spec's
  three worker actions has a stated need for one and this repo's own
  documented env vars (`CLAUDE.md`) don't include one to begin with.
- `explain` gets network tools (`WebSearch`/`WebFetch`) per Phase 2. Given
  the env-inheritance risk above, `explain`'s dispatch additionally disables
  Bash/shell tool access — it has no legitimate need to run shell commands,
  and combined with network egress, shell access would reopen the exact
  exfiltration path the env allowlist above exists to close (a
  prompt-injected job reading residual environment state and shipping it out
  over `WebFetch`). `interpret`/`investigate` have no network tools, so this
  constraint is specific to `explain`.
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
  authorized/unauthorized (missing/wrong API key), `require_dispatch_configured`
  returning 503 on default/unset key *without* affecting other routes (assert
  `GET /runs/{id}` still serves 200 in the same test), the env allowlist
  actually excluding `DASHBOARD_API_KEY`/`THETADATA_*` from a worker
  subprocess's `env=`, duplicate idempotency key, and timeout-kills-full-
  process-tree. No network-origin test — per Security, origin is not a
  control this design relies on, so there's nothing meaningful to assert
  there.
- One test confirming `load_env_once()` is actually called before
  `dashboard.auth` reads `DASHBOARD_API_KEY`, so the fail-closed check can't
  silently regress to always-default the way it does today.
- Once `explain`'s outbound-request-cap mechanism is chosen (Phase 2), it
  needs its own test proving the cap is actually enforced (e.g. a mocked
  worker making N+1 calls is cut off at N) — not committed to a specific
  test shape yet since the mechanism itself isn't chosen yet.
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

## Revision Notes (CARL R1 + R2 + R3)

Three rounds, single reviewer family throughout (fresh subagent contexts only
— `diversity: reduced`, no alternate model family available in this
environment). Every finding across all three rounds was independently
verified against source before being applied; none required relitigating a
locked decision. R3 was scoped narrowly to auditing R2's fixes rather than
re-reviewing the document from scratch, per CARL's "audit the fix, don't
restart" discipline for later rounds.

**Round 1** (7 findings, all applied):

| ID | Severity | Finding | R1 Resolution | Note |
|---|---|---|---|---|
| R1-F1 | critical | `CronCreate` doesn't support durable unattended watching | Split detection (OS scheduled task)/notification (best-effort) design in Phase 3 | Held through R2 |
| R1-F2 | major | Non-loopback-origin test had nothing to test against; misleading given documented tunnel exposure | Removed from Testing; Security states API key is the only real control | Held through R2 |
| R1-F3 | major | "Process-group kill on timeout" asserted as reuse; no precedent exists and this repo is Windows | Named as new infra; `CREATE_NEW_PROCESS_GROUP` + `taskkill /F /T` specified | **Superseded in R2** — mechanism itself was wrong, see R2-F5 |
| R1-F4 | major | Dispatch reuses run-triggering's auth despite materially larger blast radius, under a key documented to default-fallback | Dispatch router "fails closed at startup" on unset/default key | **Refined in R2** — blast radius and env-loading gap, see R2-F2/R2-F3 |
| R1-F5 | major | `explain`'s network access left unresolved despite being answerable from the spec's own stated purpose | Resolved to yes, scoped to `explain` only, with request cap + named injection surface | **Extended in R2** — env-inheritance angle, see R2-F1 |
| R1-F6 | minor | No status value distinguishes a known limitation from a regression | Added `unsupported` status, driven by `runnable` flag | Held through R2 |
| R1-F7 | minor | Context section overstated `vol-suite-viewer.py`'s role | Reworded to describe it accurately as a separate plain-text CLI | Held through R2 |

**Round 2** (audited all 7 R1 fixes for correctness/completeness, found 6 new
issues — 1 critical, 4 major, 1 minor — all applied):

| ID | Severity | Finding | Resolution |
|---|---|---|---|
| R2-F1 | critical | `explain`'s network grant + default env inheritance = a path to exfiltrate `DASHBOARD_API_KEY` itself via prompt injection, which the R1 env-scrubbing bullet (PYTHONPATH/VIRTUAL_ENV only) didn't cover | Worker env rebuilt as an explicit allowlist for all three actions, not a blocklist; `explain` additionally loses Bash/shell tool access |
| R2-F2 | major | The R1 fail-closed key check depends on `DASHBOARD_API_KEY` reaching `os.environ`, but `dashboard/app.py` never calls `load_env_once()` — an operator following this repo's own documented `.env` convention would still see the default | Adding `load_env_once()` to `dashboard/app.py`'s import chain is now an explicit required change, not assumed |
| R2-F3 | major | "Refuses to start at process startup" was ambiguous — could crash the entire dashboard (including already-working endpoints) over a dispatch-only misconfiguration | Rewritten as a per-route `require_dispatch_configured` dependency scoped to the two dispatch/poll routes only, returning 503 |
| R2-F4 | major | Phase 3's alert banner mechanism was named in prose but had no entry in the Architecture Overview endpoint list, contradicting Self-Review's claim of field-by-field consistency | Explicitly folded into `GET /quant`'s existing response rather than a new endpoint; stated in Architecture Overview |
| R2-F5 | minor | `CREATE_NEW_PROCESS_GROUP` doesn't affect `taskkill /T`'s tree-walk (it governs console signal routing, a different concern), and `taskkill /T` has a known orphan/reparent gap the doc didn't name | Windows Job Object specified as the primary termination mechanism; `taskkill /F /T` demoted to documented fallback only |
| R2-F6 | major | `explain`'s outbound-request cap was asserted with no enforcement mechanism, and was the one named Security control missing from Testing | Enforcement explicitly marked as an implementation-time decision (candidate approaches named, no unverified CLI flag asserted); Testing bullet added once mechanism is chosen |

**Round 3** (narrow audit of R2's fixes only, found 3 new issues — all major, all applied — verdict `NEEDS_REVISION` → resolved):

| ID | Severity | Finding | Resolution |
|---|---|---|---|
| R3-F1 | major | `require_dispatch_configured` (R2-F3's fix) was fully specified in Security/Testing but never actually attached to the dispatch/poll endpoints it's supposed to protect in Phase 2 — an implementer reading only Phase 2 would ship the exact gap it was created to close. Also caught a real miscount: Security/Testing said "three" dispatch/poll routes when there are only two. | Phase 2's two route descriptions now explicitly name both `verify_api_key` and `require_dispatch_configured`; "three" corrected to "two" everywhere it appeared |
| R3-F2 | major | The R2-F1 env allowlist included `ANTHROPIC_API_KEY` with no stated justification — undermining the "allowlist, every entry justified" principle that fix existed to establish, and introducing an undocumented credential this repo's own `.env` conventions don't define | Removed; documented that `claude -p` authenticates via the operator's own existing Claude Code credentials outside this repo, not via a repo-defined env var |
| R3-F3 | major | The R2-F5 Job Object mechanism requires `pywin32` (or raw `ctypes`/`kernel32.dll` calls), which isn't declared in `requirements.txt` and is only present today as an incidental transitive dependency of an unrelated package (`mcp`) | Named explicitly as a new, real, pinned dependency this spec adds — not assumed already available |

**Self-caught during plan formation** (not from any CARL round — found while
converting this spec into an implementation task list, the same way the
sibling `wire-unused-scanners` plan in this repo caught its own gap at that
stage): Goal 5 and the Data Flow section described `PushNotification` as
firing automatically on run/dispatch completion. `PushNotification` is only
callable from a live Claude Code agent session — `dashboard/app.py`'s
backend has no LLM in the loop and cannot invoke it. Corrected throughout to:
an in-page browser notification the backend *can* drive directly (only
reaches the user if the tab is open), plus a best-effort `PushNotification`
if a Claude Code session happens to be actively watching — the same posture
Phase 3 already used correctly for watchlist alerts, now applied
consistently everywhere the document mentions notification.

## Self-Review

- No unresolved placeholder markers; all three original Open Questions carry
  concrete resolutions, not restatements — and R2 checked those resolutions
  for completeness rather than taking R1's fixes on faith.
- Phase boundaries, endpoint list, and data-flow diagram agree with each
  other — this claim was itself wrong after R1 (R2-F4 caught a real gap) and
  is now actually true: the alert-banner mechanism has an explicit home in
  `GET /quant`.
- Explicitly distinguishes what already exists in `dashboard/app.py` from
  what's net-new, and after two rounds is more conservative about it in the
  right direction — R2 found that even the R1-tightened auth check silently
  depended on `.env`-loading behavior that doesn't currently exist, and says
  so rather than assuming it.
- Non-Goals section directly addresses the two biggest risks raised during
  ideation: no unattended re-running of billed suites, no new auth model
  where the existing one already covers the threat — both rounds sharpened,
  neither overturned, that reasoning.
- Security section now treats worker subprocess environment as an allowlist
  problem, not a blocklist problem, and names the specific credential
  (`DASHBOARD_API_KEY`) that a blocklist approach would have left exposed.
- One item remains genuinely open rather than fully resolved:
  `explain`'s outbound-request-cap enforcement mechanism (R2-F6) needs an
  implementation-time decision this document deliberately doesn't guess at,
  since no verified `claude -p` capability was confirmed to exist for it.
  This is accepted minor debt, not a blocker — see Revision Notes.
- R3 specifically checked whether R2's fixes were *wired into* the sections
  an implementer would actually build from, not just declared in Security —
  and found one real case (R3-F1) where they weren't. That class of gap
  (a fix declared in one section but not propagated to the section that
  matters) is exactly what a narrow audit round exists to catch; the fact
  that R3 found three real, verified, non-cosmetic issues after two prior
  rounds is itself the argument for stopping at three rounds rather than
  fewer — and for not assuming R3's own fixes are exempt from the same
  failure mode without a human or CI check confirming Phase 2's route
  descriptions and the implementation code actually agree once built.
