# P0-2/P0-3 ROUND-5 — REQUIRED UNIQUE-DAY ACQUISITION (result)

**Date:** 2026-08-14  **Driver:** `run_dual_pipeline_gate_v4.py`

**R5 corrections preserved (all from v3):** unique-day dedup (R5-1), cluster-level CI (R5-2), SVI sign provenance (R5-3), pure-SVI fallback->0 sensitivity (R5-4), low/high |dIV| stratification (R5-5), exact binomial null (R5-6), provenance/confound declaration (R5-7), SPY kept separate (R5-8).

### Data acquisition

Sequential via ThetaData proxy (THETADATA_HIST_CONCURRENCY=1), creds from main-tree .env (never persisted). 14 NEW unique calendar days, each in a SEPARATE acquisition session (session id `r5-<day>-<ticker>`), paced with a sleep between days. Per day: stock/ohlc intraday, per-strike all_greeks @600000 (C+P), open_interest, bulk_hist/option/eod_greeks single-day for the production same-day book. Prior seed-day rows merged from `dual_pipeline_gate_v3_obs.json` (separate earlier sessions — not a shared-session confound).

### ROUND-5 ACQUISITION MANIFEST (pre-registered)

| Day | Expiry | DTE | Event / rationale |
|---|---|---|---|
| 20260413 | 20260417 | 4 | pre-OpEx Mon |
| 20260417 | 20260424 | 7 | Apr OpEx Fri (MID) |
| 20260429 | 20260501 | 2 | FOMC+earnings Wed |
| 20260430 | 20260501 | 1 | post-FOMC/earnings Thu |
| 20260515 | 20260522 | 7 | May OpEx Fri (MID) |
| 20260520 | 20260522 | 2 | NVDA earnings Wed |
| 20260529 | 20260605 | 7 | month-end/QQQ-qtr Fri (MID) |
| 20260616 | 20260626 | 10 | FOMC day1 Tue (MID) |
| 20260617 | 20260624 | 7 | FOMC Wed (MID) |
| 20260721 | 20260724 | 3 | MSFT earnings Tue |
| 20260722 | 20260724 | 2 | TSLA earnings Wed |
| 20260728 | 20260731 | 3 | FOMC day1+AAPL Tue |
| 20260729 | 20260731 | 2 | FOMC+META Wed |
| 20260807 | 20260810 | 3 | post-jobs vol Fri |

### ROUND-5 ACQUISITION RESULTS (this run)

