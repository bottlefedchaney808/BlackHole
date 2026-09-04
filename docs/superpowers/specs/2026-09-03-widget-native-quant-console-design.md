# Widget-Native Quant Console — Design

Date: 2026-09-03
Status: Approved (Jason, in-session discussion) — plan only, implementation not started

## Purpose

Retire `orchestrator.py` as the dashboard's launch/rendering mechanism. Every analytical
module (dealer positioning, GEX/VEX/CEX, VRP, screener, VaR sims, IV surfaces, sentiment
scanners, options pricing models, etc.) becomes a **self-contained widget**: its own data
fetch, its own presentation, reusable across dashboard locations. Widgets can be pointed at a
shared "active symbol" (ticker/expiry/basket) via a per-widget sync toggle — a widget's ability
to fetch its own data does not depend on being synced; sync only changes *what* it asks for.
Quant Console stops being a bank of per-suite launch cards and becomes the actual hub: a
catalog you browse, widgets you add, arranged and run interactively, with room for one
widget's output to feed another.

This is a big, multi-week, multi-phase effort. This spec + its paired plan
(`docs/superpowers/plans/2026-09-03-widget-native-quant-console-plan.md`) exist to sequence it
correctly — **no code changes ship from this spec directly.**

## Current state (verified, 2026-09-03)

Three parallel, non-unified "module/widget" concepts exist today, plus one bespoke
per-page rendering convention — they share no data model or render component:

1. **Overview widgets** (`dashboard/widget_cache.py`, `GET/POST /api/widgets/*`,
   `dashboard/templates/index.html`) — a real generic backend cache
   (`widget_cache(widget_id PK, payload, status, computed_at)`) but a **fixed 4 hardcoded
   widget_ids**, each computed by a bespoke `asyncio` background tick
   (`dashboard/app.py:314-558`) calling suite/Tools functions directly, and each rendered by
   hand-copied JS in `index.html` (own poll function, own render function, own `setInterval`
   per widget — confirmed zero shared frontend component).
2. **`shared/module_registry.py`** (`ModuleSpec`/`ModuleResult`/`ArtifactRef`/`ArchiveHint`) —
   the "selectable module" system driving `orchestrator.py --modules` and Quant Console's
   flat-checkbox module picker (`quant.html:134-146`). This is the strongest existing
   contract: `ModuleSpec(name, slug, suite, category, run, requires, archive)`,
   `run(context) -> ModuleResult(status, artifacts, metrics, context_patch)`. Population by
   suite is uneven — **Vol_Suite (14 modules) and sentiment-scanner (7 modules) are real**;
   **Options_Suite (9 modules) is stub aliasing** (`_run_crr` etc. all literally
   `return _run_leisen_reimer(context)  # stub`); **VaR_Tools_Simulations is empty**
   (`MODULES: list[ModuleSpec] = []`, comment: "not in scope for this overhaul's suite
   splits"). `all_modules()` also adapts every `Tools/registry.py::TOOLS` entry via
   `from_tool_spec`, but lossily (`artifacts=[]`, `context_patch=None` always).
3. **`Tools/registry.py`** (`ToolSpec(name, slug, description, run)`, `run(context) -> dict`)
   — the older, simpler plugin surface, one dedicated Jinja template per tool
   (`dashboard/templates/tools_*.html`), each with its own ticker input/fetch/render, fully
   bespoke, zero reuse.
4. **`dashboard/app.py::dealer_book_load`** (`POST /dealer-book/load`, `app.py:1044`) — the
   one already-working reference implementation of the target shape: calls
   `resolve_modules(["dealer_exposure"])[0].run(context)` directly inside the request
   handler, synchronously, no orchestrator run-tracking, no subprocess. This is the pattern to
   generalize, not `trigger_run`/`_execute_run`'s heavier queued/polled model (which exists
   only because subprocess suite runs can take up to 1800s — in-process modules don't need it).

**Execution model today:** `dashboard/app.py::trigger_run` (`POST /run/{suite_or_unified}`)
branches three ways: `modules` present → `run_selected_modules` (in-process, no subprocess,
already widget-shaped); `kind == "unified"` → `run_unified`; otherwise → `run_suite`. Only
`run_suite` (called directly or from inside `run_unified`) spawns a subprocess
(`SHARED_PYTHON` running a suite's `main.py` with `--context`/`--context-out`) — opaque until
exit, no progress streaming (the one WS route that could stream, `WS /suites/{suite}/live`, has
nothing writing to the log file it tails — dead infrastructure).

**The one real, load-bearing thing `--unified` does that nothing else replicates**:
`orchestrator.py::_thread_vol_stats_into_context` mutates the shared context so VaR/Options see
Vol_Suite's real per-basket realized vol, correlation matrix, and GARCH conditional vol instead
of VaR's own fallbacks (`VaR_Tools_Simulations/main.py::_resolve_vol_and_quality`/
`_resolve_drift_and_quality`: prefers context, else its own independent GARCH/return estimate,
else a fixed `0.25`/`0.0` — a graceful, already-built soft-dependency pattern, not a hard
block). **The user's decision: keep this context-sharing capability — it's useful for far more
than VaR — but stop delivering it via a sequential pipeline run. Promote it to a live, queryable
store any widget can read from and write into, independent of execution order.**

There is no discoverable single "modularization overhaul" spec doc — its phases are tracked
only in code comments (`shared/module_registry.py`, `orchestrator.py`, `dashboard/app.py`
reference "Phase 1/6/7/8", "Task 2/3/4/5/6/7/9/10/12/13"). This spec supersedes that informal
tracking with an explicit plan.

## Decisions (confirmed with Jason, 2026-09-03)

1. **Orchestrator launching goes away entirely.** `run_suite` (subprocess dispatch),
   `run_unified`, the CLI `--unified`/`--suite` flags, and the dashboard's
   `trigger_run`/`_execute_run`/`_RUNS`/`GET /runs/{id}` polling machinery are retired. The
   **context-sharing mechanism is kept and promoted** — see "Context Store" below — because
   it's useful beyond VaR (any widget benefiting from another widget's recent output).
   `run_selected_modules` (in-process, already widget-shaped) and its topo-sort/`requires`
   expansion logic survive, relocated out of `orchestrator.py` into `shared/`.
