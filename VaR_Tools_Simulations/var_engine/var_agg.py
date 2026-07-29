"""var_agg.py — Module 9: VaR Aggregation (EWMA + Sub-portfolio + PCA)
Replicates VaRtools sheet '9 VaR Aggregation'.

Three approaches:
  1. Full portfolio — all assets at individual level
  2. Sub-portfolio aggregation — aggregate equities and FX separately, then combine
  3. PCA VaR — use first N principal components; optionally include residuals

EWMA covariance estimation (λ=0.94 RiskMetrics standard).
Data: ThetaData via data_loader (cached).  No yfinance.
"""
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from scipy.stats import norm


# ── EWMA covariance ───────────────────────────────────────────────────────────

def ewma_covariance(returns: np.ndarray, lam: float = 0.94) -> np.ndarray:
    """Returns (n, n) EWMA covariance matrix from (T, n) returns array.
    T must be >= 2.  Uses exponentially decaying weights, RiskMetrics convention.
    """
    T, n   = returns.shape
    weights = np.array([(1 - lam) * lam**k for k in range(T-1, -1, -1)])
    weights /= weights.sum()
    mu  = (weights[:, None] * returns).sum(axis=0)
    dR  = returns - mu
    cov = (weights[:, None] * dR).T @ dR   # (n, n)
    return cov * 252.0   # annualise


def ewma_correlation(returns: np.ndarray, lam: float = 0.94) -> np.ndarray:
    cov  = ewma_covariance(returns, lam)
    d    = np.sqrt(np.diag(cov))
    d[d == 0] = 1.0
    return cov / np.outer(d, d)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _nearest_pd(m):
    ev, evec = np.linalg.eigh(m)
    ev = np.maximum(ev, 1e-12)
    return evec @ np.diag(ev) @ evec.T


def _var_cvar_analytical(positions, cov, confidence, var_days, trading_days):
    z  = norm.ppf(confidence)
    dt = np.sqrt(var_days / trading_days)
    # scale cov to VaR horizon
    cov_h     = cov * (var_days / trading_days)
    port_var  = float(positions @ cov_h @ positions)
    sig       = np.sqrt(max(port_var, 0))
    var       = z * sig
    cvar      = sig * norm.pdf(z) / (1 - norm.cdf(z))
    return float(var), float(cvar)


def _component_var(positions, cov, confidence, var_days, trading_days):
    z  = norm.ppf(confidence)
    cov_h     = cov * (var_days / trading_days)
    port_var  = float(positions @ cov_h @ positions)
    sig       = np.sqrt(max(port_var, 0))
    if sig == 0:
        return np.zeros(len(positions))
    sigma_w = cov_h @ positions
    return z * sigma_w / sig   # (n,) signed component VaR


def _standalone_var(positions, cov, confidence, var_days, trading_days):
    z  = norm.ppf(confidence)
    cov_h = cov * (var_days / trading_days)
    sigs  = np.sqrt(np.maximum(np.diag(cov_h), 0))
    return z * np.abs(positions) * sigs


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class VaRAggInputs:
    asset_names:    List[str]
    positions:      np.ndarray          # (n,) dollar
    group_mask:     np.ndarray          # int array: 0=equity, 1=FX (for sub-portfolio)
    # returns: provide pre-loaded (T, n) or let data_loader fetch via tickers
    returns:        Optional[np.ndarray] = None
    tickers:        Optional[List[str]]  = None
    start_date:     Optional[str]        = None
    end_date:       Optional[str]        = None
    ewma_lambda:    float = 0.94
    var_days:       float = 5.0
    trading_days:   float = 252.0
    confidence:     float = 0.95
    n_pca_components: int = 2


@dataclass
class VaRAggResults:
    # 1. full portfolio
    total_var:       float
    total_cvar:      float
    component_var:   np.ndarray
    standalone_var:  np.ndarray
    # 2. sub-portfolio aggregation
    subport_var:     Dict[str, float]   # group_name → VaR
    aggregated_var:  float
    # 3. PCA VaR
    pca_var_with_resid:    float
    pca_var_without_resid: float
    explained_variance:    np.ndarray   # per component
    asset_names:     List[str]
    ewma_corr:       np.ndarray


