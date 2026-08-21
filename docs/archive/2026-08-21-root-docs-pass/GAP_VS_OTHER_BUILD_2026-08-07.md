# Gap analysis: this repo vs. the other `Financial_Development` build

**Date:** 2026-08-07 · **Source:** `Financial_Development_Audit_Deliverable_2026-08-07.tar.gz` /
`Financial_Development_All_Specs_2026-08-07.zip` (both user-supplied), containing an independent
audit of a separate, related environment (`/home/bottl/Financial_Development`, Linux) at its own
commit `5a010c2` — a commit that does not exist in this repo's history, confirming it's a genuinely
diverged sibling build, not an ancestor/descendant of this one.

**Method:** five parallel read-only agents, one per domain (root/shared/dashboard/DTCC, Vol_Suite,
Options_Suite, VaR_Tools_Simulations, sentiment-scanner), each read the other build's audited
`SPEC.md` for that domain and cross-checked every claim against this repo's actual current source
— not against CLAUDE.md's description of itself.

Each section below is ranked by what matters most: **missing entirely** first (the other build has
it, we don't), then **weaker/regressed**, then **equivalent**, then **ours-only**.

---

## Root / shared / orchestration / dashboard / Tools / DTCC

**Missing entirely**
- **`Direction/` package** — doesn't exist anywhere in this repo. No `whale_scanner.py`,
  `elliott_wave.py`, `bollinger_analyzer.py`, `trend_engine.py`, `liquidity_map.py`,
  `signal_generator.py`, no Direction tests. (This repo's only trace of it is the *unmerged*
  external `Dealer_pos_devnotes.tar.gz`, and only the whale leg has been backtest-ported so far —
  see Vol_Suite section.)
- **`tradingview-mcp` submodule** — no `.gitmodules` file at all in this repo; no TradingView/CDP
  wiring of any kind.
- **Quant bridge / worker broker / synthesis / interpretation control plane** —
  `quant_bridge.py`, `worker_broker.py`, `quant_synthesis.py`, `quant_interpret.py` are all absent.
  This is a whole architectural layer (a Hermes-worker control plane) the other build has that this
  repo's `CLAUDE.md` doesn't mention in any form.
- **`chart_live.py`** (SSE loopback chart server) and **`live_dashboard.py`** (Rich/ThetaData TUI)
  — both absent.

**Weaker / regressed relative to the other build**
- **`Tools/registry.py`** has only 2 adapters (`options_strategy_tool.py`, `backtesting_tool.py`)
  vs. the other build's 8 — consistent with the missing `Direction` package, since 6 of their 8
  adapters wrap Direction signals.
- **`shared/config.py::load_env_once`** has the same not-actually-once-per-process defect the other
  build's audit flagged (C3) — a `None` default that never persists across calls. Present in both,
  fixed in neither.

**Equivalent**
- Orchestrator (argv-only subprocess launches, `suite_context.json` handoff), `shared/cache.py`,
  `shared/thetadata.py`, `Tools/context_loader.py`, `dashboard/app.py`, DTCC ingestion/query layer.
- Same orphan-gitlink hygiene issue as their C9, just for `mcp-stockflow` instead of
  `tradingview-mcp` — a `git ls-tree` gitlink with no `.gitmodules` mapping.

**Deliberate divergence, not a gap**
- **Dashboard auth**: this repo's `dashboard/auth.py` explicitly documents that
  `DASHBOARD_API_KEY` middleware **was removed on purpose** for the localhost-only single-user
  design (see `CLAUDE.md`). The other build still has optional `DASHBOARD_API_KEY` middleware. This
  repo's docs correctly describe the no-auth state; the other build's own audit flagged its docs as
  stale/contradictory on this point (their C4) — so this repo is arguably in better shape here, not
  worse.

**Present here, not in their spec**
- `sentiment-scanner`, `VaR_Tools_Simulations`, `Options_Suite`, and `Vol_Suite` all exist here as
  full independent suites; the other build's `ROOT_SPEC.md` only references an "options-strategy
  adapter," not full suites for any of these — suggesting the other build's root layer is scoped
  narrower than this repo's, even though it's ahead on the Direction/TradingView/bridge layer.

---

## Vol_Suite

**Missing entirely**
- **Live `direction`/`oi_flow` sign models.** `dealer_positioning.py`'s `VALID_SIGN_MODELS` here
  has only 3 entries (`oi_heuristic`, `replication`, `vol_surface_replication`); the other build has
  5, including `direction` (a live Direction-package bias) and `oi_flow`. This is the concrete,
  code-level confirmation of the CARL-reviewed decision already recorded in this repo: port the
  whale-flow leg into a backtest only, don't wire live sign models on unvalidated research.
- **Whole analysis files absent**: `pooled_panel_backtest.py` (fixed-effects/permutation pooled
  regression), `sign_sensitivity.py` (dead-band sensitivity report), `run_sp500_backtest_sweep.py` /
  `agg_year_scan.py` / `scan_year_run.py` (S&P500 batch-sweep harness), `strategy_payoff_charts.py`,
  `dataquery.py`. None exist in this repo's `Vol_Suite/` — the other build's spec describes these as
  load-bearing for statistical interpretation of backtest results, and their absence here is exactly
  why `docs/PROJECT_AUDIT_AND_SPEC.md`'s dealer-gamma-sign work has stayed single-ticker /
  small-batch rather than a proper pooled cross-ticker panel.

**Weaker / regressed**
- Same `variance_swap_screener.py:136-146` downward-biasing defect (missing quotes filled with
  `0.0`, then treated as finite) exists in both builds, unfixed in either.

**Equivalent**
- Core architecture, `suite_context.py` contract, `expiry_selector.py`, `correlation_engine.py`,
  `garch_analysis.py`, `vrp_term_structure.py`, `vol_surface_2d.py`/`vol_surface_reference.py`,
  `options_chain_scanner.py`, `variance_swap_live.py`, `sentiment_backtest.py`.

**Present here, not in their spec** (this repo is ahead here)
- `whale_scanner.py` + `backtest_stage3.py`'s whale (4th) column and `v3`
  (`vol_surface_replication_weighted`) backtest-only experiment — today's work, not in their tree at
  all.
- `strategy_recommender.py`, `instrument_resolver.py`, and several tests
  (`test_context_mode.py`, `test_end_to_end.py`, `test_whale_scanner.py`, others).

---

## Options_Suite

**Missing entirely**
- **No `legacy/` quarantine.** The other build moved superseded duplicate modules into a `legacy/`
  directory with an isolation-guard test (`tests/test_active_imports.py`, an AST-scan CI control).
  This repo deleted the equivalent files outright instead — same end state for the active code path,
  but no test locks in "these files must not be reachable."
- **No GPU parity test** (`tests/test_gpu_parity.py`) despite having the same CuPy `GPU_ACTIVE`
  backend (`MC.py`, reused by `MCHestonLSM.py`) — that code path has zero coverage here.
- **No `tests/test_heston_lsm_discount.py`** — nothing regression-locks the Heston discriminant fix
  this repo independently made.

**Weaker / different**
- `main.py` here is 909 lines and *does* interactively wire `chain_evaluation.py`/`reports.py`
  (menu choices 9/10) — the other build quarantined `chain_evaluation.py` into `legacy/` entirely.
  This is actually more-wired than the other build for interactive use, but doesn't change the
  orchestrator path: `run_context_mode` never touches `chain_evaluation`/`reports` in either build,
  so headless/orchestrator runs are equally thin in both.
- Same unsandboxed context-out write (`main.py`'s `_resolve_context_out_path` — no traversal/symlink
  rejection) and same non-canonical Vanna-Volga model (`VannaVolga.py` self-labels "3-pillar" but
  implements inverse-distance interpolation, not a true 3x3 replication) — present, unfixed, in both.

**Equivalent**
- CRR/LR trees, BAW, plain + Heston MC LSM, SABR, both IV solvers — same models, same dispatch
  pattern, same CRR risk-neutral-probability clipping defect in both.

**Present here, not in their spec**
- `impliedvol.py` — imports from an external `optionmodels` package, unreferenced by `main.py` or
  anything else. Looks orphaned; candidate for the same `legacy/` quarantine treatment the other
  build already applied to its own dead code.

---

## VaR_Tools_Simulations

**Missing entirely**
- **No GPU/CuPy backend.** The other build has a full `backend.py` (NumPy/CuPy dispatch, force-CPU
  env checks, device probing/fallback) consumed by `corr_sim.py`, `mc_sim.py`, `copulas.py`. This
  repo's equivalent modules have zero GPU references at all — a full architectural layer absent, not
  a stub.
- **No GPU parity tests** (`tests/test_gpu_parity.py`) — follows from the above.
- **`hedge_optimizer.py::export_greeks_exposure`** — the other build's Greeks-exposure export API
  (and its associated symlink-following vulnerability, their C6) doesn't exist here at all. Net
  effect: their C6 finding is moot in this repo since the vulnerable path isn't present, but so is
  the feature.
- **`tests/test_greeks_exposure.py`, `tests/test_price_dist.py`** — missing, consistent with
  `CLAUDE.md`'s already-documented test gaps for this suite.

**Equivalent (including shared bugs)**
- All 9 `var_engine/` modules present with matching responsibilities and close line counts.
- `price_dist.py`'s barrier reflection-sign bug and `mc_probabilities`'s hardcoded
  `between_any = 1.0` reproduce identically in both builds.
- `hedge_optimizer.py`'s `hedge_independent` no-op (both branches build the same diagonal
  covariance) reproduces identically.
