import numpy as np
from typing import Optional, Tuple
from market_data import MarketDataController
from scipy.integrate import quad
from scipy.optimize import minimize
from scipy.stats import norm
from NewtonRaphsonIV import implied_volatility_nr, black_scholes_func
# Reuse MC.py's GPU/CPU array-backend detection (xp = cupy if a real, working
# CUDA device is present, else numpy -- see that module's docstring for the
# "cheap real op, not just an import check" verification) rather than
# duplicating it. Before this, every array op in this file was hardcoded to
# `np.`, so the Heston path simulation below never ran on GPU even when
# cupy/CUDA were available and MC.py's own GBM simulation was using them.
from MC import xp, GPU_ACTIVE, _to_scalar
import time


# Heston LSM pricer adapted to be callable from main.py
def _simulate_heston_paths(S0: float, V0: float, r: float, kappa: float, theta: float,
                           vol_sigma: float, rho: float, T: float, steps: int, sims: int,
                           seed: Optional[int] = None, q: float = 0.0,
                           z1_pre=None, z_prime_pre=None):
    """Simulate Heston (S, V) paths via full-truncation Euler discretization.

    Unlike MC.py's plain-GBM path generation, this loop over `steps` is NOT a
    candidate for cumsum-style vectorization: the variance process V is a
    CIR-type mean-reverting diffusion where each step's own diffusion
    coefficient (sqrt(V[t-1])) depends nonlinearly on the previous step's
    simulated value, and S's drift/vol in turn depend on that same path-
    dependent V. That's a genuine sequential recurrence, not an i.i.d. sum of
    log-returns -- so the Python-level step loop stays. What DOES change here
    is that every array op below now goes through `xp` (numpy or cupy) so
    this loop runs on GPU when available -- with sims in the thousands, each
    of the `steps` sequential iterations is still a large, fully parallel
    elementwise op across the simulation dimension, which is exactly the
    workload a GPU is good at even though the step dimension itself can't be
    parallelized away.
    """
    dt = T / steps
    S = xp.zeros((steps + 1, sims))
    V = xp.zeros((steps + 1, sims))
    S[0, :] = S0
    V[0, :] = V0

    # CRN support: caller can pass pre-generated z1 and z_prime (the two
    # INDEPENDENT standard-normal arrays that generate the correlated Heston
    # shocks). The correlation `rho` is applied here, so bumping rho with the
    # same z1/z_prime shifts only the correlation structure -- no fresh
    # randomness is drawn between bumps. This is what makes heston_all_greeks
    # give clean finite-difference derivatives instead of MC noise.
    if z1_pre is not None and z_prime_pre is not None:
        z1 = z1_pre
        z_prime = z_prime_pre
    else:
        if seed is not None:
            xp.random.seed(seed)
        z1 = xp.random.standard_normal((steps, sims))
        z_prime = xp.random.standard_normal((steps, sims))
    z2 = rho * z1 + xp.sqrt(1 - rho ** 2) * z_prime

    for t in range(1, steps + 1):
        vol = xp.sqrt(xp.maximum(V[t - 1, :], 0))
        # risk-neutral log-diffusion drift: (r - q - 0.5*vol^2)*dt
        drift = (r - q - 0.5 * vol ** 2) * dt
        S[t, :] = S[t - 1, :] * xp.exp(drift + vol * xp.sqrt(dt) * z1[t - 1, :])
        # full-truncation Euler update for variance to improve stability
        V_next = V[t - 1, :] + kappa * (theta - xp.maximum(V[t - 1, :], 0)) * dt + vol_sigma * xp.sqrt(xp.maximum(V[t - 1, :], 0)) * xp.sqrt(dt) * z2[t - 1, :]
        V[t, :] = xp.maximum(1e-12, V_next)

    return S, V


def _heston_vander_lstsq(X, Y, degree):
    """xp (numpy/cupy) Vandermonde least-squares continuation-value
    regression, with a CPU numpy fallback if the active cupy build's
    vander/lstsq doesn't behave as expected -- same safety net, and for the
    same reason, as MC.AmericanLSMPricer._polyfit_polyval (this GPU path was
    written/tested without a real CUDA device attached)."""
    try:
        A = xp.vander(X, N=degree + 1)
        coeffs, *_ = xp.linalg.lstsq(A, Y, rcond=None)
        return A.dot(coeffs)
    except Exception:
        X_cpu = X.get() if hasattr(X, 'get') else np.asarray(X)
        Y_cpu = Y.get() if hasattr(Y, 'get') else np.asarray(Y)
        A_cpu = np.vander(X_cpu, N=degree + 1)
        coeffs, *_ = np.linalg.lstsq(A_cpu, Y_cpu, rcond=None)
        cont_cpu = A_cpu.dot(coeffs)
        return xp.asarray(cont_cpu) if xp is not np else cont_cpu


def heston_lsm_price(S0: Optional[float] = None, K: Optional[float] = None, T: Optional[float] = None,
                     r: Optional[float] = None, q: Optional[float] = None, V0: Optional[float] = None,
                     kappa: float = 1.5, theta: float = 0.04, vol_sigma: float = 0.3, rho: float = -0.3,
                     sims: int = 10000, steps: int = 200, option: str = 'call', ticker: Optional[str] = None,
                     use_market_data: bool = True, seed: Optional[int] = 42) -> float:
    """
    Price an American option using Heston dynamics + Longstaff-Schwartz.
    If ticker is provided and use_market_data is True, market parameters will be pulled from MarketDataController.get_pricing_parameters.
    Returns the discounted option price (float).
    """
    option = option.lower()
    if option not in ['call', 'put']:
        raise ValueError("option must be 'call' or 'put'")

    if use_market_data and ticker is not None:
        md = MarketDataController()
        params = md.get_pricing_parameters(ticker, K if K is not None else 0.0, T if T is not None else 0.25)
        S0 = params.get('S', S0)
        K = params.get('K', K)
        T = params.get('T', T)
        r = params.get('r', r)
        q = params.get('q', q)
        if V0 is None:
            sigma_est = params.get('sigma', vol_sigma)
            V0 = sigma_est ** 2

    # Validate inputs
    if any(v is None for v in [S0, K, T, r, V0]):
        raise ValueError("Missing required pricing inputs")
    if q is None:
        q = 0.0

    # simulate paths -- runs on GPU (xp = cupy) when a working CUDA device
    # was detected at import time (see MC.py), CPU numpy otherwise.
    S_paths, V_paths = _simulate_heston_paths(S0, V0, r, kappa, theta, vol_sigma, rho, T, steps, sims, seed=seed, q=q)

    # payoff at maturity
    if option == 'call':
        payoff = xp.maximum(S_paths[-1, :] - K, 0.0)
    else:
        payoff = xp.maximum(K - S_paths[-1, :], 0.0)

    # Backward induction using simple polynomial basis. This loop over `steps`
    # (like the forward simulation above) is an inherent Longstaff-Schwartz
    # recurrence -- each step's continuation value depends on the next step's
    # already-discounted payoff -- so it can't be collapsed into one xp call,
    # but every op inside it now runs through xp so it executes on GPU too.
    discount = xp.exp(-r * (T / steps))
    for t in range(steps - 1, 0, -1):
        if option == 'call':
            immediate = xp.maximum(S_paths[t, :] - K, 0.0)
        else:
            immediate = xp.maximum(K - S_paths[t, :], 0.0)

        itm = immediate > 0
        if xp.any(itm):
            X = S_paths[t, itm]
            Y = payoff[itm] * discount
            # choose polynomial degree based on data
            degree = 2 if int(X.shape[0]) >= 50 else 1
            try:
                continuation = _heston_vander_lstsq(X, Y, degree)
                exercise = immediate[itm]
                exercise_now = exercise > continuation
                payoff[itm] = xp.where(exercise_now, exercise, payoff[itm] * discount)
            except Exception as e:
                # NO FALLBACK. Silently carrying the discounted continuation
                # forward means the early-exercise decision was never made at
                # this timestep -- the "American" price quietly degrades toward
                # European without anything in the output saying so. A failed
                # regression is real information; surface it.
                raise RuntimeError(
                    f"[Heston LSM] Regression fit failed at timestep {t} with "
                    f"{int(xp.sum(itm))} ITM paths. Error: {e}. "
                    f"Early-exercise decision unavailable."
                )
        payoff[~itm] = payoff[~itm] * discount

    option_price = xp.exp(-r * T) * xp.mean(payoff)
    return _to_scalar(option_price)


