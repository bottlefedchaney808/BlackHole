# PLAN — Overview/Chart layout, widget wiring, and swaps archive restore

Status: plan-only; NO production code changed
Author: Hermes Agent (grounded in live repo + kanban + disk, 2026-09-08)
Spec source: Jason screenshots (Overview + Chart tabs) + kanban `t_b82fb352` chain

> **For Hermes:** Use subagent-driven-development to implement task-by-task after Jason approves. Two independent workstreams (A dashboard, B swaps) can run in parallel.

**Goal:** Make Overview a live SPY desk (chart + IV/VRP cards + working positions), make Chart fill the window with a real widget sidebar and working ticker/TF/overlay controls, and put swap history back on the path every query already uses.

**Architecture:** No new frontend framework. Keep `<quant-widget>` + `syncBus` + `dashboard_layouts`. Chart tab is a full-bleed CSS grid (chart 1fr + sidebar), not GridStack. Positions/signals/surfaces become cache-backed ModuleSpecs so Run/state work. Live swap queries point at the untouched 323 GB OneDrive DB via `SWAPS_DB_PATH`; the 4.66 MB repo copy stays the GitHub-sized primary.

**Tech Stack:** FastAPI (`dashboard/app.py` :8787), vanilla JS (`quant-widget.js`, `chart_app/static/index.html` ECharts), SQLite (`swaps.db`, `widget_cache.db`), existing `iv_rank` ModuleSpec + variance-swap screener.

---

## Grounding (read before touching code)

| Source | What it contributes |
|---|---|
| Screenshots 2026-09-08 | Overview: 0 orchestrator runs, four widgets stuck on "Not run yet", empty history. Chart: empty left "Chart widgets", skinny SPY 15m strip, huge empty sides. |
| `dashboard/templates/base.html:71` | `main { max-width: 1500px; margin: 0 auto; }` — centers the page and leaves black gutters on a wide monitor. |
| `dashboard/templates/chart.html` | `grid-template-columns: 360px 1fr` + iframe `:8791`. No add-widget UI. Layout CSS lives in `{% block scripts %}`. |
| `chart_app/static/index.html` | ECharts `init` once; `window.resize` only; `loadSymbol` never calls `chart.resize()`. Overlays (ema20/50, vwap, BB, whale) always drawn; legend exists, no add/remove chrome. Direction legs W3/SQ/TR/WH/LQ are status pills, not series toggles. |
| `dashboard/templates/index.html` | Hardcoded `<quant-widget slug="positions\|signals\|position_analysis\|surfaces">`. Fetches `/api/layout/overview` but has no add/remove/reorder chrome. |
| `dashboard/static/js/quant-widget.js` | Always `POST /api/widgets/{slug}/run`. `_maybeFetchState` skips unless sync-bus already has ticker/expiry/basket. Never reads legacy `GET /api/widgets/{id}`. |
| `dashboard/app.py` | `POST /api/widgets/positions` writes unscoped `widget_cache`. Background ticks write `signals` / `position_analysis` / `surfaces` the same way. Generic run API is scoped. **No ModuleSpec named `positions`.** |
| `dashboard/app.py:193` | `OVERVIEW_WATCHLIST = ["SPXW", "NDAQ"]` — not SPY. Comment in `_widget_signals_tick` still claims IV rank does not exist; `sentiment-scanner/module_registry.py` already registers `slug="iv_rank"`. |
| `Vol_Suite/module_registry.py:961` | `vrp_term_structure` is a **selection-only marker**, not a runnable widget. Do not point Overview VRP cards at it. Use `screen_ticker` `vrp_pct` (already in the signals tick) or `iv_rank` scanner's `vrp_pct`. |
| `dashboard/templates/quant.html` | Catalog + up/down reorder + layout PUT. Grid is **1 column**. No drag, no resize. This is the only page that can add widgets. |
| Kanban `t_b82fb352` / `t_b75acef9` / `t_4dc1108f` / `t_2d1118d3` | Split marked done 2026-09-06. Primary at repo root = 941 rows / 4.66 MB. Original 323 GB **untouched** at OneDrive. |
| Disk 2026-09-08 | `FinancialDevelopment/swaps.db` 4.66 MB, 941 rows, `effective_date` 2026-01-12..2031-09-15. OneDrive original still 323 GB. Archives: `swaps_pre-2025-06.db.gz` (2.1G), `2025-06` (615M), `2025-07` (1.6G). **Missing `2025-08` and `2025-09-onward` (the 51M-row bulk).** `split_run.log` stops after 2025-07. `split_swaps_db.py` lives only on `wt/t_b82fb352` (not merged). `.gitignore` still ignores `swaps.db`. |
| `CLAUDE.md` | Fragile surfaces: Leisen-Reimer default, SPXW-vs-SPX aliasing, never double-launch dashboard against swaps.db. |

