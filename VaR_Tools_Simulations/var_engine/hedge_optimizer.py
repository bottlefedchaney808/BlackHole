"""hedge_optimizer.py — Portfolio hedge optimizer using quadratic programming.

Finds the minimum-variance hedge for a basket of positions using
futures/ETFs.  Uses quadratic programming (scipy.optimize.minimize)
for multi-instrument hedges and an analytical solution for the
single-instrument case.
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.optimize import minimize
from scipy.stats import norm


@dataclass
class HedgeInstrument:
    """A single hedge instrument (future, ETF, etc.)."""

    name: str
    volatility: float  # annualised vol
    correlation_to_positions: np.ndarray  # shape (n_positions,)
    beta: float  # beta relative to positions


@dataclass
class HedgeOptimizerInputs:
    positions: np.ndarray  # shape (n_positions,)
    cov_matrix: np.ndarray  # (n_positions, n_positions)
    hedge_instruments: list[HedgeInstrument]
    var_horizon: float = 1.0  # VaR horizon in trading days
    trading_days: float = 252.0
    confidence: float = 0.99
    # Optional diagonal approx — set True if hedges' mutual correlations are unknown
    hedge_independent: bool = True
    # (n_hedge, n_hedge) correlations BETWEEN hedge instruments. Required when
    # hedge_independent=False; ignored when True. Without this the flag had
    # nothing to build a non-diagonal hedge covariance out of, which is why it
    # used to be a silent no-op.
    hedge_corr_matrix: Optional[np.ndarray] = None


@dataclass
class HedgeOptimizerOutputs:
    optimal_weights: np.ndarray  # weights on each hedge instrument
    base_var: float  # portfolio VaR before hedging
    hedged_var: float  # portfolio VaR after hedging
    var_reduction_pct: float  # (base - hedged) / base * 100
    hedge_names: list[str]
    base_port_vol: float  # annualised portfolio vol before hedging
    hedged_port_vol: float  # annualised portfolio vol after hedging


def hedge_ratio_single(
    position_vol: float,
    position_weight: float,
    hedge_vol: float,
    correlation: float,
) -> float:
    """Analytical minimum-variance hedge ratio for a single hedge instrument.

    Parameters
    ----------
    position_vol   : annualised vol of the position(s) being hedged
    position_weight: weight of the position in the portfolio (1.0 = full)
    hedge_vol      : annualised vol of the hedge instrument
    correlation    : correlation between the position and the hedge

    Returns
    -------
    h* = -corr * (position_weight * position_vol / hedge_vol)
    """
    if hedge_vol == 0.0:
        return -np.inf * np.sign(correlation) if correlation != 0 else 0.0
    return -correlation * (position_weight * position_vol / hedge_vol)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_expanded_covariance(
    n_pos: int,
    n_hedge: int,
    cov_matrix: np.ndarray,
    instruments: list[HedgeInstrument],
    independent: bool,
    hedge_corr: Optional[np.ndarray] = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build the cross-covariance and hedge-covariance sub-blocks.

    Returns
    -------
    cross_cov : (n_hedge, n_pos)  — cov[i,j] = σ_pos_j * σ_hedge_i * ρ[i,j]
    hedge_cov : (n_hedge, n_hedge)
    pos_vols  : (n_pos,)          — sqrt(diag(cov_matrix))
    """
    pos_vols = np.sqrt(np.diag(cov_matrix))  # (n_pos,)

    cross_cov = np.zeros((n_hedge, n_pos))
    for j, instr in enumerate(instruments):
        cross_cov[j, :] = pos_vols * instr.volatility * instr.correlation_to_positions

    hedge_vols = np.array([h.volatility for h in instruments])

    if independent:
        hedge_cov = np.diag(hedge_vols**2)
    else:
        # Both branches used to be the identical diagonal, so
        # hedge_independent=False changed nothing at all -- two hedges that
        # are 95% correlated (say SPY and ES) were optimised as if they were
        # orthogonal, and the QP happily double-counted their variance
        # reduction. It now builds the real D*C*D, and REFUSES rather than
        # silently falling back to diagonal when the caller asked for
        # correlated hedges without supplying the correlations: a quiet
        # downgrade to the wrong model is what this finding was.
        if hedge_corr is None:
            raise ValueError(
                "hedge_independent=False requires hedge_corr_matrix "
                f"({n_hedge}x{n_hedge} correlations between hedge instruments). "
                "Pass it, or set hedge_independent=True to accept the "
                "diagonal approximation explicitly."
            )
        c = np.asarray(hedge_corr, dtype=float)
        if c.shape != (n_hedge, n_hedge):
            raise ValueError(
                f"hedge_corr_matrix must be {(n_hedge, n_hedge)}, got {c.shape}"
            )
        d = np.diag(hedge_vols)
        hedge_cov = d @ c @ d

    return cross_cov, hedge_cov, pos_vols


