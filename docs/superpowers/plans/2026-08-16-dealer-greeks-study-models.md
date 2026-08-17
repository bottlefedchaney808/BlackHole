# Dealer-Greeks Study Model Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the dealer-greeks study (`backtesting` tool, `dealer_gamma_study` mode) an explicit, selectable sign model with **three** options: **v1** (oi_heuristic, verified to match conventional GEX), **v2_live** (vol_surface_replication with accumulation on, mirroring the live `run_dealer_positioning`), and **dealer_exposure** (the dealer-frame greeks engine in `expiry_book_exposure.py`, sourced from the `Dealer-Exposure-Dev` worktree — **not** merged into master).

**Architecture:** `backtest_stage3.run_backtest` already computes v1, v2, v3, and whale simultaneously into `BacktestResult`; it has no model selector. This plan adds a `sign_model` selector to the backtesting tool's `dealer_gamma_study` path that picks which model's stats headline the result, adds a v1-convention verification test, threads `accumulate=True` for the `v2_live` variant, and wires the dealer-frame engine as a third model by importing `expiry_book_exposure.py` from the dev worktree (with a graceful unavailable guard when the worktree is absent). The dealer-frame engine's source stays on the `Dealer-Exposure-Dev` branch.

**Tech Stack:** Python 3.12 (root `.venv`), `Vol_Suite/backtest_stage3.py`, `Vol_Suite/dealer_positioning.py`, `Vol_Suite/replication_reference.py`, `Tools/tools/backtesting_tool.py`, `dashboard/app.py` + `tools_backtest.html`, pytest. The dealer-frame engine source lives on the `Dealer-Exposure-Dev` worktree (`C:/Users/bottl/FinancialDevelopment/.worktrees/dealer-exposure-dev/Vol_Suite/expiry_book_exposure.py`), absent from the master checkout.

## Global Constraints

- Do NOT modify `run_strategy_backtest` or its `StrategyBacktestResult` — the strategy-P&L half of the tool is independent and unchanged.
- Do NOT change the `BacktestResult` field names (`v1_*`, `v2_*`, `v3_*`, `whale_*`) or `format_backtest_report`'s existing signature — downstream readers (including the tool's report) depend on them. New columns may be ADDED to the report for the `dealer_exposure` series, but existing ones must not change meaning.
- `sign_model` values accepted by the tool: `'v1'`, `'v2_live'`, `'dealer_exposure'`, `'all'` (default).
- **Source-location constraint:** `expiry_book_exposure.py` is NOT copied/merged into the master tree. The study imports it from the dev worktree path at runtime. If that worktree/module is absent, `sign_model='dealer_exposure'` must fail gracefully (a clear `ValueError`/error payload), never crash the tool or the dashboard.
- Run project python as `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe`.

## File Structure

- Modify: `Tools/tools/backtesting_tool.py` (`run_dealer_gamma_study` model selector + `v2_live`/`dealer_exposure` wiring).
- Modify: `dashboard/app.py` + `dashboard/templates/tools_backtest.html` (Model dropdown).
- Create: `Vol_Suite/tests/test_dealer_gamma_gex.py` (v1 = conventional GEX).
- Modify: `Vol_Suite/backtest_stage3.py` (accumulation support for `v2_live`; `dealer_exposure` column).
- Test: `Tools/tests/test_backtesting_tool.py`, `dashboard/tests/test_tools_routes.py`, `Vol_Suite/tests/test_backtest_stage3.py`, `Vol_Suite/tests/test_dealer_gamma_gex.py`.

---

### Task 1: Dealer-greeks study model selector (v1 / v2_live / all)

**Files:**
- Modify: `Tools/tools/backtesting_tool.py::run_dealer_gamma_study`
- Modify: `dashboard/app.py::tools_backtest_form/run` + `dashboard/templates/tools_backtest.html`
- Test: `Tools/tests/test_backtesting_tool.py`, `dashboard/tests/test_tools_routes.py`

**Interfaces:**
- Consumes: `context['sign_model']` ∈ `{'v1','v2_live','all'}` (default `all`), plus the existing `ticker`/`expiration`/`target_years`/`lookback_days`/`forward_window_days`.
- Produces: `run_dealer_gamma_study(context)` returns `{"mode":"dealer_gamma_study","sign_model":..., "report":..., "result":...}`. The returned dict gains a top-level `sign_model`. `'dealer_exposure'` raises a clear "sourced from dev worktree, unavailable here" error until Task 4 lands.

