# Swaps Dashboard Split + Chart Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `dashboard.bat` boot to a usable page instantly by moving every swap-DB
query off the request path `home()` runs on every load. Swap browsing moves to a new
sibling app (`swaps_dashboard/`, port 8788); the Overview/Tools pages get a fast,
file-backed summary card instead; the old "Swap trades" nav slot becomes a **Chart**
tab embedding the existing native chart app (port 8791).

**Architecture:** Three independent local FastAPI processes, same pattern
(`uvicorn <pkg>.app:app --host 127.0.0.1 --port <N>`), each launched by its own
matched `.bat`/`.sh`:
- `dashboard/app.py` (8787) — orchestrator control panel, Overview, Tools, Quant
  Console, Suite output, and the new Chart tab. Never touches `swaps.db`.
- `swaps_dashboard/app.py` (8788, new) — everything that reads `swap_trades`: browse/
  search UI, JSON APIs, cross-source analytics. Runs a background task that writes a
  small JSON snapshot every 5 minutes so the main dashboard can show real numbers
  without a live query.
- `chart_app/server.py` (8791, existing, unmodified) — the native chart. `dashboard.bat`
  now also starts its uvicorn server headlessly so the Chart tab has something to
  embed.

**Tech Stack:** Python 3.12 repo `.venv` (shared), FastAPI + uvicorn + Jinja2
(already in repo, `dashboard/app.py`'s stack, unchanged), `pytest` + `fastapi.testclient.TestClient`.

## Global Constraints

- Spec of record: `docs/superpowers/specs/2026-08-27-swaps-dashboard-split-design.md`.
  Every task below implements one of its sections — do not deviate from documented
  route paths, ports, or file locations without re-checking that doc.
- Ports are fixed: main dashboard `8787` (unchanged), swaps dashboard `8788` (new),
  chart app `8791` (existing, unchanged).
- No auth on any of the three apps — unchanged from today, out of scope to add.
- Windows git-bash. Use the clean-python invocation for every pytest/python run in
  this repo (per `CLAUDE.md`'s "Hermes-venv leaks into the project venv" fragile
  surface): `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe`.
- `swaps_dashboard/` has no `__init__.py` (matches `dashboard/`'s existing
  implicit-namespace-package pattern; `pythonpath = ["."]` in `pyproject.toml`
  handles resolution). `swaps_dashboard/tests/__init__.py` **does** exist, empty,
  matching `dashboard/tests/__init__.py`.
- `swaps_dashboard/tests/` is not in `pyproject.toml`'s `testpaths` — neither is
  `dashboard/tests/` today. Run these tests by explicit path
  (`pytest swaps_dashboard/tests/ -q`), matching how `dashboard/tests/` already
  works. Do not add either directory to `testpaths` — out of scope, pre-existing
  repo convention.
- Stage only the files each task lists. TDD each task. Commit with the stated
  message.

## File map

| Path | Role |
|---|---|
| `swaps_dashboard/app.py` | New FastAPI app: swap routes + snapshot writer |
| `swaps_dashboard/templates/base.html` | Minimal shell, single "Swap trades" nav tab |
| `swaps_dashboard/templates/swaps.html` | Moved verbatim from `dashboard/templates/swaps.html` |
| `swaps_dashboard/tests/__init__.py` | Empty, matches `dashboard/tests/` convention |
| `swaps_dashboard/tests/test_swaps_options_cache.py` | Moved from `dashboard/tests/`, retargeted |
| `swaps_dashboard/tests/test_swaps_routes.py` | New smoke tests for the moved routes |
| `swaps_dashboard/tests/test_snapshot.py` | New tests for the snapshot writer |
| `swaps_dashboard.bat` / `.sh` | Launcher, mirrors `dashboard.bat`/`.sh` |
| `dashboard/app.py` | Loses swap routes/imports; gains `_swaps_snapshot()`, `/chart` |
| `dashboard/templates/_swap_card.html` | New partial, included by Overview + Tools |
| `dashboard/templates/chart.html` | New template: iframes `chart_app` |
| `dashboard/templates/index.html` | Old swap widgets replaced by `_swap_card.html` include |
| `dashboard/templates/tools_index.html` | Gains `_swap_card.html` include |
| `dashboard/templates/base.html` | Nav: "Swap trades" → "Chart" |
| `dashboard.bat` / `.sh` | Also starts `chart_app`'s uvicorn headlessly |
| `.gitignore` | Adds `swaps_dashboard/cache/` |

---

### Task 1: Scaffold `swaps_dashboard` — health route only

**Files:**
- Create: `swaps_dashboard/app.py`
- Create: `swaps_dashboard/tests/__init__.py` (empty)
- Create: `swaps_dashboard/tests/test_swaps_routes.py`

**Interfaces:**
- Produces: `swaps_dashboard.app.app` (FastAPI instance), `swaps_dashboard.app.DB_PATH`
  (str, same value as `orchestrator.DB_PATH`), `GET /health` → `{"ok": true, "db_path": str, "db_exists": bool}`.

- [ ] **Step 1: Write the failing test**

```python
# swaps_dashboard/tests/test_swaps_routes.py
from fastapi.testclient import TestClient

from swaps_dashboard.app import app

client = TestClient(app)


def test_health_route():
    r = client.get('/health')
    assert r.status_code == 200
    body = r.json()
    assert body['ok'] is True
    assert 'db_path' in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest swaps_dashboard/tests/test_swaps_routes.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'swaps_dashboard'`)

- [ ] **Step 3: Write minimal implementation**

```python
# swaps_dashboard/app.py
"""swaps_dashboard/app.py -- standalone swap-data browser, split out of
dashboard/app.py so the main dashboard's boot path never touches swaps.db.

See docs/superpowers/specs/2026-08-27-swaps-dashboard-split-design.md.

Routes here are moved verbatim from dashboard/app.py: /swaps, /trades,
/instruments/{upi}, /analytics/cross-source-notional, /analytics/timeseries.
A background task additionally writes cache/overview_snapshot.json every 5
minutes so dashboard/app.py's Overview/Tools cards can show real numbers
without ever querying swaps.db themselves.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

# --------------------------------------------------------------------------
# paths / repo imports
# --------------------------------------------------------------------------

SWAPS_DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SWAPS_DASHBOARD_DIR)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from shared.config import load_env_once  # noqa: E402

load_env_once()

import orchestrator  # noqa: E402  (path is set immediately above)

DB_PATH = orchestrator.DB_PATH

TEMPLATES = Jinja2Templates(directory=os.path.join(SWAPS_DASHBOARD_DIR, 'templates'))

app = FastAPI(title='Swaps Dashboard')


@app.get('/health')
def health() -> Dict[str, Any]:
    return {
        'ok': True,
        'db_path': DB_PATH,
        'db_exists': os.path.exists(DB_PATH),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest swaps_dashboard/tests/test_swaps_routes.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add swaps_dashboard/app.py swaps_dashboard/tests/__init__.py swaps_dashboard/tests/test_swaps_routes.py
git commit -m "feat(swaps-dashboard): scaffold new app with a health route"
```

---

### Task 2: Move `/swaps` (browse/search UI) into `swaps_dashboard`

**Files:**
- Modify: `swaps_dashboard/app.py`
- Create: `swaps_dashboard/templates/base.html`
- Create: `swaps_dashboard/templates/swaps.html` (moved from `dashboard/templates/swaps.html`)
- Modify: `swaps_dashboard/tests/test_swaps_routes.py`
- Delete: `dashboard/templates/swaps.html`
- Modify: `dashboard/app.py:675-755` (delete the `/swaps` route)
- Modify: `dashboard/app.py:154-189` (delete `_get_swaps_filter_options` + its cache globals)
- Modify: `dashboard/app.py:128-134` (delete `_db()` — nothing else in `dashboard/app.py`
  uses it after this task; if a later step in this task still needs it before Task 3
  removes the rest, leave it until Task 3 confirms no callers remain)
- Modify: `dashboard/tests/test_swaps_options_cache.py` → delete (superseded by the
  moved copy at `swaps_dashboard/tests/test_swaps_options_cache.py`)

**Interfaces:**
- Consumes (in `swaps_dashboard/app.py`): `swaps_query.SwapsQuery` (`SEARCH_COUNT_CAP`
  class attr, `SEARCH_SORT_COLUMNS` class attr, `.search_trades(...)`,
  `.get_database_stats()` — the last one lands in Task 3).
