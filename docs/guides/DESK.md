# The Desk — how the dashboard works now

> The page at `http://127.0.0.1:8787/`. Your book at the top, one scope bar fed
> by it, tool cards below that inherit that scope. Replaces the old
> Overview + Quant Console split (2026-09-10).

**Scope narrowed 2026-09-11.** The desk is now specifically *the tools you
reach for after the big suites have run* — hedge optimizer, sims, VaR,
screeners, options tools — plus the book and the status panels. The analysis
that used to be a dozen identical cards here moved to three dedicated tabs:

| Moved to | What went |
|---|---|
| **Volatility** (`/volatility`) | GARCH, jump models, variance swap, VRP, SVI, all four surfaces, correlation/covariance, portfolio surface |
| **Models** (`/models`) | Every Options_Suite pricer, model comparison, smile compare |
| **Sentiment** (`/sentiment`) | The seven scanners, focus tickers, and the scan-note shelf |

See **`docs/guides/TABS.md`** for those. The scope bar is now shared across all
of them — set a ticker here and it follows you. Everything below still
describes the desk itself.

---

## 1. The rule

**Scope is entered once, and normally not typed at all.** Ticker, expiry and
basket live in one bar at the top of the page, and the usual way to set them is
to click a position in your book. Every tool card below follows that bar and has
no ticker/expiry/basket fields of its own.

Before this, all twelve cards carried their own copy of those three fields, and
on the four cache-backed cards they were inert — the module's `run()` ignored
context entirely, so typing a ticker there did nothing at all.

---

## 2. Using it

**Launch:** `dashboard.bat` (Windows) — serves the desk on `:8787` and starts
the native chart app on `:8791`. Only ever run one at a time.

**Set scope.** Three ways, in order of how often you'll want them:

| You want | Do this |
|---|---|
| Focus one position | Click its row in **Your book** — sets ticker (and expiry when the payload carries one) |
| Work across everything you hold | **Basket = my book** — sets the basket to every held ticker |
| Something you don't hold | Type into the scope bar's ticker/expiry directly |

Basket chips are clickable: click one to focus that ticker, click its `×` to
drop it.

**Add a tool.** `Tools → + add a tool…`, grouped by suite. A tool that pulls in
a dependency says so in the dropdown — e.g. *Dealer Flow (pulls in
expiry_exposure)*. Cards persist across reloads (saved under the `desk` layout
key), and `Run all` runs every card on the rail against the current scope.

**Read what a run was fed.** Each card prints a muted line under its controls:

```
ran: expiry_exposure → dealer_flow   ·   fed: positions, held_tickers, iv_rank_result
```

`ran` is what the click actually executed (a `requires` dependency runs too).
`fed` is which stored context keys the run was seeded with. If a card looks
wrong, that line is the first thing to check — it tells you whether it saw your
book.

**Module options.** Some cards carry their own controls, separate from scope:

