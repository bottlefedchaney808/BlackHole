"""price_dist_tool.py -> the "Simulations" tool.

Multi-mode wrapper around three VaR context builders:
  mode='price_dist' -> _build_price_dist_from_context
  mode='mc_sim'     -> _build_mc_sim_from_context
  mode='corr_sim'   -> _build_corr_sim_peer_from_context

Ticker comes from context.focus.ticker; horizon_days/n_sims/seed/confidence
are read off context when present and passed through as override kwargs to the
VaR builders (which default to their own 1y/10k/0.99 when absent).
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

_MODES = {
    'price_dist': '_build_price_dist_from_context',
    'mc_sim': '_build_mc_sim_from_context',
    'corr_sim': '_build_corr_sim_peer_from_context',
}
_OVERRIDES = ('horizon_days', 'n_sims', 'seed', 'confidence')


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
    context_loader.load_context), optionally carrying:
      - "mode": "price_dist" (default) | "mc_sim" | "corr_sim"
      - override keys: horizon_days, n_sims, seed, confidence

    Returns the selected VaR builder's dict verbatim.
    """
    focus = context.get("focus") or {}
    ticker = context.get("ticker") or focus.get("ticker")
    if not ticker:
        raise ValueError(
            "Simulations tool requires a ticker "
            "(context.ticker or context.focus.ticker)")
    mode = str(context.get("mode") or "price_dist").strip().lower()
    if mode not in _MODES:
        raise ValueError(
            f"mode must be one of {sorted(_MODES)}; got {mode!r}")

    var_main = _import_var_main()
    builder = getattr(var_main, _MODES[mode])
    kwargs = {k: context[k] for k in _OVERRIDES if k in context}
    return builder(context, ticker, **kwargs)


TOOL_SPEC = ToolSpec(
    name="Simulations",
    slug="simulations",
    description=(
        "Three 1-year-out Monte Carlo simulations for a context's focus "
        "ticker -- price-distribution table, MC terminal price, or "
        "correlation sim vs basket peers -- seeded from live spot + GARCH "
        "vol + drift. Select via mode."
    ),
    run=run,
)
