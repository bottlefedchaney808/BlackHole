"""Tests for page rendering: index.html (the desk) and chart.html.

Both pages changed shape when Overview and Quant Console merged:

* the desk imports sync-bus directly (it owns the page-level scope bar) as
  well as quant-widget, and keys its layout under `desk`;
* the chart sidebar dropped its generic catalog picker -- any of 51 modules
  could be dropped next to the chart, unwired to the charted symbol -- for
  fixed status: the book filtered to the charted symbol, plus the
  background-fed status panels.
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
    """Desk page includes the quant-widget custom element definition."""
    resp = client.get('/')
    html = resp.text
    assert "import '/static/js/quant-widget.js?v=" in html


def test_desk_imports_the_sync_bus_directly():
    """Unlike the widget-only pages, the desk owns the page-level scope bar,
    so it drives syncBus itself rather than only through quant-widget."""
    html = client.get('/').text
    assert "import { syncBus } from '/static/js/sync-bus.js?v=" in html


def test_module_imports_are_cache_busted():
    """Module URLs carry ?v=<newest js mtime>. Without it a browser holding a
    cached copy keeps running the old file, so a shipped JS fix stays
    invisible in the page and reads as 'the fix did not work'."""
    html = client.get('/').text
    assert '/static/js/quant-widget.js?v=' in html
    version = html.split('/static/js/quant-widget.js?v=')[1].split("'")[0]
    assert version.isdigit() and int(version) > 0


def test_overview_talks_to_layout_api():
    """Desk page persists its tool rail under the `desk` layout key."""
    resp = client.get('/')
    html = resp.text
    assert "fetch('/api/layout/" in html
    assert "PAGE = 'desk'" in html


def test_desk_has_status_panels_and_a_book():
    """The desk ships the background-fed status panels plus its own book
    panel; `positions` is no longer a widget, it is the scope source."""
    resp = client.get('/')
    html = resp.text
    assert 'slug="signals" mode="status"' in html
    assert 'slug="position_analysis" mode="status"' in html
    assert 'slug="surfaces" mode="status"' in html
    assert 'id="bookPanel"' in html


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
    assert "import '/static/js/quant-widget.js?v=" in html
    # sync-bus is imported transitively via quant-widget.js, not directly
    assert "from '/static/js/sync-bus.js" not in html


def test_chart_has_side_panel():
    """Chart page has a side-panel region beside the chart."""
    resp = client.get('/chart')
    html = resp.text
    assert 'chart-side-panel' in html


def test_chart_sidebar_has_no_generic_catalog_picker():
    """The sidebar no longer lets arbitrary modules be dropped next to the
    chart: they were unrelated to the charted symbol and each arrived with
    its own unwired ticker/expiry/basket inputs."""
    html = client.get('/chart').text
    assert 'id="chartAddWidget"' not in html
    assert "fetch('/api/widgets/catalog'" not in html


def test_chart_sidebar_shows_the_book_for_the_charted_symbol():
    """The chart app announces its symbol over postMessage; the sidebar
    filters the position book to it."""
    html = client.get('/chart').text
    assert 'chart-symbol' in html
    assert 'id="sidePositions"' in html
    assert '/api/widgets/positions/run' in html


def test_chart_sidebar_status_panels_have_no_scope_inputs():
    html = client.get('/chart').text
    assert 'slug="signals" mode="status"' in html
    assert 'slug="position_analysis" mode="status"' in html


def test_static_assets_force_revalidation():
    """The dashboard's JS is unversioned, so without Cache-Control the browser
    serves a stale copy from its heuristic cache and a shipped fix looks like
    it never landed."""
    resp = client.get('/static/js/widget-renderers.js')
    assert resp.status_code == 200
    assert resp.headers.get('cache-control') == 'no-cache'
    assert resp.headers.get('etag')  # revalidation stays cheap
