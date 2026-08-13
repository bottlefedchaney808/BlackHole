#!/usr/bin/env python3
"""vol_surface_reference.py

Layer 1a of the dealer-positioning v2 design (see
DEALER_POSITIONING_V2_DESIGN.md §2/§3) -- per-strike deviation of the live
market IV surface from a "frictionless" reference curve, used as a
continuous, day-specific proxy for net buying (rich) vs. net selling
(cheap) pressure at each strike.

Why this exists, precisely: Layer 1b's replication-implied sign
(`replication_reference.py`) applies one UNIFORM sign across the entire OTM
strip (dealer short every OTM leg, both wings) -- a direct, unavoidable
consequence of the recursion's own weights being non-negative by
construction (see replication_reference._resolve_sign's docstring / this
module's discovery via the live SPY and QQQ runs on 2026-07-22). That's
correct as far as it goes, but real OTM open interest isn't all the same
kind of flow: some of it is "customer bought convexity from a dealer"
(matches Layer 1b, dealer short), but a lot of real OTM OI -- especially on
heavily-overwritten names -- comes from the OPPOSITE direction (covered-call
writers, cash-secured-put sellers selling premium TO dealers, making the
dealer LONG at those specific strikes). Layer 1b structurally cannot
express that; only a signal that can differ strike-by-strike can.

Per Jason (2026-07-22): institutional flow, not retail, is what's actually
big enough to bend a strike's IV away from a smooth reference -- retail
flow is too diffuse to leave a footprint on the smile. So "is this strike
trading rich or cheap relative to its neighbors" is plausibly reading
genuine institutional supply/demand imbalance at that specific strike, not
noise -- which is the whole premise this module rests on.

Deliberately lightweight for a first version: a quadratic fit in
log-moneyness over NEAR-ATM strikes only (where two-sided retail/
institutional flow roughly cancels, so the fit itself isn't already
flow-distorted), not full SABR/Vanna-Volga.

UPDATE (2026-07-22, live SPY validation): the quadratic-only version above
was caught red-handed. Recomputing its own fit on real SPY chain data
(20260930 expiry) and printing deviation strike-by-strike showed the
deviation was a silky-smooth, monotonic function of moneyness across
HUNDREDS of strikes on both wings, not spiky/isolated at specific strikes
-- e.g. put deviation drifted smoothly from -0.50 vol points at $445
(x=-0.544 log-moneyness) up through ~0 near $675, while the fit itself was
only ever anchored on points within +/-0.15. That's the signature of a
quadratic's x^2 term diverging once evaluated 3.5x past its own fit
window, not real per-strike overwriting flow -- real structured flow
should look like isolated jumps at specific popular strikes, not a smooth
gradient covering the entire far wing. The net-gamma sign flip
('DAMPENING' vs 'AMPLIFYING') driven by that artifact was therefore not
trustworthy as-is.

Fix: swap in the Hagan SABR formula (already built, calibrated, and
confirmed working in Monte-Carlo-American-Pricer-Greeks/SABRModel.py) as
the reference curve when a forward and time-to-expiry are available. SABR
is fit via a vega-weighted, ATM-pinned least squares across the WHOLE
observed OTM strip (not just a near-ATM band) using a bounded, well-behaved
closed-form functional form -- it does not diverge on extrapolation the way
a bare polynomial does, so a genuine per-strike anomaly shows up as a local
residual against an otherwise-good global fit instead of being swamped by
(or confused with) fit-shape error. The quadratic fitter is kept as the
fallback path when no forward/T is supplied (preserves the original
constructor signature and all of vol_surface_reference's original tests)
or when there aren't enough OTM points to trust a SABR fit.
"""
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.stats import norm
from scipy.optimize import minimize

# Near-ATM band (in log-moneyness) used to fit the QUADRATIC fallback curve
# -- +/-15% by default. Points outside this band are NOT used to fit the
# curve (that's the whole idea: anchor on the region least likely to be
# flow-distorted) but deviation IS still computed for them against the
# fitted curve's extrapolation. NOTE: this is exactly the mechanism that
# produced the smooth-extrapolation artifact documented above -- the
# quadratic path is now a fallback, not the primary fitter.
NEAR_ATM_BAND = 0.15

