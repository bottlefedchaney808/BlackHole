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
import logging
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
class ParamSpec:
    """One module-specific option, rendered as a control on the widget card.

    Scope (ticker/expiry/basket) is shared page-wide and declared by
    InputSpec; a ParamSpec is the opposite -- a knob that belongs to one
    module and no other, and that a caller passes under ``params`` (the
    dashboard's ``POST /api/widgets/{slug}/run`` body, or just a key in the
    context dict for a scripted call).

    Added because several modules already accepted meaningful options that
    had no way to reach them from the UI: GARCH takes the jump-filter inputs
    from ``jump_diffusion/garch_bridge.py``, and the variance-swap pricer can
    fit a companion jump model, but a widget card could only ever run them at
    their defaults.

    kind:    "bool" | "choice" | "number" | "text"
    choices: allowed values when kind == "choice" (first is not implicitly
             the default -- state `default` explicitly)
    """

    name: str
    label: str
    kind: str = "bool"
    default: Any = None
    choices: tuple[str, ...] = ()
    help: str = ""


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

    name:             human display, e.g. "Expiry Exposure"
    slug:             stable id, e.g. "expiry_exposure" -- used in CLI
                      --modules, DB, URLs. Alias: dealer_exposure (do not
                      confuse with the Dealer Book tab).
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
                      requires expiry_exposure's fetch)
    archive:          tells the archiver what kind of artifacts/keys to
                      expect for this module
    description:      one or two sentence UI description
    inputs:           InputSpec declaring required/optional context fields
    output_kind:      "metrics" | "chart" | "table" -- how the dashboard
                      should render this module's metrics
    sample:           example context dict for testing/documentation
    runnable:         False if run() raises because the slug is only a
                      selection marker inside a larger pipeline
    superseded_by:    slug of the module a UI should offer instead of this
                      one (this one runs, but is not a useful card alone)
    provides:         context keys this module's context_patch writes, so a
                      dependency already satisfied in context can be skipped
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
    params: tuple[ParamSpec, ...] = ()
    #: False for a slug that exists only so a UI can *select* it as part of a
    #: larger pipeline run -- its own run() raises. Such a module must not be
    #: offered as an addable tool card, since clicking Run can only ever
    #: produce a NotImplementedError.
    runnable: bool = True
    #: Slug of a registered module that does this one's job properly on its
    #: own. Set it when a module RUNS fine but is not a useful card by itself
    #: -- the four `surface_*` modules compute a grid and never draw it, and
    #: the picture only exists in the `surface-explorer` tool that wraps
    #: them. A card picker hides these and offers the replacement instead, so
    #: nothing on the desk is a dead end you have to run to discover.
    superseded_by: str = ""
    #: Context keys this module's `context_patch` writes. Declared so an
    #: auto-added `requires` dependency can be SKIPPED when the context
    #: already carries everything it would produce (the dashboard seeds
    #: prior results from the Context Store before every run). Without this,
    #: declaring a real dependency would mean re-running a billed 2-year
    #: pull on every click of the dependent card.
    provides: tuple[str, ...] = ()

    def is_pickable(self) -> bool:
        """Can this slug be offered as a standalone card?

        The rule the desk enforces: everything you can pick, runs. A module
        is not pickable if running it on its own raises (`runnable=False`) or
        if a different registered slug is the one that actually does the job
        (`superseded_by`).
        """
        return self.runnable and not self.superseded_by


# Suites whose module_registry.py::MODULES lists get aggregated by
# all_modules(). Kept as a plain tuple of importable module names (not
# imports at module scope) so a broken/missing suite registry can't crash
# import of shared.module_registry itself -- see _suite_modules() below.
logger = logging.getLogger(__name__)

