"""Contract tests for the localhost Quant Bridge (no live market calls)."""
import json
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import quant_bridge  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(quant_bridge, "OUTPUT_ROOT", tmp_path / "orchestrator_output")
    monkeypatch.setattr(quant_bridge, "DEFAULT_WATCHLIST", ["SPY", "BAD"])
    monkeypatch.setattr(quant_bridge, "fetch_ticker_data", lambda ticker: (
        {"ticker": ticker, "spot": 500.0, "atm_iv": 18.2}
        if ticker == "SPY" else {"ticker": ticker, "error": "feed unavailable"}
    ))
    return TestClient(quant_bridge.app)


def test_health_and_watchlist_tolerate_failed_symbol(client):
    assert client.get("/api/health").json()["status"] == "ok"
    payload = client.get("/api/watchlist").json()
    assert [row["ticker"] for row in payload["data"]] == ["SPY", "BAD"]
    assert payload["data"][0]["atm_iv"] == 18.2
    assert payload["data"][1]["error"] == "feed unavailable"


def test_latest_and_run_artifact_endpoints(tmp_path, client, monkeypatch):
    output = tmp_path / "orchestrator_output" / "20260801T120000Z"
    output.mkdir(parents=True)
    (output / "suite_context.json").write_text(json.dumps({
        "run_id": "20260801T120000Z", "created_at_utc": "2026-08-01T12:00:00Z",
        "output_dir": str(output), "focus": {"ticker": "AMD"},
    }))
    (output / "options_result.json").write_text(json.dumps({"suite": "options", "status": "ok"}))
    latest = client.get("/api/runs/latest")
    assert latest.status_code == 200
    assert latest.json()["run_id"] == "20260801T120000Z"
    assert latest.json()["results"]["options_result"]["status"] == "ok"
    assert client.get("/api/runs/20260801T120000Z").status_code == 200
    assert client.get("/api/runs/nope").status_code == 404


def test_run_artifact_rejects_path_traversal(client, tmp_path):
    # OUTPUT_ROOT = tmp_path/"orchestrator_output" (see fixture). Create the real
    # root dir, then plant a fake run ONE level OUTSIDE it (at the root's parent)
    # so that a dodgy run_id resolving OUTPUT_ROOT/.. serves out-of-root content.
    quant_bridge.OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    (tmp_path / "suite_context.json").write_text(json.dumps({
        "run_id": "OUTSIDE", "created_at_utc": "2026-01-01T00:00:00Z",
        "output_dir": str(tmp_path), "focus": {"ticker": "PWNED"},
    }))
    (tmp_path / "leak_result.json").write_text(json.dumps({"status": "ok", "secret": "leaked-outside"}))
    # %2e%2e is URL-encoded ".." and reaches the route (plain ".." is normalized
    # away by the HTTP client); "..." is a literal (non-traversal) name.
    for run_id in ("..", "%2e%2e", "..%2f", "...", "../../quant_bridge.py"):
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code in {404, 422}, (run_id, response.status_code, response.text)
        assert "PWNED" not in response.text, run_id
        assert "leaked-outside" not in response.text, run_id
    # A valid in-root run must still be served identically.
    output = _make_quant_run(tmp_path)
    assert client.get(f"/api/runs/{output.name}").status_code == 200


