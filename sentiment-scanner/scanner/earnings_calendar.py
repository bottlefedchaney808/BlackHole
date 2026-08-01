"""Live earnings calendar — stockanalysis.com scrape with in-memory TTL
cache and static-calendar fallback.

stockanalysis.com is this project's only sanctioned non-ThetaData data
source (see requirements.txt). Its calendar page used to be server-rendered
Next.js with the data embedded in a __NEXT_DATA__ JSON script tag; the site
has since migrated to SvelteKit, moved the page to /stocks/earnings-calendar/,
and now embeds the calendar as an inline JS object literal (unquoted keys,
e.g. ``{date:"2026-08-10",day:"Monday",symbols:[{s:"AXSM",...}, ...]}``)
inside a plain ``<script>`` tag rather than a JSON-typed one. It's not valid
JSON, so it's pulled out with a targeted regex (`_extract_sveltekit_entries`)
instead of `json.loads`. The old __NEXT_DATA__ parser is kept as a fallback
in case the site format changes again. No auth or JS execution is required
either way.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

CALENDAR_URL = "https://stockanalysis.com/stocks/earnings-calendar/"
CACHE_TTL_SECONDS = 60 * 60  # 60 minutes

# Key candidates the extractor checks for, since the exact __NEXT_DATA__
# schema isn't guaranteed to stay fixed across site updates.
_SYMBOL_KEYS = ("symbol", "s", "ticker")
_DATE_KEYS = ("date", "reportDate", "d")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)

# Current site markup: SvelteKit hydration payload with per-day blocks like
# {date:"2026-08-10",day:"Monday",symbols:[{s:"AXSM",n:"Axsome
# Therapeutics",...}, ...]}. Keys are unquoted (not JSON), so entries are
# pulled out with regex rather than parsed as a JSON tree.
_DAY_BLOCK_RE = re.compile(
    r'date:"(\d{4}-\d{2}-\d{2})",day:"[^"]*",symbols:\[(.*?)\]\}',
    re.DOTALL,
)
_SYMBOL_IN_BLOCK_RE = re.compile(r's:"([A-Z.\-]+)"')

_cache: Dict[str, object] = {"ts": 0.0, "data": {}}


def _extract_calendar_entries(next_data: dict) -> Dict[str, str]:
    """Recursively walk a parsed __NEXT_DATA__ blob for ticker/date pairs.

    Looks for any dict that has one of _SYMBOL_KEYS and one of
    _DATE_KEYS, resilient to not knowing the exact list-nesting shape.
    """
    found: Dict[str, str] = {}

    def _walk(node: object) -> None:
        if isinstance(node, dict):
            sym = next((node[k] for k in _SYMBOL_KEYS if k in node), None)
            dt = next((node[k] for k in _DATE_KEYS if k in node), None)
            if isinstance(sym, str) and isinstance(dt, str) and sym and dt:
                date_part = dt[:10]
                if _DATE_RE.match(date_part):
                    found[sym.upper()] = date_part
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(next_data)
    return found


def _extract_sveltekit_entries(html: str) -> Dict[str, str]:
    """Recursively-free regex extractor for the current site markup.

    The calendar page now embeds its data as a SvelteKit hydration payload:
    an inline JS object literal (unquoted keys, bare numbers like ``-.86``)
    rather than a JSON script tag, shaped like::

        {date:"2026-08-10",day:"Monday",symbols:[{s:"AXSM", ...}, ...]}

    Since it isn't valid JSON, pull ticker/date pairs out directly with
    regex instead of trying to ``json.loads`` it.
    """
    found: Dict[str, str] = {}
    for m in _DAY_BLOCK_RE.finditer(html):
        date_str, body = m.group(1), m.group(2)
        for sm in _SYMBOL_IN_BLOCK_RE.finditer(body):
            found[sm.group(1).upper()] = date_str
    return found


def _fetch_raw() -> Dict[str, str]:
    """Fetch and parse the live calendar page. Returns {} on any failure
    (network, missing data, malformed markup) so callers can fall back to
    the static calendar cleanly."""
    try:
        req = urllib.request.Request(
            CALENDAR_URL, headers={"User-Agent": "Mozilla/5.0"}
        )
        resp = urllib.request.urlopen(req, timeout=15)
        html = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return {}

    # Primary: current site markup (SvelteKit inline payload, no JSON
    # script tag). Tried first since this is what the live site serves now.
    try:
        sveltekit_entries = _extract_sveltekit_entries(html)
    except Exception:
        sveltekit_entries = {}
    if sveltekit_entries:
        return sveltekit_entries

    # Fallback: legacy __NEXT_DATA__ JSON script tag, kept in case the site
    # reverts or serves a different page format for some requests.
    match = _NEXT_DATA_RE.search(html)
    if not match:
        return {}

    try:
        next_data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}

    try:
        return _extract_calendar_entries(next_data)
    except Exception:
        return {}


def fetch_earnings_calendar() -> Dict[str, str]:
    """Return {TICKER: 'YYYY-MM-DD'} for the live earnings calendar.

    Cached in-memory for CACHE_TTL_SECONDS. Returns {} (never raises) on
    any fetch/parse failure.
    """
    now = time.time()
    if _cache["data"] and (now - float(_cache["ts"])) < CACHE_TTL_SECONDS:
        return _cache["data"]  # type: ignore[return-value]

    data = _fetch_raw()
    if data:
        _cache["ts"] = now
        _cache["data"] = data
    return data


def upcoming_earnings(
    days: int = 7,
    static_fallback: Optional[Dict[str, str]] = None,
) -> List[Tuple[str, str]]:
    """Return [(ticker, date), ...] for earnings in the next *days* days.

    Merges the live calendar with *static_fallback*; the live calendar
    takes precedence for tickers present in both. Sorted by date.
    """
    live = fetch_earnings_calendar()
    merged: Dict[str, str] = dict(static_fallback or {})
    merged.update(live)

    today = datetime.now(timezone.utc).date()
    horizon = today + timedelta(days=days)

    entries: List[Tuple[str, str]] = []
    for ticker, date_str in merged.items():
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if today <= d <= horizon:
            entries.append((ticker, date_str))

    entries.sort(key=lambda e: e[1])
    return entries


def format_earnings_digest(
    entries: List[Tuple[str, str]], days: int = 7, live: bool = True,
) -> str:
    """One-line digest string for console output.

    *live* should reflect whether the live calendar had any data at the
    time of the call. When False, a "[STATIC FALLBACK]" tag is prepended
    so degraded state (broken/empty live scrape) is visible in every
    cycle's console output, not just discoverable via a one-time manual
    check.
    """
    prefix = "  Upcoming Earnings" if live else "  Upcoming Earnings [STATIC FALLBACK]"
    if not entries:
        return f"{prefix} ({days}d): none scheduled"
    parts = [f"{ticker} ({date})" for ticker, date in entries]
    return f"{prefix} ({days}d): " + ", ".join(parts)
