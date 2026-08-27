from fastapi.testclient import TestClient

from swaps_dashboard.app import app

client = TestClient(app)


def test_health_route():
    r = client.get('/health')
    assert r.status_code == 200
    body = r.json()
    assert body['ok'] is True
    assert 'db_path' in body
