"""Tests for VaR_Tools_Simulations/module_registry.py ModuleSpec wiring."""

import numpy as np
import pytest

from shared.module_registry import all_modules


def _find_module(slug: str):
    for m in all_modules():
        if m.slug == slug:
            return m
    raise ValueError(f"module {slug!r} not found in all_modules()")


class TestVaRModuleRegistry:
    """Smoke + correctness tests for the newly-wired VaR module specs."""

    def test_var_modules_present(self):
        slugs = {m.slug for m in all_modules() if m.suite == "var_tools"}
        expected = {"hist_sim", "mc_sim", "corr_sim", "copulas", "forex_var", "cashflow_map", "stress_test", "var_agg", "hedge_optimizer", "price_dist"}
        assert expected <= slugs

    def test_corr_sim_runs_with_context(self):
        mod = _find_module("corr_sim")
        ctx = {
            "tickers": ["SPY", "AAPL"],
            "positions": [100000.0, -50000.0],
            "volatilities": [0.2, 0.3],
            "corr_matrix": [[1.0, 0.5], [0.5, 1.0]],
            "horizon_days": 10,
            "confidence": 0.99,
            "n_sims": 5000,
        }
        result = mod.run(ctx)
        assert result.status == "ok"
        assert result.metrics["var"] > 0
        assert result.metrics["cvar"] >= result.metrics["var"]

    def test_mc_sim_runs_with_context(self):
        mod = _find_module("mc_sim")
        ctx = {
            "tickers": ["SPY"],
            "positions": [100000.0],
            "volatilities": [0.2],
            "horizon_days": 10,
            "confidence": 0.99,
            "n_sims": 5000,
        }
        result = mod.run(ctx)
        assert result.status == "ok"
        assert result.metrics["var"] > 0

    def test_hist_sim_runs_with_context(self):
        mod = _find_module("hist_sim")
        rng = np.random.default_rng(42)
        tickers = ["SPY", "AAPL"]
        returns_dict = {t: rng.normal(0, 0.02, 252) for t in tickers}
        ctx = {
            "tickers": tickers,
            "positions": [100000.0, -50000.0],
            "horizon_days": 10,
            "confidence": 0.99,
            "method": "basic",
            "returns_dict": returns_dict,
        }
        result = mod.run(ctx)
        assert result.status == "ok"
        assert result.metrics["var"] > 0
        assert result.metrics["method"] == "basic"

    def test_copulas_runs_with_context(self):
        mod = _find_module("copulas")
        ctx = {
            "tickers": ["SPY", "AAPL"],
            "positions": [100000.0, -50000.0],
            "volatilities": [0.2, 0.3],
            "corr_matrix": [[1.0, 0.5], [0.5, 1.0]],
            "copula_type": "gaussian",
            "horizon_days": 10,
            "confidence": 0.99,
            "n_sims": 5000,
        }
        result = mod.run(ctx)
        assert result.status == "ok"
        assert result.metrics["var"] > 0
        assert result.metrics["copula_type"] == "gaussian"

    def test_stress_test_runs_with_context(self):
        mod = _find_module("stress_test")
        ctx = {
            "asset_names": ["SPY", "AAPL"],
            "positions": [100000.0, -50000.0],
            "base_vols": [0.2, 0.3],
            "base_corr": [[1.0, 0.5], [0.5, 1.0]],
            "vol_shocks": [0.05, 0.0],
            "corr_shocks": [[0.0, 0.1], [0.1, 0.0]],
            "horizon_days": 10,
            "confidence": 0.99,
        }
        result = mod.run(ctx)
        assert result.status == "ok"
        assert result.metrics["base_var"] > 0
        assert result.metrics["stress_var"] > 0

    def test_var_agg_runs_with_context(self):
        mod = _find_module("var_agg")
        ctx = {
            "positions": [100000.0, -50000.0, 20000.0],
            "volatilities": [0.2, 0.3, 0.25],
            "corr_matrix": [[1.0, 0.5, 0.3], [0.5, 1.0, 0.2], [0.3, 0.2, 1.0]],
            "group_mask": [0, 0, 1],
            "horizon_days": 10,
            "confidence": 0.99,
        }
        result = mod.run(ctx)
        assert result.status == "ok"
        assert result.metrics["var"] > 0

    def test_price_dist_runs_with_context(self):
        mod = _find_module("price_dist")
        ctx = {"prices": [100.0, 102.0, 101.0, 103.0, 105.0, 104.0, 106.0]}
        result = mod.run(ctx)
        assert result.status == "ok"
        assert result.metrics["annual_vol"] > 0
        assert result.metrics["n"] > 0

    def test_forex_var_runs_with_context(self):
        mod = _find_module("forex_var")
        ctx = {
            "tickers": ["SPY", "EURUSD"],
            "positions": [100000.0, 50000.0],
            "volatilities": [0.2, 0.1],
            "corr_matrix": [[1.0, 0.3], [0.3, 1.0]],
            "is_fx": [False, True],
            "horizon_days": 10,
            "confidence": 0.99,
        }
        result = mod.run(ctx)
        assert result.status == "ok"
        assert result.metrics["total_var"] > 0

    def test_cashflow_map_runs_with_context(self):
        mod = _find_module("cashflow_map")
        ctx = {
            "vertices": [
                {"maturity": 0.25, "zero_rate": 0.045, "vol": 0.0096},
                {"maturity": 0.5, "zero_rate": 0.05, "vol": 0.016},
            ],
            "cashflows": [{"maturity": 0.3, "amount": 120000.0}],
            "corr_matrix": [[1.0, 0.9], [0.9, 1.0]],
        }
        result = mod.run(ctx)
        assert result.status == "ok"
        assert result.metrics["portfolio_var"] > 0

    def test_hedge_optimizer_runs_with_context(self):
        mod = _find_module("hedge_optimizer")
        ctx = {
            "tickers": ["SPY", "AAPL"],
            "positions": [100000.0, -50000.0],
            "volatilities": [0.2, 0.3],
            "corr_matrix": [[1.0, 0.5], [0.5, 1.0]],
            "horizon_days": 10,
            "confidence": 0.99,
        }
        result = mod.run(ctx)
        assert result.status == "ok"
        assert "base_var" in result.metrics


