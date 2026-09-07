"""Reddit Atom-feed scraper.

Fetches public Atom feeds (.rss) since www.reddit.com .json endpoints
and OAuth are 403-blocked from this box.

Features:
- Anonymous Atom feed access (~1 request/min/IP)
- Rate-limit handling (429): sleep until reset (max 70s), retry once
- Error handling (403/exceptions): return [] with warning to stderr
- Cashtag/ticker extraction with TICKER_BLACKLIST
"""

from __future__ import annotations

import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

from .stocktwits import TICKER_BLACKLIST  # Reuse existing blacklist


USER_AGENT = "hermes-agent/1.0 (reddit-reading skill)"
TIMEOUT = 25
ARCTIC_BASE = "https://arctic-shift.photon-reddit.com/api"
ARCTIC_TIMEOUT = 15
ARCTIC_USER_AGENT = "hermes-agent/1.0 (findev sentiment-scanner)"
ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}
WWW = "https://www.reddit.com"
MCP_AVAILABLE = True  # Now works without MCP via Atom feeds


# ── Helpers ───────────────────────────────────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(text: Optional[str]) -> str:
    """Strip HTML tags and normalize whitespace from text."""
    if not text:
        return ""
    text = _TAG_RE.sub(" ", html.unescape(text))
    text = re.sub(r"submitted by\s+/u/\S+|\[link\]|\[comments\]", " ", text)
    return _WS_RE.sub(" ", html.unescape(text)).strip()


def extract_cashtags(body: str) -> List[str]:
    """Extract cashtags from text, filtering against TICKER_BLACKLIST."""
    return [t for t in re.findall(r"\$([A-Z]{1,5})", body) if t not in TICKER_BLACKLIST]


def _parse_atom_feed(data: bytes) -> List[Dict]:
    """Parse Atom feed XML and extract entry data."""
    root = ET.fromstring(data)
    entries = []

    for entry in root.findall("a:entry", ATOM_NS):
        link = entry.find("a:link", ATOM_NS)
        title = strip_html(entry.findtext("a:title", default="", namespaces=ATOM_NS))
        author_raw = entry.findtext("a:author/a:name", default="", namespaces=ATOM_NS)
        author = (author_raw.replace("/u/", "") or None) if author_raw else None
        updated = entry.findtext("a:updated", default="", namespaces=ATOM_NS) or None
        url = link.get("href") if link is not None else None
        content = strip_html(entry.findtext("a:content", default="", namespaces=ATOM_NS))

        # Truncate content to ~500 chars
        if len(content) > 500:
            content = content[:500]

        entries.append({
            "title": title,
            "author": author,
            "url": url,
            "updated": updated,
            "body": content,
            "cashtags": extract_cashtags(title + " " + content),
        })

    return entries


def _reset_seconds(headers) -> int:
    """Extract reset seconds from rate-limit headers."""
    for key in ("x-ratelimit-reset", "retry-after"):
        val = headers.get(key) if headers else None
        if val:
            try:
                return max(1, min(int(float(val)) + 1, 70))
            except ValueError:
                pass
    return 61


def _get(url: str, headers: Optional[Dict] = None, retry_on_429: bool = True) -> tuple[bytes, Dict]:
    """Fetch URL with rate-limit handling.

    Returns (data, headers) or raises HTTPError.
    """
    hdrs = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, headers=hdrs)

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        if exc.code == 429 and retry_on_429:
            wait = _reset_seconds(exc.headers)
            print(f"reddit: 429 rate-limited, sleeping {wait}s until reset", file=sys.stderr)
            time.sleep(wait)
            return _get(url, headers, retry_on_429=False)
        raise


def _arctic_get(path: str, params: Optional[Dict] = None) -> Optional[Dict]:
    """Fetch from Arctic Shift API with 15s timeout and one retry.

    Returns parsed JSON dict on success, None on failure.
    """
    url = f"{ARCTIC_BASE}{path}"
    if params:
        query = urllib.parse.urlencode(params, doseq=True)
        url = f"{url}?{query}"

    hdrs = {"User-Agent": ARCTIC_USER_AGENT, "Accept": "application/json"}

    # First attempt
    try:
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=ARCTIC_TIMEOUT) as resp:
            data = resp.read()
            return json.loads(data.decode("utf-8"))
    except Exception:
        pass

    # One retry
    try:
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=ARCTIC_TIMEOUT) as resp:
            data = resp.read()
            return json.loads(data.decode("utf-8"))
    except Exception:
        return None


# ── RedditScraper class ───────────────────────────────────────────────────────

