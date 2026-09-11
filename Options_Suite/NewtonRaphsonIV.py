import numpy as np
from scipy.stats import norm
# Re-exported, not called here: implied_volatility_nr_american's default
# pricer is Leisen-Reimer, but its docstring tells callers to pass
# `pricer=baw_american_price` for the old behaviour, so keep the name
# importable from this module alongside the solver that takes it.
try:
    from .barone_adesi_whaley import baw_american_price
except ImportError:
    from barone_adesi_whaley import baw_american_price  # noqa: F401
try:
    from .american_binomial import leisen_reimer_american_price
except ImportError:
    from american_binomial import leisen_reimer_american_price

# One volatility point (1%), the unit _vol_is_identifiable measures price
# sensitivity over for BOTH solvers in this module. If a full point of vol
# does not move the price by more than the solver's own price tolerance, the
# "solved" vol is not determined by the price.
VOL_POINT = 0.01


def _vol_is_identifiable(price_at, sigma, tol):
    """Does the market price actually pin down `sigma`, or merely admit it?

    Matching the price is NOT the same as solving for vol. Both solvers in
    this module declare convergence on `|price - C| < tol`, and there are
    real option shapes where price is FLAT in sigma across a whole interval
    -- so that test is satisfied by every vol in the interval and the loop
    just reports whichever one it happened to land on. Two different causes,
    same symptom:

      * AMERICAN, early-exercise boundary: a deep-ITM option can sit exactly
        ON the boundary. Exercise is optimal, time value is zero, and the
        price is EXACTLY intrinsic for every sigma below some boundary value.
        Vega is identically zero over that interval -- a hard flat floor.
      * EUROPEAN, saturation: no boundary, but Black-Scholes saturates at
        both ends -- price -> 0 as an OTM strike runs away from spot, and
        -> K*exp(-rT) (put) / S*exp(-qT) (call) as sigma grows. Far enough
        out, a whole range of sigma reproduces the same price to floating
        point. Strictly softer than the American case: there the price is
        EXACTLY intrinsic bit for bit, here it merely varies by far less
        than `tol`, because vega decays toward zero instead of being
        identically zero. So European accuracy degrades continuously where
        American accuracy falls off a cliff.

    The probe is one volatility point DOWN, and the direction is the whole
    point: price is monotone non-decreasing in sigma, so a degenerate region
    is a FLOOR (and the high-sigma asymptote is likewise approached from
    below). At the top edge of a flat interval the price still responds to
    RAISING sigma while lowering it changes nothing -- an upward probe
    detects nothing.

    Threshold is `tol` itself, which makes the criterion self-consistent
    rather than a magic number: if a whole vol point of movement shifts the
    price by less than the tolerance the caller already treats as "equal",
    then two vols a full point apart are indistinguishable to it and the
    answer is arbitrary within that band.

    That also makes the guard conservative by construction -- it can only
    under-flag a marginal case, never reject a solvable one -- which matters
    because vol_manager.py RAISES on converged=False.

    Same idea as MCHestonLSM._bs_iv_batch's `min_vega_frac` floor, which
    drops unidentifiable smile points to NaN rather than plotting them. That
    one thresholds vega directly because it is vectorized over a whole chain
    and already has d1 in hand; this one probes the price because a scalar
    solver has no closed-form vega for an American tree anyway, and because
    reusing `tol` avoids introducing a second tunable.

    price_at: callable(sigma) -> price, closing over the fixed contract.
    Returns True if the price discriminates `sigma` from a vol one point
    lower, False if the answer is arbitrary within that band.
    """
    try:
        below = max(sigma - VOL_POINT, 1e-6)
        return abs(price_at(sigma) - price_at(below)) > tol
    except Exception:
        # A pricer that blows up on the probe tells us nothing about
        # identifiability; leave the solve's own verdict alone.
        return True


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

    # Identifiability -- see _vol_is_identifiable. The European case has no
    # early-exercise boundary, so it is immune to the flat-intrinsic floor
    # that breaks the American solver; it fails by SATURATION instead. Far
    # enough from the money the price is numerically pinned to its asymptote
    # and many vols reproduce it: measured at S=550, T=0.25, r=0.05, a K=300
    # put prices to 7e-136 at sigma=0.05 and 7e-25 at sigma=0.12 -- different
    # numbers, but 7e-25 apart against a price tolerance of 1e-4, so the same
    # price as far as this solver can tell -- and it answered 0.28481,
    # converged=True, for both. The returned number was a function of the seed
    # and the iteration path, not of the price. (A deep-ITM K=800 put is the
    # same story at the other asymptote: 240.06224039510516 vs
    # 240.06224040001018, 5e-9 apart.)
    #
    # Unlike the American boundary case (bimodal: degenerate points probe at
    # ~6e-5, solvable ones at 0.13+, >1000x clear) this degrades CONTINUOUSLY
    # as vega decays, so no threshold cleanly partitions it. Measured over a
    # 448-point call/put x strike x vol x dividend grid, the tol=1e-4 default
    # never flags a solve accurate to better than 1% (0 of 324) and flags
    # every solve worse than 10% (112 of 112). Errors in between are a
    # gradient and are treated as such -- which is the honest answer for a
    # continuous phenomenon, not a limitation to tune away.
    if converged and not _vol_is_identifiable(
        lambda s: black_scholes_func(S, K, T, r, s, cp, q), sigma, tol
    ):
        converged = False

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
    the LEISEN-REIMER binomial (american_binomial.py) -- the suite's default
    American pricer, per CLAUDE.md.

    This default used to be Barone-Adesi-Whaley, on the argument that
    inverting against a different model than the "Leisen-Reimer" menu method
    bought "model independence" between the two IV estimates. Two problems
    with that in practice, and they are why it is now LR:

      * vol_manager.py's own NewtonRaphson branch documented the opposite --
        "Solved against the Leisen-Reimer American price ... same tree the
        Leisen-Reimer method uses" -- so the code and its caller disagreed
        about which model the number came from. A solved IV whose model you
        have to read the source to identify is not model independence, it is
        ambiguity.
      * BAW is a closed-form APPROXIMATION. Against a price generated by the
        LR tree every other part of the suite uses, it left a residual that
        looked like solver error: measured on this deep-ITM put, BAW and LR
        differ by ~0.27 in price, which is ~2 vol points of apparent IV
        error with nothing wrong in the solver.

    BAW remains available and unchanged -- pass `pricer=baw_american_price`
    to get the old behaviour deliberately rather than by default.

    C: market option price. cp: True for call, False for put.
    Returns (sigma, converged: bool).
    """
    if pricer is None:
        pricer = leisen_reimer_american_price

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

    # ------------------------------------------------------------------
    # Identifiability: matching the price is NOT the same as solving for vol
    # ------------------------------------------------------------------
    # Mechanism and threshold rationale live in _vol_is_identifiable; what
    # follows is the measured American case it was built from. Both loops
    # above happily "converge" on the early-exercise boundary, because
    # |price - C| < tol is satisfied by the entire flat interval; the
    # bisection simply lands on its top edge.
    #
    # Measured live (S=550, K=605, T=0.25, r=0.05, q=0, put): Leisen-Reimer
    # and Barone-Adesi-Whaley BOTH return exactly 55.00000 -- intrinsic -- at
    # sigma = 0.10, 0.12 and 0.15, and this function answered 0.15590 with
    # converged=True for all three. A 30% error on a 0.12 input, reported as
    # a solved number. The degeneracy is a property of the OPTION, not of the
    # pricer, which is why switching the default pricer to LR does not fix it
    # and this guard is still required.
    #
    # At the top edge of that floor the price still responds to raising sigma
    # (measured 0.109) while lowering it changes nothing (measured 0.000057),
    # which is why the probe goes DOWN. Measured separation here is wide --
    # degenerate cases land at 5.7e-5 / 8.5e-5, every solvable case at 0.13
    # or above, i.e. >1000x clear -- so this is not a knife-edge. (The
    # European solver's version of the same guard is not bimodal like this;
    # see its call site.)
    #
    # `sigma` is still returned, and is still useful: it is the TOP of the
    # flat interval, so the market price is consistent with any vol at or
    # below it -- a genuine upper bound rather than a guess. Callers that
    # check `converged` (vol_manager) now fail loudly instead of pricing off
    # a fabricated vol; callers that report it (module_registry) surface
    # converged=False alongside the number.
    if converged and not _vol_is_identifiable(
        lambda s: pricer(S, K, T, r, s, q, cp, steps), sigma, tol
    ):
        converged = False

    return max(sigma, 0.001), converged
