# FinancialDevelopment whole-repo audit

**Author:** Grok 4.6 (this session)
**Date:** 2026-09-11
**Repo:** `C:\Users\bottl\FinancialDevelopment`
**Branch:** `master`
**HEAD:** `c1ba63e23f19a45d2765f36c230d885ff4adb876`
**Working tree at session start:** dirty — VaR copulas / hist_sim / hedge_optimizer / data_loader + tests; untracked `NOK_*` scan dump, `chain_strategies.json`, `artifact-boards/`
**Audience:** Jason, then Claude, then CARL (math / claim check)

This is an inventory + hygiene + documentation + architecture brief. It is **not** a modernization estimate, a test run, or a prune execution. Nothing in this document was deleted or rewritten except this file.

---

## CARL packet

```yaml
mode: SPEC
subject: docs/audits/2026-09-11-repo-audit-grok.md
author_family: grok
artifact_identity: HEAD c1ba63e + this file dated 2026-09-11
scope:
  in:
    - every first-level folder and notable root file
    - docs vs live code
    - prune candidates with evidence
    - architecture / feature / delivery advice
  out:
    - implementing prune
    - rewriting wiki (this audit only marks it)
    - live ThetaData / dashboard verification
    - git-history rewrite of trading_journal/
locked_decisions:
  - localhost-only dashboard, no auth
  - widget-native in-process runs (Phase 7, 2026-09-04); --unified / --suite removed
  - Leisen-Reimer is the default American pricer
  - SPX chain root SPXW, price root SPX
  - failure is loud; fallback: prefix means the number is not a measurement
  - three suites share root .venv; sentiment-scanner has its own
acceptance_gates:
  - every prune item has a caller-search or gitignore evidence line
  - wiki-needed items name the operator question they fail to answer
  - stale-doc claims cite the live replacement
  - unverified items are labeled unverified, not asserted
```

**How this was built.** Parallel explore agents on (a) satellites, (b) root hanging files; parent read of CLAUDE.md, wiki 06/07/04/02/12, DESK.md, TABS.md, START_HERE.md, FINANCIAL_DEVELOPMENT_INVENTORY.md (2026-09-04), PROJECT_AUDIT_AND_SPEC.md (2026-08-04), registries, gitignore, and the live trees. Shell `git ls-files` / `cloc` failed with Access Denied — **file-count and git-tracked claims below are working-tree observations, not `git ls-files` proof.** Confirm tracking with `git ls-files -- <path>` and `git check-ignore -v` before any delete.

**Prior audits (do not treat as current):**
- `docs/PROJECT_AUDIT_AND_SPEC.md` — 2026-08-04, still useful for math critique of dealer method, stale on Options context-mode stub and `--unified`
- `docs/guides/FINANCIAL_DEVELOPMENT_INVENTORY.md` — 2026-09-04, good map, already drifting (root `test_*.py` “orphans” actually live under `tests/` now; `--demo` already fixed in VaR README)

---

## 1. Executive summary

This is a single-operator, localhost quant desk: DTCC swaps in SQLite, four analysis suites, a FastAPI desk at `:8787`, a native chart at `:8791`, and a swap browser at `:8788`. The **live desk run path** is `POST /api/widgets/{slug}/run` → `shared.module_execution.run_selected_modules` → Context Store. Root `orchestrator.py --unified` / `run_suite` were removed 2026-09-04. **Exception:** `Vol_Suite/volatility_suite.py::run_unified_flow` (interactive mode 2) still subprocesses Options_Suite and VaR if you say yes to those prompts (`volatility_suite.py:1977-2038`). Phase-7 “no subprocess launcher” is true of the *root orchestrator*, not of every file named unified.

The repo is **over-documented in volume and under-documented where an operator would act**. The wiki still teaches `orchestrator.bat --unified` and subprocess suites. `dashboard/README.md` still teaches `POST /run/unified` on port 8000. `CLAUDE.md`, `docs/guides/DESK.md`, `TABS.md`, and `FINANCIALDEVELOPMENT_ANALYSIS_PIPELINE_RUNBOOK.md` are the current truth.

The highest-leverage work is **not a new feature**. It is: (1) make the wiki match Phase 7, (2) put dashboard/Backtests/swaps_dashboard tests on the default pytest path, (3) prune dated scratch that gitignore already intended to hide but that still sits on disk and, in several cases, is still commitable.

**Headline recommendation:** freeze new product surface until the wiki, the dashboard README, and pytest `testpaths` describe the desk that actually exists. Then prune Tier 1. Then pick one IV-watchdog and one backtest story.

---

## 2. What the system is (today)

```
DTCC API ── dtcc_api_client / dtcc_parser / db_loader ──> swaps.db
                 ▲
   scheduled_ingest (5 min) / backfill / split_swaps_db

dashboard :8787  ── widgets ── shared.module_execution ── suite module_registry.py
     │                         context_patch → Context Store
     ├── Chart tab iframe → chart_app :8791 (auto-started by dashboard.bat)
     └── Swap card snapshot ← swaps_dashboard :8788 (manual start)

TradingView Desktop ── tradingview-mcp (sidecar MCP, not the Chart tab)
```

