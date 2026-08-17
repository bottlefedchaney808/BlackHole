# Shareable Dashboard (Tunnel) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an in-app "Share" control to the dashboard that starts/stops a Cloudflare quick tunnel and shows the live public URL, with no auth (plain tunnel, by design).

**Architecture:** A new `TunnelManager` in `dashboard/tunnel.py` owns a `cloudflared tunnel --url http://127.0.0.1:8787` child subprocess (async), extracting the public `https://…trycloudflare.com` URL from its stderr. Three new FastAPI routes (`/share/status`, `/share/start`, `/share/stop`) drive it. A lifespan hook terminates the child on dashboard shutdown so no orphaned tunnel lingers. A small Share control in `dashboard/templates/base.html` (renders on every page) shows status and a Start/Stop toggle with a confirmation prompt on Start.

**Tech Stack:** Python 3.12 (shared venv), FastAPI, uvicorn, asyncio, pytest, Jinja2, `cloudflared` (installed, v2026.7.3).

## Global Constraints

- No auth layer added — plain quick tunnel, shared only with trusted people (explicit user decision).
- `cloudflared` must be resolved via `shutil.which`; a missing binary must fail with a clear message, never a traceback.
- URL regex: `https://[a-z0-9-]+\.trycloudflare\.com` (the line emitted by cloudflared is `Your quick Tunnel has been created! ... https://<words>.trycloudflare.com`).
- Routes must be `async def`; subprocess reading uses `asyncio`, never blocking `subprocess.Popen` or threads.
- Exactly one tunnel at a time (an `asyncio.Lock` guards start/stop).
- Tunnel state is in-memory only; dashboard restart yields a fresh "Not sharing" state.
- Unit tests must NOT spawn a real `cloudflared` and must NOT hit the network. They must NOT require `pytest-asyncio` — wrap async calls with `asyncio.run(...)` in plain sync test functions (it is not installed in the shared venv).
- **Venv:** this worktree has no `.venv`. Use the shared venv at the primary tree: `C:\Users\bottl\FinancialDevelopment\.venv\Scripts\python.exe`. Set `PYSHARED` below once per shell. Run commands from the worktree root (`C:\Users\bottl\FinancialDevelopment\.worktrees\feat-shareable-dashboard`).
- Repo uses ruff (`ruff check` + `ruff format`); ruff 0.16.0 is in the shared venv.
- Commit subjects follow the repo policy (`feat|fix|test|docs|refactor|chore|security|improve`).

