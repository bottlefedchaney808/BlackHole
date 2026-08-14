# Tier-3B ROUND 3 — Cheap-fix run (R2 cross-examiner's (A) set, honest labels)

**Date:** 2026-08-14  **Driver:** `run_intraday_flow.py` (round-3, after R2 source-verified cheap fixes)
**Tree:** Dealer-Exposure-Dev @ `edbb61a` + uncommitted round-3 fixes

## What round-3 changed (the cheap-fix set, all accepted from R1 + R2)
- Channel (b) relabeled **NEW-model ΔIV-signed vannaflow (`ebe.vanna_flow`, −1×BS)** — no longer mislabeled "LIVE" (R2 proved it never calls production `dealer_positioning`).
- **(b2) PER-DAY sign-consistency added as the PRIMARY statistic** (D6) — 3 per-cluster corr(vf, fwd).
- **(b3) exposure-response terciles by |net vanna|** (F3) — low/mid/high.
- **(b4) opposite-convention rerun (signed_vanna = +1×BS)** sensitivity falsifier (F3).
- Channel (c) relabeled **WITHIN-ENGINE** sign-agreement (dIV cancels ⇒ sign(burst)==−sign(net_vanna)).
- Channel (d) relabeled **post-hoc EXPLORATORY** + integrity fix (code verdict BOUNDED/negative, prose no longer claims "strongly POSITIVE").
- R2-8 bootstrap disclosure: K=3 ⇒ only 10 distinct resamples ⇒ CI demoted to exploratory.
- 7 new tests lock these (57 phase+round3 tests green).

## Results (28 firing buckets, all QQQ, 3 day-clusters, SPY fired 0)

| Channel | corr vs fwd-10min | 90% CI | n | verdict |
|---|---|---|---|---|
| (a) NEW gamma-burst signed flow | −0.0884 | [−0.143, −0.040] | 28 | underpowered (md 0.993) |
| (b) NEW-model vannaflow (ebe.vanna_flow, −1×BS) | +0.2327 | [+0.205, +0.305] | 28 | CI excludes 0, |r|<md ⇒ fragile sign-hint |
| **(b2) PER-DAY sign-consistency** | **+0.3051 / +0.2939 / +0.4434** | — | 6/9/13 | **ALL 3 QQQ days POSITIVE** — the genuine directional whisper on 3 independent units |
| (b3) exposure-response terciles | +0.4867 / +0.5917 / +0.4053 | — | 9/9/10 | **NON-MONOTONIC** — does NOT show mechanism's monotonic-up signature |
| **(b4) opposite-convention rerun (+1×BS)** | **−0.2327** | [−0.305, −0.205] | 28 | **EXACT SIGN FLIP ⇒ CONVENTION-BOUND**, not mechanism-proven |
| (c) WITHIN-ENGINE sign-agreement | 67.9% (19/28) | — | 28 | dIV cancels; self-consistency only |
| (d) shadow-leak split (EXPLORATORY) | +0.4346 | n=14 | 14 | **< md 0.6883 ⇒ BOUNDED/negative** (integrity corrected) |

## Honest read (round-3)
1. **Per-day sign-consistency (b2) is the one genuine positive.** All 3 QQQ day-clusters show corr(vf,fwd) > 0 — a directional whisper on the 3 independent units, not a pooled artifact.
2. **But the opposite-convention rerun (b4) proves the headline is CONVENTION-BOUND.** Flipping signed_vanna to +1×BS flips the corr to exactly −0.2327. The positive +0.2327 is therefore the locked −1×BS convention × ordinary ΔIV/return reflexivity — NOT an independent confirmation of the dealer mechanism.
3. **Exposure-response (b3) is non-monotonic** — the low/mid/high tercile corrs (+0.49/+0.59/+0.41) do not increase with |net vanna|, so the exposure-weighting does not independently drive the signal.
4. **Cem's bar is NOT met:** still single-engine, convention-bound, QQQ-only (SPY 0), eff-n=3 (md 0.993 = zero power). The +0.2327 is a fragile sign-hint, not approval-grade mechanism evidence.

## What is REQUIRED to reach Cem's stop condition (not cheap fixes)
1. **True dual-pipeline convention-independence gate** — production `dealer_positioning` vanna (`vanna_call+put_shares`, `vol_surface_replication` SVI sign map) on the same intraday firing buckets vs the new engine. **Cannot run on existing seeds** (EOD granularity, expiry-mismatched for 2/3 firing anchors, no 20260717 seed, no intraday accumulation history) — needs new intraday chain acquisition.
2. **Index (SPY) firing coverage** — FOMC/earnings days or a justified 0.5% tolerance band. SPY fired 0.
3. **Broader independent day-clusters** to lift eff-n above the md=0.993 zero-power limit.

## Files
- Driver: `Vol_Suite/run_intraday_flow.py` (round-3)
- Raw output: `Vol_Suite/_intraday_cache/flow_from_breach_result.md`
- This summary: `Vol_Suite/_intraday_cache/Tier3B_round3_RESULT.md`
