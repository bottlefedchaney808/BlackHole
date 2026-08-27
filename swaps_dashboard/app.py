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
from datetime import datetime, timedelta, timezone
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

import contextlib  # noqa: E402  (mid-file import, same convention Task 1's bootstrap already uses)


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    task = asyncio.create_task(_snapshot_loop())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(title='Swaps Dashboard', lifespan=_lifespan)


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
from db_loader import SwapsLoader  # noqa: E402


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


# --------------------------------------------------------------------------
# background overview snapshot writer
# --------------------------------------------------------------------------
import asyncio
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


def _ingestion_state() -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """One row per (regulator, asset_class) via SwapsLoader.get_state.

    Moved from dashboard/app.py's _ingestion_state() (dashboard/app.py:274-301)
    unchanged -- it's the only remaining visibility into whether the DTCC live
    poller (run_scheduler.bat) is advancing per regulator/asset_class pair, so
    it belongs in the snapshot even though the Overview card only surfaces a
    summary of it (design spec's documented schema includes the full list;
    CARL R1-F1 caught this being dropped from an earlier draft of this task).
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


def _scrape_log() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """SwapsLoader.get_last_scrape_log(), or None plus the error text.

    This is a deliberately narrower version of dashboard/app.py's original
    _scrape_log(limit=8) (dashboard/app.py:304-322), which also returned the
    last N raw scrape_log rows for a full-history table. That table isn't
    reconstructed anywhere after the split -- it was part of the Overview
    widget set the user explicitly approved consolidating into one lightweight
    card (see design spec's "Overview page" brainstorming decision), and the
    card only ever needed the single latest entry, same as the original
    "Last scrape" card widget did. If a full scrape-log history view turns out
    to be wanted later, it belongs as a small new route in swaps_dashboard,
    not smuggled back into this helper. No `limit` parameter here (CARL R2-F10:
    an earlier draft kept `limit: int = 8` as a vestige of the original
    signature even though this version's body never reads it).
    """
    if not os.path.exists(DB_PATH):
        return None, f'swaps.db not found at {DB_PATH}'
    try:
        return SwapsLoader(DB_PATH).get_last_scrape_log(), None
    except Exception as e:
        return None, f'{type(e).__name__}: {e}'


def _build_snapshot() -> Dict[str, Any]:
    stats, stats_error = _database_stats()
    top_products, top_error = _top_notional()
    ingestion, ingestion_error = _ingestion_state()
    last_scrape, scrape_error = _scrape_log()
    return {
        'generated_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'stats': stats,
        'stats_error': stats_error,
        'top_products': top_products[:5],
        'top_error': top_error,
        'ingestion': ingestion,
        'ingestion_error': ingestion_error,
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