# Why a suite's registry failed to import, keyed by dotted name. Populated by
# _suite_modules() on every call. Read it when the catalog looks short -- a
# suite that fails to import contributes ZERO modules and used to do so with
# no trace at all.
SUITE_IMPORT_ERRORS: dict[str, str] = {}

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
        except Exception as exc:
            # Still defensive by design -- one suite's broken registry must not
            # hide the other three. But NOT silent any more: swallowing this
            # bare meant a flat-import collision could drop an entire suite
            # from the catalog with no trace. Concretely: Options_Suite and
            # Vol_Suite BOTH ship an `expiry_selector.py` and only Vol_Suite's
            # defines DEFAULT_A, so if Options_Suite precedes Vol_Suite on
            # sys.path, Vol_Suite's registry raises AttributeError here and all
            # 19 of its modules -- expiry_exposure, garch, correlation_matrix,
            # the surface_* set -- vanish from GET /api/widgets/catalog, with
            # resolve_modules() reporting only "Unknown module slug".
            SUITE_IMPORT_ERRORS[dotted_name] = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "module registry: suite %s failed to import (%s: %s); "
                "its modules are MISSING from the catalog",
                dotted_name,
                type(exc).__name__,
                exc,
            )
            continue
        else:
            SUITE_IMPORT_ERRORS.pop(dotted_name, None)
        suite_modules = getattr(mod, "MODULES", None)
        if suite_modules:
            modules.extend(suite_modules)
    return modules


# ---------------------------------------------------------------------------
# Tools/ payload -> ModuleResult adaptation
# ---------------------------------------------------------------------------
# A Tools/tools/*.py run() returns a bare dict, and the chart it drew is a
# filesystem PATH buried in one of that dict's fields. Nothing renders a
# metrics field as an image, so a tool adapted onto a widget card reported
# `artifacts=[]` while its PNG sat on disk in the run's own output_dir --
# which is exactly the "the surface does not render" symptom (confirmed live:
# outputs/<run_id>/SPCX_gamma_surface_*.png written, card showed a number
# grid). dashboard/panels.py already lifted the path for the panel tabs; this
# is that same lift, moved to the one place BOTH paths go through, so the
# desk-card path cannot drift away from the panel path again.
TOOL_CHART_KEYS: tuple[str, ...] = ("chart_path", "chart_paths", "charts")

# Dense numeric payload keys that must never reach a metrics table -- each is
# an array the chart already draws, and dumping one into a tile buries the
# handful of fields on the card that actually say something.
TOOL_GRID_KEYS: tuple[str, ...] = (
    "grid",
    "strikes",
    "dtes",
    "tenors_years",
    "strike_edges",
    "time_labels",
    "expiries",
    "skipped",
    "curves",
    "market_iv",
    "moneyness",
    "raw_points",
)

_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".svg", ".webp")


def _chart_kind(path: str) -> str:
    lowered = str(path).lower()
    for suffix in _IMAGE_SUFFIXES:
        if lowered.endswith(suffix):
            return "png" if suffix in (".png", ".jpg", ".jpeg", ".webp") else "svg"
    return "png"


def tool_payload_to_result(
    payload: dict[str, Any] | None,
    *,
    chart_keys: tuple[str, ...] = TOOL_CHART_KEYS,
    drop: tuple[str, ...] = TOOL_GRID_KEYS,
) -> ModuleResult:
    """Adapt a `Tools/tools/*` run() dict into a ModuleResult.

    Chart paths are lifted out of the payload into `artifacts` (where every
    renderer looks); `drop` keys are removed from `metrics`. A chart key that
    is present but None means the tool's plot step failed while its data is
    still good -- the card is told so explicitly rather than being left to
    look complete.
    """
    metrics: dict[str, Any] = {}
    artifacts: list[ArtifactRef] = []
    chart_key_seen = False
    chart_missing = False
    for key, value in (payload or {}).items():
        if key in chart_keys:
            chart_key_seen = True
            values = value if isinstance(value, (list, tuple)) else [value]
            found = False
            for item in values:
                if item:
                    artifacts.append(
                        ArtifactRef(path=str(item), kind=_chart_kind(str(item)))
                    )
                    found = True
            if not found:
                chart_missing = True
            continue
        if key in drop:
            continue
        metrics[key] = value
    if chart_key_seen and chart_missing and not artifacts:
        metrics.setdefault(
            "chart", "not rendered (the tool's plot step failed; data below is real)"
        )
    status = "ok" if artifacts or metrics else "skipped"
    return ModuleResult(
        status=status, artifacts=artifacts, metrics=metrics, context_patch=None
    )