**Not the desk pipeline anymore:** `orchestrator.py --unified`, `run_suite`, dashboard `POST /run/{suite}`. `orchestrator.py` still owns `build_context`, `run_market_signals_stage`, `log_run`, adapter discovery, and the `--modules` CLI. `orchestrator.bat` / `.sh` do not exist. Vol_Suite interactive mode 2 is the remaining cross-suite subprocess.

---

## 3. Folder-by-folder inventory

Legend: **Keep** = live. **Wiki** = needs a current page (missing or stale). **Prune** = hanging. Confidence in brackets.

### 3.1 Spine (keep)

| Path | What it is today | Wiki | Prune |
|---|---|---|---|
| `orchestrator.py` | Module CLI + `build_context` + market-signals + `log_run`. Not a suite launcher. | **Update wiki 06/07/04** — they still say `--unified` | No |
| `dtcc_api_client.py`, `dtcc_parser.py`, `db_loader.py` | DTCC ingest | Guides exist (`DTCC_LOCAL_SETUP.md`) | No |
| `backfill.py`, `scheduled_ingest.py`, `poll_ingest.py` | History + 5-min poll | Guides exist | No |
| `swaps_query.py`, `setup_db.py`, `shutdown_signal.py` | Query API, migrations, SIGINT | OK | No |
| `upi_decoder.py`, `decode_upis.py` | UPI name decode (OpenFIGI fallback). Distinct from `shared/identifiers.py` | **Wiki gap:** two “identifier” systems, easy to confuse | No |
| `split_swaps_db.py` | One-shot shard of the huge OneDrive book | Documented in `SWAPS_DB_LAYOUT.md` | No |
| `migrations/` | SQLite schema, trust this over `SWAPS_DATABASE_SPEC.md` | Spec is PostgreSQL — banner it | No |
| `adapters/` | DTCC live; **CME and OTC are stubs** (`TODO: Initialize CME API client`) | Cross-source guides imply they work. **Wiki: “stubs, do not enable”** | Do not delete stubs; they are the extension contract |
| `shared/` | ThetaData, schemas, module execution, Context Store, pool, cache | **Wiki gap: Context Store key contract** (the silent-breakage class of 2026-09-11) | Module docstring at top of `shared/module_registry.py` still says suite `MODULES` lists are empty — **stale comment, not empty lists** |
| `tests/` | Cross-suite pytest (in `testpaths`) | OK | Leftover `test_quant_bridge*.pyc` if sources are gone |
| `pyproject.toml`, `requirements.txt`, `.env.example` | One venv | OK | No |
| `CLAUDE.md`, `AGENTS.md` | Canonical agent context | Keep as SSOT | No |

### 3.2 Four suites

#### `Vol_Suite/` — largest, live, overgrown

What it is: dealer book (live path = `expiry_book_production`, legacy `compute_dealer_positioning` is backtest/test), GARCH, variance-swap, VRP, surfaces, jump-diffusion, correlation matrix writer for VaR.

Entry points: `vol_suite.bat` → `volatility_suite.py`; widgets via `module_registry.py`.

Registered slugs (high): `expiry_exposure`, `dealer_flow`, `position_book`, `dual_book`, `chain_scanner`, `svi_smile`, `surface_greek`, `surface_market_iv`, `surface_flow_strike_time`, `surface_flow_strike_expiry`, `variance_swap`, `jump_diffusion`, `jump_model_comparison`, `garch`, `correlation_matrix`.

`runnable=False` markers (run() raises): `group_screener`, `vol_surface_2d`, `vrp_term_structure`, `sentiment_backtest`. VRP is actually rendered by `Tools/tools/vrp_term_structure_tool.py` on the Volatility tab. That split is load-bearing and **not in the wiki**.

**Wiki needed (high):**
- How to read the dual book (chain-as-it-sits vs flow-built). `docs/wiki/03` and `START_HERE.md` do the method; operator “which card, which sign, what not to fuse” belongs next to the Dealer Book tab.
- Live vs legacy dealer (`LIVE_expiry_book_20260820.md` / `20260821.md` are the contract; `DEALER_POSITIONING_V2_DESIGN.md` still says “not yet implemented” and points at `Monte-Carlo-American-Pricer-Greeks/` — **stale, dangerous**).
- Context Store writes: `garch_conditional_vol` **and** `garch_vol`, `correlation_matrix` + `correlation_tickers` (re-index by label).
- Jump-diffusion: failures persist `{status: error}`, never `None`.

**Do not prune as dead code:** `run_dual_pipeline_gate.py` plus `_v2`…`_v7` and expiry-tier CLIs — `Vol_Suite/tests/` has matching `test_dual_pipeline_gate_v*.py`. Research harnesses with tests.

