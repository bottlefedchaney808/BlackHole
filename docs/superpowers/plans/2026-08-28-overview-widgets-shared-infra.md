# Overview Widgets — Shared Infrastructure + Widget 1 (Positions) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the shared `widget_cache` SQLite store, its `GET`/`POST` API on the dashboard,
and the first working widget (live Robinhood positions) end-to-end, so Overview shows real
data and the pattern is proven for widgets 2/3/4 (separate follow-up plans).

**Architecture:** A new `dashboard/widget_cache.py` module (`WidgetCache`, mirroring
`chart_app/bar_cache.py`'s connect-per-call SQLite style) backs one generic read route
(`GET /api/widgets/{widget_id}`) and one write route specific to positions
(`POST /api/widgets/positions`, called by a scheduled agent — not part of this plan). Overview's
template polls the read route client-side and renders a positions table, following the same
JS-poll-a-JSON-endpoint pattern `quant.html`/`chart_app`'s `static/index.html` already use.

**Tech Stack:** Python 3.12, FastAPI, sqlite3 (stdlib), Jinja2, vanilla JS (no framework/CDN,
per this repo's offline-first convention), pytest + `fastapi.testclient.TestClient`.

## Global Constraints

- No new third-party dependencies — `sqlite3`, `json`, `datetime` are all stdlib; FastAPI/pytest
  are already project dependencies.
- Follow `dashboard/app.py`'s existing conventions exactly: hand-rolled dict validation via
  `_parse_body(request)` (no pydantic models — this file doesn't use them, unlike `chart_app`),
  module-level path constants read fresh per-call so tests can `monkeypatch.setattr` them (the
  `DB_PATH`/`_db()` pattern), `HTTPException` for error responses.
- No auth on any new route — this dashboard is deliberately localhost-only with no auth
  anywhere (`CLAUDE.md`), unchanged by this plan.
- Vanilla JS only in templates: no framework, no CDN script tags — matches every existing
  dashboard template (`index.html`, `quant.html`, `chart_app/static/index.html`).
- Every new/changed route or module needs a test in the same task that introduces it — no task
  ships code without a passing test for it.

---

### Task 1: `WidgetCache` module

**Files:**
- Create: `dashboard/widget_cache.py`
- Test: `dashboard/tests/test_widget_cache.py`

**Interfaces:**
- Produces: `class WidgetCache` with `__init__(self, path: str | pathlib.Path) -> None`,
  `set(self, widget_id: str, payload: Any, status: str = "ok") -> None`,
  `get(self, widget_id: str) -> dict[str, Any] | None` returning
  `{"payload": <deserialized JSON>, "status": str, "computed_at": str (ISO 8601 UTC)}` or
  `None` if `widget_id` was never written.

- [ ] **Step 1: Write the failing tests**

Create `dashboard/tests/test_widget_cache.py`:

```python
from dashboard.widget_cache import WidgetCache


def test_get_returns_none_when_missing(tmp_path):
    cache = WidgetCache(tmp_path / "widgets.db")
    assert cache.get("positions") is None


def test_set_then_get_round_trips(tmp_path):
    cache = WidgetCache(tmp_path / "widgets.db")
    cache.set("positions", {"positions": [{"ticker": "NVDA"}]}, status="ok")
    row = cache.get("positions")
    assert row is not None
    assert row["payload"] == {"positions": [{"ticker": "NVDA"}]}
    assert row["status"] == "ok"
    assert isinstance(row["computed_at"], str) and row["computed_at"]


def test_set_overwrites_existing(tmp_path):
    cache = WidgetCache(tmp_path / "widgets.db")
    cache.set("signals", {"n": 1}, status="ok")
    first_computed_at = cache.get("signals")["computed_at"]
    cache.set("signals", {"n": 2}, status="error")
    row = cache.get("signals")
    assert row["payload"] == {"n": 2}
    assert row["status"] == "error"
    assert row["computed_at"] >= first_computed_at


def test_widget_ids_are_independent(tmp_path):
    cache = WidgetCache(tmp_path / "widgets.db")
    cache.set("positions", {"a": 1})
    cache.set("signals", {"b": 2})
    assert cache.get("positions")["payload"] == {"a": 1}
    assert cache.get("signals")["payload"] == {"b": 2}


def test_creates_parent_directory(tmp_path):
    nested = tmp_path / "nested" / "dir" / "widgets.db"
    cache = WidgetCache(nested)
    cache.set("positions", {"a": 1})
    assert nested.exists()
    assert cache.get("positions")["payload"] == {"a": 1}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest dashboard/tests/test_widget_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dashboard.widget_cache'`

- [ ] **Step 3: Write the implementation**

Create `dashboard/widget_cache.py`:

```python
"""SQLite-backed cache for Overview widget payloads.

One row per widget_id: the JSON payload a background job (or, for
"positions", an external POST from a scheduled agent) last computed, its
status, and when it was written. Mirrors chart_app/bar_cache.py's
BarCache -- connect per call, no held-open connection, so a background
asyncio task and a request handler can both write without coordinating a
shared connection object.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class WidgetCache:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS widget_cache (
                    widget_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    computed_at TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def set(self, widget_id: str, payload: Any, status: str = "ok") -> None:
        computed_at = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO widget_cache (widget_id, payload, status, computed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (widget_id) DO UPDATE SET
                    payload = excluded.payload,
                    status = excluded.status,
                    computed_at = excluded.computed_at
                """,
                (widget_id, json.dumps(payload), status, computed_at),
            )

    def get(self, widget_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload, status, computed_at FROM widget_cache WHERE widget_id = ?",
                (widget_id,),
            ).fetchone()
        if row is None:
            return None
        payload_raw, status, computed_at = row
        return {
            "payload": json.loads(payload_raw),
            "status": status,
            "computed_at": computed_at,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest dashboard/tests/test_widget_cache.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add dashboard/widget_cache.py dashboard/tests/test_widget_cache.py
git commit -m "feat(dashboard): add WidgetCache for Overview widget payloads"
```

---

### Task 2: `GET /api/widgets/{widget_id}` and `POST /api/widgets/positions` routes

**Files:**
- Modify: `dashboard/app.py` (add an import, two module-level constants, one helper, two routes)
- Test: `dashboard/tests/test_widgets_api.py`

**Interfaces:**
- Consumes: `dashboard.widget_cache.WidgetCache` (Task 1) — `WidgetCache(path).get(widget_id)`
  / `.set(widget_id, payload, status)`.
- Consumes: `dashboard.app._parse_body(request) -> dict[str, Any]` (existing helper, already in
  `dashboard/app.py`, used by every other JSON-accepting POST route in this file).
- Produces: module-level `WIDGET_CACHE_PATH: str` (monkeypatchable in tests, same convention as
  `DB_PATH`) and `_widget_cache() -> WidgetCache` (fresh instance per call, same convention as
  `_db()`), used by Task 3's frontend and by the follow-up widgets 2/3/4 plans' background jobs.

- [ ] **Step 1: Write the failing tests**

Create `dashboard/tests/test_widgets_api.py`:

```python
import pytest
from fastapi.testclient import TestClient

import dashboard.app as dashboard_app
from dashboard.app import app

pytestmark = pytest.mark.unit
client = TestClient(app)


def test_get_widget_404_before_any_write(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_app, "WIDGET_CACHE_PATH", str(tmp_path / "widgets.db"))
    r = client.get("/api/widgets/positions")
    assert r.status_code == 404


def test_post_positions_then_get_round_trips(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_app, "WIDGET_CACHE_PATH", str(tmp_path / "widgets.db"))
    body = {
        "positions": [
            {
                "account": "A",
                "ticker": "NVDA",
                "instrument_type": "equity",
                "qty": 0.028,
                "avg_price": 211.94,
                "current_price": 215.0,
                "market_value": 6.02,
                "unrealized_pl": 0.09,
            }
        ],
        "accounts": [{"account": "A", "total_value": 53.88, "cash": 10.61}],
    }
    r = client.post("/api/widgets/positions", json=body)
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    r = client.get("/api/widgets/positions")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["payload"]["positions"][0]["ticker"] == "NVDA"
    assert data["payload"]["accounts"][0]["account"] == "A"
    assert "computed_at" in data


def test_post_positions_rejects_non_list_positions(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_app, "WIDGET_CACHE_PATH", str(tmp_path / "widgets.db"))
    r = client.post("/api/widgets/positions", json={"positions": "not-a-list"})
    assert r.status_code == 400


def test_post_positions_rejects_non_list_accounts(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_app, "WIDGET_CACHE_PATH", str(tmp_path / "widgets.db"))
    r = client.post(
        "/api/widgets/positions", json={"positions": [], "accounts": "nope"}
    )
    assert r.status_code == 400


def test_post_positions_defaults_missing_accounts_to_empty_list(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_app, "WIDGET_CACHE_PATH", str(tmp_path / "widgets.db"))
    r = client.post("/api/widgets/positions", json={"positions": []})
    assert r.status_code == 200
    data = client.get("/api/widgets/positions").json()
    assert data["payload"]["accounts"] == []


def test_get_widget_unknown_id_404(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_app, "WIDGET_CACHE_PATH", str(tmp_path / "widgets.db"))
    r = client.get("/api/widgets/does-not-exist")
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest dashboard/tests/test_widgets_api.py -v`
Expected: FAIL overall. `test_get_widget_404_before_any_write` happens to pass already (FastAPI
returns its own 404 for any unregistered path), but every test that POSTs or expects a 200 GET
FAILs, since neither route exists yet — e.g. `test_post_positions_then_get_round_trips` fails
on `assert r.status_code == 200` getting 404 instead.

- [ ] **Step 3: Add the import, constants, helper, and routes**

In `dashboard/app.py`, add the import next to the other `dashboard.*` imports (near
`from dashboard.auth import get_client_ip`):

```python
from dashboard.widget_cache import WidgetCache
```

Add the path constant next to the other path constants (near `CHART_APP_URL = "http://127.0.0.1:8791"`,
around line 107):

```python
WIDGET_CACHE_PATH = os.path.join(ROOT, "artifacts", "widget_cache.db")
```

Add the helper next to `_db()` (around line 158-165):

```python
def _widget_cache() -> WidgetCache:
    """Fresh WidgetCache per call, reading WIDGET_CACHE_PATH at call time --
    same convention as _db() reading DB_PATH, so tests can monkeypatch the
    module-level constant instead of a captured value."""
    return WidgetCache(WIDGET_CACHE_PATH)
```

Add the two routes near the alerts routes (after the `ack_alert` function, before the `/quant`
route — `ack_alert`/`/quant` are adjacent; use those as the anchor rather than a line number,
since earlier edits in this file have already shifted line numbers once):

```python
@app.get("/api/widgets/{widget_id}")
def get_widget(widget_id: str):
    row = _widget_cache().get(widget_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"widget {widget_id!r} not computed yet"
        )
    return row


@app.post("/api/widgets/positions")
async def post_widget_positions(request: Request):
    body = await _parse_body(request)
    positions = body.get("positions")
    accounts = body.get("accounts")
    if not isinstance(positions, list):
        raise HTTPException(status_code=400, detail="positions must be a list")
    if accounts is not None and not isinstance(accounts, list):
        raise HTTPException(status_code=400, detail="accounts must be a list")
    payload = {"positions": positions, "accounts": accounts or []}
    _widget_cache().set("positions", payload, status="ok")
    return {"ok": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest dashboard/tests/test_widgets_api.py -v`
Expected: `6 passed`

- [ ] **Step 5: Run the full dashboard suite to check for regressions**

Run: `.venv\Scripts\python.exe -m pytest dashboard/tests -q`
Expected: all tests pass (verified baseline before this task: 220 passed — this task adds
5 (Task 1) + 6 (this task) = 11 more, so expect `231 passed`).

- [ ] **Step 6: Commit**

```bash
git add dashboard/app.py dashboard/tests/test_widgets_api.py
git commit -m "feat(dashboard): add GET/POST /api/widgets endpoints backed by WidgetCache"
```

---

### Task 3: Widget 1 (Positions) card on Overview

**Files:**
- Modify: `dashboard/templates/index.html`
- Test: `dashboard/tests/test_overview_widgets.py`

**Interfaces:**
- Consumes: `GET /api/widgets/positions` (Task 2) — response shape
  `{"payload": {"positions": [...], "accounts": [...]}, "status": "ok", "computed_at": "<ISO8601>"}`
  or HTTP 404 if nothing has been pushed yet. Each position object:
  `{"account": str, "ticker": str, "instrument_type": str, "qty": number,
  "avg_price": number, "current_price": number|null, "market_value": number|null,
  "unrealized_pl": number|null}`. Each account object: `{"account": str,
  "total_value": number|null, "cash": number|null}`.
- Consumes: existing CSS classes from `dashboard/templates/base.html` — `.widget-grid`, `.panel`,
  `.widget`, `.body`, `.tablewrap`, `table`/`.num` cell class, `.pill.ok`/`.pill.error`, `.empty`,
  `.small`, `.muted`.

- [ ] **Step 1: Write the failing test**

Create `dashboard/tests/test_overview_widgets.py`:

```python
import pytest
from fastapi.testclient import TestClient

from dashboard.app import app

pytestmark = pytest.mark.unit
client = TestClient(app)


def test_home_page_has_positions_widget_card():
    r = client.get("/")
    assert r.status_code == 200
    assert 'id="positionsWidget"' in r.text
    assert "/api/widgets/positions" in r.text


def test_home_page_positions_widget_starts_empty():
    r = client.get("/")
    assert r.status_code == 200
    assert "No positions synced yet" in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest dashboard/tests/test_overview_widgets.py -v`
Expected: FAIL — `id="positionsWidget"` not found in the response body.

- [ ] **Step 3: Add the widget card and polling script to `index.html`**

In `dashboard/templates/index.html`, insert a new card as the *first* child of the existing
`.widget-grid` div (immediately after `<div class="widget-grid">`, before the "Orchestrator run
history" panel):

```html
  <div class="panel widget" id="positionsWidget">
    <header>
      <h2>Positions</h2>
      <span class="note">Robinhood accounts A &amp; B &middot; pushed by a scheduled agent every 15-30 min</span>
    </header>
    <div class="body">
      <div id="positionsStalePill" style="margin-bottom:10px;"></div>
      <div id="positionsContent"><div class="empty">No positions synced yet.</div></div>
    </div>
  </div>

```

Add a `{% block scripts %}` at the end of the file (after the closing `</div>` of `.widget-grid`
and the `{% endblock %}` for `content`):

```html
{% block scripts %}
<script>
// Positions widget (widget 1) -- polls the cache-only /api/widgets/positions
// endpoint; a scheduled agent job is the only thing that ever writes to it
// (see POST /api/widgets/positions), this page only reads.
var POSITIONS_STALE_MS = 45 * 60 * 1000;

function escPositions(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
    return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c];
  });
}

function fmtPositionsMoney(v) {
  if (v === null || v === undefined) return '--';
  var n = Number(v);
  if (Number.isNaN(n)) return '--';
  return (n < 0 ? '-$' : '$') + Math.abs(n).toFixed(2);
}

function renderPositions(data) {
  var content = document.getElementById('positionsContent');
  var pillHost = document.getElementById('positionsStalePill');
  var positions = (data.payload && data.payload.positions) || [];
  var accounts = (data.payload && data.payload.accounts) || [];

  var ageMs = Date.now() - new Date(data.computed_at).getTime();
  pillHost.innerHTML = ageMs > POSITIONS_STALE_MS
    ? '<span class="pill error">stale since ' + escPositions(data.computed_at) + '</span>'
    : '<span class="pill ok">synced ' + escPositions(data.computed_at) + '</span>';

  if (!positions.length) {
    content.innerHTML = '<div class="empty">No open positions.</div>';
    return;
  }

  var byAccount = {};
  positions.forEach(function (p) {
    var acct = p.account || '?';
    (byAccount[acct] = byAccount[acct] || []).push(p);
  });

  var html = '';
  Object.keys(byAccount).sort().forEach(function (acct) {
    var summary = accounts.filter(function (a) { return a.account === acct; })[0];
    html += '<h3 class="small muted" style="margin:10px 0 4px;">Account ' + escPositions(acct) +
      (summary && summary.total_value != null
        ? ' &middot; ' + fmtPositionsMoney(summary.total_value) + ' total'
        : '') +
      '</h3>';
    html += '<div class="tablewrap"><table><thead><tr>' +
      '<th>ticker</th><th>type</th><th class="num">qty</th><th class="num">avg price</th>' +
      '<th class="num">current</th><th class="num">value</th><th class="num">unrealized P&amp;L</th>' +
      '</tr></thead><tbody>';
    byAccount[acct].forEach(function (p) {
      var pl = p.unrealized_pl;
      var plCls = (pl || 0) < 0 ? 'error' : 'ok';
      html += '<tr><td><strong>' + escPositions(p.ticker) + '</strong></td>' +
        '<td class="small muted">' + escPositions(p.instrument_type) + '</td>' +
        '<td class="num">' + escPositions(p.qty) + '</td>' +
        '<td class="num">' + fmtPositionsMoney(p.avg_price) + '</td>' +
        '<td class="num">' + fmtPositionsMoney(p.current_price) + '</td>' +
        '<td class="num">' + fmtPositionsMoney(p.market_value) + '</td>' +
        '<td class="num"><span class="pill ' + plCls + '">' + fmtPositionsMoney(pl) + '</span></td>' +
        '</tr>';
    });
    html += '</tbody></table></div>';
  });
  content.innerHTML = html;
}

function pollPositions() {
  fetch('/api/widgets/positions')
    .then(function (r) { return r.status === 404 ? null : r.json(); })
    .then(function (data) { if (data) renderPositions(data); })
    .catch(function (e) { console.error('positions widget poll failed', e); });
}

pollPositions();
setInterval(pollPositions, 45000);
</script>
{% endblock %}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest dashboard/tests/test_overview_widgets.py -v`
Expected: `2 passed`

- [ ] **Step 5: Run the full dashboard suite to check for regressions**

Run: `.venv\Scripts\python.exe -m pytest dashboard/tests -q`
Expected: all tests pass (baseline after Task 2: 231 passed; this task adds 2 more, so expect
`233 passed`).

- [ ] **Step 6: Manually verify in a running dashboard**

Restart the dashboard process (kill whatever's bound to `:8787`, relaunch
`.venv\Scripts\python.exe -m uvicorn dashboard.app:app --host 127.0.0.1 --port 8787`), then:

```bash
.venv/Scripts/python.exe -c "
import urllib.request, json
req = urllib.request.Request('http://127.0.0.1:8787/api/widgets/positions', method='POST',
    data=json.dumps({'positions':[{'account':'A','ticker':'NVDA','instrument_type':'equity',
    'qty':0.028,'avg_price':211.94,'current_price':215.0,'market_value':6.02,'unrealized_pl':0.09}],
    'accounts':[{'account':'A','total_value':53.88,'cash':10.61}]}).encode(),
    headers={'Content-Type':'application/json'})
print(urllib.request.urlopen(req, timeout=5).read())
r = urllib.request.urlopen('http://127.0.0.1:8787/', timeout=5)
body = r.read().decode()
print('positionsWidget' in body, 'NVDA' not in body)  # NVDA renders client-side via JS, not server-rendered
"
```

Expected: first `print` shows `{"ok": true}`; second shows `True True` (the card markup is
present; NVDA itself only appears after the browser's `fetch` runs, since this widget renders
client-side — confirm that visually by opening `http://127.0.0.1:8787/` in a browser and
checking the Positions card shows the NVDA row with a green "synced ..." pill).

- [ ] **Step 7: Commit**

```bash
git add dashboard/templates/index.html dashboard/tests/test_overview_widgets.py
git commit -m "feat(dashboard): render live Robinhood positions on Overview (widget 1)"
```

---

## Self-Review Notes

- **Spec coverage:** This plan implements the spec's "Shared cache + read endpoint" and
  "Widget 1 — Positions (agent-pushed)" architecture sections in full, including the staleness
  pill behavior from "Error handling." It does **not** implement widgets 2/3/4 or the scheduled
  Robinhood-push cron agent itself — per the spec's own scope note ("out of scope... Chart-tab
  side-panel reuse... reuse there later is additive"), and per this skill's Scope Check
  guidance, those are independent enough (each needs its own nontrivial backend job: a Vol_Suite
  signal wrapper, Options/hedge/VaR wiring, matplotlib re-theming) to warrant their own plans
  once this shared foundation lands. Recommend three follow-up plans: "Widget 2 — signal state,"
  "Widget 3 — per-position analysis," "Widget 4 — surface showcase," each following this same
  `widget_cache`/`/api/widgets/{id}` pattern plus its own background `asyncio` job (mirroring
  `chart_app/server.py`'s `background_refresh_seconds`-gated startup/shutdown handlers so tests
  never trigger real ThetaData calls). The scheduled Robinhood-push cron itself is a `schedule`
  skill artifact, not application code — set up separately, calling the `POST
  /api/widgets/positions` route this plan builds.
- **Placeholder scan:** no TBD/TODO; every step has complete, runnable code.
- **Type consistency:** `WidgetCache.get`/`.set` signatures match between Task 1's
  implementation and Task 2/3's usage (`_widget_cache().get(widget_id)` /
  `.set("positions", payload, status="ok")`). The positions payload shape
  (`{"positions": [...], "accounts": [...]}`) is identical across Task 2's route, its tests, and
  Task 3's frontend JS.
