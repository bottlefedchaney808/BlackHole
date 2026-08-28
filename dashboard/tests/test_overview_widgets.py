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


def test_home_page_has_signals_widget_card():
    r = client.get("/")
    assert r.status_code == 200
    assert 'id="signalsWidget"' in r.text
    assert "/api/widgets/signals" in r.text
    assert "No signals computed yet" in r.text


def test_home_page_has_position_analysis_widget_card():
    r = client.get("/")
    assert r.status_code == 200
    assert 'id="positionAnalysisWidget"' in r.text
    assert "/api/widgets/position_analysis" in r.text
    assert "No analysis computed yet" in r.text


def test_home_page_has_surfaces_widget_card():
    r = client.get("/")
    assert r.status_code == 200
    assert 'id="surfacesWidget"' in r.text
    assert "/api/widgets/surfaces" in r.text
    assert "No surfaces rendered yet" in r.text