**Prune / archive (high, on-disk research *data*):**
- `_causal_acquisition_20260815/` (~221 files), `_scratch_tier2/`, `_scratch_tier2b/`, `_bounded_*`, `_intraday_cache/` — **not fully gitignored** (only `_expiry_falsifier_cache/` is). Leak risk.
- `vs_output/`, dated `SPY_gamma_records_*.csv`, screenshots
- Duplicate extracted code under `docs/Dealer posistioning notes/_extracted/`

**Docs stale:** README still mentions yfinance fallback; CLAUDE.md says ThetaData-only for VaR and merged client. Confirm §7 of Vol README against `thetadata_client.py` before rewriting. [medium]

Tests: `Vol_Suite/tests/` is large and in `testpaths`. Good.

#### `Options_Suite/` — live pricers, three entry points

What it is: American pricing/Greeks/IV. Default path is Leisen-Reimer (`main.py` and widget `_resolve_sigma`).

Three entries: `main.py` (interactive + `--context`), `module_registry.py` (9 widgets), `chain_evaluation.py` + `reports.py` (multi-model PDF, not called by `main.py`).

**Wiki needed (high):**
- Default is LR, not CRR; IV solver now inverts LR too (2026-09-11). Wiki 02 already has the LR convention; it does **not** have IV identifiability (`_vol_is_identifiable`) — that is a money-path wiki page.
- `sigma_source`: if a card says `fallback:0.25`, it is not a market price.
- `chain_evaluation` vs widget `model_comparison` — two comparison paths.

**Prune (high):** ~40 `comparison_*.pdf` / `.csv` in the suite dir (gitignore already has the pattern — disk only). `cuda.txt`. `OptionChain_GME 15-Jan-21_20201218.csv`. `SABR Vol smile .xlsm`. `POTATOHEDGE_API_REFERENCE.md` duplicates `docs/phclient_v2/`. `PROJECT_ROADMAP.md` is a 2026-07-28 session log.

`impliedvol.py` **self-declares dead** (`optionmodels` not in requirements; no in-suite importer) — Tier 1 prune. `fetch_options.py` source is gone; only `__pycache__/fetch_options.cpython-310.pyc` remains. `.claude/skills/options-suite/SKILL.md` is **wrong** that `main.py` does not call `chain_evaluation` — menu choices 9/10 do (`main.py:813,861`).

#### `VaR_Tools_Simulations/` — live widgets, split brain on context CLI

What it is: Excel VaRtools port. Ten widget slugs: `hist_sim`, `mc_sim`, `corr_sim`, `copulas`, `forex_var`, `cashflow_map`, `stress_test`, `var_agg`, `hedge_optimizer`, `price_dist`.

**Confirmed still true:** `main.py::run_context_mode` only implements module 1 (`corr_sim`); any other `--module` writes `status: error`. Widgets run all ten via the registry. That is two products in one folder.

**Wiki needed (high):**
- How a VaR card gets vol/corr (Context Store → explicit context → `fallback:0.25` / identity). How to read `vol_source` / `corr_source` / `position_source`.
- Context CLI vs widget: do not tell an operator to `--context --module 2`.
- `IDEA.md` is a prompt to port the rest of `VaRtools Samples.xls` (efficient frontier, longer outlook). That is a **feature backlog**, not a wiki, but it should be one tracked page so it does not look like unfinished code.

**Docs stale (high):** README “Volatility Suite Integration (coming)” is **already done** via Context Store + `correlation_matrix` module. README still talks about a suite-local `.venv`. `--demo` is already corrected in the README body.

**Prune (high):** dated `var_components_20260828.py`, `var_components_euler_20260828.py`, `var_components_live_20260901.py`, `var_components_live_20260902.py`; `var_context_run_*.json` (gitignore has `VaR_Tools_Simulations/var_*_run_*.json`).

Tests: good on corr/copulas/hist/hedge/var_agg/context; no dedicated `test_forex_var.py` / `test_cashflow_map.py` / `test_stress_test.py` / `test_mc_sim.py` files — coverage is inside `test_module_registry.py` and `test_context_builders.py`. [medium gap]

Dirty tree at session start: copulas, hist_sim, hedge_optimizer, data_loader — treat those files as in-flight, not audit targets.

#### `sentiment-scanner/` — live scanners, own venv, default-skipped social loop

What it is: 7 option-flow scanners + narrative (StockTwits/Reddit/YouTube) + CME SDR. Widgets: `gex`, `unusual_oi`, `iv_rank`, `skew`, `max_pain`, `vol_dispersion`, `earnings`, `highlight_packs`. Social loop is not the default unified path; `run_market_signals_stage` replaced the subprocess.

**Wiki needed (high):**
- What a “sentiment” tab actually runs (scanners + shelf) vs what `sentiment.bat --no-loop` runs (social + YouTube).
- YouTube captions silently skip without POT server `:4416`.
- Own `.venv`; do not use root venv.
- `sentiment_live.py` on **:8099** is a third UI (stdlib HTTP launching `main.py` as a child), not the Sentiment tab.

**Prune (medium):** `outputs/` ~2157 PNGs (ignored). Dated export packs under `data/exports/.../[YYYYMMDD]/` ignored; keep `latest_manifest.json`.