2. **Reuse model: user-customizable, but not universally "any widget anywhere."** Most pages
   get a real customizable widget grid — Overview (fully), Quant Console (fully, it becomes
   the main hub), Chart tab (a side-panel area sized for a handful of widgets, not a full
   grid). **Dealer Book / chain-exposure keeps its own dedicated tab** — it is not required to
   decompose into the generic widget system. A lightweight GEX-4-panel widget and a flow-book
   widget *may* eventually join the generic catalog, but explicitly **last**, after the system
   has matured on lighter modules — do not attempt these in an early phase.
3. **Sync scope: ticker+expiry AND basket.** The global "active symbol" state is
   `{ticker, expiry, basket}` (basket = an ordered ticker list, used by correlation/VaR/
   screener-shaped widgets). A widget's sync toggle governs whether it reads from this shared
   state; off, it keeps its own local inputs.
4. **Cross-widget context feeding is a first-class feature, not a side effect.** Quant Console
   should visibly support "run widget A, its result becomes available to widget B" — the live
   analog of `_thread_vol_stats_into_context`, generalized to any module's `context_patch`,
   decoupled from sequential/pipeline execution order.
5. **Output visibility: preview first, then live progress.** Priority is a catalog/picker that
   shows what a widget produces (description, output shape, required inputs) *before* you add
   or run it. Per-run progress feedback (module-by-module status) is real but lower priority —
   phased later.

## Target architecture

### 1. Context Store (`shared/context_store.py`, new) — replaces orchestrator sequencing

A live, in-process (dashboard-process-resident, SQLite-backed for restart survival — reuse the
`widget_cache.db` file, new table) key-value store keyed by **scope** (`ticker`, or
`ticker+expiry`, or a `basket_id`), holding named entries (`vol_stats`, `correlation_matrix`,
`garch_conditional_vol`, `dealer_exposure_result`, `sentiment_score`, ...) each with
`computed_at` and a source module slug. Two operations any module's `run()` can use:

- `context_store.get(scope, key, max_age_s) -> value | None` — soft read, generalizes
  `_resolve_vol_and_quality`'s pattern into one shared helper instead of VaR reimplementing it.
- `context_store.put(scope, key, value, source_slug)` — called automatically by the widget-run
  API for every non-empty `ModuleResult.context_patch`, so any module that already returns
  `context_patch` (dealer_exposure, dealer_flow's `requires`, etc.) participates without
  changes.

`GET /api/context?scope=...` exposes the current store contents read-only, so a widget's
frontend can show "using vol_stats from dealer_exposure run 4 min ago" style provenance —
this satisfies "have the results of one widget feed another" as a visible, not hidden, behavior.

### 2. Unified module contract

`ModuleSpec`/`ModuleResult` (already the more capable contract) becomes the **only** contract.
`Tools/registry.py`'s `ToolSpec` entries are migrated into proper per-suite (or one shared
"tools" pseudo-suite) `ModuleSpec` entries with real `artifacts`/`context_patch`, removing the
lossy `from_tool_spec` adapter. `ModuleSpec` gains the metadata needed for a real preview
catalog:

```python
@dataclass(frozen=True)
class ModuleSpec:
    name: str
    slug: str
    suite: str
    category: str
    run: Callable[[dict], ModuleResult]
    requires: list[str] = field(default_factory=list)
    archive: ArchiveHint = ArchiveHint("global")
    description: str = ""              # new — one-liner for the catalog card
    inputs: InputSpec = InputSpec()    # new — {"ticker": required|optional|none, "expiry": ..., "basket": ...}
    output_kind: str = "metrics"       # new — "chart" | "metrics" | "distribution" | "surface" | "table"
    sample: dict | None = None         # new — a static example payload/thumbnail for the catalog preview
```