def _heston_lsm_price_crn(S0, K, T, r, q, V0, kappa, theta, vol_sigma, rho,
                          sims, steps, option, z1_pre, z_prime_pre):
    """Heston LSM price using PRE-GENERATED z1 / z_prime arrays (Common
    Random Numbers). Same math as heston_lsm_price but skips the fresh
    seed/draw step -- the caller controls the randomness so central-
    difference bumps of ANY parameter (S0, V0, kappa, theta, vol_sigma,
    rho, r, T) reuse identical shocks. Correlation rho is applied INSIDE
    _simulate_heston_paths so bumping rho itself is CRN-clean too.

    Extracted for use by heston_all_greeks; heston_lsm_price is unchanged
    (still generates its own randoms from `seed`) so no existing caller is
    affected.
    """
    option = option.lower()
    if option not in ('call', 'put'):
        raise ValueError("option must be 'call' or 'put'")

    S_paths, _V_paths = _simulate_heston_paths(
        S0, V0, r, kappa, theta, vol_sigma, rho, T, steps, sims,
        seed=None, q=q, z1_pre=z1_pre, z_prime_pre=z_prime_pre,
    )

    if option == 'call':
        payoff = xp.maximum(S_paths[-1, :] - K, 0.0)
    else:
        payoff = xp.maximum(K - S_paths[-1, :], 0.0)

    discount = xp.exp(-r * (T / steps))
    for t in range(steps - 1, 0, -1):
        if option == 'call':
            immediate = xp.maximum(S_paths[t, :] - K, 0.0)
        else:
            immediate = xp.maximum(K - S_paths[t, :], 0.0)
        itm = immediate > 0
        if xp.any(itm):
            X = S_paths[t, itm]
            Y = payoff[itm] * discount
            degree = 2 if int(X.shape[0]) >= 50 else 1
            try:
                continuation = _heston_vander_lstsq(X, Y, degree)
                exercise = immediate[itm]
                exercise_now = exercise > continuation
                payoff[itm] = xp.where(exercise_now, exercise, payoff[itm] * discount)
            except Exception as e:
                # NO FALLBACK -- same reasoning as heston_lsm_price above. A
                # skipped early-exercise decision inside a CRN bump leg would
                # also break the bump's symmetry with the base leg, corrupting
                # every Greek derived from it.
                raise RuntimeError(
                    f"[Heston LSM] Regression fit failed at timestep {t} with "
                    f"{int(xp.sum(itm))} ITM paths. Error: {e}. "
                    f"Early-exercise decision unavailable."
                )
        payoff[~itm] = payoff[~itm] * discount

    return _to_scalar(xp.exp(-r * T) * xp.mean(payoff))


