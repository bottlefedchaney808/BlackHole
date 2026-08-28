from fastapi.testclient import TestClient

from chart_app.bar_cache import BarCache
from chart_app.server import create_app


def test_state_and_rh(tmp_path):
    app = create_app(BarCache(tmp_path / "b.db"))
    c = TestClient(app)
    r = c.get("/api/state")
    assert r.status_code == 200
    assert r.json()["ticker"] == "SPY"
    r = c.post(
        "/api/rh", json={"position": {"qty": 10, "avg_price": 400.0}, "fills": []}
    )
    assert r.status_code == 200
    assert c.get("/api/state").json()["rh"]["position"]["qty"] == 10


def test_symbol_switch(tmp_path):
    app = create_app(BarCache(tmp_path / "b.db"))
    c = TestClient(app)
    r = c.post("/api/symbol", json={"ticker": "QQQ", "interval": "1d"})
    assert r.status_code == 200
    assert c.get("/api/state").json()["ticker"] == "QQQ"


def test_symbol_switch_persists_across_restart(tmp_path):
    db_path = tmp_path / "b.db"
    app1 = create_app(BarCache(db_path))
    TestClient(app1).post("/api/symbol", json={"ticker": "QQQ", "interval": "1h"})

    # A fresh app/process reading the same cache should reopen where the
    # user left off, not fall back to the SPY/15m defaults.
    app2 = create_app(BarCache(db_path))
    state = TestClient(app2).get("/api/state").json()
    assert state["ticker"] == "QQQ"
    assert state["interval"] == "1h"


def test_no_persisted_session_uses_defaults(tmp_path):
    app = create_app(
        BarCache(tmp_path / "b.db"), default_ticker="NVDA", default_interval="1d"
    )
    state = TestClient(app).get("/api/state").json()
    assert state["ticker"] == "NVDA"
    assert state["interval"] == "1d"


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


def test_background_refresh_disabled_by_default_no_network(tmp_path):
    # daily_fn/intrad_fn would raise if ever called -- create_app() with no
    # background_refresh_seconds must never touch them, even under lifespan.
    def _boom(*a, **k):
        raise AssertionError("data fetcher should not be called")

    app = create_app(BarCache(tmp_path / "b.db"), daily_fn=_boom, intrad_fn=_boom)
    with TestClient(app) as c:
        assert c.get("/api/state").json()["ok"] is True


def test_background_refresh_opt_in_calls_fetcher_on_startup(tmp_path):
    calls = []

    def _fake_intrad(ticker, interval, lookback):
        calls.append((ticker, interval, lookback))
        from shared.chart_data import CandlePayload

        return CandlePayload(
            ticker=ticker,
            interval=interval,
            lookback=lookback,
            source="test",
            observations=(),
        )

    app = create_app(
        BarCache(tmp_path / "b.db"),
        intrad_fn=_fake_intrad,
        background_refresh_seconds=9999,
    )
    with TestClient(app):
        pass
    assert calls and calls[0][0] == "SPY" and calls[0][1] == "15m"
