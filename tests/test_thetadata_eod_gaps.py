"""Regression: one empty 28-day window must not discard the whole history.

`hist_stock_eod` pages a long lookback into <=28-day chunks. A chunk the
vendor has no data for comes back as a null payload, and `_parse_rows` turns
that into `TypeError("v2 payload is None")` -- which used to propagate out of
the paging loop and throw away every chunk already fetched.

Confirmed live on SPCX (2026-09-12): 20260415-20260611 is an empty window in
the vendor's coverage while the other 24 chunks of the 2-year lookback hold
~450 usable rows. The whole pull failed, so
`Vol_Suite/correlation_engine.fetch_price_history` reported "Could not fetch
price history for any of ['SPCX']" and GARCH, realized vol and the
correlation matrix all treated a held position as having no history at all.
"""

import pytest

from shared.thetadata import ThetaDataController


class _GappyController(ThetaDataController):
    """Serves rows for every window except the ones in `empty`."""

    def __init__(self, empty_starts):  # test double, no config loading
        self.empty_starts = set(empty_starts)
        self.requested = []

    def _get(self, path, params=None):
        self.requested.append(params["start_date"])
        payload = (
            None
            if params["start_date"] in self.empty_starts
            else [{"date": int(params["start_date"]), "close": 10.0}]
        )

        class _R:
            status_code = 200

            def raise_for_status(self_inner):
                pass

            def json(self_inner):
                if payload is None:
                    raise TypeError("v2 payload is None")
                return payload

        return _R()


@pytest.mark.unit
def test_empty_window_does_not_discard_the_other_chunks(capsys):
    td = _GappyController(empty_starts={"20260130"})
    rows = td.hist_stock_eod("SPCX", "20260101", "20260401")

    # Four chunks requested, one empty -> three rows, not zero and not a raise.
    assert len(td.requested) == 4
    assert len(rows) == 3
    assert all(int(r["date"]) != 20260130 for r in rows)

    # The hole is announced, never papered over.
    out = capsys.readouterr().out
    assert "20260130" in out
    assert "no data for 1 window" in out


@pytest.mark.unit
def test_every_window_empty_still_returns_nothing():
    """Callers fail closed on an empty list; that path must be preserved."""
    starts = {"20260101", "20260130", "20260228", "20260329"}
    td = _GappyController(empty_starts=starts)
    assert td.hist_stock_eod("SPCX", "20260101", "20260401") == []


@pytest.mark.unit
def test_a_real_parse_failure_still_raises():
    """Only the known null-payload TypeError is a data gap."""

    class _BrokenController(ThetaDataController):
        def __init__(self):  # test double, no config loading
            pass

        def _get(self, path, params=None):
            class _R:
                status_code = 200

                def raise_for_status(self_inner):
                    pass

                def json(self_inner):
                    raise TypeError("unhashable type: 'dict'")

            return _R()

    with pytest.raises(TypeError, match="unhashable"):
        _BrokenController().hist_stock_eod("SPY", "20260101", "20260131")
