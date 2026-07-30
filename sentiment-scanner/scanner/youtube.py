"""YouTube Scanner — transcript-based sentiment for the sentiment scanner.

Uses yt-dlp (no API key) to search for and scrape YouTube video transcripts
from finance channels. Transcripts are richer signal than comments: they
contain the actual analysis being discussed, not just emoji reactions.

Broad searches (trending finance topics) feed into the ticker discovery,
while ticker-specific searches feed per-symbol sentiment into the
CorrelationEngine alongside StockTwits and Reddit.
"""

import json
import os
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

# Ensure yt-dlp can find Node.js for JavaScript extraction
import shutil
_NODE_BIN = shutil.which("node")
_NODE_PATH = None
if _NODE_BIN:
    _NODE_DIR = os.path.dirname(_NODE_BIN)
    if _NODE_DIR not in os.environ.get("PATH", ""):
        os.environ["PATH"] = f"{_NODE_DIR}{os.pathsep}{os.environ.get('PATH', '')}"
    _NODE_PATH = _NODE_BIN  # Full path to node binary for yt-dlp config

from scanner.options_scanner_base import get_td
# Use the same narrative scoring engine as StockTwits
from scanner.narrative import score_messages

# ── Config ─────────────────────────────────────────────────────────────
YT_SEARCH_BROAD_TOPICS = [
    "stock market today",
    "stocks to watch",
    "market analysis",
    "earnings",
    "stock market news",
]

YT_TICKER_QUERY_TEMPLATES = [
    "{ticker} stock analysis",
    "${ticker} stock",
    "{ticker} earnings",
    "{ticker} technical analysis",
]

YT_RESULTS_PER_QUERY = 3
YT_CACHE_TTL_MINUTES = 60  # don't re-fetch same video within this window

# Maximum transcript chars to score per video
YT_MAX_TRANSCRIPT_CHARS = 3000

# ── Cache (in-memory, per session) ────────────────────────────────────
_cache: Dict[str, dict] = {}  # video_id -> {ts, title, transcript, channel, views}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cache_key(ticker_or_topic: str, kind: str) -> str:
    return f"{kind}:{ticker_or_topic.upper()}"


