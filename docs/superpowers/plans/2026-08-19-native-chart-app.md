# Native Chart App (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A standalone local chart window Jason leaves up all day and Hermes can read/act on — one ticker, live candles, Direction + EMA/VWAP/BB/volume, position-gated markers — without a 30-minute per-bar PH fan-out.

**Architecture:** New `chart_app/` process on **127.0.0.1:8791** (never 8787). SQLite bar cache is the tape. Indicators and Direction **price** legs run locally on cached OHLCV. Flow/gamma are optional sampled stamps, never a live call per bar. The browser window is the human surface; `GET /api/state` is the agent surface (same data, no OCR). Robinhood: Hermes places orders via existing RH MCP **only when Jason says so in chat**; the chart only **displays** whatever RH snapshot the agent POSTs.

**Tech Stack:** Python 3.12 repo `.venv`, FastAPI+uvicorn (already in repo), SQLite, vendored Apache ECharts (no CDN), existing `shared/spot_history.py`, `shared/chart_data.py`, `shared/candlestick_chart.apply_position_gate`, Direction **pure functions only**.

## Global Constraints

- Phase 1 only. Out of scope: bar replay, backtest, watchlist, dealer-model lines, suite widgets, migrating dashboard :8787, TradingView, Hermes Desktop panes.
- Standalone window. Do **not** add routes to `dashboard/app.py`. Do **not** bind 8787.
- Market data: ThetaData / PH v2 only. Never yfinance.
- Direction tool contract: do **not** modify `Direction/signal_generator.py` or the five modules (`elliott_wave.py`, `bollinger_analyzer.py`, `trend_engine.py`, `whale_scanner.py`, `liquidity_map.py`). Import their **pure functions** only.
- **No per-bar live PH.** `flow.*` and `dealer.weighted_greeks` are forbidden inside the bar loop. Price scores compute from cached OHLCV. Missing flow/liquidity → `False` (neutral), never fabricated.
- Position gate: reuse `shared.candlestick_chart.apply_position_gate`. Sell/hold only after a long. Window starts flat. Sell flattens.
- Robinhood: the chart server **never** calls RH or places orders. Hermes uses `mcp__robinhood__*` after an explicit chat instruction, then `POST /api/rh`. No extra confirm click (Jason’s rule). No autonomous / hot orders.
- Windows git-bash. Clean launcher: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe`.
- Bind `127.0.0.1` only.
- Stage only files the task lists. TDD each task. Commit with the stated message.

## File map

| Path | Role |
|---|---|
| `chart_app/bar_cache.py` | SQLite OHLCV cache, incremental upsert |
| `chart_app/score_engine.py` | Local per-bar Direction **price** scores + classic pile |
| `chart_app/snapshot.py` | `ChartState` dataclass → JSON for `/api/state` |
| `chart_app/server.py` | FastAPI: state, bars refresh, RH snapshot ingest |
| `chart_app/static/index.html` | One-ticker ECharts window |
| `chart_app/static/vendor/echarts.min.js` | Vendored, no CDN |
| `chart_app.bat` / `chart_app.sh` | Launch (mirror dashboard.bat style) |
| `chart_app/tests/test_bar_cache.py` | Cache tests |
| `chart_app/tests/test_score_engine.py` | Score + gate tests |
| `chart_app/tests/test_snapshot.py` | State contract tests |
| `chart_app/tests/test_server.py` | HTTP tests (TestClient, no live PH) |

## Later phases (do not implement here)

2. Bar replay + backtest. 3. Watchlist + more indicators. 4. Suite overlays (dealer model, variance widgets, screeners) and dashboard absorption.

---

### Task 1: Bar cache

**Files:**
- Create: `chart_app/__init__.py` (empty)
- Create: `chart_app/bar_cache.py`
- Create: `chart_app/tests/test_bar_cache.py`

**Interfaces:**
- Consumes: `shared.chart_data.CandleRecord`, `shared.spot_history.fetch_daily_candles` / `fetch_intraday_candles` (injected `provider` in tests)
- Produces: `BarCache(path)` with `upsert(ticker, interval, records: list[CandleRecord]) -> int`, `load(ticker, interval) -> list[CandleRecord]` (ascending), `last_ts(ticker, interval) -> datetime | None`

- [ ] **Step 1: Write the failing test**

```python
from datetime import datetime
from shared.chart_data import CandleRecord
from chart_app.bar_cache import BarCache

def _bar(ts, c):
    return CandleRecord(timestamp=datetime.fromisoformat(ts), open=c, high=c, low=c, close=c, volume=1)

