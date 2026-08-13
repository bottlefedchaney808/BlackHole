"""
End-to-end wiring smoke test for volatility_suite.py's two run modes.

This targets the FIX_PLAN_20260725.md follow-up: "mode two needs to run
just like mode 1." run_unified_flow and run_focus_workflow now both call a
shared _run_core_analysis function instead of run_unified_flow writing a
context and running nothing. A refactor at this scale (moving ~250 lines
into a shared function, threading new parameters through two call sites)
compiles fine even with a typo'd kwarg or a stale local-variable reference
-- py_compile only catches syntax errors. This test runs both modes
end-to-end with every network-facing module and stdin stubbed out, and
asserts each pipeline step actually got invoked with the basket/ticker it
should have used.
"""
import builtins
from typing import List

import pytest

import volatility_suite as vsuite


class ScriptedInput:
    """Feeds a fixed sequence of answers to input(), like a scripted user.
    Returns "" (accept default) past the end of the script instead of
    raising, so a missed/extra prompt fails on an assertion downstream
    rather than on a confusing StopIteration."""

    def __init__(self, answers: List[str]):
        self._answers = iter(answers)

    def __call__(self, prompt: str = "") -> str:
        return next(self._answers, "")


class FakeTD:
    def close(self):
        pass


def _install_common_stubs(monkeypatch, calls: dict):
    """Stub every module _run_core_analysis reaches into, recording what
    basket/ticker/model each one was actually called with. These are all
    imported LOCALLY inside volatility_suite.py's functions (`import x as
    y`), which re-resolves the same module object from sys.modules each
    call -- so patching the attribute on the real module (not on
    `vsuite.x`) is what actually takes effect here.
    """
    import thetadata_client
    monkeypatch.setattr(thetadata_client, "ThetaDataController", lambda: FakeTD())

    import expiry_selector
    monkeypatch.setattr(expiry_selector, "choose_expiry_interactive",
                         lambda td, ticker: ("20261218", 0.4))

    import variance_swap_screener as vss

    def fake_run_variance_screener(tickers, target_years, output_dir=None):
        calls["group_screener_tickers"] = list(tickers)
        return [], f"Screened {len(tickers)} tickers"

    def fake_screen_ticker(ticker, target_years, expiration=None):
        calls.setdefault("screen_ticker_calls", []).append(ticker)
        return None

    monkeypatch.setattr(vss, "run_variance_screener", fake_run_variance_screener)
    monkeypatch.setattr(vss, "screen_ticker", fake_screen_ticker)

    import correlation_engine as ce

    class FakeBasketStats:
        individual_betas = {}
        dispersion_score = 0.4

    def fake_run_correlation_engine(tickers, weights=None, market="SPY", period="2y", output_dir=None, save_csv=True):
        calls["correlation_tickers"] = list(tickers)
        calls["correlation_weights"] = list(weights) if weights is not None else None
        calls["correlation_market"] = market
        return [], "Correlation completed", FakeBasketStats()

    monkeypatch.setattr(ce, "run_correlation_engine", fake_run_correlation_engine)

    import variance_swap_live as vsl

    def fake_run_variance_swap_live(ticker, target_years, output_dir=None, expiration=None):
        calls.setdefault("variance_swap_tickers", []).append(ticker)
        return [], f"{ticker} variance swap", {"fair_variance_swap_strike_vol_pct": 30.0}

    monkeypatch.setattr(vsl, "run_variance_swap_live", fake_run_variance_swap_live)

    import garch_analysis as ga
    monkeypatch.setattr(ga, "run_garch_module", lambda ticker, output_dir=None: ([], "GARCH done", 0.31))

    import dealer_positioning as dp

    def fake_run_dealer_positioning(ticker, target_years, output_dir=None, save_csv=True, expiration=None, sign_model=None):
        calls["dealer_positioning_sign_model"] = sign_model
        return [], "Dealer positioning done", None

    monkeypatch.setattr(dp, "run_dealer_positioning", fake_run_dealer_positioning)

    import options_chain_scanner as ocs

    class FakeScanResult:
        verdict = "OK"

    def fake_run_chain_scanner(ticker, target_years, expiration=None, output_dir=None,
                                dealer_result=None):
        calls["chain_scanner_called"] = True
        return [], "Chain scan done", FakeScanResult()

    monkeypatch.setattr(ocs, "run_chain_scanner", fake_run_chain_scanner)


