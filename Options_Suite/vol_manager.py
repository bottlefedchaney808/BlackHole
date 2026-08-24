from datetime import datetime, timedelta

import data_source_config
import numpy as np
from american_binomial import crr_american_price, leisen_reimer_american_price
from barone_adesi_whaley import baw_american_price, brute_force_baw
from bruteforceimpliedvol import brute_force, brute_force_lr, brute_force_mc
from market_data import MarketDataController
from NewtonRaphsonIV import (
    black_scholes_func,
    implied_volatility_nr_american,
)
from SABRModel import SABRCalibrator, SABRModel
from VannaVolga import get_vol as vanna_volga_vol

try:
    from thetadata_controller import ThetaDataController

    _THETADATA_AVAILABLE = True
except ImportError:
    _THETADATA_AVAILABLE = False


def fetch_market_iv_from_chain(
    ticker, K, expiry_date, option_type="call", use_thetadata=True, T=None
):
    """
    Fetch market IV from PotatoHedge/ThetaData (sole live source; yahoo purged).
    Returns (chain_iv, chain_price, expiry_used, data_timestamp), or all-None if
    PotatoHedge can't supply it.

    T: actual year-fraction to maturity. If not provided, it is derived from
    expiry_date. (Previously this always queried ThetaData with a hardcoded
    T=0.25, so any request for a maturity other than ~3 months silently
    pulled the wrong expiry's market price/IV.)
    """
    if T is None:
        try:
            T = max(
                (datetime.strptime(expiry_date, "%Y-%m-%d") - datetime.now()).days
                / 365.0,
                0.001,
            )
        except Exception:
            T = 0.25
    # PotatoHedge / ThetaData is the sole live source (yahoo purged). If it can't
    # return an IV, return no data rather than falling back to yahoo.
    if _THETADATA_AVAILABLE:
        try:
            td = ThetaDataController()
            try:
                iv, price, exp, ts = td.fetch_option_iv(ticker, K, T, option_type)
            finally:
                td.close()
            if iv is not None and iv > 0:
                return iv, price, exp, ts
            print("[ThetaData] Returned no IV for this contract.")
        except Exception as e:
            print(f"[ThetaData] Could not fetch IV ({e}). (yahoo fallback removed.)")
    else:
        print(
            "[ThetaData] Controller unavailable and yahoo fallback removed; no market IV."
        )
    return None, None, None, None


def print_reference_block(
    method_name,
    ticker,
    K,
    T,
    expiry_used,
    chain_iv,
    chain_price,
    model_iv,
    S,
    r,
    q,
    option_type,
    data_ts=None,
    model_price=None,
):
    price_label = "Model Price"
    if model_price is None:
        # No natively-computed model price was supplied (e.g. SABR/Vanna-Volga only
        # produce a sigma) -- fall back to the closed-form European price as a rough
        # sanity-check comparator, and label it as such so it isn't mistaken for an
        # American price.
        model_price = black_scholes_func(S, K, T, r, model_iv, option_type == "call", q)
        price_label = "Euro Price"
    print(f"\n{'=' * 55}")
    print(f"  [REFERENCE] {method_name}")
    if expiry_used:
        print(
            f"  Ticker: {ticker} | Expiry: {expiry_used} | Strike: ${K:.2f} | Type: {option_type.capitalize()}",
            end="",
        )
        if data_ts:
            print(f" | Data: {data_ts}")
        else:
            print()
    else:
        print(
            f"  Ticker: {ticker} | Strike: ${K:.2f} | Type: {option_type.capitalize()}"
        )
    print(f"  {'─' * 50}")
    if chain_iv is not None:
        if chain_price is not None:
            print(f"  Market:   IV={chain_iv:.4f} | Price=${chain_price:.2f}")
        else:
            print(f"  Market:   IV={chain_iv:.4f} | Price=N/A")
        print(f"  Model:    IV={model_iv:.4f} | {price_label}=${model_price:.2f}")
        iv_delta = model_iv - chain_iv
        iv_delta_pct = iv_delta * 100
        if chain_price is not None:
            price_delta = model_price - chain_price
            print(
                f"  Delta:    IV={iv_delta:+.4f} ({iv_delta_pct:+.2f}%) | Price=${price_delta:+.2f}"
            )
        else:
            print(f"  Delta:    IV={iv_delta:+.4f} ({iv_delta_pct:+.2f}%) | Price=N/A")
        if abs(iv_delta_pct) < 1.5:
            print("  ✅ IV delta < 1.5%")
        elif abs(iv_delta) < 0.05:
            print("  👍 IV delta < 5 vol pts")
        else:
            print("  ⚠️ IV error > 5 vol pts")
    else:
        print("  Market:   IV=N/A | Price=N/A")
        print(f"  Model:    IV={model_iv:.4f} | {price_label}=${model_price:.2f}")
    print(f"{'=' * 55}\n")