def test_upsert_and_load_sorted(tmp_path):
    cache = BarCache(tmp_path / "bars.db")
    n = cache.upsert("SPY", "1d", [_bar("2026-08-18T00:00:00", 2), _bar("2026-08-17T00:00:00", 1)])
    assert n == 2
    loaded = cache.load("SPY", "1d")
    assert [b.close for b in loaded] == [1, 2]
    assert cache.last_ts("SPY", "1d") == datetime(2026, 8, 18)

def test_upsert_is_idempotent(tmp_path):
    cache = BarCache(tmp_path / "bars.db")
    cache.upsert("SPY", "1d", [_bar("2026-08-18T00:00:00", 1)])
    cache.upsert("SPY", "1d", [_bar("2026-08-18T00:00:00", 9)])
    loaded = cache.load("SPY", "1d")
    assert len(loaded) == 1
    assert loaded[0].close == 9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest chart_app/tests/test_bar_cache.py -q`
Expected: FAIL (module not found)

- [ ] **Step 3: Write minimal implementation**

SQLite table `bars(ticker TEXT, interval TEXT, ts TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL, PRIMARY KEY(ticker, interval, ts))`. `upsert` uses `INSERT OR REPLACE`. `load` `ORDER BY ts ASC`. Store `ts` as `datetime.isoformat()`.

- [ ] **Step 4: Run test to verify it passes**

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add chart_app/__init__.py chart_app/bar_cache.py chart_app/tests/test_bar_cache.py
git commit -m "feat(chart-app): sqlite bar cache for the native tape"
```

---

### Task 2: Local score engine (no live PH)

**Files:**
- Create: `chart_app/score_engine.py`
- Create: `chart_app/tests/test_score_engine.py`

**Interfaces:**
- Consumes: `list[CandleRecord]`; `Direction.elliott_wave.count_waves`; `Direction.bollinger_analyzer.get_bands` / `detect_squeeze` / `regime`; `Direction.trend_engine.adx` / `ma_alignment`; `shared.candlestick_chart.apply_position_gate`
- Produces:
  - `classic_overlays(records) -> dict` with keys `ema20`, `ema50`, `vwap`, `bb_mid`, `bb_upper`, `bb_lower` — each a `list[float | None]` same length as `records`
  - `price_scores(records) -> list[dict]` each `{"ts": iso, "score": int, "signals": {"whale": False, "wave3": bool, "squeeze": bool, "trend": bool, "liquidity": False}}`
  - `gated_markers(entries) -> list[str]` = `apply_position_gate(entries)`

Score = count of True among the five signals. `whale` and `liquidity` are **always False** in this engine (sampled PH is a later add). Short series → all False, score 0. Never raise.

Classic pile (exact):
- `ema20` / `ema50`: EMA of close; `None` until enough bars (20 / 50)
- `vwap`: cumulative typical price * volume / cumulative volume; if volume missing, use `1.0`
- `bb_*`: from `get_bands(closes, window=20)`; `None` until 20 bars

- [ ] **Step 1: Write the failing test**

```python
from datetime import datetime, timedelta
from shared.chart_data import CandleRecord
from chart_app.score_engine import classic_overlays, price_scores, gated_markers

def _series(n, start=100.0):
    t0 = datetime(2026, 1, 2)
    out = []
    px = start
    for i in range(n):
        px = start + i * 0.2
        out.append(CandleRecord(t0 + timedelta(days=i), px, px + 0.5, px - 0.5, px, 1000))
    return out

def test_classic_overlays_length_and_warmup():
    recs = _series(60)
    ov = classic_overlays(recs)
    assert set(ov) == {"ema20", "ema50", "vwap", "bb_mid", "bb_upper", "bb_lower"}
    assert all(len(ov[k]) == 60 for k in ov)
    assert ov["ema20"][18] is None and ov["ema20"][19] is not None
    assert ov["ema50"][48] is None and ov["ema50"][49] is not None

def test_price_scores_whale_and_liq_stay_false():
    recs = _series(80)
    rows = price_scores(recs)
    assert len(rows) == 80
    assert all(r["signals"]["whale"] is False and r["signals"]["liquidity"] is False for r in rows)
    assert all(0 <= r["score"] <= 3 for r in rows)  # only 3 price legs can be True

def test_gated_markers_need_a_long():
    entries = [{"score": s} for s in [0, 3, 4, 3, 0, 0]]
    assert gated_markers(entries) == ["none", "none", "buy", "hold", "sell", "none"]
```

