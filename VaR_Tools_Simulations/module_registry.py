"""module_registry.py (VaR_Tools_Simulations)

Phase 1 scaffolding only -- see shared/module_registry.py for the contract
and shared/module_registry.py::all_modules() for how this gets aggregated.
VaR_Tools_Simulations is not in scope for this overhaul's suite splits (per
the plan), so this stays empty/deferred -- registry plumbing only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shared.module_registry import ModuleSpec

MODULES: list[ModuleSpec] = []
