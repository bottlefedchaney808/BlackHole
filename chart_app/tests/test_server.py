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
    assert 'id="ticker"' in body
    assert 'id="interval"' in body
    assert 'id="legs"' in body
    assert "echarts" in body or "chart-app" in body
    assert "cdn" not in body.lower()
    vendor = c.get("/static/vendor/echarts.min.js")
    assert vendor.status_code == 200
    assert len(vendor.content) > 10_000


def test_create_app_has_no_order_route(tmp_path):
    app = create_app(BarCache(tmp_path / "b.db"))
    c = TestClient(app)
    payload = {"order": {"side": "buy", "qty": 1, "ticker": "SPY"}}
    assert c.get("/api/order").status_code == 404
    assert c.delete("/api/order").status_code == 404
    assert c.post("/api/order", json=payload).status_code == 404
    assert c.put("/api/order", json=payload).status_code == 404
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/api/order" not in paths


def test_rh_rejects_order_body(tmp_path):
    app = create_app(BarCache(tmp_path / "b.db"))
    c = TestClient(app)
    r = c.post("/api/rh", json={"order": {"side": "buy", "qty": 1}})
    assert r.status_code in (400, 422)
    state = c.get("/api/state").json()
    assert state.get("rh") is None or state["rh"].get("order") is None
