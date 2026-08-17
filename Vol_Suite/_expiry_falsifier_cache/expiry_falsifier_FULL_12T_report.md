# Expiry Book Exposure — Phase 6 Falsifier Result (full corpus)

- Run: 2026-08-14 03:05:53
- Tickers: ['AAPL', 'AMD', 'AMZN', 'GOOGL', 'JPM', 'META', 'MSFT', 'NFLX', 'NVDA', 'QQQ', 'SPY', 'TSLA']
- n_perms: 500, corr_threshold: 0.15, perm p<0.05
- Elapsed: 4.6s
- OVERALL VERDICT: **NOT_SUPPORTED**
- SPY/QQQ sign-consistent: True

## Primary GEX channel (flow -> forward 1d returns)
| Ticker | n_days | corr | block_perm_p | q | verdict |
|---|---|---|---|---|---|
| AAPL | 145 | 0.033 | 0.7680 | 0.5640 | NOT_SUPPORTED |
| AMD | 163 | -0.019 | 0.8580 | 0.5640 | NOT_SUPPORTED |
| AMZN | 145 | -0.037 | 0.8360 | 0.5640 | NOT_SUPPORTED |
| GOOGL | 145 | 0.131 | 0.0820 | 0.5640 | NOT_SUPPORTED |
| JPM | 117 | -0.017 | 0.8900 | 0.5640 | NOT_SUPPORTED |
| META | 145 | -0.014 | 0.8940 | 0.5640 | NOT_SUPPORTED |
| MSFT | 145 | 0.133 | 0.1060 | 0.5640 | NOT_SUPPORTED |
| NFLX | 145 | -0.001 | 0.9920 | 0.5640 | NOT_SUPPORTED |
| NVDA | 145 | -0.075 | 0.3500 | 0.5640 | NOT_SUPPORTED |
| QQQ | 171 | -0.110 | 0.1540 | 0.5640 | NOT_SUPPORTED |
| SPY | 171 | -0.088 | 0.2160 | 0.5640 | NOT_SUPPORTED |
| TSLA | 145 | 0.039 | 0.6220 | 0.5640 | NOT_SUPPORTED |