- Produces: `swaps_dashboard.app._db() -> Optional[sqlite3.Connection]`,
  `swaps_dashboard.app._get_swaps_filter_options(conn, db_path) -> Tuple[List[str], List[str]]`,
  `swaps_dashboard.app._swaps_options_cache: Dict[str, Tuple[float, List[str], List[str]]]`
  (module-level, used directly by the moved cache test).

- [ ] **Step 1: Write the failing test**

Append to `swaps_dashboard/tests/test_swaps_routes.py`:

```python
def test_swaps_page_launches():
    r = client.get('/swaps')
    assert r.status_code == 200
    assert 'Swap trades' in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest swaps_dashboard/tests/test_swaps_routes.py -q`
Expected: FAIL (404, route doesn't exist yet)

- [ ] **Step 3: Write minimal implementation**

Move `dashboard/templates/swaps.html` to `swaps_dashboard/templates/swaps.html`
unchanged (same content — it only references `/swaps`, which still resolves inside
this app, and the `num`/`usd`/`ts` Jinja filters, registered below).

```bash
git mv dashboard/templates/swaps.html swaps_dashboard/templates/swaps.html
```

Create `swaps_dashboard/templates/base.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}Swaps Dashboard{% endblock %}</title>
<style>
/* Copied from dashboard/templates/base.html -- small, self-contained
   duplication rather than a cross-package import, so the two apps stay
   independently deployable. Keep in sync by hand if the main dashboard's
   palette changes; this app has no other template that needs it. */
:root {
  --bg: #070a14;
  --panel: #0c1122;
  --panel-2: #121a30;
  --border: #24304f;
  --text: #f8fafc;
  --muted: #a389ad;
  --accent: #a855f7;
  --accent-2: #3b82f6;
  --accent-soft: #a855f722;
  --accent-glow: #a855f799;
  --ok: #34d399;
  --ok-soft: #34d39922;
  --warn: #fbbf24;
  --warn-soft: #fbbf2422;
  --err: #f87171;
  --err-soft: #f8717122;
  --mono: ui-monospace, "Cascadia Mono", Consolas, "DejaVu Sans Mono", monospace;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--text);
  font: 14px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  -webkit-font-smoothing: antialiased;
}
a {
  color: var(--accent); text-decoration: none;
  border-bottom: 1px solid transparent; padding-bottom: 1px;
  transition: border-image .15s ease;
}
a:hover {
  border-bottom: 1px solid;
  border-image: linear-gradient(90deg, var(--accent-2), var(--accent)) 1;
}
code, pre, .mono { font-family: var(--mono); }
header.topbar {
  position: sticky; top: 0; z-index: 20;
  background: var(--panel);
  border-bottom: 1px solid var(--border);
  padding: 0 20px;
  display: flex; align-items: center; gap: 22px; flex-wrap: wrap;
}
.brand { font-weight: 650; letter-spacing: -0.01em; padding: 14px 0; white-space: nowrap; }
.brand span { color: var(--muted); font-weight: 400; }
nav.tabs { display: flex; gap: 4px; flex-wrap: wrap; }
nav.tabs a {
  padding: 14px 12px; color: var(--muted); font-weight: 500;
  border-bottom: 2px solid transparent; text-decoration: none;
}
nav.tabs a:hover { color: var(--text); }
nav.tabs a.on { color: var(--text); border-bottom-color: var(--accent); text-shadow: 0 0 12px var(--accent-glow); }
main { max-width: 1500px; margin: 0 auto; padding: 22px 20px 64px; }
h1 { font-size: 22px; margin: 0 0 4px; letter-spacing: -0.02em; }
h2 { font-size: 15px; margin: 0; letter-spacing: -0.01em; }
.sub { color: var(--muted); margin: 0 0 20px; font-size: 13px; }
.panel {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 12px;
  margin-bottom: 18px;
  overflow: hidden;
  box-shadow: 0 1px 2px rgba(0,0,0,.16), 0 12px 28px -16px rgba(0,0,0,.4);
}
.panel > header {
  display: flex; align-items: baseline; justify-content: space-between; gap: 12px;
  padding: 12px 16px; border-bottom: 1px solid var(--border);
  background: var(--panel-2); flex-wrap: wrap;
}
.panel > header .note { color: var(--muted); font-size: 12px; }
.panel .body { padding: 14px 16px; }
.tablewrap { overflow-x: auto; max-width: 100%; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--border); white-space: nowrap; }
th {
  position: sticky; top: 0; background: var(--panel-2);
  color: var(--muted); font-size: 11px; font-weight: 600;
  text-transform: uppercase; letter-spacing: .05em;
}
tbody tr:hover { background: var(--panel-2); }
tbody tr:last-child td { border-bottom: none; }
td.num, th.num { text-align: right; font-family: var(--mono); font-size: 12.5px; }
td.wrap { white-space: normal; max-width: 460px; }
.scrollbox { max-height: 560px; overflow: auto; }
.pill {
  display: inline-block; padding: 2px 8px; border-radius: 999px;
  font-size: 11px; font-weight: 600; letter-spacing: .02em;
  border: 1px solid transparent; white-space: nowrap;
}
.pill.ok { background: var(--ok-soft); color: var(--ok); }
.pill.partial, .pill.running, .pill.queued { background: var(--warn-soft); color: var(--warn); }
.pill.error, .pill.timeout, .pill.failed { background: var(--err-soft); color: var(--err); }
.pill.plain { background: var(--accent-soft); color: var(--accent); border-color: var(--accent); box-shadow: 0 0 10px -2px var(--accent-glow); }
.empty {
  padding: 26px 16px; text-align: center; color: var(--muted);
  border: 1px dashed var(--border); border-radius: 8px; margin: 4px 0;
}
.errbox {
  padding: 10px 12px; border-radius: 8px; margin: 0 0 12px;
  background: var(--err-soft); color: var(--err);
  border: 1px solid var(--border); font-family: var(--mono); font-size: 12px;
  word-break: break-word;
}
form.runform { display: flex; gap: 10px; align-items: flex-end; flex-wrap: wrap; }
.field { display: flex; flex-direction: column; gap: 5px; }
.field label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .05em; }
input, select, button {
  font: inherit; color: var(--text); background: var(--panel-2);
  border: 1px solid var(--border); border-radius: 7px; padding: 7px 10px;
}
input:focus, select:focus { outline: 2px solid var(--accent-soft); border-color: var(--accent); }
button {
  background: linear-gradient(135deg, var(--accent-2), var(--accent));
  border-color: transparent; color: #fff;
  font-weight: 700; cursor: pointer;
}
.muted { color: var(--muted); }
.small { font-size: 12px; }
.pager { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding: 12px 16px; }
</style>
</head>
<body>
<header class="topbar">
  <div class="brand">FinancialDevelopment <span>/ swap data</span></div>
  <nav class="tabs">
    <a href="/swaps" class="{{ 'on' if active == 'swaps' else '' }}">Swap trades</a>
  </nav>
</header>
<main>
{% block content %}{% endblock %}
</main>
{% block scripts %}{% endblock %}
</body>
</html>
```

Add to the **end** of `swaps_dashboard/app.py` (after the existing `/health` route
from Task 1). This must go after `app = FastAPI(...)`, not before it — the `/swaps`
route below decorates `app`, so `app` has to already exist as a module-level name
when this code executes at import time:

```python
import sqlite3
import threading
import time
from typing import List, Optional, Tuple

from swaps_query import SwapsQuery  # noqa: E402


def _fmt_num(value: Any) -> str:
    if value is None or value == '':
        return '--'
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f == int(f) and abs(f) < 1e15:
        return f'{int(f):,}'
    return f'{f:,.4f}'


def _fmt_usd(value: Any) -> str:
    """Compact notional -- these run to the trillions and blow out a table cell."""
    if value is None or value == '':
        return '--'
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    sign = '-' if f < 0 else ''
    f = abs(f)
    for cut, suffix in ((1e12, 'T'), (1e9, 'B'), (1e6, 'M'), (1e3, 'K')):
        if f >= cut:
            return f'{sign}${f / cut:,.2f}{suffix}'
    return f'{sign}${f:,.2f}'


def _fmt_ts(value: Any) -> str:
    if not value:
        return '--'
    text = str(value).replace('T', ' ').replace('Z', '')
    return text[:19]


TEMPLATES.env.filters['num'] = _fmt_num
TEMPLATES.env.filters['usd'] = _fmt_usd
TEMPLATES.env.filters['ts'] = _fmt_ts


def _db() -> Optional[sqlite3.Connection]:
    """Read connection with the same row_factory the rest of the repo uses."""
    if not os.path.exists(DB_PATH):
        return None
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


# --------------------------------------------------------------------------
# /swaps filter-option cache
# --------------------------------------------------------------------------
# `SELECT DISTINCT regulator`/`SELECT DISTINCT asset_class FROM swap_trades`
# were being re-run on every /swaps request to populate the filter dropdowns.
# On the production 342GB swaps.db these are effectively full scans (no
# equality predicate to make an index seek selective) and were the remaining
# source of the route's 15s timeout after migration 006 fixed search_trades
# itself. The option set changes at most once per ingest cycle (new
# regulator/asset_class values are rare), so a short in-process TTL cache is
# safe and keeps the DB path as part of the key in case DB_PATH ever changes
# within a process lifetime (e.g. tests).
_SWAPS_OPTIONS_CACHE_TTL_SEC = 60
_swaps_options_cache: Dict[str, Tuple[float, List[str], List[str]]] = {}
_swaps_options_lock = threading.Lock()


def _get_swaps_filter_options(conn: sqlite3.Connection, db_path: str) -> Tuple[List[str], List[str]]:
    """Cached (regulators, asset_classes) distinct-value lists for the /swaps filter dropdowns."""
    now = time.monotonic()
    with _swaps_options_lock:
        cached = _swaps_options_cache.get(db_path)
        if cached is not None and (now - cached[0]) < _SWAPS_OPTIONS_CACHE_TTL_SEC:
            return cached[1], cached[2]

    regulators = [r['regulator'] for r in conn.execute(
        'SELECT DISTINCT regulator FROM swap_trades '
        'WHERE regulator IS NOT NULL ORDER BY regulator;')]
    asset_class_set: set = set()
    for reg in regulators:
        asset_class_set.update(
            r['asset_class'] for r in conn.execute(
                'SELECT DISTINCT asset_class FROM swap_trades '
                'WHERE regulator = ? AND asset_class IS NOT NULL;', (reg,)))
    asset_classes = sorted(asset_class_set)

    with _swaps_options_lock:
        _swaps_options_cache[db_path] = (now, regulators, asset_classes)
    return regulators, asset_classes


@app.get('/swaps', response_class=HTMLResponse)
def swaps(request: Request,
          q: str = '',
          regulator: str = '',
          asset_class: str = '',
          cleared: str = '',
          effective_date_from: str = '',
          effective_date_to: str = '',
          sort_by: str = 'ingested_at',
          sort_dir: str = 'desc',
          page: int = 1,
          per_page: int = 50):
    columns = ['dissemination_id', 'regulator', 'asset_class', 'action_type',
               'event_type', 'effective_date', 'expiration_date', 'cleared',
               'notional_amount_leg1', 'notional_currency_leg1', 'price',
               'underlying_asset_name', 'upi', 'company_name', 'ticker',
               'upi_underlier_name', 'ingested_at']
    notional_cols = {'notional_amount_leg1'}

    cleared_bool: Optional[bool] = None
    if cleared in ('1', 'true', 'True'):
        cleared_bool = True
    elif cleared in ('0', 'false', 'False'):
        cleared_bool = False

    result: Dict[str, Any] = {
        'rows': [], 'total': 0, 'count_is_exact': True, 'has_more': False,
        'count_cap': SwapsQuery.SEARCH_COUNT_CAP, 'page': page, 'per_page': per_page,
        'pages': 1, 'sort_by': sort_by, 'sort_dir': sort_dir,
    }
    regulators: List[str] = []
    asset_classes: List[str] = []
    error: Optional[str] = None

    if not os.path.exists(DB_PATH):
        error = f'swaps.db not found at {DB_PATH}'
    else:
        try:
            sq = SwapsQuery(DB_PATH)
            result = sq.search_trades(
                query=q, regulator=regulator or None, asset_class=asset_class or None,
                cleared=cleared_bool,
                effective_date_from=effective_date_from or None,
                effective_date_to=effective_date_to or None,
                sort_by=sort_by, sort_dir=sort_dir, page=page, per_page=per_page,
            )
            conn = _db()
            if conn is not None:
                try:
                    regulators, asset_classes = _get_swaps_filter_options(conn, DB_PATH)
                finally:
                    conn.close()
        except Exception as e:
            error = f'{type(e).__name__}: {e}'

    return TEMPLATES.TemplateResponse(request, 'swaps.html', {
        'active': 'swaps',
        'columns': columns,
        'notional_cols': notional_cols,
        'rows': result['rows'],
        'total': result['total'],
        'count_is_exact': result['count_is_exact'],
        'has_more': result['has_more'],
        'count_cap': result['count_cap'],
        'page': result['page'],
        'pages': result['pages'],
        'per_page': result['per_page'],
        'q': q,
        'regulator': regulator,
        'asset_class': asset_class,
        'cleared': cleared,
        'effective_date_from': effective_date_from,
        'effective_date_to': effective_date_to,
        'sort_by': result['sort_by'],
        'sort_dir': result['sort_dir'],
        'sort_columns': list(SwapsQuery.SEARCH_SORT_COLUMNS.keys()),
        'regulators': regulators,
        'asset_classes': asset_classes,
        'error': error,
    })
```

`import time` and `from typing import Any, Dict` are already present from Task 1's
`Dict[str, Any]` usage — check the top of `swaps_dashboard/app.py` and only add
`sqlite3`, `threading`, `time`, `List`, `Optional`, `Tuple`, and the `SwapsQuery`
import if not already there.

Now delete the `/swaps` route, `_get_swaps_filter_options`, and its cache globals
from `dashboard/app.py`:

- Delete `dashboard/app.py:675-755` (the whole `@app.get('/swaps', ...)` block through
  its closing `})`).
- Delete `dashboard/app.py:137-189` (the `_swaps_options_cache`/`_swaps_options_lock`
  globals and `_get_swaps_filter_options`).
- Delete `dashboard/app.py:71-72` (`from db_loader import SwapsLoader` and
  `from swaps_query import SwapsQuery`) — **do not delete yet if `_database_stats()`,
  `_ingestion_state()`, or `_scrape_log()` still reference them**; those move in
  Task 3. For this task, only delete the `SwapsQuery` import if nothing else in
  `dashboard/app.py` uses `SwapsQuery` after removing `/swaps` and
  `_get_swaps_filter_options` (grep to confirm: `grep -n "SwapsQuery" dashboard/app.py`
  should show zero remaining hits before deleting the import).
- Leave `_db()` (`dashboard/app.py:128-134`) in place for now — `_ingestion_state()`
  and `_scrape_log()` still call it until Task 3.

Move the swap-options cache test:

```bash
git mv dashboard/tests/test_swaps_options_cache.py swaps_dashboard/tests/test_swaps_options_cache.py
```

Edit `swaps_dashboard/tests/test_swaps_options_cache.py`: change
`import dashboard.app as dashboard_app` to `import swaps_dashboard.app as dashboard_app`
(keep the local alias `dashboard_app` — it's only a local name, no need to churn the
rest of the file) and drop the manual `sys.path` insert block (Task 1's `_get_swaps_filter_options`
import path already covers `pythonpath = ["."]` via `pyproject.toml`, same as every
other test in the repo) — keep the file otherwise identical.

- [ ] **Step 4: Run test to verify it passes**

Run:
```
env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest swaps_dashboard/tests/ -q
env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest dashboard/tests/ -q
```
Expected: PASS on both. The second run also confirms `dashboard/app.py` still
imports cleanly with `/swaps` removed.

- [ ] **Step 5: Commit**

```bash
git add swaps_dashboard/app.py swaps_dashboard/templates/base.html swaps_dashboard/templates/swaps.html swaps_dashboard/tests/test_swaps_routes.py swaps_dashboard/tests/test_swaps_options_cache.py dashboard/app.py dashboard/templates/swaps.html dashboard/tests/test_swaps_options_cache.py
git commit -m "feat(swaps-dashboard): move /swaps browse UI out of the main dashboard"
```

---

### Task 3: Move `/trades`, `/instruments/{upi}`, `/analytics/*` into `swaps_dashboard`

**Files:**
- Modify: `swaps_dashboard/app.py`
- Modify: `swaps_dashboard/tests/test_swaps_routes.py`
- Modify: `dashboard/app.py:2365-2585` (delete these five routes)
- Modify: `dashboard/app.py:73` (delete `CrossSourceQueryBuilder`/`get_cross_source_summary`
  import — `get_cross_source_summary` was already unused dead code before this move;
  don't carry it into `swaps_dashboard/app.py` either)
- Modify: `dashboard/app.py` (delete `_db()` now that nothing in the file calls it —
  confirm with `grep -n "_db()" dashboard/app.py` before deleting)

**Interfaces:**
- Consumes: `shared.query_builder.CrossSourceQueryBuilder` (`.query_by_sources`,
  `.resolve_instrument_across_sources`, `.aggregate_notional_cross_source`,
  `.aggregate_notional_by_source`, `.timeseries_by_source`).
- Produces: `GET /trades`, `GET /instruments/{upi}`, `GET /analytics/cross-source-notional`,
  `GET /analytics/timeseries` on `swaps_dashboard.app.app`, all JSON (no template).

- [ ] **Step 1: Write the failing test**

Append to `swaps_dashboard/tests/test_swaps_routes.py`:

```python
def test_trades_route_launches():
    r = client.get('/trades')
    assert r.status_code == 200
    body = r.json()
    assert 'count' in body or 'error' in body


def test_analytics_timeseries_route_launches():
    r = client.get('/analytics/timeseries')
    assert r.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest swaps_dashboard/tests/test_swaps_routes.py -q`
Expected: FAIL (404 on `/trades` and `/analytics/timeseries`)

- [ ] **Step 3: Write minimal implementation**

Add to the top of `swaps_dashboard/app.py`, alongside the existing `datetime` usage
(there is none yet in this file — add the import fresh):

```python
from datetime import datetime, timedelta, timezone
```

`timedelta` was missing from the equivalent import in `dashboard/app.py`
(`from datetime import datetime, timezone` — no `timedelta`), a pre-existing latent
bug that only `/analytics/timeseries` triggers (it calls `timedelta(days=days_back)`).
Fix it here since the import is being rewritten anyway.

Add to the **end** of `swaps_dashboard/app.py` (after Task 2's `/swaps` route —
these routes also decorate `app`, so they must come after `app = FastAPI(...)` same
as `/swaps` did):

```python
from shared.query_builder import CrossSourceQueryBuilder  # noqa: E402


@app.get('/trades')
def get_trades(
    source: Optional[str] = None,
    days_back: int = 30,
    limit: int = 1000,
):
    """Get swap trades, optionally filtered by data source(s).

    Query params:
      - source: Comma-separated source names (e.g., 'DTCC,CME'). If omitted, returns all.
      - days_back: Number of days to look back (default 30)
      - limit: Maximum rows to return (default 1000)

    Returns:
        List of trade dicts with data_source field, or error dict
    """
    if not os.path.exists(DB_PATH):
        return {'error': f'swaps.db not found at {DB_PATH}'}

    try:
        sources = []
        if source:
            sources = [s.strip().upper() for s in source.split(',') if s.strip()]

        if sources:
            builder = CrossSourceQueryBuilder(DB_PATH)
            trades = builder.query_by_sources(sources, days_back=days_back, limit=limit)
        else:
            conn = _db()
            if not conn:
                return {'error': f'swaps.db not found at {DB_PATH}'}
            try:
                rows = [dict(r) for r in conn.execute(
                    f"""SELECT * FROM swap_trades
                       WHERE effective_date >= date('now', '-{days_back} days')
                       ORDER BY effective_date DESC, dissemination_id DESC
                       LIMIT ?;""", (limit,))]
                trades = rows
            finally:
                conn.close()

        return {
            'count': len(trades),
            'sources_requested': sources if sources else ['all'],
            'days_back': days_back,
            'trades': trades,
        }
    except Exception as e:
        return {'error': f'{type(e).__name__}: {e}'}


@app.get('/instruments/{upi}')
def get_instrument(upi: str, resolve_cross_source: bool = True):
    """Resolve an instrument (UPI) across data sources.

    Args:
        upi: UPI to look up
        resolve_cross_source: If true, show all occurrences across sources (default true)

    Returns:
        Dict with instrument info and trades by source
    """
    if not os.path.exists(DB_PATH):
        return {'error': f'swaps.db not found at {DB_PATH}'}

    try:
        builder = CrossSourceQueryBuilder(DB_PATH)

        if resolve_cross_source:
            trades = builder.resolve_instrument_across_sources(upi)
        else:
            trades = builder.resolve_instrument_across_sources(upi, sources=['DTCC'])

        if not trades:
            return {'upi': upi, 'found': False, 'message': 'UPI not found in any source'}

        by_source = {}
        for trade in trades:
            source = trade.get('data_source', 'unknown')
            if source not in by_source:
                by_source[source] = []
            by_source[source].append(trade)

        first_trade = trades[0]
        return {
            'upi': upi,
            'found': True,
            'underlier_asset_name': first_trade.get('underlying_asset_name'),
            'asset_class': first_trade.get('asset_class'),
            'upi_underlier_name': first_trade.get('upi_underlier_name'),
            'total_trades_across_sources': len(trades),
            'trades_by_source': {
                source: len(trade_list)
                for source, trade_list in by_source.items()
            },
            'sources': list(by_source.keys()),
            'sample_trades': by_source,
        }
    except Exception as e:
        return {'error': f'{type(e).__name__}: {e}'}


@app.get('/analytics/cross-source-notional')
def cross_source_notional(
    source: Optional[str] = None,
    days_back: int = 30,
):
    """Get total notional aggregated across data sources.

    Query params:
      - source: Comma-separated source names. If omitted, includes all available sources.
      - days_back: Number of days to aggregate (default 30)

    Returns:
        Dict with total notional and per-source breakdown
    """
    if not os.path.exists(DB_PATH):
        return {'error': f'swaps.db not found at {DB_PATH}'}

    try:
        sources = []
        if source:
            sources = [s.strip().upper() for s in source.split(',') if s.strip()]
        else:
            conn = _db()
            if conn:
                try:
                    cur = conn.cursor()
                    cur.execute('SELECT DISTINCT data_source FROM swap_trades;')
                    sources = [row[0] for row in cur.fetchall() if row[0]]
                finally:
                    conn.close()

        if not sources:
            return {
                'total_notional': 0,
                'by_source': {},
                'days_back': days_back,
                'message': 'No sources found in database',
            }

        builder = CrossSourceQueryBuilder(DB_PATH)
        total = builder.aggregate_notional_cross_source(sources, days_back=days_back)
        by_source = builder.aggregate_notional_by_source(sources, days_back=days_back)

        return {
            'total_notional': total,
            'by_source': by_source,
            'sources_included': sources,
            'days_back': days_back,
        }
    except Exception as e:
        return {'error': f'{type(e).__name__}: {e}'}


@app.get('/analytics/timeseries')
def timeseries_by_source(
    source: Optional[str] = None,
    days_back: int = 90,
):
    """Get daily time-series data by source.

    Returns daily aggregates (notional, trade count) for each source over the
    requested period.

    Query params:
      - source: Comma-separated source names. If omitted, includes all sources.
      - days_back: Number of days to look back (default 90)

    Returns:
        Dict mapping source name -> list of daily aggregates
    """
    if not os.path.exists(DB_PATH):
        return {'error': f'swaps.db not found at {DB_PATH}'}

    try:
        sources = []
        if source:
            sources = [s.strip().upper() for s in source.split(',') if s.strip()]
        else:
            conn = _db()
            if conn:
                try:
                    cur = conn.cursor()
                    cur.execute('SELECT DISTINCT data_source FROM swap_trades;')
                    sources = [row[0] for row in cur.fetchall() if row[0]]
                finally:
                    conn.close()

        if not sources:
            return {'timeseries': {}, 'message': 'No sources found'}

        start_date = (datetime.now(timezone.utc).date()
                     - timedelta(days=days_back))
        end_date = datetime.now(timezone.utc).date()

        builder = CrossSourceQueryBuilder(DB_PATH)
        timeseries = builder.timeseries_by_source(sources, start_date=start_date,
                                                  end_date=end_date)

        return {
            'timeseries': timeseries,
            'sources': sources,
            'period': {
                'start_date': str(start_date),
                'end_date': str(end_date),
                'days': days_back,
            },
        }
    except Exception as e:
        return {'error': f'{type(e).__name__}: {e}'}
```

Delete `dashboard/app.py:2365-2585` (the five routes: `/trades`, `/instruments/{upi}`,
`/analytics/cross-source-notional`, `/analytics/timeseries`, and the blank line before
`/health`).

Delete `dashboard/app.py:73` (`from shared.query_builder import CrossSourceQueryBuilder, get_cross_source_summary`).

Confirm no remaining callers, then delete `_db()`:

```bash
grep -n "_db()" dashboard/app.py
```

Expected: no matches. Then delete `dashboard/app.py:128-134` (the `_db()` function).

- [ ] **Step 4: Run test to verify it passes**

Run:
```
env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest swaps_dashboard/tests/ -q
env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest dashboard/tests/ -q
```
Expected: PASS on both.

- [ ] **Step 5: Commit**

```bash
git add swaps_dashboard/app.py swaps_dashboard/tests/test_swaps_routes.py dashboard/app.py
git commit -m "feat(swaps-dashboard): move /trades, /instruments, /analytics out of the main dashboard"
```

---

### Task 4: Regression guard — main dashboard no longer imports swap-DB modules at module scope

**Files:**
- Create: `dashboard/tests/test_no_swaps_imports.py`

**Interfaces:**
- None (test-only task; nothing new is produced for later tasks to consume).

- [ ] **Step 1: Write the failing test**

```python
# dashboard/tests/test_no_swaps_imports.py
"""Regression guard for the swaps-dashboard split
(docs/superpowers/specs/2026-08-27-swaps-dashboard-split-design.md).

dashboard/app.py must never import swaps_query, db_loader, or
shared.query_builder at module scope again -- that's exactly what made
home() slow (the queries themselves were route-scoped, but the split's whole
point is that this file has zero swap-DB code path left to regress into).
"""
import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

APP_PY = Path(__file__).resolve().parent.parent / 'app.py'
FORBIDDEN_MODULES = {'swaps_query', 'db_loader', 'shared.query_builder'}


def _imported_module_names(tree: ast.Module):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_dashboard_app_does_not_import_swap_db_modules():
    tree = ast.parse(APP_PY.read_text(encoding='utf-8'))
    imported = _imported_module_names(tree)
    overlap = imported & FORBIDDEN_MODULES
    assert not overlap, f'dashboard/app.py imports swap-DB modules: {overlap}'
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest dashboard/tests/test_no_swaps_imports.py -q`
Expected: PASS already (Tasks 2-3 removed these imports) — this step is a
verification, not a red/green cycle; if it fails, Task 2 or 3 left a stray import
behind and must be fixed before continuing.

- [ ] **Step 3: N/A — implementation already done in Tasks 2-3**

- [ ] **Step 4: Re-run to confirm**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest dashboard/tests/test_no_swaps_imports.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/tests/test_no_swaps_imports.py
git commit -m "test(dashboard): guard against swap-DB imports creeping back into app.py"
```

---

### Task 5: Snapshot writer in `swaps_dashboard`

**Files:**
- Modify: `swaps_dashboard/app.py`
- Create: `swaps_dashboard/tests/test_snapshot.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `orchestrator.get_recent_swap_activity(limit: int) -> List[Dict[str, Any]]`.
- Produces: `swaps_dashboard.app.SNAPSHOT_PATH` (str, absolute path to
  `swaps_dashboard/cache/overview_snapshot.json`), `swaps_dashboard.app._build_snapshot() -> Dict[str, Any]`
  with keys `generated_at` (str, `YYYY-MM-DDTHH:MM:SSZ`), `stats` (dict from
  `SwapsQuery.get_database_stats()` or the empty shell below), `stats_error`
  (`Optional[str]`), `top_products` (`List[Dict[str, Any]]`, capped at 5),
  `top_error` (`Optional[str]`), `last_scrape` (`Optional[Dict[str, Any]]`),
  `scrape_error` (`Optional[str]`), `swaps_dashboard.app._write_snapshot() -> None`
  (writes `SNAPSHOT_PATH` atomically).

- [ ] **Step 1: Write the failing test**

```python
# swaps_dashboard/tests/test_snapshot.py
"""Covers swaps_dashboard.app's snapshot writer -- the JSON file
dashboard/app.py's Overview/Tools cards read instead of ever querying
swaps.db directly. See
docs/superpowers/specs/2026-08-27-swaps-dashboard-split-design.md.
"""
import json

import pytest

import swaps_dashboard.app as swaps_app

pytestmark = pytest.mark.unit


def test_build_snapshot_degrades_gracefully_with_no_db(monkeypatch):
    monkeypatch.setattr(swaps_app, 'DB_PATH', '/nonexistent/swaps.db')
    snapshot = swaps_app._build_snapshot()
    assert snapshot['stats'] == {
        'total_records': 0, 'unique_upis': 0, 'by_regulator_asset_class': [],
        'earliest_date': None, 'latest_date': None,
    }
    assert snapshot['stats_error'] is not None
    assert snapshot['top_products'] == []
    assert snapshot['last_scrape'] is None
    assert 'generated_at' in snapshot


def test_write_snapshot_writes_valid_json(tmp_path, monkeypatch):
    target = tmp_path / 'cache' / 'overview_snapshot.json'
    monkeypatch.setattr(swaps_app, 'SNAPSHOT_PATH', str(target))
    monkeypatch.setattr(swaps_app, 'SNAPSHOT_DIR', str(tmp_path / 'cache'))
    monkeypatch.setattr(swaps_app, 'DB_PATH', '/nonexistent/swaps.db')

    swaps_app._write_snapshot()

    assert target.exists()
    data = json.loads(target.read_text(encoding='utf-8'))
    assert 'generated_at' in data
    assert data['stats']['total_records'] == 0


def test_write_snapshot_caps_top_products_at_five(tmp_path, monkeypatch):
    target = tmp_path / 'cache' / 'overview_snapshot.json'
    monkeypatch.setattr(swaps_app, 'SNAPSHOT_PATH', str(target))
    monkeypatch.setattr(swaps_app, 'SNAPSHOT_DIR', str(tmp_path / 'cache'))
    monkeypatch.setattr(
        swaps_app.orchestrator, 'get_recent_swap_activity',
        lambda limit=15: [{'product': f'p{i}', 'total_notional': i, 'trade_count': 1} for i in range(20)],
    )

    swaps_app._write_snapshot()

    data = json.loads(target.read_text(encoding='utf-8'))
    assert len(data['top_products']) == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest swaps_dashboard/tests/test_snapshot.py -q`
Expected: FAIL (`AttributeError: module 'swaps_dashboard.app' has no attribute '_build_snapshot'`)

- [ ] **Step 3: Write minimal implementation**

Add to the **end** of `swaps_dashboard/app.py` (after Task 3's routes):

```python
import asyncio
import contextlib
import json
import logging
import tempfile

SNAPSHOT_DIR = os.path.join(SWAPS_DASHBOARD_DIR, 'cache')
SNAPSHOT_PATH = os.path.join(SNAPSHOT_DIR, 'overview_snapshot.json')
_SNAPSHOT_INTERVAL_SEC = 300


def _database_stats() -> Tuple[Dict[str, Any], Optional[str]]:
    """SwapsQuery.get_database_stats(), or an empty shell plus the error text."""
    empty = {
        'total_records': 0, 'unique_upis': 0, 'by_regulator_asset_class': [],
        'earliest_date': None, 'latest_date': None,
    }
    if not os.path.exists(DB_PATH):
        return empty, f'swaps.db not found at {DB_PATH}'
    try:
        return SwapsQuery(DB_PATH).get_database_stats(), None
    except Exception as e:
        return empty, f'{type(e).__name__}: {e}'


def _top_notional() -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Top notional products for the most recent effective_date present."""
    try:
        return orchestrator.get_recent_swap_activity(limit=15), None
    except Exception as e:
        return [], f'{type(e).__name__}: {e}'


def _scrape_log(limit: int = 8) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """SwapsLoader.get_last_scrape_log(), or None plus the error text."""
    if not os.path.exists(DB_PATH):
        return None, f'swaps.db not found at {DB_PATH}'
    try:
        return SwapsLoader(DB_PATH).get_last_scrape_log(), None
    except Exception as e:
        return None, f'{type(e).__name__}: {e}'


def _build_snapshot() -> Dict[str, Any]:
    stats, stats_error = _database_stats()
    top_products, top_error = _top_notional()
    last_scrape, scrape_error = _scrape_log()
    return {
        'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'stats': stats,
        'stats_error': stats_error,
        'top_products': top_products[:5],
        'top_error': top_error,
        'last_scrape': last_scrape,
        'scrape_error': scrape_error,
    }


def _write_snapshot() -> None:
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    snapshot = _build_snapshot()
    fd, tmp_path = tempfile.mkstemp(dir=SNAPSHOT_DIR, prefix='.overview_snapshot_', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(snapshot, f)
        os.replace(tmp_path, SNAPSHOT_PATH)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


_logger = logging.getLogger('swaps_dashboard')


async def _snapshot_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(_write_snapshot)
        except Exception as e:
            _logger.warning('snapshot write failed: %s', e)
        await asyncio.sleep(_SNAPSHOT_INTERVAL_SEC)
```

Add `from db_loader import SwapsLoader` next to the existing `SwapsQuery` import.

Wire the loop into a lifespan handler and pass it to `FastAPI(...)`. Replace:

```python
app = FastAPI(title='Swaps Dashboard')
```

with:

```python
@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    task = asyncio.create_task(_snapshot_loop())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(title='Swaps Dashboard', lifespan=_lifespan)
```

This is an in-place edit of the original `app = FastAPI(title='Swaps Dashboard')`
line from Task 1 (near the top of the file, before Task 1's `/health` and before
Tasks 2-3's routes appended at the end). `_lifespan` referencing `_snapshot_loop`
— defined further down the file — is fine: Python resolves that name from the
module's globals when `_lifespan` actually *runs* (when uvicorn starts the app),
not when it's defined, and by then the whole module has finished importing. The
routes appended later by Tasks 2-3 still correctly bind to this same `app` object,
since they're decorated after this line has already executed.

Add to `.gitignore` (near the existing `.cache/`/`.shared_cache/` entries):

```
swaps_dashboard/cache/
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest swaps_dashboard/tests/ -q`
Expected: PASS (all of `test_snapshot.py`, `test_swaps_routes.py`, `test_swaps_options_cache.py`)

- [ ] **Step 5: Commit**

```bash
git add swaps_dashboard/app.py swaps_dashboard/tests/test_snapshot.py .gitignore
git commit -m "feat(swaps-dashboard): write an overview snapshot every 5 minutes"
```

---

### Task 6: `swaps_dashboard.bat` / `.sh` launchers

**Files:**
- Create: `swaps_dashboard.bat`
- Create: `swaps_dashboard.sh`

**Interfaces:**
- None (shell scripts; no Python interface).

- [ ] **Step 1: N/A (no automated test for launcher scripts — manual verification below)**

- [ ] **Step 2: N/A**

- [ ] **Step 3: Write the launchers**

```bat
@echo off
REM Launches the standalone swap-data browser and opens it in your default
REM browser. Binds to localhost only. Split out of dashboard.bat 2026-08-27
REM so the main dashboard's boot never has to touch swaps.db -- see
REM docs/superpowers/specs/2026-08-27-swaps-dashboard-split-design.md.
cd /d "%~dp0"
set PYTHONPATH=
set PYTHONHOME=
if not exist ".venv\Scripts\python.exe" (
    echo Shared .venv not found at %~dp0.venv — run: python -m venv .venv ^&^& .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)
.venv\Scripts\python.exe -c "import fastapi, uvicorn, jinja2" 2>nul
if errorlevel 1 (
    echo Your .venv is missing one or more required packages
    echo ^(fastapi / uvicorn / jinja2^). This usually means
    echo requirements.txt was updated after your venv was created.
    echo.
    echo Fix: .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)
REM Refuse to start a second swaps dashboard on the same port -- same reason
REM dashboard.bat refuses to double-launch on 8787: a duplicate process
REM doubles up reads against swaps.db.
netstat -ano | findstr /C:"127.0.0.1:8788" | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo A swaps dashboard already appears to be running on port 8788.
    echo Only run ONE instance of swaps_dashboard.bat at a time.
    echo Opening your existing swaps dashboard in the browser instead...
    start "" http://127.0.0.1:8788/swaps
    pause
    exit /b 0
)
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8788/swaps"
.venv\Scripts\python.exe -m uvicorn swaps_dashboard.app:app --host 127.0.0.1 --port 8788
if %ERRORLEVEL% neq 0 (
    echo.
    echo Swaps dashboard exited with an error ^(see above^). If it says the port
    echo is already in use, another process already owns port 8788 — edit
    echo swaps_dashboard.bat and pick a different --port number ^(and update the
    echo URL above it^).
    pause
)
```

```bash
#!/usr/bin/env bash
# Launches the standalone swap-data browser and opens it in your default
# browser. Binds to localhost only. Linux/Mac equivalent of
# swaps_dashboard.bat. Split out of dashboard.sh 2026-08-27 -- see
# docs/superpowers/specs/2026-08-27-swaps-dashboard-split-design.md.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$SCRIPT_DIR"
unset PYTHONPATH
unset PYTHONHOME

VENV_PYTHON=""
for candidate in "$SCRIPT_DIR/.venv/bin/python3" "$SCRIPT_DIR/.venv/bin/python"; do
    if [ -x "$candidate" ]; then
        VENV_PYTHON="$candidate"
        break
    fi
done

if [ -z "$VENV_PYTHON" ]; then
    echo "Shared .venv not found at $SCRIPT_DIR/.venv -- run: python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt"
    echo
    exit 1
fi

PORT=8788
URL="http://127.0.0.1:${PORT}/swaps"

open_url() {
    if command -v open >/dev/null 2>&1; then
        open "$URL" >/dev/null 2>&1 &
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$URL" >/dev/null 2>&1 &
    else
        echo "Open $URL in your browser."
    fi
}

if (exec 3<>"/dev/tcp/127.0.0.1/${PORT}") 2>/dev/null; then
    exec 3<&- 2>/dev/null || true
    exec 3>&- 2>/dev/null || true
    echo "A swaps dashboard already appears to be running on port ${PORT}."
    echo "Only run ONE instance of swaps_dashboard.sh at a time."
    echo "Opening your existing swaps dashboard in the browser instead..."
    open_url
    exit 0
fi

( sleep 2; open_url ) &

"$VENV_PYTHON" -m uvicorn swaps_dashboard.app:app --host 127.0.0.1 --port "$PORT"
EXIT_CODE=$?
if [ "$EXIT_CODE" -ne 0 ]; then
    echo
    echo "Swaps dashboard exited with an error (see above). If it says the port is"
    echo "already in use, another process already owns port ${PORT} -- edit"
    echo "swaps_dashboard.sh and pick a different --port number (and update the URL"
    echo "above it)."
fi
exit "$EXIT_CODE"
```

Make `swaps_dashboard.sh` executable:

```bash
chmod +x swaps_dashboard.sh
```

- [ ] **Step 4: Manual verification**

Run `swaps_dashboard.bat`. Expected: browser opens to `http://127.0.0.1:8788/swaps`
within ~2 seconds, page renders (may be slow on the real 342GB `swaps.db` — that's
expected and fine, it's no longer on anyone else's boot path). Run it a second time
while the first is still up. Expected: "already running" message, opens existing tab,
does not spawn a duplicate `uvicorn` process.

- [ ] **Step 5: Commit**

```bash
git add swaps_dashboard.bat swaps_dashboard.sh
git commit -m "feat(swaps-dashboard): add swaps_dashboard.bat/.sh launchers"
```

---

### Task 7: Overview + Tools card reading the snapshot file

**Files:**
- Modify: `dashboard/app.py` (add `_swaps_snapshot()`, update `home()` and `tools_index()`)
- Create: `dashboard/templates/_swap_card.html`
- Modify: `dashboard/templates/index.html` (replace the four swap widgets with the include)
- Modify: `dashboard/templates/tools_index.html` (add the include)
- Create: `dashboard/tests/test_swap_card.py`

**Interfaces:**
- Produces: `dashboard.app._swaps_snapshot() -> Optional[Dict[str, Any]]`,
  `dashboard.app.SWAPS_DASHBOARD_URL` (str, `'http://127.0.0.1:8788'`).

- [ ] **Step 1: Write the failing test**

```python
# dashboard/tests/test_swap_card.py
"""Covers dashboard.app._swaps_snapshot() -- the file read that replaced the
four swap-DB queries home() used to run on every load. See
docs/superpowers/specs/2026-08-27-swaps-dashboard-split-design.md.
"""
import json

import pytest
from fastapi.testclient import TestClient

import dashboard.app as dashboard_app
from dashboard.app import app

pytestmark = pytest.mark.unit
client = TestClient(app)


def test_swaps_snapshot_returns_none_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_app, 'SWAPS_DASHBOARD_SNAPSHOT_PATH', str(tmp_path / 'missing.json'))
    assert dashboard_app._swaps_snapshot() is None


def test_swaps_snapshot_returns_none_on_malformed_json(monkeypatch, tmp_path):
    bad = tmp_path / 'overview_snapshot.json'
    bad.write_text('{not valid json', encoding='utf-8')
    monkeypatch.setattr(dashboard_app, 'SWAPS_DASHBOARD_SNAPSHOT_PATH', str(bad))
    assert dashboard_app._swaps_snapshot() is None


def test_swaps_snapshot_returns_parsed_dict(monkeypatch, tmp_path):
    good = tmp_path / 'overview_snapshot.json'
    payload = {'generated_at': '2026-08-27T12:00:00Z', 'stats': {'total_records': 5}}
    good.write_text(json.dumps(payload), encoding='utf-8')
    monkeypatch.setattr(dashboard_app, 'SWAPS_DASHBOARD_SNAPSHOT_PATH', str(good))
    assert dashboard_app._swaps_snapshot() == payload


def test_home_page_renders_without_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_app, 'SWAPS_DASHBOARD_SNAPSHOT_PATH', str(tmp_path / 'missing.json'))
    r = client.get('/')
    assert r.status_code == 200
    assert "hasn't been launched yet" in r.text


def test_tools_page_includes_swap_card(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_app, 'SWAPS_DASHBOARD_SNAPSHOT_PATH', str(tmp_path / 'missing.json'))
    r = client.get('/tools')
    assert r.status_code == 200
    assert 'swapLiveBadge' in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest dashboard/tests/test_swap_card.py -q`
Expected: FAIL (`AttributeError: module 'dashboard.app' has no attribute '_swaps_snapshot'`)

- [ ] **Step 3: Write minimal implementation**

Add to `dashboard/app.py`, near the top-level constants (after `DB_PATH = orchestrator.DB_PATH`):

```python
import json

SWAPS_DASHBOARD_URL = 'http://127.0.0.1:8788'
SWAPS_DASHBOARD_SNAPSHOT_PATH = os.path.join(ROOT, 'swaps_dashboard', 'cache', 'overview_snapshot.json')


def _swaps_snapshot() -> Optional[Dict[str, Any]]:
    """Read swaps_dashboard's periodic JSON snapshot -- a plain file read, no
    DB connection, no query. Returns None if the swaps dashboard has never
    run (or its cache is missing/corrupt); the template degrades to a
    link-only card in that case, same "never 500" convention as the rest of
    this file.
    """
    try:
        with open(SWAPS_DASHBOARD_SNAPSHOT_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
```

Replace the body of `home()` (`dashboard/app.py:645-672`):

```python
@app.get('/', response_class=HTMLResponse)
def home(request: Request):
    runs, runs_error = _orchestrator_runs(limit=20)

    return TEMPLATES.TemplateResponse(request, 'index.html', {
        'active': 'home',
        'swaps_snapshot': _swaps_snapshot(),
        'swaps_dashboard_url': SWAPS_DASHBOARD_URL,
        'runs': runs,
        'runs_error': runs_error,
        'run_kinds': RUN_KINDS,
        'db_path': DB_PATH,
        'default_timeout': orchestrator.DEFAULT_TIMEOUT_SEC,
        'shared_python': orchestrator.SHARED_PYTHON,
        'shared_python_ok': os.path.exists(orchestrator.SHARED_PYTHON),
        'suites': SUITE_LABELS,
    })
```

Replace `tools_index()` (`dashboard/app.py:1828-1836`):

```python
@app.get('/tools', response_class=HTMLResponse)
def tools_index(request: Request):
    contexts, contexts_error = _tools_contexts()
    return TEMPLATES.TemplateResponse(request, 'tools_index.html', {
        'active': 'tools',
        'tools': TOOLS,
        'contexts': contexts,
        'contexts_error': contexts_error,
        'swaps_snapshot': _swaps_snapshot(),
        'swaps_dashboard_url': SWAPS_DASHBOARD_URL,
    })
```

Delete `_database_stats()`, `_top_notional()`, `_ingestion_state()`, `_scrape_log()`
from `dashboard/app.py:247-323` — nothing calls them after the `home()` rewrite
above.

Create `dashboard/templates/_swap_card.html`:

```html
<div class="panel" id="swapCard">
  <header>
    <h2>Swap data</h2>
    <span class="note"><span id="swapLiveBadge" class="pill plain">checking&hellip;</span></span>
  </header>
  <div class="body">
    {% if swaps_snapshot %}
    <div class="cards">
      <div class="card">
        <div class="k">Swap trades</div>
        <div class="v">{{ swaps_snapshot.stats.total_records | num }}</div>
        <div class="x">rows in swap_trades</div>
      </div>
      <div class="card">
        <div class="k">Unique UPIs</div>
        <div class="v">{{ swaps_snapshot.stats.unique_upis | num }}</div>
        <div class="x">distinct instruments</div>
      </div>
      <div class="card">
        <div class="k">Date range</div>
        <div class="v" style="font-size:15px;">
          {{ swaps_snapshot.stats.earliest_date or '--' }}<span class="muted"> &rarr; </span>{{ swaps_snapshot.stats.latest_date or '--' }}
        </div>
        <div class="x">effective_date min/max</div>
      </div>
      <div class="card">
        <div class="k">Top product</div>
        <div class="v" style="font-size:15px;">
          {% if swaps_snapshot.top_products %}{{ swaps_snapshot.top_products[0].product or '(unnamed)' }}{% else %}--{% endif %}
        </div>
        <div class="x">
          {% if swaps_snapshot.top_products %}{{ swaps_snapshot.top_products[0].total_notional | usd }} notional{% else %}no swap trades yet{% endif %}
        </div>
      </div>
      <div class="card">
        <div class="k">Last scrape</div>
        <div class="v" style="font-size:15px;">
          {% if swaps_snapshot.last_scrape %}
            <span class="pill {{ (swaps_snapshot.last_scrape.status or 'plain') | lower }}">{{ swaps_snapshot.last_scrape.status or 'unknown' }}</span>
          {% else %}--{% endif %}
        </div>
        <div class="x">
          {% if swaps_snapshot.last_scrape %}{{ swaps_snapshot.last_scrape.scrape_date }}{% else %}no scrape_log entries{% endif %}
        </div>
      </div>
    </div>
    {% if swaps_snapshot.stats.by_regulator_asset_class %}
    <table style="margin-top:12px;">
      <thead><tr><th>regulator</th><th>asset_class</th><th class="num">records</th></tr></thead>
      <tbody>
      {% for r in (swaps_snapshot.stats.by_regulator_asset_class | sort(attribute='record_count', reverse=true))[:3] %}
        <tr>
          <td>{{ r.regulator }}</td>
          <td>{{ r.asset_class }}</td>
          <td class="num">{{ r.record_count | num }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
    {% endif %}
    <p class="small muted" style="margin:12px 0 0;">
      snapshot generated {{ swaps_snapshot.generated_at | ts }}
      &middot; <a href="{{ swaps_dashboard_url }}/swaps" target="_blank">Open swap browser &rarr;</a>
    </p>
    {% else %}
    <div class="empty">
      Swap dashboard hasn't been launched yet -- run <code>swaps_dashboard.bat</code> to browse swap trades.
    </div>
    <p class="small muted" style="margin:12px 0 0;">
      <a href="{{ swaps_dashboard_url }}/swaps" target="_blank">Open swap browser &rarr;</a>
    </p>
    {% endif %}
  </div>
</div>
<script>
(function () {
  var badge = document.getElementById('swapLiveBadge');
  if (!badge) return;
  var ctrl = new AbortController();
  var timer = setTimeout(function () { ctrl.abort(); }, 1500);
  fetch('{{ swaps_dashboard_url }}/health', {mode: 'no-cors', signal: ctrl.signal})
    .then(function () {
      clearTimeout(timer);
      badge.textContent = 'live';
      badge.className = 'pill ok';
    })
    .catch(function () {
      clearTimeout(timer);
      badge.textContent = 'not running';
      badge.className = 'pill plain';
    });
})();
</script>
```

Edit `dashboard/templates/index.html`: replace the `<div class="cards" ...>...</div>`
block (lines 14-50) plus the `stats_error` line above it (line 12), and the entire
"Top notional products" / "Records by regulator / asset class" `grid2` block
(lines 150-202), and the "Ingestion state" panel (lines 205-233), and the "Scrape
log" panel (lines 236-269), with a single include:

```html
{% include "_swap_card.html" %}
```

Placed where the old `<div class="cards" ...>` block was (right after the
`{{ db_path }}`/`{{ shared_python }}` `<p class="sub">`), and again nowhere else —
delete the four listed blocks entirely rather than leaving empty panels behind.

Edit `dashboard/templates/tools_index.html`: add `{% include "_swap_card.html" %}`
right after the closing `</div>` of the "How to use this" panel (after line 27),
before the "Installed tools" panel.

- [ ] **Step 4: Run test to verify it passes**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest dashboard/tests/ -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/app.py dashboard/templates/_swap_card.html dashboard/templates/index.html dashboard/templates/tools_index.html dashboard/tests/test_swap_card.py
git commit -m "feat(dashboard): show a snapshot-backed swap card on Overview and Tools"
```

---

### Task 8: Chart tab (embeds `chart_app`)

**Files:**
- Modify: `dashboard/app.py` (add `/chart` route)
- Create: `dashboard/templates/chart.html`
- Modify: `dashboard/templates/base.html` (nav: "Swap trades" → "Chart")
- Create: `dashboard/tests/test_chart_tab.py`

**Interfaces:**
- Produces: `GET /chart` on `dashboard.app.app`, `dashboard.app.CHART_APP_URL` (str,
  `'http://127.0.0.1:8791'`).

- [ ] **Step 1: Write the failing test**

```python
# dashboard/tests/test_chart_tab.py
"""Covers the /chart tab -- iframes the existing native chart_app (:8791).
See docs/superpowers/specs/2026-08-27-swaps-dashboard-split-design.md."""
from fastapi.testclient import TestClient

from dashboard.app import app

client = TestClient(app)


def test_chart_page_launches():
    r = client.get('/chart')
    assert r.status_code == 200
    assert 'http://127.0.0.1:8791' in r.text


def test_nav_no_longer_links_swaps_directly():
    r = client.get('/chart')
    assert r.status_code == 200
    assert 'href="/chart"' in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest dashboard/tests/test_chart_tab.py -q`
Expected: FAIL (404 on `/chart`)

- [ ] **Step 3: Write minimal implementation**

Add to `dashboard/app.py`, near `SWAPS_DASHBOARD_URL`:

```python
CHART_APP_URL = 'http://127.0.0.1:8791'
```

Add the route (anywhere among the other `@app.get` page routes, e.g. right after
`home()`):

```python
@app.get('/chart', response_class=HTMLResponse)
def chart(request: Request):
    return TEMPLATES.TemplateResponse(request, 'chart.html', {
        'active': 'chart',
        'chart_app_url': CHART_APP_URL,
    })
```

Create `dashboard/templates/chart.html`:

```html
{% extends "base.html" %}
{% block title %}Chart -- FinancialDevelopment Dashboard{% endblock %}

{% block content %}
<h1>Chart</h1>
<p class="sub">Live native chart app at <code>{{ chart_app_url }}</code></p>
<iframe
  src="{{ chart_app_url }}"
  id="chartFrame"
  style="width:100%; height:calc(100vh - 160px); border:1px solid var(--border); border-radius:12px; background:var(--panel);"
></iframe>
{% endblock %}

{% block scripts %}
<script>
// chart_app's own uvicorn start (kicked off headlessly by dashboard.bat) is
// async and can still be binding when this page first paints -- one-shot
// reload, not a polling loop, covers the common "still starting" case.
setTimeout(function () {
  var f = document.getElementById('chartFrame');
  if (f) f.src = f.src;
}, 3000);
</script>
{% endblock %}
```

Edit `dashboard/templates/base.html:205`, replace:

```html
    <a href="/swaps" class="{{ 'on' if active == 'swaps' else '' }}">Swap trades</a>
```

with:

```html
    <a href="/chart" class="{{ 'on' if active == 'chart' else '' }}">Chart</a>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest dashboard/tests/ -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/app.py dashboard/templates/chart.html dashboard/templates/base.html dashboard/tests/test_chart_tab.py
git commit -m "feat(dashboard): add a Chart tab embedding the native chart app"
```

---

### Task 9: `dashboard.bat`/`.sh` auto-launch `chart_app` headlessly

**Files:**
- Modify: `dashboard.bat`
- Modify: `dashboard.sh`

**Interfaces:**
- None (shell scripts).

- [ ] **Step 1: N/A (no automated test for launcher scripts — manual verification below)**

- [ ] **Step 2: N/A**

- [ ] **Step 3: Write the change**

Edit `dashboard.bat`. After the existing "already running on 8787" guard block
(the `netstat ... findstr /C:"127.0.0.1:8787" ...` block that currently ends with
`exit /b 0`) and before the "Give uvicorn a couple seconds to bind" comment, insert:

```bat
REM Also make sure the native chart app (chart_app, :8791) is up, since the
REM Chart tab iframes it -- headless (no browser tab of its own; the
REM dashboard's Chart tab is the tab that shows it). chart_app.bat run
REM directly still opens its own tab exactly as it does today.
netstat -ano | findstr /C:"127.0.0.1:8791" | findstr "LISTENING" >nul
if errorlevel 1 (
    start "" /min cmd /c ".venv\Scripts\python.exe -m uvicorn chart_app.server:app --host 127.0.0.1 --port 8791"
)
```

Edit `dashboard.sh`. After the existing "already running" guard block (the
`if (exec 3<>"/dev/tcp/127.0.0.1/${PORT}") ...` block) and before the
`( sleep 2; open_url ) &` line, insert:

```bash
# Also make sure the native chart app (chart_app, :8791) is up, since the
# Chart tab iframes it -- headless (no browser tab of its own; the
# dashboard's Chart tab is the tab that shows it). chart_app.sh run directly
# still opens its own tab exactly as it does today.
if ! (exec 3<>"/dev/tcp/127.0.0.1/8791") 2>/dev/null; then
    "$VENV_PYTHON" -m uvicorn chart_app.server:app --host 127.0.0.1 --port 8791 >/dev/null 2>&1 &
else
    exec 3<&- 2>/dev/null || true
    exec 3>&- 2>/dev/null || true
fi
```

- [ ] **Step 4: Manual verification**

Stop any running `chart_app`/`dashboard` processes. Run `dashboard.bat`. Expected:
`/` renders in well under a second; separately check (e.g.
`netstat -ano | findstr "127.0.0.1:8791"`) that a `uvicorn` process is now listening
on 8791 without any extra browser tab having opened for it. Open the Chart tab —
expected: the native chart renders within a few seconds (via the one-shot reload
if it raced the still-binding server). Run `dashboard.bat` a second time while the
first is still up — expected: the existing "dashboard already running" message,
and no second `chart_app` process spawned (the 8791 check sees it already listening).
Separately, run `chart_app.bat` directly — expected: unchanged behavior, opens its
own browser tab.

- [ ] **Step 5: Commit**

```bash
git add dashboard.bat dashboard.sh
git commit -m "feat(dashboard): auto-launch chart_app headlessly alongside the dashboard"
```

---

### Task 10: End-to-end manual verification

**Files:** none (verification only).

- [ ] **Step 1: Full boot timing check**

Kill any running `dashboard`, `swaps_dashboard`, or `chart_app` processes. Run
`dashboard.bat`. Time from launch to the Overview page fully rendering in the
browser. Expected: well under a second (previously ~4 minutes) — confirms the root
cause from the spec is fixed.

- [ ] **Step 2: Swap card behavior, cold**

On that same fresh Overview page (swaps_dashboard never launched this session):
confirm the swap card shows "Swap dashboard hasn't been launched yet" and the
`swapLiveBadge` settles on "not running" within ~1.5s. Check `/tools` shows the
same card.

- [ ] **Step 3: Swap card behavior, warm**

Run `swaps_dashboard.bat`. Wait a few seconds for its first snapshot write (happens
on startup, not just after 5 minutes — confirm `_snapshot_loop`'s first
`await asyncio.to_thread(_write_snapshot)` runs before the first `sleep`). Reload
the main dashboard's `/`. Expected: the card now shows real numbers, and
`swapLiveBadge` reads "live".

- [ ] **Step 4: Swap browser parity**

On `http://127.0.0.1:8788/swaps`, run a search/filter, sort a column, and page
through results — confirm it behaves identically to the old `/swaps` route.
Hit `http://127.0.0.1:8788/trades`, `/instruments/<some-upi>`,
`/analytics/cross-source-notional`, `/analytics/timeseries` directly — confirm all
return the same JSON shapes the old `dashboard/app.py` routes did (spot-check
against a note of what they returned before this migration, or against the moved
route code in Task 3 if no prior snapshot was taken).

- [ ] **Step 5: Chart tab**

With `chart_app` already auto-launched by `dashboard.bat`, open the Chart tab —
confirm the live chart renders. Stop `chart_app`'s process, reload the Chart tab —
confirm it degrades to the browser's own connection-error page inside the iframe
(no crash of the main dashboard page around it).

- [ ] **Step 6: Full test suite**

Run:
```
env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest dashboard/tests/ swaps_dashboard/tests/ -q
env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest -q
```
Expected: PASS on both (the second confirms nothing in the default `testpaths`
collection broke).

No commit for this task — it's verification only. If any check fails, fix the
underlying task and re-run this task's checklist from the top.
