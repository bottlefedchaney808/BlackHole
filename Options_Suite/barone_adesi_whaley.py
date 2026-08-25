"""Barone-Adesi-Whaley (1987) American option analytical approximation --
the 8th model, "American Black-Scholes".

Why this model exists in this suite:
  Every other model here is either a numerical American pricer (CRR/LR
  trees, MC LSM, Heston LSM) or a smile-parameterization (SABR, Vanna-
  Volga). None is a closed-form analytical American BS. BAW is the
  industry-standard analytical American approximation -- widely used by
  vendors for real-time Greek calculation because it avoids tree/sim cost
  entirely. Adding it as a benchmark directly tests the hypothesis
  "vendors compute Market Greeks via a BS-based analytical American (BAW
  or equivalent)": if BAW's Delta/Gamma/Vega/Rho/Theta match the Market
  row closely on the run-all comparison report, the answer is yes; if
  they don't, the vendor is using something else (tree, PDE, model
  library) and matching Market Greeks would require the same.

Reference: Barone-Adesi, G., and Whaley, R.E. (1987), "Efficient
Analytic Approximation of American Option Values", Journal of Finance,
42(2), 301-320. Formula as presented in Haug, "The Complete Guide to
Option Pricing Formulas", 2nd ed., section 7.4.

Accuracy: asymptotically exact as T -> 0 and T -> infinity, and typically
within 0.1-0.5% of true American price across most reasonable
strike/vol/T ranges. For calls with q=0 (no dividend), BAW is exactly
European BS -- because no early exercise is optimal on a non-dividend-
paying call, that's a mathematical identity, not an approximation.

The 2nd-order Greeks (Vanna/Vomma/Speed/Charm/Color) are computed by
finite-differencing BAW's own closed-form price surface (baw_american_
price) rather than falling back to a shared Black-Scholes closed form --
BAW is analytical, so these FD Greeks are noise-free and reflect BAW's
own early-exercise-adjusted sensitivities end to end.
"""

import math


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _bs_price(S, K, T, r, sigma, q, cp):
    """Plain European Black-Scholes-with-carry price."""
    if T <= 0 or sigma <= 1e-6:
        return max(S - K, 0.0) if cp else max(K - S, 0.0)
    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    if cp:
        return S * math.exp(-q * T) * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(
            d2
        )
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * math.exp(-q * T) * _norm_cdf(-d1)


