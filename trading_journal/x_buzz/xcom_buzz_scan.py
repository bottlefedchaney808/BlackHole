#!/usr/bin/env python3
"""
X.com Fintwit Buzz Scanner v1.0 — per spec t_e49c9d78

CLI: xcom_buzz_scan.py --window <morning|mid-day|power-hour> [--asof ISO] [--out <json>]

Sources: X API v2 (TWITTER_BEARER_TOKEN) → Nitter RSS fallback → exit 2 on hard-fail.
Exit 0 on partial (<20 posts); exit 2 only on auth/config hard-fail.
Rank key is buzz score B, not raw likes.
"""
import argparse
import json
import math
import os
import re
import sys
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Tuple

# ── Spec constants ──────────────────────────────────────────────────────────

SCHEMA_VERSION = "1.0"

WINDOWS = {
    "morning":    {"fire_et": "07:30", "lookback_hours": 8},   # prev 23:30 → 07:30
    "mid-day":    {"fire_et": "12:00", "lookback_hours": 4.5}, # 07:30 → 12:00
    "power-hour": {"fire_et": "15:15", "lookback_hours": 3.25},# 12:00 → 15:15
}

HASHTAGS = ["#fintwit", "#stocks", "#volatility", "#VIX", "#options", "#0DTE", "#gamma", "#SPX"]
CASHTAGS = ["$SPY", "$QQQ", "$$VIX", "$NVDA", "$SPX"]

CREDIBLE_HANDLES = [
    "unusual_whales", "spotgamma", "squeezemetrics", "zerohedge",
    "kobeissiletter", "deiced", "tastytrade", "cnbc", "business",
]

BULL_TOKENS = [
    "moon", "squeeze", "calls", "long", "breakout", "rip", "melt-up",
    "bid", "bullish", "btd", "green", "rally", "tendies", "call",
]
BEAR_TOKENS = [
    "puts", "put", "crash", "dump", "short", "breakdown", "selloff",
    "melt-down", "ask", "bearish", "red", "drill", "recession", "fade", "overbought",
]

AGE_FLOOR_MIN = 5.0

X_API_BASE = "https://api.twitter.com/2/tweets/search/recent"

