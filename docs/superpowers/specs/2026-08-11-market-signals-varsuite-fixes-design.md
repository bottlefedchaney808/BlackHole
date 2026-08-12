# Market Signals / VaR / Vol Suite data-plumbing fixes + Tools additions

Date: 2026-08-11
Status: Approved (pending spec self-review)

## Problem

The dashboard's "Market Signals" block (labeled from the `sentiment` suite output) and the
VaR Tools MC/corr sims have several data-plumbing defects reported by the user during live use:

1. Max Pain scanner ignores the run's context expiry, self-selecting nearest ~30DTE instead.
2. "fair vol %" — confirmed already correctly populated from `vol_result.json`; no fix needed.
3. "GARCH conditional vol %" is computed by Vol_Suite but only printed to console — never
   written to `suite_context.json`/`vol_result.json`, so nothing downstream can read it.
4. MC sim volatility falls back to a hardcoded `0.25` whenever `estimate_garch_vol()` fails,
   which happens more often than it should because failures are silently swallowed.
5. MC sim expected return (`estimate_geometric_return()`) reads `0.0` in practice — same
   silent-failure pattern, not a scope/wiring issue (the module already runs every unified run).
6. corr_sim's `var_days` defaults to 1 (standard 1-day VaR convention) in the interactive/
   dashboard-triggered path, which reads as "broken" for a correlation sim meant to project a
   year out like the market-signals bundle's peer sim already does.
7. `vega_notional` is a bare hardcoded `100000` for every ticker, regardless of price/vol level.
8. VRP term structure is computed and plotted inside Vol_Suite but gated behind an interactive
   prompt / env var, and has no Tools-registry entry for dashboard/orchestrator access.
9. Price distribution (`price_dist.py`) has no context-mode builder and thus never produces a
   `*_result.json` — unreachable outside interactive use.
10. No placeholder exists yet for a future "Social Media Sentiment Scanner" Tools entry
    (reddit/youtube/stocktwits), distinct from the real `sentiment-scanner` suite.
11. The Market Signals bundle isn't shown as a structured section on the unified `/quant` tab
    (falls back to generic flattened-JSON pairs), and MC/copula/corr_sim/price_dist results
    render as raw JSON with no table or path visualization.

## Non-goals

- No changes to the real `sentiment-scanner` suite (StockTwits/Reddit/YouTube) — item 10 is a
  placeholder Tools entry only.
- No change to `var_horizon_days`'s role in true 1-day VaR calculations elsewhere — only
  corr_sim's own default (item 6) changes.
- No auth/security changes to the dashboard (out of scope, and a deliberate existing decision
  per `CLAUDE.md`).

## Design

### 1. Context schema: `garch_conditional_vol`

Add `garch_conditional_vol` (float, annualized, nullable) to:
- `Vol_Suite/suite_context.py` — new field on the focus block.
- `shared/schemas.py` — extend the `vol_result.json`/`suite_context.json` validators.

Vol_Suite's `garch_analysis.py::_run_garch` (or equivalent) already computes annualized
conditional vol (`garch_analysis.py:313-316`, currently console-`print`-only). Capture the
return value in `volatility_suite.py`'s result-assembly path and write it into both
`vol_result.json` and the `suite_context.json` focus block that flows downstream. If GARCH
fit fails, write `null` — never a fabricated fallback number — so consumers can tell
"unavailable" from "computed zero."

### 2. Max Pain expiry

`orchestrator.py`'s market-signals stage calls `scan_fn(ticker)` with no context
(`orchestrator.py:315`). Change the call for `max_pain` specifically to pass
`focus.expiration_date`, and extend `max_pain_scanner.py::scan_max_pain()` to accept an
optional `expiry` param that overrides its internal "nearest ~30DTE" self-selection when given.
Other scanners (iv_rank, skew, unusual_oi) are unaffected — out of scope unless they also prove
to ignore context expiry.

### 3. Fair vol % — no change

Confirmed already correctly sourced from `vol_result.json` via `shared/summary.py::_extract_vol`
into `quant_summary.json`. This item is display-only (see item 11).

### 4/5. MC sim vol + expected return

In `VaR_Tools_Simulations/var_engine/data_loader.py`:
- `estimate_garch_vol()` and `estimate_geometric_return()` both currently do
  `except Exception: return 0.0` — a real failure (ThetaData rate-limit/502 under the
  concurrent multi-suite load a unified run generates, insufficient data, etc.) is
  indistinguishable from a legitimately-computed zero. Change both to return `None` on failure,
  and log the actual exception via `shared/logging.py` instead of swallowing it silently.

In `VaR_Tools_Simulations/main.py::_build_mc_sim_from_context` (and its `_peer` counterpart):
- Vol: prefer `suite_context.json`'s `garch_conditional_vol` (item 1) when present; fall back to
  `estimate_garch_vol()`, then to `0.25` only if both are `None`. Record which source was used.
