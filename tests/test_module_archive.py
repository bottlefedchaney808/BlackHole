"""test_module_archive.py

Covers Task 7 of the Modularization Overhaul (Phase 6, `.superpowers/sdd/
task-7-brief.md`): `shared/module_archive.py`'s dedicated-DB archive index,
and the archiver hook wired into `orchestrator.py::_archive_module_result`
(orchestrator/dashboard path) and each dealer-book `cli_entry` `__main__`
block's `_archive_standalone_run` (standalone path).

Every test here uses its own isolated `ArchiveIndex(tmp_path / ...)` --
never the process-wide default singleton (`shared.module_archive.
get_default_index()`), so tests never share or pollute a real
`module_archive.db` file, and never race each other under `pytest -n`-style
parallelism.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import orchestrator
from shared.module_archive import ArchiveIndex
from shared.module_registry import ArchiveHint, ArtifactRef, ModuleResult, ModuleSpec

pytestmark = pytest.mark.unit


def _make_module_spec(
    slug: str,
    *,
    suite: str = "vol_suite",
    key_shape: str = "ticker_expiry",
) -> ModuleSpec:
    def _run(context: dict) -> ModuleResult:
        return ModuleResult(status="ok", artifacts=[], metrics={}, context_patch=None)

    return ModuleSpec(
        name=f"Stub {slug}",
        slug=slug,
        suite=suite,
        category="stub",
        run=_run,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape=key_shape),
    )


def _make_result(
    metrics: dict | None = None, artifacts: list | None = None
) -> ModuleResult:
    return ModuleResult(
        status="ok",
        artifacts=artifacts or [ArtifactRef(path="chart.png", kind="png")],
        metrics=metrics if metrics is not None else {"spot": 123.45},
        context_patch=None,
    )


# ---------------------------------------------------------------------------
# record()/query() round trip
# ---------------------------------------------------------------------------


class TestRecordAndQueryRoundtrip:
    def test_record_then_query_by_ticker_expiry_slug(self, tmp_path):
        index = ArchiveIndex(str(tmp_path / "archive.db"))
        module_spec = _make_module_spec("dealer_exposure")
        result = _make_result(metrics={"spot": 555.0, "band_regime": "neutral"})
        context = {"ticker": "spy", "expiry": "20261016", "run_id": "run-123"}

        index.record(result, module_spec, context, triggered_by="orchestrator")

        rows = index.query(
            ticker="SPY", expiry="20261016", module_slug="dealer_exposure"
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["module_slug"] == "dealer_exposure"
        assert row["suite"] == "vol_suite"
        assert row["ticker"] == "SPY"
        assert row["expiry"] == "20261016"
        assert row["run_id"] == "run-123"
        assert row["triggered_by"] == "orchestrator"
        assert row["metrics"] == {"spot": 555.0, "band_regime": "neutral"}
        assert row["artifacts"] == [{"path": "chart.png", "kind": "png"}]

        index.close()

    def test_ticker_only_key_shape_stores_no_expiry(self, tmp_path):
        index = ArchiveIndex(str(tmp_path / "archive.db"))
        module_spec = _make_module_spec("position_book", key_shape="ticker_only")
        result = _make_result(metrics={"total_net": 42.0}, artifacts=[])
        context = {"ticker": "SPY", "expiry": "20261016"}

        index.record(result, module_spec, context, triggered_by="cli")

        rows = index.query(module_slug="position_book")
        assert len(rows) == 1
        assert rows[0]["ticker"] == "SPY"
        assert rows[0]["expiry"] is None

        index.close()

    def test_global_key_shape_stores_neither_ticker_nor_expiry(self, tmp_path):
        index = ArchiveIndex(str(tmp_path / "archive.db"))
        module_spec = _make_module_spec("group_screener", key_shape="global")
        result = _make_result(metrics={"n_tickers": 10}, artifacts=[])
        context = {"ticker": "SPY", "expiry": "20261016"}

        index.record(result, module_spec, context, triggered_by="cli")

        rows = index.query(module_slug="group_screener")
        assert len(rows) == 1
        assert rows[0]["ticker"] is None
        assert rows[0]["expiry"] is None

        index.close()

    def test_query_no_match_returns_empty_list(self, tmp_path):
        index = ArchiveIndex(str(tmp_path / "archive.db"))
        rows = index.query(ticker="NOPE", module_slug="dealer_exposure")
        assert rows == []
        index.close()


# ---------------------------------------------------------------------------
# query by (module_slug, date range)
# ---------------------------------------------------------------------------


class TestQueryByDateRange:
    def _record_at(
        self, index: ArchiveIndex, module_spec: ModuleSpec, *, when: datetime
    ) -> None:
        """Bypass record()'s own timestamp (always "now") by writing the row
        directly, so date-range filtering can be tested deterministically."""
        with index._pool.get_connection_context() as conn:
            conn.execute(
                """
                INSERT INTO module_archive (
                    module_slug, suite, ticker, expiry, run_id,
                    triggered_by, timestamp, artifact_paths_json, metrics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    module_spec.slug,
                    module_spec.suite,
                    "SPY",
                    "20261016",
                    None,
                    "cli",
                    when.isoformat(),
                    "[]",
                    "{}",
                ),
            )
            conn.commit()

    def test_since_until_filters_to_expected_subset(self, tmp_path):
        index = ArchiveIndex(str(tmp_path / "archive.db"))
        module_spec = _make_module_spec("chain_scanner")
        base = datetime(2026, 8, 1, tzinfo=UTC)

        self._record_at(index, module_spec, when=base)
        self._record_at(index, module_spec, when=base + timedelta(days=5))
        self._record_at(index, module_spec, when=base + timedelta(days=10))
        self._record_at(index, module_spec, when=base + timedelta(days=20))

        rows = index.query(
            module_slug="chain_scanner",
            since=base + timedelta(days=3),
            until=base + timedelta(days=12),
        )

        assert len(rows) == 2
        timestamps = {row["timestamp"] for row in rows}
        assert (base + timedelta(days=5)).isoformat() in timestamps
        assert (base + timedelta(days=10)).isoformat() in timestamps

        index.close()

    def test_results_ordered_newest_first(self, tmp_path):
        index = ArchiveIndex(str(tmp_path / "archive.db"))
        module_spec = _make_module_spec("chain_scanner")
        base = datetime(2026, 8, 1, tzinfo=UTC)

        for offset in (0, 1, 2):
            self._record_at(index, module_spec, when=base + timedelta(days=offset))

        rows = index.query(module_slug="chain_scanner")
        timestamps = [row["timestamp"] for row in rows]
        assert timestamps == sorted(timestamps, reverse=True)

        index.close()

    def test_limit_is_respected(self, tmp_path):
        index = ArchiveIndex(str(tmp_path / "archive.db"))
        module_spec = _make_module_spec("chain_scanner")
        base = datetime(2026, 8, 1, tzinfo=UTC)

        for offset in range(5):
            self._record_at(index, module_spec, when=base + timedelta(days=offset))

        rows = index.query(module_slug="chain_scanner", limit=2)
        assert len(rows) == 2

        index.close()


