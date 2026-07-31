"""Tests for the earnings options flow scanner.

Covers EarningsResult dataclass, EarningsScanner (with all methods
mocked so no network calls are made), and the standalone convenience
functions ``scan_ticker`` and ``format_earnings``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from scanner.earnings_scanner import (
    EARNINGS_CALENDAR,
    EarningsResult,
    EarningsScanner,
    format_earnings,
    format_earnings_one,
    scan_ticker,
)


# ══════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _no_live_calendar(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default: live calendar returns nothing, so tests hit the static
    EARNINGS_CALENDAR fallback deterministically. Individual tests can
    override this with their own monkeypatch.setattr call."""
    monkeypatch.setattr(
        "scanner.earnings_scanner.fetch_earnings_calendar", lambda: {}
    )


@pytest.fixture
def mock_td() -> MagicMock:
    """A mock ThetaDataController with canned responses.

    Expiry dates straddle the patched earnings date (2027-08-01) so
    the scan finds valid before/after expiries.  All dates are well
    in the future relative to the test run date (July 29, 2026).
    """
    td = MagicMock()
    td.fetch_spot_price.return_value = 200.0
    td.list_expirations.return_value = [
        "20270724",  # before earnings (closest to target_before)
        "20270806",  # after earnings (closest to target_after)
        "20270821",  # after earnings (further)
    ]
    td.option_bulk_greeks.return_value = [
        {"strike": "195000", "right": "P", "implied_vol": 0.28},
        {"strike": "200000", "right": "P", "implied_vol": 0.27},
        {"strike": "200000", "right": "C", "implied_vol": 0.26},
        {"strike": "205000", "right": "C", "implied_vol": 0.25},
    ]
    return td


@pytest.fixture
def scanner(mock_td: MagicMock) -> EarningsScanner:
    return EarningsScanner(mock_td)


# ══════════════════════════════════════════════════════════════════════
# Test: dataclass
# ══════════════════════════════════════════════════════════════════════

class TestEarningsResultDataclass:
    """EarningsResult struct."""

    def test_basic_fields(self) -> None:
        r = EarningsResult(
            ticker="AAPL", spot=200.0, earnings_date="2026-07-30",
            expiry_before="20260724", expiry_after="20260806",
            iv_before_pct=26.0, iv_after_pct=32.0,
            premium_pct=6.0, signal="HIGH",
            num_strikes_before=4, num_strikes_after=4,
            timestamp="2026-07-29T12:00:00+00:00",
        )
        assert r.ticker == "AAPL"
        assert r.premium_pct == 6.0
        assert r.signal == "HIGH"
        assert r.error is None

    def test_error_field(self) -> None:
        r = EarningsResult(
            ticker="AAPL", spot=0.0, earnings_date="",
            expiry_before="", expiry_after="",
            iv_before_pct=0.0, iv_after_pct=0.0,
            premium_pct=0.0, signal="UNKNOWN",
            num_strikes_before=0, num_strikes_after=0,
            timestamp="", error="no_earnings_date",
        )
        assert r.error == "no_earnings_date"
        assert r.signal == "UNKNOWN"


# ══════════════════════════════════════════════════════════════════════
# Test: get_earnings_date
# ══════════════════════════════════════════════════════════════════════

class TestGetEarningsDate:
    """EarningsScanner.get_earnings_date() static lookup."""

    def test_known_ticker(self, scanner: EarningsScanner) -> None:
        date = scanner.get_earnings_date("AAPL")
        assert date == EARNINGS_CALENDAR["AAPL"]

    def test_case_insensitive(self, scanner: EarningsScanner) -> None:
        date = scanner.get_earnings_date("aapl")
        assert date == EARNINGS_CALENDAR["AAPL"]

    def test_unknown_ticker_returns_none(self, scanner: EarningsScanner) -> None:
        assert scanner.get_earnings_date("XYZ") is None

    def test_empty_string_returns_none(self, scanner: EarningsScanner) -> None:
        assert scanner.get_earnings_date("") is None


# ══════════════════════════════════════════════════════════════════════
# Test: scan_earnings (full pipeline, mocked)
# ══════════════════════════════════════════════════════════════════════

