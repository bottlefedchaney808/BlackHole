"""Tests for hist_sim.py — Historical Simulation (Basic, Hull-White, FHS)."""

import numpy as np
import pytest
from var_engine.hist_sim import HistSimInputs, HistSimResults, run


def make_synthetic_inputs(n_tickers=8, T=504, method="basic", seed=42):
    """Build HistSimInputs with synthetic returns (no network calls)."""
    rng = np.random.default_rng(seed)
    tickers = [f"ASSET{i}" for i in range(n_tickers)]
    synth = {tk: rng.normal(0, 0.02, T) for tk in tickers}
    pos = rng.uniform(50_000, 500_000, n_tickers)
    return HistSimInputs(
        tickers=tickers,
        position_vals=pos,
        var_days=1,
        trading_days=252,
        confidence=0.95,
        method=method,
        returns_dict=synth,
    )


class TestBasicHistoricalSimulation:
    """Test basic historical simulation VaR."""

    def test_var_at_95_pct_is_approx_5th_percentile(self):
        """For basic hist sim, VaR at 95% confidence should approximate the
        5th percentile of the P&L distribution (in absolute value)."""
        inp = make_synthetic_inputs(method="basic", seed=42)
        res = run(inp)

        # VaR is defined as -cut where cut = quantile(pnl, 1-confidence)
        # So for 95%, VaR ≈ -5th percentile
        expected_var = float(-np.quantile(res.pnl_distribution, 0.05))
        assert res.var == pytest.approx(expected_var, rel=1e-10)

    def test_var_is_positive(self):
        """VaR should be a positive number (a loss measure)."""
        inp = make_synthetic_inputs(method="basic", seed=99)
        res = run(inp)
        assert res.var > 0

    def test_cvar_exceeds_var(self):
        """CVaR (expected tail loss) should be >= VaR."""
        inp = make_synthetic_inputs(method="basic", seed=123)
        res = run(inp)
        assert res.cvar >= res.var

    def test_scenario_returns_shape(self):
        """scenario_returns should be (T, n_tickers)."""
        inp = make_synthetic_inputs(method="basic", seed=42)
        res = run(inp)
        n_tickers = len(inp.tickers)
        assert res.scenario_returns.shape == (504, n_tickers)

    def test_different_seeds_produce_different_var(self):
        """Different seeds should yield (slightly) different VaRs."""
        r1 = run(make_synthetic_inputs(method="basic", seed=1))
        r2 = run(make_synthetic_inputs(method="basic", seed=2))
        assert abs(r1.var - r2.var) > 1e-6


class TestHullWhiteSimulation:
    """Test Hull-White volatility adjusted historical simulation."""

    def test_hw_runs_without_error(self):
        """HW method should produce a valid result."""
        inp = make_synthetic_inputs(method="hw", seed=42)
        res = run(inp)
        assert isinstance(res, HistSimResults)
        assert res.var > 0

    def test_hw_var_differs_from_basic(self):
        """Hull-White adjusted VaR should differ from basic VaR for
        heteroskedastic data."""
        inp_basic = make_synthetic_inputs(method="basic", seed=42)
        inp_hw = make_synthetic_inputs(method="hw", seed=42)
        r_basic = run(inp_basic)
        r_hw = run(inp_hw)
        assert r_hw.var != pytest.approx(r_basic.var, rel=1e-6)

    def test_hw_returns_garch_params(self):
        """HW method should populate garch_params for each ticker."""
        inp = make_synthetic_inputs(method="hw", seed=42)
        res = run(inp)
        for tk in inp.tickers:
            assert tk in res.garch_params
            assert "omega" in res.garch_params[tk]
            assert "alpha" in res.garch_params[tk]
            assert "beta" in res.garch_params[tk]
            assert "current_vol" in res.garch_params[tk]

    def test_hw_current_vol_is_positive(self):
        """Current_vol from GARCH fit should be positive."""
        inp = make_synthetic_inputs(method="hw", seed=42)
        res = run(inp)
        for tk, params in res.garch_params.items():
            assert params["current_vol"] > 0

    def test_net_zero_book_does_not_zero_divide(self):
        rng = np.random.default_rng(0)
        inp = HistSimInputs(
            tickers=["A", "B"],
            position_vals=np.array([100_000.0, -100_000.0]),
            method="basic",
            returns_dict={"A": rng.normal(0, 0.02, 200), "B": rng.normal(0, 0.02, 200)},
        )
        res = run(inp)
        assert res.var > 0

    def test_garch_iid_marks_boundary_not_converged(self):
        from var_engine.hist_sim import _garch_fit

        rng = np.random.default_rng(0)
        fit = _garch_fit(rng.normal(0.0, 0.01, 2000))
        if fit["persistence"] >= 0.999 - 1e-6:
            assert fit["converged"] is False
            assert fit["boundary_pinned"] is True
            assert fit["long_run_vol"] != fit["long_run_vol"]  # NaN


