"""cache_widgets.py -- cache-backed ModuleSpec wrappers for Overview widgets.

Positions, signals, position_analysis, and surfaces are written by background
jobs and agent pushes, not by the generic widget run API. These wrappers read
from the legacy unscoped widget_cache table and return ModuleResult for the
generic run API to use.

Each wrapper is registered in shared/module_registry.py so resolve_modules()
knows about them. InputSpec.ticker is 'optional' so the Run button doesn't
demand SPY.
"""

from __future__ import annotations

from typing import Any

from shared.module_registry import (
    ArchiveHint,
    ArtifactRef,
    InputSpec,
    ModuleResult,
    ModuleSpec,
)


def _get_widget_cache_path():
    """Get the current WIDGET_CACHE_PATH dynamically at runtime."""
    import dashboard.app as dashboard_app

    return dashboard_app.WIDGET_CACHE_PATH


def _b64_artifacts(payload: Any) -> list[ArtifactRef]:
    """Walk a cached payload and lift every ``*image_b64`` value to an ArtifactRef.

    The surfaces widget declares ``output_kind="chart"``; the chart renderer
    reads ``result.artifacts``. But the background job that fills this cache
    row stores its PNGs inline as base64 under
    ``metrics.surfaces.<greek>.image_b64``, so the renderer found no artifacts
    and fell back to "no chart renderer found for this result -- showing raw
    JSON", dumping a screenful of base64 onto the card.

    Lifting them to artifacts with a ``data:`` URI path is enough: the
    renderer's ``resolveArtifactURL`` passes ``data:`` through untouched.
    """
    out: list[ArtifactRef] = []

    def walk(node: Any, trail: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key.endswith("image_b64") and isinstance(value, str) and value:
                    src = value
                    if not src.startswith("data:"):
                        src = "data:image/png;base64," + src
                    out.append(ArtifactRef(path=src, kind="png"))
                else:
                    walk(value, f"{trail}.{key}" if trail else str(key))
        elif isinstance(node, list):
            for item in node:
                walk(item, trail)

    walk(payload, "")
    return out


def _run_positions(context: dict[str, Any]) -> ModuleResult:
    """Read positions from widget_cache.positions."""
    from dashboard.widget_cache import WidgetCache

    cache = WidgetCache(_get_widget_cache_path())
    row = cache.get("positions")
    if row is None:
        return ModuleResult(
            status="idle",
            artifacts=[],
            metrics={"message": "no positions pushed yet"},
            context_patch=None,
        )
    payload = row.get("payload") or {}
    positions = payload.get("positions") or []
    accounts = payload.get("accounts") or []
    held = sorted(
        {
            str(p.get("ticker")).upper()
            for p in positions
            if isinstance(p, dict) and p.get("ticker")
        }
    )
    # held_tickers rides out on context_patch as well as metrics: the book is
    # the scope source for every other tool, so a positions read should seed
    # the basket without anyone clicking an "inject" button first.
    return ModuleResult(
        status=row.get("status") or "ok",
        artifacts=[],
        metrics={
            "headline": (
                f"{len(positions)} positions across {len(accounts)} accounts "
                f"· {', '.join(held) if held else 'no tickers'}"
            ),
            "positions": positions,
            "accounts": accounts,
            "held_tickers": held,
            "computed_at": row.get("computed_at"),
        },
        context_patch={"positions": {"positions": positions}, "held_tickers": held}
        if positions
        else None,
    )


def _run_signals(context: dict[str, Any]) -> ModuleResult:
    """Read signals from widget_cache.signals."""
    from dashboard.widget_cache import WidgetCache

    cache = WidgetCache(_get_widget_cache_path())
    row = cache.get("signals")
    if row is None:
        return ModuleResult(
            status="idle",
            artifacts=[],
            metrics={"message": "no signals computed yet"},
            context_patch=None,
        )
    payload = row.get("payload") or {}
    return ModuleResult(
        status=row.get("status") or "ok",
        artifacts=[],
        metrics=payload,
        context_patch=None,
    )


def _run_position_analysis(context: dict[str, Any]) -> ModuleResult:
    """Read position_analysis from widget_cache.position_analysis."""
    from dashboard.widget_cache import WidgetCache

    cache = WidgetCache(_get_widget_cache_path())
    row = cache.get("position_analysis")
    if row is None:
        return ModuleResult(
            status="idle",
            artifacts=[],
            metrics={"message": "no position analysis computed yet"},
            context_patch=None,
        )
    payload = row.get("payload") or {}
    return ModuleResult(
        status=row.get("status") or "ok",
        artifacts=[],
        metrics=payload,
        context_patch=None,
    )


def _run_surfaces(context: dict[str, Any]) -> ModuleResult:
    """Read surfaces from widget_cache.surfaces."""
    from dashboard.widget_cache import WidgetCache

    cache = WidgetCache(_get_widget_cache_path())
    row = cache.get("surfaces")
    if row is None:
        return ModuleResult(
            status="idle",
            artifacts=[],
            metrics={"message": "no surfaces computed yet"},
            context_patch=None,
        )
    payload = row.get("payload") or {}
    return ModuleResult(
        status=row.get("status") or "ok",
        artifacts=_b64_artifacts(payload),
        metrics=payload,
        context_patch=None,
    )


# ModuleSpecs for the cache-backed widgets.
# These are not in MODULES because they live in this standalone module;
# they are registered via dashboard/app.py::_cache_widget_modules.
CACHE_WIDGET_SPECS: list[ModuleSpec] = [
    ModuleSpec(
        name="Positions",
        slug="positions",
        suite="dashboard",
        category="cache",
        run=_run_positions,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="global"),
        description="Agent-pushed equity and option positions.",
        inputs=InputSpec(ticker="optional", expiry="none", basket="none"),
        output_kind="metrics",
        sample={},
    ),
    ModuleSpec(
        name="Signals",
        slug="signals",
        suite="dashboard",
        category="cache",
        run=_run_signals,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="global"),
        description="Per-ticker volatility premium signals from background tick.",
        inputs=InputSpec(ticker="optional", expiry="none", basket="none"),
        output_kind="metrics",
        sample={},
    ),
    ModuleSpec(
        name="Position Analysis",
        slug="position_analysis",
        suite="dashboard",
        category="cache",
        run=_run_position_analysis,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="global"),
        description="Hedge optimization and simulation results per held ticker.",
        inputs=InputSpec(ticker="optional", expiry="none", basket="none"),
        output_kind="metrics",
        sample={},
    ),
    ModuleSpec(
        name="Surfaces",
        slug="surfaces",
        suite="dashboard",
        category="cache",
        run=_run_surfaces,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="global"),
        description="IV, Vanna, Charm surfaces on SPXW.",
        inputs=InputSpec(ticker="optional", expiry="none", basket="none"),
        output_kind="chart",
        sample={},
    ),
]
