# Dealer Positioning Model & Vol Replication — Code Briefing

**Date:** 2026-08-05 · **Task:** t_6ad06963 · **Scope:** volatility_suite.py + the modules it drives

---

## 1. Architecture — what lives where

`volatility_suite.py` is the **CLI front-end / orchestrator**; it contains *no model math itself*.
It resolves ticker/basket/expiry, then calls the two independent engines:

| Engine | Module | Entry point | Purpose |
|---|---|---|---|
| **Vol replication** | `variance_swap_live.py` | `run_variance_swap_live()` (L427) → `compute_fair_variance_strike()` (L136) | Demeterfi-Derman-Kamal-Zou (1999) discrete fair-variance-strike ("fair vol") for index + focus ticker |
| **Dealer positioning** | `dealer_positioning.py` | `run_dealer_positioning()` (L1197) → `compute_dealer_positioning()` (L319) | Net dealer gamma sign → dampen/amplify direction, gamma-flip level, heatmap |
| Shared OTM-strip math | `replication_reference.py` | `_build_weights()` (L82), `_otm_leg_weights()` (L431) | Demeterfi Appendix-A recursion; used by BOTH engines (see §5 constraint) |
| Layer 1a IV reference | `vol_surface_reference.py` | `compute_vol_surface_reference()` (L343), `resolve_vol_surface_sign()` (L422) | Rich/cheap deviation per strike → per-strike dealer sign |
| Accuracy harness | `backtest_stage3.py` | `run_backtest()` (L663), `_summarize_regression()` (L555) | Regression w/ controls + permutation null over the 4 sign models |

**Call path in volatility_suite.py:**
- Sign-model choice prompt: `_prompt_sign_model_and_options_chain()` L405–L421 (values at L415: `'1'=oi_heuristic`, `'2'=replication`, default `'3'=vol_surface_replication`).
- Step 2 — vol replication on chosen index + focus ticker: L612–L639 (`vsl.run_variance_swap_live`).
- Step 3 — opportunities read consuming the fair-vol spread: L641–L690 (uses `fair_variance_swap_strike_vol_pct`, dispersion score, beta).
- Step 4 — dealer positioning: L739–L750 (`dp.run_dealer_positioning(ticker, target_years, output_dir, save_csv=True, expiration, sign_model)`).
- Headless `--pack` mode hardcodes `sign_model = "vol_surface_replication"` at **L1105**.

---

## 2. Dealer positioning model — exact inputs, formulas, thresholds, outputs

**Data sources (all ThetaData via `thetadata_client.py`):** `fetch_spot_price`, `fetch_dividend_yield`, `fetch_risk_free_rate` (live, fallback `RISK_FREE_RATE=0.05`, dealer_positioning.py L38/L358–L359), `list_expirations`, `option_bulk_greeks`, `option_bulk_oi`. Historical flow: `option_bulk_hist_oi/greeks` (replication_reference.py L507–L508).

**Sign conventions (`_dealer_sign` L229–L257, `_resolve_sign` L263–L316):**
- v1 `oi_heuristic`: flat **call=+1 / put=−1** on every strike (GEX-style convention; documented as a *modeling assumption*, not a measured fact — L236–L243).
- v2 `replication` (Layer 1b): dealer is **short whatever the Demeterfi strip is long** → OTM legs only, flat **−1**; ITM legs contribute **0** (L304–L307).
- v2.1 `vol_surface_replication` (Layer 1a+1b): same OTM gate, but per-strike sign from IV deviation: **rich (dev>+dead-band) → −1 short; cheap (dev<−dead-band) → +1 long; in-band/None → 0, falls back to −1** (L308–L316).

**Formulas (compute_dealer_positioning L319–L669):**
- Forward: `S0·exp((r−q)·T)` (L360, via L220–L221). T = resolved expiry TTE in years.
- Per-record contribution: `sign · gamma_raw · OI` → `gamma_by_strike`; dollar gamma `= gamma·spot·100·OI` (L485, L499–L501).
- `net_gamma = Σ gamma_arr`, `net_dollar_gamma = Σ dollar_gamma_arr` (L543–L544).
- `hedge_requirement = |net_dollar_gamma|·0.01` shares per 1% move (L546); option-contract equivalent L561–L571.
- Gamma-flip level = spot where total dealer gamma crosses 0, interpolated from the 50×50 (spot × IV) BS-gamma surface, nearest crossing to current spot; **falls back to forward if no crossing** (L580–L623).

