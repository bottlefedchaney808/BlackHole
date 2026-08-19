# Direction-Suite Chart Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-bar historical replay of the Direction 5-signal suite plus a live conviction stamp as a buy/sell overlay on candlestick charts rendered by `render_spot_chart`.

**Architecture:** Thread an optional `as_of` date through the five Direction modules and `Direction.data` fetchers so each historical bar can be evaluated with that day's data (backward-compatible: `as_of=None` behaves exactly as today). A new `Direction/replay.py` evaluates `generate(ticker, as_of=bar_date)` per bar; the chart stack (`chart_request` + `candlestick_chart`) accepts the replay series and draws ▲/△/▼ markers plus a live note.

**Tech Stack:** Python 3.12, ThetaData (via `shared.thetadata.ThetaDataController`), numpy, matplotlib. No yfinance, no new dependencies.

## Global Constraints

- Windows host, no WSL. Run everything from `C:/Users/bottl/FinancialDevelopment` with the clean launcher: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe`.
- ThetaData is the only market-data source. Never add yfinance.
- Backward compatibility is mandatory: every function's `as_of=None` path must produce byte-identical behavior to today's code.
- Never fabricate history: a bar whose data cannot be fetched degrades to `conviction="NONE", score=0` or an omitted marker — never an invented signal.
- Tests follow the repo pattern: monkeypatch `Direction.data.*` (see `Direction/tests/test_bollinger_analyzer.py`) and inject fakes for chart providers.
- Date strings: public `as_of` params accept `"YYYY-MM-DD"` or `"YYYYMMDD"`; the canonical internal form is `"YYYY-MM-DD"`.

---

### Task 1: `Direction/data.py` as-of support

**Files:**
- Modify: `Direction/data.py` (add `_normalize_date`, `_as_of_date`, `get_close_asof`; extend `get_ohlcv`, `get_chain_eod_volume`, `get_chain_oi`, `get_dealer_gamma` with `as_of=None`)
- Modify: `shared/thetadata.py` (`option_bulk_oi_latest` gains `as_of=None`)
- Test: `Direction/tests/test_data.py`

**Interfaces:**
- Produces:
  - `data._normalize_date(value: str) -> str` — `"YYYY-MM-DD"` from `"YYYY-MM-DD"`/`"YYYYMMDD"`, else `""`.
  - `data._as_of_date(as_of: str | None) -> date` — `as_of` as a `date`, or `date.today()` when `None`/invalid.
  - `data.get_ohlcv(ticker, lookback_days=180, as_of=None) -> dict | None` — window ends on `as_of`.
  - `data.get_chain_eod_volume(ticker, exp, lookback_days=35, as_of=None) -> list[dict] | None` — window ends on `as_of`; rows are the latest trading day ≤ `as_of`.
  - `data.get_chain_oi(ticker, exp, as_of=None) -> list[dict] | None` — snapshot when `None`; historical OI (probed back from `as_of`) when set.
  - `data.get_dealer_gamma(ticker, as_of=None) -> dict | None` — 10-day window ending `as_of`.
  - `data.get_close_asof(ticker, as_of=None, lookback_days=90) -> float | None` — last close ≤ `as_of`.
  - `ThetaDataController.option_bulk_oi_latest(root, exp, lookback_days=7, as_of=None)` — probes backward from `as_of` when given, else from today.

- [ ] **Step 1: Write the failing tests**

Append to `Direction/tests/test_data.py`:

```python
from datetime import date, timedelta

import pytest

from Direction import data


def _ohlcv_rows(days):
    rows = []
    for i, d in enumerate(days):
        ymd = d.strftime("%Y%m%d")
        rows.append({"date": ymd, "created": d.isoformat(), "high": "100", "low": "90",
                     "close": str(100 + i), "volume": "1000"})
    return rows


def test_normalize_date_accepts_both_forms():
    assert data._normalize_date("2026-08-14") == "2026-08-14"
    assert data._normalize_date("20260814") == "2026-08-14"
    assert data._normalize_date("") == ""
    assert data._normalize_date("garbage") == ""


def test_as_of_date_defaults_to_today():
    assert data._as_of_date(None) == date.today()


def test_get_ohlcv_ends_on_as_of(monkeypatch):
    days = [date(2026, 8, 12), date(2026, 8, 13), date(2026, 8, 14)]
    calls = {}

    class FakeC:
        def hist_stock_eod(self, root, start_date, end_date):
            calls["start"] = start_date
            calls["end"] = end_date
            return _ohlcv_rows(days)

    monkeypatch.setattr(data, "_get_controller", lambda: FakeC())
    out = data.get_ohlcv("SPY", lookback_days=90, as_of="2026-08-14")
    assert calls["end"] == "20260814"
    assert out is not None and str(out["date"][-1]) == "2026-08-14"


def test_get_chain_eod_volume_uses_as_of_window(monkeypatch):
    calls = {}

    class FakeC:
        def option_bulk_hist_eod(self, root, exp, start_date, end_date):
            calls["end"] = end_date
            return [
                {"date": "20260813", "strike": 100000, "right": "C", "volume": "5"},
                {"date": "20260814", "strike": 100000, "right": "C", "volume": "9"},
            ]

    monkeypatch.setattr(data, "_get_controller", lambda: FakeC())
    out = data.get_chain_eod_volume("SPY", "20260918", as_of="2026-08-14")
    assert calls["end"] == "20260814"
    # latest trading day <= as_of wins
    assert len(out) == 1 and out[0]["volume"] == 9.0


