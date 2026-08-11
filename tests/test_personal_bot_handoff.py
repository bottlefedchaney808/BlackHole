"""Tests for the personal-bot persistent handoff adapter.

The adapter exposes a durable enqueue / acknowledge / reject handoff onto the
personal-bot profile's state directory, gated on the real gateway heartbeat so
an unreachable gateway yields ``unavailable`` without ever touching credentials.

Handoff result states are exactly: ``accepted`` | ``rejected`` | ``unavailable``.
This is a watchlist / signal handoff path only -- it never executes a trade.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import worker_broker  # noqa: E402


def _write_heartbeat(path: Path, age_s: int = 5) -> None:
    """Write a gateway heartbeat file dated ``age_s`` seconds in the past."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc) - timedelta(seconds=age_s)
    path.write_text(json.dumps({"pid": 1, "updated_at": stamp.isoformat()}), encoding="utf-8")


def _valid_payload(**overrides):
    payload = {
        "ticker": "SPY",
        "summary": "Put flow elevated, term skew rich into an options roll.",
        "source_run_id": "run-20260802-abc",
        "requester": "quant-bridge",
        "requested_schedule": "once",
        "idempotency_key": "handoff-1",
    }
    payload.update(overrides)
    return payload


def _broker_with_handoff(tmp_path):
    """Return a broker whose handoff adapter points at tmp_path, plus its store/heartbeat."""
    store_dir = tmp_path / "handoffs"
    heartbeat = tmp_path / "gateway.heartbeat"
    broker = worker_broker.WorkerBroker(lambda: tmp_path / "worker.token")
    broker._handoff_store_dir = store_dir
    broker._handoff_heartbeat_path = heartbeat
    return broker, store_dir, heartbeat


def _client(broker):
    app = FastAPI()
    app.include_router(broker.router())
    return TestClient(app, raise_server_exceptions=False)


def _auth(broker):
    return {"Authorization": f"Bearer {broker.token}"}


# --------------------------------------------------------------------------- #
# Model validation
# --------------------------------------------------------------------------- #
def test_handoff_request_validates_strictly():
    req = worker_broker.HandoffRequest(**_valid_payload())
    assert req.ticker == "SPY"
    assert req.requester == "quant-bridge"
    assert req.requested_schedule == "once"

    with pytest.raises(Exception):
        worker_broker.HandoffRequest(**_valid_payload(ticker="not a ticker!"))
    with pytest.raises(Exception):
        worker_broker.HandoffRequest(**_valid_payload(ticker="BAD/CHAR"))
    with pytest.raises(Exception):
        worker_broker.HandoffRequest(**_valid_payload(summary="my api_key is secret"))
    with pytest.raises(Exception):
        # extra field is forbidden
        worker_broker.HandoffRequest(**_valid_payload(profile="root"))


# --------------------------------------------------------------------------- #
# Adapter state transitions
# --------------------------------------------------------------------------- #
def test_enqueue_accepts_when_gateway_heartbeat_fresh(tmp_path):
    broker, store, heartbeat = _broker_with_handoff(tmp_path)
    _write_heartbeat(heartbeat, age_s=5)
    result = broker._handoff_adapter().enqueue(worker_broker.HandoffRequest(**_valid_payload()))
    assert result["status"] == "accepted"
    assert result["handoff_id"]
    assert result["ticker"] == "SPY"
    # record persisted durably on disk
    record = json.loads((store / f"{result['handoff_id']}.json").read_text(encoding="utf-8"))
    assert record["status"] == "accepted"
    assert record["idempotency_key"] == "handoff-1"
    assert record["source_run_id"] == "run-20260802-abc"


def test_enqueue_unavailable_when_no_heartbeat(tmp_path):
    broker, store, _ = _broker_with_handoff(tmp_path)
    # no heartbeat file written -> unreachable
    result = broker._handoff_adapter().enqueue(worker_broker.HandoffRequest(**_valid_payload()))
    assert result["status"] == "unavailable"
    assert result["retryable"] is True
    assert not list(store.iterdir()) if store.exists() else True


def test_enqueue_unavailable_when_heartbeat_stale(tmp_path):
    broker, store, heartbeat = _broker_with_handoff(tmp_path)
    _write_heartbeat(heartbeat, age_s=600)  # older than the freshness window
    result = broker._handoff_adapter().enqueue(worker_broker.HandoffRequest(**_valid_payload()))
    assert result["status"] == "unavailable"
    assert result["retryable"] is True


def test_enqueue_idempotent_returns_same_handoff(tmp_path):
    broker, store, heartbeat = _broker_with_handoff(tmp_path)
    _write_heartbeat(heartbeat, age_s=5)
    adapter = broker._handoff_adapter()
    first = adapter.enqueue(worker_broker.HandoffRequest(**_valid_payload()))
    second = adapter.enqueue(worker_broker.HandoffRequest(**_valid_payload()))
    assert first["status"] == "accepted" and second["status"] == "accepted"
    assert first["handoff_id"] == second["handoff_id"]
    assert len(list(store.glob("*.json"))) == 1


