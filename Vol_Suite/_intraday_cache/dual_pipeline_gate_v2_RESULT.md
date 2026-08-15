# P0-2/P0-3 — Expanded Dual-Pipeline Convention-Independence Gate (result)

**Date:** 2026-08-14  **Driver:** `run_dual_pipeline_gate_v2.py`  **Tree:** Dealer-Exposure-Dev  **Tickers:** QQQ, SPY

**Pre-registered estimand:** see the driver docstring (LEVEL-vs-LEVEL sign convention, SPY band rule, exposure strata, lead/lag lags, placebo design, zero-sign policy, effective-n/md). Written before any network data run.


### Data acquisition (provenance)

Sequential via ThetaData proxy (THETADATA_HIST_CONCURRENCY=1), credentials from main-tree .env (never persisted). Per (ticker, day): stock/ohlc intraday, per-strike hist/all_greeks @600000 (C+P), hist/open_interest, and bulk_hist/option/eod_greeks single-day for the production same-day book.


### SPY BAND RULE (P0-2, pre-registered)

SPY: tolerance_pct = 0.005 (open spot ± 0.5%, anchored ONCE per day). QQQ: tolerance_pct = 0.01 (open spot ± 1.0%). Applied to the execution locus BEFORE burst extraction; sign is read from LEVEL quantities. Justification: SPY realized vol ≈ half of QQQ, so ±0.5% on SPY is the index analogue of ±1% on QQQ (same breach probability).


### Firing-day summary

| Ticker | Day | Expiry | tol | open | close | grid | eod | intraday(C/P) | firing/buckets | prod_vanna | prod_sign | new_LEVEL | new_sign | agree |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| QQQ | 20260508 | 20260511 | 0.010 | 700.14 | 711.11 | 25 | 320 | 20/20 | 1/40 | +1.17e+04 | 1 | -2.12e+04 | -1 | NO |
| QQQ | 20260522 | 20260526 | 0.010 | 719.03 | 717.43 | 25 | 248 | 25/25 | 0/40 | -1.05e+04 | -1 | NA | 0 | n/a(0) |
| QQQ | 20260605 | 20260608 | 0.010 | 729.70 | 705.26 | 25 | 338 | 25/25 | 7/40 | +3.25e+03 | 1 | +1.61e+05 | 1 | YES |
| QQQ | 20260716 | 20260717 | 0.010 | 712.98 | 706.05 | 25 | 592 | 25/25 | 2/40 | -4.89e+04 | -1 | +3.82e+04 | 1 | NO |
| QQQ | 20260717 | 20260717 | 0.010 | 692.56 | 695.30 | 25 | 592 | 25/25 | 2/40 | NA | 0 | +9.59e+04 | 1 | n/a(0) |
| QQQ | 20260731 | 20260803 | 0.010 | 692.63 | 687.92 | 25 | 408 | 25/25 | 4/40 | -2.11e+04 | -1 | -3.56e+03 | -1 | YES |
| SPY | 20260508 | 20260511 | 0.005 | 734.89 | 737.53 | 25 | 324 | 25/25 | 0/40 | +559 | 1 | NA | 0 | n/a(0) |
| SPY | 20260522 | 20260526 | 0.005 | 746.75 | 745.59 | 25 | 318 | 25/25 | 0/40 | +8.83e+04 | 1 | NA | 0 | n/a(0) |
| SPY | 20260605 | 20260608 | 0.005 | 751.93 | 737.42 | 25 | 342 | 25/25 | 6/40 | +733 | 1 | +1.59e+05 | 1 | YES |
| SPY | 20260716 | 20260717 | 0.005 | 753.17 | 750.76 | 25 | 498 | 25/25 | 0/40 | -5.13e+04 | -1 | NA | 0 | n/a(0) |
| SPY | 20260717 | 20260717 | 0.005 | 742.35 | 743.20 | 25 | 498 | 25/25 | 1/40 | NA | 0 | -1.84e+04 | -1 | n/a(0) |
| SPY | 20260731 | 20260803 | 0.005 | 744.97 | 746.79 | 25 | 340 | 25/25 | 0/40 | +5.39 | 1 | NA | 0 | n/a(0) |

### PRIMARY — LEVEL-vs-LEVEL SIGN AGREEMENT (Cem's gate, P0-2 correction)

- **SPY (INDEX)** level-vs-level sign agreement: **1/1 = 100%** resolvable clusters agree.
- **QQQ** level-vs-level sign agreement: **2/4 = 50%** resolvable clusters agree.
- **COMBINED** level-vs-level sign agreement: **3/5 = 60%** resolvable clusters agree.

