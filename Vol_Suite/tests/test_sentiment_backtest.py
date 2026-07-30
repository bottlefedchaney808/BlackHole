"""
Network-free tests for sentiment_backtest.py.

Uses synthetic pack JSON files in a temporary directory and a
FakeThetaDataController that returns known forward price paths — no real
ThetaData API calls, no network.

Key scenarios tested:
  1. Dataclass construction
  2. Known predictive relationship (high CNS → positive return)
  3. Null relationship (random CNS-return pairing)
  4. Empty data directory
  5. Pack with no tickers
  6. Ticker with insufficient price history
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Dict, List

import pytest

import sentiment_backtest as sb


# ---------------------------------------------------------------------------
# Fake ThetaDataController
# ---------------------------------------------------------------------------

class FakeThetaDataController:
    """Returns a canned list of daily OHLCV rows for any ticker.

    The *price_offsets* dict maps ticker symbol -> list of daily closing
    prices, one per trading day starting at the query's start_date.
    If a ticker is not in the dict, an empty list is returned (simulating
    a ticker with no price history).
    """

    def __init__(self, price_offsets: Dict[str, List[float]], start_close: float = 100.0):
        self.price_offsets = price_offsets
        self.start_close = start_close
        self._closed = False

    def hist_stock_eod(self, root: str, start_date: str, end_date: str) -> List[Dict]:
        if root not in self.price_offsets:
            return []
        offsets = self.price_offsets[root]
        # Build daily rows from start_date
        start_dt = datetime.strptime(start_date, "%Y%m%d")
        rows = []
        for i, offset in enumerate(offsets):
            d = (start_dt + timedelta(days=i)).strftime("%Y%m%d")
            close = self.start_close + offset
            rows.append({
                "date": d,
                "open": close * 0.99,
                "high": close * 1.01,
                "low": close * 0.98,
                "close": close,
                "volume": 10_000,
            })
        return rows

    def close(self):
        self._closed = True


# ---------------------------------------------------------------------------
# Helper: build a pack file
# ---------------------------------------------------------------------------

def _make_pack(
    date_dir: str,
    tickers: List[Dict],
    created_at: str,
    group_id: str = "test-pack-000000000001",
) -> dict:
    return {
        "version": 1,
        "group_id": group_id,
        "group_name": "cns-threshold-alerts",
        "created_at": created_at,
        "source_run_id": f"{date_dir}T120000Z",
        "priority": "high",
        "thesis_summary": f"{len(tickers)} highlighted tickers",
        "tickers": tickers,
        "downstream_hints": {"volatility_suite": True, "var_suite": True, "options_suite": False},
    }


def _ticker_entry(symbol: str, cns: int = 50, war_score: float = 0.3, rank: int = 1) -> Dict:
    return {
        "symbol": symbol,
        "rank": rank,
        "cns": cns,
        "war_score": war_score,
        "thesis_ratio": 0.0,
        "pump_ratio": 0.0,
        "volume": 50,
        "bullish_pct": 25.0,
        "bearish_pct": 25.0,
        "confidence": 0.5,
        "social_sources": ["stocktwits"],
    }


def _write_pack(packs_dir: str, date_dir: str, pack: dict) -> str:
    """Write a pack JSON file and return its path."""
    dir_path = os.path.join(packs_dir, date_dir)
    os.makedirs(dir_path, exist_ok=True)
    gid = pack["group_id"]
    path = os.path.join(dir_path, f"{gid}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(pack, f)
    return path


# ---------------------------------------------------------------------------
# Tests: Dataclasses
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_signal_result_dataclass():
    sr = sb.SignalResult(
        date="2026-07-24",
        ticker="AAPL",
        cns_score=80,
        war_score=0.5,
        forward_5d_return=0.02,
        forward_10d_return=0.035,
    )
    assert sr.date == "2026-07-24"
    assert sr.ticker == "AAPL"
    assert sr.cns_score == 80
    assert sr.war_score == 0.5
    assert sr.forward_5d_return == 0.02
    assert sr.forward_10d_return == 0.035


@pytest.mark.unit
def test_sentiment_backtest_result_dataclass():
    res = sb.SentimentBacktestResult(
        start_date="2026-07-20",
        end_date="2026-07-27",
        total_packs_analyzed=3,
        total_signals=15,
        top_quartile_cns_names=["AAPL", "MSFT"],
        bottom_quartile_cns_names=["XYZ"],
        hit_rate_top_vs_bottom=0.015,
        sharpe_long_only=1.2,
        cns_return_correlation=0.45,
        forward_days=5,
    )
    assert res.start_date == "2026-07-20"
    assert res.total_packs_analyzed == 3
    assert res.top_quartile_cns_names == ["AAPL", "MSFT"]
    assert res.sharpe_long_only == 1.2


# ---------------------------------------------------------------------------
# Tests: Known predictive power
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_hit_rate_positive_when_cns_predicts_correctly():
    """When high-CNS tickers go up and low-CNS tickers go down, the hit rate
    (top quartile mean - bottom quartile mean) should be > 0."""
    with tempfile.TemporaryDirectory() as tmpdir:
        packs_dir = os.path.join(tmpdir, "exports", "highlighted_ticker_packs")
        os.makedirs(packs_dir, exist_ok=True)

        date_dir = "20260724"
        created_at = "2026-07-24T12:00:00+00:00"

        # High CNS (80-100) tickers with known positive forward path
        high_cns_tickers = [
            _ticker_entry("AAPL", cns=95, war_score=0.8, rank=1),
            _ticker_entry("MSFT", cns=90, war_score=0.7, rank=2),
            _ticker_entry("GOOG", cns=85, war_score=0.6, rank=3),
        ]
        # Low CNS (10-30) tickers with known negative forward path
        low_cns_tickers = [
            _ticker_entry("XYZ", cns=20, war_score=0.1, rank=4),
            _ticker_entry("ABC", cns=15, war_score=0.05, rank=5),
        ]

        pack = _make_pack(date_dir, high_cns_tickers + low_cns_tickers, created_at)
        _write_pack(packs_dir, date_dir, pack)

        # Price offsets: high-CNS tickers go UP, low-CNS tickers go DOWN
        # Starting at 100, daily close offsets:
        #   day0=0 (reference), day1=+0.5, day2=+0.8, day3=+1.2, day4=+1.5, day5=+2.0
        price_offsets = {
            "AAPL": [0, 0.5, 0.8, 1.2, 1.5, 2.0],
            "MSFT": [0, 0.3, 0.6, 0.9, 1.2, 1.6],
            "GOOG": [0, 0.4, 0.7, 1.0, 1.3, 1.7],
            "XYZ":  [0, -0.5, -0.8, -1.2, -1.5, -2.0],
            "ABC":  [0, -0.3, -0.6, -0.9, -1.2, -1.5],
        }

        factory = lambda: FakeThetaDataController(price_offsets)

        result = sb.run_sentiment_backtest(
            tmpdir, forward_days=5, theta_controller_factory=factory
        )

        assert result.total_packs_analyzed == 1
        assert result.total_signals == 5
        # High CNS tickers should have positive returns
        for s in result.details:
            if s.ticker in ("AAPL", "MSFT", "GOOG"):
                assert s.forward_5d_return is not None and s.forward_5d_return > 0, (
                    f"{s.ticker} should have positive return"
                )
            else:
                assert s.forward_5d_return is not None and s.forward_5d_return < 0, (
                    f"{s.ticker} should have negative return"
                )

        assert result.hit_rate_top_vs_bottom > 0, (
            f"Expected top mean > bottom mean, got {result.hit_rate_top_vs_bottom}"
        )
        assert result.cns_return_correlation is not None and result.cns_return_correlation > 0
        assert len(result.top_quartile_cns_names) > 0
        assert len(result.bottom_quartile_cns_names) > 0


@pytest.mark.unit
def test_hit_rate_near_zero_when_no_relationship():
    """When forward returns are random with respect to CNS, the hit rate
    should be near zero (within a generous tolerance given small sample)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        packs_dir = os.path.join(tmpdir, "exports", "highlighted_ticker_packs")
        os.makedirs(packs_dir, exist_ok=True)

        date_dir = "20260724"
        created_at = "2026-07-24T12:00:00+00:00"

        # All tickers with varying CNS but SAME return path
        tickers = [
            _ticker_entry("A", cns=90, war_score=0.8, rank=1),
            _ticker_entry("B", cns=70, war_score=0.5, rank=2),
            _ticker_entry("C", cns=50, war_score=0.3, rank=3),
            _ticker_entry("D", cns=30, war_score=0.2, rank=4),
            _ticker_entry("E", cns=10, war_score=0.1, rank=5),
        ]
        pack = _make_pack(date_dir, tickers, created_at)
        _write_pack(packs_dir, date_dir, pack)

        # All tickers get exactly the same (small positive) return
        price_offsets = {t["symbol"]: [0, 0.1, 0.2, 0.3, 0.4, 0.5] for t in tickers}
        factory = lambda: FakeThetaDataController(price_offsets)

        result = sb.run_sentiment_backtest(
            tmpdir, forward_days=5, theta_controller_factory=factory
        )

        # All tickers have same return, so top mean == bottom mean => hit_rate ≈ 0
        assert abs(result.hit_rate_top_vs_bottom) < 0.001, (
            f"Expected near-zero hit rate when returns are uniform, "
            f"got {result.hit_rate_top_vs_bottom}"
        )