- [ ] **Step 1: Write the failing test**

```python
# Tools/tests/test_backtesting_tool.py (append)
def test_dealer_gamma_study_sign_model_v1(monkeypatch):
    from Tools.tools import backtesting_tool
    import types
    fake_bs3 = types.ModuleType('backtest_stage3')
    fake_bs3.DEFAULT_LOOKBACK_DAYS = 90
    fake_bs3.DEFAULT_FORWARD_WINDOW_DAYS = 5
    fake_bs3.run_backtest = lambda **kw: type('R', (), {'v1_diff': 0.1})()
    fake_bs3.format_backtest_report = lambda r: 'REPORT'
    monkeypatch.setitem(backtesting_tool.sys.modules, 'backtest_stage3', fake_bs3)
    ctx = {'focus': {'ticker': 'SPY'}, 'sign_model': 'v1'}
    out = backtesting_tool.run_dealer_gamma_study(ctx)
    assert out['sign_model'] == 'v1'
```

- [ ] **Step 2: Run to verify it fails**

Run: `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pytest Tools/tests/test_backtesting_tool.py::test_dealer_gamma_study_sign_model_v1 -q`
Expected: FAIL (no `sign_model` key in the result yet).

- [ ] **Step 3: Add the selector in run_dealer_gamma_study**

```python
_DEALER_SIGN_MODELS = {'v1', 'v2_live', 'dealer_exposure', 'all'}

def run_dealer_gamma_study(context):
    import backtest_stage3 as bs3
    focus = context.get("focus") or {}
    ticker = context.get("ticker") or focus.get("ticker")
    if not ticker:
        raise ValueError("mode='dealer_gamma_study' requires a ticker "
                          "(context['ticker'] or context.focus.ticker)")
    sign_model = str(context.get("sign_model") or "all").strip().lower()
    if sign_model not in _DEALER_SIGN_MODELS:
        raise ValueError(f"sign_model must be one of {sorted(_DEALER_SIGN_MODELS)}; got {sign_model!r}")
    expiration = context.get("expiration") or _iso_to_compact(focus.get("expiration_date"))
    target_years = float(context.get("target_years", focus.get("target_years", 0.25)))
    lookback_days = int(context.get("lookback_days", bs3.DEFAULT_LOOKBACK_DAYS))
    forward_window_days = int(context.get("forward_window_days", bs3.DEFAULT_FORWARD_WINDOW_DAYS))
    accumulate = sign_model == "v2_live"
    result = bs3.run_backtest(
        ticker, expiration=expiration, target_years=target_years,
        lookback_days=lookback_days, forward_window_days=forward_window_days,
        sign_model=sign_model, accumulate=accumulate)
    report = bs3.format_backtest_report(result)
    return {
        "mode": "dealer_gamma_study",
        "sign_model": sign_model,
        "report": report,
        "result": dataclasses.asdict(result),
    }
```
Note: `run_backtest(..., sign_model=..., accumulate=...)` are added by Tasks 3-4. Until they land, call `bs3.run_backtest(...)` without those kwargs here (or guard on availability); Task 3/4 will thread them through. To keep Task 1 independently green, pass `sign_model`/`accumulate` only if the signature accepts them, else call with the original 5 args.

- [ ] **Step 4: Add the Model dropdown to the backtest UI**

In `dashboard/templates/tools_backtest.html`, add after the Mode field:
```html
<div class="field">
  <label for="sign_model">Model</label>
  <select id="sign_model" name="sign_model" onchange="onModeChange()">
    <option value="all" {{ 'selected' if selected_sign_model == 'all' else '' }}>all (v1 + v2.1 + v3 + whale)</option>
    <option value="v1" {{ 'selected' if selected_sign_model == 'v1' else '' }}>v1 (oi_heuristic / conventional GEX)</option>
    <option value="v2_live" {{ 'selected' if selected_sign_model == 'v2_live' else '' }}>v2.1 vol-surface + accumulation (live)</option>
    <option value="dealer_exposure" {{ 'selected' if selected_sign_model == 'dealer_exposure' else '' }}>dealer_exposure (dev engine)</option>
  </select>
</div>
```
In `dashboard/app.py::tools_backtest_run`, read `sign_model = str(body.get('sign_model') or 'all').strip().lower()` and set `context['sign_model'] = sign_model` when `mode == 'dealer_gamma_study'`. Thread `selected_sign_model` through the response.

- [ ] **Step 5: Run to verify it passes**

