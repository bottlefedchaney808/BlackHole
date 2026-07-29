#!/usr/bin/env python3
"""implied_vol.py

Black-Scholes implied volatility from an option price.

WHY THIS EXISTS (2026-07-24): ThetaData's greeks route
(`hist/option/all_greeks`) carries implied_vol and gamma but returns exactly
ONE day per call and silently ignores `end_date` -- proven in
diagnostics/diagnose_date_collapse.py. Backfilling one expiry that way costs
430 contracts x ~110 trading days = ~47,000 requests, which is not a thing to
do to a shared proxy.

`hist/option/eod` DOES honor a date range (14 dates in a single call, same
run), but carries only prices: open/high/low/close/bid/ask/volume. So the
cheap path is to take those prices and recover IV ourselves, then feed it to
dealer_positioning.bs_gamma -- ~550 requests instead of ~47,000, with full
strike coverage retained.

PROVENANCE, stated plainly: this is a EUROPEAN Black-Scholes inversion.
SPY options are American, and ThetaData's own IV presumably accounts for early
exercise. For OTM options on a low-dividend underlying the early-exercise
premium is very small, and Stage 3 only uses IV to (a) locate the OTM
replicating set and (b) fit a smooth reference smile whose per-strike
DEVIATION drives the sign -- both are relative comparisons across strikes on
the same day, so a small systematic bias affecting all strikes alike largely
cancels. It is not appropriate for pricing. If Stage 3 ever produces a result
worth defending, re-derive it against vendor IV on a sample of days before
publishing it.

Deliberately dependency-light (math only, no scipy) so it can be exercised in
any environment, and pure -- no network, no I/O -- so tests/test_implied_vol.py
can hammer it with round-trips.
"""
import math
from typing import Optional

# Below this, an option is worth essentially nothing and its price carries no
# usable volatility information -- inverting it produces noise, not a smile.
MIN_PRICE = 0.005

# Solver bounds. 1000% vol is far past anything real; a solution pinned to
# either bound means "the price is outside what BS can explain", which we
# report as None rather than as a number.
MIN_VOL = 1e-4
MAX_VOL = 10.0

# Identifiability test, expressed in units the MARKET has rather than units
# the solver has.
#
# Found by round-trip testing (tests/test_implied_vol.py), not by theory: a
# deep-ITM 70-strike call at 8% vol prices to 30.5541 with vega 1.9e-12. The
# solver converged to |price error| < 1e-6 at sigma=0.1258 -- a 57%
# overstatement, reported with total confidence -- because the price is
# numerically FLAT in sigma there, so every sigma explains it equally well.
#
# The first attempt at a fix -- "require vega above a floor at the solution"
# -- does not work, and failing that way is instructive: vega grows with
# sigma, so the solver simply drifts up to where vega clears the bar and the
# check passes on its own output. Any criterion evaluated at the returned
# sigma is self-fulfilling.
#
# So test the thing that actually matters and that the solver cannot move:
# does a ONE VOL POINT change move the price by more than the market can even
# quote? Options are quoted to the cent; if a full point of vol is worth less
# than half a cent, the price does not pin down the vol, full stop. Deep ITM
# is where this bites, and those strikes sit in the wings the vol-surface fit
# leans on.
VOL_RESOLUTION = 0.01     # one volatility point
PRICE_RESOLUTION = 0.005  # half a cent -- finer than any real quote


def norm_cdf(x: float) -> float:
    """Standard normal CDF via erf -- exact to double precision, no scipy."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_price(S: float, K: float, T: float, r: float, q: float,
             sigma: float, right: str) -> float:
    """Black-Scholes-Merton price with continuous dividend yield q."""
    right = str(right).upper()[:1]
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        # At/past expiry, or a degenerate input: worth intrinsic only.
        intrinsic = (S - K) if right == "C" else (K - S)
        return max(intrinsic, 0.0)
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    disc_r, disc_q = math.exp(-r * T), math.exp(-q * T)
    if right == "C":
        return S * disc_q * norm_cdf(d1) - K * disc_r * norm_cdf(d2)
    return K * disc_r * norm_cdf(-d2) - S * disc_q * norm_cdf(-d1)


def bs_vega(S: float, K: float, T: float, r: float, q: float, sigma: float) -> float:
    """dPrice/dSigma, in price units per 1.00 of vol (not per vol point).
    Same for calls and puts."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrt_T)
    return S * math.exp(-q * T) * norm_pdf(d1) * sqrt_T


