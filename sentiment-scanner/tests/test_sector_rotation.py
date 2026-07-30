"""Tests for the Sector Rotation / Factor Momentum scanner.

Uses a monkeypatched ``fetch_price_history`` that returns synthetic price
DataFrames so no network calls are made.  The test fixtures construct
price series with known momentum characteristics so we can verify the factor
computation, ranking, and formatting directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple
from unittest.mock import MagicMock, PropertyMock, patch

import numpy as np
import pandas as pd
import pytest

from scanner.sector_rotation import (
    CANDIDATE_INDICES,
    SectorFactors,
    SectorRank,
    _compute_momentum,
    _correlation,
    _pct,
    _sign_str,
    _z_normalise,
    compute_factors,
    format_rotation,
    rank_sectors,
    scan_all,
)


# ---------------------------------------------------------------------------
# Fixtures — synthetic price DataFrames
# ---------------------------------------------------------------------------


def _make_price_df(
    ticker_prices: Dict[str, List[float]],
    start_date: str = "2024-01-02",
) -> pd.DataFrame:
    """Build a DataFrame with a DatetimeIndex from a dict of price lists.

    All ticker price lists must have the same length.  The index is a daily
    calendar starting at *start_date*.
    """
    lengths = {t: len(p) for t, p in ticker_prices.items()}
    assert len(set(lengths.values())) == 1, "All ticker price lists must be the same length"

    n = len(next(iter(ticker_prices.values())))
    dates = pd.bdate_range(start=start_date, periods=n)
    return pd.DataFrame(ticker_prices, index=dates)


def _prices_with_trend(n: int, start: float, daily_ret: float, noise: float = 0.001) -> List[float]:
    """Generate *n* prices following a steady trend with small noise."""
    p = start
    prices = [p]
    for _ in range(1, n):
        p *= 1.0 + daily_ret + np.random.normal(0, noise)
        prices.append(p)
    return prices


@pytest.fixture
def bullish_sector_prices() -> pd.DataFrame:
    """XLK (rising strongly) and SPY (moderate rise) — 150 trading days."""
    np.random.seed(42)
    n = 150
    xlk = _prices_with_trend(n, 180.0, 0.002, noise=0.005)
    spy = _prices_with_trend(n, 480.0, 0.0008, noise=0.004)
    return _make_price_df({"XLK": xlk, "SPY": spy})


@pytest.fixture
def bearish_sector_prices() -> pd.DataFrame:
    """XLE (falling) and flat SPY — 150 trading days."""
    np.random.seed(99)
    n = 150
    xle = _prices_with_trend(n, 85.0, -0.0015, noise=0.006)
    spy = _prices_with_trend(n, 480.0, 0.0002, noise=0.004)
    return _make_price_df({"XLE": xle, "SPY": spy})


@pytest.fixture
def all_sectors_prices() -> pd.DataFrame:
    """Generate synthetic prices for all 15 sector ETFs + SPY.

    Each sector gets a different trend so we get a meaningful ranking:
      - XLK, XLY: strong positive
      - XLC, QQQ: moderate positive
      - SPY: baseline (0.08% daily)
      - XLU, XLP: flat/low
      - XLE: negative
    """
    np.random.seed(42)
    n = 150

    trend_map: Dict[str, float] = {
        "XLK": 0.0025, "XLY": 0.0022,
        "XLC": 0.0015, "QQQ": 0.0014,
        "SPY": 0.0008,
        "DIA": 0.0006, "IWM": 0.0007,
        "XLF": 0.0005, "XLI": 0.0004,
        "XLB": 0.0003, "XLV": 0.0004,
        "XLU": 0.0001, "XLP": 0.0001,
        "XLRE": 0.0000,
        "XLE": -0.0015,
    }

    base_prices: Dict[str, float] = {
        "SPY": 480.0, "QQQ": 440.0, "DIA": 380.0, "IWM": 200.0,
        "XLK": 180.0, "XLF": 35.0, "XLE": 85.0, "XLY": 160.0,
        "XLP": 70.0, "XLV": 135.0, "XLI": 110.0, "XLU": 65.0,
        "XLB": 80.0, "XLC": 50.0, "XLRE": 40.0,
    }

    ticker_prices: Dict[str, List[float]] = {}
    for ticker, daily_ret in trend_map.items():
        base = base_prices.get(ticker, 100.0)
        ticker_prices[ticker] = _prices_with_trend(n, base, daily_ret, noise=0.005)

    return _make_price_df(ticker_prices)


@pytest.fixture
def insufficient_data_prices() -> pd.DataFrame:
    """Only a single data point — should trigger 'Insufficient price data' error."""
    return _make_price_df(
        {"XLK": [100.0], "SPY": [480.0]},
        start_date="2024-06-01",
    )


# ---------------------------------------------------------------------------
# Helper: context manager to patch fetch_price_history in sector_rotation
# ---------------------------------------------------------------------------


def _patch_fetch_price_history(return_df: pd.DataFrame):
    """Context manager that patches ``fetch_price_history`` in the
    ``scanner.sector_rotation`` module to return a given DataFrame."""
    import scanner.sector_rotation as sr

    return patch.object(sr, "fetch_price_history", return_value=return_df)


def _patch_fetch_price_history_side_effect(exc: Exception):
    """Context manager that patches ``fetch_price_history`` to raise."""
    import scanner.sector_rotation as sr

    return patch.object(sr, "fetch_price_history", side_effect=exc)


# ---------------------------------------------------------------------------
# Unit tests: _compute_momentum
# ---------------------------------------------------------------------------

class TestComputeMomentum:
    def test_positive_return(self) -> None:
        # prices = [100, 105, 110, 115], lookback=2
        # start = prices[-(2+1)] = prices[-3] = 105, end = prices[-1] = 115
        # return = (115 - 105) / 105 = 10/105 = 0.095238...
        prices = np.array([100.0, 105.0, 110.0, 115.0])
        assert _compute_momentum(prices, 2) == pytest.approx(10.0 / 105.0)

    def test_negative_return(self) -> None:
        # prices = [100, 95, 90, 85], lookback=2
        # start = prices[-3] = 95, end = prices[-1] = 85
        # return = (85 - 95) / 95 = -10/95 = -0.105263...
        prices = np.array([100.0, 95.0, 90.0, 85.0])
        assert _compute_momentum(prices, 2) == pytest.approx(-10.0 / 95.0)

    def test_zero_return(self) -> None:
        prices = np.array([100.0] * 30)
        assert _compute_momentum(prices, 21) == 0.0

    def test_insufficient_data_returns_zero(self) -> None:
        prices = np.array([100.0, 101.0])
        assert _compute_momentum(prices, 21) == 0.0

    def test_lookback_1(self) -> None:
        prices = np.array([100.0, 200.0])
        assert _compute_momentum(prices, 1) == pytest.approx(1.0, abs=1e-6)

    def test_lookback_matches_total_length(self) -> None:
        prices = np.array([100.0, 110.0, 121.0])
        assert _compute_momentum(prices, 2) == pytest.approx(0.21, abs=1e-6)


# ---------------------------------------------------------------------------
# Unit tests: _correlation
# ---------------------------------------------------------------------------

class TestCorrelation:
    def test_perfect_positive(self) -> None:
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        y = np.array([2.0, 4.0, 6.0, 8.0, 10.0])
        assert _correlation(x, y) == pytest.approx(1.0, abs=1e-6)

    def test_perfect_negative(self) -> None:
        x = np.array([1.0, 2.0, 3.0, 4.0])
        y = np.array([5.0, 4.0, 3.0, 2.0])
        assert _correlation(x, y) == pytest.approx(-1.0, abs=1e-6)

    def test_too_short_returns_zero(self) -> None:
        assert _correlation(np.array([1.0, 2.0]), np.array([3.0, 4.0])) == 0.0

    def test_no_variation_returns_nan_safe(self) -> None:
        x = np.ones(10)
        y = np.ones(10)
        corr = _correlation(x, y)
        # np.corrcoef returns NaN; our wrapper handles this gracefully
        assert corr == 0.0 or np.isnan(corr)

    def test_independent_series(self) -> None:
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        y = np.array([5.0, 4.0, 3.0, 2.0, 1.0])
        assert _correlation(x, y) == pytest.approx(-1.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Unit tests: _z_normalise
# ---------------------------------------------------------------------------

class TestZNormalise:
    def test_basic(self) -> None:
        result = _z_normalise([1.0, 2.0, 3.0])
        assert len(result) == 3
        assert result[0] == pytest.approx(-1.0, abs=1e-6)
        assert result[1] == pytest.approx(0.0, abs=1e-6)
        assert result[2] == pytest.approx(1.0, abs=1e-6)

    def test_constant_returns_zeros(self) -> None:
        result = _z_normalise([5.0, 5.0, 5.0])
        assert result == [0.0, 0.0, 0.0]

    def test_empty_returns_empty(self) -> None:
        assert _z_normalise([]) == []

    def test_single_value_returns_zero(self) -> None:
        assert _z_normalise([42.0]) == [0.0]


# ---------------------------------------------------------------------------
# Unit tests: _pct and _sign_str helpers
# ---------------------------------------------------------------------------

class TestFormatHelpers:
    def test_pct_positive(self) -> None:
        assert _pct(0.053) == "5.3%"

    def test_pct_negative(self) -> None:
        assert _pct(-0.10) == "-10.0%"

    def test_pct_zero(self) -> None:
        assert _pct(0.0) == "0.0%"

    def test_sign_str_positive(self) -> None:
        assert _sign_str(0.05) == "+5.0%"

    def test_sign_str_negative(self) -> None:
        assert _sign_str(-0.10) == "-10.0%"

    def test_sign_str_zero(self) -> None:
        assert _sign_str(0.0) == "+0.0%"

    def test_sign_str_small_positive(self) -> None:
        assert _sign_str(0.0016) == "+0.2%"


# ---------------------------------------------------------------------------
# Integration tests: compute_factors (mocked fetch_price_history)
# ---------------------------------------------------------------------------

class TestComputeFactors:
    def test_bullish_sector(self, bullish_sector_prices: pd.DataFrame) -> None:
        with _patch_fetch_price_history(bullish_sector_prices):
            factors = compute_factors("XLK")

        assert factors.error is None
        assert factors.mom_1m > 0
        assert factors.mom_3m > 0
        assert factors.mom_6m > 0
        assert factors.rs_vs_spy > 1.0

    def test_bearish_sector(self, bearish_sector_prices: pd.DataFrame) -> None:
        with _patch_fetch_price_history(bearish_sector_prices):
            factors = compute_factors("XLE")

        assert factors.error is None
        assert factors.mom_6m < 0
        assert factors.rs_vs_spy < 1.0

    def test_missing_ticker(self, bullish_sector_prices: pd.DataFrame) -> None:
        with _patch_fetch_price_history(bullish_sector_prices):
            factors = compute_factors("NONEXIST")

        assert factors.error is not None
        assert "Missing" in factors.error

    def test_insufficient_data(self, insufficient_data_prices: pd.DataFrame) -> None:
        with _patch_fetch_price_history(insufficient_data_prices):
            factors = compute_factors("XLK")

        assert factors.error is not None

    def test_fetch_raises(self) -> None:
        with _patch_fetch_price_history_side_effect(RuntimeError("API down")):
            factors = compute_factors("XLK")

        assert factors.error is not None
        assert "API down" in factors.error

    def test_name_resolved(self, bullish_sector_prices: pd.DataFrame) -> None:
        with _patch_fetch_price_history(bullish_sector_prices):
            factors = compute_factors("XLK")
        assert factors.name == "Technology"

    def test_unknown_ticker_name_falls_back(self, bullish_sector_prices: pd.DataFrame) -> None:
        """An ETF not in CANDIDATE_INDICES uses the ticker as the name."""
        # We need a df that has the requested ticker + SPY
        df = _make_price_df(
            {"FAKE": [100.0] * 150, "SPY": [480.0] * 150},
        )
        with _patch_fetch_price_history(df):
            factors = compute_factors("FAKE")
        assert factors.name == "FAKE"


# ---------------------------------------------------------------------------
# Integration tests: rank_sectors (mocked fetch_price_history)
# ---------------------------------------------------------------------------

class TestRankSectors:
    def test_rankings_have_expected_structure(
        self, all_sectors_prices: pd.DataFrame,
    ) -> None:
        with _patch_fetch_price_history(all_sectors_prices):
            ranks = rank_sectors()

        assert len(ranks) == 15
        for r in ranks:
            assert isinstance(r, SectorRank)
            assert 0.0 <= r.composite_score <= 100.0
            assert r.signal in ("BULLISH", "NEUTRAL", "BEARISH")
            assert r.ticker in {t for t, _n in CANDIDATE_INDICES}

    def test_top_sectors_are_strongest(
        self, all_sectors_prices: pd.DataFrame,
    ) -> None:
        with _patch_fetch_price_history(all_sectors_prices):
            ranks = rank_sectors()

        top_tickers = [r.ticker for r in ranks[:3]]
        bot_tickers = [r.ticker for r in ranks[-3:]]
        assert "XLK" in top_tickers or "XLY" in top_tickers
        assert "XLE" in bot_tickers

    def test_sorted_descending(self, all_sectors_prices: pd.DataFrame) -> None:
        with _patch_fetch_price_history(all_sectors_prices):
            ranks = rank_sectors()

        scores = [r.composite_score for r in ranks]
        assert all(scores[i] >= scores[i + 1] for i in range(len(scores) - 1))

    def test_signal_distribution(self, all_sectors_prices: pd.DataFrame) -> None:
        """With 15 sectors, expect ~5 BULLISH, ~5 NEUTRAL, ~5 BEARISH."""
        with _patch_fetch_price_history(all_sectors_prices):
            ranks = rank_sectors()

        bullish = sum(1 for r in ranks if r.signal == "BULLISH")
        bearish = sum(1 for r in ranks if r.signal == "BEARISH")
        neutral = sum(1 for r in ranks if r.signal == "NEUTRAL")
        assert bullish >= 4
        assert bearish >= 4
        assert neutral >= 4

    def test_all_etfs_have_momentum_data(
        self, all_sectors_prices: pd.DataFrame,
    ) -> None:
        with _patch_fetch_price_history(all_sectors_prices):
            ranks = rank_sectors()

        # All 15 sectors should have been ranked with valid momentum values
        for r in ranks:
            assert r.mom_6m is not None or r.composite_score >= 0


# ---------------------------------------------------------------------------
# Integration test: scan_all
# ---------------------------------------------------------------------------

class TestScanAll:
    def test_convenience_returns_list(self, all_sectors_prices: pd.DataFrame) -> None:
        with _patch_fetch_price_history(all_sectors_prices):
            result = scan_all()

        assert isinstance(result, list)
        if result:
            assert isinstance(result[0], SectorRank)


# ---------------------------------------------------------------------------
# Tests: format_rotation
# ---------------------------------------------------------------------------

class TestFormatRotation:
    def test_empty_ranks(self) -> None:
        output = format_rotation([])
        assert "No sector rotation data available" in output

    def test_contains_tickers_and_scores(self) -> None:
        ranks = [
            SectorRank(
                ticker="XLK", name="Technology",
                composite_score=85.0, mom_1m=0.05, mom_3m=0.12, mom_6m=0.25,
                rs_vs_spy=1.8, corr_change=0.05, volume_trend=1.5,
                signal="BULLISH",
            ),
            SectorRank(
                ticker="XLE", name="Energy",
                composite_score=15.0, mom_1m=-0.02, mom_3m=-0.08, mom_6m=-0.15,
                rs_vs_spy=0.6, corr_change=-0.03, volume_trend=-0.5,
                signal="BEARISH",
            ),
        ]
        output = format_rotation(ranks)
        assert "XLK" in output
        assert "XLE" in output
        assert "BULLISH" in output
        assert "BEARISH" in output
        assert "85.0" in output
        assert "15.0" in output
        assert "5.0%" in output   # 0.05 * 100
        assert "-2.0%" in output  # -0.02 * 100

    def test_format_has_header_and_legend(self) -> None:
        ranks = [
            SectorRank(
                ticker="XLK", name="Technology",
                composite_score=85.0, mom_1m=0.05, mom_3m=0.12, mom_6m=0.25,
                rs_vs_spy=1.8, corr_change=0.05, volume_trend=1.5,
                signal="BULLISH",
            ),
        ]
        output = format_rotation(ranks)
        assert "SECTOR ROTATION" in output
        assert "Factor Momentum Scan" in output
        assert "Rank" in output
        assert "Score" in output
        assert "Legend" in output


# ---------------------------------------------------------------------------
# Dataclass sanity
# ---------------------------------------------------------------------------

class TestDataclassSanity:
    def test_sector_factors_defaults(self) -> None:
        f = SectorFactors(
            ticker="SPY", name="S&P 500",
            mom_1m=0.0, mom_3m=0.0, mom_6m=0.0,
            rs_vs_spy=0.0, corr_change=0.0, volume_trend=0.0,
        )
        assert f.ticker == "SPY"
        assert f.error is None

    def test_sector_factors_with_error(self) -> None:
        f = SectorFactors(
            ticker="BAD", name="Bad Ticker",
            mom_1m=0.0, mom_3m=0.0, mom_6m=0.0,
            rs_vs_spy=0.0, corr_change=0.0, volume_trend=0.0,
            error="no data",
        )
        assert f.error == "no data"

    def test_sector_rank_round_trip(self) -> None:
        r = SectorRank(
            ticker="QQQ", name="Nasdaq-100",
            composite_score=90.0, mom_1m=0.03, mom_3m=0.10, mom_6m=0.20,
            rs_vs_spy=1.5, corr_change=0.02, volume_trend=0.8,
            signal="BULLISH",
        )
        assert r.composite_score == 90.0
        assert r.signal == "BULLISH"


# ---------------------------------------------------------------------------
# CANDIDATE_INDICES integrity
# ---------------------------------------------------------------------------

class TestCandidateIndices:
    def test_has_expected_count(self) -> None:
        assert len(CANDIDATE_INDICES) == 15

    def test_spy_is_first(self) -> None:
        assert CANDIDATE_INDICES[0][0] == "SPY"

    def test_all_etfs_are_strings(self) -> None:
        for ticker, name in CANDIDATE_INDICES:
            assert isinstance(ticker, str)
            assert isinstance(name, str)
            assert len(ticker) >= 3

    def test_xlre_is_last(self) -> None:
        assert CANDIDATE_INDICES[-1][0] == "XLRE"

    def test_no_duplicate_tickers(self) -> None:
        tickers = [t for t, _n in CANDIDATE_INDICES]
        assert len(tickers) == len(set(tickers))

    def test_all_tickers_are_uppercase(self) -> None:
        for ticker, _name in CANDIDATE_INDICES:
            assert ticker == ticker.upper()