Run: `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pytest Tools/tests/test_backtesting_tool.py dashboard/tests/test_tools_routes.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add Tools/tools/backtesting_tool.py dashboard/app.py dashboard/templates/tools_backtest.html Tools/tests/test_backtesting_tool.py
git commit -m "feat(tools): dealer-greeks study exposes sign-model selector (v1/v2_live/all)"
```

---

### Task 2: Verify v1 matches conventional GEX sign convention

**Files:**
- Create: `Vol_Suite/tests/test_dealer_gamma_gex.py`
- Modify: `Vol_Suite/backtest_stage3.py` (only if the sign needs correcting)

**Interfaces:**
- Consumes: `_net_gamma_v1` / `dealer_positioning._dealer_sign`.
- Produces: a test proving v1's sign matches conventional GEX: a book short calls and short puts reads net **short** gamma (sold options ⇒ negative gamma contribution).

- [ ] **Step 1: Write the failing test**

```python
# Vol_Suite/tests/test_dealer_gamma_gex.py
"""v1 (oi_heuristic) must match the conventional GEX sign convention.

Conventional GEX: a dealer who SELLS an option is short gamma -- a sold call
or sold put contributes NEGATIVE gamma to the dealer book. v1 uses
dealer_positioning._dealer_sign(right), which must honor this.
"""
import backtest_stage3 as bs3
from dealer_positioning import _dealer_sign


def test_v1_sold_call_and_put_are_negative_gamma():
    gamma_map = {(100.0, "C"): 0.05, (100.0, "P"): 0.04}
    oi_map = {(100.0, "C"): 1, (100.0, "P"): 1}
    total = bs3._net_gamma_v1(gamma_map, oi_map)
    assert total < 0.0, f"v1 must be short gamma for a sold-call+sold-put book, got {total}"


def test_dealer_sign_convention():
    assert _dealer_sign("C") in (-1.0, 1.0)
    assert _dealer_sign("P") == -_dealer_sign("C")
```

- [ ] **Step 2: Run to verify it passes (or fails, revealing the convention)**