# Minimum number of near-ATM points required to trust a quadratic fit --
# below this, there's not enough data to distinguish a real curve from
# fitting noise.
MIN_FIT_POINTS = 5

# Minimum number of OTM strikes (across the WHOLE chain, not just near-ATM)
# required to trust a SABR fit -- alpha is ATM-pinned (not free) and beta is
# fixed, so only (rho, nu) are actually being fit; 6 points is enough to
# constrain a 2-parameter fit without over-trusting fitting noise.
MIN_SABR_POINTS = 6

# beta fixed (not calibrated) -- matches SABRModel.SABRCalibrator's own
# fixed-beta baseline. Freeing beta from a single smile snapshot is poorly
# identified against rho (see SABRModel.py's calibrate() docstring); this
# module doesn't need publish-quality precision, just a globally
# well-behaved reference curve, so the cheaper fixed-beta fit is enough.
SABR_BETA = 0.5

# Coarse (rho, nu) multi-start grid for the vega-weighted SABR calibration.
# This runs once per expiry inside dealer_positioning.py's per-expiry loop
# (called up to ~2x per expiry across a multi-day/2-year window), so the
# grid is deliberately small -- a handful of L-BFGS-B starts, not the finer
# 5x5 grid SABRModel.SABRCalibrator uses for actual pricing/calibration
# work. Precision of the exact (rho, nu) isn't the point here; a fast,
# non-diverging reference curve is.
_RHO_STARTS = (-0.5, 0.0, 0.5)
_NU_STARTS = (0.3, 0.6, 0.9)

# Reference-curve fitter selection (Jason 2026-08-12 "make all things smile
# use SVI", re-affirmed 2026-08-13: SVI default, Option B). Default 'svi' =
# SSVI via the reusable svi_rp module. 'sabr' = the Hagan-SABR fitter, and
# 'quadratic' = near-ATM quadratic -- both kept reachable via this env toggle
# so we can flip back without code changes if SVI misbehaves on a chain.
#   VOL_SURFACE_FITTER=svi|sabr|quadratic
_VALID_FITTERS = ("svi", "sabr", "sabr_market", "quadratic")

# IV dead-band (in volatility POINTS, e.g. 0.01 == 1.0 vol point) applied when
# resolving a strike's dealer-direction sign from its deviation. The pre-hardening
# binary rule mapped dev>0 -> -1.0 (dealer short / rich) and dev<0 -> +1.0 (dealer
# long / cheap) with a ZERO threshold. That is fragile: a strike whose IV crosses
# the reference by a few basis points -- well below the bid/ask spread and
# cent-rounding noise floor of a real IV quote -- would flip its ENTIRE
# dealer-gamma contribution sign, which can swing net_gamma and the gamma-flip
# level. Any |dev| <= IV_DEADBAND_VOL is therefore treated as NO confident
# directional-flow read: resolve_vol_surface_sign returns 0.0 for it (downstream
# falls back to the Layer 1b default -1.0), so noise cannot flip a strike's sign.
# Only |dev| strictly greater than the dead-band asserts a direction.
#
# NOTE (2026-08-13): this dead-band EXISTS in the MIGRATED reference tree and is
# required to reproduce the reference vannaflow read (SPY live+vannaflow -> +254K
# LONG, the 08-11 flip). The current tree had dropped it, which silently destroyed
# the vannaflow accumulation signal (SPY -> -128K SHORT). Restored.
IV_DEADBAND_VOL = 0.01


def _fitter() -> str:
    """Lazily read VOL_SURFACE_FITTER (per-call, not import-time) so the env
    toggle works at runtime / in tests, not just before module load."""
    import os
    val = os.environ.get("VOL_SURFACE_FITTER", "svi").strip().lower()
    return val if val in _VALID_FITTERS else "svi"


