# ROUND-6 CORRECTED PACKET — Dual-Pipeline Convention-Independence Gate (result)

**Date:** 2026-08-14  **Driver:** `run_dual_pipeline_gate_v5.py`

**R6 corrections:** unique-day binomial (R6-1), honest binomial power (R6-2), TRUE magnitude/OI/vanna-weighted fallback->0 production level (R6-3), underpowered corr/placebo arms (R6-4), SPY/QQQ separate (R6-5), reproducibility assertions (R6-6), delta convention-distance test (R6-7). All R5-1..R5-8 corrections preserved.

### Corpus (merged seed-v3 + v4)

- 40 rows (12 seed + 28 v4), 40 unique (ticker,day). Duplicate-day check passed.

### PRIMARY — UNIQUE-DAY SIGN AGREEMENT (R6-1; eff-n on UNIQUE CALENDAR DAYS)

- Resolvable clusters: 12  **unique calendar days: 9**.
- Unique-day rule (pre-registered): a day counts AGREE only if EVERY resolvable cluster on it agrees; any disagreeing family on a day => that day is a DISAGREE. Days carrying both QQQ+SPY count ONCE.
  - 20260413: 1 resolvable cluster(s), 0 agree -> DISAGREE
  - 20260430: 1 resolvable cluster(s), 0 agree -> DISAGREE
  - 20260508: 1 resolvable cluster(s), 0 agree -> DISAGREE
  - 20260605: 2 resolvable cluster(s), 2 agree -> AGREE
  - 20260617: 2 resolvable cluster(s), 1 agree -> DISAGREE
  - 20260716: 1 resolvable cluster(s), 0 agree -> DISAGREE
  - 20260728: 1 resolvable cluster(s), 1 agree -> AGREE
  - 20260729: 2 resolvable cluster(s), 1 agree -> DISAGREE
  - 20260731: 1 resolvable cluster(s), 1 agree -> AGREE
- **Unique-day agreement: 3/9 = 33.3%**
- Exact binomial null: P(agree>=3 | n=9, p=0.5) = **0.9102**

### SENSITIVITY — 12-cluster (NOT the primary; same-day SPY/QQQ families are not independent)

- Cluster-level agreement: 6/12 = 50.0%; P(>=6|12,0.5)=0.6128

### HONEST BINOMIAL POWER (R6-2 — md is NOT power)

- Bar-clearing prob at n=9: P(clear >= 6/9 | true p=2/3) = **0.650**; at true p=1/2 = 0.254.
- Bar-clearing prob at n=12 (sensitivity): P(clear >= 8/12 | p=2/3) = 0.632.
- n-for-80% power (one-sided sign test, H0:p=0.5 vs H1:p>0.5, alpha=0.05): detect true p=2/3 -> **n=58**; detect true p=0.75 -> **n=23**.
- n-for-80% bar-clearing (power to PASS the 2/3 bar under a true p): p=2/3 -> NEVER (bar sits at the mean, power -> 0.5 asymptotically); p=0.75 -> n=3.
- **Current n=9 is ~18% test power — NOT 80%-powered.** md=0.816 is a CORRELATION MDE, NOT binomial power (do not quote it as such).

### R6-3 — TRUE MAGNITUDE/OI/VANNA-WEIGHTED FALLBACK->0 PRODUCTION LEVEL (CROSS-ENGINE)

- L_f0 = sum_k sign_k * OI_k * vanna_k, deadband/fallback strikes contribute 0 (not -1). Computed by patching the production Layer-1b -1 fallback -> 0 and re-driving the SAME production pipeline. sign(L_f0) compared to the NEW-engine day LEVEL sign (cross-engine). Per-strike arrays persisted. ZERO new-day acquisition (re-fetch of same-study EOD/OI).

