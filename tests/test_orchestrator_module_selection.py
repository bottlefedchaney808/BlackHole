"""test_orchestrator_module_selection.py

Covers the module-execution path that survives Phase 7 (retire orchestrator
launching machinery): `run_selected_modules` over `shared/module_registry.py`'s
registry contract. Phase 7 relocated `run_selected_modules` /
`_expand_module_requires` / `_topo_sort_modules` verbatim into
`shared/module_execution.py`; orchestrator.py re-exports them, so every test
here monkeypatches `all_modules`/`resolve_modules`/`_archive_module_result` on
the orchestrator namespace exactly as before -- the relocation shim keeps those
patches effective.

The invariant these tests encode -- the module-execution path does NOT call
`run_suite`/`run_unified` (both removed by Phase 7) -- still applies to the
surviving in-process run path (`shared.module_execution.run_selected_modules`
and the dashboard's `POST /api/widgets/{slug}/run` route).

The former `TestDashboardModulesDispatch` class, which exercised
`dashboard/app.py`'s removed `_execute_run`/`_RUNS`/`POST /run/{suite_or_unified}`
trigger machinery, was deleted with that machinery in Phase 7 step 3; the
widget-run dispatch it guarded is now covered by `dashboard/tests/` against the
generic widget routes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import orchestrator
from shared.module_registry import (
    ModuleResult,
    ModuleSpec,
    resolve_modules,
)

pytestmark = pytest.mark.unit


def _make_module(
    slug: str,
    *,
    default_selected: bool = False,
    requires: list[str] | None = None,
    run=None,
) -> ModuleSpec:
    if run is None:

        def run(context: dict) -> ModuleResult:
            return ModuleResult(
                status="ok", artifacts=[], metrics={}, context_patch=None
            )

    return ModuleSpec(
        name=f"Stub {slug}",
        slug=slug,
        suite="tools",
        category="stub",
        run=run,
        cli_entry=None,
        default_selected=default_selected,
        requires=requires or [],
    )


# --------------------------------------------------------------------------
# run_selected_modules: subset selection, ordering, default fallback,
# requires expansion
# --------------------------------------------------------------------------


class TestRunSelectedModulesSubsetSelection:
    def test_selecting_a_subset_runs_only_that_subset(self, monkeypatch):
        module_a = _make_module("stub_a")
        module_b = _make_module("stub_b")
        registry = [module_a, module_b]

        monkeypatch.setattr(orchestrator, "all_modules", lambda: registry)
        monkeypatch.setattr(
            orchestrator, "resolve_modules", lambda slugs: resolve_modules(slugs)
        )
        # resolve_modules looks up shared.module_registry.all_modules(), not
        # orchestrator's monkeypatched name -- patch it at the source too so
        # the real resolve_modules() sees the stub registry.
        import shared.module_registry as module_registry_mod

        monkeypatch.setattr(module_registry_mod, "all_modules", lambda: registry)

        combined = orchestrator.run_selected_modules(["stub_a"], {})

        assert combined["status"] == "ok"
        assert combined["order"] == ["stub_a"]
        assert set(combined["results"].keys()) == {"stub_a"}
        assert combined["results"]["stub_a"].status == "ok"


class TestRunSelectedModulesDefaultFallback:
    def test_empty_selection_falls_back_to_default_selected_only(self, monkeypatch):
        default_module = _make_module("stub_default", default_selected=True)
        other_module = _make_module("stub_other", default_selected=False)
        registry = [default_module, other_module]

        monkeypatch.setattr(orchestrator, "all_modules", lambda: registry)

        combined = orchestrator.run_selected_modules([], {})

        assert combined["order"] == ["stub_default"]
        assert set(combined["results"].keys()) == {"stub_default"}

    def test_no_default_selected_modules_means_nothing_runs(self, monkeypatch):
        registry = [_make_module("stub_a"), _make_module("stub_b")]
        monkeypatch.setattr(orchestrator, "all_modules", lambda: registry)

        combined = orchestrator.run_selected_modules([], {})

        assert combined["order"] == []
        assert combined["results"] == {}


class TestRunSelectedModulesRequiresExpansion:
    def test_requires_dependency_auto_included_and_runs_before_dependent(
        self, monkeypatch
    ):
        dep = _make_module("stub_dep")
        dependent = _make_module("stub_dependent", requires=["stub_dep"])
        registry = [dep, dependent]

        monkeypatch.setattr(orchestrator, "all_modules", lambda: registry)
        import shared.module_registry as module_registry_mod

        monkeypatch.setattr(module_registry_mod, "all_modules", lambda: registry)

        combined = orchestrator.run_selected_modules(["stub_dependent"], {})

        # stub_dep wasn't explicitly selected, but stub_dependent requires
        # it -- it must still execute, and before stub_dependent.
        assert set(combined["order"]) == {"stub_dep", "stub_dependent"}
        assert combined["order"].index("stub_dep") < combined["order"].index(
            "stub_dependent"
        )
        assert set(combined["results"].keys()) == {"stub_dep", "stub_dependent"}

    def test_transitive_requires_chain_fully_expanded_and_ordered(self, monkeypatch):
        a = _make_module("stub_chain_a")
        b = _make_module("stub_chain_b", requires=["stub_chain_a"])
        c = _make_module("stub_chain_c", requires=["stub_chain_b"])
        registry = [a, b, c]

        monkeypatch.setattr(orchestrator, "all_modules", lambda: registry)
        import shared.module_registry as module_registry_mod

        monkeypatch.setattr(module_registry_mod, "all_modules", lambda: registry)

        combined = orchestrator.run_selected_modules(["stub_chain_c"], {})

        order = combined["order"]
        assert set(order) == {"stub_chain_a", "stub_chain_b", "stub_chain_c"}
        assert order.index("stub_chain_a") < order.index("stub_chain_b")
        assert order.index("stub_chain_b") < order.index("stub_chain_c")

    def test_unknown_requires_slug_raises_value_error(self, monkeypatch):
        broken = _make_module("stub_broken", requires=["not_registered"])
        registry = [broken]
        monkeypatch.setattr(orchestrator, "all_modules", lambda: registry)
        import shared.module_registry as module_registry_mod

        monkeypatch.setattr(module_registry_mod, "all_modules", lambda: registry)

        with pytest.raises(ValueError, match="not_registered"):
            orchestrator.run_selected_modules(["stub_broken"], {})


class TestRunSelectedModulesContextPatchAndArchiveHook:
    def test_context_patch_merged_and_visible_to_later_modules(self, monkeypatch):
        def run_first(context: dict) -> ModuleResult:
            return ModuleResult(
                status="ok", artifacts=[], metrics={}, context_patch={"garch_vol": 0.42}
            )

        seen_by_second: dict = {}

        def run_second(context: dict) -> ModuleResult:
            seen_by_second.update(context)
            return ModuleResult(
                status="ok", artifacts=[], metrics={}, context_patch=None
            )

        first = _make_module("stub_first", run=run_first)
        second = _make_module("stub_second", requires=["stub_first"], run=run_second)
        registry = [first, second]

        monkeypatch.setattr(orchestrator, "all_modules", lambda: registry)
        import shared.module_registry as module_registry_mod

        monkeypatch.setattr(module_registry_mod, "all_modules", lambda: registry)

        orchestrator.run_selected_modules(["stub_second"], {"ticker": "NVDA"})

        assert seen_by_second["ticker"] == "NVDA"
        assert seen_by_second["garch_vol"] == 0.42

    def test_archive_hook_called_once_per_executed_module(self, monkeypatch):
        registry = [_make_module("stub_a"), _make_module("stub_b")]
        monkeypatch.setattr(orchestrator, "all_modules", lambda: registry)

        calls: list[str] = []
        monkeypatch.setattr(
            orchestrator,
            "_archive_module_result",
            lambda slug, result, context: calls.append(slug),
        )

        import shared.module_registry as module_registry_mod

        monkeypatch.setattr(module_registry_mod, "all_modules", lambda: registry)
        combined = orchestrator.run_selected_modules(["stub_a", "stub_b"], {})

        assert calls == combined["order"]


# --------------------------------------------------------------------------
# Surviving run-path invariant (widget-run reality): the in-process module
# execution the dashboard's POST /api/widgets/{slug}/run and shared.module_execution
# depend on never dispatches through run_suite/run_unified -- both removed in
# Phase 7. Run through the orchestrator re-export (the shim) to prove the
# dispatch stays on the module-execution path.
# --------------------------------------------------------------------------


class TestModuleExecutionDispatchInvariant:
    def test_run_selected_modules_does_not_call_suite_launchers(self, monkeypatch):
        # If run_selected_modules ever routes back through the removed
        # run_suite/run_unified launchers this boom fires. (setattr works even
        # though the names are gone -- the module namespace still accepts them,
        # which is exactly the failure we want to catch.)
        registry = [_make_module("stub_a")]
        monkeypatch.setattr(orchestrator, "all_modules", lambda: registry)
        import shared.module_registry as module_registry_mod

        monkeypatch.setattr(module_registry_mod, "all_modules", lambda: registry)

        for removed in ("run_suite", "run_unified"):

            def _boom(*args, **kwargs):
                raise AssertionError(f"{removed} must not be called on the modules path")

            monkeypatch.setattr(orchestrator, removed, _boom)

        combined = orchestrator.run_selected_modules(["stub_a"], {})
        assert combined["status"] == "ok"
        assert combined["order"] == ["stub_a"]

