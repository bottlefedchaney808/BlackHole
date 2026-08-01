"""YouTube Scanner — transcript-based sentiment for the sentiment scanner.

Uses yt-dlp (no API key) to search for and scrape YouTube video transcripts
from finance channels. Transcripts are richer signal than comments: they
contain the actual analysis being discussed, not just emoji reactions.

Broad searches (trending finance topics) feed into the ticker discovery,
while ticker-specific searches feed per-symbol sentiment into the
CorrelationEngine alongside StockTwits and Reddit.
"""

import json
import logging
import os
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

_log = logging.getLogger(__name__)

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

# ── bgutil-ytdlp-pot-provider (PO token server) ────────────────────────
# https://github.com/Brainicism/bgutil-ytdlp-pot-provider — the yt-dlp
# maintainer-endorsed Proof-of-Origin token provider. The `bgutil-ytdlp-pot-provider`
# pip package (importable as `yt_dlp_plugins`) is auto-discovered by yt-dlp's
# plugin system once installed; it still needs a locally running HTTP token
# server to talk to (Docker or Node.js — see sentiment-scanner/README.md).
# The extractor-arg key/param names below ('youtubepot-bgutilhttp' /
# 'base_url') come straight from the installed plugin's source
# (yt_dlp_plugins/extractor/getpot_bgutil_http.py, BgUtilHTTPPTP._base_url),
# which also defaults to the same URL when no base_url is configured.
YTDLP_POT_SERVER_URL = os.environ.get("YTDLP_POT_SERVER_URL", "http://127.0.0.1:4416").rstrip("/")

# Set YTDLP_POT_TRACE=1 to get full PO-token provider attempt/success/reject
# diagnostics in verbose runs — see the note on pot_trace below. Off by
# default because it's noisy and only matters for debugging this pipeline.
_POT_TRACE = os.environ.get("YTDLP_POT_TRACE", "").strip() not in ("", "0", "false", "False")


