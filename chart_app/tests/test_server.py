from fastapi.testclient import TestClient
from chart_app.bar_cache import BarCache
from chart_app.server import create_app

def test_state_and_rh(tmp_path):
    app = create_app(BarCache(tmp_path / "b.db"))
    c = TestClient(app)
    r = c.get("/api/state")
    assert r.status_code == 200
    assert r.json()["ticker"] == "SPY"
    r = c.post("/api/rh", json={"position": {"qty": 10, "avg_price": 400.0}, "fills": []})
    assert r.status_code == 200
    assert c.get("/api/state").json()["rh"]["position"]["qty"] == 10

def test_symbol_switch(tmp_path):
    app = create_app(BarCache(tmp_path / "b.db"))
    c = TestClient(app)
    r = c.post("/api/symbol", json={"ticker": "QQQ", "interval": "1d"})
    assert r.status_code == 200
    assert c.get("/api/state").json()["ticker"] == "QQQ"