@dataclass
class VolSurfaceReference:
    ticker: str
    spot: float
    fit_coeffs: Tuple[float, float, float]   # (a, b, c) for a*x^2 + b*x + c, x = ln(K/spot) -- quadratic fallback only
    n_fit_points: int
    deviation_by_strike: Dict[Tuple[float, str], float] = field(default_factory=dict)
    reference_iv_by_strike: Dict[Tuple[float, str], float] = field(default_factory=dict)
    fitter: str = 'quadratic'                # 'sabr' or 'quadratic' -- which path actually produced this reference
    sabr_params: Optional[dict] = None       # {'alpha','beta','rho','nu','rmse'} when fitter == 'sabr'


def _log_moneyness(strike: float, spot: float) -> float:
    return math.log(strike / spot)


# ---------------------------------------------------------------------------
# SABR (Hagan) reference curve -- primary fitter when forward/T are known.
# Math ported from Monte-Carlo-American-Pricer-Greeks/SABRModel.py so this
# module stays a self-contained, network-free dependency (that file also
# pulls in ThetaDataController/MC.py for its own live-fetch convenience
# methods, which vol_surface_reference.py has no need for -- callers here
# already have chain_iv/forward/T in hand from dealer_positioning.py).
# ---------------------------------------------------------------------------

def sabr_vol_hagan(F: float, K: float, T: float, alpha: float, beta: float,
                    rho: float, nu: float) -> float:
    """Hagan et al. (2002) SABR implied-vol asymptotic formula."""
    eps = 1e-10
    logFK = math.log(F / K + eps)
    if abs(F - K) < eps:
        term1 = alpha / (F ** (1 - beta))
        term2 = 1 + (
            ((1 - beta) ** 2 / 24) * (alpha ** 2 / (F ** (2 - 2 * beta))) +
            (rho * beta * nu * alpha) / (4 * F ** (1 - beta)) +
            (2 - 3 * rho ** 2) * nu ** 2 / 24
        ) * T
        return term1 * term2
    z = (nu / (alpha + eps)) * (F * K) ** ((1 - beta) / 2) * logFK
    x_z = math.log((math.sqrt(1 - 2 * rho * z + z ** 2 + eps) + z - rho) / (1 - rho + eps))
    num = alpha
    denom = (F * K) ** ((1 - beta) / 2) * (1 + ((1 - beta) ** 2 / 24) * logFK ** 2 + ((1 - beta) ** 4 / 1920) * logFK ** 4)
    z_over_xz = z / (x_z + eps)
    term3 = 1 + (
        ((1 - beta) ** 2 / 24) * (alpha ** 2 / (F * K) ** (1 - beta)) +
        (rho * beta * nu * alpha) / (4 * (F * K) ** ((1 - beta) / 2)) +
        (2 - 3 * rho ** 2) * nu ** 2 / 24
    ) * T
    return (num / (denom + eps)) * z_over_xz * term3


def _bs_vega_sabr(F: float, K: float, T: float, sigma: float) -> float:
    if sigma <= 0 or T <= 0:
        return 0.0
    d1 = (math.log(F / K) + 0.5 * sigma ** 2 * T) / (sigma * math.sqrt(T))
    return F * math.sqrt(T) * norm.pdf(d1)


def _solve_alpha_for_atm(target_atm_vol: float, F: float, T: float,
                          beta: float, rho: float, nu: float) -> float:
    """Bisect for the alpha that makes the Hagan ATM (F==K) formula match
    target_atm_vol exactly -- ATM vol is monotonically increasing in alpha
    for realistic parameter ranges, so this converges reliably. Ported
    directly from SABRModel.SABRCalibrator._solve_alpha_for_atm.
    """
    def atm_vol(a):
        return sabr_vol_hagan(F, F, T, a, beta, rho, nu)

    alpha_scale = max(target_atm_vol * (F ** (1 - beta)), 1e-6)
    lo, hi = alpha_scale * 1e-4, alpha_scale * 50.0
    v_lo, v_hi = atm_vol(lo), atm_vol(hi)
    if v_hi <= v_lo:
        grid = np.linspace(lo, hi, 200)
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


