# Direction Tools Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the four remaining live Direction signals (Elliott Wave, Bollinger, multi-timeframe
trend, liquidity/max-pain) from the external `Dealer_pos_devnotes.tar.gz` research package into a new
`Direction/` package in this repo, give whale-flow a live sibling to its existing backtest-only port,
and wire all five plus a combined "group" signal as six new `Tools/` plugin adapters — each callable
individually or together, matching the sibling environment's already-proven `Tools/registry.py`
wiring (`Direction/README.md` in the devnotes package documents that exact 6-adapter pattern).

**Architecture:** `Direction/` is a new top-level package (parallel to `Vol_Suite/`, `Options_Suite/`)
with one pure-logic module per signal plus a single `Direction/data.py` ThetaData access layer every
signal shares (in-process 300s cache, so a group call pays for one data pull, not five). Each signal
module exposes `analyze(ticker)`/`scan(ticker)`/`get_liquidity(ticker)` returning a dict ending in a
boolean `signal` field; `Direction/signal_generator.py` runs all five and combines them into one
conviction call (`HIGH`/`MEDIUM`/`NONE`). Six `Tools/tools/*.py` adapters wrap these as `ToolSpec`s
registered in `Tools/registry.py`, following the exact pattern `options_strategy_tool.py` and
`backtesting_tool.py` already establish: `run(context: dict) -> dict`, ticker pulled from
`context.focus.ticker` (or `context['ticker']`).

**Tech Stack:** Python 3.12, numpy (no pandas — matches the devnotes package's pure-numpy style),
`shared.thetadata.ThetaDataController` (already has every method `Direction/data.py` calls — verified
signature-for-signature against the live client before writing this plan), pytest.

## Global Constraints

- **ThetaData only, no yfinance** — the devnotes package's own stated policy, already this repo's
  policy too (`shared/thetadata.py` is the sole market-data client). Never reintroduce yfinance.
- **Scope boundary: `Tools/` plugin wiring only.** This plan does NOT touch
  `Vol_Suite/dealer_positioning.py`'s `VALID_SIGN_MODELS` or any live dealer-sign resolution — that
  stays exactly as scoped in `docs/superpowers/specs/2026-08-06-whale-sign-model-backtest-design.md`
  (backtest-only for whale-flow; the other four signals were never even backtest-ported). These tools
  are standalone, callable individually or as a group, independent of any suite's core sign logic.
- **Network-free unit tests.** Every pure-computation function (wave counting, band math, ADX,
  max-pain payout, conviction scoring) gets tests with synthetic inputs, no network access — matching
  `Vol_Suite/tests/test_whale_scanner.py`'s existing convention. Live-fetch paths (`Direction/data.py`'s
  network calls, each signal's top-level `analyze`/`scan` wrapper) are exercised via monkeypatching the
  fetch layer, not real ThetaData calls, in unit tests.
- **DRY with existing, already-tested code.** `Direction/whale_scanner.py`'s live wrapper reuses
  `Vol_Suite/whale_scanner.classify_whale_bias` (the pure classifier `backtest_stage3.py` and its tests
  already exercise) rather than re-implementing the threshold/ratio math a second time.
- **Max-pain upgrade, not a straight port.** The devnotes' `liquidity_map.py` max-pain calc is a
  self-labeled "original approximation" (nearest strike to spot) — not real max pain. This plan ports a
  proper payout-minimization calc instead (argmin of aggregate ITM payout across strikes), the same
  formula `sentiment-scanner/scanner/max_pain_scanner.py::_compute_pain_for_strike` already uses
  correctly — but selected via `argmin`, deliberately avoiding that sibling module's own documented bug
  (it picks its max-pain strike via `np.argmax` against the payout array, which is backwards).
- **`pyproject.toml`'s `testpaths` must include `Tools/tests` and `Direction/tests`.** `Tools/tests/`
  already exists with two real test files but is missing from `testpaths` today — a pre-existing gap,
  not something this plan introduces, but one this plan's own new tests would silently inherit (added
  tests that never run under the documented `pytest` command are worse than no tests). Fixed in Task 9.

---

## File Structure

New files:
- `Direction/__init__.py` — re-exports `generate` for `from Direction import generate`
- `Direction/data.py` — ThetaData access layer (price, OHLCV, chains, GEX), ported near-verbatim
- `Direction/elliott_wave.py` — wave counting / wave-3 momentum, ported near-verbatim
- `Direction/bollinger_analyzer.py` — squeeze + band-thrust regime, ported near-verbatim
- `Direction/trend_engine.py` — ADX + MA20/MA50 multi-timeframe trend, ported near-verbatim
- `Direction/liquidity_map.py` — max pain / OI strike walls / PCR / GEX, ported **with max-pain fix**
- `Direction/whale_scanner.py` — **new** live wrapper around `Vol_Suite.whale_scanner.classify_whale_bias`
- `Direction/signal_generator.py` — combines all five into one conviction call, ported near-verbatim
- `Direction.py` (repo root) — thin CLI launcher, ported near-verbatim
- `Direction/README.md` — package + Tools wiring docs
- `Direction/tests/__init__.py`, `test_data.py`, `test_elliott_wave.py`, `test_bollinger_analyzer.py`,
  `test_trend_engine.py`, `test_liquidity_map.py`, `test_whale_scanner.py`, `test_signal_generator.py`
- `Tools/tools/whale_flow_tool.py`, `elliott_wave_tool.py`, `bollinger_tool.py`,
  `trend_engine_tool.py`, `liquidity_map_tool.py`, `direction_signal_tool.py`

Modified files:
- `Tools/registry.py` — import + register the 6 new `TOOL_SPEC`s
- `Tools/tests/test_registry.py` — extend slug/count assertions
- `Tools/README.md` — document the 6 new tools (mirrors the existing "shipped today" section)
- `pyproject.toml` — add `Tools/tests` and `Direction/tests` to `testpaths`

Explicitly out of scope: bespoke `/tools/<slug>` dashboard HTML forms for the 6 new tools (the generic
`/tools` index at `dashboard/app.py:1763` already lists every registered `ToolSpec` — including these —
automatically; hand-built per-tool forms like `/tools/options-strategy` are a separate, larger frontend
task not requested here and not needed for "callable individually and as a group").

---

### Task 1: `Direction/data.py` — shared ThetaData access layer

**Files:**
- Create: `Direction/__init__.py`
- Create: `Direction/data.py`
- Create: `Direction/tests/__init__.py`
- Test: `Direction/tests/test_data.py`

**Interfaces:**
- Consumes: `shared.thetadata.ThetaDataController` (methods `fetch_spot_price`, `hist_stock_eod`,
  `list_expirations`, `option_bulk_oi`, `option_bulk_hist_eod`, `option_snapshot_quote`,
  `get_dealer_positioning`), `shared.thetadata.strike_from_theta` — all confirmed present with matching
  signatures before writing this plan.
- Produces: `get_price(ticker) -> Optional[float]`, `get_ohlcv(ticker, lookback_days=180) -> Optional[dict]`,
  `resample_ohlcv(daily, freq="W"|"M") -> Optional[dict]`, `get_expirations(ticker) -> List[str]`,
  `get_chain_oi(ticker, exp) -> Optional[List[dict]]`,
  `get_chain_eod_volume(ticker, exp, lookback_days=35) -> Optional[List[dict]]`,
  `get_chain_quote(ticker, exp, strike, right="C") -> Optional[dict]`,
  `get_dealer_gamma(ticker) -> Optional[dict]`, `clear_cache() -> None` — every later Direction module
  imports this module as `data` (`from . import data`) and calls these.

- [ ] **Step 1: Write the failing tests for the pure-transform pieces of `data.py`**

Only the pure functions are unit-testable without a live ThetaData connection: `_date_key`,
`_ohlcv_from_rows`, `_normalize_chain_rows`, `_latest_day_rows`, `resample_ohlcv`.

```python
# Direction/tests/test_data.py
"""Tests for Direction/data.py's pure row/date transforms -- network-free.
The fetch functions themselves (get_price, get_ohlcv, ...) are thin
try/except wrappers around a ThetaDataController call; they're exercised
indirectly via each signal module's tests, which monkeypatch `data.*`
directly rather than mocking the controller.
"""
import numpy as np
import pytest

from Direction import data


@pytest.mark.unit
def test_date_key_handles_iso_timestamp_with_t_separator():
    assert data._date_key({"created": "2026-07-01T17:15:06.172"}) == "2026-07-01"


@pytest.mark.unit
def test_date_key_handles_yyyymmdd_date_field():
    assert data._date_key({"date": "20260701"}) == "2026-07-01"


@pytest.mark.unit
def test_date_key_returns_empty_string_on_missing_or_short_value():
    assert data._date_key({}) == ""
    assert data._date_key({"date": "2026"}) == ""


@pytest.mark.unit
def test_ohlcv_from_rows_dedupes_by_date_and_drops_missing_close():
    rows = [
        {"date": "20260701", "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000},
        {"date": "20260702", "high": 103.0, "low": 100.0, "close": None, "volume": 500},
    ]
    out = data._ohlcv_from_rows(rows)
    assert list(out["date"]) == ["2026-07-01"]
    assert out["close"][0] == pytest.approx(100.0)


@pytest.mark.unit
def test_ohlcv_from_rows_returns_none_on_empty_input():
    assert data._ohlcv_from_rows([]) is None


@pytest.mark.unit
def test_normalize_chain_rows_upscales_theta_strike_and_normalizes_right_word():
    rows = [{"strike": 450000, "right": "CALL", "oi": 12, "close": 3.5}]
    out = data._normalize_chain_rows(rows)
    assert out[0]["strike"] == pytest.approx(450.0)
    assert out[0]["right"] == "C"
    assert out[0]["oi"] == pytest.approx(12.0)


@pytest.mark.unit
def test_normalize_chain_rows_keeps_plain_dollar_strike_unscaled():
    rows = [{"strike": 450.0, "right": "PUT"}]
    out = data._normalize_chain_rows(rows)
    assert out[0]["strike"] == pytest.approx(450.0)
    assert out[0]["right"] == "P"


@pytest.mark.unit
def test_normalize_chain_rows_drops_rows_with_unresolvable_right():
    rows = [{"strike": 100.0, "right": "?"}]
    assert data._normalize_chain_rows(rows) == []


@pytest.mark.unit
def test_latest_day_rows_keeps_only_max_date():
    rows = [
        {"date": "20260701", "strike": 100.0},
        {"date": "20260702", "strike": 100.0},
        {"date": "20260702", "strike": 105.0},
    ]
    out = data._latest_day_rows(rows)
    assert len(out) == 2
    assert all(r["date"] == "20260702" for r in out)


@pytest.mark.unit
def test_resample_ohlcv_weekly_buckets_by_iso_week():
    daily = {
        "date": np.array(["2026-07-06", "2026-07-07", "2026-07-13"]),  # Mon, Tue, next Mon
        "high": np.array([10.0, 12.0, 9.0]),
        "low": np.array([8.0, 9.0, 7.0]),
        "close": np.array([9.0, 11.0, 8.0]),
        "volume": np.array([100.0, 200.0, 50.0]),
    }
    out = data.resample_ohlcv(daily, "W")
    assert len(out["date"]) == 2  # week of 7/6 (2 days), week of 7/13 (1 day)
    assert out["high"][0] == pytest.approx(12.0)
    assert out["volume"][0] == pytest.approx(300.0)


@pytest.mark.unit
def test_resample_ohlcv_returns_none_on_empty_daily():
    assert data.resample_ohlcv({"date": np.array([])}) is None
```

