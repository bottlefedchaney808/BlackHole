# Inline Candlestick Charts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tested, inline-deliverable daily candlestick chart capability without changing the dashboard, Tools registry, or dealer-positioning model.

**Architecture:** Add a focused chart data/validation/rendering module that accepts normalized OHLC records and writes a deterministic PNG. Keep market-data acquisition behind an existing shared client adapter or a small injected provider interface. The first slice is intentionally callable from the assistant-side media path, not from the dashboard’s Tools module or a new HTTP route.

**Tech Stack:** Python 3.11+, existing shared ThetaData/httpx market-data path where suitable, existing matplotlib/Pillow stack, pytest, existing repository path conventions.

## Global Constraints

- Inline-first: produce a native PNG artifact; do not add dashboard routes, Tools registry entries, or browser JavaScript.
- Daily candles only in the first slice (`interval="1d"`).
- Dealer heatmaps remain exclusively downstream of validated Vol Suite output artifacts.
- Do not alter dealer sign conventions, Greek calculations, accumulation, scaling, or suite schemas.
- Do not read or print `.env` or credential values.
- Chart modules must be testable without network access or live credentials.
- Use the repository’s existing market-data client path; do not revive yfinance as an unreviewed fallback.

---

### Task 1: Define and validate the normalized candle contract

**Files:**
- Create: `shared/chart_data.py`
- Create: `tests/test_chart_data.py`

**Interfaces:**
- Produces `CandleRecord` with `timestamp`, `open`, `high`, `low`, `close`, optional `volume`.
- Produces `CandlePayload` with `ticker`, `interval`, `lookback`, `source`, ordered `observations`, `as_of`, and `quality`.
- Produces `normalize_candles(rows, *, ticker, interval="1d", lookback=None, source="unknown", as_of=None) -> CandlePayload`.
- Raises a documented `ChartDataError` for missing required fields, non-finite numbers, invalid timestamps, or OHLC invariant violations.

- [ ] **Step 1: Write failing tests for valid normalization and ordering.**

```python
def test_normalize_candles_sorts_and_preserves_optional_volume():
    payload = normalize_candles([
        {"timestamp": "2026-08-02", "open": 101, "high": 105, "low": 99, "close": 104, "volume": 20},
        {"timestamp": "2026-08-01", "open": 98, "high": 102, "low": 97, "close": 101},
    ], ticker="SPY", lookback="1m", source="fixture")
    assert [r.timestamp.isoformat() for r in payload.observations] == ["2026-08-01T00:00:00", "2026-08-02T00:00:00"]
    assert payload.observations[0].volume is None
    assert payload.quality.row_count == 2
```

- [ ] **Step 2: Add failing tests for invalid numeric values, missing fields, and `low <= OHLC <= high`; run `pytest tests/test_chart_data.py -q` and confirm failure because the module does not exist.**
- [ ] **Step 3: Implement the dataclasses, coercion, chronological sort, finite-number checks, and explicit `ChartDataError`.**
- [ ] **Step 4: Run `pytest tests/test_chart_data.py -q`; expect all contract tests to pass.**
- [ ] **Step 5: Commit `feat: add normalized candle chart contract`.**

### Task 2: Add the market-data adapter with dependency injection

**Files:**
- Create: `shared/spot_history.py`
- Create: `tests/test_spot_history.py`
- Inspect/modify only if required by the existing public method: `shared/thetadata.py`

**Interfaces:**
- Produces `fetch_daily_candles(ticker, *, lookback="6m", provider=None) -> CandlePayload`.
- `provider` is an injectable callable used by tests and may return raw rows accepted by `normalize_candles`.
- The default provider uses the existing shared market-data client method discovered during implementation; if that client cannot provide daily OHLC through a stable public method, the task must stop and document the missing endpoint rather than inventing one.
- Errors are wrapped as `ChartDataError` without exposing credentials.

- [ ] **Step 1: Write tests using a fake provider for successful fetch, ticker normalization, lookback forwarding, and provider failure.**
- [ ] **Step 2: Run `pytest tests/test_spot_history.py -q` and confirm the expected failures.**
- [ ] **Step 3: Implement the adapter and provider injection; do not add yfinance.**
- [ ] **Step 4: Run `pytest tests/test_spot_history.py -q`; expect all tests to pass.**
- [ ] **Step 5: Commit `feat: add injectable daily spot-history adapter`.**

### Task 3: Implement deterministic dark candlestick rendering

**Files:**
- Create: `shared/candlestick_chart.py`
- Create: `tests/test_candlestick_chart.py`