| Ticker | Day | Src | sign(L_f0) | sign(prod baseline) | sign(new) | agree(L_f0,new) | per-strike |
|---|---|---|---|---|---|---|---|
| QQQ | 20260508 | seed-v3 | 1 | 1 | -1 | NO | 34 |
| QQQ | 20260605 | seed-v3 | 1 | 1 | 1 | YES | 40 |
| QQQ | 20260716 | seed-v3 | -1 | -1 | 1 | NO | 36 |
| QQQ | 20260731 | seed-v3 | -1 | -1 | -1 | YES | 30 |
| SPY | 20260605 | seed-v3 | 1 | 1 | 1 | YES | 25 |
| QQQ | 20260617 | v4 | 1 | 1 | -1 | NO | 44 |
| QQQ | 20260729 | v4 | 1 | 1 | 1 | YES | 47 |
| SPY | 20260413 | v4 | 1 | 1 | -1 | NO | 48 |
| SPY | 20260430 | v4 | 1 | 1 | -1 | NO | 31 |
| SPY | 20260617 | v4 | 1 | 1 | 1 | YES | 35 |
| SPY | 20260728 | v4 | -1 | -1 | -1 | YES | 43 |
| SPY | 20260729 | v4 | 1 | 1 | -1 | NO | 34 |

- **Magnitude-weighted fallback->0 vs new-engine sign: 6/12 resolvable clusters agree** (50% if any).
- Per-strike arrays persisted for 12/12 re-derived clusters. Baseline (fallback=-1) sign reported for reference.

### R6-7 — CONVENTION-DISTANCE TEST ON SVI DEVIATION TERMS (delta_prod vs delta_new), FULL CORPUS incl zero-firing

