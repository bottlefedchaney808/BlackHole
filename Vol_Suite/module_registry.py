"""module_registry.py (Vol_Suite)

See `shared/module_registry.py` for the contract and
`shared/module_registry.py::all_modules()` for how this gets aggregated.

Task 3 of the modularization overhaul populated this file for the first
time, with the dealer-book module cluster: `dealer_exposure`, `dealer_flow`,
`position_book`, `dual_book`. All four are thin `ModuleSpec` adapters over
already-tested, already-live Vol_Suite code (`expiry_book_production.py`,
`dealer_position_book.py`, `delta_band.py`, `dual_book.py`,
`dealer_positioning.py`) -- no suite-internal logic changes there.

Task 4 adds two more: `chain_scanner` (a thin adapter over
`options_chain_scanner.run_chain_scanner`, same pattern as Task 3) and
`svi_smile` (new glue code -- see its section below for why it's different).

Task 5 adds four more: `surface_greek`, `surface_market_iv`,
`surface_flow_strike_time`, `surface_flow_strike_expiry` -- thin adapters
over `surface_grids.py`'s four strike x expiry / strike x time grid builders
(build_greek_surface, build_market_iv_surface, build_flow_strike_time,
build_flow_strike_expiry). `Tools/tools/surface_explorer_tool.py` (the
existing dashboard-facing wrapper for these same builders) is a thin
compatibility shim over these four modules as of this task -- see its own
docstring.

Flat cwd-relative imports fragile surface (see CLAUDE.md): the Vol_Suite-
internal modules above import each other via flat top-level names (e.g.
`import expiry_book_exposure as ebe`), which only resolve when Vol_Suite/
itself is on `sys.path`. Vol_Suite's own tests get this via
`tests/conftest.py`, but this file is also imported as `Vol_Suite.
module_registry` from the repo root (`shared/module_registry.py::
_suite_modules`), where Vol_Suite/ is NOT automatically on `sys.path`.
Insert it defensively, before importing any Vol_Suite-internal module --
verified by `Vol_Suite/tests/test_module_registry_dealer_book.py`'s
repo-root-import test (spawns a fresh subprocess with only the repo root on
sys.path). `svi_smile`'s "model_comparison" mode additionally needs
Options_Suite/ on sys.path for `smile_by_model.py` -- same treatment.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_VOL_SUITE_DIR = Path(__file__).resolve().parent
# Insert (or MOVE) Vol_Suite/ to the very front of sys.path. The membership
# check alone is not enough: Tools/tools/surface_explorer_tool.py (imported
# eagerly by Tools/registry.py) front-inserts Options_Suite/ -- whose flat
# expiry_selector.py shadows Vol_Suite's (missing DEFAULT_A) -- so in a
# process that imported Tools/ first, Vol_Suite/ could sit BEHIND
# Options_Suite/ and every Vol_Suite-internal flat import below would
# resolve to the wrong module (AttributeError). That failure mode is real
# and silent in production: shared.module_registry._suite_modules() swallows
# the import error by design, so Vol_Suite's modules would simply vanish
# from all_modules() whenever the dashboard (or any Tools/-importing
# process) had already imported Tools/registry. Move-to-front makes this
# file's own imports order-independent -- see
# TestRepoRootImport.test_all_modules_survives_tools_first_import_order.
if str(_VOL_SUITE_DIR) in sys.path:
    sys.path.remove(str(_VOL_SUITE_DIR))
sys.path.insert(0, str(_VOL_SUITE_DIR))
_REPO_ROOT = _VOL_SUITE_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(1, str(_REPO_ROOT))
_OPTIONS_SUITE_DIR = _REPO_ROOT / "Options_Suite"
if str(_OPTIONS_SUITE_DIR) not in sys.path:
    # Appended, NOT inserted at index 0: Options_Suite ships its own flat
    # top-level modules that collide by name with Vol_Suite's (e.g. its own
    # expiry_selector.py, missing DEFAULT_A) -- inserting Options_Suite/
    # ahead of Vol_Suite/ in sys.path made Vol_Suite-internal `import
    # expiry_selector` calls (dealer_positioning.py) resolve to the WRONG
    # module and crash with AttributeError. Appending keeps Options_Suite/
    # only as a last-resort resolver, present just for `import
    # smile_by_model` (svi_smile's "model_comparison" mode) without
    # shadowing anything Vol_Suite-internal.
    sys.path.append(str(_OPTIONS_SUITE_DIR))

import dealer_flow_module
import dealer_position_book
import delta_band
import dual_book
import expiry_book_exposure as ebe
import expiry_book_production as ebp
import expiry_selector
import options_chain_scanner as ocs
import smile_by_model
import surface_grids
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


# ---------------------------------------------------------------------------
# chain_scanner
# ---------------------------------------------------------------------------


def _run_chain_scanner(context: dict[str, Any]) -> ModuleResult:
    """Wraps options_chain_scanner.run_chain_scanner as-is (full single-
    expiry chain scan: greeks pull, SVI/quadratic smile edge-detection,
    vanna positioning read, strategy recommendations). run_chain_scanner
    owns its own ThetaDataController lifecycle internally -- unlike
    dealer_exposure/dual_book above, there's no `td` injection seam here to
    match against, so this helper (like _run_position_book) doesn't take a
    `td` kwarg either.
    """
    try:
        ticker = _resolve_ticker(context)
        expiry = _resolve_expiry(context)
        expiration = None if expiry == "auto" else expiry
        output_dir = str(context.get("output_dir") or ".")
        target_years = float(context.get("target_years") or 0.25)
        files, interp, result = ocs.run_chain_scanner(
            ticker,
            target_years=target_years,
            expiration=expiration,
            output_dir=output_dir,
            jump_risk_signal=context.get("jump_risk_signal"),
        )
    except Exception as exc:  # noqa: BLE001 -- fail-loud, matches dealer_exposure
        return _failed(exc)

    artifacts: list[ArtifactRef] = [
        ArtifactRef(path=f, kind="png" if str(f).lower().endswith(".png") else "csv")
        for f in files
    ]
    # run_chain_scanner always writes chain_strategies.json (either with
    # real recommendations or the "no edges" empty artifact) but doesn't
    # append it to the `files` list it returns -- pick it up here so this
    # module's artifacts list is complete.
    strategies_path = os.path.join(output_dir, "chain_strategies.json")
    if os.path.exists(strategies_path):
        artifacts.append(ArtifactRef(path=strategies_path, kind="json"))

    metrics: dict[str, Any] = {
        "ticker": result.ticker,
        "expiry": result.expiry,
        "spot": result.spot,
        "atm_iv_pct": result.atm_iv_pct,
        "rv_match_pct": result.rv_match_pct,
        "regime": result.regime,
        "verdict": result.verdict,
        "edge_candidates_count": len(result.edge_candidates or []),
        "net_vanna_shares": result.net_vanna_shares,
        "svi_params": result.svi_params,
        "interp": interp,
    }
    return ModuleResult(
        status="ok",
        artifacts=artifacts,
        metrics=metrics,
        context_patch={"chain_scanner_result": result},
    )


# ---------------------------------------------------------------------------
# svi_smile
# ---------------------------------------------------------------------------
#
# Unlike every module above, svi_smile does not wrap one already-existing
# entry point -- it's new thin glue over THREE existing SVI/smile call
# sites that never had a shared standalone runner:
#
#   "chain_scanner"    -> options_chain_scanner.fit_svi_smile, the local
#                          edge-detection smile fit (OTM-only, sqrt(OI)
#                          weighted via svi_rp.calibrate_svi) scan_chain
#                          runs against one ticker's single-expiry chain
#                          DataFrame. fit_svi_smile itself takes an
#                          already-built df/forward/T, not a ticker, so
#                          this mode first gathers those inputs with the
#                          same handful of fetch/forward-price lines
#                          scan_chain runs immediately before calling it
#                          (see _svi_chain_scanner_inputs).
#   "exposure_overlay" -> expiry_book_exposure.svi_rp_overlay, the
#                          production dealer-book cheap/rich book-signing
#                          overlay expiry_book_production.fetch_production_
#                          result runs right before calling it. Gathers the
#                          same chain_iv/oi_by inputs that call site builds
#                          (see _svi_exposure_overlay_inputs), reusing
#                          expiry_book_production.normalize_snapshot_rows/
#                          _merge_greeks_oi rather than re-deriving their
#                          put-call-parity ITM-leg-coverage fallback.
#   "model_comparison" -> Options_Suite.smile_by_model.build_iv_smile_by_model,
#                          which is already ticker-driven and fully
#                          self-contained (fetches its own spot/expiry/
#                          strike/rate/dividend-yield) -- called directly,
#                          no gathering needed.
#
# NOTE on the brief's premise: the brief says all three "ultimately
# delegate to the same underlying svi_rp.calibrate_svi" -- verified false
# for "model_comparison": smile_by_model.build_iv_smile_by_model never
# imports or calls svi_rp at all (confirmed by grep across Options_Suite/).
# It builds a completely different kind of smile -- each Options_Suite
# pricing model's OWN chain-wide IV curve (via chain_evaluation.
# build_smile_comparison) vs. the market's vendor IV, not an SVI reference
# fit. This doesn't block giving it a shared dispatch entry point (the
# brief's actual scope, "give each of the three a single shared standalone
# entry point, nothing more"), but it does mean this module dispatches to
# three genuinely different smile constructions, only two of which
# (chain_scanner, exposure_overlay) touch svi_rp.calibrate_svi.
#
# None of the three call sites' internal edge-detection / book-signing /
# model-comparison logic is reimplemented here -- each mode only gathers
# that site's real inputs (or, for model_comparison, none at all) and
# forwards them to the existing function unchanged.

SVI_SMILE_MODES = ("chain_scanner", "exposure_overlay", "model_comparison")


def _svi_chain_scanner_inputs(td: Any, ticker: str, expiration: str):
    """Same fetch/forward-price plumbing options_chain_scanner.scan_chain
    runs immediately before calling fit_svi_smile -- gathered standalone
    here since fit_svi_smile takes an already-built chain DataFrame +
    forward price, not a bare ticker."""
    spot = td.fetch_spot_price(ticker)
    if spot <= 0:
        raise ValueError(f"Could not fetch spot for {ticker}")
    dividend_yield = td.fetch_dividend_yield(ticker)
    exp_date = datetime.strptime(expiration, "%Y%m%d").replace(tzinfo=UTC).date()
    today = datetime.now(UTC).date()
    actual_T = max((exp_date - today).days, 0) / ocs.DEFAULT_A
    r_live = td.fetch_risk_free_rate(actual_T)
    r_use = r_live if r_live is not None else ocs.RISK_FREE_RATE
    forward = ocs.compute_forward_price(spot, r_use, dividend_yield, actual_T)
    df = ocs.build_chain_dataframe(td, ticker, expiration)
    return df, forward, actual_T, spot


def _run_svi_smile_chain_scanner(
    td: Any, ticker: str, expiration: str
) -> dict[str, Any]:
    df, forward, actual_T, spot = _svi_chain_scanner_inputs(td, ticker, expiration)
    _fitted_df, smile_a, smile_b, svi_params = ocs.fit_svi_smile(
        df, forward, actual_T, spot=spot
    )
    return {
        "ticker": ticker,
        "expiry": expiration,
        "spot": spot,
        "forward": forward,
        "T_years": actual_T,
        "smile_a": smile_a,
        "smile_b": smile_b,
        "svi_params": svi_params,
    }


def _svi_exposure_overlay_inputs(td: Any, ticker: str, expiration: str):
    """Same chain_iv/oi_by construction expiry_book_production.py's
    fetch_production_result runs right before its own svi_rp_overlay call
    -- reuses normalize_snapshot_rows/_merge_greeks_oi (the put-call-parity
    ITM-leg-coverage fallback) rather than re-deriving that logic here."""
    spot = float(td.fetch_spot_price(ticker))
    if spot <= 0:
        raise ValueError(f"Could not fetch spot for {ticker}")
    raw_rows = td.option_bulk_greeks(ticker, expiration)
    oi_rows = td.option_bulk_oi(ticker, expiration)
    try:
        q = float(td.fetch_dividend_yield(ticker))
    except Exception:  # noqa: BLE001 -- q fallback to 0 mirrors expiry_book_production.py:265-269
        q = 0.0
    rows = ebp.normalize_snapshot_rows(ebp._merge_greeks_oi(raw_rows, oi_rows))
    chain_iv = {(r["strike"], r["right"]): r["implied_vol"] for r in rows}
    oi_by = {(r["strike"], r["right"]): int(r["oi"]) for r in rows}
    dte = ebp._dte_of(expiration)
    t = dte / ebe.DEFAULT_A
    return chain_iv, oi_by, spot, t, q


def _run_svi_smile_exposure_overlay(
    td: Any, ticker: str, expiration: str
) -> dict[str, Any]:
    chain_iv, oi_by, spot, t, q = _svi_exposure_overlay_inputs(td, ticker, expiration)
    overlay = ebe.svi_rp_overlay(
        chain_iv, spot, t, oi_by=oi_by, ticker=ticker, r=ebe.RISK_FREE_RATE, q=q
    )
    return {
        "ticker": overlay.ticker,
        "expiry": expiration,
        "spot": spot,
        "T_years": t,
        "sigma_atm": overlay.sigma_atm,
        "cheap_strikes": overlay.cheap_strikes,
        "rich_strikes": overlay.rich_strikes,
        "net_cheap_oi": overlay.net_cheap_oi,
        "net_rich_oi": overlay.net_rich_oi,
        "term_structure_flag": overlay.term_structure_flag,
        "butterfly_clamped": overlay.butterfly_clamped,
    }


def _run_svi_smile_model_comparison(
    context: dict[str, Any], td: Any, ticker: str, expiration: str | None
) -> dict[str, Any]:
    return smile_by_model.build_iv_smile_by_model(
        ticker,
        td=td,
        expiry=expiration,
        strike=context.get("strike"),
        option_type=str(context.get("option_type") or "call"),
        include_mc=bool(context.get("include_mc", True)),
        include_heston=bool(context.get("include_heston", True)),
    )


def _run_svi_smile(context: dict[str, Any], *, td: Any = None) -> ModuleResult:
    """Dispatches on context['mode'] (default 'chain_scanner') across the
    three call sites documented above. Fail-loud: an unknown mode, a
    missing ticker, or a genuine fetch/fit failure all become
    status='failed' with real error text, never a fake 'ok'."""
    mode = str(context.get("mode") or "chain_scanner").strip().lower()
    if mode not in SVI_SMILE_MODES:
        return ModuleResult(
            status="failed",
            artifacts=[],
            metrics={
                "error": (
                    f"unknown svi_smile mode {mode!r}, expected one of "
                    f"{SVI_SMILE_MODES}"
                ),
                "error_type": "ValueError",
            },
            context_patch=None,
        )

    try:
        ticker = _resolve_ticker(context)
        expiry = _resolve_expiry(context)
        target_years = float(context.get("target_years") or 0.25)

        owns_td = td is None
        if owns_td:
            from thetadata_client import ThetaDataController

            td = ThetaDataController()
        try:
            if mode == "model_comparison":
                expiration = None if expiry == "auto" else expiry
                metrics = _run_svi_smile_model_comparison(
                    context, td, ticker, expiration
                )
            else:
                expiration = (
                    expiry
                    if expiry != "auto"
                    else expiry_selector.resolve_expiration(
                        td, ticker, None, target_years
                    )[0]
                )
                if mode == "chain_scanner":
                    metrics = _run_svi_smile_chain_scanner(td, ticker, expiration)
                else:
                    metrics = _run_svi_smile_exposure_overlay(td, ticker, expiration)
        finally:
            if owns_td:
                td.close()
    except Exception as exc:  # noqa: BLE001 -- fail-loud, matches dealer_exposure
        return _failed(exc)

    metrics["mode"] = mode
    return ModuleResult(status="ok", artifacts=[], metrics=metrics, context_patch=None)


# ---------------------------------------------------------------------------
# surface_greek / surface_market_iv / surface_flow_strike_time /
# surface_flow_strike_expiry
# ---------------------------------------------------------------------------
#
# Task 5's four modules: thin ModuleSpec adapters over surface_grids.py's
# four strike x expiry / strike x time grid builders (build_greek_surface,
# build_market_iv_surface, build_flow_strike_time, build_flow_strike_expiry
# -- see that module's docstring). No suite-internal math changes here, and
# no new PNG rendering: Tools/tools/surface_explorer_tool.py already renders
# (and best-effort tolerates a plotting failure for) the exact same grid via
# its own matplotlib helpers, and this task's compatibility shim there keeps
# that behavior by calling THESE modules and rendering from their
# `context_patch` rather than duplicating rendering here. The full grid dict
# each builder returns (strikes/expiries/grid/skipped/meta, etc.) is the real
# output -- threaded via `context_patch` under a `<slug>_result` key (same
# convention as `dealer_exposure_result`/`dual_book_result` above) so a
# caller that needs the whole grid, not just the summary `metrics`, can get
# it without a second builder call. `metrics` itself only carries the small
# scalar/summary fields each result dict already has -- no invented fields.


def _run_surface_greek(context: dict[str, Any], *, td: Any = None) -> ModuleResult:
    """Thin adapter over surface_grids.build_greek_surface. `context['greek']`
    defaults to 'gamma', matching surface_explorer_tool.py's DEFAULT_GREEK."""
    try:
        ticker = _resolve_ticker(context)
        greek = str(context.get("greek") or "gamma").strip().lower()
        max_expiries = int(context.get("max_expiries") or 12)
        result = surface_grids.build_greek_surface(
            ticker, greek, td=td, max_expiries=max_expiries
        )
    except Exception as exc:  # noqa: BLE001 -- fail-loud, matches dealer_exposure
        return _failed(exc)

    metrics: dict[str, Any] = {
        "ticker": result["ticker"],
        "greek": result["greek"],
        "spot": result["spot"],
        "n_expiries_used": result["meta"]["n_expiries_used"],
        "units": result["units"],
    }
    return ModuleResult(
        status="ok",
        artifacts=[],
        metrics=metrics,
        context_patch={"surface_greek_result": result},
    )


