"""dealer_flow_module.py -- standalone CLI + shared metrics-extraction logic
for the `dealer_flow` module-registry entry (Vol_Suite dealer-book cluster,
Task 3 of the modularization overhaul).

Judgment call (documented per the task brief): `EXPOSURE_BOOK_FLOW` defaults
to "0", which gates only the *intraday quote-based* flow layer inside
`expiry_book_production.production_result_from_rows` (the
`apply_vannacharm_flow` path -- `flow_volume_rows`/`flow_provenance` stay
0/"snapshot_only" under the default). The *vendor 7-day ΔIV vanna flow*
(`vanna_flow_live`/`vanna_flow_provenance`/`d_iv_used`, driven by
`get_iv_surface_change`) is a SEPARATE mechanism inside the same fetch and
is NOT gated by `EXPOSURE_BOOK_FLOW` -- it is already populated by the
default `dealer_exposure` fetch. So `dealer_flow` reuses `dealer_exposure`'s
already-fetched result (via `requires=["dealer_exposure"]` +
`context_patch`) rather than triggering a second `EXPOSURE_BOOK_FLOW=1`
fetch: the meaningful flow signal doesn't need that flag. `flow_layer`
(and the always-present but usually-empty `flow_volume_rows`/
`flow_provenance`) are still surfaced in metrics so a caller can see
whether the legacy intraday layer happened to be active.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

_VOL_SUITE_DIR = Path(__file__).resolve().parent
if str(_VOL_SUITE_DIR) not in sys.path:
    sys.path.insert(0, str(_VOL_SUITE_DIR))
_REPO_ROOT = _VOL_SUITE_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dealer_exposure_module import fetch_dealer_exposure


def extract_flow_metrics(exposure_result: Any) -> dict[str, Any]:
    """Pull the flow-specific fields off an already-fetched
    `ProductionDealerExposure` -- pure, no I/O. Shared by `dealer_flow`'s
    `ModuleSpec.run()` (which reuses `dealer_exposure`'s `context_patch`)
    and this file's standalone CLI (which fetches its own copy via
    `dealer_exposure_module.fetch_dealer_exposure`).
    """
    return {
        "vanna_flow_live": exposure_result.vanna_flow_live,
        "vanna_flow_provenance": exposure_result.vanna_flow_provenance,
        "d_iv_used": exposure_result.d_iv_used,
        "flow_volume_rows": exposure_result.flow_volume_rows,
        "flow_provenance": exposure_result.flow_provenance,
        "flow_layer": exposure_result.flow_layer,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Dealer flow metrics: vendor 7d vanna flow + intraday flow-layer status"
    )
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--expiry", default="auto")
    ap.add_argument("--output-dir", default=".")
    args = ap.parse_args(argv)

    ticker = args.ticker.strip().upper()
    _files, interp, result = fetch_dealer_exposure(ticker, args.expiry, args.output_dir)
    metrics = extract_flow_metrics(result)
    print(interp)
    print("\nFlow metrics:")
    for k, v in metrics.items():
        print(f"  {k}: {v}")

    _archive_standalone_run(ticker, result.expiry, metrics)
    return 0


def _archive_standalone_run(ticker: str, expiry: str, metrics: dict[str, Any]) -> None:
    """See `dealer_exposure_module.py::_archive_standalone_run`'s docstring
    -- same rationale (standalone CLI bypasses `run_selected_modules`
    entirely, so it archives directly, never raising). No chart artifacts
    of its own: `dealer_flow` extracts fields from the same fetch
    `dealer_exposure` already rendered, and this CLI doesn't re-render.
    """
    try:
        from shared.module_archive import record as archive_record
        from shared.module_registry import ModuleResult, resolve_modules

        module_spec = resolve_modules(["dealer_flow"])[0]
        context = {"ticker": ticker, "expiry": expiry}
        module_result = ModuleResult(
            status="ok", artifacts=[], metrics=dict(metrics), context_patch=None
        )
        archive_record(module_result, module_spec, context, triggered_by="cli")
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "dealer_flow_module standalone CLI: could not archive run "
            "(archiving is best-effort; the run itself already completed)",
            exc_info=True,
        )


if __name__ == "__main__":
    raise SystemExit(main())
