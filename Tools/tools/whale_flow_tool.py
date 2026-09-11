# Tools/tools/whale_flow_tool.py
"""whale_flow_tool.py

Wraps Direction/whale_scanner.py's scan() (live ThetaData whale-flow
premium classifier) as a standalone Tool.
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
    context.focus.ticker (or context['ticker']). Optional overrides:
    'min_premium' (float, default $25K), 'threshold_bps' (float, switches
    to a premium-relative bar -- see Vol_Suite.whale_scanner's threshold
    docstring). Returns Direction.whale_scanner.scan()'s dict verbatim.
    """
    from Direction import whale_scanner

    focus = context.get("focus") or {}
    ticker = context.get("ticker") or focus.get("ticker")
    if not ticker:
        raise ValueError("whale-flow tool requires a ticker (context['ticker'] or context.focus.ticker)")

    kwargs: Dict[str, Any] = {}
    if context.get("min_premium") is not None:
        kwargs["min_premium"] = float(context["min_premium"])
    if context.get("threshold_bps") is not None:
        kwargs["threshold_bps"] = float(context["threshold_bps"])

    return whale_scanner.scan(ticker, **kwargs)


TOOL_SPEC = ToolSpec(
    name="Whale Flow Tool",
    slug="whale-flow",
    description=(
        "Live large-premium option-flow bias (bullish/bearish/neutral) for "
        "a suite context's focus ticker, via Direction.whale_scanner -- the "
        "same classifier backtest_stage3.py's whale-flow backtest column "
        "uses, run against the nearest live expiry instead of historical "
        "rows."
    ),
    run=run,
)
