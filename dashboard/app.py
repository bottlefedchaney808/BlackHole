"""dashboard/app.py -- local live web dashboard over the swaps DB + orchestrator.

This is a *view* over things that already exist; it does not re-implement any of
them:

  * swap data       -> swaps_query.SwapsQuery (get_database_stats,
                       top_notional_products via orchestrator.get_recent_swap_activity)
  * ingestion state -> db_loader.SwapsLoader.get_state / get_last_scrape_log
  * runs            -> orchestrator.build_context / run_suite / run_unified,
                       durably logged by orchestrator.log_run into orchestrator_runs

Run tracking is deliberately two-layer:

  * orchestrator_runs (SQLite) is the durable record. The dashboard inserts its
    own `dashboard:*` row per triggered run and updates it on completion; the
    orchestrator additionally writes its own `suite:*` / `unified` rows from
    inside log_run, so a single dashboard-triggered unified run shows up as one
    `dashboard:unified` row plus the four `suite:*` rows it fanned out to.
  * _RUNS (in-memory dict) is only for fast polling of in-flight runs, which is
    what /runs/{run_id} reads first. It is intentionally not persisted -- if the
    server restarts mid-run the DB row is the surviving truth.

Everything degrades to an empty state rather than a 500: a cold swaps.db, an
empty orchestrator_runs, or a suite that has never produced output all render.
"""
from __future__ import annotations

import csv
import glob
import json
import os
import sqlite3
import sys
import threading
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

# --------------------------------------------------------------------------
# paths / repo imports
# --------------------------------------------------------------------------

DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(DASHBOARD_DIR)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import orchestrator  # noqa: E402  (path is set immediately above)
from db_loader import SwapsLoader  # noqa: E402
from swaps_query import SwapsQuery  # noqa: E402

DB_PATH = orchestrator.DB_PATH
SUITE_ROOTS = orchestrator.SUITE_ROOTS

TEMPLATES = Jinja2Templates(directory=os.path.join(DASHBOARD_DIR, 'templates'))

app = FastAPI(title='FinancialDevelopment Dashboard')


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _iso_utc_now() -> str:
    return orchestrator._iso_utc_now()


def _db() -> Optional[sqlite3.Connection]:
    """Read connection with the same row_factory the rest of the repo uses."""
    if not os.path.exists(DB_PATH):
        return None
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


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


# --------------------------------------------------------------------------
# swap data reads
# --------------------------------------------------------------------------

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
    """Top notional products for the most recent effective_date present.

    Reuses orchestrator.get_recent_swap_activity, which already anchors on
    MAX(effective_date) instead of top_notional_products' yesterday default and
    normalizes the pandas DataFrame path to list[dict].
    """
    try:
        return orchestrator.get_recent_swap_activity(limit=15), None
    except Exception as e:
        return [], f'{type(e).__name__}: {e}'


def _ingestion_state() -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """One row per (regulator, asset_class) via SwapsLoader.get_state.

    get_state takes a pair, so the pairs themselves come from a distinct scan of
    ingestion_state and each is then fetched through the real loader API.
    """
    conn = _db()
    if conn is None:
        return [], f'swaps.db not found at {DB_PATH}'
    try:
        pairs = [(r['regulator'], r['asset_class']) for r in conn.execute(
            'SELECT regulator, asset_class FROM ingestion_state '
            'ORDER BY regulator, asset_class;')]
    except Exception as e:
        return [], f'{type(e).__name__}: {e}'
    finally:
        conn.close()

    loader = SwapsLoader(DB_PATH)
    rows: List[Dict[str, Any]] = []
    for regulator, asset_class in pairs:
        try:
            state = loader.get_state(regulator, asset_class)
        except Exception:
            state = None
        if state:
            rows.append(state)
    return rows, None


def _scrape_log(limit: int = 8) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]], Optional[str]]:
    """Last N scrape_log entries plus SwapsLoader.get_last_scrape_log()."""
    conn = _db()
    if conn is None:
        return [], None, f'swaps.db not found at {DB_PATH}'
    try:
        rows = [dict(r) for r in conn.execute(
            'SELECT * FROM scrape_log ORDER BY created_at DESC, id DESC LIMIT ?;',
            (limit,))]
    except Exception as e:
        return [], None, f'{type(e).__name__}: {e}'
    finally:
        conn.close()

    try:
        last = SwapsLoader(DB_PATH).get_last_scrape_log()
    except Exception:
        last = rows[0] if rows else None
    return rows, last, None