def _fake_pack_ctx():
    tickers_rows = [
        {"symbol": "TSLA", "rank": 1, "cns": 60, "confidence": 0.6},
        {"symbol": "INTC", "rank": 2, "cns": 60, "confidence": 0.6},
        {"symbol": "SPCX", "rank": 3, "cns": 60, "confidence": 0.6},
    ]
    return {
        "focus_ticker": "TSLA",
        "pack": {
            "group_id": "cns-threshold-alerts-test",
            "tickers": tickers_rows,
            "json_path": "/tmp/fake_pack.json",
        },
        "manifest_path": "/tmp/fake_manifest.json",
    }


def _assert_full_pipeline_ran_on_the_pack_basket(calls: dict):
    # The pack's tickers, not an index-derived basket, drove both the group
    # screener and the correlation engine -- FIX_PLAN_20260725.md issue 1.
    assert calls["group_screener_tickers"] == ["TSLA", "INTC", "SPCX"]
    assert calls["correlation_tickers"] == ["TSLA", "INTC", "SPCX"]
    assert calls["correlation_market"] == "SPY"  # benchmark only, not basket source

    # Both replication legs ran (index benchmark + focus ticker).
    assert calls["variance_swap_tickers"] == ["SPY", "TSLA"]

    # The sign-model prompt was actually threaded through -- both modes used
    # to risk silently defaulting to v1 (oi_heuristic) if this got dropped.
    assert calls["dealer_positioning_sign_model"] == "vol_surface_replication"

    # Options chain scanner ran because the script said "y".
    assert calls["chain_scanner_called"] is True

    # TSLA is IN the group screener basket and the group screener ran, so
    # the single-ticker recheck must be skipped (FIX_PLAN issue 2) --
    # screen_ticker must never have been called.
    assert "screen_ticker_calls" not in calls


@pytest.mark.unit
def test_unified_flow_runs_the_same_pipeline_as_focus_workflow(monkeypatch, tmp_path):
    """This is the exact bug reported: mode 2 with a pack basket used to
    write a suite_context.json and run nothing else -- 'child_suites_requested=0,
    new_output_files=0' with an otherwise-empty output folder. It must now
    run the full analysis pipeline just like mode 1."""
    calls: dict = {}
    _install_common_stubs(monkeypatch, calls)
    monkeypatch.setattr(vsuite, "_load_ticker_pack_interactive", lambda: _fake_pack_ctx())
    monkeypatch.setattr(vsuite, "timestamped_output_dir", lambda base="outputs": str(tmp_path))
    monkeypatch.setattr(vsuite, "compose_pdf_report", lambda path, sections: path)

    answers = ScriptedInput([
        "2",     # input mode: highlighted ticker pack
        "3",     # sign model: vol_surface_replication
        "y",     # run options chain scanner step
        "call",  # option type (for the suite_context handoff)
        "",      # strike (keep null)
        "n",     # run Options_Suite
        "n",     # run VaR_Tools_Simulations
        "y",     # compile PDF
        "y",     # run group screener on the full highlighted pack
    ])
    monkeypatch.setattr(builtins, "input", answers)

    vsuite.run_unified_flow()

    _assert_full_pipeline_ran_on_the_pack_basket(calls)


