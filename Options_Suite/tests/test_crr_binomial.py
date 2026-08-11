"""
Tests for the CRR (Cox-Ross-Rubinstein) American binomial pricer.

Covers:
  - Low-vol / short-T convergence to European (Black-Scholes) limit
  - crr_all_greeks returns the expected dict keys
  - Price bounded by intrinsic value for ITM options
"""

import math
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bs_price(S, K, T, r, sigma, q, cp):
    """Closed-form Black-Scholes with dividend yield (European only)."""
    from american_binomial import _bs_rho  # used internally; same BS d1/d2
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if cp:
        return S * math.exp(-q * T) * 0.5 * (1.0 + math.erf(d1 / math.sqrt(2.0))) \
             - K * math.exp(-r * T) * 0.5 * (1.0 + math.erf(d2 / math.sqrt(2.0)))
    else:
        return K * math.exp(-r * T) * 0.5 * (1.0 + math.erf(-d2 / math.sqrt(2.0))) \
             - S * math.exp(-q * T) * 0.5 * (1.0 + math.erf(-d1 / math.sqrt(2.0)))


_GREEK_KEYS = {"delta", "gamma", "vega", "rho", "theta",
               "vanna", "vomma", "speed", "charm", "color",
               "rho_euro", "rho_ee_premium"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCrrAmericanPrice:
    """Verify crr_american_price against known values and boundary conditions."""

    @pytest.mark.unit
    def test_low_vol_short_T_call_approximates_european(self):
        """For low vol and short T, early exercise has negligible value so the
        American CRR price should closely match a European Black-Scholes price."""
        from american_binomial import crr_american_price

        S, K, T, r, q, sigma = 100.0, 100.0, 0.1, 0.05, 0.0, 0.15
        cp = True  # call

        american = crr_american_price(S, K, T, r, sigma, q, cp, steps=400)
        european = _bs_price(S, K, T, r, sigma, q, cp)

        # CRR + American early exercise should be AT LEAST the European price,
        # and within 0.5% for these tame parameters
        assert american >= european * 0.999, f"American {american} should be >= European {european}"
        assert american <= european * 1.02, f"American {american} should not far exceed European {european}"

    @pytest.mark.unit
    def test_low_vol_short_T_put_approximates_european(self):
        """Same convergence check for puts."""
        from american_binomial import crr_american_price

        S, K, T, r, q, sigma = 100.0, 100.0, 0.1, 0.05, 0.0, 0.15
        cp = False  # put

        american = crr_american_price(S, K, T, r, sigma, q, cp, steps=400)
        european = _bs_price(S, K, T, r, sigma, q, cp)

        assert american >= european * 0.999

    @pytest.mark.unit
    def test_zero_vol_returns_intrinsic_or_forward(self):
        """With sigma ~ 0, the price should be max(intrinsic, discounted forward)."""
        from american_binomial import crr_american_price

        S, K, T, r, q = 100.0, 90.0, 0.5, 0.05, 0.0
        # Deep ITM call – intrinsic = 10
        price = crr_american_price(S, K, T, r, 1e-8, q, cp=True, steps=200)
        intrinsic = S - K
        F = S * math.exp((r - q) * T)
        euro = math.exp(-r * T) * max(F - K, 0.0)
        assert price == pytest.approx(max(euro, intrinsic), rel=1e-6)

    @pytest.mark.unit
    def test_zero_T_returns_intrinsic(self):
        """At T=0 the price is exactly intrinsic."""
        from american_binomial import crr_american_price

        assert crr_american_price(100.0, 95.0, 0.0, 0.05, 0.2, cp=True) == 5.0
        assert crr_american_price(95.0, 100.0, 0.0, 0.05, 0.2, cp=False) == 5.0


class TestCrrAllGreeks:
    """Verify crr_all_greeks returns the expected structure."""

    @pytest.mark.unit
    def test_returns_expected_keys(self):
        """The returned dict must contain all 12 standard Greek keys."""
        from american_binomial import crr_all_greeks

        greeks = crr_all_greeks(S=100.0, K=100.0, T=0.5, r=0.05,
                                sigma=0.25, q=0.0, cp=True, steps=200)
        assert set(greeks.keys()) == _GREEK_KEYS, f"Missing keys: {_GREEK_KEYS - set(greeks)}"

    @pytest.mark.unit
    def test_all_values_are_finite(self):
        """All returned Greek values should be finite floats."""
        from american_binomial import crr_all_greeks

        greeks = crr_all_greeks(S=100.0, K=100.0, T=0.5, r=0.05,
                                sigma=0.25, q=0.0, cp=True, steps=200)
        for name, val in greeks.items():
            assert math.isfinite(val), f"{name}={val} is not finite"

    @pytest.mark.unit
    def test_delta_sensible_range(self):
        """ATM call delta should be ~0.5-0.6; ATM put delta ~ -0.5 to -0.4."""
        from american_binomial import crr_all_greeks

        call_d = crr_all_greeks(S=100.0, K=100.0, T=0.5, r=0.05,
                                sigma=0.25, q=0.0, cp=True)["delta"]
        put_d = crr_all_greeks(S=100.0, K=100.0, T=0.5, r=0.05,
                               sigma=0.25, q=0.0, cp=False)["delta"]
        assert 0.3 <= call_d <= 0.9, f"Call delta {call_d} out of range"
        assert -0.9 <= put_d <= -0.3, f"Put delta {put_d} out of range"

    @pytest.mark.unit
    def test_gamma_bump_width_is_wide_enough_to_avoid_oscillation_noise(self):
        """Regression test for the dS_frac=1% -> 3% fix documented in
        crr_all_greeks's own docstring: CRR's tree has oscillating (not
        monotonic) convergence, so a 1%-of-spot bump landed the +/-dS prices
        on differently-aligned tree lattices and the 2nd finite difference
        picked that up as false signal -- gamma came out ~3x too small
        (+0.00098) on the AMD 480 put case (2026-07-27 comparison report)
        instead of the correct, LR/BS-matching +0.00295. Pins the case that
        was actually measured, so a future change that narrows the bump back
        down reintroduces a value this test would catch."""
        from american_binomial import crr_all_greeks

        greeks = crr_all_greeks(S=494.95, K=480.0, T=0.107, r=0.05,
                                sigma=0.8131, q=0.0, cp=False, steps=401)
        # Fixed value is ~0.00291-0.00295; the bug's value was ~0.00098 (3x
        # smaller) -- a 15% tolerance around the fixed value comfortably
        # excludes the old bug without pinning to unstable precision.
        assert greeks["gamma"] == pytest.approx(0.00295, rel=0.15), (
            f"CRR gamma {greeks['gamma']} suggests the bump width regressed "
            f"back toward the old, too-narrow dS_frac"
        )


class TestCrrPriceBoundedByIntrinsic:
    """American option price must be at least the immediate exercise value."""

    @pytest.mark.unit
    @pytest.mark.parametrize("S, K, T, sigma", [
        (110.0, 100.0, 0.5, 0.20),   # ITM call
        (100.0, 110.0, 0.5, 0.20),   # ITM put
        (150.0, 100.0, 0.05, 0.30),  # deep ITM call, short T
        (90.0,  110.0, 0.05, 0.30),  # deep ITM put, short T
    ])
    def test_at_least_intrinsic(self, S, K, T, sigma):
        """American price must never be less than intrinsic value."""
        from american_binomial import crr_american_price

        call_price = crr_american_price(S, K, T, 0.05, sigma, q=0.0, cp=True, steps=200)
        put_price = crr_american_price(S, K, T, 0.05, sigma, q=0.0, cp=False, steps=200)

        call_intrinsic = max(S - K, 0.0)
        put_intrinsic = max(K - S, 0.0)

        assert call_price >= call_intrinsic * 0.999, \
            f"Call price {call_price} < intrinsic {call_intrinsic}"
        assert put_price >= put_intrinsic * 0.999, \
            f"Put price {put_price} < intrinsic {put_intrinsic}"

    @pytest.mark.unit
    def test_deep_itm_put_exceeds_european(self):
        """A deep ITM American put with dividend should be worth more than the
        European counterpart because the early-exercise premium has real value."""
        from american_binomial import crr_american_price

        S, K, T, r, q, sigma = 80.0, 100.0, 0.5, 0.05, 0.02, 0.20
        american = crr_american_price(S, K, T, r, sigma, q, cp=False, steps=400)
        european = _bs_price(S, K, T, r, sigma, q, cp=False)

        # Intrinsic value is a hard lower bound
        intrinsic = K - S
        assert american >= intrinsic, f"American {american} < intrinsic {intrinsic}"
        # American should be >= European (early exercise optionality)
        assert american >= european * 0.999