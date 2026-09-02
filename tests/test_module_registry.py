"""Tests for shared/module_registry.py -- Phase 1 registry scaffolding.

Phase 1 is registration-only: no suite MODULES lists are populated yet (they
stay empty until a later phase), so all_modules() should currently return
exactly the Tools/registry.py entries, adapted via from_tool_spec. Real
cycle-detection over the `requires` graph is deferred to a later phase (see
test_requires_cycle_not_yet_detected below) since this phase never executes
the requires graph -- it only registers modules.
"""

from __future__ import annotations

import pytest

from shared.module_registry import (
    ArchiveHint,
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


def test_all_modules_has_nothing_yet_from_the_four_suite_registries():
    # Phase 1: every suite's module_registry.py::MODULES is still [], so the
    # only contributions to all_modules() come from Tools/registry.py.
    modules = all_modules()
    tool_slugs = {t.slug for t in TOOLS}
    assert {m.slug for m in modules} == tool_slugs
    assert len(modules) == len(TOOLS)


def test_all_modules_has_no_duplicate_slugs():
    modules = all_modules()
    slugs = [m.slug for m in modules]
    assert len(slugs) == len(set(slugs))


def test_resolve_modules_unknown_slug_raises_value_error_naming_it():
    with pytest.raises(ValueError, match="not_a_real_slug"):
        resolve_modules(["not_a_real_slug"])


def test_resolve_modules_empty_list_returns_empty_list():
    assert resolve_modules([]) == []


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
