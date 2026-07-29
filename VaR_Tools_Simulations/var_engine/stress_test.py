"""stress_test.py — Module 8: VaR Stress Testing
Replicates VaRtools sheet '8 Stress Testing'.

Takes historical vol/correlation estimates and applies user-defined
adjustments (e.g. +8% shock to S&P vol, +0.2 to a correlation pair),
validates the stressed correlation matrix is positive semi-definite,
then computes both base and stress VaR/CVaR.

Stress test framework:
  1. Base VaR from EWMA vols / historical corr
  2. Stressed VaR with overridden vols and correlations
"""
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from scipy.stats import norm


def _nearest_psd(m: np.ndarray) -> np.ndarray:
    """Project to nearest positive semi-definite matrix (Higham 2002 approx)."""
    eigvals, eigvecs = np.linalg.eigh(m)
    eigvals = np.maximum(eigvals, 0.0)
    out = eigvecs @ np.diag(eigvals) @ eigvecs.T
    # re-normalise diagonal to 1 (correlation matrix)
    d = np.sqrt(np.diag(out))
    d[d == 0] = 1.0
    return out / np.outer(d, d)


def _is_psd(m: np.ndarray, tol: float = 1e-8) -> bool:
    return bool(np.all(np.linalg.eigvalsh(m) >= -tol))


@dataclass
class StressTestInputs:
    asset_names:      List[str]
    positions:        np.ndarray          # dollar positions (n,)
    base_vols:        np.ndarray          # historical/EWMA annualised vols (n,)
    base_corr:        np.ndarray          # historical correlation matrix (n,n)
    # stress adjustments (additive)
    vol_shocks:       np.ndarray          # additive shock to vols (n,), 0=no change
    corr_shocks:      np.ndarray          # additive shock to off-diag corr (n,n)
    var_days:         float = 5.0
    trading_days:     float = 252.0
    confidence:       float = 0.99


@dataclass
class StressTestResults:
    base_var:         float
    base_cvar:        float
    stress_var:       float
    stress_cvar:      float
    stressed_vols:    np.ndarray
    stressed_corr:    np.ndarray
    corr_psd:         bool
    asset_names:      List[str]


def _parametric_var_cvar(positions, vols, corr, z, dt):
    """Analytical Gaussian VaR + CVaR."""
    scaled = vols * dt             # horizon-scaled vol
    vol_d  = np.diag(scaled)
    cov    = vol_d @ corr @ vol_d
    port_var  = float(positions @ cov @ positions)
    port_sigma = np.sqrt(max(port_var, 0))
    var  = z * port_sigma
    cvar = port_sigma * norm.pdf(z) / (1 - norm.cdf(z))  # analytical CVaR for normal
    return float(var), float(cvar)


def run(inp: StressTestInputs) -> StressTestResults:
    z  = norm.ppf(inp.confidence)
    dt = np.sqrt(inp.var_days / inp.trading_days)
    n  = len(inp.asset_names)

    # ── base ──
    base_var, base_cvar = _parametric_var_cvar(
        inp.positions, inp.base_vols, inp.base_corr, z, dt)

    # ── stressed ──
    s_vols = np.maximum(inp.base_vols + inp.vol_shocks, 0.001)

    s_corr = inp.base_corr.copy()
    for i in range(n):
        for j in range(i+1, n):
            adj = inp.corr_shocks[i, j]
            if adj != 0:
                s_corr[i, j] = np.clip(inp.base_corr[i,j] + adj, -0.999, 0.999)
                s_corr[j, i] = s_corr[i, j]

    psd = _is_psd(s_corr)
    if not psd:
        s_corr = _nearest_psd(s_corr)

    stress_var, stress_cvar = _parametric_var_cvar(
        inp.positions, s_vols, s_corr, z, dt)

    return StressTestResults(
        base_var    = base_var,
        base_cvar   = base_cvar,
        stress_var  = stress_var,
        stress_cvar = stress_cvar,
        stressed_vols = s_vols,
        stressed_corr = s_corr,
        corr_psd      = psd,
        asset_names   = inp.asset_names,
    )


def demo():
    names = ["S&P500","FTSE100","NIKKEI","CAC40","DAX100","USD/GBP","USD/JPY","USD/EUR"]
    n     = len(names)
    pos   = np.array([2_385_200, 850_000, 1_025_600, 760_525,
                       985_850,   850_000, 1_025_600, 500_235], dtype=float)
    vols  = np.array([0.18, 0.16, 0.20, 0.17, 0.19, 0.09, 0.10, 0.10])

    rng   = np.random.default_rng(7)
    corr  = np.eye(n)
    for i in range(n):
        for j in range(i+1, n):
            c = rng.uniform(0.1, 0.7)
            corr[i,j] = corr[j,i] = c

    vol_shocks  = np.array([0.08, 0.05, -0.04, 0.05, 0.0, 0.0, -0.03, 0.0])
    corr_shocks = np.zeros((n,n))
    corr_shocks[0,1] = corr_shocks[1,0] = 0.2
    corr_shocks[0,3] = corr_shocks[3,0] = 0.2
    corr_shocks[1,4] = corr_shocks[4,1] = 0.2
    corr_shocks[1,7] = corr_shocks[7,1] = 0.3
    corr_shocks[2,5] = corr_shocks[5,2] = -0.5
    corr_shocks[5,6] = corr_shocks[6,5] = -0.3

    inp = StressTestInputs(
        asset_names=names, positions=pos,
        base_vols=vols, base_corr=corr,
        vol_shocks=vol_shocks, corr_shocks=corr_shocks,
        var_days=5, confidence=0.99,
    )
    r = run(inp)
    print(f"Base   VaR=${r.base_var:>12,.0f}   CVaR=${r.base_cvar:>12,.0f}")
    print(f"Stress VaR=${r.stress_var:>12,.0f}   CVaR=${r.stress_cvar:>12,.0f}")
    print(f"Correlation PSD before stress: {r.corr_psd}")


if __name__ == "__main__":
    demo()
