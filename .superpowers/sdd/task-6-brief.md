# Task 6 — VS_RUN_* Handoff Fix (final Phase 3 sub-task of the Modularization Overhaul)

Source plan: `C:\Users\bottl\.claude\plans\i-want-to-do-cosmic-turing.md` (CARL-reviewed, converged
SHIP). This is the last sub-task of Phase 3 (Vol_Suite split). Prior sub-tasks, all merged to master:
Task 3 (dealer-book cluster), Task 4 (chain_scanner/svi_smile), Task 5 (surface grid modules — may
still be in review when you start; do not touch `Tools/tools/surface_explorer_tool.py` or the four
`surface_*` `ModuleSpec` entries either way, they are out of this task's scope regardless of Task 5's
exact final shape).

## Read this first — the plan's own correction (CARL R1-F2)

The plan originally (wrongly) said the five `VS_RUN_*` env-gate reads live inside
`_run_core_analysis` and should be intercepted there. **That was verified false and corrected during
this plan's CARL review.** The actual reads are in `run_context_mode` (`volatility_suite.py:2336`),
around lines 2398-2410 (verify exact current line numbers yourself — this file has moved since). Read
`run_context_mode` in full (`:2336-2470`-ish, confirm end) before writing anything. **Do not intercept
the env-gate reads themselves — intercept the hand-off from those resolved booleans into the
`_run_core_analysis(...)` call a few lines later**, which is the actual integration point the plan's
corrected text specifies.

## Read this second — an important scoping finding you must independently confirm or refute

