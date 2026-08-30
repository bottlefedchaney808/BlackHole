# Jump-Diffusion Model Zoo (Vol_Suite, Phase 1 of "AIGamma steal-list") — Design

Date: 2026-08-30
Status: Draft — awaiting Jason's review (will be run through `carl` before implementation)

## Purpose

`docs/../Trading/AIGamma teardown - what to steal.md` (Obsidian vault, 2026-08-29) is Jason's
ranked list of presentation and modeling ideas worth porting from aigamma.com. His explicit,
named pick was "the entire /jump section — steal ALL of it": Variance Gamma, Heston, Bates SVJ,
Kou, and Merton jump-diffusion models, priced via Lewis (2001) characteristic-function inversion
and calibrated to the observed smile via Nelder-Mead in IV space.

This is **Phase 1 of a three-phase program**:
1. **This spec** — the jump-diffusion model zoo, living in Vol_Suite (not Options_Suite — Jason
   wants it tied into dealer positioning, VRP, the strategy recommender, and GARCH, which are all
   Vol_Suite concerns; Options_Suite already has its own separate Heston MC / SABR pricing path
   and is not touched here).
2. Phase 2 (future spec) — Breeden-Litzenberger risk-neutral density + Durrleman g(k) arbitrage
   diagnostic, extending `Vol_Suite/svi_rp.py`.
3. Phase 3 (future spec) — the Tier 1 presentation patterns (methodology footers, Bounded Gamma
   Index badge, ghost bars, symlog axis, near-flip pill, overnight alignment score) as dashboard
   widgets, some but not all reusing the widget pattern from `2026-08-28-overview-widgets-design.md`.

Phases 2 and 3 are noted here only to keep this spec's scope honest; they are not designed in
this document.

## Current state (verified)

- **Vol_Suite has no jump-diffusion or characteristic-function pricing today.** Confirmed by
  directory listing (`Vol_Suite/*.py`) — closest existing relatives are `garch_analysis.py`
  (historical-measure GARCH(1,1), t-distributed innovations, via the `arch` package),
  `svi_rp.py` (SVI/SSVI smile fits — `calibrate_svi`/`calibrate_ssvi`, arbitrage-free parametric
  fits, not a pricing model), and Options_Suite's separate Heston Monte Carlo (LSM), which this
  spec does not touch or replace.
- **`Vol_Suite/volatility_suite.py::_run_core_analysis`** (`volatility_suite.py:948`) is the
  per-ticker analysis pipeline every suite run/`--unified` pass drives. It already calls
  `garch_analysis.run_garch_module(ticker, output_dir=out_root)` (`volatility_suite.py:1261`),
  stores the result at `artifacts["garch_conditional_vol"]` (`:1281`), and that value flows
  straight into `suite_context.py::build_suite_context(garch_conditional_vol=...)`
  (`volatility_suite.py:1644`) → `suite_context.json`'s `focus.garch_conditional_vol` field. This
  is the exact call-site pattern the new module's default-model path will follow.
- **`garch_analysis.run_garch_module`** (`garch_analysis.py:349`) returns a `GarchModuleResult`
  3-tuple `(output_files, interpretation_text, annualized_conditional_vol)` plus an out-of-band
  `.error` attribute. It fits a **plain** GARCH(1,1) via `arch_model(log_returns, vol="Garch",
  p=1, q=1, mean="Constant", dist="t")` (`garch_analysis.py:128`) on raw log returns — no jump
  filtering, no exogenous regressor. The `arch` package's stock `arch_model` does not expose a
  variance-equation exogenous term out of the box; a GARCH-X variant needs either a custom
  `VolatilityProcess` subclass or a hand-rolled variance-equation fit. Exact implementation is a
  plan-level detail, flagged here as real work, not hand-waved.
