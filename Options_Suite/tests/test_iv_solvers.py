"""
Tests for implied-volatility solvers: Newton-Raphson and brute-force bisection.

Covers:
  - NewtonRaphsonIV.implied_volatility_nr recovers original sigma
  - bruteforceimpliedvol.brute_force recovers original sigma
  - ValueError on None market price
"""

import math
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _black_scholes_price(S, K, T, r, sigma, cp, q=0.0):
    """European Black-Scholes price with dividend yield."""
    from scipy.stats import norm
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if cp:
        return S * math.exp(-q * T) * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    else:
        return K * math.exp(-r * T) * norm.cdf(-d2) - S * math.exp(-q * T) * norm.cdf(-d1)


# Standard test parameters used across solver tests
S, K, T, r, q = 100.0, 100.0, 0.5, 0.05, 0.0
TRUE_SIGMA = 0.30


# ---------------------------------------------------------------------------
# Newton-Raphson IV tests
# ---------------------------------------------------------------------------

class TestNewtonRaphsonIV:
    """Verify that implied_volatility_nr recovers a known sigma."""

    @pytest.mark.unit
    def test_recovers_sigma_for_call(self):
        """Price an option at known sigma, then verify the NR IV solver
        recovers sigma to within 1e-3."""
        from NewtonRaphsonIV import implied_volatility_nr

        c_market = _black_scholes_price(S, K, T, r, TRUE_SIGMA, cp=True, q=q)
        sigma_est, converged = implied_volatility_nr(
            c_market, S, K, T, r, cp=True, q=q, seed=0.25
        )
        assert converged, "NR solver did not converge"
        assert abs(sigma_est - TRUE_SIGMA) < 1e-3, \
            f"NR sigma {sigma_est:.6f} != {TRUE_SIGMA:.6f}"

    @pytest.mark.unit
    def test_recovers_sigma_for_put(self):
        """Same recovery test for puts."""
        from NewtonRaphsonIV import implied_volatility_nr

        c_market = _black_scholes_price(S, K, T, r, TRUE_SIGMA, cp=False, q=q)
        sigma_est, converged = implied_volatility_nr(
            c_market, S, K, T, r, cp=False, q=q, seed=0.25
        )
        assert converged, "NR solver did not converge"
        assert abs(sigma_est - TRUE_SIGMA) < 1e-3

    @pytest.mark.unit
    def test_recovers_sigma_with_dividend(self):
        """Recovery works with non-zero dividend yield."""
        from NewtonRaphsonIV import implied_volatility_nr

        c_market = _black_scholes_price(S, K, T, r, TRUE_SIGMA, cp=True, q=0.02)
        sigma_est, converged = implied_volatility_nr(
            c_market, S, K, T, r, cp=True, q=0.02, seed=0.25
        )
        assert converged
        assert abs(sigma_est - TRUE_SIGMA) < 1e-3

    @pytest.mark.unit
    def test_nr_american_recovers_sigma(self):
        """The American NR solver (implied_volatility_nr_american) should also
        recover a known sigma when pricing through the Leisen-Reimer tree."""
        from NewtonRaphsonIV import implied_volatility_nr_american
        from american_binomial import leisen_reimer_american_price

        market_price = leisen_reimer_american_price(S, K, T, r, TRUE_SIGMA, q, cp=True, steps=200)
        sigma_est, converged = implied_volatility_nr_american(
            market_price, S, K, T, r, cp=True, q=q, seed=0.25, steps=200
        )
        assert converged, "NR American solver did not converge"
        assert abs(sigma_est - TRUE_SIGMA) < 2e-3, \
            f"NR American sigma {sigma_est:.6f} != {TRUE_SIGMA:.6f}"

    @pytest.mark.unit
    def test_none_market_price_raises(self):
        """The Newton-Raphson solver doesn't have an explicit None guard,
        so a None market price causes a TypeError (unlike the brute-force
        solver which raises ValueError)."""
        from NewtonRaphsonIV import implied_volatility_nr

        with pytest.raises(TypeError):
            implied_volatility_nr(None, S, K, T, r, cp=True, q=q, seed=0.25)