- Expected return: purely `estimate_geometric_return()` — Vol_Suite has no drift/expected-return
  concept to source from, so no context-preference chain here. This module already runs
  unconditionally every unified run via `run_market_signals_stage` (`orchestrator.py:1056`) — no
  wiring change needed for that part, only the silent-failure fix above.
- Add a `data_quality` field to the sim result (e.g. `{"vol_source": "context|garch_fit|fallback",
  "expected_return_source": "computed|unavailable"}`) so a real failure renders visibly in the
  dashboard instead of masquerading as valid data.

### 6. corr_sim horizon

In `VaR_Tools_Simulations/main.py::_build_corr_sim_from_context` (the interactive/dashboard
context-mode builder, distinct from `_build_corr_sim_peer_from_context` which already hardcodes
252 days for the market-signals bundle): change the default from `var_horizon_days` (1) to `252`,
matching the 1-year-out convention used by MC sim / copula / the peer corr sim. Keep it
overridable via an explicit context field (`corr_sim_days`) so a genuine short-horizon run is
still possible. Add a horizon-days input to the dashboard trigger form for MC sim / corr_sim so
this is visible and adjustable, not just a silent default swap.

### 7. vega_notional

`Vol_Suite/variance_swap_live.py:411` hardcodes `vega_notional = 100000` for every ticker. Per
the standard variance-swap convention (`N_var = N_vol / (2·σ_strike)`), vega notional is
properly a trade-size choice rather than something purely derived — but it should not be
identical across tickers regardless of price. Scale it from spot price so exposure is
comparable in dollar terms across a cheap vs. expensive underlying (e.g. target a consistent
dollar-notional band relative to spot/ATM straddle price), keeping `100000` as the fallback
when live pricing is unavailable. Verify the existing `variance_notional` derivation on line 412
correctly implements `N_var = N_vol / (2·σ_strike)`; fix if it doesn't.

### 8. VRP term structure

- Flip `run_vrp_term_structure` to default-on in context mode (`volatility_suite.py:1246-1280`)
  instead of requiring the interactive prompt / `VS_RUN_VRP_TERM_STRUCTURE` env var, so it always
  computes and prints in Vol_Suite's own block during unified/context-mode runs.
- New Tools entry `Tools/tools/vrp_term_structure_tool.py`, following the
  `Tools/tools/hedge_optimizer_tool.py` pattern (lazy import under a unique `sys.modules` key,
  `run(context) -> dict` delegating to `Vol_Suite`'s existing computation), registered in
  `Tools/registry.py::TOOLS`.

### 9. Price distribution

Add `_build_price_dist_from_context` in `VaR_Tools_Simulations/main.py`, mirroring
`_build_mc_sim_from_context`'s shape (flat result dict, written as a marker file so the
dashboard's generic JSON→pairs view — or the new shared renderer from item 11 — picks it up).
Wrap it as `Tools/tools/price_dist_tool.py`, registered in `Tools/registry.py`.

### 10. Social Media Sentiment Scanner skeleton

New `Tools/tools/social_sentiment_tool.py`: `run(context) -> dict` returns a stub
`{"status": "not_implemented", "reddit": {...}, "youtube": {...}, "stocktwits": {...}}` shaped
placeholder. Registered in `Tools/registry.py`. No connection to the real `sentiment-scanner`
project — purely a UI placeholder for future work.

### 11. Dashboard: Market Signals visibility + sim visualizations

- Add a dedicated Market Signals section to the unified `/quant` tab's summary builder
  (`shared/summary.py`'s `_MODULE_IDS`/`quant_summary.json` construction) so scanners/
  simulations/direction render as structured cards instead of the generic flattened/truncated
  JSON-pairs view.
- Build one shared "simulation result" renderer — a percentile/summary table plus a lightweight
  inline SVG visualization (fan chart of sample paths, or terminal-distribution histogram,
  whichever the available result data supports without new heavy computation) — and use it for
  MC sim, copula sim, corr_sim, and price_dist alike, replacing raw JSON for all four with one
  component.

## Testing

- Unit tests for `estimate_garch_vol`/`estimate_geometric_return` failure paths (mock ThetaData
  to raise; assert `None` returned and logged, not `0.0`).
- Unit test for `max_pain_scanner.scan_max_pain(ticker, expiry=...)` override behavior.
- Unit test for `_build_corr_sim_from_context` default horizon (252) and override.
- Unit test for vega_notional scaling function across a range of spot prices.
- Manual dashboard check: trigger a unified run, confirm Market Signals section renders
  structured (not raw JSON), confirm sim tables/visualizations render for MC/copula/corr_sim/
  price_dist, confirm VRP term structure appears in Vol_Suite block and is runnable as a Tool.
- `pytest` full suite must still pass; `pre-commit run --all-files` clean.
