# FinancialDevelopment — Project Spec & Technical Audit

**Purpose of this document.** This is a from-scratch, whole-repo spec written for a model/session with zero prior context on this project. It exists to do three things at once: (1) explain what every part of the repo is and how to run it, superseding the need to re-derive that from scratch each session; (2) catalog every skill/reference doc already built, and what's stale vs. current; (3) record a technical audit — concrete bugs, math errors, and design critiques found while researching this doc, including a deep debate on Vol_Suite's dealer-positioning methodology.

**How this relates to `CLAUDE.md`.** `CLAUDE.md` remains the canonical, actively-maintained quick reference — read it first. This document is a deeper, point-in-time supplement: it goes further into "how does the math actually work" and "what's wrong" than `CLAUDE.md` is scoped to cover, and it will go stale faster. Treat findings below as of **2026-08-04**; re-verify against source before trusting a specific claim in a future session (this doc itself says so explicitly per file:line citations — check the citation, don't just trust the prose).

**Methodology.** Built via CARL (Convergent Adversarial Review Loop): six parallel research agents read every folder, a driver (this session) independently spot-verified the two highest-stakes numerical claims against source, then the draft went through adversarial review rounds before this became the final version. See the CARL summary at the bottom for the review ledger.

---

## Part 1 — How to run everything

This section is deliberately a condensed, task-oriented restatement of `CLAUDE.md`'s command table plus what the research surfaced as *actually working* vs. documented-but-broken.

### First-time setup
```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt   # Windows
python setup_db.py
```
`sentiment-scanner/` is the one exception — it keeps its **own** project-local `.venv` and auto-installs its own deps on every launch (`sentiment.bat`). Don't try to run it under the shared root venv; it's missing `yt-dlp`/`curl_cffi`/`bgutil` deps there.

### Day-to-day commands

| Task | Command | Notes |
|---|---|---|
| Dashboard (`http://127.0.0.1:8787`) | `dashboard.bat` | Refuses to double-launch on port 8787 |
| Tools module (`/tools`, same process) | `tools.bat` | |
| DTCC live poller (leave running) | `run_scheduler.bat` | Polls every 5 min; refuses to double-launch |
| Orchestrator, unified | `orchestrator.bat --unified --ticker NVDA --expiry 2026-10-16` | Sentiment stage is **hardcoded skipped** in `run_unified` regardless of flags — see Part 5 |
| Orchestrator, single suite | `orchestrator.bat --suite vol\|options\|var\|sentiment --ticker AAPL` | `--suite options` currently **fails validation** (Options_Suite context-mode stub is incomplete — see Part 5); `--suite var` always runs VaR module 1 only, no flag exists to change that |
| Full swap backfill (hours, resumable) | `python backfill.py` | Ctrl+C-safe; resumes from `ingestion_state` |
| Query swap DB | `python swaps_query.py` or `from swaps_query import SwapsQuery` | `swaps.db` is **~320GB** — see Part 6 finding #1 before writing ad hoc full-table-scan queries |
| Run a suite standalone | `Options_Suite\options_suite.bat`, `Vol_Suite\vol_suite.bat`, `VaR_Tools_Simulations\var.bat`, `sentiment-scanner\sentiment.bat` | |
| Tests, whole monorepo | `pytest` | **Only covers `tests/`, `shared/tests/`, and each of the 4 suites' `tests/`** — root-level loose `test_*.py` files (e.g. `test_data_source.py`, `test_logging.py`) are NOT in `pyproject.toml`'s `testpaths` and are silently excluded from every `pytest` run. They still look accurate to current code; they're just dead to CI. |
| Tests, one suite | `pytest Vol_Suite/tests/` | `sentiment-scanner/tests/` needs its own venv's `pytest`, no config file present, run from inside that folder |
| VaR demo runs (`README.md`'s documented flag) | ~~`python main.py --demo all`~~ | **Broken — doc/code drift.** `--demo` does not exist in current `main.py` argparse (only `--module`, `--context`, `--context-out`). `README.md` and `CLAUDE.md` both still document it. Use `python main.py --module N` (interactive) or `python -m var_engine.<module>` (each module's own canned demo) instead. |

### What's genuinely NOT runnable today, and why
- `.claude/skills/options-suite/SKILL.md` and the `tool-launcher` skill both describe `Options_Suite/main.py --context` as writing a stub payload missing `method`/`sigma`/`price`/`greeks`, failing validation. **This is stale.** Directly verified against current source (`Options_Suite/main.py:130-219`): the success path (`status: "ok"`) genuinely computes real CRR pricing/Greeks and populates all four fields; `shared/schemas.py::validate_options_result` only requires those fields when `status=="ok"` and accepts a narrower error shape otherwise. `orchestrator.bat --suite options` most likely succeeds today — this wasn't run live to fully confirm (that would cost a real ThetaData call), but the code-level contradiction between the skill docs and current source is confirmed.
- `VaR_Tools_Simulations/main.py --context ... --module 2` (or any module other than 1) returns a clean `status: "error"` JSON, not a crash, by design.
- Deployment paths (`DEPLOY.md`, `DOCKER_SETUP.md`, `k8s/`, `Procfile`) describe Heroku/Docker/Kubernetes targets nobody runs — `CLAUDE.md` states outright there is no cloud deployment path in active use. `Procfile` itself warns Heroku's ephemeral filesystem would wipe `swaps.db` on every deploy.

---

## Part 2 — Full project map

### Root-level: current vs. stale

The repo root has ~30 loose `.md` docs and ~10 loose scripts outside any suite folder. Classified by what the research found (not by filename guess):

**Current / load-bearing:**
`CLAUDE.md`, `START_HERE.md`, `CROSS_PLATFORM.md`, `DTCC_LOCAL_SETUP.md`, `LOAD_YOUR_CSVS.md` (both of the last two explicitly self-document that their *older* manual-workflow content "no longer applies" — the current bodies are accurate), `CROSS_SOURCE_ANALYTICS.md` / `CROSS_SOURCE_UPI_GUIDE.md` / `IMPLEMENTATION_SUMMARY_CROSS_SOURCE.md` / `INSTRUMENT_IDENTIFIER_IMPLEMENTATION.md` (accurate about code that exists, but describe CME/OTC adapters as usable when both are stubs — see Part 5), `QUERY_MONITORING.md`.

**Stale (superseded or describing dead paths), but harmless to leave:**
`SWAPS_DATABASE_SPEC.md` (describes a PostgreSQL schema; `CLAUDE.md` itself flags this — trust `migrations/*.sql`), `DEPLOY.md` / `DOCKER_SETUP.md` / `k8s/*` / `Procfile` / `Dockerfile.*` (vestigial, no active deployment), `GRACEFUL_SHUTDOWN.md` + `IMPLEMENTATION_CHECKLIST.md` + `IMPLEMENTATION_SUMMARY.md` (three overlapping point-in-time completion reports for `shutdown_signal.py` — the *code* is current and load-bearing, the docs are historical), `LOGGING_GUIDE.md` + `LOGGING_IMPLEMENTATION.md` (same pattern for `shared/logging.py`), `OPTION_B_REFACTOR_COMPLETE.md` (dated 2026-07-28, superseded by later Options_Suite work), `agent-kb-lessons.md` (a meta-essay, not a technical reference).

**Not in pytest's collection path (orphaned from CI, not necessarily wrong):**
`test_data_source.py`, `test_decode_upis.py`, `test_logging.py`, `test_query_monitor.py`, `test_upi_decoder.py`, `test_graceful_shutdown.py`, `verify_graceful_shutdown.py`, `test_phase2.py`, `load_test_pool.py`, `pool_integration_example.py`, `quick_pool_verification.py`. `pyproject.toml`'s `testpaths` never lists the repo root.

**Fully unwired feature (found during this audit — not previously documented anywhere):**
`report_generator.py::generate_pdf_report()` is threaded all the way through `suite_context.json`'s `controls.compile_pdf` flag and `shared/schemas.py` validation, and has its own passing test (`tests/test_report_generator.py`) — but **zero live call sites** anywhere in `orchestrator.py` or `dashboard/app.py` actually invoke it. The PDF-compile feature is fully specified and tested in isolation, but not wired to anything that runs it.

**Not gitignore'd risk, checked and cleared:** the six `CFTC_SLICE_EQUITIES_2026_07_28_68{3-8}/` raw-data dump directories at repo root **are** covered by `.gitignore`'s `CFTC_SLICE_EQUITIES_*/` pattern — no accidental-commit risk there.

### `shared/` — the cross-suite library

| File | Role | Notable finding |
|---|---|---|
| `data_source.py` | `DataSourceAdapter` ABC + `TradeRecord` | Clean, minimal, no issues found |
| `query_builder.py` | `CrossSourceQueryBuilder` | `days_back`/`limit` spliced into SQL via f-string, not parameterized (low real risk, only ints from internal callers, but inconsistent style — see Part 6 #7) |
| `connection_pool.py` | Thread-safe SQLite pool, WAL mode | Solid; one narrow TimeoutError race under contention, acceptable |
| `identifiers.py` | UPI/CME/OTC identifier normalization | `LocalIdentifierResolver` is explicitly an in-memory reference impl, not production-backed; **not actually wired into DTCC ingestion** — migration 003's columns exist but nothing populates them from this resolver during ingestion today |
| `schemas.py` | 7 validators for every inter-suite JSON contract | Most rigorously written file in `shared/` — no issues found |
| `suite_validation.py` | Marker-file presence/schema gate | Well-designed defensive checks throughout |
| `context_audit.py` | Audited context mutation (sentiment fold-in) | Thorough, but only exercised via the dummy-audit stub today since sentiment is hard-skipped |
| `cache.py` | Disk cache in `.shared_cache/` | Silently no-ops on write failure (deliberate best-effort) |
| `config.py` | `.env` loader, `load_env_once()` | Uses a mutable-default-arg idiom as a call-once sentinel — works, but fragile/unusual |
| `query_monitor.py` | Slow-query tracking, `@monitor_query` | Infers `query`/`params` positionally from `args[1]`/`args[2]` — fragile if a decorated signature changes |
| `thetadata.py` (43KB) | `ThetaDataController` — the merged data client all 4 suites route through | Large public surface, not read line-by-line; no suite has its own independent ThetaData client anymore per `CLAUDE.md` |
| `summary.py` | Builds `quant_summary.json` for dashboard module cards | Deliberately half-built extension point (`module_registry` param references a thing that "doesn't yet exist" per its own docstring) |
| `logging.py` | Structured JSON logging, `MetricsCollector` | No issues found |

### DTCC ingestion pipeline

`dtcc_api_client.py` (public unauthenticated feed, exp. backoff) → `dtcc_parser.py` (per-row try/except, but **rows missing a Dissemination Identifier are silently dropped with no counter** — worth adding visibility) → `db_loader.py` (`SwapsLoader`, upsert via `ON CONFLICT...DO UPDATE`, cross-process `FileLock`; **`upsert_trades` cannot actually distinguish inserts from updates** — it always returns an estimated count, self-documented). Driven by `backfill.py` (resumable, correctly does *not* advance state on a failed entry) and `scheduled_ingest.py`/`poll_ingest.py` (5-min APScheduler loop; UPI decoding after each poll is deliberately best-effort). `adapters/dtcc_adapter.py` is real and working; `cme_adapter.py`/`otc_adapter.py` are **confirmed stubs** — `fetch_trades()` logs a warning and returns `[]` unconditionally.

`upi_decoder.py`/`decode_upis.py`: two-tier company-name decoder (local parse, then OpenFIGI fallback via a hardcoded 8-exchange RIC-suffix table), watermarked by `rowid` so it never rescans the full table.

### `migrations/`

001 (baseline schema) → 002 (adds `data_source` column) → 003 (adds cross-source identifier columns, not yet populated by ingestion — see above) → 004 (adds `quant_alerts` table for the dashboard's alert banner).

### The four suites — one-paragraph orientation each

- **Options_Suite** — American option pricing across CRR, Leisen-Reimer, Newton-Raphson, brute-force IV, SABR, Vanna-Volga, plain/Heston Monte Carlo LSM, Barone-Adesi-Whaley. **`CLAUDE.md` and its own skill both describe `main.py` as ~120 lines with only `--context`/`--context-out` reachable, and that chain_evaluation.py/reports.py are unreachable — this is now stale.** `main.py` is actually 909 lines; interactive menu choices 9 ("run all models & compare") and 10 ("full chain evaluation") genuinely import and call `chain_evaluation.py`/`reports.py`. The narrower claim — that headless `--context` mode only reaches CRR — is still correct. See Part 5 for the pricing-math findings (several past bugs, now fixed and well-documented in-code; two live issues remain: a Newton-Raphson pricer mismatch, and dead validation config).
- **Vol_Suite** — the largest suite (~1850 lines core): dealer positioning (3 sign conventions, see the full debate in Part 4), variance-swap replication, VRP term structure, GARCH(1,1), correlation/basket construction, a multi-ticker screener, strategy recommender. Full context-mode consumer (unlike VaR/Options), several expensive sub-steps opt-in only via `VS_RUN_*` env vars.
- **VaR_Tools_Simulations** — Python port of a legacy Excel VaR toolkit, one `var_engine/` module per original spreadsheet tab (corr_sim, mc_sim, hist_sim, copulas, forex_var, cashflow_map, stress_test, var_agg, hedge_optimizer, price_dist). Headless `--context` mode only ever runs module 1 (corr_sim); this is enforced explicitly, not a bug, but every other module has zero context-mode path. See Part 5 for a confirmed, currently-user-facing math bug in `var_agg.py`.
- **sentiment-scanner** — contested-narrative sentiment (StockTwits/Reddit/YouTube) cross-referenced with 7 options-flow scanners it delegates to Vol_Suite for the actual math (GEX → `dealer_positioning.py`, IV Rank → `variance_swap_screener.py`, Skew → `vol_surface_reference.py`), plus CME swap-data-repository scraping. Long-running by default, own project-local venv. See Part 5 — the correlation engine's combination logic has a significant gap (CME SDR data and the original 5 correlation rules never actually reach the signal engine in the live run path).

### Dashboard, Tools, and everything besides the Quant Console

The Quant Console (`/quant`, `/runs/*`, `/alerts/*`, worker dispatch) is covered in depth by this session's own recent work — see Part 5 for what's current there. Everything else in `dashboard/app.py`:

| Route | Purpose |
|---|---|
| `GET /` | Home — DB stats, top-notional products, ingestion state, recent runs |
| `GET /swaps` | Paginated/filterable `swap_trades` browser |
| `GET /suites/{suite}` | Newest output file (CSV/JSON) for a suite |
| `WS /suites/{suite}/live` | Live log tail |
| `GET/POST /tools`, `/tools/options-strategy`, `/tools/backtest` | The `Tools/` plugin framework's own UI |
| `GET /trades`, `/instruments/{upi}` | Cross-source query/resolution |
| `GET /analytics/cross-source-notional`, `/analytics/timeseries` | Cross-source aggregation |
| `GET /health`, `/metrics/queries`, `/metrics/slow-queries`, `/metrics/health` | Ops/observability endpoints |

`dashboard/auth.py` currently has **only** `get_client_ip` — the API-key auth layer was deliberately removed this session (commit `88a7912`); see Part 5.

`Tools/registry.py::TOOLS` has exactly **2** registered tools today: `options-strategy` (wraps `Vol_Suite/strategy_recommender.py`, cached-vs-live mode) and `backtesting` (wraps `Vol_Suite/backtest_stage3.py` — includes a real Welch's t-test study comparing dealer-positioning sign conventions against forward realized vol, see Part 4). Adding a third tool is a documented 2-step process (`run()` + `TOOL_SPEC` in a new file, append to the registry list).

### `mcp-stockflow/` — separate, unintegrated

A standalone open-source MCP stdio server (yfinance-backed, by a third-party author, last commit 2025-02-24) that this repo vendors as a nested git repo but **never imports, launches, or references anywhere in live code**. `CLAUDE.md`'s claim that its "live data layer is fully merged into the parent repo's `shared/`" is true for `Options_Suite` but **not true for `mcp-stockflow`** — it has its own independent yfinance path and zero coupling to this repo otherwise. Reads as vendored-and-never-integrated, not actively used.

---

## Part 3 — Skills and reference-doc inventory

### `.claude/skills/` — what's real and current

| Path | Type | Status |
|---|---|---|
| `quant-suite-launch-conventions.md` | Shared reference (deliberately no frontmatter) | Current — venv/port checks, `suite_context.json` contract, flags the two-different-`validate_vol_result`-signatures trap |
| `options-suite/SKILL.md` | Proper skill | Mostly current; its "~120 lines" claim is now stale per Part 2 above (main.py grew to 909 lines with a working interactive menu) — the narrower "context mode is a CRR-only stub... FAILs validation" claim needs re-checking too, since this audit found `run_context_mode` **does** now populate `method`/`sigma`/`price`/`greeks` via CRR (the skill describes an even-earlier placeholder state) |
| `sentiment-scanner/SKILL.md` | Proper skill | Current, verified accurate on the bgutil silent-degrade behavior and flag list |
| `tool-launcher/SKILL.md` | Proper skill | Current, verified accurate on orchestrator PASS/FAIL quirks per suite |
| `var-tools/SKILL.md` | Proper skill | Current, verified accurate — correctly documents context-mode-only-runs-module-1 |
| `vol-suite/SKILL.md` | Proper skill | Current, and self-aware — re-verifies an older internal FIX_PLAN against source and confirms most items already fixed, flags the still-live OI-outlier-filtering gap referenced in Part 4 |
| `vol-suite.py`, `vol-suite-viewer.py`, `vol-suite-outputs.py` | **Not skills** — loose scripts sitting in the skills directory | **Fully superseded, safe to delete.** Their dashboard-scraping logic was explicitly designed to be ported into `GET /quant` — and it was: `quant.html`'s own CSS comment says it was "Ported from vol-suite.py::generate_dashboard_html," `shared/summary.py` ports the metric-extraction logic, and 16 "Quant Console Task N" commits built the real thing. `vol-suite-outputs.py` is an even cleaner delete candidate than the other two — it's a strict subset of `vol-suite-viewer.py` with zero references anywhere. **Recommendation: delete all three, with your explicit go-ahead** (matching this repo's own established norm of not silently deleting flagged-for-deletion files). |

### `.claude/` top-level docs — a frozen snapshot from one session

16 files (`CARL_REVIEW_FINDINGS.md`, `EXPANSION_PLAN.md`, `PHASE_1_*`, `PHASE_2_*`, `PHASE_3_PLANNING.md`, `CONNECTION_POOL_*`, `DATA_SOURCE_ADAPTER_*`, `INSTRUMENT_IDENTIFIER_DELIVERY.md`, `QUERY_MONITORING_SUMMARY.md`, `IMPLEMENTATION_STATUS.md`, `FULL_SYSTEM_STATUS.md`, `PRODUCTION_READY_HANDOFF.md`) were all produced in a single dense session on 2026-07-29, following a CARL review that found the dashboard had no authentication. None of these are referenced from `CLAUDE.md`. Two concrete pieces of drift, checked directly against current code rather than inferred:
1. **Auth**: `PHASE_1_QUICK_START.md`'s headline fix ("Dashboard Auth: Open to anyone → Requires API key") describes a security posture that was later **deliberately reversed** this session (commit `88a7912`) — the API-key gate was added, then removed, on the reasoning that this is a single-user localhost tool. `dashboard/auth.py`'s current docstring states this outright.
2. **Deployment framing**: `PHASE_3_PLANNING.md`'s Heroku/k8s roadmap and `PRODUCTION_READY_HANDOFF.md`'s "production-ready, enterprise-scalable" framing directly contradict `CLAUDE.md`'s current, explicit "no cloud deployment path in active use — single-machine, localhost-only" statement.

The underlying *code* these docs describe (`connection_pool.py`, `query_monitor.py`, `data_source.py`, `identifiers.py`, `migrations/`) is still live and current — only the docs and the deployment/auth framing are stale. **Recommendation**: these 16 files would be a reasonable candidate for archiving into a `docs/archive/2026-07-29-phase-1-5/` folder (or deletion, with your say-so) — they add noise for a future session trying to find current guidance, since nothing points to them and two of their central claims are now false.

Also present: two live git worktrees under `.claude/worktrees/` (`orchestrator-ticker-cancel-09ad09`, `reload-skills-1a66fb`) — both have real recent commits, not abandoned; worth confirming with you whether either still needs to stay checked out.

---

## Part 4 — Deep dive: Vol_Suite's dealer-positioning methodology

This is the "really debate it" section. `Vol_Suite/dealer_positioning.py` implements three interchangeable "sign conventions" for turning unsigned open interest into a signed "dealer net gamma" estimate. All three share the same core aggregation (`sign * gamma * OI`, verified directly at `dealer_positioning.py:475,490`) and differ only in how `sign` is computed.

### The fundamental problem, stated precisely

Recovering *signed* dealer positioning from *unsigned* open interest is a genuinely ill-posed inverse problem. Open interest tells you a contract exists and how many are outstanding; it tells you nothing about which side of the original trade the dealer took. Every convention here is a **heuristic proxy for missing data** (real trade-direction tagging, or a CBOE-style customer/firm/market-maker OI split), not a measurement. The code is honest about this — `_dealer_sign`'s own docstring calls itself "a MODELING ASSUMPTION, not a measured fact" — which is the right posture; the question worth debating is whether the three heuristics offered are the best available proxies, and whether presenting them as three independent, equally-valid "conventions" a user picks between is the right interface at all.

### v1 — `oi_heuristic`: call OI → dealer long, put OI → dealer short, flat

```python
def _dealer_sign(right: str) -> float:
    return 1.0 if right == 'C' else -1.0
```
(`dealer_positioning.py:220-248`)

This is the standard public-GEX-tracker convention (SqueezeMetrics/SpotGamma-style). Its implicit customer-flow story: customers are net **buyers** of both calls and puts (speculation and hedging), so the dealer, as counterparty, ends up short calls (dealer long gamma from being short... wait — carefully: dealer short a call is short gamma, and this convention marks *call OI as dealer-long*). Actually tracing it through, as verified below, the convention's implicit assumption is closer to: dealer ends up **long** the calls (because customers *wrote* covered calls and the dealer bought them) and **short** the puts (because customers bought protective puts from the dealer). That's a real, coherent customer-flow story — but it is a *specific* one, and it is not the only plausible one, and the code never states which one it's assuming; a reader has to reverse-engineer it (as this document does below).

**Worked failure case** (verified by tracing the code, not asserted): a customer **sells** (writes) a cash-secured put — a mainstream, common institutional and retail strategy. The dealer buys that put and is now **long** the put — long gamma, a genuinely positive contribution to dealer net gamma. `oi_heuristic` sees `right == 'P'` and returns `-1.0`, contributing **negatively**. This is backwards for exactly this (common) trade. Symmetrically: a customer **buys** a call. The dealer sells (writes) it and is **short** — short gamma, a negative contribution. `oi_heuristic` returns `+1.0` for any call OI, contributing **positively** — also backwards for this (equally common) trade.

The convention is therefore not simply "an approximation with some error" — it's **systematically wrong for the entire class of customer-sold options**, and wrong in *opposite* directions for calls vs. puts within that class. Whether it's net directionally useful in aggregate depends entirely on whether customer-bought or customer-sold flow dominates a given name's OI — which varies by ticker (single-name meme-stock retail flow skews customer-buy-heavy; SPY/QQQ index OI has enormous embedded overwriting-program flow that skews customer-sell-heavy at OTM strikes specifically). Using one flat rule across every ticker and every strike, with no adjustment for the kind of name it's applied to, is the weakest part of this design.

### v2 — `replication`: dealer short whatever the variance-replication strip is long, OTM-only

```python
if sign_model == 'replication':
    if otm_strikes is None or (strike, right) not in otm_strikes:
        return 0.0
    return -1.0
```
(`dealer_positioning.py:295-298`)

This conflates two unrelated things: a **theoretical replication portfolio** (the Demeterfi/Carr-Madan strip that would synthetically reproduce a variance swap payoff — a *valuation and hedging construct*) with **actual observed OI** (a record of real transactions). There is no principled reason real customer OI in a given OTM option should mirror the composition of a synthetic replication strip nobody in the market is necessarily trading. The convention's own self-documented behavior — it can *never* produce a positive (dealer-long) gamma reading, on any ticker, under any market condition (`dealer_positioning.py:276-281`, confirmed empirically on SPY and QQQ per the module's own comments) — is itself the strongest evidence against it: a model that is unconditionally wrong in the same direction 100% of the time isn't merely imprecise, it has zero discriminating power. It is, in effect, a constant, dressed up as a positioning model.

### v2.1 — `vol_surface_replication`: same OTM gate, but sign flips per-strike based on IV rich/cheap vs. a SABR reference

```python
def resolve_vol_surface_sign(ref, strike, right) -> float:
    dev = ref.deviation_by_strike.get((strike, right))
    if dev is None:
        return 0.0
    return -1.0 if dev > 0 else (1.0 if dev < 0 else 0.0)
```
(`vol_surface_reference.py:405-416`, sign resolution called from `dealer_positioning.py:299-306`)

This is the most defensible of the three, and the only one capable of ever assigning a positive sign — it fixes v2's structural blind spot by inferring flow direction (rich IV → net buying → dealer short; cheap IV → net selling/overwriting → dealer long) from a genuine, per-strike, per-ticker signal rather than a flat assumption. Re-running the two worked failure cases above through v2.1: the customer-written put should trade **cheap** relative to the SABR reference (selling pressure depresses its price/IV), correctly resolving to `+1.0` (dealer long) — right answer, for the right underlying reason, not by construction. Symmetrically, the customer-bought call should trade **rich**, correctly resolving to `-1.0`.

**But this correctness is conditional on an unverified assumption**: that IV deviation from a smooth reference curve is actually driven by recent one-sided flow, rather than by, e.g., persistent structural skew, vol-of-vol effects the SABR fit doesn't capture at extreme strikes, or historical (not current) supply/demand baked into OI built up over the option's whole life. The code never validates this against ground truth — no trade prints, no CBOE customer/firm OI split, nothing signed is consulted anywhere in this file. It is an unfalsified hypothesis dressed in a rigorous SABR calibration, which can make it *look* more trustworthy than v1's obviously-crude flat rule without actually having more evidence behind it. It also collapses a continuous, informative quantity (the deviation magnitude) down to a 3-way sign (`rich`/`cheap`/`no data`) — a strike trading 0.3 vol points rich and one trading 8 vol points rich get treated identically.

### A claim retracted during review: `dollar_gamma` is NOT missing a factor of spot

The first draft of this document claimed `dollar_gamma`/`hedge_requirement` were dimensionally short one factor of spot versus the "standard" `Γ·S²·0.01·multiplier·OI` GEX convention. **An independent adversarial review round caught this, and re-deriving it by hand confirms the review was right and the original claim was wrong** — worth leaving in the document as a worked example of exactly the kind of error this whole audit is trying to catch elsewhere, including in itself.

The actual units, traced through carefully: `dollar_gamma = Γ·S·multiplier·OI` (`dealer_positioning.py:476`) has units of **shares** — (1/$)·($)·(shares/contract)·(contracts) — not dollars, despite the name. It represents "shares of stock a dealer must buy/sell to rehedge, for a $1 move in the underlying." `hedge_requirement = abs(net_dollar_gamma * 0.01)` (`dealer_positioning.py:537`) then scales that by 1% of spot's worth of move, yielding **shares per 1% move** — which is exactly what the UI labels it as ("shares per 1% move," `print_report`). The "standard" `Γ·S²·0.01·multiplier·OI` formula this was compared against is a **dollar**-denominated quantity (dollar P&L convexity per 1% move) — comparing a share count against a dollar formula and calling the difference a "missing factor" was an apples-to-oranges dimensional-analysis error, not a real bug in the code. `print_report` separately computes the true dollar-notional figure correctly, via `hedge_requirement * spot` — proof the code is internally self-consistent. A dedicated regression test, `Vol_Suite/tests/test_hedge_requirement.py`, explicitly documents and locks in the correct (non-buggy) formula, referencing a **prior, already-fixed** bug that went in the *opposite* direction (an old version divided by spot instead of not scaling by it at all). No fix is warranted here — this whole subsection is retracted as a finding; see the CARL ledger at the bottom for how this was caught.

### Recommended improvements, concretely

1. **Stop treating the three conventions as independent user-selectable modes; run all three and report agreement/disagreement as a confidence signal — but exclude v2 (`replication`) from the positive-sign side of that score.** A strike where multiple conventions agree on sign is more trustworthy than one where they diverge — that divergence is currently thrown away (a user picks one `sign_model` per report and never sees what the other two would have said). *Caveat added during review*: v2 can structurally only ever return `0.0` or `-1.0` (confirmed at `dealer_positioning.py:295-298` and stated in v2's own section above) — it can never corroborate a positive/dealer-long reading. A naive 3-way agreement score would therefore be systematically biased toward treating negative/short-gamma readings as higher-confidence than positive ones, for a reason that has nothing to do with the actual evidence. The fix: either compare only v1 vs. v2.1 (both of which can go either sign) for the agreement score, or explicitly weight v2's vote as one-sided corroboration only, never as evidence against a positive reading.
2. **Weight v2.1's sign by deviation magnitude, not just its sign** — e.g. `tanh(deviation / scale)` instead of a step function — so a barely-off-reference strike doesn't get full weight equal to a deeply mispriced one. This directly recovers the information the current 3-way sign function throws away.
3. **Use day-over-day OI *changes*, not OI levels, wherever the data is available.** A change in OI is a dateable event you can cross-reference against that day's price/IV move to infer aggressor side (opened near the ask on an up day → more likely customer-buy-initiated) — this is a genuinely different, more defensible signal than either a flat heuristic or a reference-curve-deviation proxy, and the code currently only reads current-level OI, never a time series of it.
4. **Empirically validate v2.1's core assumption using infrastructure that already exists but appears unused.** `Vol_Suite/backtest_stage3.py` already implements a real Welch's two-sample t-test comparing forward realized vol between v1- and v2.1-classified long/short-gamma regimes (confirmed at `backtest_stage3.py:344-364`), exposed as the `dealer_gamma_study` mode of the dashboard's `backtesting` Tool. **This was attempted, live, on 2026-08-04, for SPY with default parameters (90-day lookback, 5-day forward window).** It did not produce a result: the ThetaData/PotatoHedge proxy returned `502 Bad Gateway` on a large fraction of requests during the run (83 of 486 per-contract EOD-history fetches, 54 of 116 open-interest-by-day fetches), which cascaded into `0 derived from price, 0 vendor, 21038 unrecoverable (100.0% dropped)` and ultimately `0 usable days` — nothing to run the t-test on. This reads as a transient vendor/proxy reliability issue on the day this was attempted, not a bug in `backtest_stage3.py`'s logic (the study's own error path degraded correctly — it reported exactly what it could and couldn't fetch rather than fabricating a result from partial data). **Still the single highest-value next action on this whole debate — just not yet completed.** Retry when the ThetaData/PotatoHedge proxy is healthier; if 502s recur reliably on this specific call pattern (8-concurrent historical bulk fetches), that itself would be worth reporting upstream to the vendor.
5. **Add OI outlier/liquidity filtering** — already flagged by `.claude/skills/vol-suite/SKILL.md` itself as a known live gap: one large stale/illiquid strike can dominate the whole gamma sum today with no magnitude or recency sanity check.

**Note on test coverage for this module, added during review**: `Vol_Suite/tests/test_dealer_positioning_sign_model.py` and `test_hedge_requirement.py` directly exercise the functions debated above and would have caught the retracted claim immediately — worth checking first against any future claimed bug in this module, before writing it up.

---

## Part 5 — Consolidated bug/math findings, severity-ordered

| # | Severity | Location | Finding |
|---|---|---|---|
| 1 | **High — live, user-facing** | `VaR_Tools_Simulations/var_engine/var_agg.py:60-68` | `_component_var` computes `z·(Σ·pos)_i/σ_p`, omitting the `positions[i]·` multiplication the Euler/component-VaR identity requires. The value is dimensionally a marginal-return sensitivity, not a dollar figure, and does not sum to `total_var` as displayed in `main.py:890-892` next to `standalone_var` (which *is* in dollars). **Verified directly against source, and re-confirmed after adversarial review** — confirmed the exact missing term. The existing tests (`test_var_agg.py:87-91,143-147`) pass only because they manually reapply the missing `positions *` multiplication themselves — they validate the underlying math identity is *achievable*, not that the production code path actually achieves it. **Fix**: multiply `sigma_w` by `positions` before scaling by `z/sig`. |
| 2 | **Medium — correctness** | `Options_Suite/NewtonRaphsonIV.py:113-114` vs `Options_Suite/vol_manager.py:189-194` | `vol_manager.py`'s comment claims Newton-Raphson American IV is "solved against the Leisen-Reimer American price," but the actual call passes no `pricer` argument, silently defaulting to BAW. `main.py` then reprices the *displayed* price/Greeks via the LR tree at the BAW-calibrated sigma — a genuine cross-model sigma/pricer mismatch, contradicting the code's own comment. |
| 3 | **Medium — API/correctness mismatch** | `VaR_Tools_Simulations/var_engine/copulas.py:39-50,114-115` | `_sample_clayton` never receives or uses `corr_matrix` — every pairwise dependence collapses to the single scalar `alpha` regardless of a heterogeneous correlation matrix the caller supplied. Undocumented (no caveat in the class docstring). |
| 4 | **Medium — inconsistent definitions, same field name** | `Vol_Suite/vrp_term_structure.py` vs `Vol_Suite/variance_swap_screener.py` | Both produce a field called `vrp_pct`, but the term-structure module measures fair-vol minus **ATM implied vol** (a convexity premium, both sides implied), while the screener measures fair-vol minus **trailing realized vol** (the textbook VRP definition). A reader comparing outputs across the two modules would be comparing genuinely different quantities under an identical name. |
| 5 | **Medium — related but non-identical unnormalized metrics** | `Vol_Suite/variance_swap_screener.py:181-182` vs `Vol_Suite/replication_reference.py:267-298` | The screener's `tail_mass` (a strike-band weighted-contribution fraction) uses a flat, unnormalized `[0.5F, 2.0F]` strike cutoff, directly feeding its composite score. `replication_reference.py`'s `range_truncation_score` (a different, IV-standardized-distance-to-edge-of-chain metric — not the identical formula, corrected during review) was already fixed to be IV/T-normalized after finding the unnormalized version scored a high-vol name as "less truncated" than a low-vol one purely from vega-curve shape. That same *class* of fix — normalize by vol level — was never applied to the screener's `tail_mass`, which still carries the bug the sibling diagnostic self-diagnosed and fixed. |
| 6 | **Low-Medium — under-specified stationarity check** | `Vol_Suite/garch_analysis.py:104,138-139,274-299` | GARCH(1,1) persistence (`alpha+beta`) is only checked post-hoc for the unconditional-vol divide-by-zero guard, never gated before narration. A fit with `persistence ≥ 1` (non-stationary) would still pass the significance gate and emit a full directional narrative with no explicit non-stationarity flag. ADF test result is computed and printed but not wired into any suppression logic either. |
| 7 | **Low-Medium — silent no-op flag** | `VaR_Tools_Simulations/var_engine/hedge_optimizer.py:96-104` | `hedge_independent=False` is documented as using a full cross-hedge correlation matrix, but the `else` branch is unimplemented — the flag currently behaves identically regardless of its value. |
| 8 | **Low-Medium — unchecked convergence** | `VaR_Tools_Simulations/var_engine/hist_sim.py:20-51` | GARCH(1,1) fit for Hull-White/FHS variants via `scipy.optimize.minimize(Nelder-Mead)` never checks `res.success`; a non-converged fit propagates silently into displayed VaR. (Contrast with `hedge_optimizer.py`, which correctly raises on QP non-convergence.) |
| 9 | **Low — significant gap in a "combined signal"** | `sentiment-scanner/correlation/engine.py`, `main.py:172,428,448` | `correlate_with_oi()`'s original 5 rules (`DIRECTIONAL_BET_FORMING` etc.) are dead in the live run path — both call sites pass `oi_snapshot={}`. CME swap-data-repository scraping (`swap_sdr.py`) is fetched and printed to console but never fed into the correlation engine at all. In practice only the 7 newer per-scanner signals × narrative-CNS gating actually fire; the "cross-referenced with... CME swap-data-repository activity" framing in `CLAUDE.md`/skill docs overstates what's live. |
| 10 | **Low — misleading precision** | `sentiment-scanner/scanner/vol_dispersion_scanner.py:144-148` | `iv_spread_z` is presented alongside a genuine `beta` estimate as a comparable statistic, but is self-referentially derived from the stock's own current IV (`spread_vol_est = max(stock_iv*0.15, 2.0)`) rather than any real historical spread distribution — no history is stored anywhere to compute a true z-score. |
| 11 | **Low — doc/code drift, could waste a session's time** | `VaR_Tools_Simulations/README.md`, `CLAUDE.md` | `--demo all`/`--demo <module>` is documented in both but doesn't exist in current `main.py` argparse. |
| 12 | **Low — doc drift, self-correcting once verified** | `.claude/skills/options-suite/SKILL.md`, `CLAUDE.md:160-163` | "~120 lines, chain_evaluation.py unreachable" is stale — `main.py` is 909 lines and its interactive menu genuinely reaches `chain_evaluation.py`/`reports.py`. The headless-mode-only claim still holds; the separate "context mode fails validation" claim was found to be *additionally* stale during review — see Part 1. |
| 13 | **Low — real, reproducible bug** | `Tools/registry.py:66` / `Tools/tools/backtesting_tool.py:52` | Circular import: importing `Tools.tools.backtesting_tool` directly (rather than always going through `Tools.registry` first) raises `AttributeError: partially initialized module 'Tools.tools.backtesting_tool' has no attribute 'TOOL_SPEC'`. Found while running the `dealer_gamma_study` backtest programmatically for this audit (Part 4, Improvement #4) — the dashboard's own normal import order (`Tools.registry` first) never triggers it, which is almost certainly why it hadn't been caught. Anything scripting a Tool directly (as this audit needed to) hits it. |
| 14 | **Informational — test coverage gaps** | Multiple | Options_Suite: BAW, Heston, `vol_manager.py`, `chain_evaluation.py`, `reports.py` have zero test references. VaR_Tools_Simulations: `mc_sim.py`, `forex_var.py`, `cashflow_map.py`, `stress_test.py`, `price_dist.py`, and `main.py`'s context-mode dispatch (the exact "only module 1" behavior every other suite depends on) are untested. **Vol_Suite is the exception, not another gap**: `dealer_positioning.py` and its hedge-requirement math specifically *do* have targeted regression tests (`test_dealer_positioning_sign_model.py`, `test_hedge_requirement.py`) — they're what caught this document's own retracted finding during review (see Part 4 and the CARL ledger below). Check that pair first against any future claimed bug in dealer positioning. |

**Recent, already-fixed issues worth knowing about (not open findings, but useful history):** Options_Suite's CRR Gamma-bump-width bug, Vanna-Volga's RR/BF sign swap, Heston's characteristic-function discriminant bug, and MC's Greek-noise-from-no-CRN issue are all documented in-code as real past bugs with real fixes — evidence of a codebase that has been through genuine numerical debugging, not just written once and left. Similarly, `variance_swap_screener.py`'s README-flagged "realized-vol-silently-becomes-0.0" bug **is already fixed** in current code (verified directly) — the README describing it as unstarted, top-priority work is itself stale.

---

## Part 6 — Operational/architectural findings ("could have been done better")

1. **`swaps.db` is ~320GB and growing unbounded, with no retention/archival policy.** Verified directly: 71M rows in `swap_trades`, 0.0% freelist (not a VACUUM/bloat issue — this is genuinely live data), 8 secondary indexes plus a `raw_json` full-payload column stored per row. A single-file, single-writer SQLite database at this scale faces real operational risk: WAL checkpoint stalls, index-rebuild time, backup difficulty (a 320GB file can't be quickly copied/snapshotted), and continued unbounded growth with every future ingest. **Suggested route**: (a) evaluate whether `raw_json` needs to be retained indefinitely for all 71M rows, or could be archived to cheaper storage after N days once its structured fields have been extracted; (b) consider partitioning by date or regulator (e.g. a rolling "hot" table plus periodic archive tables) rather than one ever-growing table; (c) at minimum, add a documented retention/archival decision now, before this becomes a 1TB+ file.

2. **Three parallel deployment paths (Heroku/Docker/k8s) were fully built for a project that explicitly has no active deployment.** This is real, complete, well-written infrastructure work that's currently pure maintenance burden — it will silently drift out of sync with the actual (local-only) architecture every time something changes, exactly as the auth-removal already demonstrated. **Suggested route**: either commit to keeping one deployment path current (probably Docker, since it's the simplest to keep honest) and delete the other two, or archive all three explicitly as "historical, not maintained" so a future session doesn't waste time debugging a Heroku config nobody uses.

3. **Two independent implementations of the same variance-swap-strike formula exist** (`variance_swap_live.py` and `variance_swap_screener.py`), with only `vrp_term_structure.py` importing the "canonical" one. This is exactly the kind of duplication that produces the kind of drift found in finding #6 above (a fix applied to one copy's neighboring diagnostic never made it to the other). **Suggested route**: extract the formula to one shared module both files import, the same pattern already used correctly for `expiry_selector.py`.

4. **The orchestrator's interactive and CLI/argparse modes duplicate validation logic in two places** (`orchestrator.py`'s `run_interactive_orchestrator` vs. `main`) rather than sharing one `focus`-dict builder — a new focus field added to one path silently doesn't reach the other. **Suggested route**: extract a single `build_focus_from_*` function both entry points call.

5. **`shared/identifiers.py`'s cross-source resolver was built but never wired into the one pipeline (DTCC ingestion) that would populate the columns migration 003 added for it.** The schema exists, the resolver exists, but nothing connects them during actual ingestion — meaning `source_identifier`/`normalized_instrument_id` are dead columns on every row ingested so far. **Suggested route**: either wire `db_loader.py` to call the resolver at insert time, or explicitly document that this is groundwork for a not-yet-built multi-source future and isn't expected to be populated yet (right now it reads as an oversight, not a deliberate phase gate).

6. **The `.claude/` root accumulated 17 point-in-time status/completion docs from one session, none linked from `CLAUDE.md`.** This is a documentation-hygiene pattern worth naming explicitly so it doesn't recur: **completion reports and status snapshots are not living reference docs** — they should either be squashed into a single durable doc (or the commit message) once the work lands, or moved to an explicitly-labeled archive folder, rather than left at the top level where a future session has to figure out on its own that they're historical.

7. **Minor, repo-wide**: several query-building functions (`shared/query_builder.py`, `swaps_query.py`) interpolate `days_back`/`limit` into SQL text via f-string rather than binding them as parameters, while every other value in the same queries is correctly parameterized. Low real risk today (only internal `int`-typed callers), but an inconsistent pattern that's one refactor away from becoming a real injection surface if a caller ever accepts one of these values from a request. **Suggested route**: bind them as `?` params like everything else in the same files — trivial, no behavior change.

---

## CARL summary

**Mode:** SPEC
**Subject:** Full FinancialDevelopment monorepo — architecture/run-book/skills inventory + math/quality audit + Vol_Suite dealer-positioning deep-dive
**Artifact identity:** `docs/PROJECT_AUDIT_AND_SPEC.md`, post-R1-revision version
**Reviewers:** Research — 6 fresh Explore/general-purpose subagents (parallel, research-only). R1 — 1 fresh general-purpose subagent, explicitly instructed to attack the draft adversarially and re-verify claims against source/tests, independent of how the draft was produced.
**Diversity:** reduced — single model family available in this environment (Claude); fresh-context subagents used throughout per CARL's own guidance for this case.
**Rounds:** 2 (research + synthesis, then one adversarial review round with driver-verified disposition on every finding)
**Convergence:** partial — R1's findings are all resolved (5 agreed-and-patched below), but the remaining Part 2/3/6 content still rests on single-pass subagent research that hasn't had its own dedicated adversarial round; the highest-stakes claims (Part 4, Part 5 #1) have now been checked twice, independently, by two different context windows.
**Verdict:** SHIP_WITH_CAVEATS

### Findings ledger (Round 1)

| ID | Severity | Finding | Disposition | Resolution |
|---|---|---|---|---|
| R1-F1 | critical | `dollar_gamma`/`hedge_requirement` "missing spot factor" claim was wrong — units re-derived by hand confirm `hedge_requirement` is correctly a share count, not short a dollar factor; `test_hedge_requirement.py` already regression-locks the correct (non-buggy) formula | AGREE (independently re-derived, not taken on faith) | Retracted from Part 4 and Part 5; replaced with a worked explanation of the error itself |
| R1-F2 | major | Part 1 said Options_Suite `--context` mode "will FAIL" validation; Part 3 (correctly) said current code populates required fields via CRR — the two sections contradicted each other | AGREE (re-read `main.py:130-219` directly, confirms success path is real) | Part 1 corrected to match the verified claim |
| R1-F3 | minor | "Run all 3 conventions, score agreement" recommendation didn't account for v2's structural inability to ever vote positive | AGREE (re-confirmed at `dealer_positioning.py:295-298`) | Recommendation rewritten with the caveat and a concrete fix |
| R1-F4 | minor | "17 stale `.claude/` docs" — actual count is 16 | AGREE (recounted directly) | Corrected in both places |
| R1-F5 | minor | "identical diagnostic" overstated the similarity between `tail_mass` and `range_truncation_score` (related bug class, different formulas) | AGREE | Wording corrected to "related, non-identical" |
| R1-F6 | minor | Test-coverage finding omitted that `Vol_Suite/dealer_positioning.py` — the audit's own centerpiece — has targeted regression tests, which is what would have (and did) catch R1-F1 | AGREE | Added to Part 5 finding, and called out explicitly in Part 4 |

**Zero findings rejected this round.** Per CARL's own calibration-warning rule, that's worth flagging rather than treating as a clean pass — but every one of these six was independently re-derived or re-read against source by the driver before being accepted (unit analysis by hand for R1-F1, direct file reads for R1-F2/F3/F6, direct counts for R1-F4), not accepted on the reviewer's word alone, which is the check CARL actually asks for.

### What's independently verified vs. not, after this round
- **Directly verified by the driver against source, twice** (highest confidence): the `var_agg.py` Euler-allocation bug (Part 5 #1) and the dealer-positioning sign functions (Part 4) — both survived independent re-derivation after the adversarial round targeted them specifically.
- **Directly verified by the driver against source, once, this round**: Options_Suite's `run_context_mode` success path (R1-F2), the `.claude/` file count (R1-F4).
- **Reported by research subagents, not independently re-read**: most of Parts 2, 3, 6, and the remaining Part 5 findings (#2-13). These carry real file:line citations from agents whose other, spot-checked claims held up well under adversarial pressure — but per this repo's own "trust but verify" convention, treat them as strong leads, not closed findings, until checked.

### Material changes from a plain "everything's fine" description
This audit surfaces 12 concrete findings beyond what any existing doc in the repo states (after retracting one that didn't survive review), most notably: a live, currently-displayed VaR calculation bug (high confidence, checked twice); the discovery that several "Phase 1-5 production-ready" docs describe a security/deployment posture later deliberately reversed; and a fully-specified, tested, but completely unwired PDF-report feature.

### Open decisions — resolved 2026-08-04, operator-approved
- ~~Delete `vol-suite.py`/`vol-suite-viewer.py`/`vol-suite-outputs.py`~~ **Done.**
- ~~Archive the 16 stale `.claude/` Phase 1-5 docs~~ **Done** — moved to `docs/archive/2026-07-29-phase-1-5/` with an explanatory README, not deleted.
- ~~Fix the `var_agg.py` Euler bug~~ **Done** — `_component_var` now includes the `positions[i]` term; the two tests that were masking the bug (`test_var_agg.py:77-91,143-147`) rewritten to assert the actual production identity (`Σ component_var == total_var`, no manual reapplication); all 86 `VaR_Tools_Simulations` tests pass.
- **`swaps.db` retention policy: decided and partially implemented** — see `docs/SWAPS_DB_RETENTION_POLICY.md`. 90-day rolling window on `raw_json` (archived to compressed files, then NULLed — rows and structured columns never deleted). Dry-run executed against the real DB: 0 rows currently eligible (the entire 71M-row backfill landed in one ~2.5-hour window on 2026-08-01, confirmed via `MIN/MAX(ingested_at)`), so no data has been touched yet — this is expected, not a bug. `scripts/archive_raw_json.py --execute` is written and dry-run-tested but deliberately not run for real yet, since nulling raw payloads for tens of millions of regulatory-data rows is exactly the kind of hard-to-reverse action worth a separate, explicit go-ahead once there's actually something to archive.
- **Ran the `dealer_gamma_study` backtest — no result, real infrastructure failure.** SPY, default params, 2026-08-04: the ThetaData/PotatoHedge proxy returned `502 Bad Gateway` on a large fraction of requests (83/486 EOD fetches, 54/116 OI-by-day fetches), cascading into 100% of IV/gamma records being dropped and 0 usable trading days. Not a code bug — `backtest_stage3.py` degraded correctly and reported exactly what it could/couldn't fetch. Also surfaced a real, separate bug in the process: a circular import between `Tools/registry.py` and `Tools/tools/backtesting_tool.py` when the tool module is imported directly rather than through the registry (Part 5, finding #13). **The debate in Part 4 remains empirically untested — retry when the vendor proxy is healthier.**
- Still open: a dedicated R2/R3 adversarial pass over Parts 2/3/6, which haven't had the same scrutiny that caught R1-F1 in Part 4.