def from_tool_spec(tool_spec: ToolSpec) -> ModuleSpec:
    """Adapt a Tools/registry.py ToolSpec into a ModuleSpec.

    ToolSpec.run(context) -> dict is a strict subset of ModuleSpec's contract
    (ModuleSpec.run returns a ModuleResult, not a bare dict), so this wraps
    the tool's run callable rather than reassigning it directly. Tools/
    entries now carry suite/category metadata via the extended ToolSpec.
    """

    def _run(context: dict[str, Any]) -> ModuleResult:
        # Every surface this adapter feeds is the dark dashboard; a tool that
        # honours the flag would otherwise draw a white-background chart onto
        # a dark card. Tools that don't read it ignore it.
        ctx = dict(context)
        ctx.setdefault("dark_theme", True)
        # A declared param default is a promise the card makes; honour it
        # when the caller omits the key (an older saved layout, the agent, a
        # curl). Without this a tool whose run() dispatches on e.g. `mode`
        # raised "requires context['mode']" despite declaring a default.
        for param in getattr(tool_spec, "params", ()) or ():
            default = getattr(param, "default", None)
            if default is not None and ctx.get(param.name) in (None, ""):
                ctx[param.name] = default
        result = tool_spec.run(ctx)
        return tool_payload_to_result(result)

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
        # Carry the tool's own knobs through. Without this a ToolSpec that
        # declares params is adapted into a ModuleSpec with none, and the
        # card renders no controls for options its run() requires.
        params=tuple(getattr(tool_spec, "params", ()) or ()),
    )


def _tool_modules() -> list[ModuleSpec]:
    from Tools.registry import TOOLS

    return [from_tool_spec(tool_spec) for tool_spec in TOOLS]


def _dashboard_cache_modules() -> list[ModuleSpec]:
    """Dashboard's cache-backed widgets: positions, signals, position_analysis, surfaces.

    These are written by background jobs and agent pushes, not by the generic
    widget run API. We include them in all_modules() so resolve_modules() works
    for POST /api/widgets/{slug}/run, but they're registered via dashboard's
    _cache_widget_modules() helper which is called during app init.
    """
    try:
        from dashboard.cache_widgets import CACHE_WIDGET_SPECS

        return CACHE_WIDGET_SPECS
    except Exception:
        return []


def all_modules() -> list[ModuleSpec]:
    """Every registered module, repo-wide: the four suites' MODULES lists
    plus every Tools/registry.py entry adapted via from_tool_spec,
    plus dashboard's cache-backed widgets.
    """
    return _suite_modules() + _tool_modules() + _dashboard_cache_modules()


# Old widget slug. Canonical is expiry_exposure (the catalog name).
# Dealer Book is a different surface (dashboard tab), not this module.
SLUG_ALIASES: dict[str, str] = {
    "dealer_exposure": "expiry_exposure",
}


def resolve_modules(slugs: list[str]) -> list[ModuleSpec]:
    """Look up each slug in all_modules(), preserving the input order.

    Raises ValueError naming the bad slug (and listing valid slugs) on an
    unknown slug. An empty input list returns an empty list, no error.
    `dealer_exposure` is accepted as an alias for `expiry_exposure`.
    """
    if not slugs:
        return []
    index = {module.slug: module for module in all_modules()}
    resolved: list[ModuleSpec] = []
    for slug in slugs:
        key = SLUG_ALIASES.get(slug, slug)
        try:
            resolved.append(index[key])
        except KeyError:
            valid = ", ".join(sorted(index)) or "(none registered)"
            raise ValueError(
                f"Unknown module slug {slug!r}. Available slugs: {valid}"
            ) from None
    return resolved