def test_get_chain_oi_as_of_uses_historical_probe(monkeypatch):
    calls = {}

    class FakeC:
        def option_bulk_oi(self, root, exp):
            raise AssertionError("snapshot must not be called when as_of is set")

        def option_bulk_oi_latest(self, root, exp, lookback_days=7, as_of=None):
            calls["as_of"] = as_of
            return [{"strike": 100000, "right": "C", "open_interest": "50"}]

    monkeypatch.setattr(data, "_get_controller", lambda: FakeC())
    out = data.get_chain_oi("SPY", "20260918", as_of="2026-08-14")
    assert calls["as_of"] == "2026-08-14"
    assert out and out[0]["oi"] == 50.0


def test_get_close_asof_returns_last_close(monkeypatch):
    days = [date(2026, 8, 12), date(2026, 8, 13), date(2026, 8, 14)]

    class FakeC:
        def hist_stock_eod(self, root, start_date, end_date):
            return _ohlcv_rows(days)

    monkeypatch.setattr(data, "_get_controller", lambda: FakeC())
    assert data.get_close_asof("SPY", as_of="2026-08-14") == 103.0
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest Direction/tests/test_data.py -q`
Expected: FAIL — `AttributeError: module 'Direction.data' has no attribute '_normalize_date'` (and similar for the others).

- [ ] **Step 3: Implement the data-layer changes**

In `Direction/data.py`, add after `from shared.thetadata import ...`:

```python
def _normalize_date(value: str) -> str:
    """Return canonical 'YYYY-MM-DD' from 'YYYY-MM-DD' or 'YYYYMMDD'; '' if invalid."""
    if not value:
        return ""
    clean = str(value).replace("-", "")
    if len(clean) != 8 or not clean.isdigit():
        return ""
    return f"{clean[:4]}-{clean[4:6]}-{clean[6:8]}"


def _as_of_date(as_of: Optional[str]) -> date:
    """Resolve as_of to a date (defaults to today)."""
    s = _normalize_date(as_of or "")
    if not s:
        return date.today()
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return date.today()
```

Change `get_ohlcv` signature and body (keep the rest identical):

```python
def get_ohlcv(ticker: str, lookback_days: int = 180,
              as_of: Optional[str] = None) -> Optional[Dict]:
    """Daily OHLCV history ending on ``as_of`` (default: today).

    Returns ``{"date": np.ndarray[str], "high": np.ndarray[float],
    "low": ..., "close": ..., "volume": ...}`` oldest -> newest, or None.
    """
    c = _get_controller()
    if c is None:
        return None

    def _produce():
        try:
            end = _as_of_date(as_of)
            start = end - timedelta(days=lookback_days)
            rows = c.hist_stock_eod(
                ticker, start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
            )
        except Exception:
            return None
        if not rows:
            return None
        return _ohlcv_from_rows(rows)

    return _cached(f"ohlcv:{ticker}:{lookback_days}:{as_of or 'today'}", _produce)
```

Change `get_chain_eod_volume`:

```python
def get_chain_eod_volume(ticker: str, exp: str, lookback_days: int = 35,
                         as_of: Optional[str] = None) -> Optional[List[Dict]]:
    """Trading day's volume + close for every contract in one expiry.

    With ``as_of``, the fetch window ends on that date and rows are the
    latest trading day present (<= as_of). Without it, latest day as today.
    """
    c = _get_controller()
    if c is None:
        return None

    def _produce():
        try:
            end = _as_of_date(as_of)
            start = end - timedelta(days=lookback_days)
            rows = c.option_bulk_hist_eod(
                ticker, exp, start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
            )
        except Exception:
            return None
        if not rows:
            return None
        rows = _normalize_chain_rows(rows)
        rows = _latest_day_rows(rows)
        rows = [r for r in rows if "volume" in r]
        return rows or None

    return _cached(f"voleod:{ticker}:{exp}:{as_of or 'today'}", _produce)
```

Change `get_chain_oi`:

```python
def get_chain_oi(ticker: str, exp: str,
                 as_of: Optional[str] = None) -> Optional[List[Dict]]:
    """Per-strike open interest for one expiry.

    Returns normalized rows ``{"strike", "right", "oi"}`` or None. With
    ``as_of`` the historical OI route (probed back from that date) is used;
    otherwise the live snapshot endpoint.
    """
    c = _get_controller()
    if c is None:
        return None

    def _produce():
        try:
            if as_of:
                rows = c.option_bulk_oi_latest(ticker, exp, lookback_days=35, as_of=as_of)
            else:
                rows = c.option_bulk_oi(ticker, exp)
        except Exception:
            return None
        rows = _normalize_chain_rows(rows)
        for r in rows:
            if "oi" not in r:
                r["oi"] = 0.0
        return rows or None

    return _cached(f"oi:{ticker}:{exp}:{as_of or 'snapshot'}", _produce)
```

Change `get_dealer_gamma`:

```python
def get_dealer_gamma(ticker: str, as_of: Optional[str] = None) -> Optional[Dict]:
    """PotatoHedge's vendor dealer-positioning snapshot (latest day).

    With ``as_of`` the 10-day fetch window ends on that date.
    """
    c = _get_controller()
    if c is None:
        return None

    def _produce():
        try:
            end = _as_of_date(as_of)
            start = end - timedelta(days=10)
            payload = c.get_dealer_positioning(
                ticker, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"),
                latest_only=True,
            )
        except Exception:
            return None
        if isinstance(payload, dict) and payload:
            return payload
        if isinstance(payload, list) and payload:
            return {"rows": payload}
        return None

    return _cached(f"dealer:{ticker}:{as_of or 'today'}", _produce)
```

Add `get_close_asof` after `get_price`:

```python
def get_close_asof(ticker: str, as_of: Optional[str] = None,
                   lookback_days: int = 90) -> Optional[float]:
    """Last daily close on or before ``as_of`` (default: today)."""
    ohlcv = get_ohlcv(ticker, lookback_days=lookback_days, as_of=as_of)
    if ohlcv is None or len(ohlcv["close"]) == 0:
        return None
    return float(ohlcv["close"][-1])
