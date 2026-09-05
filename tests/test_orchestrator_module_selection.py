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
                raise AssertionError(
                    f"{removed} must not be called on the modules path"
                )

            monkeypatch.setattr(orchestrator, removed, _boom, raising=False)
        combined = orchestrator.run_selected_modules(["stub_a"], {})
        assert combined["status"] == "ok"
        assert combined["order"] == ["stub_a"]


# --------------------------------------------------------------------------
# C1: run_id + default output_dir in run_selected_modules
# --------------------------------------------------------------------------

import datetime
import re


class TestRunSelectedModulesRunIdAndOutputDir:
    def test_no_output_dir_mints_run_id_and_creates_output_dir(self, monkeypatch, tmp_path):
        module_a = _make_module("stub_a")
        registry = [module_a]

        monkeypatch.setattr(orchestrator, "all_modules", lambda: registry)
        import shared.module_registry as module_registry_mod
        monkeypatch.setattr(module_registry_mod, "all_modules", lambda: registry)

        context = {"ticker": "SPY"}
        # Override repo_root to use tmp_path for test isolation
        import shared.artifact_paths as artifact_paths_mod
        monkeypatch.setattr(artifact_paths_mod, "repo_root", lambda: tmp_path)

        result = orchestrator.run_selected_modules(["stub_a"], context)

        # run_id should be minted with format YYYYMMDDTHHMMSSZ-<4 hex>
        run_id = context.get("run_id")
        assert run_id is not None
        assert re.match(r"\d{8}T\d{6}Z-[0-9a-f]{4}", run_id)

        # output_dir should be created as outputs/<run_id>/
        output_dir = context.get("output_dir")
        assert output_dir is not None
        expected_dir = tmp_path / "outputs" / run_id
        assert Path(output_dir) == expected_dir
        assert expected_dir.is_dir()

        # run result shape unchanged
        assert result["status"] == "ok"
        assert "order" in result
        assert "results" in result

    def test_explicit_output_dir_respected_run_id_still_set(self, monkeypatch, tmp_path):
        module_a = _make_module("stub_a")
        registry = [module_a]

        monkeypatch.setattr(orchestrator, "all_modules", lambda: registry)
        import shared.module_registry as module_registry_mod
        monkeypatch.setattr(module_registry_mod, "all_modules", lambda: registry)

        explicit_dir = tmp_path / "custom" / "output" / "dir"
        explicit_dir.mkdir(parents=True, exist_ok=True)  # Caller creates the dir
        context = {"ticker": "SPY", "output_dir": str(explicit_dir)}

        import shared.artifact_paths as artifact_paths_mod
        monkeypatch.setattr(artifact_paths_mod, "repo_root", lambda: tmp_path)

        result = orchestrator.run_selected_modules(["stub_a"], context)

        # run_id should still be set
        run_id = context.get("run_id")
        assert run_id is not None
        assert re.match(r"\d{8}T\d{6}Z-[0-9a-f]{4}", run_id)

        # explicit output_dir should be respected
        output_dir = context.get("output_dir")
        assert output_dir == str(explicit_dir)
        assert explicit_dir.is_dir()

        # run result shape unchanged
        assert result["status"] == "ok"
        assert "order" in result
        assert "results" in result

    def test_run_returns_same_shape_with_or_without_mutation(self, monkeypatch, tmp_path):
        module_a = _make_module("stub_a")
        module_b = _make_module("stub_b", requires=["stub_a"])
        registry = [module_a, module_b]

        monkeypatch.setattr(orchestrator, "all_modules", lambda: registry)
        import shared.module_registry as module_registry_mod
        monkeypatch.setattr(module_registry_mod, "all_modules", lambda: registry)

        import shared.artifact_paths as artifact_paths_mod
        monkeypatch.setattr(artifact_paths_mod, "repo_root", lambda: tmp_path)

        # Test without explicit output_dir
        context1 = {"ticker": "NVDA"}
        result1 = orchestrator.run_selected_modules(["stub_b"], context1)
        assert result1["status"] == "ok"
        assert result1["order"] == ["stub_a", "stub_b"]
        assert "results" in result1

        # Test with explicit output_dir
        explicit_dir = tmp_path / "explicit_run"
        context2 = {"ticker": "AAPL", "output_dir": str(explicit_dir)}
        result2 = orchestrator.run_selected_modules(["stub_b"], context2)
        assert result2["status"] == "ok"
        assert result2["order"] == ["stub_a", "stub_b"]
        assert "results" in result2