def _bs_d1(S, K, T, r, sigma, q):
    return (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (
        sigma * math.sqrt(T)
    )


def _solve_baw_boundary_call(K, T, r, sigma, q, q2):
    """Solve for the critical stock price S* above which a BAW call
    should be exercised early. Uses the standard Haug (2nd ed.) iterative
    method: fixed-point iteration with the LHS/RHS gap driving the update.
    """
    # Initial guess (Haug 7-4b): S_infty is the perpetual-American boundary
    S_infty = K / (1.0 - 1.0 / q2) if q2 != 1.0 else K * 2.0
    h2 = (
        -((r - q) * T + 2.0 * sigma * math.sqrt(T)) * (K / (S_infty - K))
        if S_infty > K
        else -0.5
    )
    Sstar = K + (S_infty - K) * (1.0 - math.exp(h2))
    Sstar = max(Sstar, K * 1.001)  # must be strictly above K for a call

    # Newton iteration -- LHS - RHS = 0 with analytical derivative.
    for _ in range(120):
        d1s = _bs_d1(Sstar, K, T, r, sigma, q)
        Nd1 = _norm_cdf(d1s)
        eqT_Nd1 = math.exp(-q * T) * Nd1
        C_E = _bs_price(Sstar, K, T, r, sigma, q, True)
        # Boundary condition (Haug 7-4a): S* - K = C_E(S*) + (1 - e^(-qT) N(d1)) * S*/q2
        RHS = C_E + (1.0 - eqT_Nd1) * Sstar / q2
        f = Sstar - K - RHS
        # d/dS* of LHS = 1
        # d/dS* of C_E(S*) = e^(-qT) N(d1)   (BS call delta wrt spot)
        # d/dS* of (1 - e^(-qT) N(d1(S*))) * S*/q2:
        #   = (1 - eqT_Nd1)/q2 - (S*/q2) * e^(-qT) * pdf(d1) / (S* sigma sqrt(T))
        #   = (1 - eqT_Nd1)/q2 - e^(-qT) * pdf(d1) / (q2 * sigma * sqrt(T))
        term1 = (1.0 - eqT_Nd1) / q2
        term2 = math.exp(-q * T) * _norm_pdf(d1s) / (q2 * sigma * math.sqrt(T))
        dRHS = eqT_Nd1 + term1 - term2
        fprime = 1.0 - dRHS
        if abs(fprime) < 1e-12:
            break
        Sstar_new = Sstar - f / fprime
        if abs(Sstar_new - Sstar) < 1e-8:
            Sstar = Sstar_new
            break
        # keep boundary above K
        Sstar = max(Sstar_new, K * 1.001)
    return Sstar


def _solve_baw_boundary_put(K, T, r, sigma, q, q1):
    """Solve for the critical stock price S** below which a BAW put
    should be exercised early. Symmetric to _solve_baw_boundary_call.
    """
    S_infty = K / (1.0 - 1.0 / q1) if q1 != 1.0 else K * 0.5
    h1 = (
        ((r - q) * T - 2.0 * sigma * math.sqrt(T)) * (K / (K - S_infty))
        if K > S_infty
        else -0.5
    )
    Sstar = S_infty + (K - S_infty) * math.exp(h1)
    Sstar = max(Sstar, K * 0.001)  # must be strictly positive for a put

    for _ in range(120):
        d1s = _bs_d1(Sstar, K, T, r, sigma, q)
        Nmd1 = _norm_cdf(-d1s)
        eqT_Nmd1 = math.exp(-q * T) * Nmd1
        P_E = _bs_price(Sstar, K, T, r, sigma, q, False)
        # Boundary: K - S** = P_E(S**) - (1 - e^(-qT) N(-d1)) * S**/q1
        RHS = P_E - (1.0 - eqT_Nmd1) * Sstar / q1
        f = K - Sstar - RHS
        # d/dS** of LHS = -1
        # d/dS** of P_E(S**) = -e^(-qT) N(-d1)  (BS put delta wrt spot)
        # d/dS** of -(1 - e^(-qT) N(-d1(S**))) * S**/q1:
        #   = -(1 - eqT_Nmd1)/q1 - (S**/q1) * e^(-qT) * pdf(d1) / (S** sigma sqrt(T))
        #     (because d/dS[N(-d1)] = -pdf(-d1) * (-dd1/dS) = pdf(d1)*dd1/dS
        #      with a factor of -e^(-qT) from the outer form,
        #      then multiplied by -S**/q1)
        #   Working it out step by step:
        term1 = -(1.0 - eqT_Nmd1) / q1
        term2 = -math.exp(-q * T) * _norm_pdf(d1s) / (q1 * sigma * math.sqrt(T))
        dRHS = -eqT_Nmd1 + term1 + term2
        fprime = -1.0 - dRHS
        if abs(fprime) < 1e-12:
            break
        Sstar_new = Sstar - f / fprime
        if abs(Sstar_new - Sstar) < 1e-8:
            Sstar = Sstar_new
            break
        # Keep lower bound to prevent negative prices, but allow Newton to find true boundary
        Sstar = max(Sstar_new, 0.0)
    return Sstar


def baw_american_price(S, K, T, r, sigma, q=0.0, cp=True, steps=None):
    """Barone-Adesi-Whaley American option price.

    'steps' arg is ignored -- kept for signature compatibility with
    crr_american_price / leisen_reimer_american_price so this can drop
    into the same call sites (bruteforceimpliedvol, vol_manager, main).
    """
    # Clamp sigma floor to 0.1% to prevent OverflowError at very low sigma
    sigma = max(sigma, 0.001)

    if T <= 0 or sigma <= 1e-6:
        return float(max(S - K, 0.0) if cp else max(K - S, 0.0))

    # No-dividend call: BAW == European BS exactly (no early exercise
    # premium is ever optimal without dividends).
    if cp and q <= 0:
        return float(_bs_price(S, K, T, r, sigma, 0.0, True))

    Kh = 1.0 - math.exp(-r * T)
    if Kh <= 1e-10:
        return float(_bs_price(S, K, T, r, sigma, q, cp))

    M = 2.0 * r / (sigma * sigma)
    n_coeff = 2.0 * (r - q) / (sigma * sigma)
    discr = (n_coeff - 1.0) ** 2 + 4.0 * M / Kh
    sqrt_discr = math.sqrt(max(discr, 0.0))

    if cp:
        q2 = 0.5 * (1.0 - n_coeff + sqrt_discr)
        if q2 <= 0.0:
            return float(_bs_price(S, K, T, r, sigma, q, True))
        Sstar = _solve_baw_boundary_call(K, T, r, sigma, q, q2)
        if S >= Sstar:
            return float(S - K)  # exercise immediately
        d1s = _bs_d1(Sstar, K, T, r, sigma, q)
        A2 = (Sstar / q2) * (1.0 - math.exp(-q * T) * _norm_cdf(d1s))
        return float(_bs_price(S, K, T, r, sigma, q, True) + A2 * (S / Sstar) ** q2)
    else:
        q1 = 0.5 * (1.0 - n_coeff - sqrt_discr)
        if q1 >= 0.0:
            return float(_bs_price(S, K, T, r, sigma, q, False))
        Sstar = _solve_baw_boundary_put(K, T, r, sigma, q, q1)
        if S <= Sstar:
            return float(K - S)  # exercise immediately
        d1s = _bs_d1(Sstar, K, T, r, sigma, q)
        A1 = -(Sstar / q1) * (1.0 - math.exp(-q * T) * _norm_cdf(-d1s))
        return float(_bs_price(S, K, T, r, sigma, q, False) + A1 * (S / Sstar) ** q1)


def baw_all_greeks(S, K, T, r, sigma, q=0.0, cp=True, steps=None):
    """BAW's OWN Greek engine -- full 1st- and 2nd-order Greeks, all via
    central finite differences on baw_american_price itself.

    BAW is a closed-form analytical pricer, so FD Greeks taken against it
    are essentially noise-free (no tree quantization or MC sampling to
    fight) and reflect BAW's genuine early-exercise-premium-adjusted
    sensitivities -- including for the 2nd-order Greeks (Vanna / Vomma /
    Speed / Charm / Color), which are computed by differencing BAW's own
    price surface rather than falling back to a shared Black-Scholes
    closed form. This keeps every reported Greek attributable to BAW's
    own early-exercise boundary, not a European approximation of it.
    """
    try:
        from .american_binomial import _bs_rho
    except ImportError:
        from american_binomial import _bs_rho

    if T <= 0 or sigma <= 1e-6:
        return {
            "delta": 0.0,
            "gamma": 0.0,
            "vega": 0.0,
            "rho": 0.0,
            "theta": 0.0,
            "vanna": 0.0,
            "vomma": 0.0,
            "speed": 0.0,
            "charm": 0.0,
            "color": 0.0,
            "rho_euro": 0.0,
            "rho_ee_premium": 0.0,
        }

    is_call = cp

    def price(S_=S, T_=T, r_=r, sigma_=sigma):
        return baw_american_price(S_, K, T_, r_, sigma_, q, is_call)

    dS = max(S * 0.01, 0.01)
    dsig = max(sigma * 0.05, 0.005)
    dr = 0.0001
    dT = max(T * 0.01, 1.0 / 365.0)

    greeks = {}

    # 1st-order: Delta, Gamma, Vega, Rho, Theta
    p_base = price()
    p_s_up = price(S_=S + dS)
    p_s_down = price(S_=S - dS)

    greeks["delta"] = (p_s_up - p_s_down) / (2 * dS)
    greeks["gamma"] = (p_s_up - 2 * p_base + p_s_down) / (dS**2)

    p_sig_up = price(sigma_=sigma + dsig)
    p_sig_down = price(sigma_=sigma - dsig)
    greeks["vega"] = (p_sig_up - p_sig_down) / (2 * dsig)

    p_r_up = price(r_=r + dr)
    p_r_down = price(r_=r - dr)
    greeks["rho"] = (p_r_up - p_r_down) / (2 * dr)

    # Theta = -dV/dT (T = time to expiry), scaled to a per-calendar-day
    # figure -- clamp T_dn away from 0 rather than letting it go negative,
    # and use the actual realized span (T_span) in the denominator so the
    # clamp doesn't bias the estimate. Sign/scale convention matches the
    # other engines' *_all_greeks (see MCHestonLSM.heston_all_greeks).
    T_dn = max(1e-6, T - dT)
    T_span = dT + (T - T_dn)
    greeks["theta"] = -(price(T_=T + dT) - price(T_=T_dn)) / T_span / 365.0

    # 2nd-order: Vanna, Vomma, Speed, Charm, Color -- all via BAW closed-
    # form finite differences on baw_american_price, no shared BS.

    # Vanna = dVega/dS
    vega_s_up = (
        price(S_=S + dS, sigma_=sigma + dsig) - price(S_=S + dS, sigma_=sigma - dsig)
    ) / (2 * dsig)
    vega_s_down = (
        price(S_=S - dS, sigma_=sigma + dsig) - price(S_=S - dS, sigma_=sigma - dsig)
    ) / (2 * dsig)
    greeks["vanna"] = (vega_s_up - vega_s_down) / (2 * dS)

    # Vomma = dVega/dSigma. Bump kept narrow (0.06*sigma, vs. the old 0.15
    # which spanned an effective 30% of sigma and truncation-biased Vomma
    # high by ~21-23% against the analytic/market reference) -- and the
    # lower leg is floor-guarded so sigma - 2*dsig_vomma can never go
    # negative or near-zero for low-vol inputs.
    dsig_vomma = max(sigma * 0.06, 0.005)
    sigma_lo = max(sigma - 2 * dsig_vomma, 1e-4)
    vega_sig_up = (price(sigma_=sigma + 2 * dsig_vomma) - p_base) / (2 * dsig_vomma)
    vega_sig_down = (p_base - price(sigma_=sigma_lo)) / (2 * dsig_vomma)
    greeks["vomma"] = (vega_sig_up - vega_sig_down) / (2 * dsig_vomma)

    # Speed = dGamma/dS
    dS_speed = max(S * 0.03, 0.03)
    gamma_s_up = (price(S_=S + 2 * dS_speed) - 2 * price(S_=S + dS_speed) + p_base) / (
        dS_speed**2
    )
    gamma_s_down = (
        p_base - 2 * price(S_=S - dS_speed) + price(S_=S - 2 * dS_speed)
    ) / (dS_speed**2)
    greeks["speed"] = (gamma_s_up - gamma_s_down) / (2 * dS_speed)

    # Charm = -dDelta/dT and Color = dGamma/dT. Reuse the same T_dn/T_span
    # from Theta so all three time-sensitive Greeks agree on how "time
    # passing" is modeled (T increasing = MORE time to expiry).
    delta_Tup = (price(S_=S + dS, T_=T + dT) - price(S_=S - dS, T_=T + dT)) / (2 * dS)
    delta_Tdn = (price(S_=S + dS, T_=T_dn) - price(S_=S - dS, T_=T_dn)) / (2 * dS)
    greeks["charm"] = -(delta_Tup - delta_Tdn) / T_span

    gamma_Tup = (
        price(S_=S + dS, T_=T + dT) - 2 * price(T_=T + dT) + price(S_=S - dS, T_=T + dT)
    ) / (dS**2)
    gamma_Tdn = (
        price(S_=S + dS, T_=T_dn) - 2 * price(T_=T_dn) + price(S_=S - dS, T_=T_dn)
    ) / (dS**2)
    greeks["color"] = (gamma_Tup - gamma_Tdn) / T_span

    rho_euro = _bs_rho(S, K, T, r, q, sigma, is_call)
    rho_ee_premium = greeks["rho"] - rho_euro
    greeks["rho_euro"] = rho_euro
    greeks["rho_ee_premium"] = rho_ee_premium

    return greeks


def brute_force_baw(
    market_price, S, K, T, r, q=0.0, cp=True, low=1e-3, high=5.0, tol=1e-4, max_iter=200
):
    """Bisection IV solve against baw_american_price. Same interface as
    brute_force / brute_force_lr / brute_force_mc so this drops into
    vol_manager the same way.

    NO FALLBACK: if the bracket doesn't span the market price (or the
    solver doesn't converge), raise. Silent defaults were removed from
    the other bisection solvers in the same session's fixes (see
    bruteforceimpliedvol.py's docstring); this new one follows suit.
    """
    if market_price is None or market_price <= 0:
        raise ValueError(
            f"[BAW IV] Cannot solve for IV without a positive market price (got {market_price})."
        )
    intrinsic = max(S - K, 0.0) if cp else max(K - S, 0.0)
    if market_price < intrinsic - 1e-6:
        raise ValueError(
            f"[BAW IV] Market price {market_price:.4f} below intrinsic {intrinsic:.4f} -- arbitrageable, cannot solve."
        )

    p_low = baw_american_price(S, K, T, r, low, q, cp)
    p_high = baw_american_price(S, K, T, r, high, q, cp)
    if not (p_low <= market_price <= p_high):
        raise ValueError(
            f"[BAW IV] Market price {market_price:.4f} outside solver bracket "
            f"[p(sigma={low})={p_low:.4f}, p(sigma={high})={p_high:.4f}]. No fallback."
        )

    lo, hi = low, high
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        p_mid = baw_american_price(S, K, T, r, mid, q, cp)
        if abs(p_mid - market_price) < tol:
            return mid
        if p_mid < market_price:
            lo = mid
        else:
            hi = mid
    raise ValueError(
        f"[BAW IV] Did not converge after {max_iter} iterations. No fallback."
    )
