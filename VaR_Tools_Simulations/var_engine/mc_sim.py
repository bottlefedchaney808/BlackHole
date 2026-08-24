"""mc_sim.py — Module 2: Monte Carlo VaR (partial vs full revaluation)
Replicates VaRtools sheet '2 Monte Carlo Simulation'.

Supports three position types:
  1 = Stock (linear delta = 1)
  2 = European option  (Black-Scholes full revaluation OR delta-gamma-theta approx)
  3 = Fixed-rate bond  (duration-based delta OR full DCF revaluation)

Runs both partial (delta-gamma-theta) and full revaluation simulations and
reports VaR from each for comparison.
"""

from dataclasses import dataclass, field

import numpy as np
from scipy.stats import norm

# ── Black-Scholes helpers ─────────────────────────────────────────────────────


def _bs_price(S, K, T, r, sigma, is_call=True):
    if T <= 0 or sigma <= 0:
        intrinsic = max(S - K, 0) if is_call else max(K - S, 0)
        return intrinsic
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if is_call:
        return S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    else:
        return K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def _bs_greeks(S, K, T, r, sigma, is_call=True):
    if T <= 0 or sigma <= 0:
        return {"price": 0, "delta": 0, "gamma": 0, "theta": 0}
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    nd1 = norm.pdf(d1)
    price = _bs_price(S, K, T, r, sigma, is_call)
    delta = norm.cdf(d1) if is_call else norm.cdf(d1) - 1
    gamma = nd1 / (S * sigma * np.sqrt(T))
    theta_call = (
        -(S * nd1 * sigma) / (2 * np.sqrt(T)) - r * K * np.exp(-r * T) * norm.cdf(d2)
    ) / 365
    theta_put = (
        -(S * nd1 * sigma) / (2 * np.sqrt(T)) + r * K * np.exp(-r * T) * norm.cdf(-d2)
    ) / 365
    theta = theta_call if is_call else theta_put
    return {"price": price, "delta": delta, "gamma": gamma, "theta": theta}


# ── Bond helpers ──────────────────────────────────────────────────────────────


def _bond_price(face, coupon_rate, freq, T_years, r):
    """Simple fixed-rate bond: flat yield curve at r."""
    n_periods = int(T_years * freq)
    coupon = face * coupon_rate / freq
    pv_coupons = sum(coupon * np.exp(-r * k / freq) for k in range(1, n_periods + 1))
    pv_face = face * np.exp(-r * T_years)
    return pv_coupons + pv_face


def _bond_duration(face, coupon_rate, freq, T_years, r):
    n_periods = int(T_years * freq)
    coupon = face * coupon_rate / freq
    price = _bond_price(face, coupon_rate, freq, T_years, r)
    if price == 0:
        return 0
    w = sum(
        (k / freq) * coupon * np.exp(-r * k / freq) for k in range(1, n_periods + 1)
    )
    w += T_years * face * np.exp(-r * T_years)
    return w / price  # modified duration


# ── Data structures ───────────────────────────────────────────────────────────


@dataclass
class Position:
    pos_type: int  # 1=stock, 2=option, 3=bond
    market_id: str
    # stock / option
    quantity: float = 0.0
    # option specific
    strike: float = 0.0
    days_cal: float = 0.0
    is_call: bool = True
    # bond specific
    face: float = 0.0
    coupon: float = 0.0
    freq: float = 4.0
    maturity_years: float = 0.0


@dataclass
class MCSimInputs:
    # market state per asset [stock1, stock2, bond_yield]
    # market_ids must align with positions
    market_ids: list[str]
    spot_prices: np.ndarray  # current price/rate per market id
    volatilities: np.ndarray  # annualised vol per market id
    corr_matrix: np.ndarray
    risk_free: float = 0.05
    trading_days: float = 250.0
    var_days: float = 5.0
    confidence: float = 0.99
    n_sims: int = 10_000
    seed: int | None = 42
    positions: list[Position] = field(default_factory=list)
    filter_type: int = 0  # 0=all, 1=stock only, 2=options only, 3=bonds only
    expected_returns: np.ndarray | None = (
        None  # annualised drift per market id; None = zero-drift (legacy)
    )


@dataclass
class MCSimResults:
    var_partial: float
    var_full: float
    cvar_partial: float
    cvar_full: float
    pnl_partial: np.ndarray
    pnl_full: np.ndarray
    position_vars: dict  # pos_type → VaR from full reval
    terminal_prices: np.ndarray | None = (
        None  # (n_sims, n) simulated spot paths at horizon
    )


