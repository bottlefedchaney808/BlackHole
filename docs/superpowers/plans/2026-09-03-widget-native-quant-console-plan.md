# Widget-Native Quant Console — Plan

Date: 2026-09-03
Spec: `docs/superpowers/specs/2026-09-03-widget-native-quant-console-design.md`
Status: Plan only — no implementation started. Multi-week, phased, checkpoint after each phase.

Read the spec first — this doc is sequencing/tasks only, the "why" and "what" live there.

Ground rule for every phase below: this repo's fragile-surface list in `CLAUDE.md` still
applies throughout — in particular, Options_Suite's default pricing method must stay
Leisen-Reimer (not CRR), SPXW-vs-SPX root aliasing must be preserved in any new module wiring,
and ThetaData mass pulls must stay rate-limited. Run
`tests/test_orchestrator_market_signals.py` + `VaR_Tools_Simulations/tests/test_context_builders.py`
before and after Phase 7 (they test the exact mechanism being replaced) — don't delete them
until their Context Store equivalents exist and pass.

---

## Phase 0 — Inventory & guardrails (before touching any code)

Goal: know exactly what breaks if orchestrator launching disappears, before it disappears.

1. Grep the **whole repo, not scoped to a named-example list** — `orchestrator.run_unified`,
   `orchestrator.run_suite`, `--unified`, `--suite` CLI usage. Known candidates to check
   (not an exhaustive stopping point): `backfill.py`, `scheduled_ingest.py`, `poll_ingest.py`,
   any `schedule`-skill cron configs, `tests/`, CI workflow files, `.bat`/`.sh` entrypoints,
   `.claude/skills/*/SKILL.md` docs referencing these flags. **Confirmed hit outside the
   FinancialDevelopment dashboard entirely**: `blackhole_investments/tools/market_tools.py`'s
   `run_suite` MCP tool (a Claude Agent SDK tool server in a sibling project) subprocesses
   `[SHARED_PYTHON, "orchestrator.py", "--unified"/"--suite", ...]` directly — verified by
   reading the file (2026-09-03 CARL review). This is exactly the kind of caller a
   dashboard-scoped search misses; do the grep repo-wide, including sibling top-level
   directories, not just under `dashboard/`/`Vol_Suite/`/etc.
