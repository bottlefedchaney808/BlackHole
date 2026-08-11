"""Strict, versioned contracts and safe JSON publication for Quant workers."""
from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


WorkerName = Literal["coder", "research", "personal-bot", "quant"]
ModuleName = Literal["vol", "options", "var", "sentiment", "dtcc", "backtesting"]
WorkerStatus = Literal[
    "queued", "running", "succeeded", "failed", "cancelled", "timed_out", "lost", "degraded", "complete"
]
TaskPriority = Literal["low", "normal", "high"]

SCHEMA_VERSION = "1.0"
COMPATIBILITY_VERSION = "1.x"
MAX_REPORT_BYTES = 256 * 1024
_MAX_FILENAME_LENGTH = 128
_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.json$")
_WINDOWS_DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:")
_SENSITIVE_MARKER_RE = re.compile(
    r"(?<![a-z0-9])(?:api[_-]?key|access[_-]?key|authorization|bearer|credential|"
    r"password|private[_-]?key|secret|token)(?![a-z0-9])"
)
_OPAQUE_SECRET_RE = re.compile(
    r"(?:-----BEGIN\s+(?:[A-Z ]*PRIVATE KEY|OPENSSH PRIVATE KEY)-----|"
    r"\bbearer\s+[A-Za-z0-9._~+/=-]{16,}|\bsk-[A-Za-z0-9_-]{20,}|"
    r"\b(?:tok|token)[_-]?[A-Za-z0-9_-]{20,}\b|\b[0-9a-f]{32,}\b|"
    r"\beyJ[A-Za-z0-9_-]{2,}(?:\.{3}|…)[A-Za-z0-9_-]{2,}\b|"
    r"\bredacted\s*:\s*sk-[A-Za-z0-9_]*(?:\.{3}|…)|\b[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}\b)",
    re.IGNORECASE,
)
_FAILURE_STATUSES = {"failed", "timed_out", "lost"}
_NONZERO_EXIT_STATUSES = _FAILURE_STATUSES | {"cancelled"}


class ContractModel(BaseModel):
    """Base model which forbids unversioned or unrecognized contract fields."""

    # JSON reports must round-trip through model_validate, including ISO UTC timestamps.
    model_config = ConfigDict(extra="forbid", strict=False)


class WorkerIdentity(ContractModel):
    worker: WorkerName
    profile: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class ArtifactReference(ContractModel):
    artifact_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    path: str = Field(min_length=1, max_length=256)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at_utc: datetime

    @field_validator("path")
    @classmethod
    def relative_artifact_path(cls, value: str) -> str:
        path = Path(value)
        if (
            path.is_absolute()
            or _WINDOWS_DRIVE_PATH_RE.match(value)
            or ".." in path.parts
            or "\\" in value
            or value != path.as_posix()
        ):
            raise ValueError("artifact path must be a normalized relative path")
        return value

    @field_validator("created_at_utc")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return _require_utc(value)


class EvidenceReference(ContractModel):
    evidence_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    artifact: ArtifactReference
    description: str = Field(min_length=1, max_length=2_000)


class Provenance(ContractModel):
    source_run_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    requested_by: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    source_artifacts: list[ArtifactReference] = Field(min_length=1, max_length=32)


class FailureDetails(ContractModel):
    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    message: str = Field(min_length=1, max_length=2_000)
    retryable: bool = False


