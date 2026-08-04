"""test_quant_alerts_view.py

Covers Task 15 of docs/superpowers/plans/2026-08-01-quant-console.md:
`GET /quant` gaining an `alerts` field (unacknowledged `quant_alerts` rows,
most recent first) plus a dismissible banner, and the new
`POST /alerts/{id}/ack` endpoint (sets `acknowledged=1`, does not delete the
row -- history is kept for later inspection).

DB isolation: `dashboard.app.DB_PATH` is a module-level constant read at
call time by every helper that touches it (`_fetch_pending_alerts`, the ack
route, etc., same pattern as the pre-existing `_insert_run_row`) -- so
`monkeypatch.setattr(dashboard_app, 'DB_PATH', <tmp db>)` redirects it for
the duration of a test without touching the real `swaps.db`. This matters:
test_dispatch.py/test_dispatch_poll.py were found (during the auth-removal
work this task follows) to have been writing real rows into the live
production DB for their entire history via this exact same unguarded
module-level `DB_PATH`; every test here monkeypatches it from the start
rather than repeat that mistake.
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from setup_db import migrate  # noqa: E402
import dashboard.app as dashboard_app  # noqa: E402

pytestmark = pytest.mark.unit

client = TestClient(dashboard_app.app)


def _db(tmp_path):
    db_path = str(tmp_path / "test.db")
    migrate(db_path)
    return db_path


def _insert_alert(db_path, run_id="run-1", ticker="AAPL",
                   condition="warnings_growing:vol", detail="vol: warnings grew",
                   created_at_utc="2026-08-01T00:00:00Z", acknowledged=0):
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO quant_alerts "
            "(run_id, ticker, condition, detail, created_at_utc, acknowledged) "
            "VALUES (?, ?, ?, ?, ?, ?);",
            (run_id, ticker, condition, detail, created_at_utc, acknowledged),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    db_path = _db(tmp_path)
    monkeypatch.setattr(dashboard_app, 'DB_PATH', db_path)
    yield db_path


class TestQuantRouteAlertsBanner:
    def test_no_banner_when_no_pending_alerts(self, _isolate_db):
        resp = client.get('/quant')
        assert resp.status_code == 200
        assert 'data-role="alert-banner"' not in resp.text

    def test_pending_alert_rendered_in_banner(self, _isolate_db):
        _insert_alert(_isolate_db, ticker="ZZZZ", detail="vol: warnings grew from 1 to 2")
        resp = client.get('/quant')
        assert resp.status_code == 200
        assert 'data-role="alert-banner"' in resp.text
        assert 'ZZZZ' in resp.text
        assert 'vol: warnings grew from 1 to 2' in resp.text

    def test_acknowledged_alert_not_rendered(self, _isolate_db):
        _insert_alert(_isolate_db, ticker="ZZZZ", acknowledged=1)
        resp = client.get('/quant')
        assert 'data-role="alert-banner"' not in resp.text
        assert 'ZZZZ' not in resp.text

    def test_multiple_pending_alerts_all_rendered_most_recent_first(self, _isolate_db):
        id1 = _insert_alert(_isolate_db, ticker="AAA", created_at_utc="2026-08-01T00:00:00Z")
        id2 = _insert_alert(_isolate_db, ticker="BBB", created_at_utc="2026-08-02T00:00:00Z")
        resp = client.get('/quant')
        html = resp.text
        assert f'data-alert-id="{id2}"' in html
        assert f'data-alert-id="{id1}"' in html
        # BBB (newer) must appear before AAA (older).
        assert html.index('BBB') < html.index('AAA')

    def test_no_quant_alerts_table_degrades_to_no_banner_not_500(self, tmp_path, monkeypatch):
        # A DB with no migrations applied at all -- quant_alerts doesn't exist.
        bare_db = str(tmp_path / "bare.db")
        sqlite3.connect(bare_db).close()
        monkeypatch.setattr(dashboard_app, 'DB_PATH', bare_db)

        resp = client.get('/quant')
        assert resp.status_code == 200
        assert 'data-role="alert-banner"' not in resp.text


class TestAlertStatusTimestamp:
    def test_last_checked_timestamp_visible_when_status_file_present(self, _isolate_db, tmp_path, monkeypatch):
        output_root = tmp_path / "orchestrator_output"
        output_root.mkdir()
        (output_root / ".quant_alert_status.json").write_text(
            json.dumps({"last_checked_utc": "2026-08-03T12:00:00Z", "found": 0}),
            encoding="utf-8",
        )
        monkeypatch.setattr(dashboard_app, 'DB_PATH', str(tmp_path / "test.db"))

        resp = client.get('/quant')
        assert '2026-08-03T12:00:00Z' in resp.text

    def test_no_status_file_does_not_error(self, _isolate_db):
        resp = client.get('/quant')
        assert resp.status_code == 200


class TestAckEndpoint:
    def test_ack_sets_acknowledged_and_removes_from_next_render(self, _isolate_db):
        alert_id = _insert_alert(_isolate_db, ticker="ZZZZ")
        assert 'ZZZZ' in client.get('/quant').text

        resp = client.post(f'/alerts/{alert_id}/ack')
        assert resp.status_code == 200

        assert 'ZZZZ' not in client.get('/quant').text

    def test_ack_leaves_db_row_intact(self, _isolate_db):
        alert_id = _insert_alert(_isolate_db, ticker="ZZZZ")
        client.post(f'/alerts/{alert_id}/ack')

        conn = sqlite3.connect(_isolate_db)
        try:
            row = conn.execute(
                "SELECT acknowledged FROM quant_alerts WHERE id = ?", (alert_id,)
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row[0] == 1

    def test_ack_unknown_id_returns_404(self, _isolate_db):
        resp = client.post('/alerts/999999/ack')
        assert resp.status_code == 404

    def test_ack_requires_no_authorization_header(self, _isolate_db):
        """Same no-auth posture as the rest of this dashboard (dashboard/auth.py)."""
        alert_id = _insert_alert(_isolate_db, ticker="ZZZZ")
        resp = client.post(f'/alerts/{alert_id}/ack')
        assert resp.status_code == 200
