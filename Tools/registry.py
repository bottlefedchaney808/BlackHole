"""registry.py

Plugin registry for the Tools/ framework.

--------------------------------------------------------------------------
HOW TO ADD TOOL #3 (or #4, #5, ...)
--------------------------------------------------------------------------
1. Create a new module under Tools/tools/, e.g. Tools/tools/var_stress_tool.py.
2. In that module, implement a `run(context: dict) -> dict` function. `context`
   is a validated suite_context.json dict (see Vol_Suite/suite_context.py),
   optionally carrying extra top-level keys your tool needs (e.g. "mode",
   parameter overrides) -- validate_suite_context only checks that the
   REQUIRED fields are present and well-typed, so extra keys are always
   safe to add without touching the schema.
3. At module scope, build a ToolSpec describing it:

       from Tools.registry import ToolSpec
       TOOL_SPEC = ToolSpec(
           name="VaR Stress Tool",
           slug="var-stress",
           description="Runs a stressed-scenario VaR sweep off a suite context.",
           run=run,
           suite="var_tools",
           category="tool",
       )

4. Back in this file: import the new module and append its TOOL_SPEC to
   TOOLS below. That's the entire integration surface -- nothing else in
   Tools/ needs to change, and nothing in the four suites needs to change
   either, since every tool only ever depends on the shared context schema.

Phase 2 of the Widget-Native Quant Console adds ``suite`` and ``category``
metadata to every ToolSpec so the unified registry can place tools in the
same discovery/selection space as native suite modules.
--------------------------------------------------------------------------
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolSpec:
    """Describes one installed tool.

    name:        human-readable display name, e.g. "Options Strategy Tool"
    slug:        short, stable, URL/CLI-safe identifier, e.g. "options-strategy"
    description: one or two sentences, shown in listings/help text
    run:         run(context: dict) -> dict -- context is a validated
                 suite_context.json dict (see context_loader.load_context),
                 optionally with extra tool-specific keys mixed in by the
                 caller; the returned dict is the tool's result artifact,
                 always JSON-serializable.
    suite:       "tools" or the logical suite that owns this tool (used by
                 the unified module registry for grouping/filtering)
    category:    "tool" or a more specific category for the dashboard UI
    """

    name: str
    slug: str
    description: str
    run: Callable[[dict[str, Any]], dict[str, Any]]
    suite: str = "tools"
    category: str = "tool"
    # Tool-specific knobs, rendered as controls wherever this tool appears.
    #
    # ModuleSpec has had `params` since the ParamSpec system landed; ToolSpec
    # did not, so an adapted Tools/ entry reached the UI with no controls at
    # all no matter how many options its run() accepted. The backtesting tool
    # is the clearest case: it dispatches five genuinely different backtests
    # on `context['mode']` and has no default, so from a card it could only
    # ever raise "unknown mode". Typed as Any to keep this module free of a
    # shared.module_registry import -- that module imports ToolSpec (under
    # TYPE_CHECKING) and a real import here would close the cycle.
    params: tuple[Any, ...] = ()


TOOL_SPEC_FIELDS = {
    "name", "slug", "description", "run", "suite", "category", "params"
}


def _load_tools() -> list[ToolSpec]:
    # Imported lazily inside a function (rather than at module import time)
    # so that a broken/incomplete tool module raises at TOOLS-construction
    # time with a clear traceback, rather than silently failing to import
    # and shrinking the registry with no error at all.
    from Tools.tools import (
        backtesting_tool,
        direction_signal_tool,
        hedge_optimizer_tool,
        options_strategy_tool,
        price_dist_tool,
        surface_explorer_tool,
        vrp_term_structure_tool,
    )

    return [
        options_strategy_tool.TOOL_SPEC,
        backtesting_tool.TOOL_SPEC,
        direction_signal_tool.TOOL_SPEC,
        hedge_optimizer_tool.TOOL_SPEC,
        vrp_term_structure_tool.TOOL_SPEC,
        price_dist_tool.TOOL_SPEC,
        surface_explorer_tool.TOOL_SPEC,
        # Add new tools' TOOL_SPEC here -- see module docstring above.
    ]


TOOLS: list[ToolSpec] = _load_tools()


def get_tool(slug: str) -> ToolSpec:
    """Look up an installed tool by slug. Raises KeyError with the full
    list of valid slugs if not found, rather than returning None -- a
    typo'd slug should fail immediately and legibly, not surface as a
    confusing AttributeError three lines later.
    """
    for tool in TOOLS:
        if tool.slug == slug:
            return tool
    valid = ", ".join(t.slug for t in TOOLS)
    raise KeyError(f"No tool registered with slug {slug!r}. Available: {valid}")
