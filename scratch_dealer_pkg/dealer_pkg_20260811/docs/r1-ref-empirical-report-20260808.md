# R1-REF Empirical Referee Report — Dealer Positioning Debate

**Date:** 2026-08-08
**Evidence kit hash:** `5444b4f398b769bc` (truncated SHA-256, first 16 hex chars)
**Full SHA-256:** `5444b4f398b769bcb4c6dbcbca302bb76d79e81acd7597d7caa24a61229ac983`
**Hash verified:** ✅ MATCH

---

## verification_results

| # | Claim | Status | Path:Line | Evidence |
|---|-------|--------|-----------|----------|
| 1 | DDKZ weights guaranteed ≥ 0 | **VERIFIED** | `replication_reference.py:82-103` | `_build_weights` computes `w = slope - cum` where `slope = (f(Ki) - f(Kim1)) / denom`. f(K) = (2/T)((K-S*)/S* - ln(K/S*)) is convex with minimum at S*. The discrete second difference is always ≥ 0. Empirical: min call weight = 6.57e-5, min put weight = 6.65e-5, zero negatives across 66 strikes. |
| 2 | Weights approach 0 at deep OTM | **PARTIALLY VERIFIED — ASYMMETRIC** | `replication_reference.py:78-79` (f_payoff), empirical computation | **Call wing:** weights decay from 1.30e-4 (K=560, 1.8% OTM) to 7.94e-5 (K=715, 30% OTM) — 1.6× decay. **Put wing:** weights GROW from 6.65e-5 (K=545, 0.9% OTM) to 2.63e-4 (K=385, 30% OTM) — 4× GROWTH. The claim is TRUE for calls, FALSE for puts. |
| 3 | `vol_surface_replication` discards weight magnitude | **VERIFIED** | `dealer_positioning.py:384-391` | `_resolve_sign` returns only `0.0`, `+1.0`, `-1.0`, or `vs_sign` (which is also ±1.0 from `vol_surface_reference.resolve_vol_surface_sign`). Replication weight magnitude is NEVER used. |
| 4 | Aggregation: `gamma_by_strike[k] += sign * gamma * OI` | **VERIFIED** | `dealer_positioning.py:839-840` | Exact code: `gamma_by_strike[k] += sign * gamma_val * oi` and `dollar_gamma_by_strike[k] += sign * dollar_gamma`. No code path multiplies by replication weight. |
| 5 | SABR fit is unweighted | **VERIFIED** | `vol_surface_reference.py:240-294` | `fit_sabr_reference` docstring states "Deliberately UNWEIGHTED (equal weight per strike in IV space)". Line 291: `err += (sv - mv) ** 2` — simple squared error, no weighting. |
| 6 | NO_CALL gate uses net dollar gamma magnitude | **VERIFIED** | `dealer_positioning.py:317-318` | `if abs(result.total_net_dollar_gamma) < eff_floor: return "NO_CALL"`. Floor = `NO_CALL_FLOOR_FRAC * abs(gross_gamma_exposure)`, default frac = 0.05 (5% of gross unsigned book). |
| 7 | BS gamma decays at deep OTM | **VERIFIED** | `dealer_positioning.py:218-225` | Formula: `norm_pdf(d1) / (S * sigma_sqrt_T)`. Computed: ATM=7.24e-3, 20% OTM call=1.51e-3, 40% OTM call=2.98e-5, 60% OTM call=1.46e-7. Exponential decay via φ(d1). |

---

## weight_profile

**DDKZ replication weights for SPY-like chain: spot=550, T=0.25, strikes 385–715 (5-point grid)**

### Call Wing (OTM calls above spot)

| Strike | OTM% | Weight | Weight/Max | Relative to ATM-region |
|--------|------|--------|------------|----------------------|
| 555 | 0.9% | 6.57e-5 | 0.506 | 1.00× (nearest to S*) |
| 560 | 1.8% | 1.30e-4 | 1.000 | **MAX** |
| 600 | 9.1% | 1.13e-4 | 0.870 | 0.87× |
| 660 | 20.0% | 9.32e-5 | 0.718 | 0.72× |
| 715 | 30.0% | 7.94e-5 | 0.611 | 0.61× |

**Observation:** Call wing weights decay SLOWLY — only 1.6× from max to 30% OTM.

