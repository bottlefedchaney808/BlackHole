"""Tests for the live earnings-calendar data source."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Dict
from unittest.mock import MagicMock, patch

import pytest

from scanner.earnings_calendar import (
    _extract_calendar_entries,
    fetch_earnings_calendar,
    format_earnings_digest,
    upcoming_earnings,
)
import scanner.earnings_calendar as calendar_mod


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    """Every test starts with a cold cache."""
    calendar_mod._cache["ts"] = 0.0
    calendar_mod._cache["data"] = {}


def _next_data_html(payload: dict) -> str:
    blob = json.dumps(payload)
    return (
        "<html><head>"
        f'<script id="__NEXT_DATA__" type="application/json">{blob}</script>'
        "</head><body></body></html>"
    )


def _mock_urlopen(html: str):
    resp = MagicMock()
    resp.read.return_value = html.encode("utf-8")
    return resp


class TestExtractCalendarEntries:
    def test_finds_symbol_and_date_keys(self) -> None:
        payload = {
            "props": {
                "pageProps": {
                    "data": [
                        {"symbol": "AAPL", "date": "2026-08-05"},
                        {"symbol": "MSFT", "date": "2026-08-06"},
                    ]
                }
            }
        }
        result = _extract_calendar_entries(payload)
        assert result == {"AAPL": "2026-08-05", "MSFT": "2026-08-06"}

    def test_alternate_key_names(self) -> None:
        payload = {"rows": [{"s": "NVDA", "reportDate": "2026-08-20T00:00:00Z"}]}
        result = _extract_calendar_entries(payload)
        assert result == {"NVDA": "2026-08-20"}

    def test_ignores_dicts_missing_either_key(self) -> None:
        payload = {"rows": [{"symbol": "AAPL"}, {"date": "2026-08-05"}]}
        assert _extract_calendar_entries(payload) == {}

    def test_ignores_malformed_dates(self) -> None:
        payload = {"rows": [{"symbol": "AAPL", "date": "not-a-date"}]}
        assert _extract_calendar_entries(payload) == {}

    def test_empty_payload_returns_empty(self) -> None:
        assert _extract_calendar_entries({}) == {}


class TestFetchEarningsCalendar:
    def test_parses_next_data_script(self) -> None:
        html = _next_data_html(
            {"rows": [{"symbol": "AAPL", "date": "2026-08-05"}]}
        )
        with patch(
            "scanner.earnings_calendar.urllib.request.urlopen",
            return_value=_mock_urlopen(html),
        ):
            result = fetch_earnings_calendar()
        assert result == {"AAPL": "2026-08-05"}

    def test_network_error_returns_empty_dict(self) -> None:
        with patch(
            "scanner.earnings_calendar.urllib.request.urlopen",
            side_effect=OSError("network down"),
        ):
            assert fetch_earnings_calendar() == {}

    def test_missing_next_data_script_returns_empty_dict(self) -> None:
        with patch(
            "scanner.earnings_calendar.urllib.request.urlopen",
            return_value=_mock_urlopen("<html><body>no data here</body></html>"),
        ):
            assert fetch_earnings_calendar() == {}

    def test_malformed_json_returns_empty_dict(self) -> None:
        html = (
            '<script id="__NEXT_DATA__" type="application/json">{not json'
            "</script>"
        )
        with patch(
            "scanner.earnings_calendar.urllib.request.urlopen",
            return_value=_mock_urlopen(html),
        ):
            assert fetch_earnings_calendar() == {}

    def test_result_is_cached_within_ttl(self) -> None:
        html = _next_data_html(
            {"rows": [{"symbol": "AAPL", "date": "2026-08-05"}]}
        )
        with patch(
            "scanner.earnings_calendar.urllib.request.urlopen",
            return_value=_mock_urlopen(html),
        ) as mock_urlopen:
            fetch_earnings_calendar()
            fetch_earnings_calendar()
        assert mock_urlopen.call_count == 1

    def test_cache_expires_after_ttl(self) -> None:
        html = _next_data_html(
            {"rows": [{"symbol": "AAPL", "date": "2026-08-05"}]}
        )
        with patch(
            "scanner.earnings_calendar.urllib.request.urlopen",
            return_value=_mock_urlopen(html),
        ) as mock_urlopen:
            fetch_earnings_calendar()
            calendar_mod._cache["ts"] -= calendar_mod.CACHE_TTL_SECONDS + 1
            fetch_earnings_calendar()
        assert mock_urlopen.call_count == 2


class TestUpcomingEarnings:
    def test_filters_to_window_and_merges_static_fallback(self) -> None:
        today = datetime.now(timezone.utc).date()
        in_window = (today + timedelta(days=3)).strftime("%Y-%m-%d")
        out_of_window = (today + timedelta(days=30)).strftime("%Y-%m-%d")
        with patch(
            "scanner.earnings_calendar.fetch_earnings_calendar",
            return_value={"AAPL": in_window, "MSFT": out_of_window},
        ):
            entries = upcoming_earnings(
                days=7, static_fallback={"GOOGL": in_window}
            )
        tickers = {t for t, _d in entries}
        assert tickers == {"AAPL", "GOOGL"}

    def test_live_calendar_overrides_static_fallback_for_same_ticker(self) -> None:
        today = datetime.now(timezone.utc).date()
        live_date = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        stale_static_date = (today - timedelta(days=100)).strftime("%Y-%m-%d")
        with patch(
            "scanner.earnings_calendar.fetch_earnings_calendar",
            return_value={"AAPL": live_date},
        ):
            entries = upcoming_earnings(
                days=7, static_fallback={"AAPL": stale_static_date}
            )
        assert entries == [("AAPL", live_date)]

    def test_sorted_by_date(self) -> None:
        today = datetime.now(timezone.utc).date()
        d1 = (today + timedelta(days=5)).strftime("%Y-%m-%d")
        d2 = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        with patch(
            "scanner.earnings_calendar.fetch_earnings_calendar",
            return_value={"LATE": d1, "EARLY": d2},
        ):
            entries = upcoming_earnings(days=7)
        assert entries == [("EARLY", d2), ("LATE", d1)]

    def test_no_data_returns_empty_list(self) -> None:
        with patch(
            "scanner.earnings_calendar.fetch_earnings_calendar",
            return_value={},
        ):
            assert upcoming_earnings(days=7) == []


class TestFormatEarningsDigest:
    def test_empty_entries(self) -> None:
        assert format_earnings_digest([], days=7) == (
            "  Upcoming Earnings (7d): none scheduled"
        )

    def test_with_entries(self) -> None:
        output = format_earnings_digest(
            [("AAPL", "2026-08-05"), ("MSFT", "2026-08-06")], days=7
        )
        assert "AAPL (2026-08-05)" in output
        assert "MSFT (2026-08-06)" in output
        assert output.startswith("  Upcoming Earnings (7d):")

    def test_live_defaults_to_no_tag(self) -> None:
        output = format_earnings_digest([("AAPL", "2026-08-05")], days=7)
        assert "[STATIC FALLBACK]" not in output

    def test_live_false_prepends_static_fallback_tag(self) -> None:
        output = format_earnings_digest(
            [("AAPL", "2026-08-05")], days=7, live=False,
        )
        assert "[STATIC FALLBACK]" in output
        assert "AAPL (2026-08-05)" in output

    def test_live_false_empty_entries_still_tagged(self) -> None:
        output = format_earnings_digest([], days=7, live=False)
        assert "[STATIC FALLBACK]" in output
        assert "none scheduled" in output
