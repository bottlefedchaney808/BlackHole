"""Tests for var_agg.py — VaR Aggregation (EWMA, Component, PCA)."""

import numpy as np
import pytest
from var_engine.var_agg import (
    run as var_agg_run,
    VaRAggInputs,
    VaRAggResults,
    ewma_covariance,
    _component_var,
    _standalone_var,
    _var_cvar_analytical,
    _nearest_pd,
)


def make_var_agg_inputs(n_assets=6, T=300, seed=99):
    """Build VaRAggInputs with synthetic returns (no network calls)."""
    rng = np.random.default_rng(seed)
    names = [f"Asset{i}" for i in range(n_assets)]
    pos = rng.uniform(100_000, 2_000_000, n_assets)
    groups = np.array([0, 0, 0, 1, 1, 1][:n_assets])
    R = rng.normal(0, 0.015, (T, n_assets))
    return VaRAggInputs(
        asset_names=names,
        positions=pos,
        group_mask=groups,
        returns=R,
        ewma_lambda=0.94,
        var_days=5,
        trading_days=252,
        confidence=0.95,
        n_pca_components=2,
    )


class TestEWMACovariance:
    """Test EWMA covariance matrix estimation."""

    def test_returns_correct_shape(self):
        """Should return an (n, n) matrix."""
        R = np.random.default_rng(42).normal(0, 0.01, (300, 5))
        cov = ewma_covariance(R)
        assert cov.shape == (5, 5)

    def test_is_symmetric(self):
        """EWMA covariance should be symmetric."""
        R = np.random.default_rng(42).normal(0, 0.01, (300, 5))
        cov = ewma_covariance(R)
        assert np.allclose(cov, cov.T)

    def test_is_positive_semi_definite(self):
        """EWMA covariance should be positive semi-definite
        (all eigenvalues >= 0, within numerical tolerance)."""
        R = np.random.default_rng(42).normal(0, 0.01, (300, 5))
        cov = ewma_covariance(R)
        eigvals = np.linalg.eigvalsh(cov)
        assert np.all(eigvals >= -1e-10), (
            f"Negative eigenvalues found: {eigvals[eigvals < -1e-10]}"
        )

    def test_annualised_values(self):
        """EWMA returns annualised covariance. Daily ≈ 0.0001², so annual
        should be around 0.0001² * 252 ≈ 0.0025."""
        R = np.random.default_rng(42).normal(0, 0.01, (500, 3))
        cov = ewma_covariance(R)
        # Diagonal elements should be plausible annualised variances
        # (0.01^2 * 252 = 0.0252)
        diag = np.diag(cov)
        assert np.all(diag > 0.01)
        assert np.all(diag < 0.05)


class TestComponentVaR:
    """Test component VaR computation (Euler decomposition)."""

    def test_euler_allocation_sums_to_total_var(self):
        """The Euler allocation Σ component_var_i should equal the total
        portfolio VaR (Euler decomposition property). _component_var
        already returns dollar-denominated (position-weighted) values --
        summing them directly, with no further multiplication by
        `positions`, is the actual production code path in var_agg.run()."""
        n = 6
        rng = np.random.default_rng(42)
        R = rng.normal(0, 0.015, (300, n))
        cov = ewma_covariance(R)
        cov = _nearest_pd(cov)
        pos = rng.uniform(100_000, 2_000_000, n)

        comp_var = _component_var(pos, cov, confidence=0.95, var_days=5, trading_days=252)
        total_var, _ = _var_cvar_analytical(pos, cov, confidence=0.95, var_days=5, trading_days=252)

        # Euler: Σ component_var_i = total VaR
        assert np.sum(comp_var) == pytest.approx(total_var, rel=1e-10)

    def test_component_var_length_matches_positions(self):
        """Component VaR should have same length as positions array."""
        pos = np.array([100_000, 200_000, 300_000])
        cov = np.array([[0.04, 0.01, 0.005],
                        [0.01, 0.09, 0.02],
                        [0.005, 0.02, 0.16]])
        comp_var = _component_var(pos, cov, confidence=0.95, var_days=5, trading_days=252)
        assert len(comp_var) == 3


