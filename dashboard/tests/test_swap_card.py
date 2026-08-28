"""Covers dashboard.app._swaps_snapshot() -- the file read that replaced the
four swap-DB queries home() used to run on every load. See
docs/superpowers/specs/2026-08-27-swaps-dashboard-split-design.md.
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


def test_tools_page_includes_swap_card(monkeypatch, tmp_path):
    monkeypatch.setattr(
        dashboard_app, "SWAPS_DASHBOARD_SNAPSHOT_PATH", str(tmp_path / "missing.json")
    )
    r = client.get("/tools")
    assert r.status_code == 200
    assert "swapLiveBadge" in r.text


def test_tools_page_shows_ingestion_pill_from_snapshot(monkeypatch, tmp_path):
    """Regression test for CARL R1-F1, moved from home() to /tools now that
    the swap card lives only there: the snapshot's ingestion rows must
    actually render, not just exist in the JSON."""
    snapshot_path = tmp_path / "overview_snapshot.json"
    payload = {
        "generated_at": "2026-08-27T12:00:00Z",
        "stats": {
            "total_records": 5,
            "unique_upis": 2,
            "by_regulator_asset_class": [],
            "earliest_date": None,
            "latest_date": None,
        },
        "stats_error": None,
        "top_products": [],
        "top_error": None,
        "ingestion": [
            {
                "regulator": "SEC",
                "asset_class": "EQ",
                "last_cumulative_date": "2026-08-20",
            }
        ],
        "ingestion_error": None,
        "last_scrape": None,
        "scrape_error": None,
    }
    snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        dashboard_app, "SWAPS_DASHBOARD_SNAPSHOT_PATH", str(snapshot_path)
    )
    r = client.get("/tools")
    assert r.status_code == 200
    assert "SEC/EQ" in r.text