def _orchestrator_runs(limit: int = 20) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Last N orchestrator_runs rows, focus_json decoded for display."""
    conn = _db()
    if conn is None:
        return [], f'swaps.db not found at {DB_PATH}'
    try:
        raw = [dict(r) for r in conn.execute(
            'SELECT id, run_type, focus_json, started_at, completed_at, status '
            'FROM orchestrator_runs ORDER BY id DESC LIMIT ?;', (limit,))]
    except Exception as e:
        return [], f'{type(e).__name__}: {e}'
    finally:
        conn.close()

    for row in raw:
        focus: Dict[str, Any] = {}
        try:
            focus = json.loads(row.get('focus_json') or '{}') or {}
        except Exception:
            focus = {}
        row['focus'] = focus
        row['ticker'] = focus.get('ticker') or '--'
        bits = []
        if focus.get('expiration_date'):
            bits.append(str(focus['expiration_date']))
        if focus.get('option_type'):
            bits.append(str(focus['option_type']))
        if focus.get('strike') is not None:
            bits.append(f"K={focus['strike']}")
        row['focus_summary'] = ' '.join(bits) or '--'
        row['live'] = row['id'] in _RUNS
    return raw, None


# --------------------------------------------------------------------------
# run tracking
# --------------------------------------------------------------------------

_RUNS: Dict[Any, Dict[str, Any]] = {}
_RUNS_LOCK = threading.Lock()
_FALLBACK_SEQ = [0]

RUN_KINDS = sorted(SUITE_ROOTS) + ['unified']


def _insert_run_row(run_type: str, focus: Dict[str, Any], started_at: str) -> Optional[int]:
    """Open a `dashboard:*` orchestrator_runs row and return its id.

    Same table and column set as orchestrator.log_run, but written up-front with
    status='running' so an in-flight run is visible in the durable record too,
    and so the row id can serve as the polling run_id.
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO orchestrator_runs (
                    run_type, focus_json, started_at, completed_at, status, results_json
                ) VALUES (?, ?, ?, NULL, 'running', ?);
            """, (run_type, json.dumps(focus, default=str),
                  started_at, json.dumps({'state': 'running'})))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()
    except Exception as e:
        print(f'  [dashboard] WARNING: could not open run row: {e}', file=sys.stderr)
        return None


def _finish_run_row(run_id: Any, status: str, results: Any, completed_at: str) -> None:
    if not isinstance(run_id, int):
        return
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        try:
            conn.execute(
                'UPDATE orchestrator_runs SET status = ?, results_json = ?, '
                'completed_at = ? WHERE id = ?;',
                (status, json.dumps(results, default=str), completed_at, run_id))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f'  [dashboard] WARNING: could not close run row {run_id}: {e}',
              file=sys.stderr)


def _set_run(key: Any, **fields: Any) -> None:
    """Merge fields into the in-memory record for a run (fast-poll layer)."""
    with _RUNS_LOCK:
        entry = _RUNS.setdefault(key, {})
        entry.update(fields)


def _execute_run(run_id: Any, kind: str, focus: Dict[str, Any]) -> None:
    """Background worker. Sync on purpose: BackgroundTasks hands a `def` to the
    threadpool, and run_suite/run_unified are blocking subprocess drivers that
    would stall the event loop for up to the 1800s child timeout."""
    _set_run(run_id, status='running', started_at=_iso_utc_now())
    try:
        if kind == 'unified':
            # run_unified builds (and re-validates) the context itself.
            result = orchestrator.run_unified(focus)
            status = result.get('status', 'ok')
        else:
            context = orchestrator.build_context(focus)
            _set_run(run_id, output_dir=context.get('output_dir'),
                     orchestrator_run_id=context.get('run_id'))
            timeout = int(focus.get('timeout') or orchestrator.DEFAULT_TIMEOUT_SEC)
            result = orchestrator.run_suite(kind, context, timeout=timeout)
            status = 'error' if 'error' in result else 'ok'
    except Exception as e:
        result = {
            'error': f'{type(e).__name__}: {e}',
            'traceback': traceback.format_exc()[-4000:],
        }
        status = 'error'

    completed_at = _iso_utc_now()
    _set_run(run_id, status=status, result=result, completed_at=completed_at)
    _finish_run_row(run_id, status, result, completed_at)


def _focus_from_body(body: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
    """Build orchestrator's `focus` dict from a JSON/form body.

    Mirrors orchestrator._focus_from_args: expiry wins, otherwise target_years,
    because build_context requires expiration_date and/or target_years.
    """
    ticker = str(body.get('ticker') or '').strip().upper()
    if not ticker:
        return {}, 'ticker is required'

    focus: Dict[str, Any] = {
        'ticker': ticker,
        'option_type': str(body.get('option_type') or 'call').lower(),
    }
    if focus['option_type'] not in ('call', 'put'):
        return {}, "option_type must be 'call' or 'put'"

    strike = body.get('strike')
    if strike not in (None, ''):
        try:
            focus['strike'] = float(strike)
        except (TypeError, ValueError):
            return {}, f'strike must be numeric, got {strike!r}'
    else:
        focus['strike'] = None

    expiry = str(body.get('expiry') or body.get('expiration_date') or '').strip()
    if expiry:
        focus['expiration_date'] = expiry
    else:
        try:
            focus['target_years'] = float(body.get('target_years') or 0.25)
        except (TypeError, ValueError):
            return {}, 'target_years must be numeric'

    index = str(body.get('index') or body.get('index_ticker') or '').strip()
    if index:
        focus['index_ticker'] = index.upper()

    try:
        focus['timeout'] = int(body.get('timeout') or orchestrator.DEFAULT_TIMEOUT_SEC)
    except (TypeError, ValueError):
        return {}, 'timeout must be an integer'

    compile_pdf = body.get('compile_pdf')
    focus['compile_pdf'] = str(compile_pdf).lower() in ('1', 'true', 'on', 'yes')
    return focus, None


async def _parse_body(request: Request) -> Dict[str, Any]:
    """JSON or urlencoded, without pulling in python-multipart."""
    content_type = (request.headers.get('content-type') or '').lower()
    if 'application/json' in content_type:
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    raw = (await request.body()).decode('utf-8', 'replace').strip()
    if raw:
        if raw.startswith('{'):
            try:
                data = json.loads(raw)
                return data if isinstance(data, dict) else {}
            except Exception:
                pass
        return {k: v[0] for k, v in parse_qs(raw).items() if v}
    return {k: v for k, v in request.query_params.items()}


# --------------------------------------------------------------------------
# suite output discovery
# --------------------------------------------------------------------------

ORCH_OUTPUT = os.path.join(ROOT, 'orchestrator_output')

# Newest-file globs per suite, most specific first. Everything under a
# virtualenv or site-packages is filtered out afterwards -- Options_Suite and
# Vol_Suite each still carry their own .venv full of vendored CSVs named
# things like `comparisons.py` and `test_gamma.py`.
SUITE_OUTPUT_GLOBS: Dict[str, Dict[str, Any]] = {
    'options': {
        'label': 'Options_Suite',
        'globs': [
            os.path.join(SUITE_ROOTS['options'], 'comparison_*.csv'),
            os.path.join(ORCH_OUTPUT, '*', 'options_result.json'),
        ],
        'hint': 'newest comparison_*.csv in Options_Suite/',
    },
    'vol': {
        'label': 'Vol_Suite',
        'globs': [
            os.path.join(SUITE_ROOTS['vol'], 'outputs', '*', '*gamma*.csv'),
            os.path.join(SUITE_ROOTS['vol'], 'outputs', '*', '*vanna*.csv'),
            os.path.join(SUITE_ROOTS['vol'], '*gamma*.csv'),
            os.path.join(SUITE_ROOTS['vol'], '*vanna*.csv'),
            os.path.join(SUITE_ROOTS['vol'], 'outputs', '*', '*.csv'),
        ],
        'hint': 'newest gamma/vanna CSV in Vol_Suite/outputs/<run>/',
    },
    'var': {
        'label': 'VaR_Tools_Simulations',
        'globs': [
            os.path.join(ORCH_OUTPUT, '*', 'var_result.json'),
            os.path.join(SUITE_ROOTS['var'], 'outputs', '*.csv'),
            os.path.join(SUITE_ROOTS['var'], '*.csv'),
        ],
        'hint': 'newest var_result.json written to orchestrator_output/<run>/',
    },
    'sentiment': {
        'label': 'sentiment-scanner',
        'globs': [
            os.path.join(ORCH_OUTPUT, '*', 'sentiment_result.json'),
            os.path.join(SUITE_ROOTS['sentiment'], 'data', 'exports',
                         'highlighted_ticker_packs', '*.json'),
        ],
        'hint': 'newest sentiment_result.json / exported ticker pack',
    },
}

_EXCLUDED_PATH_BITS = (os.sep + '.venv' + os.sep, os.sep + 'site-packages' + os.sep,
                       os.sep + '__pycache__' + os.sep, os.sep + '.git' + os.sep)

MAX_TABLE_ROWS = 250
MAX_TABLE_COLS = 40


def _newest_matching(patterns: List[str]) -> Optional[str]:
    for pattern in patterns:
        candidates = [p for p in glob.glob(pattern)
                      if os.path.isfile(p)
                      and not any(bit in p for bit in _EXCLUDED_PATH_BITS)]
        if candidates:
            return max(candidates, key=os.path.getmtime)
    return None


def _read_csv_table(path: str) -> Dict[str, Any]:
    with open(path, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.reader(f)
        rows = []
        for i, row in enumerate(reader):
            rows.append(row[:MAX_TABLE_COLS])
            if i > MAX_TABLE_ROWS:
                break
    if not rows:
        return {'kind': 'empty', 'headers': [], 'rows': [], 'truncated': False}
    headers, body = rows[0], rows[1:]
    truncated = len(body) > MAX_TABLE_ROWS
    return {
        'kind': 'table',
        'headers': headers,
        'rows': body[:MAX_TABLE_ROWS],
        'truncated': truncated,
    }


def _read_json_view(path: str) -> Dict[str, Any]:
    with open(path, 'r', encoding='utf-8-sig') as f:
        payload = json.load(f)

    if isinstance(payload, list) and payload and all(isinstance(r, dict) for r in payload):
        headers: List[str] = []
        for record in payload[:MAX_TABLE_ROWS]:
            for key in record:
                if key not in headers:
                    headers.append(key)
        headers = headers[:MAX_TABLE_COLS]
        rows = [[_scalar(record.get(h)) for h in headers]
                for record in payload[:MAX_TABLE_ROWS]]
        return {'kind': 'table', 'headers': headers, 'rows': rows,
                'truncated': len(payload) > MAX_TABLE_ROWS}

    if isinstance(payload, dict):
        pairs = [(k, _scalar(v)) for k, v in payload.items()]
        return {'kind': 'pairs', 'pairs': pairs,
                'raw': json.dumps(payload, indent=2, default=str)[:20000]}

    return {'kind': 'raw', 'raw': json.dumps(payload, indent=2, default=str)[:20000]}


def _scalar(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, (dict, list)):
        text = json.dumps(value, default=str)
        return text if len(text) <= 400 else text[:400] + ' ...'
    return str(value)


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

@app.get('/', response_class=HTMLResponse)
def home(request: Request):
    stats, stats_error = _database_stats()
    top_products, top_error = _top_notional()
    ingestion, ingestion_error = _ingestion_state()
    scrapes, last_scrape, scrape_error = _scrape_log(limit=8)
    runs, runs_error = _orchestrator_runs(limit=20)

    return TEMPLATES.TemplateResponse(request, 'index.html', {
        'active': 'home',
        'stats': stats,
        'stats_error': stats_error,
        'top_products': top_products,
        'top_error': top_error,
        'ingestion': ingestion,
        'ingestion_error': ingestion_error,
        'scrapes': scrapes,
        'last_scrape': last_scrape,
        'scrape_error': scrape_error,
        'runs': runs,
        'runs_error': runs_error,
        'run_kinds': RUN_KINDS,
        'db_path': DB_PATH,
        'default_timeout': orchestrator.DEFAULT_TIMEOUT_SEC,
        'shared_python': orchestrator.SHARED_PYTHON,
        'shared_python_ok': os.path.exists(orchestrator.SHARED_PYTHON),
        'suites': SUITE_OUTPUT_GLOBS,
    })


@app.get('/swaps', response_class=HTMLResponse)
def swaps(request: Request,
          regulator: str = '',
          asset_class: str = '',
          page: int = 1,
          per_page: int = 50):
    page = max(1, int(page or 1))
    per_page = min(500, max(10, int(per_page or 50)))

    columns = ['dissemination_id', 'regulator', 'asset_class', 'action_type',
               'event_type', 'effective_date', 'expiration_date', 'cleared',
               'notional_amount_leg1', 'notional_currency_leg1', 'price',
               'underlying_asset_name', 'upi', 'company_name', 'upi_underlier_name',
               'ingested_at']
    notional_cols = {'notional_amount_leg1'}

    where, params = [], []
    if regulator:
        where.append('st.regulator = ?')
        params.append(regulator)
    if asset_class:
        where.append('st.asset_class = ?')
        params.append(asset_class)
    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    rows: List[Dict[str, Any]] = []
    total = 0
    regulators: List[str] = []
    asset_classes: List[str] = []
    error: Optional[str] = None

    conn = _db()
    if conn is None:
        error = f'swaps.db not found at {DB_PATH}'
    else:
        try:
            select_cols = ", ".join(
                'ur.company_name' if c == 'company_name' else f'st.{c}'
                for c in columns
            )
            total = conn.execute(
                f'SELECT COUNT(*) AS n FROM swap_trades st {where_sql};',
                params).fetchone()['n']
            rows = [dict(r) for r in conn.execute(
                f'SELECT {select_cols} FROM swap_trades st '
                'LEFT JOIN upi_reference ur ON ur.upi = st.upi '
                f'{where_sql} '
                'ORDER BY st.ingested_at DESC, st.dissemination_id DESC LIMIT ? OFFSET ?;',
                params + [per_page, (page - 1) * per_page])]
            regulators = [r['regulator'] for r in conn.execute(
                'SELECT DISTINCT regulator FROM swap_trades '
                'WHERE regulator IS NOT NULL ORDER BY regulator;')]
            asset_classes = [r['asset_class'] for r in conn.execute(
                'SELECT DISTINCT asset_class FROM swap_trades '
                'WHERE asset_class IS NOT NULL ORDER BY asset_class;')]
        except Exception as e:
            error = f'{type(e).__name__}: {e}'
        finally:
            conn.close()

    pages = max(1, (total + per_page - 1) // per_page) if total else 1
    return TEMPLATES.TemplateResponse(request, 'swaps.html', {
        'active': 'swaps',
        'columns': columns,
        'notional_cols': notional_cols,
        'rows': rows,
        'total': total,
        'page': page,
        'pages': pages,
        'per_page': per_page,
        'regulator': regulator,
        'asset_class': asset_class,
        'regulators': regulators,
        'asset_classes': asset_classes,
        'error': error,
    })


@app.post('/run/{suite_or_unified}')
async def trigger_run(suite_or_unified: str, request: Request,
                      background_tasks: BackgroundTasks):
    """Kick off run_suite(<name>, ...) or run_unified(...) in the background.

    Returns immediately with a run_id -- these take up to
    orchestrator.DEFAULT_TIMEOUT_SEC (1800s) per child, so the response cannot
    wait on the result. Poll GET /runs/{run_id}.
    """
    kind = suite_or_unified.strip().lower()
    if kind not in RUN_KINDS:
        return JSONResponse(status_code=400, content={
            'error': f'unknown target {suite_or_unified!r}',
            'expected': RUN_KINDS,
        })

    body = await _parse_body(request)
    focus, error = _focus_from_body(body)
    if error:
        return JSONResponse(status_code=400, content={'error': error})

    if not os.path.exists(orchestrator.SHARED_PYTHON):
        return JSONResponse(status_code=503, content={
            'error': f'shared interpreter not found: {orchestrator.SHARED_PYTHON}'})

    started_at = _iso_utc_now()
    run_type = 'dashboard:unified' if kind == 'unified' else f'dashboard:suite:{kind}'
    run_id: Any = _insert_run_row(run_type, focus, started_at)
    if run_id is None:
        # DB unavailable -- still runnable, just not durably recorded.
        with _RUNS_LOCK:
            _FALLBACK_SEQ[0] += 1
            run_id = f'mem-{_FALLBACK_SEQ[0]}'

    _set_run(run_id, run_id=run_id, kind=kind, run_type=run_type, focus=focus,
             status='queued', queued_at=started_at, started_at=None,
             completed_at=None, result=None, persisted=isinstance(run_id, int))

    background_tasks.add_task(_execute_run, run_id, kind, focus)

    return JSONResponse(status_code=202, content={
        'run_id': run_id,
        'kind': kind,
        'run_type': run_type,
        'status': 'queued',
        'focus': focus,
        'poll': f'/runs/{run_id}',
        'persisted': isinstance(run_id, int),
    })


@app.get('/runs/{run_id}')
def run_status(run_id: str):
    """In-memory state first (in-flight runs), orchestrator_runs second."""
    key: Any = int(run_id) if run_id.lstrip('-').isdigit() else run_id
    with _RUNS_LOCK:
        live = dict(_RUNS.get(key, {})) if key in _RUNS else None

    row: Optional[Dict[str, Any]] = None
    if isinstance(key, int):
        conn = _db()
        if conn is not None:
            try:
                found = conn.execute(
                    'SELECT id, run_type, focus_json, started_at, completed_at, '
                    'status, results_json FROM orchestrator_runs WHERE id = ?;',
                    (key,)).fetchone()
                row = dict(found) if found else None
            except Exception:
                row = None
            finally:
                conn.close()

    if live is None and row is None:
        return JSONResponse(status_code=404, content={'error': f'no run {run_id!r}'})

    payload: Dict[str, Any] = {'run_id': key, 'source': 'memory' if live else 'db'}

    if row:
        for field in ('run_type', 'started_at', 'completed_at', 'status'):
            payload[field] = row.get(field)
        for src, dst in (('focus_json', 'focus'), ('results_json', 'result')):
            try:
                payload[dst] = json.loads(row.get(src) or 'null')
            except Exception:
                payload[dst] = row.get(src)

    if live:
        payload['status'] = live.get('status', payload.get('status'))
        payload['kind'] = live.get('kind')
        payload['focus'] = live.get('focus', payload.get('focus'))
        payload['queued_at'] = live.get('queued_at')
        payload['started_at'] = live.get('started_at') or payload.get('started_at')
        payload['completed_at'] = live.get('completed_at') or payload.get('completed_at')
        if live.get('output_dir'):
            payload['output_dir'] = live['output_dir']
        if live.get('result') is not None:
            payload['result'] = live['result']

    payload['done'] = payload.get('status') not in ('queued', 'running')
    return JSONResponse(content=json.loads(json.dumps(payload, default=str)))


@app.get('/suites/{suite}', response_class=HTMLResponse)
def suite_output(request: Request, suite: str):
    key = suite.strip().lower()
    spec = SUITE_OUTPUT_GLOBS.get(key)
    if spec is None:
        return TEMPLATES.TemplateResponse(request, 'suite.html', {
            'active': 'suites', 'suite': key, 'spec': None,
            'path': None, 'view': None,
            'error': f'unknown suite {suite!r}; expected one of '
                     f'{", ".join(sorted(SUITE_OUTPUT_GLOBS))}',
            'suites': SUITE_OUTPUT_GLOBS,
        }, status_code=404)

    path = _newest_matching(spec['globs'])
    view: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    modified: Optional[str] = None

    if path:
        try:
            modified = datetime.fromtimestamp(
                os.path.getmtime(path), tz=timezone.utc).isoformat()
            view = (_read_json_view(path) if path.lower().endswith('.json')
                    else _read_csv_table(path))
        except Exception as e:
            error = f'could not parse {os.path.basename(path)}: {type(e).__name__}: {e}'

    return TEMPLATES.TemplateResponse(request, 'suite.html', {
        'active': 'suites',
        'suite': key,
        'spec': spec,
        'path': path,
        'rel_path': os.path.relpath(path, ROOT) if path else None,
        'modified': modified,
        'view': view,
        'error': error,
        'suites': SUITE_OUTPUT_GLOBS,
    })


@app.get('/health')
def health():
    return {
        'ok': True,
        'db_path': DB_PATH,
        'db_exists': os.path.exists(DB_PATH),
        'shared_python': orchestrator.SHARED_PYTHON,
        'shared_python_exists': os.path.exists(orchestrator.SHARED_PYTHON),
        'in_flight': [k for k, v in _RUNS.items()
                      if v.get('status') in ('queued', 'running')],
    }
