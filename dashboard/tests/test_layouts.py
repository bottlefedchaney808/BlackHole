"""Tests for dashboard.layouts -- the Phase 5 layout persistence APIRouter.

The router is standalone (not wired into dashboard/app.py), so these tests
mount it on a fresh FastAPI app via TestClient. The DB path is redirected to
tmp_path by setting the WIDGET_CACHE_PATH env var (the module reads it per
call), so tests never touch the real artifacts/widget_cache.db.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard import layouts

pytestmark = pytest.mark.unit

APP = FastAPI()
APP.include_router(layouts.router)
client = TestClient(APP)


def _set_db(tmp_path, monkeypatch):
    monkeypatch.setenv("WIDGET_CACHE_PATH", str(tmp_path / "widgets.db"))
    return tmp_path


def test_crud_round_trip(tmp_path, monkeypatch):
    _set_db(tmp_path, monkeypatch)
    page = "overview"
    body = {
        "instances": [
            {
                "widget_instance_id": "w1",
                "slug": "positions",
                "position": 0,
                "sync_enabled": True,
            },
            {
                "widget_instance_id": "w2",
                "slug": "signals",
                "position": 1,
                "sync_enabled": False,
            },
        ]
    }
    r = client.put(f"/api/layout/{page}", json=body)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "count": 2}

    r = client.get(f"/api/layout/{page}")
    assert r.status_code == 200
    data = r.json()
    assert [d["widget_instance_id"] for d in data] == ["w1", "w2"]
    assert [d["slug"] for d in data] == ["positions", "signals"]
    assert [d["sync_enabled"] for d in data] == [True, False]
    # default scope_override / config_json are None when omitted
    assert data[0]["scope_override"] is None
    assert data[0]["config_json"] is None
    assert isinstance(data[0]["created_at"], str) and data[0]["created_at"]


def test_empty_page_returns_200_empty_list(tmp_path, monkeypatch):
    _set_db(tmp_path, monkeypatch)
    r = client.get("/api/layout/never-saved-page")
    assert r.status_code == 200
    assert r.json() == []


def test_bad_body_400(tmp_path, monkeypatch):
    _set_db(tmp_path, monkeypatch)
    # negative position -> 400
    r = client.put(
        "/api/layout/overview",
        json={"instances": [{"widget_instance_id": "w1", "slug": "s", "position": -1, "sync_enabled": True}]},
    )
    assert r.status_code == 400
    # empty slug -> 400
    r = client.put(
        "/api/layout/overview",
        json={"instances": [{"widget_instance_id": "w1", "slug": " ", "position": 0, "sync_enabled": True}]},
    )
    assert r.status_code == 400
    # sync_enabled non-bool -> 400
    r = client.put(
        "/api/layout/overview",
        json={"instances": [{"widget_instance_id": "w1", "slug": "s", "position": 0, "sync_enabled": 1}]},
    )
    assert r.status_code == 400
    # instances not a list -> 400
    r = client.put("/api/layout/overview", json={"instances": "nope"})
    assert r.status_code == 400
    # missing instances -> 400
    r = client.put("/api/layout/overview", json={})
    assert r.status_code == 400
    # malformed JSON body -> 400
    r = client.put("/api/layout/overview", content="{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400


def test_delete_404_then_ok(tmp_path, monkeypatch):
    _set_db(tmp_path, monkeypatch)
    r = client.delete("/api/layout/overview/w1")
    assert r.status_code == 404

    client.put(
        "/api/layout/overview",
        json={"instances": [{"widget_instance_id": "w1", "slug": "positions", "position": 0, "sync_enabled": True}]},
    )
    r = client.delete("/api/layout/overview/w1")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert client.get("/api/layout/overview").json() == []


def test_position_ordering(tmp_path, monkeypatch):
    _set_db(tmp_path, monkeypatch)
    # saved out of order
    body = {
        "instances": [
            {"widget_instance_id": "a", "slug": "s1", "position": 2, "sync_enabled": True},
            {"widget_instance_id": "b", "slug": "s2", "position": 0, "sync_enabled": True},
            {"widget_instance_id": "c", "slug": "s3", "position": 1, "sync_enabled": True},
        ]
    }
    client.put("/api/layout/quant", json=body)
    data = client.get("/api/layout/quant").json()
    assert [d["widget_instance_id"] for d in data] == ["b", "c", "a"]
    assert [d["position"] for d in data] == [0, 1, 2]


def test_full_replace_semantics(tmp_path, monkeypatch):
    _set_db(tmp_path, monkeypatch)
    client.put(
        "/api/layout/overview",
        json={"instances": [{"widget_instance_id": "w1", "slug": "s1", "position": 0, "sync_enabled": True}]},
    )
    # second PUT replaces w1 entirely (no stale rows remain)
    client.put(
        "/api/layout/overview",
        json={"instances": [{"widget_instance_id": "w2", "slug": "s2", "position": 0, "sync_enabled": True}]},
    )
    data = client.get("/api/layout/overview").json()
    assert [d["widget_instance_id"] for d in data] == ["w2"]


def test_scope_override_round_trips_as_json(tmp_path, monkeypatch):
    _set_db(tmp_path, monkeypatch)
    scope = {"ticker": "NVDA", "expiry": "2026-09-18", "nested": {"basket": ["SPY", "QQQ"]}}
    client.put(
        "/api/layout/quant",
        json={
            "instances": [
                {
                    "widget_instance_id": "dealer",
                    "slug": "dealer_exposure",
                    "position": 0,
                    "sync_enabled": True,
                    "scope_override": scope,
                    "config_json": {"grid": True, "max": 5.5},
                }
            ]
        },
    )
    data = client.get("/api/layout/quant").json()
    assert data[0]["scope_override"] == scope
    assert data[0]["config_json"] == {"grid": True, "max": 5.5}


def test_pages_are_independent(tmp_path, monkeypatch):
    _set_db(tmp_path, monkeypatch)
    client.put(
        "/api/layout/overview",
        json={"instances": [{"widget_instance_id": "w1", "slug": "s1", "position": 0, "sync_enabled": True}]},
    )
    client.put(
        "/api/layout/quant",
        json={"instances": [{"widget_instance_id": "x1", "slug": "s2", "position": 0, "sync_enabled": True}]},
    )
    assert [d["widget_instance_id"] for d in client.get("/api/layout/overview").json()] == ["w1"]
    assert [d["widget_instance_id"] for d in client.get("/api/layout/quant").json()] == ["x1"]


def test_sanitizes_nan_in_scope_override(tmp_path, monkeypatch):
    _set_db(tmp_path, monkeypatch)
    # json= would reject NaN at client-encode time, so send a raw body with
    # the NaN literal (Starlette's json.loads parses it to float('nan'),
    # which the module's _sanitize_for_json then nulls at write time).
    raw_body = (
        '{"instances": [{"widget_instance_id": "w1", "slug": "s1", '
        '"position": 0, "sync_enabled": true, '
        '"scope_override": {"ratio": NaN, "ok": 1.5}}]}'
    )
    r = client.put(
        "/api/layout/quant",
        content=raw_body,
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 200
    data = client.get("/api/layout/quant").json()
    assert data[0]["scope_override"] == {"ratio": None, "ok": 1.5}