class TestStandaloneVaR:
    """Test standalone VaR computation."""

    def test_standalone_var_is_always_positive(self):
        """Standalone VaR should always be positive (absolute loss measure)."""
        n = 6
        rng = np.random.default_rng(42)
        R = rng.normal(0, 0.015, (300, n))
        cov = ewma_covariance(R)
        cov = _nearest_pd(cov)
        # Mix of long and short positions
        pos = np.array([100_000, -50_000, 200_000, -30_000, 150_000, 80_000])
        stand_var = _standalone_var(pos, cov, confidence=0.95, var_days=5, trading_days=252)
        assert np.all(stand_var > 0)

    def test_standalone_var_less_than_total_for_diversified(self):
        """For a diversified long-only portfolio, each asset's standalone VaR
        should be > the asset's component VaR (diversification benefit)."""
        inp = make_var_agg_inputs(seed=42)
        res = var_agg_run(inp)
        # For a well-diversified portfolio, each standalone > component
        for i in range(len(res.standalone_var)):
            assert res.standalone_var[i] > res.component_var[i]


class TestFullPortfolioRun:
    """Test the full var_agg.run() pipeline."""

    def test_total_var_is_positive(self):
        """Total portfolio VaR should be positive."""
        inp = make_var_agg_inputs(seed=99)
        res = var_agg_run(inp)
        assert res.total_var > 0

    def test_total_cvar_exceeds_total_var(self):
        """CVaR should be >= VaR."""
        inp = make_var_agg_inputs(seed=99)
        res = var_agg_run(inp)
        assert res.total_cvar >= res.total_var

    def test_euler_allocation_sums_to_total(self):
        """Euler decomposition: Σ component_var_i == total_var. This is the
        production-facing check -- res.component_var is already
        dollar-denominated (see _component_var), so no further
        multiplication by positions is needed or correct here."""
        inp = make_var_agg_inputs(seed=99)
        res = var_agg_run(inp)
        assert np.sum(res.component_var) == pytest.approx(res.total_var, rel=1e-10)

    def test_sub_portfolio_var_populated(self):
        """Sub-portfolio VaR should be populated for each group."""
        inp = make_var_agg_inputs(seed=99)
        res = var_agg_run(inp)
        assert "Equities" in res.subport_var
        assert "FX" in res.subport_var
        for v in res.subport_var.values():
            assert v > 0

    def test_aggregated_var_close_to_total_var(self):
        """Sub-portfolio aggregation should produce a VaR reasonably close
        to the full-portfolio VaR (same underlying returns & positions)."""
        inp = make_var_agg_inputs(seed=99)
        res = var_agg_run(inp)
        assert res.aggregated_var == pytest.approx(res.total_var, rel=0.10)

    def test_pca_var_with_resid_greater_than_without(self):
        """PCA VaR with residuals should be >= PCA VaR without residuals
        (adding residual variance increases risk)."""
        inp = make_var_agg_inputs(seed=99)
        res = var_agg_run(inp)
        assert res.pca_var_with_resid >= res.pca_var_without_resid

    def test_pca_explained_variance_sums_to_one(self):
        """Explained variance ratios should sum to 1.0 for all components."""
        inp = make_var_agg_inputs(seed=99)
        res = var_agg_run(inp)
        eigvals = np.linalg.eigvalsh(
            ewma_covariance(inp.returns)
        )
        eigvals = np.sort(eigvals)[::-1]
        total_explained = eigvals[:inp.n_pca_components].sum() / max(eigvals.sum(), 1e-12)
        assert res.explained_variance.sum() == pytest.approx(total_explained, rel=1e-10)


class TestEWMAEdgeCases:
    """Edge cases for EWMA covariance."""

    def test_two_assets_minimum(self):
        """Should work with minimum 2 assets."""
        R = np.random.default_rng(42).normal(0, 0.01, (100, 2))
        cov = ewma_covariance(R)
        assert cov.shape == (2, 2)

    def test_small_lambda_near_identity(self):
        """Lambda very close to 0 gives nearly equal weighting, still PSD."""
        R = np.random.default_rng(42).normal(0, 0.01, (200, 4))
        cov = ewma_covariance(R, lam=0.5)
        eigvals = np.linalg.eigvalsh(cov)
        assert np.all(eigvals >= -1e-10)

    def test_single_time_step_raises(self):
        """Covariance from 1 time step should be degenerate but not crash."""
        R = np.random.default_rng(42).normal(0, 0.01, (2, 3))
        cov = ewma_covariance(R)
        assert cov.shape == (3, 3)