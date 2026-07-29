"""
Test suite for correlation_engine.py, focusing on:
- FIX 2: handling of bad tickers (tickers with no usable price history)
- proper filtering and renormalization of weights when tickers are dropped
- reporting of dropped tickers back to the caller
"""
import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

import correlation_engine as ce


def test_compute_basket_stats_with_one_bad_ticker(capsys):
    """
    FIX 2 regression test: compute_basket_stats called with a basket containing
    one bad ticker (no price history) should:
    1. NOT raise KeyError
    2. Return successfully on the remaining tickers
    3. Report the dropped name via dropped_tickers field
    4. Print which tickers were dropped
    """
    # Build synthetic price data with only 2 of 3 tickers present
    dates = pd.date_range(end=datetime.now(), periods=100, freq='D')

    # Create price series for two good tickers
    np.random.seed(42)
    prices_a = 100.0 * np.exp(np.cumsum(np.random.normal(0.0005, 0.01, 100)))
    prices_b = 100.0 * np.exp(np.cumsum(np.random.normal(0.0005, 0.01, 100)))

    # Create DataFrame with only A and B, NOT C
    prices_df = pd.DataFrame({
        'GOOD_A': prices_a,
        'GOOD_B': prices_b,
        # 'BAD_TICKER' deliberately omitted - simulates fetch_price_history dropping it
    }, index=dates)

    # Monkey-patch fetch_price_history to return our synthetic data
    # (avoiding real ThetaData calls)
    original_fetch = ce.fetch_price_history
    def mock_fetch(tickers, period="2y"):
        # Return only the columns that exist in our synthetic data
        all_requested = tickers
        available = [t for t in all_requested if t in prices_df.columns]
        if not available:
            raise ValueError(f"Could not fetch price history for any of {all_requested}")
        return prices_df[available]

    ce.fetch_price_history = mock_fetch
    try:
        # Call with 3 tickers, but only 2 exist in the data (BAD_TICKER will be dropped)
        stats = ce.compute_basket_stats(
            tickers=['GOOD_A', 'GOOD_B', 'BAD_TICKER'],
            weights=[0.33, 0.33, 0.34],
            market_ticker='GOOD_A',
            period='2y'
        )

        # Verify the function succeeded (didn't raise KeyError)
        assert stats is not None

        # Verify dropped_tickers field reports the bad ticker
        assert stats.dropped_tickers == ['BAD_TICKER'], \
            f"Expected dropped_tickers=['BAD_TICKER'], got {stats.dropped_tickers}"

        # Verify only good tickers are in the returned stats
        assert set(stats.tickers) == {'GOOD_A', 'GOOD_B'}, \
            f"Expected tickers {{'GOOD_A', 'GOOD_B'}}, got {set(stats.tickers)}"

        # Verify weights are renormalized (should sum to 1.0)
        assert abs(np.sum(stats.weights) - 1.0) < 1e-10, \
            f"Weights don't sum to 1.0: {np.sum(stats.weights)}"

        # Verify the ratio of remaining weights is preserved
        # Original weights were [0.33, 0.33, 0.34] for [A, B, BAD]
        # After dropping BAD, remaining [0.33, 0.33] should renormalize to [0.5, 0.5]
        expected_weights = np.array([0.33, 0.33]) / (0.33 + 0.33)
        actual_weights = stats.weights
        np.testing.assert_array_almost_equal(
            actual_weights, expected_weights, decimal=5,
            err_msg=f"Weights not properly renormalized: {actual_weights} vs {expected_weights}"
        )

        # Verify the function printed a message about the dropped ticker
        captured = capsys.readouterr()
        assert 'correlation_engine' in captured.out, "Expected dropped-ticker log message"
        assert 'BAD_TICKER' in captured.out, "Expected BAD_TICKER in log message"

    finally:
        # Restore original function
        ce.fetch_price_history = original_fetch


