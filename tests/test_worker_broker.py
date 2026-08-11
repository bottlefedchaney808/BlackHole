import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import quant_bridge  # noqa: E402


def _can_symlink(tmp_path):
    """Windows may lack symlink privileges (developer mode/admin); skip those cases."""
    target = tmp_path / "symlink-target"
    link = tmp_path / "symlink-link"
    target.write_text("x", encoding="utf-8")
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        return False
    return True


def test_worker_dispatch_requires_bearer_token(tmp_path, monkeypatch):
    monkeypatch.setattr(quant_bridge, "WORKER_TOKEN_PATH", tmp_path / "worker.token")
    client = TestClient(quant_bridge.app)
    response = client.post("/api/workers/dispatch", json={"worker": "coder", "prompt": "hello"})
    assert response.status_code == 401


def test_worker_token_is_user_only_and_constant_compare(monkeypatch, tmp_path):
    monkeypatch.setattr(quant_bridge, "WORKER_TOKEN_PATH", tmp_path / "worker.token")
    token = quant_bridge.worker_broker.token
    assert token and (tmp_path / "worker.token").read_text().strip() == token
    if os.name != "nt":
        assert oct((tmp_path / "worker.token").stat().st_mode & 0o777) == "0o600"
    assert quant_bridge.worker_broker.authorize("Bearer " + token)
    assert not quant_bridge.worker_broker.authorize("Bearer wrong")


def test_unknown_worker_and_profile_override_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(quant_bridge, "WORKER_TOKEN_PATH", tmp_path / "worker.token")
    client = TestClient(quant_bridge.app)
    token = quant_bridge.worker_broker.token
    headers = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/workers/dispatch", headers=headers, json={"worker": "unknown", "prompt": "x"}).status_code == 422
    assert client.post("/api/workers/dispatch", headers=headers, json={"worker": "coder", "profile": "root", "prompt": "x"}).status_code == 422


def test_worker_list_is_authenticated(tmp_path, monkeypatch):
    monkeypatch.setattr(quant_bridge, "WORKER_TOKEN_PATH", tmp_path / "worker.token")
    client = TestClient(quant_bridge.app)
    assert client.get("/api/workers").status_code == 401
    response = client.get("/api/workers", headers={"Authorization": f"Bearer {quant_bridge.worker_broker.token}"})
    assert response.status_code == 200
    assert set(response.json()["workers"]) == {"coder", "research", "personal-bot"}


def test_prompt_cap_and_secret_marker_rejection(tmp_path, monkeypatch):
    monkeypatch.setattr(quant_bridge, "WORKER_TOKEN_PATH", tmp_path / "worker.token")
    client = TestClient(quant_bridge.app)
    headers = {"Authorization": f"Bearer {quant_bridge.worker_broker.token}"}
    assert client.post("/api/workers/dispatch", headers=headers, json={"worker": "coder", "prompt": "x" * 20001}).status_code == 422
    assert client.post("/api/workers/dispatch", headers=headers, json={"worker": "coder", "prompt": "my api_key is abc"}).status_code == 422


def test_dispatch_idempotency_returns_existing_job(tmp_path, monkeypatch):
    monkeypatch.setattr(quant_bridge, "WORKER_TOKEN_PATH", tmp_path / "worker.token")
    monkeypatch.setattr(quant_bridge.worker_broker, "_spawn", lambda job: None)
    client = TestClient(quant_bridge.app)
    headers = {"Authorization": f"Bearer {quant_bridge.worker_broker.token}"}
    body = {"worker": "coder", "prompt": "hello", "idempotency_key": "same-key"}
    first = client.post("/api/workers/dispatch", headers=headers, json=body)
    second = client.post("/api/workers/dispatch", headers=headers, json=body)
    assert first.status_code == 202 and second.status_code == 202
    assert first.json()["task_id"] == second.json()["task_id"]


