"""Unit tests for the Phase 3 generic widget API
(dashboard/app.py: catalog / run / state / context).

Every test monkeypatches both the widget-cache DB path and the Context-Store
DB path to a fresh tmp_path, and uses the *real* leisen_reimer module (pure
computation, no ThetaData network calls) so the run/state round-trip exercises
the actual registry resolution path. Tests never touch the network.
"""

import pytest
from fastapi.testclient import TestClient

import dashboard.app as dashboard_app
from dashboard.app import app
from dashboard.widget_cache import WidgetCache

pytestmark = pytest.mark.unit
client = TestClient(app)


def _db_paths(monkeypatch, tmp_path, name="widgets.db"):
    db = tmp_path / name
    monkeypatch.setattr(dashboard_app, "WIDGET_CACHE_PATH", str(db))
    monkeypatch.setattr(dashboard_app, "CONTEXT_STORE_PATH", str(db))
    return db


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------


def test_catalog_200_includes_leisen_reimer_and_a_tools_slug(monkeypatch, tmp_path):
    _db_paths(monkeypatch, tmp_path)
    r = client.get("/api/widgets/catalog")
    assert r.status_code == 200
    data = r.json()
    widgets = {w["slug"]: w for w in data["widgets"]}

    assert "leisen_reimer" in widgets
    lr = widgets["leisen_reimer"]
    assert lr["default_selected"] is True
    assert lr["name"] == "Leisen-Reimer"
    assert lr["suite"] == "options_suite"
    assert lr["output_kind"] == "metrics"
    assert isinstance(lr["inputs"], dict)
    assert lr["inputs"]["ticker"] == "required"
    assert "sample" in lr

    # A Tools/registry.py-adapted slug must also be present (from_tool_spec).
    assert "options-strategy" in widgets
    assert widgets["options-strategy"]["suite"] == "tools"

    # No run callable leaks into the serialized preview.
    assert "run" not in lr


def test_catalog_contains_dealer_exposure_or_dealer_flow(monkeypatch, tmp_path):
    _db_paths(monkeypatch, tmp_path)
    r = client.get("/api/widgets/catalog")
    assert r.status_code == 200
    slugs = {w["slug"] for w in r.json()["widgets"]}
    assert "expiry_exposure" in slugs or "dealer_flow" in slugs


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------


def _leisen_reimer_run_body(ticker="SPY"):
    return {
        "ticker": ticker,
        "spot": 550.0,
        "strike": 550.0,
        "target_years": 0.25,
        "risk_free_rate": 0.05,
        "dividend_yield": 0.0,
        "option_type": "call",
        "sigma": 0.2,
    }


def test_run_leisen_reimer_returns_ok_price(monkeypatch, tmp_path):
    _db_paths(monkeypatch, tmp_path)
    r = client.post("/api/widgets/leisen_reimer/run", json=_leisen_reimer_run_body())
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["slug"] == "leisen_reimer"
    assert data["metrics"]["price"] > 0
    assert data["metrics"]["model"] == "leisen_reimer"


def test_run_accepts_design_spec_scope_params_shape(monkeypatch, tmp_path):
    """The wire shape quant-widget.js posts: {scope: {...}, params: {...}}.

    The route must lift scope + params to top level so the module sees a
    normal context (this is the JS<->backend contract from the design spec).
    """
    _db_paths(monkeypatch, tmp_path)
    nested = {
        "scope": {"ticker": "SPY"},
        "params": {
            "spot": 550.0,
            "strike": 550.0,
            "target_years": 0.25,
            "risk_free_rate": 0.05,
            "dividend_yield": 0.0,
            "option_type": "call",
            "sigma": 0.2,
        },
    }
    r = client.post("/api/widgets/leisen_reimer/run", json=nested)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["metrics"]["price"] > 0