def _otm_iv_by_strike(chain_iv: Dict[Tuple[float, str], float], forward: float
                       ) -> Dict[float, float]:
    """Collapse chain_iv to one IV per strike, keeping only the OTM-vs-forward
    side (put IV for K<=forward, call IV for K>forward) -- same selection
    SABRModel.SABRCalibrator._fetch_and_prepare uses, and for the same
    reason: OTM quotes are consistently more liquid/reliable, and a strike's
    call/put IV can disagree slightly (put-call parity is rarely exact on
    real quotes), which would otherwise double-count/distort the fit.
    """
    by_strike: Dict[float, float] = {}
    for (k, right), iv in chain_iv.items():
        if iv <= 0:
            continue
        wants_call = k > forward
        if wants_call and right != 'C':
            continue
        if not wants_call and right != 'P':
            continue
        by_strike[k] = iv
    return by_strike


def fit_sabr_reference(chain_iv: Dict[Tuple[float, str], float], forward: float,
                        T: float, beta: float = SABR_BETA) -> Optional[dict]:
    """ATM-pinned SABR calibration across the WHOLE observed OTM strip (not
    just a near-ATM band -- SABR's closed-form asymptotic formula is
    well-behaved everywhere, unlike a bare polynomial, so it doesn't need to
    restrict itself to a "safe" fitting region the way the quadratic
    fallback does). Returns None if there aren't enough OTM strikes to trust
    a fit.

    Deliberately UNWEIGHTED (equal weight per strike in IV space), unlike
    SABRModel.SABRCalibrator's own vega-weighted fit. This is not an
    oversight -- it's the fix for a second bug this module's SABR upgrade
    surfaced on synthetic data (see tests/test_vol_surface_reference.py's
    "extrapolation artifact" section): vega decays sharply away from the
    money, so a vega-weighted objective barely constrains the deep-OTM
    strikes at all -- multiple very different (rho, nu) pairs can achieve
    nearly identical *weighted* error while diverging by ~2x in actual IV
    forty-plus percent OTM (confirmed via a dense 40x40 grid scan: the
    "best" vega-weighted fit had LOWER weighted error than the true
    generating parameters, yet was wrong by 0.05 vol points -- about half
    the true value -- at a strike 90% above spot). That weighting choice
    makes sense for SABRModel.SABRCalibrator's own purpose (pricing/hedging,
    where vega-weighting correctly prioritizes strikes that matter most for
    P&L), but it's the wrong choice here: Layer 1a specifically needs to
    read deviations at deep-OTM strikes (that's exactly where covered-call/
    cash-secured-put overwriting programs concentrate), so the reference
    curve needs to be well-identified there, not discounted. Equal-weighting
    recovered the true generating (rho, nu) almost exactly on the same
    synthetic test where vega-weighting aliased to a badly wrong wing fit.
    """
    by_strike = _otm_iv_by_strike(chain_iv, forward)
    if len(by_strike) < MIN_SABR_POINTS or T <= 0:
        return None

    strikes = np.array(sorted(by_strike.keys()))
    vols = np.array([by_strike[k] for k in strikes])

    atm_idx = int(np.argmin(np.abs(strikes - forward)))
    target_atm_vol = float(vols[atm_idx])

    def unweighted_error(params):
        rho, nu = params
        if not (-0.99 <= rho <= 0.99) or not (0.01 <= nu <= 5.0):
            return 1e9
        alpha = _solve_alpha_for_atm(target_atm_vol, forward, T, beta, rho, nu)
        err = 0.0
        for K, mv in zip(strikes, vols):
            try:
                sv = sabr_vol_hagan(forward, float(K), T, alpha, beta, rho, nu)
                if math.isnan(sv) or math.isinf(sv):
                    return 1e9
                err += (sv - mv) ** 2
            except (ValueError, ZeroDivisionError, OverflowError):
                return 1e9
        return err / len(strikes)

    best_res, best_err = None, float('inf')
    for rho0 in _RHO_STARTS:
        for nu0 in _NU_STARTS:
            res = minimize(unweighted_error, [rho0, nu0], method='L-BFGS-B',
                            bounds=[(-0.99, 0.99), (0.01, 5.0)],
                            options={'maxiter': 500, 'ftol': 1e-10})
            if res.fun < best_err:
                best_err, best_res = res.fun, res

    if best_res is None or best_err >= 1e9:
        return None

    rho_v, nu_v = best_res.x
    alpha_v = _solve_alpha_for_atm(target_atm_vol, forward, T, beta, rho_v, nu_v)
    return {
        'alpha': float(alpha_v), 'beta': float(beta), 'rho': float(rho_v),
        'nu': float(nu_v), 'rmse': float(math.sqrt(best_err)), 'n_points': len(by_strike),
    }


