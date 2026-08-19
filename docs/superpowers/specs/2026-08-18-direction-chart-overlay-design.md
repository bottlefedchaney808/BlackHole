# Design Spec: Direction-Suite Buy/Sell Chart Overlay

- **Date:** 2026-08-18
- **Status:** plan-only; NO production code changed
- **Author:** Hermes Agent

## Purpose

Let users request a candlestick chart with buy/sell markers driven by the results of the
Direction signal suite. The overlay shows a **per-bar historical replay** of the 5-signal
conviction across the chart's visible range, plus a **live conviction stamp** on the last
bar. Initial delivery is an inline PNG via the existing chart stack; the web dashboard is a
later phase.

## 1. User Experience

- **Invocation:** request a chart with a direction overlay, e.g. "SPY 15min with direction".
  Extends the `render-quant-chart` skill's `render_spot_chart` entry point with an opt-in
  `direction_overlay` argument.
- **Output:** themed candlestick PNG (existing blue/purple dark theme) with:
  - Per-bar markers: **▲ BUY** (HIGH conviction), **△ weak buy** (MEDIUM), **▼ SELL** (NONE).
  - Live stamp on the last bar: current `generate(ticker)` conviction + score.
- **Scope note:** inline PNG first. Dashboard integration is a separate later phase, not in
  this spec.

## 2. Requirements (from the approved forks)

1. **Blend mode:** per-bar replay of the 5 sub-signals across the visible chart, PLUS a live
   conviction stamp on the last bar.
2. **Replay all 5:** every signal (including whale/flow and liquidity/OI) is evaluated per
   historical bar by re-fetching that day's data — not limited to price-series signals.
3. **Symmetric conviction → marker:**
   - `HIGH` → BUY (▲)
   - `MEDIUM` → weak buy (△)
   - `NONE` → SELL (▼)
   Mapping the existing 3-way conviction as-is; no new bearish-signal logic.

## 3. Architecture

### 3.1 `as_of`-aware Direction modules (refactor, backward-compatible)

Add an optional `as_of: str | None = None` parameter to:

| Module | Entry point |
|---|---|
| `Direction/whale_scanner.py` | `scan(ticker, ...)` |
| `Direction/liquidity_map.py` | `get_liquidity(ticker)` |
| `Direction/elliott_wave.py` | `analyze(ticker)` |
| `Direction/bollinger_analyzer.py` | `analyze(ticker)` |
| `Direction/trend_engine.py` | `analyze_trend(ticker)` |
| `Direction/signal_generator.py` | `generate(ticker, as_of=None)` |

**Behavior:**
- `as_of` set → module fetches that day's flow/OI/OHLCV instead of `date.today()`.
- `as_of=None` → exact current behavior (backward compatible; existing CLI, orchestrator,
  desk-note, and Direction tests unaffected).
- `whale_scanner` + `liquidity_map` are the two modules that bake in `date.today()`; these are
  the substantive refactor.
- `Direction/data.py` gains as-of fetch helpers for flow / OI / chain / OHLCV.

### 3.2 `Direction/replay.py` (new)

- Input: `ticker`, list of bar dates.
- For each date: `generate(ticker, as_of=date)` → append `{date, conviction, score, signals}`.
- Return the series. Fakes injectable for tests (no network).

### 3.3 Overlay renderer (extend chart stack)

- `shared/chart_request.render_spot_chart(..., direction_overlay=[...])` accepts the replay
  series; `shared/candlestick_chart.render_candlestick` draws the markers.
- Per-bar: ▲/△/▼ at the corresponding bar, using existing theme colors.
- Live stamp: current conviction + score as a text annotation on the last bar.
- Empty/no overlay → unchanged chart (no behavior change for existing calls).

## 4. Data Flow

1. User requests a chart with direction overlay.
2. `render_spot_chart` fetches OHLCV bars (existing path).
3. For each bar date, `replay.generate(ticker, as_of=bar_date)` runs the 5-module suite.
4. Replay series is merged onto bars; live `generate(ticker)` stamps the last bar.
5. `render_candlestick` draws candles + markers + stamp → PNG.

## 5. Known Limits & Honest Caveats

- **Replay cost:** each bar triggers the 5-module suite (price series can be cached once;
  flow/OI refetched per bar). A 15m/5d chart ≈ ~20 bars ≈ ~20 flow/OI fetch passes.
  Throttled + cached; documented, not hidden.
- **Whale/liquidity granularity:** those modules are daily-granularity (flow/OI/EOD). On an
  intraday chart they evaluate **daily-as-of** and the same verdict repeats across that day's
  bars. Flagged on the artifact, not fabricated as per-min signals.
- **`NONE` = SELL (▼)** is the absence-of-bullish-conviction mapped to a sell marker per the
  agreed convention — a directional read, NOT a backtested short signal.
- **No fabricated history:** each historical marker is a real `generate(as_of=date)` result
  from re-fetched data; anything unfetchable for a bar is omitted, not invented.

## 6. Testing

- **Per-module `as_of`:** fake data returns that day's rows; `as_of=None` matches current.
- **`replay.generate`:** golden tests (known dates → known conviction).
- **Overlay renderer:** markers at correct bars, live stamp present, empty overlay →
  unchanged chart.
- **Regression:** existing Direction tests (no `as_of`) still pass; existing chart tests pass.

## 7. Files

**Modify:**
- `Direction/whale_scanner.py`
- `Direction/liquidity_map.py`
- `Direction/elliott_wave.py`
- `Direction/bollinger_analyzer.py`
- `Direction/trend_engine.py`
- `Direction/signal_generator.py`
- `Direction/data.py`
- `shared/chart_request.py`
- `shared/candlestick_chart.py`

**Create:**
- `Direction/replay.py`
- tests for replay + overlay + `as_of`

## 8. Out of Scope (later phase)

- Web dashboard integration of the overlay.
- Any genuinely new bearish/short signal logic.
