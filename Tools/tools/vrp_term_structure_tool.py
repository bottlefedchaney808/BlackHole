"""vrp_term_structure_tool.py

Wraps Vol_Suite/vrp_term_structure.py::compute_vrp_term_structure as a
standalone Tool, so the VRP term structure can be pulled for any ticker
without paying for a full Vol_Suite run (replication legs, correlation
engine, GARCH, dealer positioning). It needs nothing from the context but a
ticker.

Vol_Suite lands on sys.path at module scope -- the same thing
backtesting_tool.py and options_strategy_tool.py do -- but the Vol_Suite
module itself is imported lazily inside run(): importing vrp_term_structure
drags in matplotlib, thetadata_client and variance_swap_live, and
Tools/registry.py imports every tool module eagerly at TOOLS-construction
time, i.e. on dashboard start-up. Both seams (_import_vrp_module and
_theta_client) are module-level functions rather than inline imports so a
caller/test can substitute them.

The returned dict deliberately mirrors Vol_Suite's own
artifacts["vrp_term_structure"] block -- {"available", "shape", "points",
"chart_path"} -- so a consumer can read a tool result and a vol_result.json
block with the same code.
"""
from __future__ import annotations

import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
_VOL_SUITE_ROOT = _REPO_ROOT / "Vol_Suite"

if str(_VOL_SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOL_SUITE_ROOT))

from Tools.registry import ToolSpec  # noqa: E402


def _import_vrp_module():
    """Import Vol_Suite/vrp_term_structure.py lazily. See module docstring."""
    import vrp_term_structure as vts
    return vts


def _theta_client():
    from thetadata_client import ThetaDataController
    return ThetaDataController()


def _json_safe(value: Any) -> Any:
    """NaN/Inf are not JSON. Every tenor compute_vrp_term_structure could
    not resolve comes back as an all-NaN VrpTermPoint (that is its
    documented gap marker), and json.dumps would emit bare `NaN` tokens no
    strict parser can read -- the same guard Vol_Suite applies to
    vol_result.json."""
    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else value
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _resolve_output_dir(context: Dict[str, Any]) -> Optional[str]:
    override = context.get("_output_dir_override")
    raw = override or context.get("output_dir")
    return str(raw) if raw else None


def run(context: Dict[str, Any]) -> Dict[str, Any]:
    """context: a validated suite_context.json dict (see
    context_loader.load_context). Only the focus ticker is required
    (context['ticker'] or context['focus']['ticker']); an output_dir --
    or the "_output_dir_override" key the other tools accept -- additionally
    gets a chart written.

    Returns {"available": bool, "shape": str, "points": [...],
    "chart_path": str|None} on success, or {"available": False,
    "error": str} on failure. A failure is reported, never raised, so a
    dead ThetaData session does not take the caller down with it.
    """
    focus = context.get("focus") or {}
    ticker = context.get("ticker") or focus.get("ticker")
    if not ticker:
        return {"available": False,
                "error": "a ticker is required (context['ticker'] or "
                         "context['focus']['ticker'])"}
    ticker = str(ticker).upper()

    # The import and the client construction are inside the try for the same
    # reason the computation is: a missing Vol_Suite dependency or dead
    # ThetaData session must come back as available=False, not as an
    # exception out of a registry-dispatched tool.
    td = None
    try:
        vts = _import_vrp_module()
        td = _theta_client()
        spot = td.fetch_spot_price(ticker)
        q = td.fetch_dividend_yield(ticker)
        r = td.fetch_risk_free_rate(0.25) or 0.05
        result = vts.compute_vrp_term_structure(ticker, td, spot, r, q)
    except Exception as exc:
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if td is not None:
            try:
                td.close()
            except Exception:
                pass

    # The chart is a nice-to-have on top of an already-computed term
    # structure; a matplotlib/filesystem failure must not discard it.
    chart_path = None
    out_dir = _resolve_output_dir(context)
    if out_dir:
        try:
            ts_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
            candidate = str(Path(out_dir) / f"{ticker}_vrp_term_structure_{ts_tag}.png")
            chart_path = vts.plot_vrp_term_structure(result, candidate) or candidate
        except Exception:
            chart_path = None

    return {
        "available": True,
        "ticker": result.ticker,
        "timestamp": result.timestamp,
        "shape": result.shape,
        "points": [_json_safe(vars(p)) for p in result.points],
        "chart_path": chart_path,
    }


TOOL_SPEC = ToolSpec(
    name="VRP Term Structure",
    slug="vrp-term-structure",
    description=(
        "Computes the variance-risk-premium term structure (1-12mo) for a "
        "context's focus ticker: fair vol, ATM IV, VRP and trailing realized "
        "vol at each tenor, plus the term-structure shape."
    ),
    run=run,
)
