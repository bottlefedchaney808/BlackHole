"""Phase 8 (PLAN §8.5): DUAL-BOOK production output.

One command prints BOTH books side by side:

  * Exposure book  — expiry_book_production.fetch_production_result(): the
    live SNAPSHOT of the option book, with its band z already computed
    (Phase 2 wiring) on the bucketed_0_10 OU fit. Units: shares
    (delta x OI x 100).
  * Position book  — dealer_position_book.accumulate_position_book(): the
    ASSUMED DEALER POSITION accumulated over a 150-day rolling window with
    the div_signed arm (Cem's dIV-signed flow rule). Units: vanna-weighted
    OI-delta contract units — REPORTED AS-IS, NOT dollars.

The spread = exposure_n - position_n is printed with an explicit mixed-units
caveat: compare direction / magnitude class, never exact dollars.

Pure orchestration: no greek math lives here. Band z for the position book
uses the SAME bucketed_0_10 fit as the exposure book (one fit, two positions).

CLI (real live run — ThetaData creds from repo-root .env):

    python dual_book.py SPY
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import dealer_position_book as dpb
from dealer_position_book import (
    PositionBookResult,
    accumulate_position_book,
    load_history_days,
)
from delta_band import BandFit, band_position, load_band_fit

_BAND_FIT_DEFINITION = "bucketed_0_10"  # active_for_live per the Phase-1 fit JSON

_UNITS = {
    "exposure": "shares",  # delta x OI x 100 (dollars of share-equivalent delta)
    "position": "vanna_weighted_oi_delta_contracts",  # NOT dollars — report as-is
}


@dataclass
class DualBookResult:
    exposure: Any  # expiry_book_production.ProductionDealerExposure (or stand-in)
    position: PositionBookResult
    position_z: float | None = None
    position_regime: str | None = None
    spread: float | None = None
    units: dict = field(default_factory=lambda: dict(_UNITS))
    provenance: dict = field(default_factory=dict)


def _default_fit_path() -> str:
    env = os.environ.get("BAND_FIT_PATH")
    if env:
        return env
    try:
        import expiry_book_production as ebp

        return ebp._BAND_FIT_PATH
    except Exception:
        return os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "_expiry_falsifier_cache",
            "opex_full_book",
            "ou_band_fit.json",
        )


def load_dual_fit(path: str | None = None) -> BandFit:
    """The single bucketed_0_10 fit both books are z'd against."""
    return load_band_fit(path or _default_fit_path(), definition=_BAND_FIT_DEFINITION)


def build_dual_book(
    exposure: Any,
    position: PositionBookResult,
    fit: BandFit,
) -> DualBookResult:
    """Pure assembly: spread + position z from the SAME fit. No I/O."""
    exposure_n = getattr(exposure, "band_n", None)
    pos_n = position.total_net

    position_z = None
    position_regime = None
    if pos_n is not None:
        pos_bp = band_position(float(pos_n), fit)
        position_z = float(pos_bp.z)
        position_regime = str(pos_bp.regime)

    spread = None
    if exposure_n is not None and pos_n is not None:
        spread = float(exposure_n) - float(pos_n)

    dates = list(getattr(position, "dates_used", []) or [])
    provenance = {
        "band_definition": fit.definition,
        "fit_window_first": str(fit.fit_window.get("first", "unknown")),
        "fit_window_last": str(fit.fit_window.get("last", "unknown")),
        "arm": getattr(position, "arm", "unknown"),
        "lookback": str(getattr(position, "lookback", "unknown")),
        "dates_first": dates[0] if dates else "none",
        "dates_last": dates[-1] if dates else "none",
        "n_days": str(len(dates)),
    }
    return DualBookResult(
        exposure=exposure,
        position=position,
        position_z=position_z,
        position_regime=position_regime,
        spread=spread,
        units=dict(_UNITS),
        provenance=provenance,
    )


def _resolve_auto_expiry(td: Any, ticker: str) -> str:
    """expiry='auto': nearest listed expiry with positive DTE (0DTE would be
    rejected by the production validator). Falls back to the first listing."""
    from datetime import date, datetime

    listed = sorted(
        str(e).replace("-", "") for e in (td.list_expirations(ticker) or [])
    )
    today = date.today()
    fallback = None
    for e in listed:
        if len(e) != 8 or not e.isdigit():
            continue
        try:
            dte = (datetime.strptime(e, "%Y%m%d").date() - today).days
        except ValueError:
            continue
        if fallback is None:
            fallback = e
        if dte >= 1:
            return e
    if fallback is None:
        raise ValueError(f"no listed expirations for {ticker}")
    return fallback


def fetch_dual_book(
    td: Any,
    ticker: str,
    expiry: str = "auto",
    lookback: int = 150,
    arm: str = "div_signed",
    cache_dir: str | None = None,
    fit_path: str | None = None,
) -> DualBookResult:
    """Fetch BOTH books live and assemble the dual view.

    (a) exposure book via the production adapter (band fields included);
    (b) position book from the Phase-1 cache via the Phase-7 accumulator.
    """
    # (a) exposure snapshot book — band z already wired inside.
    import expiry_book_production as ebp

    resolved = expiry
    if resolved == "auto":
        resolved = _resolve_auto_expiry(td, ticker)
    exposure = ebp.fetch_production_result(td, ticker, resolved)

    # (b) assumed dealer position — cache-backed accumulation.
    days = load_history_days(cache_dir=cache_dir or dpb._CACHE_DIR, lookback=lookback)
    if len(days) < 2:
        raise RuntimeError(f"position book needs >=2 cache days, got {len(days)}")
    position = accumulate_position_book(
        days, lookback=lookback, arm=arm, ticker=ticker.upper()
    )

    fit = load_dual_fit(fit_path)
    return build_dual_book(exposure, position, fit)


