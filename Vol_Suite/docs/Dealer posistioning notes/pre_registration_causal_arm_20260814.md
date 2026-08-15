# ROUND-10 — Causal-Arm Pre-Registration (Dealer-Exposure-Dev CARL loop)

**Date:** 2026-08-14
**Status:** PRE-REGISTRATION — to be audited by a read-only design/power panel BEFORE any acquisition. Not yet executed.
**Locked context (do NOT relitigate):** live model = `vol_surface_replication` + SVI + `IV_DEADBAND 0.01` + `DEALER_VANNA_FLOW=1` + accumulation ON; `rec.vanna = −1×BS`; real spot. New expiry-book model (`expiry_book_exposure.py`) stays **descriptive/conditional** throughout this acquisition and does NOT get promoted by a positive result. Prior R3 rulings: NOT ACCEPTED (re-admitted to evidence only).

## 1. Why this arm exists (Cem's lever)

Cem (R3, deleg_c46cd30b) closed the convention-distance axis for promotion: more convention-distance rows are "a rounding error on a coin-flip gate." The ONLY lever that moves him toward APPROVAL is a **powered independent-day causal falsifier** — ≥29 genuinely independent calendar days for the correlational arm (md ≤ 0.5), or a pre-registered cluster-randomized ITT/lead-lag falsifier. This round executes that lever. A positive result RE-ADMITS / may move toward approval; it does NOT auto-promote.

## 2. Estimand (locked from tier2d + intraday-flow round-4b)

- **Signed flow (the Karsan ΔIV-signed vanna construct):** `signed_flow = −sign(ΔIV_i) × burst_i`, where `burst_i = hedge_flow_at(locus, spot_i)` (gamma burst at the day-anchored execution locus) and ΔIV is the intraday/bucket ATM IV change.
- **Channel (b) vannaflow:** `vf = Σ signed_vanna·OI·100·VANNA_PP_SCALE·(ΔIV/0.01)` via `ebe.vanna_flow(ne, d_iv)`, signed by `rec.vanna = −1×BS`. This is the new-engine re-derivation, NOT production `dealer_positioning` output.
- **Two clocks (Karsan, pre-registered):**
  - **Daily close-to-close → EXPECT NEGATIVE** (impounded-hedge shadow; positive daily = anomalous/flag).
  - **From-breach / intraday → EXPECT POSITIVE** (direct vanna push; ONLY a null here refutes the mechanism).
- **Response:** aligned forward return (10-min from breach bucket for intraday; next-day for daily). NOT `rets[i+1]` (the Tier-2C off-by-one bug is fixed — aligned horizon).

## 3. Design & independence (pre-registered BEFORE acquisition)

- **≥29 genuinely independent calendar days** for the correlational arm (md ≤ 0.5 at the tanh form). SPY/QQQ = ONE paired family (same day = one independent unit, NOT two).
- **Family separation:** QQQ and SPY treated as paired families; never pooled Simpson-contaminated. Report SPY-only and QQQ-only separately AND the joint family-index read. Power by family separately or via a pre-specified family-interaction model.
- **Retain all zero-firing and deadband observations** in the denominator where the estimand's support calls for it (de-gated continuous arm, per tier2d); the from-breach arm uses its own support (firing buckets).
- **Coverage:** mixed DTE 1–10, prioritizing high-vol event days (FOMC 2026-04-29/06-17/07-29; earnings; OpEx 3rd Fridays) that actually fire, and adding non-event days for the no-firing/control denominator.
- **Acquisition protocol:** NEW days via `seed_data_maker.py`, one SEPARATE sequential session per day, `THETADATA_HIST_CONCURRENCY=1` (proxy saturates under concurrency). Record data gaps (e.g. 20260619/20260703 had NO proxy intraday stock data) explicitly, never impute.

## 4. Causal falsifiers (pre-registered, all must be run)

| Test | Definition | Success bar |
|---|---|---|
| **Primary lead/lag** | signal formed at t predicts forward return t→t+h; `corr(signed_flow_t, fwd_{t→t+h})` | EXPECT POSITIVE on the from-breach clock; must survive family-separated + unique-day |
| **Reverse-causal check** | prior return predicts signal (lead/lag reversed) | must NOT perform similarly to primary (else reflexivity/reverse-causal, not causal) |
| **Placebo** | random/false-signal reordering → corr | null-consistent (p not significant) |
| **No-firing/control days** | days with zero burst/zero firing | no signal-return association (cleaner denominator) |
| **Opposite-convention** | re-sign vf with +1×BS (opposite of locked −1×BS) | sign flip ⇒ convention-bound; collapse ⇒ exposure-weighting does the work |
| **Reflexivity baseline (A6)** | corr(ΔIV, fwd-return) on same buckets | if positive and ≥ vanna corr ⇒ vanna sign is downstream of ΔIV/return reflexivity, NOT independent |
| **Event-window (OpEx/FOMC/earnings)** | dated-shock sub-arm, same-session design, macro-surprise confounding declared | separate verdict by habitat, never pooled into the daily read |

**Hard truth to state:** dual-pipeline agreement alone cannot establish causality; only the temporal falsifier (forward signal predicts correctly-defined forward return; reverse/placebo/no-firing fail) can.

## 5. Gates & power (pre-registered)

- **Target:** ≈29 independent days for |r|≈0.5 at 80% power (md ≤ 0.5). Power computed at effective independent units (unique days / family-adjusted), NOT pooled rows.
- **Primary index arm:** n_eff ≥ 20 index units; cluster bootstrap 90% CI; tanh-form md at eff-n; BH q<0.10 over (index, singles) where applicable.
- **Decision ladder (from tier2d, pre-registered):** BOUNDED / DAILY-SHADOW-CONSISTENT / DAILY-ANOMALOUS / SUPPORTED(from-breach) / RULED_OUT(from-breach) / FRAGILE.
- **Thresholds set BEFORE acquisition:** correlation sign + magnitude (|r| ≥ md at eff-n), lead/lag ratio, event-window acceptance, BH q.

## 6. What a positive does NOT do

- Does NOT promote the model (still descriptive/conditional until Cem APPROVES).
- Does NOT erase the existing sign-arm FAIL (3/9 unique-day heterogeneous-null P=0.8066; 6/12 P=0.6128) or the convention-bound correlational blocker — those stand unless a separate powered design clears them.
- A positive causal result re-admits to evidence / moves toward approval only after R1 → R2 → R3 review; Cem is the strict gate.

## 7. CARL loop order (locked)

1. WRITE this pre-registration (DONE).
2. DISPATCH read-only design/power panel to audit the pre-registration + independence plan. Incorporate accepted insights.
3. ACQUIRE ≥29 new independent days sequentially (concurrency=1).
4. RUN the causal falsifier + all negative controls.
5. FRESH R1 adversarial panel → R2 source cross-exam → R3 Cem (re-admission or approval decision).
6. Commit ONLY to Dealer-Exposure-Dev. Model stays descriptive/conditional throughout.

## 8. Files

- Pre-registration: `Vol_Suite/docs/Dealer posistioning notes/pre_registration_causal_arm_20260814.md` (this file).
- Reused machinery: `run_expiry_tier2d_continuous.py` (lead/lag + two-clock + permutation null), `run_intraday_flow.py` (from-breach clock + A6 + A1/A2/A4 + shadow-leak), `seed_data_maker.py` (acquisition).