def _extract_ticker_from_transcript(transcript: str) -> List[str]:
    """Pull likely stock tickers mentioned in a transcript.

    Looks for $TICKER patterns and uppercase 1-4 letter symbols in context
    of financial speech. Filters out common English words.
    """
    _STOP_WORDS = {
        "A", "I", "THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU", "ALL",
        "CAN", "HAS", "WAS", "WERE", "HAD", "BEEN", "MORE", "SOME", "THAN",
        "THAT", "THIS", "WITH", "HERE", "THERE", "WHAT", "WHEN", "WHERE",
        "WHICH", "WHO", "HOW", "MUCH", "MANY", "SUCH", "ONLY", "JUST",
        "ALSO", "VERY", "WELL", "EVEN", "STILL", "ALREADY", "NOW", "THEN",
        "TOO", "SO", "IF", "OR", "AS", "AT", "BY", "IN", "IS", "IT",
        "OF", "ON", "TO", "UP", "US", "GO", "DO", "BE", "NO", "MY",
        "ONE", "TWO", "OUT", "OFF", "TOP", "BIG", "NEW", "OLD", "GET",
        "SAY", "SEE", "WAY", "LOT", "RUN", "SET", "PUT", "LET", "USE",
        "MAKE", "LOOK", "KNOW", "TAKE", "COME", "LIKE", "BACK", "DOWN",
        "GOOD", "TIME", "YEAR", "WEEK", "DAY", "LONG", "LOW", "HIGH",
        "LAST", "NEXT", "FIRST", "REAL", "SAME", "FEW", "SURE", "HARD",
        "SIDE", "HALF", "FULL", "OPEN", "CLOSE", "LEFT", "RIGHT", "FAR",
        "BEST", "DONE", "DARK", "LATE", "EARLY", "FAST", "SLOW", "HOT",
        "COLD", "WARM", "COOL", "PAST", "FELT", "HELD", "KEPT", "SENT",
        "BORN", "GONE", "GAVE", "HELP", "NEED", "KEEP", "WANT", "SHOW",
        "TURN", "CALL", "PLAY", "MOVE", "WORK", "LIVE", "STAY", "WALK",
        "CARE", "HOPE", "WISH", "FIND", "HOLD", "BRING", "BUILD", "GROW",
        "LEAD", "LEARN", "MEET", "READ", "WRITE", "SPEND", "STAND",
        "START", "STOP", "TRY", "WAIT", "WATCH", "HEAR", "THINK", "FEEL",
        "SOME", "BODY", "TEAM", "PLAN", "FORM", "KIND", "PART", "AREA",
        "CASE", "FACT", "IDEA", "LIFE", "LINE", "LIST", "NAME", "NOTE",
        "PATH", "SALE", "SHOP", "SITE", "SORT", "TYPE", "VOTE", "WORK",
        "YARD", "ROLE", "RULE", "BILL", "BOOK", "CITY", "DATE", "DOOR",
        "EDGE", "FACE", "FARM", "FILE", "FIRE", "FISH", "FOOD", "FUND",
        "GAME", "GATE", "HALL", "HOME", "HOUR", "KING", "LAND", "MAIL",
        "MARK", "MODE", "MONEY", "NIGHT", "PAGE", "PAIR", "PARK", "RATE",
        "REST", "SAFE", "SAVE", "SEAT", "SHIP", "SHOP", "SHOW", "SIGN",
        "SITE", "SIZE", "STAR", "STEP", "TASK", "TERM", "TEST", "TEXT",
        "TOOL", "TREE", "WALL", "WAVE", "WEEK", "WEST", "WIDE", "WIFE",
        "WIND", "WING", "WIRE", "WOOD", "WORD", "SHOULD", "WOULD",
        "COULD", "ABOUT", "ABOVE", "AFTER", "AGAIN", "ALONG", "AMONG",
        "BEFORE", "BEGIN", "BEHIND", "BELOW", "BETWEEN", "BEYOND",
        "DURING", "EVERY", "INSIDE", "NEARLY", "OFFICE", "OTHERS",
        "OUTSIDE", "OVER", "PERHAPS", "RATHER", "THROUGH", "TOWARD",
        "UNDER", "UNLESS", "UNLIKE", "UNTIL", "UPON", "WITHIN", "WITHOUT",
        "SOLID", "HERE", "THAT", "RUN", "WITH", "SOME", "THE",
    }
    _TICKER_RE = re.compile(r'\b[A-Z]{1,5}\b')
    found = set()
    # Dollar-prefixed tickers — always valid
    for m in re.finditer(r'\$([A-Z]{1,5})\b', transcript):
        t = m.group(1)
        if t not in _STOP_WORDS:
            found.add(t)
    # Uppercase 1-4 letter words in finance context
    lower_transcript = transcript.lower()
    is_finance_video = any(ctx in lower_transcript for ctx in [
        "stock", "market", "trading", "earnings", "investor", "etf",
        "nasdaq", "nyse", "s&p", "dow jones", "bull", "bear",
        "rally", "correction", "volatility", "support", "resistance",
        "breakout", "breakdown", "analysis", "technical", "fundamental",
        "portfolio", "dividend", "revenue", "profit", "sector",
        "index", "futures", "options", "calls", "puts", "shares",
        "buy", "sell", "short", "long", "price", "chart", "pattern",
    ])
    for m in _TICKER_RE.finditer(transcript):
        t = m.group(0)
        if t in _STOP_WORDS or len(t) > 4:
            continue
        if t in found:
            continue
        # Found in a finance video context — accept it
        if is_finance_video:
            found.add(t)
            continue
        # Also check direct context window
        pos = m.start()
        before = lower_transcript[max(0, pos - 40):pos]
        if any(ctx in before for ctx in ["stock", "ticker", "buy ", "sell ",
                "etf", "ndaq", "nasdaq", "s&p", "spy", "qqq", "iwm",
                "sector", "rally", "pump", "dump", "crashed", "surge",
                "plunge", "soar", "slump", "support", "resistance",
                "breakout", "breakdown", "earnings", "price", "share",
                "dividend", "volatility", "momentum", "trend", "pattern",
                "fibonacci", "ema", "sma", "rsi", "macd", "bollinger",
                "correction", "reversal", "continuation"]):
            found.add(t)

    return list(found)[:10]


def _broad_scan_to_tickers(videos: List[dict]) -> Dict[str, List[str]]:
    """From broad scan results, extract ticker mentions per video.

    Returns dict of ticker -> [video_id, ...].
    """
    ticker_map: Dict[str, List[str]] = {}
    for v in videos:
        transcript = v.get("transcript", "")
        tickers = _extract_ticker_from_transcript(transcript)
        # Also check title
        title = v.get("title", "")
        for m in re.finditer(r'\$([A-Z]{1,5})\b', title):
            t = m.group(1)
            if t not in tickers:
                tickers.append(t)
        for t in tickers:
            if t not in ticker_map:
                ticker_map[t] = []
            ticker_map[t].append(v["id"])
    return ticker_map


# ── Core search functions ──────────────────────────────────────────────

