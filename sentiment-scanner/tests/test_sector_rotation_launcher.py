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

        launcher.run_once(period="3mo")

        captured = capsys.readouterr()
        assert "TABLE:['FAKE_RANK']" in captured.out


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
