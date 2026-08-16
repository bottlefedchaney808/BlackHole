"""test_tunnel.py

Covers dashboard.tunnel.TunnelManager, the in-memory Cloudflare quick-tunnel
process manager backing the dashboard's Share feature. This task (Task 1 of
the shareable-dashboard plan) covers only the TunnelManager unit tests; route
tests (`/share/*`) are added to this same file by a later task.

No real `cloudflared` process is ever spawned: `asyncio.create_subprocess_exec`
is monkeypatched with a fake async process throughout. No network is used.
"""

import asyncio

import pytest

from dashboard.tunnel import TunnelManager, TunnelStartError, TunnelUnavailable

pytestmark = pytest.mark.unit


class FakeStreamReader:
    """Fake asyncio StreamReader that yields canned lines, then EOF (b"")."""

    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return b""


class FakeProcess:
    """Fake asyncio subprocess.Process."""

    def __init__(self, stderr_lines, pid=4242, exit_code=None):
        self.stderr = FakeStreamReader(stderr_lines)
        self.pid = pid
        self.returncode = None
        self._exit_code = exit_code
        self.terminate_called = False
        self.kill_called = False

    async def wait(self):
        self.returncode = self._exit_code if self._exit_code is not None else 0
        return self.returncode

    def terminate(self):
        self.terminate_called = True
        self.returncode = 0

    def kill(self):
        self.kill_called = True
        self.returncode = -9


def make_factory(fake_proc):
    """Build an async factory usable as asyncio.create_subprocess_exec, tracking call count."""
    calls = {"count": 0}

    async def factory(*args, **kwargs):
        calls["count"] += 1
        return fake_proc

    return factory, calls


SUCCESS_LINE = (
    b"Your quick Tunnel has been created! Visit it at (it may take some time "
    b"to be reachable): https://wispy-keys-9.trycloudflare.com\n"
)


def test_is_available_true(monkeypatch):
    monkeypatch.setattr(
        "dashboard.tunnel.shutil.which", lambda name: "C:/bin/cloudflared.exe"
    )
    tm = TunnelManager()
    assert tm.is_available() is True


def test_is_available_false(monkeypatch):
    monkeypatch.setattr("dashboard.tunnel.shutil.which", lambda name: None)
    tm = TunnelManager()
    assert tm.is_available() is False


def test_start_parses_url_and_sets_running(monkeypatch):
    monkeypatch.setattr("dashboard.tunnel.shutil.which", lambda name: "cloudflared")
    fake_proc = FakeProcess([SUCCESS_LINE], pid=1234)
    factory, calls = make_factory(fake_proc)
    monkeypatch.setattr("asyncio.create_subprocess_exec", factory)

    tm = TunnelManager()
    result = asyncio.run(tm.start())

    assert result["state"] == "running"
    assert result["running"] is True
    assert result["url"] == "https://wispy-keys-9.trycloudflare.com"
    assert result["pid"] == 1234
    assert calls["count"] == 1


def test_start_raises_when_unavailable(monkeypatch):
    monkeypatch.setattr("dashboard.tunnel.shutil.which", lambda name: None)
    tm = TunnelManager()

    with pytest.raises(TunnelUnavailable):
        asyncio.run(tm.start())


def test_start_is_idempotent_when_already_running(monkeypatch):
    monkeypatch.setattr("dashboard.tunnel.shutil.which", lambda name: "cloudflared")
    fake_proc = FakeProcess([SUCCESS_LINE], pid=555)
    factory, calls = make_factory(fake_proc)
    monkeypatch.setattr("asyncio.create_subprocess_exec", factory)

    tm = TunnelManager()

    async def run_twice():
        first = await tm.start()
        second = await tm.start()
        return first, second

    first, second = asyncio.run(run_twice())

    assert calls["count"] == 1
    assert first["url"] == second["url"]
    assert first["url"] == "https://wispy-keys-9.trycloudflare.com"


def test_start_raises_tunnel_start_error_when_process_exits_without_url(monkeypatch):
    monkeypatch.setattr("dashboard.tunnel.shutil.which", lambda name: "cloudflared")
    fake_proc = FakeProcess(
        [b"failed to connect to origin\n", b"context deadline exceeded\n"],
        pid=999,
        exit_code=1,
    )
    factory, _calls = make_factory(fake_proc)
    monkeypatch.setattr("asyncio.create_subprocess_exec", factory)

    tm = TunnelManager()

    with pytest.raises(TunnelStartError):
        asyncio.run(tm.start())

    status = tm.status()
    assert status["state"] == "stopped"
    assert status["running"] is False


