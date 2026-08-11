"""Authenticated, bounded local worker broker for the Quant Bridge."""
from __future__ import annotations

import hmac
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

WORKERS = ("coder", "research", "personal-bot")
PROFILES = {"coder": "coder", "research": "research", "personal-bot": "personal-bot"}
MAX_PROMPT = 20_000
MAX_OUTPUT = 64 * 1024
MAX_CONCURRENT = 2
TIMEOUTS = {"coder": 300, "research": 300, "personal-bot": 30}
SECRET_WORDS = ("api_key", "apikey", "access_key", "authorization", "bearer", "credential", "password", "private_key", "secret", "token")
# Handoff result states are a fixed contract: nothing else is ever emitted.
HANDOFF_STATES = ("accepted", "rejected", "unavailable")
# A heartbeat older than this window means the personal-bot gateway is not
# currently reachable, so a handoff cannot be durably accepted.
HANDOFF_GATEWAY_TTL_SECONDS = 120
PROMPT_BEGIN = "\n--- WORKER PROMPT BEGIN ---\n"
PROMPT_END = "\n--- WORKER PROMPT END ---\n"


def hermes_home() -> Path:
    """Resolve the active Hermes home, Windows-first.

    On this (Windows) host the Hermes home lives under ``%LOCALAPPDATA%\\hermes``
    (``C:\\Users\\<user>\\AppData\\Local\\hermes``), not ``~/.hermes``. Prefer the
    explicit ``HERMES_HOME`` env var when set, then the Windows location, then
    the POSIX fallback.
    """
    explicit = os.environ.get("HERMES_HOME")
    if explicit:
        return Path(explicit).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "hermes"
    return Path.home() / ".hermes"


# Fallback executable used only when `hermes` is not on PATH. On Windows the
# CLI ships inside the Hermes home's own venv.
HERMES_FALLBACK = str(hermes_home() / "hermes-agent" / "venv" / "Scripts" / "hermes.exe")
PROFILE_ROOT = hermes_home() / "profiles"


def scrub_environment(source: dict[str, str] | None = None) -> dict[str, str]:
    """Return an environment safe to pass to a worker child process."""
    source = dict(os.environ if source is None else source)
    blocked = ("token", "secret", "password", "api_key", "apikey", "credential", "private_key", "authorization", "access_key")
    return {key: value for key, value in source.items() if key not in {"PYTHONPATH", "VIRTUAL_ENV"} and not any(word in key.lower() for word in blocked)}


def sanitize_tail(value: bytes | str, limit: int = MAX_OUTPUT) -> str:
    text = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)
    text = text[-limit:]
    return "[redacted sensitive output]" if _secret_marker(text) else text


def validate_run_id(run_id: str) -> str:
    if not isinstance(run_id, str) or not __import__("re").fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", run_id):
        raise ValueError("invalid run id")
    return run_id


def safe_run_dir(root: Path, run_id: str) -> Path:
    validate_run_id(run_id)
    root = Path(root).resolve()
    path = root / run_id
    if path.is_symlink() or path.exists() and path.resolve().parent != root:
        raise ValueError("run path escapes root")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _secret_marker(value: str) -> bool:
    low = value.lower()
    return any(word in low for word in SECRET_WORDS)


class DispatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    worker: str
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT)
    idempotency_key: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    ticker: str | None = Field(default=None, max_length=16, pattern=r"^[A-Z0-9.-]+$")
    source_run_id: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    # Deliberately accepted nowhere: profile selection is server-owned.
    profile: str | None = None

    @field_validator("worker")
    @classmethod
    def known_worker(cls, value: str) -> str:
        if value not in WORKERS:
            raise ValueError("unknown worker")
        return value

    @field_validator("prompt")
    @classmethod
    def safe_prompt(cls, value: str) -> str:
        if _secret_marker(value):
            raise ValueError("prompt contains a sensitive credential marker")
        return value

    @field_validator("profile")
    @classmethod
    def no_profile_override(cls, value: str | None) -> str | None:
        if value is not None:
            raise ValueError("profile is server-controlled")
        return value