def _ytdl_search(query: str, max_results: int = YT_RESULTS_PER_QUERY) -> List[dict]:
    """Run a yt-dlp search, return list of video metadata dicts."""
    try:
        import yt_dlp
    except ImportError:
        print("  [youtube] yt-dlp not installed. Install with: pip install yt-dlp")
        return []

    ydl_config = {
        "quiet": True,
        "extract_flat": True,
        "skip_download": True,
    }
    if _NODE_PATH:
        ydl_config["js_runtimes"] = {"node": {"path": _NODE_PATH}}

    with yt_dlp.YoutubeDL(ydl_config) as ydl:
        try:
            result = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
            entries = result.get("entries", []) if result else []
            return [
                {
                    "id": e.get("id", ""),
                    "title": e.get("title", ""),
                    "channel": e.get("channel", e.get("uploader", "")),
                    "view_count": e.get("view_count", 0),
                    "duration": e.get("duration", 0),
                    "upload_date": str(e.get("upload_date", "") or ""),
                    "description": (e.get("description") or "")[:500],
                }
                for e in entries if e and e.get("id")
            ]
        except Exception as e:
            return []


def _fetch_transcript(video_id: str) -> Optional[str]:
    """Get auto-captions transcript for a video.

    Returns plain text transcript, or None if unavailable.
    """
    try:
        import yt_dlp
    except ImportError:
        return None

    try:
        ydl_config = {
            "quiet": True,
            "skip_download": True,
            "writesubtitles": False,
            "writeautomaticsub": True,
            "subtitleslangs": ["en"],
        }
        if _NODE_PATH:
            ydl_config["js_runtimes"] = {"node": {"path": _NODE_PATH}}

        with yt_dlp.YoutubeDL(ydl_config) as ydl:
            info = ydl.extract_info(video_id, download=False)
    except Exception:
        return None

    if not info:
        return None

    # Try automatic captions first
    auto = info.get("automatic_captions", {}) or {}
    for lang_key in ["en", "a.en", "en-US", "en-GB"]:
        caps = auto.get(lang_key, [])
        if not caps:
            continue
        # Prefer json3 format
        url = None
        for cap in caps:
            if cap.get("ext") == "json3":
                url = cap["url"]
                break
        if not url:
            for cap in caps:
                if cap.get("ext") in ("srv1", "srv2", "vtt"):
                    url = cap["url"]
                    break
        if url:
            for attempt in range(3):
                try:
                    resp = urllib.request.urlopen(url, timeout=15)
                    data = json.loads(resp.read().decode("utf-8"))
                    events = data.get("events", [])
                    parts = []
                    for ev in events[:300]:
                        segs = ev.get("segs", [])
                        for s in segs:
                            t = (s.get("utf8") or "").strip()
                            if t:
                                parts.append(t)
                            if len(" ".join(parts)) > YT_MAX_TRANSCRIPT_CHARS:
                                break
                        if len(" ".join(parts)) > YT_MAX_TRANSCRIPT_CHARS:
                            break
                    return " ".join(parts)
                except urllib.error.HTTPError as e:
                    if e.code == 429 and attempt < 2:
                        time.sleep(5 * (attempt + 1))
                        continue
                    break
                except Exception:
                    break
    return None