# ---------------------------------------------------------------------------
# Brute-force (CRR bisection) IV tests
# ---------------------------------------------------------------------------

class TestBruteForceIV:
    """Verify that bruteforceimpliedvol.brute_force recovers a known sigma."""

    @pytest.mark.unit
    def test_recovers_sigma_for_call(self):
        """Bisect via CRR tree to recover a known sigma."""
        from bruteforceimpliedvol import brute_force
        from american_binomial import crr_american_price

        # Price using the CRR tree (same tree the solver inverts)
        c_market = crr_american_price(S, K, T, r, TRUE_SIGMA, q, cp=True, steps=200)
        sigma_est = brute_force(c_market, S, K, T, r, cp=True, q=q, steps=200)

        assert abs(sigma_est - TRUE_SIGMA) < 2e-3, \
            f"Brute-force sigma {sigma_est:.6f} != {TRUE_SIGMA:.6f}"

    @pytest.mark.unit
    def test_recovers_sigma_for_put(self):
        """Same for puts."""
        from bruteforceimpliedvol import brute_force
        from american_binomial import crr_american_price

        c_market = crr_american_price(S, K, T, r, TRUE_SIGMA, q, cp=False, steps=200)
        sigma_est = brute_force(c_market, S, K, T, r, cp=False, q=q, steps=200)
        assert abs(sigma_est - TRUE_SIGMA) < 2e-3

    @pytest.mark.unit
    def test_brute_force_lr_recovers_sigma(self):
        """The Leisen-Reimer variant of the brute-force solver also recovers."""
        from bruteforceimpliedvol import brute_force_lr
        from american_binomial import leisen_reimer_american_price

        c_market = leisen_reimer_american_price(S, K, T, r, TRUE_SIGMA, q, cp=True, steps=200)
        sigma_est = brute_force_lr(c_market, S, K, T, r, cp=True, q=q, steps=200)
        assert abs(sigma_est - TRUE_SIGMA) < 2e-3

    @pytest.mark.unit
    def test_none_market_price_raises_value_error(self):
        """Passing None as the market price must raise ValueError."""
        from bruteforceimpliedvol import brute_force

        with pytest.raises(ValueError, match="No market price"):
            brute_force(None, S, K, T, r, cp=True, q=q, steps=200)

    @pytest.mark.unit
    def test_brute_force_mc_none_raises_value_error(self):
        """MC brute force also raises ValueError on None."""
        from bruteforceimpliedvol import brute_force_mc

        with pytest.raises(ValueError, match="No market price"):
            brute_force_mc(None, S, K, T, r, cp=True, q=q, simulations=2000, steps=50)

    @pytest.mark.unit
    def test_brute_force_inverts_nr_sigma(self):
        """Cross-check: brute_force (CRR bisection) and NR (European) should
        return similar sigmas for an ATM non-dividend call where early
        exercise has near-zero value."""
        from NewtonRaphsonIV import implied_volatility_nr
        from bruteforceimpliedvol import brute_force
        from american_binomial import crr_american_price

        # Price via BS — used as market price for both solvers
        c_market = _black_scholes_price(S, K, T, r, TRUE_SIGMA, cp=True, q=q)

        nr_sig, _ = implied_volatility_nr(c_market, S, K, T, r, cp=True, q=q, seed=0.25)
        bf_sig = brute_force(c_market, S, K, T, r, cp=True, q=q, steps=200)

        # Both should be close to TRUE_SIGMA for an ATM call with no dividends
        assert abs(nr_sig - TRUE_SIGMA) < 1e-3
        assert abs(bf_sig - TRUE_SIGMA) < 2e-3

# ---------------------------------------------------------------------------
# Identifiability: a price pinned at intrinsic carries NO volatility signal
# ---------------------------------------------------------------------------