```

In `shared/thetadata.py`, change `option_bulk_oi_latest`:

```python
    def option_bulk_oi_latest(self, root: str, exp: str,
                               lookback_days: int = 7,
                               as_of: Optional[str] = None) -> List[Dict]:
        """Fallback OI fetch when the snapshot endpoint 404s.

        ``bulk_snapshot/option/open_interest`` sometimes returns 404 for
        expiries that have no current OI snapshot (e.g. a newly-listed
        expiry that hasn't settled yet).  The historical endpoint
        ``bulk_hist/option/open_interest`` *does* have OI for those
        expiries -- it just takes a single date at a time, so we probe
        backwards from ``as_of`` (default: today) until we hit a trading
        day with data.

        Returns the same list-of-dicts shape as ``option_bulk_oi``.
        Returns ``[]`` if no recent trading day has OI for this expiry.
        """
        fmt = "%Y%m%d"
        end_dt = _as_of_date(as_of) if as_of else datetime.now().date()
        for offset in range(lookback_days):
            day = end_dt - timedelta(days=offset)
            if day.weekday() >= 5:  # skip Sat/Sun
                continue
            d = day.strftime(fmt)
            path = f"/api/theta/bulk_hist/option/open_interest/{root}/{exp}"
            r = self._get_with_retry(path, params={"start_date": d, "end_date": d})
            if r.status_code == 404:
                continue
            r.raise_for_status()
            rows = self._parse_rows(r)
            if rows:
                return rows
        return []
```

If `shared/thetadata.py` has no `_as_of_date` helper, add one at module level (same implementation as `Direction.data._normalize_date`/`_as_of_date` — small duplication is acceptable; do not import Direction into shared):

```python
def _as_of_date(as_of):
    if not as_of:
        return datetime.now().date()
    clean = str(as_of).replace("-", "")
    if len(clean) != 8 or not clean.isdigit():
        return datetime.now().date()
    try:
        return datetime.strptime(clean, "%Y%m%d").date()
    except ValueError:
        return datetime.now().date()
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest Direction/tests/test_data.py -q`
Expected: PASS (all tests incl. the 6 new ones).

- [ ] **Step 5: Run the full Direction test suite (regression)**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest Direction/tests -q`
Expected: PASS (no regressions from the signature changes).

- [ ] **Step 6: Commit**

```bash
git add Direction/data.py shared/thetadata.py Direction/tests/test_data.py
git commit -m "feat(direction): as-of aware data fetchers for historical replay"
```

---

### Task 2: `as_of` in `whale_scanner`

**Files:**
- Modify: `Direction/whale_scanner.py` (`scan` gains `as_of=None`)
- Test: `Direction/tests/test_whale_scanner.py`

**Interfaces:**
- Consumes: `data.get_chain_eod_volume(ticker, exp, as_of=...)`, `data.get_close_asof(ticker, as_of=...)`, `data.get_expirations(ticker)`.
- Produces: `whale_scanner.scan(ticker, min_premium=WHALE_THRESHOLD, threshold_bps=None, as_of=None) -> dict` — same result shape; with `as_of` uses that day's EOD volume and close.

- [ ] **Step 1: Write the failing test**

Append to `Direction/tests/test_whale_scanner.py`:

```python
def test_scan_passes_as_of_to_data(monkeypatch):
    from Direction import whale_scanner
    from Direction import data as d
    seen = {}

    monkeypatch.setattr(d, "get_expirations", lambda ticker: ["20260918"])
    monkeypatch.setattr(d, "get_chain_eod_volume",
                        lambda ticker, exp, as_of=None: seen.setdefault("vol", as_of) or [])
    monkeypatch.setattr(d, "get_close_asof",
                        lambda ticker, as_of=None, lookback_days=90: seen.setdefault("close", as_of) or 100.0)

    out = whale_scanner.scan("SPY", as_of="2026-08-14")
    assert seen["vol"] == "2026-08-14"
    assert seen["close"] == "2026-08-14"
    assert out["signal"] is False  # empty chain degrades to neutral
```

- [ ] **Step 2: Run to verify it fails**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest Direction/tests/test_whale_scanner.py::test_scan_passes_as_of_to_data -q`
Expected: FAIL — `TypeError: scan() got an unexpected keyword argument 'as_of'`.

- [ ] **Step 3: Implement**

In `Direction/whale_scanner.py`, change the signature and the two data calls:

```python
def scan(ticker: str, min_premium: float = WHALE_THRESHOLD,
         threshold_bps: Optional[float] = None,
         as_of: Optional[str] = None) -> dict:
    """Scan the nearest expiry's chain for whale-sized flow (live ThetaData).

    Any fetch failure or empty chain degrades to a zeroed neutral result
    (signal False) -- never raises. Classification is delegated to
    Vol_Suite.whale_scanner.classify_whale_bias. With ``as_of`` set, uses
    that day's EOD volume and close instead of today's.
    """
    result = _neutral_result(ticker)
    try:
        exps = data.get_expirations(ticker)
        if not exps:
            return result
        today = (as_of or date.today().strftime("%Y%m%d"))
        future = [e for e in exps if e >= today]
        expiry = future[0] if future else exps[-1]

        rows = data.get_chain_eod_volume(ticker, expiry, as_of=as_of)
        if as_of:
            price = data.get_close_asof(ticker, as_of=as_of)
        else:
            price = data.get_price(ticker)
    except Exception:
        return result
```

- [ ] **Step 4: Run to verify it passes**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest Direction/tests/test_whale_scanner.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add Direction/whale_scanner.py Direction/tests/test_whale_scanner.py
git commit -m "feat(direction): whale_scanner accepts as_of for historical replay"
```

---

### Task 3: `as_of` in `liquidity_map`

**Files:**
- Modify: `Direction/liquidity_map.py` (`_pick_expiry` + `get_liquidity` gain `as_of=None`)
- Test: `Direction/tests/test_liquidity_map.py`