def _pot_extractor_args() -> dict:
    """extractor_args telling yt-dlp's bgutil plugin where the PO token server is.

    Pointing the bgutil plugin at the right base_url is necessary but NOT
    sufficient. yt-dlp's youtube extractor only actually *asks* a PO-token
    provider for a Subs-context token when it decides a token is "required"
    for the client/video in question — see fetch_po_token()/_fetch_po_token()
    in yt_dlp/extractor/youtube/_video.py (installed at
    .venv/Lib/site-packages/yt_dlp/extractor/youtube/_video.py in this repo,
    yt-dlp 2026.07.04):

      fetch_pot_policy = self._configuration_arg('fetch_pot', [''], ie_key=YoutubeIE)[0]
      if fetch_pot_policy not in ('never', 'auto', 'always'):
          fetch_pot_policy = 'auto'
      if (
          fetch_pot_policy == 'never'
          or (fetch_pot_policy == 'auto' and not kwargs.get('required', False))
      ):
          return None                                   # <-- no token even attempted

    The default fetch_pot_policy is 'auto', and 'required' is only True when
    the client's SUBS_PO_TOKEN_POLICY says so (yt_dlp/extractor/youtube/_base.py,
    WEB_PO_TOKEN_POLICIES / INNERTUBE_CLIENTS) or a per-video YouTube
    experiment flag ('xpe'/'xpv') is present on the caption baseUrl
    (_video.py, _extract_formats_and_subtitles: `requires_pot = any(e in
    qs.get('exp', []) for e in ('xpe', 'xpv')) or pot_policy.required`).
    In practice NONE of the default clients (android_vr, web, web_safari)
    set SUBS_PO_TOKEN_POLICY.required or .recommended to True by default
    (_base.py's WEB_PO_TOKEN_POLICIES sets `SubsPoTokenPolicy(required=False)`
    explicitly for web/web_safari, and android_vr inherits the library
    default `SubsPoTokenPolicy()` — also required=False, recommended=False —
    since it has no override in its INNERTUBE_CLIENTS entry). So on most
    videos (no per-video experiment flag), fetch_pot=always is the ONLY
    thing that makes yt-dlp attempt a Subs PO token fetch at all — 'auto'
    would skip it outright regardless of which client(s) got queried.

    Setting youtube:fetch_pot=always forces _fetch_po_token() past that
    'auto'+'not required' short-circuit so it always calls out to the
    configured provider (bgutil-http) for a Subs PO token at extract_info()
    time, which is when the caption URLs (with their query strings) are
    actually built (_video.py's process_language()/pot_params handling in
    _extract_formats_and_subtitles, confirmed by direct source read: the
    'pot' key lands in the `query` dict passed into process_language(), which
    calls `update_url_query(base_url, query)` synchronously while building
    `info['automatic_captions']` — there is no separate download-time step
    that adds pot= later. Concretely this rules out the "only attaches pot
    during yt-dlp's own subtitle-download step" theory: extract_info(...,
    download=False) already returns the final URL, pot param included, if a
    token was obtained. There is nothing to gain from switching to
    ydl.download()/process_ie_result(download=True) — the same cached
    caption-URL dict is reused either way.

    IMPORTANT — why a verbose (-v) run can show ZERO PO-token debug lines
    even when everything above is configured correctly: yt-dlp's PO-token
    provider framework (yt_dlp/extractor/youtube/pot/_director.py,
    initialize_pot_director()) only raises its internal logger to TRACE
    level (where the interesting lines live — "Attempting to fetch a PO
    Token from ... provider", "PO Token response from ... provider", "No PO
    Token providers were able to provide a valid PO Token", per-provider
    rejection reasons) if the extractor-arg `youtube:pot_trace=true` is ALSO
    set. Plain `verbose=True` only raises it to DEBUG, which is one level
    less verbose than TRACE (yt_dlp/extractor/youtube/pot/_provider.py:
    `TRACE = 0, DEBUG = 10, ...`; the trace()-gated log lines require
    `log_level <= LogLevel.TRACE`, i.e. exactly TRACE, not merely DEBUG).
    So `verbose=True` alone can legitimately produce a debug log with the
    provider *registration* banner ("PO Token Providers: bgutil:http-...")
    — that one line is an unconditional `.debug()` call made once at
    director init (_director.py ~line 408) — but nothing else, even though a
    fetch is being attempted and may be failing/rejected/succeeding under
    the hood. Set YTDLP_POT_TRACE=1 (see below) to add `pot_trace=true` and
    get the real per-request trace lines.

    UPDATE -- confirmed root cause via live reproduction (not just static
    analysis): standing up our own bgutil-ytdlp-pot-provider server (v1.3.1)
    and a fresh yt-dlp venv in a Linux sandbox (this required downgrading
    curl_cffi to the 0.15.x line -- curl_cffi 0.16.0 is NOT supported by
    this yt-dlp version and `--list-impersonate-targets` would not show real
    targets until we downgraded), then running with
    extractor_args={"youtube": {"fetch_pot": ["always"], "pot_trace": ["true"]}}
    to get real per-request PO-token trace lines, we caught the actual
    rejection live:

      PO Token Provider "bgutil:http" rejected this request, trying next
      available provider. Reason: Client "ANDROID_VR" is not supported by
      bgutil:http. Supported clients: WEB, MWEB, TVHTML5,
      WEB_EMBEDDED_PLAYER, WEB_CREATOR, WEB_REMIX, TVHTML5_SIMPLY,
      TVHTML5_SIMPLY_EMBEDDED_PLAYER

    So fetch_pot=always was correctly forcing the ATTEMPT (as the analysis
    above concludes), but the attempt was always being made on behalf of
    yt-dlp's default player_client -- ANDROID_VR -- which is simply not one
    of the clients bgutil:http's PO-token provider supports (notably absent:
    android_vr and web_safari, the two clients yt-dlp queries by default).
    Every attempt was rejected before a token could ever be returned, which
    is why no `pot=` param was ever observed on caption URLs regardless of
    fetch_pot policy.

    The fix, also confirmed live: add `"player_client": ["web", "android_vr"]`
    to this extractor_args dict. This forces yt-dlp to also query the `web`
    client, whose Subs PO-token request bgutil:http DOES accept, while
    keeping `android_vr` in the client list so real video formats remain
    available without needing a token (avoids changing existing format-
    resolution behavior). With this combo in place in the sandbox, `pot=`
    appeared in the caption URL and `info.get("formats")` returned 27 real
    entries (not empty) -- i.e. both the caption-token path and the format
    path worked simultaneously.
    """
    args = {
        "youtubepot-bgutilhttp": {"base_url": [YTDLP_POT_SERVER_URL]},
        "youtube": {"fetch_pot": ["always"], "player_client": ["web", "android_vr"]},
    }
    if _POT_TRACE:
        args["youtube"]["pot_trace"] = ["true"]
    return args


