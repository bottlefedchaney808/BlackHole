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