`_run_core_analysis` (`volatility_suite.py:1136`) is a large, shared pipeline function called by THREE
entry points: `run_focus_workflow`, `run_unified_flow` (both interactive), and `run_context_mode`
(headless). Its five gated steps (group screener, vol surface 2D, VRP term structure, sentiment
backtest, options chain scanner) are **not independently callable modules today** — they are inline
`if run_x: ...` blocks inside one function body that share local state across steps (most notably: the
options-chain-scanner step at `:1825-1847`-ish deliberately reuses the **already-computed dealer
positioning result** from an earlier step in the same function call, specifically to avoid a documented
"two-vanna" bug — read the comment there — rather than making its own independent fetch the way Task
4's standalone `chain_scanner` `ModuleSpec` does). This is a structurally different code path from Task
4's `chain_scanner` module, not a duplicate of it — do not try to unify them.

**Your job is to independently verify this claim (or find it's wrong) by actually reading
`_run_core_analysis` end to end before deciding your approach.** If confirmed correct: do **not**
attempt to extract these five steps into fully independent, standalone `ModuleSpec`s that bypass
`_run_core_analysis` — that would risk silently reintroducing the two-vanna bug (or similar) and is a
much larger, riskier refactor than what this task actually needs. Full extraction of tightly-coupled
pipeline steps is out of scope here, the same way Task 4's `svi_smile` brief explicitly allowed
stopping short of forcing an ugly unification when the underlying call sites turned out to differ by
design ("stop and report DONE_WITH_CONCERNS explaining exactly why, rather than forcing something
ugly" — same principle applies here if you find the coupling is real).

## This task's actual, minimally-scoped goal

1. **`run_context_mode` gains module-selection awareness, additively.** When
   `context.get("modules")` is a non-empty list (the same key Task 2's `run_selected_modules` /
   dashboard `POST /run/{kind}` already use elsewhere in this plan), resolve the five booleans
   (`run_options_chain`, `run_group_screener`, `run_vol_surface_2d`, `run_vrp_term_structure`,
   `run_sentiment_backtest`) from **membership in that list** against a fixed slug mapping you define
   (e.g. `"chain_scanner"` -> `run_options_chain=True`, `"group_screener"` -> `run_group_screener=True`,
   `"vol_surface_2d"` -> `run_vol_surface_2d=True`, `"vrp_term_structure"` -> `run_vrp_term_structure=True`,
   `"sentiment_backtest"` -> `run_sentiment_backtest=True`) **instead of** reading the `VS_RUN_*` env
   vars for that call. When `context.get("modules")` is absent or empty, behavior must be **byte-
   identical to today** — the exact same `_env_flag("VS_RUN_...", default)` reads, same defaults
   (note `VS_RUN_VRP_TERM_STRUCTURE` defaults to **True**, all four others default to **False** — do
   not accidentally flip this when adding the new path). This is the actual "hand-off interception":
   you're changing where the five booleans **come from**, not touching `_run_core_analysis`'s
   signature, body, or its three call sites' shared behavior at all.
2. **Back-compat is non-negotiable and must have a regression test**: an existing caller (dashboard
   `_widget_*_tick` functions, any cron/batch script, `orchestrator.py`'s existing subprocess-invoked
   `--context` call) that sets `VS_RUN_CHAIN_SCANNER=1` (etc.) and does NOT pass `context["modules"]`
   must still get exactly the old env-var-driven behavior. Test this explicitly, not just by absence of
   a `modules` key in your test fixtures — assert the actual boolean values resolved match the env-var
   path.
3. **Judgment call, decide after reading, document either way:** whether to also add lightweight
   `ModuleSpec` registry entries (`group_screener`, `vol_surface_2d`, `vrp_term_structure`,
   `sentiment_backtest` — `chain_scanner` already exists from Task 4, do not duplicate it) purely so
   they're **listable/selectable** via `--list-modules`/dashboard checkboxes/`context["modules"]`
   membership, even though (per the scoping finding above) their real execution still only happens
   inside `_run_core_analysis`'s shared pipeline, not via an independent `ModuleSpec.run()` callable in
   the usual Task-3/4/5 sense. Two acceptable approaches, pick whichever you judge cleaner once you've
   read the code — **do not silently skip this decision, state which you chose and why in your
   report**:
   - (a) Add the four entries with a `run()` that clearly documents (in its own docstring/error) that
     it is a **selection-only marker** — e.g. it could raise `NotImplementedError("group_screener only
     runs as part of Vol_Suite's core context-mode pipeline; select it via context['modules'] on a
     vol_suite context-mode run, not as a standalone module invocation")` — so `--list-modules` and the
     dashboard checkbox UI can surface it, while nobody accidentally treats it as independently
     runnable via `run_selected_modules`.
   - (b) Skip adding them as `ModuleSpec` entries at all, and instead have `run_context_mode`'s new
     `context["modules"]`-membership check use the same five slug strings directly (no registry lookup
     needed for something that isn't independently runnable) — simpler, but means `--list-modules`
     won't show these four as selectable, only `chain_scanner`/`svi_smile`/the dealer-book cluster/the
     four surface modules will.
   Either is acceptable; brief author's lean is (a) for UI discoverability, but defer to your read of
   the actual coupling.

## Fragile surfaces

- **`run_unified` / `_thread_vol_stats_into_context` in `orchestrator.py` are completely out of
  scope and must show zero diff** — this task is entirely inside `Vol_Suite/volatility_suite.py` (and
  optionally `Vol_Suite/module_registry.py` for the judgment call in step 3). Nothing in
  `orchestrator.py` should change.
- **`run_focus_workflow` / `run_unified_flow` (the two interactive entry points) must show zero
  behavior change** — they resolve their own booleans via `_prompt_sign_model_and_options_chain` /
  their own prompt logic (`volatility_suite.py:1998-1999`, `:2256-2257`ish — confirm current lines) and
  call `_run_core_analysis` the same way they always have. This task only touches `run_context_mode`'s
  resolution of these five booleans. Do not add a `context["modules"]` check to either interactive
  function.
- **`VS_RUN_VRP_TERM_STRUCTURE`'s default is `True`, uniquely among the five** — get this backwards in
  the new module-selection path and a context-mode run selecting specific modules but NOT including
  `"vrp_term_structure"` would silently lose output every downstream consumer of `vol_result.json`
  currently expects to see by default. Add an explicit test for this exact case: `context["modules"] =
  ["chain_scanner"]` (VRP not listed) — decide and test what SHOULD happen (options: VRP off since it
  wasn't selected: **most consistent with "selected modules run, nothing else does"**; OR VRP stays on
  by default even under module-selection since dropping the historical default silently changes
  published output shape). State your choice and reasoning in the report; either is defensible, but it
  must be a deliberate, tested decision, not an accident of how you wrote the membership check.
- Flat cwd-relative imports / SPX-SPXW routing: not directly touched by this task since you're not
  adding new ThetaData calls, but if your `ModuleSpec`-entry approach (option 3a) needs anything from
  `shared.module_registry`, make sure `Vol_Suite/module_registry.py`'s existing sys.path front-insertion
  fix (from Task 4) still applies correctly — don't duplicate or fight it.

## Testing

New test file (or extend an existing `Vol_Suite/tests/test_*.py` covering `run_context_mode` — check
for one first) covering:
- No `modules` key in context: all five booleans resolve exactly as they do today from `VS_RUN_*` env
  vars (parametrize over a few env-var combinations, including the all-default case).
- `modules` key present with a subset of the five slugs: only the matching booleans become `True`,
  others `False` — including the VRP-default interaction case above, whichever way you decided it.
- `modules` key present but empty list: treat the same as absent (falls back to env vars) — decide and
  test this explicitly, don't leave it ambiguous.
- If you added the four new `ModuleSpec` entries (option 3a): a test that `--list-modules` /
  `shared.module_registry.all_modules()` includes them with correct `slug`/`category`, and that calling
  `.run()` directly raises the documented `NotImplementedError` (or whatever you chose) rather than
  silently no-op'ing.
- Mock `_run_core_analysis` itself (it's a big function with real ThetaData calls inside — don't invoke
  it for real in these tests) to assert it's called with the correct resolved booleans for each
  scenario above, rather than trying to test full pipeline behavior end-to-end here.
- All ThetaData-dependent calls mocked, no live network calls in the committed test suite.

Run the new/updated test file, a full `Vol_Suite/tests/` pass, and a full repo `pytest -q` pass — run
these synchronously in the foreground and wait for them to actually finish before reporting; do not
start background/async test monitoring (this has repeatedly caused problems in prior tasks this
session — the specific failure mode to avoid is reporting back with something like "I'll wait for the
background test run to complete" instead of literally waiting for pytest to exit in the same tool
call). Report exact commands and pass/fail counts. Confirm against the current baseline for
`Vol_Suite/tests/` — **as of Task 4: 1092 passed, 7 pre-existing failures in
`test_dual_pipeline_gate_v2.py`/`test_dual_pipeline_gate_v7.py`, 10 skipped** — this may have shifted
slightly if Task 5 landed first (check `git log` / the ledger at `.superpowers/sdd/progress.md` for the
current baseline before you start; if Task 5 hasn't landed yet, 1092/7/10 is still correct).

## What NOT to do in this task
- Do not touch `orchestrator.py` at all (`run_unified`, `_thread_vol_stats_into_context`, `_SUITE_SPECS`
  — all untouched, zero diff).
- Do not modify `run_focus_workflow` or `run_unified_flow`'s prompt/boolean-resolution logic.
- Do not modify `_run_core_analysis`'s signature, body, or step-ordering — this task only changes what
  feeds its `run_options_chain=`/etc. keyword arguments in `run_context_mode`'s call to it.
- Do not touch `Tools/tools/surface_explorer_tool.py` or the four `surface_*` module entries (Task 5's
  scope, regardless of whether it's landed yet).
- Do not remove the `VS_RUN_*` env-var reads — they remain the back-compat path when `modules` is
  absent.
- Do not attempt a full extraction of the five pipeline steps into independent, `_run_core_analysis`-
  bypassing `ModuleSpec.run()` implementations — per the scoping finding above, this risks
  reintroducing the documented two-vanna bug and is out of scope for this task.

## Report contract
Follow the standard implementer report contract (status DONE/DONE_WITH_CONCERNS/NEEDS_CONTEXT/BLOCKED,
commits made, one-line test summary, concerns). Explicitly state which judgment calls you made (the
3a/3b registry-entry decision, and the VRP-default-under-module-selection decision) and why. Write your
full report to `.superpowers/sdd/task-6-brief-report.md` (note the distinct filename —
`.superpowers/sdd/task-6-report.md` already exists from an unrelated prior plan run on this repo; do
not overwrite it — this exact naming collision has bitten this session before, see Task 4's report for
the full story).