- [ ] **Step 2: Run tests to verify they fail (module doesn't exist yet)**

Run: `.venv\Scripts\python.exe -m pytest Direction/tests/test_data.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'Direction'`

- [ ] **Step 3: Create `Direction/__init__.py`**

```python
# Direction/__init__.py
"""Direction -- 5-input market-direction signal package (ThetaData-backed).

See Direction/README.md for the full signal catalog, conviction rules, and
Tools/ plugin wiring. `generate` is re-exported here so `from Direction
import generate` works without reaching into the submodule directly.
"""
from .signal_generator import generate

__all__ = ["generate"]
```

(This import will fail until Task 7 creates `signal_generator.py` — that's expected and fixed by the
end of this plan; `Direction/tests/test_data.py` in this task imports `from Direction import data`
directly, which does not require `signal_generator` to exist yet in Python's import machinery **only
if** `__init__.py` doesn't fail on import. Since `__init__.py` unconditionally imports
`signal_generator`, temporarily stub it in this task and let Task 7 replace the stub — see Step 4a.)

- [ ] **Step 4a: Temporarily stub `Direction/__init__.py` for this task only**

```python
# Direction/__init__.py (temporary form for Task 1 -- replaced in Task 7)
"""Direction -- 5-input market-direction signal package (ThetaData-backed).

See Direction/README.md for the full signal catalog once the package is
complete. `generate` is added to this file's exports in Task 7 once
signal_generator.py exists.
"""
```

- [ ] **Step 4b: Create `Direction/tests/__init__.py`**

```python
# Direction/tests/__init__.py
```

- [ ] **Step 5: Create `Direction/data.py`**

```python
# Direction/data.py
"""Direction/data.py -- the package's single data-access layer.

Everything in the Direction package gets its market data through this
module, which wraps the project's shared ThetaData client
(``shared.thetadata``). **No yfinance anywhere.**

Contract for consumers:
- Every public fetcher returns ``None`` / empty containers on missing data
  or network failure -- callers must degrade gracefully, never crash.
- OHLCV comes back as a small dict of numpy arrays, oldest -> newest.
- Option-chain helpers return normalized rows: ``{"strike": float,
  "right": "C"|"P", ...}``.
- All fetch results are cached in-process with a short TTL so the five
  modules can share one data pull per run.
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import numpy as np

from shared.thetadata import ThetaDataController, strike_from_theta

# ---------------------------------------------------------------------------
# Controller + caching
# ---------------------------------------------------------------------------

_controller: Optional[ThetaDataController] = None

# {cache_key: (expires_at, value)}
_CACHE: Dict[str, tuple] = {}
_CACHE_TTL = int(os.environ.get("DIRECTION_DATA_TTL", "300"))  # 5 min


def _get_controller() -> Optional[ThetaDataController]:
    """Build (once) and return the ThetaData controller.

    Returns None if credentials are missing so every public fetcher can
    degrade to an empty result instead of crashing the whole signal run.
    """
    global _controller
    if _controller is None:
        try:
            _controller = ThetaDataController()
        except Exception:
            _controller = None
    return _controller


def _cached(key: str, producer):
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and hit[0] > now:
        return hit[1]
    value = producer()
    _CACHE[key] = (now + _CACHE_TTL, value)
    return value


def _clear_cache() -> None:
    _CACHE.clear()


# ---------------------------------------------------------------------------
# Equity price data
# ---------------------------------------------------------------------------


def get_price(ticker: str) -> Optional[float]:
    """Latest spot price (last trade fallback handled by the client)."""
    c = _get_controller()
    if c is None:
        return None

    def _produce():
        try:
            return float(c.fetch_spot_price(ticker))
        except Exception:
            return None

    return _cached(f"price:{ticker}", _produce)


def get_ohlcv(ticker: str, lookback_days: int = 180) -> Optional[Dict]:
    """Daily OHLCV history.

    Returns ``{"date": np.ndarray[str], "high": np.ndarray[float],
    "low": ..., "close": ..., "volume": ...}`` oldest -> newest, or None.
    ``lookback_days`` is calendar days; the client paginates internally.
    """
    c = _get_controller()
    if c is None:
        return None

    def _produce():
        try:
            end = date.today()
            start = end - timedelta(days=lookback_days)
            rows = c.hist_stock_eod(
                ticker, start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
            )
        except Exception:
            return None
        if not rows:
            return None
        return _ohlcv_from_rows(rows)

    return _cached(f"ohlcv:{ticker}:{lookback_days}", _produce)


def _date_key(row: Dict) -> str:
    """Pull an ISO YYYY-MM-DD date string out of an EOD row.

    The ThetaData proxy returns the trading date under different keys/formats
    depending on route: 'created' (ISO timestamp) on hist/stock/eod, 'date'
    (YYYYMMDD or ISO) elsewhere.
    """
    raw = str(row.get("created") or row.get("date") or row.get("Date") or "").strip()
    if not raw:
        return ""
    clean = raw.replace("-", "").replace(":", "").replace("T", "").replace(" ", "")
    if len(clean) < 8 or not clean[:8].isdigit():
        return ""
    ymd = clean[:8]
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"


def _ohlcv_from_rows(rows: List[Dict]) -> Optional[Dict]:
    """Normalize EOD rows into per-day OHLCV arrays (dedup by date)."""
    by_date: Dict[str, dict] = {}
    for row in rows:
        d = _date_key(row)
        if not d:
            continue
        rec = by_date.setdefault(d, {"high": None, "low": None, "close": None, "volume": 0})
        for f in ("high", "low", "close"):
            try:
                v = float(row.get(f))
            except (TypeError, ValueError):
                v = rec[f]
            if v is not None and (rec[f] is None or f == "close" or (f == "volume")):
                rec[f] = v
        try:
            rec["volume"] = float(row.get("volume", 0) or 0)
        except (TypeError, ValueError):
            pass
    if not by_date:
        return None
    dates = sorted(by_date.keys())
    out = {
        "date": np.array(dates),
        "high": np.array([by_date[d]["high"] for d in dates], dtype=float),
        "low": np.array([by_date[d]["low"] for d in dates], dtype=float),
        "close": np.array([by_date[d]["close"] for d in dates], dtype=float),
        "volume": np.array([by_date[d]["volume"] for d in dates], dtype=float),
    }
    mask = ~np.isnan(out["close"])
    if not mask.any():
        return None
    return {k: v[mask] for k, v in out.items()}


def resample_ohlcv(daily: Dict, freq: str = "W") -> Optional[Dict]:
    """Resample daily OHLCV to weekly ('W') or monthly ('M').

    Pure-numpy date bucketing -- no pandas required. Requires
    ``daily["date"]`` as ISO strings.
    """
    if daily is None or len(daily["date"]) == 0:
        return None
    dates = [datetime.strptime(d, "%Y-%m-%d") for d in daily["date"]]
    buckets = []
    for dt in dates:
        if freq == "W":
            key = (dt - timedelta(days=dt.weekday())).date()
        elif freq == "M":
            key = dt.date().replace(day=1)
        else:
            key = dt.date()
        buckets.append(key)
    out_dates, out_high, out_low, out_close, out_vol = [], [], [], [], []
    i = 0
    while i < len(dates):
        key = buckets[i]
        j = i
        hi, lo = daily["high"][i], daily["low"][i]
        vol = 0.0
        while j < len(dates) and buckets[j] == key:
            hi = max(hi, daily["high"][j])
            lo = min(lo, daily["low"][j])
            vol += float(daily["volume"][j])
            j += 1
        out_dates.append(str(key))
        out_high.append(hi)
        out_low.append(lo)
        out_close.append(daily["close"][j - 1])
        out_vol.append(vol)
        i = j
    return {
        "date": np.array(out_dates),
        "high": np.array(out_high, dtype=float),
        "low": np.array(out_low, dtype=float),
        "close": np.array(out_close, dtype=float),
        "volume": np.array(out_vol, dtype=float),
    }


# ---------------------------------------------------------------------------
# Option chain data
# ---------------------------------------------------------------------------


def get_expirations(ticker: str) -> List[str]:
    """Sorted list of available expiry strings (YYYYMMDD)."""
    c = _get_controller()
    if c is None:
        return []

    def _produce():
        try:
            exps = [str(e) for e in c.list_expirations(ticker)]
            return sorted(exps)
        except Exception:
            return []

    return _cached(f"exps:{ticker}", _produce)


def _normalize_chain_rows(rows: List[Dict]) -> List[Dict]:
    """Normalize bulk rows to ``{"strike": float, "right": "C"|"P"}`` + extras."""
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        strike = None
        if row.get("strike") is not None:
            try:
                strike = float(row["strike"])
            except (TypeError, ValueError):
                strike = None
            if strike is not None and strike > 1000:  # theta-scaled int
                strike = strike_from_theta(strike)
        if strike is None and row.get("strike_theta") is not None:
            try:
                strike = strike_from_theta(float(row["strike_theta"]))
            except (TypeError, ValueError):
                strike = None
        if strike is None:
            continue
        right = str(row.get("right", "")).upper()
        if right.startswith("C"):
            right = "C"
        elif right.startswith("P"):
            right = "P"
        else:
            cp = str(row.get("call_put", row.get("cp", ""))).upper()
            right = "C" if cp.startswith("C") else "P" if cp.startswith("P") else "?"
        if right == "?":
            continue
        rec = dict(row)
        rec["strike"] = strike
        rec["right"] = right
        for f in ("oi", "open_interest", "volume", "close", "last", "bid", "ask"):
            if row.get(f) is not None:
                try:
                    rec[f] = float(row[f])
                except (TypeError, ValueError):
                    pass
        out.append(rec)
    return out


def get_chain_oi(ticker: str, exp: str) -> Optional[List[Dict]]:
    """Per-strike open interest for one expiry.

    Returns normalized rows ``{"strike", "right", "oi"}`` or None.
    """
    c = _get_controller()
    if c is None:
        return None

    def _produce():
        try:
            rows = c.option_bulk_oi(ticker, exp)
        except Exception:
            return None
        rows = _normalize_chain_rows(rows)
        for r in rows:
            if "oi" not in r:
                r["oi"] = 0.0
        return rows or None

    return _cached(f"oi:{ticker}:{exp}", _produce)


def _latest_day_rows(rows: List[Dict]) -> List[Dict]:
    """Keep only the rows of the most recent date present."""
    dates = [str(r.get("date", "")) for r in rows if r.get("date")]
    if not dates:
        return []
    latest = max(dates)
    return [r for r in rows if str(r.get("date", "")) == latest]


def get_chain_eod_volume(ticker: str, exp: str, lookback_days: int = 35) -> Optional[List[Dict]]:
    """Latest trading day's volume + close for every contract in one expiry."""
    c = _get_controller()
    if c is None:
        return None

    def _produce():
        try:
            end = date.today()
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

    return _cached(f"voleod:{ticker}:{exp}", _produce)


def get_chain_quote(ticker: str, exp: str, strike: float, right: str = "C") -> Optional[Dict]:
    """Latest quote for a single contract (bid/ask) or None."""
    c = _get_controller()
    if c is None:
        return None

    def _produce():
        try:
            row = c.option_snapshot_quote(ticker, exp, strike, right)
        except Exception:
            return None
        if not row:
            return None
        rec = {"strike": strike, "right": right}
        for f in ("bid", "ask", "last", "volume", "open_interest"):
            if row.get(f) is not None:
                try:
                    rec[f] = float(row[f])
                except (TypeError, ValueError):
                    pass
        return rec

    return _cached(f"quote:{ticker}:{exp}:{strike}:{right}", _produce)


def get_dealer_gamma(ticker: str) -> Optional[Dict]:
    """PotatoHedge's vendor dealer-positioning snapshot (latest day)."""
    c = _get_controller()
    if c is None:
        return None

    def _produce():
        try:
            end = date.today()
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

    return _cached(f"dealer:{ticker}", _produce)


def clear_cache() -> None:
    """Drop all cached data (used by tests and long-running loops)."""
    _clear_cache()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest Direction/tests/test_data.py -v`
Expected: PASS (11 tests)

- [ ] **Step 7: Commit**

```bash
git add Direction/__init__.py Direction/data.py Direction/tests/__init__.py Direction/tests/test_data.py
git commit -m "feat: add Direction/data.py shared ThetaData access layer"
```

---

### Task 2: `Direction/elliott_wave.py`

**Files:**
- Create: `Direction/elliott_wave.py`
- Test: `Direction/tests/test_elliott_wave.py`

**Interfaces:**
- Consumes: `Direction.data.get_ohlcv` (Task 1)
- Produces: `count_waves(prices: list) -> dict`, `fib_levels(high, low) -> dict`,
  `validate_impulse(p1,p2,p3,p4,p5) -> bool`, `analyze(ticker) -> dict` (keys: `wave_count`,
  `wave_number`, `wave_type`, `signal`) — consumed by `Direction/signal_generator.py` (Task 7) and
  `Tools/tools/elliott_wave_tool.py` (Task 11).

- [ ] **Step 1: Write the failing tests**

```python
# Direction/tests/test_elliott_wave.py
"""Tests for Direction/elliott_wave.py -- network-free except analyze(),
which monkeypatches Direction.data.get_ohlcv.
"""
import numpy as np
import pytest

from Direction import data, elliott_wave as ew


@pytest.mark.unit
def test_count_waves_returns_unknown_on_too_few_prices():
    assert ew.count_waves([1, 2]) == {"wave_count": 0, "wave_number": 0, "wave_type": "unknown"}


@pytest.mark.unit
def test_count_waves_returns_unknown_with_fewer_than_two_highs():
    # Monotonic rise: no local maxima at all.
    result = ew.count_waves([1, 2, 3, 4, 5])
    assert result["wave_type"] == "unknown"


@pytest.mark.unit
def test_count_waves_classifies_wave3_when_second_upleg_spans_more_bars():
    # highs at idx 1 and idx 5 (5-1=4 bars) vs lows at idx 3 (idx1-idx_low logic);
    # constructed so highs[1]-highs[0] > highs[0]-lows[0].
    prices = [1, 5, 1, 2, 1, 8, 1]
    result = ew.count_waves(prices)
    assert result["wave_count"] == 2
    assert result["wave_type"] in ("impulse_wave_3", "impulse_wave_1_or_5")


@pytest.mark.unit
def test_fib_levels_computes_all_five_retracements():
    levels = ew.fib_levels(high=110.0, low=100.0)
    assert levels["fib_236"] == pytest.approx(102.36)
    assert levels["fib_500"] == pytest.approx(105.0)
    assert levels["fib_786"] == pytest.approx(107.86)


@pytest.mark.unit
def test_validate_impulse_true_for_valid_five_point_structure():
    assert ew.validate_impulse(p1=1, p2=0.5, p3=2, p4=1.5, p5=1.2) is True


@pytest.mark.unit
def test_validate_impulse_false_when_wave2_exceeds_wave1_start():
    assert ew.validate_impulse(p1=1, p2=1.5, p3=2, p4=1.5, p5=1.2) is False


@pytest.mark.unit
def test_analyze_degrades_to_unknown_signal_false_on_no_data(monkeypatch):
    monkeypatch.setattr(data, "get_ohlcv", lambda ticker, lookback_days=90: None)
    result = ew.analyze("SPY")
    assert result == {"wave_count": 0, "wave_number": 0, "wave_type": "unknown", "signal": False}


@pytest.mark.unit
def test_analyze_sets_signal_true_when_wave_type_is_impulse_wave_3(monkeypatch):
    closes = np.array([1, 5, 1, 2, 1, 8, 1], dtype=float)
    monkeypatch.setattr(
        data, "get_ohlcv",
        lambda ticker, lookback_days=90: {"close": closes},
    )
    result = ew.analyze("SPY")
    assert result["signal"] == (result["wave_type"] == "impulse_wave_3")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest Direction/tests/test_elliott_wave.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'Direction.elliott_wave'`

- [ ] **Step 3: Create `Direction/elliott_wave.py`**

```python
# Direction/elliott_wave.py
"""Elliott Wave counter -- validates the 3 inviolable rules, identifies impulses.

Wave 3 = strongest momentum entry; Wave 5 = exhaustion warning.
"""

from . import data


def count_waves(prices: list) -> dict:
    """Count local extrema and classify the current wave.

    Returns {"wave_count": int, "wave_number": int, "wave_type": str}
    where wave_type is "impulse_wave_3" when wave 3 is the strongest
    swing, else "impulse_wave_1_or_5" (or "unknown").
    """
    if prices is None or len(prices) < 3:
        return {"wave_count": 0, "wave_number": 0, "wave_type": "unknown"}

    highs, lows = [], []
    for i in range(1, len(prices) - 1):
        if prices[i] > prices[i - 1] and prices[i] > prices[i + 1]:
            highs.append(i)
        if prices[i] < prices[i - 1] and prices[i] < prices[i + 1]:
            lows.append(i)

    if len(highs) < 2:
        return {"wave_count": 0, "wave_number": 0, "wave_type": "unknown"}

    wave3_strong = False
    if len(highs) > 1 and lows:
        wave3_strong = highs[1] - highs[0] > highs[0] - lows[0]

    return {
        "wave_count": len(highs),
        "wave_number": min(5, len(highs)),
        "wave_type": "impulse_wave_3" if wave3_strong else "impulse_wave_1_or_5",
    }


def fib_levels(high: float, low: float) -> dict:
    """Fibonacci retracement levels from a swing high/low."""
    diff = float(high) - float(low)
    return {
        "fib_236": low + diff * 0.236,
        "fib_382": low + diff * 0.382,
        "fib_500": low + diff * 0.500,
        "fib_618": low + diff * 0.618,
        "fib_786": low + diff * 0.786,
    }


def validate_impulse(p1, p2, p3, p4, p5) -> bool:
    """Validate the 5-point impulse structure (rules 1-3)."""
    return (p2 < p1 and p3 > max(p1, p2) and p4 < p3 and p4 > p1 and p5 < p4)


def analyze(ticker: str) -> dict:
    """Pull 3mo closes via Direction.data and return count_waves output
    plus ``signal`` (True when wave_type == 'impulse_wave_3')."""
    ohlcv = data.get_ohlcv(ticker, lookback_days=90)  # ~3 months
    if ohlcv is None or ohlcv["close"] is None or len(ohlcv["close"]) == 0:
        return {"wave_count": 0, "wave_number": 0, "wave_type": "unknown",
                "signal": False}
    result = count_waves(ohlcv["close"].tolist())
    result["signal"] = result["wave_type"] == "impulse_wave_3"
    return result


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Elliott Wave counter (ThetaData)")
    p.add_argument("ticker", nargs="?", default="SPY")
    args = p.parse_args()
    r = analyze(args.ticker)
    print(f"{args.ticker}: Wave {r['wave_number']} ({r['wave_type']})")
```

Note: unlike the devnotes original, this version drops the `try/except ImportError` standalone-script
fallback block, since `pyproject.toml`'s `pythonpath = ["."]` already makes `Direction` importable as a
package from any cwd under this repo's test/run conventions (matching how `Vol_Suite/whale_scanner.py`
and every other in-repo module already assumes package-relative imports without a fallback).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest Direction/tests/test_elliott_wave.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add Direction/elliott_wave.py Direction/tests/test_elliott_wave.py
git commit -m "feat: add Direction/elliott_wave.py wave counter"
```

---

### Task 3: `Direction/bollinger_analyzer.py`

**Files:**
- Create: `Direction/bollinger_analyzer.py`
- Test: `Direction/tests/test_bollinger_analyzer.py`

**Interfaces:**
- Consumes: `Direction.data.get_ohlcv` (Task 1)
- Produces: `get_bands(prices, window=20) -> dict`, `detect_squeeze(bands, threshold_pct=0.02) -> bool`,
  `regime(bands, price) -> str`, `analyze(ticker) -> dict` (keys: `squeeze`, `regime`, `signal`) —
  consumed by `Direction/signal_generator.py` (Task 7) and `Tools/tools/bollinger_tool.py` (Task 12).

- [ ] **Step 1: Write the failing tests**

```python
# Direction/tests/test_bollinger_analyzer.py
"""Tests for Direction/bollinger_analyzer.py -- network-free except
analyze(), which monkeypatches Direction.data.get_ohlcv.
"""
import numpy as np
import pytest

