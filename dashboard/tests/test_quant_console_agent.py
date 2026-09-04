"""test_quant_console_agent.py

Offline tests for the Phase 6 console-agent skeleton. NO real Anthropic calls,
NO network, NO ThetaData. The model client is a scripted FakeModelClient and
the executed module (leisen_reimer) runs on synthetic local-math context.
"""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import dashboard.quant_console_agent as qca
from shared.module_registry import all_modules


class FakeModelClient:
    """Scriptable model client. Returns a fixed list of tool calls."""

    def __init__(self, calls):
        self.calls = calls
        self.saw_prompt = None
        self.saw_tools = None

    def plan_tool_calls(self, prompt, tools):
        self.saw_prompt = prompt
        self.saw_tools = tools
        return self.calls


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(qca.router)
    return app


@pytest.fixture()
def client(monkeypatch):
    return TestClient(_app())


# ---------------------------------------------------------------------------
# 1. Tool manifest is the single source of truth over the catalog
# ---------------------------------------------------------------------------
def test_manifest_built_from_all_modules_has_leisen_reimer():
    manifest = qca.build_tool_manifest()
    slugs = {entry["name"] for entry in manifest}
    catalog_slugs = {spec.slug for spec in all_modules()}
    assert slugs == catalog_slugs
    assert "leisen_reimer" in slugs

    lr = next(e for e in manifest if e["name"] == "leisen_reimer")
    assert lr["description"].strip()  # real, non-empty description
    # ticker is required per leisen_reimer's InputSpec
    assert lr["input_schema"]["required"] == ["ticker"] or "ticker" in lr["input_schema"]["required"]
    assert "ticker" in lr["input_schema"]["properties"]
    # sample merged in as hint properties
    assert "strike" in lr["input_schema"]["properties"]


def test_manifest_one_entry_per_module():
    manifest = qca.build_tool_manifest()
    assert len(manifest) == len({m["name"] for m in manifest})
    assert len(manifest) == len(all_modules())


# ---------------------------------------------------------------------------
# 2. Confirmation gate (plan step 5): confirm=False proposes, runs nothing
# ---------------------------------------------------------------------------
def test_confirm_false_proposes_but_runs_nothing(monkeypatch, client):
    fake = FakeModelClient(
        [{"name": "leisen_reimer", "arguments": {"ticker": "SPY"}}]
    )
    monkeypatch.setattr(qca, "MODEL_CLIENT_FACTORY", lambda: fake)
    monkeypatch.setattr(qca, "read_day_count", lambda day: 0)
    monkeypatch.setattr(qca, "bump_day_count", lambda day: 1)

    res = client.post("/api/quant-console/agent", json={"prompt": "price SPY", "confirm": False})
    assert res.status_code == 200
    body = res.json()
    assert len(body["proposed"]) == 1
    assert body["proposed"][0]["slug"] == "leisen_reimer"
    assert body["executed"] == []
    # The model still ran (needed to produce the proposal) but no widget ran.


# ---------------------------------------------------------------------------
# 3. confirm=True executes in-process via a local-math module (no network)
# ---------------------------------------------------------------------------
def test_confirm_true_executes_leisen_reimer_offline(monkeypatch, client, tmp_path):
    # Layout write-back is real now (LayoutStore landed); keep the test
    # hermetic by pointing the shared store at tmp via WIDGET_CACHE_PATH.
    monkeypatch.setenv("WIDGET_CACHE_PATH", str(tmp_path / "widgets.db"))
    fake = FakeModelClient(
        [
            {
                "name": "leisen_reimer",
                "arguments": {
                    "ticker": "SPY",
                    "spot": 500.0,
                    "strike": 550,
                    "target_years": 0.25,
                    "option_type": "call",
                    "sigma": 0.2,
                },
            }
        ]
    )
    # monkeypatch factory + daily counters so no real client/sqlite is touched
    monkeypatch.setattr(qca, "MODEL_CLIENT_FACTORY", lambda: fake)
    monkeypatch.setattr(qca, "read_day_count", lambda day: 0)
    monkeypatch.setattr(qca, "bump_day_count", lambda day: 1)

    res = client.post(
        "/api/quant-console/agent",
        json={"prompt": "price SPY at 550", "confirm": True},
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body["executed"]) == 1
    run = body["executed"][0]
    assert run["slug"] == "leisen_reimer"
    assert run["status"] == "ok"
    assert run["metrics"]["model"] == "leisen_reimer"
    assert isinstance(run["metrics"]["price"], float)
    assert run["metrics"]["price"] > 0
    # Layout write-back is live: LayoutStore (dashboard/layouts.py) receives
    # the prompt-produced widget, tagged with added_from_prompt.
    assert "layout" in run
    assert run["layout"].get("added") is True, run["layout"]
    assert run["layout"]["slug"] == "leisen_reimer"


# ---------------------------------------------------------------------------
# 4. Aggregate daily cap (plan step 6) -> 429
# ---------------------------------------------------------------------------
def test_daily_cap_returns_429(monkeypatch, client):
    monkeypatch.setattr(qca, "read_day_count", lambda day: qca.AGENT_DAILY_CAP)
    monkeypatch.setattr(qca, "bump_day_count", lambda day: qca.AGENT_DAILY_CAP + 1)
    res = client.post("/api/quant-console/agent", json={"prompt": "anything"})
    assert res.status_code == 429


# ---------------------------------------------------------------------------
# 5. 503 when no key + default factory
# ---------------------------------------------------------------------------
def test_503_when_no_key_and_default_factory(monkeypatch, client):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(qca, "MODEL_CLIENT_FACTORY", qca._default_model_client_factory)
    monkeypatch.setattr(qca, "read_day_count", lambda day: 0)
    monkeypatch.setattr(qca, "bump_day_count", lambda day: 1)
    res = client.post("/api/quant-console/agent", json={"prompt": "price SPY"})
    assert res.status_code == 503
    assert "model client not configured" in res.json()["detail"]


# ---------------------------------------------------------------------------
# 6. Per-request tool-call cap truncation (plan step 2)
# ---------------------------------------------------------------------------
def test_tool_call_cap_truncates(monkeypatch, client):
    calls = [{"name": "leisen_reimer", "arguments": {"ticker": "T"}}] * 10
    fake = FakeModelClient(calls)
    monkeypatch.setattr(qca, "MODEL_CLIENT_FACTORY", lambda: fake)
    monkeypatch.setattr(qca, "read_day_count", lambda day: 0)
    monkeypatch.setattr(qca, "bump_day_count", lambda day: 1)
    monkeypatch.setattr(qca, "MAX_TOOL_CALLS_PER_REQUEST", 6)

    res = client.post("/api/quant-console/agent", json={"prompt": "lots", "confirm": True})
    assert res.status_code == 200
    body = res.json()
    assert body["truncated"] is True
    assert len(body["proposed"]) == 6
    assert body["tool_call_cap"] == 6