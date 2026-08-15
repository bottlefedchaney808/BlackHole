# ROUND-10.1 — Causal-Arm Pre-Registration v2 (Dealer-Exposure-Dev CARL loop)

**Date:** 2026-08-15
**Supersedes:** `pre_registration_causal_arm_20260814.md` (R10.0) — rejected at design/power audit because it would power a confounded gamma/reflexivity proxy, not the causal vanna mechanism.
**Status:** PRE-REGISTRATION v2 — to be re-audited by a read-only design/mechanism panel BEFORE any acquisition. Not yet executed.
**Locked context (do NOT relitigate):** live model = `vol_surface_replication` + SVI + `IV_DEADBAND 0.01` + `DEALER_VANNA_FLOW=1` + accumulation ON; `rec.vanna = −1×BS`; real spot. New expiry-book model (`expiry_book_exposure.py`) stays **descriptive/conditional** throughout and does NOT get promoted by a positive result. Prior R3 rulings: NOT ACCEPTED (re-admitted to evidence only).

## 1. Why this arm exists (Cem's lever)

Cem (R3) closed the convention-distance axis. The ONLY lever toward APPROVAL is a powered causal/independent-day falsifier. A positive result re-admits to evidence / moves toward approval after R1→R2→R3; it does NOT auto-promote. **This round tests the dealer-VANNA mechanism causally — not a gamma-burst or a reflexivity proxy.**

## 2. Estimand hierarchy (CORRECTED — vanna separated from gamma)

**The core correction from the audit:** `−sign(ΔIV) × burst` (gamma burst × vol direction) is NOT a vanna flow. It is demoted to a **reduced-form gamma/volatility directional interaction**, honestly labeled. A true vanna flow has vanna in its magnitude: `Σ vanna·OI·ΔIV`.

Primary causal object (two levels):

- **L1 — Reduced-form event effect (no conditioning on post-event flows):** total response of the prespecified forward-return window to an event/breach state, the coarsest causal object.
- **L2 — Incremental vanna predictability (the CAUSAL vanna test):**
  `response_i ~ β·(pre_event_vanna_exposure_i × ΔIV_i) + γ·gamma/burst_exposure_i + ζ·ΔIV_i + θ·ΔS_i + λ·market/common-shock_i + event_indicators_i`
  where `pre_event_vanna_exposure` = Σ vanna·OI over pre-event moneyness×T (determined BEFORE the breach/response window). **The causal claim is β: does forward return scale with pre-event vanna exposure × the IV move, holding gamma exposure and the A6 reflexivity baseline constant?**
- `ebe.vanna_flow` on the same firing rows = **descriptive decomposition only**, NOT an independent causal channel (shared ΔIV/spot/OI/bucket inputs make it a re-derivation confound).

## 3. The one discriminating contrast (audit-mandated)

The only contrast that separates a genuine dealer-vanna push from gamma-burst and pure reflexivity: **the joint `sign(ΔIV) × sign(ΔS)` state**. In the IV-moving-but-flat-spot regime (gamma≈0), vanna is the whole signal. The effect must be STRONGER where pre-event vanna exposure is high AND the joint state favors vanna. If the effect tracks gamma/burst size rather than pre-event vanna exposure, it is not a vanna mechanism.

## 4. Reflexivity & gamma control (audit-mandated, all mandatory)

- **A6 reflexivity baseline** `corr(ΔIV, fwd)` on the same buckets, as a hard discriminator.
- **Opposite-convention rerun** (+1×BS vs locked −1×BS): sign flip ⇒ convention-bound; collapse ⇒ exposure-weighting does the work.
- **Primary model controls for gamma/burst exposure, ΔIV, ΔS, market return, event indicators.** Report incremental vanna predictability AFTER these controls. β must survive; otherwise classify as reflexivity/gamma, NOT dealer-vanna causality.

## 5. Design & independence (CORRECTED)

- **≥29 genuinely independent calendar days** for the causal power target (md ≤ 0.5 at the tanh form). **SPY/QQQ on the same day = ONE independent unit** (paired family, ~0.99 correlation).
- **Unique-day effective-n everywhere:** every `md`/power/gate computed at unique-calendar-day n, NOT pooled buckets, NOT `(ticker,day)` pairs. Delete the legacy `n_eff ≥ 20` parallel gates; enforce the 29-unique-day target or state the achievable md at the in-hand unique-day count. **Do NOT claim 80% power unless the final eligible independent-day count actually reaches the locked target.**
- **Family-interaction model PRE-SPECIFIED:** within-day paired contrast or family×day mixed model with a locked homogeneity rule — **opposite-signed families ⇒ NO family-wide claim** (this resolves the "which sign binds" ambiguity that stalled Rounds 2–9).
- **Coverage:** mixed DTE 1–10; event days (FOMC 2026-04-29/06-17/07-29, earnings, OpEx 3rd Fridays) prioritized for firing; non-event/control days for the no-firing denominator. Record data gaps (20260619/20260703 had NO proxy intraday stock data) explicitly, never impute.
- **Acquisition:** NEW days via `seed_data_maker.py`, one SEPARATE sequential session/day, `THETADATA_HIST_CONCURRENCY=1`. Firing-support locked pre-acquisition (no post-hoc support redefinition).