`sentiment-scanner/scripts/iv_watchdog.py` is a **different algorithm** (IV percentile rank) from root `iv_watchdog.py` (ATM IV vs 20d ATM). CLAUDE.md’s identifiability warning applies to the scanner copy.

### 3.3 Desk and tools

| Path | Today | Wiki | Prune |
|---|---|---|---|
| `dashboard/` | FastAPI `:8787`. Desk + tabs. Widget run seeds Context Store. No auth. `GET /quant` and `/tools` 307 to `/`. | **dashboard/README.md is the most dangerous stale file after the wiki** — port 8000, `POST /run/unified`. Live docs: `DESK.md`, `TABS.md`. **`app.py` still has `/share/*` + `tunnel.py`** despite localhost-only rule | `templates/_retired/`; `dashboard_*.log`; `dashboard/outputs/`; **`quant_modules.py` is a stale second registry** (Options `runnable=False` because of a long-fixed stub) — imported only by its own test, not `app.py` |
| `swaps_dashboard/` | Swap browser `:8788`. Desk reads `cache/overview_snapshot.json`. **Not** started by `dashboard.bat` | **Wiki/ops gap:** “start this or the swap card is empty” | No |
| `Tools/` | 7 registered tools. 5 Direction wrappers exist as files and are **not** in `TOOLS` | Direction README still lists six slugs | Unregistered: `bollinger_tool.py`, `elliott_wave_tool.py`, `liquidity_map_tool.py`, `trend_engine_tool.py`, `whale_flow_tool.py`. Folded into `directional-engine`. `broker_book.py` is a library for `backtesting_tool`, not a registry entry — **keep** |
| `chart_app/` | Native chart `:8791`, auto-started | README + SKILL current enough | No |
| `Direction/` + `Direction.py` | Conviction engine. `Direction.py` is a 20-line shim | README slug list stale | Do not prune the package |
| `tui/` | Textual widget client | Header comments only; optional one-pager | No |
| `blackhole_investments/` | Claude Agent SDK REPL hitting widget API | Not in CLAUDE.md command table | No — sidecar, not a joke |

### 3.4 Satellites

| Path | Today | Wiki | Prune |
|---|---|---|---|
| `Backtests/` | Tournament H1–H4 CLI. **Not** the Vol dealer backtest, **not** `Tools/backtesting_tool` | **Wiki needed:** three backtest stacks | `Backtests/outputs/` ignored. **`Backtests/tests/` not in `testpaths`** |
| `tradingview-mcp/` | Live MCP for TradingView Desktop. `node_modules/` ~4640 files, gitignored | Distinct from Chart tab | Do not track `node_modules/` or `screenshots/` |
| `trading_journal/` | Gitignored local PII. Sentiment tab still lists it as a shelf. History purge **OPEN** (`HANDOFF_trading_journal_history_purge.md`) | “Local only; Obsidian is canonical” | Do not `git add`. History rewrite is a human decision |
| `_broker_control/` | Unsigned control corpus for chain-scan forward-return study | README is the spec | `compare_control_vs_model.py` labeled PRE-CORRECTION — do not use |
| `scripts/` | commit-msg hook, burst checkpoint, TV verify, archive_raw_json, quant_alert_check, render_direction_chart | Hook is in CLAUDE.md | `vol_watchdog.py` vs root IV watchdog — pick one [medium] |
| `k8s/`, `Dockerfile.*`, `docker-compose.yml`, `Procfile`, `system/*.service` | Vestigial deploy. k8s LoadBalancer:80 + no-auth desk = accident | `DEPLOY.md` / `DOCKER_SETUP.md` read as supported | Archive as a set if localhost-only is permanent |
| `sqlite-tools/` | Four `.exe`, **zero callers**, **not gitignored** | None | Prune or gitignore |
| `option_chains/` | Two GME CSVs, no callers, **not gitignored** | None | Delete |
| `rh_exports/` | RH probe + token path, no callers, **not gitignored** | None | Delete |
| `dtcc_data/` | Gitignored raw CSVs | None | Disk only |
| `scratch_dealer_pkg/` | Gitignored 2026-08-11 design dump | Superseded by Vol live docs | Disk archive |
| `artifact-boards/` | Empty Hermes board root + README (`tools/refresh.py` does not exist) | None | Keep stub or delete empty dir |
| `_trash/` | Staging, gitignored | None | Empty when decided |
| `docs/` | See §5 | — | Do not bulk-delete archive; banner stale guides |
| `.claude/skills/` | Launch/debug SSOT for agents | Keep | Stale skill lines should be patched when wiki is patched |
| `.cursor/skills/verify-financial-development/` | Cursor verify skill; mentions uvicorn `:8000` | Stale | Confirm then archive |

### 3.5 Generated trees (never commit; delete locally for disk)

Already gitignored: `artifacts/`, `outputs/`, `orchestrator_output/` (2737 files), `module_archive.db`, `Vol_Suite/outputs/`, `*_dealer_book_*.png`, `comparison_*.pdf`.

