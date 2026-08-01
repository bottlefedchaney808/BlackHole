# Wire Unused Sentiment-Scanner Modules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire `earnings_scanner.py`, `sector_rotation.py`, and `report.py` — three fully-built but never-called modules — into `sentiment-scanner/main.py`'s live scan loop, replacing the earnings scanner's stale hardcoded calendar with a live data source along the way.

**Architecture:** Add a new `scanner/earnings_calendar.py` data-source module (stockanalysis.com scrape, cached, with static-dict fallback). Make `earnings_scanner.py` consult it. Extend `CorrelationEngine` with a 7th scanner slot (earnings) following its existing `record_x`/`_x_module()` pattern. Wire the earnings scanner into `main.py`'s per-ticker loop as scanner #7, alongside a once-per-cycle earnings digest. Sector rotation gets its own standalone launcher script, invoked from `main.py` via an interactive (TTY-gated) prompt rather than the per-ticker loop. `report.py`'s `ScannerReport` gets wired to an end-of-cycle prompt that consumes the raw scanner-result objects already flowing into `CorrelationEngine`.

**Tech Stack:** Python 3.11, stdlib only for the new calendar fetch (`urllib.request`, `re`, `json` — matching `scanner/youtube.py`'s existing no-new-dependency pattern), pytest + `unittest.mock.monkeypatch` for tests (existing project convention).

## Global Constraints

- No new third-party dependencies — the calendar fetch uses `urllib.request`, `re`, `json` (stdlib), matching how `youtube.py` already avoids `httpx`/`requests`.
- No live network calls in the test suite — every test that touches `earnings_calendar.fetch_earnings_calendar`, `EarningsScanner.get_earnings_date`, or any options scanner must mock the network/API boundary. This matches the existing convention in `tests/test_earnings_scanner.py` and `tests/test_sector_rotation.py`.
- Respect the project's data-source policy (see `sentiment-scanner/requirements.txt` header): ThetaData is the sole market-data source; stockanalysis.com is the only sanctioned outside fallback. The new earnings calendar must use stockanalysis.com, not a third-party earnings-calendar API.
- All new interactive prompts (`_prompt_yes_no`) must return `False` without blocking when `sys.stdin.isatty()` is `False`, so scheduled/cron/CI runs of `main.py` never hang.
- Do not change `scanner/report.py`'s rendering code, `scanner/sector_rotation.py`'s ranking algorithm, or `scanner/earnings_scanner.py`'s IV-premium math — this plan only wires existing logic to new callers.
- Every new/modified function needs a docstring one-liner consistent with the file's existing style (all touched files already do this).

---

## CARL Review (Round 1) — Resolutions

This plan was reviewed adversarially (CARL Mode A/SPEC, fresh subagent reviewer) after the initial draft. Verdict: `NEEDS_REVISION` → revised below. Full findings ledger in the review record; summary of what changed:

- **R1-F1 (critical, fixed):** `_raw_result_to_dict` (Task 8) now uses `dataclasses.asdict()` instead of `vars()`, so nested dataclass fields (e.g. `UnusualOiScan.top_strikes: List[OiStrike]`) convert to plain dicts recursively. The shallow-copy version would have crashed report generation with `AttributeError` the first time a ticker had unusual-OI strikes.
- **R1-F2 (major, accepted as a documented limitation, not silently shipped):** The PDF report will not visually render earnings-vol data — `report.py`'s dashboard has no earnings card, and this plan's scope intentionally doesn't touch `report.py`'s rendering. Earnings data is still captured into the report object (harmless, forward-compatible) via `add_ticker_results`, it just won't appear on the page. **Flagged to the user** — if a rendered earnings card matters now rather than later, that's a scope addition to negotiate separately, not something auto-decided here.
- **R1-F3 (major, fixed):** `format_earnings_digest` (Task 1) now takes a `live: bool` parameter and prepends `[STATIC FALLBACK]` when the live stockanalysis.com scrape returned no data, so a permanently broken scrape (schema drift after ship) is visible in every cycle's console output — not just at Task 1's one-time manual-verification step.
- **R1-F4 (major, fixed):** `_launch_sector_rotation` (Task 7) now uses the same `timeout=1800, capture_output=True, text=True` pattern as the existing `_launch_vol_suite`, instead of a bare blocking `subprocess.run` with no timeout — a hung/unreachable ThetaData connection during the 15-ETF price-history fetch could otherwise block `main.py` from ever starting its scan loop.
- **R1-F5 (major, resolved — user chose option B):** The PDF-report prompt no longer fires every scan cycle. It now prompts once, at shutdown only (`KeyboardInterrupt` path and the `--no-loop` early-return path), matching the sector-rotation prompt's cadence. `_maybe_build_report` is called once, at the same two exit points as `_launch_sector_rotation`, using the *last completed cycle's* `cycle_raw` rather than being invoked inline after every `scan_trending()` call. See Task 8, revised below.
- **R1-F6 (minor, fixed):** Task 4's import instructions were a confusing two-step "add a line, then remove it" — now a single direct edit to the existing `close_td` import line.
- **R1-F7 (minor, fixed):** Task 4 now also updates `main.py`'s module docstring and startup banner to mention the 7th (earnings) scanner, so console output doesn't undercount what's actually running.
- **Self-caught during verification (not from R1):** Task 4's own test for `scan_trending` was monkeypatching `main_mod.upcoming_earnings`/`format_earnings_digest` before those imports exist in `main.py` (they're added in Task 5) — `monkeypatch.setattr` would have raised `AttributeError` on a nonexistent module attribute, breaking Task 4's TDD flow. Removed those two lines from Task 4's test; Task 5's own test class already covers the digest behavior correctly.

Test counts in each task's "Run tests to verify they pass" step were recomputed after these additions (Task 1: 18 → 21 tests; Task 4: unchanged at 6; Task 5: 8 → 9; Task 7: 14 → 16; Task 8: 20 → 23).

---

### Task 1: Live Earnings Calendar Data Source

**Files:**
- Create: `sentiment-scanner/scanner/earnings_calendar.py`
- Test: `sentiment-scanner/tests/test_earnings_calendar.py`

**Interfaces:**
- Consumes: nothing from other tasks (new, standalone module).
- Produces:
  - `fetch_earnings_calendar() -> Dict[str, str]` — `{TICKER: "YYYY-MM-DD"}`, `{}` on any failure.
  - `upcoming_earnings(days: int = 7, static_fallback: Optional[Dict[str, str]] = None) -> List[Tuple[str, str]]`
  - `format_earnings_digest(entries: List[Tuple[str, str]], days: int = 7, live: bool = True) -> str` — `live=False` prepends a `[STATIC FALLBACK]` tag so degraded state (live scrape broken/empty) is visible in console output every cycle, not just at ship time (CARL R1-F3).
  - `_extract_calendar_entries(next_data: dict) -> Dict[str, str]` (used directly by tests)
  - Module-level `_cache: Dict[str, object]` with keys `"ts"` (float) and `"data"` (dict) — tests reset this between runs.

- [ ] **Step 1: Write the failing tests**

Create `sentiment-scanner/tests/test_earnings_calendar.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd sentiment-scanner && python -m pytest tests/test_earnings_calendar.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scanner.earnings_calendar'`

