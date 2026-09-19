from datetime import datetime

from fastapi.testclient import TestClient

from chart_app.bar_cache import BarCache
from chart_app.server import _LOOKBACK, create_app
from shared.chart_data import CandleRecord


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


def test_every_intraday_lookback_can_seed_ema200():
    """Windows are sized by what the indicators need, not by what the fetch
    used to survive.

    This test used to assert `1h == "30d"` and `4h == "30d"`, pinning a ceiling
    that was never the provider's: it was httpx's default 5.0s timeout inside
    PHClient (see CLAUDE.md). With that fixed, both timeframes can reach the
    ~200 bars EMA200 needs, so assert the REASON rather than the string --
    a magic-string assertion is what let the stale cap survive a rewrite.
    """
    sessions_per_day = 1 / 1.45          # ~252 sessions a calendar year
    bars_per_session = {
        "3m": 130, "5m": 78, "10m": 39, "15m": 26,
        "30m": 13, "1h": 7, "4h": 2,
    }

    def calendar_days(lookback: str) -> int:
        unit, amount = lookback[-1], int(lookback[:-1])
        return amount * {"d": 1, "w": 7, "m": 30, "y": 365}[unit]

    for interval, per_session in bars_per_session.items():
        days = calendar_days(_LOOKBACK[interval])
        bars = days * sessions_per_day * per_session
        assert bars >= 200, (
            f"{interval} asks for {_LOOKBACK[interval]} = ~{bars:.0f} bars; "
            "EMA200 and the 200-bar ELMo ranks need >=200"
        )

    # The daily chart uses the daily path, not the intraday validator.
    assert _LOOKBACK["1d"] == "1y"


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


# ---------------------------------------------------------------------------
# Live strategy tester (POST /api/backtest)
# ---------------------------------------------------------------------------


def _ramp_cache(tmp_path, n=400):
    """A trending series long enough for the engine to warm up and trade."""
    from datetime import datetime, timedelta

    cache = BarCache(tmp_path / "bt.db")
    t0 = datetime(2026, 1, 2, 9, 30)
    recs = []
    for i in range(n):
        c = 100.0 + i * 0.35 + (3.0 if i % 17 == 0 else 0.0)
        op = recs[-1].close if recs else c
        recs.append(
            CandleRecord(
                timestamp=t0 + timedelta(minutes=15 * i),
                open=op,
                high=max(op, c) * 1.003,
                low=min(op, c) * 0.997,
                close=c,
                volume=1_000_000.0,
            )
        )
    cache.upsert("SPY", "15m", recs)
    cache.set_session("SPY", "15m")
    return cache


def test_backtest_endpoint_scores_the_loaded_bars(tmp_path):
    app = create_app(_ramp_cache(tmp_path))
    with TestClient(app) as client:
        res = client.post("/api/backtest", json={"config": {}, "elmo": {}})
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is True
        assert body["ticker"] == "SPY" and body["interval"] == "15m"
        # Both views are published: honest P&L, and what the chart draws.
        assert set(body["metrics"]) >= {"trades", "total_return_pct", "buy_hold_pct"}
        assert len(body["actions"]) == 400
        assert len(body["score"]) == 400
        assert len(body["elmo"]["entropy"]) == 400


def test_backtest_endpoint_respects_config_overrides(tmp_path):
    app = create_app(_ramp_cache(tmp_path))
    with TestClient(app) as client:
        loose = client.post("/api/backtest", json={"config": {"entry_long": 5.0}}).json()
        tight = client.post("/api/backtest", json={"config": {"entry_long": 95.0}}).json()
        assert loose["ok"] and tight["ok"]
        # An unreachable entry level must produce no trades at all.
        assert tight["metrics"]["trades"] == 0
        assert loose["metrics"]["trades"] >= tight["metrics"]["trades"]
        assert loose["config"]["entry_long"] == 5.0


