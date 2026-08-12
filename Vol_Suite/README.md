# Variance Swap & Volatility Analytics Suite

## Overview
A comprehensive Python suite for variance swap pricing, volatility screening, correlation/covariance analysis, dealer positioning, and portfolio-level statistics. Built around live options data via ThetaData, proxied through api.potatohedge.com.

Yahoo has been purged from this suite — ThetaData is the sole market-data source. The one remaining outside dependency is stockanalysis.com for ETF constituent weights, which ThetaData doesn't carry; if it's unreachable, callers get an empty basket rather than a silent fallback to a different data source.

## Modules

### 1. Core Variance Swap Calculator (`variance_swap_live.py`)
Pricing engine for fair variance swap strikes using model-free replication (finite-strike approximation of the Carr-Madan log-contract).

**Inputs:** Ticker, target time-to-expiry (years)
**Outputs:**
- Fair variance swap strike (annualized vol %)
- ATM implied volatility
- Convexity premium (fair vol - ATM IV)
- Realized volatility (30/60/90/matched-day lookbacks)
- VRP (fair vol - realized vol)
- Vega notional conversion
- Full replication table (strike, ΔK, weight, OTM price, contribution)
- IV smile scatter (put IV vs call IV)
- Plots: OTM prices, contribution bars, volatility smile, realized vs fair bar chart

**Data Sources:** ThetaData (live), yfinance (historical)

### 2. Cross-Sectional Screener (`variance_swap_screener.py`)
Multi-ticker ranking system for short-vol attractiveness.

**Inputs:** List of tickers (comma-separated)
**Outputs:**
- Composite score (0–100) per ticker
- Signal: STRONG SELL / MODERATE SELL / NEUTRAL / MODERATE BUY / STRONG BUY
- Fair vol, ATM IV, convexity, VRP, skew bias, tail mass per ticker
- Ranked table with top short-vol and long-vol candidates
- CSV export (strike table + summary)

**Scoring Factors:**
- VRP magnitude (30%)
- Low convexity (20%)
- Skew balance (15%)
- Low tail mass (15%)
- Data quality (10%)
- Multi-lookback consistency (10%)

### 3. Correlation & Covariance Engine (`correlation_engine.py`)
Basket-level statistics engine for portfolio construction, dispersion trading, and pair trading.

**Modes:**
1. Auto-populate diversified basket (greedy minimum-correlation selection)
2. Enter custom tickers
3. Find correlated clusters from universe

