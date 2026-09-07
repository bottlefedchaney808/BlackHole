"""Tests for main.py's scanner-loop wiring."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _load_sentiment_main():
    """Load sentiment-scanner/main.py under a unique module name.

    Options_Suite, VaR_Tools_Simulations and sentiment-scanner each ship
    their own `main.py` -- a bare `import main` silently returns whichever
    one another test file already cached in sys.modules under that generic
    name during pytest's combined collection, instead of raising.
    """
    module_name = "sentiment_scanner_main"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(
        module_name, str(Path(__file__).resolve().parent.parent / "main.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


main_mod = _load_sentiment_main()


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
        monkeypatch.setattr(main_mod, "run_reddit_scanner", lambda t, e: (None, None))
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


class TestPromptYesNo:
    def test_skip_true_returns_false_without_prompting(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        called = []
        monkeypatch.setattr("builtins.input", lambda *_: called.append(1) or "y")
        assert main_mod._prompt_yes_no("Q?", skip=True) is False
        assert called == []

    def test_non_tty_returns_false_without_prompting(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(main_mod.sys.stdin, "isatty", lambda: False)
        called = []
        monkeypatch.setattr("builtins.input", lambda *_: called.append(1) or "y")
        assert main_mod._prompt_yes_no("Q?") is False
        assert called == []

    def test_tty_yes_answer_returns_true(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(main_mod.sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda *_: "y")
        assert main_mod._prompt_yes_no("Q?") is True

    def test_tty_no_answer_returns_false(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(main_mod.sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda *_: "n")
        assert main_mod._prompt_yes_no("Q?") is False

    def test_tty_empty_answer_returns_false(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(main_mod.sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda *_: "")
        assert main_mod._prompt_yes_no("Q?") is False


class TestLaunchSectorRotation:
    def test_invokes_subprocess_with_launcher_path_and_timeout(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = []

        def _fake_run(cmd, **kw):
            calls.append((cmd, kw))
            result = MagicMock()
            result.returncode = 0
            result.stdout = "ok"
            return result

        monkeypatch.setattr(main_mod.subprocess, "run", _fake_run)

        main_mod._launch_sector_rotation()

        assert len(calls) == 1
        cmd, kwargs = calls[0]
        assert cmd[0] == main_mod.sys.executable
        assert cmd[1].endswith("sector_rotation_launcher.py")
        assert kwargs["timeout"] == 1800
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True

    def test_timeout_is_caught_and_reported(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        def _fake_run(cmd, **kw):
            raise main_mod.subprocess.TimeoutExpired(cmd, kw.get("timeout", 1800))

        monkeypatch.setattr(main_mod.subprocess, "run", _fake_run)

        main_mod._launch_sector_rotation()  # must not raise

        captured = capsys.readouterr()
        assert "timed out" in captured.out.lower()


class TestRawResultToDict:
    def test_none_result_returns_error_dict(self) -> None:
        assert main_mod._raw_result_to_dict("gex", None) == {"error": "no_data"}

    def test_plain_object_converted_via_vars(self) -> None:
        obj = _FakeScan("gex", error=None)
        result = main_mod._raw_result_to_dict("gex", obj)
        assert result == {"tag": "gex", "error": None}

    def test_dict_passed_through(self) -> None:
        result = main_mod._raw_result_to_dict("gex", {"a": 1})
        assert result == {"a": 1}

    def test_nested_dataclass_field_is_recursively_converted(self) -> None:
        """CARL R1-F1 regression test: UnusualOiScan.top_strikes is
        List[OiStrike] (a nested dataclass) — the converted dict must
        contain plain dicts, not OiStrike objects, or report.py's
        `.get('strike', 0)` calls on each entry raise AttributeError."""
        from dataclasses import dataclass

        @dataclass
        class _FakeStrike:
            strike: float
            right: str
            oi: int

        @dataclass
        class _FakeOiScan:
            ticker: str
            top_strikes: list
            error: object = None

        scan = _FakeOiScan(
            ticker="AAPL",
            top_strikes=[_FakeStrike(strike=200.0, right="C", oi=500)],
        )

        result = main_mod._raw_result_to_dict("unusual_oi", scan)

        assert result["ticker"] == "AAPL"
        assert isinstance(result["top_strikes"][0], dict)
        assert result["top_strikes"][0] == {
            "strike": 200.0, "right": "C", "oi": 500,
        }
        # Prove it actually round-trips through .get() the way report.py uses it:
        assert result["top_strikes"][0].get("strike", 0) == 200.0


class TestMaybeBuildReport:
    def test_skips_when_no_cycle_data(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        prompts = []
        monkeypatch.setattr(
            main_mod, "_prompt_yes_no",
            lambda *a, **kw: prompts.append(1) or True,
        )
        main_mod._maybe_build_report(MagicMock(), {})
        assert prompts == []

    def test_skips_when_user_declines(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(main_mod, "_prompt_yes_no", lambda *a, **kw: False)
        report_calls = []
        monkeypatch.setattr(
            main_mod, "ScannerReport",
            lambda **kw: report_calls.append(1),
        )
        main_mod._maybe_build_report(MagicMock(), {"AAPL": {"gex": None}})
        assert report_calls == []

    def test_builds_and_saves_report_when_confirmed(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(main_mod, "_prompt_yes_no", lambda *a, **kw: True)

        fake_report = MagicMock()
        monkeypatch.setattr(main_mod, "ScannerReport", lambda **kw: fake_report)

        engine = MagicMock()
        engine.correlate_with_oi.return_value = {
            "signals": ["OI_SURGE"], "severity": "MEDIUM",
        }

        cycle_raw = {
            "AAPL": {"gex": _FakeScan("gex"), "unusual_oi": None},
        }

        main_mod._maybe_build_report(engine, cycle_raw)

        fake_report.add_ticker_results.assert_any_call(
            "AAPL", "gex", {"tag": "gex", "error": None},
        )
        fake_report.add_ticker_results.assert_any_call(
            "AAPL", "unusual_oi", {"error": "no_data"},
        )
        fake_report.add_signals.assert_called_once_with(
            "AAPL", ["OI_SURGE"], "MEDIUM",
        )
        fake_report.save.assert_called_once_with(out_dir=main_mod.config.OUTPUT_DIR)


class TestMainKeyboardInterruptBeforeFirstCycle:
    """Regression test for a whole-branch-review finding: `cycle_raw` was
    only bound inside the try block (by the first `scan_trending(...)`
    call), so a KeyboardInterrupt raised *during* that first call — the
    single longest-running window in the program (7 scanners x N tickers)
    — hit the `except KeyboardInterrupt:` handler's `_maybe_build_report(
    engine, cycle_raw, ...)` call with `cycle_raw` never assigned,
    raising UnboundLocalError instead of shutting down cleanly."""

    def test_interrupt_during_initial_scan_does_not_crash(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_args = MagicMock()
        fake_args.skip_sector_prompt = True
        fake_args.skip_report_prompt = True
        fake_args.no_loop = False
        fake_args.benchmark = "SPY"
        fake_args.skip_gex = False
        fake_args.skip_youtube = False
        fake_args.export_context_path = None
        fake_args.launch_vol_suite = False
        fake_args.universe = None
        monkeypatch.setattr(main_mod, "_parse_args", lambda: fake_args)

        st = MagicMock()
        monkeypatch.setattr(main_mod, "StockTwitsScraper", lambda: st)
        monkeypatch.setattr(main_mod, "CorrelationEngine", lambda: MagicMock())
        monkeypatch.setattr(main_mod, "close_td", lambda: None)
        monkeypatch.setattr(main_mod, "_prompt_yes_no", lambda *a, **kw: False)

        launch_calls = []
        monkeypatch.setattr(
            main_mod, "_launch_sector_rotation", lambda: launch_calls.append(1),
        )

        report_calls = []
        monkeypatch.setattr(
            main_mod, "_maybe_build_report",
            lambda engine, cycle_raw, skip=False: report_calls.append(cycle_raw),
        )

        def _raise_interrupt(*a, **kw):
            raise KeyboardInterrupt()

        monkeypatch.setattr(main_mod, "scan_trending", _raise_interrupt)

        main_mod.main()  # must not raise

        assert report_calls == [{}]
        assert launch_calls == []
        st.close.assert_called_once()


class TestRunDirectionalScan:
    """--universe TICKER,TICKER runs this instead of the trending-symbol
    scrape: narrative + 6 scanners (GEX skipped) + real OI + composite
    signals over an explicit ticker list, one-shot, JSON output."""

    def _patch_common(self, monkeypatch, oi_snapshot=None, correlate_result=None):
        monkeypatch.setattr(main_mod, "_import_scanners", _fake_scanners)
        monkeypatch.setattr(main_mod, "get_td", lambda: "FAKE_TD")
        monkeypatch.setattr(
            main_mod, "_scan_earnings_ticker", lambda ticker, td=None: _FakeScan("earn"))
        monkeypatch.setattr(
            main_mod, "format_earnings_line", lambda r: f"  EARN:{r.tag}")
        monkeypatch.setattr(
            main_mod, "build_oi_snapshot",
            lambda ticker: oi_snapshot if oi_snapshot is not None else {"total_oi": 100})

        st = MagicMock()
        st.get_ticker_stream.return_value = [{"body": "to the moon"}]
        monkeypatch.setattr(main_mod, "StockTwitsScraper", lambda: st)

        monkeypatch.setattr(
            main_mod, "score_messages",
            lambda msgs: {
                "contested_narrative_score": 55, "war_score": 0.4,
                "bullish_pct": 60.0, "bearish_pct": 20.0, "volume": len(msgs),
            },
        )
        return st

    def test_writes_a_result_per_ticker_and_a_json_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path,
    ) -> None:
        monkeypatch.setattr(main_mod, "__file__", str(tmp_path / "main.py"))
        self._patch_common(monkeypatch)
        engine = MagicMock()
        engine.correlate_with_oi.return_value = {"signals": [], "severity": "LOW"}

        results, out_path = main_mod.run_directional_scan(["AAPL", "NVDA"], engine)

        assert set(results) == {"AAPL", "NVDA"}
        assert results["AAPL"]["narrative"]["cns"] == 55
        assert results["AAPL"]["scanners"]["unusual_oi"]["status"] == "ok"
        assert "gex" not in results["AAPL"]["scanners"]  # GEX always skipped here
        assert Path(out_path).exists()
        saved = json.loads(Path(out_path).read_text(encoding="utf-8"))
        assert saved["universe_size"] == 2
        assert set(saved["results"]) == {"AAPL", "NVDA"}

    def test_strong_flag_set_when_severity_is_high(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path,
    ) -> None:
        monkeypatch.setattr(main_mod, "__file__", str(tmp_path / "main.py"))
        self._patch_common(monkeypatch)
        engine = MagicMock()
        engine.correlate_with_oi.return_value = {
            "signals": ["GAMMA_SQUEEZE_RISK"], "severity": "HIGH"}

        results, _ = main_mod.run_directional_scan(["AAPL"], engine)

        assert results["AAPL"]["strong"] is True

    def test_strong_flag_false_on_low_severity_single_signal_low_cns(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path,
    ) -> None:
        monkeypatch.setattr(main_mod, "__file__", str(tmp_path / "main.py"))
        self._patch_common(monkeypatch)
        engine = MagicMock()
        engine.correlate_with_oi.return_value = {
            "signals": ["OI_SURGE_WITH_NARRATIVE"], "severity": "LOW"}
        monkeypatch.setattr(
            main_mod, "score_messages",
            lambda msgs: {
                "contested_narrative_score": 10, "war_score": 0.1,
                "bullish_pct": 50.0, "bearish_pct": 50.0, "volume": 1,
            },
        )

        results, _ = main_mod.run_directional_scan(["AAPL"], engine)

        assert results["AAPL"]["strong"] is False

    def test_a_ticker_erroring_does_not_abort_the_rest_of_the_universe(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path,
    ) -> None:
        monkeypatch.setattr(main_mod, "__file__", str(tmp_path / "main.py"))
        st = self._patch_common(monkeypatch)

        def _stream(ticker, max_pages=2):
            if ticker == "BAD":
                raise RuntimeError("stocktwits boom")
            return [{"body": "hi"}]
        st.get_ticker_stream.side_effect = _stream

        engine = MagicMock()
        engine.correlate_with_oi.return_value = {"signals": [], "severity": "LOW"}

        results, _ = main_mod.run_directional_scan(["BAD", "AAPL"], engine)

        assert results["BAD"]["errors"] == ["narrative:stocktwits boom"]
        assert results["AAPL"]["narrative"]["cns"] == 55


class TestMainUniverseDispatch:
    def test_universe_flag_dispatches_to_run_directional_scan_and_returns(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_args = MagicMock()
        fake_args.universe = "aapl, nvda ,,spy"
        monkeypatch.setattr(main_mod, "_parse_args", lambda: fake_args)
        monkeypatch.setattr(main_mod, "CorrelationEngine", lambda: MagicMock())
        close_calls = []
        monkeypatch.setattr(main_mod, "close_td", lambda: close_calls.append(1))

        calls = []

        def _fake_run(tickers, engine, benchmark="SPY"):
            calls.append(tickers)
            return {}, "out.json"

        monkeypatch.setattr(main_mod, "run_directional_scan", _fake_run)

        main_mod.main()

        assert calls == [["AAPL", "NVDA", "SPY"]], "must upper-case, strip, and drop blanks"
        assert close_calls == [1]

    def test_universe_with_only_blanks_prints_a_message_and_does_not_scan(
        self, monkeypatch: pytest.MonkeyPatch, capsys,
    ) -> None:
        fake_args = MagicMock()
        fake_args.universe = " , , "
        monkeypatch.setattr(main_mod, "_parse_args", lambda: fake_args)

        calls = []
        monkeypatch.setattr(
            main_mod, "run_directional_scan", lambda *a, **kw: calls.append(1))

        main_mod.main()

        assert calls == []
        assert "no tickers" in capsys.readouterr().out


class TestScanTrendingSkipReddit:
    def test_skip_reddit_flag_skips_reddit_scan(
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
        monkeypatch.setattr(main_mod, "run_reddit_scanner", lambda t, e: ("  AAPL | Reddit: line", {"ticker": "AAPL"}))
        monkeypatch.setattr(main_mod.time, "sleep", lambda s: None)

        alerts, cycle_raw = main_mod.scan_trending(st, engine, skip_reddit=True)

        assert alerts == []
        assert cycle_raw == {"AAPL": {"gex": cycle_raw["AAPL"]["gex"]}}
        # run_reddit_scanner should not have been called
        assert engine.record_narrative.call_count == 0
        # scanner_raw['reddit'] should not be present when skipped
        assert "reddit" not in cycle_raw["AAPL"]

    def test_reddit_result_attaches_to_scanner_raw(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        st = MagicMock()
        st.get_trending.return_value = [{"symbol": "AAPL"}]
        engine = MagicMock()

        # Mock the actual RedditScraper behavior
        class FakeScraper:
            def search_posts_arctic(self, subreddit, ticker, limit=25):
                return [{"title": "AAPL is going to the moon!", "selftext": "Buy now!",
                         "score": 10, "author": "u_t", "created_utc": 0,
                         "permalink": "/r/wallstreetbets/comments/abc/"}]
            def get_recent_comments(self, subreddit, limit=50):
                return [{"body": "AAPL calls are printing", "score": 5,
                         "author": "u_c", "link_id": "t3_abc"}]
            def close(self):
                pass

        monkeypatch.setattr(main_mod, "_reddit_scan", FakeScraper)
        monkeypatch.setattr(main_mod, "scan_ticker", lambda st, t, e: None)
        monkeypatch.setattr(
            main_mod, "run_options_scanners",
            lambda ticker, engine, benchmark, skip_gex: (
                [f"  {ticker}: line"], {"gex": _FakeScan("gex")},
            ),
        )
        monkeypatch.setattr(main_mod, "_youtube_scan", lambda t: None)
        monkeypatch.setattr(main_mod.time, "sleep", lambda s: None)

        alerts, cycle_raw = main_mod.scan_trending(st, engine, skip_reddit=False)

        assert alerts == []
        # scanner_raw['reddit'] should be attached
        assert "reddit" in cycle_raw["AAPL"]
        # Verify the raw result structure
        assert "ticker" in cycle_raw["AAPL"]["reddit"]
        assert cycle_raw["AAPL"]["reddit"]["ticker"] == "AAPL"
        # engine.record_narrative should have been called by run_reddit_scanner
        assert engine.record_narrative.call_count >= 1


def test_run_reddit_scanner_format_line():
    """Test format_reddit_line output."""
    result = main_mod.format_reddit_line({
        "ticker": "AAPL",
        "post_count": 5,
        "bullish_pct": 0.6,
        "bearish_pct": 0.4,
    })
    assert result is not None
    assert "AAPL" in result
    assert "5" in result
    assert "60%" in result
    assert "40%" in result


def test_run_reddit_scanner_none_on_empty():
    """Test format_reddit_line returns None on empty/None input."""
    assert main_mod.format_reddit_line(None) is None
    assert main_mod.format_reddit_line({}) is None
