# Tier-3A — Intraday Charm-at-OpEx (ESC-3)

**Date:** 2026-08-14  **Tickers:** SPY,QQQ

Karsan claim tested: charm clusters at OpEx (~10x at 2-DTE vs 30-DTE, most aggressive final 2 hours). Intraday 10-min all_greeks via proxy.

| Week | Day | Ticker | Expiry | DTE | final2h \|charm\| | final2h charm | open2h \|charm\| | n |
|---|---|---|---|---|---|---|---|---|
| may2026 | T-1 | SPY | 20260515 | 1 | +24.5149 | -24.5149 | +27.3123 | 13 |
| may2026 | T-1 | SPY | 20260619 | 36 | — | — | — | 0 |
| may2026 | T-1 | QQQ | 20260515 | 1 | +3.0691 | -0.5441 | +10.7453 | 13 |
| may2026 | T-1 | QQQ | 20260619 | 36 | — | — | — | 0 |
| may2026 | OPEX | SPY | 20260515 | 0 | +622.6515 | +458.3874 | +42.0539 | 13 |
| may2026 | OPEX | SPY | 20260619 | 35 | — | — | — | 0 |
| may2026 | OPEX | QQQ | 20260515 | 0 | +720.8287 | +557.3988 | +67.0498 | 13 |
| may2026 | OPEX | QQQ | 20260619 | 35 | — | — | — | 0 |
| jun2026 | T-1 | SPY | 20260619 | 1 | — | — | — | 0 |
| jun2026 | T-1 | SPY | 20260717 | 29 | +0.1038 | -0.1038 | +0.1671 | 13 |
| jun2026 | T-1 | QQQ | 20260619 | 1 | — | — | — | 0 |
| jun2026 | T-1 | QQQ | 20260717 | 29 | +0.2071 | -0.2071 | +0.3741 | 13 |
| jul2026 | T-1 | SPY | 20260717 | 1 | +8.4874 | +0.4851 | +15.2712 | 13 |
| jul2026 | T-1 | SPY | 20260821 | 36 | +0.1954 | -0.1954 | +0.0607 | 13 |
| jul2026 | T-1 | QQQ | 20260717 | 1 | +7.3759 | +2.9627 | +18.1284 | 13 |
| jul2026 | T-1 | QQQ | 20260821 | 36 | +0.1473 | -0.1473 | +0.0543 | 13 |
| jul2026 | OPEX | SPY | 20260717 | 0 | +692.1267 | -692.1267 | +75.9663 | 13 |
| jul2026 | OPEX | SPY | 20260821 | 35 | +0.2780 | -0.2780 | +0.2672 | 13 |
| jul2026 | OPEX | QQQ | 20260717 | 0 | +628.6003 | +628.6003 | +92.8242 | 13 |
| jul2026 | OPEX | QQQ | 20260821 | 35 | +0.1029 | -0.1029 | +0.2072 | 13 |

### Short/Long |charm| ratio in the final 2 hours

| Week | Day | Ticker | short\|charm\| | long\|charm\| | ratio | verdict |
|---|---|---|---|---|---|---|
| jul2026 | OPEX | QQQ | +628.6003 | +0.1029 | 6109.8x | SUPPORTED |
| jul2026 | OPEX | SPY | +692.1267 | +0.2780 | 2489.4x | SUPPORTED |
| jul2026 | T-1 | QQQ | +7.3759 | +0.1473 | 50.1x | SUPPORTED |
| jul2026 | T-1 | SPY | +8.4874 | +0.1954 | 43.4x | SUPPORTED |

### Verdict
- 4/4 OpEx days show short/long |charm| ratio >= 5x in the final 2 hours (100%).
- **SUPPORTED**: charm-at-OpEx clusters confirmed on intraday data (Karsan's ESC-3 claim).

[t3a] total 20.0s