# ---------------------------------------------------------------------------
# Tests: Edge cases
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_empty_data_dir_returns_graceful_result():
    """A directory with no pack files should produce an empty-but-valid result."""
    with tempfile.TemporaryDirectory() as tmpdir:
        result = sb.run_sentiment_backtest(tmpdir)
        assert result.total_packs_analyzed == 0
        assert result.total_signals == 0
        assert result.hit_rate_top_vs_bottom == 0.0
        assert result.sharpe_long_only is None
        assert result.cns_return_correlation is None
        assert result.forward_days == 5


@pytest.mark.unit
def test_partial_packs_dir_with_no_json():
    """A packs directory that exists but has no .json files should also be
    graceful (empty result, not an error)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        packs_dir = os.path.join(tmpdir, "exports", "highlighted_ticker_packs")
        os.makedirs(packs_dir, exist_ok=True)
        # Touch a non-json file to confirm it's skipped
        open(os.path.join(packs_dir, "readme.txt"), "w").close()
        result = sb.run_sentiment_backtest(tmpdir)
        assert result.total_packs_analyzed == 0
        assert result.total_signals == 0


@pytest.mark.unit
def test_pack_with_no_tickers_is_skipped():
    """A pack that exists but has an empty tickers list shouldn't contribute."""
    with tempfile.TemporaryDirectory() as tmpdir:
        packs_dir = os.path.join(tmpdir, "exports", "highlighted_ticker_packs")
        os.makedirs(packs_dir, exist_ok=True)

        date_dir = "20260725"
        pack = _make_pack(date_dir, [], "2026-07-25T12:00:00+00:00")
        _write_pack(packs_dir, date_dir, pack)

        result = sb.run_sentiment_backtest(tmpdir)
        assert result.total_packs_analyzed == 0
        assert result.total_signals == 0


