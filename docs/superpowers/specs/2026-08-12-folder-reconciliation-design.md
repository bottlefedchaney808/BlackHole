# Folder Reconciliation Design

**Date:** 2026-08-12

**Goal:** Produce a reconciliation-first blueprint for `C:\Users\bottl\FinancialDevelopment` and `C:\Users\bottl\Financial_Development_MIGRATED`, identifying what should remain canonical in the current Windows repo, what should be ported or merged from the migrated WSL-era tree, and what should be archived or ignored.

## 1. Scope and decision rule

This is a **convergence spec**, not a merge-everything exercise. The two folders are not peers in the same state:

- `FinancialDevelopment` is the active Windows repository with live git history, recent dashboard/orchestrator work, cross-source plumbing, adapters, migrations, shutdown/logging/query-monitoring work, and platform-specific launchers.
- `Financial_Development_MIGRATED` is a migrated WSL-era working tree containing useful research, specs, **a standalone `Backtests/` package and additional backtest harnesses**, helper scripts, and some control-plane / Direction-era experiments that were not fully carried forward.

The decision rule for every difference is:

1. **Keep current** when the Windows repo is operationally ahead or already supersedes the migrated material.
2. **Port from migrated** when the migrated tree contains a still-useful asset not present in the current repo.
3. **Merge both** when each side has durable value and neither cleanly supersedes the other.
4. **Archive only** when the material is useful as history/evidence but should not be live code.
5. **Drop** when the difference is environment noise, generated output, or stale experimentation.

## 2. Baseline conclusion

The operational host should remain **`FinancialDevelopment`**.

That conclusion is supported by:

- active git history and ongoing development;
- richer root/platform hardening (`adapters/`, `migrations/`, Docker/deploy assets, shutdown/logging/query-monitoring docs and tests);
- significantly more developed dashboard/test coverage;
- current Windows launcher surface (`.bat` entrypoints, scheduler/orchestrator wrappers);
- existing archive discipline in `docs/archive/` and active specs/plans under `docs/superpowers/`.

The migrated tree is best treated as a **selective source of research and candidate features**, not as an alternative canonical base.

## 3. Inventory map

### 3.1 Root/platform

**Current-only strengths**

- repo metadata and platformization: `.git`, `.gitattributes`, `.python-version`, `.dockerignore`;
- deployment/ops surface: `docker-compose.yml`, `Dockerfile.dashboard`, `Dockerfile.scheduler`, `Procfile`;
- live infrastructure and hardening docs: `docs/guides/CROSS_PLATFORM.md`, `docs/guides/CROSS_SOURCE_ANALYTICS.md`, `docs/guides/CROSS_SOURCE_UPI_GUIDE.md`, `docs/guides/DEPLOY.md`, `docs/guides/DOCKER_SETUP.md`, `docs/guides/GRACEFUL_SHUTDOWN.md`, `docs/guides/LOGGING_GUIDE.md`, `docs/guides/QUERY_MONITORING.md`;
- runtime support code: `shutdown_signal.py`, `report_generator.py`, root tests for cross-source / schema / audit / validation paths;
- source adapters under `adapters/`.

**Migrated-only strengths**

- research / audit / operator notes: `PROJECT_SPEC.md`, `ROOT_SPEC.md`, `HOWTO.md`, `QUICK_REFERENCE.md`, `FULL_AUDIT_2026-08-07.md`, `AUDIT_DELIVERABLE_README.md`;
- WSL-oriented helper surface: `.hermes.md`, `.hermes/`, `findev.sh`, `quant_workspace.sh`;
- experiment and monitoring scripts: `backtest_v5_batch.sh`, `bps_calibration_sweep.sh`, `seed_compare_150.sh`, `staged_m2_pairs.sh`, `staged_seed_compare.sh`, `save_seed_data.sh`;
- extra live helpers: `chart_live.py`, `daily_report.py`, `daily_summary.py`, `sentiment_live.py`, `vol_report.py`;
- archive-like research payloads: `trading_journal/`, `tradingview_watchlists.json`.

