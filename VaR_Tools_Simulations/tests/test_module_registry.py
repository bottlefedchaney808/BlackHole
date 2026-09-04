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