### Put Wing (OTM puts below spot)

| Strike | OTM% | Weight | Weight/Max | Relative to ATM-region |
|--------|------|--------|------------|----------------------|
| 545 | 0.9% | 6.65e-5 | 0.253 | 1.00× (nearest to S*) |
| 500 | 9.1% | 1.57e-4 | 0.596 | 2.36× |
| 440 | 20.0% | 2.02e-4 | 0.768 | 3.04× |
| 385 | 30.0% | 2.63e-4 | 1.000 | **MAX** (deepest OTM) |

**Observation:** Put wing weights GROW as K decreases — 4× from ATM-region to 30% OTM. This is the continuous-limit behavior w(K) ~ 2/(T·K²) manifesting in the discrete recursion.

### Summary Table (requested OTM levels)

| OTM Level | Call Strike | Call Weight | Put Strike | Put Weight |
|-----------|-------------|-------------|------------|------------|
| 20% | 660 | 9.32e-5 | 440 | 2.02e-4 |
| 40% | **OUT OF RANGE** (K=770 > 715) | N/A | **OUT OF RANGE** (K=330 < 385) | N/A |
| 60% | **OUT OF RANGE** (K=880 > 715) | N/A | **OUT OF RANGE** (K=220 < 385) | N/A |

**Note:** The chain only extends to 715 (calls) / 385 (puts), covering ~30% OTM on each wing. Extrapolation to 40%/60% OTM requires live chain data beyond this grid.

---

## gamma_profile

**BS gamma for SPY: S=550, T=0.25, σ=0.20, r=0, q=0**

| Description | Strike | OTM% | d1 | φ(d1) | Gamma | % of ATM |
|-------------|--------|------|----|-------|-------|----------|
| ATM | 550.0 | 0% | 0.0500 | 0.39844 | 7.244e-3 | 100.000% |
| 10% OTM Call | 605.0 | 10% | -0.9031 | 0.26534 | 4.824e-3 | 66.59% |
| 10% OTM Put | 495.0 | 10% | 1.1036 | 0.21699 | 3.945e-3 | 54.46% |
| 20% OTM Call | 660.0 | 20% | -1.7732 | 0.08282 | 1.506e-3 | 20.79% |
| 20% OTM Put | 440.0 | 20% | 2.2814 | 0.02956 | 5.374e-4 | 7.42% |
| 30% OTM Call | 715.0 | 30% | -2.5736 | 0.01454 | 2.644e-4 | 3.65% |
| 30% OTM Put | 385.0 | 30% | 3.6167 | 0.00058 | 1.047e-5 | 0.14% |
| 40% OTM Call | 770.0 | 40% | -3.3147 | 0.00164 | 2.983e-5 | 0.41% |
| 40% OTM Put | 330.0 | 40% | 5.1583 | 1.21e-6 | 1.210e-8 | 0.0002% |
| 60% OTM Call | 880.0 | 60% | -4.6500 | 8.28e-6 | 1.463e-7 | 0.002% |
| 60% OTM Put | 220.0 | 60% | 9.2129 | ~0 | 2.689e-21 | ~0% |

**Key asymmetry:** Put-side gamma decays FASTER than call-side gamma at the same OTM percentage, because d1 shifts more for OTM puts (ln(S/K) is large positive, pushing d1 into the tail).

---

## architecture_facts

**Verified architectural facts about how the model actually works:**

1. **Five sign models exist** (`dealer_positioning.py:287`): `oi_heuristic` (v1), `replication` (v2), `vol_surface_replication` (v2.1, default), `oi_flow` (v4), `direction` (v5). All share the same aggregation path.

2. **OTM gate is binary** (`dealer_positioning.py:767-768`): `otm_strikes = set(weights_this_expiry.keys())` — the set of (strike, right) keys from `_otm_leg_weights`. Only the KEYS are used, not the weight values.

3. **Sign is per-strike, magnitude is binary** (`dealer_positioning.py:384-391`): For `vol_surface_replication`, sign comes from SABR deviation (`+1` cheap, `-1` rich, `0` deadband) or falls back to flat `-1`. No continuous magnitude.

4. **Gamma comes from ThetaData, not computed** (`dealer_positioning.py:806`): `gamma_val = _to_float(row['gamma'])` — the gamma is pulled from the data provider, not recomputed from BS. The BS formula at lines 218-225 is used only for the spot/gamma surface grid computation (lines 948-961).