### 3.2 Backtests

This is now a **first-class transfer target**, not an archive footnote.

The migrated tree contains a dedicated `Backtests/` package that does not exist in the current repo:

- harness entrypoint: `Backtests/main.py`
- shared evaluation core: `Backtests/core.py`
- focused harnesses:
  - `Backtests/backtest_pricing.py`
  - `Backtests/backtest_greeks.py`
  - `Backtests/backtest_signals.py`
- support modules:
  - `Backtests/data.py`
  - `Backtests/models.py`
- tests:
  - `Backtests/tests/test_core.py`
  - `Backtests/tests/test_greeks.py`
  - `Backtests/tests/test_models.py`
  - `Backtests/tests/test_pricing.py`
  - `Backtests/tests/test_signals.py`

The package is not just output data. It is an organized tournament harness covering:

- pricing-vs-market comparisons;
- first- and second-order Greeks comparisons;
- signal-pack backtests;
- machine-readable and human-readable report generation.

This is materially different from the current repo’s existing `Vol_Suite/backtest_stage3.py` and related suite-local backtests. The current repo already has:

- `Vol_Suite/backtest_stage3.py`
- `Vol_Suite/sentiment_backtest.py`
- `Tools/tools/backtesting_tool.py`
- whale-sign-model backtest design docs

But it does **not** have the migrated tree’s dedicated, reusable `Backtests/` package. The two surfaces are complementary:

- current repo backtests are dealer-positioning / suite specific;
- migrated `Backtests/` is a more general evaluation harness across pricing, greeks, and signals.

That makes this a **merge-both** area with high adoption priority.

### 3.3 Dashboard

**Current repo is decisively ahead**

- many current-only modules: `auth.py`, `job_object.py`, `output_runs.py`, `quant_alerts.py`, `quant_modules.py`, `worker_env.py`, `worker_worktree.py`;
- much larger dashboard test surface;
- changed core templates and route logic (`app.py`, `templates/base.html`, `templates/index.html`, `templates/suite.html`) reflecting current product direction.

**Migrated-only dashboard assets**

- `templates/journal.html`
- `templates/run_files.html`

These look like niche UI surfaces rather than a superior dashboard architecture.

### 3.4 Direction / dealer-positioning layer

Both trees contain the `Direction/` package, but most substantive files differ. The current repo also adds `Direction/tests/__init__.py` and has diverged across:

- `whale_scanner.py`
- `elliott_wave.py`
- `bollinger_analyzer.py`
- `trend_engine.py`
- `liquidity_map.py`
- `signal_generator.py`
- corresponding tests

This aligns with the current repo’s recent dealer-positioning work and makes the migrated tree’s value mostly **research comparison**, not automatic replacement.

### 3.5 Docs / specs

**Current docs**

- stronger archival hygiene in `docs/archive/`;
- active design/plan trail for dashboard, quant console, whale backtest, market-signals fixes;
- current high-level audit docs such as `PROJECT_AUDIT_AND_SPEC.md` and `docs/archive/2026-08-21-root-docs-pass/GAP_VS_OTHER_BUILD_2026-08-07.md`.

**Migrated docs**

- richer recent debate/research materials around dealer-positioning V5, backtest evidence, and battery-style spec bundles;
- additional superpowers specs/plans not copied over;
- standalone `DEALER_POSITIONING_V5_IMPROVEMENT_RECOMMENDATIONS_20260811.md`.

This is one of the clearest **merge-both** areas: current repo has better curation, migrated repo has more raw recent research.

### 3.6 Options_Suite

**Migrated-only assets**

- planning/spec docs (`PLAN_*`, `SPEC.md`);
- `legacy/` quarantine modules;
- tests: `test_active_imports.py`, `test_gpu_parity.py`, `test_heston_lsm_discount.py`;
- `trade_journal.json`.

**Current-only assets**

- live `chain_evaluation.py` and `fetch_options.py` at the active path rather than only under `legacy/`;
- generated comparison artifacts;
- `impliedvol.py`.

