"""Tests for copulas.py — Copula-based VaR."""

import numpy as np
import pytest
from scipy.stats import norm
from var_engine.copulas import (
    run as copula_run,
    CopulaInputs,
    CopulaResults,
    _sample_gaussian,
    _sample_student_t,
    _sample_clayton,
    _nearest_pd,
)


def make_corr_matrix(n, seed=1):
    """Generate a random valid correlation matrix."""
    rng = np.random.default_rng(seed)
    A = rng.uniform(-0.3, 0.5, (n, n))
    corr = (A + A.T) / 2
    np.fill_diagonal(corr, 1.0)
    return _nearest_pd(corr)


def make_copula_inputs(n=5, copula_type="gaussian", seed=42, confidence=0.95):
    """Build a CopulaInputs with synthetic data (no network calls)."""
    rng = np.random.default_rng(seed)
    tickers = [f"ASSET{i}" for i in range(n)]
    pos_vals = rng.uniform(50_000, 500_000, n)
    vols = np.full(n, 0.25)
    corr = make_corr_matrix(n)
    return CopulaInputs(
        tickers=tickers, position_vals=pos_vals,
        volatilities=vols, corr_matrix=corr,
        copula_type=copula_type, student_df=5.0,
        marginal_dfs=np.array([0.0] * n),
        clayton_alpha=0.7,
        var_days=10, n_sims=50_000, confidence=confidence,
        seed=seed,
    )


class TestGaussianCopulaSampler:
    """Test _sample_gaussian — the core Gaussian copula sampler."""

    def test_returns_uniform_marginals(self):
        """Each dimension should be uniform on [0, 1]."""
        n = 4
        corr = make_corr_matrix(n)
        rng = np.random.default_rng(42)
        U = _sample_gaussian(100_000, corr, rng)
        assert U.shape == (100_000, n)
        assert U.min() >= 0.0
        assert U.max() <= 1.0
        # marginal means should be ~0.5
        assert np.allclose(U.mean(axis=0), 0.5, atol=0.02)
        # marginal std should be ~sqrt(1/12) ≈ 0.289
        assert np.allclose(U.std(axis=0), np.sqrt(1 / 12), atol=0.02)

    def test_preserves_correlation_approximately(self):
        """The normal-score correlation of Gaussian copula samples should
        approximately equal the input correlation."""
        n = 3
        corr = np.array([[1.0, 0.5, 0.3],
                         [0.5, 1.0, -0.2],
                         [0.3, -0.2, 1.0]])
        rng = np.random.default_rng(42)
        U = _sample_gaussian(200_000, corr, rng)
        # Convert back to normal scores
        Z = norm.ppf(U)
        recovered_corr = np.corrcoef(Z.T)
        assert np.allclose(recovered_corr, corr, atol=0.03)


class TestStudentTCopulaSampler:
    """Test _sample_student_t — Student-T copula sampler."""

    def test_returns_uniform_marginals(self):
        """Each dimension should be uniform on [0, 1]."""
        n = 4
        corr = make_corr_matrix(n)
        rng = np.random.default_rng(42)
        U = _sample_student_t(100_000, corr, df=5.0, rng=rng)
        assert U.shape == (100_000, n)
        assert U.min() >= 0.0
        assert U.max() <= 1.0
        assert np.allclose(U.mean(axis=0), 0.5, atol=0.02)

    def test_student_t_single_asset(self):
        """Student-T copula works with single dimension."""
        corr = np.array([[1.0]])
        rng = np.random.default_rng(42)
        U = _sample_student_t(10_000, corr, df=5.0, rng=rng)
        assert U.shape == (10_000, 1)
        assert U.min() >= 0.0
        assert U.max() <= 1.0


class TestClaytonCopulaSampler:
    """Test _sample_clayton — Clayton copula sampler."""

    def test_returns_uniform_marginals(self):
        """Each dimension should be uniform on [0, 1]."""
        n = 3
        rng = np.random.default_rng(42)
        U = _sample_clayton(100_000, alpha=0.7, n=n, rng=rng)
        assert U.shape == (100_000, n)
        assert U.min() >= 0.0
        assert U.max() <= 1.0

    def test_valid_alpha_zero(self):
        """alpha -> 0 should approach independence (uniform still valid)."""
        rng = np.random.default_rng(42)
        U = _sample_clayton(50_000, alpha=0.01, n=2, rng=rng)
        assert U.min() >= 0.0
        assert U.max() <= 1.0

    def test_valid_alpha_large(self):
        """alpha > 2 should still produce valid uniforms."""
        rng = np.random.default_rng(42)
        U = _sample_clayton(50_000, alpha=5.0, n=2, rng=rng)
        assert U.min() >= 0.0
        assert U.max() <= 1.0

    def test_lower_tail_dependence(self):
        """Clayton copula with alpha > 0 should show lower-tail dependence:
        when one variable is very low, the other tends to also be low."""
        rng = np.random.default_rng(42)
        U = _sample_clayton(100_000, alpha=2.0, n=2, rng=rng)
        # When column 0 is in the bottom 5%, column 1 should more often
        # also be in the bottom 5% than under independence (5%).
        bottom = U[:, 0] < 0.05
        joint_bottom_frac = np.mean(U[bottom, 1] < 0.05)
        # Under independence this would be ~5%; with tail dependence it's higher
        assert joint_bottom_frac > 0.05