**Interfaces:**
- Produces `render_candlestick(payload: CandlePayload, output_path: str | Path) -> Path`.
- Writes a PNG to the requested path, creates parent directories, and never mutates the payload.
- Includes OHLC candles, readable date axis, title metadata, and a volume subplot when at least one observation has volume.
- Closes matplotlib figures/resources after writing.

- [ ] **Step 1: Write a fixture payload and failing tests for PNG creation, non-empty bytes, parent-directory creation, and payload immutability.**
- [ ] **Step 2: Run `pytest tests/test_candlestick_chart.py -q` and confirm failure.**
- [ ] **Step 3: Implement the renderer using existing `matplotlib`, with explicit `Agg` behavior for headless execution, dark colors, green up candles, red down candles, and volume bars.**
- [ ] **Step 4: Add a test that invalid/empty payloads fail explicitly rather than producing a blank chart.**
- [ ] **Step 5: Run `pytest tests/test_candlestick_chart.py -q`; expect all tests to pass.**
- [ ] **Step 6: Commit `feat: render inline dark candlestick charts`.**

### Task 4: Add the assistant-facing chart-call orchestration seam

**Files:**
- Create: `shared/chart_request.py`
- Create: `tests/test_chart_request.py`

**Interfaces:**
- Produces `build_spot_chart_request(ticker: str, *, lookback="6m", interval="1d") -> ChartRequest`.
- Produces `render_spot_chart(ticker: str, *, lookback="6m", interval="1d", output_path, provider=None) -> ChartArtifact`.
- `ChartArtifact` contains absolute `path`, ticker, interval, lookback, source, observation range, row count, and warnings.
- Unsupported intervals fail before network/provider invocation.

- [ ] **Step 1: Write failing tests for request parsing/defaults, ticker validation, unsupported interval rejection, and end-to-end fake-provider artifact creation.**
- [ ] **Step 2: Run `pytest tests/test_chart_request.py -q` and confirm failure.**
- [ ] **Step 3: Implement orchestration by composing `fetch_daily_candles` and `render_candlestick`; preserve explicit metadata for inline caption generation.**
- [ ] **Step 4: Run `pytest tests/test_chart_request.py -q`; expect all tests to pass.**
- [ ] **Step 5: Commit `feat: add inline spot-chart request seam`.**

### Task 5: Verify the complete feature and document invocation

**Files:**
- Modify: `dashboard/README.md` only if the implementation exposes an existing invocation surface that belongs in dashboard documentation; otherwise do not modify it.
- Create: `docs/superpowers/reports/2026-08-15-inline-candlestick-verification.md` only if the project convention requires a verification report.
- Test: `tests/test_chart_data.py`, `tests/test_spot_history.py`, `tests/test_candlestick_chart.py`, `tests/test_chart_request.py`

**Interfaces:**
- The final verification must exercise the actual chart orchestration with a deterministic fake provider and inspect the produced PNG.

- [ ] **Step 1: Run the narrow suite with environment isolation:** `env -u PYTHONPATH -u PYTHONHOME pytest tests/test_chart_data.py tests/test_spot_history.py tests/test_candlestick_chart.py tests/test_chart_request.py -q`.
- [ ] **Step 2: Run lint on changed Python files: `ruff check shared/chart_data.py shared/spot_history.py shared/candlestick_chart.py shared/chart_request.py tests/test_chart_data.py tests/test_spot_history.py tests/test_candlestick_chart.py tests/test_chart_request.py`.**
- [ ] **Step 3: Run the relevant existing regression tests for shared/dashboard imports: `pytest tests/test_quant_bridge.py dashboard/tests/test_quant_modules.py -q` if those paths exist and collection is clean.**
- [ ] **Step 4: Run `git diff --check` and inspect `git status --short`; confirm no `.env`, credential, or unrelated files changed.**
- [ ] **Step 5: Commit `test: verify inline candlestick chart path`.**

## Self-review

- Every acceptance criterion in the design maps to Tasks 1–5.
- No task adds Tools-module integration, a dashboard route, JavaScript, or dealer-model logic.
- Provider endpoint uncertainty is explicit in Task 2: inspect the existing client and stop rather than inventing an API.
- Types are consistent: Task 1 owns `CandlePayload`; Tasks 2–4 consume it; Task 4 owns `ChartArtifact`.
- Error handling is concrete: validation errors, provider errors, unsupported interval, and empty payloads have explicit tests.
- No unresolved `TBD` or `TODO` placeholders are present.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-15-inline-candlestick-charts.md`. Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh worker per task, review between tasks, and keep implementation isolated.

**2. Inline Execution** — execute the tasks in this session with the executing-plans workflow and checkpoints.

Which approach should be used?

\n