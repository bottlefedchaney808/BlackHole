"""Covers swaps_dashboard.app's snapshot writer -- the JSON file
dashboard/app.py's Overview/Tools cards read instead of ever querying
swaps.db directly. See
docs/superpowers/specs/2026-08-27-swaps-dashboard-split-design.md.
"""
import json
import sqlite3

import pytest

import swaps_dashboard.app as swaps_app

pytestmark = pytest.mark.unit


def test_build_snapshot_degrades_gracefully_with_no_db(monkeypatch):
    monkeypatch.setattr(swaps_app, 'DB_PATH', '/nonexistent/swaps.db')
    # _top_notional() calls orchestrator.get_recent_swap_activity(), which reads
    # orchestrator's OWN module-level DB_PATH, not swaps_app.DB_PATH -- patching
    # only the latter leaves this call hitting the real swaps.db (CARL R2-F3).
    monkeypatch.setattr(swaps_app.orchestrator, 'get_recent_swap_activity', lambda limit=15: [])
    snapshot = swaps_app._build_snapshot()
    assert snapshot['stats'] == {
        'total_records': 0, 'unique_upis': 0, 'by_regulator_asset_class': [],
        'earliest_date': None, 'latest_date': None,
    }
    assert snapshot['stats_error'] is not None
    assert snapshot['top_products'] == []
    assert snapshot['ingestion'] == []
    assert snapshot['ingestion_error'] is not None
    assert snapshot['last_scrape'] is None
    assert 'generated_at' in snapshot


def test_write_snapshot_writes_valid_json(tmp_path, monkeypatch):
    target = tmp_path / 'cache' / 'overview_snapshot.json'
    monkeypatch.setattr(swaps_app, 'SNAPSHOT_PATH', str(target))
    monkeypatch.setattr(swaps_app, 'SNAPSHOT_DIR', str(tmp_path / 'cache'))
    monkeypatch.setattr(swaps_app, 'DB_PATH', '/nonexistent/swaps.db')
    monkeypatch.setattr(swaps_app.orchestrator, 'get_recent_swap_activity', lambda limit=15: [])

    swaps_app._write_snapshot()

    assert target.exists()
    data = json.loads(target.read_text(encoding='utf-8'))
    assert 'generated_at' in data
    assert data['stats']['total_records'] == 0


def test_write_snapshot_caps_top_products_at_five(tmp_path, monkeypatch):
    target = tmp_path / 'cache' / 'overview_snapshot.json'
    monkeypatch.setattr(swaps_app, 'SNAPSHOT_PATH', str(target))
    monkeypatch.setattr(swaps_app, 'SNAPSHOT_DIR', str(tmp_path / 'cache'))
    # _database_stats()/_ingestion_state()/_scrape_log() all key off swaps_app.DB_PATH
    # directly -- must be patched here too, or they hit the real swaps.db (CARL R2-F3).
    monkeypatch.setattr(swaps_app, 'DB_PATH', str(tmp_path / 'missing.db'))
    monkeypatch.setattr(
        swaps_app.orchestrator, 'get_recent_swap_activity',
        lambda limit=15: [{'product': f'p{i}', 'total_notional': i, 'trade_count': 1} for i in range(20)],
    )

    swaps_app._write_snapshot()

    data = json.loads(target.read_text(encoding='utf-8'))
    assert len(data['top_products']) == 5


def test_write_snapshot_includes_ingestion_rows(tmp_path, monkeypatch):
    """Regression test for CARL R1-F1: an earlier draft of this task dropped
    ingestion state from the snapshot entirely, contradicting the design
    spec's documented schema."""
    db_path = tmp_path / 'swaps.db'
    conn = sqlite3.connect(str(db_path))
    conn.execute('CREATE TABLE ingestion_state (regulator TEXT, asset_class TEXT, '
                 'last_cumulative_date TEXT, last_live_slice_id INTEGER, updated_at TEXT)')
    conn.execute("INSERT INTO ingestion_state VALUES ('SEC', 'EQ', '2026-08-20', 4, '2026-08-27T10:00:00Z')")
    conn.commit()
    conn.close()

    target = tmp_path / 'cache' / 'overview_snapshot.json'
    monkeypatch.setattr(swaps_app, 'SNAPSHOT_PATH', str(target))
    monkeypatch.setattr(swaps_app, 'SNAPSHOT_DIR', str(tmp_path / 'cache'))
    monkeypatch.setattr(swaps_app, 'DB_PATH', str(db_path))
    monkeypatch.setattr(
        swaps_app, 'SwapsLoader',
        lambda db: type('_L', (), {'get_state': staticmethod(
            lambda regulator, asset_class: {
                'regulator': regulator, 'asset_class': asset_class,
                'last_cumulative_date': '2026-08-20', 'last_live_slice_id': 4,
                'updated_at': '2026-08-27T10:00:00Z',
            })})(),
    )
    monkeypatch.setattr(swaps_app.orchestrator, 'get_recent_swap_activity', lambda limit=15: [])

    swaps_app._write_snapshot()

    data = json.loads(target.read_text(encoding='utf-8'))
    assert data['ingestion'] == [{
        'regulator': 'SEC', 'asset_class': 'EQ',
        'last_cumulative_date': '2026-08-20', 'last_live_slice_id': 4,
        'updated_at': '2026-08-27T10:00:00Z',
    }]