class VolManager:
    def __init__(self):
        self.market_data = MarketDataController()
        # simple in-memory cache for market IV lookups to avoid repeated HTTP calls
        self._iv_cache = {}
        # Set by the SABR branch of get_sigma() -- the exact calibration that
        # produced the last reported SABR sigma, so a caller building a smile
        # chart can reuse it instead of running a second, independent
        # calibrate() call (which is not guaranteed to converge to the same
        # local optimum -- see get_sigma's SABR branch for the full story).
        self.last_sabr_calibration = None

    def get_sigma(self, ticker, K, T, method="CRR", **kwargs):
        # NO FALLBACKS: every branch below either solves its own sigma from
        # real market data or raises. There used to be a catch-all
        # try/except around this whole method that quietly returned a raw
        # chain_iv or a hardcoded 0.3 on ANY error -- masking real bugs
        # (bad tickers, dead API, a genuine solver failure) as a plausible-
        # looking number. Per explicit direction: fail loudly, debug the
        # real cause, never get "tricked" by a silently-substituted default.
        S = self.market_data.fetch_spot_price(ticker)
        r = self.market_data.fetch_risk_free_rate()
        q = self.market_data.fetch_dividend_yield(ticker)
        option_type = kwargs.get("option_type", "call")
        use_td = kwargs.get("use_thetadata", None)
        if use_td is None:
            use_td = getattr(data_source_config, "PREFER_THETADATA", True)

        expiry_date = (datetime.now() + timedelta(days=int(T * 365))).strftime(
            "%Y-%m-%d"
        )
        cache_key = (ticker, K, expiry_date, option_type)
        if cache_key in self._iv_cache:
            chain_iv, chain_price, expiry_used, data_ts = self._iv_cache[cache_key]
        else:
            chain_iv, chain_price, expiry_used, data_ts = fetch_market_iv_from_chain(
                ticker, K, expiry_date, option_type, use_thetadata=use_td, T=T
            )
            if chain_iv is not None or chain_price is not None:
                self._iv_cache[cache_key] = (
                    chain_iv,
                    chain_price,
                    expiry_used,
                    data_ts,
                )

        # Only the methods that bisect/Newton-solve directly against the
        # market price AT THIS STRIKE need chain_price here -- SABR
        # calibrates its own smile independently (many strikes, not just K)
        # and Vanna-Volga uses its own atm_vol/rr25/bf25 market read, so
        # gating those on this one strike's quote would fail them for no
        # reason tied to their actual data dependency.
        _needs_chain_price = method in (
            "CRR",
            "LeisenReimer",
            "NewtonRaphson",
            "MC",
            "BAW",
        )
        if _needs_chain_price and (chain_price is None or chain_price <= 0):
            raise ValueError(
                f"[VolManager:{method}] No usable market price for {ticker} K={K} T={T:.4f} "
                f"({option_type}) -- cannot solve IV without one. No fallback."
            )

        if method == "CRR":
            sigma = brute_force(chain_price, S, K, T, r, option_type == "call", q=q)
            model_price = crr_american_price(
                S, K, T, r, sigma, q, option_type == "call"
            )
            print_reference_block(
                "CRR (American)",
                ticker,
                K,
                T,
                expiry_used,
                chain_iv,
                chain_price,
                sigma,
                S,
                r,
                q,
                option_type,
                data_ts,
                model_price=model_price,
            )
            return sigma

        elif method == "BAW":
            # Barone-Adesi-Whaley -- closed-form American BS approximation.
            # Its OWN IV solver: bisection against baw_american_price (which
            # is a fully analytical American BS closed-form -- no tree, no
            # sim -- so this converges very fast). Not borrowed from either
            # binomial tree.
            sigma = brute_force_baw(
                chain_price, S, K, T, r, q=q, cp=(option_type == "call")
            )
            model_price = baw_american_price(
                S, K, T, r, sigma, q, option_type == "call"
            )
            print_reference_block(
                "Barone-Adesi-Whaley (American BS)",
                ticker,
                K,
                T,
                expiry_used,
                chain_iv,
                chain_price,
                sigma,
                S,
                r,
                q,
                option_type,
                data_ts,
                model_price=model_price,
            )
            return sigma

        elif method == "LeisenReimer":
            sigma = brute_force_lr(chain_price, S, K, T, r, option_type == "call", q=q)
            model_price = leisen_reimer_american_price(
                S, K, T, r, sigma, q, option_type == "call"
            )
            print_reference_block(
                "Leisen-Reimer (American)",
                ticker,
                K,
                T,
                expiry_used,
                chain_iv,
                chain_price,
                sigma,
                S,
                r,
                q,
                option_type,
                data_ts,
                model_price=model_price,
            )
            return sigma

        elif method == "MC":
            # MC's own IV solver: bisection against AmericanLSMPricer's own
            # simulated price (common-random-numbers, fixed seed -- see
            # brute_force_mc's docstring in bruteforceimpliedvol.py). Not
            # borrowed from either binomial tree.
            #
            # P1 FIX: these used to default to a hardcoded 20000/100
            # regardless of what the caller actually prices Greeks with.
            # Callers pass the SAME pricing_config.simulations/steps they
            # use to build the AmericanLSMPricer for price/Greeks (see
            # main.py) so the IV solved here and the price/Greeks computed
            # downstream are from the exact same simulation config -- no
            # more solving IV at 20000/100 and then repricing Greeks at a
            # different (e.g. test) sim/step count. The 20000/100 fallback
            # only fires if a caller doesn't pass one, which is now a bug
            # smell worth fixing at the call site rather than masking here.
            simulations = kwargs.get("simulations", 20000)
            steps = kwargs.get("mc_steps", 100)
            sigma = brute_force_mc(
                chain_price,
                S,
                K,
                T,
                r,
                option_type == "call",
                q=q,
                simulations=simulations,
                steps=steps,
            )
            from MC import AmericanLSMPricer

            model_price = AmericanLSMPricer(
                S,
                K,
                T,
                r,
                q,
                sigma,
                simulations=simulations,
                steps=steps,
                option=option_type,
            ).price()
            print_reference_block(
                "Monte Carlo (LSM)",
                ticker,
                K,
                T,
                expiry_used,
                chain_iv,
                chain_price,
                sigma,
                S,
                r,
                q,
                option_type,
                data_ts,
                model_price=model_price,
            )
            return sigma

        elif method == "NewtonRaphson":
            # Solved against the Leisen-Reimer American price (early exercise
            # included) -- same tree the "Leisen-Reimer" method uses -- not the
            # closed-form European Black-Scholes price. See
            # implied_volatility_nr_american's docstring in NewtonRaphsonIV.py.
            seed = chain_iv if chain_iv is not None else 0.3
            sigma, converged = implied_volatility_nr_american(
                chain_price, S, K, T, r, option_type == "call", q=q, seed=seed
            )
            if not converged:
                raise RuntimeError(
                    f"[VolManager:NewtonRaphson] Did not converge for {ticker} K={K} T={T:.4f} "
                    f"(near-zero vega or no root in [1e-4, 5.0]). No fallback -- fix inputs or investigate."
                )
            model_price = leisen_reimer_american_price(
                S, K, T, r, sigma, q, option_type == "call"
            )
            print_reference_block(
                "Newton-Raphson (American)",
                ticker,
                K,
                T,
                expiry_used,
                chain_iv,
                chain_price,
                sigma,
                S,
                r,
                q,
                option_type,
                data_ts,
                model_price=model_price,
            )
            return sigma

        elif method == "SABR":
            # Each model solves itself, independently, off real market data --
            # no cross-model borrowing, and no substituting a "safer" number
            # when the calibration looks off. SABR used to have a ratio
            # sanity-check that silently swapped in the vendor's raw
            # implied_vol field whenever the calibrated sigma looked too far
            # from it -- that's exactly the kind of fallback that hides a
            # real calibration bug instead of surfacing it. If SABR's fit is
            # bad, this now raises so it can be debugged, not smoothed over.
            beta = kwargs.get("beta", 0.5)
            calibrator = SABRCalibrator(ticker, expiry_date, r=r, q=q, beta=beta)
            calib = calibrator.calibrate()
            # Cache this exact calibration (the one whose sigma actually
            # becomes the reported "SABR" row) so any caller building a
            # smile chart or curve reuses THIS result instead of running a
            # second, independent calibrate() call. SABR's multi-start
            # L-BFGS-B optimization is not guaranteed to land on the same
            # local optimum twice -- a second call for chart purposes could
            # silently plot a DIFFERENT SABR fit than the one in the
            # comparison table. This was exactly that bug (main.py's smile
            # chart re-instantiated and re-calibrated SABR from scratch just
            # to get curve points) -- fixed by reusing this cached result.
            self.last_sabr_calibration = {"calibrator": calibrator, "calib": calib}
            print("\nSABR Calibration Results:")
            print(
                f"Alpha: {calib['alpha']:.4f}, Beta: {calib['beta']:.4f}, Rho: {calib['rho']:.4f}, Nu: {calib['nu']:.4f}, RMSE: {calib['rmse']:.4f}"
            )
            model = SABRModel(
                alpha=calib["alpha"],
                beta=calib["beta"],
                rho=calib["rho"],
                nu=calib["nu"],
            )
            # Hagan's SABR formula is forward-based -- F must be the calibrated
            # forward (S*exp((r-q)*T), what alpha/rho/nu were actually fit
            # against), not spot S.
            sigma_raw = model.get_vol(F=calibrator.forward, K=K, T=T)
            if np.isnan(sigma_raw) or sigma_raw <= 0.0:
                raise RuntimeError(
                    f"[VolManager:SABR] Calibration produced an invalid sigma ({sigma_raw}) for "
                    f"{ticker} K={K} T={T:.4f} (forward={calibrator.forward:.2f}, "
                    f"alpha={calib['alpha']:.4f}, beta={calib['beta']:.4f}, rho={calib['rho']:.4f}, "
                    f"nu={calib['nu']:.4f}, rmse={calib['rmse']:.4f}). This is Hagan's SABR formula "
                    f"going negative at a far-OTM strike -- a known failure mode of the asymptotic "
                    f"expansion under extreme parameters (very high nu / strongly negative rho), often "
                    f"itself downstream of a bad market smile (check the strike/IV range printed above "
                    f"for implausible values -- e.g. an ATM vol over 100% on a normally-quiet name "
                    f"usually means illiquid/wide quotes got into the fit, not a real vol regime). "
                    f"No fallback -- investigate the calibration inputs."
                )
            sigma = sigma_raw
            print_reference_block(
                "SABR",
                ticker,
                K,
                T,
                expiry_used,
                chain_iv,
                chain_price,
                sigma,
                S,
                r,
                q,
                option_type,
                data_ts,
            )
            return sigma

        elif method == "VannaVolga":
            # ATM anchor is VV's OWN market read, not another model's solve
            # and not a silently-substituted default. This used to fall back
            # to the vendor's raw implied_vol field (or 0.3) if the caller
            # didn't pass an atm_vol -- masking the fact that VV's own
            # 25-delta market read failed. Now it requires a real atm_vol
            # from the caller (main.py derives this from get_auto_rr_bf's own
            # vendor chain read) and raises if one wasn't supplied.
            atm_vol = kwargs.get("atm_vol")
            if atm_vol is None or atm_vol <= 0:
                raise ValueError(
                    f"[VolManager:VannaVolga] No valid atm_vol supplied for {ticker} K={K} T={T:.4f} "
                    f"-- Vanna-Volga needs its own market ATM read (get_auto_rr_bf). No fallback."
                )
            rr25 = kwargs.get("rr25", 0.0)
            bf25 = kwargs.get("bf25", 0.0)
            sigma = vanna_volga_vol(S, K, T, r, q, atm_vol, rr25, bf25)
            print(
                f"[VannaVolga] ATM anchor (own market read)={atm_vol:.4f} (rr25={rr25}, bf25={bf25}) -> sigma={sigma:.4f}"
            )
            print_reference_block(
                "Vanna-Volga",
                ticker,
                K,
                T,
                expiry_used,
                chain_iv,
                chain_price,
                sigma,
                S,
                r,
                q,
                option_type,
                data_ts,
            )
            return sigma

        else:
            raise ValueError(f"Unknown method: {method}")