class TestAmericanIVIdentifiability:
    """A deep-ITM American option can sit exactly ON the early-exercise
    boundary: early exercise is optimal, time value is zero, and the price is
    EXACTLY intrinsic for every sigma below some boundary value. Vega is then
    identically zero over a whole interval, so no unique implied vol exists.

    Measured live (S=550, K=605, T=0.25, r=0.05, q=0, put): Leisen-Reimer and
    Barone-Adesi-Whaley BOTH return exactly 55.00000 -- the intrinsic value --
    at sigma = 0.10, 0.12 AND 0.15. The solver used to answer 0.1559 with
    converged=True for all of them: a 30% error on a 0.12 input, reported as
    a solved number.
    """

    # Pinned-at-intrinsic case, in the flat region.
    DEEP_ITM_PUT = dict(S=550.0, K=605.0, T=0.25, r=0.05, q=0.0, cp=False)

    def _price(self, sigma, **kw):
        from american_binomial import leisen_reimer_american_price

        p = {**self.DEEP_ITM_PUT, **kw}
        return float(
            leisen_reimer_american_price(
                p["S"], p["K"], p["T"], p["r"], sigma, p["q"], p["cp"]
            )
        )

    def _solve(self, price, **kw):
        from NewtonRaphsonIV import implied_volatility_nr_american

        p = {**self.DEEP_ITM_PUT, **kw}
        return implied_volatility_nr_american(
            price, p["S"], p["K"], p["T"], p["r"], p["cp"], q=p["q"], seed=0.2
        )

    @pytest.mark.unit
    def test_the_setup_really_is_degenerate(self):
        """Guard the premise: the price must be intrinsic and flat in sigma.

        If this ever fails the rest of the class is testing nothing, so assert
        the condition rather than assuming it.
        """
        intrinsic = self.DEEP_ITM_PUT["K"] - self.DEEP_ITM_PUT["S"]
        prices = [self._price(sig) for sig in (0.10, 0.12, 0.15)]
        assert all(abs(p - intrinsic) < 1e-9 for p in prices), prices
        assert len(set(prices)) == 1, "prices must be identical across sigma"

    @pytest.mark.unit
    @pytest.mark.parametrize("sigma", [0.10, 0.12, 0.15])
    def test_unidentifiable_iv_is_not_reported_as_converged(self, sigma):
        """The core fix: do not claim a solved vol when none exists."""
        _sigma_est, converged = self._solve(self._price(sigma))
        assert converged is False, (
            "price is exactly intrinsic, so every sigma below the exercise "
            "boundary reproduces it -- there is no IV to converge on"
        )

    @pytest.mark.unit
    def test_returned_sigma_is_a_usable_upper_bound(self):
        """Not-identifiable still returns the most informative number available.

        The value is the TOP of the flat interval: the market price is
        consistent with any vol at or below it, so it is a genuine upper
        bound, not a guess. Verified by repricing at it.
        """
        intrinsic = self.DEEP_ITM_PUT["K"] - self.DEEP_ITM_PUT["S"]
        sigma_est, _converged = self._solve(self._price(0.12))
        assert sigma_est > 0.0
        # Repriced with Leisen-Reimer, which is BOTH how the market price was
        # made here and the solver's own default pricer -- no cross-model gap
        # to explain away.
        assert self._price(sigma_est) == pytest.approx(intrinsic, abs=1e-3), (
            "the returned sigma must still reproduce the market price"
        )

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "sigma,K,cp",
        [
            (0.25, 605.0, False),  # same deep-ITM strike, ABOVE the boundary
            (0.45, 605.0, False),
            (0.12, 550.0, False),  # ATM put
            (0.12, 550.0, True),  # ATM call
            (0.12, 495.0, False),  # OTM put
            (0.12, 605.0, True),  # OTM call
            (0.12, 495.0, True),  # ITM call
        ],
    )
    def test_identifiable_cases_still_converge(self, sigma, K, cp):
        """Regression guard: the fix must not reject solvable options.

        Every one of these moves the price by 0.17-1.07 per vol point against
        a 0.0001 tolerance -- three orders of magnitude clear of the guard --
        so none of them is near the threshold.
        """
        price = self._price(sigma, K=K, cp=cp)
        _sigma_est, converged = self._solve(price, K=K, cp=cp)
        assert converged is True, f"K={K} cp={cp} sigma={sigma} should solve"

    @pytest.mark.unit
    def test_round_trip_is_exact_where_iv_is_identifiable(self):
        """Above the boundary the same deep-ITM strike solves essentially exactly.

        Leisen-Reimer prices it and Leisen-Reimer inverts it -- one model end
        to end -- so there is no cross-model residual to absorb. Measured
        worst case across the whole call/put x strike x vol x dividend grid is
        4e-05 relative, which is why this asserts 1e-4 rather than the 1e-3
        that a Barone-Adesi-Whaley default needed.
        """
        sigma_est, converged = self._solve(self._price(0.25))
        assert converged is True
        assert sigma_est == pytest.approx(0.25, rel=1e-4)

    @pytest.mark.unit
    def test_barone_adesi_whaley_is_still_available_explicitly(self):
        """Switching the DEFAULT must not remove BAW as an option."""
        from NewtonRaphsonIV import implied_volatility_nr_american
        from barone_adesi_whaley import baw_american_price

        p = self.DEEP_ITM_PUT
        price = float(
            baw_american_price(p["S"], p["K"], p["T"], p["r"], 0.25, p["q"], False, 200)
        )
        sigma_est, converged = implied_volatility_nr_american(
            price, p["S"], p["K"], p["T"], p["r"], p["cp"], q=p["q"], seed=0.2,
            pricer=baw_american_price,
        )
        assert converged is True
        assert sigma_est == pytest.approx(0.25, rel=1e-3)