5. **Aggregation is uniform across all sign models** (`dealer_positioning.py:839-840`): `gamma_by_strike[k] += sign * gamma_val * oi` — the same formula regardless of sign model. No path multiplies by replication weight.

6. **SABR fit is deliberately unweighted** (`vol_surface_reference.py:249-268`): The docstring explains that vega-weighted SABR barely constrains deep-OTM strikes, producing poor fits there. Equal-weighting was chosen because Layer 1a needs to read deviations at deep-OTM strikes (where overwriting flow concentrates).

7. **Deadband on vol surface sign** (`vol_surface_reference.py:107`): `IV_DEADBAND_VOL = 0.01` — deviations within ±1 vol point are treated as "no read" (return 0.0), preventing noise from flipping sign.

---

## double_counting_check

### Are gamma decay and replication weight decay correlated?

**Computation: SPY chain, spot=550, T=0.25, σ=0.20, strikes 385–715**

| Wing | Pearson r(weight_norm, gamma_norm) | Interpretation |
|------|-----------------------------------|----------------|
| Call | **+0.766** | POSITIVE — both decrease together (but weight decays 1.6× while gamma decays ~50×) |
| Put | **−0.907** | NEGATIVE — weight INCREASES while gamma DECREASES |

### Call Wing Detail (20% OTM example)
- Weight at K=560 (1.8% OTM): 1.30e-4 (normalized: 1.000)
- Weight at K=660 (20% OTM): 9.32e-5 (normalized: 0.718) → **1.4× decay**
- Gamma at K=560: 6.79e-3 (normalized: 0.937)
- Gamma at K=660: 1.51e-3 (normalized: 0.208) → **4.5× decay**
- Weight×Gamma at K=560: 8.83e-7 → at K=660: 1.41e-7 → **6.3× combined decay**

### Put Wing Detail (20% OTM example)
- Weight at K=540 (1.8% OTM): 1.35e-4 (normalized: 0.512)
- Weight at K=440 (20% OTM): 2.02e-4 (normalized: 0.768) → **1.5× GROWTH**
- Gamma at K=540: 7.04e-3 (normalized: 0.983)
- Gamma at K=440: 5.37e-4 (normalized: 0.074) → **13.1× decay**
- Weight×Gamma at K=540: 9.49e-7 → at K=440: 1.09e-7 → **8.7× combined decay**

### Critical Finding: No Double-Counting Exists

**The code does NOT multiply gamma by replication weight.** The aggregation is:
```
gamma_by_strike[k] += sign * gamma_val * oi    # sign ∈ {-1, 0, +1}
```

Replication weights determine WHO is in the OTM set (binary gate), not HOW MUCH they contribute. Therefore:

1. **Gamma decay** (BS formula) operates through `gamma_val` — applied once.
2. **Weight decay** (DDKZ recursion) operates only through the binary OTM gate — a strike with weight=6.57e-5 gets identical treatment to one with weight=2.63e-4.
3. **No multiplication of weight × gamma occurs anywhere in the aggregation path.**

The two decays are **architecturally decoupled** — gamma decay enters the computation, weight decay does not. The "double counting" concern is **invalid for the current code**.

### However: The Weight Decay IS Being Wasted

The replication weights carry information about which strikes matter more for variance replication. By using them only as a binary gate, the model treats a deep-OTM strike at the edge of the chain (weight≈0) identically to a near-ATM strike (weight=max). This means:

- Deep-OTM strikes with near-zero replication weight get full ±1 sign treatment
- Their (already small) gamma contribution is amplified to the same degree as near-ATM strikes
- This could be a source of noise, not double-counting

---

## confidence_in_findings

**0.95**

Rationale:
- Hash verification: 1.0 (trivial SHA-256 check)
- DDKZ weight non-negativity: 0.99 (empirically verified across 66 strikes, matches mathematical proof)
- Weight asymmetry (call decay / put growth): 0.98 (directly computed from the actual recursion code)
- `_resolve_sign` discards magnitude: 0.99 (exact code lines cited and verified)
- Aggregation formula: 0.99 (exact code lines cited and verified)
- SABR unweighted: 0.99 (docstring + error function code verified)
- NO_CALL gate: 0.98 (exact code lines cited and verified)
- BS gamma computation: 1.0 (standard formula, independently verified)
- Correlation analysis: 0.95 (computed from actual weights and gamma values)
- Architecture interpretation: 0.90 (code reading is unambiguous but complex systems can have hidden paths)

