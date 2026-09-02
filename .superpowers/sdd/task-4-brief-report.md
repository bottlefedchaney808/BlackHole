# Task 4 Brief Report — Vol_Suite chain_scanner + svi_smile registry entries

**Plan:** Modularization Overhaul, Phase 3 (Vol_Suite split), sub-task 2 of 3
(`C:\Users\bottl\.claude\plans\i-want-to-do-cosmic-turing.md`)
**Ledger section:** "Modularization Overhaul — started 2026-09-01" in `.superpowers/sdd/progress.md`
**Distinct from:** `task-4-report.md` (stale Jump-Diffusion plan report — left untouched)
**Session:** completed 2026-09-02 by controller continuation (handoff: [[FD Modularization handoff 20260902]])

## What was implemented

`Vol_Suite/module_registry.py` (+~355 lines) adds two `ModuleSpec` entries, following Task 3's
adapter pattern:

- **`chain_scanner`** — thin adapter over `options_chain_scanner.run_chain_scanner`
  (category `scanner`, `default_selected=False`, `requires=[]`, `archive.key_shape="ticker_expiry"`).
  Fail-loud wrapper (`except Exception -> _failed()`), picks up `chain_strategies.json` as a third
  artifact when present on disk (run_chain_scanner writes it but doesn't return it).
  `cli_entry=None`: options_chain_scanner.py's `main()` (line 1219) is fully `input()`-interactive,
  and the brief forbids new CLI files unless missing AND trivial — it isn't. Task 2's
  `run_selected_modules` in-process path is the launch surface.
- **`svi_smile`** — new dispatch glue (not a single-call-site wrapper): `context["mode"]` ∈
  {chain_scanner, exposure_overlay, model_comparison}, default chain_scanner. Chain-scanner mode
  mirrors scan_chain's real pre-fit plumbing line-for-line (`_svi_chain_scanner_inputs` vs
  options_chain_scanner.py:642-652); exposure-overlay mode reuses
  `expiry_book_production.normalize_snapshot_rows/_merge_greeks_oi` (the put-call-parity
  ITM-leg-coverage fix) instead of re-deriving it; model-comparison mode calls
  `Options_Suite/smile_by_model.build_iv_smile_by_model` directly. Fail-loud on unknown mode,
  missing ticker, simulated fetch failures. Owns its ThetaDataController lifecycle (create if not
  injected, close in finally).

## Disclosed deviations from the brief

1. **Brief's premise partially false, disclosed in code comment:** brief said all three SVI call
   sites "ultimately delegate to svi_rp.calibrate_svi". Verified false for `model_comparison`:
   `build_iv_smile_by_model` never imports svi_rp (grep across Options_Suite/) — it builds each
   pricing model's own IV curve vs. vendor IV. Not blocking (brief's scope was a shared standalone
   entry point, which still applies); documented in the module's header comment.
2. **Options_Suite sys.path: appended, never inserted at index 0** — its flat `expiry_selector.py`
   would shadow Vol_Suite's (Options_Suite's copy lacks `DEFAULT_A`; earlier crash: AttributeError
   in dealer_positioning.py).
3. **2-line relax of `test_module_registry_dealer_book.py`:** Task 3's exact-set slug assertion
   became subset; repo-root import count 4 → 6. Mechanically required by adding entries to the
   same MODULES list.

## Bugs found and fixed this session (beyond the handoff state)

1. **Silent module-vanish under Tools-first import order (REAL, latent, production-relevant).**
   `Tools/tools/surface_explorer_tool.py:45-48` front-inserts Options_Suite/ into sys.path at
   module scope (eagerly imported by Tools/registry.py). In any process importing Tools/registry
   before Vol_Suite.module_registry (the dashboard does exactly this), Vol_Suite/ sat BEHIND
   Options_Suite/, module_registry's membership-guarded insert skipped, flat
   `import expiry_selector` resolved to Options_Suite's shadowing copy → AttributeError →
   swallowed by `shared.module_registry._suite_modules()` (defensive by design) → all 6 vol_suite
   modules silently missing from `all_modules()`. Reproduced in a fresh subprocess
   (Tools-first order, pre-fix: suite_slugs empty).
   **Fix:** move-to-front (`sys.path.remove` + `insert(0)`) for Vol_Suite/ in module_registry.py,
   documented in-code. **Red-green proven:** new regression test
   `TestRepoRootImport.test_all_modules_survives_tools_first_import_order` fails on the old
   membership-guard code, passes with the fix. Root test `tests/test_module_registry.py` also
   went green as a consequence.