@pytest.mark.unit
def test_focus_workflow_reaches_the_same_pipeline_calls(monkeypatch, tmp_path):
    """Mirrors the unified-flow test with the same pack/answers so the two
    entry points are shown to converge on the identical set of pipeline
    calls, not just each pass independently."""
    calls: dict = {}
    _install_common_stubs(monkeypatch, calls)
    monkeypatch.setattr(vsuite, "_load_ticker_pack_interactive", lambda: _fake_pack_ctx())
    monkeypatch.setattr(vsuite, "timestamped_output_dir", lambda base="outputs": str(tmp_path))
    monkeypatch.setattr(vsuite, "compose_pdf_report", lambda path, sections: path)

    answers = ScriptedInput([
        "2",     # input mode: highlighted ticker pack
        "3",     # sign model: vol_surface_replication
        "y",     # run options chain scanner step
        "y",     # compile outputs into single PDF
        "y",     # run group screener on the full highlighted pack
    ])
    monkeypatch.setattr(builtins, "input", answers)

    vsuite.run_focus_workflow()

    _assert_full_pipeline_ran_on_the_pack_basket(calls)


def _core_analysis_kwargs(out_root: str) -> dict:
    """The minimum _run_core_analysis call that reaches the GARCH step with
    every network-facing module stubbed out."""
    return {
        "ticker": "TSLA",
        "pack_ctx": None,
        "group_tickers": [],
        "use_pack_basket": False,
        "tickers": ["TSLA", "INTC"],
        "weights": [0.5, 0.5],
        "chosen_index": "SPY",
        "target_years": 0.4,
        "expiration": "20261218",
        "sign_model": "vol_surface_replication",
        "run_options_chain": False,
        "out_root": out_root,
        "run_group_screener": False,
    }


@pytest.mark.unit
def test_garch_failure_is_recorded_in_artifacts_errors(monkeypatch, tmp_path):
    """run_garch_module swallows a failing fit on purpose (so a dead GARCH
    does not cost the caller its dealer-positioning run), which means the
    CALLER -- not an exception -- has to leave the machine-readable trace.
    Without this check vol_result.json reported a clean run after a blown fit."""
    import garch_analysis as ga
    real_run_garch_module = ga.run_garch_module

    calls: dict = {}
    _install_common_stubs(monkeypatch, calls)
    # _install_common_stubs replaces run_garch_module with a canned success;
    # put the real wrapper back so its internal failure handling is exercised.
    monkeypatch.setattr(ga, "run_garch_module", real_run_garch_module)

    def _boom(ticker, start=None, end=None):
        raise RuntimeError("fit did not converge")

    monkeypatch.setattr(ga, "run_garch_analysis", _boom)
    monkeypatch.setenv("VS_OUTPUT_DIR", str(tmp_path))

    _produced, _sections, artifacts = vsuite._run_core_analysis(
        **_core_analysis_kwargs(str(tmp_path)))

    assert artifacts["garch_ran"] is False
    assert artifacts["garch_conditional_vol"] is None
    garch_errors = [e for e in artifacts["errors"] if e["step"] == "garch"]
    assert len(garch_errors) == 1
    assert "fit did not converge" in garch_errors[0]["error"]


@pytest.mark.unit
def test_garch_success_with_no_conditional_vol_is_not_an_error(monkeypatch, tmp_path):
    """A converged fit whose conditional-volatility series is empty also
    returns None for the third element -- that must NOT read as a failure,
    which is why the caller keys off .error rather than off the None."""
    calls: dict = {}
    _install_common_stubs(monkeypatch, calls)

    import garch_analysis as ga
    monkeypatch.setattr(
        ga, "run_garch_module",
        lambda ticker, output_dir=None: ga.GarchModuleResult([], "GARCH done", None))
    monkeypatch.setenv("VS_OUTPUT_DIR", str(tmp_path))

    _produced, _sections, artifacts = vsuite._run_core_analysis(
        **_core_analysis_kwargs(str(tmp_path)))

    assert artifacts["garch_ran"] is True
    assert artifacts["garch_conditional_vol"] is None
    assert [e for e in artifacts["errors"] if e["step"] == "garch"] == []