- [ ] **Step 3: Implement `scanner/earnings_calendar.py`**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd sentiment-scanner && python -m pytest tests/test_earnings_calendar.py -v`
Expected: PASS (21 tests)

- [ ] **Step 5: Manually verify against the live site**

Run: `cd sentiment-scanner && python -c "from scanner.earnings_calendar import fetch_earnings_calendar as f; d = f(); print(len(d)); print(list(d.items())[:5])"`

If this prints `0` and an empty list, the live `__NEXT_DATA__` schema doesn't match `_SYMBOL_KEYS`/`_DATE_KEYS` (this was flagged as an open risk in the design — the schema couldn't be confirmed ahead of time). Inspect the actual response with:
`python -c "import urllib.request; print(urllib.request.urlopen('https://stockanalysis.com/earnings/').read().decode('utf-8', errors='ignore')[:3000])"`
and adjust `_SYMBOL_KEYS`/`_DATE_KEYS` (or `_NEXT_DATA_RE` if the script tag id differs) to match what's actually there, then re-run Step 4. The static-fallback path (Task 2) means the feature works either way — this step just confirms the live path is actually live.

- [ ] **Step 6: Commit**

```bash
cd sentiment-scanner
git add scanner/earnings_calendar.py tests/test_earnings_calendar.py
git commit -m "feat: add live earnings calendar data source (stockanalysis.com)"
```

---

### Task 2: Wire Live Calendar Into EarningsScanner

**Files:**
- Modify: `sentiment-scanner/scanner/earnings_scanner.py` (imports at top; `get_earnings_date` method, currently lines 79-85)
- Modify: `sentiment-scanner/tests/test_earnings_scanner.py` (add autouse network guard + new test class)

**Interfaces:**
- Consumes: `fetch_earnings_calendar()` from Task 1.
- Produces: `EarningsScanner.get_earnings_date(ticker: str) -> Optional[str]` now checks the live calendar before the static `EARNINGS_CALENDAR` dict (return type unchanged, callers unaffected).

- [ ] **Step 1: Add the network guard fixture and new tests (will fail against current code)**

In `sentiment-scanner/tests/test_earnings_scanner.py`, add this fixture near the top of the file, right after the existing imports and before the `mock_td` fixture:

```python
@pytest.fixture(autouse=True)
def _no_live_calendar(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default: live calendar returns nothing, so tests hit the static
    EARNINGS_CALENDAR fallback deterministically. Individual tests can
    override this with their own monkeypatch.setattr call."""
    monkeypatch.setattr(
        "scanner.earnings_scanner.fetch_earnings_calendar", lambda: {}
    )
```

Then add this new test class at the end of the file:

```python
class TestLiveCalendarPrecedence:
    """get_earnings_date() checks the live calendar before the static
    EARNINGS_CALENDAR fallback."""

    def test_live_date_wins_over_static(
        self, scanner: EarningsScanner, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "scanner.earnings_scanner.fetch_earnings_calendar",
            lambda: {"AAPL": "2099-01-01"},
        )
        assert scanner.get_earnings_date("AAPL") == "2099-01-01"

    def test_falls_back_to_static_when_live_empty(
        self, scanner: EarningsScanner,
    ) -> None:
        # _no_live_calendar autouse fixture already makes live return {}
        assert scanner.get_earnings_date("AAPL") == EARNINGS_CALENDAR["AAPL"]

    def test_falls_back_to_static_when_live_missing_ticker(
        self, scanner: EarningsScanner, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "scanner.earnings_scanner.fetch_earnings_calendar",
            lambda: {"MSFT": "2099-02-02"},
        )
        assert scanner.get_earnings_date("AAPL") == EARNINGS_CALENDAR["AAPL"]

    def test_live_lookup_is_case_insensitive(
        self, scanner: EarningsScanner, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "scanner.earnings_scanner.fetch_earnings_calendar",
            lambda: {"AAPL": "2099-01-01"},
        )
        assert scanner.get_earnings_date("aapl") == "2099-01-01"
```

- [ ] **Step 2: Run tests to verify the new class fails**

Run: `cd sentiment-scanner && python -m pytest tests/test_earnings_scanner.py -v`
Expected: FAIL — `AttributeError: <module 'scanner.earnings_scanner'> does not have the attribute 'fetch_earnings_calendar'` (raised by the autouse fixture's `monkeypatch.setattr` on every test in the file, since `fetch_earnings_calendar` isn't imported into `earnings_scanner` yet).

- [ ] **Step 3: Implement the live-first lookup**

In `sentiment-scanner/scanner/earnings_scanner.py`, add the import alongside the existing ones near the top of the file (after `from scanner.options_scanner_base import VolSuiteImporter, get_td`):

```python
from scanner.earnings_calendar import fetch_earnings_calendar
```

Replace the existing `get_earnings_date` method (currently):

```python
    def get_earnings_date(self, ticker: str) -> Optional[str]:
        """Return upcoming earnings date string (YYYY-MM-DD) for *ticker*.

        Uses a hardcoded lookup dict for well-known tickers.  Returns
        ``None`` for tickers not in the calendar.
        """
        return EARNINGS_CALENDAR.get(ticker.upper())
```

with:

```python
    def get_earnings_date(self, ticker: str) -> Optional[str]:
        """Return upcoming earnings date string (YYYY-MM-DD) for *ticker*.

        Checks the live stockanalysis.com calendar first; falls back to
        the static EARNINGS_CALENDAR dict if the ticker isn't in the
        live data (or the live fetch failed). Returns ``None`` if the
        ticker is in neither source.
        """
        ticker = ticker.upper()
        live = fetch_earnings_calendar()
        if ticker in live:
            return live[ticker]
        return EARNINGS_CALENDAR.get(ticker)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd sentiment-scanner && python -m pytest tests/test_earnings_scanner.py -v`
Expected: PASS (all existing tests + 4 new ones in `TestLiveCalendarPrecedence`)

- [ ] **Step 5: Commit**

```bash
cd sentiment-scanner
git add scanner/earnings_scanner.py tests/test_earnings_scanner.py
git commit -m "feat: EarningsScanner checks live calendar before static fallback"
```

---

### Task 3: Correlation Engine — Earnings Recording and Signals

**Files:**
- Modify: `sentiment-scanner/correlation/engine.py` (add `_earnings_module()` near line 39, `self._earnings` in `__init__` near line 58, `record_earnings` near line 110, signal block in `correlate_with_oi` near line 218, `earnings` block in `get_scanner_summary` near line 290)
- Test: `sentiment-scanner/tests/test_correlation_engine.py`

**Interfaces:**
- Consumes: `EarningsResult` dataclass and `HIGH_PREMIUM_THRESHOLD` constant from `scanner/earnings_scanner.py` (already exist, unmodified).
- Produces:
  - `CorrelationEngine.record_earnings(ticker: str, scan: object) -> None`
  - `correlate_with_oi(...)` may now append `"EARNINGS_VOL_PLUS_NARRATIVE"` (severity `"HIGH"`) or `"EARNINGS_VOL_PREMIUM"` (severity `"MEDIUM"`) to its signals list.
  - `get_scanner_summary(ticker)` return dict now includes an `"earnings"` key: `{"status": str, "premium_pct": float, "signal": str, "earnings_date": str}`.

- [ ] **Step 1: Write the failing tests**

Create `sentiment-scanner/tests/test_correlation_engine.py`:

```python
"""Tests for CorrelationEngine's earnings-vol recording and signals."""

from __future__ import annotations

import pytest

from correlation.engine import CorrelationEngine
from scanner.earnings_scanner import EarningsResult, HIGH_PREMIUM_THRESHOLD


def _narrative_scores(cns: int) -> dict:
    return {
        "war_score": 0.2,
        "contested_narrative_score": cns,
        "volume": 10,
        "thesis_ratio": 0.5,
        "pump_ratio": 0.1,
        "bullish_pct": 40.0,
        "bearish_pct": 30.0,
    }


def _earnings_result(premium_pct: float, error: str = None) -> EarningsResult:
    return EarningsResult(
        ticker="AAPL", spot=200.0, earnings_date="2026-08-05",
        expiry_before="20260731", expiry_after="20260815",
        iv_before_pct=25.0, iv_after_pct=25.0 + premium_pct,
        premium_pct=premium_pct,
        signal="HIGH" if premium_pct >= HIGH_PREMIUM_THRESHOLD else "LOW",
        num_strikes_before=4, num_strikes_after=4,
        timestamp="2026-07-31T12:00:00+00:00", error=error,
    )


def _engine_with_cns(cns: int) -> CorrelationEngine:
    """A fresh engine with enough narrative history for cns_current to
    resolve (get_narrative_trend needs >= 2 recorded points)."""
    engine = CorrelationEngine()
    engine.record_narrative("AAPL", _narrative_scores(cns))
    engine.record_narrative("AAPL", _narrative_scores(cns))
    return engine


class TestRecordEarnings:
    def test_stores_scan_for_ticker(self) -> None:
        engine = CorrelationEngine()
        scan = _earnings_result(6.0)
        engine.record_earnings("AAPL", scan)
        assert engine._earnings["AAPL"] is scan


class TestEarningsSignals:
    def test_high_premium_with_high_cns_triggers_plus_narrative(self) -> None:
        engine = _engine_with_cns(cns=60)
        engine.record_earnings("AAPL", _earnings_result(HIGH_PREMIUM_THRESHOLD))

        result = engine.correlate_with_oi("AAPL", {})

        assert "EARNINGS_VOL_PLUS_NARRATIVE" in result["signals"]
        assert result["severity"] == "HIGH"

    def test_high_premium_with_low_cns_triggers_premium_only(self) -> None:
        engine = _engine_with_cns(cns=20)
        engine.record_earnings("AAPL", _earnings_result(HIGH_PREMIUM_THRESHOLD))

        result = engine.correlate_with_oi("AAPL", {})

        assert "EARNINGS_VOL_PREMIUM" in result["signals"]
        assert "EARNINGS_VOL_PLUS_NARRATIVE" not in result["signals"]

    def test_low_premium_triggers_no_earnings_signal(self) -> None:
        engine = _engine_with_cns(cns=60)
        engine.record_earnings("AAPL", _earnings_result(1.0))

        result = engine.correlate_with_oi("AAPL", {})

        assert "EARNINGS_VOL_PREMIUM" not in result["signals"]
        assert "EARNINGS_VOL_PLUS_NARRATIVE" not in result["signals"]

    def test_errored_earnings_result_produces_no_signal_and_no_crash(self) -> None:
        engine = _engine_with_cns(cns=60)
        engine.record_earnings(
            "AAPL", _earnings_result(0.0, error="no_earnings_date")
        )

        result = engine.correlate_with_oi("AAPL", {})

        assert "EARNINGS_VOL_PREMIUM" not in result["signals"]
        assert "EARNINGS_VOL_PLUS_NARRATIVE" not in result["signals"]

    def test_no_earnings_recorded_produces_no_signal(self) -> None:
        engine = _engine_with_cns(cns=60)
        result = engine.correlate_with_oi("AAPL", {})
        assert "EARNINGS_VOL_PREMIUM" not in result["signals"]
        assert "EARNINGS_VOL_PLUS_NARRATIVE" not in result["signals"]


class TestScannerSummaryEarnings:
    def test_ok_scan_reports_status_ok(self) -> None:
        engine = CorrelationEngine()
        engine.record_earnings("AAPL", _earnings_result(6.0))

        summary = engine.get_scanner_summary("AAPL")

        assert summary["earnings"]["status"] == "ok"
        assert summary["earnings"]["premium_pct"] == 6.0
        assert summary["earnings"]["signal"] == "HIGH"
        assert summary["earnings"]["earnings_date"] == "2026-08-05"

    def test_errored_scan_reports_error_status(self) -> None:
        engine = CorrelationEngine()
        engine.record_earnings("AAPL", _earnings_result(0.0, error="no_spot"))

        summary = engine.get_scanner_summary("AAPL")

        assert "error: no_spot" in summary["earnings"]["status"]

    def test_no_scan_recorded_reports_error_status(self) -> None:
        engine = CorrelationEngine()
        summary = engine.get_scanner_summary("AAPL")
        assert "error" in summary["earnings"]["status"]
        assert summary["earnings"]["premium_pct"] == 0.0
        assert summary["earnings"]["signal"] == "UNKNOWN"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd sentiment-scanner && python -m pytest tests/test_correlation_engine.py -v`
Expected: FAIL with `AttributeError: 'CorrelationEngine' object has no attribute 'record_earnings'`

- [ ] **Step 3: Implement the engine changes**

In `sentiment-scanner/correlation/engine.py`, add this lazy-import helper after the existing `_disp_module()` function (currently ending around line 39):

```python
def _earnings_module():
    from scanner.earnings_scanner import EarningsResult, HIGH_PREMIUM_THRESHOLD
    return EarningsResult, HIGH_PREMIUM_THRESHOLD
```

In `CorrelationEngine.__init__`, add `self._earnings` alongside the other five per-scanner dicts:

```python
        self._disp: Dict[str, object] = {}
        self._earnings: Dict[str, object] = {}
```

After `record_dispersion` (currently the last of the six `record_x` methods), add:

```python
    def record_earnings(self, ticker: str, scan: object) -> None:
        self._earnings[ticker] = scan
```

In `correlate_with_oi`, after the "NEW: Vol Dispersion signal" block and before the "--- Composite severity ---" comment, add:

```python
        # --- NEW: Earnings-vol premium signal ---
        earn = self._earnings.get(ticker)
        if earn is not None and not getattr(earn, "error", None):
            earn_type, high_threshold = _earnings_module()
            if isinstance(earn, earn_type):
                if earn.premium_pct >= high_threshold and cns > 50:
                    signals.append("EARNINGS_VOL_PLUS_NARRATIVE")
                    severities.append("HIGH")
                elif earn.premium_pct >= high_threshold:
                    signals.append("EARNINGS_VOL_PREMIUM")
                    severities.append("MEDIUM")
```

In `get_scanner_summary`, add the earnings lookup alongside the other five near the top of the method:

```python
        earn = self._earnings.get(ticker)
        earn_err = getattr(earn, "error", None)
```

And add the `"earnings"` key to the returned dict (after `"dispersion"`):

```python
            "earnings": {
                "status": "ok" if earn and not earn_err else f"error: {earn_err}",
                "premium_pct": getattr(earn, "premium_pct", 0.0) if earn else 0.0,
                "signal": getattr(earn, "signal", "UNKNOWN") if earn else "UNKNOWN",
                "earnings_date": getattr(earn, "earnings_date", "") if earn else "",
            },
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd sentiment-scanner && python -m pytest tests/test_correlation_engine.py -v`
Expected: PASS (11 tests)

Also run the full existing suite to confirm nothing else broke:
Run: `cd sentiment-scanner && python -m pytest tests/ -v`
Expected: PASS (all tests, including Tasks 1-2's new files)

- [ ] **Step 5: Commit**

```bash
cd sentiment-scanner
git add correlation/engine.py tests/test_correlation_engine.py
git commit -m "feat: CorrelationEngine records earnings-vol scans and signals"
```

---

### Task 4: Wire Earnings Scanner Into main.py's Per-Ticker Loop

**Files:**
- Modify: `sentiment-scanner/main.py` (imports near line 25-37; `run_options_scanners` lines 175-233; `scan_trending` lines 247-267; both `scan_trending(...)` call sites in `main()`, lines 336 and 374)
- Test: `sentiment-scanner/tests/test_main.py` (new file)

**Interfaces:**
- Consumes: `scan_ticker`/`format_earnings_one` from `scanner/earnings_scanner.py` (existing), `get_td` from `scanner/options_scanner_base.py` (existing), `CorrelationEngine.record_earnings` from Task 3.
- Produces:
  - `run_options_scanners(ticker, engine, benchmark="SPY", skip_gex=False) -> Tuple[List[str], Dict[str, object]]` — return type changes from `List[str]` to a `(lines, raw)` tuple. `raw` keys: `"gex"`, `"unusual_oi"`, `"iv_rank"`, `"skew"`, `"max_pain"`, `"dispersion"`, `"earnings"` (values are the scan result object or `None`).
  - `scan_trending(...) -> Tuple[List[dict], Dict[str, Dict[str, object]]]` — return type changes from `List[dict]` (alerts) to `(alerts, cycle_raw)`, where `cycle_raw` maps ticker -> that ticker's `raw` dict from `run_options_scanners`.

- [ ] **Step 1: Write the failing tests**

Create `sentiment-scanner/tests/test_main.py`:

```python
"""Tests for main.py's scanner-loop wiring."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main as main_mod


class _FakeScan:
    """Minimal stand-in for a scanner result dataclass."""

    def __init__(self, tag: str, error=None) -> None:
        self.tag = tag
        self.error = error


def _fake_scanners():
    """Stand-in for main._import_scanners()'s 12-tuple return."""

    def scan_gex(ticker):
        return _FakeScan("gex")

    def fmt_gex(s):
        return f"  GEX:{s.tag}"

    def scan_oi(ticker):
        return _FakeScan("oi")

    def fmt_oi(s):
        return f"  OI:{s.tag}"

    def scan_iv(ticker):
        return _FakeScan("iv")

    def fmt_iv(s):
        return f"  IV:{s.tag}"

    def scan_skew(ticker):
        return _FakeScan("skew")

    def fmt_skew(s):
        return f"  SKEW:{s.tag}"

    def scan_pain(ticker):
        return _FakeScan("pain")

    def fmt_pain(s):
        return f"  PAIN:{s.tag}"

    def scan_disp(ticker, benchmark="SPY"):
        return _FakeScan("disp")

    def fmt_disp(s):
        return f"  DISP:{s.tag}"

    return (
        scan_gex, fmt_gex, scan_oi, fmt_oi, scan_iv, fmt_iv,
        scan_skew, fmt_skew, scan_pain, fmt_pain, scan_disp, fmt_disp,
    )


class TestRunOptionsScanners:
    def test_returns_lines_and_raw_for_all_seven_scanners(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(main_mod, "_import_scanners", _fake_scanners)
        monkeypatch.setattr(main_mod, "get_td", lambda: "FAKE_TD")
        monkeypatch.setattr(
            main_mod, "_scan_earnings_ticker",
            lambda ticker, td=None: _FakeScan("earn"),
        )
        monkeypatch.setattr(
            main_mod, "format_earnings_line", lambda r: f"  EARN:{r.tag}",
        )
        engine = MagicMock()

        lines, raw = main_mod.run_options_scanners("AAPL", engine)

        assert len(lines) == 7
        assert raw["gex"].tag == "gex"
        assert raw["unusual_oi"].tag == "oi"
        assert raw["iv_rank"].tag == "iv"
        assert raw["skew"].tag == "skew"
        assert raw["max_pain"].tag == "pain"
        assert raw["dispersion"].tag == "disp"
        assert raw["earnings"].tag == "earn"
        engine.record_gex.assert_called_once_with("AAPL", raw["gex"])
        engine.record_earnings.assert_called_once_with("AAPL", raw["earnings"])

    def test_skip_gex_leaves_gex_raw_none(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(main_mod, "_import_scanners", _fake_scanners)
        monkeypatch.setattr(main_mod, "get_td", lambda: "FAKE_TD")
        monkeypatch.setattr(
            main_mod, "_scan_earnings_ticker",
            lambda ticker, td=None: _FakeScan("earn"),
        )
        monkeypatch.setattr(
            main_mod, "format_earnings_line", lambda r: f"  EARN:{r.tag}",
        )
        engine = MagicMock()

        lines, raw = main_mod.run_options_scanners("AAPL", engine, skip_gex=True)

        assert raw["gex"] is None
        assert len(lines) == 6
        engine.record_gex.assert_not_called()

    def test_scanner_exception_produces_error_line_and_none_raw(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        scanners = list(_fake_scanners())

        def _boom(ticker):
            raise RuntimeError("boom")

        scanners[0] = _boom  # scan_gex
        monkeypatch.setattr(main_mod, "_import_scanners", lambda: tuple(scanners))
        monkeypatch.setattr(main_mod, "get_td", lambda: "FAKE_TD")
        monkeypatch.setattr(
            main_mod, "_scan_earnings_ticker",
            lambda ticker, td=None: _FakeScan("earn"),
        )
        monkeypatch.setattr(
            main_mod, "format_earnings_line", lambda r: f"  EARN:{r.tag}",
        )
        engine = MagicMock()

        lines, raw = main_mod.run_options_scanners("AAPL", engine)

        assert raw["gex"] is None
        assert any("GEX: ERROR" in line for line in lines)

    def test_earnings_scanner_exception_produces_error_line(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(main_mod, "_import_scanners", _fake_scanners)
        monkeypatch.setattr(main_mod, "get_td", lambda: "FAKE_TD")

        def _boom(ticker, td=None):
            raise RuntimeError("earnings api down")

        monkeypatch.setattr(main_mod, "_scan_earnings_ticker", _boom)
        engine = MagicMock()

        lines, raw = main_mod.run_options_scanners("AAPL", engine)

        assert raw["earnings"] is None
        assert any("EARN: ERROR" in line for line in lines)
        engine.record_earnings.assert_not_called()

    def test_earnings_none_result_skips_record_and_line(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(main_mod, "_import_scanners", _fake_scanners)
        monkeypatch.setattr(main_mod, "get_td", lambda: "FAKE_TD")
        monkeypatch.setattr(
            main_mod, "_scan_earnings_ticker",
            lambda ticker, td=None: None,
        )
        engine = MagicMock()

        lines, raw = main_mod.run_options_scanners("AAPL", engine)

        assert raw["earnings"] is None
        assert len(lines) == 6
        engine.record_earnings.assert_not_called()


class TestScanTrendingReturnsRaw:
    def test_returns_alerts_and_cycle_raw(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        st = MagicMock()
        st.get_trending.return_value = [{"symbol": "AAPL"}]
        engine = MagicMock()

        monkeypatch.setattr(main_mod, "scan_ticker", lambda st, t, e: None)
        monkeypatch.setattr(
            main_mod, "run_options_scanners",
            lambda ticker, engine, benchmark, skip_gex: (
                [f"  {ticker}: line"], {"gex": _FakeScan("gex")},
            ),
        )
        monkeypatch.setattr(main_mod, "_youtube_scan", lambda t: None)
        monkeypatch.setattr(main_mod.time, "sleep", lambda s: None)

        alerts, cycle_raw = main_mod.scan_trending(st, engine, skip_youtube=True)

        assert alerts == []
        assert cycle_raw == {"AAPL": {"gex": cycle_raw["AAPL"]["gex"]}}
        assert cycle_raw["AAPL"]["gex"].tag == "gex"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd sentiment-scanner && python -m pytest tests/test_main.py -v`
Expected: FAIL — `AttributeError: <module 'main'> does not have the attribute '_scan_earnings_ticker'` (and, once that's fixed by Step 3, `ValueError: too many values to unpack` on the `lines, raw = run_options_scanners(...)` calls until `scan_trending` is also updated).

- [ ] **Step 3: Implement the main.py changes**

Change the existing `scanner.options_scanner_base` import (currently line 31):

```python
from scanner.options_scanner_base import close_td
```

to:

```python
from scanner.options_scanner_base import close_td, get_td
```

Add these two new imports in `sentiment-scanner/main.py` alongside the existing `scanner.youtube` imports (after line 36 `_youtube_scan = scanner.youtube.scan_ticker`):

```python
from scanner.earnings_scanner import scan_ticker as _scan_earnings_ticker
from scanner.earnings_scanner import format_earnings_one as format_earnings_line
```

Update the module docstring (currently lines 2-8, which says "6 Options Scanners") and the startup banner print (currently line 329) so the console output accurately reflects the 7th scanner. Change the docstring's second sentence from:

```
Extended from the original StockTwits + CNS + deep-dive loop to run a full
suite of options scanners on every trending ticker: GEX, Unusual OI, IV Rank,
Skew, Max Pain, and Vol Dispersion — all piped into the correlation engine
for composite signals.
```

to:

```
Extended from the original StockTwits + CNS + deep-dive loop to run a full
suite of options scanners on every trending ticker: GEX, Unusual OI, IV Rank,
Skew, Max Pain, Vol Dispersion, and Earnings-Vol Premium — all piped into the
correlation engine for composite signals.
```

And change the banner line (currently `print(f"Options Scanners: GEX | Unusual OI | IV Rank | Skew | Max Pain | Vol Dispersion")`) to:

```python
    print(f"Options Scanners: GEX | Unusual OI | IV Rank | Skew | Max Pain | Vol Dispersion | Earnings")
```

Replace `run_options_scanners` (currently lines 175-233) in full:

```python
def run_options_scanners(ticker, engine, benchmark="SPY", skip_gex=False):
    """Run all 7 options scanners on a ticker and pipe results into the engine.

    Returns (lines, raw) where lines are formatted console strings and
    raw maps scanner name -> the scan result object (or None if that
    scanner errored or was skipped), for downstream reporting.
    """
    (scan_gex, fmt_gex, scan_oi, fmt_oi,
     scan_iv, fmt_iv, scan_skew, fmt_skew,
     scan_pain, fmt_pain,
     scan_disp, fmt_disp) = _import_scanners()

    results = []
    raw = {
        "gex": None, "unusual_oi": None, "iv_rank": None, "skew": None,
        "max_pain": None, "dispersion": None, "earnings": None,
    }

    # 1. GEX (most expensive — skip if flagged)
    if not skip_gex:
        try:
            gex = scan_gex(ticker)
            engine.record_gex(ticker, gex)
            raw["gex"] = gex
            results.append(fmt_gex(gex))
        except Exception as e:
            results.append(f"  {ticker:6s} | GEX: ERROR — {e}")

    # 2. Unusual OI
    try:
        oi = scan_oi(ticker)
        engine.record_oi(ticker, oi)
        raw["unusual_oi"] = oi
        results.append(fmt_oi(oi))
    except Exception as e:
        results.append(f"  {ticker:6s} | OI: ERROR — {e}")

    # 3. IV Rank
    try:
        iv = scan_iv(ticker)
        engine.record_iv(ticker, iv)
        raw["iv_rank"] = iv
        results.append(fmt_iv(iv))
    except Exception as e:
        results.append(f"  {ticker:6s} | IV: ERROR — {e}")

    # 4. Skew
    try:
        skew = scan_skew(ticker)
        engine.record_skew(ticker, skew)
        raw["skew"] = skew
        results.append(fmt_skew(skew))
    except Exception as e:
        results.append(f"  {ticker:6s} | SKEW: ERROR — {e}")

    # 5. Max Pain
    try:
        pain = scan_pain(ticker)
        engine.record_pain(ticker, pain)
        raw["max_pain"] = pain
        results.append(fmt_pain(pain))
    except Exception as e:
        results.append(f"  {ticker:6s} | PAIN: ERROR — {e}")

    # 6. Vol Dispersion
    try:
        disp = scan_disp(ticker, benchmark=benchmark)
        engine.record_dispersion(ticker, disp)
        raw["dispersion"] = disp
        results.append(fmt_disp(disp))
    except Exception as e:
        results.append(f"  {ticker:6s} | DISP: ERROR — {e}")

    # 7. Earnings-vol premium
    try:
        earn = _scan_earnings_ticker(ticker, td=get_td())
        if earn is not None:
            engine.record_earnings(ticker, earn)
            raw["earnings"] = earn
            results.append(format_earnings_line(earn))
    except Exception as e:
        results.append(f"  {ticker:6s} | EARN: ERROR — {e}")

    return results, raw
```

Replace `scan_trending` (currently lines 247-267) in full:

```python
def scan_trending(st, engine, benchmark="SPY", skip_gex=False, skip_youtube=False):
    trending = st.get_trending()
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Trending: {len(trending)} symbols")
    alerts = []
    cycle_raw = {}
    for t in trending[:config.MAX_TICKERS_TO_SCAN]:
        ticker = t["symbol"]
        result = scan_ticker(st, ticker, engine)
        if result:
            alerts.append(result)
        # Run options scanners on EVERY trending ticker, not just above-threshold
        scanner_lines, scanner_raw = run_options_scanners(ticker, engine, benchmark, skip_gex)
        for line in scanner_lines:
            print(line)
        cycle_raw[ticker] = scanner_raw
        if not skip_youtube:
            yt_result = _youtube_scan(ticker)
            if yt_result:
                yt_line = yt_format(yt_result)
                if yt_line:
                    print(yt_line)
        time.sleep(0.5)  # brief pause between tickers
    return alerts, cycle_raw
```

Update both call sites in `main()`. The first (currently line 336):

```python
        alerts = scan_trending(st, engine, args.benchmark, args.skip_gex, args.skip_youtube)
```

becomes:

```python
        alerts, cycle_raw = scan_trending(st, engine, args.benchmark, args.skip_gex, args.skip_youtube)
```

The second (currently line 374, inside the `while True:` loop):

```python
            alerts = scan_trending(st, engine, args.benchmark, args.skip_gex, args.skip_youtube)
```

becomes:

```python
            alerts, cycle_raw = scan_trending(st, engine, args.benchmark, args.skip_gex, args.skip_youtube)
```

(`cycle_raw` is unused until Task 8 — that's expected at this point in the plan.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd sentiment-scanner && python -m pytest tests/test_main.py -v`
Expected: PASS (6 tests)

Run the full suite:
Run: `cd sentiment-scanner && python -m pytest tests/ -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
cd sentiment-scanner
git add main.py tests/test_main.py
git commit -m "feat: wire earnings-vol scanner as 7th options scanner in main.py loop"
```

---

### Task 5: Weekly Earnings Digest in scan_trending

**Files:**
- Modify: `sentiment-scanner/main.py` (imports; `scan_trending`, edited again in Task 4)
- Modify: `sentiment-scanner/tests/test_main.py` (extend `TestScanTrendingReturnsRaw`'s existing mocks — those tests already monkeypatch `upcoming_earnings`/`format_earnings_digest`, written in Task 4 anticipating this task; add a new assertion-focused test class here)

**Interfaces:**
- Consumes: `upcoming_earnings`, `format_earnings_digest`, `fetch_earnings_calendar` from `scanner/earnings_calendar.py` (Task 1); `EARNINGS_CALENDAR` from `scanner/earnings_scanner.py` (existing).
- Produces: `scan_trending` prints one digest line per cycle (no new function signature — same as Task 4's).

- [ ] **Step 1: Write the failing test**

Add this class to `sentiment-scanner/tests/test_main.py` (after `TestScanTrendingReturnsRaw`):

```python
class TestScanTrendingEarningsDigest:
    def test_prints_digest_once_per_cycle_before_ticker_loop(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        monkeypatch.setattr(
            main_mod, "upcoming_earnings",
            lambda days=7, static_fallback=None: [("AAPL", "2026-08-05")],
        )
        monkeypatch.setattr(
            main_mod, "fetch_earnings_calendar",
            lambda: {"AAPL": "2026-08-05"},
        )
        monkeypatch.setattr(
            main_mod, "format_earnings_digest",
            lambda entries, days=7, live=True: f"  DIGEST:{len(entries)}:live={live}",
        )
        st = MagicMock()
        st.get_trending.return_value = []
        engine = MagicMock()

        alerts, cycle_raw = main_mod.scan_trending(st, engine)

        captured = capsys.readouterr()
        assert "DIGEST:1:live=True" in captured.out
        assert alerts == []
        assert cycle_raw == {}

    def test_passes_static_calendar_as_fallback(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured_kwargs = {}

        def _fake_upcoming(days=7, static_fallback=None):
            captured_kwargs["days"] = days
            captured_kwargs["static_fallback"] = static_fallback
            return []

        monkeypatch.setattr(main_mod, "upcoming_earnings", _fake_upcoming)
        monkeypatch.setattr(main_mod, "fetch_earnings_calendar", lambda: {})
        monkeypatch.setattr(
            main_mod, "format_earnings_digest",
            lambda entries, days=7, live=True: "  DIGEST",
        )
        st = MagicMock()
        st.get_trending.return_value = []
        engine = MagicMock()

        main_mod.scan_trending(st, engine)

        assert captured_kwargs["days"] == 7
        assert captured_kwargs["static_fallback"] is main_mod.EARNINGS_CALENDAR

    def test_live_false_when_live_calendar_empty(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured_kwargs = {}

        monkeypatch.setattr(
            main_mod, "upcoming_earnings",
            lambda days=7, static_fallback=None: [],
        )
        monkeypatch.setattr(main_mod, "fetch_earnings_calendar", lambda: {})

        def _fake_format(entries, days=7, live=True):
            captured_kwargs["live"] = live
            return "  DIGEST"

        monkeypatch.setattr(main_mod, "format_earnings_digest", _fake_format)
        st = MagicMock()
        st.get_trending.return_value = []
        engine = MagicMock()

        main_mod.scan_trending(st, engine)

        assert captured_kwargs["live"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd sentiment-scanner && python -m pytest tests/test_main.py::TestScanTrendingEarningsDigest -v`
Expected: FAIL — `AttributeError: <module 'main'> does not have the attribute 'upcoming_earnings'`

- [ ] **Step 3: Implement the digest print**

Add these imports in `sentiment-scanner/main.py`, alongside the earnings imports added in Task 4:

```python
from scanner.earnings_calendar import (
    upcoming_earnings, format_earnings_digest, fetch_earnings_calendar,
)
from scanner.earnings_scanner import EARNINGS_CALENDAR
```

In `scan_trending`, insert the digest print right after the "Trending: N symbols" line and before `alerts = []`. The `live` flag reuses `fetch_earnings_calendar()`'s in-memory cache (Task 1's `CACHE_TTL_SECONDS` TTL), so this second call is a cache hit, not a second network fetch, as long as it runs immediately after `upcoming_earnings()` (which called it first):

```python
def scan_trending(st, engine, benchmark="SPY", skip_gex=False, skip_youtube=False):
    trending = st.get_trending()
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Trending: {len(trending)} symbols")
    entries = upcoming_earnings(days=7, static_fallback=EARNINGS_CALENDAR)
    live = bool(fetch_earnings_calendar())
    print(format_earnings_digest(entries, days=7, live=live))
    alerts = []
    cycle_raw = {}
    for t in trending[:config.MAX_TICKERS_TO_SCAN]:
        ...
```

(Leave the rest of the function body — the `for t in trending[...]:` loop through `return alerts, cycle_raw` — exactly as Task 4 left it.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd sentiment-scanner && python -m pytest tests/test_main.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
cd sentiment-scanner
git add main.py tests/test_main.py
git commit -m "feat: print weekly earnings digest once per scan cycle"
```

---

### Task 6: Sector Rotation Standalone Launcher

**Files:**
- Create: `sentiment-scanner/sector_rotation_launcher.py`
- Test: `sentiment-scanner/tests/test_sector_rotation_launcher.py`

**Interfaces:**
- Consumes: `rank_sectors`, `format_rotation` from `scanner/sector_rotation.py` (existing, unmodified); `config.SCAN_INTERVAL_MINUTES` (existing).
- Produces: `run_once(period: str = "6mo") -> None`, `main(argv: Optional[List[str]] = None) -> None` — both used by Task 7.

- [ ] **Step 1: Write the failing tests**

Create `sentiment-scanner/tests/test_sector_rotation_launcher.py`:

```python
"""Tests for the standalone sector-rotation launcher script."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sector_rotation_launcher as launcher


class TestRunOnce:
    def test_calls_rank_sectors_and_prints_formatted_output(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        fake_ranks = ["FAKE_RANK"]
        monkeypatch.setattr(launcher, "rank_sectors", lambda period="6mo": fake_ranks)
        monkeypatch.setattr(launcher, "format_rotation", lambda ranks: f"TABLE:{ranks}")

        launcher.run_once(period="3mo")

        captured = capsys.readouterr()
        assert "TABLE:['FAKE_RANK']" in captured.out


class TestMain:
    def test_single_run_does_not_loop(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = []
        monkeypatch.setattr(launcher, "run_once", lambda period="6mo": calls.append(period))
        sleep_calls = []
        monkeypatch.setattr(launcher.time, "sleep", lambda s: sleep_calls.append(s))

        launcher.main([])

        assert calls == ["6mo"]
        assert sleep_calls == []

    def test_loop_flag_sleeps_between_runs(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = []
        monkeypatch.setattr(launcher, "run_once", lambda period="6mo": calls.append(period))

        sleep_calls = []

        def _fake_sleep(seconds):
            sleep_calls.append(seconds)
            raise KeyboardInterrupt()

        monkeypatch.setattr(launcher.time, "sleep", _fake_sleep)

        launcher.main(["--loop"])

        assert len(calls) == 1  # initial run_once before the loop
        assert len(sleep_calls) == 1

    def test_period_flag_passed_through(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = []
        monkeypatch.setattr(launcher, "run_once", lambda period="6mo": calls.append(period))

        launcher.main(["--period", "3mo"])

        assert calls == ["3mo"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd sentiment-scanner && python -m pytest tests/test_sector_rotation_launcher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sector_rotation_launcher'`

- [ ] **Step 3: Implement `sector_rotation_launcher.py`**

Create `sentiment-scanner/sector_rotation_launcher.py`:

```python
#!/usr/bin/env python3
"""Standalone launcher for the Sector Rotation / Factor Momentum scanner.

Runs independently of main.py's per-ticker loop since sector rotation
scores 15 sector ETFs (not a single ticker) and is comparatively
expensive (16 price-history fetches per run).

Usage:
    python sector_rotation_launcher.py              # single run
    python sector_rotation_launcher.py --loop        # repeat on an interval
    python sector_rotation_launcher.py --period 3mo   # shorter lookback
"""

import argparse
import sys
import time
from pathlib import Path
from typing import List, Optional

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import config
from scanner.sector_rotation import rank_sectors, format_rotation


def _parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sector Rotation / Factor Momentum launcher."
    )
    parser.add_argument(
        "--loop", action="store_true",
        help="Repeat the scan every SCAN_INTERVAL_MINUTES until interrupted.",
    )
    parser.add_argument(
        "--period", default="6mo",
        help="Price history period passed to rank_sectors (default 6mo).",
    )
    return parser.parse_args(argv)


def run_once(period: str = "6mo") -> None:
    """Run one sector-rotation pass and print the ranked table."""
    ranks = rank_sectors(period=period)
    print(format_rotation(ranks))


def main(argv: Optional[List[str]] = None) -> None:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    run_once(args.period)
    if not args.loop:
        return
    try:
        while True:
            print(f"\n--- Next sector scan in {config.SCAN_INTERVAL_MINUTES} min ---")
            time.sleep(config.SCAN_INTERVAL_MINUTES * 60)
            run_once(args.period)
    except KeyboardInterrupt:
        print("\nShutting down sector rotation launcher...")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd sentiment-scanner && python -m pytest tests/test_sector_rotation_launcher.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Manual smoke test**

Run: `cd sentiment-scanner && python sector_rotation_launcher.py`
Expected: prints the 15-ETF ranked table (or a ThetaData connection error if run outside a network with API access — that's an environment issue, not a code issue, since `rank_sectors()`'s underlying `fetch_price_history` is unchanged by this plan).

- [ ] **Step 6: Commit**

```bash
cd sentiment-scanner
git add sector_rotation_launcher.py tests/test_sector_rotation_launcher.py
git commit -m "feat: add standalone sector rotation launcher script"
```

---

### Task 7: Sector Rotation Prompts in main.py

**Files:**
- Modify: `sentiment-scanner/main.py` (imports; `_parse_args` lines 109-143; new `_prompt_yes_no`/`_launch_sector_rotation` helpers before `main()`; `main()` body lines 323-394)
- Modify: `sentiment-scanner/tests/test_main.py` (add prompt/launch tests)

**Interfaces:**
- Consumes: `sector_rotation_launcher.py` from Task 6 (invoked as a subprocess, not imported).
- Produces: `_prompt_yes_no(question: str, skip: bool = False) -> bool`, `_launch_sector_rotation() -> None`, new `--skip-sector-prompt` CLI flag.

- [ ] **Step 1: Write the failing tests**

Add these classes to `sentiment-scanner/tests/test_main.py`:

```python
class TestPromptYesNo:
    def test_skip_true_returns_false_without_prompting(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        called = []
        monkeypatch.setattr("builtins.input", lambda *_: called.append(1) or "y")
        assert main_mod._prompt_yes_no("Q?", skip=True) is False
        assert called == []

    def test_non_tty_returns_false_without_prompting(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(main_mod.sys.stdin, "isatty", lambda: False)
        called = []
        monkeypatch.setattr("builtins.input", lambda *_: called.append(1) or "y")
        assert main_mod._prompt_yes_no("Q?") is False
        assert called == []

    def test_tty_yes_answer_returns_true(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(main_mod.sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda *_: "y")
        assert main_mod._prompt_yes_no("Q?") is True

    def test_tty_no_answer_returns_false(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(main_mod.sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda *_: "n")
        assert main_mod._prompt_yes_no("Q?") is False

    def test_tty_empty_answer_returns_false(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(main_mod.sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda *_: "")
        assert main_mod._prompt_yes_no("Q?") is False


class TestLaunchSectorRotation:
    def test_invokes_subprocess_with_launcher_path_and_timeout(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = []

        def _fake_run(cmd, **kw):
            calls.append((cmd, kw))
            result = MagicMock()
            result.returncode = 0
            result.stdout = "ok"
            return result

        monkeypatch.setattr(main_mod.subprocess, "run", _fake_run)

        main_mod._launch_sector_rotation()

        assert len(calls) == 1
        cmd, kwargs = calls[0]
        assert cmd[0] == main_mod.sys.executable
        assert cmd[1].endswith("sector_rotation_launcher.py")
        assert kwargs["timeout"] == 1800
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True

    def test_timeout_is_caught_and_reported(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    ) -> None:
        def _fake_run(cmd, **kw):
            raise main_mod.subprocess.TimeoutExpired(cmd, kw.get("timeout", 1800))

        monkeypatch.setattr(main_mod.subprocess, "run", _fake_run)

        main_mod._launch_sector_rotation()  # must not raise

        captured = capsys.readouterr()
        assert "timed out" in captured.out.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd sentiment-scanner && python -m pytest tests/test_main.py::TestPromptYesNo tests/test_main.py::TestLaunchSectorRotation -v`
Expected: FAIL — `AttributeError: <module 'main'> does not have the attribute '_prompt_yes_no'`

- [ ] **Step 3: Implement the prompt helpers and wire them into main()**

Add these two functions in `sentiment-scanner/main.py`, right after `_make_run_id()` (before `_write_context_export`):

```python
def _prompt_yes_no(question: str, skip: bool = False) -> bool:
    """Ask *question* as a y/N prompt.

    Returns False without prompting if *skip* is set or stdin isn't a
    TTY, so scheduled/cron/CI runs of main.py never hang on input().
    """
    if skip or not sys.stdin.isatty():
        return False
    answer = input(f"{question} [y/N]: ").strip().lower()
    return answer in ("y", "yes")


def _launch_sector_rotation() -> None:
    """Launch sector_rotation_launcher.py as a foreground subprocess.

    Mirrors _launch_vol_suite's timeout/capture pattern (main.py:62-79) —
    a 15-ETF price-history fetch can be slow or hang if ThetaData is
    unreachable, so this must not block main.py indefinitely.
    """
    launcher_path = Path(__file__).resolve().parent / "sector_rotation_launcher.py"
    cmd = [sys.executable, str(launcher_path)]
    print(f"\n{'='*60}")
    print("  Launching Sector Rotation scanner...")
    print(f"{'='*60}")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        print(proc.stdout)
        if proc.returncode != 0:
            print(f"  Sector Rotation stderr: {proc.stderr[-2000:]}")
    except subprocess.TimeoutExpired:
        print("  Sector Rotation scanner timed out after 30 min.")
    except FileNotFoundError as e:
        print(f"  Could not launch Sector Rotation scanner: {e}")
```

In `_parse_args` (currently lines 109-143), add a new flag right after the existing `--skip-youtube` block:

```python
    parser.add_argument(
        "--skip-sector-prompt",
        action="store_true",
        help="Don't prompt to launch the Sector Rotation scanner at startup/shutdown.",
    )
```

In `main()`, right after the banner prints and before `st = StockTwitsScraper()` (currently line 332), add the startup prompt:

```python
    if _prompt_yes_no(
        "Launch Sector Rotation scanner now?", skip=args.skip_sector_prompt,
    ):
        _launch_sector_rotation()

    st = StockTwitsScraper()
```

Find the `if args.no_loop: return` line (currently line 367-368) and add the shutdown prompt before the return:

```python
        if args.no_loop:
            if _prompt_yes_no(
                "Launch Sector Rotation scanner before exiting?",
                skip=args.skip_sector_prompt,
            ):
                _launch_sector_rotation()
            return
```

Find the `except KeyboardInterrupt:` block (currently lines 390-391) and add the shutdown prompt after the "Shutting down..." print:

```python
    except KeyboardInterrupt:
        print("\nShutting down...")
        if _prompt_yes_no(
            "Launch Sector Rotation scanner before exiting?",
            skip=args.skip_sector_prompt,
        ):
            _launch_sector_rotation()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd sentiment-scanner && python -m pytest tests/test_main.py -v`
Expected: PASS (16 tests)

- [ ] **Step 5: Commit**

```bash
cd sentiment-scanner
git add main.py tests/test_main.py
git commit -m "feat: prompt to launch sector rotation scanner at startup/shutdown"
```

---

### Task 8: On-Demand PDF Report

**Files:**
- Modify: `sentiment-scanner/main.py` (imports; `_parse_args`; new `_raw_result_to_dict`/`_maybe_build_report` helpers; both `scan_trending(...)` call sites in `main()`)
- Modify: `sentiment-scanner/tests/test_main.py` (add report-building tests)

**Interfaces:**
- Consumes: `ScannerReport` from `scanner/report.py` (existing, unmodified); `cycle_raw` from `scan_trending` (Task 4); `CorrelationEngine.correlate_with_oi` (existing); `config.OUTPUT_DIR` (existing).
- Produces: `_raw_result_to_dict(name: str, result: object) -> dict`, `_maybe_build_report(engine, cycle_raw: dict, skip: bool = False) -> None`, new `--skip-report-prompt` CLI flag.

**CARL R1-F1 (critical, fixed in this task):** `UnusualOiScan.top_strikes` (`scanner/unusual_oi_scanner.py`) is `List[OiStrike]` — a *nested* dataclass field, not a flat one. A shallow `vars(result)` leaves `top_strikes` as a list of `OiStrike` objects, and `report.py`'s `_draw_skew_oi_card` (`scanner/report.py:321-327`) calls `.get('strike', 0)` etc. on each item, which `OiStrike` doesn't have — that raises `AttributeError` and crashes report generation the first time a ticker has any unusual-OI strikes, which is the common case. `_raw_result_to_dict` must use `dataclasses.asdict()` (which recurses through nested dataclasses and lists of them), not `vars()`.

**CARL R1-F2 (major, accepted as a documented limitation):** `report.py`'s `_build_scanner_dashboard` (`scanner/report.py:128-173`) only renders cards for `gex`, `iv_rank`, `skew`, `unusual_oi`, `max_pain`, `dispersion` — there is no earnings card, and this plan's Global Constraints explicitly forbid changing `report.py`'s rendering code. `_maybe_build_report` still calls `add_ticker_results(ticker, "earnings", ...)` so the data is captured in the report object (harmless, and available if a future plan adds an earnings card), but **the PDF report will not visually show earnings-vol data** even though it's the headline new scanner. This is called out explicitly to the user in the plan's rollout notes rather than silently shipped — see "Known Limitation" below.

- [ ] **Step 1: Write the failing tests**

Add these classes to `sentiment-scanner/tests/test_main.py`:

```python
class TestRawResultToDict:
    def test_none_result_returns_error_dict(self) -> None:
        assert main_mod._raw_result_to_dict("gex", None) == {"error": "no_data"}

    def test_plain_object_converted_via_vars(self) -> None:
        obj = _FakeScan("gex", error=None)
        result = main_mod._raw_result_to_dict("gex", obj)
        assert result == {"tag": "gex", "error": None}

    def test_dict_passed_through(self) -> None:
        result = main_mod._raw_result_to_dict("gex", {"a": 1})
        assert result == {"a": 1}

    def test_nested_dataclass_field_is_recursively_converted(self) -> None:
        """CARL R1-F1 regression test: UnusualOiScan.top_strikes is
        List[OiStrike] (a nested dataclass) — the converted dict must
        contain plain dicts, not OiStrike objects, or report.py's
        `.get('strike', 0)` calls on each entry raise AttributeError."""
        from dataclasses import dataclass

        @dataclass
        class _FakeStrike:
            strike: float
            right: str
            oi: int

        @dataclass
        class _FakeOiScan:
            ticker: str
            top_strikes: list
            error: object = None

        scan = _FakeOiScan(
            ticker="AAPL",
            top_strikes=[_FakeStrike(strike=200.0, right="C", oi=500)],
        )

        result = main_mod._raw_result_to_dict("unusual_oi", scan)

        assert result["ticker"] == "AAPL"
        assert isinstance(result["top_strikes"][0], dict)
        assert result["top_strikes"][0] == {
            "strike": 200.0, "right": "C", "oi": 500,
        }
        # Prove it actually round-trips through .get() the way report.py uses it:
        assert result["top_strikes"][0].get("strike", 0) == 200.0


class TestMaybeBuildReport:
    def test_skips_when_no_cycle_data(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        prompts = []
        monkeypatch.setattr(
            main_mod, "_prompt_yes_no",
            lambda *a, **kw: prompts.append(1) or True,
        )
        main_mod._maybe_build_report(MagicMock(), {})
        assert prompts == []

    def test_skips_when_user_declines(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(main_mod, "_prompt_yes_no", lambda *a, **kw: False)
        report_calls = []
        monkeypatch.setattr(
            main_mod, "ScannerReport",
            lambda **kw: report_calls.append(1),
        )
        main_mod._maybe_build_report(MagicMock(), {"AAPL": {"gex": None}})
        assert report_calls == []

    def test_builds_and_saves_report_when_confirmed(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(main_mod, "_prompt_yes_no", lambda *a, **kw: True)

        fake_report = MagicMock()
        monkeypatch.setattr(main_mod, "ScannerReport", lambda **kw: fake_report)

        engine = MagicMock()
        engine.correlate_with_oi.return_value = {
            "signals": ["OI_SURGE"], "severity": "MEDIUM",
        }

        cycle_raw = {
            "AAPL": {"gex": _FakeScan("gex"), "unusual_oi": None},
        }

        main_mod._maybe_build_report(engine, cycle_raw)

        fake_report.add_ticker_results.assert_any_call(
            "AAPL", "gex", {"tag": "gex", "error": None},
        )
        fake_report.add_ticker_results.assert_any_call(
            "AAPL", "unusual_oi", {"error": "no_data"},
        )
        fake_report.add_signals.assert_called_once_with(
            "AAPL", ["OI_SURGE"], "MEDIUM",
        )
        fake_report.save.assert_called_once_with(out_dir=main_mod.config.OUTPUT_DIR)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd sentiment-scanner && python -m pytest tests/test_main.py::TestRawResultToDict tests/test_main.py::TestMaybeBuildReport -v`
Expected: FAIL — `AttributeError: <module 'main'> does not have the attribute '_raw_result_to_dict'`

- [ ] **Step 3: Implement the report-building helpers and wire them into main()**

Add these imports in `sentiment-scanner/main.py`, alongside the other `scanner.*` imports:

```python
import dataclasses
from scanner.report import ScannerReport
```

Add these two functions after `_launch_sector_rotation()` (added in Task 7):

```python
def _raw_result_to_dict(name: str, result: object) -> dict:
    """Convert a scanner result object (or None) into a plain dict for
    ScannerReport.add_ticker_results(). Report cards read fields via
    dict.get(), so dataclass instances are converted with dataclasses.asdict(),
    which recurses through nested dataclasses and lists of them (e.g.
    UnusualOiScan.top_strikes: List[OiStrike] — see CARL R1-F1). A plain
    vars()/dict() shallow copy would leave nested dataclass fields as
    objects instead of dicts, breaking report.py's .get(...) calls on them.
    """
    if result is None:
        return {"error": "no_data"}
    if dataclasses.is_dataclass(result) and not isinstance(result, type):
        return dataclasses.asdict(result)
    if hasattr(result, "__dict__"):
        return dict(vars(result))
    return dict(result)


def _maybe_build_report(engine, cycle_raw: dict, skip: bool = False) -> None:
    """Prompt to build a PDF report for the most recently completed cycle.

    Called once at shutdown (CARL R1-F5 / user decision "option B"), not
    after every cycle — matching _launch_sector_rotation's cadence so a
    user who leaves an open terminal running never finds the loop
    blocked on an unattended input() mid-session.
    """
    if not cycle_raw:
        return
    if not _prompt_yes_no("Generate PDF report for the last completed run?", skip=skip):
        return

    tickers = sorted(cycle_raw.keys())
    report = ScannerReport(title="Sentiment Scanner Report", tickers=tickers)
    for ticker, raw in cycle_raw.items():
        for name, result in raw.items():
            report.add_ticker_results(ticker, name, _raw_result_to_dict(name, result))
        signals = engine.correlate_with_oi(ticker, {})
        report.add_signals(ticker, signals.get("signals", []), signals.get("severity", "LOW"))

    report.save(out_dir=config.OUTPUT_DIR)
```

In `_parse_args`, add a new flag right after `--skip-sector-prompt` (added in Task 7):

```python
    parser.add_argument(
        "--skip-report-prompt",
        action="store_true",
        help="Don't prompt to generate a PDF report at shutdown.",
    )
```

`_maybe_build_report` is called at the same two exit points as `_launch_sector_rotation` — **not** after every `scan_trending(...)` call. `cycle_raw` is already unconditionally assigned before either exit point can be reached (the first `scan_trending(...)` call, at the top of the `try:` block, runs before the `if args.no_loop:` check and before the `while True:` loop even starts), so it always holds at least the initial cycle's data, and the most recent completed cycle's data once the loop has run.

Find the `if args.no_loop:` block (added in Task 7) and add the report prompt after the sector-rotation prompt, before `return`:

```python
        if args.no_loop:
            if _prompt_yes_no(
                "Launch Sector Rotation scanner before exiting?",
                skip=args.skip_sector_prompt,
            ):
                _launch_sector_rotation()
            _maybe_build_report(engine, cycle_raw, skip=args.skip_report_prompt)
            return
```

Find the `except KeyboardInterrupt:` block (added in Task 7) and add the report prompt after the sector-rotation prompt:

```python
    except KeyboardInterrupt:
        print("\nShutting down...")
        if _prompt_yes_no(
            "Launch Sector Rotation scanner before exiting?",
            skip=args.skip_sector_prompt,
        ):
            _launch_sector_rotation()
        _maybe_build_report(engine, cycle_raw, skip=args.skip_report_prompt)
```

Leave both `scan_trending(...)` call sites (top of `try:`, and inside `while True:`) exactly as Task 4 left them — `alerts, cycle_raw = scan_trending(...)` with no report call inline.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd sentiment-scanner && python -m pytest tests/test_main.py -v`
Expected: PASS (23 tests)

Run the full suite one final time:
Run: `cd sentiment-scanner && python -m pytest tests/ -v`
Expected: PASS (all tests across every file touched in this plan)

- [ ] **Step 5: Commit**

```bash
cd sentiment-scanner
git add main.py tests/test_main.py
git commit -m "feat: on-demand PDF report generation after each scan cycle"
```

---

## Post-Plan Verification

After all 8 tasks are committed:

1. Run the full test suite once more: `cd sentiment-scanner && python -m pytest tests/ -v` — expect all tests passing.
2. Run `python main.py --no-loop --skip-sector-prompt --skip-report-prompt` in an environment with ThetaData/StockTwits access to confirm the live loop runs end-to-end with the earnings scanner (line 7) and earnings digest printing without crashing.
3. Run `python main.py --no-loop < /dev/null` (stdin redirected, simulating a non-interactive/cron invocation) and confirm it does **not** hang on either the startup or shutdown sector-rotation prompt, or the report prompt.
4. Revisit Task 1 Step 5's live-site verification if it wasn't done inline — confirm `fetch_earnings_calendar()` returns real data, not just falling back to the static dict every time.
5. Run `python main.py` (no `--no-loop`), let it complete at least two scan cycles, and confirm the PDF-report prompt does **not** appear until you interrupt with Ctrl-C — it must not appear after cycle 1 or cycle 2 (CARL R1-F5, resolved as shutdown-only per user's "option B" decision).
