# Tier-3B ROUND 2 — From-breach clock with LIVE vannaflow wired (R2-2..R2-7 build)

**Date:** 2026-08-14  **Driver:** `run_intraday_flow.py` (round-2)
**Build (Cem's merged upgrade set, loop round 2):**
- R2-1 ✅ zero-target audit: `else 0.0` placeholders removed from run_expiry_tier1.py:277 + run_expiry_tier2c_signed.py:92 (skip instead of fake 0.0); chain sweep clean
- R2-2 ✅ live-model-equivalent ΔIV-signed vannaflow wired into every firing bucket (`vanna_flow` on same rows, same dealer frame — the object never measured intraday before)
- R2-3 ✅ paired two-model × forward-return table + per-bucket sign-agreement
- R2-4 ✅ shadow-leak sub-sample (intraday ΔIV opposite day's net ΔIV)
- R2-5 ✅ effective-n md bars (3 clusters, not pooled n)
- R2-6 ✅ B1 LIVE asymmetry surfaced in compare driver (CI-excluding-0 never hidden in BOUNDED)
- R2-7 ✅ cluster-count record corrected (3 day-anchors; 20260803 is an expiry)

## Results (28 firing buckets, all QQQ, 3 day-clusters, SPY fired 0)

| Channel | corr vs fwd-10min | 90% CI | n | verdict |
|---|---|---|---|---|
| (a) NEW gamma-burst signed flow | −0.0884 | [−0.143, −0.040] | 28 | unchanged (the round-1 conflated leg) |
| **(b) NEW-model ΔIV-signed vannaflow (ebe.vanna_flow, −1×BS)** | **+0.2327** | **[+0.205, +0.305]** | 28 | **POSITIVE sign, CI excludes 0** — but single-engine, linear-in-ΔIV, QQQ-only, underpowered (md 0.993 at eff-n 3). FRAGILE SIGN-HINT, NOT a mechanism confirmation. |
| (c) WITHIN-ENGINE sign-agreement | **67.9%** (19/28) | — | 28 | dIV cancels ⇒ sign(burst)==−sign(net_vanna); within-engine self-consistency, NOT two-model coherence |
| (d) shadow-leak split (post-hoc EXPLORATORY) | **+0.4343** | n=14 | 14 | **below its own md 0.6883 → code verdict BOUNDED/negative** (reporting-integrity corrected; prose no longer claims 'strongly POSITIVE') |

**Honest labels (R2 cross-examiner, source-verified):**
- Channel (b) labeled "LIVE" in round-2 was **NOT** the production `dealer_positioning` vanna — it is a re-derivation from `expiry_book_exposure.vanna_flow` under the locked −1×BS convention, built on the same rows that feed the burst (`run_intraday_flow.py` imports only the new engine, never the production pipeline). Relabeled.
- All 28 firing buckets are **QQQ** across 3 day-clusters; **SPY fired 0**. This is NOT an index result.
- The K=3 cluster bootstrap yields only **10 distinct resampled multisets** → the 90% CI is a coarse discrete-quantile, demoted to exploratory, NOT a valid inferential interval.
- Exposure-response terciles (b3) + opposite-convention rerun (b4) are now reported to separate mechanism from convention×reflexivity.

## What this establishes (round 2, honest read)

1. **The channel-conflation from round-1 is confirmed as the cause of the negative flag — but on a SINGLE engine.** The gamma burst (a) stays −0.088; the vanna channel (b) on the SAME buckets is **+0.23 POSITIVE**. However (a) and (b) are both computed by the NEW module (`expiry_book_exposure.py`) on the same rows; channel (b) is not the production live pipeline. The round-1 "negative flag" was a gamma-burst-tested-against-a-vanna-prediction artifact — but this round does NOT yet prove the dealer vanna mechanism, because the positive sign is consistent with the locked −1×BS convention × ordinary ΔIV/return reflexivity.
2. **The shadow-leak split is suggestive but NOT confirmatory.** The leak split (d) is **+0.43** when intraday ΔIV runs opposite the day's net ΔIV — but it is below its own md (0.6883), so the code's own verdict is **BOUNDED/negative**, and it is a post-hoc exploratory selection (conditions on the same ΔIV that defines x). It does not support a "hypothesis confirmed" claim.
3. **The models do NOT show independent two-model coherence** (c): 67.9% reduces to sign(burst)==−sign(net_vanna) because dIV cancels — a within-engine self-consistency, not cross-model agreement.
4. **Honest limits (Cem's bar NOT met):** single-engine, linear-in-ΔIV, QQQ-only (SPY fired 0), md at effective-n=3 is 0.993 → underpowered. The verdict is **"fragile sign-direction hint," NOT "supported beyond md."** The +0.84 cross-sectional remains a rank-ordering claim only.

## Next (if Cem demands index power)
- SPY firing days: FOMC/earnings days or 0.5% tolerance band (~30s/day/ticker).
- Or widen QQQ window with more short-DTE days.

## Files
- Driver: `Vol_Suite/run_intraday_flow.py` (round-2)
- Raw output: `Vol_Suite/_intraday_cache/flow_from_breach_result.md`
- This summary: `Vol_Suite/_intraday_cache/Tier3B_round2_RESULT.md`