def _compute_var(
    port_variance: np.ndarray,  # scalar
    var_horizon: float,
    trading_days: float,
    confidence: float,
) -> float:
    """Parametric VaR under normal assumption."""
    port_vol = float(np.sqrt(port_variance))
    scaling = np.sqrt(var_horizon / trading_days)
    z = norm.ppf(confidence)
    return float(port_vol * scaling * z)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def min_var_hedge(inp: HedgeOptimizerInputs) -> HedgeOptimizerOutputs:
    """Portfolio minimum-variance hedge via quadratic programming.

    For a single hedge instrument this uses the analytical formula.
    For multiple instruments it solves:

        min_w  0.5 * w' Q w + c' w

    with Q = 2 * Σ_hedge  and  c = 2 * Σ_cross * w_pos.
    """
    n_pos = len(inp.positions)
    n_hedge = len(inp.hedge_instruments)

    # Normalised position weights
    total_pos = float(np.sum(inp.positions))
    if total_pos == 0.0:
        raise ValueError("Total position size is zero — cannot normalise weights.")
    pos_weights = inp.positions / total_pos  # (n_pos,)

    # Base (un-hedged) portfolio variance & VaR
    base_variance = float(pos_weights @ inp.cov_matrix @ pos_weights)
    base_var_val = _compute_var(
        base_variance, inp.var_horizon, inp.trading_days, inp.confidence
    )
    base_port_vol = float(np.sqrt(base_variance))

    # Build expanded covariance blocks
    cross_cov, hedge_cov, _ = _build_expanded_covariance(
        n_pos,
        n_hedge,
        inp.cov_matrix,
        inp.hedge_instruments,
        inp.hedge_independent,
        inp.hedge_corr_matrix,
    )

    # ---------- optimise hedge weights ----------
    if n_hedge == 0:
        optimal_weights = np.array([])
        hedged_variance = base_variance

    elif n_hedge == 1:
        # Analytical single-instrument solution
        h = inp.hedge_instruments[0]
        # cov(P, hedge) = w_pos' Σ_cross'   — cross_cov is (1, n_pos)
        cov_p_h = float((pos_weights @ cross_cov.T).item())
        optimal_weight = -cov_p_h / (h.volatility**2 + 1e-30)
        optimal_weights = np.array([optimal_weight])

        # Hedged variance
        # Var(P + w_h * H) = w' Σ w + 2 * w_h * cov(P,H) + w_h² * σ_h²
        hedged_variance = (
            base_variance
            + 2.0 * optimal_weight * cov_p_h
            + optimal_weight**2 * h.volatility**2
        )
        # Clamp: hedge_cov is treated as diagonal/independent, so the
        # analytical optimum can imply a negative variance under that
        # approximation. Floor at zero to keep downstream sqrt/VaR finite.
        hedged_variance = max(hedged_variance, 0.0)

    else:
        # Quadratic programming for multi-instrument
        # Q = 2 * Σ_hedge,  c = 2 * Σ_cross @ w_pos
        Q = 2.0 * hedge_cov
        c = 2.0 * cross_cov @ pos_weights

        def objective(w: np.ndarray) -> float:
            return float(0.5 * w @ Q @ w + c @ w)

        def jacobian(w: np.ndarray) -> np.ndarray:
            return Q @ w + c

        result = minimize(
            objective,
            x0=np.zeros(n_hedge),
            method="SLSQP",
            jac=jacobian,
        )
        if not result.success:
            raise RuntimeError(f"Hedge optimisation did not converge: {result.message}")
        optimal_weights = result.x

        # Hedged variance
        hedged_variance = (
            base_variance
            + 2.0 * float(pos_weights @ cross_cov.T @ optimal_weights)
            + float(optimal_weights @ hedge_cov @ optimal_weights)
        )
        # Clamp: hedge_cov is treated as diagonal/independent, so the QP
        # optimum can imply a negative variance under that approximation.
        # Floor at zero to keep downstream sqrt/VaR finite.
        hedged_variance = max(hedged_variance, 0.0)

    # ---------- output ----------
    hedged_var_val = _compute_var(
        hedged_variance, inp.var_horizon, inp.trading_days, inp.confidence
    )

    var_reduction = 0.0
    if abs(base_var_val) > 1e-15:
        var_reduction = (base_var_val - hedged_var_val) / abs(base_var_val) * 100.0

    return HedgeOptimizerOutputs(
        optimal_weights=optimal_weights,
        base_var=base_var_val,
        hedged_var=hedged_var_val,
        var_reduction_pct=var_reduction,
        hedge_names=[h.name for h in inp.hedge_instruments],
        base_port_vol=base_port_vol,
        hedged_port_vol=float(np.sqrt(hedged_variance)),
    )


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------


def demo():
    """Quick sanity check with two positions and one hedge."""
    rng = np.random.default_rng(42)
    n = 2
    vols = np.array([0.25, 0.30])
    corr = np.array([[1.0, 0.4], [0.4, 1.0]])
    cov = np.diag(vols) @ corr @ np.diag(vols)

    positions = np.array([1_000_000.0, 2_000_000.0])

    hedge = HedgeInstrument(
        name="ES_FUTURE",
        volatility=0.18,
        correlation_to_positions=np.array([0.6, 0.5]),
        beta=0.9,
    )

    inp = HedgeOptimizerInputs(
        positions=positions,
        cov_matrix=cov,
        hedge_instruments=[hedge],
        var_horizon=10.0,
        trading_days=252.0,
        confidence=0.99,
    )
    out = min_var_hedge(inp)
    print(f"Base VaR (10d 99%):  ${out.base_var:>10,.2f}")
    print(f"Hedged VaR (10d 99%): ${out.hedged_var:>10,.2f}")
    print(f"VaR reduction:        {out.var_reduction_pct:>9.2f}%")
    print(f"Hedge weights:        {np.round(out.optimal_weights, 6)}")
    print(f"Base port vol:        {out.base_port_vol:.4f}")
    print(f"Hedged port vol:      {out.hedged_port_vol:.4f}")


if __name__ == "__main__":
    demo()
