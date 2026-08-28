import pytest
from fastapi.testclient import TestClient

import dashboard.app as dashboard_app
from dashboard.app import app

pytestmark = pytest.mark.unit
client = TestClient(app)


def test_get_widget_404_before_any_write(monkeypatch, tmp_path):
    monkeypatch.setattr(
        dashboard_app, "WIDGET_CACHE_PATH", str(tmp_path / "widgets.db")
    )
    r = client.get("/api/widgets/positions")
    assert r.status_code == 404


def test_post_positions_then_get_round_trips(monkeypatch, tmp_path):
    monkeypatch.setattr(
        dashboard_app, "WIDGET_CACHE_PATH", str(tmp_path / "widgets.db")
    )
    body = {
        "positions": [
            {
                "account": "A",
                "ticker": "NVDA",
                "instrument_type": "equity",
                "qty": 0.028,
                "avg_price": 211.94,
                "current_price": 215.0,
                "market_value": 6.02,
                "unrealized_pl": 0.09,
            }
        ],
        "accounts": [{"account": "A", "total_value": 53.88, "cash": 10.61}],
    }
    r = client.post("/api/widgets/positions", json=body)
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    r = client.get("/api/widgets/positions")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["payload"]["positions"][0]["ticker"] == "NVDA"
    assert data["payload"]["accounts"][0]["account"] == "A"
    assert "computed_at" in data


def test_post_positions_rejects_non_list_positions(monkeypatch, tmp_path):
    monkeypatch.setattr(
        dashboard_app, "WIDGET_CACHE_PATH", str(tmp_path / "widgets.db")
    )
    r = client.post("/api/widgets/positions", json={"positions": "not-a-list"})
    assert r.status_code == 400


def test_post_positions_rejects_non_list_accounts(monkeypatch, tmp_path):
    monkeypatch.setattr(
        dashboard_app, "WIDGET_CACHE_PATH", str(tmp_path / "widgets.db")
    )
    r = client.post(
        "/api/widgets/positions", json={"positions": [], "accounts": "nope"}
    )
    assert r.status_code == 400


def test_post_positions_defaults_missing_accounts_to_empty_list(monkeypatch, tmp_path):
    monkeypatch.setattr(
        dashboard_app, "WIDGET_CACHE_PATH", str(tmp_path / "widgets.db")
    )
    r = client.post("/api/widgets/positions", json={"positions": []})
    assert r.status_code == 200
    data = client.get("/api/widgets/positions").json()
    assert data["payload"]["accounts"] == []


def test_get_widget_unknown_id_404(monkeypatch, tmp_path):
    monkeypatch.setattr(
        dashboard_app, "WIDGET_CACHE_PATH", str(tmp_path / "widgets.db")
    )
    r = client.get("/api/widgets/does-not-exist")
    assert r.status_code == 404