def heston_all_greeks(S, K, T, r, q, V0, kappa, theta, vol_sigma, rho,
                      sims=8000, steps=100, option='put', seed=42):
    """Heston's OWN Greek engine: bump S / V0 / r / T on the Heston LSM
    pricer using COMMON RANDOM NUMBERS (CRN). ALL Greek values come from
    Heston-under-Heston-dynamics -- NOT the previous shortcut of feeding
    effective_sigma = sqrt(V0) into american_all_greeks (which is a
    Leisen-Reimer tree at a single flat vol, not Heston at all).

    Why this is the fix Jason asked for:
      - Previous compute_greeks_heston mapped Heston's calibrated (V0, kappa,
        theta, xi, rho) down to a scalar `effective_sigma = sqrt(V0)` and
        passed that to american_all_greeks. The kappa / theta / xi / rho
        parameters that DEFINE Heston as a stochastic-vol model
        contributed NOTHING to any Greek in the report. That's precisely
        the "shortcut through another model's engine" the AMD comparison
        exposed, and precisely the thing this refactor is undoing.
      - Now: Delta bumps S in the Heston SDE. Vega bumps V0 (Heston's
        actual variance-level parameter, converted to a sigma-equivalent
        so the reported Vega is comparable to the other models'). Rho
        bumps r. Theta bumps T. Vanna / Vomma / Speed come from
        combinations of those same Heston-under-Heston bumps.

    Vega convention: Heston's natural "vol level" parameter is V0
    (initial variance). To match the other models' Vega reporting
    (dP / d(sigma), where sigma is a lognormal vol convention), report
    Vega = dP / d(sqrt(V0)) via chain rule: dP / dsigma = 2*sqrt(V0) *
    dP / dV0. That way "Heston Vega" and "LR Vega" are directly
    comparable in units -- both are dollar-price-change per unit-vol-change.

    Higher-order Greeks (Vanna, Vomma, Speed, Charm, Color): computed the
    same way as the 1st-order block -- CRN bump-and-revalue directly on
    the Heston LSM pricer, nested (e.g. Vanna = dVega/dS is a bump of S
    around a Vega that is itself a bump of V0, all four corners sharing
    the SAME z1/z_prime shocks). This used to fall back to closed-form
    Black-Scholes at sigma_eff = sqrt(V0) -- which, like the old
    effective-sigma Vega/Delta shortcut this module's docstring already
    calls out, discarded kappa/theta/xi/rho entirely for every 2nd-order
    Greek. Nested finite differences on a simulated LSM price are noisier
    than a closed form, but CRN cancellation (same shocks across every
    corner of each nested difference) keeps the signal usable, and the
    result is now genuinely Heston-under-Heston-dynamics like every other
    Greek in this function.

    Vomma convention (BUG FIX F8): 'vomma' is d2P/d(sigma_eff)^2 with
    sigma_eff = sqrt(V0) -- the same derivative, in the same units, that
    BAW / LR / CRR / SABR / VannaVolga / MC publish under their own
    'vomma' key, obtained by chain-ruling Heston's V0 derivatives:
    d2P/dsigma_eff^2 = 2*dP/dV0 + 4*V0*d2P/dV0^2. This key previously
    held dVega/d(xi) -- a mixed partial against Heston's vol-of-vol
    parameter, a different quantity in different units that is NOT
    comparable to any other engine's Vomma column (it ran roughly an
    order of magnitude larger). That xi derivative is still computed and
    returned, under the separate key 'vomma_xi', because it is a
    legitimate Heston-native risk measure -- it was only mislabeled.

    Read the Vomma number with the caveat below in mind. Bumping V0 while
    holding theta/kappa fixed moves the WHOLE variance path only
    partially (the CIR process mean-reverts back toward theta over T), so
    the map sigma_eff -> effective lognormal vol is itself curved, and
    that curvature -- not the option's own vol convexity -- dominates
    d2P/dsigma_eff^2. Verified against this module's own exact
    semi-analytic European price (heston_european_call_price): for
    QQQ-like inputs S=678.71 K=690 T=0.219 r=0.0408 q=0.0045 V0=0.04
    theta=0.04 kappa=1.5 xi=0.5 rho=-0.6 the true value is +62.1, and it
    swings to -56.6 at kappa=0.2 and +126.6 at kappa=3.0. Only in the
    degenerate kappa->0, xi->0 limit does it collapse onto the flat-vol
    Black-Scholes Vomma (checked: 3.958 vs BS 3.889). So this IS the
    same derivative the other engines report, but Heston's own dynamics
    legitimately give it a much larger magnitude than a flat-vol model
    does -- a real modelling difference, not a units mismatch.

    Sims / steps defaults are lower than heston_lsm_price's (8k / 100 vs
    the calibrator's live 12k / 200) because this function makes ~10
    calls to _heston_lsm_price_crn, so each individual call needs to be
    cheap. CRN cancellation makes the derivative signal clean at these
    sizes; the price returned in the report still uses the full
    heston_lsm_price with production sims/steps.
    """
    from american_binomial import _bs_rho

    is_call = (option == 'call')
    sigma_eff = float(np.sqrt(max(V0, 1e-9)))

    if T <= 0 or sigma_eff <= 1e-6:
        return {'delta': 0.0, 'gamma': 0.0, 'vega': 0.0, 'rho': 0.0, 'theta': 0.0,
                'vanna': 0.0, 'vomma': 0.0, 'vomma_xi': 0.0, 'speed': 0.0, 'charm': 0.0,
                'color': 0.0, 'rho_euro': 0.0, 'rho_ee_premium': 0.0}

    # Pre-generate the CRN shocks once. Same seed => same shocks => noise
    # cancels between price(+bump) and price(-bump) in every central diff below.
    xp.random.seed(seed)
    z1 = xp.random.standard_normal((steps, sims))
    z_prime = xp.random.standard_normal((steps, sims))

    def p(S_=S, V0_=V0, r_=r, T_=T, xi_=vol_sigma):
        return _heston_lsm_price_crn(S_, K, T_, r_, q, V0_, kappa, theta, xi_, rho,
                                      sims, steps, option, z1, z_prime)

    # Bump sizes: wider than tree defaults for the same reason MC's are wider
    # (Heston LSM inherits LSM regression's non-smoothness in the bumped
    # parameter -- CRN mitigates but doesn't eliminate). Empirical starting
    # point; can be tuned per-regime if any Greek looks unstable.
    dS = S * 0.03
    dV = max(V0 * 0.05, 1e-4)   # variance bump
    dR = 0.02
    dT = max(T * 0.05, 1.0 / 730.0)
    T_dn = max(1e-6, T - dT)
    T_span = dT + (T - T_dn)

    p0 = p()
    delta = (p(S_=S + dS) - p(S_=S - dS)) / (2 * dS)
    gamma = (p(S_=S + dS) - 2 * p0 + p(S_=S - dS)) / (dS * dS)
    # dP/dV0, then chain-rule to dP/dsigma_eff for reporting: sigma_eff =
    # sqrt(V0), so dsigma_eff/dV0 = 1/(2*sqrt(V0)) and dP/dsigma_eff =
    # dP/dV0 / (dsigma_eff/dV0) = 2*sqrt(V0) * dP/dV0.
    dp_dV0 = (p(V0_=V0 + dV) - p(V0_=V0 - dV)) / (2 * dV)
    vega = 2.0 * sigma_eff * dp_dV0
    rho_am = (p(r_=r + dR) - p(r_=r - dR)) / (2 * dR)
    theta_g = -(p(T_=T + dT) - p(T_=T_dn)) / T_span / 365.0

    # --- 2nd-order Greeks: Vanna, Vomma, Speed, Charm, Color -----------
    # All via nested CRN bump-and-revalue directly on the Heston LSM
    # pricer (same z1/z_prime shocks as the 1st-order block above) --
    # see docstring for why this replaced the old closed-form-BS shortcut.

    # Vanna = dVega/dS. Vega itself = 2*sigma_eff * dP/dV0 (same chain
    # rule used for the reported `vega` above), so Vanna is a central
    # difference of that quantity across an S bump. sigma_eff is held
    # fixed (it only depends on V0, which isn't bumped here) so the same
    # chain-rule constant applies at both S+dS_vanna and S-dS_vanna.
    dS_vanna = max(S * 0.03, 0.03)
    dp_dV0_Sup = (p(S_=S + dS_vanna, V0_=V0 + dV) - p(S_=S + dS_vanna, V0_=V0 - dV)) / (2 * dV)
    dp_dV0_Sdn = (p(S_=S - dS_vanna, V0_=V0 + dV) - p(S_=S - dS_vanna, V0_=V0 - dV)) / (2 * dV)
    vega_up = 2.0 * sigma_eff * dp_dV0_Sup
    vega_down = 2.0 * sigma_eff * dp_dV0_Sdn
    vanna = (vega_up - vega_down) / (2 * dS_vanna)

    # Vomma = d2P/d(sigma_eff)^2 where sigma_eff = sqrt(V0) -- the SAME
    # derivative, in the same units, that BAW / LR / CRR / SABR /
    # VannaVolga / MC each publish under their own 'vomma' key (dollars
    # per unit-vol per unit-vol), so the comparison report's Vomma row
    # now compares like with like.
    #
    # BUG FIX (F8): the value published under 'vomma' here used to be
    # dVega/d(xi) -- a mixed partial against Heston's vol-of-vol
    # parameter. That is a genuinely different derivative in different
    # units, so the report was silently comparing a Heston-specific
    # quantity against everyone else's flat-vol convexity. The xi
    # derivative is still computed (see vomma_xi below) -- it was only
    # ever mislabeled, not wrong.
    #
    # Chain rule, using sigma_eff^2 = V0 (so dsigma_eff/dV0 =
    # 1/(2*sigma_eff), the same relation the Vega block above uses):
    #     dP/dsigma_eff    = 2*sigma_eff * dP/dV0
    #     d2P/dsigma_eff^2 = 2*dP/dV0 + 4*V0 * d2P/dV0^2
    # Both terms come from ONE central pair of V0 bumps on the same
    # z1/z_prime CRN shocks as every other Greek here, so only two extra
    # pricer calls are made and the noise is the same kind the rest of
    # this function already carries.
    #
    # Bump width: this gets its own, much wider V0 bump than Vega's dV.
    # A central 2nd difference divides by dV^2, so at Vega's 5%-of-V0
    # bump the divisor is ~4e-6 and any LSM regression non-smoothness in
    # the numerator is amplified ~250,000x; worse, the two chain-rule
    # terms are large and near-cancelling (order +600 and -600 to produce
    # an answer of order +60), so d2P/dV0^2 has to be accurate in an
    # absolute sense, not merely a relative one. Measured on the QQQ case
    # in this function's docstring: at 5% the estimate ranged over
    # thousands and flipped sign between seeds; at 50% it lands in the
    # 45-65 band around the exact semi-analytic value of 62.1. The same
    # wide-bump-with-floor pattern (and the same reason for it) is what
    # every other 2nd-order Greek in this suite already uses -- see the
    # vomma bump-width history in PROJECT_ROADMAP.md.
    #
    # Multi-seed CRN averaging: even at the wide bump, a SINGLE shock set
    # is not enough here -- measured on the QQQ case at sims=6000-10000,
    # single-seed estimates ranged over 45 / 51 / 96 / -72 across seeds,
    # i.e. not merely imprecise but sign-unstable. Averaging the estimate
    # over 3 INDEPENDENT CRN shock sets (each internally common across
    # its own three V0 legs, so the bump cancellation is preserved within
    # each seed) collapses that: measured 3-seed averages at
    # sims=8000/steps=100 were 52.4 and 49.2 against this estimator's
    # exact semi-analytic value of 50.8. This is the same multi-seed
    # stabilization, for the same failure mode, that PROJECT_ROADMAP.md
    # documents for the vomma/speed/color block generally. It costs 6
    # extra pricer calls; every other Greek here is untouched and still
    # uses the single shared z1/z_prime set.
    #
    # RESIDUAL NOISE (read the number accordingly): after averaging, the
    # sign and order of magnitude are stable -- 6 measured runs across
    # sims=5000/steps=60 and sims=10000/steps=100 gave 41 to 123 around
    # the exact 62.1, all positive -- but the 2nd significant figure is
    # not. A 2nd difference of a Longstaff-Schwartz price w.r.t. a
    # volatility parameter is intrinsically hard to estimate, because the
    # regression's exercise decisions move discontinuously with the bump.
    # Raise sims if a tighter value is needed.
    dV_vomma = min(max(V0 * 0.50, 4e-3), V0 * 0.90)

    def _vomma_one_seed(z1_, zp_):
        def pv(V0_):
            return _heston_lsm_price_crn(S, K, T, r, q, V0_, kappa, theta, vol_sigma,
                                         rho, sims, steps, option, z1_, zp_)
        b0, bu, bd = pv(V0), pv(V0 + dV_vomma), pv(V0 - dV_vomma)
        # Use the WIDE-bump first derivative inside the chain rule (not
        # the narrow-bump dp_dV0 that Vega reports): both terms then
        # carry the same finite-bump truncation error, which partially
        # cancels in the sum instead of accumulating.
        dp_w = (bu - bd) / (2 * dV_vomma)
        d2p_w = (bu - 2.0 * b0 + bd) / (dV_vomma * dV_vomma)
        return 2.0 * dp_w + 4.0 * V0 * d2p_w

    _vomma_legs = [_vomma_one_seed(z1, z_prime)]
    for _k in (1, 2):
        xp.random.seed(seed + 1000 * _k)
        _z1_k = xp.random.standard_normal((steps, sims))
        _zp_k = xp.random.standard_normal((steps, sims))
        _vomma_legs.append(_vomma_one_seed(_z1_k, _zp_k))
    vomma = float(np.mean(_vomma_legs))

    # Vomma_xi = dVega/d(vol-of-vol). Heston's OWN vol-of-vol parameter
    # is xi (vol_sigma) -- bump that (not a lognormal-sigma proxy) and
    # recompute the same chain-ruled Vega at each xi bump. This is a
    # legitimate, genuinely Heston-native risk metric ("how does my vol
    # exposure change as the variance process itself gets more
    # volatile") with no counterpart in the flat-vol engines, which is
    # exactly why it is reported under its own key and is NOT the
    # 'vomma' column of the cross-model comparison.
    dxi = max(vol_sigma * 0.05, 0.01)
    dp_dV0_xiup = (p(V0_=V0 + dV, xi_=vol_sigma + dxi) - p(V0_=V0 - dV, xi_=vol_sigma + dxi)) / (2 * dV)
    dp_dV0_xidn = (p(V0_=V0 + dV, xi_=vol_sigma - dxi) - p(V0_=V0 - dV, xi_=vol_sigma - dxi)) / (2 * dV)
    vega_xi_up = 2.0 * sigma_eff * dp_dV0_xiup
    vega_xi_down = 2.0 * sigma_eff * dp_dV0_xidn
    vomma_xi = (vega_xi_up - vega_xi_down) / (2 * dxi)

    # Speed = dGamma/dS. Gamma at an S-shifted center, both sides, via
    # the same central second-difference formula used for the reported
    # `gamma` above.
    dS_speed = max(S * 0.03, 0.03)
    gamma_up = (p(S_=S + dS_speed + dS) - 2 * p(S_=S + dS_speed) + p(S_=S + dS_speed - dS)) / (dS * dS)
    gamma_down = (p(S_=S - dS_speed + dS) - 2 * p(S_=S - dS_speed) + p(S_=S - dS_speed - dS)) / (dS * dS)
    speed = (gamma_up - gamma_down) / (2 * dS_speed)

    # Charm = -dDelta/dT and Color = dGamma/dT. Reuse the same T bump
    # (dT / T_dn) already computed for Theta so all three time-sensitive
    # Greeks are consistent about how "time passing" is modeled.
    delta_Tup = (p(S_=S + dS, T_=T + dT) - p(S_=S - dS, T_=T + dT)) / (2 * dS)
    delta_Tdn = (p(S_=S + dS, T_=T_dn) - p(S_=S - dS, T_=T_dn)) / (2 * dS)
    charm = -(delta_Tup - delta_Tdn) / T_span

    gamma_Tup = (p(S_=S + dS, T_=T + dT) - 2 * p(T_=T + dT) + p(S_=S - dS, T_=T + dT)) / (dS * dS)
    gamma_Tdn = (p(S_=S + dS, T_=T_dn) - 2 * p(T_=T_dn) + p(S_=S - dS, T_=T_dn)) / (dS * dS)
    color = (gamma_Tup - gamma_Tdn) / T_span

    rho_euro = _bs_rho(S, K, T, r, q, sigma_eff, is_call)
    rho_ee_premium = rho_am - rho_euro

    return {'delta': delta, 'gamma': gamma, 'vega': vega, 'rho': rho_am, 'theta': theta_g,
            'vanna': vanna, 'vomma': vomma, 'vomma_xi': vomma_xi, 'speed': speed,
            'charm': charm, 'color': color,
            'rho_euro': rho_euro, 'rho_ee_premium': rho_ee_premium}