| Ticker | Day | Expiry | DTE | firing/buckets | prod_sign | new_LEVEL_sign | agree | mean\|dIV\| | pureSVI_sign |
|---|---|---|---|---|---|---|---|---|---|
| QQQ | 20260413 | 20260417 | 4 | 0/40 | 1 | 0 | n/a | 0.0000 | -1 |
| QQQ | 20260417 | 20260424 | 7 | 0/40 | 1 | 0 | n/a | 0.0000 | 0 |
| QQQ | 20260429 | 20260501 | 2 | 0/40 | 1 | 0 | n/a | 0.0000 | -1 |
| QQQ | 20260430 | 20260501 | 1 | 0/40 | 1 | 0 | n/a | 0.0000 | -1 |
| QQQ | 20260515 | 20260522 | 7 | 0/40 | 1 | 0 | n/a | 0.0000 | -1 |
| QQQ | 20260520 | 20260522 | 2 | 0/40 | 1 | 0 | n/a | 0.0000 | 1 |
| QQQ | 20260529 | 20260605 | 7 | 0/40 | 1 | 0 | n/a | 0.0000 | -1 |
| QQQ | 20260616 | 20260626 | 10 | 0/40 | 1 | 0 | n/a | 0.0000 | -1 |
| QQQ | 20260617 | 20260624 | 7 | 2/40 | 1 | -1 | NO | 0.0135 | 0 |
| QQQ | 20260721 | 20260724 | 3 | 0/40 | 1 | 0 | n/a | 0.0000 | 0 |
| QQQ | 20260722 | 20260724 | 2 | 0/40 | -1 | 0 | n/a | 0.0000 | -1 |
| QQQ | 20260728 | 20260731 | 3 | 0/40 | 1 | 0 | n/a | 0.0000 | 1 |
| QQQ | 20260729 | 20260731 | 2 | 3/40 | 1 | 1 | YES | 0.0248 | -1 |
| QQQ | 20260807 | 20260810 | 3 | 0/40 | 1 | 0 | n/a | 0.0000 | -1 |
| SPY | 20260413 | 20260417 | 4 | 7/40 | 1 | -1 | NO | 0.0167 | -1 |
| SPY | 20260417 | 20260424 | 7 | 0/40 | 1 | 0 | n/a | 0.0000 | 0 |
| SPY | 20260429 | 20260501 | 2 | 0/40 | 1 | 0 | n/a | 0.0000 | -1 |
| SPY | 20260430 | 20260501 | 1 | 1/40 | 1 | -1 | NO | 0.0257 | 0 |
| SPY | 20260515 | 20260522 | 7 | 0/40 | 1 | 0 | n/a | 0.0000 | -1 |
| SPY | 20260520 | 20260522 | 2 | 0/40 | 1 | 0 | n/a | 0.0000 | -1 |
| SPY | 20260529 | 20260605 | 7 | 0/40 | 1 | 0 | n/a | 0.0000 | 0 |
| SPY | 20260616 | 20260626 | 10 | 0/40 | 1 | 0 | n/a | 0.0000 | 0 |
| SPY | 20260617 | 20260624 | 7 | 2/40 | 1 | 1 | YES | 0.0138 | -1 |
| SPY | 20260721 | 20260724 | 3 | 0/40 | 1 | 0 | n/a | 0.0000 | 0 |
| SPY | 20260722 | 20260724 | 2 | 0/40 | 1 | 0 | n/a | 0.0000 | -1 |
| SPY | 20260728 | 20260731 | 3 | 1/40 | -1 | -1 | YES | 0.0168 | -1 |
| SPY | 20260729 | 20260731 | 2 | 1/40 | 1 | -1 | NO | 0.0187 | -1 |
| SPY | 20260807 | 20260810 | 3 | 0/40 | 1 | 0 | n/a | 0.0000 | 1 |

### PRIMARY — SIGN AGREEMENT (unique-day dedup, R5-1; merged seed + new)

- This-run clusters: 28; merged seed rows: 12 (5 resolvable, 4 unique days).
- Resolvable (both-nonzero) clusters: 12  **unique calendar days: 9** (SPY 6 independent-day resolvable; SPY kept separate, R5-8).

- Level-vs-level agreement: **6/12** resolvable = 50% if any.
  - QQQ 20260508: prod=1 new=-1 → DISAGREE (mean|dIV|=0.0113)
  - QQQ 20260605: prod=1 new=1 → AGREE (mean|dIV|=0.0161)
  - QQQ 20260716: prod=-1 new=1 → DISAGREE (mean|dIV|=0.0118)
  - QQQ 20260731: prod=-1 new=-1 → AGREE (mean|dIV|=0.0161)
  - SPY 20260605: prod=1 new=1 → AGREE (mean|dIV|=0.0170)
  - QQQ 20260617: prod=1 new=-1 → DISAGREE (mean|dIV|=0.0135)
  - QQQ 20260729: prod=1 new=1 → AGREE (mean|dIV|=0.0248)
  - SPY 20260413: prod=1 new=-1 → DISAGREE (mean|dIV|=0.0167)
  - SPY 20260430: prod=1 new=-1 → DISAGREE (mean|dIV|=0.0257)
  - SPY 20260617: prod=1 new=1 → AGREE (mean|dIV|=0.0138)
  - SPY 20260728: prod=-1 new=-1 → AGREE (mean|dIV|=0.0168)
  - SPY 20260729: prod=1 new=-1 → DISAGREE (mean|dIV|=0.0187)
- Binomial null: P(agree>=6 | n=12, p=0.5) = **0.6128**; P(pass bar >= 8/12) = **0.1938** (R5-6).
- md at eff-n=9 = **0.816** → powered.

### SPY vs QQQ SEPARATE (R5-8)

- SPY: agree 3/6 resolvable (6 independent unique days). Index sub-analysis valid only if >= 2 independent SPY days.
- QQQ: agree 3/6 resolvable (6 independent unique days).

