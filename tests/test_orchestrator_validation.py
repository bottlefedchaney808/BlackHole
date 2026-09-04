"""Tests for what survives the Phase 7 orchestrator-launcher retirement.

Phase 7 removed orchestrator.py's `run_suite` / `run_unified` / CLI
`--unified` / `--suite` and the dashboard `trigger_run` dispatch they fed;
their per-suite marker validation and PASS/FAIL folding is now exercised by
`tests/test_suite_validation.py` and `dashboard/tests/` against the generic
widget-run routes. The former `run_suite`/`run_unified` wiring tests in this
file were superseded and deleted with the machinery they drove.

What remains is the module-execution shim contract the retirement introduced,
and the surviving validation-relevant surface still owned by orchestrator.py:

- `shared/module_execution.py` owns `run_selected_modules` /
  `_expand_module_requires` / `_topo_sort_modules` (relocated verbatim out of
  orchestrator.py); orchestrator.py re-exports them so every existing importer
  (`from orchestrator import run_selected_modules`) keeps resolving through one
  shared entry point. This is the Phase 7 shim contract.
- `orchestrator.build_context` still exists and still refuses an empty ticker
  (the same guard the removed unified flow relied on), so that validation
  behaviour is asserted here so it is not lost silently.
"""

import pytest

import orchestrator

pytestmark = pytest.mark.unit


# ── Phase 7 shim contract ────────────────────────────────────────────────
# run_selected_modules and its two helpers were relocated verbatim into
# shared/module_execution.py; orchestrator.py must re-export the SAME objects
# (identity, not a wrapper) so existing importers and monkeypatch-based tests
# keep working against the orchestrator namespace.

def test_shared_module_execution_is_importable():
    import shared.module_execution

    assert callable(shared.module_execution.run_selected_modules)
    assert callable(shared.module_execution._expand_module_requires)
    assert callable(shared.module_execution._topo_sort_modules)


def test_orchestrator_reexports_run_selected_modules_from_shared():
    import shared.module_execution

    # Identity: orchestrator must hand back the exact relocated function, so a
    # monkeypatch on either name is seen by the other.
    assert orchestrator.run_selected_modules is shared.module_execution.run_selected_modules


def test_orchestrator_reexports_the_requires_helpers_too():
    import shared.module_execution

    assert orchestrator._expand_module_requires is shared.module_execution._expand_module_requires
    assert orchestrator._topo_sort_modules is shared.module_execution._topo_sort_modules


def test_orchestrator_module_execution_surface_is_still_reachable():
    # The surviving names Phase 7 kept on orchestrator.py that the module-exec
    # path and run path depend on must not have silently vanished with the
    # launcher removal.
    for name in (
        "run_selected_modules",
        "build_context",
        "run_market_signals_stage",
        "log_run",
        "SHARED_PYTHON",
        "DB_PATH",
        "DEFAULT_TIMEOUT_SEC",
    ):
        assert hasattr(orchestrator, name), f"orchestrator.{name} should survive Phase 7"


# ── build_context validation that survives ──────────────────────────────
# build_context is not removed by Phase 7 (it is the context builder the
# module-exec / widget-run path still calls). Its schema guard -- a run must
# name a ticker -- is asserted here so accidental deletion is caught.

def test_build_context_requires_a_ticker():
    with pytest.raises(ValueError, match="ticker"):
        orchestrator.build_context({}, None)


def test_build_context_accepts_a_focus_ticker(monkeypatch):
    # Cheap, network-free: with a ticker and no real basket/var resolution
    # reachable, build_context must still return a suite_context-shaped dict
    # carrying the ticker rather than raising. Stub the expensive peers/resolvers.
    import shared.module_registry  # noqa: F401  (import contract check)
    from orchestrator import _import_suite_context

    # _import_suite_context imports suite_context lazily from Vol_Suite's dir;
    # only reachable when that package imports cleanly. If not (broken local
    # env), skip rather than fail the surviving-surface test.
    try:
        _import_suite_context()
    except Exception:
        pytest.skip("Vol_Suite suite_context not importable in this env")
    monkeypatch.setattr(orchestrator, "get_recent_swap_activity", lambda *a, **k: [])
    ctx = orchestrator.build_context(
        {"ticker": "NVDA", "target_years": 0.25}, None
    )
    assert isinstance(ctx, dict)
    assert ctx["focus"]["ticker"] == "NVDA"
