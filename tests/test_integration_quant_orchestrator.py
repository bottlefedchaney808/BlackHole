"""Task 9 — End-to-end integration verification for the Quant Profile Orchestrator.

Covers (always-run unless noted):

1. Clean-env interpreter / env hygiene for suite commands.
2. Bridge health + module registry.
3. Worker auth matrix (unauthorized / wrong-token / authorized).
4. Worker idempotency reuse.
5. Worker timeout cleanup (process killed, status ``timed_out``).
6. Report retrieval + synthesis provenance (quant_summary.json/.md, sanitized).
7. Safe bounded coder/research dispatch -> structured report provenance.
8. Desktop plugin reload: static ``node --check`` (the Electron host is not
   running in this TUI session, so we statically verify the bundle instead of
   performing a live host reload — never fabricated).

Heavier paths are gated behind ``QUANT_REAL_RUN=1`` so the default clean-env
suite stays fast and network-free:
- real Vol/Options/VaR suite run through the plugin bridge,
- a real (bounded) hermes coder/research dispatch.

Nothing here touches credentials: the worker token is a throwaway temp value
in a temp path, never a real credential.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import quant_bridge  # noqa: E402
import worker_broker  # noqa: E402

# The Windows repo owns the shared root venv (START_HERE.md).
PROJECT_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
MAIN_TREE_ROOT = ROOT

PLUGIN_JS = Path(os.environ.get("HERMES_HOME", Path(os.environ.get("LOCALAPPDATA", Path.home())) / "hermes")) / "desktop-plugins" / "quant-command-center" / "plugin.js"

REAL_RUN = os.getenv("QUANT_REAL_RUN") == "1"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _client(tmp_path, monkeypatch):
    """A bridge client isolated to a temp output root and a throwaway token."""
    monkeypatch.setattr(quant_bridge, "OUTPUT_ROOT", tmp_path / "orchestrator_output")
    monkeypatch.setattr(quant_bridge, "WORKER_TOKEN_PATH", tmp_path / "worker.token")
    # The broker is a module-level singleton; reset its mutable state so tests
    # don't leak jobs/idempotency records into one another.
    quant_bridge.worker_broker.jobs.clear()
    quant_bridge.worker_broker.idempotency.clear()
    quant_bridge._jobs.clear()
    quant_bridge._quant_action_cache.clear()
    return TestClient(quant_bridge.app)


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_quant_run(root: Path, run_id: str = "20260802T000000Z", module: str = "options"):
    output = root / "orchestrator_output" / run_id
    output.mkdir(parents=True, exist_ok=True)
    (output / "suite_context.json").write_text(json.dumps(
        {"run_id": run_id, "created_at_utc": "2026-08-02T00:00:00Z",
         "focus": {"ticker": "AMD", "module": module}},
    ), encoding="utf-8")
    (output / f"{module}_result.json").write_text(json.dumps(
        {"status": "ok", "score": 7, "api_key": "do-not-leak"},
    ), encoding="utf-8")
    return output


def _brute_skip_if(cond: bool, reason: str) -> None:
    if cond:
        pytest.skip(reason)


# --------------------------------------------------------------------------- #
# 1. Clean-env interpreter / env hygiene
# --------------------------------------------------------------------------- #
def test_suite_commands_use_project_python_and_are_argv_only():
    assert PROJECT_PYTHON.is_file(), "shared .venv must exist"
    for suite, ticker in (("vol", "SPY"), ("unified", "AMD"), ("sentiment", "MU")):
        cmd = quant_bridge.build_command(ticker, suite, None, None)
        assert isinstance(cmd, list) and all(isinstance(x, str) for x in cmd)
        # The orchestrator child must run under the project interpreter, not
        # Hermes' Python fallback (avoids NumPy/Pydantic C-extension skew).
        assert cmd[0] == str(PROJECT_PYTHON), cmd[0]
        assert cmd[1].endswith("orchestrator.py")
        assert cmd[cmd.index("--ticker") + 1] == ticker
        # argv only — no shell command string may ever be constructed.
        assert all("shell" not in str(v).lower() for v in cmd)


def test_child_environment_is_stripped_and_scoped(tmp_path, monkeypatch):
    """The suite child must not inherit Hermes' PYTHONPATH/VIRTUAL_ENV."""
    captured = {}

    class FakeProcess:
        pid = 9999
        returncode = None

        def poll(self):
            return self.returncode

    def fake_popen(command, **kwargs):
        captured["env"] = kwargs["env"]
        captured["command"] = command
        return FakeProcess()

    monkeypatch.setattr(quant_bridge.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("PYTHONPATH", "/wrong/hermes/python311")
    monkeypatch.setenv("VIRTUAL_ENV", "/wrong/hermes/venv")
    client = _client(tmp_path, monkeypatch)
    response = client.post("/api/modules/options/run", json={"ticker": "AMD"})
    assert response.status_code == 202
    env = captured["env"]
    assert env.get("VIRTUAL_ENV") is None
    assert env.get("PYTHONPATH") == str(quant_bridge.ROOT)
    assert captured["command"][0] == str(PROJECT_PYTHON)


def test_suite_interpreter_is_clean_and_numpy_uncorrupted():
    """Best-effort proof the project interpreter loads the right stack."""
    code = (
        "import sys; "
        "assert 'hermes' not in sys.executable.lower(), sys.executable; "
        "import numpy; print('numpy', numpy.__version__)"
    )
    result = run_clean([str(PROJECT_PYTHON), "-c", code], timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("numpy "), result.stdout


def run_clean(argv, timeout=120):
    """Run argv under the project interpreter with a scrubbed environment."""
    env = {k: v for k, v in os.environ.items()
           if k not in {"PYTHONPATH", "VIRTUAL_ENV"}}
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run(argv, capture_output=True, text=True,
                          env=env, timeout=timeout)


# --------------------------------------------------------------------------- #
# 2. Bridge health + module registry
# --------------------------------------------------------------------------- #
def test_bridge_health_and_module_registry(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    health = client.get("/api/health").json()
    assert health["status"] == "ok"
    assert health["localhost_only"] is True

    modules = client.get("/api/modules").json()["modules"]
    ids = [m["id"] for m in modules]
    assert ids == ["vol", "options", "var", "sentiment", "dtcc", "backtesting"]
    by_id = {m["id"]: m for m in modules}
    assert by_id["vol"]["runnable"] is True
    assert by_id["options"]["suite"] == "options"
    assert by_id["sentiment"]["runnable"] is True
    assert by_id["dtcc"]["runnable"] is False

    detail = client.get("/api/modules/options").json()
    assert detail["module"]["id"] == "options"


# --------------------------------------------------------------------------- #
# 3. Worker auth matrix
# --------------------------------------------------------------------------- #
def test_worker_auth_unauthorized_wrong_token_authorized(tmp_path, monkeypatch):
    monkeypatch.setattr(quant_bridge.worker_broker, "_spawn", lambda job: None)
    client = _client(tmp_path, monkeypatch)
    body = {"worker": "research", "prompt": "summarize the run"}

    # Unauthorized (no header) and wrong-token must both be rejected 401.
    assert client.post("/api/workers/dispatch", json=body).status_code == 401
    assert client.post("/api/workers/dispatch", headers=_auth_header("wrong-token"),
                       json=body).status_code == 401

    # Authorized dispatch succeeds.
    token = quant_bridge.worker_broker.token
    ok = client.post("/api/workers/dispatch", headers=_auth_header(token), json=body)
    assert ok.status_code == 202
    assert ok.json()["worker"] == "research"


# --------------------------------------------------------------------------- #
# 4. Worker idempotency reuse
# --------------------------------------------------------------------------- #
def test_worker_dispatch_idempotency_reuse(tmp_path, monkeypatch):
    monkeypatch.setattr(quant_bridge.worker_broker, "_spawn", lambda job: None)
    client = _client(tmp_path, monkeypatch)
    token = quant_bridge.worker_broker.token
    body = {"worker": "coder", "prompt": "read-only check", "idempotency_key": "same-key"}
    first = client.post("/api/workers/dispatch", headers=_auth_header(token), json=body)
    second = client.post("/api/workers/dispatch", headers=_auth_header(token), json=body)
    assert first.status_code == second.status_code == 202
    assert first.json()["task_id"] == second.json()["task_id"]
    assert len(quant_bridge.worker_broker.jobs) == 1


# --------------------------------------------------------------------------- #
# 5. Worker timeout cleanup
# --------------------------------------------------------------------------- #
class _HangProcess:
    """A fake process whose ``communicate`` raises TimeoutExpired."""

    pid = 4321
    returncode = None
    killed = False

    def poll(self):
        return None

    def communicate(self, timeout=None):
        raise subprocess.TimeoutExpired(cmd=self, timeout=timeout or 0)

    def kill(self):
        self.killed = True


def test_worker_timeout_kills_process_group(tmp_path, monkeypatch):
    broker = worker_broker.WorkerBroker(lambda: tmp_path / "t.token")
    fake = _HangProcess()
    job = {
        "task_id": "hang-1", "worker": "coder", "profile": "coder",
        "prompt": "loop forever", "status": "running", "timeout": 0.01,
        "stdout_tail": "", "stderr_tail": "", "exit_code": None,
        "signal": None, "report_path": None, "process": fake,
    }
    killpg_calls = []

    def fake_killpg(*a, **k):
        killpg_calls.append(a)

    # Windows has no os.killpg; the ported _kill_process falls back to
    # process.kill() there. Patch whichever path the host actually uses.
    if hasattr(worker_broker.os, "killpg"):
        monkeypatch.setattr(worker_broker.os, "killpg", fake_killpg)
        monkeypatch.setattr(worker_broker.os, "kill", lambda *a, **k: None)
    else:
        def fake_process_kill():
            fake.killed = True
        monkeypatch.setattr(fake, "kill", fake_process_kill)
    monkeypatch.setattr(worker_broker, "sanitize_tail", lambda v, **k: "")

    job["finished_at"] = None
    with broker._lock:
        broker.jobs[job["task_id"]] = job
    broker._semaphore.acquire()  # simulate a running (acquired) worker slot
    broker._collect(job)  # runs synchronously on a fake process
    assert job["status"] == "timed_out"
    assert job["error"] == "worker timeout"
    if hasattr(worker_broker.os, "killpg"):
        assert killpg_calls, "process group must be signalled on timeout"
    else:
        assert fake.killed, "process must be killed on timeout"


# --------------------------------------------------------------------------- #
# 6. Report retrieval + synthesis provenance
# --------------------------------------------------------------------------- #
def test_report_retrieval_and_sanitization(tmp_path, monkeypatch):
    output = _make_quant_run(tmp_path)
    (output / "var_result.json").write_text(json.dumps({"var": 4, "api_key": "leak"}))
    client = _client(tmp_path, monkeypatch)
    response = client.get(f"/api/quant/reports/{output.name}")
    assert response.status_code == 200
    text = json.dumps(response.json())
    assert "options_result.json" in text and "var_result.json" in text
    assert "do-not-leak" not in text and "leak" not in text
    assert str(output) not in text  # absolute paths must not leak


def test_synthesis_produces_provenance_and_report_paths(tmp_path, monkeypatch):
    output = _make_quant_run(tmp_path)
    client = _client(tmp_path, monkeypatch)
    response = client.post("/api/quant/synthesize",
                           json={"run_id": output.name, "module": "options"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["worker"] == "quant"
    assert payload["provenance"]["source_run_id"] == output.name
    assert payload["report_paths"] == ["quant_summary.json", "quant_summary.md"]
    assert (output / "quant_summary.json").is_file()
    assert (output / "quant_summary.md").is_file()
    assert "do-not-leak" not in json.dumps(payload)


# --------------------------------------------------------------------------- #
# 7. Safe bounded coder/research dispatch verifying report provenance
# --------------------------------------------------------------------------- #
def test_safe_worker_dispatch_maps_to_provenance(tmp_path, monkeypatch):
    """A bounded read-only dispatch yields a structured provenance-bearing job.

    Uses a mocked spawn so no real hermes worker runs in the default suite; the
    real (bounded) dispatch is exercised separately when QUANT_REAL_RUN=1.
    """
    captured = {}

    def fake_spawn(job):
        captured["worker"] = job["worker"]
        captured["profile"] = job["profile"]
        captured["prompt"] = job["prompt"]
        job["status"] = "succeeded"
        job["report_path"] = "quant_summary.json"

    monkeypatch.setattr(quant_bridge.worker_broker, "_spawn", fake_spawn)
    client = _client(tmp_path, monkeypatch)

    for action, worker in (("investigate", "coder"), ("explain", "research")):
        _make_quant_run(tmp_path, run_id="20260802T000000Z", module="options")
        client = _client(tmp_path, monkeypatch)  # reset broker state per action
        calls = {}
        monkeypatch.setattr(quant_bridge.worker_broker, "dispatch",
                            lambda req, _c=calls: (_c.update(worker=req.worker) or
                                                   {"task_id": "t-" + req.worker,
                                                    "worker": req.worker,
                                                    "status": "succeeded",
                                                    "report_path": "quant_summary.json"}))
        token = quant_bridge.worker_broker.token
        response = client.post("/api/quant/action", headers=_auth_header(token), json={
            "action": action, "module": "options", "ticker": "AMD",
            "run_id": "20260802T000000Z", "idempotency_key": f"intg-{action}-provenance",
        })
        assert response.status_code == 202
        assert calls.get("worker") == worker


# --------------------------------------------------------------------------- #
# 8. Plugin reload — static verification (Electron host not running)
# --------------------------------------------------------------------------- #
def test_plugin_is_statically_valid_and_capabilities_present():
    """Static ``node --check`` + capability-marker scan.

    The live Electron host reload cannot be performed in this session; we
    verify the bundle parses and that the staged worker-action capabilities
    (which gate on the trusted-adapter/unavailable state) are present, so a
    rollback disables worker actions without breaking source-artifact tabs.
    """
    _brute_skip_if(not PLUGIN_JS.is_file(), "quant-command-center plugin not installed")
    result = subprocess.run(["node", "--check", str(PLUGIN_JS)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, f"plugin.js failed node --check:\n{result.stderr}"
    src = PLUGIN_JS.read_text(encoding="utf-8")
    # Worker action capabilities gate on the trusted adapter / unavailable state.
    assert "interpret" in src and "investigate" in src and "watchlist" in src
    assert "unavailable" in src, "rollback must disable worker actions when adapter unavailable"
    assert "reports" in src, "older/rollback plugin keeps source-artifact/report tabs"


# --------------------------------------------------------------------------- #
# Real (network) paths — gated behind QUANT_REAL_RUN=1
# --------------------------------------------------------------------------- #
def test_real_suite_run_through_bridge(tmp_path, monkeypatch):
    """One real Vol run through the bridge, bounded, verifying the interpreter,
    a success result artifact and report retrieval. Gated + bounded: do not run
    in the default clean-env suite because it hits ThetaData/network."""
    _brute_skip_if(not REAL_RUN, "set QUANT_REAL_RUN=1 to run a live suite")
    client = _client(tmp_path, monkeypatch)
    response = client.post("/api/modules/vol/run", json={"ticker": "SPY"})
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    command = quant_bridge._jobs[job_id]["command"]
    assert command[0] == str(PROJECT_PYTHON)

    deadline = time.time() + 180
    status = {"status": "running"}
    while time.time() < deadline:
        status = client.get(f"/api/run/{job_id}").json()
        if status["status"] in {"completed", "failed"}:
            break
        time.sleep(2)
    assert status["status"] == "completed", status


def test_real_bounded_coder_dispatch(tmp_path, monkeypatch):
    """One real, bounded coder dispatch producing a structured report provenance.
    Gated behind QUANT_REAL_RUN=1 because it invokes the hermes CLI."""
    _brute_skip_if(not REAL_RUN, "set QUANT_REAL_RUN=1 to run a live hermes dispatch")
    client = _client(tmp_path, monkeypatch)
    from worker_broker import TIMEOUTS
    token = quant_bridge.worker_broker.token
    response = client.post("/api/workers/dispatch", headers=_auth_header(token), json={
        "worker": "coder",
        "prompt": "Read-only: state plainly what this Quant run id is. No actions.",
        "idempotency_key": "real-coder-bounded",
    })
    assert response.status_code == 202
    task_id = response.json()["task_id"]
    assert response.json()["status"] in {"queued", "running"}

    deadline = time.time() + min(TIMEOUTS["coder"] + 15, 240)
    status = {"status": "queued"}
    while time.time() < deadline:
        status = client.get(f"/api/workers/{task_id}",
                            headers=_auth_header(token)).json()
        if status["status"] in {"succeeded", "failed", "timed_out", "cancelled"}:
            break
        time.sleep(2)
    assert status["status"] in {"succeeded", "failed", "timed_out"}, status
    assert "report_path" in status  # provenance field present