class HestonCalibrator:
    """Calibrate Heston parameters to market implied vols (European) using numerical Heston pricer.
    Fits kappa, theta, vol_sigma (xi), rho while keeping V0 set from market or historical vol.
    """
    def __init__(self, ticker: str, expiry_date: str, r: float = 0.01, q: float = 0.0,
                 known_expiry: Optional[str] = None):
        # known_expiry (YYYYMMDD): the REAL listed contract main.py already
        # resolved once via expiry_selector.choose_expiry() and threads into
        # every other model (CRR/LR/NR/SABR/VV/MC/BAW) and the Market row.
        # When supplied it is used DIRECTLY -- no re-derived target date, no
        # independent "nearest listed expiration" search (see _prepare).
        # Defaults to None purely for backward compatibility with callers
        # that only have a dashed target date; main.py always passes it.
        self.ticker = ticker
        self.expiry_date = expiry_date
        self.known_expiry = known_expiry
        self.r = r
        self.q = q
        self.md = MarketDataController()
        self.S = None
        self.T = None
        self.strikes = None
        self.market_vols = None
        self._prepare()

    def _prepare(self):
        # PotatoHedge/ThetaData smile only (yahoo purged).
        #
        # NO FALLBACKS: this used to degrade to a flat synthetic smile
        # (_make_fallback, a straight line at a hardcoded fallback_v) on ANY
        # failure -- no listed expiry found, insufficient smile points, or
        # any other exception. That's exactly what silently ran on EVERY
        # call before the YYYYMMDD date-format bug below was found and
        # fixed -- it was masking a real bug, not handling a rare degrade
        # path. Every failure now raises so it's visible and debuggable.
        from datetime import datetime
        import data_source_config
        from thetadata_controller import ThetaDataController

        self.S = self.md.fetch_spot_price(self.ticker)
        # T comes from the REAL contract when one was handed to us, not from a
        # re-derived approximate date -- same principle as the "T reconciliation"
        # fix in PROJECT_ROADMAP.md: every model in the report must be keyed to
        # one single resolved listed contract.
        if self.known_expiry:
            expiry = datetime.strptime(self.known_expiry, "%Y%m%d")
        else:
            expiry = datetime.strptime(self.expiry_date, "%Y-%m-%d")
        self.T = max((expiry - datetime.now()).days / 365.0, 0.001)
        self.forward = self.S * np.exp((self.r - self.q) * self.T)

        if not getattr(data_source_config, 'PREFER_THETADATA', True):
            raise RuntimeError("[HestonCalib] ThetaData disabled in data_source_config -- no other live smile source (yahoo removed). No fallback.")

        td = ThetaDataController()
        exps = td.list_expirations(self.ticker)
        td.close()
        if not exps:
            raise RuntimeError(f"[HestonCalib] No listed expirations returned for {self.ticker}. No fallback.")

        if self.known_expiry:
            # BUG FIX (expiry alignment): we ALREADY know the exact real listed
            # contract every other model and the Market row are keyed to, so
            # there is nothing to search for -- searching again is what caused
            # the bug. run_heston_full used to re-derive its own target date
            # from T (now + T*365 days) and then run the nearest-expiration
            # search below against it; on a chain with several weeklies close
            # together that landed on a DIFFERENT contract than resolved_exp.
            # Verified live 2026-07-28 (TSLA, nominal T=0.0055): the re-derived
            # path picked 20260729 (T=0.0027, 127 strikes, IV 0.49-4.54,
            # xi=3.72/rmse=0.162) while every other model and the Market row
            # used 20260731 (T=0.0055, 109 strikes, IV 0.50-3.22,
            # xi=1.12/rmse=0.037) -- Heston was calibrating to the wrong smile.
            #
            # NO FALLBACK: if the caller hands us an expiry that isn't actually
            # listed, that's a real upstream bug (stale/wrong resolved_exp), not
            # something to quietly paper over by snapping to a neighbour --
            # which is precisely the silent-substitution behaviour this whole
            # module refuses to do anywhere else.
            if self.known_expiry not in exps:
                raise RuntimeError(
                    f"[HestonCalib] known_expiry={self.known_expiry} is not a listed expiration for "
                    f"{self.ticker} (listed: {exps[:8]}{'...' if len(exps) > 8 else ''}). "
                    f"No fallback -- fix the resolved expiry upstream rather than snapping to a nearby contract."
                )
            nearest = self.known_expiry
            print(f"[HestonCalib] Using caller-resolved listed expiry {nearest} (no re-derivation) for {self.ticker}.")
        else:
            target = expiry
            # BUG FIX: list_expirations() returns YYYYMMDD (e.g. '20260727', no
            # dashes -- verified live), but this used to parse candidates with
            # strptime format "%Y-%m-%d" (dashed), raising ValueError on every
            # single candidate, every single call, silently swallowed -- this
            # had always fallen through to flat-vol regardless of real data
            # availability.
            parsed = [(abs((datetime.strptime(d, "%Y%m%d") - target).days), d) for d in exps]
            parsed.sort()
            nearest = parsed[0][1]

        # Shared smile fetch (see smile_utils.py): prefers ThetaData's own
        # implied_vol field per strike, but SOLVES IV ITSELF off that
        # strike's own bid/ask (same American Leisen-Reimer solver
        # "Leisen-Reimer" pricing uses) whenever that field is missing/null,
        # instead of just dropping the strike.
        from smile_utils import fetch_market_smile
        sarr, varr, sources, _fwd, _prices, _rights = fetch_market_smile(
            self.ticker, nearest, self.S, self.T, self.r, self.q,
        )
        if len(sarr) < 3:
            raise RuntimeError(
                f"[HestonCalib] Insufficient smile points ({len(sarr)}) for {self.ticker} {nearest}. No fallback."
            )
        self.strikes = sarr
        self.market_vols = varr
        n_solved = sum(1 for s in sources if s == 'solved')
        print(f"[HestonCalib] Spot={self.S:.2f}, r={self.r:.4f}, q={self.q:.4f}, T={self.T:.4f}, Forward={self.forward:.2f}, data points={len(self.strikes)} ({len(self.strikes)-n_solved} vendor IV, {n_solved} solved from price)")
        print(f"[HestonCalib] Strike range: {self.strikes[0]:.2f} - {self.strikes[-1]:.2f}")
        print(f"[HestonCalib] IV range: {min(self.market_vols):.4f} - {max(self.market_vols):.4f}")

    def _heston_cf_price(self, K, params) -> float:
        # params: kappa, theta, xi(vol_sigma), rho, v0
        kappa, theta, xi, rho, v0 = params
        return heston_european_call_price(self.S, K, self.T, self.r, self.q, v0, kappa, theta, xi, rho)

    def calibrate(self, initial=(1.5, 0.04, 0.3, -0.3), v0=None, maxiter=30):
        # initial: kappa, theta, xi, rho -- kept as one of the multi-start
        # seed points (see below) rather than the sole starting guess it
        # used to be. v0's own initial guess is separate, see below.
        #
        # BUG FIX (v0 as 5th free parameter): v0 used to be a FIXED input,
        # never calibrated -- only kappa/theta/xi/rho were free parameters.
        # Confirmed live (MU): the caller seeds v0 from initial_sigma**2,
        # where initial_sigma comes from vol_manager.get_sigma(method='CRR')
        # -- i.e. CRR's OWN solved sigma. Since the now-deleted
        # compute_greeks_heston derived ALL of Heston's Greeks from
        # effective_sigma =
        # sqrt(v0), and v0 never moved from that CRR-derived seed, Heston's
        # Greeks were mechanically IDENTICAL to CRR's, every single time,
        # regardless of what Heston's own calibration found for
        # kappa/theta/xi/rho -- a real violation of "no cross-model
        # borrowing", not a coincidence (verified: a live MU report showed
        # Heston's entire Greeks row byte-for-byte equal to CRR's). v0 is
        # now a genuinely free 5th calibrated parameter, fit by the same
        # vega-weighted objective as everything else -- so Heston's Greeks
        # reflect Heston's OWN fit to the market smile, not whatever CRR
        # happened to seed it with. The seed value below is still just a
        # numerically reasonable starting point for the optimizer (same
        # role as kappa/theta/xi/rho's generic literature defaults) -- not
        # a value substitution, since the optimizer is free to (and
        # generally will) move away from it.
        #
        # BUG FIX (multi-start): this used to be a SINGLE minimize() call
        # from ONE initial guess -- (kappa=1.5, theta=v0_seed, xi=~sigma,
        # rho=-0.3, v0=v0_seed). That was fine when the objective had one
        # obvious minimum, but with v0 as a free 5th parameter the loss
        # landscape gained a real risk of narrow local minima far from the
        # global one -- session notes §6 explicitly flagged this as the
        # known-not-yet-fixed source of Heston fragility on high-vol names.
        # Confirmed live on AMD Put K=480 T=0.107 sigma~0.80 (comparison
        # report 2026-07-27 15:33:43): Heston came back as N/A across every
        # column, i.e. run_heston_full() raised -- either the single-start
        # optimizer failed outright, or its converged xi/rmse landed
        # outside run_heston_full's unstable-fit guardrail (xi>=4.5 or
        # rmse>0.25). Either way, one initial guess in a 5D non-convex
        # landscape wasn't enough on this regime.
        #
        # Fix: mirror SABRCalibrator.calibrate()'s multi-start pattern --
        # sweep a small grid of seed points across the two most degenerate
        # parameters (kappa's mean-reversion scale, xi's vol-of-vol scale)
        # and rho (the calibration parameter most likely to hit a bound at
        # a local optimum), run minimize() from each, keep the lowest-loss
        # convergence. Grid size deliberately capped near SABR's own
        # multi-start count (~30-40 restarts) rather than the full 5D
        # Cartesian product, so this doesn't slow calibration to a crawl
        # for the sake of exhaustiveness -- the batched
        # heston_call_prices_batch keeps each objective evaluation cheap,
        # but 30 iterations x 5D gradient probes x N restarts still adds up.
        if v0 is None:
            v0 = (self.market_vols.mean())**2
        v0_seed = v0
        # BUG FIX: this used to hard-cap at the first 15 strikes (self.strikes
        # is sorted ascending, so "first 15" meant the 15 LOWEST strikes in
        # the chain -- not even nearest-to-forward, just whatever the sort
        # order happened to put first). That was independent of, and on top
        # of, smile_utils.fetch_market_smile's own now-removed 15-strike cap
        # -- see that module's docstring. Calibrating against the FULL chain
        # (confirmed live: 137 real strikes for a TSLA case, vs. 15 before)
        # only became tractable once the per-strike pricer below was batched
        # (see heston_call_prices_batch/_bs_iv_batch) -- the old per-strike
        # Python loop, each iteration re-running two adaptive `quad`
        # integrals plus a Newton IV solve, made calibrating against more
        # than a handful of strikes take minutes.
        strikes = self.strikes
        market_vols = self.market_vols

        # Vega-weight the objective, matching SABRCalibrator._weighted_error
        # in SABRModel.py -- computed ONCE here (it only depends on the
        # market data, not the trial kappa/theta/xi/rho) rather than inside
        # obj(). Without this, an unweighted sum-of-squares objective treats
        # a deep-OTM strike with a numerically-inflated "implied vol" (see
        # smile_utils.py's moneyness-filter docstring -- inverting a
        # near-worthless, minimum-tick price is ill-conditioned and can spit
        # out an absurd IV) exactly as importantly as a liquid near-ATM
        # point. Confirmed live on MU: unweighted, this landed on rho=0.98
        # (pinned essentially at its bound) and xi=0.016 (essentially
        # deterministic variance) -- a degenerate corner solution chasing
        # noise, not a genuine fit. SABR's own calibration on the same data
        # converges far more sensibly precisely because it already
        # vega-weights; this brings Heston's objective to the same standard.
        F, T = self.forward, self.T
        mv = np.asarray(market_vols, dtype=float)
        Karr = np.asarray(strikes, dtype=float)
        d1 = (np.log(F / Karr) + 0.5 * mv ** 2 * T) / (mv * np.sqrt(T))
        weights = F * np.sqrt(T) * norm.pdf(d1)
        w_mask = weights >= 1e-6
        if not np.any(w_mask):
            raise RuntimeError("[HestonCalib] No strike has usable vega for weighting -- cannot calibrate. No fallback.")
        w_sum = float(np.sum(weights[w_mask]))

        def obj(x):
            kappa, theta, xi, rho, v0_trial = x
            if not (0.01<=kappa<=10 and 1e-6<=theta<=2 and 0.001<=xi<=5 and -0.99<=rho<=0.99 and 1e-6<=v0_trial<=4.0):
                return 1e6
            try:
                prices = heston_call_prices_batch(self.S, strikes, self.T, self.r, self.q, v0_trial, kappa, theta, xi, rho)
                ivs = _bs_iv_batch(prices, self.S, strikes, self.T, self.r, self.q, seed_vols=market_vols)
                err = float(np.sum(weights[w_mask] * (ivs[w_mask] - mv[w_mask]) ** 2))
                return err / w_sum
            except Exception:
                return 1e6

        # Multi-start grid. The (initial, v0_seed) caller-supplied point is
        # always kept as one of the seeds so behaviour is a superset of the
        # previous single-start (any tuning that used to work still gets
        # tried) -- the remaining seeds span the 3D corner of parameter
        # space (kappa, xi, rho) where local optima most commonly hide.
        # theta_seed stays at v0_seed at all seeds (session notes explain
        # why hardcoding it to 0.04 systematically underprices high-vol
        # names -- v0_seed is the correct anchor); v0's own seed is fixed at
        # v0_seed too. All 5 parameters remain fully free during the
        # minimize() call itself -- the grid only controls WHERE each
        # restart begins searching from, not what it's allowed to search.
        seed_grid = []
        # The caller's own suggestion, first (preserves prior behaviour
        # exactly when this seed happens to be the winner).
        seed_grid.append((*initial, v0_seed))
        for kappa0 in (0.5, 1.5, 3.0):
            for xi0 in (0.2, 0.5, 1.0):
                for rho0 in (-0.7, -0.3, 0.0, 0.3):
                    seed_grid.append((kappa0, v0_seed, xi0, rho0, v0_seed))
        # Dedup on rounded tuple to avoid running the caller's suggestion twice
        # if it happens to coincide with a grid point.
        seen = set(); uniq = []
        for s in seed_grid:
            key = tuple(round(x, 4) for x in s)
            if key in seen: continue
            seen.add(key); uniq.append(s)
        seed_grid = uniq

        bounds = [(0.01,10),(1e-6,2),(0.001,5),(-0.99,0.99),(1e-6,4.0)]
        print(f"[HestonCalib] Multi-start calibration: {len(seed_grid)} restarts, "
              f"{len(strikes)} strikes ({int(np.sum(w_mask))} usable for vega-weighting)")

        best_res = None
        best_fun = float('inf')
        n_ok = 0
        for x0 in seed_grid:
            try:
                res = minimize(obj, x0=x0, bounds=bounds, options={'maxiter': maxiter})
            except Exception:
                continue
            if not res.success:
                continue
            n_ok += 1
            if res.fun < best_fun:
                best_fun = res.fun
                best_res = res

        if best_res is None:
            # NO FALLBACK: every one of ~N multi-start restarts failed to
            # converge. That's a genuine calibration failure worth seeing --
            # something about the smile itself is broken (bad data, insufficient
            # strikes, degenerate wing behavior) -- not something to paper over
            # with a degenerate default guess.
            raise RuntimeError(
                f"[HestonCalib] Optimization failed on ALL {len(seed_grid)} multi-start restarts. "
                f"No fallback -- inspect the market smile inputs (strike count, IV range, moneyness filter)."
            )
        kappa, theta, xi, rho, v0_fit = best_res.x
        rmse = float(np.sqrt(best_res.fun))  # obj is already vega-weighted MEAN squared error
        print(f"[HestonCalib] Done ({n_ok}/{len(seed_grid)} restarts converged): "
              f"kappa={kappa:.4f}, theta={theta:.4f}, xi={xi:.4f}, rho={rho:.4f}, v0={v0_fit:.6f}, rmse={rmse:.4f}")
        return {'kappa':kappa, 'theta':theta, 'xi':xi, 'rho':rho, 'v0':v0_fit, 'rmse':rmse}


