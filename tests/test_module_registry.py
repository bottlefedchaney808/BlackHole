"""Tests for shared/module_registry.py -- Phase 1 registry scaffolding.

Phase 1 was registration-only: suite MODULES lists started empty and
all_modules() returned exactly the Tools/registry.py entries, adapted via
from_tool_spec. Later phases (3+) populate suite registries; from Task 4 on,
the aggregation test asserts Tools/ entries plus whatever suite registries
are populated, with no slug collisions across the boundary -- not "Tools
only". Real cycle-detection over the `requires` graph is deferred to a later
phase (see test_requires_cycle_not_yet_detected below) since registration
never executes the requires graph.
"""

from __future__ import annotations

import pytest

from shared.module_registry import (
    ArchiveHint,
    ArtifactRef,
    ModuleResult,
    ModuleSpec,
    all_modules,
    resolve_modules,
)
from Tools.registry import TOOLS


def _dummy_run(context: dict) -> ModuleResult:
    return ModuleResult(status="ok", artifacts=[], metrics={}, context_patch=None)


def test_all_modules_contains_every_tool_registry_entry_adapted():
    modules = all_modules()
    module_slugs = {m.slug for m in modules}
    tool_slugs = {t.slug for t in TOOLS}

    assert tool_slugs, "Tools/registry.py::TOOLS is unexpectedly empty"
    assert tool_slugs <= module_slugs

    for module in modules:
        if module.slug in tool_slugs:
            assert module.suite == "tools"
            assert module.category == "tool"
            assert module.cli_entry is None
            assert module.default_selected is False
            assert module.requires == []
            assert module.archive == ArchiveHint(key_shape="global")


def test_all_modules_aggregates_tools_plus_populated_suite_registries():
    # Phase 1 kept every suite MODULES list empty; Phase 3+ populate them
    # (vol_suite first: dealer-book cluster, then chain_scanner/svi_smile).
    # Aggregation must carry the Tools/ entries plus every populated suite
    # registry, with no slug collisions across the Tools/suite boundary.
    # Written open-ended so later phases adding more suite entries (or
    # populating the other three suites) don't trip it again.
    modules = all_modules()
    slugs = [m.slug for m in modules]
    tool_slugs = {t.slug for t in TOOLS}
    suite_slugs = {m.slug for m in modules if m.suite != "tools"}

    assert tool_slugs <= set(slugs)
    assert suite_slugs, "expected at least one populated suite registry by now"
    assert len(slugs) == len(set(slugs))
    assert set(slugs) == tool_slugs | suite_slugs


def test_all_modules_has_no_duplicate_slugs():
    modules = all_modules()
    slugs = [m.slug for m in modules]
    assert len(slugs) == len(set(slugs))


def test_resolve_modules_unknown_slug_raises_value_error_naming_it():
    with pytest.raises(ValueError, match="not_a_real_slug"):
        resolve_modules(["not_a_real_slug"])


def test_resolve_modules_empty_list_returns_empty_list():
    assert resolve_modules([]) == []


def test_dealer_exposure_alias_resolves_to_expiry_exposure():
    """Catalog slug is expiry_exposure; dealer_exposure remains a resolve alias."""
    canonical = resolve_modules(["expiry_exposure"])
    aliased = resolve_modules(["dealer_exposure"])
    assert canonical and aliased
    assert canonical[0].slug == "expiry_exposure"
    assert aliased[0] is canonical[0]
    assert canonical[0].name == "Expiry Exposure"


def test_resolve_modules_returns_matching_specs_in_requested_order():
    tool_slugs = [t.slug for t in TOOLS]
    assert len(tool_slugs) >= 2

    slugs = [tool_slugs[1], tool_slugs[0]]
    resolved = resolve_modules(slugs)

    assert [m.slug for m in resolved] == slugs


def test_from_tool_spec_run_wraps_dict_result_as_module_result():
    from shared.module_registry import from_tool_spec
    from Tools.registry import ToolSpec

    def tool_run(context: dict) -> dict:
        return {"answer": 42}

    tool_spec = ToolSpec(
        name="Dummy Tool", slug="dummy-tool", description="", run=tool_run
    )
    module_spec = from_tool_spec(tool_spec)

    result = module_spec.run({})
    assert isinstance(result, ModuleResult)
    assert result.status == "ok"
    assert result.metrics == {"answer": 42}
    assert result.artifacts == []
    assert result.context_patch is None


@pytest.mark.skip(
    reason=(
        "Cycle detection over the requires graph belongs to a later phase "
        "(Phase 2 actually executes the requires graph; Phase 1 is "
        "registration-only). Left here as a TODO marker: once a phase adds "
        "execution/dependency-resolution logic, this test should inject two "
        "fake ModuleSpec objects whose requires point at each other and "
        "assert that resolving/executing them raises rather than infinite-"
        "looping or silently ignoring the cycle."
    )
)
def test_requires_cycle_not_yet_detected():
    a = ModuleSpec(
        name="A",
        slug="cycle_a",
        suite="tools",
        category="tool",
        run=_dummy_run,
        cli_entry=None,
        default_selected=False,
        requires=["cycle_b"],
    )
    b = ModuleSpec(
        name="B",
        slug="cycle_b",
        suite="tools",
        category="tool",
        run=_dummy_run,
        cli_entry=None,
        default_selected=False,
        requires=["cycle_a"],
    )
    raise NotImplementedError((a, b))  # placeholder body; test is skipped


def test_module_result_supports_dict_like_get_for_legacy_callers():
    """Covers the ModuleResult vs dict access fix for the dealer-book tab load path
    (dashboard/app.py:dealer_book_load does res["results"][slug].get("artifacts") etc).
    """
    mr = ModuleResult(
        status="ok",
        artifacts=[ArtifactRef(path="chart.png", kind="png")],
        metrics={"interp": "hello", "ticker": "SPY"},
        context_patch={"foo": 1},
    )
    # .get works like dict for the supported keys (and falls back for unknown)
    assert mr.get("status") == "ok"
    assert mr.get("status", "def") == "ok"
    assert mr.get("artifacts", []) == [ArtifactRef(path="chart.png", kind="png")]
    assert mr.get("metrics", {})["interp"] == "hello"
    assert mr.get("metrics", {}).get("interp", "") == "hello"
    assert mr.get("context_patch") == {"foo": 1}
    assert mr.get("nonexistent") is None
    assert mr.get("nonexistent", "DEF") == "DEF"

    # still works as dataclass attrs (no breakage)
    assert mr.status == "ok"
    assert mr.metrics["ticker"] == "SPY"

    # also works for failed case (as produced by _failed)
    failed = ModuleResult(status="failed", artifacts=[], metrics={"error": "boom"}, context_patch=None)
    assert failed.get("status") == "failed"
    assert failed.get("artifacts", []) == []
    assert failed.get("metrics", {}).get("error") == "boom"