- `run_context_mode`'s "module 1 (corr_sim) only" limitation is **the same partial-consumer state
  in both builds** — not a regression here, a shared unfinished state.

**Present here, not in their spec**
- A 10th main-menu entry directly wiring Hedge Optimizer into `MODULES`, where the other build
  accesses it separately from the main registry.

---

## sentiment-scanner

**Weaker / regressed relative to the other build — the one real regression found in this sweep**
- **`scanner/theta_integration.py` is at the *older, broken* state.** This repo's version (59 lines)
  hardcodes `oi_change_pct`/`call_oi`/`put_oi`/`total_oi`/`otm_oi_ratio`/`put_call_oi_ratio` all to
  `0`. The other build's audit explicitly describes this exact "dead OI wiring" bug as **already
  fixed** in their current `theta_integration.py` (99 lines). Compounding it, `main.py:448` here
  calls `engine.correlate_with_oi(ticker, {})` with an empty dict regardless, so OI-driven signal
  rules can't fire either way right now. **This is the one item in the whole sweep worth prioritizing
  as an actual fix**, not just a feature gap — it's a real bug the sibling environment already solved.

**Missing entirely**
- Targeted regression suites `test_oi_snapshot.py`, `test_youtube_bugs.py`, `test_gex_scanner.py` —
  none exist here, so none of the shared C1–C4/S1/S3 bugs below are test-guarded in this repo.

