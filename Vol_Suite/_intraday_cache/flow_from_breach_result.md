# Tier-3B — From-breach intraday clock (ΔIV-signed vanna flow vs forward 10-min returns) — v2

**Date:** 2026-08-14  **Days:** ['20260716', '20260717', '20260731']

Prediction (Karsan two-clock): from-breach/intraday EXPECT POSITIVE — only a null HERE refutes the mechanism. Locus anchored once per day at the opening bucket (C+P chain, real OI), band = open spot ±1% fixed for the day.


### Firing buckets per (ticker, day)
| Ticker | Day | Expiry | firing | buckets | locus |
|---|---|---|---|---|---|
| QQQ | 20260716 | 20260717 | 6 | 40 | zg=715 band=[705.9,720.1] |
| QQQ | 20260717 | 20260717 | 9 | 40 | zg=695 band=[685.6,699.5] |
| QQQ | 20260731 | 20260803 | 13 | 40 | zg=695 band=[685.7,699.6] |

### INDEX PRIMARY (SPY/QQQ = one family)  [R2-5: effective-n bars]
firing buckets (from-breach): 28  effective clusters: 3

  (a) NEW gamma-burst signed flow corr = -0.0884  90% CI [-0.1431, -0.0399]  n=28 eff-n=3
      verdict: underpowered (CI excludes 0 but |r|=0.0884 < md=0.993 at eff-n=3)

  (b) NEW-model ΔIV-signed vannaflow (ebe.vanna_flow, −1×BS) — nonzero-vf buckets: 28/28 (0 would mean the vanna call silently failed)
  (b) NEW-model vannaflow corr (EXPECT POSITIVE) = +0.2327  90% CI [+0.2050, +0.3051]
      verdict: underpowered (CI excludes 0 but |r|=0.2327 < md=0.993 at eff-n=3)
      NOTE: single-engine quantity linear in ΔIV — positive sign is consistent with, not probative of, the dealer mechanism (convention × vol-return reflexivity). QQQ-only, not an index result.

  (b2) PER-DAY sign-consistency (primary statistic at eff-n=3):
      QQQ 20260716: corr(vf, fwd) = +0.3051  (n=6)
      QQQ 20260717: corr(vf, fwd) = +0.2939  (n=9)
      QQQ 20260731: corr(vf, fwd) = +0.4434  (n=13)

  (b3) EXPOSURE-RESPONSE terciles by |net vanna| (F3):
      low-|vanna| tercile: corr = +0.4867  (n=9)
      mid-|vanna| tercile: corr = +0.5917  (n=9)
      high-|vanna| tercile: corr = +0.4053  (n=10)

  (b4) OPPOSITE-CONVENTION rerun (signed_vanna=+1×BS, sensitivity falsifier): corr = -0.2327  90% CI [-0.3051, -0.2050]
      read: sign flip (~−0.23) ⇒ convention-bound; collapse (~0) ⇒ exposure weighting does the work

  (c) WITHIN-ENGINE sign-agreement on firing buckets: 19/28 = 67.9%
      NOTE: dIV cancels — sign(sf)==sign(vf) reduces to sign(burst)==−sign(net_vanna), a within-engine self-consistency of the NEW model's burst vs its own net-vanna sign. NOT an independent two-model coherence; no null/CI reported (descriptive only).

  (d) SHADOW-LEAK SPLIT (post-hoc EXPLORATORY — conditions on the same ΔIV that defines x): n=14
      new-model vannaflow corr vs fwd = +0.4346  (EXPECT POSITIVE if the −0.088 was daily-shadow leak)
      md@n=14 = 0.6883 → BOUNDED/negative
      [integrity] +0.4346 < md 0.6883 → code verdict is BOUNDED/negative, NOT 'strongly POSITIVE'

  R2-5 note: md computed at effective-n (3 clusters), not pooled n.
  R2-7 note: firing clusters = 3 (20260803 is an expiry, not a day anchor).
  R2-8 note (bootstrap limitation): K=3 cluster bootstrap draws with replacement over 3 clusters → only C(3+3-1,3) distinct resampled multisets (e.g. 10 for K=3) → the reported 90% CI is a coarse discrete-quantile, demoted to exploratory. It is NOT a valid narrow inferential interval at this effective-n.

### SINGLES screen (n=0)

[t3b] total 54.8s