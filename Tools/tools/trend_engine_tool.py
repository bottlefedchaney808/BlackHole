# Tools/tools/trend_engine_tool.py
"""trend_engine_tool.py

Wraps Direction/trend_engine.py's analyze_trend() (live ThetaData
ADX + MA20/MA50 multi-timeframe trend engine) as a standalone Tool.
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
    Direction.trend_engine.analyze_trend()'s dict verbatim: daily, weekly,
    monthly (each {"adx", "ma"}), adx_ok, aligned, signal.
    """
    from Direction import trend_engine

    focus = context.get("focus") or {}
    ticker = context.get("ticker") or focus.get("ticker")
    if not ticker:
        raise ValueError("trend-engine tool requires a ticker (context['ticker'] or context.focus.ticker)")

    return trend_engine.analyze_trend(ticker)


TOOL_SPEC = ToolSpec(
    name="Trend Engine Tool",
    slug="trend-engine",
    description=(
        "Live multi-timeframe (daily/weekly/monthly) ADX + MA20/MA50 trend "
        "read for a suite context's focus ticker -- signal fires only when "
        "ADX confirms trend strength AND daily/weekly MA alignment agree."
    ),
    run=run,
)
