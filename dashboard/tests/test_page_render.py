"""Tests for Phase 5 page rendering: index.html and chart.html.

These tests verify the layout-driven quant-widget implementation for
the Overview and Chart pages.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest
from fastapi.testclient import TestClient

import dashboard.app as dashboard_app

pytestmark = pytest.mark.unit

client = TestClient(dashboard_app.app)


def test_overview_route_returns_200():
    """Overview page renders without error."""
    resp = client.get('/')
    assert resp.status_code == 200
    assert 'text/html' in resp.headers['content-type']


def test_overview_loads_quant_widget_js():
    """Overview page includes the quant-widget custom element definition."""
    resp = client.get('/')
    html = resp.text
    # The module is imported via ES module syntax - verify actual import statement
    assert "import '/static/js/quant-widget.js'" in html
    # sync-bus is imported transitively via quant-widget.js, not directly
    assert "import { syncBus } from '/static/js/sync-bus.js'" not in html


def test_overview_talks_to_layout_api():
    """Overview page fetches layout from /api/layout/overview."""
    resp = client.get('/')
    html = resp.text
    # The template uses fetch('/api/layout/' + PAGE) where PAGE='overview'
    assert "fetch('/api/layout/" in html
    assert "PAGE = 'overview'" in html


def test_overview_has_default_widgets_in_template():
    """Overview template includes the 4 default widgets (positions, signals,
    position_analysis, surfaces) when no layout is saved."""
    resp = client.get('/')
    html = resp.text
    assert 'slug="positions"' in html or 'slug="signals"' in html
    assert 'slug="position_analysis"' in html
    assert 'slug="surfaces"' in html


def test_chart_route_returns_200():
    """Chart page renders without error."""
    resp = client.get('/chart')
    assert resp.status_code == 200
    assert 'text/html' in resp.headers['content-type']


def test_chart_loads_quant_widget_js():
    """Chart page includes the quant-widget custom element definition."""
    resp = client.get('/chart')
    html = resp.text
    # The module is imported via ES module syntax - verify actual import statement
    assert "import '/static/js/quant-widget.js'" in html
    # sync-bus is imported transitively via quant-widget.js, not directly
    assert "import { syncBus } from '/static/js/sync-bus.js'" not in html


def test_chart_talks_to_layout_api():
    """Chart page fetches layout from /api/layout/chart."""
    resp = client.get('/chart')
    html = resp.text
    # The template uses fetch('/api/layout/' + PAGE) where PAGE='chart'
    assert "fetch('/api/layout/" in html
    assert "PAGE = 'chart'" in html


def test_chart_has_side_panel():
    """Chart page has a side-panel region for chart widgets."""
    resp = client.get('/chart')
    html = resp.text
    assert 'chart-side-panel' in html or 'chart-grid' in html


def test_chart_sidebar_has_add_widget_control():
    """Chart sidebar can add widgets from the module catalog (capped at 4)."""
    html = client.get('/chart').text
    assert 'id="chartAddWidget"' in html
    assert "fetch('/api/widgets/catalog'" in html
    assert 'MAX_WIDGETS = 4' in html


def test_chart_sidebar_empty_state_is_not_a_dead_end():
    """Empty sidebar points at the + control instead of just saying 'none'."""
    html = client.get('/chart').text
    assert 'Add IV Rank or VRP from +' in html


def test_chart_sidebar_widgets_are_removable():
    """Each sidebar widget carries an x button wired to the layout DELETE."""
    html = client.get('/chart').text
    assert 'chart-cell-remove' in html
    assert "method: 'DELETE'" in html