Not 1.0 because: the codebase is large (1700+ lines in dealer_positioning.py) and there could be alternative code paths not in the primary aggregation loop that I did not examine. Also, the GammaRecord.applied_sign field stores the sign per-record but I did not trace every downstream consumer of that field.

---

## escalations

1. **Chain range limitation:** The SPY-like chain only extends to ±30% OTM (385–715). Claims about 40%/60% OTM weight behavior require live chain data beyond this grid. The BS gamma values at those levels are computed analytically but have no matching DDKZ weights.

2. **ThetaData gamma vs BS gamma:** The live code uses gamma from ThetaData (`row['gamma']`), not the BS formula. The BS formula at lines 218-225 is used only for the hypothetical surface grid. Real gamma values may differ from theoretical BS gamma due to discrete dividends, borrow costs, and market microstructure.

3. **Other sign models:** This report focused on `vol_surface_replication` (default). The `direction` (v5) model applies a UNIFORM run-level bias to all OTM legs — no per-strike granularity. The `oi_flow` model uses ΔOI flow. Each has different implications for the weight-discard question.

4. **GammaRecord.applied_sign field:** The code stores `applied_sign` per record (`dealer_positioning.py:836`). I did not trace whether downstream consumers (gamma-flip surface, charts) respect this sign consistently. A mismatch there could produce inconsistent outputs.

5. **Reference model MiniMax-m3 correction:** MiniMax-m3 correctly identified that DDKZ weights GROW on the put wing as K→0 (continuous limit: w~2/(T·K²)). My empirical computation confirmed this: put weights grow 4× from ATM to 30% OTM. This means the "weights decay at wings" claim is **asymmetric** — true for calls, false for puts.

---

## best_honest_answer

### What the code actually does (empirically verified):

1. **DDKZ recursion** (`replication_reference.py:82-103`): Computes non-negative replication weights via a discrete second-difference recursion on the log-payoff f(K) = (2/T)((K-S*)/S* - ln(K/S*)). Weights are guaranteed ≥ 0 by convexity of f. On a SPY-like chain, call weights decay slowly (1.6× over 30% OTM range), while put weights GROW (4× over the same range). This is the 1/K² continuous-limit behavior.

2. **OTM gate** (`dealer_positioning.py:767-768`): The set of (strike, right) keys from `_otm_leg_weights` is used as a binary gate. Only the KEYS enter the sign resolution. Weight magnitudes are discarded at this point.

3. **Sign resolution** (`dealer_positioning.py:384-391`): For `vol_surface_replication`, returns ±1 based on SABR deviation (rich=−1, cheap=+1) or falls back to flat −1. Never returns replication weight magnitude.

4. **Aggregation** (`dealer_positioning.py:839-840`): `gamma_by_strike[k] += sign * gamma_val * oi`. No replication weight multiplication. The formula is identical across all five sign models.

5. **SABR reference** (`vol_surface_reference.py:280-294`): Unweighted equal-strike fit across the whole OTM strip. Deliberately chosen because vega-weighted SABR poorly constrains deep-OTM fits.

6. **NO_CALL gate** (`dealer_positioning.py:317-318`): Fires when `|net dollar gamma| < 5% × gross gamma exposure`. Uses unsigned gross book as the reference, not signed net.

### The bottom line for the debate:

**There is no double-counting of gamma/weight decay in the current code.** The replication weights determine membership in the OTM set (binary in/out), not the magnitude of contribution. Gamma decay enters through the BS formula (or ThetaData gamma), weight decay is architecturally excluded from the aggregation.

**The deeper issue is not double-counting but INFORMATION LOSS:** the replication weights encode which strikes matter more for variance replication, and this information is discarded after the binary gate. A deep-OTM strike with weight≈0 gets identical sign treatment to a near-ATM strike with weight=max. Whether incorporating weight magnitude would improve the model is an empirical question for Track A of the debate — but it is not a bug in the current code, it is a design simplification (explicitly documented at `replication_reference.py:478-481`).
