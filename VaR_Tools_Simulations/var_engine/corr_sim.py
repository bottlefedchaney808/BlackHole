"""corr_sim.py — Module 1: Correlated GBM Simulation
Replicates VaRtools sheet '1 Correlated Simulation'.

Generates N correlated price paths via Cholesky decomposition of the
correlation matrix, GBM increments, then computes portfolio P&L distribution
and VaR/CVaR.  Pure numpy — no external data needed unless you pass live prices.
"""
import numpy as np
from typing import List, Optional
from dataclasses import dataclass, field


@dataclass
class CorrSimInputs:
    current_prices: np.ndarray          # shape (n_assets,)
    n_shares:       np.ndarray          # shape (n_assets,)
    volatilities:   np.ndarray          # annualised, shape (n_assets,)
    corr_matrix:    np.ndarray          # (n_assets, n_assets)
    var_days:       float  = 1.0        # VaR horizon in trading days
    trading_days:   float  = 252.0
    confidence:     float  = 0.99       # e.g. 0.99 → 1% VaR
    n_sims:         int    = 10_000
    seed:           Optional[int] = 42
    asset_names:    List[str] = field(default_factory=list)


@dataclass
class CorrSimResults:
    var:              float
    cvar:             float
    portfolio_value:  float
    sim_vols:         np.ndarray    # realised vols from sim
    sim_corr:         np.ndarray    # realised corr from sim
    pnl_distribution: np.ndarray   # full P&L vector
    cholesky_ok:      bool
    asset_names:      List[str]


def _is_positive_definite(m: np.ndarray) -> bool:
    try:
        np.linalg.cholesky(m)
        return True
    except np.linalg.LinAlgError:
        return False


def run(inp: CorrSimInputs) -> CorrSimResults:
    rng = np.random.default_rng(inp.seed)
    n   = len(inp.current_prices)
    dt  = inp.var_days / inp.trading_days

    # position values
    pos_vals = inp.current_prices * inp.n_shares          # (n,)
    port_val = float(pos_vals.sum())

    names = inp.asset_names if inp.asset_names else [f"Asset{i+1}" for i in range(n)]

    # Cholesky on covariance
    vol_diag = np.diag(inp.volatilities)
    cov      = vol_diag @ inp.corr_matrix @ vol_diag
    chol_ok  = _is_positive_definite(cov)
    if not chol_ok:
        # nearest positive-definite via eigenvalue clipping
        eigvals, eigvecs = np.linalg.eigh(cov)
        eigvals = np.maximum(eigvals, 1e-10)
        cov = eigvecs @ np.diag(eigvals) @ eigvecs.T

    L = np.linalg.cholesky(cov)  # (n, n)

    # simulate correlated log-returns
    Z        = rng.standard_normal((inp.n_sims, n))   # iid normals
    lr       = Z @ L.T * np.sqrt(dt)                  # correlated, scaled

    # simulated prices
    sim_px   = inp.current_prices * np.exp(lr)        # (n_sims, n)

    # P&L of portfolio (using position weights in shares)
    pnl      = ((sim_px - inp.current_prices) * inp.n_shares).sum(axis=1)

    # VaR / CVaR
    cut      = np.quantile(pnl, 1.0 - inp.confidence)
    var      = float(-cut)
    cvar     = float(-pnl[pnl <= cut].mean())

    # realised stats from simulation
    log_sim  = np.log(sim_px / inp.current_prices)
    sim_vols = log_sim.std(axis=0) / np.sqrt(dt)      # annualised
    sim_corr = np.corrcoef(log_sim.T)

    return CorrSimResults(
        var=var, cvar=cvar,
        portfolio_value=port_val,
        sim_vols=sim_vols,
        sim_corr=sim_corr,
        pnl_distribution=pnl,
        cholesky_ok=chol_ok,
        asset_names=names,
    )


def demo():
    inp = CorrSimInputs(
        current_prices = np.array([10.0, 11.0, 9.0]),
        n_shares       = np.array([50.0, 20.0, 60.0]),
        volatilities   = np.array([0.25, 0.30, 0.35]),
        corr_matrix    = np.array([[1.0, 0.3, 0.4],
                                   [0.3, 1.0,-0.2],
                                   [0.4,-0.2, 1.0]]),
        var_days=1, n_sims=10_000, confidence=0.99,
        asset_names=["Asset1","Asset2","Asset3"],
    )
    r = run(inp)
    print(f"Portfolio Value : ${r.portfolio_value:,.2f}")
    print(f"1-day 99% VaR   : ${r.var:,.2f}")
    print(f"1-day 99% CVaR  : ${r.cvar:,.2f}")
    print(f"Cholesky OK     : {r.cholesky_ok}")
    print(f"Sim vols        : {np.round(r.sim_vols,4)}")
    print(f"Sim corr:\n{np.round(r.sim_corr,3)}")


if __name__ == "__main__":
    demo()