def test_image_artifact_endpoint_is_contained_and_readable(tmp_path, client):
    output = tmp_path / "orchestrator_output" / "20260801T120000Z"
    output.mkdir(parents=True)
    (output / "suite_context.json").write_text(json.dumps({"run_id": output.name}))
    (output / "chart.png").write_bytes(b"PNG")
    response = client.get(f"/api/runs/{output.name}/artifacts/chart.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == b"PNG"
    assert client.get(f"/api/runs/{output.name}/artifacts/../suite_context.json").status_code in {404, 422}


def test_run_validates_ticker_and_tracks_nonblocking_job(client, monkeypatch, tmp_path):
    captured = {}

    class FakeProcess:
        pid = 1234
        returncode = None
        def poll(self):
            return self.returncode
        def communicate(self, timeout=0):
            self.returncode = 0
            return (b"completed\n", b"")
        def kill(self):
            self.returncode = -9

    def fake_popen(command, **kwargs):
        captured["command"] = command
        return FakeProcess()

    monkeypatch.setattr(quant_bridge.subprocess, "Popen", fake_popen)
    response = client.post("/api/run", json={"ticker": "AMD", "suite": "vol", "index": "SPY", "strike": 100.0})
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    assert "--suite" in captured["command"] and "vol" in captured["command"]
    assert "--ticker" in captured["command"] and "AMD" in captured["command"]
    status = client.get(f"/api/run/{job_id}")
    assert status.status_code == 200
    assert status.json()["status"] in {"running", "completed"}
    assert client.post("/api/run", json={"ticker": "bad ticker", "suite": "vol"}).status_code == 422
    assert client.post("/api/run", json={"ticker": "AMD;rm", "suite": "unified"}).status_code == 422


def test_run_command_is_local_orchestrator_only(client, monkeypatch):
    class FakeProcess:
        pid = 1
        returncode = 0
        def poll(self): return 0

    monkeypatch.setattr(quant_bridge.subprocess, "Popen", lambda command, **kwargs: FakeProcess())
    response = client.post("/api/run", json={"ticker": "BRK.B", "suite": "unified"})
    assert response.status_code == 202
    command = quant_bridge._jobs[response.json()["job_id"]]["command"]
    assert command[1].endswith("orchestrator.py")
    assert "python.exe" in command[0] or command[0].endswith("python")
    assert "--unified" in command
    assert all("shell" not in str(value).lower() for value in command)


def test_cli_module_help_exists():
    assert hasattr(quant_bridge, "app")
    assert quant_bridge.build_command("AMD", "sentiment", None, None)[-2:] == ["--ticker", "AMD"]


def test_discover_output_dir_does_not_fallback_for_owned_missing_run(tmp_path, monkeypatch):
    owned = tmp_path / "owned-job"
    unrelated = tmp_path / "unrelated-job"
    owned.mkdir()
    unrelated.mkdir()
    (unrelated / "suite_context.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(quant_bridge, "OUTPUT_ROOT", tmp_path)

    assert quant_bridge._discover_output_dir(0, "owned-job") is None


def test_discover_output_dir_uses_owned_run_before_mtime(tmp_path, monkeypatch):
    owned = tmp_path / "owned-job"
    unrelated = tmp_path / "unrelated-job"
    owned.mkdir()
    unrelated.mkdir()
    (owned / "suite_context.json").write_text("{}", encoding="utf-8")
    (unrelated / "suite_context.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(quant_bridge, "OUTPUT_ROOT", tmp_path)

    assert quant_bridge._discover_output_dir(0, "owned-job") == str(owned)


def test_modules_registry_is_stable(client):
    response = client.get("/api/modules")
    assert response.status_code == 200
    assert [module["id"] for module in response.json()["modules"]] == [
        "vol", "options", "var", "sentiment", "dtcc", "backtesting"
    ]
    modules = {module["id"]: module for module in response.json()["modules"]}
    assert modules["vol"] == {
        "id": "vol", "name": "Vol Suite", "suite": "vol",
        "focus": "vol", "runnable": True,
    }
    assert modules["options"]["suite"] == "options"
    assert modules["options"]["focus"] == "options"

    assert modules["var"]["focus"] == "var"
    assert modules["dtcc"]["runnable"] is False


def test_production_modules_have_no_dealer_model_override():
    """No dashboard module can select an alternate dealer model."""
    response = TestClient(quant_bridge.app).get("/api/modules")
    modules = {module["id"]: module for module in response.json()["modules"]}
    assert "env_passthrough" not in modules["vol"]
    assert "env_passthrough" not in modules["options"]
    assert quant_bridge._module_passthrough_env("vol") == {}
    assert quant_bridge._module_passthrough_env("options") == {}
    assert quant_bridge._module_passthrough_env("nope") == {}


def test_module_detail_returns_latest_relevant_result(client, tmp_path):
    output = tmp_path / "orchestrator_output" / "20260801T120000Z"
    output.mkdir(parents=True)
    (output / "suite_context.json").write_text(json.dumps({
        "run_id": "20260801T120000Z", "created_at_utc": "2026-08-01T12:00:00Z",
        "focus": {"ticker": "AMD", "module": "options"},
    }))
    (output / "options_result.json").write_text(json.dumps({"status": "ok", "score": 7}))
    response = client.get("/api/modules/options")
    assert response.status_code == 200
    payload = response.json()
    assert payload["module"]["id"] == "options"
    assert payload["latest"]["run_id"] == "20260801T120000Z"
    assert payload["latest"]["result"]["status"] == "ok"


def test_invalid_and_non_runnable_modules_are_rejected(client):
    assert client.get("/api/modules/nope").status_code == 404
    assert client.post("/api/modules/nope/run", json={"ticker": "AMD"}).status_code == 404
    response = client.post("/api/modules/dtcc/run", json={"ticker": "AMD"})
    assert response.status_code == 400
    assert "not runnable" in response.json()["detail"].lower()


def test_module_run_maps_to_existing_orchestrator_without_network(client, monkeypatch):
    captured = {}

    class FakeProcess:
        pid = 2345
        returncode = None
        def poll(self): return self.returncode

    def fake_popen(command, **kwargs):
        captured["command"] = command
        return FakeProcess()

    monkeypatch.setattr(quant_bridge.subprocess, "Popen", fake_popen)
    response = client.post("/api/modules/options/run", json={
        "ticker": "AMD", "index": "QQQ", "strike": 100.0,
    })
    assert response.status_code == 202
    command = captured["command"]
    assert command[1].endswith("orchestrator.py")
    assert "--suite" in command
    assert command[command.index("--suite") + 1] == "options"
    assert "--unified" not in command
    assert command[command.index("--ticker") + 1] == "AMD"
    assert command[command.index("--index") + 1] == "QQQ"
    assert command[command.index("--strike") + 1] == "100.0"
    assert all("shell" not in str(value).lower() for value in command)


def test_build_command_uses_shared_project_python_when_worktree_venv_missing(monkeypatch):
    monkeypatch.setattr(quant_bridge, "PROJECT_PYTHON", Path("/missing/worktree/python"))
    shared = Path("/shared/project/python")
    monkeypatch.setattr(quant_bridge, "SHARED_PROJECT_PYTHON", shared)
    monkeypatch.setattr(Path, "is_file", lambda path: path == shared)
    command = quant_bridge.build_command("SPY", "vol", None, None)
    assert command[0] == str(shared)


def _make_quant_run(root, run_id="20260801T120000Z", module="options"):
    output = root / "orchestrator_output" / run_id
    output.mkdir(parents=True)
    (output / "suite_context.json").write_text(json.dumps({"run_id": run_id, "focus": {"ticker": "AMD"}}))
    (output / f"{module}_result.json").write_text(json.dumps({"status": "ok", "score": 7, "secret": "do-not-leak"}))
    return output


def test_synthesize_endpoint_returns_strict_result_and_provenance(client, tmp_path, monkeypatch):
    output = _make_quant_run(tmp_path)
    response = client.post("/api/quant/synthesize", json={"run_id": output.name, "module": "options"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["result"]["worker"] == "quant"
    assert payload["provenance"]["source_run_id"] == output.name
    assert payload["report_paths"] == ["quant_summary.json", "quant_summary.md"]
    assert all(not str(output) in json.dumps(payload) for _ in [0])


def test_synthesize_rejects_escape_and_report_filename(client, tmp_path):
    assert client.post("/api/quant/synthesize", json={"run_id": "../escape", "module": "options"}).status_code == 422
    output = _make_quant_run(tmp_path)
    response = client.post("/api/quant/synthesize", json={"run_id": output.name, "module": "options", "worker_reports": ["../secret.json"]})
    assert response.status_code == 422


def test_quant_action_maps_interpret_without_worker_spawn(client, tmp_path, monkeypatch):
    output = _make_quant_run(tmp_path)
    monkeypatch.setattr(quant_bridge.worker_broker, "dispatch", lambda request: (_ for _ in ()).throw(AssertionError("interpret spawned worker")))
    response = client.post("/api/quant/action", json={"action": "interpret", "module": "options", "ticker": "AMD", "run_id": output.name, "idempotency_key": "interpret-1"})
    assert response.status_code == 202
    assert response.json()["worker"] == "quant"


def test_quant_action_worker_mapping_requires_auth_and_is_idempotent(client, tmp_path, monkeypatch):
    output = _make_quant_run(tmp_path)
    calls = []
    monkeypatch.setattr(quant_bridge.worker_broker, "dispatch", lambda request: (calls.append(request) or {"task_id": "task-1", "worker": request.worker, "status": "queued"}))
    body = {"action": "investigate", "module": "options", "ticker": "AMD", "run_id": output.name, "idempotency_key": "worker-1"}
    assert client.post("/api/quant/action", json=body).status_code == 401
    token = quant_bridge.worker_broker.token
    headers = {"Authorization": f"Bearer {token}"}
    first = client.post("/api/quant/action", headers=headers, json=body)
    second = client.post("/api/quant/action", headers=headers, json=body)
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert calls[0].worker == "coder"
    assert len(calls) == 1


def test_quant_action_rejects_irrelevant_mutation_and_profile_override(client, tmp_path):
    output = _make_quant_run(tmp_path)
    base = {"module": "options", "ticker": "AMD", "run_id": output.name, "idempotency_key": "bad-1"}
    assert client.post("/api/quant/action", json={**base, "action": "trade"}).status_code == 422
    assert client.post("/api/quant/action", json={**base, "action": "explain", "profile": "root"}).status_code == 422


def test_quant_action_maps_explain_and_watchlist_personal_bot_status(client, tmp_path, monkeypatch):
    output = _make_quant_run(tmp_path)
    calls = []
    monkeypatch.setattr(quant_bridge.worker_broker, "dispatch", lambda request: (calls.append(request) or {"task_id": "task-2", "worker": request.worker, "status": "unavailable", "adapter_status": "unavailable"}))
    headers = {"Authorization": f"Bearer {quant_bridge.worker_broker.token}"}
    for action, worker in (("explain", "research"), ("watchlist", "personal-bot")):
        body = {"action": action, "module": "options", "ticker": "AMD", "run_id": output.name, "idempotency_key": action}
        result = client.post("/api/quant/action", headers=headers, json=body)
        assert result.status_code == 202
        assert result.json()["worker"] == worker
    assert [call.worker for call in calls] == ["research", "personal-bot"]
    assert calls[1].ticker == "AMD"
    assert calls[1].source_run_id == output.name


def test_quant_reports_are_relevant_and_sanitized(client, tmp_path):
    output = _make_quant_run(tmp_path)
    (output / "var_result.json").write_text(json.dumps({"var": 4, "api_key": "secret"}))
    response = client.get(f"/api/quant/reports/{output.name}")
    assert response.status_code == 200
    text = json.dumps(response.json())
    assert "options_result.json" in text and "var_result.json" in text
    assert "do-not-leak" not in text and "secret" not in text
    assert str(output) not in text


def test_module_run_sanitizes_hermes_python_environment(client, monkeypatch):
    captured = {}

    class FakeProcess:
        pid = 2346
        returncode = None
        def poll(self): return self.returncode

    def fake_popen(command, **kwargs):
        captured["env"] = kwargs["env"]
        return FakeProcess()

    monkeypatch.setenv("PYTHONPATH", "/wrong/hermes/python311")
    monkeypatch.setenv("VIRTUAL_ENV", "/wrong/hermes/venv")
    monkeypatch.setattr(quant_bridge.subprocess, "Popen", fake_popen)
    response = client.post("/api/modules/vol/run", json={"ticker": "MU"})
    assert response.status_code == 202
    assert captured["env"]["PYTHONPATH"] == str(quant_bridge.ROOT)
    assert captured["env"].get("VIRTUAL_ENV") is None
