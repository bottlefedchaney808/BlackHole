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