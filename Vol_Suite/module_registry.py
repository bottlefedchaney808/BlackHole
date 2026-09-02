"""module_registry.py (Vol_Suite)

See `shared/module_registry.py` for the contract and
`shared/module_registry.py::all_modules()` for how this gets aggregated.

Task 3 of the modularization overhaul populates this file for the first
time, with the dealer-book module cluster: `dealer_exposure`, `dealer_flow`,
`position_book`, `dual_book`. All four are thin `ModuleSpec` adapters over
already-tested, already-live Vol_Suite code (`expiry_book_production.py`,
`dealer_position_book.py`, `delta_band.py`, `dual_book.py`,
`dealer_positioning.py`) -- no suite-internal logic changes here.
`chain_scanner`, `svi_smile`, and `surface_grids` builders are separate
follow-up tasks, not this file's concern yet.

Flat cwd-relative imports fragile surface (see CLAUDE.md): the five
Vol_Suite-internal modules above import each other via flat top-level names
(e.g. `import expiry_book_exposure as ebe`), which only resolve when
Vol_Suite/ itself is on `sys.path`. Vol_Suite's own tests get this via
`tests/conftest.py`, but this file is also imported as `Vol_Suite.
module_registry` from the repo root (`shared/module_registry.py::
_suite_modules`), where Vol_Suite/ is NOT automatically on `sys.path`.
Insert it defensively, before importing any Vol_Suite-internal module --
verified by `Vol_Suite/tests/test_module_registry_dealer_book.py`'s
repo-root-import test (spawns a fresh subprocess with only the repo root on
sys.path).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

_VOL_SUITE_DIR = Path(__file__).resolve().parent
if str(_VOL_SUITE_DIR) not in sys.path:
    sys.path.insert(0, str(_VOL_SUITE_DIR))
_REPO_ROOT = _VOL_SUITE_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import dealer_flow_module
import dealer_position_book
import delta_band
import dual_book
from dealer_exposure_module import fetch_dealer_exposure

from shared.module_registry import ArchiveHint, ArtifactRef, ModuleResult, ModuleSpec


def _resolve_ticker(context: dict[str, Any]) -> str:
    focus = context.get("focus") or {}
    ticker = str(context.get("ticker") or focus.get("ticker") or "").strip().upper()
    if not ticker:
        raise ValueError(
            "requires a ticker (context['ticker'] or context['focus']['ticker'])."
        )
    return ticker


def _resolve_expiry(context: dict[str, Any]) -> str:
    focus = context.get("focus") or {}
    return str(context.get("expiry") or focus.get("expiry") or "auto").strip()


def _failed(exc: Exception) -> ModuleResult:
    return ModuleResult(
        status="failed",
        artifacts=[],
        metrics={"error": str(exc), "error_type": type(exc).__name__},
        context_patch=None,
    )


# ---------------------------------------------------------------------------
# dealer_exposure
# ---------------------------------------------------------------------------


def _run_dealer_exposure(context: dict[str, Any], *, td: Any = None) -> ModuleResult:
    """Fetch the production expiry-book exposure snapshot and render its two
    charts. Fail-loud live-render call site (see CLAUDE.md's dealer-exposure
    fail-loud-vs-backtest-loop fragile surface): a genuine fetch/render
    failure becomes `status="failed"` with the real error in `metrics`,
    never a fake `status="ok"`.
    """
    try:
        ticker = _resolve_ticker(context)
        expiry = _resolve_expiry(context)
        output_dir = str(context.get("output_dir") or ".")
        files, interp, result = fetch_dealer_exposure(ticker, expiry, output_dir, td=td)
    except Exception as exc:  # noqa: BLE001 -- fail-loud: real error, never faked "ok"
        return _failed(exc)

    metrics: dict[str, Any] = {
        "ticker": result.ticker,
        "expiry": result.expiry,
        "spot": result.spot,
        "gex_reference": result.gex_reference,
        "book_gamma": result.book_gamma,
        "charm_1d": result.charm_1d,
        "residual_vanna_inventory": result.residual_vanna_inventory,
        "band_n": result.band_n,
        "band_z": result.band_z,
        "band_regime": result.band_regime,
        "structural_status": result.structural.status,
        "interp": interp,
    }
    return ModuleResult(
        status="ok",
        artifacts=[ArtifactRef(path=f, kind="png") for f in files],
        metrics=metrics,
        context_patch={"dealer_exposure_result": result},
    )


# ---------------------------------------------------------------------------
# dealer_flow
# ---------------------------------------------------------------------------


def _run_dealer_flow(context: dict[str, Any]) -> ModuleResult:
    """Reuses `dealer_exposure`'s already-fetched result (via `requires=
    ["dealer_exposure"]` + `context_patch`) rather than re-fetching -- see
    `dealer_flow_module.py`'s module docstring for the documented judgment
    call on why the vendor 7d vanna flow fields are meaningful under the
    default `EXPOSURE_BOOK_FLOW=0` fetch.
    """
    exposure_result = context.get("dealer_exposure_result")
    if exposure_result is None:
        return ModuleResult(
            status="skipped",
            artifacts=[],
            metrics={
                "reason": (
                    "no dealer_exposure_result in context -- dealer_flow "
                    "requires dealer_exposure's context_patch. "
                    "run_selected_modules expands requires=['dealer_exposure'] "
                    "automatically; a caller invoking this run() directly "
                    "without that expansion lands here instead of re-fetching."
                )
            },
            context_patch=None,
        )
    try:
        metrics = dealer_flow_module.extract_flow_metrics(exposure_result)
    except Exception as exc:  # noqa: BLE001
        return _failed(exc)

    if metrics.get("vanna_flow_live") is None:
        metrics["reason"] = (
            f"vanna_flow_live unavailable: {metrics.get('vanna_flow_provenance')}"
        )
        status = "skipped"
    else:
        status = "ok"
    return ModuleResult(
        status=status, artifacts=[], metrics=metrics, context_patch=None
    )


# ---------------------------------------------------------------------------
# position_book
# ---------------------------------------------------------------------------

_POSITION_BOOK_UNITS = "vanna_weighted_oi_delta_contracts"


def _run_position_book(context: dict[str, Any]) -> ModuleResult:
    """Assumed-dealer-position accumulator, wrapped as-is -- no chart (out
    of scope per PLAN_dealer_band_integration_20260901's Phase 8b, still a
    pending human review). Units are explicitly NOT the exposure book's
    dollars/shares -- see the mandatory `units` metrics key.
    """
    try:
        ticker = _resolve_ticker(context)
    except Exception as exc:  # noqa: BLE001
        return _failed(exc)

    lookback = int(context.get("lookback") or 150)
    arm = str(context.get("arm") or "div_signed")
    output_dir = context.get("output_dir")

    try:
        days = dealer_position_book.load_history_days(lookback=lookback)
        if len(days) < 2:
            raise RuntimeError(f"position book needs >=2 cache days, got {len(days)}")
        result = dealer_position_book.accumulate_position_book(
            days, lookback=lookback, arm=arm, ticker=ticker
        )
        fit = dual_book.load_dual_fit()
        band = delta_band.band_position(float(result.total_net), fit)
    except Exception as exc:  # noqa: BLE001 -- fail-loud, matches dealer_exposure
        return _failed(exc)

    artifacts: list[ArtifactRef] = []
    if output_dir:
        report_text = dealer_position_book.format_accumulated_report(result)
        path = os.path.join(str(output_dir), f"position_book_{ticker}.json")
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(
                    {
                        "ticker": ticker,
                        "units": _POSITION_BOOK_UNITS,
                        "report": report_text,
                        "total_net": result.total_net,
                        "arm": result.arm,
                        "lookback": result.lookback,
                        "dates_used": result.dates_used,
                    },
                    fh,
                    indent=2,
                )
            artifacts.append(ArtifactRef(path=path, kind="json"))
        except OSError:
            # Report-artifact write is best-effort; metrics below still
            # carry the real accumulated result either way.
            pass

    metrics: dict[str, Any] = {
        "units": _POSITION_BOOK_UNITS,
        "ticker": ticker,
        "total_net": result.total_net,
        "band_z": band.z,
        "band_regime": band.regime,
        "arm": result.arm,
        "lookback": result.lookback,
        "n_days": len(result.dates_used),
        "dates_first": result.dates_used[0] if result.dates_used else None,
        "dates_last": result.dates_used[-1] if result.dates_used else None,
    }
    return ModuleResult(
        status="ok",
        artifacts=artifacts,
        metrics=metrics,
        context_patch={"position_book_result": result},
    )


# ---------------------------------------------------------------------------
# dual_book
# ---------------------------------------------------------------------------


def _run_dual_book(context: dict[str, Any], *, td: Any = None) -> ModuleResult:
    """Fetches BOTH books via `dual_book.fetch_dual_book` -- does its own
    combined fetch internally, so `requires=[]` (does not depend on
    `dealer_exposure`/`position_book` having run). No chart artifacts
    (`dual_book.py` produces none today).
    """
    try:
        ticker = _resolve_ticker(context)
        expiry = _resolve_expiry(context)
        lookback = int(context.get("lookback") or 150)
        arm = str(context.get("arm") or "div_signed")

        owns_td = td is None
        if owns_td:
            from thetadata_client import ThetaDataController

            td = ThetaDataController()
        try:
            result = dual_book.fetch_dual_book(
                td, ticker, expiry=expiry, lookback=lookback, arm=arm
            )
        finally:
            if owns_td:
                td.close()
    except Exception as exc:  # noqa: BLE001 -- fail-loud, matches dealer_exposure
        return _failed(exc)

    expo = result.exposure
    metrics: dict[str, Any] = {
        "ticker": getattr(expo, "ticker", ticker),
        "units": dict(result.units),
        "exposure_band_n": getattr(expo, "band_n", None),
        "exposure_band_z": getattr(expo, "band_z", None),
        "exposure_band_regime": getattr(expo, "band_regime", None),
        "position_total_net": result.position.total_net,
        "position_z": result.position_z,
        "position_regime": result.position_regime,
        "spread": result.spread,
    }
    return ModuleResult(
        status="ok",
        artifacts=[],
        metrics=metrics,
        context_patch={"dual_book_result": result},
    )


MODULES: list[ModuleSpec] = [
    ModuleSpec(
        name="Dealer Exposure",
        slug="dealer_exposure",
        suite="vol_suite",
        category="exposure",
        run=_run_dealer_exposure,
        cli_entry="Vol_Suite/dealer_exposure_module.py",
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
    ),
    ModuleSpec(
        name="Dealer Flow",
        slug="dealer_flow",
        suite="vol_suite",
        category="flow",
        run=_run_dealer_flow,
        cli_entry="Vol_Suite/dealer_flow_module.py",
        default_selected=False,
        requires=["dealer_exposure"],
        archive=ArchiveHint(key_shape="ticker_expiry"),
    ),
    ModuleSpec(
        name="Position Book",
        slug="position_book",
        suite="vol_suite",
        category="exposure",
        run=_run_position_book,
        cli_entry="Vol_Suite/dealer_position_book.py",
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_only"),
    ),
    ModuleSpec(
        name="Dual Book",
        slug="dual_book",
        suite="vol_suite",
        category="exposure",
        run=_run_dual_book,
        cli_entry="Vol_Suite/dual_book.py",
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
    ),
]
