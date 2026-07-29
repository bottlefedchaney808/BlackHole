# VaR Tools — Python Engine

Reimplementation of VaRtools v2.6d in Python.  
ThetaData only. Cache-first. No yfinance.

---

## 🔜 Volatility Suite Integration (coming)

The Volatility Suite (`correlation_engine.py`) already computes the basket —
tickers, position weights, correlation matrix, EWMA vols, and live spot prices.

**Planned wiring:**
- `correlation_engine.py` exports a `BasketSnapshot` (tickers, weights, corr, vols)
- `var_engine/basket_loader.py` reads it and pre-populates all module inputs
- Running `python main.py` with a loaded basket skips all manual entry

**Integration points per module:**

| Module | What gets auto-filled |
|--------|----------------------|
| `corr_sim` | tickers, vols, corr matrix, position values |
| `mc_sim` | market IDs, spots, vols, corr |
| `hist_sim` | tickers, position values, date range |
| `copulas` | tickers, position values, vols, corr, calibrated marginal dfs |
| `forex_var` | assets, vols, corr, home-currency positions, FX mask |
| `stress_test` | base vols, base corr, positions |
| `var_agg` | tickers, positions, group mask (equity/FX), date range |
| `price_dist` | spot, vol, mu — from basket screener output |

Until then, all modules work standalone — see examples below.

---

## Setup (one time)

```bash
cd C:\Users\bottl\FinancialDevelopment\VaR_Tools_Simulations
.venv\Scripts\activate
```

That's it. The `.venv` is already built and all deps are installed.

> **Important:** Always run Python from the activated venv, or prefix with  
> `PYTHONPATH="" PYTHONHOME="" .venv\Scripts\python.exe`  
> if running from a terminal that has Hermes' venv loaded.

---

## Run everything (demos, no network needed)

```bash
python main.py --demo all
```

Run one module:

```bash
python main.py --demo price_dist   # probability calculator
python main.py --demo corr         # correlated simulation
python main.py --demo mc           # monte carlo
python main.py --demo hist         # historical simulation
python main.py --demo copula       # copulas
python main.py --demo forex        # forex VaR
python main.py --demo cashflow     # cash flow mapping
python main.py --demo stress       # stress testing
python main.py --demo agg          # VaR aggregation
```

Interactive menu:

```bash
python main.py
```

---

## Use in your own code

Every module is self-contained. Import, fill the input dataclass, call `run()`.

### Probability Calculator — "what are the odds?"

```python
from var_engine.price_dist import (
    prob_at_expiry, prob_touch_any_time,
    spot_at_probability, mc_probabilities, MCProbInputs,
    lognormal_dist, price_distribution
)

# P(stock closes above 150 at expiry)
pa, pb = prob_at_expiry(S=130, target=150, days=360, vol=0.35, mu=0.08)
print(f"Above 150 at expiry: {pa:.1%}")

# P(stock touches 150 at ANY point in 360 days)
pt = prob_touch_any_time(S=130, barrier=150, days=360, vol=0.35, mu=0.08)
print(f"Touch 150 any time:  {pt:.1%}")

# What price has a 25% chance of being touched in 360 days?
h = spot_at_probability(S=130, prob=0.25, days=360, vol=0.35, mu=0.08, prob_type='A')
print(f"25% touch target:    ${h:.2f}")

# Full MC probability engine — above/below/touching upper and lower targets
r = mc_probabilities(MCProbInputs(
    spot=130, upper=160, lower=100,
    days=360, vol=0.35, mu=0.08,
    n_sims=50_000
))
print(f"Above 160 at expiry:   {r.above_upper_at_expiry:.1%}")
print(f"Above 160 any time:    {r.above_upper_any_time:.1%}")
print(f"Below 100 any time:    {r.below_lower_any_time:.1%}")
print(f"Touching either:       {r.touching_either:.1%}")
print(f"Touching both:         {r.touching_both:.1%}")
print(f"Touching neither:      {r.touching_neither:.1%}")
print(f"Avg end price:         ${r.avg_end_price:.2f}")

# Lognormal price distribution table
tbl = lognormal_dist(S=130, days=360, vol=0.35, mu=0.08, n_points=30)
for row in tbl:
    print(f"  ${row.price:7.2f}  P(below)={row.prob_below:.3f}  P(above)={row.prob_above:.3f}")

# Distribution stats on a real price series
import numpy as np
prices = np.array([...])   # your closing price array
d = price_distribution(prices)
print(f"Annual vol:      {d.annual_vol:.2%}")
print(f"Skewness:        {d.skewness:.4f}  (normal: {d.skew_normal})")
print(f"Excess kurtosis: {d.excess_kurtosis:.4f}  (normal: {d.kurt_normal})")
```

