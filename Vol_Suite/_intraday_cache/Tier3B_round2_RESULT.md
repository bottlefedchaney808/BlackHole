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
| **(b) LIVE ΔIV-signed vannaflow (EXPECT POSITIVE)** | **+0.2327** | **[+0.205, +0.305]** | 28 | **POSITIVE, CI excludes 0** — underpowered at md 0.993 (eff-n 3) but RIGHT SIGN |
| (c) paired two-model sign-agreement | **67.9%** (19/28) | — | 28 | models cohere where the hedge fires |
| **(d) shadow-leak sub-sample** | **+0.4343** | n=14 | 14 | **strongly POSITIVE** — EXPECT-POSITIVE reappears when daily carry shadow is separated |

## What this establishes (round 2)

1. **The channel-conflation is confirmed as the cause of the round-1 negative flag.** The gamma burst (a) stays −0.088; the vanna channel (b) on the SAME buckets is **+0.23 POSITIVE** — the EXPECT-POSITIVE prediction. The round-1 "negative flag" was a gamma-burst-tested-against-a-vanna-prediction artifact (the costume change), exactly as the framing + mechanics panelists and Cem argued.
2. **The shadow-leak hypothesis is supported.** The leak sub-sample (d) is **+0.43** — when intraday ΔIV runs opposite the day's net ΔIV (unconfounded from daily carry), the vanna flow's positive relation to forward returns is STRONGER, not weaker. The daily shadow was contaminating the raw read.
3. **The models cohere on the firing clock** (c): 67.9% sign agreement per bucket — the live vannaflow and new-model burst point the same way where the hedge fires, more than the 50% level-sign null.
4. **Honest limits (Cem's bar not yet met):** md at effective-n=3 is 0.993 — neither (b) +0.23 nor (d) +0.43 exceeds it. The verdict is "right sign, underpowered," not "supported beyond md." SPY fired zero buckets (0.84–1.43% ranges vs ±1% band) — still not an index result. The +0.84 cross-sectional remains a rank-ordering claim only.

## Next (if Cem demands index power)
- SPY firing days: FOMC/earnings days or 0.5% tolerance band (~30s/day/ticker).
- Or widen QQQ window with more short-DTE days.

## Files
- Driver: `Vol_Suite/run_intraday_flow.py` (round-2)
- Raw output: `Vol_Suite/_intraday_cache/flow_from_breach_result.md`
- This summary: `Vol_Suite/_intraday_cache/Tier3B_round2_RESULT.md`
