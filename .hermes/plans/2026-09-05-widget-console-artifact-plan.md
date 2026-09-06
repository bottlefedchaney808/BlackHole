# Plan — Widget Console in Hermes Interactive Artifacts (pick one or many widgets, run them)

**Date:** 2026-09-05 · **Status:** FINAL — ready to execute · **Owner:** local-agent
**Repos touched:** `C:/Users/bottl/FinancialDevelopment` (server-side CORS, already patched) + `C:/Users/bottl/hermes-artifacts` (board + collector)

## Deliverable surface — what "in Hermes Interactive Artifacts" means here

A new entry in the **installed `interactive-artifacts` plugin**:

- Desktop sidebar → **Interactive** pane lists it (backend `GET /items`)
- Jason clicks it / mounts it into a docked pane (backend serves `index.html` into webview partition `persist:interactive-artifacts`)
- The pane's Refresh button works (backend `POST /refresh {id:"widget-console"}` → shells out to `tools/refresh.py`)

Verified installed backend (`%LOCALAPPDATA%/hermes/plugins/interactive-artifacts/dashboard/plugin_api.py`):
content root is hardcoded to **`C:/Users/bottl/hermes-artifacts`**, serves only `artifacts/<id>/index.html`,
regex-validates ids (`^[a-z0-9][a-z0-9-]{0,63}$` → `widget-console` passes), refresh timeout 120s.
Board files therefore live in the hermes-artifacts git repo — that is *how* this plugin gets content;
nothing new ships as a plugin. Per framework rule: **extend interactive-artifacts, never add a
competing sidebar plugin.** Zero changes to `plugin_api.py` or either `plugin.js`.

## Goal

A board in the Interactive pane that:

1. Loads every widget from the live registry (`GET http://127.0.0.1:8787/api/widgets/catalog` — ~52 modules across vol_suite / tools / sentiment_scanner / options_suite / var_tools).
2. Lets Jason **pick one or many** widgets: search filter + checkbox list grouped by suite, select-all/none per group and globally.
3. Sets scope once (ticker, default SPY, seeded from `default_context.json`'s ticker if present — same convention the TUI uses) and runs the selection **sequentially**.
4. Streams results per-widget as they complete: status card per widget (queued → running… with live elapsed → ok/failed), headline metric line, expandable bounded JSON body. The board IS the console — no terminal, no TUI.

## Context / current state (all verified this session)

| Item | State |
|---|---|
| Widget API (`dashboard/app.py`, uvicorn `127.0.0.1:8787`) | Running (pid 12932 at audit). `GET /api/widgets/catalog` → `{widgets:[{name,slug,suite,category,description,inputs:{ticker,expiry,basket},output_kind,sample,default_selected,requires[]}]}`. `POST /api/widgets/{slug}/run` body `{scope:{ticker,...},params:{}}` → **synchronous**, returns `{status,slug,scope,artifacts[],metrics{},context_patch}`; module-level failures come back as `status=error/failed` with metrics.error — not HTTP 500s. `GET /api/widgets/{slug}/state?scope=ticker:SPY` → last cached result, 404 if none. Accepts flat context too (no scope wrapper). |
| CORS | **Patched, not live.** `CORSMiddleware allow_origins=["*"]` inserted after `app = FastAPI(...)` at `dashboard/app.py:267` (uncommitted). Running server predates it. Restart required before the board can call the API from a `file://` page (Origin `null`). |
| `tui/quant_tui.py` | Textual client of the same API; its `render_payload` (status/scope/run_id/artifacts + ~20k-char JSON cap) is the rendering reference. Not a dependency. |
| hermes-artifacts framework | 7 boards; `tools/refresh.py` inlines `assets/hermes-artifact.css/.js` into each board's `index.html` at three HERMES-ARTIFACT markers; template may carry its **own** extra `<script>` blocks and they survive rebuilds untouched. Universal renderer draws cards/charts/tables only — no interactive controls — which is why the console UI lives in the template's own script block. |
| Collector draft | Written: `tools/collectors/widget_console_collector.py` (`@register("widget-console")`, stdlib-only, 5s timeout, catalog snapshot table + API-health card, graceful API-down note). Two early typos already fixed; uncommitted. |
| Abandoned path | `hermes-artifacts/suite_stream.py` (SSE panel for raw orchestrator stdout) — wrong problem shape; **delete in cleanup**. |
| Known live-run timings (SPY, today) | gex/max_pain/iv_rank/skew ≈ seconds each; dealer_exposure + dual_book ≈ 60–90s combined. dealer_flow self-skips (expected, not an error). |

## Architecture decisions (reviewed; changes from draft marked ✱)

**D1 — Streaming = client-side sequential queue with live status cards (was Option A/C debate).**
The API runs modules in-process and returns one JSON blob per run; there is no server-side log
stream to consume. Adding one (old Option B) means new server surface, another restart, and the
log content is mostly HTTP-request noise — low signal. ✱ *Decision recorded as A-with-C-later:
build A now; if Jason asks for raw log lines after real use, add `POST /run/stream` SSE then (the
sequential runner is structured so a per-widget stream can replace the plain POST without UI changes).*

**D2 — Sequential always, no parallel toggle.** Single uvicorn worker + shared ThetaData budget +
Context Store writes. Parallel fan-out risks rate limits and interleaved writes for zero real
speedup (fast widgets are seconds; the slow pair are the tail anyway). ✱ *Change: the queue also
writes a `runlog` line per widget into the board's visible log strip (timestamp, slug, elapsed,
status) so a many-widget run is auditable after the fact, not just watchable.*

