"""smile_by_model.py

Single-expiry implied-vol smile (IV vs strike) as solved independently by
every Options_Suite pricing model (CRR, Leisen-Reimer, Newton-Raphson,
SABR, VannaVolga, BAW, and optionally MC/Heston), plus the market's own
vendor IV smile -- built for the Surface Explorer tool's
`iv_smile_by_model` mode (Tools/tools/surface_explorer_tool.py).

This module does NOT reimplement any pricing/calibration/smile-building
logic. It is a thin, headless-safe orchestration layer over two things
that already exist in this suite:

  * main.py::_gather_all_models_and_market -- calibrates/prices every
    model at one focus strike K and assembles the `models` dict,
    `vol_manager`, and VannaVolga's rr25/bf25/atm_vol_vv inputs.
  * chain_evaluation.py::build_smile_comparison -- given those inputs,
    fetches the real market chain and returns each model's OWN
    chain-wide IV curve plus the market's vendor IV curve.

Unlike the other four Surface Explorer builders (Vol_Suite/surface_grids.py),
this one does not loop across multiple expiries -- it is a single-expiry,
multi-model comparison at one point in the surface.

Headless-safety note: main.py's get_safe_float() calls input(), which
blocks forever with no terminal attached (this is called from a FastAPI
dashboard worker). build_iv_smile_by_model monkeypatches
main.get_safe_float to `lambda prompt, default: default` for the
duration of the _gather_all_models_and_market call only (restored in a
finally block), which reproduces exactly what a human hitting Enter at
both the RR25/BF25 prompts would get -- main.py's own logic and model
ordering are not touched.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np

_THIS_DIR = Path(__file__).resolve().parent


def _ensure_on_syspath() -> None:
    """Options_Suite has the flat cwd-relative-import quirk documented in
    the repo root CLAUDE.md ("Known fragile surfaces") -- `from market_data
    import ...`-style imports inside main.py/vol_manager.py/
    chain_evaluation.py only resolve when Options_Suite/ itself is on
    sys.path, not just importable via a dotted package path."""
    p = str(_THIS_DIR)
    if p not in sys.path:
        sys.path.insert(0, p)


def _import_options_main():
    """Load Options_Suite/main.py under a unique module name.

    Options_Suite, VaR_Tools_Simulations and sentiment-scanner each ship
    their own ``main.py`` -- a bare ``import main`` would silently return
    whichever one first landed in ``sys.modules`` under that generic name
    in this (possibly long-lived) dashboard process, instead of raising.
    Same pattern as Tools/tools/hedge_optimizer_tool.py::_import_var_main.
    """
    module_name = "options_suite_main"
    if module_name in sys.modules:
        return sys.modules[module_name]
    _ensure_on_syspath()
    spec = importlib.util.spec_from_file_location(
        module_name, str(_THIS_DIR / "main.py")
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _ensure_stdio_can_print_unicode() -> None:
    """vol_manager.py/main.py print Unicode box-drawing characters and
    emoji (checkmarks, warning signs) in their reference-block diagnostics
    (see vol_manager.py::print_reference_block). Those prints are fine from
    an interactive terminal (normally UTF-8-capable), but a headless
    dashboard/uvicorn process on Windows inherits whatever codepage cmd.exe
    was in (commonly cp1252, since dashboard.bat never sets
    PYTHONIOENCODING) -- and that raises UnicodeEncodeError the first time
    _gather_all_models_and_market's own vol_manager.get_sigma() prints one
    of those characters, aborting the whole run before this function's own
    error handling ever sees it. Confirmed live: build_iv_smile_by_model
    called from dashboard/app.py's uvicorn process crashed with exactly
    this UnicodeEncodeError before this guard was added. Widening stdout/
    stderr to UTF-8 (replacing anything truly unencodable rather than
    raising) is a safe, local fix -- it only affects what this process can
    print, never any suite's pricing/calibration logic."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _import_chain_evaluation():
    _ensure_on_syspath()
    import chain_evaluation

    return chain_evaluation


def _import_vol_manager_cls():
    _ensure_on_syspath()
    from vol_manager import VolManager

    return VolManager


def _json_safe(value: Any) -> Any:
    """NaN/Inf are not JSON. Same guard vrp_term_structure_tool.py's
    _json_safe applies to its own output -- copied rather than imported
    across the Tools/Options_Suite boundary."""
    if isinstance(value, (np.floating,)):
        value = float(value)
    elif isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else value
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [_json_safe(v) for v in list(value)]
    return value


def _parse_yyyymmdd(exp: str) -> date:
    return datetime.strptime(exp, "%Y%m%d").date()