# C2: run_manifest.json at end of run_selected_modules
# --------------------------------------------------------------------------

import json
from pathlib import Path


class TestRunManifestJson:
    def test_run_manifest_written_for_two_module_run(self, monkeypatch, tmp_path):
        """Stubbed 2-module run (one ok, one status=error) -> run_manifest.json exists."""
        module_a = _make_module("stub_a")
        module_b = _make_module("stub_b")

        # Override run to return status="error" for module_b
        def run_error(context: dict) -> ModuleResult:
            return ModuleResult(
                status="error",
                artifacts=[],
                metrics={"error_reason": "test failure"},
                context_patch=None,
            )

        module_b_error = _make_module("stub_b", run=run_error)

        registry = [module_a, module_b_error]
        monkeypatch.setattr(orchestrator, "all_modules", lambda: registry)
        import shared.module_registry as module_registry_mod
        monkeypatch.setattr(module_registry_mod, "all_modules", lambda: registry)

        import shared.artifact_paths as artifact_paths_mod
        monkeypatch.setattr(artifact_paths_mod, "repo_root", lambda: tmp_path)

        context = {"ticker": "SPY"}
        result = orchestrator.run_selected_modules(["stub_a", "stub_b"], context)

        # Verify run result shape
        assert result["status"] == "ok"
        assert result["order"] == ["stub_a", "stub_b"]
        assert "results" in result
        assert result["results"]["stub_a"].status == "ok"
        assert result["results"]["stub_b"].status == "error"

        # Verify run_manifest.json was written
        run_id = context.get("run_id")
        assert run_id is not None

        manifest_path = tmp_path / "outputs" / run_id / "run_manifest.json"
        assert manifest_path.exists(), f"run_manifest.json not found at {manifest_path}"

        manifest = json.loads(manifest_path.read_text())

        # Verify manifest structure
        assert manifest["run_id"] == run_id
        assert manifest["order"] == ["stub_a", "stub_b"]
        assert "results" in manifest
        assert "artifacts" in manifest
        assert "started_at" in manifest
        assert "ended_at" in manifest

        # Verify per-module results
        assert manifest["results"]["stub_a"]["status"] == "ok"
        assert manifest["results"]["stub_b"]["status"] == "error"
        assert manifest["results"]["stub_b"]["error"] is None
        assert manifest["results"]["stub_a"]["ticker"] == "SPY"
        assert manifest["results"]["stub_b"]["ticker"] == "SPY"

        # Verify artifacts dict structure
        assert "stub_a" in manifest["artifacts"]
        assert "stub_b" in manifest["artifacts"]

    def test_run_manifest_has_correct_timestamps(self, monkeypatch, tmp_path):
        """run_manifest timestamps are ISO UTC format."""
        module_a = _make_module("stub_a")
        registry = [module_a]
        monkeypatch.setattr(orchestrator, "all_modules", lambda: registry)
        import shared.module_registry as module_registry_mod
        monkeypatch.setattr(module_registry_mod, "all_modules", lambda: registry)

        import shared.artifact_paths as artifact_paths_mod
        monkeypatch.setattr(artifact_paths_mod, "repo_root", lambda: tmp_path)

        context = {"ticker": "AAPL"}
        result = orchestrator.run_selected_modules(["stub_a"], context)

        run_id = context.get("run_id")
        manifest_path = tmp_path / "outputs" / run_id / "run_manifest.json"
        manifest = json.loads(manifest_path.read_text())

        # Verify timestamps are ISO format (can be parsed)
        from datetime import datetime

        started_at = datetime.fromisoformat(manifest["started_at"])
        ended_at = datetime.fromisoformat(manifest["ended_at"])

        assert started_at is not None
        assert ended_at is not None
        assert ended_at >= started_at


# C4: agent runs persist context_patch into Context Store
# --------------------------------------------------------------------------

import logging
from unittest.mock import patch, MagicMock


