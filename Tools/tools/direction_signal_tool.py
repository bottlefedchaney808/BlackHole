# Tools/tools/direction_signal_tool.py
"""direction_signal_tool.py -> Directional Engine.

Container for the five Direction sub-signals plus a unified run.
mode='unified' (default): signal_generator.generate(ticker) -- runs all five
  (whale, elliott, bollinger, trend, liquidity) and returns one conviction.
mode in {'whale','elliott','bollinger','trend','liquidity'}: the single
  module's output dict for that sub-signal.
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

_MODULES = {
    'whale': ('whale_scanner', 'scan'),
    'elliott': ('elliott_wave', 'analyze'),
    'bollinger': ('bollinger_analyzer', 'analyze'),
    'trend': ('trend_engine', 'analyze_trend'),
    'liquidity': ('liquidity_map', 'get_liquidity'),
}


def _signal_generator():
    from Direction import signal_generator
    return signal_generator


def run(context: Dict[str, Any]) -> Dict[str, Any]:
    """context: a validated suite_context.json dict. Requires
    context.focus.ticker (or context['ticker']). mode:
      - 'unified' (default) -> signal_generator.generate(ticker) verbatim
        (all five Direction modules incl. trend, combined into one conviction)
      - 'whale'|'elliott'|'bollinger'|'trend'|'liquidity' -> that single
        module's output dict.
    """
    focus = context.get('focus') or {}
    ticker = context.get('ticker') or focus.get('ticker')
    if not ticker:
        raise ValueError('Directional Engine requires a ticker '
                         '(context.ticker or context.focus.ticker)')
    mode = str(context.get('mode') or 'unified').strip().lower()
    if mode == 'unified':
        return _signal_generator().generate(ticker)
    if mode not in _MODULES:
        raise ValueError(
            f"mode must be one of {{'unified', *{sorted(_MODULES)}}}; got {mode!r}")
    mod_name, fn = _MODULES[mode]
    module = __import__(f'Direction.{mod_name}', fromlist=[fn])
    return getattr(module, fn)(ticker)


TOOL_SPEC = ToolSpec(
    name="Directional Engine",
    slug="directional-engine",
    description=(
        "Five Direction signals -- whale flow, Elliott Wave, Bollinger, "
        "multi-timeframe trend, liquidity -- run individually or as one "
        "unified conviction call for a context's focus ticker. Select via mode."
    ),
    run=run,
)
