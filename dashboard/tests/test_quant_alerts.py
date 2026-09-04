"""test_quant_alerts.py

Covers Task 14 of docs/superpowers/plans/2026-08-01-quant-console.md:
dashboard.quant_alerts.check_for_alerts, the durable (no LLM, no active
Claude Code session required) detection half of the Phase 3 proactive
layer. Detection reads orchestrator_output/*/quant_summary.json history
directly rather than joining through orchestrator_runs -- a suite-kind
run's DB row does not durably carry its output_dir, while every
quant_summary.json is self-describing
(run_id, ticker, created_at_utc, modules[]), so it's the only source
detection needs. Must never trigger a new suite/orchestrator run (plan
Global Constraints) -- these tests never touch orchestrator.py's
run-triggering code, only fixture files and the DB.
"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from setup_db import migrate  # noqa: E402
import dashboard.quant_alerts as quant_alerts  # noqa: E402

pytestmark = pytest.mark.unit


def _db(tmp_path):
    db_path = str(tmp_path / "test.db")
    migrate(db_path)
    return db_path


def _summary(run_id, ticker, created_at, modules):
    return {
        "schema_version": 1,
        "run_id": run_id,
        "ticker": ticker,
        "created_at_utc": created_at,
        "modules": modules,
    }


def _module(name, status, warnings, headline="", metrics=None, source="x_result.json"):
    return {
        "module": name,
        "status": status,
        "headline": headline,
        "metrics": metrics or {},
        "warnings": warnings,
        "source_result": source,
    }


def _write_summary(output_root, run_id, summary):
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "quant_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return run_dir


class TestWarningsGrowing:
    def test_growing_warnings_same_ticker_triggers_alert(self, tmp_path):
        db_path = _db(tmp_path)
        out = tmp_path / "orchestrator_output"
        _write_summary(out, "run-1", _summary(
            "run-1", "AAPL", "2026-08-01T00:00:00Z",
            [_module("vol", "ok", ["one warning"])]))
        _write_summary(out, "run-2", _summary(
            "run-2", "AAPL", "2026-08-02T00:00:00Z",
            [_module("vol", "ok", ["one warning", "a second warning"])]))

        found = quant_alerts.check_for_alerts(db_path, output_root=str(out))

        assert len(found) == 1
        assert found[0]["ticker"] == "AAPL"
        assert found[0]["run_id"] == "run-2"
        assert "warnings_growing" in found[0]["condition"]
        assert "vol" in found[0]["condition"]

    def test_shrinking_or_stable_warnings_no_alert(self, tmp_path):
        db_path = _db(tmp_path)
        out = tmp_path / "orchestrator_output"
        _write_summary(out, "run-1", _summary(
            "run-1", "AAPL", "2026-08-01T00:00:00Z",
            [_module("vol", "ok", ["one", "two"])]))
        _write_summary(out, "run-2", _summary(
            "run-2", "AAPL", "2026-08-02T00:00:00Z",
            [_module("vol", "ok", ["one"])]))

        found = quant_alerts.check_for_alerts(db_path, output_root=str(out))
        assert found == []


class TestStatusFlip:
    def test_ok_to_degraded_triggers_alert(self, tmp_path):
        db_path = _db(tmp_path)
        out = tmp_path / "orchestrator_output"
        _write_summary(out, "run-1", _summary(
            "run-1", "TSLA", "2026-08-01T00:00:00Z",
            [_module("var", "ok", [])]))
        _write_summary(out, "run-2", _summary(
            "run-2", "TSLA", "2026-08-02T00:00:00Z",
            [_module("var", "degraded", [])]))

        found = quant_alerts.check_for_alerts(db_path, output_root=str(out))

        assert len(found) == 1
        assert found[0]["condition"] == "status_degraded:var"
        assert found[0]["run_id"] == "run-2"

    def test_ok_to_error_triggers_alert(self, tmp_path):
        db_path = _db(tmp_path)
        out = tmp_path / "orchestrator_output"
        _write_summary(out, "run-1", _summary(
            "run-1", "TSLA", "2026-08-01T00:00:00Z",
            [_module("sentiment", "ok", [])]))
        _write_summary(out, "run-2", _summary(
            "run-2", "TSLA", "2026-08-02T00:00:00Z",
            [_module("sentiment", "error", [])]))

        found = quant_alerts.check_for_alerts(db_path, output_root=str(out))
        assert len(found) == 1
        assert found[0]["condition"] == "status_degraded:sentiment"

    def test_degraded_to_ok_recovery_is_not_an_alert(self, tmp_path):
        db_path = _db(tmp_path)
        out = tmp_path / "orchestrator_output"
        _write_summary(out, "run-1", _summary(
            "run-1", "TSLA", "2026-08-01T00:00:00Z",
            [_module("var", "degraded", [])]))
        _write_summary(out, "run-2", _summary(
            "run-2", "TSLA", "2026-08-02T00:00:00Z",
            [_module("var", "ok", [])]))

        found = quant_alerts.check_for_alerts(db_path, output_root=str(out))
        assert found == []

    def test_status_unchanged_no_alert(self, tmp_path):
        db_path = _db(tmp_path)
        out = tmp_path / "orchestrator_output"
        _write_summary(out, "run-1", _summary(
            "run-1", "TSLA", "2026-08-01T00:00:00Z",
            [_module("var", "ok", [])]))
        _write_summary(out, "run-2", _summary(
            "run-2", "TSLA", "2026-08-02T00:00:00Z",
            [_module("var", "ok", [])]))

        found = quant_alerts.check_for_alerts(db_path, output_root=str(out))
        assert found == []


class TestIdempotency:
    def test_running_twice_does_not_duplicate_rows(self, tmp_path):
        db_path = _db(tmp_path)
        out = tmp_path / "orchestrator_output"
        _write_summary(out, "run-1", _summary(
            "run-1", "AAPL", "2026-08-01T00:00:00Z",
            [_module("vol", "ok", [])]))
        _write_summary(out, "run-2", _summary(
            "run-2", "AAPL", "2026-08-02T00:00:00Z",
            [_module("vol", "error", [])]))

        first = quant_alerts.check_for_alerts(db_path, output_root=str(out))
        second = quant_alerts.check_for_alerts(db_path, output_root=str(out))

        assert len(first) == 1
        assert second == []

        import sqlite3
        conn = sqlite3.connect(db_path)
        try:
            count = conn.execute("SELECT COUNT(*) FROM quant_alerts").fetchone()[0]
        finally:
            conn.close()
        assert count == 1


class TestMultipleTickersAndModules:
    def test_tickers_are_compared_independently(self, tmp_path):
        db_path = _db(tmp_path)
        out = tmp_path / "orchestrator_output"
        _write_summary(out, "aapl-1", _summary(
            "aapl-1", "AAPL", "2026-08-01T00:00:00Z", [_module("vol", "ok", [])]))
        _write_summary(out, "aapl-2", _summary(
            "aapl-2", "AAPL", "2026-08-02T00:00:00Z", [_module("vol", "error", [])]))
        _write_summary(out, "tsla-1", _summary(
            "tsla-1", "TSLA", "2026-08-01T00:00:00Z", [_module("vol", "ok", [])]))
        _write_summary(out, "tsla-2", _summary(
            "tsla-2", "TSLA", "2026-08-02T00:00:00Z", [_module("vol", "ok", [])]))

        found = quant_alerts.check_for_alerts(db_path, output_root=str(out))

        assert len(found) == 1
        assert found[0]["ticker"] == "AAPL"

    def test_multiple_modules_flip_in_the_same_run_are_distinct_alerts(self, tmp_path):
        db_path = _db(tmp_path)
        out = tmp_path / "orchestrator_output"
        _write_summary(out, "run-1", _summary(
            "run-1", "AAPL", "2026-08-01T00:00:00Z",
            [_module("vol", "ok", []), _module("var", "ok", [])]))
        _write_summary(out, "run-2", _summary(
            "run-2", "AAPL", "2026-08-02T00:00:00Z",
            [_module("vol", "error", []), _module("var", "degraded", [])]))

        found = quant_alerts.check_for_alerts(db_path, output_root=str(out))

        conditions = sorted(f["condition"] for f in found)
        assert conditions == ["status_degraded:var", "status_degraded:vol"]

    def test_no_history_single_run_produces_no_alerts(self, tmp_path):
        """A module needs a *previous* run to compare against -- the first
        run for a ticker can never itself be an alert."""
        db_path = _db(tmp_path)
        out = tmp_path / "orchestrator_output"
        _write_summary(out, "run-1", _summary(
            "run-1", "AAPL", "2026-08-01T00:00:00Z",
            [_module("vol", "error", ["scary warning"])]))

        found = quant_alerts.check_for_alerts(db_path, output_root=str(out))
        assert found == []


class TestRobustness:
    def test_malformed_summary_file_is_skipped_not_fatal(self, tmp_path):
        db_path = _db(tmp_path)
        out = tmp_path / "orchestrator_output"
        _write_summary(out, "run-1", _summary(
            "run-1", "AAPL", "2026-08-01T00:00:00Z", [_module("vol", "ok", [])]))
        bad_dir = out / "run-bad"
        bad_dir.mkdir(parents=True)
        (bad_dir / "quant_summary.json").write_text("{not valid json", encoding="utf-8")
        _write_summary(out, "run-2", _summary(
            "run-2", "AAPL", "2026-08-02T00:00:00Z", [_module("vol", "error", [])]))

        found = quant_alerts.check_for_alerts(db_path, output_root=str(out))
        assert len(found) == 1
        assert found[0]["run_id"] == "run-2"

    def test_missing_output_root_produces_no_alerts_without_raising(self, tmp_path):
        db_path = _db(tmp_path)
        found = quant_alerts.check_for_alerts(
            db_path, output_root=str(tmp_path / "does-not-exist"))
        assert found == []

    def test_never_triggers_a_suite_or_orchestrator_run(self, tmp_path, monkeypatch):
        """Global Constraints: detection is pure read/write against the DB
        and quant_summary.json files -- it must never call into
        orchestrator.py's run-triggering surface."""
        import orchestrator

        def _boom(*a, **k):
            raise AssertionError("check_for_alerts must never trigger a run")

        monkeypatch.setattr(orchestrator, "run_suite", _boom, raising=False)
        monkeypatch.setattr(orchestrator, "run_unified", _boom, raising=False)
        monkeypatch.setattr(orchestrator, "build_context", _boom)

        db_path = _db(tmp_path)
        out = tmp_path / "orchestrator_output"
        _write_summary(out, "run-1", _summary(
            "run-1", "AAPL", "2026-08-01T00:00:00Z", [_module("vol", "ok", [])]))
        _write_summary(out, "run-2", _summary(
            "run-2", "AAPL", "2026-08-02T00:00:00Z", [_module("vol", "error", [])]))

        quant_alerts.check_for_alerts(db_path, output_root=str(out))  # must not raise


class TestReturnShape:
    def test_returned_alert_has_expected_fields(self, tmp_path):
        db_path = _db(tmp_path)
        out = tmp_path / "orchestrator_output"
        _write_summary(out, "run-1", _summary(
            "run-1", "AAPL", "2026-08-01T00:00:00Z", [_module("vol", "ok", [])]))
        _write_summary(out, "run-2", _summary(
            "run-2", "AAPL", "2026-08-02T00:00:00Z", [_module("vol", "error", [])]))

        found = quant_alerts.check_for_alerts(db_path, output_root=str(out))

        assert len(found) == 1
        alert = found[0]
        for key in ("id", "run_id", "ticker", "condition", "detail",
                    "created_at_utc", "acknowledged"):
            assert key in alert
        assert alert["acknowledged"] in (0, False)
