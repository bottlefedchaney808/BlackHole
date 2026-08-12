"""price_dist_tool.py

Wraps VaR_Tools_Simulations/main.py's _build_price_dist_from_context as a
standalone Tool -- a 1-year-out analytic lognormal price-distribution table
plus an MC terminal-price histogram and touch/expiry probabilities for a
context's focus ticker.

var_engine/price_dist.py's only prior entry point (main.py's run_price_dist())
is fully interactive -- it prompts for spot, targets, vol and horizon. This
tool uses the non-interactive _build_price_dist_from_context() builder instead,
which derives its inputs from the selected context: live spot, the context's
Vol_Suite GARCH vol (falling back to VaR's own fit), and VaR's historical
geometric drift, with the resolution branch reported in `data_quality`.
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
    context_loader.load_context).

    Returns a dict: ticker, spot, vol, expected_return, distribution_table,
    terminal_price_histogram, avg_end_price, target probabilities and
    data_quality.
    """
    var_main = _import_var_main()

    focus = context.get("focus") or {}
    ticker = focus.get("ticker")
    return var_main._build_price_dist_from_context(context, ticker)


TOOL_SPEC = ToolSpec(
    name="Price Distribution",
    slug="price-distribution",
    description=(
        "1-year-out lognormal price-distribution table and MC terminal-price "
        "histogram for a context's focus ticker, with probabilities of "
        "reaching +/-50% targets at expiry and at any time."
    ),
    run=run,
)
