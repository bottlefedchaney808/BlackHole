from market_data import MarketDataController
from vol_manager import VolManager
from american_binomial import (
    crr_american_price, leisen_reimer_american_price,
    american_all_greeks, lr_all_greeks, crr_all_greeks,
)
from MC import AmericanLSMPricer, mc_all_greeks
from VannaVolga import get_auto_rr_bf, get_vol as vanna_volga_vol, vv_all_greeks
from NewtonRaphsonIV import implied_volatility_nr, implied_volatility_nr_american
from bruteforceimpliedvol import brute_force, brute_force_lr, brute_force_mc
from SABRModel import SABRCalibrator, sabr_vol_hagan, sabr_all_greeks
from barone_adesi_whaley import baw_american_price, baw_all_greeks, brute_force_baw
from config import PricingConfig
from datetime import datetime, timedelta
import concurrent.futures
import argparse
import json
import os
import time
import sys
import numpy as np
from typing import Any, Dict, Optional
import MCHestonLSM
import data_source_config

# Check ThetaData credentials early, fail fast
# ThetaData uses CF Access (Cloudflare) authentication
CLIENT_ID = os.environ.get('THETADATA_CF_ACCESS_CLIENT_ID')
CLIENT_SECRET = os.environ.get('THETADATA_CF_ACCESS_CLIENT_SECRET')

if not CLIENT_ID or not CLIENT_SECRET:
    print("ERROR: ThetaData CF Access credentials not set.")
    print("Required environment variables:")
    print("  - THETADATA_CF_ACCESS_CLIENT_ID")
    print("  - THETADATA_CF_ACCESS_CLIENT_SECRET")
    print("Set these variables and retry. Credentials can be in .env or environment.")
    sys.exit(1)

# chain_evaluation.build_smile_comparison is now wired in as an opt-in
# prompt inside the choice == '9' ("Run all models & compare") branch --
# it's asked for explicitly (y/n), never run automatically, so it stays
# out of every default single-K report run. See NOTES_chain_evaluation.md.

def get_safe_float(prompt, default):
    while True:
        user_input = input(prompt).strip()
        if not user_input: return default
        try: return float(user_input)
        except ValueError:
            print(f"Invalid input. Using default: {default}")
            return default


def _first_present(data: Dict[str, Any], keys) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_option(value: Any) -> Optional[str]:
    if value is None:
        return None
    lowered = str(value).strip().lower()
    if lowered in {"call", "c"}:
        return "call"
    if lowered in {"put", "p"}:
        return "put"
    return None


def _extract_context_fields(context: Dict[str, Any]) -> Dict[str, Any]:
    focus = context.get("focus", {}) if isinstance(context.get("focus"), dict) else {}
    market = context.get("market", {}) if isinstance(context.get("market"), dict) else {}
    options = context.get("options", {}) if isinstance(context.get("options"), dict) else {}

    merged = dict(context)
    merged.update(focus)
    merged.update(market)
    merged.update(options)

    ticker = _first_present(merged, ["ticker", "focus_ticker", "symbol"])
    option_type = _normalize_option(_first_present(merged, ["option_type", "option", "call_put"]))
    strike = _to_float(_first_present(merged, ["strike", "K"]))
    target_years = _to_float(_first_present(merged, ["target_years", "T", "years_to_expiry"]))
    if target_years is None:
        expiration_date = _first_present(merged, ["expiration_date", "expiry_date", "expiry"])
        if expiration_date:
            try:
                expiry_dt = datetime.fromisoformat(str(expiration_date).replace("Z", "+00:00"))
                target_years = max((expiry_dt - datetime.now(expiry_dt.tzinfo)).total_seconds() / (365.0 * 24 * 3600), 0.0)
            except ValueError:
                target_years = None
    return {
        "ticker": str(ticker).strip().upper() if ticker else "",
        "option_type": option_type,
        "strike": strike,
        "target_years": target_years,
    }


def _resolve_context_out_path(context_path: str, context: Dict[str, Any], context_out: Optional[str]) -> str:
    if context_out:
        return context_out
    output_dir = context.get("output_dir")
    if isinstance(output_dir, str) and output_dir.strip():
        return os.path.join(output_dir, "options_result.json")
    return os.path.join(os.path.dirname(os.path.abspath(context_path)), "options_result.json")


def _write_context_payload(path: str, payload: Dict[str, Any]) -> None:
    out_path = os.path.abspath(path)
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")


