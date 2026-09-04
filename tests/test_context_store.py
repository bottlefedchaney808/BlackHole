"""Tests for shared/context_store.py -- the Context Store implementation."""

import time

import pytest

from shared.context_store import ContextStore, ContextStoreError, Scope


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "test_context.db"
    with ContextStore(db) as s:
        yield s


def test_put_and_get(store):
    store.put({"ticker": "spy"}, "vol_stats", {"value": 0.25}, "dealer_exposure")
    result = store.get({"ticker": "spy"}, "vol_stats")
    assert result == {"value": 0.25}


def test_get_missing_returns_none(store):
    assert store.get({"ticker": "spy"}, "vol_stats") is None


def test_overwrite_updates_value(store):
    store.put({"ticker": "spy"}, "vol_stats", {"value": 0.25}, "dealer_exposure")
    store.put({"ticker": "spy"}, "vol_stats", {"value": 0.30}, "dealer_exposure")
    assert store.get({"ticker": "spy"}, "vol_stats") == {"value": 0.30}


def test_scope_key_normalization(store):
    store.put({"ticker": " SPY ", "expiry": "2026-10-16"}, "vol_stats", 1, "vol")
    assert store.get({"ticker": "spy", "expiry": "2026-10-16"}, "vol_stats") == 1


def test_basket_scope(store):
    store.put({"basket": ["spy", "qqq"]}, "corr_matrix", [[1, 0.5], [0.5, 1]], "corr_sim")
    assert store.get({"basket": ["SPY", "QQQ"]}, "corr_matrix") == [[1, 0.5], [0.5, 1]]


def test_basket_scope_different_order(store):
    store.put({"basket": ["spy", "qqq", "iwm"]}, "corr_matrix", "A", "corr_sim")
    assert store.get({"basket": ["iwm", "SPY", "qqq"]}, "corr_matrix") == "A"


def test_basket_and_ticker_mutually_exclusive(store):
    with pytest.raises(ContextStoreError):
        Scope.from_dict({"ticker": "spy", "basket": ["spy", "qqq"]})


def test_max_age_s_filters_stale(store):
    store.put({"ticker": "spy"}, "vol_stats", 0.25, "vol")
    time.sleep(0.1)
    assert store.get({"ticker": "spy"}, "vol_stats", max_age_s=1) == 0.25
    assert store.get({"ticker": "spy"}, "vol_stats", max_age_s=0) is None


def test_describe_returns_provenance(store):
    store.put({"ticker": "spy"}, "vol_stats", 0.25, "dealer_exposure")
    rows = store.describe({"ticker": "spy"})
    assert len(rows) == 1
    assert rows[0]["key"] == "vol_stats"
    assert rows[0]["source_slug"] == "dealer_exposure"
    assert "age_s" in rows[0]


def test_describe_all_scopes(store):
    store.put({"ticker": "spy"}, "vol_stats", 0.25, "vol")
    store.put({"basket": ["spy", "qqq"]}, "corr_matrix", "x", "corr")
    rows = store.describe()
    assert len(rows) == 2


def test_audit_trail_written(store):
    store.put({"ticker": "spy"}, "vol_stats", {"a": 1}, "vol")
    store.put({"ticker": "spy"}, "vol_stats", {"a": 2}, "vol")
    with store._pool().get_connection_context() as conn:
        rows = conn.execute(
            "SELECT before_hash, after_hash, changed_keys FROM context_store_audit "
            "WHERE scope_key = ? AND entry_key = ? ORDER BY id",
            ("ticker:SPY", "vol_stats"),
        ).fetchall()
    assert len(rows) >= 1
    # The second put should have a before_hash equal to the first put's after_hash.


def test_arbitrary_object_serializes_via_str(store):
    store.put({"ticker": "spy"}, "bad", object(), "vol")
    result = store.get({"ticker": "spy"}, "bad")
    assert "object" in str(result)


def test_connection_pool_wal_mode(tmp_path):
    db = tmp_path / "wal.db"
    with ContextStore(db) as s, s._pool().get_connection_context() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