def heston_european_call_price(S, K, T, r, q, v0, kappa, theta, xi, rho):
    """Heston (1993) semi-analytic European call via the P1/P2 characteristic
    functions, using the numerically stable "Little Heston Trap" formulation
    (Albrecher et al. 2007 -- the -d root with exp(-d*T), which avoids the
    branch-cut blow-ups the original +d/exp(+d*T) form suffers at longer T).

    Rewritten because the previous implementation's discriminant was malformed:
    it used sqrt((rho*xi*i*phi - b)^2 + (i*phi + phi^2)), dropping BOTH the xi^2
    scaling and the u_j=+/-0.5 term that distinguishes P1 from P2. That made the
    pricer fail its most basic sanity check -- as xi->0 with theta=v0 the price
    must converge to Black-Scholes(sqrt(v0)), but it returned near-zero/negative
    values -- which in turn fed garbage into HestonCalibrator (calibrates by
    inverting this price to an implied vol). Correct discriminant is
    d_j = sqrt((rho*xi*i*phi - b_j)^2 - xi^2 * (2*u_j*i*phi - phi^2)) with
    u_1 = +1/2, b_1 = kappa - rho*xi and u_2 = -1/2, b_2 = kappa. Verified against
    the xi->0 BS limit and put-call parity in /tmp (see PROJECT_ROADMAP.md).
    """
    import cmath

    a = kappa * theta
    x0 = cmath.log(S)  # carry handled via the (r-q) term in C below

    def char_func(phi, Pnum):
        i = complex(0, 1)
        if Pnum == 1:
            u = 0.5
            b = kappa - rho * xi
        else:
            u = -0.5
            b = kappa
        d = cmath.sqrt((rho * xi * phi * i - b) ** 2 - xi ** 2 * (2 * u * phi * i - phi ** 2))
        # "Little Heston Trap" stable root: g uses (... - d)/(... + d) with exp(-d*T)
        g = (b - rho * xi * phi * i - d) / (b - rho * xi * phi * i + d)
        exp_dt = cmath.exp(-d * T)
        C = (r - q) * phi * i * T + (a / xi ** 2) * ((b - rho * xi * phi * i - d) * T
                                                     - 2.0 * cmath.log((1.0 - g * exp_dt) / (1.0 - g)))
        D = (b - rho * xi * phi * i - d) / xi ** 2 * (1.0 - exp_dt) / (1.0 - g * exp_dt)
        return cmath.exp(C + D * v0 + i * phi * x0)

    def integrand_P(phi, Pnum):
        i = complex(0, 1)
        return (cmath.exp(-i * phi * cmath.log(K)) * char_func(phi, Pnum) / (i * phi)).real

    # NO FALLBACK: this used to silently substitute a plain Black-Scholes
    # price (at vol=sqrt(v0)) if the quadrature failed -- i.e. a completely
    # different, non-Heston model's number, unlabeled, standing in for
    # "Heston" in every downstream report. Let it raise instead.
    integral1 = quad(lambda x: integrand_P(x, 1), 1e-8, 200.0, limit=200)[0]
    integral2 = quad(lambda x: integrand_P(x, 2), 1e-8, 200.0, limit=200)[0]
    P1 = 0.5 + integral1 / np.pi
    P2 = 0.5 + integral2 / np.pi
    call = S * np.exp(-q * T) * P1 - K * np.exp(-r * T) * P2
    # Heston prices can drift a hair negative from quadrature error deep OTM; floor
    # at intrinsic (discounted) which is the correct arbitrage bound.
    intrinsic = max(S * np.exp(-q * T) - K * np.exp(-r * T), 0.0)
    return float(max(call.real, intrinsic))


