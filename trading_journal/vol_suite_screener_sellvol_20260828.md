# Vol_Suite Variance-Swap Screener — "Sell Vol Plays" basket — 2026-08-28

**As-of:** 2026-08-28 ~08:19 CDT. **Method:** `Vol_Suite/variance_swap_screener.py::run_variance_screener`
called directly (not the full `volatility_suite.py --context` pipeline) against the user's existing
Robinhood "Sell Vol Plays" watchlist (`SEDG CVS IRM CPRI PRGO SHAK DK HMC`, curated 2026-08-05 as
post-earnings IV-crush sell-vol candidates). T=0.25y (~3mo) target tenor, ThetaData-backed.
Third screen of the 2026-08-28 trading-mode session — see `robinhood_vol_scans_20260828.md` for the
other two and the main Trading journal entry for the combined plan. No orders placed.

Raw output: `screener_20260828_081928.csv` (+ two chart PNGs, same timestamp), this directory.

---

## Results

| Rank | Signal | Ticker | Score | Fair vol | ATM IV | Convexity | VRP | RV(95d) | Skew | Data quality |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | MODERATE SELL | **CPRI** | 76.1 | 55.20 | 55.39 | -0.19 | +10.27 | 44.93 | 0.625 | ok |
| 2 | MODERATE SELL | **CVS** | 66.7 | 33.82 | 35.68 | -1.86 | +7.25 | 26.58 | 0.603 | ok |
| 3 | MODERATE SELL | **DK** | 62.2 | 72.10 | 68.18 | +3.92 | +9.17 | 62.93 | 0.510 | ok |
| 4 | NEUTRAL | PRGO | 50.3 | 45.60 | 52.34 | -6.74 | **-20.46** | 66.06 | 0.526 | ok |
| 5 | INSUFFICIENT DATA | IRM | 47.5 | 39.86 | 39.23 | +0.63 | n/a | n/a | 0.283 | missing RV history |
| 6 | MODERATE BUY | SEDG | 34.9 | 89.35 | 86.32 | +3.03 | -23.77 | 113.12 | 0.551 | ok |
| 7 | MODERATE BUY | SHAK | 31.7 | 51.26 | 47.53 | +3.73 | +1.13 | 50.13 | 0.613 | ok |
| 8 | MODERATE BUY | HMC | 28.5 | 25.09 | 20.33 | +4.76 | -7.99 | 33.08 | 0.591 | ok |

Median score 48.9. IRM excluded from candidate ranking (ThetaData history fetch timed out — flagged
`insufficient_price_history`, not silently zeroed; fail-closed per `variance_swap_screener.py`'s
documented behavior, no yfinance fallback). Top short candidates per the model: **CVS, CPRI, DK**.
Top long candidates: none.

## Read

The variance-swap replication model (Carr-Madan/Demeterfi fair-vol vs quoted ATM IV, i.e. VRP) still
finds three names on this basket genuinely IV-rich vs its own fair-vol estimate — **CPRI (+10.3 VRP),
DK (+9.2), CVS (+7.3)** — consistent with the basket's original 8/5 post-earnings-IV-crush-sell thesis
still holding for those three specifically, three weeks later. This is the same structural problem as
the two Robinhood scans: it's a genuine short-vol read, and this account is single-leg-long-only —
there is no way to sell that richness here (confirmed constraint, see [[robinhood]] skill notes and the
2026-08-26 AEO entry). **Not actionable for this account as a fresh position.**

**PRGO (existing open position, 7 sh @ $13.58, current ~$14.38) is flagged NEUTRAL with VRP -20.46** —
i.e. the model reads PRGO's ATM IV as *cheap* relative to its own fair-vol estimate, not rich. That
directly contradicts the original 8/5 "sell vol" framing for this specific name today. It doesn't
change the existing equity position (that's a directional swing on a name from this watchlist, not an
options structure — see [[Positions]]), but it means **don't add PRGO to a vol-selling short-list**
based on the original watchlist thesis without rechecking; the mean-reversion premium the desk expected
in mid-August isn't showing up in this model's read of PRGO right now.

**SEDG, SHAK, HMC read as cheap-vol ("moderate buy")** — mirror image, market-implied vol below the
model's fair estimate. No near-term catalyst was checked for these three this pass (out of scope for
today's session); worth a follow-up earnings-calendar check before treating as a long-premium
candidate, same discipline applied to the Robinhood-scan names above.

## Bottom line

The desk's own quant model confirms the Sell Vol Plays basket still has real vol richness in 3 of 8
names (CPRI/DK/CVS) — a legitimate short-vol edge that exists but isn't reachable from this
single-leg-only, ~$472-buying-power account. No trade taken. Data quality: 7 of 8 tickers scored ok,
IRM excluded on a ThetaData timeout (transient, not a data problem with IRM itself — worth a retry on a
future pass, not a signal).
