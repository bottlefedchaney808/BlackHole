"""module_registry.py (Options_Suite)

Phase 2 of Widget-Native Quant Console: real per-model ModuleSpec entries.
Leisen-Reimer remains the default_selected pricing method per CLAUDE.md.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

_OPTS_DIR = Path(__file__).resolve().parent
if str(_OPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_OPTS_DIR))

from shared.module_registry import ArchiveHint, InputSpec, ModuleResult, ModuleSpec


def _failed(exc: Exception) -> ModuleResult:
    return ModuleResult(
        status="failed", artifacts=[], metrics={"error": str(exc)}, context_patch=None
    )


_DEFAULT_TARGET_YEARS = 0.25


def _years_to_expiry(expiry: Any) -> float:
    """Year fraction to an expiry date, or the 3-month default.

    Accepts the two forms scope carries: "YYYY-MM-DD" (what the scope bar
    shows) and "YYYYMMDD" (what ThetaData uses). "auto", empty, or an
    unparseable value falls back to `_DEFAULT_TARGET_YEARS` rather than
    raising -- an unreadable expiry should cost you the exact maturity, not
    the whole pricing run.
    """
    from datetime import date, datetime

    text = str(expiry or "").strip()
    if not text or text.lower() == "auto":
        return _DEFAULT_TARGET_YEARS
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            parsed = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        days = (parsed - date.today()).days
        # Same-day expiry is a real thing (0DTE); floor it so the pricers get
        # a positive T instead of dividing by zero.
        return max(days, 0.25) / 365.0
    return _DEFAULT_TARGET_YEARS


def _resolve_sigma(
    context: dict[str, Any],
    ticker: str,
    K: float,
    T: float,
    option_type: str,
    method: str,
) -> tuple[float, str]:
    """Solve sigma from live market data, the way main.py does.

    Returns (sigma, provenance). `provenance` is "context" when the caller
    supplied one, "<method>" when VolManager solved it from the chain, and
    "fallback:0.25" when the solve failed.

    Why this exists: CRR, Leisen-Reimer, BAW and MC each did
    `sigma = float(context.get("sigma") or 0.25)`, so from a widget card --
    which never supplies sigma -- every one of them priced at a flat 25%
    vol and reported the result as a price. Not a wrong price for the wrong
    reason; a price of a different option. `main.py::run_context_mode` has
    always gone through `VolManager.get_sigma(..., method="LeisenReimer")`,
    and VolManager deliberately raises rather than substituting a default
    (see its own "NO FALLBACKS" comment), so a failure here is surfaced in
    `sigma_source` instead of being laundered into the number.
    """
    supplied = context.get("sigma") or (context.get("focus") or {}).get("sigma")
    if supplied:
        return float(supplied), "context"
    try:
        from vol_manager import VolManager

        sigma = float(
            VolManager().get_sigma(ticker, K, T, method=method, option_type=option_type)
        )
        if sigma > 0:
            return sigma, method
    except Exception as exc:  # noqa: BLE001 -- reported, not swallowed
        return 0.25, f"fallback:0.25 ({type(exc).__name__}: {exc})"
    return 0.25, "fallback:0.25 (solver returned non-positive sigma)"


def _first_set(*values: Any) -> Any:
    """First value that is not None -- a legitimate 0.0 survives.

    Deliberately NOT an `or` chain. `r` and `q` are routinely and correctly
    zero (a non-dividend-paying name, a zero-rate scenario, a controlled
    test), and `0.0 or fallback` silently discards the caller's number and
    falls through to a live market fetch instead. That is how the
    Newton-Raphson closed-loop test started recovering sigma=0.2570 from a
    price generated at sigma=0.2500: an explicit `dividend_yield=0.0` was
    dropped and replaced with SPY's live ~0.98% yield mid-solve.
    """
    for value in values:
        if value is not None:
            return value
    return None


def _extract_pricing_args(
    context: dict[str, Any],
) -> tuple[str, float, float, float, float, float, bool, str]:
    """Pull (ticker, S, K, T, r, q, is_call, option_type) from the context.

    Context may carry either flat keys or a nested ``focus`` block.  Any
    missing required piece raises ``ValueError`` with a clear message so the
    widget-run API can surface it.
    """
    focus = context.get("focus") or {}
    ticker = str(context.get("ticker") or focus.get("ticker") or "").strip().upper()
    if not ticker:
        raise ValueError(
            "Options_Suite module requires context['ticker'] or context['focus']['ticker']"
        )

    # Everything below is fetched live when the caller did not supply it.
    #
    # These adapters used to demand that spot, strike AND target_years all
    # arrive pre-filled in the context, and raise otherwise. Nothing in the
    # widget path fills them: a card knows a ticker (and maybe an expiry), so
    # every Options_Suite model failed on "No usable spot price for SPY; got
    # S=0.0" before it priced anything -- all eight of them, always.
    # `main.py::run_context_mode` has always fetched its own spot / rate /
    # dividend and defaulted the strike to ATM; this is the same behaviour,
    # so the two entry points agree instead of one being unusable.
    _md = None

    def market_data():
        nonlocal _md
        if _md is None:
            from market_data import MarketDataController

            _md = MarketDataController()
        return _md

    S = float(
        context.get("spot")
        or focus.get("spot")
        or context.get("S")
        or focus.get("S")
        or 0.0
    )
    if S <= 0:
        S = float(market_data().fetch_spot_price(ticker))
    if S <= 0:
        raise ValueError(f"No usable spot price for {ticker}; got S={S}")

    T = (
        context.get("target_years")
        or focus.get("target_years")
        or context.get("T")
        or focus.get("T")
    )
    if T is None:
        T = _years_to_expiry(context.get("expiry") or focus.get("expiry"))
    T = float(T)
    if T <= 0:
        raise ValueError(f"Non-positive time to maturity for {ticker}: T={T}")

    K_raw = (
        context.get("strike")
        or focus.get("strike")
        or context.get("K")
        or focus.get("K")
    )
    if K_raw is None:
        # ATM by default, snapped to a listed strike -- the same default
        # main.py uses when the context carries no strike.
        K_raw = round(float(S), 2)
    try:
        K = float(
            market_data().validate_strike(
                ticker,
                float(K_raw),
                target_years=T,
                expiration_date=(context.get("expiry") or focus.get("expiry") or None),
            )["closest"]
        )
    except Exception:
        # Strike validation needs a listed chain; without one, price the
        # requested strike as given rather than losing the whole run.
        K = float(K_raw)

    # _first_set, not `or`: a caller-supplied 0.0 rate / yield is a real
    # number and must not fall through to a live fetch. See _first_set.
    r = _first_set(
        context.get("r"),
        context.get("risk_free_rate"),
        focus.get("r"),
        focus.get("risk_free_rate"),
    )
    r = float(r) if r is not None else float(market_data().fetch_risk_free_rate(T=T))
    q = _first_set(
        context.get("q"),
        context.get("dividend_yield"),
        focus.get("q"),
        focus.get("dividend_yield"),
    )
    q = float(q) if q is not None else float(market_data().fetch_dividend_yield(ticker))

    option_type = (
        str(context.get("option_type") or focus.get("option_type") or "call")
        .strip()
        .lower()
    )
    is_call = option_type == "call"

    return ticker, S, K, T, r, q, is_call, option_type


def _run_crr(context: dict[str, Any]) -> ModuleResult:
    try:
        from american_binomial import crr_all_greeks, crr_american_price

        ticker, S, K, T, r, q, is_call, option_type = _extract_pricing_args(context)
        sigma, sigma_source = _resolve_sigma(context, ticker, K, T, option_type, "CRR")
        price = float(crr_american_price(S, K, T, r, sigma, q, is_call))
        greeks = crr_all_greeks(S, K, T, r, sigma, q, is_call)
        return ModuleResult(
            status="ok",
            artifacts=[],
            metrics={
                "ticker": ticker,
                "model": "crr",
                "price": price,
                "greeks": greeks,
                "sigma": sigma,
                "sigma_source": sigma_source,
            },
            context_patch=None,
        )
    except Exception as exc:
        return _failed(exc)


def _run_leisen_reimer(context: dict[str, Any]) -> ModuleResult:
    try:
        from american_binomial import leisen_reimer_american_price, lr_all_greeks

        ticker, S, K, T, r, q, is_call, option_type = _extract_pricing_args(context)
        sigma, sigma_source = _resolve_sigma(
            context, ticker, K, T, option_type, "LeisenReimer"
        )
        price = float(leisen_reimer_american_price(S, K, T, r, sigma, q, is_call))
        greeks = lr_all_greeks(S, K, T, r, sigma, q, is_call)
        return ModuleResult(
            status="ok",
            artifacts=[],
            metrics={
                "ticker": ticker,
                "model": "leisen_reimer",
                "price": price,
                "greeks": greeks,
                "sigma": sigma,
                "sigma_source": sigma_source,
            },
            context_patch=None,
        )
    except Exception as exc:
        return _failed(exc)


def _run_newton_raphson_iv(context: dict[str, Any]) -> ModuleResult:
    try:
        from american_binomial import leisen_reimer_american_price, lr_all_greeks
        from NewtonRaphsonIV import implied_volatility_nr_american

        ticker, S, K, T, r, q, is_call, option_type = _extract_pricing_args(context)
        market_price = float(context.get("market_price") or context.get("price") or 0.0)
        price_source = "context"
        chain_iv = None
        if market_price <= 0:
            # A card knows a ticker and an expiry, never a contract's price, so
            # this used to fail every desk run. Solve against the listed
            # contract's own market price, as VolManager's NewtonRaphson
            # branch does.
            from datetime import datetime, timedelta

            from vol_manager import fetch_market_iv_from_chain

            expiry_date = (datetime.now() + timedelta(days=int(T * 365))).strftime(
                "%Y-%m-%d"
            )
            chain_iv, chain_price, expiry_used, _ts = fetch_market_iv_from_chain(
                ticker, K, expiry_date, option_type, T=T
            )
            if not chain_price or chain_price <= 0:
                raise ValueError(
                    f"No listed market price for {ticker} {K:g} {option_type} "
                    f"near T={T:.4f}y -- nothing to solve an implied vol from."
                )
            market_price = float(chain_price)
            price_source = f"chain mid ({expiry_used})"
        seed = float(context.get("seed") or chain_iv or 0.2)
        sigma, converged = implied_volatility_nr_american(
            market_price, S, K, T, r, is_call, q=q, seed=seed
        )
        # Provenance must tell the truth about an unidentifiable solve. When
        # a deep-ITM American option is priced at intrinsic, every sigma below
        # the early-exercise boundary reproduces that price, so the number is
        # an upper bound, not a measurement -- same discipline as
        # _resolve_sigma's "fallback:" prefix: if the source does not say
        # "solved", do not trade the number as an implied vol.
        sigma_source = (
            "newton_raphson_iv (solved from the market price)"
            if converged
            else (
                "newton_raphson_iv UPPER BOUND -- price is at intrinsic, so "
                "IV is not identifiable (every lower vol reprices the same)"
            )
        )
        price = float(leisen_reimer_american_price(S, K, T, r, sigma, q, is_call))
        greeks = lr_all_greeks(S, K, T, r, sigma, q, is_call)
        return ModuleResult(
            status="ok",
            artifacts=[],
            metrics={
                "ticker": ticker,
                "model": "newton_raphson_iv",
                "price": price,
                "greeks": greeks,
                "sigma": sigma,
                "sigma_source": sigma_source,
                "converged": converged,
                "market_price": market_price,
                "price_source": price_source,
                "strike": K,
            },
            context_patch=None,
        )
    except Exception as exc:
        return _failed(exc)


def _run_baw(context: dict[str, Any]) -> ModuleResult:
    try:
        from barone_adesi_whaley import baw_all_greeks, baw_american_price

        ticker, S, K, T, r, q, is_call, option_type = _extract_pricing_args(context)
        sigma, sigma_source = _resolve_sigma(context, ticker, K, T, option_type, "BAW")
        price = float(baw_american_price(S, K, T, r, sigma, q, is_call))
        greeks = baw_all_greeks(S, K, T, r, sigma, q, is_call)
        return ModuleResult(
            status="ok",
            artifacts=[],
            metrics={
                "ticker": ticker,
                "model": "baw",
                "price": price,
                "greeks": greeks,
                "sigma": sigma,
                "sigma_source": sigma_source,
            },
            context_patch=None,
        )
    except Exception as exc:
        return _failed(exc)


def _run_mc(context: dict[str, Any]) -> ModuleResult:
    try:
        from MC import AmericanLSMPricer, mc_all_greeks

        from Options_Suite.config import PricingConfig

        ticker, S, K, T, r, q, is_call, option_type = _extract_pricing_args(context)
        sigma, sigma_source = _resolve_sigma(
            context, ticker, K, T, option_type, "LeisenReimer"
        )
        cfg = PricingConfig()
        sims = int(context.get("simulations") or cfg.simulations)
        steps = int(context.get("steps") or cfg.steps)
        pricer = AmericanLSMPricer(
            S,
            K,
            T,
            r,
            q,
            sigma,
            simulations=sims,
            steps=steps,
            option="call" if is_call else "put",
        )
        price = pricer.price()
        greeks = mc_all_greeks(
            S,
            K,
            T,
            r,
            q,
            sigma,
            sims=sims,
            steps=steps,
            option="call" if is_call else "put",
            seed=42,
        )
        return ModuleResult(
            status="ok",
            artifacts=[],
            metrics={
                "ticker": ticker,
                "model": "mc",
                "price": price,
                "greeks": greeks,
                "sigma": sigma,
                "sigma_source": sigma_source,
            },
            context_patch=None,
        )
    except Exception as exc:
        return _failed(exc)


def _run_sabr(context: dict[str, Any]) -> ModuleResult:
    try:
        from american_binomial import leisen_reimer_american_price
        from SABRModel import SABRModel, sabr_all_greeks

        ticker, S, K, T, r, q, is_call, option_type = _extract_pricing_args(context)
        calibration = context.get("sabr_calibration") or context.get("calibration")
        # Plain math.exp on a scalar. This was a `np.exp(...) if "numpy" in
        # globals() else __import__("numpy").exp(...)` ternary whose first
        # branch could never run -- the module imports numpy as `np`, so the
        # name "numpy" is never a global -- and which would have raised
        # NameError on `np` the moment anyone added a module-level
        # `import numpy`. F is one float here; there is nothing to vectorize.
        F = S * math.exp((r - q) * T)
        if calibration:
            sigma_source = "sabr (calibration supplied in context)"
        else:
            # Nothing on the desk ever supplies a calibration, so this module
            # failed on every card run with "requires context['sabr_calibration']".
            # Calibrate against this expiry's live smile the way main.py does,
            # through VolManager's SABR branch, and price off THAT fit.
            from vol_manager import VolManager

            vm = VolManager()
            vm.get_sigma(ticker, K, T, method="SABR", option_type=option_type)
            cached = vm.last_sabr_calibration or {}
            calib = cached.get("calib")
            if not calib:
                raise RuntimeError(f"SABR calibration returned nothing for {ticker}")
            calibration = {
                k: float(calib[k]) for k in ("alpha", "beta", "rho", "nu", "rmse")
            }
            F = float(getattr(cached.get("calibrator"), "forward", F) or F)
            sigma_source = (
                f"sabr (calibrated to the live smile, RMSE {calibration['rmse']:.4f})"
            )
        alpha = float(calibration["alpha"])
        beta = float(calibration["beta"])
        rho = float(calibration["rho"])
        nu = float(calibration["nu"])
        model = SABRModel(alpha=alpha, beta=beta, rho=rho, nu=nu)
        sigma = model.get_vol(F, K, T)
        price = float(leisen_reimer_american_price(S, K, T, r, sigma, q, is_call))
        greeks = sabr_all_greeks(S, K, T, r, q, is_call, calibration)
        return ModuleResult(
            status="ok",
            artifacts=[],
            metrics={
                "ticker": ticker,
                "model": "sabr",
                "price": price,
                "greeks": greeks,
                "sigma": sigma,
                "sigma_source": sigma_source,
                "params": calibration,
            },
            context_patch=None,
        )
    except Exception as exc:
        return _failed(exc)


def _run_vanna_volga(context: dict[str, Any]) -> ModuleResult:
    try:
        from american_binomial import leisen_reimer_american_price
        from VannaVolga import get_vol, vv_all_greeks

        ticker, S, K, T, r, q, is_call, option_type = _extract_pricing_args(context)
        atm_vol = float(context.get("atm_vol") or context.get("sigma") or 0.0)
        rr25 = float(context.get("rr25") or 0.0)
        bf25 = float(context.get("bf25") or 0.0)
        sigma_source = "vanna_volga (atm_vol/rr25/bf25 supplied in context)"
        if atm_vol <= 0:
            # Same fix as SABR: a card never supplies the 3-pillar smile, so
            # read it off the live chain the way main.py does. get_auto_rr_bf
            # returns vol points; atm is converted to decimal, rr/bf are passed
            # through in main.py's own units.
            from datetime import datetime, timedelta

            from VannaVolga import get_auto_rr_bf

            expiry_date = (datetime.now() + timedelta(days=int(T * 365))).strftime(
                "%Y-%m-%d"
            )
            auto_rr, auto_bf, auto_atm = get_auto_rr_bf(ticker, expiry_date)
            if not auto_atm:
                raise ValueError(
                    f"No live smile for {ticker} near {expiry_date} -- Vanna-Volga "
                    "needs an ATM vol and 25-delta wings to build its smile."
                )
            atm_vol, rr25, bf25 = (
                float(auto_atm) / 100.0,
                float(auto_rr),
                float(auto_bf),
            )
            sigma_source = (
                f"vanna_volga (live 25d smile: ATM {auto_atm:.2f}, RR {auto_rr:+.2f}, "
                f"BF {auto_bf:.2f} vol pts)"
            )
        sigma = get_vol(S, K, T, r, q, atm_vol, rr25, bf25)
        price = float(leisen_reimer_american_price(S, K, T, r, sigma, q, is_call))
        greeks = vv_all_greeks(
            S, K, T, r, q, is_call, atm_vol=atm_vol, rr25=rr25, bf25=bf25
        )
        return ModuleResult(
            status="ok",
            artifacts=[],
            metrics={
                "ticker": ticker,
                "model": "vanna_volga",
                "price": price,
                "greeks": greeks,
                "sigma": sigma,
                "sigma_source": sigma_source,
            },
            context_patch=None,
        )
    except Exception as exc:
        return _failed(exc)


def _run_mc_heston_lsm(context: dict[str, Any]) -> ModuleResult:
    try:
        import MCHestonLSM

        from Options_Suite.config import PricingConfig

        ticker, S, K, T, r, q, is_call, option_type = _extract_pricing_args(context)
        # Heston LSM calibrates its own variance process; this is only the
        # starting vol for that calibration, so it keeps its own name.
        initial_sigma, sigma_source = _resolve_sigma(
            context, ticker, K, T, option_type, "LeisenReimer"
        )
        cfg = PricingConfig()
        res = MCHestonLSM.run_heston_full(
            ticker,
            S,
            K,
            T,
            r,
            q,
            initial_sigma,
            sims=cfg.heston_sims,
            steps=cfg.heston_steps,
            option="call" if is_call else "put",
            seed=42,
            exp=context.get("expiry"),
        )
        calib = res.get("calib") or {}
        if not calib:
            raise RuntimeError("Heston calibration failed -- no calib returned")
        greeks = MCHestonLSM.heston_all_greeks(
            S,
            K,
            T,
            r,
            q,
            V0=calib["v0"],
            kappa=calib["kappa"],
            theta=calib["theta"],
            vol_sigma=calib["xi"],
            rho=calib["rho"],
            sims=max(4000, cfg.heston_sims // 3),
            steps=max(80, cfg.heston_steps // 2),
            option="call" if is_call else "put",
            seed=42,
        )
        return ModuleResult(
            status="ok",
            artifacts=[],
            metrics={
                "ticker": ticker,
                "model": "mc_heston_lsm",
                "price": res.get("price"),
                "greeks": greeks,
                "calib": calib,
            },
            context_patch=None,
        )
    except Exception as exc:
        return _failed(exc)


def _explicit_expiry(context: dict[str, Any]) -> str | None:
    """The scope's expiry, or None when the caller said "pick one for me".

    Vol_Suite's modules use the sentinel string ``"auto"`` for "resolve the
    expiry yourself" (see `Vol_Suite/module_registry.py::_resolve_expiry`),
    and the dashboard's scope bar passes whatever is in the expiry box
    straight through -- including an empty box, and including ``auto`` when a
    Vol_Suite card put it there. Callers that want a *date* therefore have to
    map those to None rather than handing the literal string ``"auto"`` to a
    chain lookup, which fails deep inside the vendor call with an unhelpful
    parse error.
    """
    focus = context.get("focus") or {}
    raw = context.get("expiry") or focus.get("expiry") or focus.get("expiration_date")
    text = str(raw or "").strip()
    return None if text.lower() in ("", "auto", "none", "null") else text


def _run_model_comparison(context: dict[str, Any]) -> ModuleResult:
    """Thin wrapper around Options_Suite/smile_by_model.py IV smile comparison.

    This keeps the widget contract synchronous and self-contained while
    reusing the real multi-model smile builder already used by Surface
    Explorer.  It returns a ``distribution``-style payload (curves per
    model) rather than a single price/Greek row.

    For unit-test / offline use, pass ``context['synthetic'] = True`` to
    bypass market data and return deterministic curves.
    """
    try:
        import smile_by_model

        ticker = (
            str(context.get("ticker") or context.get("focus", {}).get("ticker") or "")
            .strip()
            .upper()
        )
        if not ticker:
            raise ValueError(
                "model_comparison requires context['ticker'] or focus.ticker"
            )

        if context.get("synthetic"):
            return _synthetic_model_comparison(ticker, context)

        result = smile_by_model.build_iv_smile_by_model(
            ticker,
            # None, not the literal "auto": build_iv_smile_by_model picks a
            # listed expiry itself when given None, and chokes on the sentinel.
            expiry=_explicit_expiry(context),
            strike=context.get("strike") or context.get("focus", {}).get("strike"),
            option_type=str(
                context.get("option_type")
                or context.get("focus", {}).get("option_type")
                or "call"
            ),
            include_mc=bool(context.get("include_mc", True)),
            include_heston=bool(context.get("include_heston", True)),
        )
        return ModuleResult(
            status="ok", artifacts=[], metrics=result, context_patch=None
        )
    except Exception as exc:
        return _failed(exc)


def _synthetic_model_comparison(ticker: str, context: dict[str, Any]) -> ModuleResult:
    """Return a deterministic multi-model IV smile for offline testing."""
    import numpy as np

    spot = float(context.get("spot") or context.get("focus", {}).get("spot") or 100.0)
    strike = float(
        context.get("strike") or context.get("focus", {}).get("strike") or spot
    )
    sigma = float(context.get("sigma") or context.get("focus", {}).get("sigma") or 0.25)
    option_type = (
        str(
            context.get("option_type")
            or context.get("focus", {}).get("option_type")
            or "call"
        )
        .strip()
        .lower()
    )
    is_call = option_type == "call"
    # Build a small strike grid around the spot
    strikes = np.linspace(spot * 0.85, spot * 1.15, 21)
    # Simple Black-Scholes-ish implied vol smile: base sigma with mild skew
    ivs = sigma + 0.05 * ((strikes / spot) - 1.0) ** 2 + 0.02 * (1.0 - strikes / spot)
    # Use each pricing model to compute a price curve for the smile
    from american_binomial import crr_american_price, leisen_reimer_american_price
    from barone_adesi_whaley import baw_american_price

    T = float(
        context.get("target_years")
        or context.get("focus", {}).get("target_years")
        or 0.25
    )
    r = float(
        context.get("risk_free_rate")
        or context.get("focus", {}).get("risk_free_rate")
        or 0.05
    )
    q = float(
        context.get("dividend_yield")
        or context.get("focus", {}).get("dividend_yield")
        or 0.0
    )
    curves = {}
    for label, price_fn in [
        ("CRR", lambda S, K: crr_american_price(S, K, T, r, sigma, q, is_call)),
        (
            "Leisen-Reimer",
            lambda S, K: leisen_reimer_american_price(S, K, T, r, sigma, q, is_call),
        ),
        ("BAW", lambda S, K: baw_american_price(S, K, T, r, sigma, q, is_call)),
    ]:
        prices = [float(price_fn(spot, K)) for K in strikes]
        curves[label] = {
            "strikes": strikes.tolist(),
            "prices": prices,
            "ivs": ivs.tolist(),
        }
    result = {
        "ticker": ticker,
        "spot": spot,
        "strike": strike,
        "option_type": option_type,
        "curves": curves,
        "market": {"strikes": [], "ivs": []},
        "meta": {
            "source": "Options_Suite.module_registry._synthetic_model_comparison",
            "synthetic": True,
            "T": T,
            "r": r,
            "q": q,
            "models_present": sorted(curves.keys()),
        },
    }
    return ModuleResult(status="ok", artifacts=[], metrics=result, context_patch=None)


MODULES: list[ModuleSpec] = [
    ModuleSpec(
        name="CRR",
        slug="crr",
        suite="options_suite",
        category="pricing",
        run=_run_crr,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
        description="Cox-Ross-Rubinstein American binomial tree pricer with its own FD Greeks.",
        inputs=InputSpec(ticker="required", expiry="optional"),
        output_kind="metrics",
        sample={
            "ticker": "SPY",
            "strike": 550,
            "target_years": 0.25,
            "option_type": "call",
            "sigma": 0.2,
        },
    ),
    ModuleSpec(
        name="Leisen-Reimer",
        slug="leisen_reimer",
        suite="options_suite",
        category="pricing",
        run=_run_leisen_reimer,
        cli_entry=None,
        default_selected=True,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
        description="Leisen-Reimer American binomial tree (default pricing method).",
        inputs=InputSpec(ticker="required", expiry="optional"),
        output_kind="metrics",
        sample={
            "ticker": "SPY",
            "strike": 550,
            "target_years": 0.25,
            "option_type": "call",
            "sigma": 0.2,
        },
    ),
    ModuleSpec(
        name="Newton-Raphson IV",
        slug="newton_raphson_iv",
        suite="options_suite",
        category="pricing",
        run=_run_newton_raphson_iv,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
        description="Implied-vol solver via Newton-Raphson against the Leisen-Reimer American price.",
        inputs=InputSpec(ticker="required", expiry="optional"),
        output_kind="metrics",
        sample={
            "ticker": "SPY",
            "strike": 550,
            "target_years": 0.25,
            "option_type": "call",
            "market_price": 12.5,
        },
    ),
    ModuleSpec(
        name="SABR",
        slug="sabr",
        suite="options_suite",
        category="pricing",
        run=_run_sabr,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
        description="SABR Hagan smile-aware American option Greeks.",
        inputs=InputSpec(ticker="required", expiry="required"),
        output_kind="metrics",
        sample={
            "ticker": "SPY",
            "strike": 550,
            "target_years": 0.25,
            "option_type": "call",
            "sabr_calibration": {"alpha": 0.3, "beta": 0.5, "rho": -0.3, "nu": 0.5},
        },
    ),
    ModuleSpec(
        name="Vanna-Volga",
        slug="vanna_volga",
        suite="options_suite",
        category="pricing",
        run=_run_vanna_volga,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
        description="Vanna-Volga smile-aware price and Greeks from 25-delta market quotes.",
        inputs=InputSpec(ticker="required", expiry="required"),
        output_kind="metrics",
        sample={
            "ticker": "SPY",
            "strike": 550,
            "target_years": 0.25,
            "option_type": "call",
            "atm_vol": 0.2,
            "rr25": 3.0,
            "bf25": 1.0,
        },
    ),
    ModuleSpec(
        name="MC",
        slug="mc",
        suite="options_suite",
        category="pricing",
        run=_run_mc,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
        description="Longstaff-Schwartz Monte Carlo American pricer with CRN Greeks.",
        inputs=InputSpec(ticker="required", expiry="optional"),
        output_kind="metrics",
        sample={
            "ticker": "SPY",
            "strike": 550,
            "target_years": 0.25,
            "option_type": "call",
            "sigma": 0.2,
        },
    ),
    ModuleSpec(
        name="MC Heston LSM",
        slug="mc_heston_lsm",
        suite="options_suite",
        category="pricing",
        run=_run_mc_heston_lsm,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
        description="Heston stochastic-vol calibration + LSM American pricer.",
        inputs=InputSpec(ticker="required", expiry="required"),
        output_kind="metrics",
        sample={
            "ticker": "SPY",
            "strike": 550,
            "target_years": 0.25,
            "option_type": "call",
            "sigma": 0.2,
        },
    ),
    ModuleSpec(
        name="BAW",
        slug="baw",
        suite="options_suite",
        category="pricing",
        run=_run_baw,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
        description="Barone-Adesi-Whaley analytical American closed-form approximation.",
        inputs=InputSpec(ticker="required", expiry="optional"),
        output_kind="metrics",
        sample={
            "ticker": "SPY",
            "strike": 550,
            "target_years": 0.25,
            "option_type": "call",
            "sigma": 0.2,
        },
    ),
    ModuleSpec(
        name="Model Comparison",
        slug="model_comparison",
        suite="options_suite",
        category="pricing",
        run=_run_model_comparison,
        cli_entry=None,
        default_selected=False,
        requires=[],
        archive=ArchiveHint(key_shape="ticker_expiry"),
        description="Multi-model implied-vol smile comparison across CRR/LR/NR/SABR/VV/BAW/etc.",
        inputs=InputSpec(ticker="required", expiry="optional"),
        output_kind="chart",
        sample={
            "ticker": "SPY",
            "expiry": "20260920",
            "include_mc": True,
            "include_heston": True,
        },
    ),
]