**Still commitable (gitignore gaps):** `NOK_*`, root `chain_strategies.json`, `_scan_params.txt`, `option_chains/`, `rh_exports/`, `sqlite-tools/`, `iv_watchdog_scan.py`, `iv_alert_scan.py`, `iv_crush_confirm.py`, `debug_identity.py`, `Vol_Suite/_scratch_*`, `_causal_*`, `_bounded_*`, `_intraday_cache/`.

### 3.6 Root files (compact)

**Keep:** `orchestrator.py`, DTCC spine, `Direction.py`, `split_swaps_db.py`, launchers (`dashboard`, `tools`, `run_scheduler`, `swaps_dashboard`, `chart_app`), `setup_bgutil_autostart.ps1`, `report_generator.py` (tested, unwired — keep until you decide the PDF product is dead), `HANDOFF_trading_journal_history_purge.md` until history is rewritten.

**Keep one IV watchdog, not three:** `iv_watchdog.py` (root ATM/20d), `_cron_iv_watchdog.py` / `_cron_iv_watchdog_run.py` (cron), `sentiment-scanner/scripts/iv_watchdog.py` (percentile), `scripts/vol_watchdog.py`. **Wiki + pick a canonical.** Dated `_cron_iv_watchdog_run_202608*.py` (14 files) → delete (already ignored).

**Delete (no callers / one-off):** `aggregate_unified.py` (scrapes removed `--unified` output), `debug_identity.py`, `iv_watchdog_scan.py`, `iv_alert_scan.py`, `iv_crush_confirm.py`, `_cron_envcheck.py`, `_dbg_ccl.py`, `_live_scan.py`, `scratch_iv_watch.py`, root `NOK_*`, root `chain_strategies.json`.

**Archive (historical, tests now elsewhere):** `live_dashboard.py` (Rich TUI; current TUI is `tui/quant_tui.py`), `load_test_pool.py`, `pool_integration_example.py`, `quick_pool_verification.py`, `verify_graceful_shutdown.py`, `sentiment_x_context_20260826.md`.

**Deploy set:** archive, do not run. `Procfile` itself warns Heroku wipes `swaps.db`.

`report_generator.generate_pdf_report`: only called from its own `__main__` and `tests/test_report_generator.py`. Vol_Suite PDF uses `vs_utils.compose_pdf_report`, not this module. `compile_pdf` in `suite_context` is a Vol-internal flag. **Unwired product, not dead code.** [high]

---

## 4. Modules that need a wiki (or a wiki rewrite)

Priority is “an operator or agent would do the wrong thing.”

### P0 — wiki is actively wrong

| Page | Wrong claim | Live replacement |
|---|---|---|
| `docs/wiki/06-architecture-and-module-map.md` | `orchestrator.py --unified` subprocess graph | Widget-native; `run_selected_modules` |
| `docs/wiki/07-workflows.md` | Morning scan = `orchestrator.bat --unified`; dealer-book posts `/run/unified` | Desk + Volatility/Dealer Book tabs; `POST /api/widgets/{slug}/run` |
| `docs/wiki/04-the-engine.md` | Vol subprocess, Options/VaR parallel subprocesses as *the* pipeline | Desk path is in-process modules + Context Store. **Exception:** Vol interactive mode 2 (`run_unified_flow`) still subprocesses Options/VaR |
| `docs/wiki/10-extending-and-debugging.md` | “No change to `run_unified`” | Add a `ModuleSpec` |
| `dashboard/README.md` | Port 8000, `POST /run/unified` | `:8787`, widget API, `DESK.md` |
| `Vol_Suite/DEALER_POSITIONING_V2_DESIGN.md` | “not yet implemented” | Live expiry book is the production path |
| `.claude/skills/options-suite/SKILL.md` | `main.py` does not call `chain_evaluation` | Menu 9/10 does |
| `.claude/skills/tool-launcher/SKILL.md` | VaR widgets fail like CLI context mode | Widgets run all ten engines |
| `Tools/README.md` | `--unified` context ticket; Direction tools “shipped” | Widget + Context Store; only `directional-engine` is registered |

### P1 — missing pages (method exists in CLAUDE.md / skills, not in the human wiki)

| Topic | Why a wiki page |
|---|---|
| **Context Store** | Silent wrong VaR if key names drift (`garch_vol` vs `garch_conditional_vol`; missing `correlation_matrix`). Fallback numbers look real. |
| **How to run a morning book on the desk** | Scope bar, which tab, which panels, 400ms sequential batch, billed ThetaData |
| **How to read a widget card** | `status`, `fed:`, `vol_source`/`sigma_source`/`corr_source`, `fallback:` prefix, artifacts vs `context_patch` grids |
| **Dual book / live expiry book** | Sign conventions, charm not extra −1, ITM IV parity fill, GEX ≠ book gamma |
| **IV identifiability** | `converged=True` can be a flat-price lie; watchdog percentile hides it |
| **SPX vs SPXW** | Wiki 02 has it; keep it on the architecture page too |
| **swaps_dashboard as a required sibling** | Desk swap card is empty if `:8788` is down |
| **Three backtest stacks** | `Backtests/` vs `Vol_Suite/backtest_stage3.py` vs `Tools/backtesting_tool` (+ broker_book arm) |
| **VaR: context CLI vs widgets** | Only corr_sim in `--context`; all ten on the desk |
| **Sentiment: two products** | Social loop vs scanner widgets vs market-signals stage |
| **Jump-diffusion** | Calibrated early in `_run_core_analysis`; error object not None |
| **chart_app vs tradingview-mcp** | Two chart products; Chart tab is native |

