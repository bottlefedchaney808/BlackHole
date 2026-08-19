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

def test_root_serves_chart_window(tmp_path):
    app = create_app(BarCache(tmp_path / "b.db"))
    c = TestClient(app)
    r = c.get("/")
    assert r.status_code == 200
    body = r.text
    assert 'id="chart"' in body
    assert "echarts" in body or "chart-app" in body
    assert "cdn" not in body.lower()
    vendor = c.get("/static/vendor/echarts.min.js")
    assert vendor.status_code == 200
    assert len(vendor.content) > 10_000