class TestScanEarnings:
    """EarningsScanner.scan_earnings() — full pipeline with mocked TD."""

    def test_successful_scan(self, scanner: EarningsScanner) -> None:
        """Patch AAPL earnings date to 2027-08-01 so it falls between
        the mock expiry dates (2027-07-24 and 2027-08-06/21).

        Target after = earnings + 14d = 2027-08-15.
        Closest after: 2027-08-21 (6d away) vs 2027-08-06 (9d away).
        """
        patched_earnings = "2027-08-01"
        with patch.dict(
            "scanner.earnings_scanner.EARNINGS_CALENDAR",
            {"AAPL": patched_earnings},
        ):
            results = scanner.scan_earnings(["AAPL"])
        assert len(results) == 1
        r = results[0]
        assert r.ticker == "AAPL"
        assert r.spot == 200.0
        assert r.earnings_date == patched_earnings
        assert r.expiry_before == "20270724"  # closest pre-earnings to target
        assert r.expiry_after == "20270821"   # closest post-earnings to target
        assert r.iv_before_pct > 0
        assert r.iv_after_pct > 0
        assert r.error is None
        assert r.signal in ("HIGH", "MODERATE", "LOW")

    def test_unknown_ticker_returns_error(self, scanner: EarningsScanner) -> None:
        results = scanner.scan_earnings(["XYZ"])
        assert len(results) == 1
        r = results[0]
        assert r.error == "no_earnings_date"

    def test_spot_fetch_failure(self, mock_td: MagicMock) -> None:
        mock_td.fetch_spot_price.side_effect = RuntimeError("API down")
        s = EarningsScanner(mock_td)
        results = s.scan_earnings(["AAPL"])
        r = results[0]
        assert "spot_fetch" in (r.error or "")

    def test_no_expirations(self, mock_td: MagicMock) -> None:
        mock_td.list_expirations.return_value = []
        s = EarningsScanner(mock_td)
        results = s.scan_earnings(["AAPL"])
        r = results[0]
        assert r.error == "no_expirations"

    def test_list_expirations_failure(self, mock_td: MagicMock) -> None:
        mock_td.list_expirations.side_effect = RuntimeError("API error")
        s = EarningsScanner(mock_td)
        results = s.scan_earnings(["AAPL"])
        r = results[0]
        assert "list_expirations:" in (r.error or "")

    def test_bad_earnings_date_format(self, mock_td: MagicMock) -> None:
        with patch.dict(
            "scanner.earnings_scanner.EARNINGS_CALENDAR",
            {"AAPL": "not-a-date"},
        ):
            s = EarningsScanner(mock_td)
            results = s.scan_earnings(["AAPL"])
            r = results[0]
            assert "bad_date_format" in (r.error or "")

    def test_zero_spot_returns_no_spot(self, mock_td: MagicMock) -> None:
        mock_td.fetch_spot_price.return_value = 0.0
        s = EarningsScanner(mock_td)
        results = s.scan_earnings(["AAPL"])
        r = results[0]
        assert r.error == "no_spot"

    def test_multiple_tickers(self, scanner: EarningsScanner) -> None:
        with patch.dict(
            "scanner.earnings_scanner.EARNINGS_CALENDAR",
            {"AAPL": "2027-08-01", "MSFT": "2027-08-01"},
        ):
            results = scanner.scan_earnings(["AAPL", "MSFT", "XYZ"])
        assert len(results) == 3
        assert results[0].ticker == "AAPL"
        assert results[0].error is None
        assert results[1].ticker == "MSFT"
        assert results[1].error is None
        assert results[2].ticker == "XYZ"
        assert results[2].error == "no_earnings_date"

    def test_no_suitable_expiry(self, mock_td: MagicMock) -> None:
        """All expirations are on the same side of the earnings date."""
        mock_td.list_expirations.return_value = [
            "20260901",  # all after earnings
            "20260915",
        ]
        s = EarningsScanner(mock_td)
        results = s.scan_earnings(["AAPL"])
        r = results[0]
        assert "no_suitable_expiry" in (r.error or "")


# ══════════════════════════════════════════════════════════════════════
# Test: _atm_iv
# ══════════════════════════════════════════════════════════════════════