- **`Vol_Suite/suite_context.py::build_suite_context`** (`suite_context.py:109`) is the single
  place `suite_context.json` gets assembled; schema is `schema_version: 1`, validated by
  `shared/schemas.py::validate_suite_context` (`shared/schemas.py:107`) — note this is a
  *different function* from `Vol_Suite/suite_context.py`'s own internal
  `validate_suite_context` (`suite_context.py:210`), the known naming collision CLAUDE.md already
  flags. Both need updating if the new context block affects them (Options_Suite/VaR consumers go
  through `shared/schemas.py`'s validator; `build_suite_context` self-validates via its own
  module's function before writing).
- **Downstream consumers confirmed real and separately owned:**
  - `Vol_Suite/strategy_recommender.py::StrategyRecommender` (`strategy_recommender.py:43`) takes
    `chain_data`, `edge_strikes`, `vol_regime` ('RICH'/'CHEAP'/'FAIR'), `current_price`,
    `expiry_days` in its constructor and returns ranked strategy dicts from `.recommend()`. It has
    no jump-risk input today — vol_regime comes purely from smile-fit rich/cheap classification.
  - `Vol_Suite/vrp_term_structure.py::compute_vrp_term_structure` (`vrp_term_structure.py:117`)
    takes `(ticker, td, spot, r, q)` and returns a `VrpTermStructureResult` (list of
    `VrpTermPoint`: fair_vol_pct, atm_iv_pct, vrp_pct, rv_30d_pct per tenor) computed from Carr-
    Madan static replication (`variance_swap_live.compute_fair_variance_strike`). No jump-model
    cross-check exists today.
  - `Vol_Suite/expiry_book_production.py::fetch_production_result` (`expiry_book_production.py:201`)
    is the live dealer-book entry point (per CLAUDE.md's "Live dealer model" note) — 4-panel
    GEX/VEX/CEX. No jump-risk-share overlay exists today.

## Scope

**In scope (this spec, Phase 1):**
- New package `Vol_Suite/jump_diffusion/` implementing 5 characteristic-function models (VG,
  Heston, Bates SVJ, Kou, Merton) behind one shared interface, priced via Lewis (2001)
  single-integral inversion, calibrated per expiry slice via Nelder-Mead in IV space.
- Two-tier execution: a comparison mode (all 5 models, for manual "which do I like" evaluation)
  and a unified-pipeline mode (one configured default model only, for speed).
- A new `jump_diffusion` block in `suite_context.json`, populated by the default-model path.
- Three downstream ties, each additive (existing behavior unchanged when the new data is absent):
  dealer positioning (`expiry_book_production`), VRP term structure cross-check
  (`vrp_term_structure`), strategy recommender input (`strategy_recommender`).
- Two GARCH ties in `garch_diffusion`/`garch_bridge`: jump-day filtering before the GARCH fit, and
  jump-variance-share as a GARCH-X exogenous regressor (both described in Architecture below).

**Out of scope (this spec):**
- Phases 2 and 3 (SVI density/arb diagnostic; presentation widgets) — separate specs.
- Any change to Options_Suite's existing Heston MC / SABR / CRR / Leisen-Reimer pricing.
- Barrier/exotic pricing (Kou's closed-form barrier formulas exist in the literature but are not
  requested or built here).
- A dashboard-facing UI for any of this — Phase 1 output lives in `suite_context.json` and
  Vol_Suite's own artifact/report output only.

## Model guide (per-model "what to use when")

This becomes the reference doc for whichever config/UI eventually lets a user pick a model
(Phase 3 territory, but worth fixing now since it drives the Phase 1 default and the GARCH-filter
model choice):

| Model | Params | Best for | Weak at |
|---|---|---|---|
| **Variance Gamma (VG)** | σ, ν, θ (3) | Cheapest/fastest calibration — quick baseline or frequent slices | No stochastic vol → misses term-structure evolution |
| **Heston (Lewis CF)** | κ, θ, ξ, ρ, v0 (5) | Free closed-form cross-check against Options_Suite's existing Heston MC | No jump term → underfits short-dated steep wings |
| **Bates SVJ** | Heston's 5 + λ, μ_J, σ_J (7) | Full diffusive-vol-vs-jump-risk decomposition — feeds GARCH-X and dealer-book jump-variance-share | Most expensive to calibrate |
| **Kou double-exponential** | p, η1, η2, λ (+ constant vol) | Asymmetric tail risk (SPX-style crash≫melt-up) | Flat vol → weak term structure |
| **Merton jump-diffusion** | λ, μ_J, σ_J (+ constant vol) | Cheap, simple jump-day *detector* — feeds the GARCH jump-filter step | No stochastic vol; least accurate full-smile fit |

Recommended defaults: **Bates** as the `JUMP_MODEL_DEFAULT` (feeds dealer/VRP/recommender/GARCH-X
in unified runs), **Merton** as the fixed jump-detector for the GARCH filtering step (cheap and
purpose-built for "which days had a jump", independent of whatever the default valuation model is).

## Architecture

### Package layout: `Vol_Suite/jump_diffusion/`

Not added to `volatility_suite.py` directly — that file is already ~1850 lines (CLAUDE.md flags
it as the suite's largest file) and this is a self-contained concern, matching the existing
`expiry_book_production.py`/`expiry_book_exposure.py` split-out pattern.

- **`models.py`** — one dataclass + characteristic-function (`phi(u, T, params)`) per model: VG,
  Heston, Bates, Kou, Merton, behind a shared `JumpDiffusionModel` protocol (`name`, `params`,
  `phi(u, T)`, `param_bounds`). Each model's blurb from the table above becomes its class
  docstring.
- **`pricer.py`** — `lewis_price(model, S, K, T, r, q) -> float`, the Lewis (2001) single-integral
  CF-to-price inversion, shared by all 5 models (this is the one piece of real numerical work that
  isn't model-specific).
- **`calibration.py`** — `calibrate(model_cls, chain_slice, spot, r, q) -> CalibrationResult`
  (params, RMSE in IV space, per-strike observed-vs-fit). Nelder-Mead (`scipy.optimize.minimize`,
  method="Nelder-Mead") minimizing sum-of-squared IV errors across the slice, seeded from
  reasonable priors per model (e.g. v0 seeded from ATM IV², κ/ξ from typical equity index
  literature values as a starting point — exact seeding is an implementation-plan detail).
- **`comparison.py`** — `run_comparison(ticker, expiry, chain_slice) -> dict` — calibrates all 5,
  returns per-model params + RMSE + observed-vs-fit arrays, plus the Heston-vs-Bates dashed
  "visible jump contribution" comparison Jason called out from the aigamma teardown. Called from
  Vol_Suite's interactive menu (new entry) or an explicit CLI flag — **not** part of
  `_run_core_analysis`, so it never runs during a unified pass.
- **`garch_bridge.py`** — the two GARCH ties:
  1. `jump_filtered_returns(log_returns, jump_model=Merton, ...) -> pd.Series` — calibrates Merton
     per rolling window (or per historical day, matching the existing 251-day book cadence) to
     flag jump-attributable return days, then either winsorizes or excludes them before the
     existing `garch_analysis.run_garch_analysis` call — output is a *cleaner* return series fed
     into the *existing* GARCH fit, not a new GARCH implementation.
  2. `jump_variance_share_regressor(bates_result) -> float` — extracts Bates' jump-variance share
     (jump-attributable variance ÷ total variance) as the exogenous input for a GARCH-X variance
     equation. Since `arch_model` has no built-in variance-equation exogenous term, this requires
     either a custom `arch.univariate.volatility.VolatilityProcess` subclass or a hand-rolled
     GARCH-X log-likelihood fit — flagged as real implementation work, resolved in the plan, not
     here.
- Hooks into **`volatility_suite.py::_run_core_analysis`**: one new call, same shape as the
  existing GARCH call site (`:1261`) — calibrate only `JUMP_MODEL_DEFAULT` (config value,
  defaulting to `"Bates"`) against the focus ticker's near-term chain slice, store result in
  `artifacts["jump_diffusion"]`, pass into `build_suite_context`.
- Hooks into **`Vol_Suite/suite_context.py::build_suite_context`**: new optional kwarg
  `jump_diffusion: Optional[Dict[str, Any]] = None`, written into context as a new top-level
  `"jump_diffusion"` key (params, RMSE, jump_variance_share, model_name) — additive, so
  `schema_version` stays at 1 per the existing "additive fields don't bump schema_version"
  convention implied by `garch_conditional_vol`'s own addition; if that convention is wrong,
  `shared/schemas.py::validate_suite_context` and `Vol_Suite/suite_context.py`'s own validator
  both need the new key added to their accepted-keys check — this is a two-file update, not one,
  per the CLAUDE.md-documented naming collision.

### Downstream tie call shape (each additive, each optional)

- **Dealer positioning**: `expiry_book_production.fetch_production_result` gains an optional
  `jump_variance_share: Optional[float] = None` parameter, surfaced as a new scalar tile alongside
  GEX/VEX/CEX — not summed into any existing exposure number (same "stays adjacent, never summed"
  rule CLAUDE.md already documents for the 7d vendor-ΔIV vanna flow).
- **VRP term structure**: `compute_vrp_term_structure`'s result gains an optional
  `model_implied_vrp_pct: Optional[float] = None` per `VrpTermPoint`, computed from the default
  model's total implied variance at that tenor, for comparison against the existing Carr-Madan
  fair_vol_pct — a cross-check column, not a replacement.
- **Strategy recommender**: `StrategyRecommender.__init__` gains an optional
  `jump_risk_signal: Optional[dict] = None` (jump_variance_share, model_name) that
  `_select_strategies_for_regime` can use as a secondary input (e.g. bias toward convexity-buying
  legs when jump-variance-share is elevated) — additive to the existing vol_regime-driven
  selection, not a replacement for it.

## Two-tier execution (compute cost)

Calibrating 5 CF models via Nelder-Mead per expiry slice is expensive — each iteration
re-evaluates a Fourier inversion across every strike. Paying that cost on every unified run for
output only one model's worth of downstream consumers ever read is waste. So:
- **Comparison mode** (`jump_diffusion.comparison.run_comparison`): all 5 models, invoked
  explicitly (Vol_Suite interactive menu or CLI flag) — this is where Jason evaluates "which do I
  like better" per Jason's own reasoning in-session.
- **Unified/context-mode runs**: only `JUMP_MODEL_DEFAULT` (config, default `"Bates"`) is
  calibrated, keeping `_run_core_analysis` fast. Change the config value once comparison mode has
  informed the choice.

## Error handling

Matches the existing GARCH call site's pattern (`garch_analysis.py`'s `GarchModuleResult.error`,
`volatility_suite.py`'s handling around `:1258-1281`): calibration failure (non-convergent
Nelder-Mead, insufficient chain liquidity for a slice, etc.) must not abort the unified run.
`artifacts["jump_diffusion"] = None` and `suite_context.json`'s `jump_diffusion` key is `null`,
same null-vs-omitted discipline the `var.positions` field already documents as load-bearing —
downstream consumers (dealer positioning, VRP, recommender, GARCH-X) each treat `None`/`null` as
"feature not available this run," falling back to their current (pre-Phase-1) behavior, never
raising.

## Testing

- `Vol_Suite/tests/test_jump_diffusion_pricer.py` — Lewis inversion round-trip: price a European
  call analytically known in closed form under degenerate parameter choices (e.g. Heston with
  ξ→0 should converge to Black-Scholes; Merton with λ=0 should converge to Black-Scholes) and
  assert convergence.
- `Vol_Suite/tests/test_jump_diffusion_calibration.py` — calibrate each of the 5 models against a
  synthetic smile generated from known parameters and assert recovered params are close (within
  calibration tolerance) — the standard "calibrate what you priced" round-trip test.
- `Vol_Suite/tests/test_garch_bridge.py` — jump-filtered returns series has no NaN/inf, excludes
  the correct flagged days on a synthetic return series with an injected known jump; GARCH-X
  regressor extraction returns a value in [0, 1].
- Extend `tests/test_orchestrator_market_signals.py` or add a Vol_Suite-local equivalent to assert
  `suite_context.json`'s new `jump_diffusion` key round-trips through `build_suite_context` →
  `validate_suite_context` (both flavors) → consumers reading `None` gracefully when calibration
  was skipped/failed.
- Manual: run `volatility_suite.py` unified pass on SPY, confirm `jump_diffusion` block appears in
  `suite_context.json`, confirm dealer-book/VRP/recommender outputs are unchanged when the new
  optional params are omitted (backward-compat check on existing call sites).
