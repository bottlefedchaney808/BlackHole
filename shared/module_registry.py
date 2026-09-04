"""module_registry.py

Umbrella module-registry contract for the modularization overhaul, parallel to
(not a replacement for) `Tools/registry.py`'s `ToolSpec`. `Tools/registry.py`
stays scoped to tools with a dedicated dashboard page; every launchable unit
in the repo -- including existing `Tools/` entries -- gets adapted into a
`ModuleSpec` here, so there is one selection/execution/archival mechanism
repo-wide.

Phase 1 scope (this file, this task): the contract dataclasses plus
aggregation/lookup helpers only. No suite logic moves here yet -- each of the
four suites' `module_registry.py::MODULES` stays an empty list until a later
phase populates it. `all_modules()` already adapts every `Tools/registry.py`
entry via `from_tool_spec`, since those are real, already-registered units.

--------------------------------------------------------------------------
HOW A LATER PHASE ADDS A REAL MODULE
--------------------------------------------------------------------------
1. Implement `run(context: dict) -> ModuleResult` for the unit being split out
   (e.g. dealer exposure, a single pricing model, one sentiment scanner).
2. Build a `ModuleSpec` describing it and append it to the owning suite's
   `module_registry.py::MODULES` list (e.g. `Vol_Suite/module_registry.py`).
3. Nothing else needs to change -- `all_modules()` picks it up automatically.
--------------------------------------------------------------------------
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from Tools.registry import ToolSpec


@dataclass(frozen=True)
class ArtifactRef:
    """One artifact a module's run() already wrote to disk.

    path: filesystem path to the artifact (chart, json, csv, ...)
    kind: "json" | "csv" | "png" | "pdf"
    """

    path: str
    kind: str


@dataclass(frozen=True)
class ArchiveHint:
    """Tells the (future) archiver what kind of artifacts/keys to expect.

    key_shape: "ticker_expiry" | "ticker_only" | "global" -- how this
               module's output should be keyed when archived.
    """

    key_shape: str


@dataclass(frozen=True)
class InputSpec:
    """Declares which scope fields a module consumes.

    Each field is one of "required", "optional", or "none".
    """

    ticker: str = "none"
    expiry: str = "none"
    basket: str = "none"


@dataclass(frozen=True)
class ModuleResult:
    """What a module's run() returns.

    status:        "ok" | "skipped" | "failed"
    artifacts:     paths already written by the module (charts, json, csv)
    metrics:       small scalar/summary payload for archive index + dashboard
                   tiles
    context_patch: optional fields to thread into downstream context
                   (generalized successor of
                   orchestrator.py::_thread_vol_stats_into_context)

    Also supports legacy dict-style .get(key, default) for callers that
    pre-date the dataclass (e.g. dashboard dealer-book tab).
    """

    status: str
    artifacts: list[ArtifactRef]
    metrics: dict[str, Any]
    context_patch: dict[str, Any] | None

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-like .get(key, default) for legacy callers (e.g. dashboard
        /dealer-book/load) that treated run results as plain dicts before
        ModuleResult existed. Matches the four fields; other keys -> default.
        """
        if key == "status":
            return self.status
        if key == "artifacts":
            return self.artifacts
        if key == "metrics":
            return self.metrics
        if key == "context_patch":
            return self.context_patch
        return default


@dataclass(frozen=True)
class ModuleSpec:
    """Describes one launchable, selectable, archivable unit.

    name:             human display, e.g. "Dealer Exposure (SPX)"
    slug:             stable id, e.g. "dealer_exposure" -- used in CLI
                      --modules, DB, URLs
    suite:            "vol_suite" | "options_suite" | "var_tools" |
                      "sentiment_scanner" | "tools" (adapted Tools/ entries)
    category:         "exposure" | "flow" | "scanner" | "pricing_model" |
                      "smile" | "surface" | "tool" (adapted Tools/ entries)
    run:              run(context) -> ModuleResult -- context is a
                      suite_context dict plus module-specific overrides
    cli_entry:        module path for standalone `python -m ...`
                      invocation, or None if there isn't one
    default_selected: whether a unified run includes it when no --modules
                      is given
    requires:         slugs this module's run() depends on (e.g. dealer_flow
                      requires dealer_exposure's fetch)
    archive:          tells the archiver what kind of artifacts/keys to
                      expect for this module
    description:      one or two sentence UI description
    inputs:           InputSpec declaring required/optional context fields
    output_kind:      "metrics" | "chart" | "table" -- how the dashboard
                      should render this module's metrics
    sample:           example context dict for testing/documentation
    """

    name: str
    slug: str
    suite: str
    category: str
    run: Callable[[dict[str, Any]], ModuleResult]
    cli_entry: str | None
    default_selected: bool
    requires: list[str] = field(default_factory=list)
    archive: ArchiveHint = field(
        default_factory=lambda: ArchiveHint(key_shape="global")
    )
    description: str = ""
    inputs: InputSpec = field(default_factory=InputSpec)
    output_kind: str = "metrics"
    sample: dict[str, Any] = field(default_factory=dict)