def _score_transcript(video: dict) -> dict:
    """Score a video's transcript with the contested-narrative engine.

    Returns a dict compatible with the existing narrative scoring format.
    """
    transcript = video.get("transcript", "")
    if not transcript:
        return {
            "war_score": 0.0,
            "contested_narrative_score": 0,
            "volume": video.get("view_count", 0),
            "thesis_ratio": 0.5,
            "pump_ratio": 0.0,
            "bullish_pct": 0.0,
            "bearish_pct": 0.0,
        }

    # The core scoring uses the same logic as StockTwits messages,
    # but treats the transcript as one long message
    # score_messages expects a list of message dicts with 'body'
    messages = [{"body": transcript}]
    scores = score_messages(messages)

    # Blend in view count as a volume/attention proxy
    views = video.get("view_count", 0)
    scores["volume"] = max(scores.get("volume", 0), min(views // 1000, 100))

    return scores


# ── Public API ─────────────────────────────────────────────────────────

def search_ticker_videos(ticker: str, max_results: int = 3) -> List[dict]:
    """Search YouTube for videos about a ticker, enrich with transcripts."""
    cached = _cache.get(_cache_key(ticker, "ticker"), {})
    cache_age = cached.get("ts")
    if cache_age and (_now() - cache_age) < timedelta(minutes=YT_CACHE_TTL_MINUTES):
        return cached.get("videos", [])

    all_videos = []
    seen_ids = set()
    for tmpl in YT_TICKER_QUERY_TEMPLATES:
        query = tmpl.format(ticker=ticker)
        results = _ytdl_search(query, max_results=max_results)
        for v in results:
            vid = v["id"]
            if vid not in seen_ids:
                seen_ids.add(vid)
                all_videos.append(v)

    # Fetch transcripts
    for v in all_videos:
        cached_v = _cache.get(v["id"])
        if cached_v and cached_v.get("transcript"):
            v["transcript"] = cached_v["transcript"]
        else:
            v["transcript"] = _fetch_transcript(v["id"])
            if v["transcript"]:
                _cache[v["id"]] = {
                    "ts": _now(),
                    "transcript": v["transcript"],
                    "title": v["title"],
                    "channel": v["channel"],
                    "views": v["view_count"],
                }
        time.sleep(0.3)  # polite delay between fetches

    result = [v for v in all_videos if v.get("transcript")]
    _cache[_cache_key(ticker, "ticker")] = {"ts": _now(), "videos": result}
    return result


def broad_scan() -> Dict[str, List[str]]:
    """Run broad financial topic searches to discover ticker mentions.

    Uses video titles and descriptions (always available) to extract ticker
    mentions, since transcripts are not available for all videos.

    Returns dict of ticker -> [video_ids].
    """
    cached = _cache.get(_cache_key("broad", "scan"), {})
    cache_age = cached.get("ts")
    if cache_age and (_now() - cache_age) < timedelta(minutes=YT_CACHE_TTL_MINUTES):
        return cached.get("data", {})

    all_videos = []
    seen_ids = set()
    for topic in YT_SEARCH_BROAD_TOPICS:
        results = _ytdl_search(topic, max_results=YT_RESULTS_PER_QUERY)
        for v in results:
            if v["id"] not in seen_ids:
                seen_ids.add(v["id"])
                all_videos.append(v)

    # Extract tickers from titles and descriptions (no transcript required)
    for v in all_videos:
        text = f"{v.get('title', '')} {v.get('description', '')}"
        tickers = _extract_ticker_from_transcript(text)
        v["_tickers"] = tickers

    ticker_map: Dict[str, List[str]] = {}
    for v in all_videos:
        for t in v.get("_tickers", []):
            if t not in ticker_map:
                ticker_map[t] = []
            ticker_map[t].append(v["id"])

    _cache[_cache_key("broad", "scan")] = {"ts": _now(), "data": ticker_map}
    return ticker_map


def scan_ticker(ticker: str) -> Optional[dict]:
    """Full YouTube sentiment scan for a ticker.

    Returns scored result dict, or None if nothing found.

    Compatible with the existing scan_ticker signature used by main.py.
    """
    videos = search_ticker_videos(ticker)
    if not videos:
        return None

    # Aggregate scores across all videos for this ticker
    all_scores = []
    total_views = 0
    for v in videos:
        scores = _score_transcript(v)
        all_scores.append(scores)
        total_views += v.get("view_count", 0)

    # Average war score and CNS
    avg_war = sum(s["war_score"] for s in all_scores) / len(all_scores)
    avg_cns = sum(s["contested_narrative_score"] for s in all_scores) // len(all_scores)

    # Compute bullish/bearish percentages across all transcripts
    bullish_count = sum(1 for s in all_scores if s.get("bullish_pct", 0) > 50)
    bearish_count = sum(1 for s in all_scores if s.get("bearish_pct", 0) > 50)

    return {
        "ticker": ticker.upper(),
        "war_score": round(avg_war, 3),
        "contested_narrative_score": avg_cns,
        "thesis_ratio": round(sum(s["thesis_ratio"] for s in all_scores) / len(all_scores), 3),
        "pump_ratio": round(sum(s["pump_ratio"] for s in all_scores) / len(all_scores), 3),
        "volume": min(total_views // 1000, 100),
        "bullish_pct": round(bullish_count / len(all_scores) * 100, 1) if all_scores else 0.0,
        "bearish_pct": round(bearish_count / len(all_scores) * 100, 1) if all_scores else 0.0,
        "video_count": len(videos),
        "total_views": total_views,
        "videos": [
            {
                "id": v["id"],
                "title": v["title"][:80],
                "channel": v["channel"],
                "views": v["view_count"],
                "transcript_preview": v.get("transcript", "")[:200],
            }
            for v in videos
        ],
        "timestamp": _now().isoformat(),
    }


def format_scanner_line(result: dict) -> str:
    """One-line summary for console output, matching StockTwits style."""
    if not result:
        return ""
    t = result["ticker"]
    cns = result["contested_narrative_score"]
    war = result["war_score"]
    vol = result["volume"]
    vids = result.get("video_count", 0)
    views = result.get("total_views", 0)
    return (
        f"  {t:6s} | YT CNS: {cns:3d} | War: {war:.2f} | "
        f"Vol: {vol:3d} | {vids} videos ({views:,} views)"
    )


if __name__ == "__main__":
    import sys
    ticker = sys.argv[1].upper() if len(sys.argv) > 1 else "SPY"
    result = scan_ticker(ticker)
    if result:
        print(format_scanner_line(result))
        for v in result.get("videos", []):
            print(f"    {v['title'][:60]} | {v['channel'][:20]} | {v['views']:,} views")
    else:
        print(f"No YouTube data found for {ticker}")