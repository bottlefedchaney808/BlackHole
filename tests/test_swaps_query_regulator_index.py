"""Regression tests for the regulator-filtered first-page query plan.

Background: on the real 342GB production swaps.db, `search_trades()`'s
default listing (sort_by='ingested_at', no filters) was already fast --
`idx_swap_trades_ingested_at` covers it directly. But adding the regulator
filter (the dashboard's most common filter -- only SEC/CFTC exist) made
SQLite prefer `idx_swap_trades_regulator_asset` for the WHERE clause and
then fall back to `USE TEMP B-TREE FOR ORDER BY` to satisfy the sort --
unbounded work over every matching row regardless of LIMIT/OFFSET. That's
what made cold `/swaps?regulator=SEC` requests exceed 180s even after the
SEARCH_COUNT_CAP bounded-count fix, since that fix only bounded the COUNT
query, not the row SELECT.

Migration 006 adds `idx_swap_trades_regulator_ingested(regulator,
ingested_at DESC, dissemination_id DESC)`, a covering index for exactly
that filter+sort shape. These tests assert the query plan no longer needs
a temp B-tree once the migration is applied, and that search_trades()'s
results/counts are unaffected by the new index (same rows, same order).
"""
import os
import sqlite3
import tempfile

import pytest

from setup_db import migrate
from swaps_query import SwapsQuery

REGULATORS = ("SEC", "CFTC")
ASSET_CLASSES = ("RATES", "CREDIT", "EQUITY")


@pytest.fixture
def swaps_db(tmp_path):
    db_path = str(tmp_path / "swaps_test.db")
    migrate(db_path)

    conn = sqlite3.connect(db_path)
    rows = []
    for i in range(400):
        rows.append((
            f"D{i:06d}",
            REGULATORS[i % len(REGULATORS)],
            ASSET_CLASSES[i % len(ASSET_CLASSES)],
            "New",
            f"2026-{(i % 12) + 1:02d}-{(i % 27) + 1:02d}T00:00:00Z",
            "{}",
            f"2026-{(i % 12) + 1:02d}-{(i % 27) + 1:02d} 00:00:00",
        ))
    conn.executemany(
        """
        INSERT INTO swap_trades
            (dissemination_id, regulator, asset_class, action_type,
             effective_date, source_file, raw_json, ingested_at)
        VALUES (?, ?, ?, ?, ?, 'test', ?, ?)
        """.replace("raw_json, ingested_at", "ingested_at")
        if False else
        """
        INSERT INTO swap_trades
            (dissemination_id, regulator, asset_class, action_type,
             effective_date, source_file, raw_json, ingested_at)
        VALUES (?, ?, ?, ?, ?, 'test', '{}', ?)
        """,
        [(r[0], r[1], r[2], r[3], r[4], r[6]) for r in rows],
    )
    conn.commit()
    conn.execute("ANALYZE;")
    conn.commit()
    conn.close()
    return db_path


@pytest.mark.unit
def test_migration_006_creates_regulator_ingested_index(swaps_db):
    conn = sqlite3.connect(swaps_db)
    try:
        names = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='swap_trades';"
            )
        }
    finally:
        conn.close()
    assert "idx_swap_trades_regulator_ingested" in names


@pytest.mark.unit
def test_regulator_filtered_default_sort_avoids_temp_btree(swaps_db):
    conn = sqlite3.connect(swaps_db)
    try:
        plan = conn.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT dissemination_id FROM swap_trades
            WHERE regulator = ?
            ORDER BY ingested_at DESC, dissemination_id DESC
            LIMIT 50 OFFSET 0;
            """,
            ("SEC",),
        ).fetchall()
    finally:
        conn.close()

    detail = " | ".join(row[3] for row in plan)
    assert "B-TREE" not in detail.upper(), (
        f"regulator-filtered default-sort query still needs a sort step: {detail}"
    )
    assert "idx_swap_trades_regulator_ingested" in detail


@pytest.mark.unit
def test_unfiltered_default_sort_still_uses_ingested_at_index(swaps_db):
    """The pre-existing, already-fast unfiltered path must not regress."""
    conn = sqlite3.connect(swaps_db)
    try:
        plan = conn.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT dissemination_id FROM swap_trades
            ORDER BY ingested_at DESC, dissemination_id DESC
            LIMIT 50 OFFSET 0;
            """
        ).fetchall()
    finally:
        conn.close()

    detail = " | ".join(row[3] for row in plan)
    assert "B-TREE" not in detail.upper()
    assert "idx_swap_trades_ingested_at" in detail


@pytest.mark.unit
def test_search_trades_regulator_filter_results_unaffected(swaps_db):
    sq = SwapsQuery(db_path=swaps_db, use_pool=False)
    result = sq.search_trades(regulator="SEC", page=1, per_page=10)

    assert result["count_is_exact"] is True
    assert result["total"] == 200  # half of the 400 synthetic rows are SEC
    assert len(result["rows"]) == 10
    assert all(row["regulator"] == "SEC" for row in result["rows"])

    # Default sort is ingested_at DESC -- rows must come back in that order.
    timestamps = [row["ingested_at"] for row in result["rows"]]
    assert timestamps == sorted(timestamps, reverse=True)


@pytest.mark.unit
def test_search_trades_regulator_plus_asset_class_filter(swaps_db):
    sq = SwapsQuery(db_path=swaps_db, use_pool=False)
    result = sq.search_trades(
        regulator="CFTC", asset_class="RATES", page=1, per_page=50,
    )

    assert result["count_is_exact"] is True
    assert all(
        row["regulator"] == "CFTC" and row["asset_class"] == "RATES"
        for row in result["rows"]
    )
