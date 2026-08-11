"""Contract and artifact-publication tests for Quant worker reports."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from shared.worker_contracts import (
    ArtifactReference,
    EvidenceReference,
    FailureDetails,
    Provenance,
    WorkerIdentity,
    WorkerResult,
    WorkerTask,
    write_worker_report,
)


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
SHA256 = "a" * 64


def _artifact() -> ArtifactReference:
    return ArtifactReference(
        artifact_id="suite-context",
        path="suite_context.json",
        sha256=SHA256,
        created_at_utc=NOW,
    )


def _task(**changes: object) -> WorkerTask:
    values: dict[str, object] = {
        "task_id": "task-20260801-001",
        "worker": "research",
        "worker_identity": WorkerIdentity(worker="research", profile="research"),
        "task_type": "explain",
        "ticker": "SPY",
        "module": "vol",
        "source_run_id": "20260801T120000Z",
        "source_artifacts": [_artifact()],
        "requested_by": "quant",
        "idempotency_key": "worker-task-001",
        "created_at_utc": NOW,
        "priority": "normal",
        "prompt_summary": "Explain the volatility report.",
    }
    values.update(changes)
    return WorkerTask(**values)


def _result(**changes: object) -> WorkerResult:
    artifact = _artifact()
    values: dict[str, object] = {
        "task_id": "task-20260801-001",
        "worker": "research",
        "worker_identity": WorkerIdentity(worker="research", profile="research"),
        "status": "succeeded",
        "headline": "Volatility summary",
        "summary": "Implied volatility is elevated.",
        "metrics": {"atm_iv": 0.25},
        "risks": ["Event risk remains elevated."],
        "actions": ["Review the term structure."],
        "report_path": "worker-research.json",
        "source_run_id": "20260801T120000Z",
        "created_at_utc": NOW,
        "exit_code": 0,
        "provenance": Provenance(
            source_run_id="20260801T120000Z",
            requested_by="quant",
            source_artifacts=[artifact],
        ),
        "evidence": [
            EvidenceReference(
                evidence_id="evidence-001",
                artifact=artifact,
                description="Suite context used for this conclusion.",
            )
        ],
    }
    values.update(changes)
    return WorkerResult(**values)


def _can_symlink(tmp_path) -> bool:
    target = tmp_path / "symlink-target"
    link = tmp_path / "symlink-link"
    target.write_text("x", encoding="utf-8")
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        return False
    return True


def test_valid_versioned_task_and_result_require_identity_evidence_and_provenance():
    task = _task()
    result = _result(task_id=task.task_id)

    assert task.schema_version == "1.0"
    assert task.compatibility_version == "1.x"
    assert result.schema_version == "1.0"
    assert result.provenance.source_artifacts[0].artifact_id == "suite-context"
    assert result.evidence[0].artifact.sha256 == SHA256


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("worker", "quant"),
        ("module", "portfolio"),
        ("priority", "immediate"),
    ],
)
def test_task_rejects_unknown_worker_module_or_priority(field: str, value: str):
    with pytest.raises(ValidationError):
        _task(**{field: value})


def test_result_rejects_unknown_status_and_extra_fields():
    assert _result(status="complete").status == "complete"

    with pytest.raises(ValidationError):
        _result(untrusted_field="not allowed")


def test_result_requires_evidence_and_provenance_and_failure_for_failed_status():
    with pytest.raises(ValidationError):
        _result(evidence=[])

    with pytest.raises(ValidationError):
        _result(provenance=None)

    with pytest.raises(ValidationError):
        _result(status="failed", exit_code=1)

    failed = _result(
        status="failed",
        exit_code=1,
        failure=FailureDetails(code="worker_error", message="Worker exited unexpectedly."),
    )
    assert failed.failure.code == "worker_error"


@pytest.mark.parametrize("filename", ["../report.json", "nested/report.json", "/tmp/report.json", "report.txt"])
def test_writer_rejects_invalid_or_traversal_filenames(tmp_path: Path, filename: str):
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    with pytest.raises(ValueError, match="filename"):
        write_worker_report(run_dir, filename, {"status": "ok"})


def test_writer_rejects_symlinked_run_directory(tmp_path: Path):
    if not _can_symlink(tmp_path):
        pytest.skip("symlink privileges unavailable on this host")
    real_run = tmp_path / "real-run"
    real_run.mkdir()
    linked_run = tmp_path / "linked-run"
    linked_run.symlink_to(real_run, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        write_worker_report(linked_run, "report.json", {"status": "ok"})


def test_writer_rejects_sensitive_credential_markers(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    with pytest.raises(ValueError, match="sensitive"):
        write_worker_report(run_dir, "report.json", {"api_key": "not-a-real-key"})


@pytest.mark.parametrize("value", [
    "eyJhbG...ture",
    "a" * 64,
    "bearer abcdefghijklmnopqrstuvwxyz0123456789",
    "«redacted:sk-…»",
    "redacted:sk-...",
    "-----BEGIN PRIVATE KEY-----",
])
def test_writer_rejects_opaque_credential_like_values(tmp_path: Path, value: str):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    with pytest.raises(ValueError, match="sensitive"):
        write_worker_report(run_dir, "report.json", {"value": value})


def test_writer_scans_values_nested_under_sha256(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    with pytest.raises(ValueError, match="sensitive"):
        write_worker_report(run_dir, "report.json", {"sha256": "-----BEGIN PRIVATE KEY-----"})


def test_writer_preserves_short_metric_strings(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    output = write_worker_report(run_dir, "report.json", {"signal": "bullish", "model": "bs", "status": "ok"})
    assert json.loads(output.read_text(encoding="utf-8"))["signal"] == "bullish"


def test_writer_rejects_symlink_destination(tmp_path: Path):
    if not _can_symlink(tmp_path):
        pytest.skip("symlink privileges unavailable on this host")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    target = tmp_path / "outside.json"
    target.write_text("outside", encoding="utf-8")
    (run_dir / "report.json").symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        write_worker_report(run_dir, "report.json", {"status": "ok"})


def test_writer_uses_marker_boundaries_to_avoid_false_positive_words(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    output = write_worker_report(run_dir, "report.json", {"strategy": "tokenomics"})

    assert output.exists()


def test_writer_rejects_common_hyphenated_secret_markers(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    with pytest.raises(ValueError, match="sensitive"):
        write_worker_report(run_dir, "report.json", {"api-key": "not-a-real-key"})


def test_artifact_paths_reject_windows_traversal_separators():
    with pytest.raises(ValidationError, match="normalized relative path"):
        ArtifactReference(
            artifact_id="suite-context",
            path=r"..\outside.json",
            sha256=SHA256,
            created_at_utc=NOW,
        )


@pytest.mark.parametrize("path", ["C:/outside.json", r"C:\\outside.json", "C:foo.json"])
def test_artifact_paths_reject_windows_drive_letters(path: str):
    with pytest.raises(ValidationError, match="normalized relative path"):
        ArtifactReference(
            artifact_id="suite-context",
            path=path,
            sha256=SHA256,
            created_at_utc=NOW,
        )


@pytest.mark.parametrize("path", ["C:/outside.json", r"C:\\outside.json", "C:foo.json"])
def test_result_paths_reject_windows_drive_letters(path: str):
    with pytest.raises(ValidationError, match="normalized relative path"):
        _result(report_path=path)


@pytest.mark.parametrize("status", ["succeeded", "complete"])
def test_success_statuses_require_zero_exit_code(status: str):
    with pytest.raises(ValidationError, match="exit_code"):
        _result(status=status, exit_code=1)


@pytest.mark.parametrize("status", ["failed", "timed_out", "lost"])
def test_failure_statuses_require_nonzero_exit_code(status: str):
    with pytest.raises(ValidationError, match="exit_code"):
        _result(
            status=status,
            exit_code=0,
            failure=FailureDetails(code="worker_error", message="Worker exited unexpectedly."),
        )


@pytest.mark.parametrize("status", ["degraded", "cancelled", "queued", "running"])
def test_non_failure_statuses_have_defined_exit_code_semantics(status: str):
    allowed = None if status in {"queued", "running"} else (0 if status == "degraded" else 1)
    result = _result(status=status, exit_code=allowed, failure=None)
    assert result.exit_code == allowed
    invalid = 1 if allowed == 0 else 0
    with pytest.raises(ValidationError, match="exit_code"):
        _result(status=status, exit_code=invalid, failure=None)


def test_writer_leaves_no_temp_litter_when_random_name_generation_fails(tmp_path: Path, monkeypatch):
    """A failure while minting the temp name must not leave stray files."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    monkeypatch.setattr("shared.worker_contracts.os.urandom", lambda _: (_ for _ in ()).throw(OSError("random failure")))
    with pytest.raises(OSError, match="random failure"):
        write_worker_report(run_dir, "report.json", {"status": "ok"})
    assert list(run_dir.glob("*.tmp")) == []


