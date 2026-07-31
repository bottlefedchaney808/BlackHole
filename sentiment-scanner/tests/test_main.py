"""Tests for main.py's scanner-loop wiring."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main as main_mod


class _FakeScan:
    """Minimal stand-in for a scanner result dataclass."""

    def __init__(self, tag: str, error=None) -> None:
        self.tag = tag
        self.error = error


def _fake_scanners():
    """Stand-in for main._import_scanners()'s 12-tuple return."""

    def scan_gex(ticker):
        return _FakeScan("gex")

    def fmt_gex(s):
        return f"  GEX:{s.tag}"

    def scan_oi(ticker):
        return _FakeScan("oi")

    def fmt_oi(s):
        return f"  OI:{s.tag}"

    def scan_iv(ticker):
        return _FakeScan("iv")

    def fmt_iv(s):
        return f"  IV:{s.tag}"

    def scan_skew(ticker):
        return _FakeScan("skew")

    def fmt_skew(s):
        return f"  SKEW:{s.tag}"

    def scan_pain(ticker):
        return _FakeScan("pain")

    def fmt_pain(s):
        return f"  PAIN:{s.tag}"

    def scan_disp(ticker, benchmark="SPY"):
        return _FakeScan("disp")

    def fmt_disp(s):
        return f"  DISP:{s.tag}"

    return (
        scan_gex, fmt_gex, scan_oi, fmt_oi, scan_iv, fmt_iv,
        scan_skew, fmt_skew, scan_pain, fmt_pain, scan_disp, fmt_disp,
    )