- QQQ 20260508: prod_sign=1 new_sign=-1 → non-zero (new_LEVEL=-2.12e+04 if set)
- QQQ 20260522: prod_sign=-1 new_sign=0 → ZERO/missing — not counted (new_LEVEL=NA if set)
- QQQ 20260605: prod_sign=1 new_sign=1 → AGREE (new_LEVEL=1.61e+05 if set)
- QQQ 20260716: prod_sign=-1 new_sign=1 → non-zero (new_LEVEL=3.82e+04 if set)
- QQQ 20260717: prod_sign=0 new_sign=1 → ZERO/missing — not counted (new_LEVEL=9.59e+04 if set)
- QQQ 20260731: prod_sign=-1 new_sign=-1 → AGREE (new_LEVEL=-3.56e+03 if set)
- SPY 20260508: prod_sign=1 new_sign=0 → ZERO/missing — not counted (new_LEVEL=NA if set)
- SPY 20260522: prod_sign=1 new_sign=0 → ZERO/missing — not counted (new_LEVEL=NA if set)
- SPY 20260605: prod_sign=1 new_sign=1 → AGREE (new_LEVEL=1.59e+05 if set)
- SPY 20260716: prod_sign=-1 new_sign=0 → ZERO/missing — not counted (new_LEVEL=NA if set)
- SPY 20260717: prod_sign=0 new_sign=-1 → ZERO/missing — not counted (new_LEVEL=-1.84e+04 if set)
- SPY 20260731: prod_sign=1 new_sign=0 → ZERO/missing — not counted (new_LEVEL=NA if set)

### EFFECTIVE-n vs md

- effective-n = **5** independent resolvable (ticker, day) clusters (SPY 1 + QQQ 4).
- md at effective-n = **0.963** (R2-5 tanh form).
- pooled firing buckets n = **23** (reported separately; the gate's inferential claim rests on effective-n = 5, not pooled n).
- effective-n 5 is **below the 8-cluster target** — power limited.

### SECONDARY — Correlations vs forward returns (descriptive)

- corr(new_LEVEL, fwd) Pearson = -0.0482  Spearman = +0.0385  (n=23 firing buckets, 5 clusters)  Fisher-tanh 95% CI [-0.451, +0.371]
  SPY sub-analysis: corr=+0.5486 r_A6=-0.2605 (n=7 buckets, 1 resolvable)
  QQQ sub-analysis: corr=-0.1853 r_A6=+0.4390 (n=16 buckets, 4 resolvable)
  effective-n = 5 clusters (SPY + QQQ) — the K=3 CI from P0-1 is now superseded; the correlational arm is inferential only if effective-n clears md.

### LEAD/LAG FALSIFIER

- corr(N_i, fwd_{i+0}) = -0.0482  (n=23)
- corr(N_i, fwd_{i+1}) = +0.0594  (n=16; response one 10-min lag AFTER breach)
- corr(N_i, fwd_{i+2}) = +0.6248  (n=11; two lags after)
- REVERSE (prior return predicts signal): corr(N_i, fwd_{i-1}) = -0.2354  (n=16)
  read: lead/lag is NULL/weak — no persistent response after the breach bucket.
  CAUTION: prior-return predicts the signal more strongly than the signal predicts forward returns → reverse-causation/reflexivity present.

### PLACEBO NULL

- 1000 permutations (seed=7): observed corr = -0.0482; empirical p = 0.5584 (55.8% of null >= obs).
- null median = -0.0185; null 95th pct = +0.3521
  read: null NOT rejected at p<0.05.

### (A6) REFLEXIVITY BASELINE (Cem's decisive cheap diagnostic)

- corr(dIV, fwd) on the SAME firing buckets = +0.3823  (n=23)
- vanna-LEVEL corr -0.0482 vs reflexivity +0.3823 → vanna <= reflexivity — the vanna sign is downstream of dIV/return reflexivity (convention-bound)

### DATA GAPS / EXCLUDED CLUSTERS

| ticker | day | endpoint | status | note |
|---|---|---|---|---|
| QQQ | 20260522 | sign-agreement | zero/missing | prod_sign=-1 new_sign=0 |
| QQQ | 20260619 | stock/ohlc | empty | no intraday stock rows |
| QQQ | 20260703 | stock/ohlc | empty | no intraday stock rows |
| QQQ | 20260717 | sign-agreement | zero/missing | prod_sign=0 new_sign=1 |
| SPY | 20260508 | sign-agreement | zero/missing | prod_sign=1 new_sign=0 |
| SPY | 20260522 | sign-agreement | zero/missing | prod_sign=1 new_sign=0 |
| SPY | 20260619 | stock/ohlc | empty | no intraday stock rows |
| SPY | 20260703 | stock/ohlc | empty | no intraday stock rows |
| SPY | 20260716 | sign-agreement | zero/missing | prod_sign=-1 new_sign=0 |
| SPY | 20260717 | sign-agreement | zero/missing | prod_sign=0 new_sign=-1 |
| SPY | 20260731 | sign-agreement | zero/missing | prod_sign=1 new_sign=0 |

NOTE on QQQ 20260717, SPY 20260717: the production same-day book returned 0 records because the firing expiry is the ANCHOR and expires THAT day (tte=0) — the production `dealer_positioning` expiry filter (0 < tte) drops an expiring weekly. This is a production-pipeline convention, not a proxy/data failure; the day is reported as sign=0 (excluded) per the pre-registered zero-sign policy.

### GATE VERDICT: **FAIL** — level-level agree 3/5=60% < 2/3

Ran in 69.8s. Raw observations: `dual_pipeline_gate_v2_obs.json`.