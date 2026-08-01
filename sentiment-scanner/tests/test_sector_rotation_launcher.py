"""Tests for the standalone sector-rotation launcher script."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sector_rotation_launcher as launcher


class TestRunOnce:
    def test_calls_rank_sectors_and_prints_formatted_output(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        fake_ranks = ["FAKE_RANK"]
        monkeypatch.setattr(launcher, "rank_sectors", lambda period="6mo": fake_ranks)
        monkeypatch.setattr(launcher, "format_rotation", lambda ranks: f"TABLE:{ranks}")
        earnings_calls = []
        monkeypatch.setattr(
            launcher, "_run_earnings_check", lambda ranks: earnings_calls.append(ranks),
        )

        launcher.run_once(period="3mo")

        assert earnings_calls == [fake_ranks]

        captured = capsys.readouterr()
        assert "TABLE:['FAKE_RANK']" in captured.out


class _FakeRank:
    def __init__(self, ticker: str, signal: str) -> None:
        self.ticker = ticker
        self.signal = signal


class TestRunEarningsCheck:
    def test_empty_ranks_is_a_noop(self, capsys: pytest.CaptureFixture) -> None:
        launcher._run_earnings_check([])
        assert capsys.readouterr().out == ""

    def test_prefers_bullish_tickers(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        ranks = [
            _FakeRank("XLK", "BULLISH"),
            _FakeRank("XLF", "NEUTRAL"),
            _FakeRank("XLE", "BULLISH"),
            _FakeRank("XLU", "BEARISH"),
        ]
        monkeypatch.setattr(launcher, "get_td", lambda: "FAKE_TD")

        scanned = {}

        class _FakeScanner:
            def __init__(self, td):
                scanned["td"] = td

            def scan_earnings(self, tickers):
                scanned["tickers"] = tickers
                return ["RESULT"]

        monkeypatch.setattr(launcher, "EarningsScanner", _FakeScanner)
        monkeypatch.setattr(launcher, "format_earnings", lambda results: f"EARN:{results}")

        launcher._run_earnings_check(ranks, top_n=3)

        assert scanned["td"] == "FAKE_TD"
        assert scanned["tickers"] == ["XLK", "XLE"]
        assert "EARN:['RESULT']" in capsys.readouterr().out

    def test_falls_back_to_top_ranked_when_none_bullish(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ranks = [
            _FakeRank("XLK", "NEUTRAL"),
            _FakeRank("XLF", "NEUTRAL"),
            _FakeRank("XLE", "BEARISH"),
        ]
        monkeypatch.setattr(launcher, "get_td", lambda: "FAKE_TD")

        scanned = {}

        class _FakeScanner:
            def __init__(self, td):
                pass

            def scan_earnings(self, tickers):
                scanned["tickers"] = tickers
                return []

        monkeypatch.setattr(launcher, "EarningsScanner", _FakeScanner)
        monkeypatch.setattr(launcher, "format_earnings", lambda results: "")

        launcher._run_earnings_check(ranks, top_n=2)

        assert scanned["tickers"] == ["XLK", "XLF"]

    def test_errors_are_caught_and_reported(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        ranks = [_FakeRank("XLK", "BULLISH")]

        def _boom():
            raise RuntimeError("no theta connection")

        monkeypatch.setattr(launcher, "get_td", _boom)

        launcher._run_earnings_check(ranks)  # must not raise

        assert "Earnings check failed" in capsys.readouterr().out


class TestMain:
    def test_single_run_does_not_loop(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = []
        monkeypatch.setattr(launcher, "run_once", lambda period="6mo": calls.append(period))
        sleep_calls = []
        monkeypatch.setattr(launcher.time, "sleep", lambda s: sleep_calls.append(s))

        launcher.main([])

        assert calls == ["6mo"]
        assert sleep_calls == []

    def test_loop_flag_sleeps_between_runs(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = []
        monkeypatch.setattr(launcher, "run_once", lambda period="6mo": calls.append(period))

        sleep_calls = []

        def _fake_sleep(seconds):
            sleep_calls.append(seconds)
            raise KeyboardInterrupt()

        monkeypatch.setattr(launcher.time, "sleep", _fake_sleep)

        launcher.main(["--loop"])

        assert len(calls) == 1  # initial run_once before the loop
        assert len(sleep_calls) == 1

    def test_period_flag_passed_through(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = []
        monkeypatch.setattr(launcher, "run_once", lambda period="6mo": calls.append(period))

        launcher.main(["--period", "3mo"])

        assert calls == ["3mo"]

    def test_reconfigures_stdout_to_utf8(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Guards against the Windows cp1252 UnicodeEncodeError crash --
        main() should attempt to reconfigure stdout to utf-8 up front."""
        monkeypatch.setattr(launcher, "run_once", lambda period="6mo": None)

        reconfigure_calls = []

        class _FakeStdout:
            def reconfigure(self, **kw):
                reconfigure_calls.append(kw)

        monkeypatch.setattr(launcher.sys, "stdout", _FakeStdout())

        launcher.main([])

        assert reconfigure_calls == [{"encoding": "utf-8", "errors": "replace"}]