class TestRunOptionsScanners:
    def test_returns_lines_and_raw_for_all_seven_scanners(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(main_mod, "_import_scanners", _fake_scanners)
        monkeypatch.setattr(main_mod, "get_td", lambda: "FAKE_TD")
        monkeypatch.setattr(
            main_mod, "_scan_earnings_ticker",
            lambda ticker, td=None: _FakeScan("earn"),
        )
        monkeypatch.setattr(
            main_mod, "format_earnings_line", lambda r: f"  EARN:{r.tag}",
        )
        engine = MagicMock()

        lines, raw = main_mod.run_options_scanners("AAPL", engine)

        assert len(lines) == 7
        assert raw["gex"].tag == "gex"
        assert raw["unusual_oi"].tag == "oi"
        assert raw["iv_rank"].tag == "iv"
        assert raw["skew"].tag == "skew"
        assert raw["max_pain"].tag == "pain"
        assert raw["dispersion"].tag == "disp"
        assert raw["earnings"].tag == "earn"
        engine.record_gex.assert_called_once_with("AAPL", raw["gex"])
        engine.record_earnings.assert_called_once_with("AAPL", raw["earnings"])

    def test_skip_gex_leaves_gex_raw_none(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(main_mod, "_import_scanners", _fake_scanners)
        monkeypatch.setattr(main_mod, "get_td", lambda: "FAKE_TD")
        monkeypatch.setattr(
            main_mod, "_scan_earnings_ticker",
            lambda ticker, td=None: _FakeScan("earn"),
        )
        monkeypatch.setattr(
            main_mod, "format_earnings_line", lambda r: f"  EARN:{r.tag}",
        )
        engine = MagicMock()

        lines, raw = main_mod.run_options_scanners("AAPL", engine, skip_gex=True)

        assert raw["gex"] is None
        assert len(lines) == 6
        engine.record_gex.assert_not_called()

    def test_scanner_exception_produces_error_line_and_none_raw(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        scanners = list(_fake_scanners())

        def _boom(ticker):
            raise RuntimeError("boom")

        scanners[0] = _boom  # scan_gex
        monkeypatch.setattr(main_mod, "_import_scanners", lambda: tuple(scanners))
        monkeypatch.setattr(main_mod, "get_td", lambda: "FAKE_TD")
        monkeypatch.setattr(
            main_mod, "_scan_earnings_ticker",
            lambda ticker, td=None: _FakeScan("earn"),
        )
        monkeypatch.setattr(
            main_mod, "format_earnings_line", lambda r: f"  EARN:{r.tag}",
        )
        engine = MagicMock()

        lines, raw = main_mod.run_options_scanners("AAPL", engine)

        assert raw["gex"] is None
        assert any("GEX: ERROR" in line for line in lines)

    def test_earnings_scanner_exception_produces_error_line(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(main_mod, "_import_scanners", _fake_scanners)
        monkeypatch.setattr(main_mod, "get_td", lambda: "FAKE_TD")

        def _boom(ticker, td=None):
            raise RuntimeError("earnings api down")

        monkeypatch.setattr(main_mod, "_scan_earnings_ticker", _boom)
        engine = MagicMock()

        lines, raw = main_mod.run_options_scanners("AAPL", engine)

        assert raw["earnings"] is None
        assert any("EARN: ERROR" in line for line in lines)
        engine.record_earnings.assert_not_called()

    def test_earnings_none_result_skips_record_and_line(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(main_mod, "_import_scanners", _fake_scanners)
        monkeypatch.setattr(main_mod, "get_td", lambda: "FAKE_TD")
        monkeypatch.setattr(
            main_mod, "_scan_earnings_ticker",
            lambda ticker, td=None: None,
        )
        engine = MagicMock()

        lines, raw = main_mod.run_options_scanners("AAPL", engine)

        assert raw["earnings"] is None
        assert len(lines) == 6
        engine.record_earnings.assert_not_called()


class TestScanTrendingReturnsRaw:
    def test_returns_alerts_and_cycle_raw(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        st = MagicMock()
        st.get_trending.return_value = [{"symbol": "AAPL"}]
        engine = MagicMock()

        monkeypatch.setattr(main_mod, "scan_ticker", lambda st, t, e: None)
        monkeypatch.setattr(
            main_mod, "run_options_scanners",
            lambda ticker, engine, benchmark, skip_gex: (
                [f"  {ticker}: line"], {"gex": _FakeScan("gex")},
            ),
        )
        monkeypatch.setattr(main_mod, "_youtube_scan", lambda t: None)
        monkeypatch.setattr(main_mod.time, "sleep", lambda s: None)

        alerts, cycle_raw = main_mod.scan_trending(st, engine, skip_youtube=True)

        assert alerts == []
        assert cycle_raw == {"AAPL": {"gex": cycle_raw["AAPL"]["gex"]}}
        assert cycle_raw["AAPL"]["gex"].tag == "gex"


class TestScanTrendingEarningsDigest:
    def test_prints_digest_once_per_cycle_before_ticker_loop(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        monkeypatch.setattr(
            main_mod, "upcoming_earnings",
            lambda days=7, static_fallback=None: [("AAPL", "2026-08-05")],
        )
        monkeypatch.setattr(
            main_mod, "fetch_earnings_calendar",
            lambda: {"AAPL": "2026-08-05"},
        )
        monkeypatch.setattr(
            main_mod, "format_earnings_digest",
            lambda entries, days=7, live=True: f"  DIGEST:{len(entries)}:live={live}",
        )
        st = MagicMock()
        st.get_trending.return_value = []
        engine = MagicMock()

        alerts, cycle_raw = main_mod.scan_trending(st, engine)

        captured = capsys.readouterr()
        assert "DIGEST:1:live=True" in captured.out
        assert alerts == []
        assert cycle_raw == {}

    def test_passes_static_calendar_as_fallback(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured_kwargs = {}

        def _fake_upcoming(days=7, static_fallback=None):
            captured_kwargs["days"] = days
            captured_kwargs["static_fallback"] = static_fallback
            return []

        monkeypatch.setattr(main_mod, "upcoming_earnings", _fake_upcoming)
        monkeypatch.setattr(main_mod, "fetch_earnings_calendar", lambda: {})
        monkeypatch.setattr(
            main_mod, "format_earnings_digest",
            lambda entries, days=7, live=True: "  DIGEST",
        )
        st = MagicMock()
        st.get_trending.return_value = []
        engine = MagicMock()

        main_mod.scan_trending(st, engine)

        assert captured_kwargs["days"] == 7
        assert captured_kwargs["static_fallback"] is main_mod.EARNINGS_CALENDAR

    def test_live_false_when_live_calendar_empty(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured_kwargs = {}

        monkeypatch.setattr(
            main_mod, "upcoming_earnings",
            lambda days=7, static_fallback=None: [],
        )
        monkeypatch.setattr(main_mod, "fetch_earnings_calendar", lambda: {})

        def _fake_format(entries, days=7, live=True):
            captured_kwargs["live"] = live
            return "  DIGEST"

        monkeypatch.setattr(main_mod, "format_earnings_digest", _fake_format)
        st = MagicMock()
        st.get_trending.return_value = []
        engine = MagicMock()

        main_mod.scan_trending(st, engine)

        assert captured_kwargs["live"] is False

    def test_live_is_driven_by_fetch_earnings_calendar_not_entries(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Regression test: `live` must come from `fetch_earnings_calendar()`
        directly, not be inferred from whether `entries` is non-empty. Here
        the live calendar has data (so live=True is correct) but nothing
        falls inside the 7-day window (so entries=[]) — a buggy
        `live = bool(entries)` implementation would report live=False and
        pass the other tests in this class, which never separate the two
        signals."""
        captured_kwargs = {}

        monkeypatch.setattr(
            main_mod, "upcoming_earnings",
            lambda days=7, static_fallback=None: [],
        )
        monkeypatch.setattr(
            main_mod, "fetch_earnings_calendar",
            lambda: {"AAPL": "2099-01-01"},  # live has data, just out of window
        )

        def _fake_format(entries, days=7, live=True):
            captured_kwargs["entries"] = entries
            captured_kwargs["live"] = live
            return "  DIGEST"

        monkeypatch.setattr(main_mod, "format_earnings_digest", _fake_format)
        st = MagicMock()
        st.get_trending.return_value = []
        engine = MagicMock()

        main_mod.scan_trending(st, engine)

        assert captured_kwargs["entries"] == []
        assert captured_kwargs["live"] is True
