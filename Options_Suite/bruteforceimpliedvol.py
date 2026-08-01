import numpy as np

from american_binomial import (crr_american_price, leisen_reimer_american_price,
                                crr_american_price_batch, leisen_reimer_american_price_batch)


def _brute_force_bisect(pricer, c_market, S, K, T, r, cp, q=0.0, steps=200, label="bisection"):
    """Shared bisection core for the binomial-tree IV solvers below.

    Bisects sigma in [1e-4, 5.0] against `pricer(S, K, T, r, sigma, q, cp,
    steps)` until the tree price matches c_market within tol.

    NO FALLBACKS: if the inputs can't actually be solved (no market price, a
    pricer that raises, or a market price outside the representable price
    range for any sigma in [1e-4, 5.0]), this raises instead of returning a
    quiet default sigma. A silently-substituted 0.3 used to masquerade as a
    real solve here -- every caller upstream (vol_manager.py) is expected to
    let this propagate rather than catch-and-guess.
    """
    if c_market is None:
        raise ValueError(f"[{label}] No market price supplied -- cannot solve IV.")
    tol = 1e-4
    max_iter = 60
    lo, hi = 1e-4, 5.0
    price_lo = pricer(S, K, T, r, lo, q, cp, steps)
    price_hi = pricer(S, K, T, r, hi, q, cp, steps)

    # Boundary clamp is a genuine (if extreme) bisection result, not a
    # fabricated default -- the true root lies at or beyond this bound.
    if c_market <= price_lo:
        return lo
    if c_market >= price_hi:
        return hi

    mid = 0.5 * (lo + hi)
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        price_mid = pricer(S, K, T, r, mid, q, cp, steps)
        diff = price_mid - c_market
        if abs(diff) < tol:
            return mid
        if price_mid > c_market:
            hi = mid
        else:
            lo = mid

    residual = abs(pricer(S, K, T, r, mid, q, cp, steps) - c_market)
    raise RuntimeError(
        f"[{label}] Failed to converge after {max_iter} iterations. Final residual: "
        f"{residual:.6e}. Target tol: {tol:.6e}. Try increasing max_iter or loosening tol."
    )


def brute_force(c_market, S, K, T, r, cp, q=0.0, steps=200):
    """
    Robust implied volatility via bisection between bounds, solved against a
    Cox-Ross-Rubinstein American binomial price (early exercise included).

    This is the "CRR" method's solver. It deliberately differs from the
    Newton-Raphson method: NR inverts an American Leisen-Reimer price via
    Newton's method, while this solves against a real CRR American price via
    bisection.

    q: continuous dividend yield (default 0.0).
    steps: depth of the CRR tree used for each price evaluation.

    Raises (no fallback) if c_market is None or the pricer itself errors.
    """
    return _brute_force_bisect(crr_american_price, c_market, S, K, T, r, cp, q, steps, label="CRR brute_force")


def brute_force_lr(c_market, S, K, T, r, cp, q=0.0, steps=200):
    """
    Same bisection IV solver as `brute_force`, but against a Leisen-Reimer
    American binomial price instead of CRR. This is the "Leisen-Reimer"
    method's solver -- a genuinely separate comparison model from "CRR",
    not a replacement for it. LR's smoother, faster convergence (see
    american_binomial.py) means a given step count is a more reliable target
    for this bisection than the same step count would be under CRR.

    q: continuous dividend yield (default 0.0).
    steps: depth of the LR tree used for each price evaluation (coerced to
    odd internally by leisen_reimer_american_price).

    Raises (no fallback) if c_market is None or the pricer itself errors.
    """
    return _brute_force_bisect(leisen_reimer_american_price, c_market, S, K, T, r, cp, q, steps, label="LR brute_force")


