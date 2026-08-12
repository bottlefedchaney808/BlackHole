"""Tests for the Max Pain scanner's expiry selection.

The scanner normally self-selects a ~30DTE expiry.  When a caller (the
orchestrator's market-signals stage) already knows which expiry the run is
analyzing, it can pass it in and the self-selection must be skipped so the
scan describes the same expiry as the rest of the run.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

import scanner.max_pain_scanner as mp
import scanner.options_scanner_base as base


def _oi_rows():
    """Three strikes of call/put OI in ThetaData strike units (price * 1000)."""
    rows = []
    for strike in (140.0, 150.0, 160.0):
        rows.append({"strike": int(strike * 1000), "right": "C", "open_interest": 500})
        rows.append({"strike": int(strike * 1000), "right": "P", "open_interest": 400})
    return rows


@pytest.fixture
def fake_td(monkeypatch):
    td = MagicMock()
    td.fetch_spot_price.return_value = 150.0
    td.option_bulk_oi.return_value = _oi_rows()
    monkeypatch.setattr(mp, "get_td", lambda: td)
    return td


@pytest.fixture
def no_self_selection(monkeypatch):
    """Make any use of the nearest-expiry self-selection path an error."""

    class _Forbidden:
        def __getattr__(self, name):
            raise AssertionError(
                f"self-selection used VolSuiteImporter.{name} despite explicit expiry"
            )

    monkeypatch.setattr(base, "VolSuiteImporter", _Forbidden)


def test_explicit_expiry_skips_self_selection(fake_td, no_self_selection):
    scan = mp.scan_max_pain("AAPL", expiry="20261120")

    assert scan.error is None
    assert scan.expiry == "20261120"
    fake_td.option_bulk_oi.assert_called_once_with("AAPL", "20261120")


def test_explicit_expiry_accepts_dashed_form(fake_td, no_self_selection):
    scan = mp.scan_max_pain("AAPL", expiry="2026-11-20")

    assert scan.error is None
    assert scan.expiry == "20261120"
    fake_td.option_bulk_oi.assert_called_once_with("AAPL", "20261120")


def test_explicit_expiry_T_years_uses_calendar_days(fake_td, no_self_selection):
    target = datetime.now(timezone.utc).date() + timedelta(days=73)
    scan = mp.scan_max_pain("AAPL", expiry=target.strftime("%Y%m%d"))

    assert scan.error is None
    assert scan.T_years == pytest.approx(73 / 365.0)


def test_past_expiry_floors_T_years_at_zero(fake_td, no_self_selection):
    target = datetime.now(timezone.utc).date() - timedelta(days=10)
    scan = mp.scan_max_pain("AAPL", expiry=target.strftime("%Y%m%d"))

    assert scan.error is None
    assert scan.T_years == 0.0


def test_unparseable_expiry_returns_error_scan(fake_td, no_self_selection):
    scan = mp.scan_max_pain("AAPL", expiry="not-a-date")

    assert scan.error
    assert scan.expiry == ""
    fake_td.option_bulk_oi.assert_not_called()


def test_no_expiry_still_self_selects(fake_td, monkeypatch):
    calls = []

    class _Importer:
        @property
        def expiry_selector(self):
            module = MagicMock()

            def _nearest(td, ticker, target_years):
                calls.append((ticker, target_years))
                return "20260918", 0.083

            module.nearest_expiry = _nearest
            return module

    monkeypatch.setattr(base, "VolSuiteImporter", _Importer)

    scan = mp.scan_max_pain("AAPL")

    assert scan.error is None
    assert scan.expiry == "20260918"
    assert calls == [("AAPL", 0.083)]