### CORRELATIONAL ARMS (cluster-level, R5-2/R5-5/R5-8; this run's firing buckets)

- corr(new_LEVEL, fwd) pooled = -0.1568 (n=17) — **Simpson caveat**: SPY -0.3774 vs QQQ +0.6493 opposite-sign families; pooled is a cancellation, NOT a null. SPY not an index claim (6 indep day).
- corr by |dIV| strata (R5-5): low -0.0032 vs high -0.5138 (median |dIV|=0.0168).
- A6 reflexivity baseline corr(ΔIV, fwd) = +0.0611 vs vanna -0.1568 → vanna <= reflexivity (convention-bound).
- cluster-level 95% CI at eff-n=9: [-0.744, +0.566] (pooled-bucket CI is descriptive only, R5-2).
- placebo p = 0.7323 (null NOT rejected); at md 0.816 this is powered.

### LEAD/LAG FALSIFIER

- corr(N_i, fwd_{i+0}) = -0.1568  (n=17)
- corr(N_i, fwd_{i+1}) = +0.2792  (n=10)
- corr(N_i, fwd_{i+2}) = -0.9862  (n=6)
- REVERSE (prior return predicts signal): corr(N_i, fwd_{i-1}) = +0.2786  (n=10)
  read: lead/lag is NULL/weak — no persistent response after the breach bucket.
  CAUTION: prior-return predicts the signal more strongly than the signal predicts forward returns → reverse-causation/reflexivity present.

### SVI SIGN PROVENANCE (R5-3) + PURE-SVI SENSITIVITY (R5-4)

| Ticker | Day | total_strikes | rich+1 | cheap-1 | deadband→fallback-1 | missing | pureSVI_sign |
|---|---|---|---|---|---|---|---|
| QQQ | 20260413 | 47 | 22 | 23 | 2 (→ -1 fallback) | 0 | -1 |
| QQQ | 20260417 | 45 | 22 | 22 | 1 (→ -1 fallback) | 0 | 0 |
| QQQ | 20260429 | 37 | 16 | 18 | 3 (→ -1 fallback) | 0 | -1 |
| QQQ | 20260430 | 42 | 20 | 21 | 1 (→ -1 fallback) | 0 | -1 |
| QQQ | 20260515 | 42 | 19 | 20 | 3 (→ -1 fallback) | 0 | -1 |
| QQQ | 20260520 | 39 | 19 | 18 | 2 (→ -1 fallback) | 0 | 1 |
| QQQ | 20260529 | 47 | 22 | 23 | 2 (→ -1 fallback) | 0 | -1 |
| QQQ | 20260616 | 50 | 23 | 24 | 3 (→ -1 fallback) | 0 | -1 |
| QQQ | 20260617 | 44 | 21 | 21 | 2 (→ -1 fallback) | 0 | 0 |
| QQQ | 20260721 | 47 | 23 | 23 | 1 (→ -1 fallback) | 0 | 0 |
| QQQ | 20260722 | 39 | 18 | 19 | 2 (→ -1 fallback) | 0 | -1 |
| QQQ | 20260728 | 50 | 24 | 23 | 3 (→ -1 fallback) | 0 | 1 |
| QQQ | 20260729 | 47 | 22 | 23 | 2 (→ -1 fallback) | 0 | -1 |
| QQQ | 20260807 | 40 | 19 | 20 | 1 (→ -1 fallback) | 0 | -1 |
| SPY | 20260413 | 50 | 24 | 25 | 1 (→ -1 fallback) | 0 | -1 |
| SPY | 20260417 | 50 | 24 | 24 | 2 (→ -1 fallback) | 0 | 0 |
| SPY | 20260429 | 38 | 18 | 19 | 1 (→ -1 fallback) | 0 | -1 |
| SPY | 20260430 | 39 | 19 | 19 | 1 (→ -1 fallback) | 0 | 0 |
| SPY | 20260515 | 42 | 20 | 21 | 1 (→ -1 fallback) | 0 | -1 |
| SPY | 20260520 | 46 | 21 | 23 | 2 (→ -1 fallback) | 0 | -1 |
| SPY | 20260529 | 50 | 24 | 24 | 2 (→ -1 fallback) | 0 | 0 |
| SPY | 20260616 | 43 | 21 | 21 | 1 (→ -1 fallback) | 0 | 0 |
| SPY | 20260617 | 44 | 19 | 22 | 3 (→ -1 fallback) | 0 | -1 |
| SPY | 20260721 | 49 | 24 | 24 | 1 (→ -1 fallback) | 0 | 0 |
| SPY | 20260722 | 39 | 18 | 19 | 2 (→ -1 fallback) | 0 | -1 |
| SPY | 20260728 | 50 | 24 | 25 | 1 (→ -1 fallback) | 0 | -1 |
| SPY | 20260729 | 43 | 20 | 21 | 2 (→ -1 fallback) | 0 | -1 |
| SPY | 20260807 | 38 | 16 | 13 | 9 (→ -1 fallback) | 0 | 1 |
- Pure-SVI sensitivity: of 5 resolvable clusters with a non-zero SVI read, **1** agree with production when deadband strikes contribute 0 (fallback→0, R5-4). This isolates agreement manufactured by the shared -1 root.

