import numpy as np
from scipy.stats import norm
try:
    from .barone_adesi_whaley import baw_american_price
except ImportError:
    from barone_adesi_whaley import baw_american_price

def black_scholes_func(S, K, T, r, sigma, cp, q=0.0):
    """
    Black-Scholes option pricing formula (continuous dividend yield q).
    cp: True for call, False for put
    q: continuous dividend yield (default 0.0)
    """
    d1 = (np.log(S / K) + (r - q + sigma ** 2 / 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if cp:
        return S * np.exp(-q * T) * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    else:
        return K * np.exp(-r * T) * norm.cdf(-d2) - S * np.exp(-q * T) * norm.cdf(-d1)

def vega(S, K, T, r, sigma, q=0.0):
    """Black-Scholes vega (sensitivity to volatility), dividend-adjusted."""
    if sigma <= 0 or T <= 0:
        return 0.0
    d1 = (np.log(S / K) + (r - q + sigma ** 2 / 2) * T) / (sigma * np.sqrt(T))
    return S * np.exp(-q * T) * np.sqrt(T) * norm.pdf(d1)

def implied_volatility_nr(C, S, K, T, r, cp, q=0.0, tol=0.0001, max_iterations=100, seed=0.3):
    """
    Compute implied volatility using Newton-Raphson method, with a bisection
    fallback if NR fails to converge (e.g. near-zero vega for deep ITM/OTM strikes).

    C: market option price
    S: spot price
    K: strike price
    T: time to maturity (years)
    r: risk-free rate
    cp: True for call, False for put
    q: continuous dividend yield (default 0.0)
    seed: initial volatility guess (default 0.3)

    Returns (sigma, converged: bool).
    """
    sigma = seed
    converged = False
    for i in range(max_iterations):
        price = black_scholes_func(S, K, T, r, sigma, cp, q)
        diff = price - C
        if abs(diff) < tol:
            converged = True
            break
        vg = vega(S, K, T, r, sigma, q)
        if abs(vg) < 1e-8:
            break
        sigma -= diff / vg
        sigma = max(sigma, 0.001)
        if sigma > 5.0:
            sigma = 5.0
            break
    else:
        converged = False

    if not converged:
        # Fallback: bounded bisection, guaranteed to converge if a root exists in [1e-6, 5.0]
        lo, hi = 1e-6, 5.0
        try:
            price_lo = black_scholes_func(S, K, T, r, lo, cp, q)
            price_hi = black_scholes_func(S, K, T, r, hi, cp, q)
            if price_lo <= C <= price_hi:
                for _ in range(80):
                    mid = 0.5 * (lo + hi)
                    price_mid = black_scholes_func(S, K, T, r, mid, cp, q)
                    if abs(price_mid - C) < tol:
                        sigma = mid
                        converged = True
                        break
                    if price_mid > C:
                        hi = mid
                    else:
                        lo = mid
                else:
                    sigma = 0.5 * (lo + hi)
        except Exception:
            pass

    return max(sigma, 0.001), converged


def implied_volatility_nr_american(C, S, K, T, r, cp, q=0.0, pricer=None, tol=0.0001,
                                    max_iterations=50, seed=0.3, steps=200):
    """
    Newton-Raphson implied volatility solved against a genuine AMERICAN price
    (early exercise applied at every tree node), not the closed-form European
    Black-Scholes price `implied_volatility_nr` above uses.

    This replaces the European solver as the "Newton-Raphson" menu method: the
    European version biased its solved sigma vs every other method (which all
    solve against an American price) for puts and dividend-paying calls, where
    early exercise has real value the European price ignores -- exactly the
    "no European solvers" gap. There's no closed-form vega for an American
    option, so vega here is a central finite difference on `pricer` itself
    (small relative sigma bump); everything else about the Newton iteration
    (and the bounded-bisection fallback if a step doesn't converge cleanly)
    mirrors `implied_volatility_nr`.

    pricer: callable(S, K, T, r, sigma, q, cp, steps) -> price. Defaults to
    the Barone-Adesi-Whaley closed-form American approximation (see
    barone_adesi_whaley.py), so this solver inverts against a genuinely
    different model than the Leisen-Reimer binomial the "Leisen-Reimer" menu
    method uses -- true model independence between the two IV estimates
    instead of both solving against the same tree.

    C: market option price. cp: True for call, False for put.
    Returns (sigma, converged: bool).
    """
    if pricer is None:
        pricer = baw_american_price

    sigma = max(seed, 0.01)
    converged = False
    for _ in range(max_iterations):
        price = pricer(S, K, T, r, sigma, q, cp, steps)
        diff = price - C
        if abs(diff) < tol:
            converged = True
            break
        dsig = max(sigma * 0.01, 1e-4)
        price_up = pricer(S, K, T, r, sigma + dsig, q, cp, steps)
        price_dn = pricer(S, K, T, r, sigma - dsig, q, cp, steps)
        vg = (price_up - price_dn) / (2 * dsig)
        if abs(vg) < 1e-8:
            break
        sigma -= diff / vg
        sigma = max(sigma, 0.001)
        if sigma > 5.0:
            sigma = 5.0
            break
    else:
        converged = False

    if not converged:
        # Fallback: bounded bisection, same shape as implied_volatility_nr's,
        # but against `pricer` (American) instead of black_scholes_func.
        lo, hi = 1e-4, 5.0
        try:
            price_lo = pricer(S, K, T, r, lo, q, cp, steps)
            price_hi = pricer(S, K, T, r, hi, q, cp, steps)
            if price_lo <= C <= price_hi:
                for _ in range(60):
                    mid = 0.5 * (lo + hi)
                    price_mid = pricer(S, K, T, r, mid, q, cp, steps)
                    if abs(price_mid - C) < tol:
                        sigma = mid
                        converged = True
                        break
                    if price_mid > C:
                        hi = mid
                    else:
                        lo = mid
                else:
                    sigma = 0.5 * (lo + hi)
        except Exception:
            pass

    return max(sigma, 0.001), converged