class RedditScraper:
    """Reddit scraper using public Atom feeds.

    Fetches posts from subreddit .rss endpoints with rate-limit handling.
    Never raises exceptions into caller - returns [] on errors.
    """

    def __init__(self):
        """Initialize scraper. Always available via Atom feeds."""
        self.available = True
        self.backend = None

    def get_hot_posts(self, subreddit: str = "wallstreetbets", limit: int = 25) -> List[Dict]:
        """Fetch hot posts from a subreddit.

        Args:
            subreddit: Reddit subreddit name (default: wallstreetbets)
            limit: Max number of posts to fetch (default: 25)

        Returns:
            List of post dicts with keys: title, author, url, updated, body, cashtags
            Returns [] on error (403 or other).
        """
        url = f"{WWW}/r/{subreddit}/hot/.rss"

        try:
            data, _ = _get(url)
            entries = _parse_atom_feed(data)
            return entries[:limit]
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                print(f"reddit: 403 Forbidden for {url}", file=sys.stderr)
            else:
                print(f"reddit: HTTP {exc.code} for {url}", file=sys.stderr)
            return []
        except Exception as exc:
            print(f"reddit: error fetching {url}: {exc}", file=sys.stderr)
            return []

    def get_hot_posts_arctic(self, subreddit: str = "wallstreetbets", limit: int = 25) -> List[Dict]:
        """Fetch hot posts from a subreddit via Arctic Shift API.

        Args:
            subreddit: Reddit subreddit name (default: wallstreetbets)
            limit: Max number of posts to fetch (default: 25)

        Returns:
            List of post dicts with keys: id, title, author, score, created_utc,
            permalink, selftext[:500]
            Returns [] on error.
        """
        params = {"subreddit": subreddit, "limit": limit}
        result = _arctic_get("/posts/search", params)

        if not result or "data" not in result:
            return []

        posts = []
        for item in result["data"]:
            body = item.get("selftext", "") or ""
            # Truncate body to 500 chars
            if len(body) > 500:
                body = body[:500]

            posts.append({
                "id": item.get("id"),
                "title": item.get("title"),
                "author": item.get("author"),
                "score": item.get("score"),
                "created_utc": item.get("created_utc"),
                "permalink": item.get("permalink"),
                "selftext": body,
            })

        return posts

    def search_posts_arctic(self, subreddit: str, query: str, limit: int = 25) -> List[Dict]:
        """Search posts in a subreddit via Arctic Shift API.

        Args:
            subreddit: Reddit subreddit name
            query: Search query string
            limit: Max number of posts to fetch (default: 25)

        Returns:
            List of post dicts with keys: id, title, author, score, created_utc,
            permalink, selftext[:500]
            Returns [] on error.
        """
        params = {"subreddit": subreddit, "query": query, "limit": limit}
        result = _arctic_get("/posts/search", params)

        if not result or "data" not in result:
            return []

        posts = []
        for item in result["data"]:
            body = item.get("selftext", "") or ""
            if len(body) > 500:
                body = body[:500]

            posts.append({
                "id": item.get("id"),
                "title": item.get("title"),
                "author": item.get("author"),
                "score": item.get("score"),
                "created_utc": item.get("created_utc"),
                "permalink": item.get("permalink"),
                "selftext": body,
            })

        return posts

    def get_recent_comments(self, subreddit: str = "wallstreetbets", limit: int = 50) -> List[Dict]:
        """Fetch recent comments from a subreddit via Arctic Shift API.

        Args:
            subreddit: Reddit subreddit name (default: wallstreetbets)
            limit: Max number of comments to fetch (default: 50)

        Returns:
            List of comment dicts with keys: body, score, author, link_id
            Returns [] on error.
        """
        params = {"subreddit": subreddit, "limit": limit, "sort": "desc"}
        result = _arctic_get("/comments/search", params)

        if not result or "data" not in result:
            return []

        comments = []
        for item in result["data"]:
            comments.append({
                "body": item.get("body"),
                "score": item.get("score"),
                "author": item.get("author"),
                "link_id": item.get("link_id"),
            })

        return comments

    def scan_all(self) -> Dict[str, List[Dict]]:
        """Fetch hot posts from all configured subreddits.

        Tries Arctic Shift first, falls back to Atom if Arctic fails.
        Sets self.backend to 'arctic' or 'atom' for logging.

        Returns:
            Dict mapping subreddit name to list of posts.
            Returns {} on error.
        """
        subreddits = ["wallstreetbets", "stocks", "investing", "options", "smallstreetbets"]
        result = {}

        for sub in subreddits:
            # Try Arctic first
            posts = self.get_hot_posts_arctic(subreddit=sub, limit=25)

            if posts:
                result[sub] = posts
                self.backend = "arctic"
            else:
                # Fall back to Atom
                posts = self.get_hot_posts(subreddit=sub, limit=25)
                if posts:
                    result[sub] = posts
                    self.backend = "atom"

        return result

    def close(self):
        """Cleanup (no-op for HTTP-based scraper)."""
        pass