---

## Symptom → root cause (do not re-diagnose)

1. **Chart skinny / empty sides.** `main` max-width 1500px centers the tab. Iframe ECharts inits before the iframe has its final box, never `resize()`s after Load/interval, so the canvas stays a ~300px strip. Leaving the tab and coming back fires a window resize → it "fixes itself."
2. **Blank on ticker/timeframe change.** `loadSymbol` → `POST /api/symbol` + `/api/refresh` + `poll()` → `setOption(opt, resetZoom)` with no `chart.resize()`. Zero-size canvas + `notMerge` reset = blank until a later resize.
3. **No add/remove indicators.** Overlays are hardcoded in `render()`. ECharts legend can hide series if you click it, but there is no checkbox bar and no persistence. Direction `indicator.py` feeds W3/SQ/TR/WH/LQ pills only.
4. **Chart widgets empty.** `/api/layout/chart` is empty. Chart tab never renders the Quant Console catalog. No way to add a widget there.
5. **Overview positions "isn't working."** `<quant-widget slug="positions">` POSTs `/api/widgets/positions/run` → `resolve_modules(["positions"])` 404s. The live payload is in unscoped `widget_cache` behind `GET /api/widgets/positions` and `POST /api/widgets/positions` (agent push). Same mismatch for `signals` / `position_analysis` / `surfaces`.
6. **Do modules sync to positions?** No. `syncBus` is `{ticker, expiry, basket}` only. `position_analysis` background tick *reads* the positions cache, but the Overview widget does not. Injecting holdings into other widgets does not exist.
7. **Orchestrator runs = 0.** Expected after Phase 7 retired `run_unified`. The Overview KPI card and "Orchestrator run history" table are leftover chrome. Do not revive subprocess launching to fill them.
8. **Swaps queries see 941 rows.** Apps default to repo-root `swaps.db`. The 71M-row book is still at OneDrive; gzip archives are incomplete and nothing ATTACHes them.

---

## Locked decisions (Jason's screenshots + fallback)

These are the defaults. Change only if Jason overrides.