def fit_svi_reference(chain_iv: Dict[Tuple[float, str], float], forward: float,
                      T: float) -> Optional[dict]:
    """SSVI/Gatheral-Jacquier reference fit via the reusable svi_rp module.

    Uses the ROBUST full-SVI least-squares fit (`svi_rp.calibrate_svi`, the
    2026-08-13 flat-smile fix) rather than the exact 3-observable SSVI
    construction -- the exact construction saturates flat on a steep equity put
    skew (measured on real SPY: reference 0.26 vs market 0.65 at K=300), which
    would badly distort the dealer sign resolver's rich/cheap deviation.

    Returns a params dict the caller uses to price per-strike reference IV via
    `SviRpReference.sigma_ref` (carried in `_ref`), or None when the fit can't
    run (thin chain / no OTM body / import failure) so the caller falls through
    to SABR or quadratic.
    """
    try:
        import svi_rp
    except Exception:
        return None
    # OTM-only body (matches the SABR fitter's convention).
    otm = {}
    for (k, right), iv in chain_iv.items():
        if iv is None or iv <= 0:
            continue
        if (right == 'C' and k > forward) or (right == 'P' and k < forward):
            otm[(k, right)] = float(iv)
    if len(otm) < MIN_SABR_POINTS or T <= 0:
        return None
    try:
        ref = svi_rp.calibrate_svi(otm, float(forward), float(T))
    except Exception:
        return None
    return {
        'theta_t': ref.theta_t, 'sigma_atm': ref.sigma_atm,
        'psi_t': ref.psi_t, 'p_t': ref.p_t,
        'phi': ref.phi, 'rho': ref.rho,
        'sigma_swap': ref.sigma_swap, 'K_var': ref.K_var,
        'n_points': len(otm), '_ref': ref,
    }


def fit_reference_curve(chain_iv: Dict[Tuple[float, str], float], spot: float,
                         near_atm_band: float = NEAR_ATM_BAND
                         ) -> Optional[Tuple[float, float, float]]:
    """Fit iv ~= a*x^2 + b*x + c, x = ln(K/spot), using only strikes within
    +/- near_atm_band of spot in log-moneyness. Returns None if there
    aren't enough near-ATM points to trust the fit (caller should fall back
    to "no deviation signal available" rather than fit garbage).
    """
    xs, ys = [], []
    for (k, right), iv in chain_iv.items():
        if iv <= 0:
            continue
        x = _log_moneyness(k, spot)
        if abs(x) <= near_atm_band:
            xs.append(x)
            ys.append(iv)
    if len(xs) < MIN_FIT_POINTS:
        return None
    coeffs = np.polyfit(xs, ys, deg=2)
    return float(coeffs[0]), float(coeffs[1]), float(coeffs[2])


def _reference_iv(coeffs: Tuple[float, float, float], x: float) -> float:
    a, b, c = coeffs
    return a * x * x + b * x + c