**Thresholds / knobs:**
| Knob | Value | Location |
|---|---|---|
| `IV_DEADBAND_VOL` (sign flip dead-band) | **0.01** (= 1 vol pt) | vol_surface_reference.py L107, applied L443–L447 |
| `NEAR_ATM_BAND` (quadratic fit window) | 0.15 | vol_surface_reference.py L79 |
| `MIN_SABR_POINTS` (SABR primary fitter gate) | 6 | vol_surface_reference.py L90 |
| Surface TTE window (`max_tte`) | 2.0 yr (or `max_days`), L368 | dealer_positioning.py |
| Greek-comparison window default | 150 d | dealer_positioning.py L1199 |
| `MIN_HEDGE_OI` | 50 | replication_reference.py L57 (used L561) |
| Aggregation **interpretation** threshold | `net_gamma > 0 → DAMPENING else AMPLIFYING` (no dead-band/hysteresis) | print_report L1115–L1121; run_dealer_positioning L1230–L1233 |

**Output signals:** `total_net_gamma` (sign → AMPLIFY/DAMPEN), `total_net_dollar_gamma`, `hedge_requirement`, `gamma_flip_level` ("critical level"), `highest_gamma_strike`, per-strike gamma/OI charts, gamma-vs-spot profile, 4-panel Greek exposure. CSV `gamma_records_*.csv` (L1256–L1264).

---

## 3. Vol replication method — exact inputs, formulas, outputs

**Where:** `variance_swap_live.compute_fair_variance_strike` (L136–L193). Data: same chain fetch (L81–L119, bid/ask mid or last, IV per strike).

**Formula (L144–L157, Demeterfi-Derman-Kamal-Zou 1999):**
```
OTM(K) = put mid if K <= F else call mid
σ² = (2/T)·e^{rT} · Σ_i (ΔK_i / K_i²) · OTM(K_i)  −  (1/T)·(F/K0 − 1)²
```
- `F = S0·e^{(r−q)T}`; `ΔK` = half-width spacing (L63–L70); `K0` = largest strike ≤ F (boundary correction, L154–L156).
- Outputs: `fair_variance_swap_strike_vol_pct`, `atm_implied_vol_pct`, `convexity_premium_vol_pct` (L173–L193) — consumed at volatility_suite.py L645–L657 for the vol-spread/dispersion read.

**Important:** this computation **never reads dealer sign, OI, or gamma**. It is a pure function of the OTM option mid curve + forward. The two engines share only `replication_reference._build_weights/_otm_leg_weights` (see §5).

---

## 4. Existing accuracy / backtest notes

- **Spec:** `DEALER_METHOD_V3_SPEC.md` (current accuracy-focused spec; M1–M6) and `DEALER_POSITIONING_V2_DESIGN.md` (v2 design, §8 testing plan, §9 honest risks).
- **Backtest harness:** `backtest_stage3.py` — per-day records w/ `atm_iv` + TTE controls (L110+), **primary test = OLS of forward realized vol on continuous net-gamma w/ permutation null** (`_summarize_regression` L555+); 4 models: `_net_gamma_v1` (L184), `_net_gamma_v2` w/ `_weighted_dealer_sign = −tanh(dev/0.05)` (L199–L256, `V2_WEIGHT_SCALE=0.05` L87), `_net_gamma_v3_oi_flow` (ΔOI flow, L259–L291), `_net_delta_oi` (M2, L294–L304). Panel pooling: `pooled_panel_backtest.py` + `tests/test_pooled_panel_backtest.py`.
- **Results (from DEALER_METHOD_V3_SPEC.md, 8/4):** single-expiry SPY 20261030 90d — v3 gives a balanced 30L/31S split (v1 60/1 and v2 5/56 were degenerate), but **all models non-significant** (best perm p = delta-OI 0.31; v3 perm p 0.90). Pooled panel AMD/NVDA/TSLA/MU n=428: **all four models null** (v3 coef +0.0005, perm p 0.798); the one-time MU v1 "hit" (perm p 0.019 on a degenerate 1/112 split) collapsed to null when pooled → false positive. **Conclusion recorded in spec: identification problem (OI×IV-residual proxies endogenous to vol level), not power; remaining honest levers are trade-level flow data + intraday horizon, not more OI regressions.**
- **Sign-stability tooling:** `sign_sensitivity.py` (dead-band flip analysis, `assess_sign_stability`), `tests/test_sign_sensitivity.py`.
- **Tests covering the model:** `tests/test_dealer_positioning*.py`, `tests/test_backtest_stage3.py` (21 tests incl. regression-recovery L170, no-spurious-signal L255), `tests/test_replication_reference_accumulation.py`, `tests/test_variance_swap_replication.py`, `tests/test_vol_surface_reference.py`.

---

## 5. Constraints (what must not break)