**Interfaces:**
- Consumes: `data.get_chain_oi(ticker, exp, as_of=...)`, `data.get_dealer_gamma(ticker, as_of=...)`, `data.get_close_asof(ticker, as_of=...)`, `data.get_price(ticker)`.
- Produces: `liquidity_map.get_liquidity(ticker, as_of=None) -> dict` — same shape; with `as_of` uses that day's chain OI, dealer snapshot, and close.

- [ ] **Step 1: Write the failing test**

Append to `Direction/tests/test_liquidity_map.py`:

```python
def test_get_liquidity_passes_as_of_to_data(monkeypatch):
    from Direction import liquidity_map as lm
    from Direction import data as d
    seen = {}

    monkeypatch.setattr(d, "get_expirations", lambda ticker: ["20260918"])
    monkeypatch.setattr(d, "get_chain_oi",
                        lambda ticker, exp, as_of=None: seen.setdefault("oi", as_of) or [])
    monkeypatch.setattr(d, "get_dealer_gamma",
                        lambda ticker, as_of=None: seen.setdefault("dealer", as_of) or None)
    monkeypatch.setattr(d, "get_price", lambda ticker: 100.0)
    monkeypatch.setattr(d, "get_close_asof",
                        lambda ticker, as_of=None, lookback_days=90: seen.setdefault("close", as_of) or 100.0)

    out = lm.get_liquidity("SPY", as_of="2026-08-14")
    assert seen["oi"] == "2026-08-14"
    assert seen["dealer"] == "2026-08-14"
    assert out["pcr"] == 0.0  # empty chain -> neutral
```

- [ ] **Step 2: Run to verify it fails**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest Direction/tests/test_liquidity_map.py::test_get_liquidity_passes_as_of_to_data -q`
Expected: FAIL — `TypeError: get_liquidity() got an unexpected keyword argument 'as_of'`.

- [ ] **Step 3: Implement**

In `Direction/liquidity_map.py`, change `_pick_expiry`:

```python
def _pick_expiry(expirations, as_of: "str | None" = None) -> "str | None":
    """Pick the nearest expiry (>= today, else closest past one). With
    ``as_of``, 'today' is that date instead of the real today."""
    if not expirations:
        return None
    today = (as_of or date.today().strftime("%Y%m%d"))
    future = [e for e in expirations if e >= today]
    if future:
        return min(future)
    return max(expirations)
```

Change `get_liquidity` signature and the three data calls:

```python
def get_liquidity(ticker: str, as_of: Optional[str] = None) -> dict:
    """Return {"price", "expiry", "max_pain", "call_wall", "put_wall",
    "pcr", "signal": bool, "dealer"}.

    ... (docstring unchanged) ...
    With ``as_of`` set, chain OI, dealer snapshot, and price are that day's.
    """
    if as_of:
        price = data.get_close_asof(ticker, as_of=as_of)
    else:
        price = data.get_price(ticker)
    expirations = data.get_expirations(ticker) or []
    exp = _pick_expiry(expirations, as_of=as_of)

    chain = None
    if exp is not None:
        chain = data.get_chain_oi(ticker, exp, as_of=as_of)

    try:
        dealer = data.get_dealer_gamma(ticker, as_of=as_of)
    except Exception:
        dealer = None
```

(Only the lines above change; the rest of the function body stays identical.)

- [ ] **Step 4: Run to verify it passes**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest Direction/tests/test_liquidity_map.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add Direction/liquidity_map.py Direction/tests/test_liquidity_map.py
git commit -m "feat(direction): liquidity_map accepts as_of for historical replay"
```

---

### Task 4: `as_of` in `elliott_wave`, `bollinger_analyzer`, `trend_engine`

**Files:**
- Modify: `Direction/elliott_wave.py`, `Direction/bollinger_analyzer.py`, `Direction/trend_engine.py`
- Test: `Direction/tests/test_elliott_wave.py`, `Direction/tests/test_bollinger_analyzer.py`, `Direction/tests/test_trend_engine.py`

**Interfaces:**
- Consumes: `data.get_ohlcv(ticker, lookback_days=..., as_of=...)`.
- Produces: `elliott_wave.analyze(ticker, as_of=None) -> dict`, `bollinger_analyzer.analyze(ticker, as_of=None) -> dict`, `trend_engine.analyze_trend(ticker, as_of=None) -> dict` — same shapes.

- [ ] **Step 1: Write the failing tests**

Append to `Direction/tests/test_elliott_wave.py`:

```python
def test_analyze_passes_as_of_to_ohlcv(monkeypatch):
    from Direction import elliott_wave
    from Direction import data as d
    seen = {}
    monkeypatch.setattr(d, "get_ohlcv",
                        lambda ticker, lookback_days=90, as_of=None:
                        seen.setdefault("as_of", as_of) or None)
    elliott_wave.analyze("SPY", as_of="2026-08-14")
    assert seen["as_of"] == "2026-08-14"
```

Append to `Direction/tests/test_bollinger_analyzer.py`:

```python
def test_analyze_passes_as_of_to_ohlcv(monkeypatch):
    from Direction import bollinger_analyzer
    from Direction import data as d
    seen = {}
    monkeypatch.setattr(d, "get_ohlcv",
                        lambda ticker, lookback_days=90, as_of=None:
                        seen.setdefault("as_of", as_of) or None)
    bollinger_analyzer.analyze("SPY", as_of="2026-08-14")
    assert seen["as_of"] == "2026-08-14"
```

Append to `Direction/tests/test_trend_engine.py`:

```python
def test_analyze_trend_passes_as_of_to_ohlcv(monkeypatch):
    from Direction import trend_engine
    from Direction import data as d
    seen = {}
    monkeypatch.setattr(d, "get_ohlcv",
                        lambda ticker, lookback_days=180, as_of=None:
                        seen.setdefault("as_of", as_of) or None)
    trend_engine.analyze_trend("SPY", as_of="2026-08-14")
    assert seen["as_of"] == "2026-08-14"
```

