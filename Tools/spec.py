"""spec.py -- the ToolSpec dataclass, split out of registry.py.

This exists to break an import cycle, not for tidiness. ToolSpec used to
live in Tools/registry.py, so every tool module did
`from Tools.registry import ToolSpec` at module scope -- while
registry._load_tools() imports those same tool modules and reads their
TOOL_SPEC. Importing any tool module DIRECTLY therefore deadlocked:

    import Tools.tools.backtesting_tool
      -> from Tools.registry import ToolSpec
        -> registry runs _load_tools()
          -> imports backtesting_tool, already partially initialized
            -> AttributeError: ... has no attribute 'TOOL_SPEC'

The dashboard never hit it because it always imports Tools.registry first,
which completes ToolSpec's definition before _load_tools() runs. Anything
scripting a tool directly hit it every time -- all 12 tool modules shared the
latent bug, not just the one that happened to get imported directly.

This module imports nothing from Tools, so it can never participate in that
cycle. Tools/registry.py re-exports ToolSpec for backwards compatibility.
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