1. **No GridStack / drag-resize in this pass.** Jason: "If you cannot resize easy just make the chart stretch out and have the widgets as a sidebar." Chart tab = full-bleed stretch + sidebar. Overview = fixed CSS grid, not a dashboard builder.
2. **Chart tab layout:** `[ chart 1fr ] [ sidebar 320px ]`. Sidebar on the **right** (chart gets the stretch he drew). Cap 4 widgets. Add from a compact catalog picker in the sidebar header (reuse Quant Console catalog fetch).
3. **Overview hero:** one live SPY chart (15m, iframe to `:8791?ticker=SPY&interval=15m`), **two compact cards per side** (left: IV Rank, VRP; right: Skew, GEX or dealer-direction — whatever those ModuleSpecs already return without a full Vol run). A second chart (SPY 1d) is CONDITIONAL, same embed with a different query string, only if the first embed is stable.
4. **Positions widget** is a **cache reader**, not a runnable scanner. Mount shows last `POST /api/widgets/positions` payload (or "no positions yet / stale"). Run/Refresh re-reads cache; it does not 404.
5. **"Inject as context"** on the positions widget writes `held_tickers` + `positions` into the Context Store under the current scope and publishes `basket=held_tickers` on `syncBus`. It does **not** auto-run sibling widgets. Other widgets pick it up on their next Run if synced.
6. **Module sync stays ticker/expiry/basket.** Positions is an optional inject, not a fourth sync field.
7. **Indicators:** checkbox bar in `chart_app` for `ema20, ema50, vwap, bb, whale, volume, flow`. Persist `localStorage['chart-app-overlays']`. Do not port TradingView indicators. Direction legs stay pills.
8. **Swaps live path:** point `SWAPS_DB_PATH` at the **untouched** OneDrive file `C:/Users/bottl/OneDrive/Stocks/Swaps/swaps.db` so every existing reader (`db_loader.py`, `swaps_query.py`, `orchestrator.py`, `swaps_dashboard`, MCP `query_swap_data`) sees the full book with zero schema change. Repo-root 4.66 MB file stays the GitHub-sized copy (merge `wt/t_b82fb352` gitignore exception). Gzip archives are cold backup, not the query path.
9. **Do not gzip-restore into the repo-root file.** That would put hundreds of GB back on the clone and break the GitHub goal.

---

## Workstream A — Dashboard / Chart

### Phase A0 — Chart fill + blank-on-switch (highest visual pain)

**Files:**
- Modify: `dashboard/templates/base.html` (opt-out max-width)
- Modify: `dashboard/templates/chart.html` (full-bleed layout, sidebar right, resize ping)
- Modify: `chart_app/static/index.html` (ResizeObserver, resize after render/loadSymbol, query-string pin)
- Test: `dashboard/tests/test_chart_tab.py`, `chart_app/tests/test_server.py` (query-string if added server-side)

**Steps:**
1. Add a body class hook: `{% block body_class %}{% endblock %}` on `<body>` in `base.html`. Chart template sets `body_class = "page-chart"`. CSS: `body.page-chart main { max-width: none; padding: 12px 16px; }`.
2. Flip `.chart-layout` to `grid-template-columns: minmax(0, 1fr) 320px`. Iframe `width:100%; height:calc(100vh - 120px);`. Sidebar `min-width:280px`.
3. In `chart_app/static/index.html`:
   - `const ro = new ResizeObserver(() => chart.resize()); ro.observe(document.getElementById("chart"));`
   - End of `render()` and `loadSymbol()` `finally`: `chart.resize();`
   - On boot, read `URLSearchParams`: if `ticker`/`interval` present, set inputs and call `loadSymbol()` once (for Overview embed).
4. Parent Chart tab: `iframe.onload` + `document.addEventListener('visibilitychange')` → `chartFrame.contentWindow.postMessage({type:'chart-resize'}, '*')`. Child listens and `chart.resize()`.
5. Tests: chart.html contains `page-chart` and `minmax(0, 1fr)`. `GET /chart` still iframes `http://127.0.0.1:8791`. Manual: switch ticker QQQ, switch 5m, confirm candles paint without leaving the tab.

- Tests: existing `test_chart_tab.py` plus one new assertion on the full-bleed class. chart_app JS has no pytest — verify with a 20s visual load on :8791.

### Phase A1 — Overlay add/remove on the native chart

**Files:**
- Modify: `chart_app/static/index.html` only (YAGNI: no Python overlay registry)

**Steps:**
1. Add a `#overlays` row of checkboxes next to `#legs`: ema20, ema50, vwap, bb, whale, volume, flow. Default all on (current behavior).
2. `enabledOverlays()` reads boxes; `render()` skips series whose name is off. Keep OHLC always on.
3. `localStorage['chart-app-overlays']` JSON on change; restore on boot.
4. ECharts legend `selected` map mirrors the checkboxes so legend clicks stay in sync.

- Tests: none network; visual. Do not touch `Direction/indicator.py`.

### Phase A2 — Cache-backed ModuleSpecs so Overview widgets actually show data