class TestFHSSimulation:
    """Test Filtered Historical Simulation (GARCH-standardised residuals)."""

    def test_fhs_runs_without_error(self):
        """FHS method should produce a valid result."""
        inp = make_synthetic_inputs(method="fhs", seed=42)
        res = run(inp)
        assert isinstance(res, HistSimResults)
        assert res.var > 0

    def test_fhs_scenario_returns_not_nan(self):
        """FHS scenario returns should have no NaN values."""
        inp = make_synthetic_inputs(method="fhs", seed=42)
        res = run(inp)
        assert not np.any(np.isnan(res.scenario_returns))

    def test_fhs_garch_params_present(self):
        """FHS should compute GARCH parameters for each ticker."""
        inp = make_synthetic_inputs(method="fhs", seed=42)
        res = run(inp)
        for tk in inp.tickers:
            assert tk in res.garch_params
            assert "sigma" in res.garch_params[tk]
            assert len(res.garch_params[tk]["sigma"]) > 0

    def test_fhs_var_different_from_basic(self):
        """FHS VaR should differ from basic VaR."""
        inp_basic = make_synthetic_inputs(method="basic", seed=42)
        inp_fhs = make_synthetic_inputs(method="fhs", seed=42)
        r_basic = run(inp_basic)
        r_fhs = run(inp_fhs)
        assert r_fhs.var != pytest.approx(r_basic.var, rel=1e-6)

    def test_method_property_correct(self):
        """The results should report the correct method."""
        for method in ["basic", "hw", "fhs"]:
            inp = make_synthetic_inputs(method=method, seed=42)
            res = run(inp)
            assert res.method == method


class TestEdgeCases:
    """Edge cases for historical simulation."""

    def test_single_asset(self):
        """Should work with a single ticker."""
        rng = np.random.default_rng(42)
        inp = HistSimInputs(
            tickers=["SINGLE"],
            position_vals=np.array([100_000.0]),
            var_days=1,
            confidence=0.95,
            method="basic",
            returns_dict={"SINGLE": rng.normal(0, 0.02, 252)},
        )
        res = run(inp)
        assert res.var > 0

    def test_ten_day_var_larger_than_one_day(self):
        """10-day VaR should be larger than 1-day VaR (sqrt-time scaling)."""
        inp_1d = make_synthetic_inputs(method="basic", seed=42)
        inp_10d = make_synthetic_inputs(method="basic", seed=42)
        inp_10d.var_days = 10.0
        r_1d = run(inp_1d)
        r_10d = run(inp_10d)
        assert r_10d.var > r_1d.var

    def test_higher_confidence_gives_larger_var(self):
        """99% VaR should be larger than 95% VaR."""
        inp_95 = make_synthetic_inputs(method="basic", seed=42)
        inp_99 = make_synthetic_inputs(method="basic", seed=42)
        inp_99.confidence = 0.99
        r_95 = run(inp_95)
        r_99 = run(inp_99)
        assert r_99.var > r_95.var