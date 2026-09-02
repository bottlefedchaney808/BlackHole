"""test_orchestrator_module_selection.py

Covers Task 2 of the modularization overhaul (`.superpowers/sdd/task-2-brief.md`):
`orchestrator.py::run_selected_modules`, a new, additive execution path over
`shared/module_registry.py`'s registry contract (Task 1), parallel to (never
calling, never called by) the existing `run_suite`/`run_unified` functions.

Task 1's own registries are still empty (all four suites' `MODULES` lists are
`[]`), so every test here injects stub `ModuleSpec` objects directly --
matching the pattern `tests/test_module_registry.py`'s own cycle-detection
placeholder test used -- rather than relying on real suite modules.

`orchestrator.py` imports `all_modules`/`resolve_modules` by name
(`from shared.module_registry import (...)`), so tests monkeypatch those
names on the `orchestrator` module itself, not on `shared.module_registry`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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
# Dashboard POST /run/{kind}: modules field routes to run_selected_modules;
# absence routes to the existing run_suite/run_unified path unchanged.
# --------------------------------------------------------------------------


class TestDashboardModulesDispatch:
    @pytest.fixture(autouse=True)
    def _isolate_runs_registry(self):
        import dashboard.app as dashboard_app

        saved = dict(dashboard_app._RUNS)
        dashboard_app._RUNS.clear()
        yield
        dashboard_app._RUNS.clear()
        dashboard_app._RUNS.update(saved)

    def test_execute_run_with_modules_calls_run_selected_modules(
        self, monkeypatch, tmp_path
    ):
        import dashboard.app as dashboard_app

        fake_context = {"output_dir": str(tmp_path), "run_id": "ctx-1"}
        monkeypatch.setattr(orchestrator, "build_context", lambda focus: fake_context)

        calls = {}

        def fake_run_selected_modules(slugs, context):
            calls["slugs"] = slugs
            calls["context"] = context
            return {"status": "ok", "order": list(slugs), "results": {}}

        monkeypatch.setattr(
            orchestrator, "run_selected_modules", fake_run_selected_modules
        )

        # A regression guard: run_suite/run_unified must NOT be invoked on
        # this path.
        def _boom(*args, **kwargs):
            raise AssertionError("run_suite must not be called on the modules path")

        monkeypatch.setattr(orchestrator, "run_suite", _boom)

        dashboard_app._execute_run(
            "test-modules-1", "vol", {"ticker": "NVDA"}, ["stub_a", "stub_b"]
        )

        assert calls["slugs"] == ["stub_a", "stub_b"]
        live = dashboard_app._RUNS["test-modules-1"]
        assert live["status"] == "ok"

    def test_execute_run_without_modules_still_uses_existing_path(
        self, monkeypatch, tmp_path
    ):
        """Explicit regression assertion, not an assumption: `modules=None`
        (the default) must dispatch through the pre-existing run_suite path,
        never run_selected_modules."""
        import dashboard.app as dashboard_app

        fake_context = {"output_dir": str(tmp_path), "run_id": "ctx-2"}
        monkeypatch.setattr(orchestrator, "build_context", lambda focus: fake_context)
        monkeypatch.setattr(
            orchestrator,
            "run_suite",
            lambda name, ctx, timeout=1800: {"status": "ok"},
        )

        def _boom(*args, **kwargs):
            raise AssertionError(
                "run_selected_modules must not be called without a modules field"
            )

        monkeypatch.setattr(orchestrator, "run_selected_modules", _boom)

        dashboard_app._execute_run("test-no-modules-1", "vol", {"ticker": "NVDA"})

        live = dashboard_app._RUNS["test-no-modules-1"]
        assert live["status"] == "ok"

    def test_post_run_with_modules_field_routes_to_run_selected_modules(
        self, monkeypatch, tmp_path
    ):
        import dashboard.app as dashboard_app

        client = TestClient(dashboard_app.app)

        fake_context = {"output_dir": str(tmp_path), "run_id": "ctx-3"}
        monkeypatch.setattr(orchestrator, "build_context", lambda focus: fake_context)

        called_with = {}

        def fake_run_selected_modules(slugs, context):
            called_with["slugs"] = slugs
            return {"status": "ok", "order": list(slugs), "results": {}}

        monkeypatch.setattr(
            orchestrator, "run_selected_modules", fake_run_selected_modules
        )

        def _boom(*args, **kwargs):
            raise AssertionError("run_suite must not be called on the modules path")

        monkeypatch.setattr(orchestrator, "run_suite", _boom)
        monkeypatch.setattr(orchestrator, "run_unified", _boom)

        resp = client.post(
            "/run/vol",
            json={"ticker": "NVDA", "modules": ["stub_a"]},
        )
        assert resp.status_code == 202

        # _execute_run runs as a BackgroundTask -- TestClient executes it
        # synchronously before returning the response in FastAPI's default
        # (non-anyio-worker) test setup.
        assert called_with.get("slugs") == ["stub_a"]

    def test_post_run_without_modules_field_routes_to_existing_path(
        self, monkeypatch, tmp_path
    ):
        """Explicit regression assertion for the endpoint layer: a POST body
        with no `modules` key must still hit run_suite, never
        run_selected_modules."""
        import dashboard.app as dashboard_app

        client = TestClient(dashboard_app.app)

        fake_context = {"output_dir": str(tmp_path), "run_id": "ctx-4"}
        monkeypatch.setattr(orchestrator, "build_context", lambda focus: fake_context)

        called = {"run_suite": False}

        def fake_run_suite(name, ctx, timeout=1800):
            called["run_suite"] = True
            return {"status": "ok"}

        monkeypatch.setattr(orchestrator, "run_suite", fake_run_suite)

        def _boom(*args, **kwargs):
            raise AssertionError(
                "run_selected_modules must not be called without a modules field"
            )

        monkeypatch.setattr(orchestrator, "run_selected_modules", _boom)

        resp = client.post("/run/vol", json={"ticker": "NVDA"})
        assert resp.status_code == 202
        assert called["run_suite"] is True
