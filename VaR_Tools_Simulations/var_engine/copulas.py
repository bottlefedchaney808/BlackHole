"""copulas.py — Module 5: Copula-based VaR
Replicates VaRtools sheet '5 Copulas'.

Supported copula types:
  gaussian  — standard Gaussian copula (Cholesky on corr matrix)
  student_t — Student-T copula (fat tails, df configurable ~3-5)
  clayton   — Clayton copula (lower-tail dependence, alpha ~0.7)

Each copula transforms uniform marginals; marginals themselves can be
normal or Student-T (per-asset df calibrated from data).
"""
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Literal
from scipy.stats import t as student_t, norm, rankdata


# ── Copula samplers ───────────────────────────────────────────────────────────

def _sample_gaussian(n_sims, corr, rng):
    """Returns (n_sims, n) uniform marginals from Gaussian copula."""
    L   = np.linalg.cholesky(_nearest_pd(corr))
    Z   = rng.standard_normal((n_sims, corr.shape[0]))
    X   = Z @ L.T
    return norm.cdf(X)   # (n_sims, n) uniforms


def _sample_student_t(n_sims, corr, df, rng):
    """Student-T copula: fatter joint tails."""
    n   = corr.shape[0]
    L   = np.linalg.cholesky(_nearest_pd(corr))
    Z   = rng.standard_normal((n_sims, n))
    X   = Z @ L.T
    chi2 = rng.chisquare(df, size=n_sims)
    Y   = X / np.sqrt(chi2[:, None] / df)
    return student_t.cdf(Y, df=df)  # (n_sims, n) uniforms


def _sample_clayton(n_sims, alpha, n, rng):
    """Clayton copula — bivariate extended to n via conditional sampling.
    Lower-tail dependent; alpha > 0.
    """
    # Use Laplace-Stieltjes representation (Marshall-Olkin algorithm)
    # Generator: phi(t) = (1+t)^(-1/alpha)
    # V ~ Gamma(1/alpha, 1)
    theta = 1.0 / alpha
    V     = rng.gamma(theta, 1.0, size=n_sims)
    E     = rng.exponential(1.0, size=(n_sims, n))
    U     = (1 + E / V[:, None]) ** (-alpha)
    return np.clip(U, 1e-8, 1 - 1e-8)


def _nearest_pd(m: np.ndarray) -> np.ndarray:
    eigvals, eigvecs = np.linalg.eigh(m)
    eigvals = np.maximum(eigvals, 1e-10)
    return eigvecs @ np.diag(eigvals) @ eigvecs.T


# ── Marginal quantile transform ───────────────────────────────────────────────

def _uniform_to_returns(U, marginal_dfs, vols, dt):
    """Transform (n_sims, n) uniforms → log returns using per-asset marginal.
    marginal_dfs: array of df per asset (0 or large → normal).
    """
    n_sims, n = U.shape
    returns   = np.empty_like(U)
    for j in range(n):
        df = marginal_dfs[j] if marginal_dfs[j] > 0 else 0
        if df < 2 or df > 300:
            returns[:, j] = norm.ppf(U[:, j]) * vols[j] * np.sqrt(dt)
        else:
            # A standard Student-T(df) has variance df/(df-2), not 1 -- must
            # normalize to unit variance before scaling by the target vol, or
            # every fat-tailed marginal (df ~3-6, the whole point of calibrating
            # marginal_dfs) silently overstates vol/VaR by sqrt(df/(df-2)).
            t_std = np.sqrt(df / (df - 2))
            returns[:, j] = (student_t.ppf(U[:, j], df=df) / t_std) * vols[j] * np.sqrt(dt)
    return returns


# ── Inputs / Outputs ──────────────────────────────────────────────────────────