def _run_surface_market_iv(context: dict[str, Any], *, td: Any = None) -> ModuleResult:
    """Thin adapter over surface_grids.build_market_iv_surface.
    `context['min_dte']` is forwarded (default 0, per the builder's own
    default)."""
    try:
        ticker = _resolve_ticker(context)
        min_dte_raw = context.get("min_dte")
        min_dte = int(min_dte_raw) if min_dte_raw not in (None, "") else 0
        result = surface_grids.build_market_iv_surface(ticker, td=td, min_dte=min_dte)
    except Exception as exc:  # noqa: BLE001 -- fail-loud, matches dealer_exposure
        return _failed(exc)

    metrics: dict[str, Any] = {
        "ticker": result["ticker"],
        "spot": result["spot"],
        "n_strikes": len(result["strikes"]),
        "n_tenors": len(result["tenors_years"]),
        "source": result["meta"]["source"],
    }
    return ModuleResult(
        status="ok",
        artifacts=[],
        metrics=metrics,
        context_patch={"surface_market_iv_result": result},
    )


def _run_surface_flow_strike_time(
    context: dict[str, Any], *, td: Any = None
) -> ModuleResult:
    """Thin adapter over surface_grids.build_flow_strike_time.
    `context['session']` is forwarded (default None -> today, per the
    builder's own default)."""
    try:
        ticker = _resolve_ticker(context)
        session = context.get("session")
        result = surface_grids.build_flow_strike_time(ticker, td=td, session=session)
    except Exception as exc:  # noqa: BLE001 -- fail-loud, matches dealer_exposure
        return _failed(exc)

    metrics: dict[str, Any] = {
        "ticker": result["ticker"],
        "spot": result["spot"],
        "session": result["session"],
        "n_trades_used": result["meta"]["n_trades_used"],
        "n_trades_total": result["meta"]["n_trades_total"],
    }
    return ModuleResult(
        status="ok",
        artifacts=[],
        metrics=metrics,
        context_patch={"surface_flow_strike_time_result": result},
    )