def test_stop_terminates_process_and_clears_state(monkeypatch):
    monkeypatch.setattr("dashboard.tunnel.shutil.which", lambda name: "cloudflared")
    fake_proc = FakeProcess([SUCCESS_LINE], pid=777)
    factory, _ = make_factory(fake_proc)
    monkeypatch.setattr("asyncio.create_subprocess_exec", factory)

    tm = TunnelManager()

    async def start_then_stop():
        await tm.start()
        return await tm.stop()

    result = asyncio.run(start_then_stop())

    assert result["state"] == "idle"
    assert result["running"] is False
    assert fake_proc.terminate_called is True


def test_stop_noop_when_nothing_running(monkeypatch):
    monkeypatch.setattr("dashboard.tunnel.shutil.which", lambda name: "cloudflared")
    tm = TunnelManager()

    result = asyncio.run(tm.stop())

    assert result["running"] is False
    assert result["last_error"] is None


def test_status_detects_process_that_died_on_its_own(monkeypatch):
    monkeypatch.setattr("dashboard.tunnel.shutil.which", lambda name: "cloudflared")
    fake_proc = FakeProcess([SUCCESS_LINE], pid=321)
    factory, _ = make_factory(fake_proc)
    monkeypatch.setattr("asyncio.create_subprocess_exec", factory)

    tm = TunnelManager()
    started = asyncio.run(tm.start())
    assert started["running"] is True

    # Simulate the process dying on its own, between status() checks.
    fake_proc.returncode = 0
    status = tm.status()

    assert status["running"] is False
    assert status["state"] == "stopped"


def test_concurrent_start_spawns_exactly_one_subprocess(monkeypatch):
    monkeypatch.setattr("dashboard.tunnel.shutil.which", lambda name: "cloudflared")
    fake_proc = FakeProcess([SUCCESS_LINE], pid=88)
    factory, calls = make_factory(fake_proc)
    monkeypatch.setattr("asyncio.create_subprocess_exec", factory)

    tm = TunnelManager()

    async def run_concurrently():
        return await asyncio.gather(tm.start(), tm.start())

    results = asyncio.run(run_concurrently())

    assert calls["count"] == 1
    assert results[0]["url"] == results[1]["url"]


# ---------------------------------------------------------------------------
# Route tests (FastAPI TestClient with a fake TunnelManager)
# ---------------------------------------------------------------------------

from fastapi.testclient import TestClient

import dashboard.app as app_module


class _FakeManager:
    def __init__(self):
        self.status_result = {
            "running": False, "url": None, "state": "idle",
            "started_at": None, "pid": None, "last_error": None,
        }
        self.start_exc = None
        self.started = 0
        self.stopped = 0

    def status(self):
        return dict(self.status_result)

    async def start(self):
        self.started += 1
        if self.start_exc is not None:
            raise self.start_exc
        self.status_result = {
            "running": True,
            "url": "https://foo.example.trycloudflare.com",
            "state": "running",
            "started_at": "2026-08-15T00:00:00+00:00",
            "pid": 1,
            "last_error": None,
        }
        return self.status()

    async def stop(self):
        self.stopped += 1
        self.status_result["running"] = False
        self.status_result["state"] = "idle"
        self.status_result["url"] = None
        return self.status()


@pytest.fixture
def fake_mgr(monkeypatch):
    mgr = _FakeManager()
    monkeypatch.setattr(app_module, "tunnel_manager", mgr)
    return mgr


def test_share_status_shape(fake_mgr):
    client = TestClient(app_module.app)
    resp = client.get("/share/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is False
    assert body["url"] is None
    assert body["state"] == "idle"


def test_share_start_returns_200_and_url(fake_mgr):
    client = TestClient(app_module.app)
    resp = client.post("/share/start")
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is True
    assert body["url"] == "https://foo.example.trycloudflare.com"


def test_share_start_409_when_unavailable(fake_mgr):
    fake_mgr.start_exc = TunnelUnavailable("cloudflared not found on PATH.")
    client = TestClient(app_module.app)
    resp = client.post("/share/start")
    assert resp.status_code == 409
    assert "cloudflared" in resp.json()["error"]


def test_share_start_500_when_start_error(fake_mgr):
    fake_mgr.start_exc = TunnelStartError("fatal: boom")
    client = TestClient(app_module.app)
    resp = client.post("/share/start")
    assert resp.status_code == 500
    assert "boom" in resp.json()["error"]


def test_share_stop_idempotent(fake_mgr):
    client = TestClient(app_module.app)
    r1 = client.post("/share/stop")
    r2 = client.post("/share/stop")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["running"] is False
    assert fake_mgr.stopped == 2