# ---------------------------------------------------------------------------
# record() never raises
# ---------------------------------------------------------------------------


class TestRecordNeverRaises:
    def test_unserializable_metrics_logs_warning_and_does_not_raise(
        self, tmp_path, caplog
    ):
        index = ArchiveIndex(str(tmp_path / "archive.db"))
        module_spec = _make_module_spec("dealer_exposure")
        # A bare set() is not JSON-serializable -- json.dumps raises
        # TypeError with no `default=` fallback in _record_unsafe (by
        # design; see that method's comment).
        result = _make_result(metrics={"bad": {1, 2, 3}})
        context = {"ticker": "SPY", "expiry": "20261016"}

        with caplog.at_level(logging.WARNING, logger="shared.module_archive"):
            index.record(
                result, module_spec, context, triggered_by="cli"
            )  # must not raise

        assert any(
            "module_archive.record failed" in rec.message for rec in caplog.records
        )
        # And genuinely no row was written.
        assert index.query(module_slug="dealer_exposure") == []

        index.close()

    def test_broken_pool_write_logs_warning_and_does_not_raise(
        self, tmp_path, monkeypatch, caplog
    ):
        index = ArchiveIndex(str(tmp_path / "archive.db"))
        module_spec = _make_module_spec("dealer_exposure")
        result = _make_result()
        context = {"ticker": "SPY", "expiry": "20261016"}

        def _boom(*args, **kwargs):
            raise OSError("simulated unwritable DB path / disk failure")

        monkeypatch.setattr(index, "_record_unsafe", _boom)

        with caplog.at_level(logging.WARNING, logger="shared.module_archive"):
            index.record(
                result, module_spec, context, triggered_by="cli"
            )  # must not raise

        assert any(
            "module_archive.record failed" in rec.message for rec in caplog.records
        )

        index.close()

    def test_invalid_triggered_by_logs_warning_and_does_not_raise(self, tmp_path):
        index = ArchiveIndex(str(tmp_path / "archive.db"))
        module_spec = _make_module_spec("dealer_exposure")
        result = _make_result()
        context = {"ticker": "SPY", "expiry": "20261016"}

        index.record(
            result, module_spec, context, triggered_by="bogus"
        )  # must not raise

        assert index.query(module_slug="dealer_exposure") == []
        index.close()