def test_run_unknown_slug_404(monkeypatch, tmp_path):
    _db_paths(monkeypatch, tmp_path)
    r = client.post("/api/widgets/does-not-exist/run", json={"ticker": "SPY"})
    assert r.status_code == 404


def test_run_missing_ticker_fails_loud_not_500(monkeypatch, tmp_path):
    _db_paths(monkeypatch, tmp_path)
    body = _leisen_reimer_run_body()
    body.pop("ticker")
    r = client.post("/api/widgets/leisen_reimer/run", json=body)
    assert r.status_code != 500
    # Either an explicit 400/4xx error, or a 200 body whose status reports the
    # module failure loudly (error/failed) with a message.
    if r.status_code == 200:
        data = r.json()
        assert data["status"] in ("error", "failed")
        assert data["metrics"].get("error")
    else:
        assert 400 <= r.status_code < 500


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------


def test_state_after_run_returns_cached_payload(monkeypatch, tmp_path):
    _db_paths(monkeypatch, tmp_path)
    run = client.post("/api/widgets/leisen_reimer/run", json=_leisen_reimer_run_body())
    assert run.status_code == 200
    scope_key = run.json()["scope"]

    r = client.get(f"/api/widgets/leisen_reimer/state?scope={scope_key}")
    assert r.status_code == 200
    data = r.json()
    assert data["slug"] == "leisen_reimer"
    assert data["scope"] == scope_key
    assert data["status"] == "ok"
    assert data["payload"]["metrics"]["price"] > 0


def test_state_404_before_any_run(monkeypatch, tmp_path):
    _db_paths(monkeypatch, tmp_path)
    r = client.get("/api/widgets/leisen_reimer/state?scope=ticker:SPY")
    assert r.status_code == 404


# --------------------------------------------------------------------------
# Context
# --------------------------------------------------------------------------


def test_context_empty_returns_200(monkeypatch, tmp_path):
    _db_paths(monkeypatch, tmp_path)
    # leisen_reimer emits no context_patch, so the store stays empty -- the
    # endpoint must still return 200 with an empty list, never 500.
    client.post("/api/widgets/leisen_reimer/run", json=_leisen_reimer_run_body())
    r = client.get("/api/context?scope=ticker:SPY")
    assert r.status_code == 200
    data = r.json()
    assert data["scope"] == "ticker:SPY"
    assert data["entries"] == []


# --------------------------------------------------------------------------
# Cache-backed widgets (positions, signals, position_analysis, surfaces)
# --------------------------------------------------------------------------


def test_catalog_includes_cache_widgets(monkeypatch, tmp_path):
    """Cache-backed widgets appear in the catalog with optional ticker."""
    _db_paths(monkeypatch, tmp_path)
    r = client.get("/api/widgets/catalog")
    assert r.status_code == 200
    data = r.json()
    widgets = {w["slug"]: w for w in data["widgets"]}

    for slug in ("positions", "signals", "position_analysis", "surfaces"):
        assert slug in widgets, f"{slug} should be in catalog"
        assert widgets[slug]["inputs"]["ticker"] == "optional"
        assert widgets[slug]["inputs"]["expiry"] == "none"
        assert widgets[slug]["inputs"]["basket"] == "none"


def test_run_cache_positions_200_after_cache_write(monkeypatch, tmp_path):
    """POST /api/widgets/positions/run returns 200 after cache is written directly."""
    _db_paths(monkeypatch, tmp_path)
    
    # Write directly to the cache using WidgetCache
    cache = WidgetCache(tmp_path / "widgets.db")
    cache.set("positions", {
        "positions": [
            {"ticker": "SPY", "qty": 100, "instrument_type": "equity"},
            {"ticker": "QQQ", "qty": 50, "instrument_type": "equity"}
        ],
        "accounts": ["ABC123"]
    }, status="ok")

    # Now run the positions widget (cache-backed)
    r = client.post("/api/widgets/positions/run", json={})
    assert r.status_code == 200, "Cache-backed positions/run should not 404"
    data = r.json()
    assert data["slug"] == "positions"
    assert data["status"] == "ok"
    assert "positions" in data["metrics"]
    assert len(data["metrics"]["positions"]) == 2


