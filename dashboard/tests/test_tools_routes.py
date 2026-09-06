"""Phase 5 tests for tools_*.html page retirement.

The legacy /tools routes now redirect to Quant Console where the generic
widgets provide the same functionality. These tests verify the redirect
behavior and that the old bespoke pages no longer render.
"""

from fastapi.testclient import TestClient
from dashboard.app import app

client = TestClient(app)


def test_vrp_term_structure_redirects_to_quant():
    """Phase 5: /tools/vrp-term-structure redirects to /quant."""
    r = client.get("/tools/vrp-term-structure")
    assert r.status_code == 200  # After redirect
    # The Quant Console page should be served
    assert "Quant Console" in r.text or "quant-widget" in r.text


def test_simulations_redirects_to_quant():
    """Phase 5: /tools/simulations redirects to /quant."""
    r = client.get("/tools/simulations")
    assert r.status_code == 200  # After redirect
    assert "Quant Console" in r.text or "quant-widget" in r.text


def test_directional_engine_redirects_to_quant():
    """Phase 5: /tools/directional-engine redirects to /quant."""
    r = client.get("/tools/directional-engine")
    assert r.status_code == 200  # After redirect
    assert "Quant Console" in r.text or "quant-widget" in r.text


def test_hedge_optimizer_redirects_to_quant():
    """Phase 5: /tools/hedge-optimizer redirects to /quant."""
    r = client.get("/tools/hedge-optimizer")
    assert r.status_code == 200  # After redirect
    assert "Quant Console" in r.text or "quant-widget" in r.text


def test_surface_explorer_redirects_to_quant():
    """Phase 5: /tools/surface-explorer redirects to /quant."""
    r = client.get("/tools/surface-explorer")
    assert r.status_code == 200  # After redirect
    assert "Quant Console" in r.text or "quant-widget" in r.text


def test_options_strategy_redirects_to_quant():
    """Phase 5: /tools/options-strategy redirects to /quant."""
    r = client.get("/tools/options-strategy")
    assert r.status_code == 200  # After redirect
    assert "Quant Console" in r.text or "quant-widget" in r.text
