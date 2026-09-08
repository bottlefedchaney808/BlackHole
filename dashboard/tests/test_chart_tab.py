"""Covers the /chart tab -- iframes the existing native chart_app (:8791).
See docs/superpowers/specs/2026-08-27-swaps-dashboard-split-design.md."""
from fastapi.testclient import TestClient

from dashboard.app import app

client = TestClient(app)


def test_chart_page_launches():
    r = client.get('/chart')
    assert r.status_code == 200
    assert 'http://127.0.0.1:8791' in r.text


def test_nav_no_longer_links_swaps_directly():
    r = client.get('/chart')
    assert r.status_code == 200
    assert 'href="/chart"' in r.text


def test_chart_page_is_full_bleed():
    """Chart opts out of base.html's centered 1500px column via body_class."""
    r = client.get('/chart')
    assert r.status_code == 200
    assert 'page-chart' in r.text
    assert 'body.page-chart main,' in r.text
    assert 'max-width: none;' in r.text


def test_chart_layout_is_chart_left_sidebar_right():
    """Chart column takes the stretch (minmax(0, 1fr)); sidebar is 280px."""
    r = client.get('/chart')
    html = r.text
    assert 'grid-template-columns: minmax(0, 1fr) 280px;' in html
    # chart iframe is emitted before the sidebar -> chart renders on the left
    assert html.index('id="chartFrame"') < html.index('chart-side-panel')
    assert 'height: calc(100vh - 48px)' in html
    assert 'height:100%' in html


def test_chart_pings_iframe_to_resize():
    """Parent nudges the embedded ECharts canvas, which inits before the
    iframe has its final box."""
    html = client.get('/chart').text
    assert "postMessage({ type: 'chart-resize' }, '*')" in html
    assert "visibilitychange" in html


def test_overview_hero_points_at_native_chart_not_self():
    """Empty chart_app_url made src='?ticker=...' which nested this dashboard."""
    r = client.get('/')
    assert r.status_code == 200
    html = r.text
    assert 'page-overview' in html
    assert 'http://127.0.0.1:8791?ticker=SPY&interval=15m' in html
    assert 'src="?ticker=' not in html
    assert 'overview-hero-chart' in html
    assert 'overview-hero-metrics' in html