def _heston_char_func_grid(phi, S, T, r, q, v0, kappa, theta, xi, rho):
    """Heston characteristic function (P1 and P2) evaluated across an array
    of phi values, ONCE. This does not depend on K at all -- only on
    v0/kappa/theta/xi/rho/T/S/r/q -- so it's the piece that should be shared
    across every strike being priced, not recomputed independently per
    strike (see heston_call_prices_batch's docstring for why that matters).
    Same "Little Heston Trap" stable-root formulation as
    heston_european_call_price, just vectorized over phi via numpy complex
    arrays instead of Python `cmath` scalars.
    """
    phi = np.asarray(phi, dtype=complex)
    i = 1j
    a = kappa * theta
    x0 = np.log(S)
    out = {}
    for Pnum, u, b in ((1, 0.5, kappa - rho * xi), (2, -0.5, kappa)):
        d = np.sqrt((rho * xi * phi * i - b) ** 2 - xi ** 2 * (2 * u * phi * i - phi ** 2))
        g = (b - rho * xi * phi * i - d) / (b - rho * xi * phi * i + d)
        exp_dt = np.exp(-d * T)
        C = (r - q) * phi * i * T + (a / xi ** 2) * ((b - rho * xi * phi * i - d) * T
                                                      - 2.0 * np.log((1.0 - g * exp_dt) / (1.0 - g)))
        D = (b - rho * xi * phi * i - d) / xi ** 2 * (1.0 - exp_dt) / (1.0 - g * exp_dt)
        out[Pnum] = np.exp(C + D * v0 + i * phi * x0)
    return out


