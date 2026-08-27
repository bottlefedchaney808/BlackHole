# Split the Swaps Dashboard Out of the Main Dashboard — Design

Date: 2026-08-27
Status: Approved (Jason, in-session brainstorm)

## Purpose

`dashboard.bat` opens a browser tab to `/` about 2 seconds after launching uvicorn.
`home()` (`dashboard/app.py:645`) synchronously runs four swap-DB queries —
`_database_stats()`, `_top_notional()`, `_ingestion_state()`, `_scrape_log()` — against
`swaps.db` (342GB) before it can render anything. That's the reported ~4-minute wait
before the dashboard shows anything at all. Process startup itself is not the
bottleneck — the home page's first request is.

Move everything swap-DB-related into its own FastAPI app/process so the main
dashboard never touches `swaps.db` on the request path that has to respond
before you see a page. While doing this, the nav slot the swap browser used to
occupy gets repurposed into a live **Chart** tab (embedding the existing native
chart app) instead of just disappearing.

## Current state (verified)

- `dashboard/app.py` is a single 2759-line FastAPI app on port 8787, launched via
  `dashboard.bat` (`uvicorn dashboard.app:app --host 127.0.0.1 --port 8787`).
- Swap-DB imports at module scope: `db_loader.SwapsLoader`, `swaps_query.SwapsQuery`,
  `shared.query_builder.CrossSourceQueryBuilder`/`get_cross_source_summary`. None of
  these are invoked at import time — only inside route handlers — so process startup
  itself is cheap; the cost is entirely in what `home()` calls on every `/` request.
- Swap-specific routes: `/swaps` (browse/filter/search UI), `/trades` (JSON API),
  `/instruments/{upi}`, `/analytics/cross-source-notional`, `/analytics/timeseries`,
  plus the `_get_swaps_filter_options()` cache and `_db()` connection helper.
- `home()` additionally calls `_database_stats()`, `_top_notional()`,
  `_ingestion_state()`, `_scrape_log()` — all swap-DB reads — to populate Overview
  page widgets, on every load.
- `/suites/{suite}` treats `'swaps'` as one of five pseudo-suite labels for grouping
  *output files* from unified runs (`dashboard/app.py:1590`) — this is unrelated to
  the swaps DB browser and stays where it is.
- Top nav (`dashboard/templates/base.html:205`) has a "Swap trades" tab linking to
  `/swaps`.
- No port conflict: grepped the repo for 8788/8789/8790 — none in use.
- `chart_app/server.py` (FastAPI, port 8791) is the existing native chart app,
  today launched standalone via `chart_app.bat`/`.sh`: starts uvicorn, waits 2s,
  opens its own browser tab. It has no port-in-use guard (unlike `dashboard.bat`)
  and sets no framing-blocking headers, so it iframes without changes.

## Scope

- New sibling app `swaps_dashboard/` (own `app.py`, own `swaps_dashboard.bat`/`.sh`,
  port 8788) owning all swap-DB routes, imports, and templates.
- A background snapshot writer inside that app so the main dashboard can show real
  swap headline numbers without ever querying `swaps.db` itself.
- Main dashboard changes: drop swap routes/imports, replace Overview's swap widgets
  with a snapshot-backed card, add a matching card to `/tools`, and repurpose the
  old "Swap trades" nav slot into a new **Chart** tab that embeds the existing
  native chart app (`chart_app/server.py`, port 8791) via iframe.
- `dashboard.bat` also auto-launches `chart_app`'s uvicorn server (headless, no
  browser tab of its own) alongside the main dashboard, so the Chart tab has
  something to embed without a separate manual launch step.
- Out of scope: changing `swaps_query.py`/`db_loader.py`/schema; auto-launching the
  *swaps* dashboard process from the main dashboard (confirmed: static link +
  status, not subprocess spawn — chart_app is the one exception, per below); auth
  (none of the three apps has any, unchanged from today).

## Architecture

### New app: `swaps_dashboard/app.py`

A second, independent FastAPI app, structured like `dashboard/app.py` but scoped to
swap data only:

- Routes moved verbatim (same paths, same query params, same templates) from
  `dashboard/app.py`: `/swaps`, `/trades`, `/instruments/{upi}`,
  `/analytics/cross-source-notional`, `/analytics/timeseries`, `/health`.
- Imports moved: `SwapsQuery`, `SwapsLoader`, `CrossSourceQueryBuilder`,
  `get_cross_source_summary`, the `_db()` helper, `_get_swaps_filter_options()` cache.
- `swaps.html` and any swap-only template partials move to
  `swaps_dashboard/templates/`. This app gets its own minimal `base.html`: a copy of
  the main dashboard's CSS block (small, self-contained duplication rather than a
  cross-package import) so the two apps stay independently deployable, but its own
  nav: just "Swap trades" — no Overview/Quant Console/Suite output/Tools tabs, since
  those don't apply here.
- `swaps_dashboard.bat`/`.sh` mirror `dashboard.bat`'s structure: venv check,
  package-import preflight, "already running on this port" guard (checks 8788), and
  a delayed browser-open. Point straight at `/swaps` instead of `/`.

### Snapshot writer (inside `swaps_dashboard/app.py`)

An `asyncio` background task, started from the app's lifespan handler, that every
5 minutes:

1. Runs the same four queries `home()` used to run (`get_database_stats()`, recent
   top-notional via `orchestrator.get_recent_swap_activity`, ingestion state, last
   scrape log).
2. Writes the result as JSON to `swaps_dashboard/cache/overview_snapshot.json`
   (`{generated_at, stats, top_products, ingestion, last_scrape}`), atomically
   (write to `.tmp` + `os.replace`).

This file is the *only* thing the main dashboard ever reads for swap data — a plain
local file read, no DB connection, no query.

