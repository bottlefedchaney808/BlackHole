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
            plot_expiry_book_greek_exposure(result, output_dir=output_dir),
            plot_expiry_book_heatmap(result, output_dir=output_dir),
        ]
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

    files, interp, _result = fetch_dealer_exposure(
        args.ticker.strip().upper(), args.expiry, args.output_dir
    )
    print(interp)
    print("\nCharts written:")
    for f in files:
        print(f"  {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
