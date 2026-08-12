# Dealer Positioning Direction — Deep Debate Notes

**Date:** 2026-08-05 · **Task:** t_8ea05d81 · **Prepared from:** t_6ad06963 briefing + DEALER_METHOD_V3_SPEC.md + code verification
**Mode:** Carl command NOT available (checked: no `carl` binary, no `hermes carl`, no Carl tool) → structured multi-voice debate per task fallback. Five voices, chair-moderated, stress-tested against the live implementation and data constraints.

---

## 0. The question

**How should the dealer positioning model determine *direction* more accurately, while keeping the vol replication method (Demeterfi-DDKZ 1999 fair-variance strike) intact?**

Scope guard (constraint §5.1 of the briefing): `variance_swap_live.compute_fair_variance_strike` (L136–L193) imports **neither** `replication_reference` nor `vol_surface_reference`; the dependency is one-directional (dealer_positioning → replication_reference/vol_surface_reference). Any change confined to dealer-direction logic cannot alter the fair-variance output **by construction**. That is the frame every position must respect.

### The referee — empirical ground truth (all voices must survive this)
- **Single expiry (SPY 20261030, 90d, 8/4):** v3 (ΔOI flow) gives a balanced 30L/31S split (v1 60/1 and v2 5/56 were degenerate/unusable), but **all four models non-significant**: best perm p = delta-OI 0.31, v3 perm p 0.90, v2 direction wobbles run-to-run.
- **Pooled panel (AMD/NVDA/TSLA/MU, n=428, ticker FE, 2000 perms, block+full shuffle):** **all four models null** — v1 +0.0000 (0.987/0.978), v2 −0.0000 (0.961/0.931), v3 +0.0005 (0.798/0.704), M2 0.0000 (0.565/0.662). The lone standalone "hit" (MU v1, perm p 0.019 on a degenerate 1/112 split) collapsed to +0.0000 (~0.98) pooled → **false positive**.
- **Spec verdict (verbatim intent):** the problem is **identification** (OI×IV-residual×gamma proxies endogenous to the vol level / too noisy at daily OI granularity), **not power**. "Do not re-run this panel; the remaining honest levers are trade-level flow data and an intraday horizon, not more regression on OI-derived signals."

### Hard constraints (from briefing §5)
1. One-directional dependency — direction edits cannot touch `compute_fair_variance_strike`.
2. `replication_reference._otm_leg_weights`/`_build_weights` are **shared** by replication diagnostics AND the dealer OTM gate — changing OTM classification changes both engines' inputs.
3. `sign_model` must stay in `VALID_SIGN_MODELS` or be extended in `_resolve_sign` + CLI prompt (volatility_suite.py L415) + `_SIGN_MODEL_LABELS` + README.
4. Accuracy gate: sign (short→higher vol) must survive ATM-IV+TTE controls AND permutation null, stable across σ/expiries.
5. Degeneracy: binary sign models collapse day-splits; magnitude-weighted or flow-based signs are required for a testable split.

---

## 1. The debate — five positions

### Voice 1 — FLOW-FIRST (ΔOI is the direction; level is noise)
**Thesis:** Direction must come from **flow, not level**. "Rich IV → dealer short" (current v2.1, `_resolve_sign` L308–L316) is mechanically tied to the IV/vol regime — that is the endogeneity that killed every IV-residual proxy. Replace the per-strike sign source with the ΔOI flow signal: `sign = −sign(ΔOI·direction)` (spec M1), plus M2 net delta-OI (`Σ sign·|delta|·OI` across the chain) as a pure-data reference that needs **no IV fit at all**.

**Pros:**
- Breaks the endogeneity loop at the root — ΔOI is orthogonal to today's vol level by construction.
- Already proven as a **classifier**: v3's balanced 30L/31S split vs v1's 60/1 and v2's 5/56. Degeneracy (constraint 5) fixed.
- Already implemented on the backtest side (`backtest_stage3._net_gamma_v3_oi_flow` L259–L291, `_net_delta_oi` L294–L304) and `replication_reference.compute_accumulated_position` L452+ — the math exists; the gap is wiring it into live `_resolve_sign`.
- Data is retrievable today: `option_bulk_hist_oi` is an existing ThetaData route (no paid vendor).

**Cons / stress-test:**
- **ΔOI ≠ executed flow.** OI change is the net of opens/closes/rolls at daily granularity; a position opened and closed the same day never touches OI. This is precisely the "too noisy at daily OI granularity" caveat in the spec's identification verdict.
- **The referee doesn't forgive it:** v3's pooled perm p 0.798/0.704. The flow model improves *classification* but still shows **zero predictive** regression effect at the daily/5d horizon. This voice must concede: flow fixes the *method's* identification, not the *target's* predictability.
- Live wiring cost: `_resolve_sign` is synchronous per-strike today; ΔOI needs a historical OI fetch per expiry in the live path (latency + 502 retry discipline on bulk routes).