class TestAtmIv:
    """EarningsScanner._atm_iv() — median IV extraction."""

    def test_returns_median_iv(self, scanner: EarningsScanner) -> None:
        iv_pct, n = scanner._atm_iv("AAPL", "20260724")
        # Implied vols: 0.28, 0.27, 0.26, 0.25 → median = 0.265 → 26.5%
        assert 26.4 <= iv_pct <= 26.6
        assert n == 4

    def test_empty_greeks_returns_zero(self, mock_td: MagicMock) -> None:
        mock_td.option_bulk_greeks.return_value = []
        s = EarningsScanner(mock_td)
        iv_pct, n = s._atm_iv("AAPL", "20260724")
        assert iv_pct == 0.0
        assert n == 0

    def test_greeks_failure_returns_zero(self, mock_td: MagicMock) -> None:
        mock_td.option_bulk_greeks.side_effect = RuntimeError("fail")
        s = EarningsScanner(mock_td)
        iv_pct, n = s._atm_iv("AAPL", "20260724")
        assert iv_pct == 0.0
        assert n == 0


# ══════════════════════════════════════════════════════════════════════
# Test: format functions
# ══════════════════════════════════════════════════════════════════════

class TestFormatEarnings:
    """format_earnings() table output."""

    def test_format_empty_list(self) -> None:
        assert "(no earnings results)" in format_earnings([])

    def test_format_successful_result(self) -> None:
        results = [
            EarningsResult(
                ticker="AAPL", spot=200.0, earnings_date="2026-07-30",
                expiry_before="20260724", expiry_after="20260806",
                iv_before_pct=26.0, iv_after_pct=32.0,
                premium_pct=6.0, signal="HIGH",
                num_strikes_before=4, num_strikes_after=4,
                timestamp="2026-07-29T12:00:00+00:00",
            ),
        ]
        output = format_earnings(results)
        assert "AAPL" in output
        assert "2026-07-30" in output
        assert "HIGH" in output

    def test_format_error_result(self) -> None:
        results = [
            EarningsResult(
                ticker="XYZ", spot=0.0, earnings_date="",
                expiry_before="", expiry_after="",
                iv_before_pct=0.0, iv_after_pct=0.0,
                premium_pct=0.0, signal="UNKNOWN",
                num_strikes_before=0, num_strikes_after=0,
                timestamp="", error="no_earnings_date",
            ),
        ]
        output = format_earnings(results)
        assert "ERROR" in output
        assert "no_earnings_date" in output

    def test_format_mixed_results(self) -> None:
        results = [
            EarningsResult(
                ticker="AAPL", spot=200.0, earnings_date="2026-07-30",
                expiry_before="20260724", expiry_after="20260806",
                iv_before_pct=26.0, iv_after_pct=32.0,
                premium_pct=6.0, signal="HIGH",
                num_strikes_before=4, num_strikes_after=4,
                timestamp="2026-07-29T12:00:00+00:00",
            ),
            EarningsResult(
                ticker="XYZ", spot=0.0, earnings_date="",
                expiry_before="", expiry_after="",
                iv_before_pct=0.0, iv_after_pct=0.0,
                premium_pct=0.0, signal="UNKNOWN",
                num_strikes_before=0, num_strikes_after=0,
                timestamp="", error="no_earnings_date",
            ),
        ]
        output = format_earnings(results)
        assert "AAPL" in output
        assert "XYZ" in output
        assert "ERROR" in output
        assert "HIGH" in output


class TestFormatEarningsOne:
    """format_earnings_one() single-line output."""

    def test_success_line(self) -> None:
        r = EarningsResult(
            ticker="AAPL", spot=200.0, earnings_date="2026-07-30",
            expiry_before="20260724", expiry_after="20260806",
            iv_before_pct=26.0, iv_after_pct=32.0,
            premium_pct=6.0, signal="HIGH",
            num_strikes_before=4, num_strikes_after=4,
            timestamp="2026-07-29T12:00:00+00:00",
        )
        line = format_earnings_one(r)
        assert "AAPL" in line
        assert "HIGH" in line
        assert "+6.0pp" in line or "6.0pp" in line

    def test_error_line(self) -> None:
        r = EarningsResult(
            ticker="XYZ", spot=0.0, earnings_date="",
            expiry_before="", expiry_after="",
            iv_before_pct=0.0, iv_after_pct=0.0,
            premium_pct=0.0, signal="UNKNOWN",
            num_strikes_before=0, num_strikes_after=0,
            timestamp="", error="no_earnings_date",
        )
        line = format_earnings_one(r)
        assert "XYZ" in line
        assert "ERROR" in line


