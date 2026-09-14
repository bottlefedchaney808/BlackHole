"""The desk's selection contract: everything you can pick, runs.

Two ways a card used to be a dead end you could only discover by adding it
and clicking Run:

* a selection-only pipeline marker (`runnable=False`) whose run() raises a
  paragraph explaining it only works inside Vol_Suite's context-mode
  pipeline -- `group_screener` did exactly this on a live desk;
* a module that runs fine but draws nothing, because the picture is in a
  *different* registered slug -- the four `surface_*` modules compute a grid
  and the `surface-explorer` tool is what renders it.

`ModuleSpec.is_pickable()` is the single verdict a picker acts on, and the
run route refuses an unpickable slug up front instead of letting run() do it.
"""

from __future__ import annotations

import pytest

from shared.module_registry import all_modules, resolve_modules


@pytest.mark.unit
def test_every_unpickable_module_says_why():
    """An unpickable slug is either a pipeline step or has a replacement."""
    for module in all_modules():
        if module.is_pickable():
            continue
        assert not module.runnable or module.superseded_by, (
            f"{module.slug} is unpickable for no declared reason"
        )


@pytest.mark.unit
def test_a_superseded_module_names_a_registered_pickable_slug():
    """'Use X instead' is only useful if X exists and can itself be picked."""
    index = {m.slug: m for m in all_modules()}
    for module in all_modules():
        if not module.superseded_by:
            continue
        replacement = index.get(module.superseded_by)
        assert replacement is not None, (
            f"{module.slug} points at unregistered {module.superseded_by!r}"
        )
        assert replacement.is_pickable(), (
            f"{module.slug} points at {replacement.slug!r}, which is itself "
            "not pickable"
        )


@pytest.mark.unit
def test_the_four_surface_modules_route_to_the_renderer():
    for slug in (
        "surface_greek",
        "surface_market_iv",
        "surface_flow_strike_time",
        "surface_flow_strike_expiry",
    ):
        spec = resolve_modules([slug])[0]
        assert not spec.is_pickable()
        assert spec.superseded_by == "surface-explorer"


@pytest.mark.unit
def test_the_renderer_can_reach_every_mode_it_supports():
    """surface-explorer replaces four slugs, so a card must be able to pick
    which one. Without a `mode` param it could only ever run the default."""
    spec = resolve_modules(["surface-explorer"])[0]
    modes = {p.name: p for p in spec.params}
    assert "mode" in modes
    assert {
        "greek_surface",
        "iv_surface_market",
        "flow_strike_time",
        "flow_strike_expiry",
    } <= set(modes["mode"].choices)
    assert "greek" in modes


@pytest.mark.unit
def test_selection_only_markers_stay_unpickable():
    for slug in (
        "group_screener",
        "vol_surface_2d",
        "vrp_term_structure",
        "sentiment_backtest",
    ):
        spec = resolve_modules([slug])[0]
        assert not spec.runnable
        assert not spec.is_pickable()
