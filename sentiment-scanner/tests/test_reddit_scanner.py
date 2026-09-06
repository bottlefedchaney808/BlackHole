"""Tests for scanner.reddit - offline fixture tests (no network)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
from xml.etree import ElementTree as ET

import pytest

# Add sentiment-scanner to path for imports
_SENTIMENT_ROOT = Path(__file__).resolve().parent.parent
if str(_SENTIMENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SENTIMENT_ROOT))

# Minimal Atom XML fixture with 3 entries (per task spec)
# Entry 1: $AAPL bull post
# Entry 2: $TSLA bear post
# Entry 3: no ticker
_MINIMAL_ATOM_FIXTURE = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
    <entry>
        <title>$AAPL is going to the moon! Buy now!</title>
        <author><name>bullishinvestor</name></author>
        <link href="https://www.reddit.com/r/wallstreetbets/comments/aapl123/"/>
        <updated>2026-09-06T10:00:00Z</updated>
        <content type="html">Apple is the best company ever. $AAPL will 10x!</content>
    </entry>
    <entry>
        <title>$TSLA is overvalued. Short it now.</title>
        <author><name>bearishtrader</name></author>
        <link href="https://www.reddit.com/r/wallstreetbets/comments/tsla456/"/>
        <updated>2026-09-06T11:00:00Z</updated>
        <content type="html">Tesla has too much competition. $TSLA will drop 50%.</content>
    </entry>
    <entry>
        <title>General market discussion</title>
        <author><name>neutraluser</name></author>
        <link href="https://www.reddit.com/r/wallstreetbets/comments/gen789/"/>
        <updated>2026-09-06T12:00:00Z</updated>
        <content type="html">What do you all think about the market today?</content>
    </entry>
</feed>"""


class TestParseAtomFeed:
    """Test Atom feed parsing with offline fixtures."""

    def test_parse_extracts_title_author_url_text(self):
        """Parse should extract title, author, url, and body correctly."""
        from scanner.reddit import _parse_atom_feed

        data = _MINIMAL_ATOM_FIXTURE.encode()
        entries = _parse_atom_feed(data)

        assert len(entries) == 3

        # First entry: $AAPL bull
        assert entries[0]["title"] == "$AAPL is going to the moon! Buy now!"
        assert entries[0]["author"] == "bullishinvestor"
        assert entries[0]["url"] == "https://www.reddit.com/r/wallstreetbets/comments/aapl123/"
        assert "Apple is the best company ever" in entries[0]["body"]

        # Second entry: $TSLA bear
        assert entries[1]["title"] == "$TSLA is overvalued. Short it now."
        assert entries[1]["author"] == "bearishtrader"
        assert entries[1]["url"] == "https://www.reddit.com/r/wallstreetbets/comments/tsla456/"
        assert "Tesla has too much competition" in entries[1]["body"]

        # Third entry: no ticker
        assert entries[2]["title"] == "General market discussion"
        assert entries[2]["author"] == "neutraluser"
        assert entries[2]["url"] == "https://www.reddit.com/r/wallstreetbets/comments/gen789/"

    def test_cashtag_extraction_respects_blacklist(self):
        """Cashtag extraction should respect TICKER_BLACKLIST."""
        from scanner.reddit import _parse_atom_feed, extract_cashtags

        data = _MINIMAL_ATOM_FIXTURE.encode()
        entries = _parse_atom_feed(data)

        # First entry should have AAPL
        assert "AAPL" in entries[0]["cashtags"]

        # Second entry should have TSLA
        assert "TSLA" in entries[1]["cashtags"]

        # Third entry should have no cashtags (no tickers in text)
        assert entries[2]["cashtags"] == []

    def test_blacklist_words_filtered(self):
        """Blacklist words like $AI and $ALL should be filtered out."""
        from scanner.reddit import extract_cashtags

        # Test with blacklist words - "F" is NOT in blacklist (Ford's ticker)
        body = "I love $AI and $ALL and $GO and $NOW"
        ticks = extract_cashtags(body)
        assert len(ticks) == 0

        # Test with non-blacklisted tickers including "F" (Ford)
        body = "I love $AAPL and $TSLA and $F"
        ticks = extract_cashtags(body)
        assert set(ticks) == {"AAPL", "TSLA", "F"}


class TestRateLimitRetry:
    """Test 429-then-success retry path via monkeypatched urlopen."""

    def test_429_then_success_retries_and_returns_data(self):
        """Should retry once on 429, then return data on success."""
        from scanner.reddit import RedditScraper
        import urllib.error

        scraper = RedditScraper()

        # First call returns 429, second call returns success
        mock_response_success = MagicMock()
        mock_response_success.__enter__.return_value = mock_response_success
        mock_response_success.read.return_value = _MINIMAL_ATOM_FIXTURE.encode()
        mock_response_success.headers = {"retry-after": "1"}

        call_count = [0]

        def mock_urlopen(req, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                # Simulate 429 error - must be HTTPError for the retry logic to work
                raise urllib.error.HTTPError(
                    req.full_url, 429, "Too Many Requests", {}, None
                )
            return mock_response_success

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            result = scraper.get_hot_posts("wallstreetbets", limit=10)

        # The _get function is called twice:
        # 1. First call with 429 triggers retry (sleeps 61s)
        # 2. Second call succeeds
        assert call_count[0] == 2
        # Should have returned data after retry
        assert len(result) == 3
        assert result[0]["title"] == "$AAPL is going to the moon! Buy now!"

    def test_exception_path_returns_empty_list(self):
        """Should return [] on any exception (not just HTTPError)."""
        from scanner.reddit import RedditScraper

        scraper = RedditScraper()

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = RuntimeError("Network error")

            result = scraper.get_hot_posts("wallstreetbets", limit=10)
            assert result == []


class TestTickerExtraction:
    """Test cashtag extraction with TICKER_BLACKLIST."""

    def test_extract_cashtags_simple(self):
        """Should extract cashtags from text."""
        from scanner.reddit import extract_cashtags

        body = "I love $AAPL and $TSLA but not $AI and $ALL"
        ticks = extract_cashtags(body)
        assert "AAPL" in ticks
        assert "TSLA" in ticks
        assert "AI" not in ticks
        assert "ALL" not in ticks

    def test_extract_cashtags_blacklist(self):
        """Should filter out blacklist words."""
        from scanner.reddit import extract_cashtags

        body = "This is $GO and $NOW and $AI and $ALL"
        ticks = extract_cashtags(body)
        assert len(ticks) == 0

    def test_extract_cashtags_mixed(self):
        """Should handle mixed cashtags and blacklist."""
        from scanner.reddit import extract_cashtags

        body = "$AAPL is going to $moon but $I and $A are common"
        ticks = extract_cashtags(body)
        assert ticks == ["AAPL"]