def heston_call_prices_batch(S, strikes, T, r, q, v0, kappa, theta, xi, rho, n_nodes=64, phi_max=200.0):
    """Price MANY strikes at once via ONE shared Gauss-Legendre quadrature
    over phi, instead of re-running an independent adaptive `quad` integral
    per strike (what heston_european_call_price does, and what
    HestonCalibrator.calibrate() used to do in a per-strike Python loop).

    Why this exists: smile_utils.fetch_market_smile stopped capping the
    fetched chain at 15 near-the-money strikes (see its docstring), and
    HestonCalibrator.calibrate() had its OWN separate, even cruder 15-strike
    cap on top of that (`strikes[:15]` -- the 15 lowest strikes by sort
    order, not even nearest-to-forward) which is now also removed. Calibrating
    against the full chain (confirmed live: 137 strikes for a TSLA case) at
    the old per-strike cost -- two adaptive `quad` integrals PER STRIKE PER
    OBJECTIVE EVALUATION, inside a gradient-based optimizer needing ~9
    evaluations per iteration over up to 30 iterations -- pushed a single
    calibrate() call from seconds to many minutes.

    The fix: the characteristic function itself (_heston_char_func_grid)
    does not depend on K, so it only needs to be evaluated ONCE per phi node,
    then reused for every strike -- only the strike-dependent phase factor
    exp(-i*phi*log(K)) varies per strike, which is a cheap vectorized
    multiply-and-sum (fixed Gauss-Legendre nodes/weights, not adaptive
    quadrature, so this trades a small amount of per-strike precision for a
    roughly n_strikes-fold speedup; 64 nodes matches or exceeds the adaptive
    integral's practical precision for this integrand in testing).

    Returns an array of call prices, one per strike.
    """
    strikes = np.asarray(strikes, dtype=float)
    eps = 1e-8
    nodes, weights = np.polynomial.legendre.leggauss(n_nodes)
    phi = 0.5 * (phi_max - eps) * nodes + 0.5 * (phi_max + eps)
    w = weights * 0.5 * (phi_max - eps)

    cf = _heston_char_func_grid(phi, S, T, r, q, v0, kappa, theta, xi, rho)
    i = 1j
    logK = np.log(strikes)
    # (n_strikes, n_phi) via outer product -- phase factor is the only
    # strike-dependent piece; cf[1]/cf[2] (shape (n_phi,)) broadcast across
    # the strikes axis.
    phase = np.exp(-i * np.outer(logK, phi))
    integrand1 = (phase * cf[1][None, :] / (i * phi)[None, :]).real
    integrand2 = (phase * cf[2][None, :] / (i * phi)[None, :]).real
    integral1 = integrand1 @ w
    integral2 = integrand2 @ w

    P1 = 0.5 + integral1 / np.pi
    P2 = 0.5 + integral2 / np.pi
    call = S * np.exp(-q * T) * P1 - strikes * np.exp(-r * T) * P2
    intrinsic = np.maximum(S * np.exp(-q * T) - strikes * np.exp(-r * T), 0.0)
    return np.maximum(call, intrinsic)