### Voice 2 — SURFACE-TUNER (keep rich/cheap, fix the mechanics)
**Thesis:** Don't throw away the IV-surface insight — the rich/cheap mapping is the only voice that uses the *vol surface* (which is what the suite is about). Direction accuracy improves by (a) porting the continuous tanh weighting live (`_weighted_dealer_sign`, `V2_WEIGHT_SCALE=0.05`, backtest L199–L256) so strikes get magnitude not binary ±1, (b) grounding the scale in data (M3: `σ = median(|dev|) + ε` over OTM legs), and (c) tuning `IV_DEADBAND_VOL` (0.01) and the fitter window (`NEAR_ATM_BAND` 0.15, `MIN_SABR_POINTS` 6) so the reference curve is sharper.

**Pros:**
- Minimal-change, maximal-continuity: the method identity (`vol_surface_replication`, the live default and headless `--pack` default at volatility_suite.py L1105) is preserved.
- No new data dependency; continuous weights fix degeneracy.
- Cheap to test: `sign_sensitivity.py` already quantifies dead-band flip exposure.

**Cons / stress-test:**
- **The referee kills it first:** the spec's central finding (CARL R1-F1) is that "rich IV → dealer short" is *structurally* endogenous to the vol regime. Tuning the dead-band and the reference curve is **re-fitting the confound**, not removing it. Every historical IV-residual sign (AMD v1, TSLA v2 standalone "hits") inverted once ATM-IV+TTE controls were added — the classic vol-clustering confound.
- v2's direction already "wobbles run-to-run"; smoothing the magnitude does not stabilize the *sign*, which is what direction accuracy means.
- Dead-band widening trades confidence for coverage: more strikes fall in-band → resolve 0 → fall back to Layer-1b −1 (short), re-injecting the very bias it tries to fix.
- **Drop verdict (as a standalone):** violates constraint 4 in spirit — it cannot plausibly survive the controls+permutation gate because the proxy is the confound.

### Voice 3 — ECONOMICS-FIRST (dollar gamma + strip weights + term structure)
**Thesis:** The *direction* signal is not the problem — the **aggregation** is. Current live net-gamma is `sign · gamma_raw · OI` summed over a single near-dated expiry: deep-OTM high-OI legs dominate, TTE sensitivity distorts, and one expiry is not a "book." Direction accuracy = economic correctness of the aggregate: (a) **M4** — dollar gamma (`γ·S·OI`) so tail legs don't dominate, and weight each leg by its Demeterfi **replication weight** (`_build_weights`) so the magnitude reflects actual variance-replication notional; (b) **M5** — aggregate the front 2–3 expiries weighted by inverse TTE.

**Pros:**
- Uses data already fetched (spot, gamma, OI, `_build_weights`); no new routes.
- Economically grounded: "short variance" dealers are short the *weighted* replicating strip, not every OTM leg equally.
- Multi-expiry aggregation reflects a real dealer book and defuses the single-expiry TTE sensitivity (spec F4).

