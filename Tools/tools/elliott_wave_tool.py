# Tools/tools/elliott_wave_tool.py
"""elliott_wave_tool.py

Wraps Direction/elliott_wave.py's analyze() (live ThetaData Elliott Wave
counter -- wave-3 momentum detection) as a standalone Tool.
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
    Direction.elliott_wave.analyze()'s dict verbatim: wave_count,
    wave_number, wave_type, signal.
    """
    from Direction import elliott_wave

    focus = context.get("focus") or {}
    ticker = context.get("ticker") or focus.get("ticker")
    if not ticker:
        raise ValueError("elliott-wave tool requires a ticker (context['ticker'] or context.focus.ticker)")

    return elliott_wave.analyze(ticker)


TOOL_SPEC = ToolSpec(
    name="Elliott Wave Tool",
    slug="elliott-wave",
    description=(
        "Live Elliott Wave count for a suite context's focus ticker -- "
        "flags Wave 3 (strongest momentum entry) vs Wave 1/5 (exhaustion "
        "warning) off 3 months of daily closes."
    ),
    run=run,
)