def _brute_force_bisect_batch(pricer_batch, c_market, S, K, T, r, cp, q=0.0, steps=200,
                               max_iter=50, tol=1e-4, label="batched bisection"):
    """Batched counterpart to _brute_force_bisect: bisects sigma for an
    ARRAY of strikes at once against a batched tree pricer
    (crr_american_price_batch / leisen_reimer_american_price_batch), instead
    of solving one strike per Python-level call.

    Why this is safe to run a fixed iteration count (no per-row early exit
    on `tol`, unlike the scalar version): 50 halvings of the [1e-4, 5.0]
    starting bracket shrink it to ~4.4e-12 width, several orders below the
    1e-4 price-tolerance the scalar solver used -- so running the full
    max_iter for every row (even ones that "converged" earlier) costs
    nothing in accuracy, and is what makes this batchable at all (every row
    walks the same number of iterations in lockstep, one vectorized pricer
    call per iteration instead of one Python call per row per iteration).

    Boundary clamps (c_market outside [price_lo, price_hi] for a given row)
    reproduce the scalar version's behavior: a genuine bisection result at
    the 1e-4/5.0 bound, not a fabricated default, applied per-row via
    np.where rather than a per-row branch.

    Returns an array of sigmas, one per strike in K.
    """
    K = np.asarray(K, dtype=float)
    c_market = np.asarray(c_market, dtype=float)
    n = K.shape[0]
    cp_arr = np.broadcast_to(np.asarray(cp, dtype=bool), (n,))

    lo = np.full(n, 1e-4)
    hi = np.full(n, 5.0)
    price_lo = pricer_batch(S, K, T, r, lo, q, cp_arr, steps)
    price_hi = pricer_batch(S, K, T, r, hi, q, cp_arr, steps)

    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        price_mid = pricer_batch(S, K, T, r, mid, q, cp_arr, steps)
        go_hi = price_mid > c_market
        hi = np.where(go_hi, mid, hi)
        lo = np.where(go_hi, lo, mid)

    result = 0.5 * (lo + hi)
    below = c_market <= price_lo
    above = c_market >= price_hi
    result = np.where(below, 1e-4, np.where(above, 5.0, result))

    # Boundary-clamped rows (below/above) are genuine bisection results, not
    # unconverged ones -- only rows that bisected internally need a residual
    # check. NO FALLBACKS: any interior row whose final bracket still misses
    # c_market by more than tol is a real solver failure and must raise,
    # matching the scalar _brute_force_bisect's behavior, rather than
    # silently returning a midpoint that doesn't actually price to market.
    interior = ~(below | above)
    if np.any(interior):
        final_price = pricer_batch(S, K, T, r, result, q, cp_arr, steps)
        residual = np.abs(final_price - c_market)
        bad = interior & (residual > tol)
        if np.any(bad):
            bad_idx = np.nonzero(bad)[0]
            i0 = bad_idx[0]
            raise RuntimeError(
                f"[{label}] {bad_idx.size} of {n} contract(s) failed to converge after "
                f"{max_iter} iterations. First failure: row {i0} (K={K[i0]}), residual "
                f"{residual[i0]:.6e} > tol {tol:.6e}."
            )
    return result


def brute_force_batch(c_market, S, K, T, r, cp, q=0.0, steps=200):
    """Batched counterpart to `brute_force` (CRR) -- solves IV for every
    strike in K at once. See _brute_force_bisect_batch for how/why."""
    return _brute_force_bisect_batch(crr_american_price_batch, c_market, S, K, T, r, cp, q, steps,
                                      label="CRR batch")


def brute_force_lr_batch(c_market, S, K, T, r, cp, q=0.0, steps=200):
    """Batched counterpart to `brute_force_lr` (Leisen-Reimer) -- solves IV
    for every strike in K at once. See _brute_force_bisect_batch for how/why.

    Also used for the "Newton-Raphson" model's smile-chart curve: NR and LR
    invert the exact same leisen_reimer_american_price against the exact
    same (market price, strike) pairs -- Newton and bisection are just two
    algorithms converging on the same root of the same monotonic price(sigma)
    function, and they were verified (live, both calls and puts, several
    strikes) to agree to ~1e-6 in sigma. So this one batched solve produces
    both curves' values without needing a separate batched-Newton
    implementation.
    """
    return _brute_force_bisect_batch(leisen_reimer_american_price_batch, c_market, S, K, T, r, cp, q, steps,
                                      label="LR batch")


