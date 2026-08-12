# Energy Watchlist Quant Deep Dive — 4-Day Recovery Sprint

As of: 2026-08-10 close (ThetaData EOD history fetched 2025-08-10 through 2026-08-10; last usable bars are 2026-08-10). Watchlist source: `/home/bottl/Financial_Development/tradingview_watchlists.json`, TradingView list `145246558`.

Account context: **$176.86**, equities only, no options. A move from $176.86 to $300 requires **+$123.14 / +69.6%** in four days; that is not a normal or reliable equity-trading expectation. The screen ranks setups and liquidity, not a promise of reaching the target. Suggested sizes are risk-capped allocations, not a recommendation to use leverage.

## Bottom line

**Best tradeable sprint candidates:**

1. **CRGY — $12.21, conviction 4.2/5.** Best combined technical trend and liquidity: EMA20 > EMA50 > EMA200, +20.7% over 30d, $63.0M average dollar volume. RSI 64.3 is warm but not extreme. Fundamental check is positive on Q2 revenue, cash flow and 1.6x net leverage, but absolute debt is high and oil exposure is large.
2. **PTEN — $10.99, conviction 3.9/5.** Highest liquidity in the list at $93.2M average dollar volume, +14.6% over 30d, RSI 54.0, and price well above EMA200. EMA20 remains below EMA50, and the company reported a Q2 net loss despite improving pricing and activity; treat as a cyclical momentum trade, not a clean fundamental compounder.
3. **WTI — $3.82, conviction 3.7/5.** +16.8% over 30d and +12.0% over 90d, $14.4M average dollar volume, RSI 54.0, and positive Q2 earnings/free cash flow. It is still 37.3% below its trailing-90-observation peak, so volatility is high; use a hard stop and do not average down.
4. **RNW — $6.13, conviction 3.6/5.** Strongest 90d return (+33.8%) with only an 8.7% trailing drawdown and $7.3M average dollar volume. Short-term return is -1.0% and RSI 45.9, so it is a pullback/reclaim setup rather than a breakout chase. Fundamental search results show growth/profitability evidence, but balance-sheet detail was not independently confirmed in this pass.

**Names to exclude from this account:** **MXC** and **SLNG** are explicitly untradeable at this size based on average dollar volume ($93.9K and $155.5K respectively). SLNG is also technically overextended (RSI 81.0). MXC's recent +26.9% move is not enough to offset the execution/slippage risk.

## Ranked screen — all 11 names

Score weighs momentum/EMA structure (30%), liquidity (25%), volatility/extension (20%), returns (15%), and fundamental sanity (10%). Liquidity flag uses the requested approximate **$2M average dollar-volume floor**.

