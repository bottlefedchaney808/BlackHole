"""Repro tests for four Market Signals bugs surfaced by UUUU run
20260817T085931Z492677:

1. Max Pain 404 on a pinned expiry with no OI snapshot must degrade to
   error='no_oi_for_pinned_expiry' instead of surfacing the raw ThetaData
   HTTP error / crashing the scanner loop.
2. Skew scanner's format_skew must not blow up when sabr_nu is None while
   sabr_rho is not.
3. `_thread_vol_stats_into_context` must read fair_vol_pct from
   vol_surface.focus.fair_vol_pct (one level deeper than vol_surface itself).
4. `build_context`'s var_horizon_days must default to 252, and accept
   focus['horizon_days'] as a fallback alias.
"""

import sys
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SENTIMENT_ROOT = os.path.join(ROOT, "sentiment-scanner")
if SENTIMENT_ROOT not in sys.path:
    sys.path.insert(0, SENTIMENT_ROOT)

import orchestrator  # noqa: E402


class _FakeTdNoOi:
    """Stands in for ThetaDataController: spot works, OI 404s."""

    def fetch_spot_price(self, ticker):
        return 100.0

    def option_bulk_oi(self, ticker, expiry):
        raise Exception(
            "Client error '404 Not Found' for url "
            f"'https://api.potatohedge.com/api/theta/bulk_snapshot/option/"
            f"open_interest/{ticker}/{expiry}'"
        )


def test_max_pain_degrades_on_404_instead_of_crashing(monkeypatch):
    from scanner import max_pain_scanner

    monkeypatch.setattr(max_pain_scanner, "get_td", lambda: _FakeTdNoOi())

    def _boom(*a, **kw):
        raise RuntimeError("stubbed out")

    monkeypatch.setattr(orchestrator, "_import_direction_suite", _boom)
    monkeypatch.setattr(orchestrator, "_import_var_engine_builders", _boom)

    def _fake_import_sentiment_scanners():
        from scanner.iv_rank_scanner import scan_iv_rank, format_iv_rank
        from scanner.skew_scanner import scan_skew, format_skew
        from scanner.unusual_oi_scanner import scan_unusual_oi, format_unusual_oi

        def _stub_iv_rank(ticker, **kw):
            raise RuntimeError("stubbed out")

        def _stub_skew(ticker, **kw):
            raise RuntimeError("stubbed out")

        def _stub_unusual_oi(ticker, **kw):
            raise RuntimeError("stubbed out")

        return (
            _stub_iv_rank, format_iv_rank,
            max_pain_scanner.scan_max_pain, max_pain_scanner.format_max_pain,
            _stub_skew, format_skew,
            _stub_unusual_oi, format_unusual_oi,
        )

    monkeypatch.setattr(orchestrator, "_import_sentiment_scanners", _fake_import_sentiment_scanners)

    context = {"focus": {"ticker": "UUUU", "expiration_date": "20261116"}}
    bundle = orchestrator.run_market_signals_stage("UUUU", context)

    max_pain = bundle["scanners"]["max_pain"]
    assert max_pain["error"] == "no_oi_for_pinned_expiry"


def test_format_skew_handles_sabr_nu_none():
    from scanner.skew_scanner import SkewScan, format_skew

    scan = SkewScan(
        ticker="UUUU", spot=10.0, forward=10.0, expiry="20261116", T_years=0.25,
        atm_iv_pct=50.0, put_skew_pts=2.0, num_strikes_total=10, num_otm_strikes=6,
        sabr_fit_success=True, sabr_alpha=0.5, sabr_rho=-0.3, sabr_nu=None,
        sabr_rmse=0.1, fitter="sabr", rich_strikes=[], cheap_strikes=[],
        skew_signal="FLAT", timestamp="2026-08-17T00:00:00Z",
    )

    line = format_skew(scan)  # must not raise
    assert "SABR" not in line


def test_thread_vol_stats_reads_nested_focus_fair_vol_pct():
    context = {"focus": {"ticker": "UUUU"}, "basket": {"tickers": ["UUUU"]}}
    vol_result = {
        "vol_surface": {
            "focus": {"fair_vol_pct": 41.7},
        },
    }
    orchestrator._thread_vol_stats_into_context(context, vol_result)
    assert context["focus"]["fair_vol_pct"] == pytest.approx(41.7)


def test_build_context_horizon_days_default_and_alias(monkeypatch):
    captured = {}

    class _FakeSuiteContext:
        def _normalize_expiration(self, expiration):
            digits = str(expiration).replace("-", "")
            return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"

        def build_suite_context(self, **kwargs):
            captured.update(kwargs)
            return {"focus": {}, "basket": {"tickers": []}}

        def validate_suite_context(self, context):
            return None

    monkeypatch.setattr(orchestrator, "_import_suite_context", lambda: _FakeSuiteContext())
    monkeypatch.setattr(orchestrator, "get_recent_swap_activity", lambda *a, **kw: [])

    orchestrator.build_context({
        "ticker": "UUUU", "expiration_date": "20261116",
        "basket_tickers": ["UUUU"],
    })
    assert captured["var_horizon_days"] == 252

    captured.clear()
    orchestrator.build_context({
        "ticker": "UUUU", "expiration_date": "20261116",
        "basket_tickers": ["UUUU"], "horizon_days": 10,
    })
    assert captured["var_horizon_days"] == 10
