"""hist_sim.py — Module 3/4: Historical Simulation (Basic, Hull-White, FHS/GARCH)
Replicates VaRtools sheets '3 Historical & MC Simulation' and
'4 Historical Simulation Prices'.

Three methods:
  basic  — plain historical simulation (Boudoukh 1998)
  hw     — Hull-White volatility-adjusted historical simulation
  fhs    — Filtered Historical Simulation (GARCH-standardised residuals)

Data comes from data_loader (ThetaData, cached).  No yfinance.
"""
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Literal
from .data_loader import fetch_log_returns, default_date_range


# ── GARCH(1,1) ────────────────────────────────────────────────────────────────

def _garch_fit(returns: np.ndarray, max_iter: int = 200):
    """Simple GARCH(1,1) via gradient-free MLE (Nelder-Mead via scipy)."""
    from scipy.optimize import minimize
    var0 = float(returns.var())

    def neg_log_lik(params):
        omega, alpha, beta = params
        if omega <= 0 or alpha < 0 or beta < 0 or alpha + beta >= 1:
            return 1e12
        T  = len(returns)
        h  = np.empty(T)
        h[0] = var0
        for t in range(1, T):
            h[t] = omega + alpha * returns[t-1]**2 + beta * h[t-1]
        return 0.5 * np.sum(np.log(h) + returns**2 / h)

    # initial guess: RiskMetrics-like
    x0  = [var0 * 0.01, 0.08, 0.90]
    bnd = [(1e-8, None), (1e-6, 0.999), (1e-6, 0.999)]
    res = minimize(neg_log_lik, x0, method="Nelder-Mead", bounds=bnd,
                   options={"maxiter": max_iter, "xatol": 1e-7, "fatol": 1e-7})
    omega, alpha, beta = res.x
    # compute conditional variance series
    T = len(returns)
    h = np.empty(T)
    h[0] = var0
    for t in range(1, T):
        h[t] = omega + alpha * returns[t-1]**2 + beta * h[t-1]
    return {"omega": omega, "alpha": alpha, "beta": beta,
            "h": h, "sigma": np.sqrt(h),
            "long_run_vol": np.sqrt(omega / max(1 - alpha - beta, 1e-8)),
            "current_vol": float(np.sqrt(h[-1]))}


def _garch_variance_series(returns: np.ndarray, omega, alpha, beta) -> np.ndarray:
    T = len(returns)
    h = np.empty(T)
    h[0] = returns.var()
    for t in range(1, T):
        h[t] = omega + alpha * returns[t-1]**2 + beta * h[t-1]
    return h


# ── Inputs / Outputs ──────────────────────────────────────────────────────────

@dataclass
class HistSimInputs:
    tickers:       List[str]
    position_vals: np.ndarray            # dollar value per ticker (n,)
    var_days:      float = 1.0
    trading_days:  float = 252.0
    confidence:    float = 0.95
    method:        Literal["basic","hw","fhs"] = "basic"
    # date range — if None uses default_date_range(504)
    start_date:    Optional[str] = None
    end_date:      Optional[str] = None
    # pre-loaded returns — skip data_loader if provided
    returns_dict:  Optional[Dict[str,np.ndarray]] = None


@dataclass
class HistSimResults:
    var:              float
    cvar:             float
    method:           str
    pnl_distribution: np.ndarray
    garch_params:     Dict   # ticker → garch params (hw/fhs only)
    scenario_returns: np.ndarray   # (n_scenarios, n_tickers) scaled returns


def _load_returns(inp: HistSimInputs) -> Dict[str, np.ndarray]:
    if inp.returns_dict:
        return inp.returns_dict
    start, end = inp.start_date, inp.end_date
    if start is None or end is None:
        start, end = default_date_range(504)
    out = {}
    for tk in inp.tickers:
        out[tk] = fetch_log_returns(tk, start, end)
    return out


def run(inp: HistSimInputs) -> HistSimResults:
    rets = _load_returns(inp)
    # align to shortest series
    min_len = min(len(v) for v in rets.values())
    R = np.column_stack([rets[tk][-min_len:] for tk in inp.tickers])  # (T, n)
    T, n = R.shape
    weights = inp.position_vals / inp.position_vals.sum()
    scale   = np.sqrt(inp.var_days)  # scale to var horizon

    garch_params = {}

    if inp.method == "basic":
        # plain: apply historical return scenarios to current position
        scenario_rets = R * scale
        pnl = scenario_rets @ inp.position_vals

    elif inp.method == "hw":
        # Hull-White: rescale each historical return by σ_today / σ_historical
        scaled = np.empty_like(R)
        for j, tk in enumerate(inp.tickers):
            g = _garch_fit(R[:, j])
            garch_params[tk] = g
            sigma_t   = g["sigma"]                   # σ at time t (historical)
            sigma_now = g["current_vol"]             # σ today
            # rescale: r_adj = r_hist * (σ_now / σ_t)
            with np.errstate(divide="ignore", invalid="ignore"):
                ratio = np.where(sigma_t > 0, sigma_now / sigma_t, 1.0)
            scaled[:, j] = R[:, j] * ratio
        scenario_rets = scaled * scale
        pnl = scenario_rets @ inp.position_vals

    elif inp.method == "fhs":
        # Filtered Historical Simulation: standardise with GARCH, then resample
        residuals = np.empty_like(R)
        for j, tk in enumerate(inp.tickers):
            g = _garch_fit(R[:, j])
            garch_params[tk] = g
            with np.errstate(divide="ignore", invalid="ignore"):
                z = np.where(g["sigma"] > 0, R[:, j] / g["sigma"], 0.0)
            residuals[:, j] = z
        # reapply current vol to standardised residuals
        current_vols = np.array([garch_params[tk]["current_vol"] for tk in inp.tickers])
        scenario_rets = residuals * current_vols * scale
        pnl = scenario_rets @ inp.position_vals

    else:
        raise ValueError(f"Unknown method: {inp.method}")

    pnl   = np.sort(pnl)
    cut   = np.quantile(pnl, 1.0 - inp.confidence)
    var   = float(-cut)
    tail  = pnl[pnl <= cut]
    cvar  = float(-tail.mean()) if len(tail) > 0 else var

    return HistSimResults(
        var=var, cvar=cvar, method=inp.method,
        pnl_distribution=pnl,
        garch_params=garch_params,
        scenario_returns=scenario_rets,
    )


def demo():
    """Demo with synthetic returns so no network call needed."""
    rng  = np.random.default_rng(42)
    tickers = ["AAPL","GME","AMZN","ABBV","C","EWZ","EWY","AMD"]
    T    = 504
    synth = {tk: rng.normal(0, 0.02, T) for tk in tickers}
    pos   = np.array([120_000, 250_000, 125_000, 52_000,
                       457_000, 110_000, 220_000, 170_000], dtype=float)

    for method in ["basic","hw","fhs"]:
        inp = HistSimInputs(tickers=tickers, position_vals=pos,
                            var_days=1, confidence=0.95,
                            method=method, returns_dict=synth)
        r = run(inp)
        print(f"{method:5s}  VaR=${r.var:>10,.0f}  CVaR=${r.cvar:>10,.0f}")


if __name__ == "__main__":
    demo()
