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
    assert "dealer_exposure" in slugs or "dealer_flow" in slugs


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