### Chart tab (embeds `chart_app`, :8791)

The old "Swap trades" slot in `base.html`'s nav becomes a **Chart** tab, `/chart`,
rendering a single full-height `<iframe src="http://127.0.0.1:8791/">`. `chart_app`
is a separate, already-existing FastAPI app (`chart_app/server.py`) with no
X-Frame-Options/CSP header set, so it iframes cleanly from `127.0.0.1:8787` (same
host, different port — no cross-origin restriction applies to iframing itself).

`dashboard.bat` is extended to also start `chart_app`'s uvicorn server headlessly
(background, no browser tab of its own — the existing `chart_app.bat`/`.sh` still
work standalone and still open their own tab when run directly) alongside the main
dashboard, guarded by the same "already listening on this port" check `dashboard.bat`
already uses for 8787, applied to 8791. Because `chart_app`'s own uvicorn start is
async, the iframe's first paint can briefly race a few hundred ms of startup; the
`/chart` page reloads the iframe `src` once, 3 seconds after initial load, as a cheap
one-shot mitigation (not a polling/retry loop).

### Main dashboard changes (`dashboard/app.py`)

- Delete `/swaps`, `/trades`, `/instruments/{upi}`, `/analytics/*`, the swap-DB
  imports, `_db()`, `_get_swaps_filter_options()`, `_database_stats()`,
  `_top_notional()`, `_ingestion_state()`, `_scrape_log()`.
- `home()` instead calls a new tiny `_swaps_snapshot()` helper: reads
  `swaps_dashboard/cache/overview_snapshot.json` if present (try/except, never
  raises), returns `None` if missing — same "degrade to empty, never 500" pattern
  the rest of the file already follows.
- Overview page gets one new card in place of the four old widgets: headline numbers
  from the snapshot (total records, top notional product, last ingestion time, DB
  size) plus a "last updated `<generated_at>`" badge and an "Open swap browser"
  link to `http://127.0.0.1:8788/swaps`. If no snapshot exists yet, the card shows
  just the link and a note that the swap dashboard hasn't been launched.
- Client-side JS (small `fetch('http://127.0.0.1:8788/health', {mode:'no-cors', ...})`
  pattern, short timeout, same spirit as the existing share-status polling) flips a
  "live" vs. "last seen" badge on the card without blocking page render — this
  never gates the initial response.
- Same card (same partial/include) added to `/tools`.
- New `/chart` route + template (see above); `base.html` nav's "Swap trades" entry
  becomes "Chart" pointing at `/chart`.
- `/suites/{suite}`'s `'swaps'` pseudo-suite label handling is untouched — it groups
  output *files*, not this DB browser.

### Data flow

```
swaps_dashboard.bat (port 8788)
  -> swaps_dashboard/app.py: /swaps, /trades, /instruments, /analytics/*
  -> background task every 5 min -> cache/overview_snapshot.json

dashboard.bat (port 8787)
  -> also starts chart_app's uvicorn headlessly (port 8791), if not already up
  -> dashboard/app.py: home() reads cache/overview_snapshot.json (file read only)
  -> Overview + Tools cards show real numbers + live/stale badge (client JS)
  -> "Open swap browser" link -> http://127.0.0.1:8788/swaps
  -> new "Chart" tab (/chart) iframes -> http://127.0.0.1:8791/
```

## Error handling

- Snapshot file missing, unreadable, or stale JSON: card falls back to link-only,
  no error surfaced to the user (matches existing "everything degrades to empty
  state" convention in `dashboard/app.py`'s module docstring).
- `swaps_dashboard` not running: main dashboard is completely unaffected — it never
  makes a network call to port 8788 server-side; only the client-side badge check
  can fail, and it fails silently to "not running."
- Two `swaps_dashboard.bat` instances: same guard pattern as `dashboard.bat` today
  (checks port 8788, offers to open the existing instance instead of double-launching
  against `swaps.db`).
- `chart_app` already running (e.g. launched standalone via `chart_app.bat` earlier):
  `dashboard.bat`'s port-8791 check sees it's up and skips starting a second copy.
- `chart_app` fails to start, or is slow to bind: the main dashboard's own boot is
  unaffected (it's launched as a background/`start ""` process, not awaited); the
  `/chart` tab's one-shot 3s iframe reload covers the common "still binding" case,
  and a hard failure just leaves the iframe showing the browser's own connection
  error — no crash, no blocked page.

## Testing

- Move `dashboard/tests/test_swaps_options_cache.py` (and any other swap-route
  tests under `dashboard/tests/`) to `swaps_dashboard/tests/`, retargeted at
  `swaps_dashboard/app.py`.
- New test: `dashboard/app.py` no longer imports `swaps_query`/`db_loader` at
  module scope (a cheap regression guard against the split silently drifting back).
- New test: `_swaps_snapshot()` handles missing file, malformed JSON, and a valid
  snapshot correctly (three cases, no real DB needed — write a fixture JSON).
- Manual verification: `dashboard.bat` launch → Overview renders in well under a
  second even with a cold/huge `swaps.db`. `swaps_dashboard.bat` launch → `/swaps`,
  `/trades`, `/instruments/{upi}`, `/analytics/*` behave identically to before the
  split. After `swaps_dashboard.bat` has run once, the Overview card shows real
  numbers on the next `dashboard.bat` launch, live-badge flips correctly when the
  swaps process is stopped. `dashboard.bat` launch with 8791 cold → Chart tab shows
  the live chart within a few seconds (via the one-shot reload); with `chart_app`
  already running standalone → dashboard skips relaunching it and the tab shows the
  running instance immediately; `chart_app.bat` run directly still opens its own
  browser tab exactly as it does today.