@pytest.mark.unit
def test_insufficient_price_history_yields_null_returns():
    """A ticker whose pack exists but has no price data should have
    forward returns set to None (not crash)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        packs_dir = os.path.join(tmpdir, "exports", "highlighted_ticker_packs")
        os.makedirs(packs_dir, exist_ok=True)

        date_dir = "20260724"
        tickers = [_ticker_entry("NODATA", cns=80, war_score=0.5)]
        pack = _make_pack(date_dir, tickers, "2026-07-24T12:00:00+00:00")
        _write_pack(packs_dir, date_dir, pack)

        # No price data for NODATA in the fake controller
        factory = lambda: FakeThetaDataController({})
        result = sb.run_sentiment_backtest(
            tmpdir, forward_days=5, theta_controller_factory=factory
        )
        # The ticker was processed but its forward returns are None
        assert result.total_signals == 1
        assert result.details[0].forward_5d_return is None
        # With no valid returns, correlation should be None
        assert result.cns_return_correlation is None


# ---------------------------------------------------------------------------
# Tests: Forward return computation
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_forward_5d_return_from_pack_date():
    """Verify that the exact forward return over 5 trading days is correct."""
    with tempfile.TemporaryDirectory() as tmpdir:
        packs_dir = os.path.join(tmpdir, "exports", "highlighted_ticker_packs")
        os.makedirs(packs_dir, exist_ok=True)

        date_dir = "20260720"
        created_at = "2026-07-20T12:00:00+00:00"
        tickers = [_ticker_entry("SPY", cns=80, war_score=0.5)]
        pack = _make_pack(date_dir, tickers, created_at)
        _write_pack(packs_dir, date_dir, pack)

        # Known price path: 100, 101, 102, 103, 104, 105  (0%, +1%, ..., +5%)
        # forward_5d from day 0 close (100) -> day 5 close (105): +5%
        price_offsets = {"SPY": [0, 1, 2, 3, 4, 5]}
        factory = lambda: FakeThetaDataController(price_offsets, start_close=100.0)

        result = sb.run_sentiment_backtest(
            tmpdir, forward_days=5, theta_controller_factory=factory
        )

        assert len(result.details) == 1
        sr = result.details[0]
        assert sr.ticker == "SPY"
        assert sr.forward_5d_return is not None
        assert abs(sr.forward_5d_return - 0.05) < 1e-10, f"Got {sr.forward_5d_return}"


@pytest.mark.unit
def test_forward_days_parameter_is_respected():
    """Test with forward_days=1 so only 1-day forward return is measured."""
    with tempfile.TemporaryDirectory() as tmpdir:
        packs_dir = os.path.join(tmpdir, "exports", "highlighted_ticker_packs")
        os.makedirs(packs_dir, exist_ok=True)

        date_dir = "20260720"
        created_at = "2026-07-20T12:00:00+00:00"
        tickers = [_ticker_entry("SPY", cns=90, war_score=0.6)]
        pack = _make_pack(date_dir, tickers, created_at)
        _write_pack(packs_dir, date_dir, pack)

        price_offsets = {"SPY": [0, 2, 4, 6, 8]}  # day1 = +2%
        factory = lambda: FakeThetaDataController(price_offsets, start_close=100.0)

        result = sb.run_sentiment_backtest(
            tmpdir, forward_days=1, theta_controller_factory=factory
        )

        assert len(result.details) == 1
        sr = result.details[0]
        assert sr.forward_5d_return is not None
        assert abs(sr.forward_5d_return - 0.02) < 1e-10, f"Got {sr.forward_5d_return}"
        assert result.forward_days == 1


@pytest.mark.unit
def test_correlation_is_positive_with_upward_trend_and_high_cns():
    """When high CNS maps to large positive returns, correlation > 0."""
    with tempfile.TemporaryDirectory() as tmpdir:
        packs_dir = os.path.join(tmpdir, "exports", "highlighted_ticker_packs")
        os.makedirs(packs_dir, exist_ok=True)

        date_dir = "20260724"
        created_at = "2026-07-24T12:00:00+00:00"
        tickers = [
            _ticker_entry("A", cns=90, war_score=0.9, rank=1),
            _ticker_entry("B", cns=80, war_score=0.7, rank=2),
            _ticker_entry("C", cns=60, war_score=0.4, rank=3),
            _ticker_entry("D", cns=40, war_score=0.2, rank=4),
            _ticker_entry("E", cns=20, war_score=0.1, rank=5),
        ]
        pack = _make_pack(date_dir, tickers, created_at)
        _write_pack(packs_dir, date_dir, pack)

        # Returns proportional to CNS: high CNS -> high return
        # (CNS/10)% roughly: 90->9%, 80->8%, 60->6%, 40->4%, 20->2%
        price_offsets = {
            "A": [0, 9, 9, 9, 9, 9],
            "B": [0, 8, 8, 8, 8, 8],
            "C": [0, 6, 6, 6, 6, 6],
            "D": [0, 4, 4, 4, 4, 4],
            "E": [0, 2, 2, 2, 2, 2],
        }
        factory = lambda: FakeThetaDataController(price_offsets, start_close=100.0)

        result = sb.run_sentiment_backtest(
            tmpdir, forward_days=5, theta_controller_factory=factory
        )

        assert result.cns_return_correlation is not None
        # For 5 points with strictly increasing CNS and returns, correlation >> 0
        assert result.cns_return_correlation > 0.8, (
            f"Expected strong positive correlation, got {result.cns_return_correlation}"
        )


@pytest.mark.unit
def test_long_only_sharpe_is_positive_on_up_strategy():
    """When top-quartile CNS tickers all have positive returns, Sharpe > 0."""
    with tempfile.TemporaryDirectory() as tmpdir:
        packs_dir = os.path.join(tmpdir, "exports", "highlighted_ticker_packs")
        os.makedirs(packs_dir, exist_ok=True)

        date_dir = "20260724"
        created_at = "2026-07-24T12:00:00+00:00"
        tickers = [
            _ticker_entry("A", cns=95, war_score=0.9, rank=1),
            _ticker_entry("B", cns=90, war_score=0.8, rank=2),
            _ticker_entry("C", cns=85, war_score=0.7, rank=3),
            _ticker_entry("D", cns=60, war_score=0.4, rank=4),
            _ticker_entry("E", cns=30, war_score=0.1, rank=5),
        ]
        pack = _make_pack(date_dir, tickers, created_at)
        _write_pack(packs_dir, date_dir, pack)

        # Top 3 (CNS >= 85) all have positive returns
        price_offsets = {
            "A": [0, 2, 2, 2, 2, 2],   # +2%
            "B": [0, 1.5, 1.5, 1.5, 1.5, 1.5],  # +1.5%
            "C": [0, 1, 1, 1, 1, 1],   # +1%
            "D": [0, 0, 0, 0, 0, 0],    # 0%
            "E": [0, -1, -1, -1, -1, -1],  # -1%
        }
        factory = lambda: FakeThetaDataController(price_offsets, start_close=100.0)

        result = sb.run_sentiment_backtest(
            tmpdir, forward_days=5, theta_controller_factory=factory
        )

        assert result.sharpe_long_only is not None
        assert result.sharpe_long_only > 0, (
            f"Expected positive Sharpe for profitable top-quartile strategy, "
            f"got {result.sharpe_long_only}"
        )


@pytest.mark.unit
def test_multiple_days_of_packs():
    """Multiple date directories should all be scanned and aggregated."""
    with tempfile.TemporaryDirectory() as tmpdir:
        packs_dir = os.path.join(tmpdir, "exports", "highlighted_ticker_packs")
        os.makedirs(packs_dir, exist_ok=True)

        # Day 1: two tickers
        tickers_d1 = [
            _ticker_entry("AAPL", cns=90, war_score=0.8, rank=1),
            _ticker_entry("MSFT", cns=30, war_score=0.2, rank=2),
        ]
        _write_pack(packs_dir, "20260724",
                     _make_pack("20260724", tickers_d1, "2026-07-24T12:00:00+00:00"))

        # Day 2: two tickers (one new, one duplicate)
        tickers_d2 = [
            _ticker_entry("GOOG", cns=85, war_score=0.7, rank=1),
            _ticker_entry("AAPL", cns=92, war_score=0.9, rank=2),  # duplicate
        ]
        _write_pack(packs_dir, "20260725",
                     _make_pack("20260725", tickers_d2, "2026-07-25T12:00:00+00:00"))

        price_offsets = {
            "AAPL": [0, 1, 2, 3, 4, 5],
            "MSFT": [0, 0, 0, 0, 0, 0],
            "GOOG": [0, 2, 2, 2, 2, 2],
        }
        factory = lambda: FakeThetaDataController(price_offsets, start_close=100.0)

        result = sb.run_sentiment_backtest(
            tmpdir, forward_days=5, theta_controller_factory=factory
        )

        # 2 packs analyzed, 4 total signals (AAPL appears twice, once per day)
        assert result.total_packs_analyzed == 2
        assert result.total_signals == 4
        assert result.start_date == "2026-07-24"
        assert result.end_date == "2026-07-25"