## All channels
| Ticker | channel | n_days | corr | block_perm_p | q | verdict |
|---|---|---|---|---|---|---|
| ('AAPL', 'charm') | 145 | 0.042 | 0.6520 | 1.6215 | NOT_SUPPORTED |
| ('AAPL', 'gex') | 145 | 0.033 | 0.7680 | 0.5640 | NOT_SUPPORTED |
| ('AAPL', 'vanna') | 145 | 0.036 | 0.6380 | 0.8310 | NOT_SUPPORTED |
| ('AMD', 'charm') | 163 | 0.082 | 0.3360 | 1.6215 | NOT_SUPPORTED |
| ('AMD', 'gex') | 163 | -0.019 | 0.8580 | 0.5640 | NOT_SUPPORTED |
| ('AMD', 'vanna') | 163 | 0.029 | 0.7420 | 0.8310 | NOT_SUPPORTED |
| ('AMZN', 'charm') | 145 | 0.014 | 0.8540 | 1.6215 | NOT_SUPPORTED |
| ('AMZN', 'gex') | 145 | -0.037 | 0.8360 | 0.5640 | NOT_SUPPORTED |
| ('AMZN', 'vanna') | 145 | 0.066 | 0.4100 | 0.8310 | NOT_SUPPORTED |
| ('GOOGL', 'charm') | 145 | 0.051 | 0.5520 | 1.6215 | NOT_SUPPORTED |
| ('GOOGL', 'gex') | 145 | 0.131 | 0.0820 | 0.5640 | NOT_SUPPORTED |
| ('GOOGL', 'vanna') | 145 | 0.049 | 0.5940 | 0.8310 | NOT_SUPPORTED |
| ('JPM', 'charm') | 117 | -0.113 | 0.1580 | 1.6215 | NOT_SUPPORTED |
| ('JPM', 'gex') | 117 | -0.017 | 0.8900 | 0.5640 | NOT_SUPPORTED |
| ('JPM', 'vanna') | 117 | -0.143 | 0.0640 | 0.8310 | NOT_SUPPORTED |
| ('META', 'charm') | 145 | 0.065 | 0.5280 | 1.6215 | NOT_SUPPORTED |
| ('META', 'gex') | 145 | -0.014 | 0.8940 | 0.5640 | NOT_SUPPORTED |
| ('META', 'vanna') | 145 | 0.088 | 0.3980 | 0.8310 | NOT_SUPPORTED |
| ('MSFT', 'charm') | 145 | 0.056 | 0.4900 | 1.6215 | NOT_SUPPORTED |
| ('MSFT', 'gex') | 145 | 0.133 | 0.1060 | 0.5640 | NOT_SUPPORTED |
| ('MSFT', 'vanna') | 145 | 0.003 | 0.9780 | 0.8310 | NOT_SUPPORTED |
| ('NFLX', 'charm') | 145 | 0.046 | 0.6340 | 1.6215 | NOT_SUPPORTED |
| ('NFLX', 'gex') | 145 | -0.001 | 0.9920 | 0.5640 | NOT_SUPPORTED |
| ('NFLX', 'vanna') | 145 | -0.043 | 0.6680 | 0.8310 | NOT_SUPPORTED |
| ('NVDA', 'charm') | 145 | 0.110 | 0.1780 | 1.6215 | NOT_SUPPORTED |
| ('NVDA', 'gex') | 145 | -0.075 | 0.3500 | 0.5640 | NOT_SUPPORTED |
| ('NVDA', 'vanna') | 145 | 0.160 | 0.0500 | 0.8310 | NOT_SUPPORTED |
| ('QQQ', 'charm') | 171 | 0.051 | 0.4940 | 1.6215 | NOT_SUPPORTED |
| ('QQQ', 'gex') | 171 | -0.110 | 0.1540 | 0.5640 | NOT_SUPPORTED |
| ('QQQ', 'vanna') | 171 | 0.044 | 0.5680 | 0.8310 | NOT_SUPPORTED |
| ('SPY', 'charm') | 171 | 0.023 | 0.7560 | 1.6215 | NOT_SUPPORTED |
| ('SPY', 'gex') | 171 | -0.088 | 0.2160 | 0.5640 | NOT_SUPPORTED |
| ('SPY', 'vanna') | 171 | 0.013 | 0.8720 | 0.8310 | NOT_SUPPORTED |
| ('TSLA', 'charm') | 145 | 0.019 | 0.8540 | 1.6215 | NOT_SUPPORTED |
| ('TSLA', 'gex') | 145 | 0.039 | 0.6220 | 0.5640 | NOT_SUPPORTED |
| ('TSLA', 'vanna') | 145 | 0.041 | 0.6660 | 0.8310 | NOT_SUPPORTED |
## ARMS (run separately; the main run_expiry_falsifier does not populate `arms`)

### Vanna-lead arm (event-gated, only days with exogenous |dIV| >= 0.005)
Verdict: **INCONCLUSIVE** on 10/12 (shock-gating cuts sample below the usable-days floor); AMD NOT_SUPPORTED; NVDA INCONCLUSIVE (p=0.052, corr +0.273 — nearest to the threshold, but below p<0.05). Highest event-gated corrs: AAPL +0.269, NVDA +0.273, GOOGL +0.252 — all positive direction, none significant.

### OpEx event-window arm
Verdict: **NOT TESTABLE in this run** — the offline seed corpus carries no OpEx/expiry calendar, so `opex_dates=[]` → n=0 for every ticker. This is a DATA-SCOPE gap, not a model result: the event-window arm needs an OpEx Thu→Fri date list the corpus doesn't provide. Flagged for the build.

### Accumulated-overlay retest (does multi-day accumulated GEX add R2 over snapshot?)
Verdict: **REDUNDANT on 12/12** — adding the accumulated read never improves R² over the snapshot. This CONFIRMS the v1 falsifier finding (accumulation is redundant) under the new vector. Note: the absolute R² values are astronomically negative (order 1e9-1e15), a scale artifact in `accumulated_overlay_retest._r2` — the DIRECTIONAL conclusion (delta_R2 < 0, accumulation adds nothing) is consistent across all 12, but the R² magnitude should not be quoted.

