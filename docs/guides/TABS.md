# The dashboard tabs — what lives where, and why

> `http://127.0.0.1:8787`. Six tabs, one scope. Added 2026-09-11: **Volatility**,
> **Models** and **Sentiment**, replacing the Suite-output tab.

---

## 1. The split

| Tab | Question it answers | What's on it |
|---|---|---|
| **Desk** | *What do I hold, and what do I do about it?* | The book, status panels, and the tools you reach for **after** the suites have run — hedge optimizer, sims, VaR, screeners, options tools |
| **Chart** | *What is price doing?* | The native chart app, indicators, your position in the charted symbol |
| **Volatility** | *What is vol doing?* | GARCH, jump models, variance swap, VRP, SVI, all four surfaces, correlation/covariance, portfolio surface |
| **Models** | *What is this contract worth, and where is the chain wrong?* | Every Options_Suite pricer, model comparison, smile compare, strategy builder |
| **Dealer Book** | *Whose position is this?* | Two independent dealer-exposure estimates, A vs B per greek |
| **Sentiment** | *What are people saying, and is the chain paying for it?* | The scan shelf, plus the seven scanners + VRP + focus tickers + your book |

The rule that makes this work: **scope is entered once**. Ticker, expiry and
basket live in one bar at the top of each tab, and that bar is now *persisted
across tabs* — set SPY on Volatility, switch to Models, it's still SPY. Panels
never carry their own ticker fields.

---

## 2. Why these are pages and not more desk cards

The desk's tool rail is a **catalog**: any of 57 modules, one card each. Right
for "I'm going to use the hedge optimizer now".

These tabs are a **workbench**: a fixed set of panels that belong together, on
one scope, checked on or off, run as a batch. Right for "show me everything
this ticker's vol is doing".

But the real reason is that several of the most-wanted modules **could not
render on a generic card at all**, which is why they looked broken:

| What looked broken | What was actually wrong |
|---|---|
| All four **surfaces** showed five scalars | `surface_*` modules return `artifacts=[]` — the grid rides on `context_patch` and nothing ever drew it. The panel asks `Tools/tools/surface_explorer_tool.py` for the PNG, which already knew how to render these exact grids. |
| **VRP** never ran | `Vol_Suite`'s `vrp_term_structure` is `runnable=False` by design — a gated step inside `_run_core_analysis` whose `run()` raises. The runnable VRP is the `Tools/` entry point. The panel calls that one. |
| **Variance swap** and **GARCH** charts were blank | They *do* write PNGs — into `outputs/<run_id>/`. `GET /files` only served `artifacts/` and returned **403** for everything else. A broken `<img>`, no error anywhere. Fixed: `FILE_SERVE_ROOTS` in `dashboard/app.py`. |
| **Jump diffusion** had no chart | It genuinely is metrics-only. It now sits in a group with the model zoo, where a table is the right output. |

A panel is a server-side spec that returns the **same ModuleResult shape** the
widget API returns, so the browser reuses `widget-renderers.js::renderResult`
unchanged. No quant logic lives in `dashboard/panels.py` — every panel
delegates to `run_selected_modules` or a `Tools/tools/*` entry point.

---

## 3. Running panels

Each panel card has a **checkbox** (include in a batch) and its own **Run**.
The controls bar has *Run selected* / *Run all* / *Select all* / *Select none*.

**Runs are sequential, with a 400ms gap, on purpose.** Every panel is a real
billed ThetaData pull, and CLAUDE.md's rule is explicit: sleep 0.3–0.5s, never
fan out. `Run all` on eleven panels in parallel gets the whole batch throttled
and every card fails at once.

Only a few panels are checked by default, for the same reason: opening a tab
should not fire eleven billed pulls.

Each card prints the same provenance line the desk cards do:

```
ran: expiry_exposure → dealer_flow   ·   fed: positions, held_tickers, garch_conditional_vol
```

`ran` is what executed (a `requires` dependency runs too). `fed` is which
stored context keys the run was seeded with. **If a number looks wrong, read
that line first.**

---

## 4. How context moves between tabs

Two separate mechanisms, often confused:

- **Scope** (which ticker you're looking at) rides in `localStorage` via
  `sync-bus.js`. Per-viewer convenience only.
- **Results** ride in the **Context Store** (`shared/context_store.py`,
  server-side, scope-keyed). This is what makes a Models panel see the
  Volatility tab's GARCH fit instead of recomputing it.

Every panel run calls `_seed_context_from_store` first, broad to narrow
(`ticker:GLOBAL` → basket → ticker → ticker+expiry), with an explicit request
value always winning. The **Available context** strip at the top of each tab
shows what is actually stored for the current scope.

The chain that now closes, which did not before:

```
Volatility · GARCH          → garch_conditional_vol ─┐
Volatility · Correlation    → correlation_matrix     ├→ Desk · VaR tools
                              covariance_matrix      │   (corr_sim, mc_sim,
                              volatilities           │    copulas, forex_var,
                              correlation_tickers  ──┘    hedge_optimizer)
Volatility · Jump Diffusion → jump_diffusion        ──→ Models · Heston MC
```

And the result flows back out — every sim publishes a `risk_outlook` summary
(source module, tickers, VaR/CVaR, horizon, confidence, and a `measured` flag
that is false when any input was a fallback), so "the simulated outlook for
returns" is readable by the next tool instead of dying on the card. Scalars
only: the raw paths never enter the store, because every later run on that
scope is seeded from it.

**Two links in that chain were broken and are now fixed:**

- `garch` wrote `garch_vol`; VaR reads `garch_conditional_vol`. The names never
  met, so every VaR run took the flat-0.25 fallback no matter how many times
  GARCH had run. It now writes both.
- **Nothing wrote a correlation matrix at all.** VaR's `_resolve_corr` always
  fell back to identity — every basket VaR on the desk was a zero-correlation
  simulation presented as a risk number. The new `correlation_matrix` module is
  what fills it.

### Provenance, because a fallback looks like a measurement

Every VaR module now reports where its inputs came from:

```
vol_source: context_store:volatilities      corr_source: fallback:identity
position_source: book:market_value
```

A `fallback:` prefix means **that number is not a measurement**. Same
convention as Options_Suite's `sigma_source`.

Stored vectors and matrices are re-indexed onto the requested basket **by
label** (`correlation_tickers`), never applied positionally — a matrix stored
for `[QQQ, SPY]` applied to `[SPY, QQQ]` is wrong with no error at all. A
stored value that does not cover every requested name is refused, not
silently padded.

---

## 5. Tab specifics

### Volatility

Tenor is a **scope** field here, not a per-panel knob: the variance swap, the
jump calibration and VRP must be talking about the same horizon or their
numbers aren't comparable.

- **Correlation / Covariance** is basket-scoped — press *Basket = my book*
  first. Weights come from your book's market values when the book is in
  context, not equal-weight.
- **Portfolio IV Surface** blends every holding's IV surface by market value,
  in moneyness space. **This is not portfolio volatility** — averaging vols
  ignores correlation. It's the vol surface your book is exposed to. Portfolio
  vol is on the correlation panel, which has the covariance to compute it.
  Interpolation never extrapolates: a name whose chain doesn't reach 0.80
  moneyness drops out of that cell rather than padding it with a quote that
  was never made.

### Models

The scope bar carries **strike**, **right** and **tenor** — "which contract" is
what a pricer is asked about. Leave strike blank to price ATM (snapped to a
listed strike).

**Default pricer is Leisen-Reimer**, and the picker defends that: a regression
to plain CRR for the default path is a bug that has been reported more than
once. The picker marks the default card and keeps it checked; it never rewires
what another pricer computes.

If a pricer's `sigma_source` reads `fallback:0.25`, the number is not a market
price — read the reason it carries.

### Sentiment

Two halves, deliberately paired: a rumor with no options premium behind it is
noise, and unusual OI with no story attached is unexplained.

**The shelf** reads markdown off disk — the Obsidian vault's `Trading/` folder
plus the repo's `trading_journal/` — and buckets notes onto shelves by
filename: `x/twitter`, `rumors`, `reddit`, `morning scans`, `sentiment`,
`desk notes`, `research`, `other`. Click a note to read it; tick *only the
scope ticker* to filter to what mentions what you're looking at.

Notes are rendered with a small escape-first markdown renderer — no library
(this dashboard runs offline, no CDN) and no trusting file content as HTML.
`GET /api/library/note` containment-checks the path against the library roots,
the same rule `GET /files` applies to artifacts; without it the endpoint would
read any file on the machine.

---

## 6. The desk's surfaces panel

It used to be hardcoded to `SPXW` — the one status card on the desk that could
never be about anything you hold. It now models the **largest single position
in your book by absolute market value**, and reports both the reason it chose
that name and the portfolio weights it chose from, so the choice is visible
rather than implied. Falls back to `SPXW` when the book is empty or unpriced.
`WIDGET_SURFACES_TICKER=QQQ` pins it.

---

## 7. Gotchas worth knowing

**Per-run output directories.** Each panel run gets a fresh
`artifacts/panels/<tab>/<panel>_<stamp>_<hex>/`. Not cosmetic: several suite
entry points collect output by **listing** `output_dir` and matching a filename
prefix — `garch_analysis.run_garch_module` globs `{ticker}_garch_*.png`. Point
two runs at one directory and the second returns the first's charts too.
Confirmed live on SPY: a second GARCH run came back with six artifacts, three
from a run eleven seconds earlier.

**The Suite-output tab is gone from the nav, not deleted.** `/suites/*` still
resolves, so a direct link to an output file doesn't 404. It's simply no
longer a destination.

**The dashboard does not auto-reload.** `dashboard.bat` runs uvicorn without
`--reload`, so a running instance serves the code it started with. After
changing dashboard code, restart it — a test passing proves the code is right,
not that the live process has it.

---

## 8. Where things live

| Thing | Path |
|---|---|
| Panel registry (specs + runners) | `dashboard/panels.py` |
| Panel API | `dashboard/app.py` (`panels_catalog`, `run_panel`, `library_index`, `library_note`) |
| Shared tab engine (scope bar, cards, sequential runner) | `dashboard/static/js/panel-tab.js` |
| Shared tab styling | `dashboard/templates/_panel_tab.html` |
| Pages | `dashboard/templates/{volatility,models,sentiment}.html` |
| Artifact serving + roots | `dashboard/app.py` (`FILE_SERVE_ROOTS`, `files`) |
| Cross-tab scope persistence | `dashboard/static/js/sync-bus.js` |
| Correlation/covariance module | `Vol_Suite/module_registry.py` (`_run_correlation_matrix`) |
| VaR context resolvers + provenance | `VaR_Tools_Simulations/module_registry.py` |
| Tests | `dashboard/tests/test_panel_tabs.py`, `Vol_Suite/tests/test_module_registry_vol_context.py`, `VaR_Tools_Simulations/tests/test_context_pull_from_vol.py` |

**Adding a panel:** write a runner returning
`{status, artifacts, metrics, ran}`, wrap it in a `PanelSpec` in
`dashboard/panels.py`, give it a `tab` and a `group`. The page picks it up
automatically. If it wraps a registered module, use `module_panel(slug)` —
never call `spec.run()` directly, and never wrap a `runnable=False` slug
(a test enforces both).
