# Tier-3B ROUND 4 (A) — Cem's cheap-fix diagnostics, run result

**Date:** 2026-08-14  **Driver:** `run_intraday_flow.py` (round-4, after Cem NOT ACCEPTED verdict)
**Tree:** Dealer-Exposure-Dev

## Cem round-3 verdict (deleg_5ca3bd76): NOT ACCEPTED
- **b4 convention-flip dispositive:** exact ±0.2327 mirror ⇒ magnitude in shared ΔIV×reflexivity, sign in the locked prior. Convention-bound, zero independent mechanistic info.
- **b2:** 3/3 per-day positive = genuine directional whisper, but same engine/convention/days — survives day-stability, NOT convention-independence.
- **b3 non-monotonic** kills exposure-weighted mechanism claim.
- **Un-named risk:** the 3 "independent" day-clusters are NOT independent — same family/construction/engine/convention/window. One family, three mouths.

## Round-4 (A) cheap-fix diagnostics (28 buckets, all QQQ, 3 day-clusters)

| Diag | Result | Read |
|---|---|---|
| **(A6) REFLEXIVITY BASELINE corr(ΔIV, fwd)** | **+0.4254** (n=28) | **DECISIVE: positive and LARGER than the +0.2327 vanna corr ⇒ the vanna signal is ΔIV/return reflexivity, sign downstream.** |
| (A1) CONVENTION-DEPENDENCE SWEEP (α × −1×BS) | +0.2327 / +0.2327 / 0.0000 / −0.2327 / −0.2327 (α=+1/+0.5/0/−0.5/−1) | linear in the prior ⇒ **convention-bound**, confirmed |
| (A2) MAGNITUDE RESPONSE corr(\|vf\|, \|fwd\|) | **+0.1153** (n=28, convention-free) | weak, no mechanism signal on magnitude alone |
| (A4) SIGN-STABILITY vs binomial null | 3/3 positive, **P(all positive \| null)=0.125** | not significant even at 0.10 |
| (A3) CI demotion | effective-n=3 | no valid inferential CI exists; K=3 bootstrap CI exploratory-only |

## What this establishes
Cem's A6 prediction is **confirmed on data**: the reflexivity baseline (+0.4254) exceeds the vanna correlation (+0.2327). The +0.23 vannaflow is not an independent dealer-flow signal — it is ΔIV/return reflexivity that the locked −1×BS convention signs. The A1 sweep shows the correlation is exactly linear in the sign prior. **Round-3's +0.2327 is dead as a mechanism claim.** The only surviving directional hint is b2's 3/3 per-day positivity (P=0.125, not significant).

## Next (Cem's round-4 (B), priority)
- **P0-1 dual-pipeline convention-independence gate** (the single item that changes his verdict): production `dealer_positioning` vanna (150d accumulated, SVI sign map, vanna_call+put_shares) on the same intraday firing buckets vs the new engine. Requires NEW intraday chain acquisition via ThetaData at IVL=600000 over a 150d accumulation window (existing seeds EOD/expiry-mismatched/missing 20260717).
- **P0-2 index (SPY) firing coverage** — FOMC/earnings or justified 0.5% band.
- **P0-3 independent day-clusters** to lift effective-n past md.

## Files
- Driver: `Vol_Suite/run_intraday_flow.py`
- Raw: `Vol_Suite/_intraday_cache/flow_from_breach_result.md`
- This summary: `Vol_Suite/_intraday_cache/Tier3B_round4A_RESULT.md`
