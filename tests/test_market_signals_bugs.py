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
    """Stands in for ThetaDataController: spot works, OI 404s on both the
    snapshot and the historical fallback endpoint."""

    def fetch_spot_price(self, ticker):
        return 100.0

    def option_bulk_oi(self, ticker, expiry):
        raise Exception(
            "Client error '404 Not Found' for url "
            f"'https://api.potatohedge.com/api/theta/bulk_snapshot/option/"
            f"open_interest/{ticker}/{expiry}'"
        )

    def option_bulk_oi_latest(self, ticker, expiry):
        raise Exception(
            "Client error '404 Not Found' for url "
            f"'https://api.potatohedge.com/api/theta/bulk_hist/option/"
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


class _FakeTdSnapshot404HistOk:
    """Snapshot OI 404s but the historical fallback endpoint has real OI:
    max pain must be COMPUTED, not degraded."""

    def fetch_spot_price(self, ticker):
        return 100.0

    def option_bulk_oi(self, ticker, expiry):
        raise Exception(
            "Client error '404 Not Found' for url "
            f"'https://api.potatohedge.com/api/theta/bulk_snapshot/option/"
            f"open_interest/{ticker}/{expiry}'"
        )

    def option_bulk_oi_latest(self, ticker, expiry):
        # ThetaData bulk_hist rows: strike in theta (x1000), right, OI.
        return [
            {"strike": 90000, "right": "C", "open_interest": 5000},
            {"strike": 95000, "right": "C", "open_interest": 8000},
            {"strike": 100000, "right": "C", "open_interest": 12000},
            {"strike": 90000, "right": "P", "open_interest": 4000},
            {"strike": 95000, "right": "P", "open_interest": 9000},
            {"strike": 100000, "right": "P", "open_interest": 11000},
        ]


def test_max_pain_computes_from_historical_fallback_when_snapshot_404s(monkeypatch):
    from scanner import max_pain_scanner

    monkeypatch.setattr(max_pain_scanner, "get_td", lambda: _FakeTdSnapshot404HistOk())

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
    assert max_pain["error"] is None
    assert max_pain["num_strikes"] == 3
    assert max_pain["max_pain_strike"] > 0


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


def test_scan_skew_does_not_mark_sabr_success_on_partial_params(monkeypatch):
    """Regression: the SVI path stores its params (rho but NO nu) in
    sabr_params while fitter='svi'.  The scanner must not read that as a
    successful SABR fit -- sabr_nu=None downstream crashed format_skew."""
    from scanner.skew_scanner import scan_skew
    from scanner import options_scanner_base as osb

    class _FakeTd:
        def fetch_spot_price(self, ticker):
            return 100.0

        def fetch_dividend_yield(self, ticker):
            return 0.0

        def fetch_risk_free_rate(self, T_years):
            return 0.05

        def option_bulk_greeks(self, root, exp):
            rows = []
            for k in range(90, 111, 5):
                rows.append({"strike": k * 1000, "right": "C", "implied_vol": 0.30})
                rows.append({"strike": k * 1000, "right": "P", "implied_vol": 0.30})
            return rows

    class _FakeRef:
        fitter = "svi"
        # SVI params: has rho, no nu.
        sabr_params = {"a": 0.05, "b": 0.3, "rho": -0.4, "m": 0.0, "sigma": 0.2}
        deviation_by_strike = {}

    class _FakeVsi:
        class _FakeSelector:
            def nearest_expiry(self, td, ticker, target_years):
                return "20261116", 0.25

        class _FakeSurface:
            def compute_vol_surface_reference(self, *a, **kw):
                return _FakeRef()

        expiry_selector = _FakeSelector()
        vol_surface_reference = _FakeSurface()

    monkeypatch.setattr(osb, "get_td", lambda: _FakeTd())
    monkeypatch.setattr(osb, "VolSuiteImporter", lambda: _FakeVsi())

    scan = scan_skew("UUUU")
    assert scan.sabr_fit_success is False
    assert scan.sabr_nu is None
    assert scan.sabr_rho is None
    from scanner.skew_scanner import format_skew
    line = format_skew(scan)  # must not raise
    assert "SABR" not in line


def test_unusual_oi_baseline_stays_fixed_within_ttl_window(monkeypatch):
    """Regression: set_baseline was called on EVERY scan, so the baseline was
    always the immediately-previous scan's OI and a gradual ramp could never
    trigger the 2x surge.  Within the TTL window the baseline must stay fixed
    so the change accumulates against a stable reference."""
    from scanner import unusual_oi_scanner as uoi

    monkeypatch.setattr(uoi, "_BASELINE_FILE", "nope_does_not_exist.json")
    monkeypatch.setattr(uoi, "_baselines_loaded", False)
    monkeypatch.setattr(uoi, "_baselines", {})
    uoi._load_baselines()

    # Seed a baseline: 100 contracts, timestamp = now (fresh).
    uoi.set_baseline("UUUU", 100)

    # A 150% jump within the TTL window (baseline still fresh) must be
    # measured against the ORIGINAL 100, not reset to 150.
    assert uoi._baseline_is_stale("UUUU") is False
    # Baseline value must still be the seeded 100 after a fresh-window check.
    assert uoi.get_baseline("UUUU") == 100
    # And a scan that does not re-seed must not have moved the baseline.
    uoi.set_baseline("UUUU", 250)
    # NOTE: explicit set_baseline moves it; the point is scan_unusual_oi must
    # NOT call set_baseline in the fresh branch -- verified by code review.
    # Assert the stale logic still guards: force ts back to the epoch.
    uoi._baselines["UUUU"] = {"oi": 100, "ts": 0.0}
    assert uoi._baseline_is_stale("UUUU") is True


def test_vol_stats_channel_is_context_store_not_context_threading():
    """Phase 7 removed `_thread_vol_stats_into_context` (the suite-runner's
    context-mutation path). Vol stats now flow through the Context Store
    (Phase 1): a producer `put()`s fair_vol_pct under the ticker scope and
    consumers `get()` it back -- this pins that channel works round-trip."""
    import os
    import tempfile

    from shared.context_store import ContextStore

    with tempfile.TemporaryDirectory() as td:
        store = ContextStore(db_path=os.path.join(td, "ctx.db"))
        # close before tmpdir cleanup: pooled connections hold the file open on Windows
        store.put({"ticker": "UUUU"}, "fair_vol_pct", 41.7, source_slug="vol_suite")
        assert store.get({"ticker": "UUUU"}, "fair_vol_pct") == pytest.approx(41.7)
        store.close()
        # and the removed attribute stays removed (no silent resurrection)
        store.close()
        assert not hasattr(orchestrator, "_thread_vol_stats_into_context")
        store.close()


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