### PROVENANCE / CONFOUND DECLARATION (R5-7)

- Production = whole-day EOD **vendor** vanna field + vendor IV (`eod_greeks`). New = intraday **analytic BS** vanna + intraday IV summed over firing-breach buckets.
- These are DIFFERENT objects (IV surface, vanna definition, aggregation window). Any sign disagreement is CONFOUNDED and cannot be cleanly attributed without per-strike decomposition / aligned inputs. Declared, not hidden.

### DATA GAPS / EXCLUDED CLUSTERS

- none recorded.

Excluded (zero-sign / non-resolvable) clusters:
- QQQ 20260522: prod=-1 new=0 firing=0
- QQQ 20260717: prod=0 new=1 firing=2
- SPY 20260508: prod=1 new=0 firing=0
- SPY 20260522: prod=1 new=0 firing=0
- SPY 20260716: prod=-1 new=0 firing=0
- SPY 20260717: prod=0 new=-1 firing=1
- SPY 20260731: prod=1 new=0 firing=0
- QQQ 20260413: prod=1 new=0 firing=0
- QQQ 20260417: prod=1 new=0 firing=0
- QQQ 20260429: prod=1 new=0 firing=0
- QQQ 20260430: prod=1 new=0 firing=0
- QQQ 20260515: prod=1 new=0 firing=0
- QQQ 20260520: prod=1 new=0 firing=0
- QQQ 20260529: prod=1 new=0 firing=0
- QQQ 20260616: prod=1 new=0 firing=0
- QQQ 20260721: prod=1 new=0 firing=0
- QQQ 20260722: prod=-1 new=0 firing=0
- QQQ 20260728: prod=1 new=0 firing=0
- QQQ 20260807: prod=1 new=0 firing=0
- SPY 20260417: prod=1 new=0 firing=0
- SPY 20260429: prod=1 new=0 firing=0
- SPY 20260515: prod=1 new=0 firing=0
- SPY 20260520: prod=1 new=0 firing=0
- SPY 20260529: prod=1 new=0 firing=0
- SPY 20260616: prod=1 new=0 firing=0
- SPY 20260721: prod=1 new=0 firing=0
- SPY 20260722: prod=1 new=0 firing=0
- SPY 20260807: prod=1 new=0 firing=0

### GATE VERDICT

**FAIL** — level-level agree 6/12=50% < 2/3

Required for a defensible correlational arm: eff-n >= 29 (md<=0.5) — documented; the binomial sign-agreement arm targets eff-n >= 8.

Ran in 532.1s. Raw: `dual_pipeline_gate_v4_obs.json`.

### TESTS (network-free)
`env -u PYTHONPATH -u VIRTUAL_ENV C:/Users/bottl/FinancialDevelopment/.venv/Scripts/python.exe -m pytest tests/test_dual_pipeline_gate_v4.py tests/test_dual_pipeline_gate_v3.py tests/test_dual_pipeline_gate.py tests/test_expiry_intraday_round3.py -q`
→ **60 passed** (17 v4 + all prior R5-1..8 locks + P0-1/P0-2 + expiry-intraday round-3). No network / .env / wall-clock dependency.