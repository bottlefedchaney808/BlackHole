"""dashboard/quant_modules.py -- static module registry for the Quant Console.

Quant Console plan, Phase 1 / Task 5
(docs/superpowers/plans/2026-08-01-quant-console.md); design spec at
docs/superpowers/specs/2026-08-01-quant-console-design.md.

Lists the modules `GET /quant` (Task 6) renders as cards: one entry per
suite this repo already knows how to run via `POST /run/{suite_or_unified}`
(`dashboard/app.py::RUN_KINDS` -- `vol`, `options`, `var`, `sentiment`), plus
the orchestrator's `unified` chain. Mirrors the shape of the sibling
project's own `MODULE_REGISTRY` (see the design spec's Context section),
adapted to this repo's actual suite names and current known-good/known-bad
state rather than copied verbatim.

`shared/summary.py::build_run_summary()` (Task 3) already accepts this
module's `MODULE_REGISTRY` as an optional `module_registry` argument -- see
that module's docstring -- to implement the `runnable=False` -> `status:
"unsupported"` short-circuit described in the design spec's Phase 1 table
and Resolved Design Decision #2. Task 6's `GET /quant` template is the
other consumer, listing one card per entry here.

`runnable=False` for `options` is sourced from this repo's own
`.claude/skills/tool-launcher/SKILL.md`, which documents (verified against
`shared/schemas.py::validate_options_result`) that Options_Suite's current
context-mode stub (`Options_Suite/main.py::_build_options_result`) writes
only `status`/`ticker`/`expiry`/`pricing_models: []` -- no `method`, no
`sigma`, no `price`, no `greeks` -- which fails validation immediately with
`"method must be a non-empty string"`. `--suite options` (and the options
leg of `--unified`) therefore reports FAIL, not PASS, today. Do NOT flip
this to `True` as part of routine dashboard work -- it changes only when
Options_Suite's context-mode stub itself is fixed (out of scope for this
plan; see the plan's "Deferred / Not In This Plan" section), independently
of anything in the Quant Console.
"""

from __future__ import annotations

from typing import Any

__all__ = ["MODULE_REGISTRY", "get_module"]


MODULE_REGISTRY: tuple[dict[str, Any], ...] = (
    {
        "id": "vol",
        "name": "Volatility",
        "suite": "vol",
        "focus": (
            "Dealer positioning (gamma/vanna), correlation/basket "
            "construction, variance-swap replication and VRP term "
            "structure, GARCH(1,1)."
        ),
        "runnable": True,
    },
    {
        "id": "options",
        "name": "Options",
        "suite": "options",
        "focus": (
            "American option pricing/Greeks/IV across CRR, "
            "Leisen-Reimer, SABR, Vanna-Volga, Monte Carlo, Heston, "
            "and Barone-Adesi-Whaley."
        ),
        # Known FAIL in context mode today -- see module docstring. Do not
        # silently flip to True.
        "runnable": False,
    },
    {
        "id": "var",
        "name": "VaR",
        "suite": "var",
        "focus": (
            "Correlated Monte Carlo / historical / copula-based Value-at-"
            "Risk simulation (Python port of the legacy Excel VaR toolkit)."
        ),
        "runnable": True,
    },
    {
        "id": "sentiment",
        "name": "Sentiment",
        "suite": "sentiment",
        "focus": (
            "Contested-narrative sentiment (StockTwits/Reddit/YouTube) "
            "cross-referenced with options flow and CME swap activity."
        ),
        "runnable": True,
    },
    {
        "id": "unified",
        "name": "Unified",
        "suite": "unified",
        "focus": (
            "Full cross-suite orchestrated run: sentiment -> vol -> "
            "{options, var}, sharing one suite_context.json handoff."
        ),
        "runnable": True,
    },
)


def get_module(module_id: str) -> dict[str, Any]:
    """Look up a module registry entry by id.

    Raises `KeyError` with the full list of valid ids if not found, rather
    than returning `None` -- same pattern as `Tools/registry.py::get_tool`,
    so a typo'd module id fails immediately and legibly.
    """
    for entry in MODULE_REGISTRY:
        if entry["id"] == module_id:
            return entry
    valid = ", ".join(entry["id"] for entry in MODULE_REGISTRY)
    raise KeyError(
        f"No module registered with id {module_id!r}. Available: {valid}"
    )