**Files:**
- Create: `dashboard/cache_widgets.py` (four tiny `run(context) -> ModuleResult` wrappers)
- Modify: `shared/module_registry.py` `_suite_modules()` or a dashboard-local register so `all_modules()` includes them
- Modify: `dashboard/static/js/quant-widget.js` — on mount, if catalog `output_kind`/`category=="cache"` OR slug in `{positions,signals,position_analysis,surfaces}`, `GET /api/widgets/{slug}` (legacy unscoped) **then** scoped state; render without requiring Run
- Modify: `dashboard/tests/test_widget_generic_api.py`, `test_overview_widgets.py`

**Positions wrapper (sketch):**

```python
def run_positions(context: dict) -> ModuleResult:
    from dashboard.widget_cache import WidgetCache  # or injected cache
    row = cache.get("positions")
    if row is None:
        return ModuleResult(status="idle", artifacts=[], metrics={"message": "no positions pushed yet"}, context_patch=None)
    payload = row["payload"] or {}
    return ModuleResult(
        status=row.get("status") or "ok",
        artifacts=[],
        metrics={"positions": payload.get("positions") or [], "accounts": payload.get("accounts") or [], "computed_at": row.get("computed_at")},
        context_patch=None,
    )
```

Same shape for `signals` / `position_analysis` / `surfaces` reading their unscoped ids.

Also register them in `all_modules()` with `inputs=InputSpec(ticker="optional")` so `_validate` does not demand SPY.

**quant-widget mount:** if first paint would be "Not run yet", call `_maybeFetchLegacy(slug)` → `GET /api/widgets/{slug}` (the unscoped route already exists at `app.py:1838`).

- Tests: `POST /api/widgets/positions` then `POST /api/widgets/positions/run` returns 200 with the same rows (not 404). Overview HTML still has `slug="positions"`.

### Phase A3 — "Inject as context" on positions

**Files:**
- Modify: `dashboard/static/js/quant-widget.js` (positions-only chrome)
- Modify: `dashboard/app.py` `POST /api/widgets/positions/inject` **or** reuse `context_store.put` from a small route
- Modify: `dashboard/static/js/sync-bus.js` — no schema change; inject calls `syncBus.setScope({basket: tickers})`
- Test: `dashboard/tests/test_widget_generic_api.py`

**Behavior:**
1. Positions widget header grows a button `Inject as context` (visible only when `slug==="positions"` and last payload has ≥1 ticker).
2. Click → `POST /api/context/inject-positions` body `{positions: [...]}` writes Context Store keys `positions`, `held_tickers` with `source_slug="positions"`.
3. Same click publishes `syncBus.setScope({basket: unique_tickers})`. Synced siblings update their basket input. They do **not** auto-run.
4. Provenance pane on Quant Console already reads `GET /api/context` — it will show the inject.

- Tests: inject with two fake positions → `GET /api/context` contains `held_tickers`; unknown slug still 404.

### Phase A4 — Overview hero: SPY chart + four metric cards

**Files:**
- Modify: `dashboard/templates/index.html`
- Modify: `dashboard/app.py` `OVERVIEW_WATCHLIST` to include `"SPY"` (keep SPXW for surfaces)
- Reuse: `iv_rank` and `skew` ModuleSpecs (sentiment-scanner). VRP card reads `signals` cache `vrp_pct` for SPY (already computed by `_widget_signals_tick` once watchlist includes SPY). Fourth card: `gex` ModuleSpec if it runs cheap; otherwise dealer-direction from signals `signal`/`score`.

**Layout (CSS grid, not drag):**

```
.overview-hero {
  display: grid;
  grid-template-columns: 220px 220px minmax(0, 1fr) 220px 220px;
  gap: 12px;
  min-height: 420px;
}
```

Left: `<quant-widget slug="iv_rank" synced data-context='{"ticker":"SPY"}'>`, `<quant-widget slug="signals">` filtered to VRP (or a tiny `data-output-kind="metrics"` card). Center: iframe `{{ chart_app_url }}?ticker=SPY&interval=15m` with the same resize ping as Chart tab. Right: skew + gex (or signals score).

