# Task 5 — Vol_Suite Surface Grid Modules (part of Phase 3 of the Modularization Overhaul)

Source plan: `C:\Users\bottl\.claude\plans\i-want-to-do-cosmic-turing.md` (CARL-reviewed, converged SHIP).
Sub-task 3 of 3 for Phase 3's Vol_Suite split (Task 3 = dealer-book cluster, already merged; Task 4 =
chain_scanner/svi_smile, already merged). **This task adds four more entries to
`Vol_Suite/module_registry.py`: `surface_greek`, `surface_market_iv`, `surface_flow_strike_time`,
`surface_flow_strike_expiry`.** Do not remove or modify the six existing entries (dealer_exposure,
dealer_flow, position_book, dual_book, chain_scanner, svi_smile).

## Prior work — read before writing code

- `shared/module_registry.py`: `ModuleSpec`/`ModuleResult`/`ArtifactRef`/`ArchiveHint` shapes — read the
  actual file, field names/defaults are ground truth there.
- `Vol_Suite/module_registry.py` (current state, six entries): follow the exact same pattern as the
  existing entries — a private `_run_x(context, *, td=None)` function that does the real work, wrapped
  by a public `ModuleSpec`. Note Task 4 fixed a real sys.path-ordering bug at the top of this file
  (Vol_Suite/ must be moved to the FRONT of sys.path, not just membership-checked, because
  `Tools/tools/surface_explorer_tool.py` front-inserts Options_Suite/ which can otherwise shadow
  Vol_Suite's flat internal modules) — do not undo that fix, and be aware `surface_explorer_tool.py`
  does its OWN sys.path insertion too (see below) which is a second, independent instance of the same
  class of risk you should be alert to when this task also touches that file.
- `Vol_Suite/surface_grids.py` (506 lines) — the four builder functions this task promotes to
  `ModuleSpec`s: `build_greek_surface` (:99-197), `build_market_iv_surface` (:203-291),
  `build_flow_strike_time` (:323-414), `build_flow_strike_expiry` (:417-506). Read the whole file —
  it's not large and every builder's docstring documents real, hard-won behavior (e.g.
  `build_market_iv_surface`'s strike-axis derivation from observed 1st/99th percentile strikes, not a
  symmetric moneyness band — a prior bug). Each builder already accepts an optional `td=` kwarg and
  raises `ValueError` with a specific reason on missing spot/no listed expiries/no usable trades (the
  fail-closed convention this repo's dealer-exposure code follows) — do not change any builder's
  internal math or fail-closed behavior. This module has no existing tests of its own to check for
  before starting (confirm at implementation time).
- `Tools/tools/surface_explorer_tool.py` (458 lines) — the existing `Tools/registry.py::ToolSpec`
  wrapper this task must keep working unchanged from the dashboard's `/tools/surface-explorer` page.
  Its `run(context)` (`:409-440`) dispatches on `context['mode']` across 5 modes: `greek_surface`
  (+`GREEK_SURFACE_MODES` aliases), `iv_surface_market` (+`IV_SURFACE_MODES` aliases),
  `flow_strike_time` (+`FLOW_TIME_MODES` aliases), `flow_strike_expiry` (+`FLOW_EXPIRY_MODES` aliases),
  and `iv_smile_by_model` (+`SMILE_BY_MODEL_MODES` aliases — **out of scope for this task**, it wraps
  `smile_by_model.build_iv_smile_by_model` directly, not a `surface_grids.py` builder, and is unrelated
  to the four modules you're adding). Each of the four in-scope modes' existing `_run_*` helper
  (`_run_greek_surface`, `_run_iv_surface_market`, `_run_flow_strike_time`, `_run_flow_strike_expiry` —
  read the file to find their exact line numbers) already: resolves context args, calls the matching
  `surface_grids.py` builder, best-effort renders a static PNG via matplotlib if `out_dir` is
  resolvable (never lets a plotting failure discard an already-computed grid), and returns the result
  dict with `mode`/`chart_path` set.

## This task's scope

1. **Four new `ModuleSpec` entries in `Vol_Suite/module_registry.py`**, `category="surface"` for all
   four, `default_selected=False`, `cli_entry=None` (no standalone CLI required — these are
   registry/dashboard-driven, matching Task 4's `chain_scanner`/`svi_smile` precedent), `requires=[]`,
   `archive.key_shape="ticker_expiry"`:
   - **`surface_greek`** — thin adapter over `surface_grids.build_greek_surface`. `context` needs a
     `greek` key (one of `ebe.GREEKS` — delta/gamma/vega/vanna/charm/volga); default to `"gamma"`
     matching `surface_explorer_tool.py`'s `DEFAULT_GREEK`. `ModuleResult.metrics` should carry the
     scalar/summary fields the result dict already has (ticker, greek, spot, n_expiries_used, units —
     don't invent new fields); `artifacts=[]` unless you choose to also render+save a PNG the way
     `surface_explorer_tool.py` does (your choice — if you skip PNG rendering here, document why in
     your report; the grid itself is the primary output, consumed via `context_patch` or `metrics`,
     your call on which given `ModuleResult`'s shape).
   - **`surface_market_iv`** — thin adapter over `surface_grids.build_market_iv_surface`. `context` may
     carry `min_dte` (forwarded, default 0 per the builder's own default).
   - **`surface_flow_strike_time`** — thin adapter over `surface_grids.build_flow_strike_time`.
     `context` may carry `session` (forwarded, default None -> today per the builder's own default).
   - **`surface_flow_strike_expiry`** — thin adapter over `surface_grids.build_flow_strike_expiry`.
     `context` may carry `session`/`max_expiries`/`min_dte`/`max_dte` (all forwarded, builder's own
     defaults apply when absent).
   - Fail-loud: same convention as every existing entry in this file — a narrow `try/except Exception`
     around the builder call, converting to `status="failed"` with real error text in `metrics`, never
     a fake `"ok"`. This matches `surface_grids.py`'s own fail-closed `ValueError`s — don't swallow
     them into a fake success.
2. **`Tools/tools/surface_explorer_tool.py::run()` becomes a thin compatibility shim** for the 4
   in-scope modes (leave `iv_smile_by_model` mode's dispatch exactly as-is, untouched): instead of each
   mode's existing `_run_*` helper calling `surface_grids.py` directly, it should call the new
   registry `run()` function for the matching module (via `shared.module_registry.resolve_modules` or a
   direct import of `Vol_Suite.module_registry`'s new `_run_surface_*` functions — your choice, pick
   whichever keeps this file's existing PNG-rendering/`chart_path` behavior intact with the least
   duplication) and adapt the `ModuleResult` back into the exact same return shape
   (`mode`/`chart_path`/the grid fields) this page's callers already depend on. **The existing
   `/tools/surface-explorer` dashboard page's behavior must not change at all** — same JSON shape, same
   PNG rendering, same error behavior on a bad mode/missing ticker. Add a regression test asserting
   this (call `surface_explorer_tool.run()` for each of the 4 in-scope modes with a mocked/injected
   `td`, confirm the returned dict shape and `chart_path` presence match what a pre-change call would
   have produced — mock the underlying ThetaData calls, no live network in tests).

## Fragile surfaces

- **sys.path ordering** (Task 4's finding): `surface_explorer_tool.py` does its own sys.path insertion
  at module scope (`:45-50`) — Vol_Suite/ then Options_Suite/ then repo root, all via `insert(0, ...)`,
  which means whichever is inserted LAST ends up first (repo root ends up at index 0, Options_Suite/ at
  index 1, Vol_Suite/ at index 2 — verify this yourself by reading the actual code, don't trust this
  paraphrase). If this task's shim now imports `Vol_Suite.module_registry` (which does its OWN
  defensive sys.path front-insertion, per Task 4's fix), make sure the two files' sys.path manipulations
  don't fight each other or reintroduce the Task 4 shadowing bug in the other direction. Add this
  specific interaction to your test coverage: a repo-root subprocess import test (matching Task 3/4's
  pattern) that imports `Tools.tools.surface_explorer_tool` FIRST, then confirms
  `Vol_Suite.module_registry.MODULES` still contains all ten expected slugs (six existing + four new)
  with no `AttributeError`/shadowing.
- **SVI/smile fitting untouched**: `iv_smile_by_model` mode is explicitly out of scope — don't touch it
  or `smile_by_model.py`.
- **`vol_surface_2d.py` / `expiry_book_exposure.py` internal logic**: do not modify — `surface_grids.py`
  only wraps them, and this task only wraps `surface_grids.py`. No suite-internal math changes anywhere
  in this task.
- Flat cwd-relative imports: same fragile surface as Tasks 3/4 — confirm the extended file still
  imports cleanly from the repo root (extend the existing subprocess-based repo-root import test in
  `Vol_Suite/tests/test_module_registry_dealer_book.py` or `test_module_registry_chain_svi.py`, your
  choice, to cover the new imports `surface_grids` pulls in — note it lazily imports `vol_surface_2d`
  inside `build_market_iv_surface` and `chart_app.flow_stamp` inside `_flow_helpers()`, so the
  module-level import surface of `surface_grids.py` itself is smaller than its runtime one; make sure
  your test still exercises the runtime imports, not just the module-level ones — call through to a
  mocked builder invocation, not just `import surface_grids`).

## Testing

Add to `Vol_Suite/tests/test_module_registry_dealer_book.py`, `test_module_registry_chain_svi.py`, or a
new file (your choice, keep it in `Vol_Suite/tests/`):
- All four new slugs present in `MODULES` with correct `slug`/`category="surface"`/
  `default_selected=False`.
- `shared.module_registry.all_modules()` includes all four without raising.
- Fail-loud test per module (at least one representative, mirroring Tasks 3/4's pattern): mock the
  underlying `surface_grids.build_*` call to raise, assert `status="failed"`, not a fake `"ok"`.
- `surface_explorer_tool.py` compatibility shim: for each of the 4 in-scope modes, a test with a
  mocked/injected `td` confirming the returned dict/`chart_path` shape is unchanged from what the
  pre-change direct-`surface_grids`-call path produced (i.e., you need to understand and mirror the
  CURRENT return shape exactly — read the current `_run_greek_surface`/etc. helpers' return statements
  before writing this test, don't guess the shape).
- The sys.path double-insertion interaction test described above (Tools-first import order, all ten
  Vol_Suite slugs present).
- All ThetaData-dependent calls mocked, no live network calls in the committed test suite.

Run the new/updated test file(s), a full `Vol_Suite/tests/` pass, and a full repo `pytest -q` pass —
run these synchronously in the foreground and wait for them to actually finish before reporting; do not
start background/async test monitoring (this has caused problems in prior tasks this session — twice in
Task 2/3, and note the exact pattern to avoid: reporting back with a message like "I'll wait for the
background test run to complete" instead of literally waiting for `pytest` to exit in the same shell
call). Report exact commands and pass/fail counts, and confirm they match the established baseline:
**1092 passed, 7 pre-existing failures in `test_dual_pipeline_gate_v2.py`/`test_dual_pipeline_gate_v7.py`,
10 skipped** for `Vol_Suite/tests/` (this baseline was updated by Task 4 — 1073 was the Task-3 baseline,
1092 is current; use 1092 as your pre-task baseline, not 1073) — any new failure beyond that baseline is
your regression to fix, not baseline noise.

## What NOT to do in this task
- Do not touch `volatility_suite.py`'s `VS_RUN_*` env-gate wiring — that's a separate follow-up task
  (Task 6).
- Do not modify `surface_grids.py`'s internal math/fail-closed behavior, `vol_surface_2d.py`,
  `expiry_book_exposure.py`, or `smile_by_model.py`.
- Do not touch `surface_explorer_tool.py`'s `iv_smile_by_model` mode/dispatch branch, or its
  `TOOL_SPEC`/`ToolSpec` registration itself (`Tools/registry.py` stays untouched, per the plan's
  Module Registry Contract — `from_tool_spec()` is how `Tools/` entries get a free `ModuleSpec`, this
  task is deliberately NOT converting `surface-explorer` into a first-class `Vol_Suite/module_registry.py`
  citizen, only having its 4 in-scope modes delegate their real work to the new modules internally).
- Do not touch the six existing `Vol_Suite/module_registry.py` entries.
- Do not set `default_selected=True` on any of the four new modules.

## Report contract
Follow the standard implementer report contract (status DONE/DONE_WITH_CONCERNS/NEEDS_CONTEXT/BLOCKED,
commits made, one-line test summary, concerns). Write your full report to
`.superpowers/sdd/task-5-brief-report.md` (note the distinct filename —
`.superpowers/sdd/task-5-report.md` already exists from an unrelated prior plan run on this repo; do
not overwrite it — this exact naming collision has bitten this session before, see Task 4's report for
the full story).