2. **Stale Phase-1 root test (pre-existing since Task 3, surfaced by full-repo gate).**
   `tests/test_module_registry.py::test_all_modules_has_nothing_yet_from_the_four_suite_registries`
   asserted all_modules() == Tools-only — true at Phase 1, false once Task 3 populated Vol_Suite's
   registry (Task 3's gates only ran Vol_Suite/tests/, never root tests/, so it slipped).
   Task 1's ledger entry pre-authorized this exact update ("will need updating once Phase 3+
   populate real MODULES"). Replaced with an open-ended aggregation test
   (`test_all_modules_aggregates_tools_plus_populated_suite_registries`): Tools entries present +
   ≥1 populated suite registry + no slug collisions — future-proof against Phases 4-8.
3. **9 ruff findings on the Task-4 diff** (module_registry.py was lint-clean on master, held to
   Task 3's standard): DTZ007 (aware-date fix, verified value-identical), BLE001 (noqa with
   precedent citation, mirroring expiry_book_production.py:265-269), UP031/UP032 (f-strings),
   PLW1510 (check=False), C408 (dict literals), RUF059 (`_strike`). All three in-scope files now
   ruff-clean.

## Gate evidence (all run this session, clean env, `.venv`, synchronous)

- Both module-registry test files: **37 passed** (pre-lint-fix), then **45 passed + 1 skip** incl.
  the new regression test and root tests/test_module_registry.py (final state).
- Full `Vol_Suite/tests/`: **1091 passed / 7 failed / 10 skipped** — failures exactly the
  documented pre-existing `test_dual_pipeline_gate_v2|v7` set; 1091 = baseline 1073 + 18 new tests.
- Phase-boundary pair (`tests/test_orchestrator_market_signals.py` +
  `VaR_Tools_Simulations/tests/test_context_builders.py`): **39 passed**.
- Full repo `pytest -q` (mid-session): 2661 passed / 8 failed / 7 errored — 7 dual_pipeline_gate
  failures + decode_upis/phase2 errors all in the documented pre-existing categories; the one new
  name, `test_module_registry.py`, was bug #2 above (since fixed).
- Final full repo `pytest -q` after all fixes: **[PENDING — filled in below]**
- Transient, not a regression: `test_options_chain_scanner.py::test_chain_scanner_includes_strategy_recommendations`
  hit a live-ThetaData httpx ReadTimeout once under parallel suite load, passes in isolation
  (7.6s) — it is a live-network integration test, untouched by Task 4.
- ruff: clean on module_registry.py, both module-registry test files, tests/test_module_registry.py.

## Files changed (Task 4 commit scope)

- `Vol_Suite/module_registry.py` (modified: chain_scanner + svi_smile entries, sys.path fix)
- `Vol_Suite/tests/test_module_registry_chain_svi.py` (new)
- `Vol_Suite/tests/test_module_registry_dealer_book.py` (modified: 2-line relax + lint)
- `tests/test_module_registry.py` (modified: stale Phase-1 aggregation test, bug #2)
- `.superpowers/sdd/task-4-brief-report.md` (this file, force-added past gitignore)
- `.superpowers/sdd/progress.md` (ledger Task 4 entry)

## Explicitly NOT touched

- `volatility_suite.py` VS_RUN_* wiring, `surface_grids.py`, and the 5 protected dealer files —
  zero diff verified via `git diff --stat`.
- The stale Jump-Diffusion-era `.superpowers/sdd/task-3/4/5/6-report.md` working-tree rewrites —
  left uncommitted.
- All unrelated dirty files from the handoff (requirements.txt, scratch files, trading_journal,
  latest_manifest.json, _expiry_falsifier_cache).
- `Tools/tools/surface_explorer_tool.py` (the other half of bug #1's cause) — out of Task 4 scope;
  the move-to-front fix in module_registry.py neutralizes it. Flagged for a future hygiene pass.

## Task 4 leftovers per brief

- `cli_entry=None` for both new modules — confirmed acceptable; no clean headless `__main__`
  exists; brief says don't build one.
- Fail-loud + mode-dispatch coverage — in the new test file, verified passing (incl. red-green for
  the sys.path regression test).