# Suites whose module_registry.py::MODULES lists get aggregated by
# all_modules(). Kept as a plain tuple of importable module names (not
# imports at module scope) so a broken/missing suite registry can't crash
# import of shared.module_registry itself -- see _suite_modules() below.
_SUITE_REGISTRY_MODULES: tuple[str, ...] = (
    "Vol_Suite.module_registry",
    "sentiment-scanner.module_registry",
    "Options_Suite.module_registry",
    "VaR_Tools_Simulations.module_registry",
)


def _suite_modules() -> list[ModuleSpec]:
    """Aggregate MODULES from every suite's module_registry.py.

    Imports each suite's module_registry defensively: a missing or broken
    suite registry should not crash all_modules() for the others -- catch
    import errors per-suite, skip that suite's contribution, and never let
    one suite's bug hide every module.
    """
    modules: list[ModuleSpec] = []
    for dotted_name in _SUITE_REGISTRY_MODULES:
        # "sentiment-scanner" isn't a valid Python identifier segment, so it
        # can't be imported via a dotted `import` statement -- fall back to
        # importlib for every suite uniformly (works for hyphenated and
        # plain package names alike).
        try:
            mod = importlib.import_module(dotted_name)
        except Exception:
            # Defensive by design: a suite's registry may not exist yet, or
            # may be broken, without that hiding every other suite's modules.
            continue
        suite_modules = getattr(mod, "MODULES", None)
        if suite_modules:
            modules.extend(suite_modules)
    return modules


def from_tool_spec(tool_spec: ToolSpec) -> ModuleSpec:
    """Adapt a Tools/registry.py ToolSpec into a ModuleSpec.

    ToolSpec.run(context) -> dict is a strict subset of ModuleSpec's contract
    (ModuleSpec.run returns a ModuleResult, not a bare dict), so this wraps
    the tool's run callable rather than reassigning it directly. Tools/
    entries now carry suite/category metadata via the extended ToolSpec.
    """

    def _run(context: dict[str, Any]) -> ModuleResult:
        result = tool_spec.run(context)
        return ModuleResult(
            status="ok", artifacts=[], metrics=result, context_patch=None
        )

    return ModuleSpec(
        name=tool_spec.name,
        slug=tool_spec.slug,
        suite=tool_spec.suite,
        category=tool_spec.category,
        run=_run,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="global"),
        description=tool_spec.description,
        inputs=InputSpec(),
        output_kind="metrics",
        sample={},
    )


def _tool_modules() -> list[ModuleSpec]:
    from Tools.registry import TOOLS

    return [from_tool_spec(tool_spec) for tool_spec in TOOLS]


def all_modules() -> list[ModuleSpec]:
    """Every registered module, repo-wide: the four suites' MODULES lists
    plus every Tools/registry.py entry adapted via from_tool_spec.
    """
    return _suite_modules() + _tool_modules()


def resolve_modules(slugs: list[str]) -> list[ModuleSpec]:
    """Look up each slug in all_modules(), preserving the input order.

    Raises ValueError naming the bad slug (and listing valid slugs) on an
    unknown slug. An empty input list returns an empty list, no error.
    """
    if not slugs:
        return []
    index = {module.slug: module for module in all_modules()}
    resolved: list[ModuleSpec] = []
    for slug in slugs:
        try:
            resolved.append(index[slug])
        except KeyError:
            valid = ", ".join(sorted(index)) or "(none registered)"
            raise ValueError(
                f"Unknown module slug {slug!r}. Available slugs: {valid}"
            ) from None
    return resolved
