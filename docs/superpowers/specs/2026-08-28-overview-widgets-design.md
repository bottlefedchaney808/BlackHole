# Overview Widgets (Phase 1) — Design

Date: 2026-08-28
Status: Approved (Jason, in-session brainstorm)

## Purpose

The Overview page (`dashboard/app.py::home()`, `dashboard/templates/index.html`) was just
decluttered — the swap card moved to `/tools` and the run-trigger form was removed in favor
of Quant Console, leaving only a run-history table and suite links. Jason wants it to become
an actual "widgets home": read-only status cards showing what's live right now, with room to
grow (and the same widget pattern reusable on Chart's empty side space later — out of scope
here, but the design should not foreclose it).

This spec covers four widgets:

1. **Positions** — live Robinhood positions, both accounts (A & B).
2. **Vol/quant signal state** — dealer positioning / IV rank / screener hits for a small
   watchlist (SPX, NDAQ to start).
3. **Per-position analysis** — options pricing stats, hedge advice, and VaR sim output for
   whatever's actually held (driven by widget 1's position list).
4. **Surface Explorer showcase** — IV, Vanna, and Charm surfaces on SPX, re-themed to match
   the dashboard's dark palette instead of matplotlib's default light theme.

## Current state (verified)

- `dashboard/app.py` is a single FastAPI app on `:8787`. It already has a proven pattern for
  "backend computes on a timer, page polls a JSON endpoint" — see `chart_app/server.py`'s
  `_refresh_loop` (background `asyncio` task registered via `@app.on_event("startup")`,
  writing into `chart_app/bar_cache.py`'s SQLite `BarCache`, read by `GET /api/state`).
  This spec reuses that exact shape for the dashboard process instead of chart_app's.
- **Robinhood has no Python client in this repo** — only `mcp__robinhood__*` MCP tools,
  reachable exclusively from a Claude Code agent session (confirmed: no `robin_stocks` or
  similar import anywhere under `dashboard/`, `chart_app/`, or `requirements.txt`). The only
  existing "push live external data into a backend" precedent is `chart_app/server.py`'s
  `POST /api/rh` (`_RhBody`), which the agent calls after fetching Robinhood positions itself
  — single position/fills only, no accounts split. Widget 1 needs the same push shape,
  generalized to a full position list across two accounts.
- **`Tools/registry.py`** (`Tools.registry.TOOLS`) is the existing plugin surface: each tool
  is a module with a top-level `run(context: dict) -> dict`, registered via a frozen
  `ToolSpec(name, slug, description, run)`. Confirmed callable directly, in-process, with a
  loosely-shaped dict (not necessarily the full schema-validated `suite_context.json`) —
  `Tools/tools/hedge_optimizer_tool.py::run_options_hedge` reads `context['ticker']` or
  `context['focus']['ticker']` directly, no `context_loader` file round-trip required.
- **VaR sims available via `Tools/`, verified by slug:** `Tools/tools/price_dist_tool.py`
  registers under slug `"simulations"` and dispatches on `context["mode"]` — but only
  `price_dist` (default), `mc_sim`, and `corr_sim` are wired (`_MODES` dict, `price_dist_tool.py:28`).
  **`hist_sim` is NOT currently exposed through `Tools/`** even though `var_engine/hist_sim.py`
  exists — it's only reachable via VaR_Tools_Simulations' own `--module`/interactive CLI.
  Widget 3 therefore ships with `price_dist` + `mc_sim` + `corr_sim` (the three modes actually
  pluggable today); wiring `hist_sim` into `price_dist_tool.py` as a fourth mode is a
  reasonable fast-follow but is out of scope for this spec.
- **Surface Explorer** (`Tools/tools/surface_explorer_tool.py`) renders matplotlib PNGs via
  `_plot_surface_3d` and `_plot_heatmap` (both `matplotlib.use("Agg")`, `fig.savefig(...,
  dpi=150)`) — confirmed no theming applied anywhere in that file or `Vol_Suite/surface_grids.py`
  today; every chart uses matplotlib's default light background/colors. `base.html`'s CSS
  custom properties (`--bg: #070a14`, `--panel: #0c1122`, `--text: #f8fafc`,
  `--muted: #a389ad`, `--accent: #a855f7`, `--accent-2: #3b82f6`, `--ok/--warn/--err`) are the
  palette to match.
- Vol_Suite's dealer-positioning / IV-rank / screener logic (`volatility_suite.py`) is
  currently invoked either interactively or via `orchestrator.py`'s subprocess-per-suite path
  — there is no existing in-process "just get me the signal numbers for one ticker" function
  used by `Tools/`. Widget 2's background job will need a thin new wrapper calling into the
  same underlying Vol_Suite functions `_run_production_dealer_positioning` /
  `expiry_book_production.fetch_production_result` already use for the live dealer model (per
  `CLAUDE.md`'s "Live dealer model" note) — exact call shape is an implementation-plan detail,
  not fixed here.
- `dashboard/templates/base.html` already has `.widget-grid`/`.widget` CSS (added in the
  Overview-cleanup pass immediately preceding this spec) and `.panel`/`.cards`/`.pill` for
  general widget-card styling.

## Scope

- New SQLite cache: `artifacts/widget_cache.db`, one table `widget_cache(widget_id TEXT
  PRIMARY KEY, payload TEXT, status TEXT, computed_at TEXT)`.
- One generic read endpoint: `GET /api/widgets/{id}` → `{payload: <json>, status, computed_at}`
  (404 if `id` was never written — e.g. before the first background tick completes).
- One write endpoint for the agent-push path: `POST /api/widgets/positions` (body: full
  position list for accounts A & B) — writes straight into the cache under `id="positions"`,
  no compute.
- Three background `asyncio` tasks registered at dashboard startup (same
  `@app.on_event("startup")` pattern as `chart_app`), one each for widgets 2/3/4, each on its
  own cadence, each writing its widget's cache row.
- `dashboard/templates/index.html` becomes a `.widget-grid` of (at minimum) these 4 cards,
  plus the existing run-history/suite-links panels already there.
- A new scheduled Claude Code cron agent (via the `schedule` skill) that calls Robinhood MCP
  tools every 15–30 min and `POST`s to `/api/widgets/positions`.
- Out of scope: drag/rearrange or add/remove widgets, any widget beyond these 4, Chart-tab
  side-panel reuse (the `.widget-grid`/`/api/widgets/{id}` pattern is deliberately generic
  enough that reuse there later is additive, not a redesign), wiring `hist_sim` into
  `price_dist_tool.py`, and auth on the new endpoints (unchanged — this dashboard has none by
  design, per `CLAUDE.md`).

## Architecture

### Shared cache + read endpoint

`dashboard/widget_cache.py` (new, mirrors `chart_app/bar_cache.py`'s shape): a small
`WidgetCache` class wrapping the SQLite table above, with `get(widget_id)` and
`set(widget_id, payload, status)`. `dashboard/app.py` owns one module-level instance.

`GET /api/widgets/{id}` reads via `WidgetCache.get`, returns the cached row verbatim (JSON
payload, status, computed_at as ISO string) or 404. This is the only route the frontend polls
for widgets 2/3/4 and for rendering widget 1 (even though widget 1's cache is filled by a
different path).

### Widget 1 — Positions (agent-pushed)

`POST /api/widgets/positions` (new pydantic body: list of `{account, ticker, instrument_type,
qty, avg_price, current_price, market_value, unrealized_pl}` plus per-account summary) writes
into `widget_cache` under `id="positions"`, `status="ok"`. No compute in the backend — the
scheduled agent already has live Robinhood data from its own MCP calls.

New `schedule`-skill cron: every 15–30 min, calls `get_equity_positions` +
`get_option_positions(nonzero=true)` + `get_portfolio` for both accounts (per the `robinhood`
skill's account table), normalizes into the shape above, `POST`s it. If the cron hasn't run
recently, the cache row's `computed_at` just ages — the frontend card shows a "stale since
{computed_at}" pill once it's >2-3 missed ticks old (~45 min), rather than erroring.

### Widget 2 — Vol/quant signal state

Background task, cadence ~5–10 min. Iterates `OVERVIEW_WATCHLIST = ["SPX", "NDAQ"]` (a plain
module-level list in `dashboard/app.py` or a small JSON config — implementation-plan detail;
either way, adding a ticker is a one-line change, per Jason's "make it easy to add to").
For each ticker, computes dealer-positioning direction, IV rank, and any active screener flag
via the in-process Vol_Suite call path noted above, and writes one payload row keyed
`id="signals"` containing a list of per-ticker results.

### Widget 3 — Per-position analysis

Background task, cadence ~30–60 min (heaviest of the three). Reads `widget_cache.get("positions")`
first; if absent or empty, writes an empty/`status="no_positions"` payload and returns. For
each held position, builds a minimal context dict (`{"ticker": ..., "focus": {"ticker": ...},
...}`) and calls, in-process:
- Options pricing: Options_Suite's context-mode path (`run_context_mode`, LeisenReimer
  default per `CLAUDE.md`'s fragile-surface note — do not regress to CRR).
- `Tools/tools/hedge_optimizer_tool.py::run` (mode `options_hedge`).
- `Tools/tools/price_dist_tool.py::run` for `mode="mc_sim"` and `mode="corr_sim"` (plus the
  default `price_dist` mode).

Writes one payload row keyed `id="position_analysis"`, a list of per-position results (pricing
stats, hedge suggestion, and the sim histograms — same `bins`/`percentiles`/`unit` shape
`quant.html`'s `renderDistribution` already knows how to draw, so the frontend can reuse that
renderer rather than inventing a new chart).

### Widget 4 — Surface Explorer showcase

Background task, cadence ~30–60 min. Calls `surface_explorer_tool`'s underlying
`_plot_surface_3d`/`_plot_heatmap` (or thin new wrappers around them) for SPX, three surfaces:
IV, Vanna, Charm. These functions get a **new optional dark-theme code path** — background/
text/gridline colors sourced from `base.html`'s CSS tokens (`--bg`, `--panel`, `--text`,
`--muted`, `--accent`/`--accent-2` for the surface colormap) — gated so the existing
light-themed output used elsewhere (e.g. the standalone `/tools/surface-explorer` page) is
unaffected unless it opts in. Renders to PNG, base64-encodes (or writes under `artifacts/` and
stores the path — implementation-plan detail depending on payload-size tradeoffs), writes
`id="surfaces"`.

### Frontend (`dashboard/templates/index.html`)

Four new `.widget-grid` cards (reusing `.panel`/`.widget` CSS already added). Each card's JS
polls its own `GET /api/widgets/{id}` on a light interval (~30–60s — cheap, cache-only reads,
independent of each widget's own backend compute cadence) and renders:
- Widget 1: per-account table + P&L, staleness pill.
- Widget 2: one row per watchlist ticker with direction/IV-rank/screener pills.
- Widget 3: one sub-section per held position (pricing stats, hedge suggestion, sim charts via
  `quant.html`'s existing `renderDistribution`/histogram pattern).
- Widget 4: three PNG panels (IV/Vanna/Charm), SPX, dark-themed.

## Error handling

- Every background task wraps its tick in `try/except`, writing `status="error"` (with a short
  message in the payload) rather than crashing the loop or leaving a stale `status="ok"` row —
  same convention as `chart_app`'s `_refresh_loop`.
- `GET /api/widgets/{id}` never 500s for a widget that simply hasn't computed yet — 404 with a
  clear "not computed yet" body, which the frontend renders as an empty/loading card state.
- Widget 1 has no error state from the backend's perspective (it never computes) — only
  staleness, surfaced as described above.
- Widget 3's `status="no_positions"` is a distinct, non-error state (flat is a valid state, not
  a failure) — the card should say "no open positions" rather than showing an error box.

## Testing

- `dashboard/widget_cache.py`: unit tests mirroring `chart_app/tests/test_bar_cache.py` (round
  trip, missing key, overwrite).
- `dashboard/tests/test_widgets.py` (new): `GET /api/widgets/{id}` 404-before-write / 200-after-write;
  `POST /api/widgets/positions` writes and is immediately readable; background tasks
  disabled-by-default in tests the same way `chart_app.create_app`'s
  `background_refresh_seconds` defaults to `None` — no widget background job may run network
  calls under plain test-client instantiation.
- Widget 3/4 background-job unit tests inject fake `hedge_optimizer_tool.run`/`price_dist_tool.run`/
  surface-plot functions (dependency injection, same shape as `chart_app.create_app`'s
  `daily_fn`/`intrad_fn` params) rather than hitting ThetaData in tests.
- Template/route tests assert the four widget cards render on `/` and that removed content
  (swap card, run-trigger form) stays removed — extending the existing
  `dashboard/tests/test_swap_card.py` coverage.