def test_backtest_endpoint_respects_elmo_overrides(tmp_path):
    app = create_app(_ramp_cache(tmp_path))
    with TestClient(app) as client:
        a = client.post("/api/backtest", json={"elmo": {"entropy_window": 10}}).json()
        b = client.post("/api/backtest", json={"elmo": {"entropy_window": 40}}).json()
        # A longer window warms up later, so the series must differ.
        first_a = next(i for i, v in enumerate(a["elmo"]["entropy"]) if v is not None)
        first_b = next(i for i, v in enumerate(b["elmo"]["entropy"]) if v is not None)
        assert first_a < first_b


def test_backtest_endpoint_charges_costs(tmp_path):
    app = create_app(_ramp_cache(tmp_path))
    with TestClient(app) as client:
        free = client.post(
            "/api/backtest", json={"config": {"entry_long": 5.0}, "cost_bps": 0.0}
        ).json()
        dear = client.post(
            "/api/backtest", json={"config": {"entry_long": 5.0}, "cost_bps": 100.0}
        ).json()
        assert free["metrics"]["total_return_pct"] > dear["metrics"]["total_return_pct"]


def test_backtest_endpoint_reports_too_few_bars_instead_of_raising(tmp_path):
    cache = BarCache(tmp_path / "short.db")
    cache.upsert(
        "SPY",
        "15m",
        [CandleRecord(datetime(2026, 1, 2, 9, 30), 1, 1, 1, 1, 1)],
    )
    cache.set_session("SPY", "15m")
    app = create_app(cache)
    with TestClient(app) as client:
        body = client.post("/api/backtest", json={}).json()
        assert body["ok"] is False
        assert "60 bars" in body["error"]


def test_backtest_endpoint_never_calls_a_data_provider(tmp_path):
    """Dragging a slider must not cost a billed request."""

    def boom(*_a, **_k):
        raise AssertionError("the tester must never hit a data provider")

    app = create_app(_ramp_cache(tmp_path), daily_fn=boom, intrad_fn=boom, flow_fn=boom)
    with TestClient(app) as client:
        assert client.post("/api/backtest", json={}).json()["ok"] is True


# ---------------------------------------------------------------------------
# Per-indicator gear panels (POST /api/indicators)
# ---------------------------------------------------------------------------


def test_indicator_defaults_are_served_not_hardcoded_client_side(tmp_path):
    """The panel seeds its sliders from here, so a slider can never sit at 14
    while the engine quietly uses 20.

    (Note: BarCache(":memory:") does not work -- every _connect() opens a
    fresh in-memory database, so the tables created in __init__ are gone by
    the first query. Always give it a path.)
    """
    app = create_app(BarCache(tmp_path / "d.db"))
    with TestClient(app) as client:
        body = client.get("/api/indicator-defaults").json()
        assert body["indicators"]["rsi_period"] == 14
        assert body["indicators"]["macd_fast"] == 12
        assert body["elmo"]["entropy_window"] == 20
        assert body["elmo"]["liq_window"] == 5
        assert body["signal"]["entry_long"] == 30.0
        assert sum(body["weights"].values()) == 100.0


def test_indicators_endpoint_retunes_periods(tmp_path):
    app = create_app(_ramp_cache(tmp_path))
    with TestClient(app) as client:
        base = client.post("/api/indicators", json={"params": {}}).json()
        tuned = client.post(
            "/api/indicators",
            json={"params": {"rsi_period": 4, "ema_fast": 5, "bb_mult": 3.0}},
        ).json()
        assert base["ok"] and tuned["ok"]
        assert base["oscillators"]["rsi"][-1] != tuned["oscillators"]["rsi"][-1]
        assert base["overlays"]["ema20"][-1] != tuned["overlays"]["ema20"][-1]
        # A 3-sigma band must be wider than a 2-sigma one.
        wide = tuned["overlays"]["bb_upper"][-1] - tuned["overlays"]["bb_lower"][-1]
        narrow = base["overlays"]["bb_upper"][-1] - base["overlays"]["bb_lower"][-1]
        assert wide > narrow


def test_indicators_endpoint_keeps_the_wire_keys_stable(tmp_path):
    """`ema20` stays `ema20` even at period 5 -- the key is the contract the
    client renders against, not a description of the period."""
    app = create_app(_ramp_cache(tmp_path))
    with TestClient(app) as client:
        tuned = client.post("/api/indicators", json={"params": {"ema_fast": 5}}).json()
        assert "ema20" in tuned["overlays"]
        assert "ema5" not in tuned["overlays"]


