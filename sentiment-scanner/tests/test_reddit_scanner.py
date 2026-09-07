"""Tests for scanner.reddit - offline fixture tests (no network)."""

from __future__ import annotations

import sys
import urllib.error
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


# ── Arctic Shift backend fixtures ─────────────────────────────────────────────
# Mock response bodies for Arctic Shift API endpoints
_ARCTIC_POSTS_RESPONSE = """{
    \"data\": [
        {
            \"id\": \"post123\",
            \"title\": \"$AAPL is going to the moon!\",
            \"score\": 450,
            \"author\": \"bullishinvestor\",
            \"created_utc\": 1725612000,
            \"permalink\": \"/r/wallstreetbets/comments/post123/\",
            \"selftext\": \"Apple is the best company ever. $AAPL will 10x!\"
        },
        {
            \"id\": \"post456\",
            \"title\": \"$TSLA is overvalued\",
            \"score\": -120,
            \"author\": \"bearishtrader\",
            \"created_utc\": 1725615600,
            \"permalink\": \"/r/wallstreetbets/comments/post456/\",
            \"selftext\": \"Tesla has too much competition.\"
        }
    ]
}"""

_ARCTIC_COMMENTS_RESPONSE = """{
    \"data\": [
        {
            \"body\": \"Great analysis!\",
            \"score\": 25,
            \"author\": \"commenter1\",
            \"link_id\": \"t3_post123\"
        },
        {
            \"body\": \"I disagree with your thesis.\",
            \"score\": -5,
            \"author\": \"commenter2\",
            \"link_id\": \"t3_post123\"
        }
    ]
}"""

_ARCTIC_EMPTY_RESPONSE = """{\"data\": []}"""