class TestContextPatchPersistence:
    def test_context_patch_stored_for_module_run(self, monkeypatch, tmp_path):
        """Stub module with context_patch -> store contains entry for scope."""
        from shared.context_store import ContextStore

        def run_with_patch(context: dict) -> ModuleResult:
            return ModuleResult(
                status="ok",
                artifacts=[],
                metrics={},
                context_patch={"garch_vol": 0.42, "iv_rank": 0.65},
            )

        module_a = _make_module("stub_a", run=run_with_patch)
        registry = [module_a]

        monkeypatch.setattr(orchestrator, "all_modules", lambda: registry)
        import shared.module_registry as module_registry_mod
        monkeypatch.setattr(module_registry_mod, "all_modules", lambda: registry)

        import shared.artifact_paths as artifact_paths_mod
        monkeypatch.setattr(artifact_paths_mod, "repo_root", lambda: tmp_path)

        context = {"ticker": "SPY", "output_dir": str(tmp_path / "outputs")}

        # Patch ContextStore to track calls
        mock_store = MagicMock()
        monkeypatch.setattr("shared.context_store.ContextStore", lambda *a, **kw: mock_store)

        orchestrator.run_selected_modules(["stub_a"], context)

        # Verify put was called for each key in context_patch
        assert mock_store.put.call_count >= 2  # At least 2 keys: garch_vol, iv_rank

        # Verify scope is correct (ticker:SPY) for at least one call
        found_spy_scope = False
        for call in mock_store.put.call_args_list:
            scope_arg = call[0][0]
            if isinstance(scope_arg, dict):
                if scope_arg.get("ticker") == "SPY":
                    found_spy_scope = True
                    break
        assert found_spy_scope, "No call found with ticker:SPY scope"

    def test_context_store_failure_does_not_fail_run(self, monkeypatch, tmp_path, caplog):
        """ContextStore patched to raise -> run succeeds, warning logged."""
        from shared.context_store import ContextStore

        def run_with_patch(context: dict) -> ModuleResult:
            return ModuleResult(
                status="ok",
                artifacts=[],
                metrics={},
                context_patch={"garch_vol": 0.42},
            )

        module_a = _make_module("stub_a", run=run_with_patch)
        registry = [module_a]

        monkeypatch.setattr(orchestrator, "all_modules", lambda: registry)
        import shared.module_registry as module_registry_mod
        monkeypatch.setattr(module_registry_mod, "all_modules", lambda: registry)

        import shared.artifact_paths as artifact_paths_mod
        monkeypatch.setattr(artifact_paths_mod, "repo_root", lambda: tmp_path)

        context = {"ticker": "SPY", "output_dir": str(tmp_path / "outputs")}

        # Patch ContextStore.put to raise
        with patch.object(ContextStore, "put", side_effect=Exception("DB error")):
            # The run should still succeed despite ContextStore failure
            result = orchestrator.run_selected_modules(["stub_a"], context)

            assert result["status"] == "ok"
            assert "stub_a" in result["results"]
            assert result["results"]["stub_a"].status == "ok"

        # Verify warning was logged
        log_messages = [r.getMessage() for r in caplog.records]
        assert any("context_patch" in msg.lower() or "context store" in msg.lower() for msg in log_messages)

    def test_context_patch_persists_to_store_in_integration(self, monkeypatch, tmp_path):
        """Full integration: context_patch actually gets stored via real ContextStore."""
        from shared.context_store import ContextStore

        def run_with_patch(context: dict) -> ModuleResult:
            return ModuleResult(
                status="ok",
                artifacts=[],
                metrics={},
                context_patch={"garch_vol": 0.42},
            )

        module_a = _make_module("stub_a", run=run_with_patch)
        registry = [module_a]

        monkeypatch.setattr(orchestrator, "all_modules", lambda: registry)
        import shared.module_registry as module_registry_mod
        monkeypatch.setattr(module_registry_mod, "all_modules", lambda: registry)

        import shared.artifact_paths as artifact_paths_mod
        monkeypatch.setattr(artifact_paths_mod, "repo_root", lambda: tmp_path)

        db_path = tmp_path / "test_context.db"
        context = {"ticker": "SPY", "output_dir": str(tmp_path / "outputs")}

        with ContextStore(db_path) as store:
            # Patch ContextStore constructor to use our test db
            monkeypatch.setattr("shared.context_store.ContextStore", lambda *a, **kw: store)

            orchestrator.run_selected_modules(["stub_a"], context)

            # Verify the value was stored
            result = store.get({"ticker": "SPY"}, "garch_vol")
            assert result == 0.42