### P2 — suite READMEs that lie or stall

| File | Issue |
|---|---|
| `VaR_Tools_Simulations/README.md` | “Vol integration coming”; suite-local venv |
| `Direction/README.md` | Six tool slugs; live is `directional-engine` |
| `Vol_Suite/README.md` | yfinance fallback vs current ThetaData-only story — verify then fix |
| `Options_Suite/PROJECT_ROADMAP.md` | Session log, not a roadmap |
| `shared/module_registry.py` header | “MODULES stays an empty list” |
| `docs/guides/FINANCIAL_DEVELOPMENT_INVENTORY.md` | Root test orphans, `--demo` — refresh or date-banner |
| `docs/guides/SWAPS_DATABASE_SPEC.md` | PostgreSQL |
| `docs/guides/DEPLOY.md` | Reads as supported |

**What does *not* need a new wiki.** CME/OTC adapters (stubs). `report_generator.py` (unwired). k8s. `blackhole_investments` (sidecar). `artifact-boards` empty README. Superpowers plans under `docs/superpowers/` — they are dated design history; leave them, do not promote them.

---

## 5. Documentation estate (volume vs truth)

There is already a method wiki (`docs/wiki/01–12`), human guides (`START_HERE`, `DESK`, `TABS`, runbook), agent SSOT (`CLAUDE.md` + `.claude/skills/`), a 2026-08-04 audit, a 2026-09-04 inventory, Vol dealer contracts, PHClient vendor wiki, and a pile of superpowers specs.

**The failure mode is not “no docs.” It is three generations of architecture sitting at the same apparent authority.** A new reader who opens `docs/wiki/README.md` will run a command that no longer exists.

Canonical rank going forward (proposal, for CARL to attack):

1. `CLAUDE.md` — agents
2. `docs/guides/DESK.md` + `TABS.md` + runbook — operators
3. `docs/wiki/` — method, **after** 06/07/04 are rewritten
4. Everything else dated or archived

---

## 6. Prune tiers

Do not bulk-delete. Confirm `git check-ignore` / `git ls-files` first.

### Tier 1 — safe after human OK (no live callers found)

- Dated `_cron_iv_watchdog_run_202608*.py` (14) + matching `artifacts/iv_watchdog_results_*.json`
- `aggregate_unified.py`
- `Options_Suite/impliedvol.py` (self-declared dead; `optionmodels` not installed)
- `debug_identity.py`, `iv_watchdog_scan.py`, `iv_alert_scan.py`, `iv_crush_confirm.py`
- Root `NOK_*`, root `chain_strategies.json`, `_scan_params.txt`
- `option_chains/`, `rh_exports/`, `sqlite-tools/*.exe`
- Unregistered Direction tool wrappers (keep `direction_signal_tool.py`)
- `_broker_control/compare_control_vs_model.py` (author-forbidden)
- Local generated trees already ignored (orchestrator_output, artifacts dumps, Vol outputs, sentiment PNGs) — disk only
- Leftover `__pycache__/quant_bridge*.pyc` etc.

### Tier 2 — confirm (live-looking or ops)

- Which IV cron Task Scheduler actually launches (`_cron_iv_watchdog_run.py` vs dated copies vs `scripts/vol_watchdog.py`)
- `live_dashboard.py` vs `tui/quant_tui.py` — archive the Rich one
- Deploy set (Docker/k8s/Procfile/system) — keep as museum if a Linux VM is planned
- `report_generator.py` — wire or mark deprecated
- `artifact-boards/` empty stub
- `blackhole_investments/` daily use unknown
- Vol `_causal_*` / `_scratch_*` — research *data* vs leak (keep the gate *scripts*; they have tests)
- `dashboard/quant_modules.py` — dead to `app.py`, still unit-tested; delete only with that test
- Vol `run_unified_flow` — interactive leftover of `--unified`; confirm before removing
- `sentiment-scanner/sentiment_live.py` (`:8099`) — fifth dashboard; used or archive
- `VaR_Tools_Simulations/IDEA.md` + remaining Excel tools — backlog, not prune

### Do not prune

Suites, `shared/`, `dashboard/` (except `_retired` templates and logs), `swaps_dashboard/`, `chart_app/`, `Direction/`, `Backtests/` source, `tradingview-mcp/src`, `_broker_control/build_broker_book.py`, `Tools/tools/broker_book.py`, adapters stubs, `HANDOFF_trading_journal_history_purge.md`, live dealer docs, **Vol dual-pipeline / expiry-tier CLIs (tested)**.

