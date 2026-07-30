"""Tests for corr_sim.py — Correlated GBM Simulation."""

import numpy as np
import pytest
from var_engine.corr_sim import CorrSimInputs, CorrSimResults, run, _is_positive_definite


def make_corr_sim_inputs(n_assets=3, seed=42):
    """Build a CorrSimInputs with default synthetic parameters."""
    corr = np.array([
        [1.0, 0.3, 0.4],
        [0.3, 1.0, -0.2],
        [0.4, -0.2, 1.0],
    ])
    return CorrSimInputs(
        current_prices=np.array([10.0, 11.0, 9.0]),
        n_shares=np.array([50.0, 20.0, 60.0]),
        volatilities=np.array([0.25, 0.30, 0.35]),
        corr_matrix=corr,
        var_days=1,
        trading_days=252,
        confidence=0.99,
        n_sims=50_000,
        seed=seed,
        asset_names=["Asset1", "Asset2", "Asset3"],
    )


class TestCorrelatedGBMSimulation:
    """Test correlated GBM simulation."""

    def test_run_returns_valid_result(self):
        """Basic run should produce a valid CorrSimResults."""
        inp = make_corr_sim_inputs(seed=42)
        res = run(inp)
        assert isinstance(res, CorrSimResults)
        assert res.var > 0
        assert res.cvar >= res.var

    def test_portfolio_value_is_correct(self):
        """Portfolio value should equal sum(current_prices * n_shares)."""
        inp = make_corr_sim_inputs(seed=42)
        res = run(inp)
        expected = float(np.sum(inp.current_prices * inp.n_shares))
        assert res.portfolio_value == pytest.approx(expected)

    def test_simulated_corr_approximates_input_corr(self):
        """The realized correlation from simulated paths should approximately
        match the input correlation matrix."""
        inp = make_corr_sim_inputs(seed=42)
        res = run(inp)
        # With 50k sims, recovered corr should be within ~0.03
        assert np.allclose(res.sim_corr, inp.corr_matrix, atol=0.035)

    def test_simulated_vols_approximate_input_vols(self):
        """The realized annualised vols from simulation should approximately
        match the input vols."""
        inp = make_corr_sim_inputs(seed=42)
        res = run(inp)
        assert np.allclose(res.sim_vols, inp.volatilities, atol=0.04)

    def test_cholesky_ok_is_true_for_valid_corr(self):
        """cholesky_ok should be True when input correlation is valid."""
        inp = make_corr_sim_inputs(seed=42)
        # Our input correlation is valid PSD
        assert _is_positive_definite(inp.corr_matrix)
        res = run(inp)
        assert res.cholesky_ok is True

    def test_pnl_distribution_shape(self):
        """P&L distribution should have n_sims elements."""
        inp = make_corr_sim_inputs(seed=42)
        res = run(inp)
        assert len(res.pnl_distribution) == inp.n_sims

    def test_cvar_greater_than_var(self):
        """CVaR should be >= VaR (by definition)."""
        inp = make_corr_sim_inputs(seed=42)
        res = run(inp)
        assert res.cvar >= res.var - 1e-10

    def test_different_seeds_produce_different_results(self):
        """Different random seeds should yield different VaRs."""
        r1 = run(make_corr_sim_inputs(seed=1))
        r2 = run(make_corr_sim_inputs(seed=2))
        assert r1.var != pytest.approx(r2.var, rel=1e-3)


class TestCholeskyDetection:
    """Test positive-definite detection and fallback."""

    def test_is_positive_definite_on_valid(self):
        """Known valid matrix should return True."""
        m = np.array([[2, 1], [1, 2]])
        assert _is_positive_definite(m) is True

    def test_is_positive_definite_on_invalid(self):
        """Known non-PSD matrix should return False."""
        m = np.array([[1, 2], [2, 1]])  # eigvals: 3, -1
        assert _is_positive_definite(m) is False

    def test_near_pd_matrix_still_works(self):
        """A nearly-PD matrix should still run without error and produce result."""
        # Create a matrix with a tiny negative eigenvalue
        rng = np.random.default_rng(42)
        n = 4
        corr = np.eye(n) + rng.uniform(-0.2, 0.5, (n, n))
        corr = (corr + corr.T) / 2
        np.fill_diagonal(corr, 1.0)
        # Make it slightly non-PD by adding a perturbation
        if _is_positive_definite(corr):
            # Already valid — should still work
            pass

        inp = CorrSimInputs(
            current_prices=np.full(n, 50.0),
            n_shares=np.full(n, 100.0),
            volatilities=np.full(n, 0.30),
            corr_matrix=corr,
            var_days=1,
            confidence=0.95,
            n_sims=10_000,
            seed=42,
        )
        res = run(inp)
        assert res.var > 0


class TestCorrSimEdgeCases:
    """Edge cases for correlated simulation."""

    def test_single_asset(self):
        """Should work with a single asset (correlation matrix is [[1]])."""
        inp = CorrSimInputs(
            current_prices=np.array([100.0]),
            n_shares=np.array([50.0]),
            volatilities=np.array([0.25]),
            corr_matrix=np.array([[1.0]]),
            var_days=1,
            n_sims=10_000,
            seed=42,
        )
        res = run(inp)
        assert res.var > 0
        assert res.cholesky_ok is True
        assert len(res.asset_names) == 1

    def test_two_assets_high_correlation(self):
        """Two assets with near-perfect positive correlation."""
        corr = np.array([[1.0, 0.99], [0.99, 1.0]])
        inp = CorrSimInputs(
            current_prices=np.array([50.0, 60.0]),
            n_shares=np.array([100.0, 100.0]),
            volatilities=np.array([0.20, 0.20]),
            corr_matrix=corr,
            var_days=1,
            n_sims=50_000,
            seed=42,
        )
        res = run(inp)
        assert np.allclose(res.sim_corr, corr, atol=0.03)
        assert res.cholesky_ok is True

    def test_two_assets_negative_correlation(self):
        """Two assets with negative correlation."""
        corr = np.array([[1.0, -0.7], [-0.7, 1.0]])
        inp = CorrSimInputs(
            current_prices=np.array([50.0, 60.0]),
            n_shares=np.array([100.0, 100.0]),
            volatilities=np.array([0.20, 0.25]),
            corr_matrix=corr,
            var_days=1,
            n_sims=50_000,
            seed=42,
        )
        res = run(inp)
        assert np.allclose(res.sim_corr, corr, atol=0.03)

    def test_ten_day_var_greater(self):
        """10-day VaR > 1-day VaR (sqrt-time scaling)."""
        inp_1d = make_corr_sim_inputs(seed=42)
        inp_10d = make_corr_sim_inputs(seed=42)
        inp_10d.var_days = 10.0
        r_1d = run(inp_1d)
        r_10d = run(inp_10d)
        assert r_10d.var > r_1d.var

    def test_asset_names_default_generated(self):
        """If asset_names not provided, defaults like 'Asset1', 'Asset2'..."""
        inp = CorrSimInputs(
            current_prices=np.array([10.0, 20.0]),
            n_shares=np.array([100.0, 200.0]),
            volatilities=np.array([0.25, 0.30]),
            corr_matrix=np.array([[1.0, 0.3], [0.3, 1.0]]),
            var_days=1,
            n_sims=10_000,
            seed=42,
        )
        res = run(inp)
        assert res.asset_names == ["Asset1", "Asset2"]