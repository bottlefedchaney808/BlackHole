#!/usr/bin/env python3
"""
Direct verification of FIX 6 (bs_gamma/bs_gamma_vec dividend discount fix).

This script directly tests the bs_gamma and bs_gamma_vec functions
without relying on the full test suite infrastructure.
"""
import sys
import math
import numpy as np

# Add the Vol_Suite directory to the path
sys.path.insert(0, '.')

try:
    from dealer_positioning import bs_gamma, bs_gamma_vec, norm_pdf
    print("Successfully imported bs_gamma, bs_gamma_vec")
except ImportError as e:
    print(f"Import error: {e}")
    sys.exit(1)


def test_bs_gamma_with_dividend():
    """Test that bs_gamma includes exp(-q*T) factor."""
    print("\n=== Test 1: bs_gamma with q > 0 ===")

    S = 100.0
    K = 100.0
    T = 0.25
    r = 0.05
    sigma = 0.20

    # Calculate with q=0
    gamma_q0 = bs_gamma(S, K, T, r, 0.0, sigma)
    print(f"gamma(q=0):   {gamma_q0:.10f}")

    # Calculate with q=0.012 (SPY-like)
    q = 0.012
    gamma_q_pos = bs_gamma(S, K, T, r, q, sigma)
    print(f"gamma(q=0.012): {gamma_q_pos:.10f}")

    # Expected: gamma_q_pos = exp(-q*T) * gamma_q0
    exp_neg_qT = math.exp(-q * T)
    expected = exp_neg_qT * gamma_q0
    print(f"exp(-q*T):    {exp_neg_qT:.10f}")
    print(f"expected:     {expected:.10f}")

    error = abs(gamma_q_pos - expected)
    rel_error = error / expected if expected != 0 else 0

    print(f"Error:        {error:.2e}")
    print(f"Rel Error:    {rel_error:.2e}")

    if rel_error < 1e-10:
        print("✓ PASS: bs_gamma includes exp(-q*T) factor")
        return True
    else:
        print(f"✗ FAIL: bs_gamma dividend discount is incorrect")
        return False


def test_bs_gamma_vec_with_dividend():
    """Test that bs_gamma_vec includes exp(-q*T) factor."""
    print("\n=== Test 2: bs_gamma_vec with q > 0 ===")

    S = 100.0
    K_array = np.array([95.0, 100.0, 105.0])
    T_array = np.array([0.25, 0.25, 0.25])
    r = 0.05
    sigma = 0.20

    # Calculate with q=0
    gamma_q0 = bs_gamma_vec(S, K_array, T_array, r, 0.0, sigma)
    print(f"gamma_vec(q=0):   {gamma_q0}")

    # Calculate with q=0.012
    q = 0.012
    gamma_q_pos = bs_gamma_vec(S, K_array, T_array, r, q, sigma)
    print(f"gamma_vec(q=0.012): {gamma_q_pos}")

    # Expected discount
    exp_neg_qT = math.exp(-q * 0.25)
    expected = exp_neg_qT * gamma_q0
    print(f"exp(-q*T):      {exp_neg_qT:.10f}")
    print(f"expected:       {expected}")

    errors = np.abs(gamma_q_pos - expected)
    rel_errors = errors / expected
    max_rel_error = np.max(rel_errors)

    print(f"Max Rel Error:  {max_rel_error:.2e}")

    if max_rel_error < 1e-10:
        print("✓ PASS: bs_gamma_vec includes exp(-q*T) factor")
        return True
    else:
        print(f"✗ FAIL: bs_gamma_vec dividend discount is incorrect")
        return False


def test_scalar_vs_vector_agreement():
    """Test that bs_gamma and bs_gamma_vec agree."""
    print("\n=== Test 3: bs_gamma vs bs_gamma_vec agreement ===")

    S = 100.0
    K = 102.5
    T = 0.33
    r = 0.04
    q = 0.018
    sigma = 0.25

    scalar_result = bs_gamma(S, K, T, r, q, sigma)
    vector_result = bs_gamma_vec(S, np.array([K]), np.array([T]), r, q, sigma)[0]

    print(f"bs_gamma:     {scalar_result:.15f}")
    print(f"bs_gamma_vec: {vector_result:.15f}")

    diff = abs(scalar_result - vector_result)
    print(f"Difference:   {diff:.2e}")

    if diff < 1e-12:
        print("✓ PASS: Scalar and vector versions agree")
        return True
    else:
        print(f"✗ FAIL: Results do not agree")
        return False


def test_high_dividend_yield():
    """Test with higher dividend yield (e.g., 5%)."""
    print("\n=== Test 4: High dividend yield (5%) ===")

    S = 100.0
    K = 105.0
    T = 1.0
    r = 0.05
    sigma = 0.20
    q = 0.05

    gamma_q0 = bs_gamma(S, K, T, r, 0.0, sigma)
    gamma_q_high = bs_gamma(S, K, T, r, q, sigma)

    # For q=5%, T=1yr, exp(-qT) = e^(-0.05) ≈ 0.9512
    exp_neg_qT = math.exp(-q * T)
    expected = exp_neg_qT * gamma_q0

    print(f"gamma(q=0):     {gamma_q0:.10f}")
    print(f"gamma(q=0.05):  {gamma_q_high:.10f}")
    print(f"exp(-q*T):      {exp_neg_qT:.10f}")
    print(f"expected:       {expected:.10f}")

    rel_error = abs(gamma_q_high - expected) / expected
    print(f"Rel Error:      {rel_error:.2e}")

    if rel_error < 1e-10:
        print("✓ PASS: High dividend yield discount applied correctly")
        return True
    else:
        print(f"✗ FAIL: High dividend yield test failed")
        return False


if __name__ == "__main__":
    print("=" * 70)
    print("FIX 6 Verification: bs_gamma/bs_gamma_vec Dividend Discount Factor")
    print("=" * 70)

    results = []
    results.append(test_bs_gamma_with_dividend())
    results.append(test_bs_gamma_vec_with_dividend())
    results.append(test_scalar_vs_vector_agreement())
    results.append(test_high_dividend_yield())

    print("\n" + "=" * 70)
    print(f"Results: {sum(results)}/{len(results)} tests passed")
    print("=" * 70)

    if all(results):
        print("\n✓ ALL TESTS PASSED - FIX 6 is correctly implemented")
        sys.exit(0)
    else:
        print("\n✗ SOME TESTS FAILED - Fix needs review")
        sys.exit(1)