from Direction import bollinger_analyzer as ba, data


@pytest.mark.unit
def test_get_bands_returns_empty_arrays_when_shorter_than_window():
    bands = ba.get_bands([1, 2, 3], window=20)
    assert len(bands["sma"]) == 0


@pytest.mark.unit
def test_get_bands_computes_sma_and_symmetric_upper_lower():
    prices = [100.0] * 25  # flat -> std=0 -> upper==lower==sma
    bands = ba.get_bands(prices, window=20)
    assert bands["sma"][-1] == pytest.approx(100.0)
    assert bands["upper"][-1] == pytest.approx(100.0)
    assert bands["lower"][-1] == pytest.approx(100.0)


@pytest.mark.unit
def test_detect_squeeze_true_when_width_below_threshold_pct_of_price():
    bands = {"sma": np.array([100.0]), "width": np.array([1.0])}  # 1% < 2%
    assert ba.detect_squeeze(bands) is True


@pytest.mark.unit
def test_detect_squeeze_false_when_width_above_threshold_pct():
    bands = {"sma": np.array([100.0]), "width": np.array([5.0])}  # 5% > 2%
    assert ba.detect_squeeze(bands) is False


@pytest.mark.unit
def test_detect_squeeze_false_on_empty_bands():
    assert ba.detect_squeeze({"sma": np.array([]), "width": np.array([])}) is False


@pytest.mark.unit
def test_regime_upper_thrust_bullish_near_upper_band():
    bands = {"upper": np.array([110.0]), "lower": np.array([100.0])}
    assert ba.regime(bands, price=109.5) == "upper_thrust_bullish"


@pytest.mark.unit
def test_regime_lower_thrust_bearish_near_lower_band():
    bands = {"upper": np.array([110.0]), "lower": np.array([100.0])}
    assert ba.regime(bands, price=100.5) == "lower_thrust_bearish"


@pytest.mark.unit
def test_regime_neutral_midband():
    bands = {"upper": np.array([110.0]), "lower": np.array([100.0])}
    assert ba.regime(bands, price=105.0) == "neutral"


@pytest.mark.unit
def test_analyze_degrades_to_neutral_signal_false_on_no_data(monkeypatch):
    monkeypatch.setattr(data, "get_ohlcv", lambda ticker, lookback_days=90: None)
    result = ba.analyze("SPY")
    assert result == {"squeeze": False, "regime": "neutral", "signal": False}