def compute_vol_surface_reference(ticker: str, chain_iv: Dict[Tuple[float, str], float],
                                   spot: float, forward: Optional[float] = None,
                                   T: Optional[float] = None,
                                   near_atm_band: float = NEAR_ATM_BAND
                                   ) -> Optional[VolSurfaceReference]:
    """Fit the reference curve and compute per-strike deviation for every
    strike/right in chain_iv (not just the near-ATM fitting points).

    deviation(K, right) = IV_market(K, right) - IV_reference(K)

    Positive deviation = trading RICH vs. the frictionless reference = net
    BUYING pressure = dealer assumed SHORT there (matches Layer 1b's
    default). Negative deviation = trading CHEAP = net SELLING pressure =
    dealer assumed LONG there (flips Layer 1b's default) -- e.g. a
    strike that's the target of a covered-call overwriting program should
    show up cheap (extra supply pressing IV down) relative to its
    neighbors, exactly the case Layer 1b alone cannot express.

    Fitter selection: `VOL_SURFACE_FITTER` (default 'svi', per Jason 2026-08-12/13
    "make all things smile use SVI" + Option B) picks the reference curve when
    `forward`/`T` are supplied and there are enough OTM strikes. 'svi' fits the
    robust full-SVI curve via the reusable svi_rp module (see fit_svi_reference --
    this is the flat-smile fix, 2026-08-13); 'sabr' fits the ATM-pinned Hagan-SABR
    curve; 'quadratic' uses the near-ATM quadratic. Each can fall through to the
    next (or to quadratic) when forward/T aren't given (preserves the original
    call signature) or when there isn't enough OTM data to trust a fit.

    Returns None if no fitter can produce a trustworthy reference curve (caller
    should fall back to Layer 1b alone / v1 for this ticker/day rather than trust
    a garbage fit).
    """
    if forward is not None and T is not None and T > 0:
        fitter = _fitter()
        params = None
        if fitter == 'svi':
            params = fit_svi_reference(chain_iv, forward, T)
        if params is None and fitter in ('svi', 'sabr'):
            params = fit_sabr_reference(chain_iv, forward, T)
        if params is None and fitter == 'sabr_market':
            # The FIXED market-grade SABR (Options_Suite/sabr_market_calib):
            # vega-weighted, 5x5 grid, free-beta pass. This is the one Jason
            # had fixed from the ATM-pinned fit_sabr_reference. Returns the
            # same {alpha,beta,rho,nu,rmse,n_points} shape as fit_sabr_reference.
            try:
                from Options_Suite import sabr_market_calib
                params = sabr_market_calib.fit_sabr_market(
                    chain_iv, forward, T, calibrate_beta=True)
            except Exception:
                params = fit_sabr_reference(chain_iv, forward, T)
            if params is None:
                params = fit_sabr_reference(chain_iv, forward, T)
        if params is not None:
            if params.get('_ref') is not None:
                # SVI path: price reference IV via the SSVI/SVI object.
                ref_obj = params['_ref']
                deviation_by_strike: Dict[Tuple[float, str], float] = {}
                reference_iv_by_strike: Dict[Tuple[float, str], float] = {}
                for (k, right), iv in chain_iv.items():
                    if iv <= 0:
                        continue
                    ref_iv = ref_obj.sigma_ref(k)
                    reference_iv_by_strike[(k, right)] = ref_iv
                    deviation_by_strike[(k, right)] = iv - ref_iv
                return VolSurfaceReference(
                    ticker=ticker, spot=spot, fit_coeffs=(0.0, 0.0, 0.0),
                    n_fit_points=params['n_points'],
                    deviation_by_strike=deviation_by_strike,
                    reference_iv_by_strike=reference_iv_by_strike,
                    fitter='svi', sabr_params=params,
                )
            # SABR path.
            deviation_by_strike: Dict[Tuple[float, str], float] = {}
            reference_iv_by_strike: Dict[Tuple[float, str], float] = {}
            for (k, right), iv in chain_iv.items():
                if iv <= 0:
                    continue
                ref_iv = sabr_vol_hagan(forward, k, T, params['alpha'],
                                        params['beta'], params['rho'],
                                        params['nu'])
                reference_iv_by_strike[(k, right)] = ref_iv
                deviation_by_strike[(k, right)] = iv - ref_iv

            return VolSurfaceReference(
                ticker=ticker, spot=spot, fit_coeffs=(0.0, 0.0, 0.0),
                n_fit_points=params['n_points'],
                deviation_by_strike=deviation_by_strike,
                reference_iv_by_strike=reference_iv_by_strike,
                fitter=fitter, sabr_params=params,
            )
        # SVI/SABR couldn't run -- fall through to the quadratic path below
        # rather than give up entirely.

    coeffs = fit_reference_curve(chain_iv, spot, near_atm_band)
    if coeffs is None:
        return None

    n_fit_points = sum(1 for (k, _r), iv in chain_iv.items()
                       if iv > 0 and abs(_log_moneyness(k, spot)) <= near_atm_band)

    deviation_by_strike: Dict[Tuple[float, str], float] = {}
    reference_iv_by_strike: Dict[Tuple[float, str], float] = {}
    for (k, right), iv in chain_iv.items():
        if iv <= 0:
            continue
        x = _log_moneyness(k, spot)
        ref_iv = _reference_iv(coeffs, x)
        reference_iv_by_strike[(k, right)] = ref_iv
        deviation_by_strike[(k, right)] = iv - ref_iv

    return VolSurfaceReference(
        ticker=ticker, spot=spot, fit_coeffs=coeffs, n_fit_points=n_fit_points,
        deviation_by_strike=deviation_by_strike, reference_iv_by_strike=reference_iv_by_strike,
        fitter='quadratic',
    )