| Rank | Ticker | Price | EMA20 / EMA50 / EMA200 | Structure | RSI14 | 30d | 90d | 90d max DD | Avg $Vol30 | Vol trend | Conviction | Trade status |
|---:|---|---:|---:|---|---:|---:|---:|---:|---:|---|---:|---|
| 1 | CRGY | $12.21 | $11.09 / $11.06 / $10.78 | aligned | 64.3 | +20.7% | -9.6% | -34.3% | $63.0M | 0.93x recent/prior volume; up/down $vol 1.73x | 4.2/5 | Tradeable; pullback preferred |
| 2 | PTEN | $10.99 | $10.02 / $10.18 / $9.18 | mixed, above 200 | 54.0 | +14.6% | +1.5% | -33.4% | $93.2M | 0.88x; up/down $vol 1.08x | 3.9/5 | Tradeable |
| 3 | WTI | $3.82 | $3.48 / $3.51 / $3.02 | mixed, above 200 | 54.0 | +16.8% | +12.0% | -37.3% | $14.4M | 1.01x; up/down $vol 1.30x | 3.7/5 | Tradeable; high beta |
| 4 | RNW | $6.13 | $6.18 / $6.09 / $6.11 | mixed-bull | 45.9 | -1.0% | +33.8% | -8.7% | $7.3M | 0.90x; up/down $vol 1.59x | 3.6/5 | Tradeable; reclaim setup |
| 5 | SD | $14.37 | $13.70 / $14.01 / $14.25 | mixed, below 50/200 | 56.6 | +6.4% | -11.9% | -17.4% | $4.0M | 0.80x; up/down $vol 1.37x | 3.3/5 | Tradeable but weaker trend |
| 6 | DEC | $14.34 | $13.45 / $13.73 / $14.42 | mixed, below 200 | 64.0 | +10.5% | -17.8% | -27.5% | $12.6M | 0.61x; up/down $vol 1.31x | 3.1/5 | Tradeable; avoid chasing |
| 7 | KOS | $2.51 | $2.46 / $2.49 / $2.25 | mixed, above 200 | 52.9 | +15.1% | -9.7% | -37.9% | $39.0M | 0.86x; up/down $vol 1.24x | 3.0/5 | Tradeable but debt-heavy |
| 8 | GPRK | $9.66 | $9.64 / $9.72 / $8.91 | mixed, below 50 | 40.2 | +4.7% | +1.7% | -23.2% | $3.7M | 0.78x; up/down $vol 0.92x | 2.9/5 | Marginally tradeable |
| 9 | INR | $13.95 | $13.02 / $13.36 / $14.41 | mixed, below 200 | 56.1 | +7.3% | -20.8% | -29.8% | $3.7M | 0.74x; up/down $vol 0.75x | 2.6/5 | Tradeable but weak trend |
| 10 | SLNG | $5.005 | $4.56 / $4.37 / $4.37 | mixed-bull, extended | 81.0 | +28.0% | +12.2% | -31.6% | **$155.5K** | 1.39x; up/down $vol 2.16x | 1.8/5 | **Exclude: illiquid/overbought** |
| 11 | MXC | $9.47 | $8.95 / $8.84 / $9.26 | mixed, below 200 | 55.1 | +26.9% | -10.9% | -28.5% | **$93.9K** | 1.29x; up/down $vol 6.49x | 1.5/5 | **Exclude: illiquid** |

Volume trend is last 20 average volume divided by the prior 20 average. Up/down dollar-volume ratio is calculated over the latest 20 usable bars; it is a confirmation statistic, not a standalone signal.

## Detailed trade notes

### 1. CRGY — preferred momentum/liquidity balance

- Price: **$12.21**
- EMA20/50/200: **$11.09 / $11.06 / $10.78**; clean bullish alignment.
- RSI(14): **64.3**; positive momentum, not yet at the screen's overbought exclusion zone.
- Returns: **+20.7% 30d; -9.6% 90d**. The 90d drawdown is **-34.3%**, so this is a rebound leg inside a volatile larger range, not a low-risk trend.
- Liquidity: **$62.98M average dollar volume over 30 bars**.
- Entry band: **$11.10-$11.65** on a pullback toward EMA20/EMA50; a clean hold/reclaim of $12.21 can be a smaller starter only. Do not chase a gap materially above $12.21.
- Suggested allocation: **$45-$50**; use a small position because of commodity sensitivity and leverage.
- Technical read: strongest alignment in the list among liquid names, with recent momentum and positive up-day volume confirmation.
- Fundamental sanity: Q2 2026 revenue was **$1.395B**, six-month operating cash flow **$1.116B**, cash about **$265M**, total debt **$5.166B**, net debt **$4.901B**, and net leverage **1.6x**. Earnings/cash generation are supportive, but the debt load and oil-price exposure are material risks.

### 2. PTEN — liquid cyclical recovery

- Price: **$10.99**
- EMA20/50/200: **$10.02 / $10.18 / $9.18**; price is above EMA200, but EMA20 below EMA50 means the short-term trend is not fully aligned.
- RSI(14): **54.0**.
- Returns: **+14.6% 30d; +1.5% 90d**; 90d max drawdown **-33.4%**.
- Liquidity: **$93.23M average dollar volume over 30 bars**, best execution profile in the list.
- Entry band: **$10.02-$10.55** around EMA20 and the recent support zone. Above $11.00, wait for a new consolidation rather than buying a vertical move.
- Suggested allocation: **$40-$45**.
- Technical read: liquid rebound above the 200-day average, but 30d strength has not yet translated into a strong 90d trend.
- Fundamental sanity: Q1 2026 revenue was **$1.117B**, cash **$337M**, and long-term debt **$1.221B**; Q1 net income was a **$24.5M loss**. Q2 commentary reported sequential revenue growth, improving day rates and completion pricing, but also a roughly **$20M net loss** and working-capital pressure. This is a cyclical operating-improvement trade, not a clean balance-sheet trade.

