"""Covers dashboard.app._swaps_snapshot() -- the file read that replaced the
four swap-DB queries home() used to run on every load. See
docs/superpowers/specs/2026-08-27-swaps-dashboard-split-design.md.
Phase 5 updates: tools_*.html pages retired, redirect to Quant Console.
"""

import json

import pytest
from fastapi.testclient import TestClient

import dashboard.app as dashboard_app
from dashboard.app import app

pytestmark = pytest.mark.unit
client = TestClient(app)


def test_swaps_snapshot_returns_none_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        dashboard_app, "SWAPS_DASHBOARD_SNAPSHOT_PATH", str(tmp_path / "missing.json")
    )
    assert dashboard_app._swaps_snapshot() is None


def test_swaps_snapshot_returns_none_on_malformed_json(monkeypatch, tmp_path):
    bad = tmp_path / "overview_snapshot.json"
    bad.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(dashboard_app, "SWAPS_DASHBOARD_SNAPSHOT_PATH", str(bad))
    assert dashboard_app._swaps_snapshot() is None


def test_swaps_snapshot_returns_parsed_dict(monkeypatch, tmp_path):
    good = tmp_path / "overview_snapshot.json"
    payload = {"generated_at": "2026-08-27T12:00:00Z", "stats": {"total_records": 5}}
    good.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(dashboard_app, "SWAPS_DASHBOARD_SNAPSHOT_PATH", str(good))
    assert dashboard_app._swaps_snapshot() == payload


def test_home_page_renders_without_swap_card(monkeypatch, tmp_path):
    """Overview dropped the swap card and the run-trigger form -- both moved
    (swaps to its own dashboard, run-triggering to Quant Console) -- so home()
    no longer needs a snapshot at all, and the page must not render either."""
    monkeypatch.setattr(
        dashboard_app, "SWAPS_DASHBOARD_SNAPSHOT_PATH", str(tmp_path / "missing.json")
    )
    r = client.get("/")
    assert r.status_code == 200
    assert "swapLiveBadge" not in r.text
    assert "Trigger a run" not in r.text
    # Regression test for CARL R2-F4: the non-swap "Orchestrator runs" card must
    # survive the template edit that removed the swap-only cards around it.
    assert "Orchestrator runs" in r.text


# Phase 5: tools_*.html pages retired, redirect to Quant Console
# --------------------------------------------------------------------------


def test_tools_page_redirects_to_quant():
    """Phase 5: /tools routes redirect to Quant Console where widgets provide
    the same functionality. TestClient follows redirects by default, so we
    check the final rendered page contains Quant Console content."""
    r = client.get("/tools")
    assert r.status_code == 200  # After redirect
    # The Quant Console page should be served
    assert "Quant Console" in r.text or "quant-widget" in r.text


def test_tools_page_includes_swap_card_removed():
    """Phase 5: The swap card moved to the swaps dashboard. The old tools page
    is now a redirect and no longer contains swap card content."""
    r = client.get("/tools")
    # After redirect to /quant, the swap card should NOT be present
    assert "swapLiveBadge" not in r.text


def test_tools_subroutes_redirect_to_quant():
    """Phase 5: Sub-routes like /tools/simulations redirect to /quant."""
    r = client.get("/tools/simulations")
    assert r.status_code == 200  # After redirect
    # The Quant Console page should be served
    assert "Quant Console" in r.text or "quant-widget" in r.text
