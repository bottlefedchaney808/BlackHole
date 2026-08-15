# P0-2/P0-3 ROUND-5 — Corrected Dual-Pipeline Convention-Independence Gate (result)

**Date:** 2026-08-14  **Driver:** `run_dual_pipeline_gate_v3.py`

**R5 corrections applied:** unique-day dedup (R5-1), cluster-level CI (R5-2), SVI sign provenance (R5-3), pure-SVI fallback->0 sensitivity (R5-4), low/high |dIV| stratification (R5-5), exact binomial null (R5-6), provenance/confound declaration (R5-7), SPY kept separate (R5-8).

### Data acquisition

Sequential via ThetaData proxy (THETADATA_HIST_CONCURRENCY=1), creds from main-tree .env (never persisted). Per day: stock/ohlc intraday, per-strike all_greeks @600000 (C+P), open_interest, bulk_hist/option/eod_greeks single-day for the production same-day book.

### Firing-day summary

| Ticker | Day | Expiry | firing/buckets | prod_sign | new_LEVEL_sign | agree | mean\|dIV\| |
|---|---|---|---|---|---|---|---|
| QQQ | 20260508 | 20260511 | 1/40 | 1 | -1 | NO | 0.0113 |
| QQQ | 20260522 | 20260526 | 0/40 | -1 | 0 | n/a | 0.0000 |
| QQQ | 20260605 | 20260608 | 7/40 | 1 | 1 | YES | 0.0161 |
| QQQ | 20260716 | 20260717 | 2/40 | -1 | 1 | NO | 0.0118 |
| QQQ | 20260717 | 20260717 | 2/40 | 0 | 1 | n/a | 0.0127 |
| QQQ | 20260731 | 20260803 | 4/40 | -1 | -1 | YES | 0.0161 |
| SPY | 20260508 | 20260511 | 0/40 | 1 | 0 | n/a | 0.0000 |
| SPY | 20260522 | 20260526 | 0/40 | 1 | 0 | n/a | 0.0000 |
| SPY | 20260605 | 20260608 | 6/40 | 1 | 1 | YES | 0.0170 |
| SPY | 20260716 | 20260717 | 0/40 | -1 | 0 | n/a | 0.0000 |
| SPY | 20260717 | 20260717 | 1/40 | 0 | -1 | n/a | 0.0107 |
| SPY | 20260731 | 20260803 | 0/40 | 1 | 0 | n/a | 0.0000 |

### PRIMARY — SIGN AGREEMENT (unique-day dedup, R5-1)

- Resolvable (both-nonzero) clusters: 5  **unique calendar days: 4** (SPY 1 independent-day resolvable; SPY kept separate, R5-8).

- Level-vs-level agreement: **3/5** resolvable = 60% if any.
  - QQQ 20260508: prod=1 new=-1 → DISAGREE (mean|dIV|=0.0113)
  - QQQ 20260605: prod=1 new=1 → AGREE (mean|dIV|=0.0161)
  - QQQ 20260716: prod=-1 new=1 → DISAGREE (mean|dIV|=0.0118)
  - QQQ 20260731: prod=-1 new=-1 → AGREE (mean|dIV|=0.0161)
  - SPY 20260605: prod=1 new=1 → AGREE (mean|dIV|=0.0170)
- Binomial null: P(agree>=3 | n=5, p=0.5) = **0.5000**; P(pass bar >= 4/5) = **0.1875** (R5-6).
- md at eff-n=4 = **1.000** → INDETERMINATE (zero power).

### CORRELATIONAL ARMS (cluster-level, R5-2/R5-5/R5-8)

- corr(new_LEVEL, fwd) pooled = +0.2444 (n=23) — **Simpson caveat**: SPY -0.0031 vs QQQ +0.3193 opposite-sign families; pooled is a cancellation, NOT a null. SPY not an index claim (1 indep day).
- corr by |dIV| strata (R5-5): low -0.1502 vs high +0.3435.
- A6 reflexivity baseline corr(ΔIV, fwd) = +0.3823 vs vanna +0.2444 → vanna <= reflexivity (convention-bound).
- cluster-level 95% CI at eff-n=4: [-0.937, +0.976] (pooled-bucket CI is descriptive only, R5-2).
- placebo p = 0.1449 (null NOT rejected); at md 1.000 this is no-information (underpowered).

### SVI SIGN PROVENANCE (R5-3) + PURE-SVI SENSITIVITY (R5-4)

| Ticker | Day | total_strikes | rich+1 | cheap-1 | deadband→fallback-1 | missing |
|---|---|---|---|---|---|---|
| QQQ | 20260508 | 38 | 18 | 19 | 1 (→ -1 fallback) | 0 |
| QQQ | 20260522 | 43 | 20 | 21 | 2 (→ -1 fallback) | 0 |
| QQQ | 20260605 | 45 | 22 | 21 | 2 (→ -1 fallback) | 0 |
| QQQ | 20260716 | 42 | 20 | 21 | 1 (→ -1 fallback) | 0 |
| QQQ | 20260717 | 36 | 10 | 10 | 16 (→ -1 fallback) | 0 |
| QQQ | 20260731 | 41 | 20 | 19 | 2 (→ -1 fallback) | 0 |
| SPY | 20260508 | 39 | 19 | 19 | 1 (→ -1 fallback) | 0 |
| SPY | 20260522 | 42 | 20 | 21 | 1 (→ -1 fallback) | 0 |
| SPY | 20260605 | 41 | 20 | 18 | 3 (→ -1 fallback) | 0 |
| SPY | 20260716 | 40 | 16 | 15 | 9 (→ -1 fallback) | 0 |
| SPY | 20260717 | 37 | 11 | 11 | 15 (→ -1 fallback) | 0 |
| SPY | 20260731 | 39 | 19 | 13 | 7 (→ -1 fallback) | 0 |
- Deadband strikes fall to the Layer-1b `-1` fallback (== the new engine's flat −1×BS). Agreement on high-deadband days is partly MANUFACTURED by this shared root (R5-4). Pure-SVI sensitivity (fallback→0) isolates this; report baseline vs pure-SVI side-by-side.

### PROVENANCE / CONFOUND DECLARATION (R5-7)

- Production = whole-day EOD **vendor** vanna field + vendor IV (`eod_greeks`). New = intraday **analytic BS** vanna + intraday IV summed over firing-breach buckets.
- These are DIFFERENT objects (IV surface, vanna definition, aggregation window). A sign disagreement (notably QQQ 20260716) is CONFOUNDED and cannot be cleanly attributed without per-strike decomposition / aligned inputs. This is declared, not hidden.

### GATE VERDICT

**INDETERMINATE** — insufficient power/clusters (buckets=23, resolvable=5, eff-n=4, md=1.000) — must reach eff-n>=8

Required for a defensible correlational arm: eff-n >= 29 (md<=0.5) — documented; the binomial sign-agreement arm targets eff-n >= 8.

Ran in 89.2s. Raw: `dual_pipeline_gate_v3_obs.json`.