NITTER_INSTANCES = [
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


# ── HTTP helpers ────────────────────────────────────────────────────────────

def _fetch(url: str, headers: Optional[Dict] = None, timeout: int = 15) -> Optional[str]:
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _fetch_json(url: str, headers: Optional[Dict] = None, timeout: int = 15) -> Optional[dict]:
    text = _fetch(url, headers=headers, timeout=timeout)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# ── Time-window math (ET-aware) ────────────────────────────────────────────

def _et_now() -> datetime:
    """Current time in US Eastern (DST-aware)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except ImportError:
        utc = datetime.now(timezone.utc)
        # Rough EDT/EST offset — DST-aware via zoneinfo is preferred
        return utc - timedelta(hours=4)  # EDT


def _parse_asof(asof_str: Optional[str]) -> datetime:
    """Parse --asof ISO string, falling back to ET now."""
    if not asof_str:
        return _et_now()
    # Handle Z suffix
    s = asof_str.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        print(f"[error] Cannot parse --asof: {asof_str}", file=sys.stderr)
        sys.exit(2)
    # Ensure timezone-aware
    if dt.tzinfo is None:
        try:
            from zoneinfo import ZoneInfo
            dt = dt.replace(tzinfo=ZoneInfo("America/New_York"))
        except ImportError:
            dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _lookback_window(window_id: str, asof: datetime) -> Tuple[datetime, datetime]:
    """
    Return (lookback_start, lookback_end) in ET-aware datetimes.
    Morning crosses midnight: prev day 23:30 → today 07:30.
    """
    try:
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
    except ImportError:
        et = timezone.utc

    # Normalize asof to ET
    asof_et = asof.astimezone(et)

    fire_et = WINDOWS[window_id]["fire_et"]
    fire_h, fire_m = map(int, fire_et.split(":"))

    if window_id == "morning":
        # start = prev day 23:30, end = today 07:30
        end = asof_et.replace(hour=fire_h, minute=fire_m, second=0, microsecond=0)
        start = (end - timedelta(days=1)).replace(hour=23, minute=30, second=0, microsecond=0)
    elif window_id == "mid-day":
        end = asof_et.replace(hour=fire_h, minute=fire_m, second=0, microsecond=0)
        start = end.replace(hour=7, minute=30, second=0, microsecond=0)
    elif window_id == "power-hour":
        end = asof_et.replace(hour=fire_h, minute=fire_m, second=0, microsecond=0)
        start = end.replace(hour=12, minute=0, second=0, microsecond=0)
    else:
        raise ValueError(f"Unknown window_id: {window_id}")

    return start, end


def _iso_et(dt: datetime) -> str:
    """Format datetime as ISO string with ET offset."""
    try:
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
        dt_et = dt.astimezone(et)
    except ImportError:
        dt_et = dt
    return dt_et.isoformat(timespec="seconds")


def _is_weekend(dt: datetime) -> bool:
    return dt.weekday() >= 5  # 5=Sat, 6=Sun


def _is_xnys_holiday(dt: datetime) -> bool:
    """Check if date is a known NYSE holiday (best-effort, no dependency)."""
    try:
        import pandas_market_calendars as mcal
        xnys = mcal.get_calendar("XNYS")
        import pandas as pd
        day = pd.Timestamp(dt.date())
        schedule = xnys.schedule(start_date=day, end_date=day)
        return schedule.empty
    except ImportError:
        pass
    except Exception:
        pass
    # Fallback: known US market holidays (2026 approx)
    holidays_2026 = {
        (1, 1), (1, 19), (2, 16), (4, 3), (5, 25),
        (6, 19), (7, 3), (9, 7), (11, 26), (12, 25),
    }
    return (dt.month, dt.day) in holidays_2026


# ── Buzz score (spec §4) ───────────────────────────────────────────────────

def _credibility(post: Dict, asof: datetime) -> float:
    """Compute credibility C in [0, 1] per spec formula."""
    followers = post.get("followers")
    verified = post.get("verified")
    account_age_days = post.get("account_age_days")
    following = post.get("following")
    handle = post.get("handle", "").lower()

    if followers is None and verified is None and account_age_days is None and following is None:
        C = 0.50
    else:
        parts = 0.0
        # 0.40 * min(log10(1+followers) / 6.0, 1.0)
        if followers is not None:
            parts += 0.40 * min(math.log10(1 + followers) / 6.0, 1.0)
        # 0.25 * (1.0 if verified else 0.0)
        if verified is not None:
            parts += 0.25 * (1.0 if verified else 0.0)
        # 0.20 * min(account_age_days / 365.0, 1.0)
        if account_age_days is not None:
            parts += 0.20 * min(account_age_days / 365.0, 1.0)
        # 0.15 * min(followers / (following + 1.0), 10.0) / 10.0
        if followers is not None and following is not None:
            parts += 0.15 * min(followers / (following + 1.0), 10.0) / 10.0
        C = max(0.0, min(parts, 1.0))

    # Allowlist override
    if handle in CREDIBLE_HANDLES:
        C = max(C, 0.80)
    # Low followers cap
    if followers is not None and followers < 50:
        C = min(C, 0.25)
    # New account cap
    if account_age_days is not None and account_age_days < 7:
        C = min(C, 0.20)

    return C


def _buzz_score(post: Dict, asof: datetime) -> float:
    """Compute buzz score B per spec §4."""
    likes = post.get("likes", 0) or 0
    replies = post.get("replies", 0) or 0
    reposts = post.get("reposts", 0) or 0
    quotes = post.get("quotes", 0) or 0

    created_at = _parse_created_at(post.get("created_at", ""))
    if created_at:
        age_sec = max((asof - created_at).total_seconds(), AGE_FLOOR_MIN * 60)
    else:
        age_sec = AGE_FLOOR_MIN * 60

    age_min = age_sec / 60.0
    raw_eng = likes + 2 * replies + 3 * reposts + 1 * quotes
    EV = raw_eng / age_min
    C = _credibility(post, asof)
    B = 100.0 * EV * (0.35 + 0.65 * C)
    return round(B, 2)


def _parse_created_at(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    # Twitter format: "Wed Oct 10 20:19:24 +0000 2018"
    try:
        return datetime.strptime(ts, "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        pass
    # ISO format
    ts_clean = ts.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(ts_clean)
    except ValueError:
        return None


# ── Sentiment (spec §5) ────────────────────────────────────────────────────

_BULL_RE = re.compile(r'\b(' + '|'.join(re.escape(t) for t in BULL_TOKENS) + r')\b', re.I)
_BEAR_RE = re.compile(r'\b(' + '|'.join(re.escape(t) for t in BEAR_TOKENS) + r')\b', re.I)


def _sentiment(text: str) -> str:
    n_bull = len(_BULL_RE.findall(text))
    n_bear = len(_BEAR_RE.findall(text))
    if n_bull > n_bear:
        return "bullish"
    if n_bear > n_bull:
        return "bearish"
    return "neutral"


# ── Topic extraction (spec §5) ─────────────────────────────────────────────

_CASHTAG_RE = re.compile(r'\$([A-Z]{1,5})')
_HASHTAG_RE = re.compile(r'#(\w+)')

MONITORED_HASHTAGS = {h.lower().lstrip("#") for h in HASHTAGS}


def _extract_topic(text: str) -> str:
    cashtags = _CASHTAG_RE.findall(text)
    if cashtags:
        return f"${cashtags[0]}"
    hashtags = _HASHTAG_RE.findall(text)
    for h in hashtags:
        if h.lower() in MONITORED_HASHTAGS:
            return f"#{h}"
    if hashtags:
        return f"#{hashtags[0]}"
    return "untagged"


# ── X API v2 backend ───────────────────────────────────────────────────────

def _search_x_api(
    bearer_token: str,
    hashtags: List[str],
    cashtags: List[str],
    start_time: str,
    end_time: str,
    target_count: int = 100,
) -> List[Dict]:
    """Search X API v2 with pagination. Returns normalized post dicts."""
    # Build query: OR of hashtags + cashtags, -is:retweet
    query_parts = [f"({tag})" for tag in hashtags] + [f"({ct})" for ct in cashtags]
    query = " OR ".join(query_parts) + " -is:retweet lang:en"

    all_posts = []
    next_token = None
    pages = 0
    max_pages = 5

    while len(all_posts) < target_count and pages < max_pages:
        params = {
            "query": query,
            "max_results": min(100, target_count - len(all_posts) + 20),
            "tweet.fields": "created_at,public_metrics,author_id,lang",
            "expansions": "author_id",
            "user.fields": "username,name,public_metrics,verified,created_at",
            "start_time": start_time,
            "end_time": end_time,
        }
        if next_token:
            params["next_token"] = next_token

        qs = urllib.parse.urlencode(params)
        url = f"{X_API_BASE}?{qs}"
        headers = {"Authorization": f"Bearer {bearer_token}"}

        data = _fetch_json(url, headers=headers, timeout=20)
        if not data or "data" not in data:
            break

        # Build author lookup
        authors = {}
        for u in data.get("includes", {}).get("users", []):
            created = u.get("created_at", "")
            age_days = None
            if created:
                ct = _parse_created_at(created)
                if ct:
                    age_days = max(0, (datetime.now(timezone.utc) - ct).days)
            authors[u["id"]] = {
                "username": u.get("username", ""),
                "name": u.get("name", ""),
                "followers": u.get("public_metrics", {}).get("followers_count", 0),
                "following": u.get("public_metrics", {}).get("following_count", 0),
                "verified": u.get("verified", False),
                "account_age_days": age_days,
            }

        for tweet in data["data"]:
            pm = tweet.get("public_metrics", {})
            author = authors.get(tweet.get("author_id"), {})
            handle = author.get("username", "")

            # Reconstruct URL
            tweet_id = tweet.get("id", "")
            url = f"https://x.com/{handle}/status/{tweet_id}" if handle else ""

            all_posts.append({
                "post_id": tweet_id,
                "url": url,
                "handle": handle,
                "author_name": author.get("name", ""),
                "text": tweet.get("text", ""),
                "created_at": tweet.get("created_at", ""),
                "likes": pm.get("like_count", 0),
                "replies": pm.get("reply_count", 0),
                "reposts": pm.get("retweet_count", 0),
                "quotes": pm.get("quote_count", 0),
                "followers": author.get("followers"),
                "following": author.get("following"),
                "verified": author.get("verified"),
                "account_age_days": author.get("account_age_days"),
            })

        next_token = data.get("meta", {}).get("next_token")
        if not next_token:
            break
        pages += 1
        time.sleep(0.5)

    return all_posts


# ── Nitter RSS fallback ────────────────────────────────────────────────────

def _search_nitter(
    hashtags: List[str],
    cashtags: List[str],
    start: datetime,
    end: datetime,
) -> List[Dict]:
    """Fetch from Nitter RSS feeds. Returns normalized post dicts."""
    results = []
    seen = set()
    all_queries = hashtags + cashtags

    for query in all_queries:
        for instance in NITTER_INSTANCES:
            url = f"{instance}/search/rss?f=tweets&q={urllib.parse.quote(query)}"
            text = _fetch(url, timeout=10)
            if text is None:
                continue

            try:
                root = ET.fromstring(text)
            except ET.ParseError:
                continue

            items = root.findall(".//item")
            for item in items:
                title_el = item.find("title")
                link_el = item.find("link")
                pubdate_el = item.find("pubDate")
                desc_el = item.find("description")

                title = title_el.text if title_el is not None else ""
                link = link_el.text if link_el is not None else ""
                pubdate = pubdate_el.text if pubdate_el is not None else ""

                # Parse timestamp
                ts = _parse_created_at(pubdate)
                if ts:
                    ts_utc = ts.astimezone(timezone.utc)
                    start_utc = start.astimezone(timezone.utc)
                    end_utc = end.astimezone(timezone.utc)
                    if ts_utc < start_utc or ts_utc >= end_utc:
                        continue

                # Extract tweet ID
                tweet_id = link.rstrip("/").split("/")[-1] if link else ""
                if tweet_id in seen:
                    continue
                seen.add(tweet_id)

                # Extract username
                username = _extract_nitter_username(link)

                results.append({
                    "post_id": tweet_id,
                    "url": link,
                    "handle": username,
                    "author_name": username,
                    "text": re.sub(r'<[^>]+>', '', title).strip(),
                    "created_at": pubdate,
                    "likes": 0,
                    "replies": 0,
                    "reposts": 0,
                    "quotes": 0,
                    "followers": None,
                    "following": None,
                    "verified": None,
                    "account_age_days": None,
                })

            if results:
                break
        if results:
            break

    return results


def _extract_nitter_username(link: str) -> str:
    if not link:
        return "unknown"
    parts = link.rstrip("/").split("/")
    for i, p in enumerate(parts):
        if p == "status" and i > 0:
            return parts[i - 1]
    return "unknown"


# ── xAI Grok x_search backend (SuperGrok OAuth, native X, no Twitter bearer) ─
# This is the preferred native source now that the X API v2 app-only bearer is
# not in use. It calls xAI's built-in `x_search` Responses API tool with the
# SuperGrok OAuth bearer (or XAI_API_KEY if configured). NOTE: the SuperGrok
# OAuth path returns citations only when NO date/handle filters are applied
# (filtered modes degrade to a no-citation answer — xAI #88040), so we query
# broadly and filter client-side. Engagement counts are not returned by x_search,
# so buzz is citation/credibility-weighted (see scan()).

XAI_RESPONSES_URL = "https://api.x.ai/v1/responses"
XAI_X_SEARCH_MODEL = "grok-4.5"


def _resolve_xai_bearer() -> tuple:
    """Return (api_key, base_url) for xAI, or (None, default_url).

    Prefers the full Hermes resolver (which auto-refreshes the OAuth token),
    then falls back to reading the SuperGrok OAuth token straight out of
    ~/.hermes/auth.json so the scanner works even without importing the
    heavier toolchain.
    """
    # 1) Full Hermes resolver (auto-refresh, honors XAI_API_KEY + OAuth).
    try:
        home = os.environ.get("HERMES_HOME") or os.environ.get("LOCALAPPDATA") or ""
        if home and os.path.isdir(os.path.join(home, "hermes-agent")):
            sys.path.insert(0, os.path.join(home, "hermes-agent"))
        from tools.xai_http import resolve_xai_http_credentials

        creds = resolve_xai_http_credentials(prefer_api_key=True)
        key = str(creds.get("api_key") or "").strip()
        if key:
            return key, creds.get("base_url") or "https://api.x.ai/v1"
    except Exception:
        pass

    # 2) Fallback: read the SuperGrok OAuth token from auth.json directly.
    try:
        candidates = []
        h = os.environ.get("HERMES_HOME")
        if h:
            candidates.append(os.path.join(h, "auth.json"))
        lap = os.environ.get("LOCALAPPDATA")
        if lap:
            candidates.append(os.path.join(lap, "hermes", "auth.json"))
        candidates.append(os.path.expanduser("~/.hermes/auth.json"))
        for path in candidates:
            if not os.path.exists(path):
                continue
            store = json.loads(open(path, encoding="utf-8-sig").read())
            x = store.get("providers", {}).get("xai-oauth", {})
            tok = x.get("tokens", {}).get("access_token")
            if str(tok or "").strip():
                return str(tok).strip(), "https://api.x.ai/v1"
            for e in store.get("credential_pool", {}).get("xai-oauth", []) or []:
                if isinstance(e, dict) and str(e.get("access_token", "")).strip():
                    return str(e["access_token"]).strip(), "https://api.x.ai/v1"
    except Exception:
        pass

    return None, "https://api.x.ai/v1"


def _extract_x_search_citations(data: dict) -> list:
    """Pull inline url_citation annotations (real X post URLs) from a response.

    xAI returns them nested under output[].content[].annotations (on the
    output_text block), not as top-level annotation blocks.
    """
    cites = []
    try:
        for item in data.get("output", []):
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for block in item.get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "output_text":
                    continue
                for ann in block.get("annotations") or []:
                    if isinstance(ann, dict) and ann.get("type") == "url_citation":
                        cites.append(
                            {
                                "url": ann.get("url"),
                                "title": ann.get("title"),
                                "start_index": ann.get("start_index"),
                                "end_index": ann.get("end_index"),
                            }
                        )
    except Exception:
        pass
    return cites


_NUM_RE = re.compile(r"([\d,]+)\s*(likes|reposts|replies|reposts/quotes|bookmarks|views)?", re.I)


def _parse_engagement_near(text: str, start: int, end: int) -> dict:
    """Best-effort parse of engagement figures embedded in the answer snippet
    near a citation (e.g. '2,795 likes, 479 reposts')."""
    span = text[max(0, start - 220): end + 220]
    likes = replies = reposts = quotes = 0
    # crude: find 'N likes' / 'N reposts' / 'N replies' patterns
    m_likes = re.search(r"([\d,]+)\s*likes", span, re.I)
    m_reposts = re.search(r"([\d,]+)\s*reposts", span, re.I)
    m_replies = re.search(r"([\d,]+)\s*replies", span, re.I)
    m_quotes = re.search(r"([\d,]+)\s*quotes", span, re.I)
    if m_likes:
        likes = int(m_likes.group(1).replace(",", ""))
    if m_reposts:
        reposts = int(m_reposts.group(1).replace(",", ""))
    if m_replies:
        replies = int(m_replies.group(1).replace(",", ""))
    if m_quotes:
        quotes = int(m_quotes.group(1).replace(",", ""))
    return {"likes": likes, "replies": replies, "reposts": reposts, "quotes": quotes}


def _search_x_search(window_id: str, asof, hashtags: list, cashtags: list,
                     target_count: int = 50) -> list:
    """Query xAI Grok x_search for fintwit buzz and return normalized posts.

    Returns [] on any failure (so the caller falls through to other sources).
    """
    api_key, base_url = _resolve_xai_bearer()
    if not api_key:
        print("[x_search] No xAI bearer resolved — skipping.", file=sys.stderr)
        return []

    # Broad, unfiltered query — filtered modes degrade to no citations (#88040).
    # Explicitly ask for a long numbered list so the model emits many
    # url_citation annotations (each cited post becomes a buzz signal).
    tags = " ".join(cashtags[:5] + hashtags[:3])
    query = (
        f"List the 15 most-discussed fintwit posts on X right now about {tags}. "
        f"For EACH post write a separate numbered bullet with: the author @handle, the post text, "
        f"approximate likes and reposts, and the source post URL (https://x.com/handle/status/ID). "
        f"Cover the loudest tickers and macro conversations."
    )
    payload = {
        "model": XAI_X_SEARCH_MODEL,
        "input": [{"role": "user", "content": query}],
        "tools": [{"type": "x_search"}],
        "store": False,
    }
    req = urllib.request.Request(
        f"{base_url}/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Hermes-Agent/x_buzz",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"[x_search] HTTP/parse error: {exc!r}", file=sys.stderr)
        return []

    # Reconstruct the answer text so we can pull a snippet per citation.
    answer = ""
    try:
        for item in data.get("output", []):
            if isinstance(item, dict) and item.get("type") == "message":
                for b in item.get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "output_text":
                        answer += b.get("text", "")
    except Exception:
        pass

    cites = _extract_x_search_citations(data)
    # Primary signal: every post URL that appears in the answer text (robust —
    # the SuperGrok OAuth path emits url_citation annotations inconsistently,
    # but the model reliably writes https://x.com/handle/status/ID URLs inline).
    # Citations (when present) are used only to anchor a snippet + engagement.
    cite_by_url = {}
    for c in cites:
        u = c.get("url")
        if u:
            cite_by_url[u] = c

    url_positions = [(m.start(), m.group(0)) for m in re.finditer(r"https?://x\.com/([^/\s)\]]+)/status/(\d+)", answer)]
    print(f"[x_search] citations={len(cites)} text_urls={len(url_positions)} answer_len={len(answer)}", file=sys.stderr)

    posts = []
    seen = set()
    for pos, url in url_positions:
        if url in seen:
            continue
        m = re.search(r"x\.com/([^/]+)/status/(\d+)", url)
        if not m:
            continue
        seen.add(url)
        handle = m.group(1)
        post_id = m.group(2)
        text = ""
        eng = {"likes": 0, "replies": 0, "reposts": 0, "quotes": 0}
        c = cite_by_url.get(url)
        if c and c.get("start_index") is not None and answer:
            s, e = int(c["start_index"]), int(c["end_index"])
            text = answer[max(0, s - 80):e + 80].strip()
            eng = _parse_engagement_near(answer, s, e)
        else:
            # No annotation anchor: take a window around the URL in the text.
            text = answer[max(0, pos - 120):pos + 160].strip()
            eng = _parse_engagement_near(answer, max(0, pos - 80), pos + 80)
        C = 0.50
        if handle.lower() in CREDIBLE_HANDLES:
            C = max(C, 0.80)
        posts.append(
            {
                "post_id": post_id,
                "url": url,
                "handle": handle,
                "author_name": handle,
                "text": text,
                "created_at": _iso_et(asof),
                "likes": eng["likes"],
                "replies": eng["replies"],
                "reposts": eng["reposts"],
                "quotes": eng["quotes"],
                "followers": None,
                "following": None,
                "verified": None,
                "account_age_days": None,
                "credibility": round(C, 4),
                "via_x_search": True,
            }
        )
        if len(posts) >= target_count:
            break
    return posts


# ── Deduplication ───────────────────────────────────────────────────────────

def _dedup(posts: List[Dict]) -> List[Dict]:
    seen = set()
    out = []
    for p in posts:
        pid = p.get("post_id", "")
        if pid and pid not in seen:
            seen.add(pid)
            out.append(p)
    return out


# ── Main scanner ────────────────────────────────────────────────────────────

def scan(window_id: str, asof: Optional[str] = None) -> Dict:
    """
    Run the buzz scan. Returns the scanner JSON dict per spec §3.
    """
    asof_dt = _parse_asof(asof)
    start, end = _lookback_window(window_id, asof_dt)

    # Weekend/holiday skip
    if _is_weekend(asof_dt):
        return _empty_result(window_id, asof_dt, start, end, "skip_weekend")
    if _is_xnys_holiday(asof_dt):
        return _empty_result(window_id, asof_dt, start, end, "skip_holiday")

    bearer_token = os.environ.get("TWITTER_BEARER_TOKEN", "")
    source = "none"
    posts = []

    # ── Attempt 1: xAI Grok x_search (SuperGrok OAuth — native X, no bearer) ──
    # Preferred native source. Returns real cited posts via inline url_citations.
    posts = _search_x_search(window_id, asof_dt, HASHTAGS, CASHTAGS)
    if posts:
        source = "x_search"

    # ── Attempt 2: X API v2 (only if a bearer token is explicitly set) ──
    if not posts and bearer_token:
        print(f"[x_api] Querying with bearer token...", file=sys.stderr)
        posts = _search_x_api(
            bearer_token=bearer_token,
            hashtags=HASHTAGS,
            cashtags=CASHTAGS,
            start_time=start_api,
            end_time=end_api,
            target_count=50,
        )
        if posts:
            source = "x_api_v2"
            print(f"[x_api] Got {len(posts)} tweets", file=sys.stderr)

    # ── Attempt 3: Nitter fallback (instances are often unreachable) ──
    if not posts:
        print("[nitter] Trying Nitter RSS...", file=sys.stderr)
        posts = _search_nitter(HASHTAGS, CASHTAGS, start, end)
        if posts:
            source = "nitter"
            print(f"[nitter] Got {len(posts)} posts", file=sys.stderr)

    # Dedup
    posts = _dedup(posts)

    # Score and rank
    for post in posts:
        post["credibility"] = round(_credibility(post, asof_dt), 4)
        if post.get("via_x_search") and not (post.get("likes") or post.get("replies")
                                             or post.get("reposts") or post.get("quotes")):
            # x_search returned no parseable engagement — weight buzz by a
            # single citation signal * credibility so credible cited posts rank.
            ev = 1.0
            post["ev"] = ev
            post["buzz_score"] = round(100.0 * ev * (0.35 + 0.65 * post["credibility"]), 2)
        else:
            post["buzz_score"] = _buzz_score(post, asof_dt)
            raw_eng = (post.get("likes", 0) or 0) + 2 * (post.get("replies", 0) or 0) \
                + 3 * (post.get("reposts", 0) or 0) + 1 * (post.get("quotes", 0) or 0)
            age_min = max(5.0, ((asof_dt - _parse_created_at(post.get("created_at", ""))).total_seconds() / 60.0)
                         if _parse_created_at(post.get("created_at", "")) else 5.0)
            post["ev"] = round(raw_eng / age_min, 4) if age_min > 0 else 0
        post["sentiment"] = _sentiment(post.get("text", ""))
        post["topic"] = _extract_topic(post.get("text", ""))

    posts.sort(key=lambda p: (-p["buzz_score"], p.get("created_at", "")))

    partial = len(posts) < 20
    data_quality = {
        "partial": partial,
        "reason": f"source returned {len(posts)} posts" if partial else None,
        "n_posts": len(posts),
    }
    if source == "x_search":
        # x_search does not return per-post timestamps or reliable follower
        # counts, so created_at is set to the scan time and credibility uses the
        # allowlist default. Buzz is weighted by parsed engagement where present.
        data_quality["source_note"] = (
            "native X via xAI Grok x_search (SuperGrok OAuth); post times are scan time, "
            "not original post time; engagement parsed from cited text where available"
        )

    if source == "none" and len(posts) == 0:
        # Hard fail — no source available
        print("[error] No data source available (no TWITTER_BEARER_TOKEN, Nitter unreachable)", file=sys.stderr)
        data_quality["reason"] = "auth/config hard-fail: no bearer token and Nitter unreachable"
        # Still write what we have (empty) and exit 0 per spec, unless it's truly a hard-fail
        # Spec says exit 2 only on auth/config hard-fail
        pass

    result = {
        "schema_version": SCHEMA_VERSION,
        "window_id": window_id,
        "asof_et": _iso_et(asof_dt),
        "lookback_start_et": _iso_et(start),
        "lookback_end_et": _iso_et(end),
        "source": source,
        "data_quality": data_quality,
        "posts": posts,
    }

    return result


def _empty_result(window_id, asof_dt, start, end, reason):
    return {
        "schema_version": SCHEMA_VERSION,
        "window_id": window_id,
        "asof_et": _iso_et(asof_dt),
        "lookback_start_et": _iso_et(start),
        "lookback_end_et": _iso_et(end),
        "source": "none",
        "data_quality": {"partial": True, "reason": reason, "n_posts": 0},
        "posts": [],
    }


# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="X.com Fintwit Buzz Scanner v1.0"
    )
    parser.add_argument(
        "--window", "-w",
        required=True,
        choices=["morning", "mid-day", "power-hour"],
        help="Scan window ID",
    )
    parser.add_argument(
        "--asof", "-a",
        default=None,
        help="Override 'now' with ISO-8601 timestamp (for backfill/tests)",
    )
    parser.add_argument(
        "--out", "-o",
        default=None,
        help="Output JSON file path (default: stdout)",
    )

    args = parser.parse_args()

    result = scan(args.window, args.asof)

    output = json.dumps(result, indent=2)

    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"[ok] Written to {args.out}", file=sys.stderr)
    else:
        print(output)

    # Exit code: 0 = ok/partial, 2 = hard-fail
    n = result["data_quality"]["n_posts"]
    reason = result["data_quality"].get("reason") or ""
    if "hard-fail" in reason:
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
