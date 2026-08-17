# CARL Round-3 Merged Upgrade Set — B1–B5 Results (built on Dealer-Exposure-Dev)

**Date:** 2026-08-14
**Panel:** 3 adversarial panelists (R1) → cross-examiner (R2, 0.86 conf) → **Cem Karsan arbiter (R3)**.
**Directive:** "whatever the panel recommends build and test it on the Dealer-Exposure-Dev tree."
**Evidence hash:** `f2f4f6faa6adebbc06896165e84fd52761dc915940de165394f97bbc71eb97c0` (FILE hash, not git ref)
**Driver (modified, this tree):** `Vol_Suite/run_compare_live_vs_new.py`
**Status:** ✅ B1–B4 built + run (106s, 96 clusters, no network); B5 folded (Tier-2D already ran). 50 exposure-model tests still green.

---

## R2's two blockers — both confirmed in code

1. **y≡0 predictiveness bug (BLOCKER 1, now FIXED as B1):** old statistic (3) fed `s["fwd"][-1]` = the terminal 0.0 placeholder in `build_burst_series_from_seed` (run_expiry_tier2_multi.py:112-118), so `y≡0` for every cluster and corr was 0.0000 **by construction** for both models. Verified 96/96 seeds. The old "neither model predicts" conclusion was void.
2. **Consensus next-test already blocked (BLOCKER 2, ESCALATED):** the ΔIV-signed event study IS Tier-2C, which already ran + blocked (10 shock days, 1 index vs ≥20 needed); Tier-2D (de-gated continuous) already ran → **BOUNDED** (index +0.0407, CI [−0.107,+0.162], md 0.59, LOO flips). Arbiter's ruling: do NOT re-run; fold B5; the affirmative test needs a data acquisition (ESC-1/ESC-3).

---

## B1 — FIXED per-day predictiveness (real aligned fwd[i])

| Model | per-day corr(sign, fwd) | 90% cluster-CI | n | md@eff-n | Verdict |
|---|---|---|---|---|---|
| NEW | −0.0163 | [−0.074, +0.039] | 755 | 0.102 | **BOUNDED/NOT_SUPPORTED** |
| LIVE | −0.0741 | [−0.142, −0.009] | 755 | 0.102 | **BOUNDED/NOT_SUPPORTED** |

- The 0.0000 is gone — replaced by real per-day numbers. **Both models' level-sign is NOT a supported forward-return predictor** on the daily clock at in-hand power (CI for NEW straddles 0; LIVE's CI excludes 0 but |r| < md → underpowered).
- **LIVE's negative sign (−0.074) is directionally shadow-consistent** with Karsan's two-clock prediction (daily close-to-close EXPECT NEGATIVE = impounded-hedge shadow), but underpowered. That's the honest daily realization, not a refutation.

## B2 — Units standardization (live_gamma → dollar-gamma-per-1%)

| Cross-sectional corr | Value |
|---|---|
| Raw (mixed units) | +0.1595 |
| **Standardized (Γ·OI·100·spot²·0.01 both)** | **+0.8449** |

**The raw +0.16 was mostly a spot² size confound** (moved +0.6854). On like-for-like dollar-gamma-per-1% units, the two models' per-ticker mean exposures agree strongly. This resolves the data-measurement panelist's units objection — and flips the headline reading: **the models DO rank names similarly on units-comparable exposure; the earlier +0.16 understated agreement.**

## B3 — Continuous concordance (within-ticker z-scored) + divergence decomposition

- **Within-ticker z-scored Spearman = +0.1603, 95% CI [−0.429, +0.654] @ eff-n~13** → **"models disagree by habitat, not by rank"** at this power. (Cross-sectional +0.84 vs within-ticker +0.16: the models agree on cross-sectional LEVELS but not on within-name time-variation — consistent with habitat difference.)
- **Per-ticker decomposition: EVERY ticker (incl. JPM 12%) is "oscillating/noise", NOT "consistently-opposite"** — disagreement-flips ≥ 2 for all 12. **JPM 12% is a sign-determinacy effect, not a habitat fingerprint** — confirms R2's correction over the mechanics panelist's over-reach. No ticker shows a systematic divergence worth chasing with more data.

## B4 — Firing-day burst-sign agreement (where the hedge actually fires)

| Habitat | firing days | burst-sign agreement | SE | Verdict |
|---|---|---|---|---|
| INDEX (SPY/QQQ) | 29 | 55.2% | 0.092 | **BOUNDED** (n≥20 but CI [0.37, 0.73] straddles 50%) |
| SINGLES | 245 | 49.0% | — | no signal |

- **The two models do NOT agree on the sign of the hedge burst where it fires** (55% index ≈ 50% singles). On the daily-close proxy clock, no burst-sign coherence. Arbiter's pre-reg: SUPPORTED only if CI excludes 50% AND n≥20 — **BOUNDED, not supported**.
- Hard caveat (arbiter): daily-close burst is still the shadow clock; the *affirmative* from-breach claim can only be confirmed on intraday. Flat/positive daily = flag, not refutation.

## B5 — Fold Tier-2D (NO re-run)

Tier-2D already ran on this tree with both bugs fixed: **BOUNDED** — index n=39, +0.0407, CI [−0.107,+0.162], md 0.59, LOO sign-flips. Standing disposition: **index ΔIV-signed mechanism = OPEN at in-hand power.** Re-running is waste.

---

## Verdict synthesis (arbiter's framing, now with real numbers)

1. **The 50%/0.16/0.0000 headline is retired.** 50% sign agreement was a coarse binary read on mixed units; 0.0000 was a y≡0 bug; +0.16 was a spot² confound.
2. **What the corrected comparison establishes:** the models are NOT the same object (50% sign / B3 within-ticker +0.16 → "disagree by habitat"), but on units-comparable cross-sectional exposure they rank names strongly together (**B2 +0.84**) with NO systematic per-ticker divergence (B3).
3. **What it does NOT establish:** neither model's level predicts daily forward returns (B1, both BOUNDED); the models don't co-fire hedge bursts (B4, BOUNDED); the ΔIV-signed flow mechanism stays OPEN (B5/Tier-2D BOUNDED).
4. **Disposition:** the new expiry-book model ships as a descriptive/conditional instrument (not validated as a flow predictor). The decisive flow adjudication is gated on data acquisition, not more in-hand statistics.

## Escalations (human decision required — Jason)

- **ESC-1 (R2 BLOCKER 2):** unblock the event study — any ONE of: (a) more short-DTE windows, (b) FOMC/earnings/OpEx dated calendar, (c) intraday bars. Arbiter's note: (c) is the strongest, (a) the weakest (buys n against an endogeneity you haven't removed).
- **ESC-3:** intraday bars specifically for the charm-at-OpEx claim (final-2-hours, ~10× 2-DTE vs 30-DTE) — daily clock structurally cannot test it.
- **No build item is blocked** — B1–B5 are done. The next build (event study at proper power) needs the acquisition.

## Files
- Driver (modified, this tree): `Vol_Suite/run_compare_live_vs_new.py`
- This report: `Vol_Suite/_expiry_falsifier_cache/compare_live_vs_new_result.md` (supersedes the 50%/0.16/0.0000 version)
- R1 ledger: `docs/Dealer posistioning notes/carl_consult_20260814_r1_ledger.md`
- R2 verdict: `~/.hermes/cache/delegation/subagent-summary-0-20260814_164746_716688.txt`
- R3 arbiter verdict: `~/.hermes/cache/delegation/subagent-summary-0-20260814_165209_514983.txt`
- Tests: 50 passed (7 phase files) after the changes.