def _no_arbitrage_bounds(S: float, K: float, T: float, r: float, q: float,
                         right: str) -> tuple:
    """(lower, upper) price bounds BS can produce as sigma sweeps 0 -> inf.
    A quote outside these can't be inverted at any volatility -- which happens
    for real, on stale or crossed EOD quotes, and must be rejected rather than
    silently clamped to a bound."""
    disc_r, disc_q = math.exp(-r * T), math.exp(-q * T)
    if str(right).upper()[:1] == "C":
        return max(S * disc_q - K * disc_r, 0.0), S * disc_q
    return max(K * disc_r - S * disc_q, 0.0), K * disc_r


def implied_vol(price: float, S: float, K: float, T: float, r: float, q: float,
                right: str, tol: float = 1e-6, max_iter: int = 100,
                pricer=None) -> Optional[float]:
    """Invert an option pricing model for sigma. Returns None -- never a
    fabricated number -- when the price carries no recoverable volatility.

    `pricer` selects the MODEL being inverted. Default is European
    Black-Scholes (`bs_price`), which is closed-form and fast enough to run
    across a whole backtest. Pass any callable with the same signature
    `(S, K, T, r, q, sigma, right) -> price` to invert something else --
    notably Leisen-Reimer / CRR from Options_Suite/american_binomial.py, which
    gives AMERICAN implied vol and is the correct model for SPY.

    Why default to BS rather than LR, given LR is more correct: a 110-day x
    430-contract Stage 3 backfill needs ~47,000 inversions, each of which
    costs 10-30 price evaluations. Closed-form BS makes that free; a 401-step
    binomial makes it two to three orders of magnitude more expensive. The
    intended workflow is BS for the full sweep, LR on a sample of days to
    MEASURE the early-exercise bias -- and if that bias turns out to matter
    for the gamma sign classification, switch the sweep. Guessing which way
    that goes without measuring it is how you end up with a slow pipeline
    defending a difference that was never material.

    Note the bracketing below assumes price is monotonically increasing in
    sigma, which holds for any sensible option model, American included.

    Newton-Raphson with a bisection fallback. Newton alone is not safe here:
    vega collapses toward zero for deep ITM/OTM options, so the update step
    explodes and the iteration can diverge or oscillate on exactly the far-wing
    strikes a vol-surface fit cares about. Bisection is slower but cannot
    diverge, so any Newton step that leaves the bracket hands control back to
    it.

    Returning None matters as much as the math. This project has been bitten
    twice by a failure being encoded as a plausible number -- an empty cache
    range read as "no data", a missing realized vol read as 0.0 and ranked as
    a strong signal. An unrecoverable IV is missing information, and callers
    must be able to tell the difference.
    """
    price_fn = pricer or bs_price
    if price is None or S <= 0 or K <= 0 or T <= 0:
        return None
    price = float(price)
    if price < MIN_PRICE:
        return None

    lower, upper = _no_arbitrage_bounds(S, K, T, r, q, right)
    # Small tolerance: EOD marks sit a hair outside the bound often enough
    # that a strict test would discard perfectly usable quotes.
    if price < lower - 1e-6 or price > upper + 1e-6:
        return None

    # Bracket the root first, so bisection always has somewhere to stand.
    lo, hi = MIN_VOL, MAX_VOL
    f_lo = price_fn(S, K, T, r, q, lo, right) - price
    f_hi = price_fn(S, K, T, r, q, hi, right) - price
    if f_lo > 0 or f_hi < 0:
        # Price below the zero-vol floor or above the max-vol ceiling: no
        # solution exists in [MIN_VOL, MAX_VOL].
        return None

    # Manaster-Koehler style starting guess -- near the maximum-vega point,
    # which is where Newton behaves best.
    sigma = max(math.sqrt(abs(math.log(S / K) + (r - q) * T) * 2.0 / T), 0.10) \
        if S != K else 0.20
    sigma = min(max(sigma, lo), hi)

    def _identified(sig):
        """Is this sigma actually pinned down by the price, or just one point
        on a flat stretch? Move it a full vol point either way and see whether
        the price responds by more than a quote's resolution. Uses the same
        pricer being inverted, so it stays honest for LR as well as BS."""
        lo_p = price_fn(S, K, T, r, q, max(sig - VOL_RESOLUTION, MIN_VOL), right)
        hi_p = price_fn(S, K, T, r, q, sig + VOL_RESOLUTION, right)
        return (hi_p - lo_p) >= PRICE_RESOLUTION

    for _ in range(max_iter):
        diff = price_fn(S, K, T, r, q, sigma, right) - price
        if abs(diff) < tol:
            return float(sigma) if _identified(sigma) else None
        # Maintain the bracket from every evaluation, so the fallback stays
        # valid no matter how Newton wanders.
        if diff > 0:
            hi = sigma
        else:
            lo = sigma

        vega = bs_vega(S, K, T, r, q, sigma)
        if vega > 1e-8:
            step = sigma - diff / vega
            if lo < step < hi:
                sigma = step
                continue
        sigma = 0.5 * (lo + hi)          # Newton left the bracket -- bisect.
        if hi - lo < tol:
            break

    # Converged on the bracket rather than the tolerance. Accept it unless it
    # is pinned to a bound, which means "outside what BS explains", not "10%".
    if sigma <= MIN_VOL * 1.01 or sigma >= MAX_VOL * 0.99:
        return None
    return float(sigma) if _identified(sigma) else None


