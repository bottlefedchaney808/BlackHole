import pytest
from fastapi.testclient import TestClient

from dashboard.app import app

pytestmark = pytest.mark.unit
client = TestClient(app)


def test_home_page_has_positions_widget_card():
    r = client.get("/")
    assert r.status_code == 200
    assert 'id="positionsWidget"' in r.text
    assert "/api/widgets/positions" in r.text


def test_home_page_positions_widget_starts_empty():
    r = client.get("/")
    assert r.status_code == 200
    assert "No positions synced yet" in r.text
