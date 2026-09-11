"""Tests for the desk page (GET /) -- the merged Overview + Quant Console.

The page's contract changed shape when the two surfaces were merged:

* scope is entered ONCE, in a page-level scope bar, and normally comes from
  the position book rather than being typed;
* the book is rendered by the page itself (it is the scope source), not by a
  <quant-widget> with its own inert ticker/expiry/basket inputs;
* the cache-backed widgets render as status panels (`mode="status"`) -- no
  scope inputs, no Run button;
* tool cards are added from the catalog and render with `scope-ui="hidden"`,
  so they follow the bar instead of carrying their own copy of the fields.

These tests pin that contract; the previous versions pinned the four
hardcoded widget instances and the `PAGE = 'overview'` layout key, both of
which the merge replaced.
"""

import pytest
from fastapi.testclient import TestClient

from dashboard.app import app

pytestmark = pytest.mark.unit
client = TestClient(app)


def test_desk_route_returns_200():
    r = client.get("/")
    assert r.status_code == 200


def test_desk_has_a_single_scope_bar():
    """Scope is entered in exactly one place on the page."""
    html = client.get("/").text
    assert 'id="scopeBar"' in html
    assert 'id="scTicker"' in html
    assert 'id="scExpiry"' in html
    assert 'id="scBasketChips"' in html
    # Exactly one ticker field and one expiry field in the page markup.
    assert html.count('id="scTicker"') == 1
    assert html.count('id="scExpiry"') == 1


def test_desk_renders_the_book_itself_not_as_a_widget():
    """The book is the scope source, so it is page markup, not a quant-widget
    with inert scope inputs (which is what it used to be)."""
    html = client.get("/").text
    assert 'id="bookPanel"' in html
    assert 'id="bookTable"' in html
    assert 'slug="positions"' not in html


def test_desk_cache_widgets_render_as_status_panels():
    """signals / position_analysis / surfaces are background-fed: no Run,
    no scope inputs."""
    html = client.get("/").text
    for slug in ("signals", "position_analysis", "surfaces"):
        assert f'slug="{slug}" mode="status"' in html


def test_desk_tool_cards_do_not_carry_their_own_scope_inputs():
    """Tool widgets are created with scope-ui hidden so the page's one scope
    bar is the only place ticker/expiry/basket are typed."""
    html = client.get("/").text
    assert "setAttribute('scope-ui', 'hidden')" in html


def test_desk_uses_the_desk_layout_key():
    html = client.get("/").text
    assert "fetch('/api/layout/" in html
    assert "PAGE = 'desk'" in html


def test_desk_offers_the_catalog_as_a_tool_picker():
    html = client.get("/").text
    assert 'id="toolPicker"' in html
    assert "/api/widgets/catalog" in html


def test_desk_book_publishes_scope_and_context():
    """Loading the book sets the basket and injects positions into the
    Context Store, so a tool run is fed the book without a button press."""
    html = client.get("/").text
    assert "/api/context/inject-positions" in html
    assert "Basket = my book" in html


def test_quant_console_redirects_to_the_desk():
    r = client.get("/quant", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/"