def _run_surface_flow_strike_expiry(
    context: dict[str, Any], *, td: Any = None
) -> ModuleResult:
    """Thin adapter over surface_grids.build_flow_strike_expiry.
    `context['session']`/`max_expiries`/`min_dte`/`max_dte` are all
    forwarded (builder's own defaults apply when absent)."""
    try:
        ticker = _resolve_ticker(context)
        session = context.get("session")
        max_expiries = int(context.get("max_expiries") or 12)
        min_dte_raw = context.get("min_dte")
        min_dte = int(min_dte_raw) if min_dte_raw not in (None, "") else 0
        max_dte_raw = context.get("max_dte")
        max_dte = int(max_dte_raw) if max_dte_raw not in (None, "") else 60
        result = surface_grids.build_flow_strike_expiry(
            ticker,
            td=td,
            session=session,
            max_expiries=max_expiries,
            min_dte=min_dte,
            max_dte=max_dte,
        )
    except Exception as exc:  # noqa: BLE001 -- fail-loud, matches dealer_exposure
        return _failed(exc)

    metrics: dict[str, Any] = {
        "ticker": result["ticker"],
        "spot": result["spot"],
        "session": result["session"],
        "n_expiries_used": len(result["expiries"]),
        "n_trades_total": result["meta"]["n_trades_total"],
    }
    return ModuleResult(
        status="ok",
        artifacts=[],
        metrics=metrics,
        context_patch={"surface_flow_strike_expiry_result": result},
    )