Below hero: keep the 2-col `.widget-grid` of positions / position_analysis / surfaces / (drop the dead "Orchestrator runs = 0" KPI or relabel it "Widget cache" — do not resurrect `run_unified`).

Move run-history + suite-output pills under the grid, smaller.

Default Overview layout if `/api/layout/overview` is empty: the hardcoded hero + four cards above. If a saved layout exists, **do not** wipe the hero; layouts apply only to `.widget-grid`.

- Tests: `test_overview_widgets.py` asserts `ticker=SPY` iframe and `slug="iv_rank"`. Watchlist unit test includes SPY.

### Phase A5 — Chart sidebar can add widgets

**Files:**
- Modify: `dashboard/templates/chart.html`
- Reuse Quant Console catalog JS (extract `loadCatalog`/`addInstance` into `dashboard/static/js/layout-chrome.js` if copy-paste would exceed ~40 lines; otherwise duplicate the 20-line fetch+PUT — YAGNI extract if Chart + Overview both need it)

**Steps:**
1. Sidebar header: `+ Add` opens a `<select>` of catalog slugs (cap 4 instances).
2. Persist via existing `PUT /api/layout/chart`.
3. Remove button per widget.
4. Empty state copy: "Add IV Rank or VRP from +" — not a dead end.

- Tests: `test_page_render.py` already checks layout fetch; add assertion for `#chartAddWidget` id.

### Phase A6 — Quant Console: 2-column grid only (no drag)

Quant Console already has up/down reorder. Change `.console-grid` from `1fr` to `repeat(auto-fill, minmax(520px, 1fr))` so two widgets sit side by side. No GridStack.

- Tests: none beyond existing layout PUT tests.

---

## Workstream B — Swaps archive restore

### What the bots actually did (kanban, verified on disk)

| Task | Claimed | Reality 2026-09-08 |
|---|---|---|
| `t_b75acef9` coder1 | Analyzed 346 GB, recommended year shards | Report in worktree. Source file untouched. |
| `t_4dc1108f` coder1 | Wrote `db_shrink_split.py`, 6 tests | Script in `.worktrees/t_4dc1108f` only. Never ran against the 346 GB file. |
| `t_2d1118d3` coder1 | "Apps connect, DB 0.12 MB" | Verified an **empty schema** DB. Not the live book. |
| `t_b82fb352` local-agent | Built 4.66 MB primary at repo root; archives gzipping in background | Primary is real (941 rows). Archives **incomplete**: missing 2025-08 and 2025-09-onward. Original 323 GB still at OneDrive. Branch `wt/t_b82fb352` **not merged**. `.gitignore` still ignores `swaps.db`. |

Jason's original order: shrink/split so GitHub can take it, **then move data back where apps that query swaps still function.** The first half landed. The second half did not: every default reader now hits 941 rows.

### Phase B0 — Put the full book back on the live query path (do this first)

**Files:**
- Modify: `dashboard.bat` / `dashboard.sh` / `swaps_dashboard.bat` / `swaps_dashboard.sh` / `run_scheduler.bat` to export
  `SWAPS_DB_PATH=C:\Users\bottl\OneDrive\Stocks\Swaps\swaps.db`
- Modify: `docs/guides/START_HERE.md` and `CLAUDE.md` one-liner: live DB is OneDrive; repo-root `swaps.db` is the GitHub-sized recent window.
- Do **not** copy 323 GB into the repo.

**Why this and not ATTACH-gzip:** every consumer already honors `SWAPS_DB_PATH`. Gzip shards are not SQLite. ATTACH would require a new query layer in `swaps_query.py`, `db_loader.py`, MCP, and the swaps dashboard — that is a project, not a restore. The original file is still there.

**Verify (read-only):**