2. For each real caller found, write down its replacement call path (most will become a direct
   `run_selected_modules(all_slugs, context)` call or a suite's own `--context` mode — decide
   per-caller, don't batch-assume). For `blackhole_investments/tools/market_tools.py`
   specifically: point its `run_suite` tool at `POST /api/widgets/{slug}/run` (Phase 3) over
   HTTP instead of a CLI subprocess — it already treats this repo as an external service, so
   swapping subprocess-exec for an HTTP call is a like-for-like replacement, not a redesign of
   that tool.
3. Snapshot current test coverage for the pieces being replaced:
   `tests/test_orchestrator_market_signals.py`, `VaR_Tools_Simulations/tests/test_context_builders.py`,
   any `dashboard/tests/test_*` covering `trigger_run`/`_execute_run`/`GET /runs/*`/
   `WS /suites/*/live`. These get superseded, not silently dropped — track what needs an
   equivalent test against the new Context Store / widget-run API.
4. Confirm current `Vol_Suite/module_registry.py` "pipeline_step" markers (`group_screener`,
   `vol_surface_2d`, `vrp_term_structure`, `sentiment_backtest`) — these share local state
   inside one `_run_core_analysis` call and can't be split without either duplicating that
   state or reintroducing the documented "two-vanna" double-count bug. Decide explicitly per
   marker: extract to a real standalone widget (do the state-duplication work) vs. leave as a
   composite "run full Vol_Suite core analysis" widget that stays one opaque call. Do not
   attempt this decomposition inside a later phase without having made the call here.
   **Default if this isn't explicitly revisited with Jason during Phase 0**: leave all 4 as
   composite markers — the code's own existing comment already documents the state-duplication
   cost and two-vanna regression risk of splitting them, so composite-by-default is the safe,
   cheap choice. Only decompose a specific marker later if a widget design genuinely needs it
   standalone.

Exit criteria: a written inventory (add it as an appendix to this plan doc) of every non-dashboard
orchestrator caller with a replacement path, and an explicit decision on the 4 pipeline-step
markers. No code changes yet.

---

## Phase 1 — Context Store (`shared/context_store.py`)

Goal: the live, queryable replacement for `_thread_vol_stats_into_context`, generalized to any
module's `context_patch`, keyed by scope, independent of execution order.

1. New table in `artifacts/widget_cache.db` (reuse the file, add a table — don't spin up a
   second SQLite file): `context_entries(scope_key, entry_key, value_json, source_slug,
   computed_at)`. `scope_key` is a normalized string from `{ticker, expiry, basket}` (define
   the normalization once — e.g. `ticker` alone, `ticker|expiry`, or `basket:<sorted-ticker-csv>`
   for basket-scoped entries). **Concurrency, not optional**: `dashboard/widget_cache.py`
   today opens a bare per-call `sqlite3.connect` with no WAL journal mode and no
   `busy_timeout` (confirmed by reading the file, 2026-09-03 CARL review) — fine for its
   current low-write-volume background-tick usage, but Phase 3 makes *every* widget run write
   to this same file (both the widget_cache row and any `context_patch`), which is exactly the
   racing-writers shape `CLAUDE.md` documents as a real, previously-hit "database is locked"
   failure mode (for `swaps.db`, solved there via `shared/connection_pool.py`'s WAL-mode
   thread-safe pool). Route both the generalized `WidgetCache` and `context_store.py` through
   that same pooled-connection pattern (or, at minimum, enable `PRAGMA journal_mode=WAL` and a
   `busy_timeout` on connect) rather than reusing `widget_cache.py`'s current bare-connect
   approach unmodified.
2. `shared/context_store.py`: `get(scope, key, max_age_s=None) -> value | None`,
   `put(scope, key, value, source_slug)`, `describe(scope) -> list[{key, source_slug, computed_at, age_s}]`
   (backs the provenance endpoint). **Audit-trail decision, explicit, not implicit**:
   `shared/context_audit.py` (the repo's existing hashed/rollback-capable context-mutation
   audit primitive) writes its records into `orchestrator_runs.results_json` today — confirmed
   by reading the file. Phase 7 retires the code path that produces those `orchestrator_runs`
   rows. Before Phase 1 is called done, explicitly decide: (a) Context Store's `put()` gets its
   own audit-trail table reusing `context_audit.py`'s hash/snapshot format (if that
   observability capability should survive), or (b) it's a deliberate, documented regression
   (if not). Don't let this fall out silently when Phase 7 deletes `orchestrator_runs` writers —
   record the decision in this plan's appendix either way.
3. Migrate `VaR_Tools_Simulations/main.py::_resolve_vol_and_quality`/`_resolve_drift_and_quality`
   to call `context_store.get(...)` first (same fallback chain: context → own GARCH/return
   estimate → fixed default), replacing their current dependence on a context dict that only
   orchestrator populated. Keep the `(value, source)` return shape — that provenance tuple is
   good, generalize the *source* of the "context" branch, not the shape.
4. Any `ModuleSpec.run()` that already returns a non-empty `context_patch` (dealer_exposure,
   dealer_flow, Vol_Suite's vol-stats-producing modules) gets its `context_patch` written into
   the store automatically by the widget-run API built in Phase 3 — no per-module change needed
   beyond confirming their `context_patch` keys are named usefully (`vol_stats`,
   `correlation_matrix`, `garch_conditional_vol`, not suite-internal names).
5. Tests: round-trip get/put, TTL/staleness (`max_age_s` expiry), scope-key normalization edge
   cases (ticker-only vs ticker+expiry vs basket), and a direct port of whatever
   `test_context_builders.py` covers today re-pointed at the new store.

Exit criteria: `context_store.py` fully tested standalone, VaR's soft-dependency resolvers
consume it, `test_orchestrator_market_signals.py`'s intent (VaR sees real vol stats when
available) passes against the new mechanism — orchestrator's sequencing not yet removed, this
phase just builds the replacement alongside it.

---

## Phase 2 — Unify the module contract

Goal: one contract (`ModuleSpec`/`ModuleResult`), real population for every suite, no more stub
aliasing or lossy Tools adapter.