class TestEuropeanIVIdentifiability:
    """The same defect in the EUROPEAN solver, reached by a different route.

    implied_volatility_nr has no early-exercise boundary, so it is immune to
    the flat-intrinsic floor that breaks the American solver -- it recovers
    0.12 exactly on the K=605 put where the American one could not. It fails
    by SATURATION instead: far enough from the money the Black-Scholes price
    is numerically pinned to its asymptote (0 for a runaway OTM strike,
    K*exp(-rT) / S*exp(-qT) for deep ITM), and a whole range of sigma
    reproduces it.

    Measured (S=550, T=0.25, r=0.05, q=0, put): a K=300 put prices to 7e-136
    at sigma=0.05 and 7e-25 at sigma=0.12 -- a difference of 7e-25 against a
    price tolerance of 1e-4, i.e. the same price as far as the solver can
    tell -- and it answered 0.28481 with converged=True for both. The number
    was a function of the seed and the iteration path, not of the price.

    Note the contrast with the American boundary case, which is degenerate in
    a strictly stronger sense: there the price is EXACTLY intrinsic, bit for
    bit, at every sigma below the boundary. Here the prices differ, just by
    astronomically less than anything the solver treats as a difference. That
    is why this class asserts indistinguishability at `tol` rather than float
    equality the way TestAmericanIVIdentifiability can.
    """

    TOL = 1e-4  # implied_volatility_nr's default price tolerance

    BASE = dict(S=550.0, T=0.25, r=0.05, q=0.0)

    def _price(self, K, sigma, cp, q=None):
        from NewtonRaphsonIV import black_scholes_func

        b = self.BASE
        return float(
            black_scholes_func(
                b["S"], K, b["T"], b["r"], sigma, cp, b["q"] if q is None else q
            )
        )

    def _solve(self, price, K, cp, q=None):
        from NewtonRaphsonIV import implied_volatility_nr

        b = self.BASE
        return implied_volatility_nr(
            price, b["S"], K, b["T"], b["r"], cp, q=b["q"] if q is None else q
        )

    @pytest.mark.unit
    @pytest.mark.parametrize("K,cp", [(300.0, False), (1000.0, True)])
    def test_the_setup_really_is_degenerate(self, K, cp):
        """Guard the premise, so this class cannot silently test nothing."""
        lo, hi = (self._price(K, sig, cp) for sig in (0.05, 0.12))
        assert abs(hi - lo) < self.TOL, (
            f"K={K} cp={cp} must price indistinguishably across a 7-vol-point "
            f"spread to be degenerate: {lo!r} vs {hi!r}"
        )

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "K,cp,sigma",
        [
            (300.0, False, 0.05),   # far-OTM put, price pinned at 0
            (300.0, False, 0.12),
            (1000.0, True, 0.05),   # far-OTM call, price pinned at 0
            (1000.0, True, 0.12),
            (800.0, False, 0.05),   # deep-ITM put, price pinned at its floor
            (400.0, True, 0.05),    # deep-ITM call, price pinned at its floor
        ],
    )
    def test_saturated_price_is_not_reported_as_converged(self, K, cp, sigma):
        """The fix: an unidentifiable European price must not claim a solve."""
        _est, converged = self._solve(self._price(K, sigma, cp), K, cp)
        assert converged is False, (
            f"K={K} cp={cp} sigma={sigma}: price is saturated, so many vols "
            "reproduce it -- there is no IV to converge on"
        )

    @pytest.mark.unit
    @pytest.mark.parametrize("K", [450.0, 500.0, 525.0, 550.0, 575.0, 600.0, 650.0])
    @pytest.mark.parametrize("cp", [True, False])
    @pytest.mark.parametrize("sigma", [0.12, 0.25, 0.45])
    @pytest.mark.parametrize("q", [0.0, 0.02])
    def test_guard_does_not_reject_identifiable_strikes(self, K, cp, sigma, q):
        """Over-rejection is the dangerous direction -- pin it broadly.

        Every strike here has real time value, so the price does determine a
        vol and the solver must both converge AND recover it. 84 combinations
        of strike x right x vol x dividend.
        """
        est, converged = self._solve(self._price(K, sigma, cp, q), K, cp, q)
        assert converged is True, f"K={K} cp={cp} sigma={sigma} q={q} should solve"
        assert est == pytest.approx(sigma, rel=1e-3)

    @pytest.mark.unit
    def test_guard_is_conservative_across_the_grid(self):
        """The guard must never reject a solve that was accurate to 1%.

        Unlike the American boundary case -- bimodal, degenerate points
        probing ~1000x below solvable ones -- this degrades CONTINUOUSLY as
        vega decays, so no threshold partitions it cleanly. What the tol=1e-4
        default does guarantee, measured over the whole grid, is the safe
        direction: nothing accurate to 1% is ever flagged, and nothing worse
        than 10% is ever missed. This asserts that property directly rather
        than hard-coding the counts, so it keeps meaning if the grid changes.
        """
        strikes = (300.0, 350.0, 400.0, 450.0, 500.0, 525.0,
                   550.0, 575.0, 600.0, 650.0, 700.0, 800.0, 900.0, 1000.0)
        vols = (0.05, 0.08, 0.12, 0.18, 0.25, 0.35, 0.45, 0.60)
        rejected_but_accurate, accepted_but_wrong = [], []
        for cp in (True, False):
            for K in strikes:
                for sigma in vols:
                    for q in (0.0, 0.02):
                        est, conv = self._solve(self._price(K, sigma, cp, q), K, cp, q)
                        err = abs(est - sigma) / sigma
                        if conv and err > 0.10:
                            accepted_but_wrong.append((K, cp, sigma, q, est, err))
                        if not conv and err <= 0.01:
                            rejected_but_accurate.append((K, cp, sigma, q, est, err))
        assert rejected_but_accurate == [], (
            "guard rejected a solve that was accurate to 1% -- it has become "
            f"over-aggressive, and vol_manager RAISES on that: {rejected_but_accurate[:5]}"
        )
        assert accepted_but_wrong == [], (
            "guard passed a solve off by more than 10% as converged: "
            f"{accepted_but_wrong[:5]}"
        )

    @pytest.mark.unit
    def test_american_solver_is_unaffected_by_the_shared_helper(self):
        """Both solvers now share _vol_is_identifiable; the American case is
        the one that already had coverage, so assert the refactor moved no
        behaviour: the K=605 boundary put that the European solver handles
        fine must still be rejected by the American one."""
        from NewtonRaphsonIV import implied_volatility_nr_american
        from american_binomial import leisen_reimer_american_price

        price = float(leisen_reimer_american_price(550.0, 605.0, 0.25, 0.05, 0.12, 0.0, False, 200))
        _est, converged = implied_volatility_nr_american(
            price, 550.0, 605.0, 0.25, 0.05, False, q=0.0, seed=0.2
        )
        assert converged is False
        # ... while the European solver, having no boundary, recovers it.
        est_eu, conv_eu = self._solve(self._price(605.0, 0.12, False), 605.0, False)
        assert conv_eu is True
        assert est_eu == pytest.approx(0.12, rel=1e-3)