---

## 7. Architecture advice

1. **Treat the desk as the product, the suites as libraries.** The wiki still describes a batch pipeline. The operator product is: book → scope bar → tab → widget. Suites remain for interactive/CLI and for math ownership. Do not say “no subprocess anywhere”: Vol interactive mode 2 (`run_unified_flow`) still can spawn Options/VaR. Decide whether that mode is a feature or a Phase-7 leftover.

2. **One execution spine, two registries, is enough — but the comments lie.** `ModuleSpec` is the desk. `ToolSpec` is a subset adapted via `from_tool_spec`. Do not add a third. Fix the Phase-1 docstring. Do not re-register the five Direction wrappers.

3. **Context Store is the new fragile surface, not subprocess handoff.** Document key names as a contract test, not a prose hope. The 2026-09-11 `garch_vol` / missing-correlation bugs are the template for every future silent 0.25 VaR.

4. **Stop growing parallel “almost the same” stacks.** Three backtests, three IV watchdogs, two chart products, two TUIs, two PDF composers, two swap UIs (intentional split — keep, but start 8788 from ops docs), and **five things named dashboard** (`:8787`, `:8788`, `chart_app :8791`, `sentiment_live.py :8099`, `live_dashboard.py`). Pick or explicitly dual-purpose.

5. **`runnable=False` is a catalog footgun.** Four Vol markers raise if someone hits Run. The Volatility tab already special-cases VRP. Either hide them from the addable-tool list everywhere (already the intent of `runnable=False`) or give them real `run()` adapters. Do not leave “selection-only” as a surprise error on a card.

6. **VaR’s context-mode corr_sim-only is a historical scar.** Widget path already runs all ten. Either delete context-mode restriction and route `--context` through the registry, or document context-mode as deprecated. Do not leave README “coming soon.”

7. **CME/OTC adapters are honest stubs that fail quiet.** `fetch_trades()` returns `[]`. `DATA_SOURCES=DTCC,CME` ingests zero extra trades and does not error at discovery. Leave the files; do not enable them; say so in CROSS_SOURCE guides.

8. **Localhost-only is load-bearing — and already has an escape hatch.** `dashboard/tunnel.py` + `/share/start` exist. k8s LoadBalancer and `DEPLOY.md` fight the same rule. The quant-console agent can spend ThetaData money. Do not “just expose it”; decide whether share-tunnel stays behind an explicit Jason-only flag.

9. **Vol_Suite is a research landfill around a production core.** The live path is `_run_production_dealer_positioning` → `expiry_book_production`. Keep dual-pipeline scripts (they have tests). Move `_causal_acquisition_*` / `_scratch_*` *data* out of the source tree.

10. **Do not add a fifth suite.** Direction, chart_app, Backtests, blackhole, tui are sidecars. Promote a sidecar only when it has a desk tab and a Context Store contract.

---

## 8. Feature advice

Do these only after P0 wiki + pytest testpaths, or they will be built on a map that is wrong.

**Worth doing (desk-shaped):**
- A real “morning book” workflow on the desk: seed scope from positions, run a named panel batch, persist provenance. The runbook still talks in suite-result JSON; the desk talks in widgets.
- Finish VaR provenance UX: if `vol_source` starts with `fallback:`, the card should look broken, not pretty.
- Efficient-frontier / longer-horizon tools from `VaRtools Samples.xls` (`IDEA.md`) — this is the only suite with an explicit “the Excel still has tools we did not port” prompt. That is a feature, not hygiene.
- Wire or kill `report_generator.py`. A tested PDF compiler that nothing calls is a trap for the next agent.
- `swaps_dashboard` auto-start from `dashboard.bat` **or** a louder empty-state. Today it is a second process people forget.

**Do not do:**
- Implement CME/OTC.
- Cloud/k8s/Heroku.
- Re-add root `--unified`. Decide Vol mode 2 separately — it already does that job for one suite.
- Re-register five Direction tools as separate desk cards.
- Fan-out ThetaData (400ms sequential batch is the rule).
- Public dashboard without auth (and even then, think twice).
- Another chart surface. Chart tab is `chart_app`; TradingView is MCP. That is enough.

---

## 9. Delivery advice

Order of operations:

1. **Rewrite wiki 04/06/07/10 and `dashboard/README.md` to Phase 7.** Highest blast radius. Wrong commands waste billed API and operator time.
2. **Add `dashboard/tests`, `Backtests/tests`, `swaps_dashboard/tests` to `pyproject.toml` `testpaths`.** Known gap since at least the 2026-08-09 dashboard spec. Default `pytest` currently does not prove the desk.
3. **Gitignore the remaining commitable dumps** (`NOK_*`, root `chain_strategies.json`, `option_chains/`, `rh_exports/`, `sqlite-tools/`, Vol `_scratch_*` / `_causal_*`).
4. **Tier 1 prune** after `git check-ignore`.
5. **Pick canonical IV watchdog + document Task Scheduler.**
6. **Banner stale guides** (`SWAPS_DATABASE_SPEC`, `DEPLOY`, `PROJECT_AUDIT_AND_SPEC`, inventory) rather than deleting them — they still hold math and history.
7. **Trading journal history purge** only if a public remote is real. Until then the repo is private-by-necessity (`HANDOFF_*.md`).