def run(inp: VaRAggInputs) -> VaRAggResults:
    # ── get returns ──────────────────────────────────────────────────────────
    if inp.returns is not None:
        R = inp.returns
    else:
        from .data_loader import fetch_log_returns, default_date_range
        if not inp.tickers:
            raise ValueError("Provide either returns or tickers")
        start = inp.start_date or default_date_range(504)[0]
        end   = inp.end_date   or default_date_range(504)[1]
        arrays = [fetch_log_returns(tk, start, end) for tk in inp.tickers]
        min_len = min(len(a) for a in arrays)
        R = np.column_stack([a[-min_len:] for a in arrays])

    # ── EWMA covariance ──────────────────────────────────────────────────────
    cov      = ewma_covariance(R, inp.ewma_lambda)
    cov      = _nearest_pd(cov)
    d        = np.sqrt(np.diag(cov))
    d[d == 0]= 1.0
    corr     = cov / np.outer(d, d)

    pos = inp.positions
    n   = len(pos)

    # ── 1. Full portfolio ────────────────────────────────────────────────────
    total_var, total_cvar = _var_cvar_analytical(
        pos, cov, inp.confidence, inp.var_days, inp.trading_days)
    comp_var  = _component_var(pos, cov, inp.confidence, inp.var_days, inp.trading_days)
    stand_var = _standalone_var(pos, cov, inp.confidence, inp.var_days, inp.trading_days)

    # ── 2. Sub-portfolio aggregation ─────────────────────────────────────────
    groups     = np.unique(inp.group_mask)
    group_names= {0:"Equities", 1:"FX", 2:"Rates"}
    subport_vars = {}
    sub_vols     = {}
    sub_pos_total= {}

    for g in groups:
        mask = inp.group_mask == g
        p_sub  = np.where(mask, pos, 0.0)
        cov_sub= cov[np.ix_(mask, mask)]
        pos_sub= pos[mask]
        v, _   = _var_cvar_analytical(pos_sub, cov_sub,
                                       inp.confidence, inp.var_days, inp.trading_days)
        name = group_names.get(int(g), f"Group{g}")
        subport_vars[name] = v
        # store sub-portfolio vol for aggregation
        cov_h  = cov_sub * (inp.var_days / inp.trading_days)
        sub_vols[name]      = np.sqrt(max(float(pos_sub @ cov_h @ pos_sub), 0))
        sub_pos_total[name] = float(pos_sub.sum())

    # aggregate: build 2x2 correlation from sub-portfolio return series
    sub_rets = {}
    for g in groups:
        mask = inp.group_mask == g
        if mask.sum() == 0: continue
        w    = pos[mask] / max(pos[mask].sum(), 1.0)
        sub_rets[g] = R[:, mask] @ w

    if len(sub_rets) >= 2:
        keys   = sorted(sub_rets.keys())
        sub_R  = np.column_stack([sub_rets[k] for k in keys])
        sub_cov= ewma_covariance(sub_R, inp.ewma_lambda)
        sub_pos= np.array([pos[inp.group_mask == k].sum() for k in keys])
        agg_var, _ = _var_cvar_analytical(sub_pos, sub_cov,
                                           inp.confidence, inp.var_days, inp.trading_days)
    else:
        agg_var = total_var

    # ── 3. PCA VaR ───────────────────────────────────────────────────────────
    # Eigen-decompose annualised cov
    eigvals, eigvecs = np.linalg.eigh(cov)
    # sort descending
    idx     = np.argsort(eigvals)[::-1]
    eigvals = eigvals[idx]
    eigvecs = eigvecs[:, idx]

    k = min(inp.n_pca_components, n)
    # factor exposures: β_i = position_i * eigvec_i (factor loadings)
    # portfolio factor variance = Σ_j λ_j (pos' · v_j)²
    pos_factor = eigvecs.T @ pos           # (n,) factor returns of portfolio
    z  = norm.ppf(inp.confidence)
    dt = inp.var_days / inp.trading_days

    # with residuals (exact reconstruction)
    cov_h = cov * dt
    pca_var_full, _ = _var_cvar_analytical(pos, cov, inp.confidence,
                                            inp.var_days, inp.trading_days)

    # without residuals: only first k components
    var_pca = z * np.sqrt(max(float(dt * np.sum(eigvals[:k] * pos_factor[:k]**2)), 0))

    # with residuals: add specific (residual) variance
    resid_var_contrib = float(dt * np.sum(eigvals[k:] * pos_factor[k:]**2))
    var_pca_resid = z * np.sqrt(max(float(dt * np.sum(eigvals[:k] * pos_factor[:k]**2))
                                    + resid_var_contrib, 0))

    explained = eigvals[:k] / max(eigvals.sum(), 1e-12)

    return VaRAggResults(
        total_var       = total_var,
        total_cvar      = total_cvar,
        component_var   = comp_var,
        standalone_var  = stand_var,
        subport_var     = subport_vars,
        aggregated_var  = agg_var,
        pca_var_with_resid    = var_pca_resid,
        pca_var_without_resid = var_pca,
        explained_variance    = explained,
        asset_names     = inp.asset_names,
        ewma_corr       = corr,
    )


def demo():
    names = ["S&P500","FTSE100","NIKKEI","CAC40","DAX100","USD/GBP","USD/JPY","USD/EUR"]
    pos   = np.array([2_385_200, 850_000, 1_025_600, 760_525,
                       985_850,   850_000, 1_025_600, 500_235], dtype=float)
    groups = np.array([0,0,0,0,0,1,1,1])   # 0=equity, 1=FX

    rng = np.random.default_rng(99)
    T   = 300
    R   = rng.normal(0, 0.01, (T, len(names)))

    inp = VaRAggInputs(
        asset_names=names, positions=pos, group_mask=groups,
        returns=R, var_days=5, confidence=0.95, n_pca_components=2,
    )
    r = run(inp)
    print(f"Total VaR         : ${r.total_var:>12,.0f}")
    print(f"Aggregated VaR    : ${r.aggregated_var:>12,.0f}")
    print(f"PCA VaR (no resid): ${r.pca_var_without_resid:>12,.0f}")
    print(f"PCA VaR (w/ resid): ${r.pca_var_with_resid:>12,.0f}")
    print(f"PCA explained var : {r.explained_variance.round(3)}")
    print("\nSub-portfolio VaR:")
    for k, v in r.subport_var.items():
        print(f"  {k:12s}: ${v:>12,.0f}")


if __name__ == "__main__":
    demo()