_POT_SERVER_HEALTH_CHECKED = False
_POT_SERVER_AVAILABLE = False


def _check_pot_server_once() -> bool:
    """Best-effort, short-timeout health check against the bgutil PO token server.

    Logs exactly one warning (not per-request) if the server isn't reachable,
    and never raises — an unreachable PO token server is a clean, expected-
    possible state (same as "no API key configured" elsewhere in this
    codebase), not a scanner failure. The bgutil HTTP provider itself exposes
    GET /ping (see getpot_bgutil_http.py's _check_server_availability), so we
    reuse that same endpoint here.
    """
    global _POT_SERVER_HEALTH_CHECKED, _POT_SERVER_AVAILABLE
    if _POT_SERVER_HEALTH_CHECKED:
        return _POT_SERVER_AVAILABLE
    _POT_SERVER_HEALTH_CHECKED = True
    try:
        with urllib.request.urlopen(f"{YTDLP_POT_SERVER_URL}/ping", timeout=2) as resp:
            resp.read()
        _POT_SERVER_AVAILABLE = True
    except Exception as e:
        _POT_SERVER_AVAILABLE = False
        _log.warning(
            "bgutil PO-token server not reachable at %s/ping (%s). YouTube "
            "transcript fetches that require a PO token will be skipped "
            "until it's running. Start it with one of:\n"
            "  Docker:  docker run --name bgutil-provider -d --init -p 4416:4416 brainicism/bgutil-ytdlp-pot-provider\n"
            "  Node.js: cd bgutil-ytdlp-pot-provider\\server && npm ci && npx tsc && node build\\main.js\n"
            "See sentiment-scanner/README.md for full setup instructions. "
            "Set YTDLP_POT_SERVER_URL if the server runs elsewhere.",
            YTDLP_POT_SERVER_URL, e,
        )
    return _POT_SERVER_AVAILABLE

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
        "remote_components": ["ejs:github"],
        "extractor_args": _pot_extractor_args(),
        # Avoids a hard ExtractorError("Requested format is not available")
        # if format/JS-signature resolution fails for one of the queried
        # player_clients (e.g. no working JS runtime for 'web'). Harmless
        # here since extract_flat=True never resolves real formats anyway.
        "ignore_no_formats_error": True,
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
            _log.warning("YouTube search failed for query %r: %s", query, e)
            return []


_PO_TOKEN_NOTICE_LOGGED = False