```bash
env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -c "
import os, sqlite3
os.environ['SWAPS_DB_PATH'] = r'C:\Users\bottl\OneDrive\Stocks\Swaps\swaps.db'
from pathlib import Path
p = Path(os.environ['SWAPS_DB_PATH'])
c = sqlite3.connect(f'file:{p.as_posix()}?mode=ro', uri=True, timeout=30)
print(c.execute('SELECT COUNT(*) FROM swap_trades').fetchone()[0])
"
```

Expected: ~70,998,132 (or whatever `MAX(rowid)` is — if `COUNT(*)` is too slow, use `SELECT MAX(rowid)` and `MIN/MAX(effective_date)`). Then one `SwapsQuery` call from `swaps_query.py` against that env.

- Tests: existing `tests/test_swaps_query_regulator_index.py` stay on fixtures. Add a skip-gated live probe only if Jason wants it; default is the env-var smoke above, not CI.

### Phase B1 — Merge the GitHub-sized primary onto main

**Files:**
- Merge `wt/t_b82fb352` **gitignore exception only** + `split_swaps_db.py` (script). Do not force-add the 4.66 MB DB if Jason prefers it gitignored forever — **recommended:** un-ignore root `swaps.db` as local-agent did, keep `-wal/-shm/.lock` ignored, and `git add -f swaps.db` only if size stays <10 MB.
- Copy `split_swaps_db.py` from `.worktrees/t_b82fb352/` onto main if the branch merge is messy.

Confirm: `git check-ignore -v swaps.db` after merge.

### Phase B2 — Finish the incomplete cold archives (optional, history insurance)

Resume `split_swaps_db.py` Phase B only for missing chunks `2025-08` and `2025-09-onward`. Do not rebuild primary. Do not delete the OneDrive original.

Kill leftover python matching `split_swaps_db.py` first. Stage dir: `%TEMP%/swaps_split_stage/`. Log: `C:/Users/bottl/hermes-artifacts/split_run.log`.

This is CONDITIONAL: only if Jason still wants gzip cold copies. The live path does not need them.

### Phase B3 — Reader documentation, not a new query engine

Add `docs/SWAPS_DB_LAYOUT.md` (short):

| File | Role | Apps |
|---|---|---|
| `C:/Users/bottl/OneDrive/Stocks/Swaps/swaps.db` (323 GB) | Live query DB | `SWAPS_DB_PATH` |
| `FinancialDevelopment/swaps.db` (4.66 MB) | GitHub-sized recent window | clone default if env unset |
| `OneDrive/.../archive/*.db.gz` | Cold backup of pre-2026-01-01 | restore script only |

If env is unset on a fresh clone, apps see 941 rows and should log a warning once: `"SWAPS_DB_PATH unset; using repo-root recent-window DB (N rows)"`.

- Tests: a unit test that `db_loader.DB_PATH` follows env, already true — add the warning.

---

## Global constraints

- No new JS libraries (no GridStack, no React).
- No CDN on dashboard (already inline). Chart app already vendors ECharts.
- Do not revive `orchestrator.run_unified` / `--suite` to fill Overview.
- Do not call `vrp_term_structure` as a widget (selection-only marker).
- Options default stays Leisen-Reimer.
- Surfaces stay on SPXW, not SPX.
- Never `COUNT(*)` the 323 GB `swap_trades` in CI. Live probe is opt-in.
- Never write the 323 GB file into git.
- One dashboard process at a time (`CLAUDE.md`).
- Commit subjects: `feat|fix|test|docs|refactor|chore` via `scripts/hooks/commit-msg`.
- Clean pytest: `env -u PYTHONPATH -u VIRTUAL_ENV .venv/Scripts/python.exe -m pytest <slice>`.

---

## Scope taxonomy

**SELECTED**
- A0 chart full-bleed + ECharts resize/query-string
- A1 overlay checkboxes
- A2 cache-backed positions/signals/position_analysis/surfaces ModuleSpecs + legacy GET on mount
- A3 Inject as context
- A4 Overview SPY hero + IV Rank / VRP / Skew / GEX-or-signals cards
- A5 Chart sidebar add/remove (cap 4)
- A6 Quant Console 2-col grid
- B0 `SWAPS_DB_PATH` → OneDrive original
- B1 merge gitignore + split script
- B3 layout doc + unset-env warning