## 6. Causal falsifiers (audit-mandated, all mandatory)

| Test | Definition | Success bar |
|---|---|---|
| **Primary lead/lag (pre-specified horizon)** | signal at t predicts forward return t→t+h with **one locked h** (no best-lag selection) | EXPECT POSITIVE on from-breach clock; β survives exposure controls |
| **Reverse-causal** | prior return predicts signal | must NOT perform like primary |
| **Placebo/permuted signal** | signal reordering/shuffle | null-consistent |
| **No-firing/control days** | zero-burst/zero-firing days | no signal-return association |
| **Opposite-convention** | +1×BS re-sign | sign flip ⇒ convention-bound |
| **Reflexivity baseline (A6)** | corr(ΔIV, fwd) on same buckets | if ≥ vanna corr ⇒ reflexivity, not vanna |
| **Market/common-shock & cross-family spillover** | market return + same-day SPY↔QQQ spillover controls in the intraday arm | vanna effect survives |
| **Event-window (OpEx/FOMC/earnings)** | dated-shock sub-arm, pre-event balance/placebo window, surprise (not just level) | separate verdict by habitat; macro-surprise confound declared |

**Multiplicity/disposition rule (pre-registered):** a single falsifier FAIL does NOT auto-block, but the JOINT decision rule (below) requires the primary temporal effect + correct clock sign + survival of gamma/reflexivity/common-shock controls + reverse/placebo failure + adequate unique-day power. State which clocks are confirmatory vs diagnostic.

## 7. Two clocks + single decision table (CORRECTED)

- **Daily close-to-close EXPECT NEGATIVE** (impounded-hedge shadow) — a **diagnostic flag, not a gate**.
- **From-breach/intraday EXPECT POSITIVE** (direct push) — the **confirmatory clock**; only a null here refutes the mechanism.
- **JOINT-clock cell (the only re-admission state):** daily NEGATIVE (shadow-consistent) AND from-breach POSITIVE (supported) = **BOTH-CLOCK-CONFIRMED**. Enumerate failure dispositions (e.g. daily ANOMALOUS + breach positive ⇒ flag, not confirmation). Lock one- vs two-sided per test.
- **One decision table:** one row per unique day with eligibility, family, event habitat, firing status, pre-event vanna/gamma exposure, primary/reverse/placebo response, controls. **One joint rule**: causal support = primary temporal effect + correct clock sign + survival of gamma/reflexivity/common-shock controls + reverse/placebo failure + adequate unique-day power.

## 8. Language (CORRECTED)

- This is a **pre-registered cluster-level temporal falsifier / event-study**, NOT a "cluster-randomized ITT" (no genuine random assignment of dealer exposure exists).
- The daily-negative/intraday-positive clocks are habitat hypotheses; neither alone establishes causality.
- A positive result re-admits ONLY to the evidence record (per Cem's wording) and triggers fresh R1 → R2 → R3; it cannot promote automatically.

## 9. What a positive does NOT do

- Does NOT promote (still descriptive/conditional until Cem APPROVES).
- Does NOT erase sign-arm FAIL (3/9 P=0.8066; 6/12 P=0.6128) or the convention-bound correlational blocker — those stand unless separately cleared.
- If β fails to survive exposure/reflexivity/common-shock controls, the honest result is reflexivity/gamma, NOT dealer-vanna causality.

## 10. CARL loop order (locked)

1. WRITE R10.1 (DONE).
2. DISPATCH read-only design/mechanism audit of R10.1. Incorporate accepted insights.
3. ACQUIRE ≥29 independent days sequentially (concurrency=1), firing-support locked.
4. RUN the corrected causal falsifier (L2 exposure-controlled vanna test + all negative controls) + single decision table.
5. FRESH R1 → R2 → R3 Cem. Model stays descriptive/conditional throughout.

## 11. Files

- Pre-registration: `Vol_Suite/docs/Dealer posistioning notes/pre_registration_causal_arm_20260814.md` (this file, R10.1).
- Reused machinery (to be corrected): `run_expiry_tier2d_continuous.py`, `run_intraday_flow.py`, `seed_data_maker.py`.