def brute_force_mc(c_market, S, K, T, r, cp, q=0.0, simulations=20000, steps=100, seed=42, rand=None):
    """
    Implied volatility for the "MC" (Monte Carlo / Longstaff-Schwartz LSM)
    method, solved via bisection against AmericanLSMPricer's own simulated
    price -- not against either binomial tree.

    Why bisection instead of Newton-Raphson here: Newton needs a locally
    smooth derivative (vega), and a numerical central-difference vega taken
    across two independent LSM simulations would be dominated by regression/
    path noise rather than the true sigma sensitivity. AmericanLSMPricer
    fixes its random draws to a constant seed (see MC.py's
    `_generate_rand(seed=42)`), so re-pricing at different sigma with the
    same seed uses the *same* underlying random path draws (common random
    numbers) -- this makes price(sigma) monotonic enough in sigma for
    bisection to converge cleanly, without needing a derivative at all.

    This means MC gets its own real IV solve against its own real pricer,
    exactly like CRR and Leisen-Reimer do against theirs -- no borrowing
    another model's tree, and no default sigma substituted if this fails.

    rand: optional pre-generated (steps, simulations) standard-normal draw
    array. Since `seed` is fixed, every call to `_generate_rand(seed=seed)`
    -- whether it's the lo/hi probe, one of the 40 mid-bisection evals
    within THIS call, or another strike's call to this same function with
    the same seed/simulations/steps -- produces bit-identical draws. The
    old code regenerated that array from scratch on every single one of
    those calls (~42 regenerations per strike, all producing the exact same
    numbers), which is pure waste once you're bisecting a whole options
    chain (a caller solving 100+ strikes was regenerating and discarding
    the same (steps, simulations) array thousands of times). Pass a
    `rand` computed ONCE by the caller (e.g. one shared array for an entire
    chain-wide smile-curve solve) to skip regeneration entirely; if omitted,
    this generates it once internally, same as before just hoisted out of
    the per-evaluation closure.

    Raises (no fallback) if c_market is None or the pricer itself errors.
    """
    from MC import AmericanLSMPricer

    if c_market is None:
        raise ValueError("[MC brute_force_mc] No market price supplied -- cannot solve IV.")

    is_call = cp

    if rand is None:
        _seed_pricer = AmericanLSMPricer(S, K, T, r, q, 0.3, simulations=simulations, steps=steps,
                                          option=('call' if is_call else 'put'))
        rand = _seed_pricer._generate_rand(seed=seed)

    def _mc_price(sigma):
        pricer = AmericanLSMPricer(S, K, T, r, q, sigma, simulations=simulations, steps=steps,
                                    option=('call' if is_call else 'put'))
        return pricer.price_with_rand(rand)

    tol = 1e-3  # MC price has residual simulation noise; a tighter tol than the tree solvers' won't reliably converge
    max_iter = 40
    lo, hi = 1e-4, 5.0
    price_lo = _mc_price(lo)
    price_hi = _mc_price(hi)

    if c_market <= price_lo:
        return lo
    if c_market >= price_hi:
        return hi

    # Convergence is on the SIGMA INTERVAL WIDTH, not the price residual.
    #
    # Why: the LSM price is only piecewise-smooth in sigma. The Longstaff-
    # Schwartz regression re-fits its continuation-value basis on each
    # re-price, and the exercise boundary it implies moves in discrete jumps
    # as sigma varies -- so price(sigma) is locally step-like rather than
    # continuous, even under common random numbers. Observed live: with a
    # $26.92 target, price(0.2425)=26.565 and price(0.245)=26.956 -- a $0.39
    # jump straddling the target with NO sigma in between that prices within
    # tol=1e-3. Bisecting on the price residual there can never terminate,
    # and the old code burned all 40 iterations and then raised, on a solve
    # that had actually bracketed the root to ~1e-12 in sigma.
    #
    # Bracketing sigma to 1e-6 is the meaningful convergence criterion: it is
    # far finer than any downstream use of the IV, and it is achievable
    # regardless of how the price steps. The price residual is then checked
    # ONCE at the end (with slack for LSM noise/steps) as a sanity gate, so
    # this still raises on a genuinely wrong answer instead of returning a
    # quiet default -- NO FALLBACKS, same contract as the tree solvers.
    sigma_tol = 1e-6
    mid = 0.5 * (lo + hi)
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        price_mid = _mc_price(mid)
        diff = price_mid - c_market
        if hi - lo < sigma_tol:
            break
        if abs(diff) < tol:
            return mid
        if price_mid > c_market:
            hi = mid
        else:
            lo = mid

    result = 0.5 * (lo + hi)
    residual = abs(_mc_price(result) - c_market)

    # Residual sanity gate, sized to the LSM price step rather than to a flat
    # multiple of tol.
    #
    # A fixed slack (e.g. 10x tol) does not work here, and measuring it proves
    # why: on a QQQ-like 580 call the bracket converges to sigma in
    # [0.22957925, 0.22957985] -- within 4e-5 of the true sigma -- yet the
    # price residual there is 1.05e-1, because the LSM price jumps ~$0.10
    # across that step. No sigma prices closer than the step is wide, so
    # judging the solve by a tol-relative residual would reject a demonstrably
    # correct answer. That is the same false failure this fix exists to remove,
    # just moved from the loop to the exit check.
    #
    # The invariant that IS meaningful for a step function: the final bracket
    # must actually straddle the target, i.e. the residual must be no larger
    # than the price step across [lo, hi] (plus tol). If price(sigma) were
    # smooth this collapses to the usual tight residual check, since the step
    # across a ~1e-6-wide bracket is then ~0. If the bracket does NOT contain
    # the target -- noise broke monotonicity, or the root was never bracketed
    # -- the residual exceeds the step and this raises, preserving the
    # NO FALLBACKS contract shared with the tree solvers.
    price_step = abs(_mc_price(hi) - _mc_price(lo))
    gate = max(tol * 10, price_step + tol)
    if residual > gate:
        raise RuntimeError(
            f"[MC brute_force_mc] Failed to converge after {max_iter} iterations. Final residual: "
            f"{residual:.6e} exceeds gate {gate:.6e} (tol {tol:.6e}, LSM price step across final "
            f"bracket {price_step:.6e}). Final sigma bracket: [{lo:.8f}, {hi:.8f}] -- it does not "
            f"straddle the target price {c_market:.6f}. Try increasing simulations/steps."
        )
    return result
