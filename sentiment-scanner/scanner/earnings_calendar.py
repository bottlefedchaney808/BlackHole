"""Live earnings calendar — stockanalysis.com scrape with in-memory TTL
cache and static-calendar fallback.

stockanalysis.com is this project's only sanctioned non-ThetaData data
source (see requirements.txt). Its /earnings/ page is server-rendered
Next.js with the calendar embedded in a __NEXT_DATA__ JSON script tag —
no auth, no JS execution required to read it.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

CALENDAR_URL = "https://stockanalysis.com/earnings/"
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


def _fetch_raw() -> Dict[str, str]:
    """Fetch and parse the live calendar page. Returns {} on any failure
    (network, missing script tag, malformed JSON) so callers can fall
    back to the static calendar cleanly."""
    try:
        req = urllib.request.Request(
            CALENDAR_URL, headers={"User-Agent": "Mozilla/5.0"}
        )
        resp = urllib.request.urlopen(req, timeout=15)
        html = resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return {}

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
