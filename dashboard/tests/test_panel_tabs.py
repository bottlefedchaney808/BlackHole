"""Tests for the three panel tabs: Volatility, Models, Sentiment.

These pages exist because several modules produce nothing a generic widget
card can render (see dashboard/panels.py's docstring). The tests below pin
the parts of that fix that are easy to regress silently:

* the panel catalog and the pages that render from it;
* `GET /files` serving `outputs/` -- the 403 that made every chart a broken
  image -- while still refusing anything outside the repo's artifact trees;
* the scan library's containment check, which is the only thing stopping a
  browser-supplied path from reading arbitrary files;
* that no panel wraps a module slug that does not exist, and that no panel
  wraps one whose `run()` is a selection-only marker that raises.
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest
from fastapi.testclient import TestClient

import dashboard.app as dashboard_app
from dashboard import panels

pytestmark = pytest.mark.unit

client = TestClient(dashboard_app.app)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/volatility", "/models", "/sentiment"])
def test_panel_pages_render(path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


@pytest.mark.parametrize("path", ["/volatility", "/models", "/sentiment"])
def test_panel_pages_load_the_shared_tab_engine(path):
    html = client.get(path).text
    assert "/static/js/panel-tab.js?v=" in html
    assert 'id="panelData"' in html


def test_nav_replaces_suite_output_with_the_new_tabs():
    """The Output tab is gone from the nav; its slot is Volatility."""
    html = client.get("/").text
    assert 'href="/volatility"' in html
    assert 'href="/models"' in html
    assert 'href="/sentiment"' in html
    assert ">Suite output<" not in html


def test_suite_output_routes_still_resolve():
    """Dropped from the nav, not deleted: a direct link to an output file
    must not 404 just because the tab is no longer a destination."""
    resp = client.get("/suites/options")
    assert resp.status_code == 200


def test_models_page_declares_leisen_reimer_as_the_default_pricer():
    """A regression to plain CRR for the default path is a bug Jason has
    reported more than once (CLAUDE.md fragile surfaces)."""
    html = client.get("/models").text
    assert "'leisen_reimer'" in html
    assert "localStorage.getItem(DEFAULT_KEY) || 'leisen_reimer'" in html


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def test_panel_catalog_filters_by_tab():
    body = client.get("/api/panels", params={"tab": "volatility"}).json()
    ids = {p["id"] for p in body["panels"]}
    assert {"garch", "variance_swap", "vrp_term_structure", "correlation_matrix"} <= ids
    assert "leisen_reimer" not in ids  # that one lives on Models


def test_panel_catalog_without_tab_returns_everything():
    body = client.get("/api/panels").json()
    assert len(body["panels"]) == len(panels.PANELS)


def test_every_panel_id_is_unique():
    ids = [p.id for p in panels.PANELS]
    assert len(ids) == len(set(ids))


def test_unknown_panel_is_404_not_500():
    assert client.post("/api/panels/not-a-panel/run", json={}).status_code == 404


# ---------------------------------------------------------------------------
# Panels must wrap real, runnable modules
# ---------------------------------------------------------------------------


def _module_panel_slugs():
    """The slug each `module_panel(...)`-backed panel closes over.

    Read off the closure rather than duplicating the mapping in the test:
    a duplicated list would drift and stop catching the thing this guards.
    """
    out = {}
    for spec in panels.PANELS:
        closure = getattr(spec.run, "__closure__", None) or ()
        names = getattr(spec.run, "__code__", None)
        if not names or "slug" not in names.co_freevars:
            continue
        index = names.co_freevars.index("slug")
        out[spec.id] = closure[index].cell_contents
    return out


def test_every_module_panel_names_a_registered_slug():
    from shared.module_registry import all_modules

    known = {m.slug for m in all_modules()}
    for panel_id, slug in _module_panel_slugs().items():
        assert slug in known, f"panel {panel_id!r} wraps unknown slug {slug!r}"


def test_no_panel_wraps_a_selection_only_marker():
    """`runnable=False` slugs raise NotImplementedError by design -- wrapping
    one in a panel would put a permanently-failing card on a tab. This is
    exactly what made VRP look broken: the Vol_Suite slug is a marker, and
    the runnable VRP is the Tools entry point."""
    from shared.module_registry import all_modules

    markers = {m.slug for m in all_modules() if not getattr(m, "runnable", True)}
    assert "vrp_term_structure" in markers  # guard the premise itself
    for panel_id, slug in _module_panel_slugs().items():
        assert slug not in markers, (
            f"panel {panel_id!r} wraps selection-only slug {slug!r}, whose run() raises"
        )


def test_vrp_panel_does_not_go_through_the_marker_slug():
    spec = panels.get_panel("vrp_term_structure")
    assert spec is not None
    assert spec.id not in _module_panel_slugs()


# ---------------------------------------------------------------------------
# GET /files -- the 403 that made every chart a broken image
# ---------------------------------------------------------------------------


def test_files_serves_from_outputs(tmp_path_factory):
    """`run_selected_modules` mints output_dir=<repo>/outputs/<run_id>/, so
    every chart a module writes lands there. Refusing that directory is why
    variance_swap and garch charts never appeared."""
    target = Path(dashboard_app.ROOT) / "outputs" / "_pytest_files" / "chart.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\x89PNG\r\n\x1a\n")
    try:
        resp = client.get("/files", params={"path": str(target)})
        assert resp.status_code == 200
    finally:
        target.unlink(missing_ok=True)
        target.parent.rmdir()


def test_files_serves_from_artifacts():
    target = Path(dashboard_app.ROOT) / "artifacts" / "_pytest_files" / "chart.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\x89PNG\r\n\x1a\n")
    try:
        resp = client.get("/files", params={"path": str(target)})
        assert resp.status_code == 200
    finally:
        target.unlink(missing_ok=True)
        target.parent.rmdir()


@pytest.mark.parametrize(
    "relative",
    [".env", "CLAUDE.md", os.path.join("outputs", "..", ".env")],
)
def test_files_refuses_paths_outside_the_artifact_trees(relative):
    resp = client.get(
        "/files", params={"path": os.path.join(dashboard_app.ROOT, relative)}
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Scan library
# ---------------------------------------------------------------------------


def test_library_index_returns_shelves_and_notes():
    body = client.get("/api/library").json()
    assert "notes" in body and "shelves" in body
    for note in body["notes"]:
        assert {"note", "shelf", "where", "modified", "path"} <= set(note)


def test_library_note_refuses_a_path_outside_the_shelves():
    """The path comes from the browser. Without the containment check this
    endpoint reads any file on the machine."""
    resp = client.get(
        "/api/library/note", params={"path": os.path.join(dashboard_app.ROOT, ".env")}
    )
    assert resp.status_code == 403


def test_library_note_missing_file_is_404():
    inside = panels.LIBRARY_ROOTS[1][1]  # the repo's own trading_journal/
    resp = client.get(
        "/api/library/note", params={"path": os.path.join(inside, "no-such-note.md")}
    )
    assert resp.status_code in (404, 403)


def test_library_classifies_notes_onto_shelves():
    assert panels.classify_note("X.com buzz 20260910.md") == "x/twitter"
    assert panels.classify_note("Rumor watchlist GME 20260909.md") == "rumors"
    assert panels.classify_note("Thu open plays 20260910.md") == "morning scans"
    assert panels.classify_note("reddit wsb sweep.md") == "reddit"
    assert panels.classify_note("unrelated file.md") == "other"


def test_library_panel_filters_to_the_scope_ticker():
    everything = panels.library_panel({})["metrics"]["notes"]
    scoped = panels.library_panel({"ticker": "GME"})["metrics"]["notes"]
    assert len(scoped) <= len(everything)


# ---------------------------------------------------------------------------
# Portfolio surface
# ---------------------------------------------------------------------------


def test_portfolio_surface_without_a_basket_is_idle_not_an_error():
    result = panels.portfolio_surface_panel({})
    assert result["status"] == "idle"
    assert "Basket = my book" in result["metrics"]["message"]


def test_portfolio_weights_use_market_value_and_fall_back_to_equal():
    book = {
        "positions": [
            {"ticker": "SPY", "market_value": 300},
            {"ticker": "NOK", "market_value": 100},
        ]
    }
    assert panels._weights_from_context({"positions": book}, ["SPY", "NOK"]) == [
        0.75,
        0.25,
    ]
    assert panels._weights_from_context({}, ["SPY", "NOK"]) == [0.5, 0.5]


def test_portfolio_surface_interpolation_never_extrapolates():
    """A name whose chain does not reach a moneyness must drop out of that
    cell, not pad it with an edge value that was never quoted."""
    assert panels._interp_row([1.0, 2.0, 3.0], [10.0, 20.0, 30.0], 2.5) == 25.0
    assert panels._interp_row([1.0, 2.0, 3.0], [10.0, 20.0, 30.0], 0.5) is None
    assert panels._interp_row([1.0, 2.0, 3.0], [10.0, 20.0, 30.0], 9.0) is None


# ---------------------------------------------------------------------------
# The desk's surfaces panel is no longer hardcoded to SPXW
# ---------------------------------------------------------------------------


def test_surfaces_subject_falls_back_to_the_index_with_no_book(monkeypatch):
    monkeypatch.setattr(dashboard_app, "SURFACES_TICKER_OVERRIDE", "")

    class _EmptyCache:
        def get(self, _key):
            return None

    monkeypatch.setattr(dashboard_app, "_widget_cache", lambda: _EmptyCache())
    ticker, reason, portfolio = dashboard_app._surfaces_subject()
    assert ticker == dashboard_app.SURFACES_FALLBACK_TICKER
    assert "no priced positions" in reason
    assert portfolio == []


def test_surfaces_subject_picks_the_largest_holding(monkeypatch):
    monkeypatch.setattr(dashboard_app, "SURFACES_TICKER_OVERRIDE", "")

    class _Cache:
        def get(self, _key):
            return {
                "payload": {
                    "positions": [
                        {"ticker": "NOK", "market_value": 400},
                        {"ticker": "SPY", "market_value": 1200},
                        {"ticker": "SPY", "market_value": 300},
                    ]
                }
            }

    monkeypatch.setattr(dashboard_app, "_widget_cache", lambda: _Cache())
    ticker, reason, portfolio = dashboard_app._surfaces_subject()
    # SPY's two legs aggregate to 1500 vs NOK's 400.
    assert ticker == "SPY"
    assert "largest position" in reason
    assert [row["ticker"] for row in portfolio] == ["SPY", "NOK"]
    assert portfolio[0]["weight_pct"] == pytest.approx(78.9, abs=0.1)


def test_surfaces_subject_honours_the_pin(monkeypatch):
    monkeypatch.setattr(dashboard_app, "SURFACES_TICKER_OVERRIDE", "QQQ")
    ticker, reason, _ = dashboard_app._surfaces_subject()
    assert ticker == "QQQ"
    assert "pinned" in reason