### 3. WTI — best pure short-window beta among the liquid names

- Price: **$3.82**
- EMA20/50/200: **$3.48 / $3.51 / $3.02**; above EMA200, but EMA20 is slightly below EMA50.
- RSI(14): **54.0**.
- Returns: **+16.8% 30d; +12.0% 90d**; 90d max drawdown **-37.3%**.
- Liquidity: **$14.45M average dollar volume over 30 bars**.
- Entry band: **$3.48-$3.65**; $3.82 is acceptable only with a defined stop and small size. A break below EMA50 without recovery is a failed setup.
- Suggested allocation: **$35-$40**.
- Technical read: positive 30d/90d momentum with modest up-day volume confirmation, but the deep drawdown shows this can move sharply against the position.
- Fundamental sanity: Q2 2026 revenue **$162.6M**, net income **$12.6M**, adjusted EBITDA **$54.4M**, operating cash flow **$33.3M**, cash **$150.7M**, total debt **$351.6M**, and net debt **$200.9M**. Q2 free cash flow was **$31.4M**. Positive current cash generation is offset by offshore/decommissioning obligations and commodity risk.

### 4. RNW — strongest medium-window trend, currently resting

- Price: **$6.13**
- EMA20/50/200: **$6.18 / $6.09 / $6.11**; short-term mixed-bull structure, with price just under EMA20.
- RSI(14): **45.9**.
- Returns: **-1.0% 30d; +33.8% 90d**; 90d max drawdown **-8.7%**, the least extended drawdown in the group.
- Liquidity: **$7.34M average dollar volume over 30 bars**.
- Entry band: **$6.09-$6.18** on an EMA reclaim/hold. If it loses $6.09 and cannot reclaim quickly, stand aside rather than assuming the 90d trend will resume.
- Suggested allocation: **$25-$35**.
- Technical read: best 90d trend-to-drawdown combination; current RSI and 30d pause are preferable to an overextended chase.
- Fundamental sanity: ReNew's investor materials/search results reported H1 FY26 power-sale revenue of **INR 51.548B ($581M)** versus INR 48.342B, an FY26 adjusted-EBITDA outlook of **INR 87B-INR 93B**, and FY26 cash-flow-to-equity guidance of **INR 14B-INR 17B**. The search pass did not independently verify a current debt/cash figure, so balance-sheet risk remains an open item.

### 5. SD — fundamentally clean, technically less compelling

- Price: **$14.37**; EMA20/50/200 **$13.70 / $14.01 / $14.25**; RSI **56.6**.
- Returns **+6.4% 30d / -11.9% 90d**, 90d max drawdown **-17.4%**, average dollar volume **$4.02M**.
- Entry band: **$13.70-$14.05**; current price is above the EMA support area.
- Fundamental sanity: Q2 revenue approximately **$51M**, up **48% YoY**, adjusted EBITDA approximately **$34M**, and roughly **$115M cash with no debt**. Partial hedging leaves commodity exposure, and the Cherokee acquisition adds execution risk.
- Use as a lower-balance-sheet-risk satellite only if price reclaims/holds above $14.25 with volume; it is not ranked above the top four because its 90d trend is negative.

## Allocation template for $176.86

Fractional-share version, assuming entries are near the bands rather than blindly at the latest close:

| Name | Planned dollars | Role |
|---|---:|---|
| CRGY | $48 | Primary liquid momentum |
| PTEN | $43 | Liquid cyclical recovery |
| WTI | $38 | Higher-beta oil exposure |
| RNW | $32 | Medium-window trend/reclaim |
| Cash reserve | $15.86 | Slippage, stops, and optional follow-through |
| **Total** | **$176.86** | |

Do not deploy the cash reserve merely to force the $300 objective. If fractional shares are unavailable, prefer whole shares of **PTEN, WTI, RNW, or SD** and do not force CRGY if the planned share count would concentrate too much capital. MXC and SLNG should not be used for this account regardless of headline momentum.

Risk handling for a four-day sprint: define the stop before entry; reduce or exit a position that loses its entry band and fails to reclaim it; do not average down; avoid holding through an earnings/news binary unless that risk is intentional. A 69.6% account-level target would require extreme concentration or leverage and is not supported by this screen.