**CONDITIONAL**
- Second Overview chart (SPY 1d) after A0+A4 are visually stable
- B2 finish gzip 2025-08 / 2025-09-onward
- Extract shared `layout-chrome.js` if Chart + Overview add-widget JS duplicates >40 lines
- GEX card on Overview if `gex` ModuleSpec is too heavy — fall back to signals score

**PRESERVED**
- Dealer Book tab
- `syncBus` shape `{ticker, expiry, basket}`
- `POST /api/widgets/positions` agent-push contract
- Unscoped `widget_cache` table (adapters read it)
- `chart_app` on :8791 as the only live tape
- OneDrive original `swaps.db` (read-only from this plan)
- Direction indicator math (`Direction/indicator.py`)
- `vrp_term_structure` selection-only marker

**EXCLUDED**
- GridStack / freeform resize
- TradingView indicator port
- Auto-run of sibling widgets on inject
- ATTACH-union query engine over `.db.gz`
- Copying 323 GB into the git repo
- Reviving orchestrator run-history as a live KPI
- Auth on dashboard endpoints

---

## Risks / pitfalls

1. **Stale uvicorn on :8787.** Template changes show, Python does not. Kill LISTENING pid with `/T`, then relaunch (`quant-chart-delivery` stale-server-recovery).
2. **Iframe + ECharts zero-size** is the whole Chart bug. If A0 is skipped, A4's Overview embed will look identical to screenshot 2.
3. **`COUNT(*)` on OneDrive 323 GB** will look like a hang. Use `MAX(rowid)` / indexed date range.
4. **OneDrive WAL.** Live scheduler and dashboard must not double-open for write. B0 is env-only; do not start a second `run_scheduler`.
5. **coder1 "verified apps work"** was against an empty DB — do not treat `t_2d1118d3` as evidence the 71M-row book is wired.
6. **Overview layout fetch wiping hardcoded widgets** (`index.html:80-93`). A4 must stop treating a non-empty layout as "replace the whole page."
7. **`iv_rank` hits ThetaData.** Hero cards should `GET /state` first and only Run on button, same as other widgets — do not auto-fire four scanners on every Overview load.

---

## Open questions (non-blocking; defaults above)

1. Fourth Overview card: GEX vs signals score? **Default: signals score/VRP pair already in cache; skip live GEX.**
2. Commit the 4.66 MB `swaps.db` to git after un-ignoring, or keep gitignored and only document it? **Default: un-ignore + commit if still <10 MB.**
3. Finish gzip archives (B2)? **Default: skip until Jason asks; live path does not need them.**

---

## Implementation order

1. A0 (Chart stretch + blank fix) — Jason sees this immediately
2. B0 (SWAPS_DB_PATH) — independent, do in parallel
3. A1 overlays
4. A2 cache widgets (positions starts working)
5. A3 inject
6. A4 Overview hero
7. A5 Chart sidebar add
8. A6 2-col console
9. B1 merge split artifacts
10. B3 docs

---

## Acceptance (definition of done)

**Chart tab**
- Chart canvas fills the area between the topbar and the right sidebar (no 300px strip, no 1500px gutters).
- Changing ticker or interval paints candles without leaving the tab.
- Overlay checkboxes hide/show ema/vwap/bb/whale and survive reload.
- Sidebar can add ≤4 widgets and persist across refresh.

**Overview**
- SPY 15m chart is running in the center without visiting Chart.
- IV Rank and VRP cards show numbers or a clear idle/error, not "unknown widget."
- Positions table shows the last pushed book or "no positions pushed yet" — never a 404 Run failure.
- Inject as context updates Quant Console provenance + sibling basket inputs.

**Swaps**
- With dashboard/scheduler env set, `swap_trades` is the full OneDrive book, not 941 rows.
- Repo clone without env still opens, warns, and sees the recent-window DB.
- `git` does not contain a 323 GB blob.
