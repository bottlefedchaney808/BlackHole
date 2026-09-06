"""Euler + standalone component VaR for a given suite context.

Replicates corr_sim engine mechanics exactly (seed=42, n_sims from ctx/default 10k,
var_days=horizon_days, trading_days=252, eigenvalue-clipped Cholesky), using the
SAME defaults main.py's _build_corr_sim_from_context applies when the context omits
vols/corr/positions: vols=0.25, corr=identity, notionals = norm(weights)*1,000,000.
Spot cancels out of GBM P&L (n_shares = notional/price), so fallback 100.0 is fine.

Usage: python var_components_context.py <path-to-suite_context.json>
"""
import json
import sys

import numpy as np

CTX = sys.argv[1] if len(sys.argv) > 1 else r"C:/Users/bottl/FinancialDevelopment/Vol_Suite/outputs/20260731_151642/suite_context.json"
with open(CTX, "r", encoding="utf-8-sig") as f:
    payload = json.load(f)

basket_obj = payload.get("basket") if isinstance(payload.get("basket"), dict) else None
basket = basket_obj["tickers"]
weights = np.array(basket_obj.get("weights") or ([1.0] * len(basket)), dtype=float)
norm_w = weights / weights.sum()
notionals = norm_w * 1_000_000.0
n = len(basket)
prices = np.full(n, 100.0)

var_cfg = payload.get("var", {})
confidence = float(var_cfg.get("confidence", 0.99))
horizon_days = int(var_cfg.get("horizon_days", 252))
n_sims = int(var_cfg.get("n_sims", 10_000))
seed = int(var_cfg.get("seed", 42))

# Same defaults as main.py._build_corr_sim_from_context
vols_raw = var_cfg.get("volatilities")
vols = np.array(vols_raw, dtype=float) if vols_raw is not None else np.full(n, 0.25)
corr_raw = basket_obj.get("correlation_matrix") or payload.get("correlation_matrix") or payload.get("corr_matrix")
if corr_raw is None:
    corr = np.eye(n)
else:
    corr = np.array(corr_raw, dtype=float)
if vols.shape[0] != n or corr.shape != (n, n):
    raise SystemExit(f"dim mismatch: n={n}, vols={vols.shape}, corr={corr.shape}")

n_shares = notionals / prices
dt = horizon_days / 252.0

rng = np.random.default_rng(seed)
# engine clips corr to [-0.999, 0.999] and fixes diagonal to 1.0
corr = np.clip(corr, -0.999, 0.999)
np.fill_diagonal(corr, 1.0)
cov = np.diag(vols) @ corr @ np.diag(vols)
try:
    L = np.linalg.cholesky(cov)
except np.linalg.LinAlgError:
    eigvals, eigvecs = np.linalg.eigh(cov)
    eigvals = np.maximum(eigvals, 1e-10)
    cov = eigvecs @ np.diag(eigvals) @ eigvecs.T
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
sim_cov = np.cov(lr.T)
w = notionals / notionals.sum()
port_var = float(w @ sim_cov @ w)
comp = var_total * w * (sim_cov @ w) / port_var

# standalone VaR per name (same engine mechanics on the single asset)
standalone = [float(-np.quantile(pnl_i[:, i], 1.0 - confidence)) for i in range(n)]

rows = sorted(zip(basket, notionals, norm_w, comp, standalone), key=lambda r: -r[3])
check = 0.10 * var_total
print(f"Context run {payload.get('run_id','?')} | focus={payload.get('focus',{}).get('ticker','?')} | "
      f"conf={confidence} | horizon={horizon_days}d | n_sims={n_sims} seed={seed}")
print(f"Portfolio value: ${notionals.sum():,.0f} | Total VaR: ${var_total:,.2f} | "
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
