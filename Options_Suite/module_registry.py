"""module_registry.py (Options_Suite)

Phase 1 scaffolding only -- see shared/module_registry.py for the contract
and shared/module_registry.py::all_modules() for how this gets aggregated.
Stays empty until a later phase splits Options_Suite's launchable units
(per-model pricing: CRR, Leisen-Reimer, SABR, Vanna-Volga, Heston MC,
BAW, ...) into real ModuleSpec entries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shared.module_registry import ModuleSpec

MODULES: list[ModuleSpec] = []