## Ranked picks list

1. **CRGY** — 4.2/5 — $11.10-$11.65 pullback band; best liquid technical setup, but debt/commodity risk.
2. **PTEN** — 3.9/5 — $10.02-$10.55; best liquidity, improving industry pricing, mixed EMA.
3. **WTI** — 3.7/5 — $3.48-$3.65; positive 30d/90d momentum, high drawdown risk.
4. **RNW** — 3.6/5 — $6.09-$6.18; best 90d trend/drawdown profile, needs EMA reclaim.
5. **SD** — 3.3/5 — $13.70-$14.05; debt-free fundamental profile, weaker 90d tape.
6. **DEC** — 3.1/5 — $13.45-$14.00; strong cash generation but below EMA200 and leverage 2.45x.
7. **KOS** — 3.0/5 — $2.46-$2.50; liquid and positive 30d, but negative 90d and $2.56B net debt.
8. **GPRK** — 2.9/5 — $9.64-$9.72; sound Q2 fundamentals, weak short-term volume/trend confirmation.
9. **INR** — 2.6/5 — $13.02-$13.35; below EMA200, -20.8% over 90d, fundamentals not independently verified in this pass.
10. **SLNG** — 1.8/5 — exclude; $155.5K average dollar volume and RSI 81.0.
11. **MXC** — 1.5/5 — exclude; $93.9K average dollar volume despite +26.9% 30d return.

## Data and provider notes

### ThetaData

- **11/11 tickers returned usable history; no ThetaData provider failures.**
- Most names returned 251 usable daily bars; SLNG returned 231 and MXC 245. The resulting date coverage is 2025-08-11 through 2026-08-10 for most names, with SLNG beginning 2025-08-14 and MXC beginning 2025-08-11.
- Metrics are computed from close and volume: recursive EMA spans 20/50/200; close-based RSI(14); returns from approximately 30 and 90 observations; max drawdown over the last 90 observations; average `(close × volume)` over the last 30 observations.
- The final EOD print is not necessarily a live executable bid/ask. Recheck live quote, spread, halt status, and news before trading.

### Web/fundamental search

- Reachable search produced usable current/fiscal evidence for **RNW, CRGY, PTEN, WTI, SLNG, KOS, DEC, GPRK, INR ticker identification, and SD**. Sources are listed below.
- **MXC** search returned older/aggregated snippets (including approximately $6.56M revenue and approximately $1.4M cash/no bank-line debt in one result), but no sufficiently current primary filing was confirmed in this pass; treat fundamentals as unverified.
- **INR** was confirmed as Infinity Natural Resources, but the first-pass result set did not provide a sufficiently complete current cash/debt/dilution check; do not use it as a fundamental pick.
- Search snippets and secondary summaries are sanity checks, not substitutes for reading the latest 10-Q/20-F. No claim above should be read as audited or valuation advice.

## Fundamental source links

- RNW: https://investor.renew.com/news-releases/news-release-details/renew-announces-results-second-quarter-fiscal-year-2026-q2-fy26
- CRGY: https://www.stocktitan.net/sec-filings/CRGY/8-k-crescent-energy-co-reports-material-event-1a347899e7ba.html
- PTEN: https://investor.patenergy.com/Investors/News-and-Events/news/news-details/2026/Patterson-UTI-Energy-Reports-Financial-Results-for-the-Quarter-Ended-June-30-2026-/default.aspx
- WTI: https://www.wtoffshore.com/news-media/press-releases/detail/507/wt-offshore-announces-second-quarter-2026-results-and
- SLNG: https://www.sec.gov/Archives/edgar/data/1043186/000143774926015274/ex_933796.htm
- KOS: https://investors.kosmosenergy.com/news-releases/news-release-details/kosmos-energy-announces-second-quarter-2026-results
- DEC: https://ir.div.energy/news-events/us-press-releases/detail/229/diversified-energy-reports-second-quarter-2026-results
- GPRK: https://www.geo-park.com/press_releases/second-quarter-2026-results/
- INR identification: https://ir.infinitynaturalresources.com/overview/default.aspx
- SD: https://uk.finance.yahoo.com/news/sandridge-energy-inc-sd-q2-010644258.html

This is a quantitative screen and execution framework, not individualized investment advice or a guarantee of profit.