**D3 — CORS on loopback accepted.** Server binds `127.0.0.1` only, no credentials, artifacts pages
are local files. `allow_origins=["*"]` + methods GET/POST/OPTIONS + Content-Type header only.
✱ *Change: also confirmed `OPTIONS` preflight is exempt from slowapi rate limiting (limiter
targets specific routes), and the middleware is inserted before the static mount so it covers all
routes. The proxy-port alternative is rejected: more moving parts, no security gain on loopback.*

**D4 — Result rendering: headline + bounded JSON.** Per-category headline map (gex→`total_net_dollar_gamma`+`gamma_flip_level`, max_pain→`max_pain_strike`+`price_vs_pain_pct`, iv_rank→`atm_iv_pct`+`regime`, skew→`skew_signal`+`put_skew_pts`, dual_book→`position_regime`+`position_z`, dealer_exposure→`band_regime`+`band_z`) with generic fallback (status + first 4 metric keys). Metrics live at the top level of the run response; the rich objects (context_patch) are **not** rendered — they're huge and already persisted server-side. JSON body capped ~20k chars in a collapsed `<details>`.

**D5 — ✱ New: cached-state warm start.** On catalog load, the board also tries
`GET /state?scope=ticker:<T>` per widget and pre-fills any card that has cached state with a
"Cached <timestamp>" chip — so a freshly mounted board isn't blank before the first run. 404s are
silently ignored (expected for never-run widgets).

**D6 — ✱ New: run-safe restart discipline.** The dashboard restart (CORS activation) kills any
in-flight widget run and resets the background widget jobs (which make their own ThetaData calls on
startup cadence). Restart ONLY when no run is in progress, via the documented stale-process rule
(netstat → LISTENING pid → PowerShell `Stop-Process -Force`; bash taskkill wrappers fail), then
`dashboard.bat`. Verify CORS with a preflight curl before building the board.

## Work items (dependency order)