**Equivalent (including shared, unfixed bugs)**
- Core StockTwits → 6 scanners → `CorrelationEngine` pipeline.
- Max Pain wrong-direction bug (`np.argmax` against a buyer-payout array), process-local OI baseline
  lost on restart, and the `group_name` path-traversal gap in `ticker_pack.py` all reproduce
  identically, unfixed in both.
- `scanner/reddit.py` and `scanner/swap_sdr.py` are stubs in both builds.

**Present here, not in their spec (this repo is ahead here)**
- `report.py`, `sector_rotation_launcher.py`, and `earnings_scanner.py` are **live-wired** into
  `main.py` here (7 scanners including earnings, PDF reporting, interactive sector rotation) — the
  other build's spec describes all three as not imported by its live orchestrator, i.e. only 6
  scanners actually run there. This repo's `CLAUDE.md` "7 scanners" claim is accurate; theirs isn't.
- `scanner/youtube.py` here is architecturally different (in-process `yt_dlp`, not a generated
  subprocess script) — the other build's S1 script-injection finding doesn't directly transfer and
  would need its own separate review, not an assumption either way.

---

## Cross-cutting takeaways

1. **The other build's edge is almost entirely the Direction/TradingView/bridge layer** — a 5-signal
   research package wired live into dealer sign resolution, a TradingView/CDP submodule, and a whole
   Hermes-worker control plane (`quant_bridge`/`worker_broker`/`quant_synthesis`/`quant_interpret`).
   None of it is a small gap; it's a different scope of ambition for that part of the system. Whether
   to chase it is a real product decision, not a bug list — and this repo's own audit already found
   good reason for caution on the Direction/whale-flow piece specifically (see
   `docs/superpowers/specs/2026-08-06-whale-sign-model-backtest-design.md`: the imported numbers
   don't survive proper multiple-comparisons correction).
2. **Most "missing" items elsewhere are missing *analysis tooling*, not missing *correctness*** —
   `pooled_panel_backtest.py`, GPU backends, sensitivity sweeps. The actual pricing/VaR/scanner math
   this repo does implement tracks the other build closely, bug-for-bug in several places (Max Pain,
   variance-swap screener, hedge_optimizer no-op, price_dist reflection sign) — these are shared
   defects inherited from a common ancestor, not divergent quality.
3. **One genuine regression, not just a gap**: `sentiment-scanner/scanner/theta_integration.py`'s
   dead OI wiring. The other build already fixed this; porting that fix (not re-deriving it) is the
   highest-value single change out of this whole comparison.
4. **This repo is ahead in a few concrete spots**: today's whale-flow backtest port (Vol_Suite),
   sentiment-scanner's 7-live-scanner wiring (report/earnings/sector-rotation all live here vs. 6
   there), and Options_Suite's interactive `chain_evaluation`/`reports` wiring.