- [ ] **Step 2: Run to verify they fail**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest Direction/tests/test_elliott_wave.py::test_analyze_passes_as_of_to_ohlcv Direction/tests/test_bollinger_analyzer.py::test_analyze_passes_as_of_to_ohlcv Direction/tests/test_trend_engine.py::test_analyze_trend_passes_as_of_to_ohlcv -q`
Expected: FAIL — `TypeError: analyze() got an unexpected keyword argument 'as_of'` etc.

- [ ] **Step 3: Implement**

`Direction/elliott_wave.py` — signature and one line:

```python
def analyze(ticker: str, as_of: Optional[str] = None) -> dict:
    """Pull 3mo closes via Direction.data and return count_waves output
    plus ``signal`` (True when wave_type == 'impulse_wave_3'). With
    ``as_of`` set, the window ends on that date."""
    ohlcv = data.get_ohlcv(ticker, lookback_days=90, as_of=as_of)  # ~3 months
```

`Direction/bollinger_analyzer.py`:

```python
def analyze(ticker: str, as_of: Optional[str] = None) -> dict:
    """3mo closes -> {"squeeze": bool, "regime": str, "signal": bool}.
    signal = squeeze OR regime in (upper_thrust_bullish, lower_thrust_bearish).
    With ``as_of`` set, the window ends on that date."""
    ohlcv = data.get_ohlcv(ticker, lookback_days=90, as_of=as_of)  # ~3 months
```

`Direction/trend_engine.py`:

```python
def analyze_trend(ticker: str, as_of: Optional[str] = None) -> dict:
    """Resample daily ThetaData OHLCV to daily/weekly/monthly and return
    {"daily": {...}, "weekly": {...}, "monthly": {...}, "adx_ok": bool,
     "aligned": bool, "signal": bool}. With ``as_of`` set, the window ends
     on that date."""
    daily_data = data.get_ohlcv(ticker, lookback_days=_LOOKBACK_DAYS, as_of=as_of)
```

Ensure each file imports `Optional` (add `from typing import Optional` where missing).

- [ ] **Step 4: Run to verify they pass**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest Direction/tests/test_elliott_wave.py Direction/tests/test_bollinger_analyzer.py Direction/tests/test_trend_engine.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add Direction/elliott_wave.py Direction/bollinger_analyzer.py Direction/trend_engine.py Direction/tests/test_elliott_wave.py Direction/tests/test_bollinger_analyzer.py Direction/tests/test_trend_engine.py
git commit -m "feat(direction): price-series modules accept as_of"
```

---

### Task 5: `as_of` in `signal_generator.generate`

**Files:**
- Modify: `Direction/signal_generator.py`
- Test: `Direction/tests/test_signal_generator.py`

**Interfaces:**
- Consumes: the five module entry points from Tasks 2–4.
- Produces: `signal_generator.generate(ticker, as_of=None) -> dict` — same shape plus `"as_of"` key (the resolved date string, or `None`).

- [ ] **Step 1: Write the failing test**

Append to `Direction/tests/test_signal_generator.py`:

```python
def test_generate_passes_as_of_to_all_modules(monkeypatch):
    from Direction import signal_generator as sg
    from Direction import (whale_scanner, elliott_wave, bollinger_analyzer,
                           trend_engine, liquidity_map)
    seen = {}

    def _watch(name, ret):
        def fn(ticker, as_of=None):
            seen[name] = as_of
            return ret
        return fn

    monkeypatch.setattr(whale_scanner, "scan", _watch("whale", {}))
    monkeypatch.setattr(elliott_wave, "analyze", _watch("wave3", {}))
    monkeypatch.setattr(bollinger_analyzer, "analyze", _watch("squeeze", {}))
    monkeypatch.setattr(trend_engine, "analyze_trend", _watch("trend", {}))
    monkeypatch.setattr(liquidity_map, "get_liquidity", _watch("liquidity", {}))

    out = sg.generate("SPY", as_of="2026-08-14")
    assert seen == {"whale": "2026-08-14", "wave3": "2026-08-14",
                    "squeeze": "2026-08-14", "trend": "2026-08-14",
                    "liquidity": "2026-08-14"}
    assert out["as_of"] == "2026-08-14"
```

- [ ] **Step 2: Run to verify it fails**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest Direction/tests/test_signal_generator.py::test_generate_passes_as_of_to_all_modules -q`
Expected: FAIL — `TypeError: generate() got an unexpected keyword argument 'as_of'`.

- [ ] **Step 3: Implement**

In `Direction/signal_generator.py`, change the signature and the five module calls, and add `as_of` to the return dict:

```python
def generate(ticker: str, as_of: Optional[str] = None) -> dict:
    """Run all five modules and combine into a conviction call.

    Returns {"ticker", "price", "signals": {"whale","wave3","squeeze",
    "trend","liquidity": bool}, "score": int (0-5), "conviction":
    "HIGH"|"MEDIUM"|"NONE", "as_of": str|None, "details": {...}}.
    With ``as_of`` set, every module evaluates that day's data.
    """
    try:
        whale = whale_scanner.scan(ticker, as_of=as_of)
    except Exception:
        whale = {}
    try:
        elliott = elliott_wave.analyze(ticker, as_of=as_of)
    except Exception:
        elliott = {}
    try:
        bollinger = bollinger_analyzer.analyze(ticker, as_of=as_of)
    except Exception:
        bollinger = {}
    try:
        trend = trend_engine.analyze_trend(ticker, as_of=as_of)
    except Exception:
        trend = {}
    try:
        liquidity = liquidity_map.get_liquidity(ticker, as_of=as_of)
    except Exception:
        liquidity = {}