def _selection_only_marker(slug: str) -> Any:
    """Builds a `run()` for a Task 6 selection-only marker ModuleSpec.

    These four slugs (group_screener, vol_surface_2d, vrp_term_structure,
    sentiment_backtest) are gated steps INSIDE volatility_suite.py's
    `_run_core_analysis` -- inline `if run_x: ...` blocks sharing local
    state across steps within one function call (most notably: the
    options-chain-scanner step deliberately reuses the dealer-positioning
    step's already-computed result from earlier in the SAME
    `_run_core_analysis` call, to avoid a documented "two-vanna" bug -- see
    `_run_core_analysis`'s options-chain-scanner step comment). They are not
    independently callable the way `chain_scanner`/`svi_smile`/the
    dealer-book cluster/the surface modules are: there is no
    `_run_core_analysis`-bypassing implementation to wrap.

    These entries exist purely so `--list-modules` / the dashboard checkbox
    UI can surface and select them (via `context["modules"]` membership,
    resolved by `volatility_suite.run_context_mode` ->
    `_resolve_core_analysis_flags`) -- NOT so `run_selected_modules` or any
    other direct `.run()` caller can execute them standalone. Calling
    `.run()` directly always raises NotImplementedError explaining this,
    rather than silently no-op'ing or (worse) attempting an independent
    fetch that could reintroduce the two-vanna bug.
    """

    def _run(context: dict[str, Any], *, td: Any = None) -> ModuleResult:
        raise NotImplementedError(
            f"{slug} only runs as part of Vol_Suite's core context-mode "
            "pipeline (_run_core_analysis, one of its five gated steps). "
            "Select it via context['modules'] on a vol_suite context-mode "
            "run (run_context_mode), not as a standalone module invocation "
            "-- it has no independent, _run_core_analysis-bypassing "
            "implementation to run here."
        )

    return _run


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
    ModuleSpec(
        name="Chain Scanner",
        slug="chain_scanner",
        suite="vol_suite",
        category="scanner",
        run=_run_chain_scanner,
        # options_chain_scanner.py has a real __main__ block (see main()
        # above _run_chain_scanner in that file), but it's a fully
        # interactive prompt-driven flow (input("Enter ticker: ")) with no
        # headless/--context entry point of its own -- not a clean existing
        # standalone-CLI target, and Task 4's brief says not to build a new
        # CLI file unless one is missing and trivial to add, which this
        # isn't (it would need the same headless-argument plumbing Task 3's
        # two new CLI files added from scratch).
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
    ),
    ModuleSpec(
        name="SVI Smile",
        slug="svi_smile",
        suite="vol_suite",
        category="smile",
        run=_run_svi_smile,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
    ),
    ModuleSpec(
        name="Surface Greek",
        slug="surface_greek",
        suite="vol_suite",
        category="surface",
        run=_run_surface_greek,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
    ),
    ModuleSpec(
        name="Surface Market IV",
        slug="surface_market_iv",
        suite="vol_suite",
        category="surface",
        run=_run_surface_market_iv,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
    ),
    ModuleSpec(
        name="Surface Flow (Strike x Time)",
        slug="surface_flow_strike_time",
        suite="vol_suite",
        category="surface",
        run=_run_surface_flow_strike_time,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
    ),
    ModuleSpec(
        name="Surface Flow (Strike x Expiry)",
        slug="surface_flow_strike_expiry",
        suite="vol_suite",
        category="surface",
        run=_run_surface_flow_strike_expiry,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
    ),
    # Task 6 (Modularization Overhaul, Phase 3): selection-only markers for
    # four of _run_core_analysis's five gated pipeline steps (chain_scanner,
    # the fifth, already exists above as a real independently-runnable
    # module from Task 4 -- not duplicated here). See
    # _selection_only_marker's docstring: calling .run() on any of these
    # four always raises NotImplementedError; their real execution only
    # happens inside volatility_suite.run_context_mode's
    # _run_core_analysis call, selected via context["modules"] membership.
    ModuleSpec(
        name="Group Screener (selection-only)",
        slug="group_screener",
        suite="vol_suite",
        category="pipeline_step",
        run=_selection_only_marker("group_screener"),
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="global"),
    ),
    ModuleSpec(
        name="Vol Surface 2D (selection-only)",
        slug="vol_surface_2d",
        suite="vol_suite",
        category="pipeline_step",
        run=_selection_only_marker("vol_surface_2d"),
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
    ),
    ModuleSpec(
        name="VRP Term Structure (selection-only)",
        slug="vrp_term_structure",
        suite="vol_suite",
        category="pipeline_step",
        run=_selection_only_marker("vrp_term_structure"),
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
    ),
    ModuleSpec(
        name="Sentiment Backtest (selection-only)",
        slug="sentiment_backtest",
        suite="vol_suite",
        category="pipeline_step",
        run=_selection_only_marker("sentiment_backtest"),
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
    ),
]
