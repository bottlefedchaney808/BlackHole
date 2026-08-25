# Design Spec: Options-Flow Overlay on the Native Chart

- **Date:** 2026-08-24
- **Status:** plan-only; NO production code changed
- **Author:** Hermes Agent
- **Branch:** `feature/native-chart-flow-overlay`

## Purpose

Add an **options-flow overlay** to the native chart app (`chart_app/`, served at
`127.0.0.1:8791`). The overlay renders three per-bar **line series** in a dedicated
bottom pane — **call premium**, **put premium**, and **net premium** (call − put) —
sourced from the PotatoHedge `flow.scanner_trades` tape. It reuses the same tape that
already drives the existing whale-flow dots, so both overlays fall out of one fetch.

This is the "is the whole options book pushing up or down, and how hard, this bar" view —
the direction + size of options flow — which the current whale dots (a ≥$25k print landed
here) do not show.

## 1. User Experience

- The chart's third pane (below the volume pane) shows three lines on their own y-axis:
  - **Call premium** (green) — total call premium traded in the bar.
  - **Put premium** (red) — total put premium traded in the bar.
  - **Net premium** (white/cyan) — call − put; above zero = net call buying, below = net put.
- Bars with no flow in the window render as **zero** (zero-fill, not a gap) — the lines are
  continuous across the visible range and net sits on the zero baseline where there is no flow.
- The overlay is **gated** like the existing whale/liquidity overlays: it renders only when
  the flow fetch succeeds; on failure the pane is empty and the chart still loads (safe
  fallback, never a broken path).
- No new invocation surface: the flow pane is part of the standard chart load, toggled by
  the same flow-enable flag the whale overlay uses.

## 2. Requirements (from the approved forks)

1. **Overlay shape:** continuous per-bar **line series** (not event dots) — call / put / net.
2. **Measure:** **premium** (money), not volume. Net = call − put.
3. **Placement:** dedicated **bottom flow pane** with its own y-axis (premium is money, not
   price, so it gets its own scale — the standard "flow tape" layout).
4. **Scope:** **full tape** (`min_premium=0`) — total premium per bar, honest. First load of
   a new session-date is slow; cached after.
5. **Cost posture:** **cache + time-filter + 5-day cap** — fetch only the hours the loaded
   bars span, cache to disk keyed (root, date), cap the fan-out so a 1y daily view does not
   page every day.

## 3. Data source (verified live, 2026-08-24)

| Source | Result | Verdict |
|---|---|---|
| `flow.timeseries` (`aggregate_by`, `interval`) | `PHPermissionError` — capability not on this account | **Unavailable.** The ideal interval-aggregated series is permission-gated here. |
| `flow.scanner_trades` @ `min_premium=$25k` | ~500 rows/day, one call | What the **existing whale overlay** already fetches. |
| `flow.scanner_trades` @ `min_premium=0` | **30,000+ rows/day**, paginated (`limit`≤10000, `offset`) | The full tape. `safe`/low-risk, time-filterable (`start_time`/`end_time`), the workhorse. |

`scanner_trades` row fields (confirmed): `date, datetime, ms_of_day, root, expiration,
strike_price, trade_right (C/P), size, price, premium, net_premium, bias_direction,
sentiment, trade_type, ...`. `premium` is the per-trade premium in dollars; `trade_right`
is the call/put split; `datetime`/`ms_of_day` give the bar-bin timestamp.

**Key fact:** the flow lines and the whale dots are the **same tape at two thresholds**.
Whale = the premium ≥ $25k slice; flow lines = the full aggregation. One source of truth,
one fetch.

## 4. Architecture

### 4.1 One fetch, one cache, two overlays

The flow path in `server.py` (`_production_flow_fn`) already calls
`flow.scanner_trades` **once per session-date** (capped at 5 days, never per-bar). It is
extended, not replaced:

- **`min_premium=0`** (full tape) instead of the whale threshold.
- **Pagination:** loop `offset` in 10000-row pages until a short/empty page, per session-date.
- **Time-filter:** pass `start_time`/`end_time` covering only the hours the loaded bars span
  for that date, so we don't page the full 24h tape for a 15-min chart of one session.
