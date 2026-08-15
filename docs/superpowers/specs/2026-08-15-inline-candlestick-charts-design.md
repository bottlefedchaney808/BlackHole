# Inline Candlestick Charts Design

**Status:** Approved by Jason on 2026-08-15

## Goal

Allow a natural-language chart request in chat, such as `chart SPY candles 6m`, to produce a reliable inline candlestick chart without requiring a dashboard page, Tools-module integration, or a separate browser surface.

## Scope

### In scope

- Inline candlestick chart rendering for a requested ticker.
- Daily OHLC candles as the first supported series.
- Optional lookback parsing with a safe default.
- Volume bars when volume is available.
- Human-readable metadata: ticker, interval, lookback, source, observation range, and data timestamp.
- Explicit empty/error states when data cannot be fetched or validated.
- Deterministic chart-ready data and rendering tests.
- A chart artifact format that can later support interactive or dashboard delivery without requiring that work now.

### Out of scope

- Tools-module routes or plugin registration.
- A new dashboard page or frontend application.
- CDN dependencies or a browser-based interactive chart runtime.
- Dealer-positioning calculations in the chart path.
- Dealer heatmap generation from raw market data.
- Changes to Vol Suite sign conventions, Greek calculations, accumulation, scaling, or output schemas.
- Intraday/live streaming candles in the first slice.

## Design

### Chat-first delivery

The chart request is handled as an inline assistant response. The implementation should produce a native image artifact suitable for the chat client's inline media rendering, with a concise caption containing the chart metadata. If the platform's file-delivery convention requires an absolute path, the final response uses that path rather than linking to a dashboard route.

The first rendering target is a static, high-quality candlestick image. Interactivity, embedded HTML, and a browser-side renderer are intentionally deferred until the inline path is proven reliable.

### Data path

The chart layer must use an existing repository market-data client or a clearly bounded adapter rather than inventing a parallel HTTP client. Python remains responsible for fetching, normalizing, validating, and formatting the OHLC records. The renderer receives a typed chart payload and does not perform financial calculations.

The normalized payload must contain:

- `ticker`
- `interval` (`1d` initially)
- `lookback`
- `source`
- `observations`: ordered records with `timestamp`, `open`, `high`, `low`, `close`, and optional `volume`
- `as_of` or source timestamp when available
- `quality`: row count, missing-field status, and any non-fatal warnings

Records must be chronologically ordered, numeric OHLC values must be finite, and each candle must satisfy `low <= min(open, close) <= max(open, close) <= high`. Invalid rows must not be silently plotted; the request should return an explicit error or a warning with the invalid-row count according to the project’s existing error conventions.

### Rendering

Use the project’s existing Python visualization stack for the first implementation, preferring a focused renderer that can produce a self-contained PNG. The visual style should fit the existing dark dashboard language without requiring dashboard template changes:

- dark background and high-contrast text
- green up candles and red/down candles
- subdued grid and axes
- readable date labels that adapt to the observation count
- volume subplot when volume is present
- title containing ticker and interval/lookback
- no misleading technical indicators unless explicitly requested

The renderer must close figures/resources after writing the artifact and use a deterministic output configuration suitable for tests.

### Error and provenance behavior

No data, unavailable credentials, malformed provider responses, unsupported interval, or invalid ticker must result in a truthful user-facing failure state. The implementation must not fabricate candles, substitute an unreported ticker, or imply that a stale cache is current. When cached data is used, the caption must identify it as cached and retain its source timestamp.

### Dealer heatmap boundary

Dealer gamma/vanna/charm heatmaps belong only to validated Vol Suite output artifacts. A later suite-output renderer may visualize those artifacts, but this design does not add that renderer and does not recompute dealer values. The heatmap path must preserve the suite’s existing sign model, accumulation window, scaling, provenance, and data-quality fields verbatim.

## Testing strategy

- Unit-test request parsing for ticker, candle mode, lookback, and default interval.
- Unit-test normalization, chronological sorting, numeric validation, OHLC invariants, and optional volume handling.
- Unit-test empty, malformed, and provider-error responses.
- Unit-test deterministic renderer output creation and cleanup using a small fixture payload; do not require live market data.
- Verify the produced image is a readable non-empty PNG and that the renderer does not mutate the input payload.
- Run the narrow chart tests first, then the relevant existing test suite and lint checks.

## Deferred evolution

The chart-ready payload should be kept independent from the PNG renderer so a future implementation can add local ECharts/D3 assets, interactive HTML, dashboard embedding, or chat-native richer media without changing the market-data contract. Those additions require a separate design and approval.

## Research basis

The dependency research compared D3.js, Observable Plot, Apache ECharts, and uPlot. ECharts is the strongest future dashboard renderer; D3 is reserved for genuinely bespoke quantitative overlays; uPlot is a performance option for dense time series. None is required for this approved inline-first slice. Relevant sources include:

- D3: https://d3js.org/d3-scale and https://d3js.org/d3-shape
- Apache ECharts: https://echarts.apache.org/handbook/en/basics/import/
- uPlot: https://github.com/leeoniya/uPlot
- FastAPI static assets, for future dashboard work: https://fastapi.tiangolo.com/advanced/templates

## Acceptance criteria

1. A supported request for a ticker and daily lookback produces an inline-deliverable candlestick PNG.
2. The chart contains OHLC candles, and volume when supplied by the source.
3. Metadata identifies ticker, interval, lookback, source, and data range.
4. Invalid or unavailable data produces an explicit truthful error state.
5. No Tools-module, dashboard-route, or dealer-model changes are required for the first slice.
6. Automated tests cover parsing, normalization, validation, rendering, and error paths.
7. The implementation leaves a clean seam for future interactive chart delivery.

## Self-review

- No unresolved placeholders remain.
- Scope is limited to one independently testable feature: inline daily candlestick rendering.
- Dealer heatmaps are explicitly separated from callable spot charts and remain suite-output-only.
- The data contract and rendering contract use consistent field names and responsibilities.
- Acceptance criteria map directly to the scope, data path, error behavior, and tests above.

## Approval

Approved in chat by Jason on 2026-08-15. Implementation must wait for the follow-up implementation plan and its execution-choice gate.
\n