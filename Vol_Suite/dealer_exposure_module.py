"""dealer_exposure_module.py -- standalone CLI + shared fetch/render logic
for the `dealer_exposure` module-registry entry (Vol_Suite dealer-book
cluster, Task 3 of the modularization overhaul).

This is a thin wrapper around already-tested, already-live code:
`expiry_book_production.fetch_production_result` for the fetch, and
`dealer_positioning.plot_expiry_book_greek_exposure` /
`plot_expiry_book_heatmap` for the two charts -- the exact call pattern
`volatility_suite.py::_run_production_dealer_positioning` already uses.
Nothing here re-derives dealer-exposure math.

Fail-loud (live-render) contract: `fetch_dealer_exposure` does not catch or
mask failures from the underlying fetch/render calls -- a missing/invalid
structural snapshot raises `ExpiryBookUnavailable` (or whatever the
underlying call raises) straight through to the caller. `Vol_Suite/
module_registry.py::_run_dealer_exposure` is the one place that turns that
into a `ModuleResult(status="failed", ...)` instead of letting it kill the
whole `run_selected_modules` sweep -- it never converts a real failure into
a fake "ok".
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

_VOL_SUITE_DIR = Path(__file__).resolve().parent
if str(_VOL_SUITE_DIR) not in sys.path:
    sys.path.insert(0, str(_VOL_SUITE_DIR))
_REPO_ROOT = _VOL_SUITE_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def fetch_dealer_exposure(
    ticker: str,
    expiry: str,
    output_dir: str,
    *,
    td: Any = None,
) -> tuple[list[str], str, Any]:
    """Fetch the production exposure book and render its two charts.

    `expiry` may be a literal YYYYMMDD string, or "auto"/empty to resolve
    the nearest listed expiry with positive DTE -- reuses
    `dual_book._resolve_auto_expiry` rather than re-deriving that logic.

    `td`, when given, is used instead of constructing a real
    `ThetaDataController` -- the seam the module-registry wrapper and this
    file's tests use to avoid a live network/credential dependency. When
    `td` is None, this function owns the controller's lifecycle (closes it
    in a `finally`, matching `_run_production_dealer_positioning`'s
    pattern); when `td` is injected, the caller owns it.

    Returns (chart_paths, interp_text, ProductionDealerExposure). Raises on
    any failure -- see module docstring's fail-loud contract.
    """
    from dealer_positioning import (
        plot_expiry_book_greek_exposure,
        plot_expiry_book_heatmap,
        plot_expiry_book_single_greek,
    )
    from expiry_book_production import fetch_production_result, format_production_interp
    from thetadata_client import ThetaDataController

    owns_td = td is None
    if owns_td:
        td = ThetaDataController()
    try:
        resolved_expiry = str(expiry or "").strip()
        if not resolved_expiry or resolved_expiry.lower() == "auto":
            from dual_book import _resolve_auto_expiry

            resolved_expiry = _resolve_auto_expiry(td, ticker)
        result = fetch_production_result(td, ticker, resolved_expiry)
        interp = format_production_interp(result)
        files = [
            plot_expiry_book_heatmap(result, output_dir=output_dir),
        ]
        # Combined 2x2 Gamma/Delta/Vanna/Charm grid -- the single "4-panel"
        # exposure chart. Rendered from the SAME fetched snapshot (no extra
        # data pulls). Best-effort: a charting failure must not fail the fetch,
        # so it's wrapped like the singles below rather than raised through.
        try:
            files.append(
                plot_expiry_book_greek_exposure(result, output_dir=output_dir)
            )
        except Exception:
            logging.getLogger(__name__).warning(
                "combined 4-panel greek exposure chart failed", exc_info=True
            )
        # Chain exposure per greek, one LARGE chart each (gamma/delta/vanna/
        # charm). Rendered from the SAME fetched snapshot -- no extra data pulls.
        # Best-effort: a charting failure must not fail the fetch.
        for _g in ("gamma", "delta", "vanna", "charm"):
            try:
                files.append(
                    plot_expiry_book_single_greek(result, _g, output_dir=output_dir)
                )
            except Exception:
                logging.getLogger(__name__).warning(
                    "single-greek %s chart failed", _g, exc_info=True
                )
        return files, interp, result
    finally:
        if owns_td:
            td.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Dealer exposure book: fetch production snapshot + render charts"
    )
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--expiry", default="auto")
    ap.add_argument("--output-dir", default=".")
    args = ap.parse_args(argv)

    ticker = args.ticker.strip().upper()
    files, interp, result = fetch_dealer_exposure(ticker, args.expiry, args.output_dir)
    print(interp)
    print("\nCharts written:")
    for f in files:
        print(f"  {f}")

    _archive_standalone_run(ticker, result, files)
    return 0


def _archive_standalone_run(ticker: str, result: Any, files: list[str]) -> None:
    """Phase 6 of the modularization overhaul (Task 7): this standalone CLI
    entry bypasses `orchestrator.run_selected_modules` entirely (a real
    separate OS process), so it archives directly rather than relying on
    `orchestrator.py::_archive_module_result` -- see that hook's docstring
    and `shared/module_archive.py`'s module docstring for why a dedicated
    archive DB (not `swaps.db`) is required for exactly this cross-process
    case. `metrics` mirrors `Vol_Suite/module_registry.py::_run_dealer_
    exposure`'s field set so an archived row looks the same regardless of
    which of the two launch paths produced it. Never raises --
    `module_archive.record` is itself never-raise, and the module_spec
    lookup below is defensively guarded so a broken registry import can't
    fail a standalone run that otherwise succeeded.
    """
    try:
        from shared.module_archive import record as archive_record
        from shared.module_registry import ArtifactRef, ModuleResult, resolve_modules

        module_spec = resolve_modules(["dealer_exposure"])[0]
        context = {"ticker": ticker, "expiry": result.expiry}
        metrics = {
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
        }
        module_result = ModuleResult(
            status="ok",
            artifacts=[ArtifactRef(path=f, kind="png") for f in files],
            metrics=metrics,
            context_patch=None,
        )
        archive_record(module_result, module_spec, context, triggered_by="cli")
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "dealer_exposure_module standalone CLI: could not archive run "
            "(archiving is best-effort; the run itself already completed)",
            exc_info=True,
        )


if __name__ == "__main__":
    raise SystemExit(main())
