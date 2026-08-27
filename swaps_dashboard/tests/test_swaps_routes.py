from fastapi.testclient import TestClient

import swaps_dashboard.app as swaps_app
from swaps_dashboard.app import app

client = TestClient(app)


def test_health_route():
    r = client.get('/health')
    assert r.status_code == 200
    body = r.json()
    assert body['ok'] is True
    assert 'db_path' in body


def test_swaps_page_launches(monkeypatch, tmp_path):
    # CARL R3-F1: a real swaps.db exists at the default DB_PATH on this
    # machine (346GB) -- without this monkeypatch, every run of this test
    # (including every red/green TDD cycle) queries it directly through
    # _get_swaps_filter_options(), the exact route the code's own comments
    # call out as the prior 15s-timeout source. Point at a path that can't
    # exist so the route's "not found" branch fires instead, and this test
    # verifies routing/rendering only -- live-data behavior is Task 10's job.
    monkeypatch.setattr(swaps_app, 'DB_PATH', str(tmp_path / 'missing.db'))
    r = client.get('/swaps')
    assert r.status_code == 200
    assert 'Swap trades' in r.text
