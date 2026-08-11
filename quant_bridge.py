"""Local-only FastAPI bridge for FinancialDevelopment run artifacts and controls.

The service intentionally binds to loopback when launched by ``quant_bridge.bat``.
It never accepts shell command strings: run requests are converted to an argv list
and passed directly to the existing orchestrator subprocess.

Windows port of the original WSL ``Financial_Development/quant_bridge.py``: the
project interpreter is the shared root ``.venv`` (``.venv/Scripts/python.exe``),
the Hermes home resolves through ``worker_broker.hermes_home()``
(``%LOCALAPPDATA%\\hermes`` on Windows), and the broker token lives under that
home's ``run/`` directory.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, field_validator
from quant_synthesis import MODULES as SYNTHESIS_MODULES, synthesize_run
from worker_broker import DispatchRequest
from worker_broker import WorkerBroker
from worker_broker import hermes_home

ROOT = Path(__file__).resolve().parent
ORCHESTRATOR = ROOT / "orchestrator.py"
# The shared root venv is the single project interpreter on this host
# (START_HERE.md / SETUP_GUIDE.md). The WSL-era Financial_Dev_Env layout does
# not exist here; keep the SHARED_* names as aliases of the same venv so
# tests that exercise the fallback path still work.
PROJECT_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
SHARED_PROJECT_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
SHARED_PROJECT_ROOT = ROOT
OUTPUT_ROOT = ROOT / "orchestrator_output"
WORKER_TOKEN_PATH = hermes_home() / "run" / "quant-bridge-worker.token"
worker_broker = WorkerBroker(lambda: WORKER_TOKEN_PATH)
DEFAULT_WATCHLIST = ["SPY", "QQQ", "AAPL", "NVDA", "MSFT", "AMD", "MU", "TSLA", "META", "GOOGL"]
_TICKER_RE = re.compile(r"^[A-Z0-9.-]+$")
_jobs: Dict[str, Dict[str, Any]] = {}
_quant_action_cache: Dict[str, Dict[str, Any]] = {}
_REPORT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.json$")
_SENSITIVE_REPORT_RE = re.compile(
    r"(?:api[_-]?key|access[_-]?key|authorization|bearer|credential|password|private[_-]?key|secret|token|prompt)", re.I
)

# This order is part of the UI contract: tabs can render it without sorting or
# inferring capabilities from suite names. Only commands verified against the
# current orchestrator CLI are marked runnable.
# The Vol module has no dealer-model override surface. Its production path is
# the canonical accumulated replication + SABR implementation.
MODULE_REGISTRY = (
    {
        "id": "vol", "name": "Vol Suite", "suite": "vol", "focus": "vol", "runnable": True,
    },
    {
        "id": "options", "name": "Options Suite", "suite": "options",
        "focus": "options", "runnable": True,
    },
    {"id": "var", "name": "VaR Tools", "suite": "unified", "focus": "var", "runnable": True},
    {"id": "sentiment", "name": "Sentiment Scanner", "suite": "sentiment", "focus": "sentiment", "runnable": True},
    {"id": "dtcc", "name": "DTCC Swaps", "suite": None, "focus": "dtcc", "runnable": False},
    {"id": "backtesting", "name": "Backtesting", "suite": None, "focus": "backtesting", "runnable": False},
)
_MODULES = {module["id"]: module for module in MODULE_REGISTRY}


def _module_passthrough_env(module_id: Optional[str]) -> Dict[str, str]:
    """Resolve a module's declared env passthrough keys from the bridge env.

    MODULE_REGISTRY entries may declare ``env_passthrough`` — the env vars the
    module honors from the bridge's launch environment. The child env already
    inherits everything via ``os.environ.copy()`` in ``_start_job``; this
    makes the declared contract explicit (and testable). Note: the dealer
    sign model is FIXED to V5 Direction (no DEALER_SIGN_MODEL override
    exists -- Jason's exclusive choice, 2026-08-09); only threshold knobs
    like DEALER_DIRECTION_MIN_SCORE remain forwardable.
    """
    module = _MODULES.get(module_id)
    if module is None:
        return {}
    return {key: os.environ[key] for key in module.get("env_passthrough", ()) if key in os.environ}

app = FastAPI(title="Quant Bridge", version="1.0.0")
# The UI is local too; these are loopback origins only, never a wildcard.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://localhost:3000", "http://127.0.0.1", "http://127.0.0.1:3000"],
    allow_origin_regex=r"^(file://.*|app://.*|https?://(localhost|127\.0\.0\.1)(:\d+)?)$",
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)
app.include_router(worker_broker.router())


class RunRequest(BaseModel):
    ticker: str
    suite: str = Field(pattern=r"^(unified|vol|sentiment)$")
    index: Optional[str] = None
    strike: Optional[float] = None

    @field_validator("ticker")
    @classmethod
    def safe_ticker(cls, value: str) -> str:
        value = value.strip().upper()
        if not value or not _TICKER_RE.fullmatch(value):
            raise ValueError("ticker must contain only A-Z, 0-9, dot, or hyphen")
        return value

    @field_validator("index")
    @classmethod
    def safe_index(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = value.strip().upper()
        if not value or not _TICKER_RE.fullmatch(value):
            raise ValueError("index must contain only A-Z, 0-9, dot, or hyphen")
        return value


class ModuleRunRequest(RunRequest):
    suite: str = "unified"


class SynthesisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    run_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    module: str = Field(min_length=1, max_length=32)
    worker_reports: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("module")
    @classmethod
    def known_module(cls, value: str) -> str:
        if value not in SYNTHESIS_MODULES:
            raise ValueError("unknown module")
        return value

    @field_validator("worker_reports")
    @classmethod
    def safe_report_names(cls, value: list[str]) -> list[str]:
        if any(not _REPORT_NAME_RE.fullmatch(name) or Path(name).name != name for name in value):
            raise ValueError("worker_reports must contain simple .json filenames")
        return value


class QuantActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["interpret", "investigate", "explain", "watchlist"]
    module: str = Field(min_length=1, max_length=32)
    ticker: str = Field(min_length=1, max_length=16)
    run_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    idempotency_key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    summary: Optional[str] = Field(default=None, max_length=4000)
    profile: Optional[str] = None

    @field_validator("module")
    @classmethod
    def action_module(cls, value: str) -> str:
        if value not in SYNTHESIS_MODULES:
            raise ValueError("unknown module")
        return value

    @field_validator("ticker")
    @classmethod
    def action_ticker(cls, value: str) -> str:
        value = value.strip().upper()
        if not _TICKER_RE.fullmatch(value):
            raise ValueError("invalid ticker")
        return value

    @field_validator("profile")
    @classmethod
    def reject_profile_override(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            raise ValueError("profile is server-controlled")
        return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _watchlist() -> list[str]:
    raw = os.environ.get("DASHBOARD_TICKERS", "")
    values = [item.strip().upper() for item in raw.split(",") if item.strip()] if raw else DEFAULT_WATCHLIST
    return [item for item in values if _TICKER_RE.fullmatch(item)]


def fetch_ticker_data(ticker: str) -> Dict[str, Any]:
    """Fetch one ticker through the project's existing ThetaData implementation.

    Import and network errors are deliberately converted to a per-symbol error so
    one unavailable contract cannot turn the watchlist endpoint into a 500.
    """
    try:
        from live_dashboard import _fetch_ticker_data
        from shared.thetadata import ThetaDataController
        return _fetch_ticker_data(ThetaDataController(), ticker)
    except Exception as exc:
        return {"ticker": ticker, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}


def _json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def _run_summary(run_dir: Path) -> Optional[Dict[str, Any]]:
    context_path = run_dir / "suite_context.json"
    context = _json_file(context_path)
    if not isinstance(context, dict):
        return None
    results: Dict[str, Any] = {}
    for path in sorted(run_dir.glob("*_result.json")):
        payload = _json_file(path)
        if payload is not None:
            cleaned = _sanitized_report_value(payload)
            if cleaned is not None:
                results[path.stem] = cleaned
    summary = {
        "run_id": context.get("run_id", run_dir.name),
        "created_at_utc": context.get("created_at_utc"),
        "output_dir": str(run_dir),
        "focus": context.get("focus", {}),
        "status": "ok" if all(
            isinstance(result, dict) and result.get("status", "ok") in {"ok", "success"}
            for result in results.values()
        ) else "error",
        "results": results,
        "artifacts": sorted(path.name for path in run_dir.iterdir() if path.is_file()),
    }
    return summary


def _canonical_quant_run(run_id: str) -> Path:
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", run_id):
        raise HTTPException(status_code=422, detail="invalid run id")
    root = OUTPUT_ROOT.resolve()
    path = OUTPUT_ROOT / run_id
    if path.is_symlink():
        raise HTTPException(status_code=404, detail="run not found")
    try:
        canonical = path.resolve(strict=True)
    except OSError:
        raise HTTPException(status_code=404, detail="run not found")
    if not canonical.is_dir() or canonical.parent != root:
        raise HTTPException(status_code=404, detail="run not found")
    return canonical


def _suite_output_root(module_id: str) -> Optional[Path]:
    roots = {
        "vol": ROOT / "Vol_Suite" / "outputs",
        "options": ROOT / "Options_Suite" / "outputs",
        "var": ROOT / "VaR_Tools_Simulations" / "outputs",
        "sentiment": ROOT / "sentiment-scanner" / "outputs",
    }
    root = roots.get(module_id)
    return root if root and root.is_dir() else None


def _artifact_run_dir(run_id: str) -> Path:
    """Resolve an orchestrator run or suite output directory safely."""
    try:
        return _canonical_quant_run(run_id)
    except HTTPException:
        for module_id in ("vol", "options", "var", "sentiment"):
            root = _suite_output_root(module_id)
            if root is None:
                continue
            candidate = (root / run_id).resolve()
            if candidate.parent == root.resolve() and candidate.is_dir():
                return candidate
        raise HTTPException(status_code=404, detail="run not found")


def _synthesis_response(result: Any, run_dir: Path) -> Dict[str, Any]:
    return {
        "result": result.model_dump(mode="json"),
        "provenance": result.provenance.model_dump(mode="json"),
        "report_paths": [name for name in ("quant_summary.json", "quant_summary.md", "interpretation.json", "interpretation.md") if (run_dir / name).is_file()],
    }


def _sanitized_report_value(value: Any, key: str = "") -> Any:
    if _SENSITIVE_REPORT_RE.search(key):
        return None
    if isinstance(value, dict):
        return {str(k): cleaned for k, item in value.items() if (cleaned := _sanitized_report_value(item, str(k))) is not None}
    if isinstance(value, list):
        return [_sanitized_report_value(item, key) for item in value]
    if isinstance(value, str):
        if _SENSITIVE_REPORT_RE.search(value) or value.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", value):
            return None
        return value
    return value if value is None or isinstance(value, (bool, int, float)) else str(value)


def _report_module(name: str) -> Optional[str]:
    if name in {"quant_summary.json", "quant_summary.md"}:
        return "quant"
    for module in SYNTHESIS_MODULES:
        if name == f"{module}_result.json":
            return module
    if name.endswith(".json") and "_" in name:
        return "worker"
    return None


def _worker_auth(authorization: str | None = Header(default=None)) -> None:
    if not worker_broker.authorize(authorization):
        raise HTTPException(status_code=401, detail="worker authorization required")


def latest_run() -> Optional[Dict[str, Any]]:
    if not OUTPUT_ROOT.is_dir():
        return None
    candidates = []
    for path in OUTPUT_ROOT.iterdir():
        if path.is_dir() and (path / "suite_context.json").is_file():
            context = _json_file(path / "suite_context.json") or {}
            candidates.append((str(context.get("created_at_utc", "")), path.name))
    if not candidates:
        return None
    _, run_id = max(candidates)
    return _run_summary(OUTPUT_ROOT / run_id)


def build_command(ticker: str, suite: str, index: Optional[str], strike: Optional[float]) -> list[str]:
    # The bridge server may run under Hermes' Python fallback so FastAPI
    # remains available. Financial suite children must use the project's
    # shared .venv interpreter, otherwise Hermes' PYTHONPATH can select
    # incompatible NumPy/Pydantic binary extensions.
    interpreter_path = PROJECT_PYTHON if PROJECT_PYTHON.is_file() else SHARED_PROJECT_PYTHON
    interpreter = str(interpreter_path) if interpreter_path.is_file() else sys.executable
    command = [interpreter, str(ORCHESTRATOR)]
    command += ["--unified"] if suite == "unified" else ["--suite", suite]
    command += ["--ticker", ticker]
    if index:
        command += ["--index", index]
    if strike is not None:
        command += ["--strike", str(strike)]
    return command


def build_module_command(module_id: str, ticker: str, index: Optional[str],
                         strike: Optional[float]) -> list[str]:
    """Map a registered module to an existing, allow-listed orchestrator mode."""
    module = _MODULES.get(module_id)
    if module is None:
        raise KeyError(module_id)
    if not module["runnable"]:
        raise ValueError(f"module '{module_id}' is not runnable")
    return build_command(ticker, module["suite"], index, strike)


def _tail(path: Optional[Path], limit: int = 4000) -> str:
    if not path:
        return ""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - limit), os.SEEK_SET)
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _discover_output_dir(started_at: float, run_id: Optional[str] = None) -> Optional[str]:
    if not OUTPUT_ROOT.is_dir():
        return None
    if run_id and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", run_id):
        candidate = OUTPUT_ROOT / run_id
        if (candidate / "suite_context.json").is_file():
            return str(candidate)
        # A valid job-owned ID is authoritative. Do not fall back to an
        # unrelated directory selected by mtime when this job has not emitted
        # its context yet; concurrent runs can otherwise be mis-associated.
        return None
    matches = []
    for path in OUTPUT_ROOT.iterdir():
        if path.is_dir() and (path / "suite_context.json").exists():
            try:
                if path.stat().st_mtime >= started_at - 1:
                    matches.append(path)
            except OSError:
                pass
    return str(max(matches, key=lambda p: p.stat().st_mtime)) if matches else None


def _job_status(job: Dict[str, Any]) -> Dict[str, Any]:
    process = job["process"]
    returncode = process.poll()
    if returncode is None:
        status = "running"
    else:
        status = "completed" if returncode == 0 else "failed"
    output_dir = job.get("output_dir") or _discover_output_dir(
        job["started_epoch"], job.get("run_id")
    )
    if output_dir:
        job["output_dir"] = output_dir
    stdout = _tail(job.get("stdout_path"))
    progress = "completed" if status in {"completed", "failed"} else (stdout.strip().splitlines()[-1] if stdout.strip() else "starting")
    return {"job_id": job["job_id"], "status": status, "progress": progress, "stdout_tail": stdout, "output_dir": output_dir, "pid": process.pid, "returncode": returncode, "started_at": job["started_at"], "command": job["command"]}


def _module_latest(module_id: str) -> Optional[Dict[str, Any]]:
    """Return the newest run containing this module's result/artifacts."""
    module = _MODULES[module_id]
    result_name = f"{module['focus']}_result"
    candidates = []
    search_roots = [OUTPUT_ROOT]
    suite_root = _suite_output_root(module_id)
    if suite_root is not None:
        search_roots.append(suite_root)
    for search_root in search_roots:
      if not search_root.is_dir():
        continue
      for path in search_root.iterdir():
        if not path.is_dir() or not (path / "suite_context.json").is_file():
            if search_root != suite_root:
                continue
            try:
                candidates.append((str(path.stat().st_mtime), path.name, path, False))
            except OSError:
                pass
            continue
        context = _json_file(path / "suite_context.json") or {}
        if not isinstance(context, dict):
            continue
        if (path / f"{result_name}.json").is_file() or module_id == "vol":
            candidates.append((str(context.get("created_at_utc", "")), path.name, path, True))
    if not candidates:
        return None
    _, run_id, path, is_orchestrator_run = max(candidates)
    summary = _run_summary(path) if is_orchestrator_run else None
    artifacts = sorted(item.name for item in path.iterdir() if item.is_file())
    return {
        "run_id": summary["run_id"] if summary else run_id,
        "created_at_utc": summary["created_at_utc"] if summary else None,
        "output_dir": str(path),
        "result": summary["results"].get(result_name) if summary else None,
        "artifacts": summary["artifacts"] if summary else artifacts,
        "status": summary["status"] if summary else "completed",
    }


@app.get("/api/modules")
def modules() -> Dict[str, Any]:
    return {"modules": [dict(module) for module in MODULE_REGISTRY]}


@app.get("/api/modules/{module_id}")
def module_detail(module_id: str) -> Dict[str, Any]:
    module = _MODULES.get(module_id)
    if module is None:
        raise HTTPException(status_code=404, detail="module not found")
    return {"module": dict(module), "latest": _module_latest(module_id)}


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "service": "quant-bridge", "localhost_only": True, "timestamp": _now()}