1. **One-directional dependency:** dealer_positioning imports `replication_reference._otm_leg_weights` (gate only — keys used, **weight magnitudes deliberately NOT reused**, dealer_positioning.py L406–L434 & `_resolve_sign` docstring) and `vol_surface_reference` (Layer 1a). The fair-variance formula in `variance_swap_live` imports neither — so **editing dealer-direction logic cannot alter the vol-replication output by construction**, as long as you don't touch `compute_fair_variance_strike` or the OTM mid-curve fetch.
2. **Shared function risk:** `replication_reference._otm_leg_weights` / `_build_weights` are used by BOTH the replication diagnostics (`compute_replication_reference`, `compute_accumulated_position`) AND the dealer OTM gate. Changing the OTM classification (e.g. min-strikes-per-side at L440–L441, or spot-vs-forward boundary) changes both engines' inputs — verify against `tests/test_variance_swap_replication.py` (validated vs. paper Fig. 3).
3. **Sign-model threading:** the `sign_model` string must stay one of `VALID_SIGN_MODELS = ('oi_heuristic','replication','vol_surface_replication')` (dealer_positioning.py L260) or be extended in `_resolve_sign` + the CLI prompt maps (volatility_suite.py L415, dealer_positioning.py L1142) + `_SIGN_MODEL_LABELS` (L747–L751, L1045–L1049) + README.
4. **Accuracy gate (per DEALER_METHOD_V3_SPEC.md):** any direction change must keep the primary regression honest — sign (short→higher vol) must survive ATM-IV+TTE controls AND the permutation null, and stay stable across σ / expiries. Historical evidence says IV-richness-based signs fail this gate; ΔOI-flow (M1) is the intended replacement.
5. **Degeneracy:** binary sign models collapse day-splits (v1 60/1, v2 5/56). Magnitude-weighted (`tanh`, V2_WEIGHT_SCALE) or flow-based signs are needed for the split to be testable.

---

## 6. Candidate modification points — direction accuracy WITHOUT touching vol replication

All of these change the *dealer direction* signal only; the fair-variance computation (variance_swap_live.py L136–L193) is untouched.

**A. Sign resolution per strike (highest leverage)**
- `dealer_positioning._resolve_sign` L263–L316 — add a 4th model or alter the rich/cheap mapping. The spec's **M1 (ΔOI flow sign, `sign = −sign(ΔOI·direction)`, endogeneity-free)** and **M2 (net delta-OI)** are already implemented on the backtest side (`backtest_stage3._net_gamma_v3_oi_flow` L259–L291, `_net_delta_oi` L294–L304) and in `replication_reference.compute_accumulated_position` L452+ — wiring a ΔOI-based sign into the *live* `_resolve_sign` is the concrete next step (needs `option_bulk_hist_oi` fetch, per spec M1 "How").
- `_dealer_sign` L229–L257 — the call=+/put=− convention itself; flipping it flips every direction output (documented assumption, L236–L243).

**B. Layer 1a thresholds (vol_surface_reference.py)**
- `IV_DEADBAND_VOL` L107 (0.01) — widen/narrow the band: fewer/more strikes get a confident direction (in-band resolves 0 → Layer-1b fallback −1). `resolve_vol_surface_sign` L422–L447 is the exact flip logic; `sign_sensitivity.py` quantifies flip exposure.
- Fitter choice: SABR (`fit_sabr_reference` L240) vs quadratic fallback (`fit_reference_curve` L316), `NEAR_ATM_BAND` L79, `MIN_SABR_POINTS` L90 — changes the reference curve, hence each strike's deviation sign.

**C. OTM gate (replication_reference._otm_leg_weights L431–L449)**
- Boundary (spot vs forward), min-2-strikes-per-side requirement, or weight-magnitude reuse (M4: dollar gamma + strip weights — spec L83–L94). Watch constraint §5.2.

**D. Aggregation & magnitude**
- Expiry window: `max_tte` L368 (2.0 yr) and `greek_days_window` (150 d default, L1199) — which expiries' gamma drives the direction; **M5 multi-expiry inverse-TTE weighting** (spec L96–L101).
- Magnitude weighting in live aggregation: currently binary ±1/OI; `_weighted_dealer_sign` (backtest L199–L216, `V2_WEIGHT_SCALE=0.05` L87) is the backtest-only tanh version. Porting continuous weighting live (or changing V2_WEIGHT_SCALE — M3, ground it in data per spec L74–L81) changes the day-level direction split degeneracy.

**E. Interpretation / decision layer (cheapest, no model change)**
- Aggregate direction threshold: `print_report` L1115–L1121 and `run_dealer_positioning` L1230–L1233 use a single `net_gamma > 0` boundary with no dead-band — add hysteresis / a minimum |net_gamma| (or |dollar gamma|) before emitting AMPLIFY/DAMPEN; this is pure output-gating and cannot affect vol replication.
- `hedge_requirement` scaling (L546) and the flip-level fallback-to-forward behavior (L623) are additional presentation knobs.

**Recommended sequencing (per DEALER_METHOD_V3_SPEC.md):** M1+M2 (ΔOI flow signs) → M4 (dollar-gamma/strip magnitudes) → M3/M5 (robustness) — each gated on the primary regression surviving controls + permutation null.
