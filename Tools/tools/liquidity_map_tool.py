# Tools/tools/liquidity_map_tool.py
"""liquidity_map_tool.py

Wraps Direction/liquidity_map.py's get_liquidity() (live ThetaData max
pain / OI strike walls / PCR / GEX proximity map) as a standalone Tool.
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
    Direction.liquidity_map.get_liquidity()'s dict verbatim: price, expiry,
    max_pain, call_wall, put_wall, pcr, signal, dealer.
    """
    from Direction import liquidity_map

    focus = context.get("focus") or {}
    ticker = context.get("ticker") or focus.get("ticker")
    if not ticker:
        raise ValueError("liquidity-map tool requires a ticker (context['ticker'] or context.focus.ticker)")

    return liquidity_map.get_liquidity(ticker)


TOOL_SPEC = ToolSpec(
    name="Liquidity Map Tool",
    slug="liquidity-map",
    description=(
        "Live max pain (payout-minimization, not a spot-proximity "
        "shortcut), OI call/put strike walls, put/call ratio, and GEX "
        "proximity for a suite context's focus ticker's nearest expiry."
    ),
    run=run,
)
