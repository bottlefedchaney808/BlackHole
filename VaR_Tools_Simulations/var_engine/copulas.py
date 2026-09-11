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
    """Clayton copula via Marshall-Olkin. Lower-tail dependent; alpha > 0.

    Exchangeable by construction: ONE parameter governs every pair, so this
    cannot represent a heterogeneous correlation matrix. That is a property
    of the Archimedean family, not a shortcut -- see run()'s clayton branch,
    which converts the caller's corr_matrix into the alpha that best matches
    its average dependence rather than silently discarding it.

    Marshall-Olkin for Clayton parameter alpha:
        V   ~ Gamma(1/alpha, 1)
        E_j ~ Exp(1), iid
        U_j = phi(E_j / V),  phi(t) = (1 + t)^(-1/alpha)

    The exponent is -1/alpha, NOT -alpha. This applied ^(-alpha) while its
    own comment documented ^(-1/alpha), and the two agree only at alpha=1.
    Because Kendall's tau is invariant under monotone marginal transforms,
    the DEPENDENCE stayed correct (measured tau matched alpha/(alpha+2) at
    every alpha) and only the marginals broke -- which is exactly why this
    survived: every test of the copula's dependence structure passed.

    Measured at the alpha=0.7 default: marginals came back with mean 0.672
    instead of 0.5 (KS vs U(0,1): p = 0.0). Feeding that to norm.ppf in
    _uniform_to_returns shifts every asset about +0.45 sigma, which thins the
    loss tail: a 3-name $1M-each book at 25% vol over 10 days reported 99%
    VaR of $160,897 against a true $312,749. The bug UNDERSTATED risk by 49%.
    """
    if alpha <= 0:
        raise ValueError(f"clayton_alpha must be > 0, got {alpha}")
    V = rng.gamma(1.0 / alpha, 1.0, size=n_sims)
    E = rng.exponential(1.0, size=(n_sims, n))
    U = (1 + E / V[:, None]) ** (-1.0 / alpha)
    return np.clip(U, 1e-8, 1 - 1e-8)


def clayton_alpha_from_corr(corr: np.ndarray) -> float:
    """Average pairwise dependence of `corr`, expressed as a Clayton alpha.

    A Clayton copula has one dependence parameter, so a full matrix has to
    collapse to a scalar somewhere. Doing it here, explicitly and by the
    standard identities, beats ignoring the matrix and using a hardcoded
    default that has no relationship to the book at all:

        Gaussian:  tau = (2/pi) * arcsin(rho)     (rho -> Kendall tau)
        Clayton:   tau = alpha / (alpha + 2)      (tau -> alpha)

    Averaging is over tau, not rho, because tau is the quantity both
    families actually share. Non-positive average dependence has no Clayton
    representation (the family only models positive dependence), so it
    clamps to a small positive alpha rather than producing a negative one.
    """
    c = np.asarray(corr, dtype=float)
    iu = np.triu_indices_from(c, k=1)
    if iu[0].size == 0:
        return 0.7
    rho = np.clip(c[iu], -1.0, 1.0)
    tau = float(np.mean((2.0 / np.pi) * np.arcsin(rho)))
    tau = min(max(tau, 1e-3), 0.95)
    return 2.0 * tau / (1.0 - tau)


def _nearest_pd(m: np.ndarray) -> np.ndarray:
    eigvals, eigvecs = np.linalg.eigh(m)
    eigvals = np.maximum(eigvals, 1e-10)
    return eigvecs @ np.diag(eigvals) @ eigvecs.T


# ── Marginal quantile transform ───────────────────────────────────────────────

def _uniform_to_returns(U, marginal_dfs, vols, dt, expected_returns=None):
    """Transform (n_sims, n) uniforms → log returns using per-asset marginal.
    marginal_dfs: array of df per asset (0 or large → normal).
    expected_returns: annualised drift per asset; None = zero-drift (legacy).
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
        if expected_returns is not None:
            returns[:, j] += (expected_returns[j] - 0.5 * vols[j]**2) * dt
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
    # Clayton cannot represent a full correlation matrix (one parameter, all
    # pairs). Leave this False to derive alpha from corr_matrix; set True to
    # pin clayton_alpha exactly as given.
    clayton_alpha_explicit: bool = False
    var_days:       float  = 10.0
    trading_days:   float  = 252.0
    confidence:     float  = 0.99
    n_sims:         int    = 50_000
    seed:           Optional[int] = 42
    spot_prices:    Optional[np.ndarray] = None    # per-ticker spot; required for terminal_prices
    expected_returns: Optional[np.ndarray] = None  # annualised drift per ticker; None = zero-drift (legacy)


@dataclass
class CopulaResults:
    var:              float
    cvar:             float
    copula_type:      str
    pnl_distribution: np.ndarray
    terminal_prices:  Optional[np.ndarray] = None  # (n_sims, n) simulated spot paths at horizon, if spot_prices given


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
        # Clayton is exchangeable -- one alpha for every pair -- so the
        # caller's corr_matrix cannot be honoured elementwise. It used to be
        # dropped on the floor, with a hardcoded clayton_alpha applied to a
        # book whose real dependence it had no relationship to. Derive alpha
        # from the matrix instead, unless the caller pinned one explicitly.
        alpha = (inp.clayton_alpha if inp.clayton_alpha_explicit
                 else clayton_alpha_from_corr(inp.corr_matrix))
        U = _sample_clayton(inp.n_sims, alpha, n, rng)
    else:
        raise ValueError(f"Unknown copula: {inp.copula_type}")

    # 2. transform uniforms → asset returns via marginals
    R = _uniform_to_returns(U, mdf, inp.volatilities, dt, inp.expected_returns)  # (n_sims, n)

    # 3. portfolio P&L
    pnl = R @ inp.position_vals

    # 4. VaR / CVaR
    cut  = np.quantile(pnl, 1.0 - inp.confidence)
    var  = float(-cut)
    tail = pnl[pnl <= cut]
    cvar = float(-tail.mean()) if len(tail) > 0 else var

    terminal_prices = None
    if inp.spot_prices is not None:
        terminal_prices = np.asarray(inp.spot_prices) * np.exp(R)

    return CopulaResults(
        var=var, cvar=cvar,
        copula_type=inp.copula_type,
        pnl_distribution=pnl,
        terminal_prices=terminal_prices,
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
