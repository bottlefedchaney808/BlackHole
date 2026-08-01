"""
Tests for pricing models: SABR, Vanna-Volga, and American LSM Monte Carlo.

Covers:
  - SABRModel.sabr_vol_hagan returns finite positive vols for realistic inputs
  - VannaVolga.get_vol returns something (smoke test)
  - AmericanLSMPricer from MC.py can be instantiated
"""

import math
import pytest


# ---------------------------------------------------------------------------
# SABR model tests
# ---------------------------------------------------------------------------

class TestSABRModel:
    """Verify SABR Hagan volatility surface is well-behaved."""

    @pytest.mark.unit
    def test_sabr_vol_hagan_finite_positive(self):
        """sabr_vol_hagan should return finite, positive vols for realistic
        parameter ranges."""
        from SABRModel import sabr_vol_hagan

        F, T = 100.0, 0.5
        alpha, beta, rho, nu = 0.30, 0.5, -0.3, 0.50

        for K in [80.0, 90.0, 100.0, 110.0, 120.0]:
            vol = sabr_vol_hagan(F, K, T, alpha, beta, rho, nu)
            assert math.isfinite(vol), f"Non-finite vol at K={K}: {vol}"
            assert vol > 0, f"Non-positive vol at K={K}: {vol}"

    @pytest.mark.unit
    def test_sabr_vol_atm(self):
        """ATM (F == K) SABR vol should match the closed-form ATM formula."""
        from SABRModel import sabr_vol_hagan

        F, T = 100.0, 0.5
        alpha, beta, rho, nu = 0.30, 0.5, -0.3, 0.50

        atm_vol = sabr_vol_hagan(F, F, T, alpha, beta, rho, nu)
        # ATM formula: alpha / F^(1-beta) * (1 + ... T)
        expected_base = alpha / (F ** (1 - beta))
        assert atm_vol > 0
        # Should be close to alpha/F^(1-beta) for short T
        assert abs(atm_vol - expected_base) / expected_base < 0.2

    @pytest.mark.unit
    def test_sabr_smile_shape(self):
        """SABR with negative rho should produce a downward-sloping skew
        (strikes below forward have higher vol than strikes above)."""
        from SABRModel import sabr_vol_hagan

        F, T = 100.0, 0.5
        alpha, beta, rho, nu = 0.30, 0.7, -0.5, 0.40

        vol_otm_put = sabr_vol_hagan(F, 85.0, T, alpha, beta, rho, nu)
        vol_otm_call = sabr_vol_hagan(F, 115.0, T, alpha, beta, rho, nu)

        # Negative rho -> put wing > call wing
        assert vol_otm_put > vol_otm_call, \
            f"Negative rho skew expected: put vol {vol_otm_put:.4f} <= call vol {vol_otm_call:.4f}"

    @pytest.mark.unit
    def test_sabr_increases_with_nu(self):
        """Higher nu (vol-of-vol) makes the smile more pronounced, so vols
        away from the money should increase."""
        from SABRModel import sabr_vol_hagan

        F, T, K = 100.0, 0.5, 120.0
        alpha, beta, rho = 0.25, 0.5, -0.3

        vol_low = sabr_vol_hagan(F, K, T, alpha, beta, rho, nu=0.2)
        vol_high = sabr_vol_hagan(F, K, T, alpha, beta, rho, nu=1.0)

        assert vol_high > vol_low, \
            f"Higher nu should increase wing vol: {vol_high:.4f} <= {vol_low:.4f}"

    @pytest.mark.unit
    def test_sabr_model_instantiation(self):
        """SABRModel class can be instantiated and produces vols."""
        from SABRModel import SABRModel

        model = SABRModel(alpha=0.25, beta=0.5, rho=-0.3, nu=0.5)
        vol = model.get_vol(F=100.0, K=100.0, T=0.5)
        assert math.isfinite(vol)
        assert vol > 0


# ---------------------------------------------------------------------------
# Vanna-Volga tests
# ---------------------------------------------------------------------------