# ---------------------------------------------------------------------------
# Genuine multi-process concurrency (not threads -- see brief: same-process
# thread-based concurrency cannot reproduce the documented cross-process
# "database is locked" failure mode).
# ---------------------------------------------------------------------------


_CHILD_SCRIPT = """
import sys
sys.path.insert(0, {repo_root!r})
from shared.module_archive import ArchiveIndex
from shared.module_registry import ArchiveHint, ModuleResult, ModuleSpec

def _run(context):
    return ModuleResult(status="ok", artifacts=[], metrics={{}}, context_patch=None)

module_spec = ModuleSpec(
    name="mp child", slug="mp_child", suite="vol_suite", category="stub",
    run=_run, cli_entry=None, default_selected=False, requires=[],
    archive=ArchiveHint(key_shape="ticker_expiry"),
)
index = ArchiveIndex({db_path!r})
for i in range(20):
    result = ModuleResult(
        status="ok", artifacts=[], metrics={{"i": i}}, context_patch=None
    )
    index.record(result, module_spec, {{"ticker": "SPY", "expiry": "20261016"}},
                 triggered_by="cli")
index.close()
print("CHILD_DONE")
"""


class TestConcurrentMultiprocessWrites:
    def test_two_processes_writing_same_db_file_no_locking_errors(self, tmp_path):
        db_path = str(tmp_path / "mp_archive.db")
        script_path = tmp_path / "child_writer.py"
        script_path.write_text(
            _CHILD_SCRIPT.format(repo_root=str(REPO_ROOT), db_path=db_path),
            encoding="utf-8",
        )

        proc = subprocess.Popen(
            [sys.executable, str(script_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        # Parent writes concurrently (interleaved, not sequentially after
        # the child finishes) via its own separate ArchiveIndex instance
        # pointing at the SAME db file -- a real second connection to the
        # same file, from a different OS process than the one that created
        # the schema (the child, or this parent, whichever gets there
        # first -- both use CREATE TABLE IF NOT EXISTS).
        parent_index = ArchiveIndex(db_path)
        module_spec = _make_module_spec("mp_parent")
        for i in range(20):
            result = _make_result(metrics={"i": i}, artifacts=[])
            parent_index.record(
                result,
                module_spec,
                {"ticker": "SPY", "expiry": "20261016"},
                triggered_by="orchestrator",
            )

        stdout, _ = proc.communicate(timeout=30)
        assert proc.returncode == 0, f"child process failed:\n{stdout}"
        assert "database is locked" not in stdout.lower()
        assert "CHILD_DONE" in stdout

        child_rows = parent_index.query(module_slug="mp_child", limit=100)
        parent_rows = parent_index.query(module_slug="mp_parent", limit=100)
        assert len(child_rows) == 20
        assert len(parent_rows) == 20

        parent_index.close()


# ---------------------------------------------------------------------------
# Archiver hook wired at each trigger point
# ---------------------------------------------------------------------------


class TestArchiverHookWiredAtOrchestrator:
    def test_run_selected_modules_calls_archive_record_with_triggered_by_orchestrator(
        self, monkeypatch
    ):
        module_spec = _make_module_spec("stub_archived")
        registry = [module_spec]

        monkeypatch.setattr(orchestrator, "all_modules", lambda: registry)
        import shared.module_registry as module_registry_mod

        monkeypatch.setattr(module_registry_mod, "all_modules", lambda: registry)

        calls = []
        import shared.module_archive as module_archive_mod

        def fake_record(result, spec, context, *, triggered_by):
            calls.append((spec.slug, triggered_by))

        monkeypatch.setattr(module_archive_mod, "record", fake_record)

        orchestrator.run_selected_modules(
            ["stub_archived"], {"ticker": "SPY", "expiry": "20261016"}
        )

        assert calls == [("stub_archived", "orchestrator")]

    def test_unresolvable_module_slug_does_not_raise(self, monkeypatch, caplog):
        """`_archive_module_result` is defensively guarded even though
        `run_selected_modules` can't actually call it with an unregistered
        slug today -- confirms the guard itself works and never propagates."""
        with caplog.at_level(logging.WARNING):
            orchestrator._archive_module_result(
                "totally_unregistered_slug_xyz", _make_result(), {}
            )
        # No exception raised is the actual assertion (pytest would fail the
        # test if one propagated); also confirm the warning was logged.
        assert any(
            "could not resolve ModuleSpec" in rec.message for rec in caplog.records
        )


class TestArchiverHookWiredAtCliEntry:
    def test_dealer_exposure_module_cli_archives_with_triggered_by_cli(
        self, monkeypatch
    ):
        vol_suite_dir = REPO_ROOT / "Vol_Suite"
        if str(vol_suite_dir) not in sys.path:
            sys.path.insert(0, str(vol_suite_dir))
        import dealer_exposure_module

        class _FakeStructural:
            status = "ok"

        class _FakeResult:
            ticker = "SPY"
            expiry = "20261016"
            spot = 555.0
            gex_reference = 1.0
            book_gamma = 2.0
            charm_1d = 3.0
            residual_vanna_inventory = 4.0
            band_n = 5
            band_z = 0.1
            band_regime = "neutral"
            structural = _FakeStructural()

        calls = []
        import shared.module_archive as module_archive_mod

        def fake_record(result, spec, context, *, triggered_by):
            calls.append((spec.slug, triggered_by, context))

        monkeypatch.setattr(module_archive_mod, "record", fake_record)

        dealer_exposure_module._archive_standalone_run(
            "SPY", _FakeResult(), ["chart1.png", "chart2.png"]
        )

        assert len(calls) == 1
        slug, triggered_by, context = calls[0]
        assert slug == "dealer_exposure"
        assert triggered_by == "cli"
        assert context == {"ticker": "SPY", "expiry": "20261016"}

    def test_dealer_exposure_module_cli_archive_failure_does_not_raise(
        self, monkeypatch, caplog
    ):
        vol_suite_dir = REPO_ROOT / "Vol_Suite"
        if str(vol_suite_dir) not in sys.path:
            sys.path.insert(0, str(vol_suite_dir))
        import dealer_exposure_module

        class _FakeStructural:
            status = "ok"

        class _FakeResult:
            ticker = "SPY"
            expiry = "20261016"
            spot = 555.0
            gex_reference = 1.0
            book_gamma = 2.0
            charm_1d = 3.0
            residual_vanna_inventory = 4.0
            band_n = 5
            band_z = 0.1
            band_regime = "neutral"
            structural = _FakeStructural()

        import shared.module_archive as module_archive_mod

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated archive failure")

        monkeypatch.setattr(module_archive_mod, "record", _boom)

        with caplog.at_level(logging.WARNING):
            # Must not raise even though module_archive.record itself blows up.
            dealer_exposure_module._archive_standalone_run(
                "SPY", _FakeResult(), ["chart1.png"]
            )

        assert any("could not archive run" in rec.message for rec in caplog.records)


class TestDefaultIndexSingleton:
    def test_free_functions_use_default_index(self, monkeypatch, tmp_path):
        import shared.module_archive as module_archive_mod

        index = ArchiveIndex(str(tmp_path / "singleton.db"))
        module_archive_mod.set_default_index(index)
        try:
            module_spec = _make_module_spec("dealer_exposure")
            result = _make_result(metrics={"spot": 1.0})
            module_archive_mod.record(
                result,
                module_spec,
                {"ticker": "SPY", "expiry": "20261016"},
                triggered_by="cli",
            )

            rows = module_archive_mod.query(module_slug="dealer_exposure")
            assert len(rows) == 1
        finally:
            module_archive_mod.set_default_index(None)
            index.close()


# ---------------------------------------------------------------------------
# Task B2: repo-relative artifact paths
# ---------------------------------------------------------------------------


class TestRepoRelativeArtifactPaths:
    """Test that artifact paths are stored repo-relative (Task B2)."""

    def test_record_stores_repo_path_relative(self, tmp_path):
        """Repo under-root path should be stored as relative."""
        index = ArchiveIndex(str(tmp_path / "archive.db"))
        module_spec = _make_module_spec("dealer_exposure")
        # Path under repo root
        repo_path = REPO_ROOT / "outputs" / "r1" / "chart.png"
        result = _make_result(artifacts=[ArtifactRef(path=str(repo_path), kind="png")])
        context = {"ticker": "SPY", "expiry": "20261016"}

        index.record(result, module_spec, context, triggered_by="orchestrator")

        rows = index.query(module_slug="dealer_exposure")
        assert len(rows) == 1
        artifacts = rows[0]["artifacts"]
        assert len(artifacts) == 1
        # Should be stored relative, not absolute
        assert artifacts[0]["path"] == "outputs/r1/chart.png"
        assert not os.path.isabs(artifacts[0]["path"])

        index.close()

    def test_record_stores_windows_style_path_relative(self, tmp_path):
        """Fake Windows-style path should be stored as relative."""
        index = ArchiveIndex(str(tmp_path / "archive.db"))
        module_spec = _make_module_spec("dealer_exposure")
        # Fake Windows path (doesn't exist, but should be normalized)
        windows_path = "C:\\Users\\bottl\\FinancialDevelopment\\outputs\\r1\\chart.png"
        result = _make_result(artifacts=[ArtifactRef(path=windows_path, kind="png")])
        context = {"ticker": "SPY", "expiry": "20261016"}

        index.record(result, module_spec, context, triggered_by="orchestrator")

        rows = index.query(module_slug="dealer_exposure")
        assert len(rows) == 1
        artifacts = rows[0]["artifacts"]
        assert len(artifacts) == 1
        # Should be stored relative, not as Windows absolute
        assert artifacts[0]["path"] == "outputs/r1/chart.png"
        assert not os.path.isabs(artifacts[0]["path"])

        index.close()

    def test_record_mixed_repo_and_windows_paths(self, tmp_path):
        """One repo-path + one fake Windows-style path both stored relative."""
        index = ArchiveIndex(str(tmp_path / "archive.db"))
        module_spec = _make_module_spec("dealer_exposure")
        repo_path = REPO_ROOT / "outputs" / "r1" / "chart.png"
        windows_path = "C:\\Users\\bottl\\FinancialDevelopment\\outputs\\r1\\vol.json"
        result = _make_result(
            artifacts=[
                ArtifactRef(path=str(repo_path), kind="png"),
                ArtifactRef(path=windows_path, kind="json"),
            ]
        )
        context = {"ticker": "SPY", "expiry": "20261016"}

        index.record(result, module_spec, context, triggered_by="orchestrator")

        rows = index.query(module_slug="dealer_exposure")
        assert len(rows) == 1
        artifacts = rows[0]["artifacts"]
        assert len(artifacts) == 2
        assert artifacts[0]["path"] == "outputs/r1/chart.png"
        assert artifacts[1]["path"] == "outputs/r1/vol.json"

        index.close()

    def test_legacy_absolute_row_returned_unmuted_by_query(self, tmp_path):
        """Pre-seeded legacy ABSOLUTE row still returned unmuted by query."""
        index = ArchiveIndex(str(tmp_path / "archive.db"))
        module_spec = _make_module_spec("dealer_exposure")

        # Pre-seed a legacy absolute row (simulating old data)
        legacy_path = "/opt/data/FinancialDevelopment/outputs/legacy/chart.png"
        with index._pool.get_connection_context() as conn:
            conn.execute(
                """
                INSERT INTO module_archive (
                    module_slug, suite, ticker, expiry, run_id,
                    triggered_by, timestamp, artifact_paths_json, metrics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    module_spec.slug,
                    module_spec.suite,
                    "SPY",
                    "20261016",
                    None,
                    "cli",
                    "2026-08-01T00:00:00+00:00",
                    f'[{{"path": "{legacy_path}", "kind": "png"}}]',
                    "{}",
                ),
            )
            conn.commit()

        # Query should return the absolute path unchanged (query does NOT mutate)
        rows = index.query(ticker="SPY")
        assert len(rows) == 1
        artifacts = rows[0]["artifacts"]
        assert len(artifacts) == 1
        # Should be returned as-is (absolute), not converted
        assert artifacts[0]["path"] == legacy_path

        index.close()


# ---------------------------------------------------------------------------#
# Task B5: backfill legacy absolute paths
# ---------------------------------------------------------------------------#


def _run_backfill_script(tmp_path):
    """Run the backfill script against a temp DB and return results."""
    import subprocess
    script_path = REPO_ROOT / "scripts" / "backfill_archive_paths.py"
    env = os.environ.copy()
    env["MODULE_ARCHIVE_DB_PATH"] = str(tmp_path / "archive.db")
    result = subprocess.run(
        [sys.executable, str(script_path)],
        capture_output=True,
        text=True,
        env=env,
    )
    return result


class TestBackfillArchivePaths:
    """Test the backfill_archive_paths.py script (Task B5)."""

    def test_backfill_converts_absolute_to_relative_paths(self, tmp_path):
        """Absolute paths under repo root become relative."""
        index = ArchiveIndex(str(tmp_path / "archive.db"))
        module_spec = _make_module_spec("dealer_exposure")

        # Seed a legacy absolute row (simulating old data)
        legacy_path = str(REPO_ROOT / "outputs" / "r1" / "chart.png")
        with index._pool.get_connection_context() as conn:
            conn.execute(
                """
                INSERT INTO module_archive (
                    module_slug, suite, ticker, expiry, run_id,
                    triggered_by, timestamp, artifact_paths_json, metrics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    module_spec.slug,
                    module_spec.suite,
                    "SPY",
                    "20261016",
                    None,
                    "cli",
                    "2026-08-01T00:00:00+00:00",
                    f'[{{"path": "{legacy_path}", "kind": "png"}}]',
                    "{}",
                ),
            )
            conn.commit()

        index.close()

        # Run backfill
        result = _run_backfill_script(tmp_path)
        assert result.returncode == 0, f"script failed: {result.stderr}"

        # Verify path is now relative
        index2 = ArchiveIndex(str(tmp_path / "archive.db"))
        rows = index2.query(module_slug="dealer_exposure")
        assert len(rows) == 1
        artifacts = rows[0]["artifacts"]
        assert len(artifacts) == 1
        assert artifacts[0]["path"] == "outputs/r1/chart.png"
        index2.close()

    def test_backfill_is_idempotent(self, tmp_path):
        """Running backfill twice changes 0 rows on second run."""
        index = ArchiveIndex(str(tmp_path / "archive.db"))
        module_spec = _make_module_spec("dealer_exposure")

        # Seed a legacy absolute row
        legacy_path = str(REPO_ROOT / "outputs" / "r1" / "chart.png")
        with index._pool.get_connection_context() as conn:
            conn.execute(
                """
                INSERT INTO module_archive (
                    module_slug, suite, ticker, expiry, run_id,
                    triggered_by, timestamp, artifact_paths_json, metrics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    module_spec.slug,
                    module_spec.suite,
                    "SPY",
                    "20261016",
                    None,
                    "cli",
                    "2026-08-01T00:00:00+00:00",
                    f'[{{"path": "{legacy_path}", "kind": "png"}}]',
                    "{}",
                ),
            )
            conn.commit()

        index.close()

        # Run backfill first time
        result1 = _run_backfill_script(tmp_path)
        assert result1.returncode == 0

        # Run backfill second time
        result2 = _run_backfill_script(tmp_path)
        assert result2.returncode == 0

        # Second run should report 0 changes
        assert "Rows changed: 0" in result2.stdout

    def test_backfill_leaves_outside_root_paths_unchanged(self, tmp_path):
        """Paths outside repo root are left unchanged."""
        index = ArchiveIndex(str(tmp_path / "archive.db"))
        module_spec = _make_module_spec("dealer_exposure")

        # Seed a row with a path outside repo root
        outside_path = "/elsewhere/outputs/chart.png"
        with index._pool.get_connection_context() as conn:
            conn.execute(
                """
                INSERT INTO module_archive (
                    module_slug, suite, ticker, expiry, run_id,
                    triggered_by, timestamp, artifact_paths_json, metrics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    module_spec.slug,
                    module_spec.suite,
                    "SPY",
                    "20261016",
                    None,
                    "cli",
                    "2026-08-01T00:00:00+00:00",
                    f'[{{"path": "{outside_path}", "kind": "png"}}]',
                    "{}",
                ),
            )
            conn.commit()

        index.close()

        result = _run_backfill_script(tmp_path)
        assert result.returncode == 0

        # Verify path is unchanged
        index2 = ArchiveIndex(str(tmp_path / "archive.db"))
        rows = index2.query(module_slug="dealer_exposure")
        artifacts = rows[0]["artifacts"]
        assert artifacts[0]["path"] == outside_path
        index2.close()

    def test_backfill_handles_windows_style_paths(self, tmp_path):
        """Windows-style absolute paths are converted to relative."""
        index = ArchiveIndex(str(tmp_path / "archive.db"))
        module_spec = _make_module_spec("dealer_exposure")

        # Seed a Windows-style absolute path
        windows_path = "C:\\\\Users\\\\bottl\\\\FinancialDevelopment\\\\outputs\\\\r1\\\\chart.png"
        with index._pool.get_connection_context() as conn:
            conn.execute(
                """
                INSERT INTO module_archive (
                    module_slug, suite, ticker, expiry, run_id,
                    triggered_by, timestamp, artifact_paths_json, metrics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    module_spec.slug,
                    module_spec.suite,
                    "SPY",
                    "20261016",
                    None,
                    "cli",
                    "2026-08-01T00:00:00+00:00",
                    f'[{{"path": "{windows_path}", "kind": "png"}}]',
                    "{}",
                ),
            )
            conn.commit()

        index.close()

        result = _run_backfill_script(tmp_path)
        assert result.returncode == 0

        index2 = ArchiveIndex(str(tmp_path / "archive.db"))
        rows = index2.query(module_slug="dealer_exposure")
        artifacts = rows[0]["artifacts"]
        assert artifacts[0]["path"] == "outputs/r1/chart.png"
        index2.close()

    def test_backfill_report_format(self, tmp_path):
        """Backfill produces expected report format."""
        index = ArchiveIndex(str(tmp_path / "archive.db"))
        module_spec = _make_module_spec("dealer_exposure")

        legacy_path = str(REPO_ROOT / "outputs" / "r1" / "chart.png")
        with index._pool.get_connection_context() as conn:
            conn.execute(
                """
                INSERT INTO module_archive (
                    module_slug, suite, ticker, expiry, run_id,
                    triggered_by, timestamp, artifact_paths_json, metrics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    module_spec.slug,
                    module_spec.suite,
                    "SPY",
                    "20261016",
                    None,
                    "cli",
                    "2026-08-01T00:00:00+00:00",
                    f'[{{"path": "{legacy_path}", "kind": "png"}}]',
                    "{}",
                ),
            )
            conn.commit()

        index.close()

        result = _run_backfill_script(tmp_path)
        assert result.returncode == 0

        # Should show summary with counts
        assert "rows processed" in result.stdout.lower()
        assert "rows changed" in result.stdout.lower()