**Setup (once):**
```bash
cd /c/Users/bottl/FinancialDevelopment/.worktrees/feat-shareable-dashboard
export PYSHARED="C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe"
```
(Note: the parent repo's venv must have `requirements.txt` installed. It already has `httpx`, `pytest`, and `ruff`; `pytest-asyncio` is intentionally NOT required.)

---

### Task 1: `TunnelManager` (dashboard/tunnel.py)

**Files:**
- Create: `dashboard/tunnel.py`
- Test: `dashboard/tests/test_tunnel.py`

**Interfaces:**
- Consumes: nothing (standalone module).
- Produces:
  - `class TunnelManager(target_url: str = "http://127.0.0.1:8787")`
  - `def is_available(self) -> bool`
  - `async def start(self) -> dict` — raises `TunnelUnavailable` or `TunnelStartError`
  - `async def stop(self) -> dict`
  - `def status(self) -> dict` — `{"running", "url", "state", "started_at", "pid", "last_error"}`
  - `async def shutdown(self) -> None`
  - `class TunnelUnavailable(Exception)`
  - `class TunnelStartError(Exception)`

- [ ] **Step 1: Write the failing test**

Create `dashboard/tests/test_tunnel.py`:

```python
"""test_tunnel.py

Covers dashboard/tunnel.py (the Cloudflare quick-tunnel manager).
Run via the shared venv from the repo root:
  C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe -m pytest dashboard/tests/test_tunnel.py -v
No real cloudflared and no network are used -- the subprocess spawn is
mocked via a fake Process with an async stderr. Async methods are driven
with asyncio.run() so pytest-asyncio is not required.
"""
import asyncio
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dashboard.tunnel import TunnelManager, TunnelStartError, TunnelUnavailable

pytestmark = pytest.mark.unit


class _FakeStderr:
    """Async stderr that yields canned lines then EOF."""

    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        if not self._lines:
            return b""
        return self._lines.pop(0)


class _FakeProc:
    def __init__(self, stderr, pid=4242, returncode=None):
        self.stderr = stderr
        self.pid = pid
        self.returncode = returncode
        self.terminated = False
        self.killed = False
        self.waited = False

    async def wait(self):
        self.waited = True
        self.returncode = 0
        return 0

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


@pytest.fixture
def fake_spawn(monkeypatch):
    """Replace asyncio.create_subprocess_exec with a factory returning fakes."""
    procs = []

    async def _spawn(*args, **kwargs):
        p = _FakeProc(_FakeStderr([]))
        procs.append(p)
        return p

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _spawn)
    return procs


# is_available -----------------------------------------------------------

def test_is_available_true_when_on_path(monkeypatch):
    monkeypatch.setattr(
        "dashboard.tunnel.shutil.which", lambda name: "/usr/bin/cloudflared"
    )
    assert TunnelManager().is_available() is True


def test_is_available_false_when_missing(monkeypatch):
    monkeypatch.setattr("dashboard.tunnel.shutil.which", lambda name: None)
    assert TunnelManager().is_available() is False


# start() URL parsing ----------------------------------------------------

def test_start_parses_url_from_stderr(monkeypatch, fake_spawn):
    url_line = (
        b"Your quick Tunnel has been created! Visit it at "
        b"(it may take some time to be reachable): "
        b"https://wispy-keys-9.example.trycloudflare.com\n"
    )
    fake_spawn[0].stderr = _FakeStderr([url_line])

    tm = TunnelManager()
    st = asyncio.run(tm.start())

    assert st["state"] == "running"
    assert st["running"] is True
    assert st["url"] == "https://wispy-keys-9.example.trycloudflare.com"
    assert st["pid"] == 4242
    asyncio.run(tm.shutdown())


def test_start_unavailable_raises(monkeypatch):
    monkeypatch.setattr("dashboard.tunnel.shutil.which", lambda name: None)
    tm = TunnelManager()
    with pytest.raises(TunnelUnavailable):
        asyncio.run(tm.start())


def test_start_idempotent_when_running(monkeypatch, fake_spawn):
    url_line = (
        b"Your quick Tunnel has been created! ... "
        b"https://foo.example.trycloudflare.com\n"
    )
    fake_spawn[0].stderr = _FakeStderr([url_line])
    tm = TunnelManager()
    first = asyncio.run(tm.start())
    second = asyncio.run(tm.start())
    assert first["url"] == second["url"]
    assert len(fake_spawn) == 1  # no second subprocess spawned
    asyncio.run(tm.shutdown())


def test_start_error_when_exits_without_url(monkeypatch, fake_spawn):
    fake_spawn[0].stderr = _FakeStderr([b"fatal: could not connect\n"])
    fake_spawn[0].returncode = 1
    tm = TunnelManager()
    with pytest.raises(TunnelStartError):
        asyncio.run(tm.start())
    asyncio.run(tm.shutdown())


# stop() -----------------------------------------------------------------

def test_stop_terminates_and_clears(monkeypatch, fake_spawn):
    fake_spawn[0].stderr = _FakeStderr([
        b"Your quick Tunnel ... https://foo.example.trycloudflare.com\n"
    ])
    tm = TunnelManager()
    asyncio.run(tm.start())
    st = asyncio.run(tm.stop())
    assert st["running"] is False
    assert st["state"] == "idle"
    assert fake_spawn[0].terminated is True


def test_stop_noop_when_not_running(monkeypatch, fake_spawn):
    tm = TunnelManager()
    st = asyncio.run(tm.stop())
    assert st["running"] is False


# process-died detection --------------------------------------------------

def test_status_reports_stopped_when_proc_died(monkeypatch, fake_spawn):
    fake_spawn[0].stderr = _FakeStderr([
        b"Your quick Tunnel ... https://foo.example.trycloudflare.com\n"
    ])
    fake_spawn[0].returncode = None  # alive at start
    tm = TunnelManager()
    asyncio.run(tm.start())
    # simulate the child dying on its own
    fake_spawn[0].returncode = 0
    st = tm.status()
    assert st["running"] is False
    assert st["state"] == "stopped"
    asyncio.run(tm.shutdown())


# lock serialization -----------------------------------------------------

def test_concurrent_start_spawns_one_proc(monkeypatch, fake_spawn):
    fake_spawn[0].stderr = _FakeStderr([
        b"Your quick Tunnel ... https://foo.example.trycloudflare.com\n"
    ])
    tm = TunnelManager()

    async def _both():
        await asyncio.gather(tm.start(), tm.start())

    asyncio.run(_both())
    assert len(fake_spawn) == 1
    asyncio.run(tm.shutdown())


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/bottl/FinancialDevelopment/.worktrees/feat-shareable-dashboard && "C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe" -m pytest dashboard/tests/test_tunnel.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboard.tunnel'`.

- [ ] **Step 3: Write minimal implementation**

Create `dashboard/tunnel.py`:

```python
"""dashboard/tunnel.py -- manage a Cloudflare quick-tunnel child process.

Owns a `cloudflared tunnel --url http://127.0.0.1:8787` subprocess, extracts
the public https URL from its stderr, and exposes start/stop/status for the
dashboard's Share control.

No auth by design: this is the documented account-less quick tunnel. State is
in-memory only; a dashboard restart yields a fresh "Not sharing" state.
"""
from __future__ import annotations

import asyncio
import re
import shutil
from datetime import datetime, timezone
from typing import Dict, List, Optional

_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
_START_TIMEOUT_SEC = 10.0
_MAX_STDERR_TAIL = 2000


class TunnelUnavailable(Exception):
    """cloudflared is not on PATH."""


class TunnelStartError(Exception):
    """cloudflared exited before emitting a tunnel URL."""


class TunnelManager:
    def __init__(self, target_url: str = "http://127.0.0.1:8787") -> None:
        self._target_url = target_url
        self._lock = asyncio.Lock()
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._url: Optional[str] = None
        self._state = "idle"
        self._started_at: Optional[str] = None
        self._last_error: Optional[str] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._url_seen = asyncio.Event()
        self._stderr_tail: List[str] = []

    # -- public API --------------------------------------------------------

    def is_available(self) -> bool:
        """True if a `cloudflared` binary is on PATH."""
        return shutil.which("cloudflared") is not None

    def status(self) -> Dict:
        """Current tunnel state as a plain dict (safe for JSON)."""
        # Detect a child that died on its own since the last check.
        proc = self._proc
        if proc is not None and proc.returncode is not None:
            self._state = "stopped"
            if self._url is None and not self._last_error:
                self._last_error = "cloudflared exited unexpectedly."
            self._proc = None
            self._url = None
        return {
            "running": self._state == "running",
            "url": self._url,
            "state": self._state,
            "started_at": self._started_at,
            "pid": proc.pid if proc is not None else None,
            "last_error": self._last_error,
        }

    async def start(self) -> Dict:
        """Spawn the tunnel and wait (briefly) for the URL or an error."""
        if not self.is_available():
            raise TunnelUnavailable(
                "cloudflared not found on PATH. Install it or run "
                "`cloudflared tunnel --url http://127.0.0.1:8787` manually."
            )
        async with self._lock:
            if self._state == "running":
                return self.status()
            if self._proc is not None and self._proc.returncode is None:
                return self.status()

            self._stderr_tail = []
            self._url = None
            self._last_error = None
            self._url_seen = asyncio.Event()
            self._proc = await asyncio.create_subprocess_exec(
                "cloudflared",
                "tunnel",
                "--url",
                self._target_url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._state = "connecting"
            self._started_at = datetime.now(timezone.utc).isoformat()
            self._reader_task = asyncio.create_task(self._read_stderr())

            try:
                await asyncio.wait_for(self._url_seen.wait(), timeout=_START_TIMEOUT_SEC)
            except asyncio.TimeoutError:
                # Still connecting; the UI polls /share/status to resolve.
                return self.status()

            if self._url is not None:
                self._state = "running"
                return self.status()

            # Process exited without emitting a URL.
            self._state = "stopped"
            proc = self._proc
            self._proc = None
            tail = "".join(self._stderr_tail)[-_MAX_STDERR_TAIL:]
            self._last_error = tail or "cloudflared exited before creating a tunnel."
            if proc is not None and proc.returncode is None:
                proc.kill()
            raise TunnelStartError(self._last_error)

    async def stop(self) -> Dict:
        """Terminate the child and reset state. No-op when not running."""
        async with self._lock:
            if self._reader_task is not None:
                self._reader_task.cancel()
                self._reader_task = None
            proc = self._proc
            self._proc = None
            self._url = None
            self._state = "idle"
            if proc is not None and proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            return self.status()

    async def shutdown(self) -> None:
        """Lifespan hook -- best-effort cleanup on dashboard exit."""
        if self._proc is not None or self._reader_task is not None:
            await self.stop()

    # -- internals ----------------------------------------------------------

    async def _read_stderr(self) -> None:
        """Read stderr line-by-line; capture the URL and a tail for errors."""
        proc = self._proc
        if proc is None or proc.stderr is None:
            self._url_seen.set()
            return
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace")
                match = _URL_RE.search(text)
                if match and self._url is None:
                    self._url = match.group(0)
                self._stderr_tail.append(text)
                # Keep only the last N chars to bound memory.
                joined = "".join(self._stderr_tail)
                if len(joined) > _MAX_STDERR_TAIL:
                    self._stderr_tail = [joined[-_MAX_STDERR_TAIL:]]
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            pass
        finally:
            self._url_seen.set()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Users/bottl/FinancialDevelopment/.worktrees/feat-shareable-dashboard && "C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe" -m pytest dashboard/tests/test_tunnel.py -v`
Expected: PASS (all unit + route tests green).

- [ ] **Step 5: Commit**

```bash
cd /c/Users/bottl/FinancialDevelopment/.worktrees/feat-shareable-dashboard
git add dashboard/tunnel.py dashboard/tests/test_tunnel.py
git commit -m "feat: add dashboard quick-tunnel manager"
```

---

### Task 2: Routes + lifespan wiring in `dashboard/app.py`

**Files:**
- Modify: `dashboard/app.py` (add imports, `tunnel_manager` global, lifespan, 3 routes)
- Test: `dashboard/tests/test_tunnel.py` (route tests already added in Task 1)

**Interfaces:**
- Consumes: `TunnelManager`, `TunnelUnavailable`, `TunnelStartError` from `dashboard.tunnel` (Task 1).
- Produces: routes `GET /share/status`, `POST /share/start`, `POST /share/stop`; module global `tunnel_manager`; lifespan that calls `tunnel_manager.shutdown()` on exit.

- [ ] **Step 1: Write the failing route test**

The route tests are already written in Task 1, Step 1 (the block starting `# Route tests ...`). They will fail until the routes exist. Verify they fail now:

Run: `cd /c/Users/bottl/FinancialDevelopment/.worktrees/feat-shareable-dashboard && "C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe" -m pytest dashboard/tests/test_tunnel.py -k "share_" -v`
Expected: FAIL — routes `/share/*` return 404.

- [ ] **Step 2: Implement the routes + lifespan in app.py**

Add an import near the top of `dashboard/app.py`. Place it with the other `from dashboard.*` / project imports (e.g. after `from slowapi.errors import RateLimitExceeded`, around line 51):

```python
from dashboard.tunnel import TunnelManager, TunnelStartError, TunnelUnavailable
```

Add a module-level manager and a lifespan. Locate the line `app = FastAPI(title='FinancialDevelopment Dashboard')` (line 98) and replace it:

```python
tunnel_manager = TunnelManager()


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    yield
    # Best-effort cleanup so a tunnel never outlives the dashboard process.
    await tunnel_manager.shutdown()


app = FastAPI(title='FinancialDevelopment Dashboard', lifespan=_lifespan)
```

Add the three routes near the other `@app.get`/`@app.post` routes (e.g. right after the `/health` route, which ends at line ~2233):

```python
# --------------------------------------------------------------------------
# /share -- Cloudflare quick-tunnel control
# --------------------------------------------------------------------------

@app.get('/share/status')
async def share_status():
    """Current tunnel state: running / url / state / pid / last_error."""
    return tunnel_manager.status()


@app.post('/share/start')
async def share_start():
    """Start a quick tunnel. 409 if cloudflared missing; 500 on start error."""
    try:
        return await tunnel_manager.start()
    except TunnelUnavailable as e:
        return JSONResponse({'error': str(e)}, status_code=409)
    except TunnelStartError as e:
        return JSONResponse({'error': str(e)}, status_code=500)


@app.post('/share/stop')
async def share_stop():
    """Stop the running tunnel. Idempotent."""
    return await tunnel_manager.stop()
```

Note: `JSONResponse` and `contextlib` are already imported in `app.py`. Verify the `app = FastAPI(...)` line is the only place `app` is assigned (grep `app = ` in `app.py`).

- [ ] **Step 3: Run the route tests to verify they pass**

Run: `cd /c/Users/bottl/FinancialDevelopment/.worktrees/feat-shareable-dashboard && "C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe" -m pytest dashboard/tests/test_tunnel.py -k "share_" -v`
Expected: PASS.

- [ ] **Step 4: Run the full new test file**

Run: `cd /c/Users/bottl/FinancialDevelopment/.worktrees/feat-shareable-dashboard && "C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe" -m pytest dashboard/tests/test_tunnel.py -v`
Expected: PASS (all unit + route tests).

- [ ] **Step 5: Regression-check the app imports cleanly**

Run: `cd /c/Users/bottl/FinancialDevelopment/.worktrees/feat-shareable-dashboard && "C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe" -c "import dashboard.app; print('ok')"`
Expected: prints `ok` (confirms the lifespan/route wiring doesn't break import).

- [ ] **Step 6: Commit**

```bash
cd /c/Users/bottl/FinancialDevelopment/.worktrees/feat-shareable-dashboard
git add dashboard/app.py
git commit -m "feat: add share tunnel routes and lifespan to dashboard"
```

---

### Task 3: Share control UI in `dashboard/templates/base.html`

**Files:**
- Modify: `dashboard/templates/base.html`
- Test: manual (Task 4)

**Interfaces:**
- Consumes: `GET /share/status`, `POST /share/start`, `POST /share/stop` (Task 2).
- Produces: a visible Share control in the topbar on every page; a JS poll loop; a confirmation prompt on Start.

- [ ] **Step 1: Add the Share control markup + CSS + JS**

In `dashboard/templates/base.html`:

1. Add CSS inside the existing `<style>` block (after the `.banner-live .body` rule at line ~188):

```css
/* Share (quick-tunnel) control -- dashboard/templates/base.html */
.share { display: flex; align-items: center; gap: 10px; margin-left: auto; }
.share .label { color: var(--muted); font-size: 12px; }
.share .status { color: var(--ok); font-size: 12px; font-weight: 600; }
.share .status.idle { color: var(--muted); }
.share .status.error { color: var(--err); }
.share a.shareurl { font-family: var(--mono); font-size: 12px; word-break: break-all; max-width: 320px; }
```

2. Add the control markup inside the `<header class="topbar">` (after the `</nav>` at line 200, before `</header>`):

```html
  <div class="share" id="shareControl">
    <span class="label">Share:</span>
    <span class="status idle" id="shareStatus">Not sharing</span>
    <a class="shareurl" id="shareUrl" href="#" target="_blank" rel="noopener" style="display:none"></a>
    <button type="button" id="shareBtn" class="ghost">Start sharing</button>
  </div>
```

3. Add the JS block just before `</body>` (after `{% block scripts %}{% endblock %}` at line 205). It must be present on every page, so put it in `base.html` itself:

```html
<script>
(function () {
  var control = document.getElementById('shareControl');
  if (!control) return;
  var statusEl = document.getElementById('shareStatus');
  var urlEl = document.getElementById('shareUrl');
  var btn = document.getElementById('shareBtn');

  function render(s) {
    if (s.state === 'running') {
      statusEl.textContent = 'Sharing';
      statusEl.className = 'status';
      urlEl.textContent = s.url;
      urlEl.href = s.url;
      urlEl.style.display = '';
      btn.textContent = 'Stop sharing';
      btn.disabled = false;
    } else if (s.state === 'connecting') {
      statusEl.textContent = 'Connecting…';
      statusEl.className = 'status';
      urlEl.style.display = 'none';
      btn.textContent = 'Starting…';
      btn.disabled = true;
    } else {
      statusEl.textContent = s.last_error ? 'Error' : 'Not sharing';
      statusEl.className = 'status ' + (s.last_error ? 'error' : 'idle');
      urlEl.style.display = 'none';
      btn.textContent = 'Start sharing';
      btn.disabled = false;
      if (s.last_error) console.warn('share:', s.last_error);
    }
  }

  function poll() {
    fetch('/share/status')
      .then(function (r) { return r.json(); })
      .then(render)
      .catch(function (e) { console.error('share poll failed', e); });
  }

  btn.addEventListener('click', function () {
    if (btn.textContent === 'Start sharing') {
      var ok = window.confirm(
        'This exposes your dashboard publicly with NO password. ' +
        'Only share the link with people you trust. Continue?'
      );
      if (!ok) return;
      btn.disabled = true;
      btn.textContent = 'Starting…';
      fetch('/share/start', { method: 'POST' })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
        .then(function (res) {
          if (!res.ok) {
            statusEl.textContent = 'Error';
            statusEl.className = 'status error';
            btn.textContent = 'Start sharing';
            btn.disabled = false;
            console.error('share start failed:', res.body);
            return;
          }
          render(res.body);
        })
        .catch(function (e) { console.error('share start error', e); });
    } else if (btn.textContent === 'Stop sharing') {
      fetch('/share/stop', { method: 'POST' })
        .then(function (r) { return r.json(); })
        .then(render)
        .catch(function (e) { console.error('share stop error', e); });
    }
  });

  poll();
  setInterval(poll, 4000);
})();
</script>
```

- [ ] **Step 2: Verify base.html renders without Jinja errors**

Start the dashboard (from the worktree root, using the shared venv — the worktree has no venv, so run uvicorn directly):
```bash
cd /c/Users/bottl/FinancialDevelopment/.worktrees/feat-shareable-dashboard/dashboard
"C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8787
```
Then load `http://127.0.0.1:8787/` in a browser. Expected: the topbar shows `Share: Not sharing` + a `Start sharing` button; no template exception. (Note: only one dashboard may run on 8787 at a time — stop any existing instance first.)

- [ ] **Step 3: Commit**

```bash
cd /c/Users/bottl/FinancialDevelopment/.worktrees/feat-shareable-dashboard
git add dashboard/templates/base.html
git commit -m "feat: add share quick-tunnel control to dashboard topbar"
```

---

### Task 4: Update START_HERE.md + manual verification

**Files:**
- Modify: `START_HERE.md`
- Test: manual (real cloudflared)

**Interfaces:**
- Consumes: the finished feature (Tasks 1-3).
- Produces: updated docs; verified end-to-end behavior.

- [ ] **Step 1: Update the "Sharing the dashboard publicly" section**

Replace the current section in `START_HERE.md` (lines ~67-78) with:

```markdown
## Sharing the dashboard publicly

The dashboard has a built-in **Share** control in the top bar. Start the
dashboard, click **Start sharing**, and confirm — a public HTTPS link
(e.g. `https://<random-words>.trycloudflare.com`) appears that proxies to your
local dashboard for as long as your machine and the dashboard stay running.
Click **Stop sharing** (or close the dashboard) to take it down.

Under the hood it runs Cloudflare's free, account-less "quick tunnel"
(`cloudflared tunnel --url http://127.0.0.1:8787`). The URL changes each time
you start a new one, and `cloudflared` must be installed and on PATH.

**There is no password on the dashboard.** Anyone with the link can see all
swap data and trigger orchestrator runs (which call real, billed ThetaData
API requests). Only share the link with people you trust, and stop the tunnel
when you're done.
```

- [ ] **Step 2: Manual verification (real proof)**

1. Start the dashboard: `cd /c/Users/bottl/FinancialDevelopment/.worktrees/feat-shareable-dashboard/dashboard && "C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8787`, then open `http://127.0.0.1:8787`.
2. In the topbar click **Start sharing** → confirm the dialog.
3. Expected: status flips to "Connecting…" then "Sharing" with a live `https://…trycloudflare.com` link.
4. Open that link in an incognito window (or another device). Expected: it proxies to the dashboard.
5. Click **Stop sharing**. Expected: the link 404s / no longer loads.
6. Restart the dashboard (Ctrl+C then start again). Expected: status shows "Not sharing", and **no `cloudflared` process remains** (verify with `tasklist | grep -i cloudflared` in git-bash, or `tasklist | findstr cloudflared` in cmd).

- [ ] **Step 3: Run lint**

Run from the worktree root:
```bash
cd /c/Users/bottl/FinancialDevelopment/.worktrees/feat-shareable-dashboard
"C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe" -m ruff check dashboard/tunnel.py dashboard/app.py dashboard/tests/test_tunnel.py
"C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe" -m ruff format --check dashboard/tunnel.py dashboard/app.py dashboard/tests/test_tunnel.py
```
Expected: no errors. Auto-fix any format issues with `"C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe" -m ruff format dashboard/tunnel.py dashboard/app.py dashboard/tests/test_tunnel.py`.

- [ ] **Step 4: Run the full test suite for the touched area**

Run: `cd /c/Users/bottl/FinancialDevelopment/.worktrees/feat-shareable-dashboard && "C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe" -m pytest dashboard/tests/test_tunnel.py dashboard/tests/test_output_runs.py -v`
Expected: all PASS (new tests + an existing dashboard test, to confirm no regression in the shared venv/test setup).

- [ ] **Step 5: Commit**

```bash
cd /c/Users/bottl/FinancialDevelopment/.worktrees/feat-shareable-dashboard
git add START_HERE.md
git commit -m "docs: document dashboard share quick-tunnel control"
```

---

## Self-Review Notes

- **Spec coverage:** TunnelManager + is_available (Task 1), 3 routes + idempotency + 409/500 (Task 2), lifespan cleanup (Task 2), Share control in base.html + confirmation (Task 3), START_HERE.md update (Task 4), unit + route + manual tests (Tasks 1, 2, 4). All spec sections mapped.
- **No placeholders:** every code step carries complete, runnable content; every command includes expected output.
- **Type consistency:** `TunnelManager.status()` returns the dict shape `{running, url, state, started_at, pid, last_error}` everywhere; `state` values are `idle|connecting|running|stopped`; routes return exactly what the manager methods return.
- **Environment verified:** the shared venv at `C:\Users\bottl\FinancialDevelopment\.venv` has Python 3.12.10, `pytest` 9.1.1, `httpx` 0.28.1 (needed for TestClient), and `ruff` 0.16.0. `pytest-asyncio` is intentionally NOT required — tests use `asyncio.run(...)`. The worktree has no `.venv`; all commands use the primary-tree venv. `pyproject.toml`'s `testpaths` does not include `dashboard/tests/` (a pre-existing gap), so tests are run explicitly by path.