- delta_prod = (rich_plus1 - cheap_minus1)/total_strikes (production's SVI deviation term). delta_new = 0 BY CONSTRUCTION (new engine has no SVI branch; dealer_frame_vanna is flat -1xBS). Convention distance D = |delta_prod - delta_new| = |delta_prod|. Pre-registered rule: DEMOTE if mean|D|<0.10 AND <25% of clusters have |D|>=0.10 (production hugs the shared -1 baseline); OPEN otherwise.

| Ticker | Day | firing_buckets | delta_prod | delta_new | D |
|---|---|---|---|---|---|
| QQQ | 20260508 | 1 | -0.0263 | +0.0000 | 0.0263 |
| QQQ | 20260522 | 0 | -0.0233 | +0.0000 | 0.0233 |
| QQQ | 20260605 | 7 | +0.0222 | +0.0000 | 0.0222 |
| QQQ | 20260716 | 2 | -0.0238 | +0.0000 | 0.0238 |
| QQQ | 20260717 | 2 | +0.0000 | +0.0000 | 0.0000 |
| QQQ | 20260731 | 4 | +0.0244 | +0.0000 | 0.0244 |
| SPY | 20260508 | 0 | +0.0000 | +0.0000 | 0.0000 |
| SPY | 20260522 | 0 | -0.0238 | +0.0000 | 0.0238 |
| SPY | 20260605 | 6 | +0.0488 | +0.0000 | 0.0488 |
| SPY | 20260716 | 0 | +0.0250 | +0.0000 | 0.0250 |
| SPY | 20260717 | 1 | +0.0000 | +0.0000 | 0.0000 |
| SPY | 20260731 | 0 | +0.1538 | +0.0000 | 0.1538 |
| QQQ | 20260413 | 0 | -0.0213 | +0.0000 | 0.0213 |
| QQQ | 20260417 | 0 | +0.0000 | +0.0000 | 0.0000 |
| QQQ | 20260429 | 0 | -0.0541 | +0.0000 | 0.0541 |
| QQQ | 20260430 | 0 | -0.0238 | +0.0000 | 0.0238 |
| QQQ | 20260515 | 0 | -0.0238 | +0.0000 | 0.0238 |
| QQQ | 20260520 | 0 | +0.0256 | +0.0000 | 0.0256 |
| QQQ | 20260529 | 0 | -0.0213 | +0.0000 | 0.0213 |
| QQQ | 20260616 | 0 | -0.0200 | +0.0000 | 0.0200 |
| QQQ | 20260617 | 2 | +0.0000 | +0.0000 | 0.0000 |
| QQQ | 20260721 | 0 | +0.0000 | +0.0000 | 0.0000 |
| QQQ | 20260722 | 0 | -0.0256 | +0.0000 | 0.0256 |
| QQQ | 20260728 | 0 | +0.0200 | +0.0000 | 0.0200 |
| QQQ | 20260729 | 3 | -0.0213 | +0.0000 | 0.0213 |
| QQQ | 20260807 | 0 | -0.0250 | +0.0000 | 0.0250 |
| SPY | 20260413 | 7 | -0.0200 | +0.0000 | 0.0200 |
| SPY | 20260417 | 0 | +0.0000 | +0.0000 | 0.0000 |
| SPY | 20260429 | 0 | -0.0263 | +0.0000 | 0.0263 |
| SPY | 20260430 | 1 | +0.0000 | +0.0000 | 0.0000 |
| SPY | 20260515 | 0 | -0.0238 | +0.0000 | 0.0238 |
| SPY | 20260520 | 0 | -0.0435 | +0.0000 | 0.0435 |
| SPY | 20260529 | 0 | +0.0000 | +0.0000 | 0.0000 |
| SPY | 20260616 | 0 | +0.0000 | +0.0000 | 0.0000 |
| SPY | 20260617 | 2 | -0.0682 | +0.0000 | 0.0682 |
| SPY | 20260721 | 0 | +0.0000 | +0.0000 | 0.0000 |
| SPY | 20260722 | 0 | -0.0256 | +0.0000 | 0.0256 |
| SPY | 20260728 | 1 | -0.0200 | +0.0000 | 0.0200 |
| SPY | 20260729 | 1 | -0.0233 | +0.0000 | 0.0233 |
| SPY | 20260807 | 0 | +0.0789 | +0.0000 | 0.0789 |

- Corpus: 40 clusters (seed 12 + v4 28), INCLUDING zero-firing clusters. mean|D| = 0.0246; 2.5% of clusters have |D| >= 0.1.
- **DELTA VERDICT: DEMOTE** — delta_prod hugs the shared -1 baseline (mean|D|=0.0246 < 0.1, 2.5% of clusters >= 0.1) and delta_new==0 by construction -> convention-bound, demote permanently

  - SPY: 20 clusters, mean|delta_prod| = 0.0291.
  - QQQ: 20 clusters, mean|delta_prod| = 0.0201.
### CORRELATIONAL + PLACEBO ARMS (R6-4 honest underpowered labels; R6-5 SPY/QQQ separate, never pooled)

- corr(new_LEVEL, fwd) pooled = -0.1568 (n=17 buckets) — DESCRIPTIVE ONLY (Simpson cancellation; never inferential).
  - SPY corr = -0.3774 (n=12 buckets) — BUCKET-LEVEL NOISE, underpowered, not an index claim.
  - QQQ corr = +0.6493 (n=5 buckets) — BUCKET-LEVEL NOISE.
- A6 reflexivity baseline corr(dIV,fwd) = +0.0611 vs vanna -0.1568 -> vanna <= reflexivity (convention-bound).
- cluster-level 95% CI at eff-n=9: [-0.744, +0.566] — SPANS MEANINGFUL RANGE (not a clean negative).
- Correlational POWER (HONEST): at eff-n=9, |r| MDE = 0.816 (Fisher-z); power at |r|=0.3 ~19%, |r|=0.4 ~27%, |r|=0.5 ~38% -> **UNDERPOWERED**. n-for-80% at |r|=0.5 ~ n=29 (documented, not gated).
- Placebo: p = 0.7323 on n=17 pooled buckets (md(17)=0.634) — null-consistent but **UNDERPOWERED / EXPLORATORY**, NOT a clean powered null.

### GATE VERDICT

**FAIL** — level-level agree 6/12=50% < 2/3

Ran in 70.1s. Raw: `dual_pipeline_gate_v5_obs.json`.