class TestCopulaRun:
    """Test the full copula VaR pipeline via run()."""

    def test_gaussian_run_returns_valid_result(self):
        """Full Gaussian copula VaR run should produce a valid result."""
        inp = make_copula_inputs(copula_type="gaussian", seed=42)
        res = copula_run(inp)
        assert isinstance(res, CopulaResults)
        assert res.var > 0
        assert res.cvar >= res.var

    def test_student_t_run_returns_valid_result(self):
        """Full Student-T copula VaR run should produce a valid result."""
        inp = make_copula_inputs(copula_type="student_t", seed=42)
        res = copula_run(inp)
        assert isinstance(res, CopulaResults)
        assert res.var > 0

    def test_clayton_run_returns_valid_result(self):
        """Full Clayton copula VaR run should produce a valid result."""
        inp = make_copula_inputs(copula_type="clayton", seed=42)
        res = copula_run(inp)
        assert isinstance(res, CopulaResults)
        assert res.var > 0

    def test_copula_type_reported_correctly(self):
        """Results should report the copula type used."""
        for ct in ["gaussian", "student_t", "clayton"]:
            inp = make_copula_inputs(copula_type=ct, seed=42)
            res = copula_run(inp)
            assert res.copula_type == ct

    def test_high_confidence_gives_higher_var(self):
        """99% VaR should be larger than 90% VaR on the same P&L
        distribution (same seed, different confidence level)."""
        from dataclasses import replace
        inp = make_copula_inputs(copula_type="gaussian", seed=42)
        r_90 = copula_run(inp)
        r_99 = copula_run(replace(inp, confidence=0.99))
        assert r_99.var > r_90.var


class TestCopulaEdgeCases:
    """Edge cases for copula samplers."""

    def test_identity_correlation_gaussian(self):
        """With identity correlation, Gaussian copula marginals should be
        nearly independent."""
        n = 4
        corr = np.eye(n)
        rng = np.random.default_rng(42)
        U = _sample_gaussian(100_000, corr, rng)
        Z = norm.ppf(U)
        recovered = np.corrcoef(Z.T)
        # Off-diagonals should be near 0
        off_diag = recovered[np.triu_indices(n, k=1)]
        assert np.all(np.abs(off_diag) < 0.02)

    def test_single_asset(self):
        """Copula with a single asset should still work."""
        n = 1
        corr = np.eye(1)
        rng = np.random.default_rng(42)
        U = _sample_gaussian(10_000, corr, rng)
        assert U.shape == (10_000, 1)
        assert U.min() >= 0.0

    def test_student_t_marginal_matches_target_vol(self):
        """Regression test: _uniform_to_returns used to scale a raw (unnormalized)
        Student-T quantile by the target vol directly. A standard Student-T(df)
        has variance df/(df-2), not 1, so a fat-tailed marginal (e.g. df=3, used
        for realistic calibrated tails) silently overstated realized vol by
        sqrt(df/(df-2)) -- ~1.73x at df=3. This checks the empirical stdev of
        simulated one-day returns for a single fat-tailed asset actually matches
        the requested annualized vol, not vol*sqrt(df/(df-2))."""
        from var_engine.copulas import _uniform_to_returns

        n_sims, df, target_vol, dt = 200_000, 3.0, 0.25, 1.0
        rng = np.random.default_rng(7)
        U = rng.uniform(1e-6, 1 - 1e-6, size=(n_sims, 1))
        marginal_dfs = np.array([df])
        vols = np.array([target_vol])

        returns = _uniform_to_returns(U, marginal_dfs, vols, dt)
        realized_vol = returns[:, 0].std()

        assert realized_vol == pytest.approx(target_vol, rel=0.03), (
            f"realized vol {realized_vol:.4f} should match target {target_vol} "
            f"-- got {realized_vol / target_vol:.3f}x, suggesting the "
            f"Student-T marginal isn't normalized to unit variance before scaling"
        )
        assert U.max() <= 1.0