class TestArcticShiftBackend:
    """Test Arctic Shift API integration with mocked HTTP responses."""

    def test_get_hot_posts_arctic_returns_posts_with_correct_fields(self):
        """get_hot_posts_arctic should return posts with id, title, author, score, etc."""
        from scanner.reddit import RedditScraper
        
        scraper = RedditScraper()
        
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.__enter__.return_value = mock_response
            mock_response.read.return_value = _ARCTIC_POSTS_RESPONSE.encode()
            mock_response.headers = {}
            mock_urlopen.return_value = mock_response
            
            result = scraper.get_hot_posts_arctic("wallstreetbets", limit=25)
            
            assert len(result) == 2
            assert result[0]["id"] == "post123"
            assert result[0]["title"] == "$AAPL is going to the moon!"
            assert result[0]["score"] == 450
            assert result[0]["author"] == "bullishinvestor"
            assert result[0]["created_utc"] == 1725612000
            assert result[0]["permalink"] == "/r/wallstreetbets/comments/post123/"
            assert result[0]["selftext"] == "Apple is the best company ever. $AAPL will 10x!"

    def test_search_posts_arctic_with_query(self):
        """search_posts_arctic should call the API with query parameter."""
        from scanner.reddit import RedditScraper
        
        scraper = RedditScraper()
        
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.__enter__.return_value = mock_response
            mock_response.read.return_value = _ARCTIC_POSTS_RESPONSE.encode()
            mock_response.headers = {}
            mock_urlopen.return_value = mock_response
            
            result = scraper.search_posts_arctic("wallstreetbets", "AAPL", limit=10)
            
            assert len(result) == 2
            # Verify the URL was called with query parameter
            call_args = mock_urlopen.call_args
            url = call_args[0][0].full_url if hasattr(call_args[0][0], 'full_url') else str(call_args[0][0])
            assert "query=AAPL" in url

    def test_get_recent_comments_returns_comments(self):
        """get_recent_comments should return comments with body, score, author, link_id."""
        from scanner.reddit import RedditScraper
        
        scraper = RedditScraper()
        
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.__enter__.return_value = mock_response
            mock_response.read.return_value = _ARCTIC_COMMENTS_RESPONSE.encode()
            mock_response.headers = {}
            mock_urlopen.return_value = mock_response
            
            result = scraper.get_recent_comments("wallstreetbets", limit=50)
            
            assert len(result) == 2
            assert result[0]["body"] == "Great analysis!"
            assert result[0]["score"] == 25
            assert result[0]["author"] == "commenter1"
            assert result[0]["link_id"] == "t3_post123"

    def test_arctic_returns_empty_on_error(self):
        """Arctic methods should return [] on any exception (no raises)."""
        from scanner.reddit import RedditScraper
        
        scraper = RedditScraper()
        
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = Exception("Network error")
            
            assert scraper.get_hot_posts_arctic("wallstreetbets") == []
            assert scraper.search_posts_arctic("wallstreetbets", "AAPL") == []
            assert scraper.get_recent_comments("wallstreetbets") == []

    def test_arctic_uses_correct_user_agent(self):
        """Arctic requests should use the hermes-agent User-Agent header."""
        from scanner.reddit import RedditScraper
        
        scraper = RedditScraper()
        
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.__enter__.return_value = mock_response
            mock_response.read.return_value = _ARCTIC_EMPTY_RESPONSE.encode()
            mock_response.headers = {}
            mock_urlopen.return_value = mock_response
            
            scraper.get_hot_posts_arctic("wallstreetbets")
            
            call_args = mock_urlopen.call_args
            request = call_args[0][0]
            # urllib Request.headers is MIMEHeaders (case-insensitive keys)
            # The key is 'User-agent' (lowercase 'a') due to urllib's normalization
            ua = request.headers.get("User-agent") or request.headers.get("User-Agent")
            assert ua == "hermes-agent/1.0 (findev sentiment-scanner)"

    def test_arctic_15_second_timeout(self):
        """Arctic requests should use 15 second timeout."""
        from scanner.reddit import RedditScraper
        
        scraper = RedditScraper()
        
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.__enter__.return_value = mock_response
            mock_response.read.return_value = _ARCTIC_EMPTY_RESPONSE.encode()
            mock_response.headers = {}
            mock_urlopen.return_value = mock_response
            
            scraper.get_hot_posts_arctic("wallstreetbets")
            
            call_args = mock_urlopen.call_args
            assert call_args[1]["timeout"] == 15

    def test_arctic_fallback_on_fail(self):
        """scan_all should try Arctic first, then fall back to Atom on failure."""
        from scanner.reddit import RedditScraper
        
        scraper = RedditScraper()
        
        # First call fails (Arctic raises), second call succeeds (Atom)
        with patch("urllib.request.urlopen") as mock_urlopen:
            # First call to Arctic fails
            arctic_call_count = [0]
            
            def side_effect(req, timeout=None):
                arctic_call_count[0] += 1
                if arctic_call_count[0] == 1:
                    # First call is to Arctic, simulate failure
                    raise urllib.error.HTTPError(req.full_url, 500, "Internal Server Error", {}, None)
                else:
                    # Second call is to Atom, return success
                    mock_resp = MagicMock()
                    mock_resp.__enter__.return_value = mock_resp
                    mock_resp.read.return_value = _MINIMAL_ATOM_FIXTURE.encode()
                    mock_resp.headers = {}
                    return mock_resp
            
            mock_urlopen.side_effect = side_effect
            
            result = scraper.scan_all()
            
            # Should have returned Atom data
            assert "wallstreetbets" in result
            assert len(result["wallstreetbets"]) == 3
            assert scraper.backend == "atom"

    def test_arctic_uses_backend_flag(self):
        """scan_all should set self.backend to 'arctic' on success."""
        from scanner.reddit import RedditScraper
        
        scraper = RedditScraper()
        
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.__enter__.return_value = mock_response
            mock_response.read.return_value = _ARCTIC_POSTS_RESPONSE.encode()
            mock_response.headers = {}
            mock_urlopen.return_value = mock_response
            
            result = scraper.scan_all()
            
            assert scraper.backend == "arctic"
            assert len(result["wallstreetbets"]) == 2

