"""Replicate corr_sim context-mode inputs from the latest suite_context.json
and compute per-asset component VaR (conditional tail contribution) + standalone
VaR, so we can report largest components and the 10%-of-total check."""
import json, os
import numpy as np

CTX = r"C:/Users/bottl/FinancialDevelopment/orchestrator_output/20260828T142311Z288545/suite_context.json"
with open(CTX, "r", encoding="utf-8-sig") as f:
    payload = json.load(f)

basket = payload["basket"]["tickers"]
weights = np.array(payload["basket"]["weights"], dtype=float)
norm_w = weights / weights.sum()
total_notional = 1_000_000.0
notionals = norm_w * total_notional

n = len(basket)
prices = np.full(n, 100.0)
vols = np.full(n, 0.25)
corr = np.eye(n)
n_shares = notionals / prices

var_cfg = payload.get("var", {})
confidence = float(var_cfg.get("confidence", 0.99))
horizon_days = int(var_cfg.get("horizon_days", 252))
n_sims = int(var_cfg.get("n_sims", 10_000))
seed = int(var_cfg.get("seed", 42))
trading_days = 252.0
dt = horizon_days / trading_days

rng = np.random.default_rng(seed)
L = np.linalg.cholesky(np.diag(vols) @ corr @ np.diag(vols))
Z = rng.standard_normal((n_sims, n))
lr = Z @ L.T * np.sqrt(dt)
sim_px = prices * np.exp(lr)
# per-asset P&L
pnl_i = (sim_px - prices) * n_shares            # (n_sims, n)
pnl = pnl_i.sum(axis=1)

cut = np.quantile(pnl, 1.0 - confidence)
var_total = float(-cut)
cvar_total = float(-pnl[pnl <= cut].mean())

# component VaR = conditional tail contribution per asset
mask = pnl <= cut
comp_var = -(pnl_i[mask].mean(axis=0))           # dollar contribution in tail

# standalone VaR per asset (single-name, same engine mechanics)
standalone = []
for i in range(n):
    c = np.quantile(pnl_i[:, i], 1.0 - confidence)
    standalone.append(float(-c))

rows = sorted(zip(basket, notionals, norm_w, comp_var, standalone),
              key=lambda r: -r[3])
print(f"Context: {payload['run_id']} | ticker={payload['focus']['ticker']} | "
      f"conf={confidence} | horizon={horizon_days}d | n_sims={n_sims} | seed={seed}")
print(f"Portfolio value: ${notionals.sum():,.0f}")
print(f"Total VaR (engine): ${var_total:,.2f}  CVaR: ${cvar_total:,.2f}")
print(f"10% of total VaR = ${0.10*var_total:,.2f}")
print()
print(f"{'Ticker':6s} {'Notional':>10s} {'Wt%':>6s} {'CompVaR($)':>12s} {'%ofTot':>7s} "
      f"{'Standalone':>12s} {'>10%?':>6s}")
for tk, notl, w, cv, sa in rows:
    pct = 100.0 * cv / var_total
    flag = "YES" if pct > 10.0 else ""
    print(f"{tk:6s} {notl:10,.0f} {100*w:6.2f} {cv:12,.2f} {pct:7.2f} {sa:12,.2f} {flag:>6s}")

n_over = sum(1 for r in rows if r[3] > 0.10 * var_total)
print(f"\nPositions exceeding 10% of total VaR: {n_over}")
print("(All params were context fallbacks: identity corr, flat 0.25 vol, $100 spot.)")