def run_context_mode(context_path: str, context_out: Optional[str], no_interactive: bool) -> int:
    try:
        with open(context_path, "r", encoding="utf-8-sig") as f:
            context = json.load(f)
    except FileNotFoundError:
        print(f"Context file not found: {context_path}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"Invalid context JSON: {exc}", file=sys.stderr)
        return 2

    if not isinstance(context, dict):
        print("Context root must be a JSON object.", file=sys.stderr)
        return 2

    fields = _extract_context_fields(context)
    ticker = fields["ticker"]
    option_type = fields["option_type"] or "call"
    target_years = fields["target_years"] if fields["target_years"] is not None else 1.0

    missing = []
    if not ticker:
        missing.append("focus.ticker")
    if fields["option_type"] is None:
        missing.append("focus.option_type")
    if fields["target_years"] is None:
        missing.append("focus.target_years or focus.expiration_date")

    output_path = _resolve_context_out_path(context_path, context, context_out)

    if missing and no_interactive:
        payload = {
            "suite": "options",
            "status": "error",
            "ticker": ticker,
            "method": "CRR",
            "error": "Missing required context fields: " + ", ".join(missing),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        _write_context_payload(output_path, payload)
        print(payload["error"], file=sys.stderr)
        return 2

    try:
        market_data = MarketDataController()
        S = market_data.fetch_spot_price(ticker)
        r = market_data.fetch_risk_free_rate()
        q = market_data.fetch_dividend_yield(ticker)
        K = fields["strike"] if fields["strike"] is not None else round(float(S), 2)

        vol_manager = VolManager()
        sigma = float(vol_manager.get_sigma(ticker, K, target_years, method="CRR", option_type=option_type))

        # CRR prices via its own Cox-Ross-Rubinstein tree -- not
        # AmericanLSMPricer's generic Longstaff-Schwartz Monte Carlo. See the
        # interactive choice=='1' path's own fix for the full reasoning: a
        # constant-vol plain American option is exactly what a deterministic
        # tree is for.
        is_call = (option_type == 'call')
        price = float(crr_american_price(S, K, target_years, r, sigma, q, is_call))
        # Non-interactive/context-mode path uses CRR sigma, so Greeks come
        # from crr_all_greeks (CRR's own tree engine) -- matches the
        # interactive choice=='1' branch below.
        greeks = {k: float(v) for k, v in crr_all_greeks(S, K, target_years, r, sigma, q, is_call).items()}

        payload = {
            "suite": "options",
            "status": "ok",
            "ticker": ticker,
            "method": "CRR",
            "sigma": sigma,
            "price": price,
            "greeks": greeks,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        _write_context_payload(output_path, payload)
        print(json.dumps(payload, indent=2))
        return 0
    except Exception as exc:
        payload = {
            "suite": "options",
            "status": "error",
            "ticker": ticker,
            "method": "CRR",
            "error": str(exc),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        _write_context_payload(output_path, payload)
        print(f"Context-mode run failed: {exc}", file=sys.stderr)
        return 1


def _gather_all_models_and_market(ticker, S, K, T, r, q, option_type, is_call,
                                   resolved_exp, vol_manager, pricing_config):
    """Shared per-model price/Greeks/market gathering used by BOTH
    choice == '9' (single-K "Run all models & compare") and choice == '10'
    (full-chain evaluation) -- both need the exact same `models` dict,
    `vol_manager` state, and VannaVolga rr25/bf25/atm_vol_vv that this block
    assembles, so it was factored out here rather than duplicated verbatim
    (see NOTES_chain_evaluation.md's "full chain" spec). This is a straight
    move of the code that used to live inline in choice == '9' -- logic,
    order, and prints are unchanged, including the Heston expiry-alignment
    fix (exp=market_exp passed through) and the "no substituted number on
    Heston failure" behavior.

    Returns a dict: td, market_iv, market_price, market_exp, bulk, models,
    rr25, bf25, atm_vol_vv, market_greeks.
    """
    try:
        from thetadata_controller import ThetaDataController
        td = ThetaDataController()
        right = 'C' if option_type == 'call' else 'P'
        # Use the expiry already resolved from the target above (real listed
        # date, with T derived from it). T is therefore ALREADY correct here --
        # no separate reconciliation step, and every model + the Market row
        # share exactly this expiry/T. Falls back to fetch_option_iv's own
        # nearest-match only if the selector didn't resolve one.
        market_iv, market_price, market_exp, market_ts = td.fetch_option_iv(
            ticker, K, T, option_type, exp=resolved_exp)
        bulk = td.option_bulk_greeks(ticker, market_exp)
    except Exception:
        td = None
        market_iv = None
        market_price = None
        market_exp = resolved_exp
        bulk = []

    # Gather models -- each prices via ITS OWN textbook American
    # solver (see the single-model block above for the same
    # reasoning): CRR via its own tree, Leisen-Reimer/Newton-Raphson/
    # SABR/Vanna-Volga via the LR tree (their Greeks already use it),
    # MC via AmericanLSMPricer (the one model that legitimately uses
    # it), Heston via its own distinct engine. No model here borrows
    # another's sigma or price.
    models = {}

    # ---- Each model uses its OWN Greek engine below (per the
    # "each model solves everything on its own" principle Jason
    # explicitly re-asserted after seeing comparison_20260727_155415).
    # The old code called american_all_greeks(...) for EVERY model
    # here, which meant every "model row" in the report was
    # Delta/Gamma/Vega from ONE shared Leisen-Reimer tree at that
    # model's flat sigma -- so Delta/Gamma/Vega read byte-for-byte
    # identical across CRR/LR/NR/SABR/VV/MC on the AMD report, hiding
    # the real per-model differences (smile response for SABR/VV,
    # LSM regression response for MC, stochastic-vol response for
    # Heston, and even CRR-tree vs LR-tree convergence differences
    # for the two binomial models). Now every model has and uses
    # its own engine.

    # CRR -- Greeks via FD on the CRR tree (the same tree that
    # produced CRR's price).
    sigma_crr = vol_manager.get_sigma(ticker, K, T, method='CRR', option_type=option_type)
    p_crr = float(crr_american_price(S, K, T, r, sigma_crr, q, is_call))
    g_crr = crr_all_greeks(S, K, T, r, sigma_crr, q, is_call)
    models['CRR'] = {'price': p_crr, 'greeks': g_crr, 'sigma': sigma_crr}
    print(f"[CRR] sigma={sigma_crr:.4f} | price={p_crr:.4f}")
    print(f"[CRR] greeks: {g_crr}")

    # Leisen-Reimer -- Greeks via FD on the LR tree.
    sigma_lr = vol_manager.get_sigma(ticker, K, T, method='LeisenReimer', option_type=option_type)
    p_lr = float(leisen_reimer_american_price(S, K, T, r, sigma_lr, q, is_call))
    g_lr = lr_all_greeks(S, K, T, r, sigma_lr, q, is_call)
    models['Leisen-Reimer'] = {'price': p_lr, 'greeks': g_lr, 'sigma': sigma_lr}
    print(f"[Leisen-Reimer] sigma={sigma_lr:.4f} | price={p_lr:.4f}")
    print(f"[Leisen-Reimer] greeks: {g_lr}")

    # Newton-Raphson -- prices via the LR tree, so Greeks via
    # lr_all_greeks (its OWN pricer's engine, same as LR).
    sigma_nr = vol_manager.get_sigma(ticker, K, T, method='NewtonRaphson', option_type=option_type)
    p_nr = float(leisen_reimer_american_price(S, K, T, r, sigma_nr, q, is_call))
    g_nr = lr_all_greeks(S, K, T, r, sigma_nr, q, is_call)
    models['Newton-Raphson'] = {'price': p_nr, 'greeks': g_nr, 'sigma': sigma_nr}
    print(f"[Newton-Raphson] sigma={sigma_nr:.4f} | price={p_nr:.4f}")
    print(f"[Newton-Raphson] greeks: {g_nr}")

    # SABR -- Greeks via sabr_all_greeks, which propagates bumps
    # through the SABR SMILE (Hagan formula recomputed at each
    # bumped state). vol_manager caches the last SABR calibration
    # under .last_sabr_calibration so this doesn't recalibrate.
    sigma_sabr = vol_manager.get_sigma(ticker, K, T, method='SABR', option_type=option_type)
    p_sabr = float(leisen_reimer_american_price(S, K, T, r, sigma_sabr, q, is_call))
    sabr_cached = getattr(vol_manager, 'last_sabr_calibration', None)
    # NO FALLBACK. Substituting LR-flat-sigma Greeks here would publish
    # another model's numbers under the SABR label -- the row would look
    # populated and plausible while containing nothing smile-aware at all.
    # This shouldn't happen (get_sigma(method='SABR') above caches a
    # calibration); if it does, that's a real failure worth seeing.
    if not (sabr_cached and sabr_cached.get('calib')):
        raise RuntimeError(
            '[SABR] Cannot compute smile-aware Greeks without cached SABR calibration.'
        )
    g_sabr = sabr_all_greeks(S, K, T, r, q, is_call, sabr_cached['calib'])
    models['SABR'] = {'price': p_sabr, 'greeks': g_sabr, 'sigma': sigma_sabr}
    print(f"[SABR] sigma={sigma_sabr:.4f} | price={p_sabr:.4f}")
    print(f"[SABR] greeks: {g_sabr}")

    # Vanna-Volga
    expiry_date = (datetime.now() + timedelta(days=int(T * 365))).strftime("%Y-%m-%d")
    auto_rr, auto_bf, auto_atm = get_auto_rr_bf(ticker, expiry_date)
    rr25_default = auto_rr
    bf25_default = auto_bf
    rr25 = get_safe_float(f"Enter RR25 to use for Vanna-Volga (default {rr25_default:.2f}): ", rr25_default)
    bf25 = get_safe_float(f"Enter BF25 to use for Vanna-Volga (default {bf25_default:.2f}): ", bf25_default)
    # auto_atm is percent-vol-point form (get_auto_rr_bf's own convention);
    # VV's own independent market read, not another model's solved sigma.
    atm_vol_vv = auto_atm / 100.0 if auto_atm else None
    sigma_vv = vol_manager.get_sigma(
        ticker, K, T, method='VannaVolga', rr25=rr25, bf25=bf25,
        atm_vol=atm_vol_vv, option_type=option_type,
    )
    p_vv = float(leisen_reimer_american_price(S, K, T, r, sigma_vv, q, is_call))
    # VV Greeks: re-interpolate the 3-pillar smile at every S / T / r
    # bump (see vv_all_greeks). NO FALLBACK -- without an ATM vol and the
    # two wing quotes there is no 3-pillar smile to bump through, and
    # LR-flat-sigma Greeks under the VannaVolga label would be a different
    # model's output wearing VV's name. Raise instead.
    if atm_vol_vv is None or rr25 is None or bf25 is None:
        raise RuntimeError(
            '[VannaVolga] Cannot compute smile-aware Greeks without ATM vol, RR25, BF25.'
        )
    g_vv = vv_all_greeks(S, K, T, r, q, is_call, atm_vol=atm_vol_vv, rr25=rr25, bf25=bf25)
    models['VannaVolga'] = {'price': p_vv, 'greeks': g_vv, 'sigma': sigma_vv, 'rr25': rr25, 'bf25': bf25}
    print(f"[VannaVolga] rr25={rr25} bf25={bf25} sigma={sigma_vv:.4f} | price={p_vv:.4f}")
    print(f"[VannaVolga] greeks: {g_vv}")

    # Monte Carlo (LSM) -- the only model here that legitimately
    # prices via AmericanLSMPricer, with its own bisection IV solver
    # (brute_force_mc) targeting that exact same simulation.
    # P1 FIX: pass the SAME simulations/steps used for the price/Greeks
    # pricer below into the IV solve, so both come from one sim config
    # instead of IV being solved at a hardcoded 20000/100 while price/
    # Greeks use pricing_config's (possibly much smaller, e.g. in tests)
    # simulations/steps.
    sigma_mc = vol_manager.get_sigma(ticker, K, T, method='MC', option_type=option_type,
                                      simulations=pricing_config.simulations, mc_steps=pricing_config.steps)
    mc_pricer_all = AmericanLSMPricer(S, K, T, r, q, sigma_mc, simulations=pricing_config.simulations,
                                       steps=pricing_config.steps, option=option_type)
    p_mc = mc_pricer_all.price()
    g_mc = mc_pricer_all.calculate_greeks()
    models['MC'] = {'price': p_mc, 'greeks': g_mc, 'sigma': sigma_mc}
    print(f"[MC] sigma={sigma_mc:.4f} | price={p_mc:.4f}")
    print(f"[MC] greeks: {g_mc}")

    # Barone-Adesi-Whaley (American BS) -- analytical American
    # closed-form approximation. Its own bisection IV solver
    # against baw_american_price, and its own FD-on-analytical-
    # pricer Greeks. Included as the direct "does Market use BS?"
    # test: if BAW's Delta/Gamma/Vega/Rho/Theta match the Market
    # row closely, the vendor is using BAW or a very similar
    # analytical American BS approach.
    sigma_baw = vol_manager.get_sigma(ticker, K, T, method='BAW', option_type=option_type)
    p_baw = float(baw_american_price(S, K, T, r, sigma_baw, q, is_call))
    g_baw = baw_all_greeks(S, K, T, r, sigma_baw, q, is_call)
    models['BAW'] = {'price': p_baw, 'greeks': g_baw, 'sigma': sigma_baw}
    print(f"[BAW] sigma={sigma_baw:.4f} | price={p_baw:.4f}")
    print(f"[BAW] greeks: {g_baw}")

    # Heston -- its own distinct engine. Unlike every other model
    # above, a genuine Heston failure here is caught rather than
    # aborting the whole multi-model comparison report; it's
    # reported as an honest None, never silently replaced with
    # another model's number (see run_heston_full's own no-fallback
    # docstring in MCHestonLSM.py for why that distinction matters).
    try:
        initial_sigma_heston = vol_manager.get_sigma(ticker, K, T, method='CRR', option_type=option_type)
        # exp=market_exp: the real listed contract this whole compare
        # block is keyed to (it came from resolved_exp, reconfirmed by
        # the fetch_option_iv call above). Heston must calibrate to
        # THAT contract's smile, not to one it re-derives from T --
        # see HestonCalibrator._prepare's expiry-alignment note.
        res_all = MCHestonLSM.run_heston_full(ticker, S, K, T, r, q, initial_sigma_heston,
                                               sims=pricing_config.heston_compare_sims,
                                               steps=pricing_config.heston_compare_steps, option=option_type,
                                               exp=market_exp)
        calib_all = res_all.get('calib') or {}
        # Replace run_heston_full's shortcut Greeks (which routed
        # through american_all_greeks with effective_sigma=sqrt(v0),
        # ignoring kappa/theta/xi/rho entirely) with heston_all_greeks:
        # CRN bump-and-revalue on the actual Heston LSM using ALL
        # calibrated params. This is the whole point of the refactor
        # -- Heston's row now reflects Heston-under-Heston dynamics.
        # NO FALLBACK. The old else-branch reached for res_all['greeks'] --
        # run_heston_full's american_all_greeks-at-sqrt(v0) shortcut -- which
        # is exactly the non-Heston output this refactor removed. Without a
        # calibration there are no Heston params to bump, so raise.
        if not calib_all:
            raise RuntimeError(
                '[Heston] Cannot compute Heston-specific Greeks without calibration result.'
            )
        g_heston = MCHestonLSM.heston_all_greeks(
            S, K, T, r, q,
            V0=calib_all['v0'], kappa=calib_all['kappa'],
            theta=calib_all['theta'], vol_sigma=calib_all['xi'],
            rho=calib_all['rho'],
            sims=max(4000, pricing_config.heston_compare_sims // 3),
            steps=max(80, pricing_config.heston_compare_steps // 2),
            option=option_type, seed=42,
        )
        models['Heston'] = {
            'price': res_all.get('price'), 'greeks': g_heston, 'calib': calib_all,
            'sigma': (calib_all.get('v0') ** 0.5) if calib_all.get('v0') is not None else None,
        }
        print(f"[Heston] greeks (own dynamics): {g_heston}")
    except Exception as e:
        print(f"[Heston] Failed: {e}. Reporting as unavailable (no substituted number).")
        models['Heston'] = {'price': None, 'greeks': None}

    # Market / ThetaData greeks selector
    market_greeks = {}
    if td and bulk:
        k_theta = int(round(K * 1000))
        right_sym = 'C' if option_type == 'call' else 'P'
        for row in bulk:
            try:
                if int(row.get('strike', 0)) == k_theta and row.get('right') == right_sym:
                    # extract common greeks robustly
                    for g in ['delta','gamma','vega','rho','theta','vanna','vomma','color','speed','charm']:
                        for key in (g, g.upper(), g.capitalize()):
                            if key in row and row[key] not in (None, ''):
                                market_greeks[g] = float(row[key])
                                break
                    break
            except Exception:
                continue

    return {
        'td': td, 'market_iv': market_iv, 'market_price': market_price,
        'market_exp': market_exp, 'bulk': bulk, 'models': models,
        'rr25': rr25, 'bf25': bf25, 'atm_vol_vv': atm_vol_vv,
        'market_greeks': market_greeks,
    }


def main():
    print("=== Professional Trading Terminal ===")
    print("Welcome to the Option Pricing Engine.\n")

    pricing_config = PricingConfig()
    if not pricing_config.validate_config():
        raise RuntimeError("PricingConfig validation failed. Check configuration values and constraints.")

    try:
        # PotatoHedge/ThetaData is the sole live market-data source (yahoo purged).
        # No source-selection or yahoo-fallback prompt anymore -- if PotatoHedge is
        # unavailable, pricing degrades via the guarded constants in
        # data_source_config.py rather than switching providers.
        raw_ticker = input("Enter the ticker symbol (e.g., 'AAPL'): ").strip().upper()
        # Strip stray characters (e.g. a mistyped ']SPY') so they don't get baked
        # into the request URL and produce a confusing 400 / "check credentials".
        ticker = ''.join(ch for ch in raw_ticker if ch.isalnum() or ch in '.-')
        if ticker != raw_ticker:
            print(f"[Input] Cleaned ticker '{raw_ticker}' -> '{ticker}'")
        if not ticker: raise ValueError("Ticker cannot be empty.")

        market_data = MarketDataController()
        S = market_data.fetch_spot_price(ticker)
        r = market_data.fetch_risk_free_rate()
        q = market_data.fetch_dividend_yield(ticker)
        beta_mkt = market_data.fetch_beta(ticker)
        beta_str = f"{beta_mkt:.4f}" if beta_mkt is not None else "N/A"

        print(f"\nMarket Data for {ticker}: Spot={S:.2f}, RFR={r:.4f}, DivYield={q:.4f}, Equity Beta={beta_str}\n")

        T = get_safe_float("Enter time to maturity (e.g., 0.5 for 6 months): ", 1.0)
        option_type = input("Enter option type ('call' or 'put'): ").strip().lower()
        if option_type not in ['call', 'put']: option_type = 'call'

        # Resolve the typed target T to a CONCRETE listed expiry and derive T from
        # that real date -- do NOT carry the typed 0.33 through as T. The selector
        # offers the OPEX and the nearest weekly bracketing the target; T then comes
        # from the chosen date (calendar-day diff / 365), which fixes the time-of-day
        # truncation that put a stale T into every model's d1/d2 (esp. vanna/vomma).
        # MOVED BEFORE STRIKE VALIDATION: expiry must be resolved first so strike
        # validation can check against the correct chain.
        resolved_exp = None
        try:
            from expiry_selector import choose_expiry
            from thetadata_controller import ThetaDataController as _TDC
            _td_sel = _TDC()
            try:
                resolved_exp, T_resolved = choose_expiry(_td_sel, ticker, T, interactive=True)
                if abs(T_resolved - T) > 1e-9:
                    print(f"[Expiry] Using T={T_resolved:.4f}yr from resolved contract {resolved_exp} (typed {T:.4f}).")
                T = T_resolved
            finally:
                _td_sel.close()
        except Exception as e:
            print(f"[Expiry] Could not resolve a listed expiry ({e}); using typed T={T:.4f}.")

        while True:
            try:
                strike_input = input("Enter the strike price (K): ").strip()
                if not strike_input: raise ValueError("Empty strike")
                K = float(strike_input)
                val_res = market_data.validate_strike(ticker, K)
                if not val_res['valid']:
                    print(f"Invalid strike. Closest: {val_res['closest']:.2f}")
                    if input("Use closest? (y/n): ").strip().lower() == 'y':
                        K = val_res['closest']
                    else: continue
                break
            except ValueError: print("Please enter a valid number.")

        is_call = (option_type == 'call')

        print("\nVolatility Method: (1) CRR (American), (2) SABR, (3) Vanna-Volga, (4) Newton-Raphson (American), (5) Heston, (6) Leisen-Reimer (American), (7) Monte Carlo (LSM), (8) Barone-Adesi-Whaley (American BS), (9) Run all models & compare, (10) Full chain evaluation (every model, every strike)")
        choice = input("Choice (1-10): ").strip()
        vol_manager = VolManager()

        if choice == '1':
            method, sigma = 'CRR', vol_manager.get_sigma(ticker, K, T, method='CRR', option_type=option_type)

        elif choice == '2':
            method, sigma = 'SABR', vol_manager.get_sigma(ticker, K, T, method='SABR', option_type=option_type)

        elif choice == '3':
            expiry_date = (datetime.now() + timedelta(days=int(T * 365))).strftime("%Y-%m-%d")
            auto_rr, auto_bf, auto_atm = get_auto_rr_bf(ticker, expiry_date)
            
            if auto_rr == 0.0 and auto_bf == 0.0:
                print("\n[VV Auto-Calc] Could not fetch market smile for auto-calculation.")
                print("[VV Auto-Calc] See typical ranges below for manual input.")
                print("  Equity indexes (SPY):    RR25 = 2-5%,  BF25 = 0.5-1.5%")
                print("  Tech stocks (NVDA):      RR25 = 3-8%,  BF25 = 1-3%")
                print("  Meme stocks (GME):       RR25 = 5-15%, BF25 = 2-5%")
                print("  Negative RR = puts more expensive (bearish skew)\n")
            else:
                print(f"\n[VV Auto-Calc] Market-implied values for {ticker}:")
                print(f"  RR25 = {auto_rr:+.2f}% (negative = puts more expensive)")
                print(f"  BF25 = {auto_bf:.2f}%  (butterfly convexity)")
                print(f"  ATM Vol = {auto_atm:.1f}%")
                print(f"  Suggested: RR25={auto_rr:.1f}, BF25={auto_bf:.1f}")
                
                scale = get_safe_float(
                    f"  Enter scale factor (1.0 = market, 0.5 = half, 2.0 = double) [1.0]: ", 1.0
                )
                auto_rr *= scale
                auto_bf *= max(scale, 0.1)
                print(f"  Using: RR25={auto_rr:.2f}, BF25={auto_bf:.2f}\n")
            
            rr25 = get_safe_float(f"Enter RR25 (default {auto_rr:.1f}): ", auto_rr)
            bf25 = get_safe_float(f"Enter BF25 (default {auto_bf:.1f}): ", auto_bf)
            # auto_atm is in percent-vol-point form (e.g. 34.5 = 34.5%), matching
            # get_auto_rr_bf's own return convention -- vol_manager's VannaVolga
            # branch wants a decimal sigma, hence the /100. This is VV's OWN
            # independent market read (vendor 25-delta chain), not another
            # model's solved sigma -- see vol_manager.py's VannaVolga branch.
            method, sigma = 'Vanna-Volga', vol_manager.get_sigma(
                ticker, K, T, method='VannaVolga', rr25=rr25, bf25=bf25,
                atm_vol=(auto_atm / 100.0 if auto_atm else None), option_type=option_type,
            )

        elif choice == '4':
            method, sigma = 'Newton-Raphson', vol_manager.get_sigma(ticker, K, T, method='NewtonRaphson', option_type=option_type)

        elif choice == '5':
            print("\n[Heston] Running Heston LSM pricer & calibration...")
            method = 'HestonLSM'
            # This is only an optimizer SEED (v0 = sigma^2 starting point for
            # HestonCalibrator's gradient search) -- Heston's actual reported
            # sigma/price/greeks come entirely from its own calibration
            # against the real market smile (HestonCalibrator._prepare/
            # calibrate). CRR is used here only because it's a fast, cheap,
            # already-solved starting guess -- not because Heston depends on
            # it, and not as a fallback if Heston fails (see below: no
            # fallback, this raises instead).
            sigma = vol_manager.get_sigma(ticker, K, T, method='CRR', option_type=option_type)

            # NO FALLBACK: Heston used to catch any failure here (including a
            # user Ctrl+C) and silently reprice with CRR/"Standard" instead,
            # then let the rest of the script treat that substituted number
            # as if it were Heston's. If Heston can't run, that's real
            # information -- let it raise/abort rather than mislabel a
            # different model's output.
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as exc:
                # Pass the ALREADY-resolved real listed expiry (same one every
                # other model and the Market row use). Without it Heston
                # re-derived its own target date from T and could calibrate
                # against a different nearby weekly's smile entirely -- see
                # HestonCalibrator._prepare's expiry-alignment note.
                future = exc.submit(MCHestonLSM.run_heston_full, ticker, S, K, T, r, q, sigma,
                                    pricing_config.heston_sims, pricing_config.heston_steps, option_type,
                                    42, resolved_exp)
                print("[Heston] Background job submitted. Waiting for completion (press Ctrl+C to abort)...")
                while not future.done():
                    print('.', end='', flush=True)
                    time.sleep(1)
                print('\n[Heston] Background job finished. Collecting results...')
                res = future.result()  # re-raises the worker's exception here if run_heston_full failed
                calib = res['calib']
                heston_price = res['price']
                # Replace run_heston_full's shortcut Greeks (american_all_greeks
                # at effective_sigma=sqrt(v0), which ignores kappa/theta/xi/rho
                # entirely) with heston_all_greeks -- CRN bump-and-revalue on
                # the actual Heston LSM. Same fix applied in the compare block.
                heston_greeks = MCHestonLSM.heston_all_greeks(
                    S, K, T, r, q,
                    V0=calib['v0'], kappa=calib['kappa'], theta=calib['theta'],
                    vol_sigma=calib['xi'], rho=calib['rho'],
                    sims=max(4000, pricing_config.heston_sims // 3),
                    steps=max(80, pricing_config.heston_steps // 2),
                    option=option_type, seed=42,
                )
                print(f"[Heston] Calibrated params: kappa={calib['kappa']:.4f}, theta={calib['theta']:.4f}, xi={calib['xi']:.4f}, rho={calib['rho']:.4f}, v0={calib['v0']:.6f}, rmse={calib['rmse']:.6f}")
                print(f"[Heston] Heston LSM price calculated: {heston_price:.4f}")
                print(f"[Heston] Heston-own-dynamics greeks: {heston_greeks}")

        elif choice == '6':
            method, sigma = 'Leisen-Reimer', vol_manager.get_sigma(ticker, K, T, method='LeisenReimer', option_type=option_type)

        elif choice == '7':
            # P1 FIX: same sim config passed to get_sigma as will be used
            # to build the AmericanLSMPricer for price/Greeks further down
            # (see the choice == '7' price/Greeks block below), so IV is
            # solved and priced with an identical simulations/steps count.
            method, sigma = 'MC', vol_manager.get_sigma(
                ticker, K, T, method='MC', option_type=option_type,
                simulations=pricing_config.simulations, mc_steps=pricing_config.steps)

        elif choice == '8':
            # Barone-Adesi-Whaley -- analytical American Black-Scholes
            # approximation. See barone_adesi_whaley.py's module docstring
            # for the "does Market use BS?" test rationale.
            method, sigma = 'BAW', vol_manager.get_sigma(ticker, K, T, method='BAW', option_type=option_type)

        elif choice == '9':
            # Run-all: sigma/method resolved per-model further down; this
            # placeholder keeps method/sigma defined for the FINAL RESULTS
            # print path below (which is skipped for choice=='9' anyway,
            # since that branch returns before reaching it).
            method, sigma = 'RunAll', None

        elif choice == '10':
            # Full-chain evaluation: same placeholder story as choice=='9'
            # above -- sigma/method are resolved per-model/per-strike further
            # down (inside the choice=='10' branch itself), and that branch
            # returns before reaching the FINAL RESULTS print path below.
            method, sigma = 'FullChain', None

        else:
            raise ValueError(f"Unknown menu choice: {choice!r}. No fallback -- pick 1-10.")

        # Per-model pricing: each model prices via ITS OWN textbook American
        # solver, not a shared generic AmericanLSMPricer standing in for all
        # of them regardless of name. CRR -> its own CRR tree. Leisen-Reimer,
        # Newton-Raphson, SABR, and Vanna-Volga all price off the
        # Leisen-Reimer tree (none of them has -- or needs -- its own
        # distinct tree; the tree is neutral pricing machinery once sigma is
        # solved, and this is exactly the tree their own Greeks already use
        # via american_all_greeks). Heston keeps its own distinct engine.
        # MC is the one model that legitimately prices via AmericanLSMPricer
        # -- it IS the Monte Carlo model.
        if choice == '1':
            # CRR -- own tree for both price and Greeks (see main compare
            # block for full "each model uses its own dynamics" rationale).
            price = float(crr_american_price(S, K, T, r, sigma, q, is_call))
            greeks = crr_all_greeks(S, K, T, r, sigma, q, is_call)
        elif choice == '2':
            # SABR -- smile-aware Greeks via sabr_all_greeks, using the
            # calibration vol_manager just cached under the sigma() call above.
            price = float(leisen_reimer_american_price(S, K, T, r, sigma, q, is_call))
            sabr_cached = getattr(vol_manager, 'last_sabr_calibration', None)
            if sabr_cached and sabr_cached.get('calib'):
                greeks = sabr_all_greeks(S, K, T, r, q, is_call, sabr_cached['calib'])
            else:
                print("[SABR] WARNING: no cached calibration; using LR-flat-sigma Greeks.")
                greeks = lr_all_greeks(S, K, T, r, sigma, q, is_call)
        elif choice == '3':
            # Vanna-Volga -- smile-aware Greeks via vv_all_greeks, using the
            # atm_vol / rr25 / bf25 the VV branch above resolved. auto_atm
            # comes from that branch in percent-vol-point form (e.g. 34.5 =
            # 34.5%), matching get_auto_rr_bf's convention.
            price = float(leisen_reimer_american_price(S, K, T, r, sigma, q, is_call))
            _atm_local = (auto_atm / 100.0) if ('auto_atm' in locals() and auto_atm) else None
            if _atm_local is not None and 'rr25' in locals() and 'bf25' in locals():
                greeks = vv_all_greeks(S, K, T, r, q, is_call, atm_vol=_atm_local, rr25=rr25, bf25=bf25)
            else:
                print("[VV] WARNING: no atm_vol/rr25/bf25 available for smile-aware Greeks; using LR-flat-sigma Greeks.")
                greeks = lr_all_greeks(S, K, T, r, sigma, q, is_call)
        elif choice == '5':
            price = heston_price
            greeks = heston_greeks
        elif choice == '7':
            # MC -- own LSM pricer, own CRN-based Greeks via mc_all_greeks
            # (routed through AmericanLSMPricer.calculate_greeks internally).
            mc_pricer = AmericanLSMPricer(S, K, T, r, q, sigma, simulations=pricing_config.simulations,
                                           steps=pricing_config.steps, option=option_type)
            price = mc_pricer.price()
            greeks = mc_pricer.calculate_greeks()
        elif choice == '8':
            # Barone-Adesi-Whaley -- analytical American BS. First-order
            # Greeks via FD on baw_american_price itself (closed-form
            # pricer is noise-free); 2nd/3rd-order Greeks via closed-form
            # BS at BAW's own sigma per the model's design (see
            # barone_adesi_whaley.py's baw_all_greeks docstring).
            price = float(baw_american_price(S, K, T, r, sigma, q, is_call))
            greeks = baw_all_greeks(S, K, T, r, sigma, q, is_call)
        elif choice not in ('9', '10'):
            # Leisen-Reimer (4) or Newton-Raphson (6) -- both price via LR tree,
            # so Greeks via lr_all_greeks (the tree's own FD engine).
            price = float(leisen_reimer_american_price(S, K, T, r, sigma, q, is_call))
            greeks = lr_all_greeks(S, K, T, r, sigma, q, is_call)

        # Option to run all models and compare to market (ThetaData) - if selected, perform above for each model
        if choice == '9':
            gathered = _gather_all_models_and_market(
                ticker, S, K, T, r, q, option_type, is_call, resolved_exp,
                vol_manager, pricing_config,
            )
            td = gathered['td']
            market_iv = gathered['market_iv']
            market_price = gathered['market_price']
            market_exp = gathered['market_exp']
            bulk = gathered['bulk']
            models = gathered['models']
            rr25 = gathered['rr25']
            bf25 = gathered['bf25']
            atm_vol_vv = gathered['atm_vol_vv']
            market_greeks = gathered['market_greeks']

            # Build comparison table and print (and save if requested)
            from reports import save_comparison_csv, save_comparison_pdf

            print('\nComparison summary:')
            for name, data in models.items():
                # standardized debug block per model
                ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                sigma_used = data.get('sigma') or data.get('calib', {}).get('xi') or 'N/A'
                calib = data.get('calib') or {}
                print('\n' + '='*60)
                print(f"[MODEL] {name} | {ts}")
                print(f"  Inputs: S={S:.4f}, K={K:.4f}, T={T:.4f}, r={r:.4f}, q={q:.4f}")
                print(f"  Sigma/Params: sigma={sigma_used}")
                if calib:
                    print(f"  Calib: kappa={calib.get('kappa')}, theta={calib.get('theta')}, xi={calib.get('xi')}, rho={calib.get('rho')}, v0={calib.get('v0')}, rmse={calib.get('rmse')}")
                print(f"  Price: {data.get('price')}")
                print(f"  Greeks: {data.get('greeks')}")
                print('='*60)

            # Chain-wide smile comparison is opt-in (see NOTES_chain_evaluation.md):
            # deliberately NOT run automatically on every single-K "Run all models &
            # compare" -- it's a separate, more expensive chain-wide computation
            # (solves each model's own IV across every real strike in the chain).
            smile_data = None
            include_smile = input("Include chain-wide smile comparison chart? (y/n) [n]: ").strip().lower()
            if include_smile == 'y':
                print("[Smile Chart] Building chain-wide smile comparison (this solves every model's IV across every chain strike -- may take a while)...")
                import chain_evaluation
                smile_data = chain_evaluation.build_smile_comparison(
                    ticker, market_exp, S, K, T, r, q, models, vol_manager,
                    atm_vol_vv, rr25, bf25,
                )
                if smile_data is None:
                    print("[Smile Chart] Could not build smile comparison (see error above) -- continuing without it.")

            # offer to save CSV or PDF
            report_meta = {
                'ticker': ticker, 'S': S, 'K': K, 'T': T, 'r': r, 'q': q,
                'option_type': option_type, 'expiry': market_exp,
            }
            save_choice = input("Save comparison? (none/csv/pdf/both) [none]: ").strip().lower() or 'none'
            if save_choice in ('csv','both'):
                csv_path = save_comparison_csv(models, {'price': market_price, **market_greeks}, meta=report_meta)
                print(f"Saved CSV: {csv_path}")
            if save_choice in ('pdf','both'):
                pdf_path = save_comparison_pdf(models, {'price': market_price, **market_greeks}, meta=report_meta, smile=smile_data)
                print(f"Saved PDF: {pdf_path}")

            if td:
                td.close()
            return

        # Full-chain evaluation: every model, every real strike in the chain
        # -- not one priced K plus a bolted-on smile chart. See
        # NOTES_chain_evaluation.md's "full chain" spec. Reuses the exact
        # same market-data setup / expiry resolution / per-model gathering
        # as choice=='9' via the shared helper above (run_full_chain needs
        # the same `models` dict, `vol_manager`, and VV atm_vol/rr25/bf25
        # that helper already assembles), then hands off to
        # chain_evaluation.run_full_chain for the actual per-strike solve.
        if choice == '10':
            gathered = _gather_all_models_and_market(
                ticker, S, K, T, r, q, option_type, is_call, resolved_exp,
                vol_manager, pricing_config,
            )
            td = gathered['td']
            market_exp = gathered['market_exp']
            models = gathered['models']
            rr25 = gathered['rr25']
            bf25 = gathered['bf25']
            atm_vol_vv = gathered['atm_vol_vv']

            print("\n[Full Chain] Running every model across every chain strike "
                  "(price + IV + Greeks) -- this is more expensive than the "
                  "single-K comparison and may take a while...")
            import chain_evaluation
            # MC and Heston are excluded from the full-chain solve by default
            # (2026-07-28): live timing showed these two alone were ~76% of
            # a 63.6s run (MC ~26s, Heston ~22.5s) on an 89-strike chain --
            # too slow for routine use. CRR/LR/NR/SABR/VannaVolga/BAW still
            # run at every strike; see chain_evaluation.run_full_chain's
            # include_mc/include_heston docstring if you need them back for
            # a specific run.
            chain_result = chain_evaluation.run_full_chain(
                ticker, market_exp, S, K, T, r, q, models, vol_manager,
                atm_vol_vv, rr25, bf25, option_type,
                include_mc=False, include_heston=False,
            )
            if chain_result is None:
                print("[Full Chain] Could not build the full-chain evaluation (see error above).")
                if td:
                    td.close()
                return

            n_strikes = len(chain_result.get('strikes') or [])
            n_models = len(chain_result.get('per_model') or {})
            print(f"[Full Chain] Done: {n_strikes} strikes x {n_models} models solved.")

            from reports import save_full_chain_csv, save_full_chain_pdf

            report_meta = {
                'ticker': ticker, 'S': S, 'K': K, 'T': T, 'r': r, 'q': q,
                'option_type': option_type, 'expiry': market_exp,
            }
            save_choice = input("Save full-chain report? (none/csv/pdf/both) [none]: ").strip().lower() or 'none'
            if save_choice in ('csv', 'both'):
                csv_path = save_full_chain_csv(chain_result, meta=report_meta)
                print(f"Saved CSV: {csv_path}")
            if save_choice in ('pdf', 'both'):
                pdf_path = save_full_chain_pdf(chain_result, meta=report_meta)
                print(f"Saved PDF: {pdf_path}")

            if td:
                td.close()
            return

        print("\n=== FINAL RESULTS ===")
        print(f"Ticker: {ticker} | Spot: {S:.2f} | Vol ({method}): {sigma:.4f}")
        print(f"Option: {option_type.capitalize()} | Strike: {K:.2f} | T: {T:.2f}")
        print(f"--------------------------------------------------")
        print(f"American Price: {price:.4f}")
        print(f"--------------------------------------------------")
        print(f"FIRST ORDER GREEKS:")
        print(f"Delta: {greeks['delta']:.4f} | Gamma: {greeks['gamma']:.4f} | Vega: {greeks['vega']:.4f}")
        print(f"Rho: {greeks['rho']:.4f}   | Theta: {greeks['theta']:.4f}")
        # Rho (American, early-exercise-aware) alongside the closed-form European
        # rho and their difference -- vendor Greek feeds are commonly European/
        # Black-Scholes-based even for American options, so the European number
        # is the closer analog to a quoted "Market" rho; the premium isolates how
        # much early exercise itself is worth on this Greek. See _bs_rho's
        # docstring in american_binomial.py.
        if 'rho_euro' in greeks:
            print(f"Rho (Euro): {greeks['rho_euro']:.4f} | Early-Exercise Rho Premium: {greeks['rho_ee_premium']:.4f}")
        print(f"--------------------------------------------------")
        print(f"SECOND ORDER GREEKS:")
        print(f"Vanna: {greeks['vanna']:.4f} | Vomma: {greeks['vomma']:.4f}")
        print(f"Color: {greeks['color']:.4f} | Speed: {greeks['speed']:.4f} | Charm: {greeks.get('charm', float('nan')):.4f}")
        print(f"--------------------------------------------------")

    except Exception as e:
        print(f"\nCritical Error: {e}")
        sys.exit(1)

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Options Suite terminal")
    parser.add_argument("--context", type=str, default=None, help="Path to suite_context.json")
    parser.add_argument("--context-out", type=str, default=None, help="Path to standardized context output JSON")
    parser.add_argument(
        "--no-interactive",
        action="store_true",
        help="Fail fast when required context fields are missing",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.context:
        raise SystemExit(run_context_mode(args.context, args.context_out, args.no_interactive))
    main()