class WorkerTask(ContractModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    compatibility_version: Literal["1.x"] = COMPATIBILITY_VERSION
    task_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    worker: WorkerName
    worker_identity: WorkerIdentity
    task_type: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    ticker: str = Field(min_length=1, max_length=16, pattern=r"^[A-Z0-9.-]+$")
    module: ModuleName
    source_run_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    source_artifacts: list[ArtifactReference] = Field(min_length=1, max_length=32)
    requested_by: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    created_at_utc: datetime
    priority: TaskPriority
    prompt_summary: str = Field(min_length=1, max_length=4_000)

    @field_validator("created_at_utc")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def identity_matches_worker(self) -> "WorkerTask":
        if self.worker_identity.worker != self.worker:
            raise ValueError("worker_identity.worker must match worker")
        return self


class WorkerResult(ContractModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    compatibility_version: Literal["1.x"] = COMPATIBILITY_VERSION
    task_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    worker: WorkerName
    worker_identity: WorkerIdentity
    status: WorkerStatus
    headline: str = Field(min_length=1, max_length=512)
    summary: str = Field(min_length=1, max_length=8_000)
    metrics: dict[str, Any] = Field(default_factory=dict, max_length=128)
    risks: list[str] = Field(default_factory=list, max_length=64)
    actions: list[str] = Field(default_factory=list, max_length=64)
    report_path: str = Field(min_length=1, max_length=256)
    source_run_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    created_at_utc: datetime
    exit_code: int | None = None
    provenance: Provenance
    evidence: list[EvidenceReference] = Field(min_length=1, max_length=64)
    failure: FailureDetails | None = None

    @field_validator("report_path")
    @classmethod
    def relative_report_path(cls, value: str) -> str:
        return ArtifactReference.relative_artifact_path(value)

    @field_validator("created_at_utc")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def validate_result_consistency(self) -> "WorkerResult":
        if self.worker_identity.worker != self.worker:
            raise ValueError("worker_identity.worker must match worker")
        if self.provenance.source_run_id != self.source_run_id:
            raise ValueError("provenance.source_run_id must match source_run_id")
        if self.status in _FAILURE_STATUSES and self.failure is None:
            raise ValueError("failure is required for failed, timed_out, and lost statuses")
        if self.status not in _FAILURE_STATUSES and self.failure is not None:
            raise ValueError("failure is only allowed for failed, timed_out, and lost statuses")
        if self.status in {"succeeded", "complete"} and self.exit_code != 0:
            raise ValueError("succeeded and complete statuses require exit_code 0")
        if self.status in _NONZERO_EXIT_STATUSES and (self.exit_code is None or self.exit_code == 0):
            raise ValueError("failure and cancelled statuses require a nonzero exit_code")
        if self.status == "degraded" and self.exit_code != 0:
            raise ValueError("degraded status requires exit_code 0")
        if self.status in {"queued", "running"} and self.exit_code is not None:
            raise ValueError("queued and running statuses require exit_code None")
        return self


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value


def _contains_sensitive_marker(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if _contains_sensitive_marker(key):
                return True
            if str(key).lower() == "sha256" and isinstance(item, str) and re.fullmatch(r"[a-f0-9]{64}", item):
                continue
            if _contains_sensitive_marker(item):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive_marker(item) for item in value)
    if isinstance(value, str):
        return (_SENSITIVE_MARKER_RE.search(value.lower()) is not None
                or _OPAQUE_SECRET_RE.search(value) is not None)
    return False


def _canonical_run_dir(run_dir: Path) -> Path:
    path = Path(run_dir)
    if path.is_symlink():
        raise ValueError("run_dir must not be a symlink")
    try:
        canonical = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError("run_dir must exist and be resolvable") from exc
    if not canonical.is_dir():
        raise ValueError("run_dir must be a directory")
    return canonical


def _validate_filename(filename: str) -> None:
    if (
        not isinstance(filename, str)
        or len(filename) > _MAX_FILENAME_LENGTH
        or not _FILENAME_RE.fullmatch(filename)
        or Path(filename).name != filename
    ):
        raise ValueError("filename must be a simple .json filename")


def _atomic_write_text(canonical_run_dir: Path, filename: str, encoded: bytes) -> Path:
    """Atomically publish a JSON payload below *run_dir* on any platform.

    The WSL-era implementation used ``dir_fd``/``O_NOFOLLOW`` open semantics
    which do not exist on Windows. This port keeps the same guarantees with
    portable primitives: the destination must not be a symlink, the temp file
    lives in the same directory (same volume, so ``os.replace`` is atomic),
    and a failed write leaves no temp litter behind.
    """
    destination = canonical_run_dir / filename
    if destination.is_symlink():
        raise ValueError("report destination must not be a symlink")
    if len(encoded) > MAX_REPORT_BYTES:
        raise ValueError("payload exceeds maximum report size")

    fd: int | None = None
    temporary_name: str | None = None
    try:
        temporary_name = f".{filename}.{os.urandom(12).hex()}.tmp"
        fd = os.open(
            canonical_run_dir / temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(fd, "wb") as report_file:
            fd = None
            report_file.write(encoded)
            report_file.flush()
            os.fsync(report_file.fileno())
        os.replace(canonical_run_dir / temporary_name, destination)
        return destination
    except Exception:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if temporary_name is not None:
            try:
                (canonical_run_dir / temporary_name).unlink()
            except FileNotFoundError:
                pass
        raise


def write_worker_report(run_dir: Path, filename: str, payload: Mapping[str, Any]) -> Path:
    """Atomically publish a bounded, sanitized JSON report below *run_dir*.

    The caller controls only a simple filename; report contents cannot contain
    credential-like material, and the resulting file is user-readable only.
    """
    canonical_run_dir = _canonical_run_dir(run_dir)
    _validate_filename(filename)
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a mapping")
    if _contains_sensitive_marker(payload):
        raise ValueError("payload contains a sensitive credential marker")
    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("payload must be JSON serializable") from exc
    return _atomic_write_text(canonical_run_dir, filename, encoded)