### 3. Generic backend widget API (`dashboard/app.py`, replacing the fixed-4 Overview routes)

- `GET /api/widgets/catalog` — every `ModuleSpec`'s preview metadata (powers the picker).
- `POST /api/widgets/{slug}/run` — body `{scope: {ticker, expiry?, basket?}, params?}` →
  `resolve_modules([slug])[0].run(context)` synchronously (the `dealer_book_load` pattern,
  generalized to every slug), writes result into the generalized `widget_cache` (keyed
  `slug+scope`, not a fixed id) and any `context_patch` into the Context Store, returns the
  `ModuleResult` JSON.
- `GET /api/widgets/{slug}/state?scope=...` — last cached result for a slug+scope, for a
  widget to show "last known" on mount without forcing a re-run.
- `GET /api/context?scope=...` — read-only Context Store view (provenance display).

`widget_archive_renderer.py`'s category→payload dispatch (currently a stub, one unfinished
consumer) is replaced by this: `output_kind` on `ModuleSpec` drives which generic frontend
renderer is used, so no per-widget bespoke render code is needed for the common cases.

### 4. Frontend: a real reusable widget component

A single vanilla-JS custom element, `<quant-widget slug="..." >`, registered once
(`dashboard/static/js/quant-widget.js`, new — first real shared JS module; today there is none),
instantiable anywhere a page includes it:

- Reads a page-global sync store (`{ticker, expiry, basket}` + subscribe/publish — a small
  `EventTarget`-based bus, since none exists today) when its own `sync` toggle is on; otherwise
  uses its own local inputs.
- Calls `POST /api/widgets/{slug}/run` and `GET /api/widgets/{slug}/state`.
- Renders via one of a small fixed set of generic renderers selected by `output_kind`: metrics
  table, distribution histogram (reuse `quant.html`'s existing `renderDistribution`, currently
  duplicated — de-duplicate into this shared module), image/PNG artifact viewer, generic JSON
  fallback. Only widgets with a genuinely bespoke visual (e.g. a future dealer-book widget) get
  a custom renderer registered against their slug; everything else free-rides on the generic set.

### 5. Layout persistence (new `dashboard_layouts` table)

`dashboard_layouts(page, widget_instance_id, slug, position, scope_override, sync_enabled,
config_json)`. CRUD endpoints under `/api/layout/{page}`. Pages that host a customizable grid
(Overview, Quant Console fully; Chart tab, a bounded side-panel region) load their saved widget
instances on mount and reconstruct `<quant-widget>` elements from them. This is what makes
widgets usable "close to anywhere" per Jason's scoping — not literally every page, and Dealer
Book stays a dedicated bespoke tab.

### 6. What retires, what relocates

| Today | Fate |
|---|---|
| `orchestrator.py::run_suite`, `run_unified`, CLI `--unified`/`--suite` | Removed |
| `dashboard/app.py::trigger_run`/`_execute_run`/`_RUNS`/`GET /runs/{id}` polling | Removed |
| `WS /suites/{suite}/live` (already-dead log-tail infra) | Removed |
| `orchestrator.py::run_selected_modules`, `_expand_module_requires`, `_topo_sort_modules` | Relocated to `shared/module_execution.py` (new), kept — still useful for a widget declaring `requires` |
| `orchestrator.py::_thread_vol_stats_into_context` | Replaced by Context Store `put`/`get`, generalized beyond vol→VaR |
| `dashboard/widget_cache.py` fixed 4 ids | Generalized to `slug+scope` keying |
| `dashboard/widget_archive_renderer.py` stub | Replaced by `output_kind`-driven generic rendering |
| `Tools/registry.py` + `Tools/tools/*` + `tools_*.html` templates | Migrated into `ModuleSpec` entries; bespoke templates retired as their generic widget equivalents ship |
| `Options_Suite/module_registry.py` stub aliasing | Replaced with real per-model `run` functions |
| `VaR_Tools_Simulations/module_registry.py` (empty) | Populated — this suite's widgets were explicitly out of scope for the *prior* overhaul; they are in scope now, since standalone VaR widgets are the point |
| `dealer_book.html`, chain-exposure tab | **Unchanged**, kept as dedicated pages (out of scope) |

Anything outside the dashboard that currently calls `orchestrator.run_unified`/`run_suite`
(check `backfill.py`, `scheduled_ingest.py`, cron/schedule-skill jobs, CI, tests) must be
inventoried and given a replacement call path (most likely: script directly against
`run_selected_modules` or the suite's own `--context` mode) **before** removal — this is a
Phase 0 task, not an assumption.

## Non-goals (this spec)

- Dealer Book / chain-exposure tab redesign — stays as-is.
- Auth — unchanged (none, by design, per `CLAUDE.md`).
- Any change to the DTCC ingestion pipeline, `swaps.db` schema, or `shared/thetadata.py`.
- Real-time push (WebSocket) progress streaming — noted as a later phase, not blocking.