- **GARCH(1,1)** — *Filter jump days before fitting* (winsorizes
  jump-attributable days so the fit reflects diffusive clustering only),
  *Jump filter model* (Merton / Kou / VarianceGamma — only models with a
  constant diffusive sigma qualify), *Adjust forecast by jump share* (scales the
  forecast by Bates' option-implied jump-variance share).
- **Variance Swap** — *Enhance with jump model*. Fits the chosen model to the
  same chain alongside the replication. It does **not** change the replication
  fair strike; with Bates it additionally splits the fair variance into jump and
  diffusive legs.
- **Highlight Packs** — group / priority / max packs.

**Status panels** (Signals, Position Analysis, Surfaces) are background-fed.
They have a *Refresh* rather than a *Run*, and no scope inputs, because their
`run()` is a cache read — it ignores scope by design.

---

## 3. Getting your book in

The desk reads positions from a cache row an agent pushes:

```
POST /api/widgets/positions   {"positions": [...], "accounts": [...]}
```

The **as of** badge next to *Your book* turns amber past six hours — a trading
session — so a stale book can't quietly look current. If it says `as of 13d
ago`, nothing has pushed since then.

Two known gaps in what gets pushed today:

- Option rows carry no `strike` / `expiry` / `right`, so they can't be resolved
  to a contract and clicking one sets the ticker only. The page says so under
  the table rather than hiding it. Add those fields to the pusher for
  contract-level scope.
- Some rows have no `current_price`, so they show `--` and sit out of the
  market-value total (counted separately as *Unpriced legs*).

**Highlight Packs** is the other basket source: it reads the scanner's
`latest_manifest.json` and publishes the packs' tickers as the basket, so "the
scan found these names" and "run the vol tools over them" is one step.

---

## 4. The chart tab

The sidebar is fixed context, not a widget catalog: your position in whatever
symbol is charted (the chart app announces it over `postMessage`), plus the
background-fed status panels. Analysis tools live on the desk, on one scope.

Indicators are on the chart itself, top-left. Price-pane overlays: `ema20`,
`ema50`, `ema200`, `vwap`, `vwap±σ`, `bb`, `atr ch`, `pd h/l`, `whale`.
Own-pane oscillators: `rsi`, `cci`, `macd`. Panes are computed from whatever is
enabled, so turning on two oscillators gives you two panes.

All eight timeframes load (`3m` … `1d`). If one fails, the stamp line says why
and tells you to press Load again — a coarse interval is aggregated from a lot
of one-minute rows and the provider can time out under load. The refresh retries
transient timeouts twice with backoff before reporting.

**Direction legs and timeframe.** The Direction group (`whale / wave3 / squeeze
/ trend / liquidity`) is *bar-count* based — MA20, MA50, Bollinger(20), ADX —
and needs 50 bars before it computes anything. That is the standard convention,
but it means a coarse timeframe can be short: 4h over 30 days is about 52 bars,
right at the edge. When there aren't enough, the legs strike through and the
stamp says `direction inactive (N/50 bars)`, so "not enough history" no longer
looks identical to "nothing fired".

---

## 5. What changed underneath (and why a widget works now)

Worth knowing, because it explains a whole class of past symptoms.

**The Context Store was write-only.** `POST /api/widgets/{slug}/run` wrote each
module's `context_patch` in; the only reader in the dashboard was `describe()`,
which feeds the provenance list — key names, never values. Widgets deposited
data that nothing ever read. The run route now seeds each run from the store
first (`_seed_context_from_store`, broad-to-narrow: `ticker:GLOBAL` → basket →
ticker → ticker+expiry, with an explicit request value always winning).

**`requires` was never expanded.** The route called `spec.run(context)`
directly, so `dealer_flow` returned `status: skipped` forever with a message
explaining that its caller had failed to expand requires — the dashboard *was*
that caller. It now goes through `shared.module_execution.run_selected_modules`,
the same entry point the orchestrator CLI uses, which also mints `run_id` /
`output_dir` (chart-producing modules had been dropping PNGs in the repo root).

**Scanners rendered empty.** Six of the seven put their whole payload in
`context_patch` and left `metrics={"ticker": "SPY"}` — and the card renders
`metrics`. They were working; the UI was rendering the empty half.

**Two silent-correctness bugs, both worth knowing about:**

- *Options_Suite priced everything at a flat 25% vol.* CRR / Leisen-Reimer /
  BAW / MC each did `sigma = float(context.get("sigma") or 0.25)`. From a card,
  which never supplies sigma, that meant every price was the price of a
  different option — SPY quoted at **$41.66** where the real solved IV (14.33%)
  gives **$25.52**. They now resolve sigma through `VolManager` the way
  `main.py` always has, and report where it came from in `sigma_source`.
- *A print statement was killing the IV solve.* The root cause of the above:
  `VolManager.get_sigma` prints a banner containing `U+2500` (a box-drawing
  character), which raises `UnicodeEncodeError` on a cp1252 console — mid
  computation. `run_selected_modules` now makes stdout/stderr lossy up front, so
  no module can lose a result to a decoration.

If you ever see a plausible-looking number you don't trust, check the
provenance fields first: `sigma_source` on a pricer, the `fed:` line on the
card, and `data_quality` where a module reports one.

---

## 6. Where things live

| Thing | Path |
|---|---|
| Desk page | `dashboard/templates/index.html` |
| Chart tab | `dashboard/templates/chart.html`, chart app in `chart_app/` |
| Widget element / renderers / scope bus | `dashboard/static/js/` |
| Run route, context seeding | `dashboard/app.py` (`run_widget`, `_seed_context_from_store`) |
| Dependency expansion, console guard | `shared/module_execution.py` |
| `ModuleSpec` / `ParamSpec` contract | `shared/module_registry.py` |
| Per-suite module lists | `<Suite>/module_registry.py` |
| Cache-backed status panels | `dashboard/cache_widgets.py` |

**Adding a module:** implement `run(context) -> ModuleResult`, wrap it in a
`ModuleSpec` in your suite's `module_registry.py`. Put the human-readable
payload in `metrics` **and** anything downstream needs in `context_patch` —
they are two audiences, not two alternatives. Declare `requires` for anything
you depend on rather than re-fetching it. Add `ParamSpec`s for module-specific
knobs and the card renders them automatically. Set `runnable=False` if the slug
exists only to be *selected* as part of a larger pipeline.

**Three of the four items that used to be listed here as open are done
(2026-09-11):**

- *The basket-dependent VaR modules ignored `held_tickers`.* Fixed —
  `_extract_tickers` now reads `basket`/`held_tickers`, position sizes come
  from the book's market values instead of `np.ones(n)`, and every module
  reports `vol_source` / `corr_source` / `position_source` so a fallback is
  visible instead of looking like a measurement.
- *VRP/SVI needed tying to `variance_swap`.* All three are panels on the
  Volatility tab now, on one scope and one tenor, and VRP routes through the
  runnable `Tools/` entry point rather than Vol_Suite's `runnable=False`
  marker.
- *The Output tab knew nothing about widget runs.* It is gone from the nav,
  replaced by Volatility. (`/suites/*` still resolves for direct links.)

Still open: the position pusher does not send `strike`/`expiry`/`right` on
option rows, so clicking an option leg sets the ticker only.