**Cons / stress-test:**
- **Magnitudes don't fix a wrong sign.** If the per-strike sign is endogenous (Voice 2's disease), weighting it more correctly still aggregates garbage. M4/M5 presuppose a sound sign layer.
- **Constraint 2 tripwire:** reusing strip *weight magnitudes* (not just keys) from `_build_weights`/`_otm_leg_weights` changes what the shared OTM gate feeds into BOTH engines — must verify `tests/test_variance_swap_replication.py` (validated vs paper Fig. 3) before touching it. The briefing explicitly notes weight magnitudes are deliberately NOT reused today (dealer_positioning.py L406–L434).
- **M5 is empirically pre-tested and dead as a predictor:** the pooled panel *is* M5-style pooling (ticker FE, 4 expiries' worth of names) — all null. Term-structure aggregation as a *direction fix* is refuted by the referee.

### Voice 4 — HONESTY-GATE (no-call layer + re-target the research)
**Thesis:** The empirical record says daily-horizon OI/IV proxies **cannot** separate dealer flow from vol clustering. The most accurate *output* a model can produce today is a **no-call** when evidence is weak: add hysteresis / a minimum |net_gamma| (or |dollar gamma|) before emitting AMPLIFY/DAMPEN (briefing §6E, `print_report` L1115–L1121, `run_dealer_positioning` L1230–L1233), and shift the research budget to the only honest levers: **intraday horizon and trade-level flow**.

**Pros:**
- Zero risk to vol replication (pure output gating — cheapest possible change).
- Immediately implementable, no data dependency.
- Improves *decision* accuracy (precision over recall): fewer confident-but-wrong calls, no flip-flop AMPLIFY/DAMPEN near the zero crossing.
- Aligns with the spec's own conclusion ("remaining honest levers are trade-level flow data and an intraday horizon").

**Cons / stress-test:**
- **Gating is hygiene, not accuracy.** A no-call threshold on a null signal just hides the null; it does not create information. If net_gamma is noise, a dead-band merely randomizes when you're silent.
- |net_gamma| magnitude is itself vol-regime-correlated — a magnitude gate can *reintroduce* the confound constraint 4 forbids.
- Intraday/trade-level data is a real cost (latency, ThetaData route support, backtest redesign) — a research program, not a patch.

### Voice 5 — SYNTHESIS (flow direction + economic magnitude + honesty gate)
**Thesis:** The winning structure is **layered, each layer solving one proven failure**:
1. **Direction source = flow (M1/M2).** ΔOI sign is the only endogeneity-free, non-degenerate sign available from retrievable data. Keep `oi_heuristic`/`replication`/`vol_surface_replication` as selectable references, but the accuracy path is `oi_flow`.
2. **Magnitude = economics (M4).** Dollar gamma + replication-strip weights *under the flow sign* — now the weighting is principled because the sign is principled.
3. **Output = honesty gate (E).** Dead-band on the final net-gamma before AMPLIFY/DAMPEN, with a documented NO-CALL state.
4. **Vol replication untouched** by construction (constraint 1) — `compute_fair_variance_strike` never reads the sign layer.

**Stress-test against the referee:** This voice is the only one that *accepts* the pooled null as its premise: it claims direction *classification* accuracy (which side is the book on) plus *decision* hygiene, and explicitly does **not** claim daily-horizon predictive power for forward vol — it re-targets that claim to intraday/trade-level research as a follow-on. That honesty is what lets it survive the gate.

---

## 2. Cross-examination summary

| Stress test | V1 Flow-first | V2 Surface-tuner | V3 Economics-first | V4 Honesty-gate | V5 Synthesis |
|---|---|---|---|---|---|
| Survives constraint 1 (vol replication intact) | ✅ | ✅ | ✅ (with test guard) | ✅ | ✅ |
| Survives constraint 4 (controls+perm gate) | ⚠️ classifier only, predictor null | ❌ proxy is the confound | ❌ pre-refuted by pooled panel | ⚠️ doesn't claim signal | ⚠️ claims classification+hygiene, re-targets prediction |
| Fixes degeneracy (constraint 5) | ✅ (30L/31S) | ⚠️ continuous helps, sign wobbles | ❌ sign still binary | ❌ | ✅ (flow sign + continuous magnitude) |
| New data needed | `hist_oi` (existing route) | none | none | none | `hist_oi` only |
| Risk to shared OTM gate (constraint 2) | none | none | **high** (weight reuse) | none | low (weights reused only under flow sign, behind the existing gate) |
| Live-implementable this sprint | ✅ (`_resolve_sign` extension) | ✅ | ✅ | ✅ | ✅ (staged) |

---

## 3. Dropped and why

1. **Surface-tuning alone (V2) — DROPPED.** It re-fits the confound the spec already identified (CARL R1-F1): IV-richness→dealer-short is mechanically tied to the vol regime; every historical IV-residual "hit" inverted under controls. Tuning `IV_DEADBAND_VOL`/fitter parameters cannot survive constraint 4 because the proxy *is* the endogeneity. Kept only as a selectable legacy reference (`vol_surface_replication` remains the default for continuity, with a documented accuracy caveat).
2. **Economics-first alone (V3) — DROPPED as a direction fix.** M4's strip-weight reuse risks constraint 2 (shared OTM gate, validated vs. paper Fig. 3), and M5-style pooling is *already refuted by the referee* (n=428 panel, all null). Magnitudes are adopted **under the flow sign** (V5), never as a standalone sign source.
3. **Honesty-gate alone (V4) — DROPPED as a standalone.** A no-call threshold on a null signal is silence, not accuracy; |net_gamma| gating risks re-correlating with the vol regime. Adopted as the output layer of V5, where it does real work (flip-flop suppression on a *plausible* signal).
4. **More OI-regression research — DROPPED explicitly.** The spec forbids re-running the panel ("do not re-run this panel"); the identification problem is not a power problem.
5. **v1 `oi_heuristic` — DROPPED as a direction candidate.** Degenerate 60/1 splits, unmeasurable, and the MU v1 "hit" was a documented false positive.

---

## 4. WINNING NARRATIVE

> **Direction is a flow question, not a surface question. Determine *which side the book is on* with ΔOI flow (endogeneity-free, non-degenerate), weight it economically (dollar gamma × replication-strip notional), gate the output honestly (no-call below a minimum evidence threshold), and keep the Demeterfi fair-variance computation byte-for-byte untouched. Claim classification + decision accuracy now; re-target forward-vol *prediction* to the intraday/trade-level horizon as the explicitly separated research track.**

Rationale in one line: of the five positions, only the synthesis is consistent with every empirical fact (all daily proxies null → don't predict vol from OI regressions; v3 balanced split → flow fixes the classifier; pooled panel null → don't promise term-structure magic) while still delivering an implementable, testable change to the live model.

---

## 5. Concrete recommendation for implementation/testing

**Phase 1 — live `oi_flow` sign model (this sprint):**
1. Extend `VALID_SIGN_MODELS` in `dealer_positioning.py` L260 with `'oi_flow'`; add the branch in `_resolve_sign` (L263–L316): `sign = −sign(ΔOI_k · direction)` per (k, right), direction = +1 call / −1 put (consistent with `_dealer_sign` L229–L257); missing/zero ΔOI → 0 (no contribution), never a forced fallback.
2. Add the ΔOI fetch: `option_bulk_hist_oi` (yesterday vs. prior day, per expiry) — reuse the `compute_accumulated_position` plumbing (replication_reference.py L452+) which already implements the M1 accumulation.
3. Thread the model string: volatility_suite.py L415 prompt map, dealer_positioning.py L1142, `_SIGN_MODEL_LABELS` (L747–L751 / L1045–L1049), README, and the headless `--pack` default decision at L1105 (keep `vol_surface_replication` as the shipped default for continuity; `oi_flow` becomes the accuracy-recommended option — do NOT silently change the headless default without a release note).
4. Magnitude: apply `γ·spot·100·OI` (dollar gamma, already computed at L499–L501) under the flow sign — M4's dollar-gamma half. **Do not** reuse `_build_weights` *magnitudes* until the shared-gate test guard (§3.1) passes; start with keys-only gating as today.
5. Output gate: in `print_report` (L1115–L1121) and `run_dealer_positioning` (L1230–L1233), replace the bare `net_gamma > 0` boundary with a dead-band: emit NO-CALL when `|net_dollar_gamma|` is below a floor (propose: 0.5× its 90-day rolling median, configurable); AMPLIFY/DAMPEN only outside it.

**Phase 2 — testing (must all pass):**
- Unit: synthetic OI-change series → ΔOI→sign mapping recovery (spec M1 test angle, network-free); no-call gate fires below floor and is silent at exactly 0; `oi_flow` respects the OTM gate keys.
- Regression gate (constraint 4): `backtest_stage3.py` with `oi_flow` as a 5th proxy — coef sign (short→higher vol) under ATM-IV+TTE controls; report perm p; **expectation set: classification quality + balanced split, prediction may remain null at daily horizon — that is acceptable and documented**, not a failure, because Phase 3 owns prediction.
- Degeneracy: assert day-split for `oi_flow` has ≥20% of days on each side (constraint 5).
- Shared-gate regression: run `tests/test_variance_swap_replication.py` (paper Fig. 3 validation) untouched — must stay green with zero changes to `_otm_leg_weights`/`_build_weights`.
- Vol replication invariance: assert `compute_fair_variance_strike` output is identical before/after the change for a fixed chain snapshot (constraint 1, by construction — verify anyway).

**Phase 3 — the separated prediction track (research, not a patch):**
- Intraday horizon backtest (dealer tape-amplification is an intraday effect per spec) using trade-level or intraday-sampled flow; trade-level ΔOI at higher frequency is the only lever the referee leaves open.
- Do NOT re-run the daily pooled panel.

**Sequencing (matches spec recommendation M1+M2 → M4 → M3/M5):** Phase 1 here = M1+M2 (live) + M4-dollar-gamma half + E-gate; M3 (data-grounded scale) and M5 (multi-expiry sensitivity) remain optional robustness after Phase 2 gates pass.

---

*Debate run under task fallback (no Carl command). Chair's note: the referee (all-null empirical record) is the reason every voice had to state its predictive claim precisely — the synthesis wins precisely because it is the only structure whose claims match the evidence.*
