"""test_scanners_context.py -- Plan B: the option-chain scanners consume
context-supplied GARCH/fair vol when present (IV Rank), and accept the
context vols without signature error (Max Pain)."""
from scanner import iv_rank_scanner, max_pain_scanner


class _FakeTD:
    def fetch_spot_price(self, ticker):
        return 100.0


def test_scan_iv_rank_prefers_context_garch_and_fair_vol(monkeypatch):
    # Force the internal GARCH fit to raise so the context value is the only
    # nonzero source. The spot fetch returns 100; no expiry/ATM -> atm_iv 0, but
    # garch/fair-vol must still come from the supplied kwargs.
    monkeypatch.setattr(iv_rank_scanner, "get_td", lambda: _FakeTD())
    monkeypatch.setattr(iv_rank_scanner.vsi.garch_analysis, "run_garch_analysis",
                        lambda tk: (_ for _ in ()).throw(RuntimeError("no fit")))
    scan = iv_rank_scanner.scan_iv_rank("SPY", garch_cond_vol_pct=27.0, fair_vol_pct=24.0)
    assert scan.garch_cond_vol_pct == 27.0
    assert scan.fair_vol_pct == 24.0


def test_scan_max_pain_accepts_context_vols(monkeypatch):
    monkeypatch.setattr(max_pain_scanner, "get_td", lambda: _FakeTD())
    # expiry given -> no nearest-expiry self-selection path; OI fetch not mocked
    # so it returns an error scan, but the call must not raise on the kwargs.
    scan = max_pain_scanner.scan_max_pain("SPY", "2026-10-16",
                                          garch_cond_vol_pct=27.0, fair_vol_pct=24.0)
    assert scan.error is not None or scan.expiry == "20261016"
