# Implementation Plan — Dealer Positioning v3: Accumulation + Per-Expiry Direction

## Overview
Three features, built in dependency order:
1. **SABR all-strikes mode** — optional config flag, minimal code change
2. **Multi-day accumulation** — wire `compute_accumulated_position()` into live path, fix flat-sign bug, ON by default (150-day lookback)
3. **Per-expiry direction bias** — three competing options (A/B/C), all implemented, backtested, debated

All three direction options run ON TOP of replication + accumulation.
Accumulation is ON by default, built to current day.

---

## Feature 1: SABR All-Strikes Mode

**File:** `Vol_Suite/vol_surface_reference.py`

**Change:** Add env flag `SABR_INCLUDE_ITM` (default false). When true, `fit_sabr_reference()` includes ALL strikes (OTM + ITM) instead of OTM-only.

**Code path:** `vol_surface_reference.py:218-237` — `_otm_iv_by_strike()` currently filters to OTM only (calls above forward, puts below forward). Add conditional: if `SABR_INCLUDE_ITM`, skip the OTM filter and return all strikes.

**RMSE guard:** Compare RMSE on OTM-only vs all-strikes. If all-strikes RMSE > 1.5x OTM-only RMSE, warn and fall back to OTM-only.

**Risk:** ITM IVs have wider bid-ask, different exercise dynamics. Default-off protects production.

**Test:** Compare SABR fit RMSE and sign stability on SPY (dense, 200+ strikes) vs an illiquid name (8-15 strikes) with and without ITM.

---

## Feature 2: Multi-Day Accumulation (ON by default)

**Files:** `Vol_Suite/replication_reference.py` + `Vol_Suite/dealer_positioning.py`

### Step 2a: Fix the flat-sign bug in accumulation
`compute_accumulated_position()` at `replication_reference.py:477-480` uses flat `−1.0 * delta_oi` for ALL strikes. This must use per-strike `_resolve_sign` logic instead.

The accumulation code at lines 597-625 does:
```python
signed_change = -1.0 * delta_oi   # FLAT -1, line 610
```

Fix: replace with a call to the same sign resolution logic used in `dealer_positioning.py:384-391`. For each day's OI change, compute the SABR deviation sign for that day's chain and use it instead of the flat -1.

### Step 2b: Wire accumulation into the live path
`dealer_positioning.py` main entry must:
1. After building the single-day gamma surface, call `compute_accumulated_position()` with `lookback_days=150`
2. Accumulated position becomes the PRIMARY signal
3. Single-day is the FALLBACK if accumulation fails

**Env flags:**
- `DEALER_ACCUMULATION_LOOKBACK_DAYS` (default 150)
- `DEALER_ACCUMULATION_HALFLIFE` (default 0 = no decay, configurable for EWMA)

**API rate limit risk:** 150-day lookback = 150 days of historical OI per ticker per expiry. For SPY with 12 expiries = 1,800 API calls just for seeding. Add progress logging and a seed cache to avoid re-fetching.

**Regime change risk:** The 150-day window could cross a regime shift. The halflife parameter allows exponential decay of old positions.

---

## Feature 3: Per-Expiry Direction Bias (Three Competing Options)

All three run ON TOP of replication + accumulation. They replace the run-level uniform bias at `dealer_positioning.py:402-419` with per-expiry decomposition.

**Env flag:** `DEALER_DIRECTION_MODE` (values: `uniform`, `gamma_weighted`, `whale_decomposed`, `sabr_deviation`, default `uniform`)

### Option A: Gamma-Weighted Decomposition (gamma_weighted)
- Take single run-level `direction_bias` from Direction package
- Split across expiries by each expiry's gross dollar gamma share
- `bias_per_expiry[exp] = direction_bias * (gross_dollar_gamma[exp] / total_gross_dollar_gamma)`
- If an expiry has zero gross gamma, it gets zero bias
- **Cost:** No new API calls. One code block.

### Option B: Per-Expiry Whale OI Decomposition (whale_decomposed)
- Group whale scanner OI by expiry → compute per-expiry whale flow direction
- Requires modifying Direction package (`Direction/whale_scanner.py`) to expose `get_whale_direction_by_expiry(ticker)`
- Returns dict of `{expiry: direction_bias}`
- **Risk:** Modifies Direction package. Make per-expiry field OPTIONAL so existing code is unaffected.

### Option C: SABR Deviation Per-Expiry (sabr_deviation)
- Each expiry already gets its own SABR fit via `vol_surface_reference.compute_vol_surface_reference()`
- Net deviation per expiry = sum of all per-strike deviations
- `bias_per_expiry[exp] = sign(-sum(deviation_by_strike[exp]))` for deviations exceeding `IV_DEADBAND_VOL`
- Falls back to uniform direction_bias if SABR fit fails or < 15 OTM strikes
- **Cost:** No new API calls. Reuses existing SABR fit results.
- **Risk:** Noisy on illiquid expiries (same FM-2 issue). Minimum-strike gate protects.

---

## Backtest Plan

Run `pooled_panel_backtest.py` with 5 variants:
1. `uniform` — current baseline (run-level direction bias)
2. `gamma_weighted` — Option A
3. `whale_decomposed` — Option B
4. `sabr_deviation` — Option C
5. `none` — pure v2.1 vol_surface_replication, no Direction package

Same ticker set as Aug-6 debate (SPY/AAPL/NVDA/TSLA/AMD).
Key metrics: aggregate coef, t-stat, block perm p-value, sign consistency per name.

Winner criteria: beats baseline with p < 0.05 AND survives cross-examination.

---

## Files to Modify

| File | Changes |
|------|---------|
| `Vol_Suite/vol_surface_reference.py` | SABR all-strikes flag (Feature 1) |
| `Vol_Suite/replication_reference.py` | Fix flat-sign bug in accumulation (Feature 2) |
| `Vol_Suite/dealer_positioning.py` | Wire accumulation (Feature 2) + per-expiry direction A/B/C (Feature 3) |
| `Vol_Suite/pooled_panel_backtest.py` | Add direction mode variants to backtest comparison |

## Implementation Order

1. Feature 1 (SABR all-strikes) — parallel, touches only vol_surface_reference.py
2. Feature 2 (accumulation) — sequential, touches replication_reference.py + dealer_positioning.py
3. Feature 3 (direction A/B/C) — after Feature 2, touches dealer_positioning.py + possibly whale_scanner.py
4. Backtest — after all three features implemented
5. Debate — after backtest results

## Escalation Rules

Text Jason via Photon ONLY for:
- Blocking errors that prevent progress (API rate limits, build failures)
- Decisions only Jason can make (Direction package breaking changes)
- Completion notification when all work is done

Do NOT text for: minor test failures, non-blocking warnings, progress updates.