**Shared files changed heavily**

- `main.py`, `reports.py`, `vol_manager.py`, pricing model files, tests, wrappers, `thetadata_controller.py`.

Interpretation: current repo is ahead operationally, but migrated repo preserves useful **quarantine/test patterns** that are worth re-adopting selectively.

### 3.7 Vol_Suite

This is a mixed case:

- migrated tree includes many generated analysis artifacts plus additional research/backtest surfaces;
- current repo includes stronger live features such as `instrument_resolver.py`, `strategy_recommender.py`, richer tests, and live output examples;
- core files have diverged broadly (`dealer_positioning.py`, `backtest_stage3.py`, `correlation_engine.py`, `suite_context.py`, `options_chain_scanner.py`, tests).

The migrated-only set is noisy because it includes many generated PNG/CSV artifacts. Most of those should be treated as evidence or archive material, not live code to port.

### 3.8 VaR_Tools_Simulations

**Migrated-only assets**

- `var_engine/backend.py` GPU/CuPy backend layer;
- tests: `test_gpu_parity.py`, `test_greeks_exposure.py`, `test_price_dist.py`;
- `SPEC.md`, `var.sh`.

**Current-only assets**

- `__init__.py`, `tests/conftest.py`.

**Changed shared core**

- `main.py`, `hedge_optimizer.py`, `price_dist.py`, `var_agg.py`, simulation modules, tests.

This is one of the more plausible areas for selective adoption: migrated-only GPU/test infrastructure may still be worth recovering if it matches current goals.

### 3.9 sentiment-scanner

The raw diff is dominated by exported ticker-pack artifacts on both sides. Ignoring generated exports, the meaningful differences are:

- migrated-only `SPEC.md`;
- changed scanner internals (`theta_integration.py`, `youtube.py`, `ticker_pack.py`, scanners, `main.py`);
- current-only `README.md`.

The already-written current-doc audit (`docs/archive/2026-08-21-root-docs-pass/GAP_VS_OTHER_BUILD_2026-08-07.md`) identifies one especially high-value migrated fix: **dead OI wiring in `scanner/theta_integration.py`**.

### 3.10 shared / tests / Tools / scripts / adapters