def resolve_vol_surface_sign(ref: VolSurfaceReference, strike: float, right: str) -> float:
    """+1.0 (dealer long, cheap/net-selling) or -1.0 (dealer short,
    rich/net-buying) for this specific strike/right, or 0.0 if this
    strike/right had no usable deviation (e.g. IV was missing/zero for it).

    This is the piece that can differ strike-by-strike within the same
    OTM side -- unlike Layer 1b's flat -1, which cannot, by construction.
    """
    dev = ref.deviation_by_strike.get((strike, right))
    if dev is None:
        return 0.0
    if dev > IV_DEADBAND_VOL:
        return -1.0
    if dev < -IV_DEADBAND_VOL:
        return 1.0
    return 0.0


if __name__ == "__main__":
    # Quick self-check with a synthetic smile that has a deliberate
    # "overwriting program" dip at one strike -- confirms the mechanism
    # (not live data; run from the project root for a real ticker via
    # dealer_positioning.py's sign_model='vol_surface_replication').
    spot = 100.0
    chain = {}
    for k in range(70, 131):
        x = math.log(k / spot)
        base_iv = 0.20 + 0.30 * x * x - 0.05 * x   # smooth smile shape
        chain[(float(k), 'C' if k >= spot else 'P')] = max(base_iv, 0.05)
    # Deliberately push $110's IV down (cheap) -- simulating heavy call
    # overwriting supply at that strike.
    chain[(110.0, 'C')] = max(chain[(110.0, 'C')] - 0.08, 0.02)

    print("--- quadratic fallback path (no forward/T given) ---")
    ref = compute_vol_surface_reference("SELFTEST", chain, spot)
    print(f"fitter: {ref.fitter}, fit points: {ref.n_fit_points}, coeffs: {ref.fit_coeffs}")
    print(f"deviation at $110 C (should be negative/cheap): {ref.deviation_by_strike[(110.0,'C')]:.4f}")
    print(f"sign at $110 C (should be +1.0, dealer long): {resolve_vol_surface_sign(ref, 110.0, 'C')}")
    print(f"deviation at $105 C (should be near the smooth curve): {ref.deviation_by_strike[(105.0,'C')]:.4f}")
    print(f"sign at $105 C: {resolve_vol_surface_sign(ref, 105.0, 'C')}")

    print("\n--- SABR path (forward/T given) -- the fix ---")
    forward = spot  # r=q=0 for this synthetic self-test
    T = 0.25
    ref_sabr = compute_vol_surface_reference("SELFTEST", chain, spot, forward=forward, T=T)
    print(f"fitter: {ref_sabr.fitter}, fit points: {ref_sabr.n_fit_points}, params: {ref_sabr.sabr_params}")
    print(f"deviation at $110 C (should still flip, isolated to this strike): "
          f"{ref_sabr.deviation_by_strike[(110.0,'C')]:.4f}  sign={resolve_vol_surface_sign(ref_sabr, 110.0, 'C')}")
    print(f"deviation at $105 C (undistorted neighbor -- should be small): "
          f"{ref_sabr.deviation_by_strike[(105.0,'C')]:.4f}  sign={resolve_vol_surface_sign(ref_sabr, 105.0, 'C')}")
    print(f"deviation at $70 P (far wing, undistorted -- should NOT show the old "
          f"quadratic's huge smooth extrapolation blow-up): "
          f"{ref_sabr.deviation_by_strike[(70.0,'P')]:.4f}  sign={resolve_vol_surface_sign(ref_sabr, 70.0, 'P')}")
