"""hedge_optimizer_tool.py

Wraps VaR_Tools_Simulations/var_engine/hedge_optimizer.py (min_var_hedge) as a
standalone Tool, pointed at any suite's suite_context.json.

hedge_optimizer.py's only prior entry point (main.py's run_hedge_optimizer())
is fully interactive -- it prompts for every position, covariance term, and
hedge instrument. This tool uses the non-interactive
_build_hedge_optimizer_from_context() builder added to
VaR_Tools_Simulations/main.py instead, which auto-derives its inputs from the
selected context: position notional from the run's options_result.json (or a
spot*100-share default), and hedge-instrument candidates from the context's
Vol_Suite basket peers, with per-peer vol/correlation estimated via the same
GARCH-vol methodology the new market-signals corr_sim uses.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
_VAR_SUITE_ROOT = _REPO_ROOT / "VaR_Tools_Simulations"

if str(_VAR_SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(_VAR_SUITE_ROOT))

from Tools.registry import ToolSpec  # noqa: E402


def _resolve_output_dir(context: Dict[str, Any]):
    override = context.get("_output_dir_override")
    raw = override or context.get("output_dir")
    return str(raw) if raw else None


def _import_var_main():
    """Load VaR_Tools_Simulations/main.py under a unique module name.

    Options_Suite, VaR_Tools_Simulations and sentiment-scanner each ship
    their own `main.py` -- a bare `import main` would silently return
    whichever one first landed in sys.modules under that generic name in
    this (possibly long-lived) dashboard process, instead of raising.
    """
    module_name = "var_tools_main"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(
        module_name, str(_VAR_SUITE_ROOT / "main.py"))
    var_main = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = var_main
    spec.loader.exec_module(var_main)
    return var_main


def run(context: Dict[str, Any]) -> Dict[str, Any]:
    """context: a validated suite_context.json dict (see
    context_loader.load_context), optionally carrying
    "_output_dir_override" the same way the options-strategy tool does.

    Returns a dict: ticker, position_value, hedge_names, optimal_weights,
    base_var, hedged_var, var_reduction_pct, base_port_vol, hedged_port_vol.
    """
    var_main = _import_var_main()

    focus = context.get("focus") or {}
    ticker = focus.get("ticker")
    output_dir = _resolve_output_dir(context)
    return var_main._build_hedge_optimizer_from_context(context, ticker, output_dir)


TOOL_SPEC = ToolSpec(
    name="Hedge Optimizer",
    slug="hedge-optimizer",
    description=(
        "Computes the minimum-variance hedge for a context's focus ticker "
        "against its Vol_Suite basket peers -- optimal hedge weights and the "
        "resulting VaR reduction."
    ),
    run=run,
)