Run: `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pytest Vol_Suite/tests/test_dealer_gamma_gex.py -v`
Expected: if `_dealer_sign` already implements the conventional GEX frame (sold ⇒ negative), this passes. If it FAILS, the convention is inverted and `_dealer_sign` (or `_net_gamma_v1`'s use of it) must be corrected so the sold-call+sold-put book reads net short gamma. Fix it, re-run until green, and confirm `test_dealer_positioning_sign_model.py` still passes. Do NOT weaken the assertion.

- [ ] **Step 3: Run the full dealer test module to confirm no regressions**

Run: `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pytest Vol_Suite/tests/test_dealer_gamma_gex.py Vol_Suite/tests/test_dealer_positioning_sign_model.py Vol_Suite/tests/test_backtest_stage3.py -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add Vol_Suite/tests/test_dealer_gamma_gex.py Vol_Suite/backtest_stage3.py
git commit -m "test(vol): verify v1 dealer-gamma sign matches conventional GEX"
```

---

### Task 3: v2_live = vol_surface_replication with accumulation in the backtest

**Files:**
- Modify: `Vol_Suite/backtest_stage3.py` (`run_backtest` + `_run_backtest_from_history` to accept `accumulate: bool = False`; when True, apply `replication_reference.compute_accumulated_position_for_expiry`'s accumulated signed position for the anchor expiry).
- Modify: `Tools/tools/backtesting_tool.py` (map `sign_model='v2_live'` → `run_backtest(..., accumulate=True)`, label the v2 column "v2.1 + accumulation (live)").
- Test: `Vol_Suite/tests/test_backtest_stage3.py`, `Tools/tests/test_backtesting_tool.py`.

**Interfaces:**
- Consumes: `replication_reference.compute_accumulated_position_for_expiry` (already imported by backtest_stage3).
- Produces: `run_backtest(..., accumulate=True)` computes the v2 net gamma from the accumulated signed position (sign baked in) instead of `_resolve_sign * today's OI`, mirroring `dealer_positioning.compute_dealer_positioning(..., accumulate=True)` (the live runner). `sign_model='v2_live'` uses this path.

- [ ] **Step 1: Write the failing test**

```python
# Vol_Suite/tests/test_backtest_stage3.py (append)
def test_run_backtest_accepts_accumulate_flag(monkeypatch):
    import backtest_stage3 as bs3
    # The network orchestrator must thread accumulate through to the pure
    # function; assert it is accepted and forwarded.
    captured = {}
    def fake_pure(ticker, expiry, greeks, oi, prices, fwd, **kw):
        captured.update(kw)
        return bs3.BacktestResult(ticker=ticker, expiry=expiry, forward_window_days=fwd)
    monkeypatch.setattr(bs3, '_run_backtest_from_history', fake_pure)
    class TD:
        def close(self): pass
        def option_bulk_hist_eod(self, *a, **k): return []
        def option_bulk_hist_oi_by_day(self, *a, **k): return []
        def hist_stock_eod(self, *a, **k): return []
    monkeypatch.setattr(bs3, 'ThetaDataController', lambda: TD())
    monkeypatch.setattr(bs3.expiry_selector, 'resolve_expiration', lambda td, tk, e, ty: ('20261016', 0.25))
    bs3.run_backtest('SPY', accumulate=True)
    assert captured.get('accumulate') is True
```
(Adjust to the real `_run_backtest_from_history` signature — read it first. The essential assertion is that `accumulate` reaches the pure backtest function.)

- [ ] **Step 2: Run to verify it fails**

Run: `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pytest Vol_Suite/tests/test_backtest_stage3.py::test_run_backtest_accepts_accumulate_flag -q`
Expected: FAIL (TypeError: unexpected keyword `accumulate`).

- [ ] **Step 3: Thread accumulate through**

Add `accumulate: bool = False` to `run_backtest` and `_run_backtest_from_history`. In the pure function, when `accumulate` is True, compute the accumulated signed position for the anchor expiry via `replication_reference.compute_accumulated_position_for_expiry` and use its `position_by_strike` (sign already baked) for the v2 aggregation instead of `_resolve_sign(...) * oi` — the same split `dealer_positioning.compute_dealer_positioning` uses (read that function's `accumulate` branch to mirror it exactly). `format_backtest_report` labels the v2 column "v2.1 + accumulation (live)" when `accumulate` is True (thread a flag onto `BacktestResult` if needed).

- [ ] **Step 4: Wire sign_model='v2_live' in the tool**

In `backtesting_tool.run_dealer_gamma_study`, pass `accumulate=True` when `sign_model == 'v2_live'` (this replaces the Task 1 placeholder call).

- [ ] **Step 5: Run to verify it passes**

Run: `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pytest Vol_Suite/tests/test_backtest_stage3.py Tools/tests/test_backtesting_tool.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add Vol_Suite/backtest_stage3.py Tools/tools/backtesting_tool.py Vol_Suite/tests/test_backtest_stage3.py
git commit -m "feat(vol): dealer-greeks study v2_live uses accumulated position (live model)"
```

---

### Task 4: Wire the dealer_exposure_model (sourced from the dev worktree) into the study

**Files:**
- Modify: `Vol_Suite/backtest_stage3.py` (add a `dealer_exposure` series computed via the dealer-frame greeks engine).
- Modify: `Tools/tools/backtesting_tool.py` (enable `sign_model='dealer_exposure'`).
- Modify: `dashboard/app.py` + `tools_backtest.html` (option already added in Task 1).
- Source: `expiry_book_exposure.py` on the `Dealer-Exposure-Dev` worktree (`C:/Users/bottl/FinancialDevelopment/.worktrees/dealer-exposure-dev/Vol_Suite/expiry_book_exposure.py`). NOT merged into master.

**Interfaces:**
- Consumes: `expiry_book_exposure.build_net_exposure(rows, spot, ticker)` → `NetExposure`, and `execution_locus`/`el_karoui_closure_gate` for the dealer-frame greeks.
- Produces: `run_backtest(..., sign_model='dealer_exposure')` computes a third net-gamma/exposure series from the dealer-frame engine and reports its diff/t-stat alongside v1/v2 in `format_backtest_report`. When the dev worktree is absent, the tool returns a clear unavailable error.

- [ ] **Step 1: Read the source's actual interface**

Read `C:/Users/bottl/FinancialDevelopment/.worktrees/dealer-exposure-dev/Vol_Suite/expiry_book_exposure.py` and note the exact signatures of `build_net_exposure`, `NetExposure` fields, and `execution_locus`. The `rows` argument is a per-strike greek-row list; confirm the exact row dict shape (keys like `strike`, `right`, `gamma`, `vanna`, `open_interest`, ...). Record these in a short interface note in the code (a comment above the wiring).

- [ ] **Step 2: Write the failing test (pure-function path)**

```python
# Vol_Suite/tests/test_backtest_stage3.py (append)
def test_dealer_exposure_series_unavailable_without_worktree():
    import backtest_stage3 as bs3
    # When the dev-worktree engine cannot be located, the dealer_exposure
    # series must fail gracefully, not raise out of the backtest.
    assert bs3._dealer_exposure_engine_available() is False or \
           bs3._dealer_exposure_series_error() is not None
```
(Define `_dealer_exposure_engine_available()` / the availability guard in backtest_stage3; it checks for the worktree path and a successful import.)

- [ ] **Step 3: Add the guarded import + dealer_exposure series**

In `backtest_stage3.py`, add a guarded loader:
```python
_DEV_WORKTREE_EXPIRY_EXPOSURE = (
    Path(__file__).resolve().parent.parent
    / ".worktrees" / "dealer-exposure-dev" / "Vol_Suite" / "expiry_book_exposure.py"
)

def _dealer_exposure_engine_available() -> bool:
    return _DEV_WORKTREE_EXPIRY_EXPOSURE.is_file()

def _load_dealer_exposure_engine():
    if not _dealer_exposure_engine_available():
        raise FileNotFoundError(
            "dealer_exposure_model requires the Dealer-Exposure-Dev worktree "
            f"(expected {_DEV_WORKTREE_EXPIRY_EXPOSURE}); not merged into master.")
    spec = importlib.util.spec_from_file_location(
        "expiry_book_exposure", str(_DEV_WORKTREE_EXPIRY_EXPOSURE))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
```
Add a `dealer_exposure` series to the pure backtest: for each historical day, build the dealer-frame net exposure from that day's per-strike greek rows (`build_net_exposure(rows, spot, ticker)`), classify long/short by the resulting net gamma/exposure sign, and compute the same long/short forward-vol diff + t-stat as v1/v2. Mirror the v1/v2 aggregation loop. When `sign_model == 'dealer_exposure'`, `run_backtest` uses this series as the headline (and the tool enables it). `format_backtest_report` gains a `dealer_exposure` column when the series is present.

- [ ] **Step 4: Wire sign_model='dealer_exposure' in the tool**

In `backtesting_tool.run_dealer_gamma_study`, when `sign_model == 'dealer_exposure'`, ensure the worktree engine is available (call `bs3._dealer_exposure_engine_available()`) and raise a clear error if not; otherwise pass `sign_model='dealer_exposure'` to `run_backtest`. The Task 1 guard's placeholder error for `dealer_exposure` is replaced by this real wiring.

- [ ] **Step 5: Run to verify it passes**

Run: `env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe -m pytest Vol_Suite/tests/test_backtest_stage3.py Tools/tests/test_backtesting_tool.py -q`
Expected: PASS (the unavailable-guard test passes; a synthetic-rows test for the series uses the real engine only if the worktree is present — otherwise assert the guard fires cleanly).

- [ ] **Step 6: Commit**

```bash
git add Vol_Suite/backtest_stage3.py Tools/tools/backtesting_tool.py Vol_Suite/tests/test_backtest_stage3.py
git commit -m "feat(vol): add dealer_exposure_model to dealer-greeks study (sourced from dev worktree)"
```

---

## Self-Review

**Spec coverage:** R7 (dealer-greeks study updated to use v1 [GEX], current live model with accumulation on, and the new dealer_exposure_model) → Task 1 (selector), Task 2 (v1 = conventional GEX), Task 3 (v2_live = live model + accumulation), Task 4 (dealer_exposure_model, sourced from the dev worktree). All four model options implemented; the source-location constraint keeps the engine off master.

**Placeholder scan:** Tasks 1-3 contain complete runnable code. Task 4's Step 1 is an explicit interface-read of an un-merged module (a real prerequisite, not a placeholder), and Step 3's series wiring depends on that read; the plan names the exact functions/guard and the graceful-unavailable behavior so an engineer can implement without guessing. No "implement later".

**Type consistency:** `sign_model` is a `str` used identically in the tool (`run_dealer_gamma_study`), the dashboard form (`selected_sign_model`), and the dropdown options. `_net_gamma_v1`/`_net_gamma_v2`/`_dealer_sign` are referenced by their real names. `accumulate: bool` is threaded consistently through `run_backtest`/`_run_backtest_from_history`/`compute_dealer_positioning`. `_dealer_exposure_engine_available`/`_load_dealer_exposure_engine`/`build_net_exposure`/`NetExposure` are consistent across Tasks 2/4. `BacktestResult` existing field names are untouched; new `dealer_exposure` fields are additive.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-16-dealer-greeks-study-models.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