**This phase is split into 2a and 2b with separate checkpoints — do not treat it as one
uniform-risk block.** 2a is cheap, mechanical, low-risk (dataclass fields, adapter migration).
2b is real financial-model integration work across 16+ currently-stubbed/absent call paths
(7 of Options_Suite's 9 modules are confirmed, by direct source read, to be
`return _run_leisen_reimer(context)  # stub` today; VaR's `MODULES` list is confirmed empty) —
each one needs a genuinely correct test, not a wiring checkbox, and is the single most likely
place in this whole plan to blow the schedule. Budget 2b as its own milestone with its own
go/no-go, not a line item inside 2a's estimate.

### 2a — Contract + mechanical migration

1. Extend `ModuleSpec` with `description`, `inputs` (an `InputSpec` — ticker/expiry/basket each
   `required|optional|none`), `output_kind` (`chart|metrics|distribution|surface|table`),
   `sample` (static example payload for the catalog card). Backward-compatible: give every new
   field a default so existing `ModuleSpec` construction call sites don't all need touching in
   one commit.
2. Migrate `Tools/registry.py`'s `ToolSpec` entries into real `ModuleSpec`s (their own
   `context_patch`/`artifacts`, not `from_tool_spec`'s lossy `[]`/`None`). Once every `ToolSpec`
   has a `ModuleSpec` equivalent, `Tools/registry.py` and `from_tool_spec` are deletable —
   don't delete until every current `tools_*.html` page's functionality is confirmed reachable
   through the new path (Phase 4 replaces those pages; don't remove the old ones before the new
   ones work).

Exit criteria (2a): every `ModuleSpec` (including migrated `ToolSpec`s) has real preview
metadata; no behavior change to any currently-real module's `run()`.

### 2b — Real per-suite model wiring (highest-risk phase in this plan)

1. **Options_Suite**: replace `Options_Suite/module_registry.py`'s stub aliasing with real
   per-model `run` functions — wire each of `crr`, `newton_raphson_iv`, `sabr`, `vanna_volga`,
   `mc`, `mc_heston_lsm`, `baw`, `model_comparison` (currently all aliased to LeisenReimer's
   output) to the actual pricing logic (there's already a real, tested implementation to call
   into — `Options_Suite/main.py::run_context_mode` for LeisenReimer, `chain_evaluation.py`/
   `reports.py` for the multi-model comparison path). Keep LeisenReimer as
   `default_selected=True` — do not let this become a CRR-default regression (explicit
   `CLAUDE.md` fragile-surface warning).
2. **VaR_Tools_Simulations**: populate the empty `MODULES` list — one `ModuleSpec` per
   `var_engine/*.py` module (`corr_sim`, `mc_sim`, `hist_sim`, `copulas`, `forex_var`,
   `cashflow_map`, `stress_test`, `var_agg`, `hedge_optimizer`, `price_dist`), each pulling
   `ticker`/`basket` from the widget's scope, each reading vol/correlation from
   `context_store.get(...)` (Phase 1) with its existing fallback chain. `hist_sim` in particular
   currently has no `Tools/` exposure at all (noted as a gap in the 2026-08-28 overview-widgets
   spec) — this phase is where it finally gets one.
3. Tests: for each newly-real module, a real (not stub) test — assert it produces different,
   *correct* output for at least two different models/modes, not just "doesn't crash." This is
   the phase most likely to surface real bugs in previously-stubbed code paths; budget real time
   for that, don't treat it as mechanical wiring.

Exit criteria (2b, gates Phase 3): `all_modules()` returns a fully real, fully described
catalog — no suite has a stub or empty registry, and every newly-wired module has a passing
correctness test (not just an existence test). **Phase 3 (backend widget API) may not start
against a catalog that still contains stub-aliased entries** — a widget API serving
mislabeled-LR-as-SABR results is worse than the feature not existing yet (silently wrong output,
not a visibly missing one).

---

## Phase 3 — Generic backend widget API

Goal: the `dealer_book_load` pattern, generalized to every slug, with caching and Context Store
integration built in.

1. Generalize `dashboard/widget_cache.py`: `widget_cache(slug, scope_key, payload, status,
   computed_at)` — composite key, not 4 fixed ids.
2. New routes in `dashboard/app.py`:
   - `GET /api/widgets/catalog` — serializes `all_modules()`'s new preview metadata.
   - `POST /api/widgets/{slug}/run` — resolves scope from body, calls
     `resolve_modules([slug])[0].run(context)` synchronously, writes result to the generalized
     cache and `context_patch` to the Context Store, returns `ModuleResult` JSON.
   - `GET /api/widgets/{slug}/state?scope=...` — last cached result, no re-run.
   - `GET /api/context?scope=...` — read-only Context Store provenance view.
3. Retire the 4 fixed Overview background-tick functions (`_widget_signals_tick`,
   `_widget_position_analysis_tick`, `_widget_surfaces_tick`) in favor of the generic run route
   called on-demand or on a light poll — decide per-widget whether it still needs a background
   refresh cadence (e.g. Positions stays agent-pushed via `POST /api/widgets/positions`,
   unchanged) or becomes purely on-demand now that running it is cheap and synchronous.
4. `widget_archive_renderer.py`'s stub dispatch is replaced by `output_kind`-driven generic
   handling — delete the stub once Phase 4's frontend renderers cover its cases.
5. Tests: route-level tests for catalog/run/state/context, cache round-trip with the new
   composite key, and a regression test confirming `dealer_book_load` itself could be
   rewritten on top of this generic route without behavior change (don't rewrite it yet if
   Dealer Book's tab is staying bespoke — just prove the generic path could serve it, as a
   design validation).

Exit criteria: any of the now-fully-real `ModuleSpec`s from Phase 2 can be run and its result
fetched through these 4 generic routes, with no per-widget backend code.

---

## Phase 4 — Frontend widget component + sync bus + catalog UI

Goal: the actual reusable `<quant-widget>` component, the sync toggle, and the preview-first
catalog (the top user-facing priority).

1. `dashboard/static/js/quant-widget.js` (new, first real shared JS module): the
   `<quant-widget slug="...">` custom element — local ticker/expiry/basket inputs, sync toggle,
   run button, calls the Phase 3 routes, renders via `output_kind`.
2. `dashboard/static/js/sync-bus.js` (new): page-global `{ticker, expiry, basket}` store +
   `EventTarget`-based publish/subscribe. Every synced `<quant-widget>` subscribes; toggling
   sync on/off attaches/detaches its listener.
3. Generic renderers (own small module or same file): metrics table, distribution histogram
   (de-duplicate `quant.html`'s `renderDistribution` and `index.html`'s `renderDistChart` into
   this one shared implementation — both exist today and are near-identical copies), image/PNG
   viewer, JSON fallback.
4. Catalog/picker UI: browse `GET /api/widgets/catalog`, show each module's `description`,
   `inputs`, `output_kind`, and `sample` preview *before* adding it to a page — this is the
   feature that directly answers "no way to know what it's going to output."
5. Tests: component-level tests (whatever this repo's frontend test convention is — check for
   existing JS test tooling before introducing a new one) for sync-on/off behavior, and
   route-integration tests confirming the catalog preview data round-trips correctly.

Exit criteria: a widget can be added to a throwaway test page, toggled synced/unsynced, run, and
rendered — fully generic, zero bespoke JS for a standard `output_kind`.

---

## Phase 5 — Layout persistence & page rollout

Goal: make widgets usable "close to anywhere" per Jason's actual scope — Overview and Quant
Console fully customizable, Chart tab gets a bounded side-panel slot, Dealer Book untouched.

1. New `dashboard_layouts(page, widget_instance_id, slug, position, scope_override,
   sync_enabled, config_json)` table + `/api/layout/{page}` CRUD.
2. Rebuild `dashboard/templates/index.html` (Overview) as a real customizable
   `<quant-widget>` grid backed by saved layout — replaces the 4 hardcoded widget divs.
3. Rebuild `dashboard/templates/quant.html` (Quant Console) as the primary hub: catalog
   browser + add/arrange widget instances + a visible Context Store provenance panel
   ("dealer_exposure ran 4m ago, feeding vol_stats into this VaR widget") satisfying the
   cross-widget-feeding requirement as a visible UX, not hidden plumbing. This fully replaces
   the old per-suite launch-card UI — there is no reason to keep those per Jason's explicit
   instruction.
4. Chart tab (`chart_app/` or wherever its template lives — confirm exact file before starting):
   add a bounded side-panel region that can host a small number of `<quant-widget>` instances,
   same layout persistence mechanism, smaller slot count.
5. Retire `tools_*.html` bespoke pages one at a time as their generic widget equivalents are
   confirmed working on Quant Console — don't delete a page until its replacement is verified
   live in the browser (per this repo's UI-change verification convention).
6. Tests: layout CRUD round-trip, page-render tests confirming saved widget instances
   reconstruct correctly on load, and a manual live-browser pass per widget migrated (per
   `CLAUDE.md`'s "test the golden path... in a browser before reporting complete" rule for UI
   work) — take screenshots via the `claude-in-chrome` tools or the `run` skill for each
   migrated page.

Exit criteria: Overview and Quant Console are fully widget-driven and customizable; Chart tab
has a working (if smaller) widget slot; Dealer Book is unchanged and confirmed still working.

---

## Phase 6 — Console agent (natural-language widget selection)

Goal: a text box on Quant Console — describe what you want, the agent picks the right widgets,
runs them, and arranges them in the layout. Gated on Phase 2's catalog actually being rich
(real `description`/`inputs`/`output_kind` per module, not stubs) — a vague catalog produces
vague widget selection, so don't start this before Phase 2 is genuinely done.

**Scope for this phase: select-and-run only.** The agent chooses which existing widgets to run
and with what scope, executes them, and places the results — it does not editorialize on or
interpret the results (no "vol looks rich here, consider X"). Whether to add result-interpretation
is an explicit later decision, not assumed here.

1. New endpoint `POST /api/quant-console/agent` — body `{prompt: str}`. Runs an Anthropic
   Messages API tool-use loop (see the `claude-api` skill for the pattern) where the **tool
   definitions are generated directly from `GET /api/widgets/catalog`** — one tool per
   `ModuleSpec`, its `description`/`inputs` becoming the tool's description/parameter schema.
   No hand-maintained duplicate list of what the agent can do; the catalog is the single source
   of truth for both the human-facing picker (Phase 4) and the agent's tool manifest.
2. Each tool call the model makes → `POST /api/widgets/{slug}/run` (the Phase 3 route) with the
   resolved scope. Cap the number of tool calls per request (e.g. 6-8) to bound cost/blast
   radius from a single prompt — these are real, billed ThetaData calls, not free.
3. Successful runs → written as widget instances into `dashboard_layouts` (Phase 5's API) for
   the current page, tagged with the prompt that produced them, so the UI can show "added from:
   '<prompt>'" per widget rather than making it look like a fixed catalog pick.
4. Credential: reuse (or add) an `ANTHROPIC_API_KEY`-shaped env var in root `.env` — this is a
   new, separate credential path from Claude Code's own session auth; document it in
   `CLAUDE.md`'s "Notable env vars" section as part of this phase.
5. Confirmation step before execution, at least initially: return the proposed widget/scope list
   to the frontend first, require a click to actually run — don't fire-and-forget billed calls
   from a single unconfirmed text submission. Revisit removing this friction only after the
   selection quality has been observed to be reliable.
6. **Aggregate cost ceiling, not just per-request.** The per-request tool-call cap (step 2)
   bounds one submission's blast radius but not the cost of repeated submissions — add a
   session/day-level cap on `/api/quant-console/agent` calls (or total widget-runs it triggers)
   independent of the per-request cap.
7. **This endpoint does not change the dashboard's no-auth posture — if anything, it raises the
   bar for keeping that posture strict.** `CLAUDE.md`'s dashboard section documents zero auth
   as a deliberate, permanent decision, and explicitly warns that a prior `cloudflared tunnel`
   exposure let anyone trigger billed runs — this endpoint turns *free-text* into billed
   ThetaData-backed calls, which is a strictly larger exposure than the existing structured-
   param `POST /run/*` routes. Ship this phase with an explicit line in `CLAUDE.md` reaffirming
   "don't expose this dashboard beyond localhost, especially now that free text can trigger
   billed calls" — do not let Phase 6 quietly widen the existing risk without restating it.

**"What can I ask" reference (explicit requirement, not optional polish):** a page/panel on
Quant Console listing every available request in human terms, generated from the exact same
`GET /api/widgets/catalog` data the agent's tool manifest is built from — same `description`,
same `inputs`, same `sample`. This must be one code path serving both the human catalog browser
(Phase 4) and the agent's tool list, not two lists that can drift apart. If a widget's
`description` isn't good enough to guide a person deciding what to type, it isn't good enough to
guide the agent either — that's a signal to fix the `ModuleSpec` metadata, not to write separate
agent-facing prompting docs.

Exit criteria: a handful of real prompts ("show me SPY dealer positioning and a 10% drawdown VaR
sim", "what's the vol picture on QQQ") reliably select and run the right 2-4 widgets; the
catalog/reference view is live and demonstrably the same data source as the agent's tool list
(not two things kept in sync by hand).

---

## Phase 7 — Retire orchestrator launching machinery

Goal: remove what Phase 0-5 made obsolete, now that everything has a replacement.

1. Remove `orchestrator.py::run_suite`, `run_unified`, CLI `--unified`/`--suite`,
   `_thread_vol_stats_into_context` (superseded by Context Store).
2. Relocate `run_selected_modules`, `_expand_module_requires`, `_topo_sort_modules` out of
   `orchestrator.py` into `shared/module_execution.py` — still used by any widget declaring
   `requires`, now living somewhere that isn't a file about to be deleted.
3. Remove `dashboard/app.py::trigger_run`/`_execute_run`/`_RUNS`/`GET /runs/{id}`/
   `GET /runs/{id}/summary` and the dead `WS /suites/{suite}/live` route.
4. Apply the Phase 0 inventory: update every non-dashboard caller found there to its
   replacement path. Update `.bat`/`.sh` entrypoints and `.claude/skills/tool-launcher/SKILL.md`
   to match. Update this repo's root `CLAUDE.md` (orchestrator command table, architecture
   section, "spine" diagram) to reflect the new reality — this is a required part of this
   phase, not a follow-up.
5. Delete `orchestrator_runs` table usage where it was only for the removed run-tracking (check
   whether anything else reads it, e.g. dashboard analytics pages, before dropping the table
   itself — dropping the table is optional/separate from removing the code that wrote to it).
6. Run the fragile-surfaces reviewer (`.claude/skills/fragile-surfaces-reviewer` /
   the `fragile-surfaces-reviewer` agent) against this diff specifically — this phase touches
   exactly the surfaces it exists to catch regressions in.

Exit criteria: `orchestrator.py` no longer exists as a launcher (or is deleted outright if
nothing else uses the module); full test suite green; dashboard fully functional with zero
references to the removed run-tracking code. **Rollback plan, stated explicitly rather than
left to "git can always revert":** keep this phase's deletions as an isolated commit/PR range
so revert is a one-command operation, and run the dashboard live for at least one full trading
day post-Phase-7 before treating the phase as closed — this phase removes the only path some of
Phase 0's inventoried external callers had, and a regression there surfaces as "a script Jason
runs occasionally silently breaks," not a test failure. Don't consider Phase 7 done at "tests
pass"; consider it done at "tests pass and it survived a real day of use."

---

## Phase 8 — Per-run progress (deferred, lower priority per Jason's ranking)

Goal: live status feedback during a widget run, for the (should be rare, since widgets are
in-process/no-subprocess) case where a run takes long enough to matter.

1. For widgets with a `requires` chain (multi-module), surface per-step status using the
   natural loop boundary already in `run_selected_modules`.
2. Lightweight mechanism first (polling `GET /api/widgets/{slug}/run/{run_id}/status` is
   probably sufficient given everything is in-process and fast) — only reach for SSE/WebSocket
   if a real widget demonstrates it needs push updates.

Exit criteria: at least one genuinely multi-step widget (e.g. `dealer_flow` which `requires`
`dealer_exposure`) shows visible per-step progress in its catalog-added widget instance.

---

## Phase 9 — Heavy widgets, last, only once the system has matured

Goal: per Jason's explicit instruction — do this last, and only if it still makes sense once
Phases 1-8 are live and used for a while.

1. Revisit whether a simplified GEX-4-panel widget and a flow-book widget belong in the
   generic catalog, now that real usage has shown what the generic renderers can/can't handle.
2. Dealer Book's dedicated tab is not replaced by this — at most, a lighter-weight widget
   version becomes addable elsewhere (Overview, Quant Console) alongside the full tab, not
   instead of it.

Exit criteria: explicit go/no-go decision with Jason before writing any code for this phase —
do not start it automatically after Phase 8.

---

## Appendix: Phase 0 inventory

Partially pre-filled by the 2026-09-03 CARL review (which read source directly, not this
plan's own description of it) — treat as a starting point for Phase 0, not a substitute for
actually running it.

- **`blackhole_investments/tools/market_tools.py`** — `run_suite` MCP tool, sibling top-level
  project, subprocesses `orchestrator.py --unified`/`--suite`. Replacement path: point it at
  `POST /api/widgets/{slug}/run` (Phase 3) over HTTP instead of CLI subprocess.
- **`shared/context_audit.py`** — writes audited context-mutation records into
  `orchestrator_runs.results_json`. Not a launcher caller, but a real dependent of the table
  Phase 7 considers dropping usage of. Resolve via Phase 1's audit-trail decision (keep/drop,
  documented) before Phase 7 touches `orchestrator_runs`.
- **`dashboard/quant_alerts.py`** — has an existing, deliberate design decision to *not* join
  through `orchestrator_runs` for output_dir resolution. Worth reading before Phase 7 to confirm
  that decision's reasoning doesn't imply another hidden dependency.
- Still to do in actual Phase 0 execution: `backfill.py`, `scheduled_ingest.py`,
  `poll_ingest.py`, `schedule`-skill cron configs, CI workflow files, `.bat`/`.sh` entrypoints,
  `.claude/skills/*/SKILL.md` — confirm each named candidate one way or the other, don't assume
  from this partial list.

## Phase 0 inventory results (2026-09-03 execution)

**Decision on the 4 pipeline-step markers:** Leave all four
(`group_screener`, `vol_surface_2d`, `vrp_term_structure`, `sentiment_backtest`)
as composite, selection-only markers inside `Vol_Suite/volatility_suite.py`
`run_context_mode` / `_run_core_analysis`. Do not decompose them into standalone
widgets. Rationale: they share local state across gated steps inside one
`_run_core_analysis` call; splitting them would require duplicating that state or
reintroducing the documented "two-vanna" double-count bug. The existing
`_selection_only_marker` guard already raises `NotImplementedError` if any
other caller tries to invoke them standalone.

**Confirmed callers of `orchestrator.run_unified` / `orchestrator.run_suite` /
CLI `--unified` / `--suite` (repo-wide grep, 2026-09-03):**

| Caller / file | Call site / usage | Replacement path (Phase 7) |
|---|---|---|
| `blackhole_investments/tools/market_tools.py` | Subprocess `[SHARED_PYTHON, "orchestrator.py", "--unified"/"--suite", ...]` (lines 101-104) | `POST /api/widgets/{slug}/run` over HTTP (Phase 3) — it already treats this repo as an external service |
| `dashboard/app.py` | `_execute_run` dispatches `orchestrator.run_unified(focus)` and `orchestrator.run_suite(kind, context, ...)` (lines 808-820) | Generic widget-run routes; retire `trigger_run`/`_execute_run`/`_RUNS`/`GET /runs/{id}` per Phase 7 |
| `dashboard/tests/test_quant_alerts.py` | Monkey-patches `orchestrator.run_suite`/`run_unified` to force failures | Update to cover new widget-run routes (Phase 7) |
| `dashboard/tests/test_quant_summary_route.py` | Monkey-patches `orchestrator.run_suite`/`run_unified` | Update/replace with generic route tests (Phase 7) |
| `tests/test_orchestrator_validation.py` | Directly exercises `orchestrator.run_suite` and `orchestrator.run_unified` | Superseded by Context Store / widget-run tests; retain until Phase 7 removes the functions |
| `tests/test_orchestrator_module_selection.py` | Asserts modules path does NOT call `run_suite`/`run_unified` | Keep; invariant still applies to generic run path |
| `orchestrator.py` | Defines `run_suite`/`run_unified`; CLI `--unified`/`--suite` | Remove functions and CLI flags in Phase 7; relocate `run_selected_modules` etc. to `shared/module_execution.py` |
| `orchestrator.bat` / `orchestrator.sh` | Pass `--unified`/`--suite` to `orchestrator.py` | Remove or repurpose entrypoints in Phase 7 |
| `.claude/skills/tool-launcher/SKILL.md` | Documents `--unified`/`--suite`/selectable-modules usage | Update docs in Phase 7 |
| `.claude/skills/fragile-surfaces-reviewer.md` | References `--unified` context threading | Update after Phase 7 |
| `.claude/skills/quant-suite-launch-conventions.md` | Documents `--unified`/`--suite` and selectable modules | Update docs in Phase 7 |
| `.claude/skills/dashboard/SKILL.md` | Mentions orchestrator-level PASS/FAIL | Update docs in Phase 7 |
| `.claude/skills/vol-suite/SKILL.md` | References `_thread_vol_stats_into_context` / `--unified` context threading | Update docs in Phase 7 |
| `.claude/skills/trading-mode/SKILL.md` | References `orchestrator.py --unified` | Update docs in Phase 7 |
| `.claude/skills/sentiment-scanner/SKILL.md` | Mentions `--unified` run context for max_pain | Update docs in Phase 7 |
| `CLAUDE.md` | Command table, architecture spine, `--unified` examples | Update in Phase 7 |
| `docs/guides/*.md` (START_HERE, DEPLOY, CROSS_PLATFORM, etc.) | Document `--unified`/`--suite` usage | Update in Phase 7 |
| `k8s/deployment.yaml`, `k8s/service.yaml` | Reference orchestrator endpoints / `orchestrator_runs` table | Update in Phase 7 |
| `tests/test_context_audit.py` | Tests sentiment fold audit in `run_unified` | Depends on Phase 1 audit-trail decision; revisit in Phase 7 |

**Candidates that do NOT call `run_unified`/`run_suite` (confirmed):**
- `backfill.py`, `scheduled_ingest.py`, `poll_ingest.py` — DTCC ingestion only, no orchestrator usage.
- No `schedule`-skill cron configs found referencing orchestrator launching.
- CI workflow files (none found with orchestrator launching references in repo search).

**Audit-trail decision (Context Store → `context_audit.py`):**
Defer to Phase 1, step 2. The current `context_audit.py` writes into
`orchestrator_runs.results_json`; Phase 7 will remove those writers. Before
Phase 1 is closed, decide explicitly: (a) add a Context Store audit table using
`context_audit.py`'s hash/snapshot format, or (b) document the deliberate
regression. This decision will be recorded here once made.

## Phase 1 audit-trail decision

**Decision: (a) Keep the audit capability.** `shared/context_store.py`
implemented an audit-trail table (`context_store_audit`) reusing the same
hash/snapshot format as `context_audit.py`: every `put()` writes a row with
`scope_key`, `entry_key`, `before_hash`, `after_hash`, `changed_keys`,
`source_slug`, and `created_at`. This preserves the observability capability
that would otherwise silently regress when Phase 7 removes the
`orchestrator_runs` writers.

**Environment variable:** `CONTEXT_STORE_PATH` overrides the default
`artifacts/widget_cache.db` location. This is useful for tests and for any
future configuration, but the default path matches the plan's instruction to
reuse `widget_cache.db` rather than spinning up a second SQLite file.

## Appendix: CARL review ledger (2026-09-03, R1)

Mode A/SPEC, single round, `diversity: reduced` (one model family — Claude — available in this
environment; R1 was a fresh subagent context, not inline self-review). Reviewer verdict:
`NEEDS_REVISION`. All 8 findings independently re-verified against source by the driver before
patching (see conversation for the 3 directly re-checked: `blackhole_investments` caller,
`context_audit.py`→`orchestrator_runs` write, `widget_cache.py`'s bare `sqlite3.connect`).

| ID | Severity | Finding | Disposition | Resolution |
|---|---|---|---|---|
| R1-F1 | major | Phase 0's named-caller list misses `blackhole_investments/tools/market_tools.py` | AGREE | Patched into Phase 0 step 1-2 + appendix |
| R1-F2 | major | Context Store/widget_cache reuse an un-tuned SQLite file (no WAL/busy_timeout), risking the documented "database is locked" failure mode under concurrent widget writers | AGREE | Patched into Phase 1 step 1 |
| R1-F3 | major | `orchestrator_runs` retirement (Phase 7) has an unaddressed dependent (`context_audit.py`'s audit trail) | AGREE | Patched into Phase 1 step 2 (explicit decision required) + appendix |
| R1-F4 | major | Phase 2 bundles cheap mechanical work with expensive, correctness-critical real-model wiring under one risk profile/checkpoint | AGREE | Phase 2 split into 2a/2b with separate exit criteria; Phase 3 explicitly gated on 2b |
| R1-F5 | minor | No existing frontend pub-sub pattern (verification-only, no issue) | AGREE (no-op) | No change needed — claim confirmed accurate |
| R1-F6 | major | Phase 6's console agent lacks an aggregate cost cap and doesn't restate the no-auth exposure risk this repo already documents | AGREE | Patched into Phase 6 steps 6-7 |
| R1-F7 | minor | Phase 0's pipeline-step-marker decision has no stated default if not made in time | AGREE | Patched into Phase 0 step 4 |
| R1-F8 | minor | Phase 7 has no explicit rollback/bake-in criteria beyond "tests pass" | AGREE | Patched into Phase 7 exit criteria |

Convergence: yes (all major/critical findings resolved; no disagreements requiring escalation
to Jason). `Calibration note: all 8 findings were accepted — the driver independently
re-verified the 3 highest-stakes factual claims directly against source (not just trusting R1's
citations) before agreeing, so this is not blind rubber-stamping, but it's worth naming per
CARL's own convention that zero-pushback rounds warrant a explicit note.` No second round run —
findings were concrete, evidenced, and actionable enough that a second round was assessed as
unlikely to surface additional load-bearing issues; revisit with a fresh round if Phase 0/1/2
execution surfaces something these findings didn't anticipate.