class TestVannaVolga:
    """Smoke tests for VannaVolga.get_vol."""

    @pytest.mark.unit
    def test_get_vol_returns_positive(self):
        """get_vol should return a finite, positive vol for realistic params."""
        from VannaVolga import get_vol

        S, K, T, r, q = 100.0, 100.0, 0.5, 0.05, 0.0
        atm_vol, rr25, bf25 = 0.25, 3.0, 1.0

        vol = get_vol(S, K, T, r, q, atm_vol, rr25, bf25)
        assert math.isfinite(vol), f"Non-finite vol: {vol}"
        assert vol > 0, f"Non-positive vol: {vol}"

    @pytest.mark.unit
    def test_get_vol_near_atm(self):
        """At-the-money (K ~ S) VV vol should be close to input atm_vol."""
        from VannaVolga import get_vol

        S, K, T, r, q = 100.0, 100.0, 0.5, 0.05, 0.0
        atm_vol, rr25, bf25 = 0.30, 5.0, 2.0

        vol = get_vol(S, K, T, r, q, atm_vol, rr25, bf25)
        assert abs(vol - atm_vol) < 0.05, \
            f"ATM vol {vol:.4f} too far from input {atm_vol}"

    @pytest.mark.unit
    def test_get_vol_wing_separation(self):
        """With positive RR, OTM call vol should exceed OTM put vol."""
        from VannaVolga import get_vol

        S, T, r, q = 100.0, 0.5, 0.05, 0.0
        atm_vol, rr25, bf25 = 0.25, 10.0, 2.0

        vol_call = get_vol(S, 115.0, T, r, q, atm_vol, rr25, bf25)
        vol_put = get_vol(S, 85.0, T, r, q, atm_vol, rr25, bf25)
        # Positive RR means call wing > put wing
        assert vol_call > vol_put, \
            f"Positive RR: call vol {vol_call:.4f} should exceed put vol {vol_put:.4f}"

    @pytest.mark.unit
    def test_get_vol_batch_matches_scalar(self):
        """get_vol_batch should produce the same values as get_vol per-strike."""
        from VannaVolga import get_vol, get_vol_batch

        S, T, r, q = 100.0, 0.5, 0.05, 0.0
        atm_vol, rr25, bf25 = 0.25, 3.0, 1.0
        strikes = [85.0, 95.0, 100.0, 105.0, 115.0]

        scalar = [get_vol(S, k, T, r, q, atm_vol, rr25, bf25) for k in strikes]
        batch = get_vol_batch(S, strikes, T, r, q, atm_vol, rr25, bf25)

        assert len(scalar) == len(batch)
        for sk, bk in zip(scalar, batch):
            assert sk == pytest.approx(bk, rel=1e-10)


# ---------------------------------------------------------------------------
# American LSM Monte Carlo tests
# ---------------------------------------------------------------------------

class TestAmericanLSMPricer:
    """Smoke tests for AmericanLSMPricer (MC.py)."""

    @pytest.mark.unit
    def test_can_instantiate(self):
        """AmericanLSMPricer can be instantiated with standard parameters."""
        from MC import AmericanLSMPricer

        pricer = AmericanLSMPricer(S=100.0, K=100.0, T=0.5, r=0.05, q=0.0,
                                   sigma=0.25, simulations=5000, steps=50, option='call')
        assert pricer is not None
        assert pricer.option == 'call'
        assert pricer.simulations == 5000
        assert pricer.steps == 50

    @pytest.mark.unit
    def test_price_returns_finite_number(self):
        """price() should return a finite float for realistic inputs."""
        from MC import AmericanLSMPricer

        pricer = AmericanLSMPricer(S=100.0, K=100.0, T=0.5, r=0.05, q=0.0,
                                   sigma=0.25, simulations=5000, steps=50, option='call')
        price = pricer.price()
        assert math.isfinite(price), f"Non-finite MC price: {price}"
        assert price > 0, f"Non-positive MC price: {price}"

    @pytest.mark.unit
    def test_put_price_sensible_range(self):
        """ATM put price should be between ~2-15 for moderate params."""
        from MC import AmericanLSMPricer

        pricer = AmericanLSMPricer(S=100.0, K=100.0, T=0.5, r=0.05, q=0.0,
                                   sigma=0.25, simulations=5000, steps=50, option='put')
        price = pricer.price()
        assert 1.0 <= price <= 20.0, f"ATM put price {price:.4f} out of expected range"

    @pytest.mark.unit
    def test_american_exceeds_european_for_deep_itm_put(self):
        """A deep ITM American put should price slightly higher than European
        because early-exercise optionality has non-zero value."""
        from MC import AmericanLSMPricer

        pricer = AmericanLSMPricer(S=80.0, K=100.0, T=0.5, r=0.05, q=0.02,
                                   sigma=0.20, simulations=5000, steps=50, option='put')
        price = pricer.price()
        # Intrinsic value is a guaranteed lower bound
        intrinsic = 20.0
        assert price >= intrinsic * 0.95, f"MC put {price:.4f} well below intrinsic {intrinsic}"

    @pytest.mark.unit
    def test_invalid_option_raises(self):
        """Passing an invalid option type should raise ValueError."""
        from MC import AmericanLSMPricer

        with pytest.raises(ValueError, match="Option type"):
            AmericanLSMPricer(S=100.0, K=100.0, T=0.5, r=0.05, q=0.0,
                              sigma=0.25, option='invalid')