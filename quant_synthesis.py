"""Strict, deterministic synthesis of module artifacts into Quant reports."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.worker_contracts import (
    ArtifactReference, EvidenceReference, FailureDetails, Provenance, WorkerIdentity,
    WorkerResult, write_worker_report,
)

MODULES = ("vol", "options", "var", "sentiment", "dtcc", "backtesting")
_FILE_MODULE = {f"{name}_result.json": name for name in MODULES}
_SENSITIVE = re.compile(r"(?:api[_-]?key|access[_-]?key|authorization|bearer|credential|password|private[_-]?key|secret|token|prompt)", re.I)
_OPAQUE_SECRET = re.compile(r"(?:-----BEGIN\s+(?:[A-Z ]*PRIVATE KEY|OPENSSH PRIVATE KEY)-----|\bbearer\s+[A-Za-z0-9._~+/=-]{16,}|\bsk-[A-Za-z0-9_-]{20,}|\b(?:tok|token)[_-]?[A-Za-z0-9_-]{20,}\b|\b[0-9a-f]{32,}\b|\beyJ[A-Za-z0-9_-]{2,}(?:\.{3}|…)[A-Za-z0-9_-]{2,}\b|\bredacted\s*:\s*sk-[A-Za-z0-9_-]*(?:\.{3}|…)|\b[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}\b)", re.I)
_FIXED = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _module_for(path: Path) -> str | None:
    return _FILE_MODULE.get(path.name)


def _valid_worker_report(data: Any) -> bool:
    return (
        isinstance(data, Mapping)
        and isinstance(data.get("worker"), str)
        and data["worker"] in {"coder", "research", "personal-bot", "quant"}
        and isinstance(data.get("status"), str)
        and data["status"] in {"queued", "running", "succeeded", "failed", "cancelled", "timed_out", "lost", "degraded", "complete"}
    )


def _read_regular_json(run_dir: Path, filename: str) -> tuple[dict[str, Any] | None, bytes]:
    """Read a regular JSON file inside *run_dir*, never following symlinks.

    Windows port of the WSL-era ``dir_fd``/``O_NOFOLLOW`` reader: the same
    security property (only regular files are read; symlinked/dir entries are
    treated as unreadable) is enforced with portable primitives.
    """
    candidate = Path(run_dir) / filename
    if candidate.is_symlink() or not candidate.is_file():
        return None, b""
    try:
        raw = candidate.read_bytes()
    except OSError:
        return None, b""
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, raw
    return (value if isinstance(value, dict) else None), raw


def _clean(value: Any, key: str = "") -> Any:
    if _SENSITIVE.search(key):
        return None
    if isinstance(value, Mapping):
        return {str(k): v for k, x in sorted(value.items(), key=lambda p: str(p[0])) if (v := _clean(x, str(k))) is not None}
    if isinstance(value, (list, tuple)):
        return [_clean(x, key) for x in value]
    if isinstance(value, str) and (value.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", value)):
        return None
    if isinstance(value, str) and _OPAQUE_SECRET.search(value):
        return None
    return value if value is None or isinstance(value, (str, int, float, bool)) else str(value)


def _load(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _pick(data: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _extract(module: str, data: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    fields = {
        "vol": {"fair_vol": ("fair_vol",), "atm_iv": ("atm_iv",), "vrp": ("vrp",), "signal": ("signal",), "score": ("score",)},
        "options": {"model": ("model", "method"), "sigma": ("sigma", "iv"), "iv_rank": ("iv_rank",), "price": ("price",), "call_put_skew": ("call_put_skew",), "delta": ("delta",), "gamma": ("gamma",), "vega": ("vega",), "theta": ("theta",), "rho": ("rho",)},
        "var": {"var": ("var", "var_95"), "cvar": ("cvar", "es", "expected_shortfall"), "confidence": ("confidence",), "horizon": ("horizon", "horizon_days")},
        "sentiment": {"signal": ("signal",), "score": ("score",), "label": ("label",), "gex": ("gex",), "iv": ("iv", "iv_rank"), "vrp": ("vrp",), "skew": ("skew",), "max_pain": ("max_pain",)},
        "dtcc": {"swap_count": ("swap_count", "swap_counts", "count", "total_swaps"), "fails": ("fails",), "notional": ("notional",), "scrape_status": ("scrape_status", "status"), "date_range": ("date_range",), "errors": ("errors", "error")},
        "backtesting": {"returns": ("returns", "return"), "sharpe": ("sharpe", "sharpe_ratio"), "hit_rate": ("hit_rate", "win_rate"), "forward_returns": ("forward_returns",)},
    }[module]
    for name, aliases in fields.items():
        value = _pick(data, *aliases)
        if value is None and module == "options" and name in {"delta", "gamma", "vega", "theta", "rho"}:
            greeks = data.get("greeks")
            if isinstance(greeks, Mapping):
                value = _pick(greeks, name)
        if value is not None:
            out[name] = _clean(value, name)
    return out


def _artifact(run_dir: Path, path: Path, raw: bytes | None = None) -> ArtifactReference:
    if raw is None:
        _, raw = _read_regular_json(run_dir, path.name)
    if not raw:
        raise OSError("artifact input must be a regular file")
    rel = path.relative_to(run_dir).as_posix()
    return ArtifactReference(
        artifact_id=re.sub(r"[^A-Za-z0-9_.-]", "-", rel.rsplit(".", 1)[0])[:128] or "artifact",
        path=rel, sha256=hashlib.sha256(raw).hexdigest(),
        created_at_utc=_FIXED,
    )


def _publish_text(run_dir: Path, filename: str, text: str) -> None:
    """Atomically write *text* below *run_dir* on any platform.

    Windows port of the WSL-era dir-fd writer. The temp file is created in
    the same directory (same volume) and moved over the destination with
    ``os.replace``, so a concurrent reader never sees a partial file and a
    failed write leaves no temp litter behind.
    """
    run_dir = Path(run_dir)
    destination = run_dir / filename
    if destination.is_symlink():
        raise ValueError("publication destination must not be a symlink")
    temp_name: str | None = None
    fd: int | None = None
    try:
        temp_name = f".{filename}.{os.urandom(12).hex()}.tmp"
        fd = os.open(run_dir / temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(run_dir / temp_name, destination)
    except Exception:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if temp_name is not None:
            try:
                (run_dir / temp_name).unlink()
            except FileNotFoundError:
                pass
        raise


def synthesize_run(run_dir: Path, module: str | None = None, worker_reports: Sequence[Path] = ()) -> WorkerResult:
    """Read only recognized JSON artifacts and atomically publish Quant summaries."""
    run_dir = Path(run_dir)
    if not run_dir.is_dir() or run_dir.is_symlink():
        raise ValueError("run_dir must be an existing directory")
    if module is not None and module not in MODULES:
        raise ValueError(f"module must be one of: {', '.join(MODULES)}")
    paths: dict[str, Path] = {}
    data_by_name: dict[str, dict[str, Any]] = {}
    raw_by_name: dict[str, bytes] = {}
    worker_report_names: set[str] = set()
    explicit_worker_names = {
        Path(candidate).name
        for candidate in worker_reports
        if Path(candidate).parent.resolve() == run_dir.resolve()
    }
    invalid_inputs: set[str] = set()
    for path in sorted(run_dir.glob("*.json"), key=lambda p: p.name):
        if path.name == "quant_summary.json" or path.name in explicit_worker_names:
            continue
        data, raw = _read_regular_json(run_dir, path.name)
        recognized = _module_for(path) or path.name == "suite_context.json"
        if recognized and (data is None or not raw):
            invalid_inputs.add(f"unreadable {_module_for(path) or 'suite'} input")
        if recognized and data is not None and raw:
            paths[path.name] = path
            data_by_name[path.name] = data
            raw_by_name[path.name] = raw
    for candidate in worker_reports:
        path = Path(candidate)
        try:
            same_parent = path.parent.resolve() == run_dir.resolve()
        except OSError:
            continue
        worker_data, worker_raw = _read_regular_json(run_dir, path.name) if same_parent else (None, b"")
        if (
            same_parent
            and path.name != "quant_summary.json"
            and _valid_worker_report(worker_data)
        ):
            assert worker_data is not None
            paths[path.name] = path
            worker_report_names.add(path.name)
            data_by_name[path.name] = worker_data
            raw_by_name[path.name] = worker_raw
    if not paths:
        paths = {}
    selected = {
        name: path for name, path in paths.items()
        if name in worker_report_names
        or module is None or path.name == "suite_context.json" or _module_for(path) == module
    }
    artifacts: list[ArtifactReference] = []
    for name in sorted(selected):
        try:
            artifacts.append(_artifact(run_dir, selected[name], raw_by_name[name]))
        except (OSError, ValueError):
            pass
    run_id = run_dir.name
    context = data_by_name.get("suite_context.json")
    if context and isinstance(context.get("run_id"), str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", context["run_id"]):
        run_id = context["run_id"]
    metrics: dict[str, Any] = {}; risks: list[str] = sorted(invalid_inputs); worker_items: list[dict[str, Any]] = []
    for name in sorted(selected):
        path = selected[name]
        data = data_by_name.get(path.name)
        mod = _module_for(path)
        if path.name in worker_report_names and _valid_worker_report(data):
            assert data is not None
            item = {k: _clean(data[k], k) for k in ("worker", "profile", "status", "headline") if k in data and _clean(data[k], k) is not None}
            worker_items.append(item)
            if data.get("status") not in {"succeeded", "complete"}: risks.append("worker report is not succeeded")
        elif mod and (module is None or mod == module):
            if data is None: risks.append(f"unreadable {mod} input")
            else: metrics[mod] = _extract(mod, data)
    if worker_items: metrics["worker_reports"] = worker_items
    missing_info: list[str] = []
    if module is not None:
        if module not in metrics: risks.append(f"missing {module} input")
        if module == "vol" and "options" not in metrics: risks.append("missing options companion data")
        if module == "options" and "vol" not in metrics: risks.append("missing vol companion data")
    else:
        # A unified (module=None) run only requires the modules actually
        # present in it. Absent optional modules (sentiment/dtcc/backtesting)
        # are reported as informational only and do not degrade the status.
        # Unreadable present inputs are already flagged as risks above.
        present = [name for name in MODULES if name in metrics]
        if present:
            missing_info = [name for name in MODULES if name not in metrics]
        else:
            for name in MODULES:
                if name not in metrics: risks.append(f"missing {name} input")
    status = "failed" if not metrics else ("degraded" if risks else "complete")
    failure = FailureDetails(code="missing_data", message="No recognized quant input data was available") if status == "failed" else None
    identity = WorkerIdentity(worker="quant", profile="quant")
    if not artifacts:
        _publish_text(run_dir, "quant_summary.md", "# Quant Summary\n\nStatus: failed\nSummary: No recognized quant input data was available.\n")
        artifacts = [_artifact(run_dir, run_dir / "quant_summary.md")]
    provenance = Provenance(source_run_id=run_id, requested_by="quant", source_artifacts=artifacts)
    evidence = [EvidenceReference(evidence_id=f"evidence-{i:03d}", artifact=a, description=f"Structured synthesis input: {a.path}") for i, a in enumerate(artifacts, 1)]
    summary = f"Synthesized {len(metrics)} metric group(s)."
    if missing_info:
        summary += f" Optional modules absent (informational): {', '.join(missing_info)}."
    result = WorkerResult(task_id=f"quant-synthesis-{run_id}", worker="quant", worker_identity=identity, status=status, headline="Quant synthesis complete" if status == "complete" else "Quant synthesis degraded", summary=summary, metrics=metrics, risks=sorted(set(risks)), actions=[], report_path="quant_summary.json", source_run_id=run_id, created_at_utc=_FIXED, exit_code=1 if status == "failed" else 0, provenance=provenance, evidence=evidence, failure=failure)
    payload = result.model_dump(mode="json")
    write_worker_report(run_dir, "quant_summary.json", payload)
    lines = ["# Quant Summary", "", f"Status: {status}", f"Summary: {result.summary}", "", "## Metrics"]
    lines.extend(f"- {key}: {json.dumps(value, sort_keys=True, ensure_ascii=True)}" for key, value in sorted(metrics.items()))
    if risks: lines.extend(["", "## Warnings", *[f"- {risk}" for risk in sorted(set(risks))]])
    lines.extend(["", "## Evidence", *[f"- {a.path} (sha256: {a.sha256})" for a in artifacts if a.path != "quant_summary.md"]])
    _publish_text(run_dir, "quant_summary.md", "\n".join(lines) + "\n")
    return result