@app.get("/api/watchlist")
def watchlist() -> Dict[str, Any]:
    return {"timestamp": _now(), "data": [fetch_ticker_data(ticker) for ticker in _watchlist()]}


@app.get("/api/runs/latest")
def runs_latest() -> Dict[str, Any]:
    summary = latest_run()
    if summary is None:
        raise HTTPException(status_code=404, detail="no orchestrator runs found")
    return summary


@app.get("/api/runs/{run_id}")
def run_artifact(run_id: str) -> Dict[str, Any]:
    run_dir = _canonical_quant_run(run_id)
    summary = _run_summary(run_dir)
    if summary is None:
        raise HTTPException(status_code=404, detail="run not found")
    return summary


@app.get("/api/runs/{run_id}/artifacts/{filename}")
def run_artifact_file(run_id: str, filename: str) -> FileResponse:
    """Serve only image artifacts from a canonical run directory."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,255}\.(?:png|jpe?g|webp)$", filename, re.I):
        raise HTTPException(status_code=404, detail="artifact not found")
    run_dir = _artifact_run_dir(run_id)
    artifact = (run_dir / filename).resolve()
    if run_dir.resolve() not in artifact.parents or not artifact.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    suffix = artifact.suffix.lower()
    media_type = "image/png" if suffix == ".png" else "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/webp"
    return FileResponse(artifact, media_type=media_type, filename=artifact.name)


@app.post("/api/quant/synthesize")
def quant_synthesize(request: SynthesisRequest) -> Dict[str, Any]:
    run_dir = _canonical_quant_run(request.run_id)
    reports: list[Path] = []
    for filename in request.worker_reports:
        path = run_dir / filename
        if path.is_symlink() or not path.is_file():
            raise HTTPException(status_code=404, detail="worker report not found")
        reports.append(path)
    try:
        result = synthesize_run(run_dir, module=request.module, worker_reports=reports)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _synthesis_response(result, run_dir)


@app.post("/api/quant/action", status_code=202)
def quant_action(request: QuantActionRequest, authorization: str | None = Header(default=None)) -> Dict[str, Any]:
    if request.action != "interpret":
        _worker_auth(authorization)
    run_dir = _canonical_quant_run(request.run_id)
    if request.idempotency_key in _quant_action_cache:
        return _quant_action_cache[request.idempotency_key]
    if request.action == "interpret":
        try:
            result = synthesize_run(run_dir, module=request.module)
            from quant_interpret import build_interpretation
            interpretation = build_interpretation(run_dir, module=request.module)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        response = {"action": request.action, "module": request.module, "ticker": request.ticker, "run_id": request.run_id, "worker": "quant", "status": result.status, **_synthesis_response(result, run_dir), "interpretation": interpretation}
        _quant_action_cache[request.idempotency_key] = response
        return response
    workers = {"investigate": "coder", "explain": "research", "watchlist": "personal-bot"}
    prompt = (request.summary or f"{request.action.title()} the {request.module} run for {request.ticker}.")[:4000]
    dispatch = DispatchRequest(
        worker=workers[request.action],
        prompt=prompt,
        idempotency_key=request.idempotency_key,
        ticker=request.ticker,
        source_run_id=request.run_id,
    )
    worker_result = worker_broker.dispatch(dispatch)
    response = {"action": request.action, "module": request.module, "ticker": request.ticker, "run_id": request.run_id, "worker": workers[request.action], "status": worker_result.get("status"), "worker_result": worker_result}
    _quant_action_cache[request.idempotency_key] = response
    return response


@app.get("/api/quant/reports/{run_id}")
def quant_reports(run_id: str) -> Dict[str, Any]:
    run_dir = _canonical_quant_run(run_id)
    reports = []
    for path in sorted(run_dir.iterdir(), key=lambda item: item.name):
        module = _report_module(path.name)
        if module is None or path.is_symlink() or not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8")) if path.suffix == ".json" else path.read_text(encoding="utf-8")
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        content = _sanitized_report_value(raw)
        if content is not None:
            reports.append({"name": path.name, "module": module, "size": path.stat().st_size, "content": content})
    return {"run_id": run_id, "reports": reports}


def _start_job(command: list[str], module_id: Optional[str] = None) -> Dict[str, Any]:
    job_id = uuid.uuid4().hex
    log_dir = OUTPUT_ROOT / "bridge_jobs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{job_id}.log"
    log_handle = stdout_path.open("w", encoding="utf-8")
    started_epoch = datetime.now(timezone.utc).timestamp()
    # Hermes launches the bridge with its own Python environment exposed
    # through PYTHONPATH. Never pass that interpreter path into the financial
    # subprocess: it can make the project's Python load incompatible
    # NumPy/Pydantic C extensions. The child gets only the project root for
    # imports and otherwise inherits ordinary process settings.
    child_env = os.environ.copy()
    child_env.pop("PYTHONPATH", None)
    child_env.pop("VIRTUAL_ENV", None)
    child_env["PYTHONPATH"] = str(ROOT)
    # Give the child a unique run directory so concurrent bridge jobs never
    # compete for the same second-resolution timestamped output directory.
    child_env["ORCH_RUN_ID"] = job_id
    if SHARED_PROJECT_PYTHON.is_file():
        child_env["FINDEV_PYTHON"] = str(SHARED_PROJECT_PYTHON)
    if (SHARED_PROJECT_ROOT / ".env").is_file():
        child_env["FINDEV_ENV_FILE"] = str(SHARED_PROJECT_ROOT / ".env")
    # Modules may declare env passthrough keys in MODULE_REGISTRY (see
    # _module_passthrough_env) — e.g. threshold knobs like
    # DEALER_DIRECTION_MIN_SCORE. The dealer sign MODEL itself is fixed to
    # V5 Direction and has no env selector. Forward the declared keys
    # explicitly so a bridge run can tune the winner config from its launch
    # env (they are already inherited via the copy above; this makes the
    # contract explicit and independent of that copy).
    child_env.update(_module_passthrough_env(module_id))
    try:
        process = subprocess.Popen(command, cwd=str(ROOT), stdout=log_handle, stderr=subprocess.STDOUT, start_new_session=True, env=child_env)
    except OSError:
        log_handle.close()
        raise
    job = {"job_id": job_id, "run_id": job_id, "process": process, "command": command, "stdout_path": stdout_path, "started_at": _now(), "started_epoch": started_epoch, "output_dir": None, "log_handle": log_handle}
    _jobs[job_id] = job
    return {"job_id": job_id, "status": "running", "pid": process.pid, "output_dir": None}


@app.post("/api/run", status_code=202)
def start_run(request: RunRequest) -> Dict[str, Any]:
    return _start_job(build_command(request.ticker, request.suite, request.index, request.strike))


@app.post("/api/modules/{module_id}/run", status_code=202)
def start_module_run(module_id: str, request: ModuleRunRequest) -> Dict[str, Any]:
    module = _MODULES.get(module_id)
    if module is None:
        raise HTTPException(status_code=404, detail="module not found")
    if not module["runnable"]:
        raise HTTPException(status_code=400, detail=f"module '{module_id}' is not runnable; no verified orchestrator command exists")
    return _start_job(build_module_command(module_id, request.ticker, request.index, request.strike), module_id=module_id)


@app.get("/api/run/{job_id}")
def run_status(job_id: str) -> Dict[str, Any]:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    result = _job_status(job)
    if result["status"] != "running":
        try:
            job["log_handle"].close()
        except (KeyError, OSError, ValueError):
            pass
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("QUANT_BRIDGE_PORT", "8765")))