# ══════════════════════════════════════════════════════════════════════
# Test: scan_ticker (standalone convenience)
# ══════════════════════════════════════════════════════════════════════

class TestScanTicker:
    """scan_ticker() convenience wrapper."""

    def test_with_provided_td(self, mock_td: MagicMock) -> None:
        with patch.dict(
            "scanner.earnings_scanner.EARNINGS_CALENDAR",
            {"AAPL": "2027-08-01"},
        ):
            result = scan_ticker("AAPL", td=mock_td)
        assert result is not None
        assert result.ticker == "AAPL"
        assert result.error is None
        assert result.iv_before_pct > 0

    def test_with_unknown_ticker(self, mock_td: MagicMock) -> None:
        result = scan_ticker("XYZ", td=mock_td)
        assert result is not None
        assert result.error == "no_earnings_date"


# ══════════════════════════════════════════════════════════════════════
# Test: signal thresholds
# ══════════════════════════════════════════════════════════════════════

class TestEarningsVolSignal:
    """Verify signal classification thresholds."""

    def test_premium_below_threshold_is_low(self) -> None:
        r = EarningsResult(
            ticker="SPY", spot=500.0, earnings_date="2026-08-01",
            expiry_before="20260725", expiry_after="20260815",
            iv_before_pct=22.0, iv_after_pct=23.0,
            premium_pct=1.0, signal="LOW",
            num_strikes_before=4, num_strikes_after=4,
            timestamp="2026-07-29T12:00:00+00:00",
        )
        assert r.signal == "LOW"
        # Verify our thresholds: 1.0 < MODERATE(2.0)
        from scanner.earnings_scanner import MODERATE_PREMIUM_THRESHOLD
        assert r.premium_pct < MODERATE_PREMIUM_THRESHOLD

    def test_premium_at_moderate_is_moderate(self) -> None:
        r = EarningsResult(
            ticker="SPY", spot=500.0, earnings_date="2026-08-01",
            expiry_before="20260725", expiry_after="20260815",
            iv_before_pct=22.0, iv_after_pct=25.0,
            premium_pct=3.0, signal="MODERATE",
            num_strikes_before=4, num_strikes_after=4,
            timestamp="2026-07-29T12:00:00+00:00",
        )
        assert r.signal == "MODERATE"

    def test_premium_at_high_is_high(self) -> None:
        r = EarningsResult(
            ticker="SPY", spot=500.0, earnings_date="2026-08-01",
            expiry_before="20260725", expiry_after="20260815",
            iv_before_pct=22.0, iv_after_pct=29.0,
            premium_pct=7.0, signal="HIGH",
            num_strikes_before=4, num_strikes_after=4,
            timestamp="2026-07-29T12:00:00+00:00",
        )
        assert r.signal == "HIGH"


# ══════════════════════════════════════════════════════════════════════
# Live calendar precedence
# ══════════════════════════════════════════════════════════════════════

class TestLiveCalendarPrecedence:
    """get_earnings_date() checks the live calendar before the static
    EARNINGS_CALENDAR fallback."""

    def test_live_date_wins_over_static(
        self, scanner: EarningsScanner, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "scanner.earnings_scanner.fetch_earnings_calendar",
            lambda: {"AAPL": "2099-01-01"},
        )
        assert scanner.get_earnings_date("AAPL") == "2099-01-01"

    def test_falls_back_to_static_when_live_empty(
        self, scanner: EarningsScanner,
    ) -> None:
        # _no_live_calendar autouse fixture already makes live return {}
        assert scanner.get_earnings_date("AAPL") == EARNINGS_CALENDAR["AAPL"]

    def test_falls_back_to_static_when_live_missing_ticker(
        self, scanner: EarningsScanner, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "scanner.earnings_scanner.fetch_earnings_calendar",
            lambda: {"MSFT": "2099-02-02"},
        )
        assert scanner.get_earnings_date("AAPL") == EARNINGS_CALENDAR["AAPL"]

    def test_live_lookup_is_case_insensitive(
        self, scanner: EarningsScanner, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "scanner.earnings_scanner.fetch_earnings_calendar",
            lambda: {"AAPL": "2099-01-01"},
        )
        assert scanner.get_earnings_date("aapl") == "2099-01-01"