"""module_registry.py (Options_Suite)

Phase 2 of Widget-Native Quant Console: real per-model ModuleSpec entries.
Leisen-Reimer remains the default_selected pricing method per CLAUDE.md.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_OPTS_DIR = Path(__file__).resolve().parent
if str(_OPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_OPTS_DIR))

from shared.module_registry import ArchiveHint, InputSpec, ModuleResult, ModuleSpec

def _failed(exc: Exception) -> ModuleResult:
    return ModuleResult(status="failed", artifacts=[], metrics={"error": str(exc)}, context_patch=None)


def _extract_pricing_args(context: dict[str, Any]) -> tuple[str, float, float, float, float, float, bool, str]:
    """Pull (ticker, S, K, T, r, q, is_call, option_type) from the context.

    Context may carry either flat keys or a nested ``focus`` block.  Any
    missing required piece raises ``ValueError`` with a clear message so the
    widget-run API can surface it.
    """
    focus = context.get("focus") or {}
    ticker = str(context.get("ticker") or focus.get("ticker") or "").strip().upper()
    if not ticker:
        raise ValueError("Options_Suite module requires context['ticker'] or context['focus']['ticker']")

    S = float(context.get("spot") or focus.get("spot") or context.get("S") or focus.get("S") or 0.0)
    if S <= 0:
        raise ValueError(f"No usable spot price for {ticker}; got S={S}")

    K_raw = context.get("strike") or focus.get("strike") or context.get("K") or focus.get("K")
    if K_raw is None:
        raise ValueError(f"Options_Suite module requires context['strike'] (or focus.strike) for {ticker}")
    K = float(K_raw)

    T = context.get("target_years") or focus.get("target_years") or context.get("T") or focus.get("T")
    if T is None:
        raise ValueError(f"Options_Suite module requires context['target_years'] (or focus.target_years) for {ticker}")
    T = float(T)

    r = float(context.get("r") or context.get("risk_free_rate") or focus.get("r") or focus.get("risk_free_rate") or 0.05)
    q = float(context.get("q") or context.get("dividend_yield") or focus.get("q") or focus.get("dividend_yield") or 0.0)

    option_type = str(context.get("option_type") or focus.get("option_type") or "call").strip().lower()
    is_call = option_type == "call"

    return ticker, S, K, T, r, q, is_call, option_type


def _run_crr(context: dict[str, Any]) -> ModuleResult:
    try:
        from american_binomial import crr_american_price, crr_all_greeks
        ticker, S, K, T, r, q, is_call, _ = _extract_pricing_args(context)
        sigma = float(context.get("sigma") or 0.25)
        price = float(crr_american_price(S, K, T, r, sigma, q, is_call))
        greeks = crr_all_greeks(S, K, T, r, sigma, q, is_call)
        return ModuleResult(
            status="ok",
            artifacts=[],
            metrics={"ticker": ticker, "model": "crr", "price": price, "greeks": greeks, "sigma": sigma},
            context_patch=None,
        )
    except Exception as exc:
        return _failed(exc)


def _run_leisen_reimer(context: dict[str, Any]) -> ModuleResult:
    try:
        from american_binomial import leisen_reimer_american_price, lr_all_greeks
        ticker, S, K, T, r, q, is_call, _ = _extract_pricing_args(context)
        sigma = float(context.get("sigma") or 0.25)
        price = float(leisen_reimer_american_price(S, K, T, r, sigma, q, is_call))
        greeks = lr_all_greeks(S, K, T, r, sigma, q, is_call)
        return ModuleResult(
            status="ok",
            artifacts=[],
            metrics={"ticker": ticker, "model": "leisen_reimer", "price": price, "greeks": greeks, "sigma": sigma},
            context_patch=None,
        )
    except Exception as exc:
        return _failed(exc)


def _run_newton_raphson_iv(context: dict[str, Any]) -> ModuleResult:
    try:
        from american_binomial import leisen_reimer_american_price, lr_all_greeks
        from NewtonRaphsonIV import implied_volatility_nr_american
        ticker, S, K, T, r, q, is_call, _ = _extract_pricing_args(context)
        market_price = float(context.get("market_price") or context.get("price") or 0.0)
        if market_price <= 0:
            raise ValueError("Newton-Raphson IV requires context['market_price'] > 0")
        seed = float(context.get("seed") or 0.2)
        sigma, converged = implied_volatility_nr_american(market_price, S, K, T, r, is_call, q=q, seed=seed)
        price = float(leisen_reimer_american_price(S, K, T, r, sigma, q, is_call))
        greeks = lr_all_greeks(S, K, T, r, sigma, q, is_call)
        return ModuleResult(
            status="ok",
            artifacts=[],
            metrics={
                "ticker": ticker, "model": "newton_raphson_iv", "price": price,
                "greeks": greeks, "sigma": sigma, "converged": converged,
            },
            context_patch=None,
        )
    except Exception as exc:
        return _failed(exc)


def _run_baw(context: dict[str, Any]) -> ModuleResult:
    try:
        from barone_adesi_whaley import baw_american_price, baw_all_greeks
        ticker, S, K, T, r, q, is_call, _ = _extract_pricing_args(context)
        sigma = float(context.get("sigma") or 0.25)
        price = float(baw_american_price(S, K, T, r, sigma, q, is_call))
        greeks = baw_all_greeks(S, K, T, r, sigma, q, is_call)
        return ModuleResult(
            status="ok",
            artifacts=[],
            metrics={"ticker": ticker, "model": "baw", "price": price, "greeks": greeks, "sigma": sigma},
            context_patch=None,
        )
    except Exception as exc:
        return _failed(exc)


def _run_mc(context: dict[str, Any]) -> ModuleResult:
    try:
        from MC import AmericanLSMPricer, mc_all_greeks
        from Options_Suite.config import PricingConfig
        ticker, S, K, T, r, q, is_call, _ = _extract_pricing_args(context)
        sigma = float(context.get("sigma") or 0.25)
        cfg = PricingConfig()
        sims = int(context.get("simulations") or cfg.simulations)
        steps = int(context.get("steps") or cfg.steps)
        pricer = AmericanLSMPricer(S, K, T, r, q, sigma, simulations=sims, steps=steps, option="call" if is_call else "put")
        price = pricer.price()
        greeks = mc_all_greeks(S, K, T, r, q, sigma, sims=sims, steps=steps, option="call" if is_call else "put", seed=42)
        return ModuleResult(
            status="ok",
            artifacts=[],
            metrics={"ticker": ticker, "model": "mc", "price": price, "greeks": greeks, "sigma": sigma},
            context_patch=None,
        )
    except Exception as exc:
        return _failed(exc)


def _run_sabr(context: dict[str, Any]) -> ModuleResult:
    try:
        from SABRModel import SABRModel, sabr_all_greeks
        from american_binomial import leisen_reimer_american_price
        ticker, S, K, T, r, q, is_call, _ = _extract_pricing_args(context)
        calibration = context.get("sabr_calibration") or context.get("calibration")
        if not calibration:
            raise ValueError("SABR module requires context['sabr_calibration'] with alpha/beta/rho/nu")
        alpha = float(calibration["alpha"])
        beta = float(calibration["beta"])
        rho = float(calibration["rho"])
        nu = float(calibration["nu"])
        model = SABRModel(alpha=alpha, beta=beta, rho=rho, nu=nu)
        F = S * np.exp((r - q) * T) if "numpy" in globals() else S * __import__("numpy").exp((r - q) * T)
        sigma = model.get_vol(F, K, T)
        price = float(leisen_reimer_american_price(S, K, T, r, sigma, q, is_call))
        greeks = sabr_all_greeks(S, K, T, r, q, is_call, calibration)
        return ModuleResult(
            status="ok",
            artifacts=[],
            metrics={"ticker": ticker, "model": "sabr", "price": price, "greeks": greeks, "sigma": sigma, "params": calibration},
            context_patch=None,
        )
    except Exception as exc:
        return _failed(exc)


def _run_vanna_volga(context: dict[str, Any]) -> ModuleResult:
    try:
        from VannaVolga import get_vol, vv_all_greeks
        from american_binomial import leisen_reimer_american_price
        ticker, S, K, T, r, q, is_call, _ = _extract_pricing_args(context)
        atm_vol = float(context.get("atm_vol") or context.get("sigma") or 0.0)
        rr25 = float(context.get("rr25") or 0.0)
        bf25 = float(context.get("bf25") or 0.0)
        if atm_vol <= 0:
            raise ValueError("Vanna-Volga requires context['atm_vol'] > 0")
        sigma = get_vol(S, K, T, r, q, atm_vol, rr25, bf25)
        price = float(leisen_reimer_american_price(S, K, T, r, sigma, q, is_call))
        greeks = vv_all_greeks(S, K, T, r, q, is_call, atm_vol=atm_vol, rr25=rr25, bf25=bf25)
        return ModuleResult(
            status="ok",
            artifacts=[],
            metrics={"ticker": ticker, "model": "vanna_volga", "price": price, "greeks": greeks, "sigma": sigma},
            context_patch=None,
        )
    except Exception as exc:
        return _failed(exc)


def _run_mc_heston_lsm(context: dict[str, Any]) -> ModuleResult:
    try:
        import importlib.util
        import MCHestonLSM
        from Options_Suite.config import PricingConfig
        ticker, S, K, T, r, q, is_call, _ = _extract_pricing_args(context)
        initial_sigma = float(context.get("sigma") or 0.25)
        cfg = PricingConfig()
        res = MCHestonLSM.run_heston_full(
            ticker, S, K, T, r, q, initial_sigma,
            sims=cfg.heston_sims, steps=cfg.heston_steps,
            option="call" if is_call else "put", seed=42, exp=context.get("expiry"),
        )
        calib = res.get("calib") or {}
        if not calib:
            raise RuntimeError("Heston calibration failed -- no calib returned")
        greeks = MCHestonLSM.heston_all_greeks(
            S, K, T, r, q,
            V0=calib["v0"], kappa=calib["kappa"], theta=calib["theta"],
            vol_sigma=calib["xi"], rho=calib["rho"],
            sims=max(4000, cfg.heston_sims // 3),
            steps=max(80, cfg.heston_steps // 2),
            option="call" if is_call else "put", seed=42,
        )
        return ModuleResult(
            status="ok",
            artifacts=[],
            metrics={"ticker": ticker, "model": "mc_heston_lsm", "price": res.get("price"), "greeks": greeks, "calib": calib},
            context_patch=None,
        )
    except Exception as exc:
        return _failed(exc)


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
        ticker = str(context.get("ticker") or context.get("focus", {}).get("ticker") or "").strip().upper()
        if not ticker:
            raise ValueError("model_comparison requires context['ticker'] or focus.ticker")

        if context.get("synthetic"):
            return _synthetic_model_comparison(ticker, context)

        result = smile_by_model.build_iv_smile_by_model(
            ticker,
            expiry=context.get("expiry") or context.get("focus", {}).get("expiry"),
            strike=context.get("strike") or context.get("focus", {}).get("strike"),
            option_type=str(context.get("option_type") or context.get("focus", {}).get("option_type") or "call"),
            include_mc=bool(context.get("include_mc", True)),
            include_heston=bool(context.get("include_heston", True)),
        )
        return ModuleResult(status="ok", artifacts=[], metrics=result, context_patch=None)
    except Exception as exc:
        return _failed(exc)


def _synthetic_model_comparison(ticker: str, context: dict[str, Any]) -> ModuleResult:
    """Return a deterministic multi-model IV smile for offline testing."""
    import numpy as np
    spot = float(context.get("spot") or context.get("focus", {}).get("spot") or 100.0)
    strike = float(context.get("strike") or context.get("focus", {}).get("strike") or spot)
    sigma = float(context.get("sigma") or context.get("focus", {}).get("sigma") or 0.25)
    option_type = str(context.get("option_type") or context.get("focus", {}).get("option_type") or "call").strip().lower()
    is_call = option_type == "call"
    # Build a small strike grid around the spot
    strikes = np.linspace(spot * 0.85, spot * 1.15, 21)
    # Simple Black-Scholes-ish implied vol smile: base sigma with mild skew
    ivs = sigma + 0.05 * ((strikes / spot) - 1.0) ** 2 + 0.02 * (1.0 - strikes / spot)
    # Use each pricing model to compute a price curve for the smile
    from american_binomial import leisen_reimer_american_price, crr_american_price
    from barone_adesi_whaley import baw_american_price
    T = float(context.get("target_years") or context.get("focus", {}).get("target_years") or 0.25)
    r = float(context.get("risk_free_rate") or context.get("focus", {}).get("risk_free_rate") or 0.05)
    q = float(context.get("dividend_yield") or context.get("focus", {}).get("dividend_yield") or 0.0)
    curves = {}
    for label, price_fn in [
        ("CRR", lambda S, K: crr_american_price(S, K, T, r, sigma, q, is_call)),
        ("Leisen-Reimer", lambda S, K: leisen_reimer_american_price(S, K, T, r, sigma, q, is_call)),
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
        sample={"ticker": "SPY", "strike": 550, "target_years": 0.25, "option_type": "call", "sigma": 0.2},
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
        sample={"ticker": "SPY", "strike": 550, "target_years": 0.25, "option_type": "call", "sigma": 0.2},
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
        sample={"ticker": "SPY", "strike": 550, "target_years": 0.25, "option_type": "call", "market_price": 12.5},
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
        sample={"ticker": "SPY", "strike": 550, "target_years": 0.25, "option_type": "call", "sabr_calibration": {"alpha": 0.3, "beta": 0.5, "rho": -0.3, "nu": 0.5}},
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
        sample={"ticker": "SPY", "strike": 550, "target_years": 0.25, "option_type": "call", "atm_vol": 0.2, "rr25": 3.0, "bf25": 1.0},
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
        sample={"ticker": "SPY", "strike": 550, "target_years": 0.25, "option_type": "call", "sigma": 0.2},
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
        sample={"ticker": "SPY", "strike": 550, "target_years": 0.25, "option_type": "call", "sigma": 0.2},
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
        sample={"ticker": "SPY", "strike": 550, "target_years": 0.25, "option_type": "call", "sigma": 0.2},
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
        sample={"ticker": "SPY", "expiry": "20260920", "include_mc": True, "include_heston": True},
    ),
]
