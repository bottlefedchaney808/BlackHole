"""module_registry.py (Options_Suite)

Phase 5 of Modularization Overhaul: per-model ModuleSpec for pricing models.
Default only leisen_reimer.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_OPTS_DIR = Path(__file__).resolve().parent
if str(_OPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_OPTS_DIR))

from shared.module_registry import ArchiveHint, ModuleResult, ModuleSpec

def _failed(exc: Exception) -> ModuleResult:
    return ModuleResult(status="failed", artifacts=[], metrics={"error": str(exc)}, context_patch=None)

def _run_leisen_reimer(context: dict[str, Any]) -> ModuleResult:
    try:
        # placeholder; real would call into chain or main pricing
        ticker = context.get("ticker", "SPY")
        return ModuleResult(status="ok", artifacts=[], metrics={"model": "leisen_reimer", "ticker": ticker}, context_patch={"pricing_result": {"model": "leisen"}})
    except Exception as exc:
        return _failed(exc)

# similar for others (stubs for now; full would wire the 8 models)
def _run_crr(context): return _run_leisen_reimer(context)  # stub
def _run_newton_raphson_iv(context): return _run_leisen_reimer(context)
def _run_sabr(context): return _run_leisen_reimer(context)
def _run_vanna_volga(context): return _run_leisen_reimer(context)
def _run_mc(context): return _run_leisen_reimer(context)
def _run_mc_heston_lsm(context): return _run_leisen_reimer(context)
def _run_baw(context): return _run_leisen_reimer(context)

MODULES: list[ModuleSpec] = [
    ModuleSpec(name="CRR", slug="crr", suite="options_suite", category="pricing", run=_run_crr, cli_entry=None, default_selected=False, requires=[], archive=ArchiveHint(key_shape="ticker_expiry")),
    ModuleSpec(name="Leisen-Reimer", slug="leisen_reimer", suite="options_suite", category="pricing", run=_run_leisen_reimer, cli_entry=None, default_selected=True, requires=[], archive=ArchiveHint(key_shape="ticker_expiry")),
    ModuleSpec(name="Newton-Raphson IV", slug="newton_raphson_iv", suite="options_suite", category="pricing", run=_run_newton_raphson_iv, cli_entry=None, default_selected=False, requires=[], archive=ArchiveHint(key_shape="ticker_expiry")),
    ModuleSpec(name="SABR", slug="sabr", suite="options_suite", category="pricing", run=_run_sabr, cli_entry=None, default_selected=False, requires=[], archive=ArchiveHint(key_shape="ticker_expiry")),
    ModuleSpec(name="Vanna-Volga", slug="vanna_volga", suite="options_suite", category="pricing", run=_run_vanna_volga, cli_entry=None, default_selected=False, requires=[], archive=ArchiveHint(key_shape="ticker_expiry")),
    ModuleSpec(name="MC", slug="mc", suite="options_suite", category="pricing", run=_run_mc, cli_entry=None, default_selected=False, requires=[], archive=ArchiveHint(key_shape="ticker_expiry")),
    ModuleSpec(name="MC Heston LSM", slug="mc_heston_lsm", suite="options_suite", category="pricing", run=_run_mc_heston_lsm, cli_entry=None, default_selected=False, requires=[], archive=ArchiveHint(key_shape="ticker_expiry")),
    ModuleSpec(name="BAW", slug="baw", suite="options_suite", category="pricing", run=_run_baw, cli_entry=None, default_selected=False, requires=[], archive=ArchiveHint(key_shape="ticker_expiry")),
    ModuleSpec(name="Model Comparison", slug="model_comparison", suite="options_suite", category="pricing", run=_run_leisen_reimer, cli_entry=None, default_selected=False, requires=[], archive=ArchiveHint(key_shape="ticker_expiry")),
]
