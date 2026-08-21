# Backtest Tools Expansion — Plan

Goal: fix the live dealer backtest (it never showed up), clean the model
selector, and add standard stock + options backtests and an options-based
hedging optimizer — all reachable from the dashboard **Tools** tab.

## Root cause (confirmed empirically)
`Vol_Suite/backtest_stage3.py::_load_dealer_exposure_engine()` still points at
the dead `dealer-exposure-dev` worktree. The engine was merged into
`Vol_Suite/expiry_book_exposure.py` (self-contained, stdlib+numpy only).
`_dealer_exposure_engine_available()` returns `False`, so the live GEX arm is
skipped and the default `sign_model='all'` run RAISES. Only the 3 legacy
sign-convention arms (v1 oi_heuristic, v2_live accumulation, + the broken
"dev engine" slot) ever produce results.

## Changes

### 1. Fix live dealer model (primary bug) — `backtest_stage3.py`
- Repoint `_load_dealer_exposure_engine()` to the merged in-tree
  `Vol_Suite/expiry_book_exposure.py` module (import it directly); keep the
  dev-worktree path only as a fallback. Fix the error message.
- Net effect: `run_backtest(..., sign_model in {'all','dealer_exposure','live'})`
  now actually runs the live dealer-frame GEX arm.

### 2. Clean model selector — `backtesting_tool.py` + template
- `run_dealer_gamma_study`: accept `sign_model` in {`live` (default), `all`,
  `legacy`} (+ back-compat aliases `dealer_exposure`, `v1`, `v2_live`).
  `live`/`all`/`dealer_exposure` → engine on; `legacy`/`v1`/`v2_live` → engine off.
- Legacy arms (v1/v2_live) stay computed and labeled **legacy/comparison**
  (per dealer-model-adoption: keep comparison arms, don't delete).
- Template Model dropdown relabeled: **live (expiry_book GEX)** = default,
  `all` = live + legacy comparison, `legacy` = old models only.

### 3. Standard stock backtest — new mode `stock_strategy_backtest`
- Self-contained on `td.hist_stock_eod`. Strategies: `buy_hold`, `sma_cross`
  (fast/slow), `momentum` (n-day sign). Position on day t from data ≤ t;
  P&L = pos_t · ret_{t+1}.
- Metrics: total/annualized return, Sharpe, max drawdown, win rate, n_trades,
  vs buy&hold benchmark. Network-touching like the existing study modes.

### 4. Standard options backtest — new mode `option_strategy_backtest`
- Canned strategies built into the existing leg-dict contract: `covered_call`,
  `short_straddle`, `short_strangle`, `long_call`, `long_put`; strike from a
  delta offset. Loop over historical entry dates, reuse the existing
  `run_strategy_backtest` (already verified leg-pricing machinery), aggregate
  P&L / win rate / max adverse excursion.

### 5. Options hedging optimizer — `hedge_optimizer_tool` new mode
- New helper `Tools/tools/options_hedge.py`. `options_hedge` mode: given a
  signed position (shares of the focus ticker) + a call & a put from the
  nearest-expiry chain (vendor EOD greeks via `dealer_positioning.bs_*`),
  solve a **delta + vega neutral** hedge using underlying stock + call + put.
  Output: n_stock, n_call, n_put, residual delta/vega/gamma, premium estimate,
  and a plain-English execution summary.
- `hedge_optimizer_tool.run` dispatches on `context['hedge_mode']`:
  `stocks` (existing min-var vs basket peers) vs `stocks+options` (new).
- Give hedge-optimizer a **bespoke form** (GET/POST `/tools/hedge-optimizer`,
  new template) with a mode selector + position size + (options) delta target;
  remove `hedge-optimizer` from `GENERIC_TOOL_SLUGS`.

### 6. Dashboard wiring — `tools_backtest.html` + `app.py`
- Mode dropdown: add `stock_strategy_backtest`, `option_strategy_backtest`
  (+ keep `broker_book_accuracy`). Per-mode param fields + JS toggles.
- POST `/tools/backtest`: pass through stock/option params into context.

## Verification
- Offline: registry loads; `get_tool('backtesting')`; engine available == True;
  options_hedge solve on a synthetic greek fixture (delta/vega ≈ 0).
- Online (small lookback, real ticker): dealer study (live arm populated),
  stock backtest, option backtest, options hedge all return 200 + sane numbers.
- `pytest Tools/tests/` (update test_registry slugs if changed).
- Restart dashboard (kill owning PID of :8787, start one detached) and confirm
  the Tools tab lists the new modes.

## Out of scope
- No new ToolSpec (all additions are modes of existing tools) → registry
  unchanged. No cloud/auth changes.
