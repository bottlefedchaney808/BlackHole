"""
Unit tests for bs_gamma and bs_gamma_vec dividend discount factor fix (FIX_PLAN_20260728, Issue 6).

Black-Scholes gamma with continuous dividend yield q is:
  Γ = e^{-qT} * N'(d1) / (S * σ * √T)

The code was missing the exp(-q*T) factor, causing gamma to be overstated
by e^{qT} whenever q > 0.

This test verifies that:
1. bs_gamma includes the exp(-q*T) factor
2. bs_gamma_vec includes the exp(-q*T) factor
3. With q > 0, the result is smaller than with q = 0 by the appropriate factor
4. The functions produce consistent results between scalar and vectorized versions
"""
import pytest
import math
import numpy as np

import dealer_positioning as dp


class TestBsGammaDividendDiscount:
    """Test bs_gamma with dividend yields q > 0."""

    @pytest.mark.unit
    def test_bs_gamma_with_zero_dividend(self):
        """
        bs_gamma with q=0 should work as before (baseline test).
        """
        S = 100.0      # spot
        K = 100.0      # strike (ATM)
        T = 0.25       # 3 months
        r = 0.05       # risk-free rate
        q = 0.0        # dividend yield
        sigma = 0.20   # volatility

        gamma = dp.bs_gamma(S, K, T, r, q, sigma)

        # Gamma should be positive and non-zero
        assert gamma > 0, f"Expected positive gamma, got {gamma}"
        assert not math.isnan(gamma), "Gamma should not be NaN"

    @pytest.mark.unit
    def test_bs_gamma_with_positive_dividend(self):
        """
        bs_gamma with q > 0 should produce smaller gamma than q=0 by factor e^{-qT}.

        For the same S, K, T, r, sigma:
        - gamma(q=0) = N'(d1) / (S * σ * √T)
        - gamma(q>0) = e^{-qT} * N'(d1) / (S * σ * √T) = e^{-qT} * gamma(q=0)

        So: gamma(q>0) / gamma(q=0) = e^{-qT}
        """
        S = 100.0
        K = 100.0
        T = 0.25       # 3 months
        r = 0.05
        sigma = 0.20

        # Calculate gamma with q=0
        gamma_no_div = dp.bs_gamma(S, K, T, r, 0.0, sigma)

        # Calculate gamma with SPY-like dividend (q≈1.2%)
        q = 0.012
        gamma_with_div = dp.bs_gamma(S, K, T, r, q, sigma)

        # Expected discount factor
        exp_neg_qT = math.exp(-q * T)

        # gamma_with_div should be approximately e^{-qT} * gamma_no_div
        expected_gamma = exp_neg_qT * gamma_no_div

        # Check the relationship with tight tolerance
        relative_error = abs(gamma_with_div - expected_gamma) / expected_gamma
        assert relative_error < 1e-10, (
            f"bs_gamma with q={q} not properly discounted.\n"
            f"  Expected: {expected_gamma} (= e^{{{-q}*{T}}} * {gamma_no_div})\n"
            f"  Got:      {gamma_with_div}\n"
            f"  Error:    {relative_error:.2e}"
        )

    @pytest.mark.unit
    def test_bs_gamma_discount_factor_magnitude(self):
        """
        Test that the discount factor e^{-qT} matches the observed ratio
        for a higher dividend yield (e.g., q=5%).
        """
        S = 100.0
        K = 105.0      # slightly OTM call
        T = 1.0        # 1 year
        r = 0.05
        sigma = 0.20
        q = 0.05       # 5% dividend yield

        gamma_no_div = dp.bs_gamma(S, K, T, r, 0.0, sigma)
        gamma_with_div = dp.bs_gamma(S, K, T, r, q, sigma)

        exp_neg_qT = math.exp(-q * T)

        # The ratio should match the discount factor
        observed_ratio = gamma_with_div / gamma_no_div
        expected_ratio = exp_neg_qT

        relative_error = abs(observed_ratio - expected_ratio) / expected_ratio
        assert relative_error < 1e-10, (
            f"Discount ratio mismatch for q={q}, T={T}.\n"
            f"  Expected: {expected_ratio} = e^{{{-q}*{T}}}\n"
            f"  Observed: {observed_ratio}\n"
            f"  Error:    {relative_error:.2e}"
        )

    @pytest.mark.unit
    def test_bs_gamma_vec_with_dividend(self):
        """
        Test the vectorized bs_gamma_vec with dividend yields.

        Verify that bs_gamma_vec produces results consistent with bs_gamma
        when given a single element.
        """
        S = 100.0
        K_array = np.array([95.0, 100.0, 105.0])  # multiple strikes
        T_array = np.array([0.25, 0.25, 0.25])     # same time
        r = 0.05
        q = 0.012      # 1.2% dividend
        sigma = 0.20

        # Vectorized version
        gamma_vec = dp.bs_gamma_vec(S, K_array, T_array, r, q, sigma)

        # Scalar version for comparison
        gamma_scalar = np.array([
            dp.bs_gamma(S, K_array[i], T_array[i], r, q, sigma)
            for i in range(len(K_array))
        ])

        # Results should match closely
        np.testing.assert_allclose(gamma_vec, gamma_scalar, rtol=1e-10,
            err_msg="bs_gamma_vec results should match bs_gamma for each strike")

    @pytest.mark.unit
    def test_bs_gamma_vec_discount_consistency(self):
        """
        Test that bs_gamma_vec applies the discount factor consistently
        across multiple strikes.
        """
        S = 100.0
        K_array = np.array([90.0, 95.0, 100.0, 105.0, 110.0])
        T_array = np.array([0.5, 0.5, 0.5, 0.5, 0.5])
        r = 0.05
        sigma = 0.20

        # Calculate with q=0
        gamma_no_div = dp.bs_gamma_vec(S, K_array, T_array, r, 0.0, sigma)

        # Calculate with q>0
        q = 0.015
        gamma_with_div = dp.bs_gamma_vec(S, K_array, T_array, r, q, sigma)

        # Expected discount
        exp_neg_qT = math.exp(-q * 0.5)  # T=0.5 for all

        # Verify the discount is applied consistently
        expected_gamma = exp_neg_qT * gamma_no_div

        np.testing.assert_allclose(gamma_with_div, expected_gamma, rtol=1e-10,
            err_msg=f"bs_gamma_vec should apply e^(-qT) discount consistently")

    @pytest.mark.unit
    def test_bs_gamma_edge_cases_with_dividend(self):
        """
        Test edge cases (T→0, very high q, etc.) to ensure the dividend
        discount doesn't break boundary conditions.
        """
        S = 100.0
        K = 100.0
        r = 0.05
        sigma = 0.20

        # T → 0: gamma blows up (1/sqrt(T) dominates), but should be finite and positive
        gamma_short_t = dp.bs_gamma(S, K, 1e-6, r, 0.05, sigma)
        assert gamma_short_t > 0 and math.isfinite(gamma_short_t), (
            f"Gamma should be finite and positive for very short T, got {gamma_short_t}"
        )

        # q = 0: should be same as before (no discount)
        gamma_q0 = dp.bs_gamma(S, K, 0.25, r, 0.0, sigma)
        assert gamma_q0 > 0, "Gamma with q=0 should be positive"

        # Large q (50%) should still produce valid results
        gamma_large_q = dp.bs_gamma(S, K, 0.25, r, 0.50, sigma)
        expected_discount = math.exp(-0.50 * 0.25)
        expected_gamma = expected_discount * gamma_q0
        assert abs(gamma_large_q - expected_gamma) < 1e-10, (
            f"Large q=0.50 test failed"
        )

    @pytest.mark.unit
    def test_bs_gamma_vec_with_mixed_ttes(self):
        """
        Test bs_gamma_vec with different times-to-expiry to ensure
        each element gets its own exp(-q*T) discount.
        """
        S = 100.0
        K_array = np.array([100.0, 100.0, 100.0])
        T_array = np.array([0.1, 0.25, 0.5])  # different TTEs
        r = 0.05
        q = 0.02   # 2% dividend
        sigma = 0.20

        gamma_vec = dp.bs_gamma_vec(S, K_array, T_array, r, q, sigma)

        # Each should have its own discount
        gamma_q0_vec = dp.bs_gamma_vec(S, K_array, T_array, r, 0.0, sigma)

        # Apply discount factors individually
        for i in range(len(T_array)):
            discount = math.exp(-q * T_array[i])
            expected = discount * gamma_q0_vec[i]
            relative_error = abs(gamma_vec[i] - expected) / expected
            assert relative_error < 1e-10, (
                f"Element {i} (T={T_array[i]}) discount mismatch"
            )

    @pytest.mark.unit
    def test_bs_gamma_and_vec_agreement(self):
        """
        Verify that single-element calls to bs_gamma_vec agree with bs_gamma.
        """
        S = 100.0
        K = 102.5
        T = 0.33
        r = 0.04
        q = 0.018
        sigma = 0.25

        scalar_result = dp.bs_gamma(S, K, T, r, q, sigma)
        vector_result = dp.bs_gamma_vec(S, np.array([K]), np.array([T]), r, q, sigma)[0]

        assert abs(scalar_result - vector_result) < 1e-12, (
            f"bs_gamma and bs_gamma_vec should agree:\n"
            f"  bs_gamma:     {scalar_result}\n"
            f"  bs_gamma_vec: {vector_result}"
        )
