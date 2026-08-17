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
       )

4. Back in this file: import the new module and append its TOOL_SPEC to
   TOOLS below. That's the entire integration surface -- nothing else in
   Tools/ needs to change, and nothing in the four suites needs to change
   either, since every tool only ever depends on the shared context schema.
--------------------------------------------------------------------------
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List


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
    """
    name: str
    slug: str
    description: str
    run: Callable[[Dict[str, Any]], Dict[str, Any]]


def _load_tools() -> List[ToolSpec]:
    # Imported lazily inside a function (rather than at module import time)
    # so that a broken/incomplete tool module raises at TOOLS-construction
    # time with a clear traceback, rather than silently failing to import
    # and shrinking the registry with no error at all.
    from Tools.tools import options_strategy_tool
    from Tools.tools import backtesting_tool
    from Tools.tools import direction_signal_tool
    from Tools.tools import hedge_optimizer_tool
    from Tools.tools import vrp_term_structure_tool
    from Tools.tools import price_dist_tool

    return [
        options_strategy_tool.TOOL_SPEC,
        backtesting_tool.TOOL_SPEC,
        direction_signal_tool.TOOL_SPEC,
        hedge_optimizer_tool.TOOL_SPEC,
        vrp_term_structure_tool.TOOL_SPEC,
        price_dist_tool.TOOL_SPEC,
        # Add new tools' TOOL_SPEC here -- see module docstring above.
    ]


TOOLS: List[ToolSpec] = _load_tools()


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