### Using the interactive CLI (recommended)

Run without arguments for the full interactive menu. Module 9 auto-populates:

- **Volatility** → GARCH(1,1) estimate from historical prices (scipy-based, no `arch` dependency)
- **Expected return** → Geometric mean (CAGR) from price history
- **Horizon** → Fixed at 360 calendar days
- **Distribution stats** → Live fetch from ThetaData, cached on disk

---

### Correlated Simulation — Module 1

```python
import numpy as np
from var_engine.corr_sim import run, CorrSimInputs

r = run(CorrSimInputs(
    current_prices = np.array([150.0, 3200.0, 55.0]),
    n_shares       = np.array([100,   10,     500]),
    volatilities   = np.array([0.30,  0.35,   0.25]),   # annualised
    corr_matrix    = np.array([[1.0,  0.1,  0.4],
                                [0.1,  1.0, -0.1],
                                [0.4, -0.1,  1.0]]),
    var_days=1, confidence=0.99, n_sims=10_000,
))
print(f"1-day 99% VaR:  ${r.var:,.0f}")
print(f"1-day 99% CVaR: ${r.cvar:,.0f}")
```

---

### Monte Carlo — partial vs full revaluation — Module 2

```python
from var_engine.mc_sim import run, MCSimInputs, Position

positions = [
    Position(pos_type=1, market_id="SPY",  quantity=50.0),           # stock
    Position(pos_type=2, market_id="AAPL", quantity=100.0,            # option
             strike=175.0, days_cal=45, is_call=True),
    Position(pos_type=3, market_id="TLT",                             # bond
             face=100_000, coupon=0.04, freq=2, maturity_years=7.0),
]
r = run(MCSimInputs(
    market_ids   = ["SPY", "AAPL", "TLT"],
    spot_prices  = np.array([450.0, 180.0, 0.045]),  # bond uses yield
    volatilities = np.array([0.18,  0.30,  0.20]),
    corr_matrix  = np.eye(3),
    risk_free=0.05, var_days=5, confidence=0.99, n_sims=10_000,
    positions=positions,
))
print(f"Partial reval VaR: ${r.var_partial:,.0f}")
print(f"Full    reval VaR: ${r.var_full:,.0f}")
```

---

### Historical Simulation — Module 3  
*(fetches from ThetaData, caches to `.cache/`)*

```python
from var_engine.hist_sim import run, HistSimInputs

r = run(HistSimInputs(
    tickers       = ["AAPL", "GME", "AMZN", "AMD"],
    position_vals = np.array([120_000, 250_000, 125_000, 170_000]),
    var_days      = 1,
    confidence    = 0.95,
    method        = "fhs",          # "basic", "hw", or "fhs" (GARCH)
    start_date    = "20230101",
    end_date      = "20241231",
))
print(f"FHS VaR:  ${r.var:,.0f}")
print(f"FHS CVaR: ${r.cvar:,.0f}")
```

---

### Copula VaR — Module 5

