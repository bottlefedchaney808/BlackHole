"""Regression tests for the SPXW -> SPX price-root alias on history routes.

CLAUDE.md's known-open-gap note (fixed 2026-08-31): `_INDEX_PRICE_ROOT_ALIASES`
was only applied on the index-*price* routes (`_fetch_index_spot_price`), so
`hist_stock_eod("SPXW", ...)` paged an empty chain-root history and
`Vol_Suite/correlation_engine.fetch_price_history` (raw-ticker pass-through)
silently lost SPX realized-vol inputs. The alias now applies at the
stock-EOD route, the same source-level map the price routes use.
"""

import pytest

from shared.thetadata import ThetaDataController


class _RecordingController(ThetaDataController):
    """Capture the path actually requested, without touching the network."""

    def __init__(self):  # noqa: D107 - test double, no config loading
        pass

    def _get(self, path, params=None):
        self.last_path = path
        self.last_params = params

        class _R:
            status_code = 200

            def raise_for_status(self_inner):
                pass

            def json(self_inner):
                return []

        return _R()


@pytest.mark.unit
def test_hist_stock_eod_resolves_spxw_to_spx_at_source():
    td = _RecordingController()
    td.hist_stock_eod("SPXW", "20260101", "20260131")
    assert td.last_path == "/api/theta/hist/stock/eod/SPX"


@pytest.mark.unit
def test_hist_stock_eod_leaves_other_roots_untouched():
    td = _RecordingController()
    td.hist_stock_eod("SPY", "20260101", "20260131")
    assert td.last_path == "/api/theta/hist/stock/eod/SPY"


@pytest.mark.unit
def test_correlation_engine_passes_chain_root_and_still_gets_history(monkeypatch):
    """The correlation engine passes raw tickers through to hist_stock_eod;
    with the alias at the route, the SPXW chain root now resolves instead of
    paging an empty history."""
    import sys

    if "C:/Users/bottl/FinancialDevelopment/Vol_Suite" not in sys.path:
        sys.path.insert(0, "C:/Users/bottl/FinancialDevelopment/Vol_Suite")
    import Vol_Suite.correlation_engine as ce

    captured = {}

    class _FakeTD:
        def __init__(self):
            self.tickers_seen = []

        def hist_stock_eod(self, ticker, start_str, end_str):
            self.tickers_seen.append(ticker)
            return [
                {"created": f"2026-01-{i + 1:02d}T17:15:00", "close": 5000.0 + i}
                for i in range(10)
            ]

        def close(self):
            pass

    class _FakeClient:
        def __init__(self):
            self.td = _FakeTD()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    fake_td = _FakeTD()
    monkeypatch.setattr(ce, "ThetaDataController", lambda: fake_td)

    df = ce.fetch_price_history(["SPXW", "SPY"], period="1y")

    assert fake_td.tickers_seen == ["SPXW", "SPY"]  # raw pass-through unchanged
    assert "SPXW" in df.columns  # but history now resolves via the alias
    assert "SPY" in df.columns