class HandoffRequest(BaseModel):
    """Structured, non-trading handoff into the personal-bot gateway.

    This is a watchlist / signal handoff only. ``worker`` and ``profile`` are
    deliberately absent: they are server-owned and never client-supplied.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    ticker: str = Field(min_length=1, max_length=16, pattern=r"^[A-Z0-9.-]+$")
    summary: str = Field(min_length=1, max_length=4_000)
    source_run_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    requester: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    requested_schedule: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9 _/:,*-]{0,63}$")
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

    @field_validator("ticker")
    @classmethod
    def upper_ticker(cls, value: str) -> str:
        return value.upper()

    @field_validator("summary")
    @classmethod
    def safe_summary(cls, value: str) -> str:
        if _secret_marker(value):
            raise ValueError("summary contains a sensitive credential marker")
        return value


class PersonalBotHandoffAdapter:
    """Durable enqueue / acknowledge / reject handoff into the personal-bot gateway.

    The adapter never spawns a service and never touches credentials or the
    profile ``state.db``. It writes *only* handoff records under ``store_dir``,
    and gates acceptance on the real gateway heartbeat file so an unreachable
    gateway yields ``unavailable`` rather than a false positive. Handoff result
    states are exactly ``accepted`` | ``rejected`` | ``unavailable``.
    """

    def __init__(
        self,
        store_dir: Path | str,
        heartbeat_path: Path | str | None = None,
        gateway_ttl: int = HANDOFF_GATEWAY_TTL_SECONDS,
    ) -> None:
        self.store_dir = Path(store_dir)
        self.heartbeat_path = Path(heartbeat_path).expanduser() if heartbeat_path else None
        self.gateway_ttl = gateway_ttl

    def gateway_reachable(self) -> bool:
        """True only when the personal-bot gateway heartbeat is fresh."""
        if self.heartbeat_path is None or not self.heartbeat_path.is_file():
            return False
        try:
            payload = json.loads(self.heartbeat_path.read_text(encoding="utf-8"))
            updated = datetime.fromisoformat(str(payload["updated_at"]).replace("Z", "+00:00"))
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - updated).total_seconds()
            return 0 <= age < self.gateway_ttl
        except Exception:
            return False

    def _record_path(self, handoff_id: str) -> Path:
        return self.store_dir / f"{handoff_id}.json"

    def _write_record(self, record: dict[str, Any]) -> None:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        path = self._record_path(record["handoff_id"])
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        if _secret_marker(encoded):
            raise ValueError("handoff record contains a sensitive credential marker")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def _find_by_idempotency(self, key: str) -> dict[str, Any] | None:
        if not self.store_dir.is_dir():
            return None
        for path in self.store_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if payload.get("idempotency_key") == key and payload.get("handoff_id"):
                return payload
        return None

    def enqueue(self, request: HandoffRequest) -> dict[str, Any]:
        """Durably accept a handoff, or report ``unavailable`` if the gateway is down."""
        if not self.gateway_reachable():
            return {
                "status": "unavailable",
                "retryable": True,
                "adapter": "personal-bot-gateway",
                "error": "personal-bot gateway heartbeat not fresh",
            }
        existing = self._find_by_idempotency(request.idempotency_key)
        if existing is not None:
            return self._respond(existing, existing["status"])
        handoff_id = uuid.uuid4().hex
        now = _now()
        record = {
            "handoff_id": handoff_id,
            "status": "accepted",
            "ticker": request.ticker,
            "summary": request.summary,
            "source_run_id": request.source_run_id,
            "requester": request.requester,
            "requested_schedule": request.requested_schedule,
            "idempotency_key": request.idempotency_key,
            "created_at": now,
            "updated_at": now,
        }
        self._write_record(record)
        return self._respond(record, "accepted")

    def acknowledge(self, handoff_id: str) -> dict[str, Any]:
        return self._set_status(handoff_id, "accepted")

    def reject(self, handoff_id: str) -> dict[str, Any]:
        return self._set_status(handoff_id, "rejected")

    def _set_status(self, handoff_id: str, status: str) -> dict[str, Any]:
        path = self._record_path(handoff_id)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise KeyError(handoff_id)
        record["status"] = status
        record["updated_at"] = _now()
        self._write_record(record)
        return self._respond(record, status)

    def _respond(self, record: dict[str, Any], status: str) -> dict[str, Any]:
        response = {
            "status": status,
            "handoff_id": record["handoff_id"],
            "ticker": record["ticker"],
            "source_run_id": record["source_run_id"],
            "requester": record["requester"],
            "requested_schedule": record["requested_schedule"],
            "idempotency_key": record["idempotency_key"],
            "adapter": "personal-bot-gateway",
        }
        return response

    def list(self) -> dict[str, Any]:
        records = []
        if self.store_dir.is_dir():
            for path in sorted(self.store_dir.glob("*.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if payload.get("handoff_id"):
                    records.append(self._respond(payload, payload.get("status", "unavailable")))
        return {"handoffs": records}


class WorkerBroker:
    def __init__(self, token_path: Path | Callable[[], Path], *, max_concurrent: int = MAX_CONCURRENT):
        self._token_path_source = token_path
        self.max_concurrent = max_concurrent
        self.jobs: dict[str, dict[str, Any]] = {}
        self.idempotency: dict[str, str] = {}
        self._lock = threading.RLock()
        self._semaphore = threading.BoundedSemaphore(max_concurrent)
        self._active_token_path: Path | None = None
        self._active_token: str | None = None
        # Handoff adapter targets, overridable in tests. Defaults to the
        # personal-bot profile's durable state dir + live gateway heartbeat.
        personal_profile = PROFILE_ROOT / "personal-bot"
        self._handoff_store_dir = personal_profile / "state" / "handoffs"
        self._handoff_heartbeat_path = personal_profile / "state" / "gateway.heartbeat"

    def _handoff_adapter(self) -> PersonalBotHandoffAdapter:
        """Return a handoff adapter bound to the configured profile state paths."""
        return PersonalBotHandoffAdapter(
            self._handoff_store_dir,
            heartbeat_path=self._handoff_heartbeat_path,
        )

    @property
    def token_path(self) -> Path:
        return Path(self._token_path_source() if callable(self._token_path_source) else self._token_path_source).expanduser()

    @property
    def token(self) -> str:
        path = self.token_path
        if self._active_token_path == path and self._active_token is not None:
            return self._active_token
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            raise RuntimeError("worker token path must not be a symlink")
        token = secrets.token_urlsafe(32)
        # A fresh token is generated for each broker start/path, and never logged.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        self._active_token_path, self._active_token = path, token
        return token

    def authorize(self, authorization: str | None) -> bool:
        if not authorization or not authorization.startswith("Bearer "):
            return False
        return hmac.compare_digest(authorization[7:], self.token)

    def _profile_health(self, worker: str) -> dict[str, Any]:
        profile = PROFILES[worker]
        path = PROFILE_ROOT / profile
        return {"profile": profile, "healthy": path.is_dir(), "retryable": True}

    @staticmethod
    def _hermes_executable() -> str:
        return shutil.which("hermes") or HERMES_FALLBACK

    def _dispatch_personal_bot(self, job: dict[str, Any]) -> dict[str, Any]:
        """Hand watchlist/signal work to the live gateway, without spawning."""
        request = HandoffRequest(
            ticker=job.get("ticker") or "WATCHLIST",
            summary=job["prompt"],
            source_run_id=job["source_run_id"],
            requester="quant-bridge",
            requested_schedule="on-demand",
            idempotency_key=job["idempotency_key"],
        )
        result = self._handoff_adapter().enqueue(request)
        result["adapter_status"] = result["status"]
        result["finished_at"] = _now()
        return result

    def _spawn(self, job: dict[str, Any]) -> None:
        if job["worker"] == "personal-bot":
            job.update(self._dispatch_personal_bot(job))
            job["profile_health"] = {"profile": PROFILES["personal-bot"], "healthy": job["status"] == "accepted", "retryable": True}
            return
        if not self._semaphore.acquire(blocking=False):
            job["status"] = "queued"
            return
        try:
            job["status"] = "running"
            job["started_at"] = _now()
            env = scrub_environment()
            env["QUANT_WORKER_PROFILE"] = PROFILES[job["worker"]]
            # argv only; no shell command strings are ever accepted.
            prompt = PROMPT_BEGIN + job["prompt"] + PROMPT_END
            command = [self._hermes_executable(), "--profile", PROFILES[job["worker"]], "chat", "-q", prompt]
            job["command"] = command
            process = subprocess.Popen(command, cwd=str(Path(__file__).resolve().parent), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, env=env, start_new_session=True)
            job["process"] = process
            thread = threading.Thread(target=self._collect, args=(job,), daemon=True)
            job["thread"] = thread
            thread.start()
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = type(exc).__name__
            job["finished_at"] = _now()
            self._semaphore.release()

    def _collect(self, job: dict[str, Any]) -> None:
        process = job["process"]
        try:
            stdout, stderr = process.communicate(timeout=job["timeout"])
            job["stdout_tail"] = sanitize_tail(stdout)
            job["stderr_tail"] = sanitize_tail(stderr)
            job["exit_code"] = process.returncode
            job["status"] = "succeeded" if process.returncode == 0 else "failed"
        except subprocess.TimeoutExpired:
            self._kill_process(job)
            job["status"] = "timed_out"
            job["error"] = "worker timeout"
        finally:
            job["finished_at"] = _now()
            self._semaphore.release()
            with self._lock:
                for queued in self.jobs.values():
                    if queued.get("status") == "queued" and queued is not job:
                        self._spawn(queued)
                        break

    @staticmethod
    def _kill_process(job: dict[str, Any]) -> None:
        process = job.get("process")
        if process is None or process.poll() is not None:
            return
        # Windows has no os.killpg; kill the child directly (TerminateProcess
        # semantics). On POSIX, signal the whole process group so grandchildren
        # die too.
        if hasattr(os, "killpg"):
            try:
                os.killpg(process.pid, signal.SIGTERM)
                time.sleep(0.05)
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                try:
                    process.kill()
                except OSError:
                    pass
        else:
            try:
                process.kill()
            except OSError:
                pass

    def dispatch(self, request: DispatchRequest) -> dict[str, Any]:
        with self._lock:
            if request.idempotency_key and request.idempotency_key in self.idempotency:
                return self.status(self.idempotency[request.idempotency_key])
            task_id = uuid.uuid4().hex
            job = {"task_id": task_id, "worker": request.worker, "profile": PROFILES[request.worker], "prompt": request.prompt, "idempotency_key": request.idempotency_key or f"task-{task_id}", "ticker": request.ticker, "source_run_id": request.source_run_id or f"worker-{task_id}", "status": "queued", "created_at": _now(), "started_at": None, "finished_at": None, "timeout": TIMEOUTS[request.worker], "stdout_tail": "", "stderr_tail": "", "exit_code": None, "signal": None, "report_path": None, "profile_health": self._profile_health(request.worker)}
            self.jobs[task_id] = job
            if request.idempotency_key:
                self.idempotency[request.idempotency_key] = task_id
            self._spawn(job)
            return self.status(task_id)

    def status(self, task_id: str) -> dict[str, Any]:
        job = self.jobs.get(task_id)
        if job is None:
            raise KeyError(task_id)
        result = {k: v for k, v in job.items() if k not in {"process", "thread", "prompt", "command"}}
        return result

    def cancel(self, task_id: str) -> dict[str, Any]:
        job = self.jobs[task_id]
        self._kill_process(job)
        job["status"] = "cancelled"
        job["finished_at"] = _now()
        return self.status(task_id)

    def router(self) -> APIRouter:
        router = APIRouter()
        def auth(authorization: str | None = Header(default=None)) -> None:
            if not self.authorize(authorization):
                raise HTTPException(status_code=401, detail="worker authorization required")
        @router.post("/api/workers/dispatch", status_code=202)
        def dispatch(request: DispatchRequest, _: None = Depends(auth)) -> dict[str, Any]:
            return self.dispatch(request)
        @router.get("/api/workers/{task_id}")
        def get_status(task_id: str, _: None = Depends(auth)) -> dict[str, Any]:
            try: return self.status(task_id)
            except KeyError: raise HTTPException(status_code=404, detail="task not found")
        @router.get("/api/workers")
        def list_workers(_: None = Depends(auth)) -> dict[str, Any]:
            return {"workers": list(WORKERS), "jobs": [self.status(task_id) for task_id in self.jobs]}
        @router.post("/api/workers/personal-bot/handoff", status_code=202)
        def handoff_enqueue(request: HandoffRequest, _: None = Depends(auth)) -> dict[str, Any]:
            return self._handoff_adapter().enqueue(request)
        @router.post("/api/workers/personal-bot/handoff/{handoff_id}/acknowledge")
        def handoff_acknowledge(handoff_id: str, _: None = Depends(auth)) -> dict[str, Any]:
            try:
                return self._handoff_adapter().acknowledge(handoff_id)
            except KeyError:
                raise HTTPException(status_code=404, detail="handoff not found")
        @router.post("/api/workers/personal-bot/handoff/{handoff_id}/reject")
        def handoff_reject(handoff_id: str, _: None = Depends(auth)) -> dict[str, Any]:
            try:
                return self._handoff_adapter().reject(handoff_id)
            except KeyError:
                raise HTTPException(status_code=404, detail="handoff not found")
        @router.get("/api/workers/personal-bot/handoff")
        def handoff_list(_: None = Depends(auth)) -> dict[str, Any]:
            return self._handoff_adapter().list()
        return router

    def recover_orphans(self) -> None:
        for job in self.jobs.values():
            if job.get("status") == "running" and job.get("process") is None:
                job["status"] = "lost"
                job["finished_at"] = _now()
