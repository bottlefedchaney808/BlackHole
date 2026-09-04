"""test_quant_view.py

Covers the Phase 5 step 3 rebuild of `GET /quant` (widget-native console hub,
2026-09-03 plan; replaced the Task 6 launch-card view of the 2026-08-01 plan).

The launch-card UI (per-suite cards, module picker, POST /run/{suite},
/runs/{run_id} polling, dispatch-worker buttons) was removed when Phase 7
retired the orchestrator run-tracking machinery. These tests pin what *is*
mechanically checkable without a browser about the new hub:

- the route exists and renders the shell (alerts + alert_status carried);
- the page loads the widget JS modules (sync-bus, quant-widget) that talk to
  the generic widget API;
- the launch-card machinery is *gone* (no data-module-id cards, no
  POST /run/ or /runs/ polling JS, no dispatch buttons);
- the nav link survives.

Real browser rendering / click-through of catalog -> add -> run is NOT
covered here; that's the manual smoke pass (see CLAUDE.md's UI-change
verification convention).

`dashboard.app` is imported in-process, same convention as the other
dashboard tests -- no .env-loading behavior is under test here.
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import dashboard.app as dashboard_app  # noqa: E402

pytestmark = pytest.mark.unit

client = TestClient(dashboard_app.app)


def test_quant_route_returns_200():
    resp = client.get('/quant')
    assert resp.status_code == 200
    assert 'text/html' in resp.headers['content-type']


def test_quant_route_loads_widget_js_modules():
    """The hub page must load the shared widget frontend (sync bus +
    custom element). The catalog browser, console grid, and provenance
    panel all run through these; without them the page is a dead shell.
    """
    resp = client.get('/quant')
    html = resp.text
    assert '/static/js/sync-bus.js' in html
    assert '/static/js/quant-widget.js' in html
    assert 'quant-widget' in html  # the custom element tag or its registry


def test_quant_route_talks_to_generic_widget_api():
    """The page's own JS (catalog browser / console grid / provenance) must
    target the Phase 3 generic routes -- not the removed orchestrator
    launching endpoints. Cheap regression guard against a typo'd path.
    """
    resp = client.get('/quant')
    html = resp.text
    assert '/api/widgets/catalog' in html
    assert '/api/layout/quant' in html
    assert '/api/context' in html


def test_quant_route_launch_card_ui_is_gone():
    """Phase 7 removed POST /run/{suite}, GET /runs/{run_id} polling, and
    the dispatch-worker buttons. The rebuilt page must not reference any
    of them -- a stray reference would 404 in the browser.
    """
    resp = client.get('/quant')
    html = resp.text
    assert "data-module-id=" not in html
    assert "startModuleRun" not in html
    assert "pollDispatchJob" not in html
    assert "'/run/'" not in html and '"/run/"' not in html
    assert "'/runs/'" not in html and '"/runs/"' not in html


def test_quant_route_carries_alerts_contract():
    """The alerts banner markup + ackAlert() survive the rebuild (the route
    still passes `alerts` + `alert_status`)."""
    resp = client.get('/quant')
    html = resp.text
    assert 'ackAlert' in html
    assert '/ack' in html


def test_quant_nav_link_present_and_marked_active():
    resp = client.get('/quant')
    html = resp.text
    assert 'href="/quant"' in html
    assert 'class="on"' in html or 'class="tabs-on"' in html or ' on"' in html

    home_resp = client.get('/')
    assert 'href="/quant"' in home_resp.text