```python
from var_engine.copulas import run, CopulaInputs

r = run(CopulaInputs(
    tickers       = ["AAPL", "MSFT", "AMZN", "GOOG"],
    position_vals = np.array([120_000, 170_000, 125_000, 220_000]),
    volatilities  = np.array([0.30, 0.25, 0.33, 0.28]),
    corr_matrix   = corr,           # (4,4) correlation matrix
    copula_type   = "student_t",    # "gaussian", "student_t", "clayton"
    student_df    = 5.0,            # joint tail thickness
    marginal_dfs  = np.array([3.26, 0.0, 3.08, 0.0]),  # 0 = normal marginal
    var_days=10, n_sims=50_000, confidence=0.99,
))
print(f"Student-T copula VaR:  ${r.var:,.0f}")
print(f"Student-T copula CVaR: ${r.cvar:,.0f}")
```

---

### VaR Aggregation + EWMA + PCA — Module 9

```python
from var_engine.var_agg import run, VaRAggInputs

r = run(VaRAggInputs(
    asset_names = ["SPY","QQQ","IWM","GLD","TLT","UUP"],
    positions   = np.array([2e6, 1.5e6, 1e6, 5e5, 8e5, 3e5]),
    group_mask  = np.array([0,   0,     0,   0,   1,   1]),  # 0=equity, 1=other
    tickers     = ["SPY","QQQ","IWM","GLD","TLT","UUP"],
    start_date  = "20230101",
    end_date    = "20241231",
    ewma_lambda = 0.94,
    var_days=5, confidence=0.95, n_pca_components=2,
))
print(f"Total VaR (full):        ${r.total_var:,.0f}")
print(f"Aggregated sub-port VaR: ${r.aggregated_var:,.0f}")
print(f"PCA VaR (2 components):  ${r.pca_var_without_resid:,.0f}")
print(f"PCA VaR (+ residuals):   ${r.pca_var_with_resid:,.0f}")
for name, v in r.subport_var.items():
    print(f"  {name}: ${v:,.0f}")
```

---

### Stress Testing — Module 8

```python
from var_engine.stress_test import run, StressTestInputs

r = run(StressTestInputs(
    asset_names = ["SPY","TLT","GLD","USD/EUR"],
    positions   = np.array([2e6, 5e5, 3e5, 4e5]),
    base_vols   = np.array([0.18, 0.12, 0.15, 0.09]),
    base_corr   = corr,
    # shock vols up 8% on equity, -3% on gold
    vol_shocks  = np.array([0.08, 0.0, -0.03, 0.0]),
    # shock the SPY/TLT correlation up by 0.3 (flight to safety reversal)
    corr_shocks = corr_shocks,
    var_days=5, confidence=0.99,
))
print(f"Base   VaR: ${r.base_var:,.0f}   CVaR: ${r.base_cvar:,.0f}")
print(f"Stress VaR: ${r.stress_var:,.0f}   CVaR: ${r.stress_cvar:,.0f}")
print(f"Increase:   {(r.stress_var/r.base_var - 1):.1%}")
```

---

## Cache

All ThetaData calls are cached in `.cache/` as hash-keyed JSON.  
Delete `.cache/` to force a fresh fetch. Spot price caches in 10-min buckets.  
Historical price series cache permanently until you delete them.

---

## File layout

```
.venv/                  Python 3.10 environment
.cache/                 ThetaData response cache (auto-created)
.env                    ThetaData credentials (from Variance_Swap_Module)
main.py                 CLI entry point
requirements.txt
var_engine/
    data_loader.py      ThetaData client wrapper + cache logic
    corr_sim.py         Module 1 — correlated GBM
    mc_sim.py           Module 2 — MC partial vs full reval
    hist_sim.py         Module 3/4 — basic / Hull-White / FHS
    copulas.py          Module 5 — Gaussian / Student-T / Clayton
    forex_var.py        Module 6 — multi-currency VaR
    cashflow_map.py     Module 7 — fixed income CF mapping
    stress_test.py      Module 8 — vol + corr stress scenarios
    var_agg.py          Module 9 — EWMA covariance + PCA
    price_dist.py       Price distribution + full probability calculator
```
