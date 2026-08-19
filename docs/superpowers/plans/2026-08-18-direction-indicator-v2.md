# Direction Chart Overlay v2 — Per-Bar Indicator Plan

**Status:** DRAFT v2 (2026-08-18) — redrafted after PH v2 SDK education; supersedes v1's
"daily wall" assumptions.
**Branch:** `fix/adversarial-audit-20260817` (merged to master `aad047d` + marker fixes
`a643979`, `fedd92f`).
**Scope boundary (Jason, verbatim):** "make sure that these changes are for the indicator
only not for the direction module tool" — **zero changes** to `Direction/*` module entry
points (`analyze`, `scan`, `get_liquidity`, `generate`), `shared/thetadata.py` facade, or
any tool/dashboard consumer. All new behavior lives in a new indicator layer.

---

## 1. Why v2 exists

v1 replay (`Direction/replay.py` + `scripts/render_direction_chart.py`) evaluates the
suite **once per unique calendar date** and stamps every intraday bar of that day with the
same verdict — Jason: "you need to run the direction tool on each bar like a real
indicator, not once at the start of the day and carry it."

v2 evaluates **every bar** against data available as of that bar's timestamp.

## 2. What the SDK education changed

Prior assumption: whale-flow and liquidity are daily-only ("the daily wall"). The PH v2
catalog (wiki: `docs/phclient_v2/WIKI.md`, "Intraday-capability inventory" + "Complete
endpoint catalog", commit `6d23a5f`) shows intraday resolution EXISTS:

| Signal | v1 data (daily) | v2 intraday source | Endpoint |
|---|---|---|---|
| Whale flow | `option_bulk_hist_eod` volume | per-bar flow/trades | `flow.scanner_trades_in_time_range(root, start_datetime, end_datetime)`, `flow.recent(window=5/15/30/60)` |
| Liquidity/OI | `option_bulk_hist_oi` (EOD OI) | OI stays EOD (OI settles daily); dealer gamma intraday | `dealer.weighted_greeks(root, interval=, ms_of_day=)` |
| Dealer gamma | `get_dealer_gamma` (daily) | intraday weighted greeks | `dealer.weighted_greeks` / `weighted_greeks_summary` (interval+ms_of_day) |
| Price series (wave3/squeeze/trend) | daily OHLCV | intraday OHLCV up to bar | `market.stock_ohlc(root, ivl=, rth=, start_time=, end_time=)` (already used by chart stack) |
| S/R / gamma walls | none | intraday level history | `support_resistance.history_intraday(ticker, date, interval=)` |

**Constraint:** `dealer.weighted_greeks` is `retry_policy=unsafe` + risk MEDIUM —
per-bar fan-out over a full chart will 502-storm the shared proxy. Design must stage
concurrency (`THETADATA_HIST_CONCURRENCY`-style) and/or compute intraday dealer gamma on a
**coarse bar grid** (e.g. every N bars / hourly) with neutral interpolation between — never
fabricated, explicitly "sampled" per the honest-data rule.

## 3. Architecture (indicator-only)

```
scripts/render_direction_chart.py  (v2)
  └─ Direction/indicator.py  [NEW — the only new Direction module]
       ├─ bar_eval(ticker, bars, generate_fn=None) -> list[dict]  per-bar verdicts
       ├─ _price_signals(ticker, bar_ts)  -> calls PURE fns: count_waves, get_bands/
       │                                    detect_squeeze/regime, adx/ma_alignment
       │                                    on intraday OHLCV up to bar_ts
       ├─ _flow_signal(ticker, bar_ts)    -> flow.scanner_trades_in_time_range / flow.recent
       ├─ _liquidity_signal(ticker, bar_ts)-> dealer.weighted_greeks (coarse grid) + EOD OI
       └─ _score(conviction_like) -> 0-5 score, same composition rule as signal_generator
  └─ shared/chart_request.render_spot_chart(direction_overlay=, live_note=)  (already supports
       per-entry score/conviction; marker matching v2 keyed by bar timestamp)
```

**Pure-function reuse (NO module entry-point changes):**
- `Direction/elliott_wave.count_waves(prices)` — feed intraday closes up to bar_ts.
- `Direction/bollinger_analyzer.get_bands/detect_squeeze/regime` — intraday closes.
- `Direction/trend_engine.adx/ma_alignment` — intraday high/low/close.
- `Direction/liquidity_map._max_pain/_sum_oi` — daily OI chain (OI is EOD by nature).
- `Direction/whale_scanner` — v2 swaps to intraday flow source (indicator-local logic, not
  the module's `scan()`).

**Score/conviction composition** — replicate `signal_generator.generate`'s rule verbatim in
`indicator.py` (documented; this is the one place that mirrors the tool, by explicit
design — the tool itself is untouched): whale && wave3 && (squeeze || trend) && score>=3 →
HIGH; whale && score>=3 → MEDIUM; else NONE.

**Marker semantics (unchanged from `fedd92f`):** 0/5 ▼ sell, 3/5 ● hold, 4/5 ▲ buy,
5/5 ◆ add, 1-2/5 none. Overlay entries keyed by bar timestamp (v2) instead of date (v1).

## 4. Tasks (TDD, subagent-driven)

### Task 1

- **Task 1 — Data: intraday OHLCV-as-of.** `shared/spot_history` (or a small helper in
  `indicator.py`) gains `intraday_bars_as_of(ticker, interval, ts)` → bars with
  `timestamp <= ts`, RTH. Pure addition, no existing signature changed.
### Task 2

- **Task 2 — Indicator: price signals per bar.** `indicator._price_signals(ticker, bar_ts)`
  using pure fns on intraday series up to bar_ts. Unit tests: series growing bar-to-bar
  produces different wave3/squeeze/trend verdicts on a crafted fixture.
### Task 3

- **Task 3 — Indicator: flow signal per bar (intraday whale).** `_flow_signal` via
  `flow.scanner_trades_in_time_range` for the bar window; degrades neutral on timeout.
  Injectable fetcher for tests (no live network in unit tests).
### Task 4

- **Task 4 — Indicator: liquidity per bar (intraday dealer gamma, coarse grid).**
  `_liquidity_signal`: `dealer.weighted_greeks` on a coarse grid (configurable
  `gamma_every_n_bars`, default e.g. 4) + EOD OI max-pain via `liquidity_map._max_pain`.
  Neutral between grid points, never interpolated-as-fact.
### Task 5

- **Task 5 — Indicator: score composition + `bar_eval`.** `bar_eval(ticker, bars)` returns
  per-bar `{ts, score, conviction, signals{}}`; degrade-on-failure neutral; same
  composition rule as generate. Injectable `generate_fn` retained for tests.
### Task 6

- **Task 6 — Replay v2: `replay.py` per-timestamp.** `replay_direction(ticker,
  bar_timestamps, eval_fn=None)` — one eval per bar timestamp (not per date); v1's
  date-keyed signature deprecated but kept for back-compat (or replaced — decide at T6,
  zero external callers beyond the CLI).
### Task 7

- **Task 7 — Chart: per-timestamp marker matching.** `render_candlestick` overlay matches
  `obs.timestamp` → entry `ts` (exact bar match, not date). `render_spot_chart` passthrough
  unchanged (already forwards direction_overlay). Marker styles/glyphs unchanged.
### Task 8

- **Task 8 — CLI v2 + live verification + skill update.**
  `render_direction_chart.py` passes every bar's timestamp; live SPY 15m run; verify
  markers now VARY bar-to-bar (the whole point); update `run-direction-chart` +
  `render-quant-chart` skills (per-bar semantics, coarse-grid caveat, intraday endpoints).
### Task 9

- **Task 9 — Full regression + honest-docs.** Direction suite + chart tests + full suite;
  document the coarse-grid gamma sampling and any unfetchable-per-bar degradation in
  `indicator.py` docstring + wiki.

## 5. Constraints & guardrails (carried from v1)

- **Indicator-only.** No `Direction/*` entry-point, facade, or tool changes. The one mirror
  (score composition) is explicitly documented and tested to match `generate` byte-for-byte
  on the same inputs.
- Clean launcher: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe`.
- ThetaData/PH v2 only; never yfinance.
- Never fabricate history: unfetchable → neutral (NONE/0), coarse-grid gamma is *sampled*,
  documented, never presented as continuous.
- `dealer.weighted_greeks` cost: staged concurrency, coarse grid; live run must not 502-storm.
- Public as_of accepts `YYYY-MM-DD`/`YYYYMMDD`; bar timestamps canonical ISO.

## 6. Definition of done

- `indicator.bar_eval` produces per-bar verdicts that **vary intraday** on live data.
- Chart shows per-bar markers (▲/●/◆/▼/none by score), not per-day repeats.
- Direction module tool output unchanged (regression: `generate(ticker)` on live = same as
  pre-v2 for the same inputs).
- All tests green (Direction + chart + indicator), full suite regression reported honestly.
- Skills + wiki updated with v2 semantics and the coarse-grid caveat.