- [ ] **Step 2: Run to verify fail**

`env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest chart_app/tests/test_score_engine.py -q`
Expected: FAIL

- [ ] **Step 3: Implement**

`price_scores`: for each index `i`, take `records[: i + 1]` (closed-bar). If `len < 50`, all price signals False. Else call the same pure-fn rules as `Direction/indicator.py::_price_signals` (read that file — `wave_type == "impulse_wave_3"`, `adx > 25` and bullish MA alignment). Do **not** import `_price_signals` if it fetches OHLCV; copy the boolean rules against the in-memory series only.

- [ ] **Step 4: Tests pass**

- [ ] **Step 5: Commit**

```bash
git add chart_app/score_engine.py chart_app/tests/test_score_engine.py
git commit -m "feat(chart-app): local price scores and classic overlays"
```

---

### Task 3: Snapshot contract

**Files:**
- Create: `chart_app/snapshot.py`
- Create: `chart_app/tests/test_snapshot.py`

**Interfaces:**
- Consumes: `BarCache`, `price_scores`, `classic_overlays`, `gated_markers`
- Produces: `build_state(cache, ticker, interval, rh=None) -> dict` with **exact keys**:

```python
{
  "ticker": str,
  "interval": str,
  "as_of": str | None,
  "bars": [{"ts", "open", "high", "low", "close", "volume"}, ...],
  "scores": [int, ...],          # raw, same length as bars
  "markers": [str, ...],         # gated, same length
  "overlays": {                  # classic pile, each list same length
      "ema20": [...], "ema50": [...], "vwap": [...],
      "bb_mid": [...], "bb_upper": [...], "bb_lower": [...],
  },
  "live": {"conviction": "HIGH"|"MEDIUM"|"NONE", "score": int},
  "rh": {"position": None | {"qty": float, "avg_price": float}, "fills": list},
}
```

Conviction from the last score using the same rule as `signal_generator.generate` **price-only**: HIGH if wave3 and (squeeze or trend) and score >= 3 (whale is False so HIGH/MEDIUM from whale will not fire — live stamp will usually be `NONE (n/5)`. That is honest. Do not fake whale.)

Empty cache → `bars: []`, `live: {NONE, 0}`, no raise.

- [ ] **Step 1: Failing test**

```python
from datetime import datetime
from shared.chart_data import CandleRecord
from chart_app.bar_cache import BarCache
from chart_app.snapshot import build_state

def test_build_state_empty(tmp_path):
    st = build_state(BarCache(tmp_path / "b.db"), "SPY", "1d")
    assert st["bars"] == []
    assert st["live"] == {"conviction": "NONE", "score": 0}
    assert st["rh"]["position"] is None

def test_build_state_lengths(tmp_path):
    cache = BarCache(tmp_path / "b.db")
    recs = [
        CandleRecord(datetime(2026, 1, 2), 100, 101, 99, 100, 1)
        for _ in range(3)
    ]
    # distinct timestamps
    recs = [CandleRecord(datetime(2026, 1, 2 + i), 100, 101, 99, 100.0 + i, 1) for i in range(3)]
    cache.upsert("SPY", "1d", recs)
    st = build_state(cache, "SPY", "1d")
    n = len(st["bars"])
    assert n == 3
    assert len(st["scores"]) == n and len(st["markers"]) == n
    assert all(len(st["overlays"][k]) == n for k in st["overlays"])
```

- [ ] **Step 2: Fail** then **Step 3: Implement** `build_state` in `chart_app/snapshot.py`

- [ ] **Step 4: Pass** then **Step 5: Commit**

```bash
git add chart_app/snapshot.py chart_app/tests/test_snapshot.py
git commit -m "feat(chart-app): agent snapshot contract"
```

---

### Task 4: FastAPI server (no live PH)

**Files:**
- Create: `chart_app/server.py`
- Create: `chart_app/tests/test_server.py`

**Interfaces:**
- `create_app(cache: BarCache, *, default_ticker="SPY", default_interval="15m") -> FastAPI`
- `GET /api/state` → `build_state(...)` plus `{"ok": true}`
- `POST /api/symbol` body `{"ticker": "SPY", "interval": "15m"}` → switches the in-memory symbol (validated via `shared.spot_history.validate_ticker` and `SUPPORTED_INTERVALS`). Does **not** fetch network in tests.
- `POST /api/rh` body `{"position": {"qty": float, "avg_price": float} | null, "fills": list}` → stored, next `/api/state` echoes it
- `GET /` → `chart_app/static/index.html` (404 until Task 5 adds the file; for this task return a 1-line HTML stub `"chart-app"` so the test can hit `/`)