@dataclass
class CopulaInputs:
    tickers:        List[str]
    position_vals:  np.ndarray      # dollar exposure per ticker (n,)
    volatilities:   np.ndarray      # annualised, shape (n,)
    corr_matrix:    np.ndarray      # (n, n)
    copula_type:    Literal["gaussian","student_t","clayton"] = "student_t"
    student_df:     float  = 5.0    # T copula df (joint)
    marginal_dfs:   Optional[np.ndarray] = None   # per-asset df; 0=normal
    clayton_alpha:  float  = 0.7
    var_days:       float  = 10.0
    trading_days:   float  = 252.0
    confidence:     float  = 0.99
    n_sims:         int    = 50_000
    seed:           Optional[int] = 42


@dataclass
class CopulaResults:
    var:              float
    cvar:             float
    copula_type:      str
    pnl_distribution: np.ndarray


def run(inp: CopulaInputs) -> CopulaResults:
    rng  = np.random.default_rng(inp.seed)
    n    = len(inp.tickers)
    dt   = inp.var_days / inp.trading_days
    mdf  = inp.marginal_dfs if inp.marginal_dfs is not None else np.zeros(n)

    # 1. sample uniform marginals via copula
    if inp.copula_type == "gaussian":
        U = _sample_gaussian(inp.n_sims, inp.corr_matrix, rng)
    elif inp.copula_type == "student_t":
        U = _sample_student_t(inp.n_sims, inp.corr_matrix, inp.student_df, rng)
    elif inp.copula_type == "clayton":
        U = _sample_clayton(inp.n_sims, inp.clayton_alpha, n, rng)
    else:
        raise ValueError(f"Unknown copula: {inp.copula_type}")

    # 2. transform uniforms → asset returns via marginals
    R = _uniform_to_returns(U, mdf, inp.volatilities, dt)  # (n_sims, n)

    # 3. portfolio P&L
    pnl = R @ inp.position_vals

    # 4. VaR / CVaR
    cut  = np.quantile(pnl, 1.0 - inp.confidence)
    var  = float(-cut)
    tail = pnl[pnl <= cut]
    cvar = float(-tail.mean()) if len(tail) > 0 else var

    return CopulaResults(
        var=var, cvar=cvar,
        copula_type=inp.copula_type,
        pnl_distribution=pnl,
    )


def calibrate_marginal_dfs(returns_dict: Dict[str, np.ndarray]) -> np.ndarray:
    """Fit Student-T df per asset using MLE (scipy.stats.t.fit).
    Returns array of df values.  Large df (~100+) means effectively normal.
    """
    from scipy.stats import t as st
    dfs = []
    for tk, r in returns_dict.items():
        try:
            df, _, _ = st.fit(r)
            dfs.append(float(df))
        except Exception:
            dfs.append(0.0)   # fallback to normal
    return np.array(dfs)


def demo():
    tickers  = ["TWX","XOM","CSCO","GE","KO","SPY","GOOG","MSFT"]
    n        = len(tickers)
    rng      = np.random.default_rng(1)
    pos_vals = np.array([120_000, 250_000, 125_000, 52_000,
                          457_000, 110_000, 220_000, 170_000], dtype=float)
    vols     = np.full(n, 0.25)
    corr     = np.eye(n) + rng.uniform(-0.3, 0.5, (n, n))
    corr     = (corr + corr.T) / 2
    np.fill_diagonal(corr, 1.0)

    mdf = np.array([3.26, 3.08, 99.0, 3.0, 3.1, 0.0, 3.2, 0.0])

    for ct in ["gaussian","student_t","clayton"]:
        inp = CopulaInputs(
            tickers=tickers, position_vals=pos_vals,
            volatilities=vols, corr_matrix=corr,
            copula_type=ct, student_df=5.0,
            marginal_dfs=mdf, clayton_alpha=0.7,
            var_days=10, n_sims=50_000, confidence=0.99,
        )
        r = run(inp)
        print(f"{ct:12s}  VaR=${r.var:>12,.0f}  CVaR=${r.cvar:>12,.0f}")


if __name__ == "__main__":
    demo()