def test_run_cache_signals_200_after_cache_write(monkeypatch, tmp_path):
    """POST /api/widgets/signals/run returns 200 after cache is written."""
    _db_paths(monkeypatch, tmp_path)
    
    cache = WidgetCache(tmp_path / "widgets.db")
    cache.set("signals", {"tickers": [{"ticker": "SPXW", "signal": "LONG", "score": 75, "vrp_pct": 2.1, "data_quality": "good"}]}, status="ok")

    r = client.post("/api/widgets/signals/run", json={})
    assert r.status_code == 200
    data = r.json()
    assert data["slug"] == "signals"
    assert data["status"] == "ok"
    assert "tickers" in data["metrics"]


def test_run_cache_position_analysis_200_after_cache_write(monkeypatch, tmp_path):
    """POST /api/widgets/position_analysis/run returns 200 after cache is written."""
    _db_paths(monkeypatch, tmp_path)
    
    cache = WidgetCache(tmp_path / "widgets.db")
    cache.set("position_analysis", {"positions": [{"ticker": "SPY", "hedge": {}, "hedge_headline": "Buy 100 SPY"}]}, status="ok")

    r = client.post("/api/widgets/position_analysis/run", json={})
    assert r.status_code == 200
    data = r.json()
    assert data["slug"] == "position_analysis"
    assert data["status"] == "ok"
    assert "positions" in data["metrics"]


def test_run_cache_surfaces_200_after_cache_write(monkeypatch, tmp_path):
    """POST /api/widgets/surfaces/run returns 200 after cache is written."""
    _db_paths(monkeypatch, tmp_path)
    
    cache = WidgetCache(tmp_path / "widgets.db")
    cache.set("surfaces", {"iv": "base64png...", "vanna": "base64png...", "charm": "base64png..."}, status="ok")

    r = client.post("/api/widgets/surfaces/run", json={})
    assert r.status_code == 200
    data = r.json()
    assert data["slug"] == "surfaces"
    assert data["status"] == "ok"
    assert "iv" in data["metrics"] or "message" in data["metrics"]


def test_cache_positions_idle_when_empty(monkeypatch, tmp_path):
    """Cache-backed positions returns idle status when cache is empty."""
    _db_paths(monkeypatch, tmp_path)
    
    # Clear any existing positions by writing empty
    cache = WidgetCache(tmp_path / "widgets.db")
    cache.set("positions", {}, status="idle")

    r = client.post("/api/widgets/positions/run", json={})
    assert r.status_code == 200
    data = r.json()
    assert data["status"] in ("idle", "ok")
    assert "message" in data["metrics"] or "positions" in data["metrics"]


def test_inject_positions_writes_context_store(monkeypatch, tmp_path):
    """POST /api/context/inject-positions writes to Context Store and returns held_tickers."""
    _db_paths(monkeypatch, tmp_path)

    positions = [
        {"ticker": "SPY", "qty": 100, "instrument_type": "equity"},
        {"ticker": "QQQ", "qty": 50, "instrument_type": "equity"},
        {"ticker": "SPY", "qty": 25, "instrument_type": "equity"}  # duplicate ticker
    ]
    r = client.post("/api/context/inject-positions", json={"positions": positions})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["positions_count"] == 3
    # held_tickers should be deduplicated and sorted
    assert data["held_tickers"] == ["QQQ", "SPY"]

    # Verify context store was written
    r = client.get("/api/context")
    assert r.status_code == 200
    ctx = r.json()
    entries = {e["key"]: e for e in ctx["entries"]}
    assert "positions" in entries
    assert entries["positions"]["source_slug"] == "positions"
    assert "held_tickers" in entries
    assert entries["held_tickers"]["source_slug"] == "positions"
