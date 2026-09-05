"""test_run_store.py

Covers Task C2 of the FinDev Artifact Layer Redo plan: run_store.py read API
over module_archive.db + run dirs.

Tests use a tmp archive DB (get_default_index override pattern from
tests/test_module_archive.py) + tmp run dirs.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.module_archive import ArchiveIndex, set_default_index
from shared.run_store import artifacts, last_run, runs_since


def _make_archive_index(tmp_path: Path) -> tuple[ArchiveIndex, Path]:
    """Create an ArchiveIndex backed by a temp DB file."""
    db_path = str(tmp_path / "archive.db")
    index = ArchiveIndex(db_path)
    return index, Path(db_path)


class TestRunStoreApi:
    """Test the run_store read API surface."""

    def test_last_run_returns_none_when_no_archive_rows(self, tmp_path):
        """last_run with no archive rows returns None."""
        index, _ = _make_archive_index(tmp_path)
        set_default_index(index)
        try:
            result = last_run()
            assert result is None
        finally:
            set_default_index(None)
            index.close()

    def test_last_run_returns_latest_archive_row_enriched_with_manifest(
        self, tmp_path
    ):
        """last_run returns latest row enriched with manifest when dir exists."""
        index, _ = _make_archive_index(tmp_path)
        set_default_index(index)
        try:
            # Create a run dir
            run_id = "20260905T120000Z-abcd"
            run_dir = REPO_ROOT / "outputs" / run_id
            run_dir.mkdir(parents=True, exist_ok=True)

            # Write a manifest to that dir
            manifest = {
                "run_id": run_id,
                "order": ["stub_a", "stub_b"],
                "results": {
                    "stub_a": {"status": "ok", "error": None, "ticker": "SPY", "expiry": "20261016"},
                    "stub_b": {"status": "ok", "error": None, "ticker": "SPY", "expiry": "20261016"},
                },
                "artifacts": {
                    "stub_a": ["outputs/r1/chart.png"],
                    "stub_b": ["outputs/r1/results.json"],
                },
                "started_at": "2026-09-05T12:00:00+00:00",
                "ended_at": "2026-09-05T12:00:30+00:00",
            }
            (run_dir / "run_manifest.json").write_text(json.dumps(manifest))

            try:
                # Record an archive row
                from shared.module_registry import ArchiveHint, ArtifactRef, ModuleResult, ModuleSpec

                def _run(context: dict) -> ModuleResult:
                    return ModuleResult(
                        status="ok", artifacts=[], metrics={}, context_patch=None
                    )

                module_spec = ModuleSpec(
                    name="Stub A",
                    slug="stub_a",
                    suite="tools",
                    category="stub",
                    run=_run,
                    cli_entry=None,
                    default_selected=False,
                    requires=[],
                    archive=ArchiveHint(key_shape="ticker_only"),
                )

                context = {"ticker": "SPY", "run_id": run_id}
                index.record(
                    ModuleResult(status="ok", artifacts=[], metrics={}, context_patch=None),
                    module_spec,
                    context,
                    triggered_by="orchestrator",
                )

                # last_run should return the row enriched with manifest
                result = last_run()
                assert result is not None
                assert result["module_slug"] == "stub_a"
                assert result["run_id"] == run_id
                # Enrichment should contain the manifest
                assert "manifest" in result
                assert result["manifest"]["run_id"] == run_id
                assert result["manifest"]["order"] == ["stub_a", "stub_b"]
            finally:
                # Cleanup
                import shutil
                shutil.rmtree(run_dir, ignore_errors=True)
        finally:
            set_default_index(None)
            index.close()

    def test_last_run_filters_by_ticker(self, tmp_path):
        """last_run(ticker=\"SPY\") returns latest SPY row only."""
        index, _ = _make_archive_index(tmp_path)
        set_default_index(index)
        try:
            from shared.module_registry import ArchiveHint, ArtifactRef, ModuleResult, ModuleSpec

            def _run(context: dict) -> ModuleResult:
                return ModuleResult(
                    status="ok", artifacts=[], metrics={}, context_patch=None
                )

            # Row 1: TSLA
            module_spec_tsla = ModuleSpec(
                name="Stub TSLA",
                slug="stub_tsla",
                suite="tools",
                category="stub",
                run=_run,
                cli_entry=None,
                default_selected=False,
                requires=[],
                archive=ArchiveHint(key_shape="ticker_only"),
            )
            context_tsla = {"ticker": "TSLA", "run_id": "run_tsla"}
            index.record(
                ModuleResult(status="ok", artifacts=[], metrics={}, context_patch=None),
                module_spec_tsla,
                context_tsla,
                triggered_by="orchestrator",
            )

            # Row 2: SPY
            module_spec_spy = ModuleSpec(
                name="Stub SPY",
                slug="stub_spy",
                suite="tools",
                category="stub",
                run=_run,
                cli_entry=None,
                default_selected=False,
                requires=[],
                archive=ArchiveHint(key_shape="ticker_only"),
            )
            context_spy = {"ticker": "SPY", "run_id": "run_spy"}
            index.record(
                ModuleResult(status="ok", artifacts=[], metrics={}, context_patch=None),
                module_spec_spy,
                context_spy,
                triggered_by="orchestrator",
            )

            # last_run(ticker=\"SPY\") should return only SPY row
            result = last_run(ticker="SPY")
            assert result is not None
            assert result["module_slug"] == "stub_spy"
            assert result["ticker"] == "SPY"

            # last_run(ticker=\"TSLA\") should return only TSLA row
            result_tsla = last_run(ticker="TSLA")
            assert result_tsla is not None
            assert result_tsla["module_slug"] == "stub_tsla"
            assert result_tsla["ticker"] == "TSLA"
        finally:
            set_default_index(None)
            index.close()

    def test_last_run_filters_by_suite(self, tmp_path):
        """last_run(suite=\"vol_suite\") returns latest vol_suite row only."""
        index, _ = _make_archive_index(tmp_path)
        set_default_index(index)
        try:
            from shared.module_registry import ArchiveHint, ModuleResult, ModuleSpec

            def _run(context: dict) -> ModuleResult:
                return ModuleResult(
                    status="ok", artifacts=[], metrics={}, context_patch=None
                )

            # Row 1: vol_suite
            module_spec_vol = ModuleSpec(
                name="Vol Stub",
                slug="vol_stub",
                suite="vol_suite",
                category="stub",
                run=_run,
                cli_entry=None,
                default_selected=False,
                requires=[],
                archive=ArchiveHint(key_shape="global"),
            )
            index.record(
                ModuleResult(status="ok", artifacts=[], metrics={}, context_patch=None),
                module_spec_vol,
                {"run_id": "run_vol"},
                triggered_by="orchestrator",
            )

            # Row 2: options_suite
            module_spec_opt = ModuleSpec(
                name="Opt Stub",
                slug="opt_stub",
                suite="options_suite",
                category="stub",
                run=_run,
                cli_entry=None,
                default_selected=False,
                requires=[],
                archive=ArchiveHint(key_shape="global"),
            )
            index.record(
                ModuleResult(status="ok", artifacts=[], metrics={}, context_patch=None),
                module_spec_opt,
                {"run_id": "run_opt"},
                triggered_by="orchestrator",
            )

            # last_run(suite=\"vol_suite\") should return only vol_suite row
            result = last_run(suite="vol_suite")
            assert result is not None
            assert result["module_slug"] == "vol_stub"
            assert result["suite"] == "vol_suite"
        finally:
            set_default_index(None)
            index.close()

    def test_runs_since_returns_rows_since_iso_timestamp(self, tmp_path):
        """runs_since(ts) returns rows with timestamp >= ts."""
        index, _ = _make_archive_index(tmp_path)
        set_default_index(index)
        try:
            from shared.module_registry import ArchiveHint, ModuleResult, ModuleSpec

            def _run(context: dict) -> ModuleResult:
                return ModuleResult(
                    status="ok", artifacts=[], metrics={}, context_patch=None
                )

            # Row at 2026-09-05 10:00
            module_spec_1 = ModuleSpec(
                name="Stub 1",
                slug="stub_1",
                suite="tools",
                category="stub",
                run=_run,
                cli_entry=None,
                default_selected=False,
                requires=[],
                archive=ArchiveHint(key_shape="global"),
            )
            index.record(
                ModuleResult(status="ok", artifacts=[], metrics={}, context_patch=None),
                module_spec_1,
                {"run_id": "run_1"},
                triggered_by="orchestrator",
            )

            # Wait to ensure distinct timestamps
            import time
            time.sleep(0.1)

            # Row at 2026-09-05 12:00
            module_spec_2 = ModuleSpec(
                name="Stub 2",
                slug="stub_2",
                suite="tools",
                category="stub",
                run=_run,
                cli_entry=None,
                default_selected=False,
                requires=[],
                archive=ArchiveHint(key_shape="global"),
            )
            index.record(
                ModuleResult(status="ok", artifacts=[], metrics={}, context_patch=None),
                module_spec_2,
                {"run_id": "run_2"},
                triggered_by="orchestrator",
            )

            # Get actual row timestamps (newest first from archive)
            rows_all = index.query(limit=50)
            ts_newest = rows_all[0]["timestamp"]
            ts_oldest = rows_all[1]["timestamp"]

            # Parse timestamps
            from datetime import datetime, timedelta
            dt_newest = datetime.fromisoformat(ts_newest)
            dt_oldest = datetime.fromisoformat(ts_oldest)

            # Run just after oldest should get only newest row
            ts_between = dt_oldest + timedelta(milliseconds=5)
            rows = runs_since(ts_between.isoformat())
            assert len(rows) == 1
            assert rows[0]["module_slug"] == "stub_2"

            # Run after newest should get nothing
            ts_after = dt_newest + timedelta(seconds=1)
            rows_after = runs_since(ts_after.isoformat())
            assert len(rows_after) == 0
        finally:
            set_default_index(None)
            index.close()

    def test_artifacts_returns_resolved_absolute_paths(self, tmp_path):
        """artifacts(run_id) returns resolved paths via resolve_stored."""
        index, _ = _make_archive_index(tmp_path)
        set_default_index(index)
        try:
            from shared.module_registry import ArchiveHint, ArtifactRef, ModuleResult, ModuleSpec

            def _run(context: dict) -> ModuleResult:
                return ModuleResult(
                    status="ok",
                    artifacts=[ArtifactRef(path=str(tmp_path / "chart.png"), kind="png")],
                    metrics={},
                    context_patch=None,
                )

            # Create the chart file first
            chart_file = tmp_path / "chart.png"
            chart_file.write_text("fake chart data")

            module_spec = ModuleSpec(
                name="Stub A",
                slug="stub_a",
                suite="tools",
                category="stub",
                run=_run,
                cli_entry=None,
                default_selected=False,
                requires=[],
                archive=ArchiveHint(key_shape="ticker_only"),
            )
            run_id = "run_artifacts_test"
            context = {"ticker": "SPY", "run_id": run_id}
            index.record(
                ModuleResult(
                    status="ok",
                    artifacts=[ArtifactRef(path=str(tmp_path / "chart.png"), kind="png")],
                    metrics={},
                    context_patch=None,
                ),
                module_spec,
                context,
                triggered_by="orchestrator",
            )

            # artifacts(run_id) should resolve the artifact path
            result = artifacts(run_id)
            # Result is a list of resolved Paths (or None for missing)
            assert len(result) >= 1
            # The resolved path should exist (chart.png exists in tmp_path)
            assert any(p is not None for p in result)
            # The resolved path should point to the actual file
            assert chart_file in result
        finally:
            set_default_index(None)
            index.close()

    def test_artifacts_filters_none_entries(self, tmp_path):
        """artifacts(run_id) filters out None entries (missing artifacts)."""
        index, _ = _make_archive_index(tmp_path)
        set_default_index(index)
        try:
            from shared.module_registry import ArchiveHint, ModuleResult, ModuleSpec

            def _run(context: dict) -> ModuleResult:
                return ModuleResult(
                    status="ok",
                    artifacts=[],
                    metrics={},
                    context_patch=None,
                )

            module_spec = ModuleSpec(
                name="Stub NoArtifacts",
                slug="stub_noartifacts",
                suite="tools",
                category="stub",
                run=_run,
                cli_entry=None,
                default_selected=False,
                requires=[],
                archive=ArchiveHint(key_shape="ticker_only"),
            )
            run_id = "run_no_artifacts"
            context = {"ticker": "SPY", "run_id": run_id}
            index.record(
                ModuleResult(status="ok", artifacts=[], metrics={}, context_patch=None),
                module_spec,
                context,
                triggered_by="orchestrator",
            )

            # artifacts(run_id) with no artifacts should return empty list
            result = artifacts(run_id)
            assert result == []
        finally:
            set_default_index(None)
            index.close()


def test_write_run_manifest_stores_relative_artifact_paths(tmp_path):
    """Regression (PM, 2026-09-05): _write_run_manifest stored ABSOLUTE artifact
    paths because it assumed modules emit relative ones; dealer_exposure emits
    absolutes. Manifest paths must go through to_rel() like the archive does."""
    import types

    from shared import module_execution as me
    from shared.artifact_paths import repo_root
    from shared.module_registry import ArtifactRef, ModuleResult

    img = repo_root() / "outputs" / "test_rel" / "chart.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"png")
    try:
        result = ModuleResult(
            status="ok",
            artifacts=[ArtifactRef(path=str(img), kind="png")],
            metrics={},
            context_patch=None,
        )
        ordered = [types.SimpleNamespace(slug="stub_rel")]
        context = {
            "run_id": "TEST-REL-1",
            "output_dir": str(tmp_path),
            "ticker": "SPY",
            "expiry": "auto",
        }
        me._write_run_manifest({"stub_rel": result}, ordered, context)
        manifest = json.loads((tmp_path / "run_manifest.json").read_text())
        assert manifest["artifacts"]["stub_rel"] == ["outputs/test_rel/chart.png"]
    finally:
        img.unlink(missing_ok=True)
