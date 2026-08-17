# Dealer-Greeks Study Refactor — v1 / v2_live / dealer_exposure

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the dealer-greeks study run ONE backtest that outputs **v1 + v2_live + dealer_exposure** together (replacing v1/v2/v3/whale), remove the per-model individual-run selector, fix the "invalid argument" crash from `all`, and fix the degenerate all-long v2_live accumulation book.

**Architecture:** The dealer-greeks study (`Vol_Suite/backtest_stage3.py`) currently computes v1/v2/v3/whale every day and treats `sign_model` as a selector. Refactor so a single run produces the three live models Jason cares about (v1 GEX, v2_live accumulated, dealer_exposure), drop the experimental v3/whale columns, and route v2_live's accumulated book through `dealer_positioning.compute_accumulated_position()` (the single live-model facade).

**Tech Stack:** Python, pandas/numpy, scipy (Welch's t-test), ThetaData via `shared/thetadata.py`, `replication_reference.py`, `expiry_book_exposure.py` (dev worktree).

## Global Constraints
- `dealer_exposure_model` stays on the `Dealer-Exposure-Dev` worktree — NOT merged into master; loaded at runtime via `_load_dealer_exposure_engine()`.
- v2_live accumulation must fail loudly (no silent same-day fallback) per Jason.
- No silent fallbacks / no fabricated data. Reproduce errors, don't guess.
- Keep the single-live-model facade: backtest consumes `dealer_positioning.compute_accumulated_position`, not `replication_reference` directly.
- Tests must pass: `Vol_Suite/tests/` (backtest_stage3, replication_reference_accumulation, dealer_gamma_gex, dealer_positioning_accumulation).
- Run tests via: `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pytest <path> -q` from repo root.

---
## Context / Current State (verified)
- `run_backtest(ticker, expiration, target_years, lookback_days, forward_window_days, accumulate=False, sign_model='all')` — `sign_model` only toggles `accumulate` (v2_live) and `use_dealer_exposure`.
- `_run_backtest_from_history` always computes v1/v2/v3/whale; dealer only when `use_dealer_exposure=True`.
- `_build_day_records` (≈line 561): v2_live regime = `net_v2 = Σ gamma * accumulated_position[(k,right)]` (pass-through sign=1.0); whale = `whale_scanner.classify_whale_bias`.
- `BacktestResult` fields: `v1_*`, `v2_*`, `v3_*`, `whale_*`, `dealer_exposure_*`.
- `format_backtest_report` (≈line 799): v1/v2/v3/whale columns + a dealer_exposure line.
- Live verification: `sign_model='all'` works on SPY at lookback 20 AND 90. v1/v2_live/dealer_exposure all run on SPY at lookback=45.
- **v2_live at lookback=45 → `57 long / 0 short` (degenerate all-long); at lookback=20 → `25/5`.** This is the "dealer model is fucked up" symptom.
- "invalid argument" from `all` is NOT reproducible on SPY — requires Jason's specific ticker/expiry/context.

---

### Task 1: Reproduce and root-cause the "invalid argument" crash from `all`

**Files:**
- Diagnose: `Vol_Suite/backtest_stage3.py`, `Direction/whale_scanner.py`, `Vol_Suite/expiry_selector.py`
- Reproduce: a temp diagnostic script calling `bs3.run_backtest(ticker, sign_model='all')` for the failing context.

**Interfaces:**
- Produces: the exact exception type + traceback for the "invalid argument" crash; identifies whether it's in the whale path, expiry resolution, or a model computation.

- [ ] **Step 1: Get the failing context.** Ask Jason for the exact ticker/expiry/context (the dashboard showed NVDA and TSMC contexts) that produced "invalid argument" when running with `all`. Without it, the crash is not reproducible (all SPY runs pass).
- [ ] **Step 2: Reproduce.** Write `%LOCALAPPDATA%\Temp\bt_repro.py`:
```python
import sys, traceback
sys.path.insert(0, r"C:\Users\bottl\FinancialDevelopment")
sys.path.insert(0, r"C:\Users\bottl\FinancialDevelopment\Vol_Suite")
import Tools.registry  # noqa: F401  (load registry before the tool: circular-import guard)
import backtest_stage3 as bs3
TICKER = "<from Jason>"        # e.g. NVDA
EXPIRY = "<from Jason>"        # e.g. 20261121
r = bs3.run_backtest(TICKER, expiration=EXPIRY, target_years=0.25,
                     lookback_days=90, forward_window_days=5,
                     accumulate=False, sign_model='all')
print("OK", len(r.day_records))
```
Run it, capture the full traceback.
- [ ] **Step 3: Root-cause.** Map the traceback to the failing line. Likely candidates:
  - `whale_scanner.classify_whale_bias(...)` on `whale_rows_by_date.get(d, [])` — a bad arg from whale rows.
  - `expiry_selector.resolve_expiration` on a far-dated/illiquid expiry.
  - A `datetime.strptime` / strike parse on an unexpected data shape for the ticker.
  Fix the root cause (not a symptom): correct the bad argument at its source.
- [ ] **Step 4: Verify.** Re-run the repro for the failing ticker/expiry — it must pass. Run the affected unit tests. Commit with message `fix(vol): dealer-greeks "all" run no longer throws invalid argument for <ticker>`.

---

### Task 2: Fix the degenerate all-long v2_live accumulation book

**Files:**
- Modify: `Vol_Suite/replication_reference.py` (`_accumulate_from_history`), `Vol_Suite/backtest_stage3.py` (v2_live classification)
- Test: `Vol_Suite/tests/test_backtest_stage3.py`, `Vol_Suite/tests/test_replication_reference_accumulation.py`

**Interfaces:**
- Produces: `accumulated_position` (dict `(strike, right) -> signed OI`) whose sign yields a NON-degenerate long/short mix (not uniformly long).
- Consumes: the existing `compute_accumulated_position_for_expiry` / `dealer_positioning.compute_accumulated_position` facade.

**Root-cause hypothesis (verify first):** the replication seed is SHORT (`seed_sign=-1.0`), yet v2_live classifies every day LONG. So `net_v2 = Σ gamma * accumulated_position[(k,right)]` is positive everywhere → the accumulated book's signed values are positive. Either (a) the seed-advance lands on a long-gamma seed, (b) the daily flow sign dominates positive, or (c) `accumulated_position` is being applied with the wrong sign in `_build_day_records` (the pass-through `sign=1.0` assumption may be wrong vs how `dealer_positioning` applies it).

- [ ] **Step 1: Write a failing test** asserting v2_live produces a non-degenerate regime split on a synthetic multi-day history (not all-long):
```python
def test_v2_live_accumulated_book_is_not_uniformly_long(monkeypatch):
    # build synthetic greeks/OI/price rows over ~20 days with a realistic
    # mix; monkeypatch the accumulation to return a signed book that SHOULD
    # yield both long and short regimes; assert not (n_long == 0 or n_short == 0).
    ...
```
- [ ] **Step 2: Run to verify it fails** (reproduces the degenerate all-long bug): `pytest Vol_Suite/tests/test_backtest_stage3.py::test_v2_live_accumulated_book_is_not_uniformly_long -v`.
- [ ] **Step 3: Diagnose the sign.** Read `_accumulate_from_history` in `replication_reference.py` and instrument the produced `position_by_strike`: is it net-positive (long) or net-negative (short)? Compare against how `dealer_positioning.py`'s accumulate branch (`applied_sign=1.0`, ≈line 555-575) applies the same book. If the backtest's pass-through `sign=1.0` differs from dealer_positioning's application, that's the bug.
- [ ] **Step 4: Implement the fix.** Correct the sign application in `_build_day_records` (or the accumulation), so the v2_live regime reflects the true signed dealer book.
- [ ] **Step 5: Verify.** The failing test passes; `test_backtest_stage3.py` + `test_replication_reference_accumulation.py` all pass. Live-check SPY 20261120 at lookback=45 → expect a non-degenerate mix (not 57/0). Commit.

---

### Task 3: Refactor dealer-greeks study to output v1 / v2_live / dealer_exposure in ONE run

**Files:**
- Modify: `Vol_Suite/backtest_stage3.py` — `BacktestResult` (drop `v3_*`, `whale_*`), `_run_backtest_from_history` (compute v1/v2_live/dealer only), `run_backtest` (default = accumulate + dealer_exposure ON), `format_backtest_report` (show 3 columns), `_build_day_records` (keep v3/whale internals only if needed, don't report)
- Modify: `Tools/tools/backtesting_tool.py` — remove per-model `sign_model` selection; `run_dealer_gamma_study` always runs the combined 3-model study
- Test: `Tools/tests/test_backtesting_tool.py`, `Vol_Suite/tests/test_backtest_stage3.py`

**Interfaces:**
- Produces: `BacktestResult` with `v1_*`, `v2_*` (v2_live), `dealer_exposure_*` fields only; `run_backtest(..., accumulate=True, use_dealer_exposure=True)` for the default study.
- Consumes: the `_summarize` helper on `regime_v1`, `regime_v2`, `regime_dealer_exposure`.

- [ ] **Step 1: Update `BacktestResult` dataclass.** Remove `v3_*` and `whale_*` fields (8 fields each). Keep `v1_*`, `v2_*`, `dealer_exposure_*`.
- [ ] **Step 2: Update `_run_backtest_from_history`.** Drop `v3 = _summarize(records, 'regime_v3')` and `whale = _summarize(records, 'regime_whale')`; construct `BacktestResult` with v1/v2/dealer only.
- [ ] **Step 3: Update `run_backtest`.** The study default should compute v2_live + dealer_exposure too: `accumulate=True` and `use_dealer_exposure=True` when running the study (no per-model selector). Resolve dealer_exposure engine availability and fail loudly if unavailable (it's one of Jason's 3 models).
- [ ] **Step 4: Update `format_backtest_report`.** Replace the v1/v2/v3/whale header + rows with v1 / v2(v2_live) / dealer_exposure columns. Update the Hypothesis prose (drop v3/whale references; describe v1/v2_live/dealer_exposure).
- [ ] **Step 5: Update `backtesting_tool.py`.** `run_dealer_gamma_study` drops the `sign_model` param/selector — always runs the combined study. Remove `_DEALER_SIGN_MODELS` individual-mode handling (keep only the combined path).
- [ ] **Step 6: Update tests.** `Tools/tests/test_backtesting_tool.py` (drop sign_model-specific cases), `Vol_Suite/tests/test_backtest_stage3.py` (drop v3/whale assertions, add v1/v2_live/dealer assertions). Remove `dashboard/app.py` `sign_model` parsing + `tools_backtest.html` dropdown if present.
- [ ] **Step 7: Verify.** Full affected suite passes. Live-run the study on SPY — report must show exactly v1 / v2_live / dealer_exposure columns. Commit with message `refactor(vol): dealer-greeks study outputs v1+v2_live+dealer_exposure in one run`.

---

## Self-Review
- **Spec coverage:** design change (Task 3) ✓, "invalid arg" from `all` (Task 1) ✓, dealer model regression (Task 2) ✓.
- **Type consistency:** `BacktestResult` field names used in `_run_backtest_from_history`, `format_backtest_report`, `backtesting_tool.py`, and tests must all be updated together (v3/whale removed everywhere).
- **Placeholder check:** Task 1 requires Jason's failing ticker/expiry — that is an explicit external dependency, not a code placeholder.

## Execution Handoff
After Jason approves this plan, execute task-by-task (Task 1 needs Jason's failing ticker/expiry first).