**Outputs:**
- Correlation matrix (N×N)
- Covariance matrix (annualized)
- Basket vol, beta, expected return, Sharpe, alpha (Jensen's)
- Individual ticker stats (vol, return, beta vs market)
- Pairwise correlations with p-values and betas
- Dispersion score (avg pairwise correlation)
- Diversification ratio (weighted avg vol / basket vol)
- Trading implications: dispersion, pair trading, portfolio insurance
- CSV export (correlation matrix, pairs, summary)

### 4. Dealer Positioning (`dealer_positioning.py`)
Estimates the sign and size of the dealer community's aggregate options book —
the thing that determines whether dealer hedging *dampens* or *amplifies* moves.

**Outputs:** net dealer gamma, gamma-by-strike profile, gamma flip level,
hedging-requirement heatmap, and a 4-panel Gamma/Delta/Vanna/Charm exposure chart.

**Three sign conventions** (`sign_model=`), in increasing order of sophistication:

| Model | What it assumes |
|---|---|
| `oi_heuristic` (v1) | Flat call = +1 / put = −1 across the whole chain. The standard public-GEX convention. Every strike counts, moneyness ignored. |
| `replication` (v2 Layer 1b) | Dealers are short whatever the Demeterfi replication strip is long, and that strip only holds OTM legs — so ITM strikes contribute 0, not a smaller weight. Structurally can never produce a dealer-long strike. |
| `vol_surface_replication` (v2, Layers 1a + 1b) | Same OTM gate, but the *sign per strike* is read off the vol surface: a strike whose IV trades **rich** vs a smooth near-ATM reference implies net customer buying (dealer short); one trading **cheap** implies overwriting supply (dealer long). This is what lets a strike come out dealer-long. |

Supporting modules: `replication_reference.py` (Layer 1b, replication weights +
seed-and-accumulate position history), `vol_surface_reference.py` (Layer 1a,
quadratic/SABR reference-smile fit and per-strike deviation),
`expiry_selector.py` (shared expiry resolution).

Full derivation and the staged validation plan: `DEALER_POSITIONING_V2_DESIGN.md`.

### 5. Options Strategy Recommendations (`strategy_recommender.py`)

Automatically recommends multi-leg options strategies based on detected volatility edges and Greeks positioning from the chain scanner.

**How It Works:**
1. **Edge Detection**: Smile-fit analysis identifies strikes where implied vol deviates from the fitted curve
2. **Strategy Selection**: Based on vol regime (RICH/CHEAP/FAIR), appropriate strategies are recommended
3. **Strike Sizing**: Strategies use identified edges as core positions with protection/offsets
4. **Ranking**: Recommendations ranked by Greeks alignment (theta for RICH, gamma/vega for CHEAP)
5. **Export**: Strategies exported as `chain_strategies.json` and included in `suite_context.json`

**Recommended Strategies by Vol Regime:**

**RICH Regime (High IV)** — Strategies to collect theta:
- **Call spreads**: Sell high-IV calls, buy upside protection → collect theta
- **Put spreads**: Sell high-IV puts, buy downside protection → collect theta
- **Iron condor**: Sell premium on both sides → maximize theta collection
- **Collars**: Sell calls, buy puts → protected upside with premium offset

**CHEAP Regime (Low IV)** — Strategies for vol expansion:
- **Straddles**: Long ATM call + put → long gamma and vega for IV expansion
- **Strangles**: Long OTM call + put → cheaper exposure to wider moves
- **Long call spreads**: Buy call, sell higher call → directional bet with defined risk
- **Long put spreads**: Buy put, sell lower put → directional bet with defined risk

**FAIR Regime (Balanced IV)** — Balanced exposure:
- **Straddles**: ATM gamma play → benefit from realized volatility
- **Strangles**: OTM gamma play → cheaper exposure with directional flavor
- **Spreads**: Neutral positioning → hedge with theta collection

**Output Format:**
- **chain_strategies.json**: Structured recommendation artifact with strategy type, legs, Greeks summary, and rank score
- **suite_context.json**: Includes strategies field for seamless Options_Suite integration

**Integration with Options_Suite:**
Strategies are automatically included in suite_context.json for import to Options_Suite for pricing, Greeks monitoring, and portfolio comparison.

For detailed format specifications and data conventions, see: `docs/STRATEGY_RECOMMENDATIONS.md`

### 6. Stage 3 Behavioral Backtest (`backtest_stage3.py`)
The only module that tests whether the *economic claim* holds, rather than
whether the machinery runs. For each historical day it classifies the book as
long- or short-gamma under both v1 and v2, then measures forward realized vol,
and reports a Welch t-test of short-gamma vs long-gamma days.

The hypothesis is that short-gamma days should show **higher** forward realized
vol. The point of running both conventions side by side is that v2 producing a
larger, more significant gap than v1 is the evidence that its richer sign logic
reads something real — a prettier chart alone proves nothing.

```bash
python backtest_stage3.py SPY 90     # ticker, lookback in trading days
```

Uncached by design — it pulls history straight from ThetaData on every run.
A cache lived here briefly and was removed: its core rule ("a date already
checked is never re-checked") silently converts one transient failure into a
permanent hole, and this proxy fails transiently often enough that it did.

**Where IV and gamma come from (changed 2026-07-24).** Only one per-contract
route on this proxy honors a date range, and it isn't the greeks one:

| Route | Range? | Carries | Requests per expiry, 110 days |
|---|---|---|---|
| `hist/option/all_greeks` | ✗ one day, ignores `end_date` | IV + greeks | ~47,000 |
| `hist/option/eod` | ✓ | prices only | ~430 |
| `bulk_hist/option/open_interest` | ✗ one day, but whole chain | OI | ~110 |

So the backtest buys prices and reconstructs the rest: `implied_vol.py`
inverts IV from the bid/ask mid, and `dealer_positioning.bs_gamma` turns that
into gamma. ~550 requests instead of ~47,000, with full strike coverage kept.
Vendor greeks are still used verbatim on any row that carries them, so this
degrades gracefully if the greeks route is ever fixed.

The inversion defaults to European Black-Scholes. SPY options are American, so
`implied_vol.american_pricer()` returns a Leisen-Reimer pricer
(`Options_Suite/american_binomial.py`) that drops into the same solver:

```python
from implied_vol import implied_vol, american_pricer
iv = implied_vol(mark, S, K, T, r, q, right, pricer=american_pricer())
```

BS is the default because a full backfill needs ~47,000 inversions and LR is
two to three orders of magnitude slower per solve. The intended use of LR is
to **measure** the early-exercise bias on a sample of days — and switch the
sweep only if it turns out to matter for the gamma sign. Every run prints its
own provenance (`N derived from price, N vendor, N unrecoverable`), and
unrecoverable strikes are dropped rather than imputed.

### 7. Data Sources

**ThetaData Controller** (via api.potatohedge.com):
- Live option chain (all strikes, all greeks)
- Bulk snapshot with IV, bid, ask, last, OI
- Stock snapshot quote (mid/bid/ask/last)
- Expiration and strike listings

**Yahoo Finance** (fallback):
- Historical prices for realized vol computation
- Spot price

## Build Order (Completed)

| Phase | Module | Status |
|-------|--------|--------|
| 1 | Core variance swap calculator (yfinance) | ✅ Complete |
| 2 | ThetaData integration | ✅ Complete |
| 3 | Realized vol + VRP + ATM IV comparison | ✅ Complete |
| 4 | Plots + CSV export | ✅ Complete |
| 5 | Multi-ticker screener | ✅ Complete |
| 6 | Correlation & covariance engine | ✅ Complete |
| 7 | Hedging heatmap & dealer positioning (v1) | ✅ Complete |
| 8 | Term structure & GARCH | ✅ Complete |
| 9 | Dealer positioning v2 (Layers 1a + 1b) | ✅ Complete |
| 10 | Stage 3 realized-vol behavioral backtest | ✅ Runs end to end |

## Build Order (Next)

| Phase | Module | Priority |
|-------|--------|----------|
| 11 | ~~Rewrite `variance_swap_screener.py`~~ — **DONE 2026-08-12** (data-quality fix + tests; see below) | **DONE** |
| 12 | Multi-expiry aggregate in the Stage 3 backtest (currently single near-dated expiry) | After |
| 13 | Arbitrage detection (butterfly/calendar) | After |
| 14 | Merge all + PDF export | Final |

### Phase 11 — `variance_swap_screener.py` data-quality hardening (DONE 2026-08-12)

Flagged 2026-07-24. The two original failure modes are **fixed in the working tree** (verified 2026-08-12
against `Vol_Suite/variance_swap_screener.py` + `Vol_Suite/tests/test_variance_swap_screener.py`, 6 passed):

**1. Missing realized vol is now NaN, not `0.0` (was: max VRP → false STRONG SELL).**
`screen_ticker` sets `vrp_pct = float('nan')` when RV history is missing, `data_quality =
"insufficient_price_history"`, and forces `signal = "INSUFFICIENT DATA"` regardless of the numeric score.
Flagged tickers are excluded from the top long/short candidate lists in both `main()` and
`run_variance_screener()`. A missing input can no longer produce a confident BUY/SELL read.

**2. Blanket `except` no longer drops tickers silently.** Failures are collected into a `skipped` list
in `run_variance_screener()` and reported as `[SKIPPED] N/M requested tickers returned no result (see
[SKIP] lines above for reasons)`; per-ticker `[SKIP] {ticker}: {reason}` lines identify the cause.

**Remaining follow-ups (not regressions):** scoring weights (30/20/15/15/10 + normalizers) are still
undocumented magic numbers; consider making them configurable + sensitivity-tested.

**How it was verified (2026-08-12):** `screen_ticker` tracks `insufficient_data` (NaN realized-vol match
or NaN ATM IV), uses a `vrp_for_score`/`convexity_for_score` stand-in of `0.0` ONLY inside the score math
(so ranking order is unchanged) while the outward `vrp_pct`/`convexity_pct` stay `NaN`, and forces
`signal = "INSUFFICIENT DATA"` when `insufficient_data`. `data_quality` is set to
`"insufficient_price_history"` so callers can filter. `run_variance_screener` accumulates `skipped` for
tickers that raised and reports the count + reasons; the ranked candidate lists (top short/long, chart,
interpretation text) draw only from `data_quality == "ok"` rows. `Vol_Suite/tests/test_variance_swap_screener.py`
(6 tests) covers the pure scoring/classification logic. Run: `.venv/Scripts/python.exe -m pytest
Vol_Suite/tests/test_variance_swap_screener.py -q` (clear `PYTHONPATH`/`PYTHONHOME` first to avoid the
Hermes-venv PIL `_imaging` leak).

### Open question on Stage 3

The pipeline is correct, but no run has yet produced a statistically meaningful
result — the first clean run yielded 2 usable days, which is a sample size, not
an answer. `_build_day_records` now prints per-input date coverage on every run
specifically so a thin result names its own cause (missing OI? missing closes?)
rather than arriving as an unexplained small number.

## Key Formulas

**Fair Variance (annualized):**
```
sigma^2_fair = (2 * e^{rT} / T) * sum(ΔK_i / K_i^2 * OTM_i)
```
where `OTM_i = put(K_i)` if K_i ≤ F, else `call(K_i)`.

**Realized Vol (annualized):**
```
RV = std(log_returns) * sqrt(252)
```

**VRP:**
```
VRP = FairVol - RealizedVol (positive = variance expensive, good to sell)
```

**Basket Vol:**
```
sigma_basket = sqrt(w' * Cov * w)
```

**Diversification Ratio:**
```
DR = (sum(w_i * sigma_i)) / sigma_basket
```

## Requirements

Python 3.10+.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt    # runtime deps + pytest
```

Runtime only (no test tooling): `pip install -r requirements.txt`.

Then create `.env` from `.env.example` and fill in the two Cloudflare Access
credentials — nothing that touches the network will run without them.

## Usage
```python
# Single ticker analysis
python variance_swap_live.py

# Cross-sectional screener (multi-ticker)
python variance_swap_screener.py

# Correlation / basket engine
python correlation_engine.py

# Dealer positioning (v1 default; pass a sign_model for v2)
python dealer_positioning.py

# Stage 3 behavioral backtest
python backtest_stage3.py SPY 90
```

### Tests

```bash
pytest tests/ -q
```

All tests are network-free — they run against synthetic chains with a known
embedded answer, so a failure means the math changed, not that the market did.

### Environment notes

- Credentials come from `.env` (see `.env.example`); never hardcoded.
- `THETADATA_HIST_CONCURRENCY` (default 8) controls how many contracts' history
  are pulled in parallel. **Turn this down to 1 if you're on a VPN** — a VPN
  tunnel buckling under parallel connections produces a failure that looks
  exactly like proxy rate limiting (everything 502s, retries don't help), and
  cost a full debugging session to correctly attribute once already.
- `diagnostics/` holds the one-off scripts that established how the proxy
  actually behaves; see its README before assuming an endpoint is broken.

## Volatility Suite (Integrated Front-End)

A single CLI entrypoint to run any combination of modules, collect outputs, and produce a polished PDF report.

Usage:

```bash
python volatility_suite.py
```

At startup, choose either:
- Standard focus workflow (existing behavior).
- Unified cross-suite run (new): writes `suite_context.json` into the timestamped output folder and can optionally launch sibling suites in context mode.

Follow the interactive prompts to select modules (any combination or "all"), enter shared inputs (ticker, start/end, target time-to-expiry), and choose whether to compile a single PDF report.

Features:
- Centralized, timestamped output folder: outputs/<YYYYMMDD_HHMMSS>/
- Modules save CSVs and PNGs into the output folder; images include short interpretation text rendered on the image for direct inclusion in the report.
- The final PDF is composed using ReportLab and includes a title page, per-module headings, multi-line interpretation text, and module images scaled to the page.

The PDF features need `reportlab` and `pillow` — both already in
`requirements.txt`, so nothing extra to install.

Notes:
- The suite sets an environment variable VS_OUTPUT_DIR to the timestamped output folder so each module writes its files there.
- Screener output includes a CSV, a rendered PNG table, and a ranking chart. Correlation engine produces a heatmap PNG and a short basket summary PNG. Dealer positioning overlays an interpretation box on the heatmap.
- If you prefer a different PDF layout (TOC, fonts, branded header/footer), open an issue or request enhancements — the ReportLab composer is in vs_utils.compose_pdf_report and can be customized.


## References
- Carr & Madan (1998) — Towards a Theory of Volatility Trading
- JPMorgan — Variance Swaps (2006)
- Demeterfi, Derman, Kamal & Zou — More Than You Ever Wanted to Know About Volatility Swaps
- FlashAlpha-lab/volatility-surface-python — Production-grade extensions

## Risk Warning
This software is for research and educational purposes. Variance swap replication involves assumptions about continuous strike availability, liquidity, and market completeness. Always validate results against live market quotes before making trading decisions.