**shared/**

- current repo has substantial current-only infra: `connection_pool.py`, `context_audit.py`, `data_source.py`, `identifiers.py`, `logging.py`, `query_builder.py`, `query_monitor.py`, `suite_validation.py`, `summary.py`, plus fixtures/tests;
- shared changed files include `config.py`, `schemas.py`, `thetadata.py`, `worker_contracts.py`.

This is a strong **keep current** signal.

**tests/**

- migrated-only tests target dashboard context, orchestrator env/logging, swap schema/security, TradingView watchlists, UI tools, and bridge-related paths;
- current-only tests target connection pool, context audit, cross-source, identifiers, schemas, suite validation, pytest collection, report generation.

This is a **merge-both** opportunity, but only after culling tests that depend on dead or WSL-only workflows.

**Tools/**

- migrated-only `cli.py` and `tools/direction_common.py`;
- current-only `tools/hedge_optimizer_tool.py` and stronger tool tests/README;
- many direction-oriented tool files changed.

This suggests keeping the current tool registry architecture while reviewing whether `direction_common.py` contains reusable code.

**scripts/**

- migrated-only: `burst_checkpoint.sh`, `hooks/commit-msg`, `quant_bridge_watchdog.sh`, `verify_tradingview_submodule.sh`;
- current-only: `archive_raw_json.py`, `quant_alert_check.py`.

This is a meaningful small adoption area: the commit hook and burst checkpoint script look operationally useful.

**adapters/**

- present only in current: `dtcc_adapter.py`, `cme_adapter.py`, `otc_adapter.py`, package init.

This is current-only platform progress and should stay canonical.

## 4. Convergence decisions

### 4.1 Keep current

- root operational host, repo history, and active branch of development;
- dashboard architecture and tests;
- shared infrastructure and adapter layer;
- cross-source / shutdown / logging / validation work;
- current `Tools/` registry shape;
- current Windows launcher surface and current `docs/guides/START_HERE.md` operating model.

### 4.2 Port from migrated

1. **Research and operator docs worth preserving**
   - `DEALER_POSITIONING_V5_IMPROVEMENT_RECOMMENDATIONS_20260811.md`
   - selected debate / evidence / battery specs under `docs/superpowers/specs/`
   - selected plans under `docs/superpowers/plans/`

2. **Useful operational scripts**
   - `scripts/burst_checkpoint.sh`
   - `scripts/hooks/commit-msg`
   - `scripts/verify_tradingview_submodule.sh`

3. **Potentially valuable test coverage**
   - `Options_Suite/tests/test_active_imports.py`
   - `Options_Suite/tests/test_gpu_parity.py`
   - `Options_Suite/tests/test_heston_lsm_discount.py`
   - `VaR_Tools_Simulations/tests/test_gpu_parity.py`
   - `VaR_Tools_Simulations/tests/test_greeks_exposure.py`
   - `VaR_Tools_Simulations/tests/test_price_dist.py`
   - selected root tests around orchestrator env/logging and TradingView watchlists, after validating they still target live code.

4. **Known bugfix candidate**
   - migrated `sentiment-scanner/scanner/theta_integration.py` logic should be compared directly against current and mined for the OI propagation fix already identified by current docs.

5. **Backtests package**
   - `Backtests/main.py`
   - `Backtests/core.py`
   - `Backtests/data.py`
   - `Backtests/models.py`
   - `Backtests/backtest_pricing.py`
   - `Backtests/backtest_greeks.py`
   - `Backtests/backtest_signals.py`
   - `Backtests/tests/*`

### 4.3 Merge both

- docs/spec history: keep current archive discipline but ingest migrated research into `docs/archive/` or `docs/research/` rather than leaving it outside the curated docs tree;
- tests: merge useful migrated regression coverage into the current repo’s better-organized test surface;
- backtests: integrate the migrated `Backtests/` package as a separate reusable harness while keeping current suite-specific backtests in place;
- Direction / dealer-positioning analysis: use migrated materials as evidence inputs, not blind code replacements;
- Options/VaR legacy or GPU layers: review and selectively re-home where current code can benefit.

### 4.4 Archive only

- migrated generated outputs in `sentiment-scanner/data/exports/...`;
- migrated Vol_Suite PNG/CSV output artifacts;
- root experimental logs, snapshots, and zip/bundle payloads;
- WSL-specific control-plane notes that no longer match the current operating environment but are still historically useful.

### 4.5 Drop

- editor/cache noise (`.vs`, cache directories, logs duplicated only as runtime residue);
- duplicated generated comparison/output files when the underlying code already exists in current;
- any migrated helper that only makes sense for the old WSL execution environment and has no surviving workflow owner.

## 5. What is worth adopting

### High-confidence adoptions

1. **Migrate the migrated research/spec corpus into the current repo’s curated docs tree.**
   The current repo is missing a meaningful chunk of recent dealer-positioning and battery-analysis reasoning that should be preserved alongside active work.

2. **Transfer the standalone `Backtests/` package into the current repo.**
   This is the clearest code-level adoption target: it is organized, tested, and fills a capability gap the current repo does not currently cover with a dedicated package.

3. **Recover the migrated script hygiene that still fits current workflow.**
   `scripts/burst_checkpoint.sh` and `scripts/hooks/commit-msg` are low-risk, high-value operational assets.

4. **Port the best migrated regression tests.**
   Several migrated tests cover exactly the kinds of math/regression drift that this codebase is vulnerable to.

5. **Extract the already-identified sentiment OI fix from migrated code.**
   This is the clearest example of a migrated improvement that current docs already regard as a real regression.

### Medium-confidence adoptions

1. **Review migrated `legacy/` quarantine patterns in Options_Suite.**
   The idea is good, but the current repo’s live wiring differs, so this should be adapted rather than copied wholesale.

2. **Review how the migrated `Backtests/` package should integrate with current `Tools/` and suite outputs.**
   The package should transfer, but its final home, launchers, and data-contract wiring should be designed deliberately.

3. **Review migrated GPU/backend work in VaR_Tools_Simulations.**
   Worth adopting only if GPU execution is still a real requirement and the code matches current interfaces.

4. **Review `Tools/cli.py` and `tools/direction_common.py` for reusable abstractions.**
   Useful only if they reduce duplication in the current tool layer.

### Low-confidence or likely-not-worth-adopting

1. **WSL-era root control-plane sprawl as live code.**
   `findev.sh`, `quant_workspace.sh`, and `.hermes`/`.hermes.md` are valuable as reference, but not as direct imports into the current Windows operational path.

2. **Bulk generated artifacts.**
   These are evidence, not code.

3. **Blind replacement of Direction / Vol_Suite / Options / VaR core files.**
   Both trees have moved independently. Direct overwrite would destroy newer current work.

## 6. Reconciliation sequence

### Phase 1 — Documentary capture

1. Inventory migrated-only docs/specs/plans into a curated ledger.
2. Copy high-value migrated docs into `docs/archive/` or a dedicated research folder in `FinancialDevelopment`.
3. Label each imported doc as historical research, evidence, or active reference.

### Phase 2 — Backtests transfer

1. Transfer `Backtests/` code and tests into the current repo as a dedicated package.
2. Exclude `Backtests/outputs/` from the live transfer except for a small curated evidence sample, if desired.
3. Rewire imports and launcher assumptions from WSL paths / env scrubbing to the current repo conventions.
4. Decide whether the package should remain root-level as `Backtests/` or be renamed/lowered into a more general evaluation namespace.

### Phase 3 — Low-risk workflow adoption

1. Review and port `scripts/burst_checkpoint.sh`.
2. Review and port `scripts/hooks/commit-msg`.
3. Review and port `scripts/verify_tradingview_submodule.sh` if the current repo still treats `tradingview-mcp` as a tracked submodule workflow.

### Phase 4 — Test-surface recovery

1. Compare migrated-only tests against current interfaces.
2. Port tests that still match live code with minimal adaptation.
3. Skip tests that only target dead WSL-only launch paths or superseded APIs.

### Phase 5 — Targeted code harvest

1. Diff migrated vs current `sentiment-scanner/scanner/theta_integration.py` and port the OI fix.
2. Review migrated `VaR_Tools_Simulations/var_engine/backend.py` for recoverable GPU abstractions.
3. Review migrated `Options_Suite/legacy/` and quarantine/test patterns for selective reuse.
4. Review `Tools/direction_common.py` for reusable shared logic.

### Phase 6 — Evidence-backed research reconciliation

1. Compare migrated dealer-positioning research docs against current Direction / Vol_Suite implementation and audits.
2. Promote only evidence-backed findings into live code or active docs.
3. Archive the rest as reference material rather than letting it influence production paths implicitly.

## 7. What is not worth doing

- Do **not** attempt a folder-wide merge or file overwrite between trees.
- Do **not** import generated outputs into live source paths.
- Do **not** treat migrated WSL environment conventions as superior by default; current Windows infrastructure is more mature.
- Do **not** reintroduce migrated experimental dealer-positioning code without verifying it against the newer current repo and its recent audits.
- Do **not** transfer only the `Backtests/outputs/` artifacts without the package code and tests that make them reproducible.

## 8. Final recommendation

Use `FinancialDevelopment` as the sole canonical working repo and execute reconciliation as a **harvest-and-curate program**, not a merge.

The migrated tree is valuable primarily for:

- missing research/spec history,
- the standalone `Backtests/` package and its tests,
- a handful of useful workflow scripts,
- targeted regression tests,
- selected bugfix logic already known to be better in at least one area (`theta_integration.py`),
- and historical evidence for dealer-positioning work.

It is **not** valuable as a replacement base for the current platform, dashboard, shared infrastructure, or broad suite implementations.