In-memory process state: `ticker`, `interval`, `rh`. One cache instance.

- [ ] **Step 1: Failing tests**

```python
from fastapi.testclient import TestClient
from chart_app.bar_cache import BarCache
from chart_app.server import create_app

def test_state_and_rh(tmp_path):
    app = create_app(BarCache(tmp_path / "b.db"))
    c = TestClient(app)
    r = c.get("/api/state")
    assert r.status_code == 200
    assert r.json()["ticker"] == "SPY"
    r = c.post("/api/rh", json={"position": {"qty": 10, "avg_price": 400.0}, "fills": []})
    assert r.status_code == 200
    assert c.get("/api/state").json()["rh"]["position"]["qty"] == 10

def test_symbol_switch(tmp_path):
    app = create_app(BarCache(tmp_path / "b.db"))
    c = TestClient(app)
    r = c.post("/api/symbol", json={"ticker": "QQQ", "interval": "1d"})
    assert r.status_code == 200
    assert c.get("/api/state").json()["ticker"] == "QQQ"
```

- [ ] **Step 2: Fail** **Step 3: Implement** `create_app` in `chart_app/server.py` using FastAPI. Bind logic is launch-only (Task 6).

- [ ] **Step 4: Pass**

`env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest chart_app/tests/test_server.py -q`

- [ ] **Step 5: Commit** `feat(chart-app): local state API for the native window`

---

### Task 5: ECharts window

**Files:**
- Create: `chart_app/static/index.html`
- Create: `chart_app/static/vendor/echarts.min.js` (copy a local echarts build — if not present, `npm pack echarts` into `chart_app/static/vendor/` or download **once** into that folder; do not load from a CDN at runtime)
- Modify: `chart_app/server.py` to `StaticFiles` mount `/static` and serve `index.html` at `GET /`
- Modify: `chart_app/tests/test_server.py` — `GET /` contains `echarts` or `chart-app` and a `#chart` node

**UI (exact):**
- Dark background `#0e1117`, no page chrome beyond ticker/interval label + live stamp
- Candlestick series from `state.bars`
- Volume as a second grid
- Line series: ema20, ema50, vwap, bb_upper/mid/lower
- Scatter/markPoint for gated markers: `buy` lime ▲, `add` gold ◆, `hold` gray ●, `sell` orchid ▼, `none` skip
- Poll `GET /api/state` every 5 seconds
- Colors from the existing candlestick theme if easy; otherwise the hex above

- [ ] **Step 1: Test** `GET /` 200 and body includes `id="chart"`

- [ ] **Step 2: Fail** (no file)

- [ ] **Step 3: Write `index.html`** — single file, vanilla JS, `fetch('/api/state')`, `echarts.init`. No React. No CDN script tags.

- [ ] **Step 4: Pass** + open is manual (Task 6)

- [ ] **Step 5: Commit** `feat(chart-app): echarts window for one ticker`

---

### Task 6: Cache fill + launch (still no per-bar PH)

**Files:**
- Create: `chart_app/ingest.py`
- Create: `chart_app/tests/test_ingest.py`
- Create: `chart_app.bat`
- Create: `chart_app.sh`
- Modify: `chart_app/server.py` add `POST /api/refresh` that calls ingest with injected fetchers

**Interfaces:**
- `refresh_cache(cache, ticker, interval, lookback, *, daily_fn, intrad_fn) -> int`  
  If `interval == "1d"`: `payload = daily_fn(ticker, lookback=lookback)` else `intrad_fn(ticker, interval=interval, lookback=lookback)`. Upsert `payload.observations`. Return row count upserted.
- Default production fetchers: `shared.spot_history.fetch_daily_candles` / `fetch_intraday_candles`.
- `POST /api/refresh` body `{"lookback": "30d"}` uses those defaults. Tests inject via `create_app(..., daily_fn=..., intrad_fn=...)`.

`chart_app.bat` (Windows, same venv style as `dashboard.bat`):

```bat
@echo off
cd /d "%~dp0"
set PYTHONPATH=
set VIRTUAL_ENV=
start "" "%~dp0.venv\Scripts\python.exe" -m uvicorn chart_app.server:app --host 127.0.0.1 --port 8791
timeout /t 2 >nul
start "" "http://127.0.0.1:8791/"
```

Expose `app = create_app(BarCache(Path("artifacts/chart_app_bars.db")))` at module level in `server.py` for uvicorn.