def test_writer_leaves_no_temp_litter_when_write_fails(tmp_path: Path, monkeypatch):
    """A failure writing the payload must clean up the temp file."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    real_open = os.open

    def failing_open(path, flags, mode=0o777):
        if str(path).endswith(".tmp"):
            raise OSError("injected write failure")
        return real_open(path, flags, mode)

    monkeypatch.setattr("shared.worker_contracts.os.open", failing_open)
    with pytest.raises(OSError, match="injected"):
        write_worker_report(run_dir, "report.json", {"status": "ok"})
    assert list(run_dir.glob("*.tmp")) == []


def test_writer_publishes_bounded_json_atomically_with_user_only_permissions(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    calls: list[tuple[Path, Path]] = []
    original_replace = os.replace

    def recording_replace(source: str | Path, destination: str | Path, **kwargs: object) -> None:
        calls.append((Path(source), Path(destination)))
        original_replace(source, destination, **kwargs)

    monkeypatch.setattr("shared.worker_contracts.os.replace", recording_replace)
    output = write_worker_report(run_dir, "worker-report.json", {"status": "ok", "count": 1})

    assert output == run_dir.resolve() / "worker-report.json"
    assert calls[0][1] == run_dir.resolve() / "worker-report.json"
    assert calls[0][0].name.startswith(".worker-report.json.")
    assert json.loads(output.read_text(encoding="utf-8")) == {"count": 1, "status": "ok"}
    if os.name != "nt":
        assert output.stat().st_mode & 0o077 == 0
    assert not list(run_dir.glob(".worker-report.json.*.tmp"))


def test_writer_rejects_oversized_payload(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    with pytest.raises(ValueError, match="maximum"):
        write_worker_report(run_dir, "report.json", {"summary": "x" * (300 * 1024)})


def test_writer_rejects_non_finite_json_numbers(tmp_path: Path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    with pytest.raises(ValueError, match="JSON serializable"):
        write_worker_report(run_dir, "report.json", {"metric": float("nan")})
