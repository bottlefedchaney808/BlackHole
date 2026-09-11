# Tools/tools/bollinger_tool.py
"""bollinger_tool.py

Wraps Direction/bollinger_analyzer.py's analyze() (live ThetaData squeeze +
band-thrust regime detector) as a standalone Tool.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from Tools.spec import ToolSpec  # noqa: E402


def run(context: Dict[str, Any]) -> Dict[str, Any]:
    """context: a validated suite_context.json dict. Requires
    context.focus.ticker (or context['ticker']). Returns
    Direction.bollinger_analyzer.analyze()'s dict verbatim: squeeze,
    regime, signal.
    """
    from Direction import bollinger_analyzer

    focus = context.get("focus") or {}
    ticker = context.get("ticker") or focus.get("ticker")
    if not ticker:
        raise ValueError("bollinger tool requires a ticker (context['ticker'] or context.focus.ticker)")

    return bollinger_analyzer.analyze(ticker)


TOOL_SPEC = ToolSpec(
    name="Bollinger Bands Tool",
    slug="bollinger",
    description=(
        "Live Bollinger Band squeeze/thrust regime for a suite context's "
        "focus ticker -- squeeze flags an imminent volatility expansion, "
        "upper/lower thrust flags momentum continuation."
    ),
    run=run,
)