```

Add to the return dict, after `"conviction": conviction,`:

```python
        "as_of": as_of,
```

- [ ] **Step 4: Run to verify it passes**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest Direction/tests/test_signal_generator.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add Direction/signal_generator.py Direction/tests/test_signal_generator.py
git commit -m "feat(direction): signal_generator.generate accepts as_of"
```

---

### Task 6: `Direction/replay.py`

**Files:**
- Create: `Direction/replay.py`
- Test: `Direction/tests/test_replay.py`

**Interfaces:**
- Consumes: `signal_generator.generate(ticker, as_of=...)`.
- Produces: `replay_direction(ticker: str, bar_dates: list[str], generate_fn: Callable | None = None) -> list[dict]` — one entry per date: `{"date", "conviction", "score", "signals"}`. `generate_fn` injectable for tests; defaults to `signal_generator.generate`. A per-date failure degrades to `{"conviction": "NONE", "score": 0, "signals": {}}` without raising.

- [ ] **Step 1: Write the failing tests**

Create `Direction/tests/test_replay.py`:

```python
import pytest

from Direction import replay


def test_replay_returns_one_entry_per_date():
    def fake_generate(ticker, as_of=None):
        return {"conviction": "HIGH", "score": 4, "signals": {"whale": True}}

    out = replay.replay_direction("SPY", ["2026-08-12", "2026-08-13"], generate_fn=fake_generate)
    assert out == [
        {"date": "2026-08-12", "conviction": "HIGH", "score": 4, "signals": {"whale": True}},
        {"date": "2026-08-13", "conviction": "HIGH", "score": 4, "signals": {"whale": True}},
    ]


def test_replay_degrades_on_per_date_failure():
    def fake_generate(ticker, as_of=None):
        raise RuntimeError("network down")

    out = replay.replay_direction("SPY", ["2026-08-14"], generate_fn=fake_generate)
    assert out == [{"date": "2026-08-14", "conviction": "NONE", "score": 0, "signals": {}}]


def test_replay_defaults_to_real_generate(monkeypatch):
    from Direction import signal_generator
    calls = []
    monkeypatch.setattr(signal_generator, "generate",
                        lambda ticker, as_of=None: calls.append(as_of) or
                        {"conviction": "NONE", "score": 0, "signals": {}})
    replay.replay_direction("SPY", ["2026-08-12"])
    assert calls == ["2026-08-12"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest Direction/tests/test_replay.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'Direction.replay'`.

- [ ] **Step 3: Implement**

Create `Direction/replay.py`:

```python
"""Direction/replay.py -- per-bar historical replay of the Direction suite.

Evaluates ``signal_generator.generate(ticker, as_of=date)`` for each bar
date in a chart's visible range so a candlestick chart can show a
buy/sell marker per bar. ``generate_fn`` is injectable for tests; a
per-date failure degrades to a neutral entry, never an invented signal.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional


def replay_direction(
    ticker: str,
    bar_dates: List[str],
    generate_fn: Optional[Callable] = None,
) -> List[Dict]:
    """Return [{date, conviction, score, signals}] for each bar date.

    ``bar_dates`` are canonical 'YYYY-MM-DD' strings. ``generate_fn``
    defaults to ``signal_generator.generate`` and is called as
    ``generate_fn(ticker, as_of=date)``. A failing evaluation degrades to
    ``{"conviction": "NONE", "score": 0, "signals": {}}``.
    """
    if generate_fn is None:
        from .signal_generator import generate as _generate
        generate_fn = _generate

    out: List[Dict] = []
    for d in bar_dates:
        try:
            r = generate_fn(ticker, as_of=d)
            out.append({
                "date": d,
                "conviction": r.get("conviction", "NONE"),
                "score": int(r.get("score", 0) or 0),
                "signals": r.get("signals", {}),
            })
        except Exception:
            out.append({"date": d, "conviction": "NONE", "score": 0, "signals": {}})
    return out
```

- [ ] **Step 4: Run to verify they pass**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest Direction/tests/test_replay.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add Direction/replay.py Direction/tests/test_replay.py
git commit -m "feat(direction): per-bar historical replay module"
```

---

### Task 7: Overlay support in the chart stack

**Files:**
- Modify: `shared/chart_request.py` (`render_spot_chart` gains `direction_overlay=None`, `live_note=None`)
- Modify: `shared/candlestick_chart.py` (`render_candlestick` gains `direction_overlay=None`, `live_note=None`; add `_marker_for_conviction` + drawing)
- Test: `tests/test_chart_request.py`, `tests/test_candlestick_chart.py`

**Interfaces:**
- Consumes: `replay_direction` output shape: list of `{"date": "YYYY-MM-DD", "conviction": "HIGH"|"MEDIUM"|"NONE", "score": int, "signals": dict}`.
- Produces:
  - `render_candlestick(payload, output_path, *, direction_overlay=None, live_note=None) -> Path`
  - `render_spot_chart(ticker, *, lookback=None, interval="1d", output_path, provider=None, renderer=None, direction_overlay=None, live_note=None) -> ChartArtifact`
  - Marker mapping: HIGH → ▲ below bar (buy), MEDIUM → △ below bar (weak buy), NONE → ▼ above bar (sell). Overlay entries match bars by calendar date (`observation.timestamp.date().isoformat()`); unmatched entries are ignored.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_candlestick_chart.py`:

```python
def test_render_candlestick_draws_direction_markers(tmp_path):
    from shared.chart_data import CandlePayload, CandleRecord
    from shared.candlestick_chart import render_candlestick, _marker_for_conviction
    from datetime import datetime

    assert _marker_for_conviction("HIGH") == "buy"
    assert _marker_for_conviction("MEDIUM") == "weak_buy"
    assert _marker_for_conviction("NONE") == "sell"
    assert _marker_for_conviction("BOGUS") == "none"

    records = tuple(
        CandleRecord(timestamp=datetime(2026, 8, 12, 9, 30), open=100.0, high=102.0,
                     low=99.0, close=101.0, volume=1000)
        for _ in range(3)
    )
    payload = CandlePayload(ticker="SPY", interval="15m", lookback="1d",
                            source="thetadata", observations=records)
    out = tmp_path / "chart.png"
    path = render_candlestick(payload, out,
                              direction_overlay=[
                                  {"date": "2026-08-12", "conviction": "HIGH",
                                   "score": 4, "signals": {}},
                              ],
                              live_note="LIVE: HIGH (4/5)")
    assert path.exists() and path.stat().st_size > 0
```

Append to `tests/test_chart_request.py`:

```python
def test_render_spot_chart_accepts_direction_overlay(tmp_path, monkeypatch):
    from shared.chart_data import CandlePayload, CandleRecord
    from shared import chart_request as cr
    from datetime import datetime

    records = (CandleRecord(timestamp=datetime(2026, 8, 12, 9, 30), open=100.0,
                            high=102.0, low=99.0, close=101.0, volume=1000),)
    payload = CandlePayload(ticker="SPY", interval="1d", lookback="6m",
                            source="thetadata", observations=records)

    def fake_provider(ticker, lookback=None, provider=None):
        return payload

    monkeypatch.setattr(cr, "fetch_daily_candles", fake_provider)
    out = tmp_path / "chart.png"
    art = cr.render_spot_chart("SPY", interval="1d", output_path=out,
                               direction_overlay=[
                                   {"date": "2026-08-12", "conviction": "HIGH",
                                    "score": 4, "signals": {}},
                               ],
                               live_note="LIVE: HIGH (4/5)")
    assert art.path.exists()
```

- [ ] **Step 2: Run to verify they fail**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest tests/test_candlestick_chart.py::test_render_candlestick_draws_direction_markers tests/test_chart_request.py::test_render_spot_chart_accepts_direction_overlay -q`
Expected: FAIL — `TypeError: render_candlestick() got an unexpected keyword argument 'direction_overlay'`.

- [ ] **Step 3: Implement**

In `shared/candlestick_chart.py`, add after the existing helper functions:

```python
def _marker_for_conviction(conviction: str) -> str:
    """Map a Direction conviction to a marker kind: buy/weak_buy/sell/none."""
    return {
        "HIGH": "buy",
        "MEDIUM": "weak_buy",
        "NONE": "sell",
    }.get(str(conviction).upper(), "none")
```

Change `render_candlestick` signature and add the drawing block after the candles/volume are drawn but before `figure.savefig` / `plt.close`. The block (insert after the existing axes are fully configured, just before savefig):

```python
def render_candlestick(payload: CandlePayload, output_path: str | PathLike[str],
                       *, direction_overlay: list | None = None,
                       live_note: str | None = None) -> Path:
```

Then, inside the function after the axes are drawn (locate the `plt.tight_layout()` or the savefig call and insert before it):

```python
    if direction_overlay:
        by_date = {str(entry.get("date")): entry for entry in direction_overlay if isinstance(entry, dict)}
        spread = max(obs.high - obs.low for obs in observations) or 1.0
        marker_style = {
            "buy": ("^", "lime", 1.0),
            "weak_buy": ("^", "cyan", 0.65),
            "sell": ("v", "orchid", 1.0),
        }
        for i, obs in enumerate(observations):
            kind = _marker_for_conviction(
                by_date.get(obs.timestamp.date().isoformat(), {}).get("conviction", "")
            )
            if kind not in marker_style:
                continue
            glyph, color, alpha = marker_style[kind]
            y = obs.low - 0.03 * spread if kind != "sell" else obs.high + 0.03 * spread
            axis.annotate(glyph, xy=(dates[i], y), fontsize=11, color=color,
                          alpha=alpha, ha="center", va="center",
                          annotation_clip=False)
    if live_note:
        axis.text(0.012, 0.985, live_note, transform=axis.transAxes,
                  fontsize=9, color="white", alpha=0.9, va="top",
                  bbox=dict(boxstyle="round,pad=0.3", fc="#1b2a4a", ec="none"))
```

In `shared/chart_request.py`, change `render_spot_chart`:

```python
def render_spot_chart(
    ticker: str,
    *,
    lookback: Any = None,
    interval: str = "1d",
    output_path: str | PathLike[str],
    provider=None,
    renderer: Renderer | None = None,
    direction_overlay: list | None = None,
    live_note: str | None = None,
) -> ChartArtifact:
    """Fetch, normalize, and render a spot chart.

    ... (docstring unchanged) ...
    ``direction_overlay`` is a list of {"date": "YYYY-MM-DD",
    "conviction": "HIGH"|"MEDIUM"|"NONE", "score": int} entries from
    ``Direction.replay.replay_direction``; markers are drawn at matching
    bars. ``live_note`` stamps the last bar with the current conviction.
    """
```

And the render call:

```python
    selected_renderer = renderer if renderer is not None else render_candlestick
    if direction_overlay is not None or live_note is not None:
        selected_renderer(payload, path, direction_overlay=direction_overlay,
                          live_note=live_note)
    else:
        selected_renderer(payload, path)
```

- [ ] **Step 4: Run the chart tests to verify they pass**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest tests/test_candlestick_chart.py tests/test_chart_request.py tests/test_chart_data.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full Direction suite (regression)**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest Direction/tests -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add shared/chart_request.py shared/candlestick_chart.py tests/test_candlestick_chart.py tests/test_chart_request.py
git commit -m "feat(chart): direction buy/sell overlay + live note on candlestick charts"
```

---

### Task 8: End-to-end — replay + overlay a real chart, update skill

**Files:**
- Create: `scripts/render_direction_chart.py`
- Modify: `C:/Users/bottl/AppData/Local/hermes/skills/quantitative-finance/render-quant-chart/SKILL.md` (add overlay section)
- No repo code changes beyond the script.