- **Disk cache** keyed `(root, session_date, start_time, end_time)`: a completed fetch is
  written to `artifacts/chart_app_flow_cache/`; a reload reads the cache and makes **zero**
  PH calls. Only genuinely new (root, date, window) tuples fetch.

The whale dots are derived from the **same rows** (the premium ≥ $25k slice) — no second
fetch, no behavior change to `flow_stamp.stamp_whale`.

### 4.2 New module: `chart_app/flow_pane.py`

Bins the tape onto closed bars and emits the three series.

- `bin_flow(records: list[CandleRecord], trades: list[dict]) -> dict[str, list[float]]`
  returns `{"call": [...], "put": [...], "net": [...]}` aligned 1:1 to `records`.
- Binning uses the **existing `(prev_ts, ts]` rule** from `flow_stamp.py` (first bar
  `[ts0, ts0]`): a trade lands in the bar whose interval contains its timestamp.
- Per bar: `call += premium` where `trade_right == "C"`, `put += premium` where
  `trade_right == "P"`, `net = call − put`.
- Rows with no timestamp are attributed to the first bar (same trust-the-window fallback
  `flow_stamp` uses).
- Pure function, no I/O — unit-testable against a fixed tape fixture.

### 4.3 Payload + render

- `snapshot.py` adds the three series to the chart payload (alongside the existing
  `whale` flags), only when the flow fetch succeeded.
- `static/index.html` adds a **third pane** (own y-axis, like the volume pane) with three
  `line()` series: call (green), put (red), net (white). Legend entries added.

### 4.4 Files touched

| File | Change |
|---|---|
| `chart_app/server.py` | `_production_flow_fn`: `min_premium=0`, paginate, time-filter, disk cache. |
| `chart_app/flow_pane.py` | **New** — `bin_flow` (tape → per-bar call/put/net). |
| `chart_app/snapshot.py` | Emit the three flow series when flow is present. |
| `chart_app/static/index.html` | Third pane + three lines + legend. |
| `chart_app/flow_stamp.py` | **Unchanged** — whale reuses the same rows. |

## 5. Error handling

- A page that 400s/times out degrades to the rows already fetched (partial tape; lines still
  render), matching the existing per-day `except: continue`.
- Any flow-call permission/transport failure → overlay off, pane empty, chart still loads.
  Never a broken path (safe fallback over broken path).
- Cache write failure is non-fatal: the in-memory rows still render; next load re-fetches.

## 6. Testing

- **Unit — `flow_pane.bin_flow`:** fixed tape fixture with known C/P premiums and timestamps
  spanning several bars; assert per-bar call/put/net and the `(prev_ts, ts]` boundary
  (a trade exactly at a bar's `ts` lands in that bar, not the next).
- **Overlay agreement:** on the same rows, the premium ≥ $25k slice aggregated equals the
  existing `flow_stamp.stamp_whale` flags — the two overlays agree on the shared tape.
- **Cache-hit path:** a second call with the same (root, date, window) makes **zero** PH
  calls (mock the client; assert not called) and returns identical rows.
- **Degradation:** a mid-pagination failure returns the partial rows and does not raise.

## 7. Non-goals (YAGNI)

- No volume (contracts) lines — premium only (approved fork).
- No per-bar live PH on long windows — the 5-day fan-out cap and time-filter are the guard.
- No new invocation surface or order route — the pane rides the standard chart load.
- No change to the whale overlay's threshold, color, or stamping logic.

## 8. Open items (resolved by live probe, recorded for the plan)

- `flow.timeseries` is permission-gated on this account → the design does **not** depend on
  it. If the capability is later granted, `flow_pane` could switch to the pre-aggregated
  series with no UI change (the binning becomes a passthrough).
- Full-tape row count is unbounded (30k+ and growing); the cache + time-filter + cap are the
  cost controls, not a hard row limit.
