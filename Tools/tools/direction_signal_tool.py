# Tools/tools/direction_signal_tool.py
"""direction_signal_tool.py

Wraps Direction/signal_generator.py's generate() -- the GROUP tool. Runs
all five Direction modules (whale flow, Elliott Wave, Bollinger, trend,
liquidity) against a suite context's focus ticker in one call and returns
the combined conviction call, using one shared 300s Direction.data cache
across all five fetches instead of five separate tool invocations.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from Tools.registry import ToolSpec  # noqa: E402


def run(context: Dict[str, Any]) -> Dict[str, Any]:
    """context: a validated suite_context.json dict. Requires
    context.focus.ticker (or context['ticker']). Returns
    Direction.signal_generator.generate()'s dict verbatim: ticker, price,
    signals (per-module booleans), score (0-5), conviction
    (HIGH|MEDIUM|NONE), details (each module's full output dict).
    """
    from Direction import signal_generator

    focus = context.get("focus") or {}
    ticker = context.get("ticker") or focus.get("ticker")
    if not ticker:
        raise ValueError("direction-signal tool requires a ticker (context['ticker'] or context.focus.ticker)")

    return signal_generator.generate(ticker)


TOOL_SPEC = ToolSpec(
    name="Direction Signal Tool",
    slug="direction-signal",
    description=(
        "Runs all five Direction signals (whale flow, Elliott Wave, "
        "Bollinger, multi-timeframe trend, liquidity zones) against a "
        "suite context's focus ticker and combines them into one "
        "conviction call (HIGH/MEDIUM/NONE) -- the group entry point "
        "alongside the five individual Direction tools."
    ),
    run=run,
)