@pytest.mark.unit
def test_analyze_signal_true_on_squeeze(monkeypatch):
    closes = np.array([100.0] * 25)
    monkeypatch.setattr(
        data, "get_ohlcv",
        lambda ticker, lookback_days=90: {"close": closes},
    )
    result = ba.analyze("SPY")
    assert result["squeeze"] is True
    assert result["signal"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest Direction/tests/test_bollinger_analyzer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'Direction.bollinger_analyzer'`

- [ ] **Step 3: Create `Direction/bollinger_analyzer.py`**

```python
# Direction/bollinger_analyzer.py
"""Bollinger Bands analyzer -- squeeze, thrust, and regime detection.

Squeeze (width < 2% of price) = volatile expansion imminent.
Band thrust through upper/lower = momentum continuation.
"""

import numpy as np

from . import data


def get_bands(prices, window: int = 20) -> dict:
    """Return {"sma", "upper", "lower", "width"} arrays (aligned, valid region)."""
    if prices is None or len(prices) < window:
        return {"sma": np.array([]), "upper": np.array([]),
                "lower": np.array([]), "width": np.array([])}
    prices = np.asarray(prices, dtype=float)
    sma = np.convolve(prices, np.ones(window) / window, mode="valid")
    std = np.array([np.std(prices[i:i + window])
                    for i in range(len(prices) - window + 1)])
    upper = sma + 2 * std
    lower = sma - 2 * std
    return {"sma": sma, "upper": upper, "lower": lower, "width": upper - lower}


def detect_squeeze(bands: dict, threshold_pct: float = 0.02) -> bool:
    """True when the latest band width is below threshold_pct of price."""
    width = bands.get("width")
    sma = bands.get("sma")
    if width is None or sma is None or len(width) == 0 or len(sma) == 0:
        return False
    if sma[-1] == 0:
        return False
    return bool((width[-1] / sma[-1]) < threshold_pct)


def regime(bands: dict, price: float) -> str:
    """Classify %B position: upper_thrust_bullish | lower_thrust_bearish |
    bullish | bearish | neutral."""
    upper = bands.get("upper")
    lower = bands.get("lower")
    if upper is None or lower is None or len(upper) == 0 or len(lower) == 0:
        return "neutral"
    band_range = upper[-1] - lower[-1]
    if band_range <= 0:
        return "neutral"
    bbp = (price - lower[-1]) / band_range
    if bbp > 0.90:
        return "upper_thrust_bullish"
    if bbp < 0.10:
        return "lower_thrust_bearish"
    if bbp > 0.60:
        return "bullish"
    if bbp < 0.40:
        return "bearish"
    return "neutral"


def analyze(ticker: str) -> dict:
    """3mo closes -> {"squeeze": bool, "regime": str, "signal": bool}.
    signal = squeeze OR regime in (upper_thrust_bullish, lower_thrust_bearish)."""
    ohlcv = data.get_ohlcv(ticker, lookback_days=90)  # ~3 months
    if ohlcv is None or ohlcv["close"] is None or len(ohlcv["close"]) == 0:
        return {"squeeze": False, "regime": "neutral", "signal": False}
    closes = ohlcv["close"]
    bands = get_bands(closes)
    sqz = detect_squeeze(bands)
    reg = regime(bands, float(closes[-1]))
    return {
        "squeeze": sqz,
        "regime": reg,
        "signal": sqz or reg in ("upper_thrust_bullish", "lower_thrust_bearish"),
    }


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Bollinger Bands analyzer (ThetaData)")
    p.add_argument("ticker", nargs="?", default="SPY")
    args = p.parse_args()
    r = analyze(args.ticker)
    print(f"{args.ticker}: {'SQUEEZE' if r['squeeze'] else 'Normal'} | "
          f"Regime: {r['regime']}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest Direction/tests/test_bollinger_analyzer.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add Direction/bollinger_analyzer.py Direction/tests/test_bollinger_analyzer.py
git commit -m "feat: add Direction/bollinger_analyzer.py squeeze/regime detector"
```

---

### Task 4: `Direction/trend_engine.py`

**Files:**
- Create: `Direction/trend_engine.py`
- Test: `Direction/tests/test_trend_engine.py`

**Interfaces:**
- Consumes: `Direction.data.get_ohlcv`, `Direction.data.resample_ohlcv` (Task 1)
- Produces: `adx(high, low, close, period=14) -> float`, `ma_alignment(prices, ma20, ma50) -> str`,
  `analyze_trend(ticker) -> dict` (keys: `daily`, `weekly`, `monthly`, `adx_ok`, `aligned`, `signal`) —
  consumed by `Direction/signal_generator.py` (Task 7) and `Tools/tools/trend_engine_tool.py` (Task 13).

- [ ] **Step 1: Write the failing tests**

```python
# Direction/tests/test_trend_engine.py
"""Tests for Direction/trend_engine.py -- network-free except
analyze_trend(), which monkeypatches Direction.data.get_ohlcv/resample_ohlcv.
"""
import numpy as np
import pytest

from Direction import data, trend_engine as te


@pytest.mark.unit
def test_adx_returns_zero_on_none_inputs():
    assert te.adx(None, None, None) == 0.0


@pytest.mark.unit
def test_adx_returns_zero_on_mismatched_lengths():
    assert te.adx([1, 2], [1], [1, 2]) == 0.0


@pytest.mark.unit
def test_adx_positive_on_strong_uptrend():
    n = 30
    high = np.linspace(100, 130, n)
    low = high - 1
    close = high - 0.5
    result = te.adx(high, low, close)
    assert result > 0.0


@pytest.mark.unit
def test_ma_alignment_bullish_when_price_above_ma20_above_ma50():
    prices = np.array([110.0])
    ma20 = np.array([105.0])
    ma50 = np.array([100.0])
    assert te.ma_alignment(prices, ma20, ma50) == "bullish"


@pytest.mark.unit
def test_ma_alignment_bearish_when_price_below_ma20_below_ma50():
    prices = np.array([90.0])
    ma20 = np.array([95.0])
    ma50 = np.array([100.0])
    assert te.ma_alignment(prices, ma20, ma50) == "bearish"


@pytest.mark.unit
def test_ma_alignment_mixed_on_missing_inputs():
    assert te.ma_alignment(None, None, None) == "mixed"


@pytest.mark.unit
def test_analyze_trend_degrades_to_neutral_on_no_data(monkeypatch):
    monkeypatch.setattr(data, "get_ohlcv", lambda ticker, lookback_days=180: None)
    result = te.analyze_trend("SPY")
    assert result["adx_ok"] is False
    assert result["aligned"] is False
    assert result["signal"] is False


@pytest.mark.unit
def test_analyze_trend_aligned_true_when_daily_and_weekly_agree_and_adx_high(monkeypatch):
    n = 120
    closes = np.linspace(100, 160, n)
    daily = {
        "date": np.array([f"2026-01-{(i % 28) + 1:02d}" for i in range(n)]),
        "high": closes + 1, "low": closes - 1, "close": closes,
        "volume": np.ones(n) * 1000,
    }
    monkeypatch.setattr(data, "get_ohlcv", lambda ticker, lookback_days=180: daily)
    monkeypatch.setattr(data, "resample_ohlcv", lambda d, freq: daily)
    result = te.analyze_trend("SPY")
    assert result["daily"]["ma"] == "bullish"
    assert result["signal"] == result["aligned"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest Direction/tests/test_trend_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'Direction.trend_engine'`

- [ ] **Step 3: Create `Direction/trend_engine.py`**

```python
# Direction/trend_engine.py
"""Multi-timeframe trend engine -- ADX + MA20/MA50 alignment (daily/weekly/monthly).

ADX > 25 = confirmed trend. Price > MA20 > MA50 = uptrend confirmed.
Weekly/Daily must align for high conviction.
"""

import numpy as np

from . import data


def adx(high, low, close, period: int = 14) -> float:
    """ADX-style directional strength in [0, 100].

    plus_dm  = max(high - prev_high, 0)
    minus_dm = max(prev_low - low, 0)
    tr       = max(high - low, |close - prev_close|)
    plus_di  = 100 * mean(plus_dm[-period:]) / (mean(tr[-period:]) + 1e-9)
    minus_di = 100 * mean(minus_dm[-period:]) / (mean(tr[-period:]) + 1e-9)
    adx      = plus_di / (plus_di + minus_di + 1e-9) * 100
    """
    if high is None or low is None or close is None:
        return 0.0
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    if len(high) < 2 or len(high) != len(low) or len(high) != len(close):
        return 0.0

    plus_dm = np.maximum(high[1:] - high[:-1], 0)
    minus_dm = np.maximum(low[:-1] - low[1:], 0)
    tr = np.maximum(high[1:] - low[1:], np.abs(close[1:] - close[:-1]))
    plus_di = 100 * np.mean(plus_dm[-period:]) / (np.mean(tr[-period:]) + 1e-9)
    minus_di = 100 * np.mean(minus_dm[-period:]) / (np.mean(tr[-period:]) + 1e-9)
    return float(plus_di / (plus_di + minus_di + 1e-9) * 100)


def ma_alignment(prices, ma20, ma50) -> str:
    """'bullish' | 'bearish' | 'mixed' from price > ma20 > ma50 ordering."""
    if prices is None or ma20 is None or ma50 is None:
        return "mixed"
    if len(prices) == 0 or len(ma20) == 0 or len(ma50) == 0:
        return "mixed"
    if prices[-1] > ma20[-1] > ma50[-1]:
        return "bullish"
    if prices[-1] < ma20[-1] < ma50[-1]:
        return "bearish"
    return "mixed"


def _timeframe(d) -> dict:
    """One timeframe's {"adx": float, "ma": str} from an OHLCV dict (or None)."""
    if d is None or d["close"] is None or len(d["close"]) == 0:
        return {"adx": 0.0, "ma": "mixed"}
    closes = np.asarray(d["close"], dtype=float)
    a = adx(d["high"], d["low"], d["close"])
    ma20 = np.convolve(closes, np.ones(20) / 20, mode="valid")
    ma50 = np.convolve(closes, np.ones(50) / 50, mode="valid")
    return {"adx": float(a), "ma": ma_alignment(closes, ma20, ma50)}


def _neutral_trend() -> dict:
    """Zeroed result used for graceful degradation (fetch failure / no data)."""
    return {
        "daily": {"adx": 0.0, "ma": "mixed"},
        "weekly": {"adx": 0.0, "ma": "mixed"},
        "monthly": {"adx": 0.0, "ma": "mixed"},
        "adx_ok": False,
        "aligned": False,
        "signal": False,
    }


def analyze_trend(ticker: str) -> dict:
    """Resample daily ThetaData OHLCV to daily/weekly/monthly and return
    {"daily": {...}, "weekly": {...}, "monthly": {...}, "adx_ok": bool,
     "aligned": bool, "signal": bool} where aligned = adx_ok AND daily/weekly
     agree."""
    daily_data = data.get_ohlcv(ticker, lookback_days=180)
    if daily_data is None or daily_data["close"] is None or len(daily_data["close"]) == 0:
        return _neutral_trend()

    weekly_data = data.resample_ohlcv(daily_data, "W")
    monthly_data = data.resample_ohlcv(daily_data, "M")

    daily = _timeframe(daily_data)
    weekly = _timeframe(weekly_data)
    monthly = _timeframe(monthly_data)

    adx_ok = daily["adx"] > 25
    aligned = adx_ok and daily["ma"] != "mixed" and weekly["ma"] == daily["ma"]
    return {"daily": daily, "weekly": weekly, "monthly": monthly,
            "adx_ok": adx_ok, "aligned": aligned, "signal": aligned}


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Multi-timeframe trend engine (ThetaData)")
    p.add_argument("ticker", nargs="?", default="SPY")
    args = p.parse_args()
    r = analyze_trend(args.ticker)
    for tf in ("daily", "weekly", "monthly"):
        print(f"{tf.upper()}: ADX={r[tf]['adx']:.1f} | MA={r[tf]['ma']}")
```

Note: `_neutral_trend()` gained an explicit `"signal": False` key here (the devnotes original omits it
and relies on `analyze_trend`'s final return line to add `signal`, but that line is only reached on the
non-degraded path — the degraded path returns `_neutral_trend()` directly, which in the original never
carried a `signal` key at all, a latent inconsistency with every other module's `analyze()` contract of
always including `signal`). Fixed here rather than ported verbatim.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest Direction/tests/test_trend_engine.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add Direction/trend_engine.py Direction/tests/test_trend_engine.py
git commit -m "feat: add Direction/trend_engine.py ADX/MA trend engine"
```

---

### Task 5: `Direction/liquidity_map.py` (with max-pain fix)

**Files:**
- Create: `Direction/liquidity_map.py`
- Test: `Direction/tests/test_liquidity_map.py`

**Interfaces:**
- Consumes: `Direction.data.get_price`, `Direction.data.get_expirations`, `Direction.data.get_chain_oi`,
  `Direction.data.get_dealer_gamma` (Task 1)
- Produces: `_pick_expiry(expirations) -> Optional[str]`, `_sum_oi(rows, right) -> float`,
  `_max_pain(chain, default) -> float` (**new**, replaces the devnotes' nearest-strike approximation),
  `get_liquidity(ticker) -> dict` (keys: `price`, `expiry`, `max_pain`, `call_wall`, `put_wall`, `pcr`,
  `signal`, `dealer`) — consumed by `Direction/signal_generator.py` (Task 7) and
  `Tools/tools/liquidity_map_tool.py` (Task 14).

- [ ] **Step 1: Write the failing tests**

```python
# Direction/tests/test_liquidity_map.py
"""Tests for Direction/liquidity_map.py -- network-free except
get_liquidity(), which monkeypatches Direction.data.*.
"""
import pytest

from Direction import data, liquidity_map as lm


def _row(strike, right, oi):
    return {"strike": strike, "right": right, "oi": oi}


@pytest.mark.unit
def test_pick_expiry_prefers_nearest_future_expiry():
    assert lm._pick_expiry(["20260101", "20260601", "20261231"]) in (
        "20260101", "20260601", "20261231"
    )  # exact answer depends on "today", but must be one of the inputs
    assert lm._pick_expiry([]) is None


@pytest.mark.unit
def test_sum_oi_totals_only_matching_right():
    rows = [_row(100, "C", 50), _row(105, "C", 30), _row(100, "P", 10)]
    assert lm._sum_oi(rows, "C") == pytest.approx(80.0)
    assert lm._sum_oi(rows, "P") == pytest.approx(10.0)


@pytest.mark.unit
def test_max_pain_picks_strike_minimizing_aggregate_itm_payout():
    # All OI concentrated at 100 on both sides -> settling AT 100 makes both
    # sides' payout zero (calls/puts both expire worthless there), so 100
    # must be the max-pain strike even though it isn't "nearest to some
    # arbitrary spot" -- this is exactly the case the old nearest-strike
    # approximation could get wrong if spot weren't already near 100.
    chain = [_row(90, "C", 5), _row(100, "C", 100), _row(110, "C", 5),
             _row(90, "P", 5), _row(100, "P", 100), _row(110, "P", 5)]
    assert lm._max_pain(chain, default=999.0) == pytest.approx(100.0)


@pytest.mark.unit
def test_max_pain_favors_side_with_more_oi_away_from_naive_center():
    # Heavy call OI at 90, heavy put OI at 110, light OI at 100 -- the
    # nearest-to-spot(100) approximation would pick 100, but the true
    # payout-minimizing strike is wherever total dollar payout is lowest.
    chain = [_row(90, "C", 1000), _row(100, "C", 1), _row(110, "C", 1),
             _row(90, "P", 1), _row(100, "P", 1), _row(110, "P", 1000)]
    result = lm._max_pain(chain, default=999.0)
    assert result == pytest.approx(100.0)  # midpoint minimizes both tails here


@pytest.mark.unit
def test_max_pain_returns_default_on_empty_chain():
    assert lm._max_pain([], default=123.0) == pytest.approx(123.0)


@pytest.mark.unit
def test_get_liquidity_degrades_gracefully_on_no_price(monkeypatch):
    monkeypatch.setattr(data, "get_price", lambda ticker: None)
    monkeypatch.setattr(data, "get_expirations", lambda ticker: [])
    monkeypatch.setattr(data, "get_dealer_gamma", lambda ticker: None)
    result = lm.get_liquidity("SPY")
    assert result["signal"] is False
    assert result["max_pain"] is None
    assert result["pcr"] == 0.0


@pytest.mark.unit
def test_get_liquidity_signal_true_when_max_pain_within_2pct_of_spot(monkeypatch):
    chain = [_row(100, "C", 50), _row(100, "P", 50)]
    monkeypatch.setattr(data, "get_price", lambda ticker: 100.0)
    monkeypatch.setattr(data, "get_expirations", lambda ticker: ["20261231"])
    monkeypatch.setattr(data, "get_chain_oi", lambda ticker, exp: chain)
    monkeypatch.setattr(data, "get_dealer_gamma", lambda ticker: None)
    result = lm.get_liquidity("SPY")
    assert result["signal"] is True
    assert result["max_pain"] == pytest.approx(100.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest Direction/tests/test_liquidity_map.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'Direction.liquidity_map'`

- [ ] **Step 3: Create `Direction/liquidity_map.py`**

```python
# Direction/liquidity_map.py
"""Liquidity zones -- max pain, OI strike walls, put/call ratio, GEX proximity.

4-layer strike walls: OI concentration + GEX + PCR + Max Pain proximity.
Support = cluster of put OI below price; Resistance = call OI above.

All market data comes from the package's ThetaData adapter (``Direction.data``).
Every fetch may return None/empty on missing data or network failure, so this
module degrades gracefully: no chain/price -> ``max_pain = call_wall =
put_wall = price`` (or None if no price at all), ``pcr = 0.0``,
``signal = False``. The ``dealer`` layer (GEX) is best-effort and may be
None -- it never takes the rest of the map down.

Max-pain fix vs. the source devnotes package: the original picked "the
strike nearest to spot" (self-labeled "original argmin approximation") --
that is a spot-proximity heuristic, not max pain. True max pain is the
strike that MINIMIZES the aggregate dollar payout to ITM option holders at
expiry across the WHOLE chain (sum over every strike of
call_oi * max(0, strike - K) + put_oi * max(0, K - strike), argmin over
candidate K). ``_max_pain`` below implements that properly -- the same
payout formula sentiment-scanner/scanner/max_pain_scanner.py already uses
correctly, but selected via argmin (not that module's own documented
argmax bug, which picks the wrong side of the curve).
"""

from __future__ import annotations

from datetime import date

from . import data


def _pick_expiry(expirations) -> "str | None":
    """Nearest expiry: first >= today (YYYYMMDD string compare), else the
    closest past one. Returns None when the list is empty."""
    if not expirations:
        return None
    today = date.today().strftime("%Y%m%d")
    future = [e for e in expirations if e >= today]
    if future:
        return min(future)
    return max(expirations)


def _sum_oi(rows, right: str) -> float:
    """Total open interest across all rows of one right (C/P)."""
    return sum(
        float(r.get("oi", 0.0) or 0.0)
        for r in rows
        if r.get("right") == right
    )


def _max_pain(chain, default: float) -> float:
    """True max-pain strike: argmin of aggregate ITM payout across the chain.

    Returns ``default`` (typically spot price) when the chain is empty.
    """
    strikes = sorted({float(r["strike"]) for r in chain})
    if not strikes:
        return default

    call_oi = {
        k: sum(float(r.get("oi", 0.0) or 0.0) for r in chain
               if r.get("right") == "C" and float(r["strike"]) == k)
        for k in strikes
    }
    put_oi = {
        k: sum(float(r.get("oi", 0.0) or 0.0) for r in chain
               if r.get("right") == "P" and float(r["strike"]) == k)
        for k in strikes
    }

    def _payout(K: float) -> float:
        return sum(
            call_oi[s] * max(0.0, s - K) + put_oi[s] * max(0.0, K - s)
            for s in strikes
        )

    return min(strikes, key=_payout)


def get_liquidity(ticker: str) -> dict:
    """Return {"price", "expiry", "max_pain", "call_wall", "put_wall",
    "pcr", "signal": bool, "dealer"}.

    - ``expiry``: nearest expiry (>= today preferred, else closest past).
    - ``max_pain``: strike minimizing aggregate ITM option-holder payout,
      defaults to price when no chain is available.
    - ``call_wall``/``put_wall``: strikes with the largest call/put OI,
      defaulting to price when that side has no contracts.
    - ``pcr``: total put OI / total call OI (0.0 when no call OI).
    - ``signal``: True when max pain sits within 2% of spot (gravity
      proximity); always False on missing chain/price.
    - ``dealer``: raw dealer-positioning payload (GEX layer), may be None.
    """
    price = data.get_price(ticker)
    expirations = data.get_expirations(ticker) or []
    exp = _pick_expiry(expirations)

    chain = None
    if exp is not None:
        chain = data.get_chain_oi(ticker, exp)

    try:
        dealer = data.get_dealer_gamma(ticker)
    except Exception:
        dealer = None

    if price is None or price <= 0:
        return {
            "price": price,
            "expiry": exp,
            "max_pain": price,
            "call_wall": price,
            "put_wall": price,
            "pcr": 0.0,
            "signal": False,
            "dealer": dealer,
        }

    if not chain:
        return {
            "price": price,
            "expiry": exp,
            "max_pain": price,
            "call_wall": price,
            "put_wall": price,
            "pcr": 0.0,
            "signal": False,
            "dealer": dealer,
        }

    max_pain = _max_pain(chain, price)

    calls = [r for r in chain if r.get("right") == "C"]
    puts = [r for r in chain if r.get("right") == "P"]
    call_wall = max(calls, key=lambda r: r.get("oi", 0.0))["strike"] if calls else price
    put_wall = max(puts, key=lambda r: r.get("oi", 0.0))["strike"] if puts else price

    call_oi = _sum_oi(chain, "C")
    put_oi = _sum_oi(chain, "P")
    pcr = put_oi / call_oi if call_oi > 0 else 0.0

    signal = abs(max_pain - price) / price <= 0.02

    return {
        "price": price,
        "expiry": exp,
        "max_pain": max_pain,
        "call_wall": call_wall,
        "put_wall": put_wall,
        "pcr": pcr,
        "signal": bool(signal),
        "dealer": dealer,
    }


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Liquidity zones map (ThetaData)")
    p.add_argument("ticker", nargs="?", default="SPY")
    args = p.parse_args()
    z = get_liquidity(args.ticker)
    pct = (z["max_pain"] - z["price"]) / z["price"] * 100
    print(f"{args.ticker}: Price={z['price']:.2f} | MaxPain={z['max_pain']:.2f} "
          f"({pct:+.2f}%) | PCR={z['pcr']:.2f} | Expires: {z['expiry']}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest Direction/tests/test_liquidity_map.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add Direction/liquidity_map.py Direction/tests/test_liquidity_map.py
git commit -m "feat: add Direction/liquidity_map.py with real max-pain calc"
```

---

### Task 6: `Direction/whale_scanner.py` (live wrapper, reuses `Vol_Suite.whale_scanner`)

**Files:**
- Create: `Direction/whale_scanner.py`
- Test: `Direction/tests/test_whale_scanner.py`

**Interfaces:**
- Consumes: `Direction.data.get_expirations`, `Direction.data.get_chain_eod_volume`,
  `Direction.data.get_price` (Task 1); `Vol_Suite.whale_scanner.classify_whale_bias` and
  `Vol_Suite.whale_scanner.WHALE_THRESHOLD` (existing, from the backtest-only port).
- Produces: `scan(ticker, min_premium=WHALE_THRESHOLD, threshold_bps=None) -> dict` (keys: `ticker`,
  `price`, `whale_calls`, `whale_puts`, `call_premium`, `put_premium`, `direction`, `expiry`, `signal`)
  — consumed by `Direction/signal_generator.py` (Task 7) and `Tools/tools/whale_flow_tool.py` (Task 10).

- [ ] **Step 1: Write the failing tests**

```python
# Direction/tests/test_whale_scanner.py
"""Tests for Direction/whale_scanner.py -- the LIVE sibling of
Vol_Suite/whale_scanner.py's backtest-only pure classifier. Network-free:
monkeypatches Direction.data.* and asserts the classification is delegated
to Vol_Suite.whale_scanner.classify_whale_bias rather than reimplemented.
"""
import sys
from pathlib import Path

import pytest

_VOL_SUITE_ROOT = Path(__file__).resolve().parent.parent.parent / "Vol_Suite"
if str(_VOL_SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOL_SUITE_ROOT))

from Direction import data, whale_scanner as dws
import whale_scanner as vs_whale_scanner  # Vol_Suite's pure classifier


def _row(strike, right, volume, close):
    return {"strike": strike, "right": right, "volume": volume, "close": close}


@pytest.mark.unit
def test_scan_degrades_to_neutral_on_no_expirations(monkeypatch):
    monkeypatch.setattr(data, "get_expirations", lambda ticker: [])
    result = dws.scan("SPY")
    assert result["direction"] == "neutral"
    assert result["signal"] is False


@pytest.mark.unit
def test_scan_degrades_to_neutral_on_no_chain_rows(monkeypatch):
    monkeypatch.setattr(data, "get_expirations", lambda ticker: ["20261231"])
    monkeypatch.setattr(data, "get_chain_eod_volume", lambda ticker, exp: None)
    monkeypatch.setattr(data, "get_price", lambda ticker: 100.0)
    result = dws.scan("SPY")
    assert result["direction"] == "neutral"
    assert result["signal"] is False


@pytest.mark.unit
def test_scan_bullish_matches_vol_suite_classifier_directly(monkeypatch):
    rows = [
        _row(110, "C", 20, 15.0),   # $30,000 premium
        _row(90, "P", 5, 10.0),     # $5,000 -- filtered out
    ]
    monkeypatch.setattr(data, "get_expirations", lambda ticker: ["20261231"])
    monkeypatch.setattr(data, "get_chain_eod_volume", lambda ticker, exp: rows)
    monkeypatch.setattr(data, "get_price", lambda ticker: 100.0)

    result = dws.scan("SPY")
    expected_direction, expected_stats = vs_whale_scanner.classify_whale_bias(rows)

    assert result["direction"] == expected_direction == "bullish"
    assert result["whale_calls"] == expected_stats["whale_calls"]
    assert result["call_premium"] == pytest.approx(expected_stats["call_premium"])
    assert result["signal"] is True
    assert result["expiry"] == "20261231"
    assert result["price"] == pytest.approx(100.0)


@pytest.mark.unit
def test_scan_picks_nearest_future_expiry(monkeypatch):
    captured = {}

    def _fake_chain(ticker, exp):
        captured["exp"] = exp
        return [_row(100, "C", 1, 1.0)]

    monkeypatch.setattr(data, "get_expirations", lambda ticker: ["20200101", "20990101"])
    monkeypatch.setattr(data, "get_chain_eod_volume", _fake_chain)
    monkeypatch.setattr(data, "get_price", lambda ticker: 100.0)
    dws.scan("SPY")
    assert captured["exp"] == "20990101"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest Direction/tests/test_whale_scanner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'Direction.whale_scanner'`

- [ ] **Step 3: Create `Direction/whale_scanner.py`**

```python
# Direction/whale_scanner.py
"""Direction/whale_scanner.py -- live ThetaData-backed whale-flow scanner.

Live wrapper around Vol_Suite/whale_scanner.py's classify_whale_bias -- the
same pure classification function backtest_stage3.py's whale-flow backtest
column already uses and Vol_Suite/tests/test_whale_scanner.py already
exercises -- rather than a second copy of the threshold/ratio math. This
module's only job is to fetch one day's chain rows via Direction.data and
hand them to that already-tested classifier, then reshape the result into
this package's scan() contract (ticker/price/whale_calls/whale_puts/
call_premium/put_premium/direction/expiry/signal) so it matches the other
four Direction modules' analyze()-shaped output.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Optional

from . import data

_VOL_SUITE_ROOT = Path(__file__).resolve().parent.parent / "Vol_Suite"
if str(_VOL_SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOL_SUITE_ROOT))

import whale_scanner as _vs_whale_scanner  # noqa: E402 -- Vol_Suite's already-tested classifier

WHALE_THRESHOLD = _vs_whale_scanner.WHALE_THRESHOLD


def _neutral_result(ticker: str) -> dict:
    return {
        "ticker": ticker,
        "price": None,
        "whale_calls": 0,
        "whale_puts": 0,
        "call_premium": 0.0,
        "put_premium": 0.0,
        "direction": "neutral",
        "expiry": None,
        "signal": False,
    }


def scan(ticker: str, min_premium: float = WHALE_THRESHOLD,
          threshold_bps: Optional[float] = None) -> dict:
    """Scan the nearest expiry's chain for whale-sized flow (live ThetaData).

    Any fetch failure or empty chain degrades to a zeroed neutral result
    (signal False) -- never raises. Classification is delegated to
    Vol_Suite.whale_scanner.classify_whale_bias.
    """
    result = _neutral_result(ticker)
    try:
        exps = data.get_expirations(ticker)
        if not exps:
            return result
        today = date.today().strftime("%Y%m%d")
        future = [e for e in exps if e >= today]
        expiry = future[0] if future else exps[-1]

        rows = data.get_chain_eod_volume(ticker, expiry)
        price = data.get_price(ticker)
    except Exception:
        return result

    if not rows:
        return result

    direction, stats = _vs_whale_scanner.classify_whale_bias(
        rows, min_premium=min_premium, threshold_bps=threshold_bps, price=price,
    )

    result.update({
        "price": price,
        "whale_calls": stats["whale_calls"],
        "whale_puts": stats["whale_puts"],
        "call_premium": stats["call_premium"],
        "put_premium": stats["put_premium"],
        "direction": direction,
        "expiry": expiry,
        "signal": direction != "neutral",
    })
    return result


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Whale flow scanner (ThetaData, live)")
    p.add_argument("ticker", nargs="?", default="SPY")
    args = p.parse_args()
    r = scan(args.ticker)
    print(f"{r['ticker']}: {r['direction']} | Calls: {r['whale_calls']} | "
          f"Puts: {r['whale_puts']} | Call$: ${r['call_premium']:,.0f} | "
          f"Put$: ${r['put_premium']:,.0f}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest Direction/tests/test_whale_scanner.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add Direction/whale_scanner.py Direction/tests/test_whale_scanner.py
git commit -m "feat: add Direction/whale_scanner.py live wrapper (reuses Vol_Suite classifier)"
```

---

### Task 7: `Direction/signal_generator.py` (group signal) + finalize `Direction/__init__.py`

**Files:**
- Create: `Direction/signal_generator.py`
- Modify: `Direction/__init__.py` (replace Task 1's temporary stub)
- Create: `Direction.py` (repo root, thin CLI launcher)
- Test: `Direction/tests/test_signal_generator.py`

**Interfaces:**
- Consumes: `Direction.whale_scanner.scan`, `Direction.elliott_wave.analyze`,
  `Direction.bollinger_analyzer.analyze`, `Direction.trend_engine.analyze_trend`,
  `Direction.liquidity_map.get_liquidity`, `Direction.data.get_price` (Tasks 1-6)
- Produces: `generate(ticker) -> dict` (keys: `ticker`, `price`, `signals` (dict of 5 booleans),
  `score` (0-5), `conviction` (`HIGH`|`MEDIUM`|`NONE`), `details`) — re-exported as
  `Direction.generate`; consumed by `Tools/tools/direction_signal_tool.py` (Task 15).

- [ ] **Step 1: Write the failing tests**

```python
# Direction/tests/test_signal_generator.py
"""Tests for Direction/signal_generator.py's conviction-combination logic
-- network-free, monkeypatches each of the five submodules' entry points
directly so only the combination logic (score/conviction rules) is under
test, not any submodule's own behavior (already covered by their own test
files).
"""
import pytest

from Direction import (
    bollinger_analyzer, data, elliott_wave, liquidity_map,
    signal_generator as sg, trend_engine, whale_scanner,
)


def _patch_all(monkeypatch, *, whale_sig, wave_sig, squeeze_sig, trend_sig, liq_sig):
    monkeypatch.setattr(whale_scanner, "scan",
                         lambda ticker, **kw: {"signal": whale_sig, "price": 100.0})
    monkeypatch.setattr(elliott_wave, "analyze", lambda ticker: {"signal": wave_sig})
    monkeypatch.setattr(bollinger_analyzer, "analyze", lambda ticker: {"signal": squeeze_sig})
    monkeypatch.setattr(trend_engine, "analyze_trend", lambda ticker: {"signal": trend_sig})
    monkeypatch.setattr(liquidity_map, "get_liquidity", lambda ticker: {"signal": liq_sig})


@pytest.mark.unit
def test_generate_high_conviction_when_whale_wave3_and_squeeze_and_score_ge_3(monkeypatch):
    _patch_all(monkeypatch, whale_sig=True, wave_sig=True, squeeze_sig=True,
               trend_sig=False, liq_sig=False)
    result = sg.generate("NVDA")
    assert result["score"] == 3
    assert result["conviction"] == "HIGH"


@pytest.mark.unit
def test_generate_medium_conviction_when_whale_and_score_ge_3_but_not_high(monkeypatch):
    _patch_all(monkeypatch, whale_sig=True, wave_sig=False, squeeze_sig=True,
               trend_sig=True, liq_sig=False)
    result = sg.generate("NVDA")
    assert result["score"] == 3
    assert result["conviction"] == "MEDIUM"  # whale AND score>=3, but wave3 is False so not HIGH


@pytest.mark.unit
def test_generate_none_conviction_without_whale_signal(monkeypatch):
    _patch_all(monkeypatch, whale_sig=False, wave_sig=True, squeeze_sig=True,
               trend_sig=True, liq_sig=True)
    result = sg.generate("NVDA")
    assert result["score"] == 4
    assert result["conviction"] == "NONE"  # no whale = no trade, regardless of score


@pytest.mark.unit
def test_generate_none_conviction_when_whale_true_but_score_below_3(monkeypatch):
    _patch_all(monkeypatch, whale_sig=True, wave_sig=False, squeeze_sig=False,
               trend_sig=False, liq_sig=False)
    result = sg.generate("NVDA")
    assert result["score"] == 1
    assert result["conviction"] == "NONE"


@pytest.mark.unit
def test_generate_falls_back_to_data_get_price_when_whale_has_no_price(monkeypatch):
    monkeypatch.setattr(whale_scanner, "scan", lambda ticker, **kw: {"signal": False, "price": None})
    monkeypatch.setattr(elliott_wave, "analyze", lambda ticker: {"signal": False})
    monkeypatch.setattr(bollinger_analyzer, "analyze", lambda ticker: {"signal": False})
    monkeypatch.setattr(trend_engine, "analyze_trend", lambda ticker: {"signal": False})
    monkeypatch.setattr(liquidity_map, "get_liquidity", lambda ticker: {"signal": False})
    monkeypatch.setattr(data, "get_price", lambda ticker: 55.5)
    result = sg.generate("NVDA")
    assert result["price"] == pytest.approx(55.5)


@pytest.mark.unit
def test_generate_details_carries_all_five_module_outputs(monkeypatch):
    _patch_all(monkeypatch, whale_sig=True, wave_sig=True, squeeze_sig=True,
               trend_sig=True, liq_sig=True)
    result = sg.generate("NVDA")
    assert set(result["details"].keys()) == {"whale", "elliott", "bollinger", "trend", "liquidity"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest Direction/tests/test_signal_generator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'Direction.signal_generator'`

- [ ] **Step 3: Create `Direction/signal_generator.py`**

```python
# Direction/signal_generator.py
"""signal_generator -- unified Direction signal: all 5 inputs, one conviction.

Runs the five independent Direction modules (whale flow, Elliott Wave,
Bollinger, multi-timeframe trend, liquidity zones) and combines them into a
single conviction call. ThetaData is the only data source.

Conviction rules (EXACT):
  HIGH   = whale AND wave3 AND (squeeze OR trend) AND score >= 3
  MEDIUM = whale AND score >= 3 AND not HIGH
  NONE   = otherwise (no whale signal = no trade)

`score` is the number of the five signals that fired (0-5).
"""

from __future__ import annotations

import argparse

from . import bollinger_analyzer, data, elliott_wave, liquidity_map, trend_engine, whale_scanner


def _sig(result) -> bool:
    """Defensively map a module output dict to its boolean signal."""
    if isinstance(result, dict):
        return bool(result.get("signal", False))
    return False


def generate(ticker: str) -> dict:
    """Run all five modules and combine into a conviction call.

    Returns {"ticker", "price", "signals": {"whale","wave3","squeeze",
    "trend","liquidity": bool}, "score": int (0-5), "conviction":
    "HIGH"|"MEDIUM"|"NONE", "details": {module-name: module-output-dict}}.
    """
    whale = whale_scanner.scan(ticker)
    elliott = elliott_wave.analyze(ticker)
    bollinger = bollinger_analyzer.analyze(ticker)
    trend = trend_engine.analyze_trend(ticker)
    liquidity = liquidity_map.get_liquidity(ticker)

    signals = {
        "whale": _sig(whale),
        "wave3": _sig(elliott),
        "squeeze": _sig(bollinger),
        "trend": _sig(trend),
        "liquidity": _sig(liquidity),
    }
    score = sum(1 for v in signals.values() if v)

    price = None
    if isinstance(whale, dict) and whale.get("price") is not None:
        try:
            price = float(whale["price"])
        except (TypeError, ValueError):
            price = None
    if price is None:
        try:
            price = data.get_price(ticker)
        except Exception:
            price = None

    if (signals["whale"] and signals["wave3"]
            and (signals["squeeze"] or signals["trend"]) and score >= 3):
        conviction = "HIGH"
    elif signals["whale"] and score >= 3:
        conviction = "MEDIUM"
    else:
        conviction = "NONE"

    details = {
        "whale": whale if isinstance(whale, dict) else {},
        "elliott": elliott if isinstance(elliott, dict) else {},
        "bollinger": bollinger if isinstance(bollinger, dict) else {},
        "trend": trend if isinstance(trend, dict) else {},
        "liquidity": liquidity if isinstance(liquidity, dict) else {},
    }

    return {
        "ticker": ticker,
        "price": price,
        "signals": signals,
        "score": score,
        "conviction": conviction,
        "details": details,
    }


def main(argv=None) -> int:
    """CLI: python Direction.py NVDA | python -m Direction.signal_generator --ticker NVDA"""
    p = argparse.ArgumentParser(
        description="Unified Direction signal (ThetaData)")
    p.add_argument("ticker", nargs="?", default=None,
                   help="equity symbol (positional, e.g. NVDA)")
    p.add_argument("--ticker", dest="ticker_flag", default=None,
                   help="equity symbol (flag, default: SPY)")
    args = p.parse_args(argv)
    ticker = args.ticker_flag or args.ticker or "SPY"

    r = generate(ticker)
    price_str = f" | price ${r['price']:.2f}" if r["price"] is not None else ""
    print(f"{r['ticker']}: {r['conviction']} | score {r['score']}/5{price_str}")
    for name in ("whale", "wave3", "squeeze", "trend", "liquidity"):
        print(f"  {name:<9} {'ON' if r['signals'][name] else 'off'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Replace `Direction/__init__.py`'s temporary stub**

```python
# Direction/__init__.py (final form)
"""Direction -- 5-input market-direction signal package (ThetaData-backed).

See Direction/README.md for the full signal catalog, conviction rules, and
Tools/ plugin wiring. `generate` is re-exported here so `from Direction
import generate` works without reaching into the submodule directly.
"""
from .signal_generator import generate

__all__ = ["generate"]
```

- [ ] **Step 5: Create the repo-root `Direction.py` CLI launcher**

```python
#!/usr/bin/env python3
# Direction.py (repo root)
"""Direction -- unified market-direction signal CLI (ThetaData).

Thin launcher for the Direction package's unified signal generator:

    python Direction.py NVDA
    python Direction.py --ticker SPY

Equivalent to: python -m Direction.signal_generator --ticker NVDA
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from Direction.signal_generator import main

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest Direction/tests/test_signal_generator.py -v`
Expected: PASS (6 tests)

- [ ] **Step 7: Run the FULL `Direction/tests/` suite to confirm Tasks 1-7 are all green together**

Run: `.venv\Scripts\python.exe -m pytest Direction/tests/ -v`
Expected: PASS (all tests from Tasks 1-7, ~55 tests total)

- [ ] **Step 8: Commit**

```bash
git add Direction/signal_generator.py Direction/__init__.py Direction.py Direction/tests/test_signal_generator.py
git commit -m "feat: add Direction/signal_generator.py group signal + root CLI launcher"
```

---

### Task 8: `Direction/README.md`

**Files:**
- Create: `Direction/README.md`

**Interfaces:**
- Consumes: nothing (documentation only)
- Produces: nothing (documentation only)

- [ ] **Step 1: Write `Direction/README.md`**

```markdown
# Direction -- 5-Input Market Direction Signal Engine

Unified direction signal built from **five independent inputs** (whale flow,
Elliott Wave, Bollinger Bands, multi-timeframe trend, liquidity zones).
ThetaData-backed only.

Ported from an external devnotes research package (`Dealer_pos_devnotes.tar.gz`,
sourced from a separate `/home/bottl/Financial_Development` environment) --
see `docs/superpowers/plans/2026-08-06-direction-tools-suite.md` for the full
port plan, what changed vs. the source package, and why.

## Package layout

| File | Role |
|---|---|
| `data.py` | Single ThetaData access layer (price, OHLCV, chains, GEX) |
| `whale_scanner.py` | Live whale-flow scanner (`scan`) -- wraps `Vol_Suite.whale_scanner.classify_whale_bias` |
| `elliott_wave.py` | Wave counting / wave-3 momentum (`analyze`) |
| `bollinger_analyzer.py` | Squeeze + band-thrust regime (`analyze`) |
| `trend_engine.py` | ADX + MA20/MA50 multi-timeframe trend (`analyze_trend`) |
| `liquidity_map.py` | Max pain (proper payout-minimization, not spot-proximity) / OI strike walls / PCR / GEX (`get_liquidity`) |
| `signal_generator.py` | **Unified layer** -- combines all five into one conviction |

## Relationship to Vol_Suite's whale-flow backtest port

`Vol_Suite/whale_scanner.py` (added 2026-08-06, see
`docs/superpowers/specs/2026-08-06-whale-sign-model-backtest-design.md`) is a
**pure, network-free classifier** (`classify_whale_bias`) used by
`backtest_stage3.py`'s historical backtest. `Direction/whale_scanner.py` here
is its **live** sibling: it fetches a fresh chain via `Direction.data` and
calls that exact same classifier, so both paths agree on what counts as
"whale" flow -- no second copy of the threshold/ratio math.

## Standalone module usage

Every module runs on its own (ThetaData only):

```bash
python -m Direction.whale_scanner SPY
python -m Direction.elliott_wave SPY
python -m Direction.bollinger_analyzer SPY
python -m Direction.trend_engine SPY
python -m Direction.liquidity_map SPY
```

Each module returns a dict contract ending in a boolean `signal`:
`whale_scanner.scan(ticker)`, `elliott_wave.analyze(ticker)`,
`bollinger_analyzer.analyze(ticker)`, `trend_engine.analyze_trend(ticker)`,
`liquidity_map.get_liquidity(ticker)`.

## Unified usage

Combine all five inputs into a single conviction call:

```bash
python Direction.py NVDA
python -m Direction.signal_generator --ticker NVDA
python Direction.py   # default ticker SPY
```

Programmatic:

```python
from Direction import generate

r = generate("NVDA")
# {
#   "ticker": "NVDA",
#   "price": 130.55,
#   "signals": {"whale": True, "wave3": True, "squeeze": False,
#               "trend": True, "liquidity": True},
#   "score": 4,                      # number of signals firing (0-5)
#   "conviction": "HIGH",            # HIGH | MEDIUM | NONE
#   "details": {"whale": {...}, "elliott": {...}, "bollinger": {...},
#               "trend": {...}, "liquidity": {...}},
# }
```

## Conviction rules

| Conviction | Requirement | Tradeable |
|---|---|---|
| **HIGH** | whale **AND** wave3 **AND** (squeeze **OR** trend) **AND** score >= 3 | Yes |
| **MEDIUM** | whale **AND** score >= 3, and not HIGH | Yes (smaller) |
| **NONE** | anything else -- **no whale signal = no trade** | No |

- `score` = number of the five signals firing (0-5).
- Whale flow is the mandatory gate: without it, conviction is always NONE.
- Module outputs are mapped defensively (`result.get("signal", False)`), so a
  module returning `None`/`{}` on a data hiccup degrades to "off" instead of
  crashing the run.

## Platform integration (Tools registry)

All six modules are wired into the `Tools` plugin platform as registered
`ToolSpec` adapters (see `docs/superpowers/plans/2026-08-06-direction-tools-suite.md`
Tasks 10-16), invocable from the dashboard's `/tools` page or programmatically:

| Slug | Adapter | Backing module |
|---|---|---|
| `whale-flow` | `Tools/tools/whale_flow_tool.py` | `Direction.whale_scanner.scan` |
| `elliott-wave` | `Tools/tools/elliott_wave_tool.py` | `Direction.elliott_wave.analyze` |
| `bollinger` | `Tools/tools/bollinger_tool.py` | `Direction.bollinger_analyzer.analyze` |
| `trend-engine` | `Tools/tools/trend_engine_tool.py` | `Direction.trend_engine.analyze_trend` |
| `liquidity-map` | `Tools/tools/liquidity_map_tool.py` | `Direction.liquidity_map.get_liquidity` |
| `direction-signal` | `Tools/tools/direction_signal_tool.py` | `Direction.signal_generator.generate` |

These are **live** ThetaData tools (not artifact readers), so they require a
suite context with a ticker:

```python
from Tools.registry import get_tool

result = get_tool("direction-signal").run({"focus": {"ticker": "NVDA"}})
result = get_tool("whale-flow").run({"focus": {"ticker": "NVDA"}})
```

The unified `direction-signal` tool is the efficient group entry point --
one process, one shared 300s data cache (`DIRECTION_DATA_TTL` env var)
across all five inputs.

## Scope note

These tools are standalone -- **none of the five signals is wired into
`Vol_Suite/dealer_positioning.py`'s live `VALID_SIGN_MODELS`**. That stays
scoped exactly as decided in
`docs/superpowers/specs/2026-08-06-whale-sign-model-backtest-design.md`:
the source package's own numbers don't survive proper multiple-comparisons
correction, so nothing here feeds a live sign convention without a
separate, deliberate decision to do so.

## Data note

All market data comes from **ThetaData** through `Direction.data` (spot,
EOD OHLCV, option chains, dealer positioning). If credentials are missing or
a fetch fails, fetchers return `None` and modules degrade gracefully.
```

- [ ] **Step 2: Commit**

```bash
git add Direction/README.md
git commit -m "docs: add Direction/README.md"
```

---

### Task 9: Fix `pyproject.toml` `testpaths` (pre-existing gap: `Tools/tests` was never included)

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: nothing
- Produces: nothing (test-discovery configuration only)

- [ ] **Step 1: Confirm the gap**

Run: `.venv\Scripts\python.exe -m pytest --collect-only -q 2>&1 | Select-String "Tools/tests"`
Expected: no output (Tools/tests is not currently collected by plain `pytest`)

- [ ] **Step 2: Edit `pyproject.toml`**

Change:
```toml
testpaths = ["tests", "shared/tests", "Vol_Suite/tests", "Options_Suite/tests", "VaR_Tools_Simulations/tests", "sentiment-scanner/tests"]
```
To:
```toml
testpaths = ["tests", "shared/tests", "Vol_Suite/tests", "Options_Suite/tests", "VaR_Tools_Simulations/tests", "sentiment-scanner/tests", "Tools/tests", "Direction/tests"]
```

- [ ] **Step 3: Verify both test directories are now collected**

Run: `.venv\Scripts\python.exe -m pytest --collect-only -q 2>&1 | Select-String "Tools/tests|Direction/tests"`
Expected: multiple matching lines, one per test file in each directory

- [ ] **Step 4: Run the full monorepo test suite to confirm nothing regressed**

Run: `.venv\Scripts\python.exe -m pytest -m unit -q`
Expected: PASS, higher total test count than before (now includes `Tools/tests/*` and `Direction/tests/*`)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add Tools/tests and Direction/tests to pytest testpaths"
```

---

### Task 10: `Tools/tools/whale_flow_tool.py`

**Files:**
- Create: `Tools/tools/whale_flow_tool.py`
- Modify: `Tools/registry.py`
- Test: `Tools/tests/test_registry.py` (extend)

**Interfaces:**
- Consumes: `Direction.whale_scanner.scan` (Task 6), `Tools.registry.ToolSpec`
- Produces: `run(context: dict) -> dict`, `TOOL_SPEC` (slug `whale-flow`) — registered in
  `Tools.registry.TOOLS`.

- [ ] **Step 1: Write the failing test (extend `Tools/tests/test_registry.py`)**

```python
# Tools/tests/test_registry.py -- ADD this test function (keep all existing ones)

def test_whale_flow_tool_is_registered():
    tool = get_tool("whale-flow")
    assert tool.slug == "whale-flow"
    assert tool.name == "Whale Flow Tool"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest Tools/tests/test_registry.py::test_whale_flow_tool_is_registered -v`
Expected: FAIL with `KeyError: "No tool registered with slug 'whale-flow'..."`

- [ ] **Step 3: Create `Tools/tools/whale_flow_tool.py`**

```python
# Tools/tools/whale_flow_tool.py
"""whale_flow_tool.py

Wraps Direction/whale_scanner.py's scan() (live ThetaData whale-flow
premium classifier) as a standalone Tool.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from Tools.registry import ToolSpec  # noqa: E402


def run(context: Dict[str, Any]) -> Dict[str, Any]:
    """context: a validated suite_context.json dict. Requires
    context.focus.ticker (or context['ticker']). Optional overrides:
    'min_premium' (float, default $25K), 'threshold_bps' (float, switches
    to a premium-relative bar -- see Vol_Suite.whale_scanner's threshold
    docstring). Returns Direction.whale_scanner.scan()'s dict verbatim.
    """
    from Direction import whale_scanner

    focus = context.get("focus") or {}
    ticker = context.get("ticker") or focus.get("ticker")
    if not ticker:
        raise ValueError("whale-flow tool requires a ticker (context['ticker'] or context.focus.ticker)")

    kwargs: Dict[str, Any] = {}
    if context.get("min_premium") is not None:
        kwargs["min_premium"] = float(context["min_premium"])
    if context.get("threshold_bps") is not None:
        kwargs["threshold_bps"] = float(context["threshold_bps"])

    return whale_scanner.scan(ticker, **kwargs)


TOOL_SPEC = ToolSpec(
    name="Whale Flow Tool",
    slug="whale-flow",
    description=(
        "Live large-premium option-flow bias (bullish/bearish/neutral) for "
        "a suite context's focus ticker, via Direction.whale_scanner -- the "
        "same classifier backtest_stage3.py's whale-flow backtest column "
        "uses, run against the nearest live expiry instead of historical "
        "rows."
    ),
    run=run,
)
```

- [ ] **Step 4: Register it in `Tools/registry.py`**

Change:
```python
    from Tools.tools import options_strategy_tool
    from Tools.tools import backtesting_tool

    return [
        options_strategy_tool.TOOL_SPEC,
        backtesting_tool.TOOL_SPEC,
        # Add new tools' TOOL_SPEC here -- see module docstring above.
    ]
```
To:
```python
    from Tools.tools import options_strategy_tool
    from Tools.tools import backtesting_tool
    from Tools.tools import whale_flow_tool

    return [
        options_strategy_tool.TOOL_SPEC,
        backtesting_tool.TOOL_SPEC,
        whale_flow_tool.TOOL_SPEC,
        # Add new tools' TOOL_SPEC here -- see module docstring above.
    ]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest Tools/tests/test_registry.py -v`
Expected: PASS (all existing + new test)

- [ ] **Step 6: Commit**

```bash
git add Tools/tools/whale_flow_tool.py Tools/registry.py Tools/tests/test_registry.py
git commit -m "feat: register Whale Flow Tool in Tools/ plugin registry"
```

---

### Task 11: `Tools/tools/elliott_wave_tool.py`

**Files:**
- Create: `Tools/tools/elliott_wave_tool.py`
- Modify: `Tools/registry.py`
- Test: `Tools/tests/test_registry.py` (extend)

**Interfaces:**
- Consumes: `Direction.elliott_wave.analyze` (Task 2), `Tools.registry.ToolSpec`
- Produces: `run(context: dict) -> dict`, `TOOL_SPEC` (slug `elliott-wave`)

- [ ] **Step 1: Write the failing test**

```python
# Tools/tests/test_registry.py -- ADD

def test_elliott_wave_tool_is_registered():
    tool = get_tool("elliott-wave")
    assert tool.slug == "elliott-wave"
    assert tool.name == "Elliott Wave Tool"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest Tools/tests/test_registry.py::test_elliott_wave_tool_is_registered -v`
Expected: FAIL with `KeyError`

- [ ] **Step 3: Create `Tools/tools/elliott_wave_tool.py`**

```python
# Tools/tools/elliott_wave_tool.py
"""elliott_wave_tool.py

Wraps Direction/elliott_wave.py's analyze() (live ThetaData Elliott Wave
counter -- wave-3 momentum detection) as a standalone Tool.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from Tools.registry import ToolSpec  # noqa: E402


def run(context: Dict[str, Any]) -> Dict[str, Any]:
    """context: a validated suite_context.json dict. Requires
    context.focus.ticker (or context['ticker']). Returns
    Direction.elliott_wave.analyze()'s dict verbatim: wave_count,
    wave_number, wave_type, signal.
    """
    from Direction import elliott_wave

    focus = context.get("focus") or {}
    ticker = context.get("ticker") or focus.get("ticker")
    if not ticker:
        raise ValueError("elliott-wave tool requires a ticker (context['ticker'] or context.focus.ticker)")

    return elliott_wave.analyze(ticker)


TOOL_SPEC = ToolSpec(
    name="Elliott Wave Tool",
    slug="elliott-wave",
    description=(
        "Live Elliott Wave count for a suite context's focus ticker -- "
        "flags Wave 3 (strongest momentum entry) vs Wave 1/5 (exhaustion "
        "warning) off 3 months of daily closes."
    ),
    run=run,
)
```

- [ ] **Step 4: Register it in `Tools/registry.py`**

Change:
```python
    from Tools.tools import options_strategy_tool
    from Tools.tools import backtesting_tool
    from Tools.tools import whale_flow_tool

    return [
        options_strategy_tool.TOOL_SPEC,
        backtesting_tool.TOOL_SPEC,
        whale_flow_tool.TOOL_SPEC,
        # Add new tools' TOOL_SPEC here -- see module docstring above.
    ]
```
To:
```python
    from Tools.tools import options_strategy_tool
    from Tools.tools import backtesting_tool
    from Tools.tools import whale_flow_tool
    from Tools.tools import elliott_wave_tool

    return [
        options_strategy_tool.TOOL_SPEC,
        backtesting_tool.TOOL_SPEC,
        whale_flow_tool.TOOL_SPEC,
        elliott_wave_tool.TOOL_SPEC,
        # Add new tools' TOOL_SPEC here -- see module docstring above.
    ]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest Tools/tests/test_registry.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add Tools/tools/elliott_wave_tool.py Tools/registry.py Tools/tests/test_registry.py
git commit -m "feat: register Elliott Wave Tool in Tools/ plugin registry"
```

---

### Task 12: `Tools/tools/bollinger_tool.py`

**Files:**
- Create: `Tools/tools/bollinger_tool.py`
- Modify: `Tools/registry.py`
- Test: `Tools/tests/test_registry.py` (extend)

**Interfaces:**
- Consumes: `Direction.bollinger_analyzer.analyze` (Task 3), `Tools.registry.ToolSpec`
- Produces: `run(context: dict) -> dict`, `TOOL_SPEC` (slug `bollinger`)

- [ ] **Step 1: Write the failing test**

```python
# Tools/tests/test_registry.py -- ADD

def test_bollinger_tool_is_registered():
    tool = get_tool("bollinger")
    assert tool.slug == "bollinger"
    assert tool.name == "Bollinger Bands Tool"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest Tools/tests/test_registry.py::test_bollinger_tool_is_registered -v`
Expected: FAIL with `KeyError`

- [ ] **Step 3: Create `Tools/tools/bollinger_tool.py`**

```python
# Tools/tools/bollinger_tool.py
"""bollinger_tool.py

Wraps Direction/bollinger_analyzer.py's analyze() (live ThetaData squeeze +
band-thrust regime detector) as a standalone Tool.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from Tools.registry import ToolSpec  # noqa: E402


def run(context: Dict[str, Any]) -> Dict[str, Any]:
    """context: a validated suite_context.json dict. Requires
    context.focus.ticker (or context['ticker']). Returns
    Direction.bollinger_analyzer.analyze()'s dict verbatim: squeeze,
    regime, signal.
    """
    from Direction import bollinger_analyzer

    focus = context.get("focus") or {}
    ticker = context.get("ticker") or focus.get("ticker")
    if not ticker:
        raise ValueError("bollinger tool requires a ticker (context['ticker'] or context.focus.ticker)")

    return bollinger_analyzer.analyze(ticker)


TOOL_SPEC = ToolSpec(
    name="Bollinger Bands Tool",
    slug="bollinger",
    description=(
        "Live Bollinger Band squeeze/thrust regime for a suite context's "
        "focus ticker -- squeeze flags an imminent volatility expansion, "
        "upper/lower thrust flags momentum continuation."
    ),
    run=run,
)
```

- [ ] **Step 4: Register it in `Tools/registry.py`**

Change:
```python
    from Tools.tools import whale_flow_tool
    from Tools.tools import elliott_wave_tool

    return [
        options_strategy_tool.TOOL_SPEC,
        backtesting_tool.TOOL_SPEC,
        whale_flow_tool.TOOL_SPEC,
        elliott_wave_tool.TOOL_SPEC,
        # Add new tools' TOOL_SPEC here -- see module docstring above.
    ]
```
To:
```python
    from Tools.tools import whale_flow_tool
    from Tools.tools import elliott_wave_tool
    from Tools.tools import bollinger_tool

    return [
        options_strategy_tool.TOOL_SPEC,
        backtesting_tool.TOOL_SPEC,
        whale_flow_tool.TOOL_SPEC,
        elliott_wave_tool.TOOL_SPEC,
        bollinger_tool.TOOL_SPEC,
        # Add new tools' TOOL_SPEC here -- see module docstring above.
    ]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest Tools/tests/test_registry.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add Tools/tools/bollinger_tool.py Tools/registry.py Tools/tests/test_registry.py
git commit -m "feat: register Bollinger Bands Tool in Tools/ plugin registry"
```

---

### Task 13: `Tools/tools/trend_engine_tool.py`

**Files:**
- Create: `Tools/tools/trend_engine_tool.py`
- Modify: `Tools/registry.py`
- Test: `Tools/tests/test_registry.py` (extend)

**Interfaces:**
- Consumes: `Direction.trend_engine.analyze_trend` (Task 4), `Tools.registry.ToolSpec`
- Produces: `run(context: dict) -> dict`, `TOOL_SPEC` (slug `trend-engine`)

- [ ] **Step 1: Write the failing test**

```python
# Tools/tests/test_registry.py -- ADD

def test_trend_engine_tool_is_registered():
    tool = get_tool("trend-engine")
    assert tool.slug == "trend-engine"
    assert tool.name == "Trend Engine Tool"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest Tools/tests/test_registry.py::test_trend_engine_tool_is_registered -v`
Expected: FAIL with `KeyError`

- [ ] **Step 3: Create `Tools/tools/trend_engine_tool.py`**

```python
# Tools/tools/trend_engine_tool.py
"""trend_engine_tool.py

Wraps Direction/trend_engine.py's analyze_trend() (live ThetaData
ADX + MA20/MA50 multi-timeframe trend engine) as a standalone Tool.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from Tools.registry import ToolSpec  # noqa: E402


def run(context: Dict[str, Any]) -> Dict[str, Any]:
    """context: a validated suite_context.json dict. Requires
    context.focus.ticker (or context['ticker']). Returns
    Direction.trend_engine.analyze_trend()'s dict verbatim: daily, weekly,
    monthly (each {"adx", "ma"}), adx_ok, aligned, signal.
    """
    from Direction import trend_engine

    focus = context.get("focus") or {}
    ticker = context.get("ticker") or focus.get("ticker")
    if not ticker:
        raise ValueError("trend-engine tool requires a ticker (context['ticker'] or context.focus.ticker)")

    return trend_engine.analyze_trend(ticker)


TOOL_SPEC = ToolSpec(
    name="Trend Engine Tool",
    slug="trend-engine",
    description=(
        "Live multi-timeframe (daily/weekly/monthly) ADX + MA20/MA50 trend "
        "read for a suite context's focus ticker -- signal fires only when "
        "ADX confirms trend strength AND daily/weekly MA alignment agree."
    ),
    run=run,
)
```

- [ ] **Step 4: Register it in `Tools/registry.py`**

Change:
```python
    from Tools.tools import bollinger_tool

    return [
        options_strategy_tool.TOOL_SPEC,
        backtesting_tool.TOOL_SPEC,
        whale_flow_tool.TOOL_SPEC,
        elliott_wave_tool.TOOL_SPEC,
        bollinger_tool.TOOL_SPEC,
        # Add new tools' TOOL_SPEC here -- see module docstring above.
    ]
```
To:
```python
    from Tools.tools import bollinger_tool
    from Tools.tools import trend_engine_tool

    return [
        options_strategy_tool.TOOL_SPEC,
        backtesting_tool.TOOL_SPEC,
        whale_flow_tool.TOOL_SPEC,
        elliott_wave_tool.TOOL_SPEC,
        bollinger_tool.TOOL_SPEC,
        trend_engine_tool.TOOL_SPEC,
        # Add new tools' TOOL_SPEC here -- see module docstring above.
    ]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest Tools/tests/test_registry.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add Tools/tools/trend_engine_tool.py Tools/registry.py Tools/tests/test_registry.py
git commit -m "feat: register Trend Engine Tool in Tools/ plugin registry"
```

---

### Task 14: `Tools/tools/liquidity_map_tool.py`

**Files:**
- Create: `Tools/tools/liquidity_map_tool.py`
- Modify: `Tools/registry.py`
- Test: `Tools/tests/test_registry.py` (extend)

**Interfaces:**
- Consumes: `Direction.liquidity_map.get_liquidity` (Task 5), `Tools.registry.ToolSpec`
- Produces: `run(context: dict) -> dict`, `TOOL_SPEC` (slug `liquidity-map`)

- [ ] **Step 1: Write the failing test**

```python
# Tools/tests/test_registry.py -- ADD

def test_liquidity_map_tool_is_registered():
    tool = get_tool("liquidity-map")
    assert tool.slug == "liquidity-map"
    assert tool.name == "Liquidity Map Tool"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest Tools/tests/test_registry.py::test_liquidity_map_tool_is_registered -v`
Expected: FAIL with `KeyError`

- [ ] **Step 3: Create `Tools/tools/liquidity_map_tool.py`**

```python
# Tools/tools/liquidity_map_tool.py
"""liquidity_map_tool.py

Wraps Direction/liquidity_map.py's get_liquidity() (live ThetaData max
pain / OI strike walls / PCR / GEX proximity map) as a standalone Tool.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from Tools.registry import ToolSpec  # noqa: E402


def run(context: Dict[str, Any]) -> Dict[str, Any]:
    """context: a validated suite_context.json dict. Requires
    context.focus.ticker (or context['ticker']). Returns
    Direction.liquidity_map.get_liquidity()'s dict verbatim: price, expiry,
    max_pain, call_wall, put_wall, pcr, signal, dealer.
    """
    from Direction import liquidity_map

    focus = context.get("focus") or {}
    ticker = context.get("ticker") or focus.get("ticker")
    if not ticker:
        raise ValueError("liquidity-map tool requires a ticker (context['ticker'] or context.focus.ticker)")

    return liquidity_map.get_liquidity(ticker)


TOOL_SPEC = ToolSpec(
    name="Liquidity Map Tool",
    slug="liquidity-map",
    description=(
        "Live max pain (payout-minimization, not a spot-proximity "
        "shortcut), OI call/put strike walls, put/call ratio, and GEX "
        "proximity for a suite context's focus ticker's nearest expiry."
    ),
    run=run,
)
```

- [ ] **Step 4: Register it in `Tools/registry.py`**

Change:
```python
    from Tools.tools import trend_engine_tool

    return [
        options_strategy_tool.TOOL_SPEC,
        backtesting_tool.TOOL_SPEC,
        whale_flow_tool.TOOL_SPEC,
        elliott_wave_tool.TOOL_SPEC,
        bollinger_tool.TOOL_SPEC,
        trend_engine_tool.TOOL_SPEC,
        # Add new tools' TOOL_SPEC here -- see module docstring above.
    ]
```
To:
```python
    from Tools.tools import trend_engine_tool
    from Tools.tools import liquidity_map_tool

    return [
        options_strategy_tool.TOOL_SPEC,
        backtesting_tool.TOOL_SPEC,
        whale_flow_tool.TOOL_SPEC,
        elliott_wave_tool.TOOL_SPEC,
        bollinger_tool.TOOL_SPEC,
        trend_engine_tool.TOOL_SPEC,
        liquidity_map_tool.TOOL_SPEC,
        # Add new tools' TOOL_SPEC here -- see module docstring above.
    ]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest Tools/tests/test_registry.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add Tools/tools/liquidity_map_tool.py Tools/registry.py Tools/tests/test_registry.py
git commit -m "feat: register Liquidity Map Tool in Tools/ plugin registry"
```

---

### Task 15: `Tools/tools/direction_signal_tool.py` (the group tool)

**Files:**
- Create: `Tools/tools/direction_signal_tool.py`
- Modify: `Tools/registry.py`
- Test: `Tools/tests/test_registry.py` (extend)

**Interfaces:**
- Consumes: `Direction.signal_generator.generate` (Task 7), `Tools.registry.ToolSpec`
- Produces: `run(context: dict) -> dict`, `TOOL_SPEC` (slug `direction-signal`)

- [ ] **Step 1: Write the failing test**

```python
# Tools/tests/test_registry.py -- ADD

def test_direction_signal_tool_is_registered():
    tool = get_tool("direction-signal")
    assert tool.slug == "direction-signal"
    assert tool.name == "Direction Signal Tool"


def test_all_six_direction_tools_and_original_two_are_registered():
    slugs = {tool.slug for tool in TOOLS}
    expected = {
        "options-strategy", "backtesting", "whale-flow", "elliott-wave",
        "bollinger", "trend-engine", "liquidity-map", "direction-signal",
    }
    assert expected.issubset(slugs)
    assert len(TOOLS) >= 8
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest Tools/tests/test_registry.py::test_direction_signal_tool_is_registered -v`
Expected: FAIL with `KeyError`

- [ ] **Step 3: Create `Tools/tools/direction_signal_tool.py`**

```python
# Tools/tools/direction_signal_tool.py
"""direction_signal_tool.py

Wraps Direction/signal_generator.py's generate() -- the GROUP tool. Runs
all five Direction modules (whale flow, Elliott Wave, Bollinger, trend,
liquidity) against a suite context's focus ticker in one call and returns
the combined conviction call, using one shared 300s Direction.data cache
across all five fetches instead of five separate tool invocations.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from Tools.registry import ToolSpec  # noqa: E402


def run(context: Dict[str, Any]) -> Dict[str, Any]:
    """context: a validated suite_context.json dict. Requires
    context.focus.ticker (or context['ticker']). Returns
    Direction.signal_generator.generate()'s dict verbatim: ticker, price,
    signals (per-module booleans), score (0-5), conviction
    (HIGH|MEDIUM|NONE), details (each module's full output dict).
    """
    from Direction import signal_generator

    focus = context.get("focus") or {}
    ticker = context.get("ticker") or focus.get("ticker")
    if not ticker:
        raise ValueError("direction-signal tool requires a ticker (context['ticker'] or context.focus.ticker)")

    return signal_generator.generate(ticker)


TOOL_SPEC = ToolSpec(
    name="Direction Signal Tool",
    slug="direction-signal",
    description=(
        "Runs all five Direction signals (whale flow, Elliott Wave, "
        "Bollinger, multi-timeframe trend, liquidity zones) against a "
        "suite context's focus ticker and combines them into one "
        "conviction call (HIGH/MEDIUM/NONE) -- the group entry point "
        "alongside the five individual Direction tools."
    ),
    run=run,
)
```

- [ ] **Step 4: Register it in `Tools/registry.py`**

Change:
```python
    from Tools.tools import liquidity_map_tool

    return [
        options_strategy_tool.TOOL_SPEC,
        backtesting_tool.TOOL_SPEC,
        whale_flow_tool.TOOL_SPEC,
        elliott_wave_tool.TOOL_SPEC,
        bollinger_tool.TOOL_SPEC,
        trend_engine_tool.TOOL_SPEC,
        liquidity_map_tool.TOOL_SPEC,
        # Add new tools' TOOL_SPEC here -- see module docstring above.
    ]
```
To:
```python
    from Tools.tools import liquidity_map_tool
    from Tools.tools import direction_signal_tool

    return [
        options_strategy_tool.TOOL_SPEC,
        backtesting_tool.TOOL_SPEC,
        whale_flow_tool.TOOL_SPEC,
        elliott_wave_tool.TOOL_SPEC,
        bollinger_tool.TOOL_SPEC,
        trend_engine_tool.TOOL_SPEC,
        liquidity_map_tool.TOOL_SPEC,
        direction_signal_tool.TOOL_SPEC,
        # Add new tools' TOOL_SPEC here -- see module docstring above.
    ]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest Tools/tests/test_registry.py -v`
Expected: PASS (all tests, including the new "all eight registered" check)

- [ ] **Step 6: Commit**

```bash
git add Tools/tools/direction_signal_tool.py Tools/registry.py Tools/tests/test_registry.py
git commit -m "feat: register Direction Signal Tool (group) in Tools/ plugin registry"
```

---

### Task 16: `Tools/README.md` update + full-suite verification

**Files:**
- Modify: `Tools/README.md`

**Interfaces:**
- Consumes: nothing (documentation only)
- Produces: nothing (documentation only)

- [ ] **Step 1: Add a new section to `Tools/README.md`, after "The two tools shipped today"**

```markdown
### The Direction tools (6 more, shipped 2026-08-06)

Five live ThetaData signals ported from `Direction/` (see `Direction/README.md`
for the full catalog and conviction rules), each callable individually or as
a group:

- **Whale Flow Tool** (`slug="whale-flow"`) -- large-premium option-flow bias
  (bullish/bearish/neutral), live. Delegates classification to
  `Vol_Suite.whale_scanner.classify_whale_bias`, the same function
  `backtest_stage3.py`'s backtest-only whale-flow column already uses.
- **Elliott Wave Tool** (`slug="elliott-wave"`) -- wave-3 momentum vs.
  wave-1/5 exhaustion, off 3 months of daily closes.
- **Bollinger Bands Tool** (`slug="bollinger"`) -- squeeze (imminent
  expansion) and upper/lower band thrust (momentum continuation).
- **Trend Engine Tool** (`slug="trend-engine"`) -- ADX + MA20/MA50
  alignment across daily/weekly/monthly timeframes.
- **Liquidity Map Tool** (`slug="liquidity-map"`) -- max pain (real
  payout-minimization, not a spot-proximity shortcut), OI call/put strike
  walls, put/call ratio, GEX proximity.
- **Direction Signal Tool** (`slug="direction-signal"`) -- the **group**
  entry point: runs all five above in one call against a shared 300s data
  cache and combines them into one conviction call (HIGH/MEDIUM/NONE).

```python
from Tools.registry import get_tool

# Individually:
whale = get_tool("whale-flow").run({"focus": {"ticker": "NVDA"}})
wave = get_tool("elliott-wave").run({"focus": {"ticker": "NVDA"}})

# As a group:
combined = get_tool("direction-signal").run({"focus": {"ticker": "NVDA"}})
print(combined["conviction"], combined["score"])
```

None of these six tools is wired into `Vol_Suite/dealer_positioning.py`'s
live sign models -- they're standalone, same as every other `Tools/` plugin.
```

- [ ] **Step 2: Run the ENTIRE Direction + Tools test suites together**

Run: `.venv\Scripts\python.exe -m pytest Direction/tests/ Tools/tests/ -v`
Expected: PASS, all tests from every prior task in this plan

- [ ] **Step 3: Run the full monorepo suite to confirm zero regressions anywhere**

Run: `.venv\Scripts\python.exe -m pytest -m unit -q`
Expected: PASS, total count includes every suite plus the new `Direction/tests/` and `Tools/tests/`
entries

- [ ] **Step 4: Sanity-check the registry end-to-end (import-time wiring, not a live network call)**

Run: `.venv\Scripts\python.exe -c "from Tools.registry import TOOLS; print(len(TOOLS)); print([t.slug for t in TOOLS])"`
Expected: `10` and a list containing all ten slugs (`options-strategy`, `backtesting`, `whale-flow`,
`elliott-wave`, `bollinger`, `trend-engine`, `liquidity-map`, `direction-signal`) — confirms every
adapter module imports cleanly with no circular-import or syntax errors, without needing live
ThetaData credentials (import-time only, `run()` is never called).

- [ ] **Step 5: Commit**

```bash
git add Tools/README.md
git commit -m "docs: document the 6 new Direction tools in Tools/README.md"
```

---

## Self-Review

**Spec coverage:**
- "the rest of the tools from direction.py wired into our tools plugin suite" — Tasks 2-6 port all
  four remaining signals + give whale-flow a live sibling; Tasks 10-15 wire all six as `Tools/`
  adapters. Covered.
- "callable individually and as a group" — five individual slugs (`whale-flow`, `elliott-wave`,
  `bollinger`, `trend-engine`, `liquidity-map`) plus one group slug (`direction-signal`) that runs all
  five and combines them. Covered (Tasks 10-15, Task 7's `signal_generator.generate`).
- "I leave to you what is an upgrade and what is not, or if it could be just because we do things
  better" — documented per-module in Global Constraints and inline: `liquidity_map.py`'s max-pain
  calc is a deliberate upgrade (payout-minimization instead of the source's spot-proximity
  approximation), `whale_scanner.py`'s live wrapper reuses the already-tested `Vol_Suite` classifier
  instead of duplicating it (DRY), `trend_engine.py`'s `_neutral_trend()` gained the `signal` key the
  original omitted. Everything else is a straight, verified-compatible port. Covered.
- "make an informed change on whales to prepare for being implemented with this... it's going to make
  the implementation plan regardless" — whale-flow is included as a full Direction tool (Task 6, Task
  10), built on top of the already-audited `Vol_Suite.whale_scanner.classify_whale_bias` rather than a
  fresh copy, which is the "informed" version of the port. It stays `Tools/`-only per the existing
  scope decision (not live-wired into `dealer_positioning.py`) — flagged explicitly in Global
  Constraints and `Direction/README.md`'s Scope note so this doesn't silently expand later without a
  deliberate decision.

**Placeholder scan:** No TBD/TODO/"add error handling"/"similar to Task N" phrases anywhere in the
plan; every step has complete, runnable code.

**Type consistency:** `ToolSpec(name, slug, description, run)` used identically across all six new
adapter files, matching `Tools/registry.py`'s existing dataclass. Every adapter's `run(context)`
extracts `ticker` via the same `context.get("ticker") or focus.get("ticker")` pattern
`backtesting_tool.py` already establishes. Every Direction module's public function signature used in
a later task (`data.get_ohlcv`, `data.resample_ohlcv`, `whale_scanner.scan`, `elliott_wave.analyze`,
`bollinger_analyzer.analyze`, `trend_engine.analyze_trend`, `liquidity_map.get_liquidity`,
`signal_generator.generate`) matches its own Task's "Produces" line and its constructor Task's actual
implementation.