def test_personal_bot_safe_unavailable_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(quant_bridge, "WORKER_TOKEN_PATH", tmp_path / "worker.token")
    client = TestClient(quant_bridge.app)
    headers = {"Authorization": f"Bearer {quant_bridge.worker_broker.token}"}
    response = client.post("/api/workers/dispatch", headers=headers, json={"worker": "personal-bot", "prompt": "hello"})
    assert response.status_code == 202
    assert response.json()["status"] in {"queued", "accepted", "unavailable"}


def test_localhost_cors_allows_authorization(tmp_path, monkeypatch):
    monkeypatch.setattr(quant_bridge, "WORKER_TOKEN_PATH", tmp_path / "worker.token")
    client = TestClient(quant_bridge.app)
    response = client.options("/api/workers", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"})
    assert response.status_code == 200
    assert "authorization" in response.headers.get("access-control-allow-headers", "").lower()


if __name__ == "__main__":
    raise SystemExit()

def test_worker_command_uses_argv_and_scrubs_environment(monkeypatch):
    import worker_broker
    captured = {}
    class Process:
        pid = 123
        returncode = 0
        def communicate(self, timeout=None):
            return b"ok", b""
        def poll(self):
            return self.returncode
    def popen(argv, **kwargs):
        captured.update(argv=argv, kwargs=kwargs)
        return Process()
    monkeypatch.setattr(worker_broker.subprocess, "Popen", popen)
    monkeypatch.setattr(worker_broker.shutil, "which", lambda name: "/usr/local/bin/hermes")
    monkeypatch.setenv("PYTHONPATH", "bad")
    monkeypatch.setenv("VIRTUAL_ENV", "bad")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    broker = worker_broker.WorkerBroker(lambda: Path("/tmp/nonexistent-token-test"))
    task = worker_broker.DispatchRequest(worker="coder", prompt="run safely")
    result = broker.dispatch(task)
    assert all(isinstance(item, str) for item in captured["argv"])
    assert captured["kwargs"]["shell"] is False
    assert "PYTHONPATH" not in captured["kwargs"]["env"]
    assert "VIRTUAL_ENV" not in captured["kwargs"]["env"]
    assert "OPENAI_API_KEY" not in captured["kwargs"]["env"]
    assert captured["argv"][:5] == ["/usr/local/bin/hermes", "--profile", "coder", "chat", "-q"]
    assert worker_broker.PROMPT_BEGIN in captured["argv"][-1]
    assert result["status"] == "succeeded"


@pytest.mark.parametrize("worker,profile", [("coder", "coder"), ("research", "research")])
def test_fresh_workers_use_hermes_profile_chat_argv(monkeypatch, worker, profile):
    import worker_broker
    captured = {}

    class Process:
        pid = 123
        returncode = 0
        def communicate(self, timeout=None):
            return b"ok", b""
        def poll(self):
            return self.returncode

    monkeypatch.setattr(worker_broker.shutil, "which", lambda name: "/opt/hermes/bin/hermes")
    monkeypatch.setattr(worker_broker.subprocess, "Popen", lambda argv, **kwargs: (captured.update(argv=argv, kwargs=kwargs) or Process()))
    broker = worker_broker.WorkerBroker(lambda: Path("/tmp/hermes-worker-token"))

    result = broker.dispatch(worker_broker.DispatchRequest(worker=worker, prompt="investigate safely"))

    assert captured["argv"][:5] == ["/opt/hermes/bin/hermes", "--profile", profile, "chat", "-q"]
    assert captured["argv"][5].startswith(worker_broker.PROMPT_BEGIN)
    assert captured["kwargs"]["shell"] is False
    assert result["status"] == "succeeded"


def test_personal_bot_uses_adapter_status_without_spawning(monkeypatch, tmp_path):
    import worker_broker
    spawned = False

    def forbidden_spawn(*args, **kwargs):
        nonlocal spawned
        spawned = True
        raise AssertionError("personal-bot must not spawn a fresh process")

    monkeypatch.setattr(worker_broker.subprocess, "Popen", forbidden_spawn)
    broker = worker_broker.WorkerBroker(lambda: Path("/tmp/personal-bot-token"))
    broker._handoff_store_dir = tmp_path / "handoffs"
    broker._handoff_heartbeat_path = tmp_path / "missing.heartbeat"

    result = broker.dispatch(worker_broker.DispatchRequest(worker="personal-bot", prompt="check watchlist"))

    assert worker_broker.PROFILES["personal-bot"] == "personal-bot"
    assert result["status"] == "unavailable"
    assert result["retryable"] is True
    assert result["adapter"] == "personal-bot-gateway"
    assert spawned is False


def test_personal_bot_fresh_heartbeat_durably_accepts_watchlist_handoff(tmp_path, monkeypatch):
    import worker_broker
    heartbeat = tmp_path / "gateway.heartbeat"
    heartbeat.write_text(json.dumps({"updated_at": worker_broker._now()}), encoding="utf-8")
    broker = worker_broker.WorkerBroker(lambda: tmp_path / "worker.token")
    broker._handoff_store_dir = tmp_path / "handoffs"
    broker._handoff_heartbeat_path = heartbeat
    monkeypatch.setattr(worker_broker.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("must not spawn personal-bot"))

    result = broker.dispatch(worker_broker.DispatchRequest(worker="personal-bot", prompt="add signal to watchlist", idempotency_key="watch-1"))

    assert result["status"] == "accepted"
    assert result["adapter"] == "personal-bot-gateway"
    record = json.loads((tmp_path / "handoffs" / f"{result['handoff_id']}.json").read_text(encoding="utf-8"))
    assert record["status"] == "accepted"
    assert record["summary"] == "add signal to watchlist"


def test_personal_bot_stale_or_missing_heartbeat_is_unavailable(tmp_path):
    import worker_broker
    broker = worker_broker.WorkerBroker(lambda: tmp_path / "worker.token")
    broker._handoff_store_dir = tmp_path / "handoffs"
    broker._handoff_heartbeat_path = tmp_path / "missing.heartbeat"
    missing = broker.dispatch(worker_broker.DispatchRequest(worker="personal-bot", prompt="watch SPY"))
    assert missing["status"] == "unavailable"
    assert not (tmp_path / "handoffs").exists()

    stale = tmp_path / "stale.heartbeat"
    stale.write_text(json.dumps({"updated_at": "2020-01-01T00:00:00Z"}), encoding="utf-8")
    broker._handoff_heartbeat_path = stale
    result = broker.dispatch(worker_broker.DispatchRequest(worker="personal-bot", prompt="watch QQQ"))
    assert result["status"] == "unavailable"


def test_personal_bot_dispatch_is_idempotent_and_does_not_duplicate_handoff(tmp_path):
    import worker_broker
    heartbeat = tmp_path / "gateway.heartbeat"
    heartbeat.write_text(json.dumps({"updated_at": worker_broker._now()}), encoding="utf-8")
    broker = worker_broker.WorkerBroker(lambda: tmp_path / "worker.token")
    broker._handoff_store_dir = tmp_path / "handoffs"
    broker._handoff_heartbeat_path = heartbeat

    body = worker_broker.DispatchRequest(worker="personal-bot", prompt="watch NVDA", idempotency_key="same-watch")
    first = broker.dispatch(body)
    second = broker.dispatch(body)

    assert first["status"] == second["status"] == "accepted"
    assert first["task_id"] == second["task_id"]
    assert len(list((tmp_path / "handoffs").glob("*.json"))) == 1


def test_run_id_and_symlink_boundaries(tmp_path):
    import worker_broker
    assert worker_broker.safe_run_dir(tmp_path, "run-1").parent == tmp_path
    with pytest.raises(ValueError):
        worker_broker.safe_run_dir(tmp_path, "../escape")
    if not _can_symlink(tmp_path):
        pytest.skip("symlink privileges unavailable on this host")
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "outside", target_is_directory=False)
    with pytest.raises(ValueError):
        worker_broker.safe_run_dir(tmp_path, "link")


def test_output_tail_is_bounded_and_redacted():
    import worker_broker
    assert len(worker_broker.sanitize_tail("x" * 100_000)) == worker_broker.MAX_OUTPUT
    assert "api_key" not in worker_broker.sanitize_tail("api_key=do-not-leak")
