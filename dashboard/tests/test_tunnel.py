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
