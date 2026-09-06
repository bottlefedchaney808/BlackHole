"""Tests for Phase 5: Overview page uses quant-widget instances.

The Overview page (GET /) now uses layout-driven <quant-widget> elements
instead of hardcoded widget divs. These tests verify the layout persistence
integration works.
"""

import pytest
from fastapi.testclient import TestClient

from dashboard.app import app

pytestmark = pytest.mark.unit
client = TestClient(app)


def test_overview_route_returns_200():
    """Overview page renders without error."""
    r = client.get("/")
    assert r.status_code == 200


def test_overview_has_quant_widget_elements():
    """Overview page uses quant-widget custom elements."""
    r = client.get("/")
    html = r.text
    # The page should have quant-widget elements for each widget
    assert "quant-widget" in html
    assert 'slug="positions"' in html
    assert 'slug="signals"' in html
    assert 'slug="position_analysis"' in html
    assert 'slug="surfaces"' in html


def test_overview_layout_api_integration():
    """Overview page fetches layout from /api/layout/overview."""
    r = client.get("/")
    html = r.text
    # Layout persistence JS is present
    assert "fetch('/api/layout/" in html
    assert "PAGE = 'overview'" in html


def test_overview_layout_driven_widget_instances():
    """Overview page renders widget instances from layout when saved."""
    r = client.get("/")
    html = r.text
    # The widget-grid div exists
    assert "widget-grid" in html
    # Layout persistence script exists
    assert "fetch('/api/layout/" in html