def _run_module(slug: str, ctx: dict):
    """Run a module spec and return metrics, asserting it did not fail."""
    result = _find_module(slug).run(ctx)
    assert result.status == "ok", f"{slug} failed: {result.metrics.get('error')}"
    return result.metrics


class TestVaRModuleRegistryCorrectness:
    """Phase 2b: real numeric correctness per engine, not just 'doesn't crash'.

    Each test asserts a mathematically *correct* property of the engine's
    output (homogeneity of VaR in notional, scale invariance of price
    statistics, min-variance hedging reducing risk, stressed VaR > base VaR),
    or that two genuinely different engines produce genuinely different
    numbers -- i.e. the ModuleSpecs are not stub-aliased.  All engines are
    deterministic (fixed seed / fixed returns) so the assertions are exact
    to float precision.
    """

    _CORR_SIM_CTX = {
        "tickers": ["SPY", "AAPL"],
        "positions": [100000.0, -50000.0],
        "volatilities": [0.2, 0.3],
        "corr_matrix": [[1.0, 0.5], [0.5, 1.0]],
        "horizon_days": 10,
        "confidence": 0.99,
        "n_sims": 5000,
    }

    def _doubled_positions(self, ctx: dict) -> dict:
        return {**ctx, "positions": [2.0 * p for p in ctx["positions"]]}

    def test_corr_sim_var_is_homogeneous_in_position_size(self):
        """Doubling every position must double VaR (deterministic, seeded)."""
        base = _run_module("corr_sim", self._CORR_SIM_CTX)["var"]
        scaled = _run_module("corr_sim", self._doubled_positions(self._CORR_SIM_CTX))["var"]
        assert base > 0
        assert scaled == pytest.approx(2.0 * base, rel=1e-6)

    def test_mc_sim_var_is_homogeneous_in_position_size(self):
        base = _run_module("mc_sim", self._CORR_SIM_CTX)["var"]
        scaled = _run_module("mc_sim", self._doubled_positions(self._CORR_SIM_CTX))["var"]
        assert base > 0
        assert scaled == pytest.approx(2.0 * base, rel=1e-6)

    def test_mc_sim_differs_from_copula_var_on_identical_market_context(self):
        """Different VaR engines must not be aliases: copula (fat tails) and
        Monte-Carlo/parametric engines should disagree on the same market
        inputs, not return bit-identical figures."""
        mc = _run_module("mc_sim", self._CORR_SIM_CTX)["var"]
        copula_ctx = {**self._CORR_SIM_CTX, "copula_type": "student_t"}
        cop = _run_module("copulas", copula_ctx)["var"]
        assert mc > 0 and cop > 0
        assert cop != pytest.approx(mc, rel=1e-3)

    def test_hist_sim_var_changes_when_positions_change(self):
        """Historical-sim VaR must respond to the book: scaling positions by
        2 doubles the weighted-return quantile (same fixed returns history)."""
        rng = np.random.default_rng(42)
        returns_dict = {"SPY": rng.normal(0, 0.02, 252), "AAPL": rng.normal(0, 0.03, 252)}
        ctx = {
            "tickers": ["SPY", "AAPL"],
            "positions": [100000.0, -50000.0],
            "horizon_days": 10,
            "confidence": 0.99,
            "method": "basic",
            "returns_dict": returns_dict,
        }
        base = _run_module("hist_sim", ctx)["var"]
        scaled = _run_module("hist_sim", self._doubled_positions(ctx))["var"]
        assert base > 0
        assert scaled != pytest.approx(base, rel=1e-6)  # VaR actually changed
        assert scaled == pytest.approx(2.0 * base, rel=1e-6)

    def test_copulas_var_is_homogeneous_in_position_size(self):
        ctx = {**self._CORR_SIM_CTX, "copula_type": "student_t"}
        base = _run_module("copulas", ctx)["var"]
        scaled = _run_module("copulas", self._doubled_positions(ctx))["var"]
        assert base > 0
        assert scaled == pytest.approx(2.0 * base, rel=1e-6)

    def test_forex_var_scales_with_position_size(self):
        ctx = {
            "tickers": ["SPY", "EURUSD"],
            "positions": [100000.0, 50000.0],
            "volatilities": [0.2, 0.1],
            "corr_matrix": [[1.0, 0.3], [0.3, 1.0]],
            "is_fx": [False, True],
            "horizon_days": 10,
            "confidence": 0.99,
        }
        base = _run_module("forex_var", ctx)["total_var"]
        scaled = _run_module("forex_var", self._doubled_positions(ctx))["total_var"]
        assert base > 0
        assert scaled == pytest.approx(2.0 * base, rel=1e-6)

    def test_cashflow_map_var_is_homogeneous_in_cashflow_amount(self):
        """Doubling every mapped cash flow must double portfolio parametric VaR."""
        ctx = {
            "vertices": [
                {"maturity": 0.25, "zero_rate": 0.045, "vol": 0.0096},
                {"maturity": 0.5, "zero_rate": 0.05, "vol": 0.016},
            ],
            "cashflows": [{"maturity": 0.3, "amount": 120000.0}],
            "corr_matrix": [[1.0, 0.9], [0.9, 1.0]],
        }
        base = _run_module("cashflow_map", ctx)["portfolio_var"]
        scaled_ctx = {**ctx, "cashflows": [{"maturity": 0.3, "amount": 240000.0}]}
        scaled = _run_module("cashflow_map", scaled_ctx)["portfolio_var"]
        assert base > 0
        assert scaled == pytest.approx(2.0 * base, rel=1e-6)

    def test_stress_test_shock_raises_var_for_long_book(self):
        """Positive vol/correlation shocks must raise VaR of a long-only book."""
        ctx = {
            "asset_names": ["SPY", "AAPL"],
            "positions": [100000.0, 50000.0],
            "base_vols": [0.2, 0.3],
            "base_corr": [[1.0, 0.5], [0.5, 1.0]],
            "vol_shocks": [0.05, 0.0],
            "corr_shocks": [[0.0, 0.1], [0.1, 0.0]],
            "horizon_days": 10,
            "confidence": 0.99,
        }
        metrics = _run_module("stress_test", ctx)
        assert metrics["base_var"] > 0
        assert metrics["stress_var"] > metrics["base_var"]

    def test_var_agg_is_homogeneous_in_position_size(self):
        ctx = {
            "positions": [100000.0, -50000.0, 20000.0],
            "volatilities": [0.2, 0.3, 0.25],
            "corr_matrix": [[1.0, 0.5, 0.3], [0.5, 1.0, 0.2], [0.3, 0.2, 1.0]],
            "group_mask": [0, 0, 1],
            "horizon_days": 10,
            "confidence": 0.99,
        }
        base = _run_module("var_agg", ctx)["var"]
        scaled = _run_module("var_agg", self._doubled_positions(ctx))["var"]
        assert base > 0
        assert scaled == pytest.approx(2.0 * base, rel=1e-6)

    def test_hedge_optimizer_reduces_var(self):
        """A minimum-variance hedge must not increase risk: hedged VaR < base."""
        ctx = {
            "tickers": ["SPY", "AAPL"],
            "positions": [100000.0, -50000.0],
            "volatilities": [0.2, 0.3],
            "corr_matrix": [[1.0, 0.5], [0.5, 1.0]],
            "horizon_days": 10,
            "confidence": 0.99,
        }
        metrics = _run_module("hedge_optimizer", ctx)
        assert metrics["base_var"] > 0
        assert 0 < metrics["hedged_var"] < metrics["base_var"]

    def test_price_dist_is_scale_invariant_and_vol_monotonic(self):
        """Doubling every price leaves log-return statistics unchanged; wider
        price swings must raise measured annual volatility."""
        prices = [100.0, 102.0, 101.0, 103.0, 105.0, 104.0, 106.0]
        base = _run_module("price_dist", {"prices": prices})
        scaled = _run_module("price_dist", {"prices": [2.0 * p for p in prices]})
        assert base["annual_vol"] > 0
        assert scaled["annual_vol"] == pytest.approx(base["annual_vol"], rel=1e-9)
        assert scaled["skewness"] == pytest.approx(base["skewness"], abs=1e-9)
        assert scaled["excess_kurtosis"] == pytest.approx(base["excess_kurtosis"], abs=1e-9)
        wide = _run_module("price_dist", {"prices": [100.0, 108.0, 92.0, 110.0, 90.0, 112.0, 88.0]})
        assert wide["annual_vol"] > base["annual_vol"]
