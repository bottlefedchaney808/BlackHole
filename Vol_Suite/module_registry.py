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

from shared.module_registry import (
    ArchiveHint,
    ArtifactRef,
    InputSpec,
    ModuleResult,
    ModuleSpec,
    ParamSpec,
)


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


def _as_bool(value: Any, *, default: bool = False) -> bool:
    """Coerce a param that arrived over JSON or as a query string.

    A checkbox reaches a module as a real bool from the dashboard but as the
    string "false"/"0"/"off" from a hand-rolled curl or a saved layout, and
    `bool("false")` is True -- so the strings are handled explicitly.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes", "on")


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
                        "spot": result.spot,
                    },
                    fh,
                    indent=2,
                )
            artifacts.append(ArtifactRef(path=path, kind="json"))
        except OSError:
            # Report-artifact write is best-effort; metrics below still
            # carry the real accumulated result either way.
            pass

        # Phase 7: add the 4-panel + heatmap charts (new for dealer-book tab Side B)
        try:
            from dealer_positioning import (
                plot_position_book,
                plot_position_book_heatmap,
            )

            chart_files = [
                plot_position_book(result, output_dir=output_dir),
                plot_position_book_heatmap(result, output_dir=output_dir),
            ]
            for f in chart_files:
                artifacts.append(ArtifactRef(path=f, kind="png"))
        except Exception:
            # charting best-effort; scalar result + json still valid
            pass

    metrics: dict[str, Any] = {
        "units": _POSITION_BOOK_UNITS,
        "ticker": ticker,
        "total_net": result.total_net,
        "spot": result.spot,
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
    td: Any, ticker: str, expiration: str, capture: dict | None = None
) -> dict[str, Any]:
    df, forward, actual_T, spot = _svi_chain_scanner_inputs(td, ticker, expiration)
    fitted_df, smile_a, smile_b, svi_params = ocs.fit_svi_smile(
        df, forward, actual_T, spot=spot
    )
    if capture is not None:
        # The fitted frame was being dropped on the floor here, which is why
        # the SVI card could only ever show scalars. Out-of-band for the same
        # reason as jump_diffusion's: per-strike arrays do not belong in the
        # metrics payload every caller serializes. Only the OTM wing is fitted
        # (fit_svi_smile masks is_otm & iv>0), so plot exactly that.
        try:
            fit_rows = fitted_df[fitted_df["is_otm"] & fitted_df["fit_iv"].notna()]
            capture["strikes"] = [float(v) for v in fit_rows["strike"]]
            capture["market_ivs"] = [float(v) for v in fit_rows["iv"]]
            capture["fitted_ivs"] = [float(v) for v in fit_rows["fit_iv"]]
            capture["spot"] = float(spot)
        except Exception:  # noqa: BLE001 -- a chart must never fail the fit
            capture.clear()
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

    smile: dict[str, Any] = {}
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
                    metrics = _run_svi_smile_chain_scanner(
                        td, ticker, expiration, capture=smile
                    )
                else:
                    metrics = _run_svi_smile_exposure_overlay(td, ticker, expiration)
        finally:
            if owns_td:
                td.close()
    except Exception as exc:  # noqa: BLE001 -- fail-loud, matches dealer_exposure
        return _failed(exc)

    metrics["mode"] = mode
    artifacts: list[ArtifactRef] = []
    if smile:
        chart = _plot_smile_fit(
            ticker,
            str(metrics.get("expiry") or ""),
            smile.get("strikes") or [],
            smile.get("market_ivs") or [],
            smile.get("fitted_ivs") or [],
            smile.get("spot"),
            "SVI",
            str(context.get("output_dir") or "."),
        )
        if chart:
            artifacts.append(ArtifactRef(path=chart, kind="png"))
    return ModuleResult(
        status="ok", artifacts=artifacts, metrics=metrics, context_patch=None
    )


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


# Substrings that mark a "the market was quiet" condition rather than a
# broken module. `surface_grids` raises ValueError for both cases -- a missing
# controller method (a real bug) and an empty session (a Sunday, a holiday, a
# thin name before the open) -- and a card that paints both red teaches you to
# ignore the colour. A data condition is `skipped` with the reason on it.
_NO_DATA_MARKERS = (
    "no trades",
    "no trades with usable",
    "no trades matched",
)


def _skipped_if_no_data(exc: Exception, ticker: str) -> ModuleResult | None:
    """`skipped` for an empty session, None to let the caller fail loudly."""
    message = str(exc)
    if not any(marker in message.lower() for marker in _NO_DATA_MARKERS):
        return None
    return ModuleResult(
        status="skipped",
        artifacts=[],
        metrics={
            "ticker": ticker,
            "message": message,
            "why": (
                "no options flow recorded for this session -- a weekend, a "
                "holiday, before the open, or a name that simply did not "
                "trade. Not a module failure."
            ),
        },
        context_patch=None,
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
        quiet = _skipped_if_no_data(exc, str(context.get("ticker") or ""))
        return quiet if quiet is not None else _failed(exc)

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
        quiet = _skipped_if_no_data(exc, str(context.get("ticker") or ""))
        return quiet if quiet is not None else _failed(exc)

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


# ---------------------------------------------------------------------------
# variance_swap
# ---------------------------------------------------------------------------
#
# The variance-swap replication pricer (Carr-Madan / Demeterfi) is the piece
# Vol_Suite is arguably built around -- fair variance strike, ATM IV, the
# convexity premium between them, and VRP against realized vol -- and it
# already had a clean programmatic entry point in `variance_swap_live.
# run_variance_swap_live`. It was simply never registered, so it did not
# appear in the catalog and could not be run as a widget at all; the only
# `vrp_term_structure` entry nearby is a selection-only marker that raises.
#
# This is a thin adapter, same pattern as the dealer-book cluster: no
# suite-internal math changes.


def _run_variance_swap(context: dict[str, Any]) -> ModuleResult:
    """Price a variance swap on the focus ticker at the resolved expiry."""
    try:
        import variance_swap_live as vsl

        ticker = _resolve_ticker(context)
        expiry = _resolve_expiry(context)
        expiration = None if expiry in ("", "auto") else expiry.replace("-", "")
        target_years = float(context.get("target_years") or 0.25)
        output_dir = str(context.get("output_dir") or ".")

        files, interp, result = vsl.run_variance_swap_live(
            ticker,
            target_years=target_years,
            output_dir=output_dir,
            expiration=expiration,
        )
    except Exception as exc:  # noqa: BLE001 -- fail-loud, matches dealer_exposure
        return _failed(exc)

    result = result or {}
    fair_vol = result.get("fair_variance_swap_strike_vol_pct")
    atm_iv = result.get("atm_implied_vol_pct")
    convexity = result.get("convexity_premium_vol_pct")
    # `strike_table` holds numpy arrays per strike -- fine for the plotting
    # path that produced it, but it is neither JSON-serializable nor useful
    # on a card, so it stays out of metrics. Everything scalar goes on.
    metrics: dict[str, Any] = {
        "headline": (
            f"{ticker} fair vol {fair_vol:.2f}%"
            if fair_vol is not None
            else f"{ticker} fair vol --"
        )
        + (f" vs ATM IV {atm_iv:.2f}%" if atm_iv is not None else "")
        + (f" · convexity {convexity:+.2f} pts" if convexity is not None else ""),
        "ticker": ticker,
        "interp": interp,
    }
    for key, value in result.items():
        if key == "strike_table":
            continue
        metrics[key] = value

    # Optional companion jump fit on the SAME chain. This does not alter the
    # replication fair strike above -- that stays pure Carr-Madan/Demeterfi
    # over market prices. It sits beside it so you can see how much of the
    # fair variance a jump model attributes to jumps rather than diffusion,
    # and how well the model actually fits the smile it was read off.
    jump_model = str(context.get("jump_model") or "none").strip()
    if jump_model.lower() not in ("", "none"):
        try:
            chain, spot = _jump_chain(ticker, expiration or "", target_years)
            model_cls, calib = _calibrate_named_model(
                jump_model, chain, spot, target_years
            )
            jump_block: dict[str, Any] = {
                "model_name": calib.model_name,
                "params": calib.params,
                # rmse_iv = calib_mask; rmse_iv_full = all valid strikes.
                "rmse_iv": calib.rmse_iv,
                "rmse_iv_full": calib.rmse_iv_full,
            }
            if calib.model_name == _JUMP_SHARE_MODEL:
                fitted = model_cls.from_array(
                    [calib.params[p] for p in model_cls.param_names]
                )
                share = fitted.jump_variance_share(target_years)
                jump_block["jump_variance_share"] = share
                if fair_vol is not None:
                    # Split the replication fair VARIANCE (not vol) by the
                    # model's jump share, then re-express each leg as a vol.
                    fair_var = (fair_vol / 100.0) ** 2
                    jump_block["jump_leg_vol_pct"] = 100.0 * (fair_var * share) ** 0.5
                    jump_block["diffusive_leg_vol_pct"] = (
                        100.0 * (fair_var * (1.0 - share)) ** 0.5
                    )
            metrics["jump_fit"] = jump_block
            if "jump_variance_share" in jump_block:
                metrics["headline"] += (
                    f" · {jump_block['jump_variance_share']:.0%} jump"
                )
        except Exception as exc:  # noqa: BLE001
            # A failed companion fit must not lose you the variance swap the
            # module was actually asked for.
            metrics["jump_fit"] = {
                "status": "error",
                "model": jump_model,
                "error": f"{type(exc).__name__}: {exc}",
            }

    artifacts = [
        ArtifactRef(path=f, kind="png" if str(f).endswith(".png") else "csv")
        for f in (files or [])
    ]
    return ModuleResult(
        status="ok",
        artifacts=artifacts,
        metrics=metrics,
        context_patch={"variance_swap_result": metrics},
    )


# ---------------------------------------------------------------------------
# jump_diffusion / jump_model_comparison / garch
# ---------------------------------------------------------------------------
#
# Three more pieces that existed, were tested, and had clean programmatic
# entry points, but were never registered -- so they had no catalog entry and
# could not be run as widgets. They only ever executed as inline steps of
# `volatility_suite._run_core_analysis`, which means reaching them required a
# full suite run.
#
# `jump_diffusion` reuses volatility_suite's own
# `_calibrate_default_jump_model` rather than reimplementing the calibration:
# that helper is deliberately self-contained (fetches its own spot / rate /
# dividend / chain) and already returns the dict shape suite_context.json's
# `jump_diffusion` key expects, including the `{status: error, error: ...}`
# failure form. Imported lazily inside run() -- volatility_suite is ~2900
# lines and importing it at registry-import time would slow every catalog
# read and every CLI listing.


# Models whose calibrated parameters expose a plain diffusive `sigma`, which
# is what garch_bridge.jump_filtered_returns needs as a measure-consistent
# threshold scale. Heston and Bates carry stochastic variance (v0/theta)
# rather than a constant sigma, so they cannot fill that role -- offering
# them here would produce a KeyError at run time instead of a choice.
_DIFFUSIVE_SIGMA_MODELS = ("Merton", "Kou", "VarianceGamma")

# Only Bates decomposes total variance into jump vs. diffusive legs
# (BatesModel.jump_variance_share), which is the input
# garch_bridge.adjust_garch_forecast scales the forecast by.
_JUMP_SHARE_MODEL = "Bates"


def _calibrate_named_model(name: str, chain: Any, spot: float, T: float):
    """Calibrate one model of the zoo by its display name."""
    from jump_diffusion.calibration import calibrate
    from jump_diffusion.models import ALL_MODELS

    model_cls = next((m for m in ALL_MODELS if m.name == name), None)
    if model_cls is None:
        raise ValueError(
            f"unknown jump model {name!r}; known: "
            + ", ".join(m.name for m in ALL_MODELS)
        )
    return model_cls, calibrate(model_cls, chain, spot, T)


def _jump_chain(ticker: str, expiration: str, target_years: float):
    """Fetch (chain, spot, T) for the jump-model calibrators.

    Same fetch volatility_suite._calibrate_default_jump_model does; factored
    out here because the comparison module needs the chain itself, not the
    calibrated result.
    """
    from thetadata_client import ThetaDataController
    from variance_swap_live import fetch_chain_thetadata

    td = ThetaDataController()
    try:
        spot = float(td.fetch_spot_price(ticker))
        r = float(td.fetch_risk_free_rate(target_years))
        q = float(td.fetch_dividend_yield(ticker, spot))
        chain = fetch_chain_thetadata(td, ticker, expiration, r, q)
    finally:
        td.close()
    return chain, spot


def _resolved_expiration(
    context: dict[str, Any], ticker: str, target_years: float
) -> str:
    """context expiry -> a concrete YYYYMMDD, resolving 'auto' the way the
    rest of the suite does (one shared expiry per run)."""
    expiry = _resolve_expiry(context)
    if expiry and expiry != "auto":
        return expiry.replace("-", "")
    from thetadata_client import ThetaDataController

    td = ThetaDataController()
    try:
        return expiry_selector.resolve_expiration(td, ticker, None, target_years)[0]
    finally:
        td.close()


def _plot_smile_fit(
    ticker: str,
    expiry: str,
    strikes,
    market_ivs,
    fitted_ivs,
    spot,
    label: str,
    output_dir: str,
    rmse=None,
) -> str | None:
    """Draw model-vs-market IV and return the PNG path (None if it can't).

    A calibration card that shows only RMSE asks you to trust one number.
    The smile is the diagnostic that actually tells you WHERE the fit is
    wrong -- a model can post a tidy RMSE and still miss the whole put wing,
    which is the half that matters for a hedge. Market points are scattered,
    the fit is a line, and spot is marked so the wings are readable.

    Never raises: a chart is a nice-to-have, and a plotting failure must not
    turn a good calibration into a failed module run.
    """
    try:
        import os
        from datetime import datetime

        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        k = np.asarray(strikes, dtype=float)
        mkt = np.asarray(market_ivs, dtype=float)
        fit = np.asarray(fitted_ivs, dtype=float)
        if k.size == 0 or k.size != mkt.size or k.size != fit.size:
            return None

        # Drop legs the vendor could not solve. `calibration.calibrate` masks
        # on ~isnan only, so strikes where ThetaData reports implied_vol=0 --
        # deep-ITM legs with almost no extrinsic value, the same rows
        # expiry_book_production.normalize_snapshot_rows had to repair --
        # survive into market_ivs as a literal 0.0. Plotted raw they draw a
        # flat shelf of zeros along the bottom (confirmed live on SPY: ~490
        # points from strike 150 to 640) that compresses the real smile into
        # the top of the axis. The CALIBRATION is unaffected: it fits the
        # MAX_CALIB_STRIKES nearest spot, which are all genuinely solved.
        usable = np.isfinite(k) & np.isfinite(mkt) & np.isfinite(fit) & (mkt > 0)
        if not usable.any():
            return None
        k, mkt, fit = k[usable], mkt[usable], fit[usable]

        order = np.argsort(k)
        k, mkt, fit = k[order], mkt[order], fit[order]

        fig, ax = plt.subplots(figsize=(9, 5))
        ax.scatter(k, mkt * 100.0, s=34, zorder=3, label="Market IV", color="#2b6cb0")
        ax.plot(k, fit * 100.0, lw=2, zorder=2, label=f"{label} fit", color="#c05621")
        try:
            if spot and float(spot) > 0:
                ax.axvline(
                    float(spot),
                    ls="--",
                    lw=1,
                    color="#718096",
                    label=f"spot {float(spot):.2f}",
                )
        except (TypeError, ValueError):
            pass

        title = f"{ticker} {expiry} - {label} fit vs market smile"
        if isinstance(rmse, (int, float)):
            # Scoped to the calibration mask (MAX_CALIB_STRIKES), not the
            # full smile -- say so rather than implying whole-curve accuracy.
            title += f"  (RMSE on calibrated strikes {rmse:.4f})"
        ax.set_title(title, fontsize=11)
        ax.set_xlabel("Strike")
        ax.set_ylabel("Implied vol (%)")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=9)
        fig.tight_layout()

        os.makedirs(output_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe = str(label).replace(" ", "_")
        path = os.path.join(output_dir, f"{ticker}_{safe}_smile_fit_{ts}.png")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return path
    except Exception:
        return None


def _run_jump_diffusion(context: dict[str, Any]) -> ModuleResult:
    """Calibrate the default jump model (Bates unless JUMP_MODEL_DEFAULT says
    otherwise) plus the Merton sigma the GARCH jump-day filter wants."""
    try:
        ticker = _resolve_ticker(context)
        target_years = float(context.get("target_years") or 0.25)
        expiration = _resolved_expiration(context, ticker, target_years)

        from volatility_suite import (
            _calibrate_default_jump_model,
            resolve_jump_model_default,
        )

        choice = str(context.get("model") or "default").strip()
        saved_default = None
        if _as_bool(context.get("save_as_default")) and choice != "default":
            from shared.desk_settings import set_setting

            set_setting("jump_model_default", choice)
            saved_default = choice
        default_model = resolve_jump_model_default()

        # capture= asks for the smile arrays so the card can draw the fit;
        # they stay out of the returned dict (and so out of suite_context).
        smile: dict[str, Any] = {}
        result = _calibrate_default_jump_model(
            ticker,
            expiration,
            target_years,
            capture=smile,
            model_name=None if choice == "default" else choice,
        )
    except Exception as exc:  # noqa: BLE001 -- fail-loud, matches dealer_exposure
        return _failed(exc)

    result = result or {}
    # The helper's documented failure form is a dict, not an exception -- a
    # calibration that could not fit must not render as a green "ok" card.
    if result.get("status") == "error":
        return ModuleResult(
            status="failed",
            artifacts=[],
            metrics={"ticker": ticker, "expiry": expiration, **result},
            context_patch=None,
        )

    params = result.get("params") or {}
    rmse = result.get("rmse_iv")
    rmse_full = result.get("rmse_iv_full")
    n_atm = 15  # MAX_CALIB_STRIKES; headline must not imply full-smile RMSE
    metrics: dict[str, Any] = {
        "headline": (
            f"{ticker} {result.get('model_name', 'jump model')} calibrated"
            + (
                f" · RMSE(IV, {n_atm} ATM) {rmse:.4f}"
                if isinstance(rmse, (int, float))
                else ""
            )
            + (
                f" · jump share {result['jump_variance_share']:.1%}"
                if isinstance(result.get("jump_variance_share"), (int, float))
                else ""
            )
        ),
        "ticker": ticker,
        "expiry": expiration,
        "model_name": result.get("model_name"),
        "rmse_iv": rmse,
        "rmse_iv_full": rmse_full,
        "jump_variance_share": result.get("jump_variance_share"),
        "merton_sigma": result.get("merton_sigma"),
        "params": params,
        "model_choice": choice,
        "default_model": default_model,
    }
    if saved_default:
        metrics["saved_default"] = saved_default
    if result.get("zoo_rmse_iv"):
        metrics["headline"] += " · best of zoo"
        metrics["zoo_rmse_iv"] = result["zoo_rmse_iv"]
    artifacts: list[ArtifactRef] = []
    chart = _plot_smile_fit(
        ticker,
        expiration,
        smile.get("strikes") or [],
        smile.get("market_ivs") or [],
        smile.get("fitted_ivs") or [],
        smile.get("spot"),
        str(result.get("model_name") or "jump model"),
        str(context.get("output_dir") or "."),
        rmse=rmse,
    )
    if chart:
        artifacts.append(ArtifactRef(path=chart, kind="png"))

    return ModuleResult(
        status="ok",
        artifacts=artifacts,
        metrics=metrics,
        context_patch={"jump_diffusion": result},
    )


def _run_jump_model_comparison(context: dict[str, Any]) -> ModuleResult:
    """Calibrate every model in the zoo (VG / Heston / Bates / Kou / Merton)
    on one chain and rank them by RMSE in IV space."""
    try:
        ticker = _resolve_ticker(context)
        target_years = float(context.get("target_years") or 0.25)
        expiration = _resolved_expiration(context, ticker, target_years)
        chain, spot = _jump_chain(ticker, expiration, target_years)

        from jump_diffusion.comparison import run_comparison

        comparison = run_comparison(chain, spot, target_years)
    except Exception as exc:  # noqa: BLE001
        return _failed(exc)

    # run_comparison returns CalibrationResult objects plus numpy arrays under
    # jump_contribution; neither is JSON-shaped, so flatten to a rows table the
    # metrics renderer can show and drop the raw arrays.
    models = comparison.get("models") or {}
    rows = []
    for name, res in models.items():
        if res is None:
            rows.append({"model": name, "rmse_iv": None, "status": "failed"})
            continue
        rows.append(
            {
                "model": name,
                "rmse_iv": getattr(res, "rmse_iv", None),
                "status": "ok",
                **{k: v for k, v in (getattr(res, "params", {}) or {}).items()},
            }
        )
    rows.sort(key=lambda r: (r["rmse_iv"] is None, r["rmse_iv"]))
    best = comparison.get("best_fit")
    return ModuleResult(
        status="ok",
        artifacts=[],
        metrics={
            "headline": (
                f"{ticker} {expiration} · best fit {best}"
                if best
                else f"{ticker} {expiration} · no model converged"
            ),
            "ticker": ticker,
            "expiry": expiration,
            "best_fit": best,
            "models": rows,
        },
        context_patch={"jump_model_comparison": {"best_fit": best, "models": rows}},
    )


def _run_garch(context: dict[str, Any]) -> ModuleResult:
    """GARCH(1,1) conditional volatility for the focus ticker.

    The annualized conditional vol is what VaR's `_resolve_vol_and_quality`
    reads back out of the Context Store instead of falling back to a flat
    0.25 (see CLAUDE.md's Context-Store fragile surface), so it goes out on
    context_patch under the same `garch_vol` key that consumer expects.
    """
    try:
        ticker = _resolve_ticker(context)
        output_dir = str(context.get("output_dir") or ".")

        # Jump enhancement (jump_diffusion/garch_bridge.py), both legs
        # opt-in from the card:
        #   jump_filter        -> winsorize jump-attributable days out of the
        #                         return series BEFORE the fit, using a
        #                         model-implied diffusive sigma as the
        #                         threshold scale (a rolling-std threshold is
        #                         contaminated by the jumps it is detecting).
        #   jump_adjust_forecast -> scale the resulting conditional-vol
        #                         forecast by Bates' option-implied
        #                         jump-variance share.
        # Anything already threaded in by a prior jump_diffusion run in the
        # same context wins over a fresh calibration -- that is the whole
        # point of the Context Store.
        prior = context.get("jump_diffusion") or {}
        merton_sigma = prior.get("merton_sigma")
        jump_share = prior.get("jump_variance_share")
        provenance = "context" if (merton_sigma or jump_share) else None

        want_filter = _as_bool(context.get("jump_filter"), default=False)
        want_adjust = _as_bool(context.get("jump_adjust_forecast"), default=False)
        filter_model = str(context.get("jump_filter_model") or "Merton").strip()

        if (want_filter and merton_sigma is None) or (
            want_adjust and jump_share is None
        ):
            target_years = float(context.get("target_years") or 0.25)
            expiration = _resolved_expiration(context, ticker, target_years)
            chain, spot = _jump_chain(ticker, expiration, target_years)
            provenance = "calibrated"
            if want_filter and merton_sigma is None:
                if filter_model not in _DIFFUSIVE_SIGMA_MODELS:
                    raise ValueError(
                        f"{filter_model} has no constant diffusive sigma; "
                        f"choose one of {', '.join(_DIFFUSIVE_SIGMA_MODELS)}"
                    )
                _, calib = _calibrate_named_model(
                    filter_model, chain, spot, target_years
                )
                merton_sigma = calib.params["sigma"]
            if want_adjust and jump_share is None:
                bates_cls, bates_calib = _calibrate_named_model(
                    _JUMP_SHARE_MODEL, chain, spot, target_years
                )
                fitted = bates_cls.from_array(
                    [bates_calib.params[p] for p in bates_cls.param_names]
                )
                jump_share = fitted.jump_variance_share(target_years)

        from garch_analysis import run_garch_module

        # Keep the GarchModuleResult itself: it is a tuple SUBCLASS carrying
        # an out-of-band `.error`, and destructuring straight into three names
        # throws that away -- which is the difference between "the fit raised"
        # and "the fit worked but produced no conditional-vol series", both of
        # which give cond_vol None.
        garch_result = run_garch_module(
            ticker,
            output_dir=output_dir,
            merton_sigma=merton_sigma if want_filter else None,
            jump_variance_share=jump_share if want_adjust else None,
        )
        files, interp, cond_vol = garch_result
    except Exception as exc:  # noqa: BLE001
        return _failed(exc)

    error = getattr(garch_result, "error", None)
    metrics: dict[str, Any] = {
        "ticker": ticker,
        "garch_cond_vol": cond_vol,
        "garch_cond_vol_pct": None if cond_vol is None else 100.0 * float(cond_vol),
        "jump_filter": want_filter,
        "jump_filter_model": filter_model if want_filter else None,
        "jump_filter_sigma": merton_sigma if want_filter else None,
        "jump_adjust_forecast": want_adjust,
        "jump_variance_share": jump_share if want_adjust else None,
        "jump_params_from": provenance,
        "interp": interp,
    }
    if error:
        metrics["error"] = str(error)
        return ModuleResult(
            status="failed", artifacts=[], metrics=metrics, context_patch=None
        )
    enhancements = []
    if want_filter:
        enhancements.append(f"{filter_model} jump-filtered")
    if want_adjust:
        enhancements.append("jump-adjusted")
    metrics["headline"] = (
        f"{ticker} GARCH(1,1) conditional vol "
        + (f"{100.0 * float(cond_vol):.2f}%" if cond_vol is not None else "unavailable")
        + (f" · {' + '.join(enhancements)}" if enhancements else " · plain")
    )
    return ModuleResult(
        status="ok" if cond_vol is not None else "skipped",
        artifacts=[ArtifactRef(path=f, kind="png") for f in (files or [])],
        metrics=metrics,
        # Two keys, one number, deliberately. `garch_vol` is the name
        # Vol_Suite's own suite_context/orchestrator path has always used;
        # `garch_conditional_vol` is the name VaR actually reads back out of
        # the Context Store (VaR_Tools_Simulations/module_registry.py::
        # _resolve_vol -> store.get(scope, "garch_conditional_vol")). Writing
        # only the first meant every VaR widget silently fell through to the
        # flat 0.25 fallback no matter how many times GARCH had run on the
        # same scope -- the exact Context-Store threading regression
        # CLAUDE.md's fragile-surfaces section warns about.
        context_patch=(
            {"garch_vol": cond_vol, "garch_conditional_vol": cond_vol}
            if cond_vol is not None
            else None
        ),
    )


# ---------------------------------------------------------------------------
# correlation_matrix
# ---------------------------------------------------------------------------
#
# `correlation_engine.py` computes the correlation AND covariance matrices,
# per-name vols/betas, and basket-level vol/beta/Sharpe/diversification --
# and has had a clean programmatic entry point (`run_correlation_engine`)
# the whole time, which writes a heatmap PNG plus two CSVs. It was never
# registered, so the matrix had nowhere to live in the UI and, more
# importantly, nothing ever wrote a real correlation matrix into the Context
# Store.
#
# That second half is the point. VaR's corr_sim/mc_sim resolve their
# correlation input as `context['corr_matrix']` -> Context Store
# `correlation_matrix` -> **identity**, and their vols as
# `context['volatilities']` -> Context Store `garch_conditional_vol` ->
# **flat 0.25**. With nothing writing either key, every basket VaR on the
# desk was silently a zero-correlation simulation at a made-up 25% vol. This
# module is what fills them: run it on a basket and the VaR tools downstream
# stop falling back.


def _resolve_basket(context: dict[str, Any]) -> list[str]:
    """Every place a basket can arrive from on the desk, in priority order.

    The desk feeds a basket from three different places (the scope bar's
    chips, `Basket = my book`, and the highlight-pack scanner), so this
    accepts all of their key names rather than demanding one.
    """
    for key in ("basket", "tickers", "held_tickers"):
        raw = context.get(key)
        if isinstance(raw, str):
            raw = raw.split(",")
        if isinstance(raw, (list, tuple)) and raw:
            out = [str(t).strip().upper() for t in raw if str(t).strip()]
            if out:
                return list(dict.fromkeys(out))
    focus = context.get("ticker") or (context.get("focus") or {}).get("ticker")
    return [str(focus).strip().upper()] if focus else []


def _resolve_weights(context: dict[str, Any], tickers: list[str]) -> list[float] | None:
    """Market-value weights from the position book when it is in context.

    Equal-weighting a book you are not equally weighted in reports a basket
    vol you do not have. Falls back to None (equal weight, the engine's own
    default) when no book is present or none of it overlaps the basket.
    An unlabeled `weights` list is refused (raises) unless a book is also
    present to replace it -- silently equal-weighting after discarding a
    provided vector is a hidden swap.
    """
    explicit = context.get("weights")
    labels = context.get("weight_tickers") or context.get("correlation_tickers")
    unlabeled_explicit = False
    if isinstance(explicit, (list, tuple)) and explicit:
        # Refuse unlabeled lists — positional apply silently swaps names.
        if isinstance(labels, (list, tuple)) and len(labels) == len(explicit):
            index = {str(t).strip().upper(): i for i, t in enumerate(labels)}
            try:
                return [float(explicit[index[t]]) for t in tickers]
            except (KeyError, IndexError, TypeError, ValueError):
                unlabeled_explicit = True
        else:
            unlabeled_explicit = True
    book = context.get("positions")
    rows = book.get("positions") if isinstance(book, dict) else book
    if isinstance(rows, list) and rows:
        by_ticker: dict[str, float] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            tkr = str(row.get("ticker") or "").strip().upper()
            try:
                value = abs(float(row.get("market_value")))
            except (TypeError, ValueError):
                continue
            if tkr and value:
                by_ticker[tkr] = by_ticker.get(tkr, 0.0) + value
        weights = [by_ticker.get(t, 0.0) for t in tickers]
        if sum(weights) > 0:
            return weights
    if unlabeled_explicit:
        raise ValueError(
            "weights require weight_tickers or correlation_tickers; "
            "unlabeled lists are refused"
        )
    return None


def _run_correlation_matrix(context: dict[str, Any]) -> ModuleResult:
    """Correlation / covariance / basket stats over the scope's basket.

    A single name is not a basket, so that case fails loudly rather than
    returning a 1x1 matrix of 1.0.
    """
    try:
        import correlation_engine as ce

        tickers = _resolve_basket(context)
        if len(tickers) < 2:
            raise ValueError(
                "correlation_matrix needs at least 2 tickers; got "
                f"{tickers or 'none'}. Set a basket (the desk's 'Basket = my "
                "book' button fills it from your positions)."
            )
        market = str(context.get("market") or "SPY").strip().upper()
        period = str(context.get("period") or "2y").strip()
        output_dir = str(context.get("output_dir") or ".")

        stats = ce.compute_basket_stats(
            tickers,
            weights=_resolve_weights(context, tickers),
            market_ticker=market,
            period=period,
        )
        # Returns (files, interp, stats) -- its annotation still says a
        # 2-tuple, which is how this unpack silently broke the whole module.
        files, interp, _stats = ce.run_correlation_engine(
            tickers, market=market, period=period, output_dir=output_dir
        )
    except Exception as exc:  # noqa: BLE001 -- fail-loud, matches dealer_exposure
        return _failed(exc)

    used = list(stats.tickers)
    corr = [[float(v) for v in row] for row in stats.correlation_matrix]
    cov = [[float(v) for v in row] for row in stats.covariance_matrix]

    # The matrix as rows the card's table renderer can show directly: a bare
    # nested list renders as an unreadable JSON blob.
    corr_rows = [
        {"": name, **{used[j]: round(corr[i][j], 3) for j in range(len(used))}}
        for i, name in enumerate(used)
    ]
    top_pairs = sorted(
        (
            {
                "pair": f"{p.ticker1}/{p.ticker2}",
                "corr": round(float(p.correlation), 3),
                "cov": round(float(p.covariance), 6),
                "beta": round(float(p.beta), 3),
            }
            for p in (stats.correlation_pairs or [])
        ),
        key=lambda r: -abs(r["corr"]),
    )[:12]

    metrics: dict[str, Any] = {
        "headline": (
            f"{len(used)} names \u00b7 basket vol "
            f"{100.0 * float(stats.basket_vol):.1f}%"
            f" \u00b7 beta {float(stats.basket_beta):.2f}"
            f" \u00b7 diversification {float(stats.diversification_ratio):.2f}"
        ),
        "tickers": used,
        "basket_vol": float(stats.basket_vol),
        "basket_beta": float(stats.basket_beta),
        "basket_sharpe": float(stats.basket_sharpe),
        "dispersion_score": float(stats.dispersion_score),
        "diversification_ratio": float(stats.diversification_ratio),
        "dropped_tickers": list(stats.dropped_tickers or []),
        "correlation_rows": corr_rows,
        "top_pairs": top_pairs,
        "interp": interp,
    }

    artifacts = [
        ArtifactRef(path=f, kind="png" if str(f).endswith(".png") else "csv")
        for f in (files or [])
    ]
    return ModuleResult(
        status="ok",
        artifacts=artifacts,
        metrics=metrics,
        # These keys are exactly what VaR_Tools_Simulations/
        # module_registry.py::_resolve_corr and _resolve_vol look for. Keyed
        # by this run's basket scope, so a VaR run on the same basket picks
        # them up instead of an identity matrix at a flat 0.25.
        context_patch={
            "correlation_matrix": corr,
            "covariance_matrix": cov,
            "volatilities": [float(stats.individual_vols.get(t, 0.0)) for t in used],
            "correlation_tickers": used,
        },
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
        name="Expiry Exposure",
        slug="expiry_exposure",
        suite="vol_suite",
        category="exposure",
        run=_run_dealer_exposure,
        cli_entry="Vol_Suite/dealer_exposure_module.py",
        default_selected=False,
        requires=[],
        provides=("dealer_exposure_result",),
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
        requires=["expiry_exposure"],
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
        provides=("position_book_result",),
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
        provides=("dual_book_result",),
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
        provides=("chain_scanner_result",),
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
        # Computes the grid and never draws it -- the picture lives in
        # Tools/tools/surface_explorer_tool.py, which calls this same
        # function and adds the matplotlib render. As a standalone card
        # this is five tiles and no surface, so the picker offers the
        # tool instead; this stays registered as the data provider.
        superseded_by="surface-explorer",
        provides=("surface_greek_result",),
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
        # Computes the grid and never draws it -- the picture lives in
        # Tools/tools/surface_explorer_tool.py, which calls this same
        # function and adds the matplotlib render. As a standalone card
        # this is five tiles and no surface, so the picker offers the
        # tool instead; this stays registered as the data provider.
        superseded_by="surface-explorer",
        provides=("surface_market_iv_result",),
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
        # Computes the grid and never draws it -- the picture lives in
        # Tools/tools/surface_explorer_tool.py, which calls this same
        # function and adds the matplotlib render. As a standalone card
        # this is five tiles and no surface, so the picker offers the
        # tool instead; this stays registered as the data provider.
        superseded_by="surface-explorer",
        provides=("surface_flow_strike_time_result",),
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
        # Computes the grid and never draws it -- the picture lives in
        # Tools/tools/surface_explorer_tool.py, which calls this same
        # function and adds the matplotlib render. As a standalone card
        # this is five tiles and no surface, so the picker offers the
        # tool instead; this stays registered as the data provider.
        superseded_by="surface-explorer",
        provides=("surface_flow_strike_expiry_result",),
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
        runnable=False,  # run() raises; pipeline-selection marker only
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
        runnable=False,  # run() raises; pipeline-selection marker only
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
        runnable=False,  # run() raises; pipeline-selection marker only
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
        runnable=False,  # run() raises; pipeline-selection marker only
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
    ),
    ModuleSpec(
        name="Variance Swap",
        slug="variance_swap",
        suite="vol_suite",
        category="pricing",
        run=_run_variance_swap,
        cli_entry="Vol_Suite/variance_swap_live.py",
        default_selected=False,
        requires=[],
        provides=("variance_swap_result",),
        archive=ArchiveHint(key_shape="ticker_expiry"),
        description=(
            "Variance-swap replication (Carr-Madan/Demeterfi): fair variance "
            "strike, ATM IV, convexity premium and VRP vs realized vol."
        ),
        inputs=InputSpec(ticker="required", expiry="optional"),
        output_kind="metrics",
        sample={"ticker": "SPY"},
        params=(
            ParamSpec(
                name="jump_model",
                label="Enhance with jump model",
                kind="choice",
                default="none",
                choices=("none", "Merton", "Heston", "Bates", "Kou", "VarianceGamma"),
                help=(
                    "Fit this model to the same chain alongside the "
                    "replication. Bates additionally splits the fair variance "
                    "into jump and diffusive legs."
                ),
            ),
        ),
    ),
    ModuleSpec(
        name="Jump Diffusion",
        slug="jump_diffusion",
        suite="vol_suite",
        category="pricing",
        run=_run_jump_diffusion,
        cli_entry=None,
        default_selected=False,
        requires=[],
        provides=("jump_diffusion",),
        archive=ArchiveHint(key_shape="ticker_expiry"),
        description=(
            "Calibrate a jump model in IV space -- your saved default, a named "
            "model, or the best fit of the whole zoo; feeds dealer "
            "positioning, VRP and the strategy recommender."
        ),
        inputs=InputSpec(ticker="required", expiry="optional"),
        output_kind="metrics",
        sample={"ticker": "SPY"},
        params=(
            ParamSpec(
                name="model",
                label="Model",
                kind="choice",
                default="default",
                choices=(
                    "default",
                    "best_fit",
                    "Bates",
                    "Merton",
                    "Heston",
                    "Kou",
                    "VarianceGamma",
                ),
                help=(
                    "'default' uses your saved default (Bates until you change "
                    "it); 'best_fit' calibrates the whole zoo and keeps the "
                    "lowest IV RMSE."
                ),
            ),
            ParamSpec(
                name="save_as_default",
                label="Make this my default",
                kind="bool",
                default=False,
                help="Save the chosen model as the default for every future run.",
            ),
        ),
    ),
    ModuleSpec(
        name="Jump Model Comparison",
        slug="jump_model_comparison",
        suite="vol_suite",
        category="pricing",
        run=_run_jump_model_comparison,
        cli_entry=None,
        default_selected=False,
        requires=[],
        provides=("jump_model_comparison",),
        archive=ArchiveHint(key_shape="ticker_expiry"),
        description=(
            "Calibrate the whole model zoo (VG, Heston, Bates, Kou, Merton) "
            "on one chain and rank by RMSE in IV space."
        ),
        inputs=InputSpec(ticker="required", expiry="optional"),
        output_kind="metrics",
        sample={"ticker": "SPY"},
    ),
    ModuleSpec(
        name="GARCH(1,1)",
        slug="garch",
        suite="vol_suite",
        category="metrics",
        run=_run_garch,
        cli_entry="Vol_Suite/garch_analysis.py",
        default_selected=False,
        requires=[],
        provides=(
            "garch_vol",
            "garch_conditional_vol",
        ),
        archive=ArchiveHint(key_shape="ticker"),
        description=(
            "GARCH(1,1) conditional volatility; the vol VaR reads back from "
            "the Context Store instead of a flat 0.25 fallback."
        ),
        inputs=InputSpec(ticker="required"),
        output_kind="metrics",
        sample={"ticker": "SPY"},
        params=(
            ParamSpec(
                name="jump_filter",
                label="Filter jump days before fitting",
                kind="bool",
                default=False,
                help=(
                    "Winsorize jump-attributable days out of the return "
                    "series so the fit reflects diffusive clustering only."
                ),
            ),
            ParamSpec(
                name="jump_filter_model",
                label="Jump filter model",
                kind="choice",
                default="Merton",
                choices=_DIFFUSIVE_SIGMA_MODELS,
                help=(
                    "Supplies the diffusive sigma used as the jump-detection "
                    "threshold. Only models with a constant sigma qualify."
                ),
            ),
            ParamSpec(
                name="jump_adjust_forecast",
                label="Adjust forecast by jump share",
                kind="bool",
                default=False,
                help=(
                    "Scale the conditional-vol forecast by Bates' "
                    "option-implied jump-variance share."
                ),
            ),
        ),
    ),
    ModuleSpec(
        name="Correlation Matrix",
        slug="correlation_matrix",
        suite="vol_suite",
        category="metrics",
        run=_run_correlation_matrix,
        cli_entry="Vol_Suite/correlation_engine.py",
        default_selected=False,
        requires=[],
        provides=(
            "correlation_matrix",
            "covariance_matrix",
            "volatilities",
            "correlation_tickers",
        ),
        archive=ArchiveHint(key_shape="global"),
        description=(
            "Correlation + covariance matrix, per-name vol/beta and basket "
            "vol/beta/Sharpe/diversification over the scope basket. Writes "
            "correlation_matrix / covariance_matrix / volatilities to the "
            "Context Store, which is what VaR's corr_sim and mc_sim read "
            "instead of falling back to an identity matrix at a flat 0.25."
        ),
        inputs=InputSpec(ticker="optional", basket="required"),
        output_kind="metrics",
        sample={"basket": ["SPY", "QQQ", "IWM"]},
        params=(
            ParamSpec(
                name="period",
                label="History window",
                kind="choice",
                default="2y",
                choices=("6m", "1y", "2y", "5y"),
                help="Price history the returns are estimated over.",
            ),
            ParamSpec(
                name="market",
                label="Market proxy (beta)",
                kind="text",
                default="SPY",
                help="Benchmark each name's beta is measured against.",
            ),
        ),
    ),
]