- [ ] **Step 1: Test ingest with fake payload (no network)**

```python
from datetime import datetime
from types import SimpleNamespace
from shared.chart_data import CandleRecord, CandlePayload
from chart_app.bar_cache import BarCache
from chart_app.ingest import refresh_cache

def test_refresh_cache_upserts(tmp_path):
    rec = CandleRecord(datetime(2026, 8, 18), 1, 1, 1, 1, 1)
    payload = CandlePayload("SPY", "1d", "5d", "injected", (rec,))
    n = refresh_cache(BarCache(tmp_path / "b.db"), "SPY", "1d", "5d",
                      daily_fn=lambda t, lookback: payload,
                      intrad_fn=None)
    assert n == 1
```

- [ ] **Step 2–4: TDD ingest + wire `/api/refresh`**

- [ ] **Step 5: Commit** `feat(chart-app): cache ingest and localhost:8791 launcher`

Do **not** run a live 1y refresh in this task. A 5d/15m or 30d/1d refresh is enough for a smoke comment in the report.

---

### Task 7: Agent skill + RH display loop

**Files:**
- Create: Hermes skill `C:/Users/bottl/AppData/Local/hermes/skills/quantitative-finance/native-chart-app/SKILL.md` (outside git, no commit) — **and** a repo copy `chart_app/SKILL.md` that is committed so the repo documents the agent contract
- Modify: `chart_app/tests/test_server.py` — RH snapshot round-trip already in Task 4; add test that `/api/rh` rejects a body with `{"order": ...}` (no order endpoint)

**Skill body (exact trigger + commands):**

```markdown
# Native Chart App

Use when Jason says launch the chart, native chart, live tape, or "what's on the chart".

Launch: from repo root
`env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m uvicorn chart_app.server:app --host 127.0.0.1 --port 8791`
Then open http://127.0.0.1:8791/

Read: GET http://127.0.0.1:8791/api/state
Switch: POST /api/symbol {"ticker":"SPY","interval":"15m"}
Refresh bars: POST /api/refresh {"lookback":"30d"}
Push RH: after mcp__robinhood__get_equity_positions / get_equity_orders, POST /api/rh
  {"position": {"qty": <float>, "avg_price": <float>} or null, "fills": [...]}

Orders: NEVER call a chart URL to place. Place only via mcp__robinhood__place_equity_order
and only when Jason explicitly says to in chat. Then refresh RH snapshot.

Do not fan out PH per bar. Do not use dashboard :8787.
```

- [ ] **Step 1: Test that `create_app` has no `/api/order` route** (404)

- [ ] **Step 2–4: 404 + write both SKILL.md files**

- [ ] **Step 5: Commit repo files only** `docs(chart-app): agent contract and no-order API`

---

### Task 8: Live smoke (bounded) + honest docs

**Files:**
- Modify: `chart_app/server.py` module docstring (honest: price-only scores, whale/liq off, sampled PH later)
- Create: `chart_app/README.md` (launch, port, what is / is not computed)

**Live smoke (required, bounded):**

```bash
env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -c "
from pathlib import Path
from chart_app.bar_cache import BarCache
from chart_app.ingest import refresh_cache
from shared.spot_history import fetch_daily_candles
from chart_app.snapshot import build_state
cache = BarCache(Path('artifacts/chart_app_bars.db'))
n = refresh_cache(cache, 'SPY', '1d', '30d', daily_fn=fetch_daily_candles, intrad_fn=None)
st = build_state(cache, 'SPY', '1d')
print('upserted', n, 'bars', len(st['bars']), 'markers', set(st['markers']))
"
```

If today’s EOD chunk returns `v2 payload is None`, pass a wrapper `daily_fn` that fetches through **yesterday** (last weekday) — do not hang a 1y job. Report the date range actually cached.

Regression: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest chart_app/tests tests/test_candlestick_chart.py -q`

- [ ] **Step 1–4: docs + smoke + pytest**

- [ ] **Step 5: Commit** `docs(chart-app): honest phase-1 semantics and 30d smoke`

---

## Self-review

- Spec coverage: standalone window, agent GET state, local cache, no per-bar PH, Direction price legs, classic pile, position gate, RH display-only, chat-gated orders via MCP — each has a task.
- Non-goals called out (replay, watchlist, dealer, dashboard merge).
- No TBD/placeholder steps. Types consistent (`build_state` → `/api/state`).
- Order fire is **not** in the server (matches “when you say so in chat” without giving the window a hidden hot path).
