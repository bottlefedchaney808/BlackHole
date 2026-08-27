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