def _log_po_token_notice_once() -> None:
    """One-time startup note explaining the PO-token limitation.

    This is not a bug in this scanner — it's an accurate description of a
    yt-dlp-documented YouTube requirement. As of yt-dlp's own PO Token Guide
    (https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide), the 'web' client
    (which is what produces most 'a.en'/json3 automatic-caption URLs) requires
    a valid Proof-of-Origin (PO) token for Subs (subtitle/timedtext) requests,
    separately from and in addition to TLS/browser impersonation. Without a
    PO token, YouTube will keep returning HTTP 429/403 on caption fetches
    regardless of how convincing the impersonated TLS fingerprint is.
    Fixing this requires a PO token provider plugin — e.g.
    bgutil-ytdlp-pot-provider (https://github.com/Brainicism/bgutil-ytdlp-pot-provider),
    which is the yt-dlp-maintainer-endorsed option — configured via yt-dlp's
    plugin mechanism. That's a real dependency decision (it runs a small local
    token-generation service), not something this scanner should silently
    install. Until one is configured, YouTube transcript-based sentiment will
    keep failing on videos that require a PO token for subtitles.
    """
    global _PO_TOKEN_NOTICE_LOGGED
    if _PO_TOKEN_NOTICE_LOGGED:
        return
    _PO_TOKEN_NOTICE_LOGGED = True
    _log.warning(
        "YouTube automatic-caption fetches may require a PO (Proof-of-Origin) "
        "token in addition to browser impersonation — this is a documented "
        "YouTube/yt-dlp requirement (see "
        "https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide), not a bug. "
        "If transcript fetches keep failing with HTTP 429/403 even though "
        "`yt-dlp --list-impersonate-targets` shows working targets, the fix "
        "is to configure a PO token provider plugin (e.g. "
        "bgutil-ytdlp-pot-provider: "
        "https://github.com/Brainicism/bgutil-ytdlp-pot-provider). Until "
        "that's set up, YouTube transcript-based sentiment will keep failing "
        "for videos/clients that require a PO token for subtitles."
    )


def _fetch_caption_payload(ydl, cap: dict) -> Optional[bytes]:
    """Fetch a single caption/timedtext URL's raw bytes.

    YouTube's timedtext endpoint now bot-detects plain HTTP clients and
    returns HTTP 429 for most automatic-caption URLs unless the request is
    made with browser impersonation (TLS/HTTP fingerprinting) — yt-dlp
    surfaces this via the cap dict's 'impersonate' flag. Route the request
    through yt-dlp's own networking stack (which supports impersonation via
    curl_cffi) instead of raw urllib, which cannot impersonate and will
    reliably get 429'd. Note: cap['__yt_dlp_client'] (e.g. "android_vr") is
    the YouTube *API* client that produced this URL, not a browser
    impersonation target name — don't pass it as the impersonate client, let
    curl_cffi pick from whatever browser profiles it has compiled support
    for on this platform. Falls back to plain urllib for caption formats
    that don't require impersonation.

    An empty ImpersonateTarget() is intentional and correct here, not a bug:
    yt-dlp's own extractors (e.g. instagram.py, generic.py) use exactly this
    "wildcard" pattern to mean "impersonate with whatever profile is
    available." Internally, yt-dlp's curl_cffi request handler sorts its
    supported-target map to prefer non-deprioritized, desktop, and newest-
    version targets first (see yt_dlp/networking/_curlcffi.py,
    _SUPPORTED_IMPERSONATE_TARGET_MAP's sort key), so an empty target
    resolves to a modern, reliable Chrome/Firefox/Safari profile — not to an
    ancient one. So a passing impersonated request that still gets HTTP 429
    is NOT explained by a bad/missing impersonation target.

    What *does* explain a 429 after a successful impersonated request: as of
    yt-dlp's PO Token Guide
    (https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide), YouTube's 'web'
    client — which is what serves most automatic-caption ('a.en') json3
    URLs — now requires a Proof-of-Origin (PO) token for Subs requests,
    independent of TLS/browser fingerprinting. Impersonation alone cannot
    satisfy that requirement; only a PO token (typically from a provider
    plugin such as bgutil-ytdlp-pot-provider) can. See
    _log_po_token_notice_once() below.
    """
    url = cap["url"]
    needs_impersonate = bool(cap.get("impersonate"))

    if needs_impersonate:
        # Cheap, one-time, short-timeout heads-up if the bgutil PO token
        # server isn't running -- doesn't block or fail the fetch itself
        # (the yt-dlp plugin does its own availability check/caching and
        # will just fail to supply a token, which surfaces below as the
        # existing PO-token warning path).
        _check_pot_server_once()

        from yt_dlp.networking.exceptions import (
            HTTPError,
            NoSupportingHandlers,
            UnsupportedRequest,
        )

        try:
            from yt_dlp.networking.common import Request
            from yt_dlp.networking.impersonate import ImpersonateTarget
            req = Request(url, extensions={"impersonate": ImpersonateTarget()})
            resp = ydl.urlopen(req)
            return resp.read()
        except (UnsupportedRequest, NoSupportingHandlers) as e:
            # These are raised when NO handler can satisfy the 'impersonate'
            # extension at all — i.e. curl_cffi/its impersonation backend
            # genuinely isn't usable in this environment. This is the one
            # case where the old generic message was actually correct.
            _log.warning(
                "YouTube transcript fetch requires browser impersonation, "
                "but no impersonation-capable backend is available: %s. "
                "Run `yt-dlp --list-impersonate-targets` to check what's "
                "available in this environment (needs curl_cffi with a "
                "working compiled extension for this platform/Python "
                "version).",
                e,
            )
            return None
        except HTTPError as e:
            # The request WAS made with browser impersonation and yt-dlp had
            # no trouble selecting/using an impersonation backend — this is
            # YouTube itself rejecting the (successfully impersonated)
            # request, most likely because it also requires a PO token for
            # this client/endpoint. Do not blame the impersonation backend.
            status = getattr(getattr(e, "response", None), "status", None) or getattr(e, "code", None)
            _log.warning(
                "YouTube transcript fetch was made WITH browser impersonation "
                "(impersonation backend is working) but YouTube still "
                "rejected it: HTTP %s for %s. This is not an impersonation "
                "problem — it is most likely YouTube requiring a PO "
                "(Proof-of-Origin) token for automatic-caption/subtitle "
                "requests, which impersonation cannot provide.",
                status,
                url,
            )
            _log_po_token_notice_once()
            return None
        except Exception as e:
            _log.warning(
                "YouTube transcript fetch failed during an impersonated "
                "request (not clearly an impersonation-availability issue "
                "nor a plain HTTP error): %s",
                e,
            )
            return None

    for attempt in range(3):
        try:
            resp = urllib.request.urlopen(url, timeout=15)
            return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            _log.warning("YouTube transcript fetch failed (HTTP %s) for %s", e.code, url)
            return None
        except Exception as e:
            _log.warning("YouTube transcript fetch failed: %s", e)
            return None
    return None


