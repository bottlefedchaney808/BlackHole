"""cashflow_map.py — Module 7: Cash Flow Mapping
Replicates VaRtools sheet '7 Cash Flow Map'.

Maps arbitrary cash flows to standard time vertices, preserving:
  1. Present value
  2. Market risk (volatility)

Standard method: 2-vertex interpolation with correlation between vertices.
"""
import numpy as np
from dataclasses import dataclass, field
from typing import List, Tuple


@dataclass
class Vertex:
    maturity:   float   # in years  e.g. 0.25, 0.5, 1, 2, 5, 10
    zero_rate:  float   # continuously compounded
    vol:        float   # bond price volatility (% pa)


@dataclass
class CashFlow:
    maturity:   float   # years (arbitrary, not a vertex)
    amount:     float   # cash amount


@dataclass
class CashFlowMapInputs:
    vertices:         List[Vertex]
    cashflows:        List[CashFlow]
    corr_matrix:      np.ndarray      # (n_vertices, n_vertices)


@dataclass
class MappedCashFlow:
    original:       CashFlow
    vertex_amounts: np.ndarray   # allocated amount to each vertex
    interp_rate:    float
    interp_vol:     float
    pv:             float


@dataclass
class CashFlowMapResults:
    mapped:          List[MappedCashFlow]
    vertex_totals:   np.ndarray    # total CF at each vertex
    portfolio_var:   float         # 1-day 99% VaR of the mapped CFs
    vertices:        List[Vertex]


def _interpolate(t: float, t1: float, t2: float, v1: float, v2: float) -> float:
    """Linear interpolation between two vertex values."""
    if t2 == t1:
        return v1
    w = (t - t1) / (t2 - t1)
    return v1 * (1 - w) + v2 * w


def _find_bracket(t: float, vertices: List[Vertex]) -> Tuple[int,int]:
    """Return indices of the two vertices bracketing maturity t."""
    mats = [v.maturity for v in vertices]
    if t <= mats[0]:
        return 0, 0
    if t >= mats[-1]:
        i = len(mats) - 1
        return i, i
    for i in range(len(mats)-1):
        if mats[i] <= t <= mats[i+1]:
            return i, i+1
    return 0, 0


def _map_single(cf: CashFlow, vertices: List[Vertex],
                corr_matrix: np.ndarray) -> MappedCashFlow:
    n  = len(vertices)
    i1, i2 = _find_bracket(cf.maturity, vertices)
    v1, v2 = vertices[i1], vertices[i2]

    r_interp   = _interpolate(cf.maturity, v1.maturity, v2.maturity,
                               v1.zero_rate, v2.zero_rate)
    vol_interp = _interpolate(cf.maturity, v1.maturity, v2.maturity,
                               v1.vol,      v2.vol)

    pv = cf.amount * np.exp(-r_interp * cf.maturity)

    # allocate PV to two vertices preserving risk (vol * pv = const)
    vertex_amounts = np.zeros(n)
    if i1 == i2:
        vertex_amounts[i1] = pv
    else:
        # risk-weighting: σ_1 * alpha = σ_interp * pv_total (two-equation system)
        # alpha (v1) + beta (v2) = pv
        # sqrt(α²σ₁² + β²σ₂² + 2αβρσ₁σ₂) = pv * σ_interp
        # Solve quadratic for α
        s1  = v1.vol * pv
        s2  = v2.vol * pv
        rho = corr_matrix[i1, i2] if corr_matrix is not None else 0.0
        s_t = vol_interp * pv
        # α = fraction to v1
        # σ² = (α·s1)² + ((1-α)·s2)² + 2α(1-α)ρ·s1·s2 = s_t²
        a   = s1**2 + s2**2 - 2*rho*s1*s2
        b   = 2*rho*s1*s2 - 2*s2**2
        c   = s2**2 - s_t**2
        if abs(a) < 1e-12:
            alpha = 0.5
        else:
            disc = b**2 - 4*a*c
            disc = max(disc, 0)
            alpha = (-b - np.sqrt(disc)) / (2*a)
            alpha = float(np.clip(alpha, 0.0, 1.0))
        vertex_amounts[i1] = alpha * pv
        vertex_amounts[i2] = (1 - alpha) * pv

    return MappedCashFlow(
        original       = cf,
        vertex_amounts = vertex_amounts,
        interp_rate    = r_interp,
        interp_vol     = vol_interp,
        pv             = pv,
    )


def run(inp: CashFlowMapInputs) -> CashFlowMapResults:
    mapped = [_map_single(cf, inp.vertices, inp.corr_matrix)
              for cf in inp.cashflows]

    n = len(inp.vertices)
    vertex_totals = np.zeros(n)
    for m in mapped:
        vertex_totals += m.vertex_amounts

    # simple 1-day 99% VaR on mapped CFs (parametric)
    from scipy.stats import norm
    z   = norm.ppf(0.99)
    vols = np.array([v.vol for v in inp.vertices])
    # portfolio variance = Σ_ij V_i * V_j * σ_i * σ_j * ρ_ij
    W    = vertex_totals * vols
    port_var = float(W @ inp.corr_matrix @ W)
    var  = z * np.sqrt(max(port_var, 0))

    return CashFlowMapResults(
        mapped        = mapped,
        vertex_totals = vertex_totals,
        portfolio_var = var,
        vertices      = inp.vertices,
    )


def demo():
    verts = [
        Vertex(maturity=0.25, zero_rate=0.045, vol=0.0096),
        Vertex(maturity=0.50, zero_rate=0.050, vol=0.0160),
    ]
    cfs   = [CashFlow(maturity=0.30, amount=120_000.0)]
    corr  = np.array([[1.0, 0.9],[0.9, 1.0]])

    inp = CashFlowMapInputs(vertices=verts, cashflows=cfs, corr_matrix=corr)
    r   = run(inp)

    for m in r.mapped:
        print(f"CF maturity={m.original.maturity:.2f}y  amount={m.original.amount:,.0f}")
        print(f"  PV={m.pv:,.2f}  interp_rate={m.interp_rate:.4f}  interp_vol={m.interp_vol:.5f}")
        for i, v in enumerate(r.vertices):
            print(f"  → vertex {v.maturity:.2f}y : {m.vertex_amounts[i]:,.2f}")

    print(f"\nVertex totals: {r.vertex_totals}")
    print(f"Portfolio VaR (1d 99%): ${r.portfolio_var:,.2f}")


if __name__ == "__main__":
    demo()