def _bs_iv_batch(prices, S, strikes, T, r, q, seed_vols, tol=1e-4, max_iter=50):
    """Vectorized Newton-Raphson Black-Scholes implied vol across an array of
    (price, strike) pairs at once -- same purpose as looping
    implied_volatility_nr per strike (what HestonCalibrator.calibrate() used
    to do), just batched so this doesn't reintroduce a per-strike Python-level
    cost right after heston_call_prices_batch removed the other one. Falls
    back to leaving a point at its seed vol if Newton doesn't converge for it
    (matching implied_volatility_nr's bisection fallback closely enough for
    an optimizer objective, where occasional per-point imprecision on a hard
    strike washes out across the whole chain rather than needing to be exact).
    """
    from scipy.stats import norm
    K = np.asarray(strikes, dtype=float)
    C = np.asarray(prices, dtype=float)
    sigma = np.clip(np.asarray(seed_vols, dtype=float).copy(), 0.01, 5.0)
    sqrtT = np.sqrt(T)
    for _ in range(max_iter):
        d1 = (np.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * sqrtT)
        d2 = d1 - sigma * sqrtT
        price = S * np.exp(-q * T) * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
        vega = S * np.exp(-q * T) * sqrtT * norm.pdf(d1)
        diff = price - C
        step = np.where(vega > 1e-8, diff / np.maximum(vega, 1e-8), 0.0)
        sigma = np.clip(sigma - step, 0.001, 5.0)
        if np.max(np.abs(diff)) < tol:
            break
    return sigma


def heston_iv_smile_batch(S, strikes, T, r, q, v0, kappa, theta, xi, rho, seed_vols=None):
    """Public one-call replacement for "price every strike via
    heston_european_call_price (adaptive `quad`, twice per strike) then
    Newton-solve its IV one strike at a time" -- exactly the pattern
    main.py's smile chart used to run for the 'Heston' curve (~100+ strikes,
    each doing two adaptive quadratures plus a scalar Newton loop). Reuses
    the same shared-quadrature batch pricer and vectorized Newton solver
    HestonCalibrator.calibrate() already relies on for the identical reason
    (see heston_call_prices_batch/_bs_iv_batch docstrings) -- one call prices
    every strike at once, then one vectorized Newton pass solves every IV at
    once. Returns an array of implied vols, one per strike.
    """
    strikes = np.asarray(strikes, dtype=float)
    if seed_vols is None:
        seed_vols = np.full(strikes.shape, np.sqrt(max(v0, 1e-8)))
    else:
        seed_vols = np.asarray(seed_vols, dtype=float)
        if seed_vols.shape != strikes.shape:
            seed_vols = np.full(strikes.shape, float(np.mean(seed_vols)))
    prices = heston_call_prices_batch(S, strikes, T, r, q, v0, kappa, theta, xi, rho)
    return _bs_iv_batch(prices, S, strikes, T, r, q, seed_vols=seed_vols)


def run_heston_full(ticker: str, S: float, K: float, T: float, r: float, q: float,
                    initial_sigma: float, sims: int = 12000, steps: int = 200, option: str = 'call', seed: int = 42,
                    exp: Optional[str] = None):
    """Run full Heston workflow: calibrate to market IVs and price via Heston LSM.
    Returns dict with calibrated params and price.

    Greeks are NOT returned. Call heston_all_greeks() with the returned
    calib to get Heston-under-Heston-dynamics Greeks.

    `exp` (YYYYMMDD) is the REAL listed expiry main.py already resolved via
    expiry_selector.choose_expiry() and threads into every other model and the
    Market row. Pass it -- it is what keeps Heston calibrating against the SAME
    contract/smile as the rest of the report. Optional only for backward
    compatibility; when omitted, the legacy re-derive-a-date-from-T behaviour
    below runs, which is exactly what produced the wrong-smile bug documented
    in HestonCalibrator._prepare.
    """
    # NO FALLBACKS anywhere in this workflow. This used to (a) substitute a
    # hardcoded default calib dict (rmse=999.0 sentinel) if calibration
    # raised, and (b) if the calibration merely looked "unstable" (xi>=4.5 or
    # rmse>0.25), silently threw the whole Heston fit away and repriced with
    # CRR/"Standard"'s sigma through the generic AmericanLSMPricer -- then
    # returned that under the SAME 'Heston' key every downstream report
    # reads from. That's not Heston degrading gracefully, it's a different
    # model wearing Heston's name tag. If calibration is genuinely unstable,
    # that's real information the user needs to see and debug, not something
    # to paper over.
    expiry_date = ( __import__('datetime').datetime.now() + __import__('datetime').timedelta(days=int(T*365)) ).strftime('%Y-%m-%d')
    calib_engine = HestonCalibrator(ticker, expiry_date, r=r, q=q, known_expiry=exp)
    # Seed long-run variance theta at v0 (= initial_sigma^2), not a hardcoded
    # 0.04. A fixed 0.04 (~20% vol) is a terrible start for a high-IV name --
    # it biases the optimizer toward a variance path that mean-reverts sharply
    # DOWN from v0, systematically underpricing. Starting theta at v0 lets the
    # fit move it wherever the smile actually implies.
    v0_seed = initial_sigma ** 2
    calib = calib_engine.calibrate(initial=(1.5, v0_seed, initial_sigma, -0.3), v0=v0_seed)

    xi = float(calib['xi'])
    rmse = float(calib['rmse'])
    if xi >= 4.5 or rmse > 0.25:
        raise RuntimeError(
            f"[Heston Full] Calibration unstable (xi={xi:.3f}, rmse={rmse:.3f}) for {ticker}. "
            f"No fallback -- investigate the smile/calibration inputs rather than substituting another model."
        )

    # price via LSM under Heston
    price = heston_lsm_price(S0=S, K=K, T=T, r=r, q=q, V0=calib['v0'], kappa=calib['kappa'], theta=calib['theta'], vol_sigma=calib['xi'], rho=calib['rho'], sims=sims, steps=steps, option=option, seed=seed)

    # Greeks are deliberately NOT computed here. The old compute_greeks_heston
    # routed them through american_all_greeks at effective_sigma=sqrt(v0),
    # which discards kappa/theta/xi/rho entirely -- a Leisen-Reimer tree at a
    # single flat vol, not Heston. Callers that want Heston Greeks call
    # heston_all_greeks() on the returned calib (CRN bump-and-revalue on the
    # actual Heston LSM); see main.py's Heston branches.
    return {'calib': calib, 'price': price}


if __name__ == '__main__':
    # quick local run for debugging
    val = heston_lsm_price(S0=156.91, K=165.0, T=0.528, r=0.0023, V0=0.2994**2)
    print('Heston LSM debug price:', val)