def american_pricer(steps: int = 101, model: str = "lr"):
    """Return a `pricer(S, K, T, r, q, sigma, right)` backed by Options_Suite's
    American binomial trees, for passing to implied_vol(pricer=...).

    SPY options are American; European BS is the approximation. This makes the
    correct model one keyword away rather than a rewrite, so the
    early-exercise bias can actually be MEASURED on a sample of days instead
    of argued about.

        from implied_vol import implied_vol, american_pricer
        lr = american_pricer()
        iv_american = implied_vol(mark, S, K, T, r, q, right, pricer=lr)

    model: 'lr'  -> Leisen-Reimer (Peizer-Pratt inversion; far faster
                    convergence, so ~101 steps beats CRR at ~1000)
           'crr' -> Cox-Ross-Rubinstein

    `steps` defaults low deliberately: LR's convergence is smooth and
    monotone rather than oscillating, so a shallow tree is genuinely accurate,
    and inversion evaluates the pricer 10-30 times per solve.

    Imports lazily from the sibling Options_Suite package (resolved relative
    to this file, not hardcoded), so Vol_Suite keeps working normally if that
    folder isn't present -- this raises only when someone actually asks for an
    American pricer.
    """
    import os
    import sys

    # Validate the argument BEFORE touching the filesystem, so a typo'd model
    # reports itself as a typo rather than as a missing-dependency problem.
    fn = {"lr": "leisen_reimer_american_price", "crr": "crr_american_price"}.get(model)
    if fn is None:
        raise ValueError(f"model must be 'lr' or 'crr', got {model!r}")

    suite = os.environ.get(
        "OPTIONS_SUITE_ROOT",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "Options_Suite"))
    if suite not in sys.path:
        sys.path.insert(0, suite)
    try:
        import american_binomial as ab
    except ImportError as e:
        raise ImportError(
            f"Options_Suite/american_binomial.py not importable from {suite}: {e}"
        ) from e

    impl = getattr(ab, fn)

    def _pricer(S, K, T, r, q, sigma, right):
        # Argument-order adapter: american_binomial uses
        # (S, K, T, r, sigma, q, cp) with a boolean call flag, this module
        # uses (S, K, T, r, q, sigma, right) with 'C'/'P'. Getting sigma and
        # q the wrong way round here would silently produce plausible,
        # completely wrong vols, so it is done in exactly one place.
        return float(impl(S, K, T, r, sigma, q=q,
                          cp=(str(right).upper()[:1] == "C"), steps=steps))

    _pricer.__name__ = f"american_{model}_{steps}"
    return _pricer


def mid_price(bid: Optional[float], ask: Optional[float],
              close: Optional[float] = None) -> Optional[float]:
    """Best available mark for inversion, preferring the bid/ask midpoint.

    Mid beats close for this purpose: an option's last trade can be hours
    stale by the bell (most strikes don't trade near the close, and far-OTM
    strikes may not trade at all), while the closing quote is a live
    two-sided market at that moment. Falls back to close when the quote is
    absent, one-sided, or crossed.
    """
    def _num(x):
        try:
            v = float(x)
            return v if v == v and v > 0 else None      # v != v rejects NaN
        except (TypeError, ValueError):
            return None

    b, a, c = _num(bid), _num(ask), _num(close)
    if b is not None and a is not None and a >= b:
        return 0.5 * (b + a)
    return c
