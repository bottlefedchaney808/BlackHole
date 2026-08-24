"""sabr_market_calib.py -- MARKET-grade SABR smile calibration for Options_Suite.

This is the pricing/backtest-grade SABR calibrator: vega-weighted objective,
finer multi-start grid, and an optional free-beta pass -- exposed as a PURE
function that takes an already-fetched chain-IV map (network-free), so the
Backtests tournament and Options_Suite can calibrate offline.

The fitting core (_fit_sabr_series) is SHARED with
SABRModel.SABRCalibrator.calibrate -- that class delegates to it with
verbose=True (its diagnostic prints / raise-on-total-failure); this module
calls it with verbose=False and returns None on failure. Keeping one core
means the market-grade SABR algorithm lives in exactly one place.

The DEALER-facing SABR fitter is deliberately NOT this module. The dealer
sign layer (Vol_Suite/vol_surface_reference.fit_sabr_reference) is a coarse,
equal-weight, fixed-beta fit tuned for deep-OTM deviation reads; it is kept
untouched behind Vol_Suite/sabr_dealer_calib.py. Nothing in this module
touches the dealer sign path.

Contract mirrors fit_sabr_reference so callers can swap seamlessly:
    fit_sabr_market(chain_iv, forward, T, calibrate_beta=True) -> dict
        {'alpha','beta','rho','nu','rmse','n_points'} | None
chain_iv: {(strike, 'C'|'P'): implied_vol}
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import minimize
from scipy.stats import norm

from Options_Suite.SABRModel import (  # type: ignore
    _sabr_vol_hagan_vec,
    sabr_vol_hagan,
)

# Match SABRModel.SABRCalibrator's pricing-grade grid (5x5 + free-beta 4x3x3),
# not the dealer fitter's coarse 3x3.
_FIXED_RHO_STARTS = (-0.6, -0.3, 0.0, 0.3, 0.6)
_FIXED_NU_STARTS = (0.2, 0.4, 0.6, 0.8, 1.0)
_FREE_BETA_STARTS = (0.3, 0.5, 0.7, 1.0)
_FREE_RHO_STARTS = (-0.5, 0.0, 0.5)
_FREE_NU_STARTS = (0.3, 0.6, 0.9)

BETA_DEFAULT = 0.5
MIN_SABR_POINTS = 6

# Shared sentinel for "this objective evaluation is invalid" (non-finite vol,
# no qualifying vega, or an out-of-bounds parameter guess). Every invalid-
# objective return path must use this exact value so downstream selection
# logic (best_fixed/best_free acceptance) can reliably distinguish a real
# optimizer result from a failed one.
_ERR_SENTINEL = 1e9


def _solve_alpha_for_atm(
    target_atm_vol: float, forward: float, T: float, beta: float, rho: float, nu: float
) -> float:
    """Bisect alpha so Hagan ATM (F==K) matches target_atm_vol exactly.

    Warm-started from the analytic ATM estimate alpha ~= target*F^(1-beta)
    (Hagan ATM vol ~= alpha/F^(1-beta) * (1 + O(T))), which brackets the root
    far tighter than the old [1e-4, 50]x-scale band -- the same root, ~30%
    fewer bisection iterations. The grid-scan fallback is retained for the
    non-monotone (extreme rho/nu/beta) edge case.
    """

    def atm_vol(a):
        return sabr_vol_hagan(forward, forward, T, a, beta, rho, nu)

    alpha_scale = max(target_atm_vol * (forward ** (1 - beta)), 1e-6)
    lo, hi = alpha_scale * 0.1, alpha_scale * 10.0
    v_lo, v_hi = atm_vol(lo), atm_vol(hi)
    if v_hi <= v_lo:
        grid = np.linspace(lo, hi, 400)
        vals = np.array([atm_vol(a) for a in grid])
        return float(grid[int(np.argmin(np.abs(vals - target_atm_vol)))])
    if target_atm_vol <= v_lo:
        return lo
    if target_atm_vol >= v_hi:
        return hi
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        v_mid = atm_vol(mid)
        if abs(v_mid - target_atm_vol) < 1e-7:
            return mid
        if v_mid > target_atm_vol:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def _otm_iv_by_strike(
    chain_iv: dict[tuple[float, str], float], forward: float
) -> dict[float, float]:
    """One IV per strike, keeping only the OTM-vs-forward side per right."""
    by_strike: dict[float, float] = {}
    for (k, right), iv in chain_iv.items():
        if iv <= 0:
            continue
        wants_call = k > forward
        if wants_call and right != "C":
            continue
        if not wants_call and right != "P":
            continue
        by_strike[k] = iv
    return by_strike


def _weighted_error(
    strikes,
    vols,
    forward: float,
    T: float,
    alpha: float,
    beta: float,
    rho: float,
    nu: float,
) -> float:
    """Vega-weighted IV RMSE objective (shared with SABRCalibrator.calibrate).

    Strikes that matter for pricing/P&L (near-ATM) dominate; deep-OTM noise is
    down-weighted. Returns _ERR_SENTINEL on any non-finite vol or no usable vega.
    """
    K = np.asarray(strikes, dtype=float)
    mv = np.asarray(vols, dtype=float)
    sv = _sabr_vol_hagan_vec(forward, K, T, alpha, beta, rho, nu)
    if np.any(np.isnan(sv)) or np.any(np.isinf(sv)):
        return _ERR_SENTINEL
    d1 = (np.log(forward / K) + 0.5 * mv**2 * T) / (mv * np.sqrt(T))
    vega = forward * np.sqrt(T) * norm.pdf(d1)
    w_mask = vega >= 1e-6
    if not np.any(w_mask):
        return _ERR_SENTINEL
    err = float(np.sum(vega[w_mask] * (sv[w_mask] - mv[w_mask]) ** 2))
    w_sum = float(np.sum(vega[w_mask]))
    return err / w_sum if w_sum > 1e-6 else _ERR_SENTINEL


def _fit_sabr_series(
    strikes,
    vols,
    forward: float,
    T: float,
    beta: float = BETA_DEFAULT,
    calibrate_beta: bool = True,
    verbose: bool = False,
    label: str = "",
) -> dict | None:
    """Two-pass multi-start ATM-pinned SABR fit (fixed-beta then free-beta).

    Shared by fit_sabr_market (network-free) and SABRCalibrator.calibrate
    (which fetches its own smile). Callers pre-validate their own minimum
    strike count (fit_sabr_market: MIN_SABR_POINTS, SABRCalibrator: 3).

    alpha is ATM-pinned via bisection; (rho, nu) are fit on a 5x5 multi-start
    grid; if calibrate_beta, a free-beta pass (4x3x3) is kept only when it
    beats the fixed-beta RMSE by >=8% (beta is poorly identified against rho).

    verbose=True reproduces SABRCalibrator's [SABR Debug] prints and raises
    on total failure; verbose=False returns None.
    Returns {'alpha','beta','rho','nu','rmse','n_points'} or None.
    """
    K = np.asarray(strikes, dtype=float)
    mv = np.asarray(vols, dtype=float)
    valid = (K > 0) & (mv > 0)
    K, mv = K[valid], mv[valid]
    if len(K) < 3 or T <= 0:
        if verbose:
            raise RuntimeError(
                f"[SABR] Only {len(K)} strikes available -- cannot calibrate. No fallback."
            )
        return None
    n_points = len(K)
    target_atm_vol = float(mv[int(np.argmin(np.abs(K - forward)))])
    if verbose:
        print(
            f"[SABR Debug] True ATM market vol (nearest strike to forward) = {target_atm_vol:.4f}"
        )

    def obj_fixed(params):
        rho, nu = params
        if not (-0.99 <= rho <= 0.99) or not (0.01 <= nu <= 5.0):
            return _ERR_SENTINEL
        alpha = _solve_alpha_for_atm(target_atm_vol, forward, T, beta, rho, nu)
        return _weighted_error(K, mv, forward, T, alpha, beta, rho, nu)

    # Pass 1: fixed beta, 5x5 multi-start on (rho, nu)
    best_fixed, best_fixed_err = None, float("inf")
    for rho0 in _FIXED_RHO_STARTS:
        for nu0 in _FIXED_NU_STARTS:
            try:
                res = minimize(
                    obj_fixed,
                    [rho0, nu0],
                    method="L-BFGS-B",
                    bounds=[(-0.99, 0.99), (0.01, 5.0)],
                    options={"maxiter": 2000, "ftol": 1e-12},
                )
            except Exception:
                continue
            if (
                res.success
                and np.isfinite(res.fun)
                and res.fun < _ERR_SENTINEL
                and res.fun < best_fixed_err
            ):
                best_fixed_err, best_fixed = res.fun, res

    fixed_result = None
    if best_fixed is not None:
        rho_f, nu_f = best_fixed.x
        alpha_f = _solve_alpha_for_atm(target_atm_vol, forward, T, beta, rho_f, nu_f)
        fixed_result = {
            "alpha": float(alpha_f),
            "beta": float(beta),
            "rho": float(rho_f),
            "nu": float(nu_f),
            "rmse": float(np.sqrt(best_fixed_err)),
            "n_points": n_points,
        }
        if verbose:
            print(
                f"[SABR Debug] Fixed-beta ({beta}) calibration: RMSE={fixed_result['rmse']:.6f}"
            )

    best_result = fixed_result

    if calibrate_beta:

        def obj_free(params):
            b, rho, nu = params
            if (
                not (0.1 <= b <= 1.0)
                or not (-0.99 <= rho <= 0.99)
                or not (0.01 <= nu <= 5.0)
            ):
                return _ERR_SENTINEL
            alpha = _solve_alpha_for_atm(target_atm_vol, forward, T, b, rho, nu)
            return _weighted_error(K, mv, forward, T, alpha, b, rho, nu)

        best_free, best_free_err = None, float("inf")
        for b0 in _FREE_BETA_STARTS:
            for rho0 in _FREE_RHO_STARTS:
                for nu0 in _FREE_NU_STARTS:
                    try:
                        res = minimize(
                            obj_free,
                            [b0, rho0, nu0],
                            method="L-BFGS-B",
                            bounds=[(0.1, 1.0), (-0.99, 0.99), (0.01, 5.0)],
                            options={"maxiter": 2000, "ftol": 1e-12},
                        )
                    except Exception:
                        continue
                    if (
                        res.success
                        and np.isfinite(res.fun)
                        and res.fun < _ERR_SENTINEL
                        and res.fun < best_free_err
                    ):
                        best_free_err, best_free = res.fun, res

        if best_free is not None:
            b_v, rho_v, nu_v = best_free.x
            alpha_v = _solve_alpha_for_atm(target_atm_vol, forward, T, b_v, rho_v, nu_v)
            free_result = {
                "alpha": float(alpha_v),
                "beta": float(b_v),
                "rho": float(rho_v),
                "nu": float(nu_v),
                "rmse": float(np.sqrt(best_free_err)),
                "n_points": n_points,
            }
            if verbose:
                print(
                    f"[SABR Debug] Free-beta calibration: beta={b_v:.4f} RMSE={free_result['rmse']:.6f}"
                )

            # Require a MEANINGFUL RMSE improvement (>=8% relative) before trusting
            # the free-beta result, not just any improvement no matter how tiny.
            # beta is poorly identified against rho from a single smile snapshot --
            # without this guard, the optimizer would happily walk beta all the way
            # to its bound chasing a near-zero RMSE gain (curve-fitting noise).
            IMPROVEMENT_THRESHOLD = (
                0.92  # free-beta RMSE must be <= 92% of fixed-beta RMSE
            )
            if fixed_result is None:
                if verbose:
                    print(
                        f"[SABR Debug] No fixed-beta baseline available; using free-beta result: beta={b_v:.4f}"
                    )
                best_result = free_result
            elif free_result["rmse"] < fixed_result["rmse"] * IMPROVEMENT_THRESHOLD:
                if verbose:
                    print(
                        f"[SABR Debug] Selecting free-beta result (meaningful improvement: "
                        f"{fixed_result['rmse']:.6f} -> {free_result['rmse']:.6f}): beta={b_v:.4f} vs fixed beta={beta}"
                    )
                best_result = free_result
            elif verbose:
                print(
                    f"[SABR Debug] Keeping fixed beta={beta} (free-beta RMSE "
                    f"{free_result['rmse']:.6f} vs fixed {fixed_result['rmse']:.6f} -- "
                    f"not a large enough improvement to trust an unidentified beta)"
                )

    if best_result is None:
        # NO FALLBACK: both the fixed-beta and free-beta multi-start passes
        # failed to converge on ANY restart -- a genuine calibration failure.
        if verbose:
            raise RuntimeError(
                f"[SABR] Calibration failed to converge on any multi-start restart for "
                f"{label or 'ticker'} (target_atm_vol={target_atm_vol:.4f}, {len(K)} strikes). No fallback."
            )
        return None

    if verbose:
        print(
            f"[SABR Debug] Calibration converged! Final beta={best_result['beta']:.4f} RMSE={best_result['rmse']:.6f}"
        )
    return best_result


def fit_sabr_market(
    chain_iv: dict[tuple[float, str], float],
    forward: float,
    T: float,
    calibrate_beta: bool = True,
    beta: float = BETA_DEFAULT,
) -> dict | None:
    """Market-grade ATM-pinned SABR calibration over the whole OTM strip.

    vega-weighted (like SABRModel.SABRCalibrator, unlike the dealer fitter's
    equal-weight objective) so strikes that matter for pricing/P&L dominate.
    alpha is ATM-pinned via bisection; (rho, nu) are fit on a 5x5 multi-start
    grid; if calibrate_beta, a free-beta pass (4x3x3) is kept only when it
    beats the fixed-beta RMSE by >=8%. Delegates the actual fit to the shared
    _fit_sabr_series core (also used by SABRCalibrator.calibrate).

    Returns None if there aren't enough OTM strikes or no restart converges.
    """
    by_strike = _otm_iv_by_strike(chain_iv, forward)
    if len(by_strike) < MIN_SABR_POINTS or T <= 0:
        return None
    strikes = sorted(by_strike.keys())
    vols = [by_strike[k] for k in strikes]
    return _fit_sabr_series(
        strikes,
        vols,
        forward,
        T,
        beta=beta,
        calibrate_beta=calibrate_beta,
        verbose=False,
    )


__all__ = ["BETA_DEFAULT", "fit_sabr_market", "sabr_vol_hagan"]
