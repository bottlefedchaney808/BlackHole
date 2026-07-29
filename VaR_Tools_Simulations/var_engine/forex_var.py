"""forex_var.py — Module 6: VaR with Foreign Currency Exposures
Replicates VaRtools sheet '6 VaR and Forex'.

Computes total VaR for a multi-currency equity portfolio, breaking out:
  - Equity VaR (systematic)
  - FX VaR
  - Component VaR per asset
  - Standalone VaR per asset
  - Total portfolio VaR with diversification benefit

All in analytical (parametric) form — no MC overhead for this module.
"""
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional
from scipy.stats import norm


@dataclass
class ForexVaRInputs:
    asset_names:          List[str]      # e.g. ["S&P500","DAX","USD/EUR"]
    volatilities:         np.ndarray     # annualised, per asset (including FX)
    corr_matrix:          np.ndarray     # (n, n)
    # position in HOME currency (USD)
    positions_home:       np.ndarray     # (n,) dollar value
    # which assets are pure FX (True) vs equity (False)
    is_fx:                np.ndarray     # bool array (n,)
    var_days:             float = 5.0
    trading_days:         float = 252.0
    confidence:           float = 0.99


@dataclass
class ForexVaRResults:
    total_var:       float
    equity_var:      float
    fx_var:          float
    standalone_var:  np.ndarray   # (n,) VaR treating each asset in isolation
    component_var:   np.ndarray   # (n,) sum = total_var
    diversification: float        # standalone.sum() - total_var
    asset_names:     List[str]


def run(inp: ForexVaRInputs) -> ForexVaRResults:
    z   = norm.ppf(inp.confidence)
    dt  = np.sqrt(inp.var_days / inp.trading_days)
    w   = inp.positions_home                          # (n,)
    sig = inp.volatilities * dt                       # scaled to VaR horizon
    n   = len(w)

    # covariance matrix of returns
    vol_diag = np.diag(sig)
    cov      = vol_diag @ inp.corr_matrix @ vol_diag  # (n, n) variance-covariance

    # portfolio variance  σ_p² = w' Σ w
    port_var   = float(w @ cov @ w)
    port_sigma = np.sqrt(max(port_var, 0))
    total_var  = z * port_sigma

    # standalone VaR per asset
    standalone = z * np.abs(w) * sig

    # component VaR: C_i = ρ_{i,P} * σ_i * |w_i| * z
    # where ρ_{i,P} = (Σw)_i / σ_p
    # C_i = w_i * (Σw)_i * z / σ_p  — sums exactly to total VaR
    sigma_w = cov @ w
    component = (w * sigma_w * z / port_sigma) if port_sigma > 0 else np.zeros(n)

    # equity vs FX breakdown
    equity_mask = ~inp.is_fx
    fx_mask     = inp.is_fx

    w_eq = np.where(equity_mask, w, 0.0)
    w_fx = np.where(fx_mask,     w, 0.0)

    eq_var_sq = float(w_eq @ cov @ w_eq)
    fx_var_sq = float(w_fx @ cov @ w_fx)
    equity_var = z * np.sqrt(max(eq_var_sq, 0))
    fx_var     = z * np.sqrt(max(fx_var_sq, 0))

    diversification = float(standalone.sum()) - total_var

    return ForexVaRResults(
        total_var      = float(total_var),
        equity_var     = float(equity_var),
        fx_var         = float(fx_var),
        standalone_var = standalone,
        component_var  = component,
        diversification= diversification,
        asset_names    = inp.asset_names,
    )


def demo():
    inp = ForexVaRInputs(
        asset_names   = ["S&P500","DAX100","USD/Euro"],
        volatilities  = np.array([0.185, 0.240, 0.097]),
        corr_matrix   = np.array([[1.00,  0.36, -0.17],
                                   [0.36,  1.00,  0.20],
                                   [-0.17, 0.20,  1.00]]),
        positions_home= np.array([2_500_250.0, 1_444_256.25, 1_214_256.25]),
        is_fx         = np.array([False, False, True]),
        var_days=5, confidence=0.99,
    )
    r = run(inp)
    print(f"Total VaR      : ${r.total_var:>12,.0f}")
    print(f"Equity VaR     : ${r.equity_var:>12,.0f}")
    print(f"FX VaR         : ${r.fx_var:>12,.0f}")
    print(f"Diversif. benefit: ${r.diversification:>10,.0f}")
    print("\nStandalone VaR:")
    for name, sv in zip(r.asset_names, r.standalone_var):
        print(f"  {name:15s}: ${sv:>12,.0f}")
    print("\nComponent VaR:")
    for name, cv in zip(r.asset_names, r.component_var):
        print(f"  {name:15s}: ${cv:>12,.0f}")


if __name__ == "__main__":
    demo()