def test_acknowledge_and_reject_transitions(tmp_path):
    broker, store, heartbeat = _broker_with_handoff(tmp_path)
    _write_heartbeat(heartbeat, age_s=5)
    adapter = broker._handoff_adapter()
    enqueued = adapter.enqueue(worker_broker.HandoffRequest(**_valid_payload()))
    handoff_id = enqueued["handoff_id"]

    rejected = adapter.reject(handoff_id)
    assert rejected["status"] == "rejected"
    assert json.loads((store / f"{handoff_id}.json").read_text(encoding="utf-8"))["status"] == "rejected"

    acknowledged = adapter.acknowledge(handoff_id)
    assert acknowledged["status"] == "accepted"
    assert json.loads((store / f"{handoff_id}.json").read_text(encoding="utf-8"))["status"] == "accepted"


def test_handoff_states_are_exactly_the_enum(tmp_path):
    broker, store, heartbeat = _broker_with_handoff(tmp_path)
    _write_heartbeat(heartbeat, age_s=5)
    adapter = broker._handoff_adapter()
    accepted = adapter.enqueue(worker_broker.HandoffRequest(**_valid_payload()))
    handoff_id = accepted["handoff_id"]
    assert accepted["status"] in {"accepted", "rejected", "unavailable"}
    assert adapter.reject(handoff_id)["status"] in {"accepted", "rejected", "unavailable"}
    assert worker_broker.HANDOFF_STATES == ("accepted", "rejected", "unavailable")


# --------------------------------------------------------------------------- #
# HTTP surface (authenticated, loopback)
# --------------------------------------------------------------------------- #
def test_handoff_enqueue_endpoint_requires_auth(tmp_path):
    broker, _, _ = _broker_with_handoff(tmp_path)
    client = _client(broker)
    response = client.post("/api/workers/personal-bot/handoff", json=_valid_payload())
    assert response.status_code == 401


def test_handoff_enqueue_endpoint_accepted_with_auth(tmp_path):
    broker, _, heartbeat = _broker_with_handoff(tmp_path)
    _write_heartbeat(heartbeat, age_s=5)
    client = _client(broker)
    response = client.post(
        "/api/workers/personal-bot/handoff",
        headers=_auth(broker),
        json=_valid_payload(),
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"
    assert body["handoff_id"]


def test_handoff_reject_endpoint(tmp_path):
    broker, _, heartbeat = _broker_with_handoff(tmp_path)
    _write_heartbeat(heartbeat, age_s=5)
    client = _client(broker)
    enqueued = client.post(
        "/api/workers/personal-bot/handoff", headers=_auth(broker), json=_valid_payload()
    ).json()
    handoff_id = enqueued["handoff_id"]
    response = client.post(
        f"/api/workers/personal-bot/handoff/{handoff_id}/reject", headers=_auth(broker)
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


def test_handoff_list_endpoint_requires_auth_and_lists(tmp_path):
    broker, _, heartbeat = _broker_with_handoff(tmp_path)
    _write_heartbeat(heartbeat, age_s=5)
    client = _client(broker)
    assert client.get("/api/workers/personal-bot/handoff").status_code == 401
    ok = client.get("/api/workers/personal-bot/handoff", headers=_auth(broker))
    assert ok.status_code == 200
    assert "handoffs" in ok.json()


# --------------------------------------------------------------------------- #
# Regression: generic prompt dispatch uses the durable handoff, no spawn
# --------------------------------------------------------------------------- #
def test_personal_bot_generic_dispatch_uses_handoff_without_spawn(monkeypatch, tmp_path):
    spawned = []

    def forbidden_spawn(*args, **kwargs):
        spawned.append(args)
        raise AssertionError("personal-bot must not spawn a fresh process")

    monkeypatch.setattr(worker_broker.subprocess, "Popen", forbidden_spawn)
    broker = worker_broker.WorkerBroker(lambda: tmp_path / "personal-bot-token")
    # Point the adapter at tmp_path with a fresh heartbeat so the dispatch
    # deterministically takes the durable-handoff path (no live gateway needed).
    broker._handoff_store_dir = tmp_path / "handoffs"
    heartbeat = tmp_path / "gateway.heartbeat"
    heartbeat.write_text(json.dumps({"updated_at": worker_broker._now()}), encoding="utf-8")
    broker._handoff_heartbeat_path = heartbeat
    result = broker.dispatch(worker_broker.DispatchRequest(worker="personal-bot", prompt="check"))
    assert result["status"] == "accepted"
    assert result["adapter"] == "personal-bot-gateway"
    assert spawned == []


@pytest.mark.unit
def test_smoke_no_credentials_written(tmp_path):
    """Smoke: a handoff enqueue touches only the handoff store, never credentials."""
    broker, store, heartbeat = _broker_with_handoff(tmp_path)
    _write_heartbeat(heartbeat, age_s=5)
    adapter = broker._handoff_adapter()
    adapter.enqueue(worker_broker.HandoffRequest(**_valid_payload()))
    # Only handoff*.json records live under the store; no token/auth material.
    written = [p.name for p in store.iterdir()]
    assert written and all(p.endswith(".json") for p in written)
    for p in store.glob("*.json"):
        text = p.read_text(encoding="utf-8").lower()
        for marker in ("api_key", "auth", "password", "bearer", "credential", "authorization"):
            assert marker not in text
