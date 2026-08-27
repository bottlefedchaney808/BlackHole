"""test_swaps_options_cache.py

Covers swaps_dashboard.app._get_swaps_filter_options: the in-process, 60s-TTL cache
for the /swaps route's regulator/asset_class filter-dropdown option lists.

Context: on the production 342GB swaps.db, `SELECT DISTINCT regulator` /
`SELECT DISTINCT asset_class FROM swap_trades` were being re-run on every
/swaps request and were the remaining source of the route's 15s timeout after
migration 006 fixed search_trades() itself. These tests use a tiny in-memory
sqlite3 connection (no real DB needed) to verify: the cache actually avoids
re-querying within the TTL, it expires after the TTL, and it's keyed per
DB path so unrelated paths don't share stale data.
"""
import sqlite3

import pytest

import swaps_dashboard.app as dashboard_app

pytestmark = pytest.mark.unit


def _make_conn():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE swap_trades (regulator TEXT, asset_class TEXT)')
    conn.executemany(
        'INSERT INTO swap_trades (regulator, asset_class) VALUES (?, ?)',
        [('SEC', 'EQ'), ('CFTC', 'EQ'), ('CFTC', 'IR')],
    )
    conn.commit()
    return conn


@pytest.fixture(autouse=True)
def _isolate_cache():
    saved = dict(dashboard_app._swaps_options_cache)
    dashboard_app._swaps_options_cache.clear()
    yield
    dashboard_app._swaps_options_cache.clear()
    dashboard_app._swaps_options_cache.update(saved)


def test_returns_distinct_sorted_values():
    conn = _make_conn()
    regulators, asset_classes = dashboard_app._get_swaps_filter_options(conn, 'db-a')
    assert regulators == ['CFTC', 'SEC']
    assert asset_classes == ['EQ', 'IR']


class _ExecuteSpyingConnProxy:
    """Wraps a real sqlite3.Connection and records .execute() calls.

    sqlite3.Connection.execute is a C-level method and can't be monkeypatched
    directly (read-only type slot), so this proxies attribute access instead
    and only intercepts execute().
    """

    def __init__(self, real_conn):
        self._real_conn = real_conn
        self.calls = []

    def execute(self, sql, *args, **kwargs):
        self.calls.append(sql)
        return self._real_conn.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real_conn, name)


def test_second_call_within_ttl_does_not_requery():
    conn = _make_conn()
    dashboard_app._get_swaps_filter_options(conn, 'db-a')

    spy = _ExecuteSpyingConnProxy(conn)

    regulators, asset_classes = dashboard_app._get_swaps_filter_options(spy, 'db-a')
    assert regulators == ['CFTC', 'SEC']
    assert asset_classes == ['EQ', 'IR']
    assert spy.calls == []  # served entirely from cache, no SQL re-executed


def test_expires_after_ttl(monkeypatch):
    conn = _make_conn()
    dashboard_app._get_swaps_filter_options(conn, 'db-a')

    # Force the cached entry to look stale without sleeping in the test.
    cached_at, regulators, asset_classes = dashboard_app._swaps_options_cache['db-a']
    stale_at = cached_at - (dashboard_app._SWAPS_OPTIONS_CACHE_TTL_SEC + 1)
    dashboard_app._swaps_options_cache['db-a'] = (stale_at, regulators, asset_classes)

    conn.execute('INSERT INTO swap_trades (regulator, asset_class) VALUES (?, ?)', ('SEC', 'FX'))
    conn.commit()

    regulators, asset_classes = dashboard_app._get_swaps_filter_options(conn, 'db-a')
    assert asset_classes == ['EQ', 'FX', 'IR']


def test_cache_is_keyed_per_db_path():
    conn_a = _make_conn()
    conn_b = sqlite3.connect(':memory:')
    conn_b.row_factory = sqlite3.Row
    conn_b.execute('CREATE TABLE swap_trades (regulator TEXT, asset_class TEXT)')
    conn_b.execute("INSERT INTO swap_trades (regulator, asset_class) VALUES ('SEC', 'FX')")
    conn_b.commit()

    regs_a, _ = dashboard_app._get_swaps_filter_options(conn_a, 'db-a')
    regs_b, classes_b = dashboard_app._get_swaps_filter_options(conn_b, 'db-b')

    assert regs_a == ['CFTC', 'SEC']
    assert regs_b == ['SEC']
    assert classes_b == ['FX']