def _fetch_transcript(video_id: str) -> Optional[str]:
    """Get auto-captions transcript for a video.

    Returns plain text transcript, or None if unavailable.
    """
    try:
        import yt_dlp
    except ImportError:
        _log.warning("YouTube scanner skipped: yt-dlp not installed.")
        return None

    try:
        ydl_config = {
            "quiet": True,
            "skip_download": True,
            "writesubtitles": False,
            "writeautomaticsub": True,
            "subtitleslangs": ["en"],
            "remote_components": ["ejs:github"],
            "extractor_args": _pot_extractor_args(),
            # Avoids a hard ExtractorError("Requested format is not
            # available") if format/JS-signature resolution fails for one of
            # the queried player_clients (e.g. 'web' with no working JS
            # runtime/EJS setup) -- we only need auto-captions here
            # (skip_download=True), so a missing real format must not be
            # fatal.
            "ignore_no_formats_error": True,
        }
        if _NODE_PATH:
            ydl_config["js_runtimes"] = {"node": {"path": _NODE_PATH}}

        with yt_dlp.YoutubeDL(ydl_config) as ydl:
            info = ydl.extract_info(video_id, download=False)
            if not info:
                return None

            # Try automatic captions first
            auto = info.get("automatic_captions", {}) or {}
            for lang_key in ["en", "a.en", "en-US", "en-GB"]:
                caps = auto.get(lang_key, [])
                if not caps:
                    continue
                # Prefer json3 format
                cap = None
                for c in caps:
                    if c.get("ext") == "json3":
                        cap = c
                        break
                if not cap:
                    for c in caps:
                        if c.get("ext") in ("srv1", "srv2", "vtt"):
                            cap = c
                            break
                if not cap:
                    continue

                raw = _fetch_caption_payload(ydl, cap)
                if not raw:
                    continue
                try:
                    data = json.loads(raw.decode("utf-8"))
                except Exception as e:
                    _log.warning("YouTube transcript payload wasn't valid json3: %s", e)
                    continue
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
    except Exception as e:
        _log.warning("YouTube transcript extraction failed for %s: %s", video_id, e)
        return None

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