def test_indicators_endpoint_returns_elmo_only_when_asked(tmp_path):
    app = create_app(_ramp_cache(tmp_path))
    with TestClient(app) as client:
        assert "elmo" not in client.post("/api/indicators", json={}).json()
        body = client.post(
            "/api/indicators", json={"elmo": {"entropy_window": 30}}
        ).json()
        assert len(body["elmo"]["entropy"]) == 400


def test_indicators_endpoint_ignores_unknown_knobs(tmp_path):
    app = create_app(_ramp_cache(tmp_path))
    with TestClient(app) as client:
        base = client.post("/api/indicators", json={"params": {}}).json()
        odd = client.post("/api/indicators", json={"params": {"nope": 99}}).json()
        assert odd["ok"] is True
        assert odd["oscillators"]["rsi"][-1] == base["oscillators"]["rsi"][-1]


def test_indicators_endpoint_never_calls_a_data_provider(tmp_path):
    def boom(*_a, **_k):
        raise AssertionError("a gear drag must never hit a data provider")

    app = create_app(_ramp_cache(tmp_path), daily_fn=boom, intrad_fn=boom, flow_fn=boom)
    with TestClient(app) as client:
        assert client.post("/api/indicators", json={"params": {"rsi_period": 9}}).json()["ok"]


def test_indicators_endpoint_reports_an_empty_cache(tmp_path):
    app = create_app(BarCache(tmp_path / "empty.db"))
    with TestClient(app) as client:
        body = client.post("/api/indicators", json={}).json()
        assert body["ok"] is False and "no bars" in body["error"]


def test_backtest_inherits_the_active_profile_so_it_scores_what_the_chart_draws(tmp_path, monkeypatch):
    """Indicators are read in ONE place; the tester scores what they read.

    The tester panel owns levels/stop/cooldown/costs. The ELMo windows belong
    to the gears. If a request omits `elmo`, the run must fall back to the
    ACTIVE PROFILE -- not to shipped defaults, which would score a chart nobody
    is looking at and report the P&L as if it were this one's.
    """
    from chart_app import profiles

    store = tmp_path / "profiles.json"
    monkeypatch.setenv("CHART_APP_PROFILES", str(store))

    profiles.save("SPY", "15m", elmo={"liq_window": 17}, path=store)

    app = create_app(_ramp_cache(tmp_path))
    with TestClient(app) as client:
        out = client.post("/api/backtest", json={}).json()
    assert out["ok"] is True, out.get("error")
    # The run used the profile's window, not elmo.DEFAULTS' 5.
    assert out["elmo_config"]["liq_window"] == 17
    assert out["profile_source"] == "SPY|15m"


def test_an_explicit_override_still_beats_the_profile(tmp_path, monkeypatch):
    """The gears must be able to try a value without saving it first."""
    from chart_app import profiles

    store = tmp_path / "profiles.json"
    monkeypatch.setenv("CHART_APP_PROFILES", str(store))

    profiles.save("SPY", "15m", elmo={"liq_window": 17}, path=store)

    app = create_app(_ramp_cache(tmp_path))
    with TestClient(app) as client:
        out = client.post("/api/backtest", json={"elmo": {"liq_window": 6}}).json()
    assert out["ok"] is True, out.get("error")
    assert out["elmo_config"]["liq_window"] == 6


def test_backtest_response_keys_do_not_collide(tmp_path):
    """`available` is the conviction engine's per-component dict. A second
    `available` carrying the cache span silently clobbered one of the two --
    the date pickers were being fed {trend: true, momentum: true, ...}.
    """
    app = create_app(_ramp_cache(tmp_path))
    with TestClient(app) as client:
        out = client.post("/api/backtest", json={}).json()
    assert out["ok"] is True
    # Components, unchanged wire contract.
    assert set(out["available"]) == {"trend", "momentum", "elmo", "whale", "breakout"}
    # The span lives under its own key.
    assert set(out["cache_span"]) >= {"start", "end", "bars", "interval"}
    assert out["cache_span"]["bars"] > 0