def test_compute_basket_stats_all_bad_tickers():
    """
    Edge case: if all tickers have no usable price history, should raise ValueError
    with a clear message, not crash with KeyError.
    """
    # Empty DataFrame - no tickers available
    prices_df = pd.DataFrame()

    original_fetch = ce.fetch_price_history
    def mock_fetch(tickers, period="2y"):
        # Return empty DataFrame (all tickers failed)
        return prices_df

    ce.fetch_price_history = mock_fetch
    try:
        with pytest.raises(ValueError, match="No tickers in basket have usable price history"):
            ce.compute_basket_stats(
                tickers=['BAD_1', 'BAD_2', 'BAD_3'],
                weights=None,
                market_ticker='GOOD_MARKET',
                period='2y'
            )
    finally:
        ce.fetch_price_history = original_fetch


def test_compute_basket_stats_no_dropped_tickers():
    """
    Happy path: when all tickers are good, dropped_tickers should be empty.
    """
    dates = pd.date_range(end=datetime.now(), periods=100, freq='D')
    np.random.seed(42)

    prices_df = pd.DataFrame({
        'GOOD_A': 100.0 * np.exp(np.cumsum(np.random.normal(0.0005, 0.01, 100))),
        'GOOD_B': 100.0 * np.exp(np.cumsum(np.random.normal(0.0005, 0.01, 100))),
    }, index=dates)

    original_fetch = ce.fetch_price_history
    def mock_fetch(tickers, period="2y"):
        available = [t for t in tickers if t in prices_df.columns]
        if not available:
            raise ValueError(f"Could not fetch price history for any of {tickers}")
        return prices_df[available]

    ce.fetch_price_history = mock_fetch
    try:
        stats = ce.compute_basket_stats(
            tickers=['GOOD_A', 'GOOD_B'],
            weights=None,
            market_ticker='GOOD_A',
            period='2y'
        )

        # Verify no tickers were dropped
        assert stats.dropped_tickers == [], \
            f"Expected no dropped tickers, got {stats.dropped_tickers}"

        # Verify all requested tickers are in the result
        assert set(stats.tickers) == {'GOOD_A', 'GOOD_B'}

    finally:
        ce.fetch_price_history = original_fetch


def test_compute_basket_stats_equal_weights_with_dropped_ticker(capsys):
    """
    When weights=None (equal weighting) and one ticker is dropped,
    the remaining tickers should get equal weight.
    """
    dates = pd.date_range(end=datetime.now(), periods=100, freq='D')
    np.random.seed(42)

    prices_df = pd.DataFrame({
        'GOOD_A': 100.0 * np.exp(np.cumsum(np.random.normal(0.0005, 0.01, 100))),
        'GOOD_B': 100.0 * np.exp(np.cumsum(np.random.normal(0.0005, 0.01, 100))),
    }, index=dates)

    original_fetch = ce.fetch_price_history
    def mock_fetch(tickers, period="2y"):
        available = [t for t in tickers if t in prices_df.columns]
        if not available:
            raise ValueError(f"Could not fetch price history for any of {tickers}")
        return prices_df[available]

    ce.fetch_price_history = mock_fetch
    try:
        stats = ce.compute_basket_stats(
            tickers=['GOOD_A', 'BAD_TICKER', 'GOOD_B'],
            weights=None,  # Equal weighting
            market_ticker='GOOD_A',
            period='2y'
        )

        # With 3 tickers requested at equal weight, then 1 dropped,
        # the 2 survivors should have equal weight (0.5 each)
        assert len(stats.tickers) == 2
        np.testing.assert_array_almost_equal(
            stats.weights, [0.5, 0.5], decimal=5,
            err_msg=f"Equal weights not preserved: {stats.weights}"
        )

        # Verify dropped ticker is reported
        assert 'BAD_TICKER' in stats.dropped_tickers

    finally:
        ce.fetch_price_history = original_fetch
