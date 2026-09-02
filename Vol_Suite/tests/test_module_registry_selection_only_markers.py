"""Tests for Vol_Suite/module_registry.py's four Task 6 "selection-only
marker" ModuleSpec entries (group_screener, vol_surface_2d,
vrp_term_structure, sentiment_backtest -- see task-6-brief.md's judgment
call 3a).

These four slugs correspond one-to-one with four of _run_core_analysis's
five gated pipeline steps (chain_scanner, the fifth, already has a real,
independently-runnable ModuleSpec from Task 4 and is NOT duplicated here).
Unlike every other ModuleSpec in this file, these four exist purely for
--list-modules / dashboard checkbox discoverability -- their real execution
only happens inside volatility_suite.run_context_mode's
_run_core_analysis call, selected via context["modules"] membership
(see _resolve_core_analysis_flags in volatility_suite.py and
test_context_mode_module_selection.py). Calling .run() on any of them
directly must raise NotImplementedError, never silently no-op.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import Vol_Suite.module_registry as vs_registry
from shared.module_registry import all_modules

pytestmark = pytest.mark.unit

_MARKER_SLUGS = (
    "group_screener",
    "vol_surface_2d",
    "vrp_term_structure",
    "sentiment_backtest",
)


def _by_slug(slug: str):
    matches = [m for m in vs_registry.MODULES if m.slug == slug]
    assert len(matches) == 1, f"expected exactly one {slug!r} module, got {matches}"
    return matches[0]


class TestSelectionOnlyMarkerRegistration:
    def test_all_four_slugs_present_and_chain_scanner_not_duplicated(self):
        slugs = [m.slug for m in vs_registry.MODULES]
        for slug in _MARKER_SLUGS:
            assert slugs.count(slug) == 1, f"{slug} should appear exactly once"
        # chain_scanner is the fifth gated step, already a real Task 4
        # module -- must not be re-added as a marker here.
        assert slugs.count("chain_scanner") == 1

    @pytest.mark.parametrize("slug", _MARKER_SLUGS)
    def test_marker_spec_fields(self, slug):
        m = _by_slug(slug)
        assert m.suite == "vol_suite"
        assert m.category == "pipeline_step"
        assert m.requires == []
        assert m.default_selected is False
        assert m.cli_entry is None

    @pytest.mark.parametrize("slug", _MARKER_SLUGS)
    def test_run_raises_not_implemented_rather_than_no_op(self, slug):
        m = _by_slug(slug)
        with pytest.raises(NotImplementedError) as excinfo:
            m.run({})
        message = str(excinfo.value)
        assert slug in message
        assert "context['modules']" in message or "context[" in message


class TestAllModulesAggregation:
    def test_list_modules_includes_all_four_markers(self):
        slugs = {m.slug for m in all_modules()}
        assert set(_MARKER_SLUGS) <= slugs

    def test_list_modules_still_includes_chain_scanner_unduplicated(self):
        matches = [m for m in all_modules() if m.slug == "chain_scanner"]
        assert len(matches) == 1
