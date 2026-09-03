"""Euler + standalone component VaR for VaR_Tools_Simulations context-mode run.

Matches corr_sim engine mechanics exactly (identity corr, 0.25 flat vol,
n_sims=10_000, seed=42, var_days=1 / trading_days=252). Spot cancels out of
VaR in this GBM setup (n_shares = notional/price), so the live/fallback spot
choice does not change the numbers.
"""
import json
import numpy as np

CTX = r"C:/Users/bottl/FinancialDevelopment/Vol_Suite/outputs/20260731_151642/suite_context.json"
with open(CTX, "r", encoding="utf-8-sig") as f:
    payload = json.load(f)

basket = payload["basket"]["tickers"]
weights = np.array(payload["basket"]["weights"], dtype=float)
norm_w = weights / weights.sum()
notionals = norm_w * 1_000_000.0
n = len(basket)
prices = np.full(n, 100.0)
vols = np.full(n, 0.25)
n_shares = notionals / prices

var_cfg = payload.get("var", {})
confidence = float(var_cfg.get("confidence", 0.99))
horizon_days = int(var_cfg.get("horizon_days", 252))
n_sims = int(var_cfg.get("n_sims", 10_000))
seed = int(var_cfg.get("seed", 42))
dt = horizon_days / 252.0

rng = np.random.default_rng(seed)
cov = np.diag(vols) @ np.eye(n) @ np.diag(vols)
L = np.linalg.cholesky(cov)
Z = rng.standard_normal((n_sims, n))
lr = Z @ L.T * np.sqrt(dt)
sim_px = prices * np.exp(lr)
pnl_i = (sim_px - prices) * n_shares
pnl = pnl_i.sum(axis=1)
cut = np.quantile(pnl, 1.0 - confidence)
var_total = float(-cut)
cvar_total = float(-pnl[pnl <= cut].mean())

# Euler component VaR from simulated covariance (sums to total VaR)
pos_vals = notionals
sim_cov = np.cov(lr.T)
w = pos_vals / pos_vals.sum()
port_var = float(w @ sim_cov @ w)
comp = var_total * w * (sim_cov @ w) / port_var

# standalone VaR per name (engine mechanics on the single asset)
standalone = [float(-np.quantile(pnl_i[:, i], 1.0 - confidence)) for i in range(n)]

rows = sorted(zip(basket, notionals, norm_w, comp, standalone), key=lambda r: -r[3])
check = 0.10 * var_total
print(f"Context run {payload['run_id']} | focus={payload['focus']['ticker']} | "
      f"conf={confidence} | horizon={horizon_days}d | n_sims={n_sims} seed={seed}")
print(f"Portfolio value: ${pos_vals.sum():,.0f} | Total VaR: ${var_total:,.2f} | "
      f"CVaR: ${cvar_total:,.2f}")
print(f"10%-of-total-VaR threshold: ${check:,.2f}")
print(f"Sum of Euler components vs total VaR: "
      f"${sum(c for *_, c, _ in rows):,.2f} vs ${var_total:,.2f}")
print()
print(f"{'Ticker':6s} {'Notional':>10s} {'Wt%':>6s} {'EulerCompVar':>13s} {'%ofTot':>7s} "
      f"{'Standalone':>12s} {'>10%?':>6s}")
for tk, notl, w_, cv, sa in rows:
    pct = 100.0 * cv / var_total
    flag = "YES" if pct > 10.0 else ""
    print(f"{tk:6s} {notl:10,.0f} {100*w_:6.2f} {cv:13,.2f} {pct:7.2f} {sa:12,.2f} {flag:>6s}")
print(f"\nPositions exceeding 10% of total VaR (Euler): {sum(1 for r in rows if r[3] > check)}")
print(f"Largest standalone VaR: {max(standalone):,.2f} "
      f"({basket[int(np.argmax(standalone))]}) "
      f"= {100*max(standalone)/var_total:.2f}% of total VaR")