| # | Item | Where | Done-when |
|---|------|-------|-----------|
| 1 | Activate CORS: restart dashboard (run-safe discipline D6) | FinDev | preflight `curl -i -X OPTIONS http://127.0.0.1:8787/api/widgets/catalog -H "Origin: null" -H "Access-Control-Request-Method: GET"` → 204 with `access-control-allow-origin: *`; `GET /` still 200 |
| 2 | Board scaffold | `hermes-artifacts/artifacts/widget-console/artifact.json` + `template.html` | artifact.json `{id, title:"Widget Console — pick & run widgets", collector:"widget-console", repo:"C:/Users/bottl/FinancialDevelopment", description, refresh hint}`; template = standard 3-marker shell + one ES5 `<script>` block |
| 3 | Console UI + runner in template script | same template | catalog fetch on load (+ auto-retry banner if API down, with "run dashboard.bat" hint); search filter; checkbox list grouped by suite with group select-all; ticker input (default SPY); Run Selected / Stop; per-widget card with status dot, elapsed timer, headline, cached chip (D5), `<details>` JSON; sequential queue (D2) with runlog strip; Abort abandons remaining queued items client-side |
| 4 | Collector + refresh | `tools/refresh.py widget-console` | payload passes `validate_payload`; `grep -c "runSelected"` (or equivalent marker) > 0 in **both** template.html and regenerated index.html — the stale-build gate |
| 5 | Live verification (with Jason) | Desktop → Interactive → mount `widget-console` | catalog count matches `orchestrator.py --list-modules` (52); iv_rank single run: queued→running→ok with ATM IV + regime rendered; multi-select ≥3 mixed-speed (incl. dealer_exposure): completes in order, runlog correct, one deliberate failure (bad ticker) renders failed-with-error and does NOT kill the queue |
| 6 | Commit | FinDev: `fix(dashboard): CORS for local artifact boards`; hermes-artifacts: board + collector | conventional-commit hook enforced in FinDev; hermes-artifacts commits as bottlefedchaney808 |
| 7 | Cleanup | delete `suite_stream.py` + `$LOCALAPPDATA/Temp/t1.py t2.py t3.py` scratch | gone from disk; not committed anywhere |

## Risks / pitfalls

- **Stale index.html** — index.html is a tracked build artifact; after ANY template edit, refresh + grep-gate, or Jason mounts stale UI silently (framework lesson).
- **Long fetches** — dealer_exposure+dual_book ≈ 90s today; fetch has no artificial short timeout (generous 15-min ceiling); elapsed timer prevents "hung" reads; Stop abandons remaining queue client-side only (server finishes current widget — acceptable).
- **Don't touch shared assets** — console JS lives in the template's own block, ES5 (`var`, function expressions, no arrow funcs) to match the framework's parser constraints; edits to `assets/hermes-artifact.js/.css` would drift all 8 boards.
- **Two dashboards** — launcher refuses duplicate ports; never hand-start uvicorn on 8787; restart via the kill-and-relaunch rule only.
- **Slowapi rate limiter exists** on the app — sequential queue keeps request cadence identical to one TUI session (proven safe today).
- **API-down at board open** — must render the retry banner, not a blank page (offline `data.json` snapshot gives the suite/grouping so the board is still useful read-only).

## Acceptance criteria

1. `refresh.py widget-console` exits 0; board listed in `--list`; visible in Desktop Interactive pane; mounts without console errors.
2. Live catalog count == `--list-modules` count; suite grouping correct.
3. Single run (SPY iv_rank): card transitions with true elapsed; headline shows ATM IV % + regime matching the returned metrics.
4. Multi-run (≥3, mixed speed, one bad ticker): strict sequential order, every card reaches terminal state (ok **or** failed), runlog strip records each, board stays interactive throughout.
5. Refresh button in the pane works (`POST /refresh` 200) and the console UI survives it (marker grep).
6. Zero diffs in `assets/` and in the other 7 boards after this work.

## Explicitly out of scope

- Server-side SSE/log streaming (D1 — revisit only on real demand).
- Parallel execution, saved presets, cross-ticker batch matrices, editing `default_context.json` from the board.
- Any change to `plugin_api.py`, `plugin.yaml`, or either `plugin.js`.
