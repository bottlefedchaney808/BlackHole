"""Tests for CorrelationEngine's earnings-vol recording and signals."""

from __future__ import annotations

import pytest

from correlation.engine import CorrelationEngine
from scanner.earnings_scanner import EarningsResult, HIGH_PREMIUM_THRESHOLD


def _narrative_scores(cns: int) -> dict:
    return {
        "war_score": 0.2,
        "contested_narrative_score": cns,
        "volume": 10,
        "thesis_ratio": 0.5,
        "pump_ratio": 0.1,
        "bullish_pct": 40.0,
        "bearish_pct": 30.0,
    }


def _earnings_result(premium_pct: float, error: str = None) -> EarningsResult:
    return EarningsResult(
        ticker="AAPL", spot=200.0, earnings_date="2026-08-05",
        expiry_before="20260731", expiry_after="20260815",
        iv_before_pct=25.0, iv_after_pct=25.0 + premium_pct,
        premium_pct=premium_pct,
        signal="HIGH" if premium_pct >= HIGH_PREMIUM_THRESHOLD else "LOW",
        num_strikes_before=4, num_strikes_after=4,
        timestamp="2026-07-31T12:00:00+00:00", error=error,
    )


def _engine_with_cns(cns: int) -> CorrelationEngine:
    """A fresh engine with enough narrative history for cns_current to
    resolve (get_narrative_trend needs >= 2 recorded points)."""
    engine = CorrelationEngine()
    engine.record_narrative("AAPL", _narrative_scores(cns))
    engine.record_narrative("AAPL", _narrative_scores(cns))
    return engine


class TestRecordEarnings:
    def test_stores_scan_for_ticker(self) -> None:
        engine = CorrelationEngine()
        scan = _earnings_result(6.0)
        engine.record_earnings("AAPL", scan)
        assert engine._earnings["AAPL"] is scan


class TestEarningsSignals:
    def test_high_premium_with_high_cns_triggers_plus_narrative(self) -> None:
        engine = _engine_with_cns(cns=60)
        engine.record_earnings("AAPL", _earnings_result(HIGH_PREMIUM_THRESHOLD))

        result = engine.correlate_with_oi("AAPL", {})

        assert "EARNINGS_VOL_PLUS_NARRATIVE" in result["signals"]
        assert result["severity"] == "HIGH"

    def test_high_premium_with_low_cns_triggers_premium_only(self) -> None:
        engine = _engine_with_cns(cns=20)
        engine.record_earnings("AAPL", _earnings_result(HIGH_PREMIUM_THRESHOLD))

        result = engine.correlate_with_oi("AAPL", {})

        assert "EARNINGS_VOL_PREMIUM" in result["signals"]
        assert "EARNINGS_VOL_PLUS_NARRATIVE" not in result["signals"]

    def test_low_premium_triggers_no_earnings_signal(self) -> None:
        engine = _engine_with_cns(cns=60)
        engine.record_earnings("AAPL", _earnings_result(1.0))

        result = engine.correlate_with_oi("AAPL", {})

        assert "EARNINGS_VOL_PREMIUM" not in result["signals"]
        assert "EARNINGS_VOL_PLUS_NARRATIVE" not in result["signals"]

    def test_errored_earnings_result_produces_no_signal_and_no_crash(self) -> None:
        engine = _engine_with_cns(cns=60)
        engine.record_earnings(
            "AAPL", _earnings_result(0.0, error="no_earnings_date")
        )

        result = engine.correlate_with_oi("AAPL", {})

        assert "EARNINGS_VOL_PREMIUM" not in result["signals"]
        assert "EARNINGS_VOL_PLUS_NARRATIVE" not in result["signals"]

    def test_no_earnings_recorded_produces_no_signal(self) -> None:
        engine = _engine_with_cns(cns=60)
        result = engine.correlate_with_oi("AAPL", {})
        assert "EARNINGS_VOL_PREMIUM" not in result["signals"]
        assert "EARNINGS_VOL_PLUS_NARRATIVE" not in result["signals"]


class TestScannerSummaryEarnings:
    def test_ok_scan_reports_status_ok(self) -> None:
        engine = CorrelationEngine()
        engine.record_earnings("AAPL", _earnings_result(6.0))

        summary = engine.get_scanner_summary("AAPL")

        assert summary["earnings"]["status"] == "ok"
        assert summary["earnings"]["premium_pct"] == 6.0
        assert summary["earnings"]["signal"] == "HIGH"
        assert summary["earnings"]["earnings_date"] == "2026-08-05"

    def test_errored_scan_reports_error_status(self) -> None:
        engine = CorrelationEngine()
        engine.record_earnings("AAPL", _earnings_result(0.0, error="no_spot"))

        summary = engine.get_scanner_summary("AAPL")

        assert "error: no_spot" in summary["earnings"]["status"]

    def test_no_scan_recorded_reports_error_status(self) -> None:
        engine = CorrelationEngine()
        summary = engine.get_scanner_summary("AAPL")
        assert "error" in summary["earnings"]["status"]
        assert summary["earnings"]["premium_pct"] == 0.0
        assert summary["earnings"]["signal"] == "UNKNOWN"
