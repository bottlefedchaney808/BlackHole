# Task 6 — Selectable-Module Core Analysis (Vol_Suite context mode)

## What was implemented

Added module-selection support to `Vol_Suite/volatility_suite.py::run_context_mode`: a new
`_resolve_core_analysis_flags` helper plus `_CORE_ANALYSIS_MODULE_SLUGS` constant, wired in
additively so that when `context["modules"]` is present and non-empty, only the selected module
slugs run; when it is absent or empty, behavior is byte-identical to the prior env-var-driven
fallback (no regression for existing callers that don't pass `modules`).

Complemented this with four new selection-only marker `ModuleSpec` entries in
`Vol_Suite/module_registry.py` (option 3a from the design discussion) — these register the newly
selectable slugs in the registry for discovery/introspection purposes, but raise
`NotImplementedError` if `.run()` is invoked on them directly, since their actual execution path is
the flags threaded through `run_context_mode`/`_run_core_analysis`, not a standalone registry
dispatch.

## Judgment calls

1. **Option 3a (marker entries, not full dispatch)** for the four new `ModuleSpec` registry
   entries: they exist so the registry has a complete, queryable list of selectable core-analysis
   modules, but real execution stays inside `run_context_mode`'s flag-resolution path rather than
   duplicating dispatch logic in two places. Calling `.run()` on a marker entry directly is a
   programming error and fails loud (`NotImplementedError`) rather than silently no-op'ing.
2. **"Selected modules run, nothing else does"** semantics for `context["modules"]`: when a caller
   explicitly passes a module list, it is treated as the complete desired set — including
   `vrp_term_structure`, which does NOT default on just because other modules were selected. This
   matches the principle of least surprise for an explicit opt-in list; an implicit "always-on"
   module would make module selection load-bearing in a way that's easy to miss when auditing a
   context payload. Documented in the `run_context_mode` docstring.

## Files changed

- `Vol_Suite/volatility_suite.py` — additive `_resolve_core_analysis_flags` /
  `_CORE_ANALYSIS_MODULE_SLUGS`, used only inside `run_context_mode`.
- `Vol_Suite/module_registry.py` — four selection-only marker `ModuleSpec` entries.
- `Vol_Suite/tests/test_context_mode_module_selection.py` — new test file.
- `Vol_Suite/tests/test_module_registry_selection_only_markers.py` — new test file.

## Test results (controller-verified, independently, synchronously)

- `Vol_Suite/tests/test_context_mode_module_selection.py` +
  `Vol_Suite/tests/test_module_registry_selection_only_markers.py`: **24 passed**.
- Phase-boundary regression gate (`tests/test_orchestrator_market_signals.py` +
  `VaR_Tools_Simulations/tests/test_context_builders.py`): **39 passed, unchanged**.
- `orchestrator.py`: zero diff (`git diff --stat` confirmed clean — this task never touched the
  orchestrator).