def build_iv_smile_by_model(
    ticker: str,
    td=None,
    expiry: str | None = None,
    strike: float | None = None,
    option_type: str = "call",
    include_mc: bool = True,
    include_heston: bool = True,
) -> dict:
    """Build the multi-model IV smile at ONE expiry.

    Fail-closed (raises ValueError with a specific reason) on: no usable
    ticker/spot, no listed expiries when `expiry` wasn't given, and a
    market chain fetch failure inside build_smile_comparison (which
    returns None on failure per its own docstring).

    include_mc / include_heston (default True, both): _gather_all_models_and_market
    does not itself take these flags (unlike chain_evaluation.run_full_chain,
    which does) -- changing its signature is out of scope for this tool (it
    is shared with main.py's own interactive choice=='9'/'10' paths). So
    when a flag is False, _gather_all_models_and_market is still called
    UNCHANGED (it still computes that model's calibration/price -- accept
    that cost when both flags are absent/True, the default), and the
    corresponding entry is stripped from the `models` dict AFTER the call,
    before it's passed into build_smile_comparison. This suppresses
    Heston's curve computation itself (chain_evaluation only builds a
    Heston curve when models['Heston']['calib'] is present), but NOT MC's
    -- build_smile_comparison's MC curve is an unconditional bisection over
    the chain, independent of `models`. To guarantee the flag is honored
    either way, the MC/Heston curve is also explicitly dropped from this
    function's own returned `curves` dict when its flag is False. A full
    "skip the calibration entirely" optimization would require changing
    _gather_all_models_and_market's signature, which this task deliberately
    does not do.
    """
    ticker = str(ticker).upper()
    if not ticker:
        raise ValueError("iv_smile_by_model requires a ticker.")
    option_type = "put" if str(option_type).strip().lower() == "put" else "call"
    is_call = option_type == "call"

    if td is None:
        from shared.thetadata import ThetaDataController

        td = ThetaDataController()

    try:
        spot = float(td.fetch_spot_price(ticker))
    except (TypeError, ValueError):
        spot = float("nan")
    except Exception as exc:
        raise ValueError(f"could not fetch spot price for {ticker!r}: {exc}") from exc
    if not math.isfinite(spot) or spot <= 0:
        raise ValueError(f"no usable spot price for {ticker!r}")

    if expiry:
        resolved_exp = str(expiry).replace("-", "")
    else:
        try:
            listed = td.list_expirations(ticker) or []
        except Exception as exc:
            raise ValueError(
                f"could not list expirations for {ticker!r}: {exc}"
            ) from exc
        exps = sorted(str(e).replace("-", "") for e in listed)
        if not exps:
            raise ValueError(f"no listed expiries for {ticker!r}")
        resolved_exp = exps[0]

    if strike is not None:
        K = float(strike)
    else:
        K = spot
        list_strikes = getattr(td, "list_strikes", None)
        if callable(list_strikes):
            try:
                strikes = list_strikes(ticker, resolved_exp) or []
                if strikes:
                    K = float(min(strikes, key=lambda s: abs(float(s) - spot)))
            except Exception:
                pass  # K stays spot -- the smile CENTER, not a strike that
                # must exactly match a listed one.

    try:
        exp_date = _parse_yyyymmdd(resolved_exp)
    except Exception as exc:
        raise ValueError(
            f"could not parse expiry {resolved_exp!r} as YYYYMMDD: {exc}"
        ) from exc
    dte = (exp_date - date.today()).days
    T = dte / 365.0  # DEFAULT_A=365 convention -- see Vol_Suite/expiry_book_exposure.py

    try:
        r = td.fetch_risk_free_rate(T)
    except Exception:
        r = None
    if not r:
        r = 0.05

    try:
        q = float(td.fetch_dividend_yield(ticker))
    except Exception:
        q = 0.0

    _ensure_stdio_can_print_unicode()
    options_main = _import_options_main()
    vol_manager_cls = _import_vol_manager_cls()
    vol_manager = vol_manager_cls()
    pricing_config = options_main.PricingConfig()

    # Module-global monkeypatch, not thread-safe against a concurrent call
    # into this function (or any other options_main.get_safe_float caller)
    # from another request. Safe today only because dashboard/app.py's
    # _run_tool_safe runs every tool synchronously with no threadpool
    # offload, so requests are already serialized process-wide -- revisit
    # this if that ever changes.
    original_get_safe_float = options_main.get_safe_float
    options_main.get_safe_float = lambda prompt, default: default
    try:
        gathered = options_main._gather_all_models_and_market(
            ticker,
            spot,
            K,
            T,
            r,
            q,
            option_type,
            is_call,
            resolved_exp,
            vol_manager,
            pricing_config,
        )
    finally:
        options_main.get_safe_float = original_get_safe_float

    models = dict(gathered.get("models") or {})
    if not include_mc:
        models.pop("MC", None)
    if not include_heston:
        models.pop("Heston", None)

    chain_evaluation = _import_chain_evaluation()
    comparison = chain_evaluation.build_smile_comparison(
        ticker,
        gathered.get("market_exp") or resolved_exp,
        spot,
        K,
        T,
        r,
        q,
        models,
        vol_manager,
        gathered.get("atm_vol_vv"),
        gathered.get("rr25"),
        gathered.get("bf25"),
    )
    if comparison is None:
        raise ValueError(
            f"could not build a smile comparison for {ticker!r} @ {resolved_exp}"
        )

    curves: dict[str, dict[str, list]] = {}
    for label, curve in (comparison.get("curves") or {}).items():
        if label == "MC" and not include_mc:
            continue
        if label == "Heston" and not include_heston:
            continue
        ks, ivs = curve
        curves[label] = {
            "strikes": np.asarray(ks, dtype=float).tolist(),
            "ivs": np.asarray(ivs, dtype=float).tolist(),
        }

    _m_strikes = comparison.get("market_strikes")
    _m_ivs = comparison.get("market_ivs")
    market_strikes = np.asarray(
        _m_strikes if _m_strikes is not None else [], dtype=float
    )
    market_ivs = np.asarray(_m_ivs if _m_ivs is not None else [], dtype=float)

    result = {
        "ticker": ticker,
        "spot": spot,
        "expiry": gathered.get("market_exp") or resolved_exp,
        "strike": K,
        "option_type": option_type,
        "curves": curves,
        "market": {
            "strikes": market_strikes.tolist(),
            "ivs": market_ivs.tolist(),
        },
        "meta": {
            "source": (
                "Options_Suite.chain_evaluation.build_smile_comparison "
                "(+ main._gather_all_models_and_market)"
            ),
            "T": T,
            "r": r,
            "q": q,
            "include_mc": bool(include_mc),
            "include_heston": bool(include_heston),
            "models_present": sorted(curves.keys()),
        },
    }
    return _json_safe(result)