def format_dual_book_interp(dual: DualBookResult) -> str:
    """Desk-readable block: both books, band z on each, the spread, caveat."""
    expo = dual.exposure
    pos = dual.position
    ticker = getattr(expo, "ticker", "?")

    lines = [f"=== DUAL BOOK ({ticker}) ==="]

    # Exposure book (snapshot, units: shares).
    if getattr(expo, "band_n", None) is not None:
        lines.append(
            f"Exposure book (snapshot): N=${float(expo.band_n) / 1e6:,.1f}M "
            f"z={float(expo.band_z):+.2f} {expo.band_regime}"
        )
    else:
        prov = getattr(expo, "band_fit_provenance", "") or "unavailable"
        lines.append(f"Exposure book (snapshot): band unavailable ({prov})")

    # Position book (accumulated flow, units: vanna-weighted OI-delta — NOT $).
    arm = getattr(pos, "arm", "?")
    lookback = getattr(pos, "lookback", "?")
    dates = list(getattr(pos, "dates_used", []) or [])
    window = f"{dates[0]}->{dates[-1]}" if dates else "no dates"
    if dual.position_z is not None:
        lines.append(
            f"Position book (accumulated flow, {lookback}d {arm}, {window}): "
            f"N={float(pos.total_net) / 1e6:.2f}M vanna-weighted-oi "
            f"z={dual.position_z:+.2f} {dual.position_regime}"
        )
    else:
        lines.append("Position book (accumulated flow): N unavailable")

    # Spread with the mixed-units caveat.
    if dual.spread is not None:
        lines.append(
            f"Spread (exposure - position): {float(dual.spread) / 1e6:,.2f}M "
            "[units caveat: mixed units — compare direction/magnitude class, "
            "not exact dollars]"
        )
    else:
        lines.append("Spread (exposure - position): n/a (one book missing)")

    prov = dual.provenance
    lines.append(
        f"Provenance: fit={prov.get('band_definition')} "
        f"window={prov.get('fit_window_first')}-{prov.get('fit_window_last')} "
        f"arm={prov.get('arm')} lookback={prov.get('lookback')} "
        f"days={prov.get('n_days')}"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI: real live run
# ---------------------------------------------------------------------------


_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "Vol_Suite")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load_root_env() -> None:
    """Load repo-root .env (ThetaData creds), like band_monitor."""
    for candidate in (
        Path(__file__).resolve().parent.parent / ".env",  # worktree root
        Path(r"C:/Users/bottl/FinancialDevelopment/.env"),
    ):
        if candidate.exists():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if k and k not in os.environ:
                        os.environ[k] = v
            break


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Dual-book output: exposure snapshot + assumed dealer position"
    )
    ap.add_argument("ticker", nargs="?", default="SPY")
    ap.add_argument("expiry", nargs="?", default="auto")
    ap.add_argument("--lookback", type=int, default=150)
    ap.add_argument("--arm", choices=("div_signed", "fixed_sign"), default="div_signed")
    args = ap.parse_args(argv)

    _load_root_env()
    import shared.thetadata as th

    ticker = args.ticker.upper()
    td = th.ThetaDataController()  # NOTE: first positional arg is base_url, not ticker
    dual = fetch_dual_book(
        td,
        ticker,
        expiry=args.expiry,
        lookback=args.lookback,
        arm=args.arm,
    )
    print(format_dual_book_interp(dual))

    _archive_standalone_run(ticker, dual)
    return 0


def _archive_standalone_run(ticker: str, dual: DualBookResult) -> None:
    """Phase 6 of the modularization overhaul (Task 7): see
    `dealer_exposure_module.py::_archive_standalone_run`'s docstring for the
    shared rationale. `metrics` mirrors `Vol_Suite/module_registry.py::
    _run_dual_book`'s field set. No chart artifacts (`dual_book.py`
    produces none today, matching the ModuleSpec wrapper)."""
    try:
        from shared.module_archive import record as archive_record
        from shared.module_registry import ModuleResult, resolve_modules

        module_spec = resolve_modules(["dual_book"])[0]
        expo = dual.exposure
        context = {"ticker": ticker, "expiry": getattr(expo, "expiry", None)}
        metrics = {
            "ticker": getattr(expo, "ticker", ticker),
            "units": dict(dual.units),
            "exposure_band_n": getattr(expo, "band_n", None),
            "exposure_band_z": getattr(expo, "band_z", None),
            "exposure_band_regime": getattr(expo, "band_regime", None),
            "position_total_net": dual.position.total_net,
            "position_z": dual.position_z,
            "position_regime": dual.position_regime,
            "spread": dual.spread,
        }
        module_result = ModuleResult(
            status="ok", artifacts=[], metrics=metrics, context_patch=None
        )
        archive_record(module_result, module_spec, context, triggered_by="cli")
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "dual_book standalone CLI: could not archive run (archiving is "
            "best-effort; the run itself already completed)",
            exc_info=True,
        )


if __name__ == "__main__":
    raise SystemExit(main())
