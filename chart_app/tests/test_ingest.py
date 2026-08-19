from datetime import datetime
from pathlib import Path
from fastapi.testclient import TestClient
from shared.chart_data import CandleRecord, CandlePayload
from shared.spot_history import fetch_daily_candles, fetch_intraday_candles
from chart_app.bar_cache import BarCache
from chart_app.ingest import refresh_cache
from chart_app.server import create_app

def test_refresh_cache_upserts(tmp_path):
    rec = CandleRecord(datetime(2026, 8, 18), 1, 1, 1, 1, 1)
    payload = CandlePayload("SPY", "1d", "5d", "injected", (rec,))
    n = refresh_cache(BarCache(tmp_path / "b.db"), "SPY", "1d", "5d",
                      daily_fn=lambda t, lookback: payload,
                      intrad_fn=None)
    assert n == 1

def test_refresh_cache_uses_intrad_fn(tmp_path):
    rec = CandleRecord(datetime(2026, 8, 18, 10, 0), 1, 1, 1, 1, 1)
    payload = CandlePayload("SPY", "15m", "5d", "injected", (rec,))
    called: dict[str, object] = {}

    def intrad_fn(ticker, *, interval, lookback):
        called["ticker"] = ticker
        called["interval"] = interval
        called["lookback"] = lookback
        return payload

    n = refresh_cache(
        BarCache(tmp_path / "b.db"),
        "SPY",
        "15m",
        "5d",
        daily_fn=None,
        intrad_fn=intrad_fn,
    )
    assert n == 1
    assert called == {"ticker": "SPY", "interval": "15m", "lookback": "5d"}

def test_api_refresh_uses_injected_fetchers(tmp_path):
    rec = CandleRecord(datetime(2026, 8, 18), 1, 1, 1, 1, 1)
    payload = CandlePayload("SPY", "1d", "30d", "injected", (rec,))
    app = create_app(
        BarCache(tmp_path / "b.db"),
        default_interval="1d",
        daily_fn=lambda t, lookback: payload,
        intrad_fn=None,
    )
    client = TestClient(app)
    response = client.post("/api/refresh", json={"lookback": "30d"})
    assert response.status_code == 200
    assert response.json()["upserted"] == 1
    bars = client.get("/api/state").json()["bars"]
    assert len(bars) == 1
    assert bars[0]["close"] == 1

def test_create_app_defaults_to_spot_history_fetchers():
    params = create_app.__kwdefaults__
    assert params["daily_fn"] is fetch_daily_candles
    assert params["intrad_fn"] is fetch_intraday_candles

def test_module_app_exposes_refresh_route():
    from chart_app.server import app
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/api/refresh" in paths

def test_launchers_bind_localhost_8791():
    root = Path(__file__).resolve().parents[2]
    bat = (root / "chart_app.bat").read_text(encoding="utf-8")
    sh = (root / "chart_app.sh").read_text(encoding="utf-8")
    assert "--port 8791" in bat
    assert "127.0.0.1:8791" in bat
    assert "8787" not in bat
    assert "set VIRTUAL_ENV=" in bat
    assert "chart_app.server:app" in bat
    assert "--port \"$PORT\"" in sh or "--port 8791" in sh
    assert "8791" in sh
    assert "8787" not in sh
    assert "chart_app.server:app" in sh
