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

  (b) LIVE ΔIV-signed vannaflow — nonzero-vf buckets: 28/28 (0 would mean the vanna call silently failed)
  (b) LIVE ΔIV-signed vannaflow corr (EXPECT POSITIVE) = +0.2327  90% CI [+0.2052, +0.3051]
      verdict: underpowered (CI excludes 0 but |r|=0.2327 < md=0.993 at eff-n=3)

  (c) PAIRED two-model sign-agreement on firing buckets: 19/28 = 67.9%  (coherence, NOT predictiveness)

  (d) SHADOW-LEAK SUB-SAMPLE (intraday ΔIV opposite day's net ΔIV): n=14
      live vannaflow corr vs fwd = +0.4343  (EXPECT POSITIVE if the −0.088 was daily-shadow leak)
      md@n=14 = 0.6883 → BOUNDED/negative

  R2-5 note: md computed at effective-n (3 clusters), not pooled n.
  R2-7 note: firing clusters = 3 (20260803 is an expiry, not a day anchor).

### SINGLES screen (n=0)

[t3b] total 24.3s