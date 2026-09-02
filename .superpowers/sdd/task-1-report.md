# Task 1 — Module Registry Scaffolding — Report

## Status: DONE

## What was implemented

1. **`shared/module_registry.py`** (new) — the umbrella contract:
   - `ArtifactRef`, `ArchiveHint`, `ModuleResult`, `ModuleSpec` frozen dataclasses, exactly matching
     the brief's shapes (Python 3.12, `from __future__ import annotations`, PEP 604 unions where
     used).
   - `all_modules() -> list[ModuleSpec]`: aggregates `MODULES` from `Vol_Suite/module_registry.py`,
     `sentiment-scanner/module_registry.py`, `Options_Suite/module_registry.py`,
     `VaR_Tools_Simulations/module_registry.py` (via `importlib.import_module`, since
     `sentiment-scanner` isn't a valid dotted-import identifier — `importlib.import_module` handles
     the hyphenated name fine, verified live), plus `from_tool_spec(t)` applied to every entry in
     `Tools/registry.py::TOOLS`. Each suite import is wrapped in `try/except Exception: continue` so
     one suite's broken/missing registry can't hide the others' modules.
   - `resolve_modules(slugs) -> list[ModuleSpec]`: builds a slug→spec index over `all_modules()`,
     preserves input order, raises `ValueError` naming the bad slug and listing all valid slugs on an
     unknown one; `resolve_modules([])` returns `[]` with no error.
   - `from_tool_spec(tool_spec) -> ModuleSpec`: maps `name`/`slug` straight across, wraps
     `tool_spec.run` so its bare `dict` return becomes a `ModuleResult(status="ok", artifacts=[],
     metrics=<the dict>, context_patch=None)` — `ModuleSpec.run` must return a `ModuleResult`, not a
     dict, so the callable itself needed a thin wrapper, not a direct reassignment.
     `suite="tools"`, `category="tool"`, `cli_entry=None`, `default_selected=False`, `requires=[]`,
     `archive=ArchiveHint(key_shape="global")` as specified.

2. **Four empty suite registries** — `Vol_Suite/module_registry.py`, `Options_Suite/module_registry.py`,
   `VaR_Tools_Simulations/module_registry.py`, `sentiment-scanner/module_registry.py` — each just
   `MODULES: list["ModuleSpec"] = []` with the type hint imported under `TYPE_CHECKING` (no runtime
   dependency on `shared`, avoiding any import-order coupling). Verified each imports cleanly both
   from the repo root (via `shared.module_registry.all_modules()`) and from inside its own suite
   directory (`cd <suite> && python -c "import module_registry"`) — the brief specifically flagged
   this repo's flat cwd-relative import fragility, so both directions were checked explicitly.

3. **`Tools/registry.py`** — untouched, as instructed.

4. **`tests/test_module_registry.py`** (new) — 8 tests:
   - `all_modules()` contains every `Tools/registry.py::TOOLS` entry, correctly adapted (suite="tools",
     category="tool", cli_entry=None, default_selected=False, requires=[], archive=global).
   - `all_modules()` currently equals exactly the adapted `TOOLS` set (nothing yet from the four empty
     suite registries) — a Phase-1-specific assertion that will need updating once a later phase
     populates a suite registry.
   - No duplicate slugs across the aggregated list.
   - `resolve_modules(["not_a_real_slug"])` raises `ValueError` naming the bad slug.
   - `resolve_modules([])` returns `[]`.
   - `resolve_modules` preserves requested order for a 2-slug lookup.
   - `from_tool_spec` wraps a tool's dict-returning `run` into a proper `ModuleResult`.
   - **Cycle detection**: written as a `@pytest.mark.skip`-marked TODO test (not a real assertion),
     per the brief's explicit "use your judgment" option. Rationale: this task is registration-only —
     nothing calls into `requires` yet (no executor exists to walk the graph), so there's no code path
     for a cycle to actually break at this phase. The skipped test's body is left as a concrete
     scaffold (two fake `ModuleSpec` objects with `requires` pointing at each other) so whichever later
     phase adds `requires`-graph execution can un-skip it and assert the executor raises rather than
     infinite-looping. This phase does catch the two things the brief explicitly requires now:
     duplicate slugs and unknown slugs.

## What was tested

- `.venv\Scripts\python.exe -m pytest tests/test_module_registry.py -v` → **7 passed, 1 skipped**
  (the skip is the deliberate cycle-detection TODO above).
- `.venv\Scripts\python.exe -m pytest -q` (full repo suite) → **2613 passed, 54 skipped, 7 failed,
  7 errored** in ~8 minutes. All 14 failures/errors are pre-existing and unrelated to this task:
  - `Vol_Suite/tests/test_dual_pipeline_gate_v2.py` (1) and `test_dual_pipeline_gate_v7.py` (6) —
    dealer-model numerics, documented as pre-existing in `.superpowers/sdd/progress.md` (e.g. the
    2026-08-30 Jump-Diffusion ledger entry, Task 14).
  - `tests/test_decode_upis.py::TestRunBatch` (6 errors) and `tests/test_phase2.py::test` (1 error) —
    also documented pre-existing elsewhere in the progress ledger.
  - Confirmed via `git status --porcelain`: this task's 6 new files (`shared/module_registry.py` +
    4 suite `module_registry.py` files + `tests/test_module_registry.py`) are all newly-added/untracked
    — nothing pre-existing was modified, so these failures cannot be regressions from this change.

## Files changed
- New: `shared/module_registry.py`
- New: `Vol_Suite/module_registry.py`
- New: `Options_Suite/module_registry.py`
- New: `VaR_Tools_Simulations/module_registry.py`
- New: `sentiment-scanner/module_registry.py`
- New: `tests/test_module_registry.py`
- Untouched (verified, not part of this commit): `Tools/registry.py`, `orchestrator.py`,
  `dashboard/app.py`, all suite `main.py`/`volatility_suite.py` entry points.

## Self-review findings
- No functional issues found. `from_tool_spec`'s `run` wrapper was the one place the brief's shapes
  required active reconciliation (bare-dict `ToolSpec.run` vs. `ModuleResult`-returning
  `ModuleSpec.run`) rather than a straight field copy — called out above since it's the one place a
  reviewer should double check the mapping is faithful to "strict subset" as the brief describes it.
- Cycle detection deliberately deferred per brief's own stated option; disclosed explicitly per the
  brief's instruction to "say so explicitly in your report" (see above).
- No CLI flags, no suite `MODULES` population, no `orchestrator.py`/`dashboard/app.py`/suite
  `main.py` edits — confirmed against the brief's "What NOT to do" list.

## Concerns
None blocking. One forward-looking note for whichever task next populates a suite's `MODULES` list:
`test_all_modules_has_nothing_yet_from_the_four_suite_registries` in this task's test file asserts
`all_modules()` equals exactly the adapted `Tools/registry.py` set — that assertion is correct only
for Phase 1 and will need to be updated (or replaced with a suite-scoped assertion) once any suite
registry stops being empty.
