# Shareable Dashboard (Tunnel) — Design

Date: 2026-08-15
Status: Approved (Jason, in-session brainstorm)
Branch: `feat/shareable-dashboard`

## Purpose

Turn the dashboard's already-documented, account-less Cloudflare "quick tunnel"
(`cloudflared tunnel --url http://127.0.0.1:8787`) into a first-class, in-app
feature: a **Share control on the dashboard UI** that starts/stops the tunnel and
shows the live public URL. No auth is added by design — the tunnel remains a plain
no-password quick tunnel shared only with trusted people (Jason's explicit choice).

## Current state (verified)

- `dashboard/app.py` (FastAPI, `:8787`, localhost-only) is launched via
  `python -m uvicorn app:app --host 127.0.0.1 --port 8787` (from `dashboard.bat`);
  the file has **no** `uvicorn.run`, **no** lifespan/startup/shutdown hooks today.
- `dashboard/auth.py` notes the API-key layer was deliberately removed and warns
  not to expose beyond localhost without adding auth back.
- `START_HERE.md` §"Sharing the dashboard publicly" documents the manual command.
- `cloudflared` is **installed and on PATH** (v2026.7.3, verified working).
- The dashboard already uses `asyncio.create_task` (line 1665) — an async-precedent.

## Scope

- New in-app tunnel manager + 3 routes + a Share control in `base.html`
  (renders on every page).
- Confirmation prompt on Start.
- Automated unit tests + route tests + manual verification.
- Out of scope: any auth layer, persistent/named tunnels, dashboard restart survival.

## Architecture

The dashboard stays a single FastAPI process. A new `TunnelManager` lives inside
it and owns the `cloudflared` child process.

### New module: `dashboard/tunnel.py`

Self-contained `TunnelManager`:

- `is_available()` -> bool — whether `cloudflared` is on PATH (checked via
  `shutil.which`), so a missing binary fails with a clear message, not a traceback.
- `async start()` -> status — spawn
  `asyncio.create_subprocess_exec("cloudflared", "tunnel", "--url",
  "http://127.0.0.1:8787", stdout=PIPE, stderr=PIPE)`; tail stderr in a background
  `asyncio` task; regex-extract the `https://[a-z-]+\.trycloudflare\.com` token from
  the line `Your quick Tunnel has been created! ... https://<words>.trycloudflare.com`;
  store URL + PID in memory. Until the URL line arrives, state is `"connecting"`.
- `async stop()` -> status — terminate the child (`terminate()`, brief wait,
  `kill()` if needed), clear state. No-op when already stopped.
- `status()` -> dict — `{running: bool, url: str|None, state: "idle"|"connecting"|"running"|"stopped",
  started_at, pid}`. If we hold a PID but the process has exited (`poll() is not None`),
  report `running:false, state:"stopped"` and clear stale state.
- Module-level `asyncio.Lock` guards start/stop so concurrent/double requests cannot
  spawn two `cloudflared` processes.
- State is in-memory only (like `_RUNS`). Dashboard restart kills the child on
  shutdown and the UI starts fresh at "Not sharing".

### Routes in `app.py`

| Route | Method | Behavior |
|---|---|---|
| `/share/status` | GET | JSON from `TunnelManager.status()` |
| `/share/start` | POST | `start()`, returns status. `409` JSON if `cloudflared` unavailable. Idempotent (`200` + existing URL if already running). `500` with stderr tail if the process exits without emitting a URL. |
| `/share/stop` | POST | `stop()`, returns `{running:false}`. Idempotent. |

Routes are `async def`. The subprocess read-loop is an `asyncio` task — no threads,
no blocking `subprocess.Popen`.

### Lifecycle wiring

Wrap the manager in a FastAPI lifespan (`@asynccontextmanager`) so that on dashboard
shutdown the tunnel child is terminated — no orphaned `cloudflared` keeping a public
URL alive past the dashboard.

### UI: Share control in `base.html`

A small block in the shared layout (so it appears on every page). Shows current
status and a toggle:

- Idle: "Not sharing" + `Start sharing` button.
- Connecting: "Connecting…" (button disabled).
- Running: live `https://…trycloudflare.com` link (copyable) + `Stop sharing` button.
- On Start, a **confirmation prompt** before spawning:
  "This exposes your dashboard publicly with no password. Only share the link with
  people you trust. Continue?"
- Polls `GET /share/status` to stay in sync (and to detect a tunnel that died).
- Keeps the existing "no password — only share with people you trust" warning visible.

## Error handling

| Situation | Behavior |
|---|---|
| `cloudflared` not on PATH | Start -> `409` JSON, clear message + pointer to `START_HERE.md`. |
| Already sharing | Start -> `200` + existing URL (idempotent, no second tunnel). |
| Already stopped | Stop -> `200` + `{running:false}` (idempotent). |
| URL not yet parsed | Status -> `{running:true, url:null, state:"connecting"}`; UI shows "Connecting…". |
| Process dies on its own | Next status poll detects exit, reports `state:"stopped"`, clears URL. |
| Exits with error before URL | Start -> `500` with captured stderr tail. |
| Tunnel outlives dashboard | Lifespan shutdown terminates child. |
| Duplicate/rapid start | `asyncio.Lock` serializes; second start returns running state. |
| Regex no-match on line | Keep reading; if process exits with no URL, error with stderr tail. |

**Security stance:** unchanged — plain quick tunnel, no auth (Jason's choice). The
UI control is only reachable by someone already on the local dashboard, so it does
not itself widen exposure; the exposure is the tunnel link, identical to the
documented one-liner. The UI retains the no-password trust warning.

## Testing

### Unit tests — `dashboard/tests/test_tunnel.py`

Fake subprocess / mocked `cloudflared`; no network dependency in CI:

- `is_available()` true when a stub binary is on PATH, false otherwise.
- `start()` parses URL from a canned stderr line.
- `start()` idempotent when already running.
- `stop()` terminates + clears state; no-op when stopped.
- Process-died detection -> status reports `stopped`.
- No-URL-before-exit -> error with stderr tail.
- Lock serializes concurrent start/stop.

### Route tests

FastAPI `TestClient` with a dependency-injected fake `TunnelManager`:
- Start -> `409` when unavailable; `200` when started.
- Stop idempotent.
- Status response shape.

### Manual verification (real proof)

1. Start the dashboard.
2. Click Share -> confirm -> UI shows a live `https://…trycloudflare.com` link.
3. Load it in an incognito / other-device browser to confirm it proxies.
4. Stop -> link 404s.
5. Dashboard restart -> no orphaned `cloudflared` in Task Manager.

### Lint

`ruff check` + `ruff format` on new/changed files.

## Files touched (planned)

- `dashboard/tunnel.py` (new) — `TunnelManager`.
- `dashboard/app.py` — 3 routes, lifespan wiring, manager instantiation.
- `dashboard/templates/base.html` — Share control block (+ small JS).
- `dashboard/tests/test_tunnel.py` (new) — unit + route tests.
- `START_HERE.md` — update the "Sharing the dashboard publicly" section to mention
  the in-app Share control as the primary path (keep the manual command as fallback).