Delivery metric that matters: a new session can follow `START_HERE` + wiki 07 and hit the **current** desk without reading CLAUDE.md. That is false today.

Do not measure this repo in COCOMO. It is one operator, agent-assisted, localhost. Size is a hygiene problem, not a staffing problem.

---

## 10. Test / CI gaps (delivery, not prune)

| Gap | Evidence |
|---|---|
| `dashboard/tests/` not in `testpaths` | `pyproject.toml` line 28; specs already admit this |
| `Backtests/tests/` not in `testpaths` | same |
| `swaps_dashboard/tests/` not in `testpaths` | same |
| Inventory claim that root `test_data_source.py` etc. are orphans | **Stale** — those files live under `tests/` now and *are* collected |
| VaR forex/cashflow/stress/mc dedicated files | Exercised via registry tests, not module-level files |
| No live pytest run in this audit | Unverified green/red |

---

## 11. Claims CARL should check (math and facts)

These are the load-bearing assertions. Attack these first.

1. Root `--unified` / `run_unified` / `run_suite` were removed 2026-09-04. Wiki 04/06/07 still describe them as the pipeline. [high — grepped]
1b. Vol_Suite `run_unified_flow` still exists and can subprocess Options/VaR. [high — `volatility_suite.py:1977-2038`]
2. VaR `run_context_mode` still errors on any module other than 1. Widgets still register ten modules. tool-launcher skill over-generalizes this to widget runs. [high — `main.py:813-824`]
3. CME/OTC adapters are stubs that return `[]`, not an error. [high]
4. Five Direction `Tools/tools/*_tool.py` files are not in `Tools/registry.py::_load_tools`. [high]
5. `report_generator.generate_pdf_report` has no production caller; Vol PDF is `compose_pdf_report`. [high]
6. `dashboard.bat` starts `:8787` and `:8791`, not `:8788`. [high — DESK.md; **did not execute the bat**]
7. Default `pytest` misses dashboard, Backtests, swaps_dashboard tests even though `dashboard/tests/` is large and current. [high — pyproject]
8. Live dealer path is `expiry_book_production` via `_run_production_dealer_positioning`. [high — `volatility_suite.py:614-642`; V2 design doc is stale]
9. Three IV watchdog algorithms coexist. [high]
10. `trading_journal/` is gitignored for new commits; history still contains it. [high — HANDOFF; **did not run git log**]
11. `from_tool_spec` always sets `ModuleResult(status="ok")` even if the tool dict is an error. [medium — `shared/module_registry.py:263-267`; UI impact not live-verified]
12. `dashboard/quant_modules.py` still marks Options `runnable=False` for a fixed stub; not imported by `app.py`. [high]
13. `Options_Suite/impliedvol.py` is self-declared dead. [high]
14. options-suite skill is wrong: `main.py` does call `chain_evaluation` on menu 9/10. [high]

---

## 12. What this audit did not do

- No `git ls-files`, `cloc`, `scc`, or pytest (shell Access Denied / out of scope).
- No live dashboard or ThetaData run.
- No CARL round (Jason will run it).
- No prune execution.
- Core-suites explore agent completed after first draft; material corrections are in §14.
- Did not open `VaRtools Samples.xls` against `IDEA.md`.
- Did not verify Task Scheduler cron targets.
- Did not measure OneDrive `swaps.db` size (older audits said ~320GB; layout doc is the authority).

---

## 13. Suggested CARL questions

- Is the P0 wiki rewrite the right first delivery, or is pytest `testpaths` higher because it catches desk regressions?
- Should `run_context_mode` die in favor of widgets, or be expanded to all VaR modules?
- Is the deploy set museum or delete?
- Which IV watchdog is canonical?
- Is “no new features until wiki+testpaths” the right lock, given `IDEA.md` Excel tools are the original VaR promise?
- Keep, hide, or delete Vol `run_unified_flow` (the remaining cross-suite subprocess)?
- Keep `dashboard/tunnel.py` `/share` behind a flag, or remove it to match localhost-only?

---

## 14. Corrections from the core-suites pass (folded above)

First draft over-claimed “no subprocess launcher.” Root orchestrator `--unified` is gone; **Vol interactive mode 2 is not.** Dual-pipeline gate scripts are tested research harnesses, not prune candidates. `impliedvol.py` is confirmed dead in-file. `fetch_options.py` source is gone. `dashboard/quant_modules.py` is a fossil registry. `from_tool_spec` can paint a failed Tools dict as `status: ok`. CME/OTC fail quiet (`[]`). options-suite and tool-launcher skills have specific lies. `sentiment_live.py:8099` and share-tunnel are extra surfaces CLAUDE.md does not treat as first-class.

End of audit.