**Interfaces:**
- Consumes: `shared.chart_request.render_spot_chart(..., direction_overlay=..., live_note=...)`, `Direction.replay.replay_direction`, `shared.spot_history.fetch_intraday_candles` / `fetch_daily_candles`.
- Produces: `scripts/render_direction_chart.py` — CLI: `python scripts/render_direction_chart.py TICKER [--interval 15m] [--lookback 5d] [--out path]`.

- [ ] **Step 1: Write the script**

Create `scripts/render_direction_chart.py`:

```python
"""Render a candlestick chart with the Direction buy/sell overlay.

Usage:
  python scripts/render_direction_chart.py SPY --interval 15m --lookback 5d
  python scripts/render_direction_chart.py QQQ --interval 1h --lookback 30d --out artifacts/qqq_dir.png

The chart shows one marker per bar (HIGH=▲ buy, MEDIUM=△ weak buy,
NONE=▼ sell) computed by replaying the Direction suite as-of each bar's
date, plus a live conviction stamp on the last bar.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.chart_request import render_spot_chart  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Direction-overlay candlestick chart")
    p.add_argument("ticker", help="equity symbol, e.g. SPY")
    p.add_argument("--interval", default="15m",
                   choices=["3m", "5m", "10m", "15m", "30m", "1h", "4h", "1d"])
    p.add_argument("--lookback", default="5d", help="e.g. 5d, 30d, 6m")
    p.add_argument("--out", default=None, help="output PNG path")
    args = p.parse_args(argv)

    out = args.out or f"artifacts/{args.ticker}_{args.lookback}_{args.interval}_direction.png"

    from Direction.replay import replay_direction
    from shared import spot_history

    # 1. Get the bars first so the overlay covers exactly the visible range.
    if args.interval == "1d":
        payload = spot_history.fetch_daily_candles(args.ticker, lookback=args.lookback)
    else:
        payload = spot_history.fetch_intraday_candles(
            args.ticker, interval=args.interval, lookback=args.lookback)
    bar_dates = sorted({obs.timestamp.date().isoformat() for obs in payload.observations})

    # 2. Replay the Direction suite as-of each bar date.
    overlay = replay_direction(args.ticker, bar_dates)

    # 3. Live stamp: current conviction + score.
    from Direction.signal_generator import generate
    live = generate(args.ticker)
    live_note = f"LIVE: {live['conviction']} ({live['score']}/5)"

    # 4. Render.
    art = render_spot_chart(args.ticker, interval=args.interval,
                            lookback=args.lookback, output_path=out,
                            direction_overlay=overlay, live_note=live_note)
    print(f"saved {art.path} | {art.row_count} bars | {bar_dates[0]} -> {bar_dates[-1]}")
    print(f"live: {live_note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run it on SPY 15m**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe scripts/render_direction_chart.py SPY --interval 15m --lookback 5d`
Expected: prints `saved .../artifacts/SPY_5d_15m_direction.png | N bars | ... -> ...` and `live: ...`.

- [ ] **Step 3: Inspect the artifact visually**

Load the PNG with `vision_analyze` and confirm: candles render, ▲/△/▼ markers sit on bars, live note is present. If markers are absent, verify `bar_dates` match the payload timestamps' dates (the overlay matches on `date().isoformat()`).

- [ ] **Step 4: Update the `render-quant-chart` skill**

Patch `C:/Users/bottl/AppData/Local/hermes/skills/quantitative-finance/render-quant-chart/SKILL.md` — add to the "Canonical entry point" section:

```markdown
### Direction buy/sell overlay

To add the Direction suite's buy/sell markers, pass `direction_overlay` (the
list returned by `Direction.replay.replay_direction(ticker, bar_dates)`) and
an optional `live_note` string:

```python
from Direction.replay import replay_direction
from Direction.signal_generator import generate
from shared.chart_request import render_spot_chart

bar_dates = sorted({obs.timestamp.date().isoformat() for obs in payload.observations})
overlay = replay_direction('SPY', bar_dates)
live = generate('SPY')
art = render_spot_chart('SPY', interval='15m', lookback='5d', output_path=...,
                        direction_overlay=overlay,
                        live_note=f"LIVE: {live['conviction']} ({live['score']}/5)")
```

Markers: HIGH=▲ buy, MEDIUM=△ weak buy, NONE=▼ sell (per the agreed
convention). Daily-granularity signals (whale/liquidity) repeat the same
verdict across a day's intraday bars — that is expected, not a bug. The
canonical CLI is `scripts/render_direction_chart.py TICKER --interval 15m --lookback 5d`.
```

- [ ] **Step 5: Run the repo test suite once more (full regression)**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest Direction/tests tests/test_chart_data.py tests/test_chart_request.py tests/test_candlestick_chart.py tests/test_spot_history.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/render_direction_chart.py
git commit -m "feat(chart): direction-overlay render CLI + end-to-end"
```

---

## Self-Review (completed)

**1. Spec coverage:** every spec requirement maps to a task — as-of modules (Tasks 1–5), replay engine (Task 6), overlay renderer + live stamp (Task 7), end-to-end + skill update (Task 8). Out-of-scope items (dashboard, new bearish logic) are not in any task. ✓

**2. Placeholder scan:** no TBD/TODO; every step has complete code and exact commands with expected output. ✓

**3. Type consistency:** `as_of` is `Optional[str]` everywhere; `replay_direction(ticker, bar_dates, generate_fn=None)` shape `[{date, conviction, score, signals}]`; `direction_overlay` is `list[dict]` with `date`/`conviction`/`score` keys in both Tasks 6–8; `live_note` is `str | None` in Tasks 7–8; `_marker_for_conviction` maps HIGH/MEDIUM/NONE. ✓
