# P0-1 — Dual-Pipeline Convention-Independence Gate (result)

**Date:** 2026-08-14  **Driver:** `run_dual_pipeline_gate.py`  **Tree:** Dealer-Exposure-Dev  **Ticker:** QQQ

**Pre-registered estimand:** see the driver docstring (bucket inclusion, exposure strata, sign convention, lead/lag lags, placebo design). Written before any network data run.

**Adaptation:** firing expiries are short-DTE weeklies not listed 150d out; the gate is the SAME-DAY production dealer_positioning vanna (vol_surface_replication SVI sign map, accumulate=False) vs new-engine ebe.vanna_flow on the same firing buckets.


### Data acquisition (provenance)

Sequential via ThetaData proxy (THETADATA_HIST_CONCURRENCY=1), credentials from main-tree .env (never persisted). Per day: stock/ohlc intraday, per-strike hist/all_greeks @600000 (C+P), hist/open_interest, and bulk_hist/option/eod_greeks single-day for the production same-day book.


### Firing-day summary

| Day | Expiry | T | open | close | grid | eod | intraday(C/P) | firing/buckets | prod_vanna | prod_sign | new_vanna_sum | new_sign | agree |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 20260716 | 20260717 | 0.0100 | 712.98 | 706.05 | 25 | 592 | 25/25 | 2/40 | -4.89e+04 | -1 | +4.61e+04 | 1 | NO |
| 20260717 | 20260717 | 0.0100 | 692.56 | 695.30 | 25 | 592 | 25/25 | 2/40 | NA | 0 | -1.36e+04 | -1 | n/a(0) |
| 20260731 | 20260803 | 0.0100 | 692.63 | 687.92 | 25 | 408 | 25/25 | 4/40 | -2.11e+04 | -1 | +5.43e+03 | 1 | NO |

### PRIMARY — SIGN AGREEMENT (Cem's convention-independence gate)

Production (SVI sign map) vs New-engine (−1×BS) vanna sign on firing days: **0/2** resolvable days agree (0% if resolvable else 'n/a').

- 20260716: prod_sign=-1 new_sign=1 → non-zero
- 20260717: prod_sign=0 new_sign=-1 → ZERO/missing — not counted
- 20260731: prod_sign=-1 new_sign=1 → non-zero

### SECONDARY — Correlations vs forward returns (descriptive)

- corr(new_vanna, fwd) Pearson = +0.5135  Spearman = +0.3571  (n=8 firing buckets, 3 days)  Fisher-tanh 95% CI [-0.300, +0.895]
  effective-n = 3 day-clusters (QQQ only, one family) — the K=3 CI is exploratory-only, NOT a valid narrow interval.

### LEAD/LAG FALSIFIER

- corr(N_i, fwd_{i+0}) = +0.5135  (n=8)
- corr(N_i, fwd_{i+1}) = +0.5224  (n=5; response one 10-min lag AFTER breach)
- corr(N_i, fwd_{i+2}) = +0.0000  (n=2; two lags after)
- REVERSE (prior return predicts signal): corr(N_i, fwd_{i-1}) = -0.6474  (n=5)
  20260731: lag0=-0.630 lag1=+0.000 lag2=+0.000 prior=+0.000
  read: lead/lag is NON-NULL (response persists after the breach bucket).
  CAUTION: prior-return predicts the signal more strongly than the signal predicts forward returns → reverse-causation/reflexivity present.

### PLACEBO NULL

- 1000 permutations (seed=7): observed corr = +0.5135; empirical p = 0.0949 (9.4% of null >= obs).
- null median = -0.0119; null 95th pct = +0.6002
  read: null NOT rejected at p<0.05.

### (A6) REFLEXIVITY BASELINE (Cem's decisive cheap diagnostic)

- corr(dIV, fwd) on the SAME firing buckets = +0.6549  (n=8)
- vanna corr +0.5135 vs reflexivity +0.6549 → vanna <= reflexivity — the vanna sign is downstream of dIV/return reflexivity (convention-bound)

### DATA GAPS

| day | endpoint | status | note |
|---|---|---|---|
| 20260717 | sign-agreement | zero/missing | prod_sign=0 new_sign=-1 |

NOTE on 20260717: the production same-day book returned 0 records because the firing expiry is the ANCHOR and expires THAT day (tte=0) — the production `dealer_positioning` expiry filter (0 < tte) drops an expiring weekly. This is a production-pipeline convention, not a proxy/data failure; the day is reported as sign=0 (not counted) per the pre-registered rule. The new engine is unaffected (it uses real days-to-expiry/365).

### GATE VERDICT: **FAIL** — sign-agreement 0/2 < 2/2

## IMPLEMENTATION AUDIT — level-vs-flow artifact (MANDATORY, done before Cem)

**Audit result: the raw 0/2 was PARTLY a level-vs-flow comparison artifact.** Correcting it, the gate is really **1/2, still FAIL**:

- Production `vanna_call+put_shares` (`dealer_positioning.py:765`) = vanna **LEVEL** = `vanna_total × CONTRACT_MULTIPLIER × VANNA_PP_SCALE` — no ΔIV factor.
- New `ebe.vanna_flow(ne, dIV)` (`expiry_book_exposure.py:374`) = **FLOW** = `Σ signed_vanna·OI·100·0.01·(dIV/0.01)` = level × (dIV/0.01).
- The driver compared sign(level) vs sign(level×ΔIV) — a level-vs-flow mismatch where the ΔIV factor mechanically flips the sign.
- **Corrected level-vs-level** (undo the dIV/0.01 on the new side, from cached obs, no re-fetch):

| Day | prod_level (sign) | new_day_LEVEL (sign) | agree |
|---|---|---|---|
| 20260716 | −4.89e4 (−1) | +3.82e4 (+1) | **NO** |
| 20260717 | NA (zero-DTE drop, 0) | +9.59e4 (+1) | n/a |
| 20260731 | −2.11e4 (−1) | −3.55e3 (−1) | **YES** |

- **20260731 flips to AGREE once compared level-vs-level.** The raw 0/2 was therefore overstated by the flow/level mismatch on that day.
- **20260716 genuinely disagrees** (−1 vs +1) — so even corrected, the gate is **1/2 < 2/2 → FAIL**.
- Remaining audit checks: zero-DTE 20260717 is a production-pipeline convention (expiry filter 0<tte drops the expiring weekly), handled explicitly (sign=0, not counted) — correct. Both engines use the same $5 grid, same day, same OI. Production SVI sign map vs new −1×BS are genuinely different conventions — the sign disagreement on 20260716 is real (not just the flow/level artifact).

**Status:** P0-1 gate FAILS on corrected 1/2 (20260716 genuine disagreement) AND the correlational arm is subsumed by reflexivity (A6 +0.6549 > vanna +0.5135, placebo p=0.095). **Cem is NOT dispatched** — the gate does not cross his bar. Model remains descriptive/conditional only.

Ran in 13.3s. Raw observations: `dual_pipeline_gate_obs.json`.