def run(inp: MCSimInputs) -> MCSimResults:
    rng = np.random.default_rng(inp.seed)
    n = len(inp.market_ids)
    dt = inp.var_days / inp.trading_days
    idx = {mid: i for i, mid in enumerate(inp.market_ids)}
    T_option = inp.var_days / 365.0  # calendar-adjusted remaining life

    positions = [
        p
        for p in inp.positions
        if inp.filter_type == 0 or p.pos_type == inp.filter_type
    ]

    # build covariance / Cholesky
    vol_diag = np.diag(inp.volatilities)
    cov = vol_diag @ inp.corr_matrix @ vol_diag
    # nearest PD
    eigvals, eigvecs = np.linalg.eigh(cov)
    eigvals = np.maximum(eigvals, 1e-10)
    cov = eigvecs @ np.diag(eigvals) @ eigvecs.T
    L = np.linalg.cholesky(cov)

    # simulate market moves
    Z = rng.standard_normal((inp.n_sims, n))
    lr = Z @ L.T * np.sqrt(dt)  # (n_sims, n) log returns
    if inp.expected_returns is not None:
        expected_returns = np.asarray(inp.expected_returns, dtype=float)
        if expected_returns.shape != (n,):
            raise ValueError(
                f"expected_returns must have shape ({n},), got {expected_returns.shape}"
            )
        drift = (expected_returns - 0.5 * inp.volatilities**2) * dt
        lr = lr + drift
    S_sim = inp.spot_prices * np.exp(lr)  # (n_sims, n) simulated prices

    pnl_full = np.zeros(inp.n_sims)
    pnl_partial = np.zeros(inp.n_sims)
    pnl_by_type = {
        1: np.zeros(inp.n_sims),
        2: np.zeros(inp.n_sims),
        3: np.zeros(inp.n_sims),
    }

    r = inp.risk_free
    t_cal = max(inp.var_days / 365.0, 1 / 365.0)  # horizon in years

    for pos in positions:
        i = idx.get(pos.market_id)
        if i is None:
            continue
        S0 = inp.spot_prices[i]
        dS = S_sim[:, i] - S0
        lret = lr[:, i]

        if pos.pos_type == 1:  # ── STOCK
            full = dS * pos.quantity
            partial = dS * pos.quantity
            pnl_full += full
            pnl_partial += partial
            pnl_by_type[1] += full

        elif pos.pos_type == 2:  # ── OPTION
            T_rem = max(pos.days_cal / 365.0, 1 / 365.0)
            g = _bs_greeks(S0, pos.strike, T_rem, r, inp.volatilities[i], pos.is_call)
            V0 = g["price"]
            # partial: delta + gamma + theta
            partial = pos.quantity * (
                g["delta"] * dS + 0.5 * g["gamma"] * dS**2 + g["theta"] * inp.var_days
            )
            # full revaluation
            T_new = max(T_rem - t_cal, 1 / 365.0)
            V_sim = np.array(
                [
                    _bs_price(
                        S_sim[j, i],
                        pos.strike,
                        T_new,
                        r,
                        inp.volatilities[i],
                        pos.is_call,
                    )
                    for j in range(inp.n_sims)
                ]
            )
            full = pos.quantity * (V_sim - V0)
            pnl_partial += partial
            pnl_full += full
            pnl_by_type[2] += full

        elif pos.pos_type == 3:  # ── BOND (rate is the market variable)
            r0 = S0
            T_mat = pos.maturity_years
            V0 = _bond_price(pos.face, pos.coupon, pos.freq, T_mat, r0)
            r_sim = S_sim[:, i]  # simulated yield
            dur = _bond_duration(pos.face, pos.coupon, pos.freq, T_mat, r0)
            dr = r_sim - r0
            # partial: duration approximation
            partial = -V0 * dur * dr
            # full: reprice at each sim yield
            V_sim = np.array(
                [
                    _bond_price(
                        pos.face,
                        pos.coupon,
                        pos.freq,
                        max(T_mat - t_cal, 0.001),
                        r_sim[j],
                    )
                    for j in range(inp.n_sims)
                ]
            )
            full = V_sim - V0
            pnl_partial += partial
            pnl_full += full
            pnl_by_type[3] += full

    def _var_cvar(pnl):
        cut = np.quantile(pnl, 1.0 - inp.confidence)
        v = float(-cut)
        cv = float(-pnl[pnl <= cut].mean()) if (pnl <= cut).any() else v
        return v, cv

    vp, cvp = _var_cvar(pnl_partial)
    vf, cvf = _var_cvar(pnl_full)

    pos_vars = {}
    for t, arr in pnl_by_type.items():
        if arr.any():
            pos_vars[t], _ = _var_cvar(arr)

    return MCSimResults(
        var_partial=vp,
        var_full=vf,
        cvar_partial=cvp,
        cvar_full=cvf,
        pnl_partial=pnl_partial,
        pnl_full=pnl_full,
        position_vars=pos_vars,
        terminal_prices=S_sim,
    )


def demo():
    positions = [
        Position(pos_type=1, market_id="Stock1", quantity=10.0),
        Position(
            pos_type=2,
            market_id="Stock2",
            quantity=100.0,
            strike=10.0,
            days_cal=100,
            is_call=True,
        ),
        Position(
            pos_type=3,
            market_id="Bond",
            face=1000.0,
            coupon=0.05,
            freq=4,
            maturity_years=6.0,
        ),
    ]
    inp = MCSimInputs(
        market_ids=["Stock1", "Stock2", "Bond"],
        spot_prices=np.array([100.0, 11.0, 0.07]),
        volatilities=np.array([0.20, 0.30, 0.25]),
        corr_matrix=np.array([[1.0, 0.6, 0.3], [0.6, 1.0, 0.3], [0.3, 0.3, 1.0]]),
        risk_free=0.05,
        var_days=5,
        n_sims=10_000,
        confidence=0.99,
        positions=positions,
    )
    r = run(inp)
    print(f"Partial  VaR : ${r.var_partial:,.2f}  CVaR: ${r.cvar_partial:,.2f}")
    print(f"Full     VaR : ${r.var_full:,.2f}  CVaR: ${r.cvar_full:,.2f}")
    for t, v in r.position_vars.items():
        name = {1: "Stock", 2: "Option", 3: "Bond"}[t]
        print(f"  {name} VaR: ${v:,.2f}")


if __name__ == "__main__":
    demo()
