"""Tests for Options_Suite/module_registry.py ModuleSpec wiring."""

import sys
from pathlib import Path

import pytest

# Ensure Options_Suite root is first so flat imports work, then project root.
_OPTIONS_SUITE = str(Path(__file__).resolve().parent.parent)
if _OPTIONS_SUITE not in sys.path:
    sys.path.insert(0, _OPTIONS_SUITE)
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.append(_PROJECT_ROOT)

from shared.module_registry import all_modules  # noqa: E402


def _find_module(slug: str):
    for m in all_modules():
        if m.slug == slug:
            return m
    raise ValueError(f"module {slug!r} not found in all_modules()")


class TestOptionsModuleRegistry:
    """Smoke + correctness tests for the newly-wired Options pricing module specs."""

    def test_options_modules_present(self):
        slugs = {m.slug for m in all_modules() if m.suite == "options_suite"}
        expected = {"crr", "leisen_reimer", "newton_raphson_iv", "sabr", "vanna_volga", "mc", "mc_heston_lsm", "baw", "model_comparison"}
        assert expected <= slugs

    def test_crr_runs(self):
        mod = _find_module("crr")
        result = mod.run({
            "ticker": "SPY", "spot": 550.0, "strike": 550.0, "target_years": 0.25,
            "risk_free_rate": 0.05, "dividend_yield": 0.0, "option_type": "call", "sigma": 0.25,
        })
        assert result.status == "ok"
        assert result.metrics["model"] == "crr"
        assert result.metrics["price"] > 0

    def test_leisen_reimer_is_default(self):
        mod = _find_module("leisen_reimer")
        assert mod.default_selected is True

    def test_leisen_reimer_runs(self):
        mod = _find_module("leisen_reimer")
        result = mod.run({
            "ticker": "SPY", "spot": 550.0, "strike": 550.0, "target_years": 0.25,
            "risk_free_rate": 0.05, "dividend_yield": 0.0, "option_type": "call", "sigma": 0.25,
        })
        assert result.status == "ok"
        assert result.metrics["model"] == "leisen_reimer"
        assert result.metrics["price"] > 0

    def test_newton_raphson_iv_runs(self):
        mod = _find_module("newton_raphson_iv")
        result = mod.run({
            "ticker": "SPY", "spot": 550.0, "strike": 550.0, "target_years": 0.25,
            "risk_free_rate": 0.05, "dividend_yield": 0.0, "option_type": "call", "sigma": 0.25,
            "market_price": 25.0,
        })
        assert result.status == "ok"
        assert result.metrics["model"] == "newton_raphson_iv"
        assert "sigma" in result.metrics

    def test_baw_runs(self):
        mod = _find_module("baw")
        result = mod.run({
            "ticker": "SPY", "spot": 550.0, "strike": 550.0, "target_years": 0.25,
            "risk_free_rate": 0.05, "dividend_yield": 0.0, "option_type": "call", "sigma": 0.25,
        })
        assert result.status == "ok"
        assert result.metrics["model"] == "baw"
        assert result.metrics["price"] > 0

    def test_mc_runs(self):
        mod = _find_module("mc")
        result = mod.run({
            "ticker": "SPY", "spot": 550.0, "strike": 550.0, "target_years": 0.25,
            "risk_free_rate": 0.05, "dividend_yield": 0.0, "option_type": "call", "sigma": 0.25,
            "simulations": 2000, "steps": 10,
        })
        assert result.status == "ok"
        assert result.metrics["model"] == "mc"
        assert result.metrics["price"] > 0

    def test_sabr_runs(self):
        mod = _find_module("sabr")
        result = mod.run({
            "ticker": "SPY", "spot": 550.0, "strike": 550.0, "target_years": 0.25,
            "risk_free_rate": 0.05, "dividend_yield": 0.0, "option_type": "call",
            "sabr_calibration": {"alpha": 0.3, "beta": 0.5, "rho": -0.3, "nu": 0.5},
        })
        assert result.status == "ok"
        assert result.metrics["model"] == "sabr"
        assert result.metrics["price"] > 0

    def test_vanna_volga_runs(self):
        mod = _find_module("vanna_volga")
        result = mod.run({
            "ticker": "SPY", "spot": 550.0, "strike": 550.0, "target_years": 0.25,
            "risk_free_rate": 0.05, "dividend_yield": 0.0, "option_type": "call",
            "atm_vol": 0.25, "rr25": 3.0, "bf25": 1.0,
        })
        assert result.status == "ok"
        assert result.metrics["model"] == "vanna_volga"
        assert result.metrics["price"] > 0

    def test_mc_heston_lsm_runs(self):
        mod = _find_module("mc_heston_lsm")
        result = mod.run({
            "ticker": "SPY", "spot": 550.0, "strike": 550.0, "target_years": 0.25,
            "risk_free_rate": 0.05, "dividend_yield": 0.0, "option_type": "call", "sigma": 0.25,
            "expiry": "20261016",
        })
        # heston calibration requires ThetaData; without credentials the module
        # returns a failed result. We assert the failure is graceful and the
        # module is wired correctly rather than requiring live market data.
        assert result.status in ("ok", "failed")
        if result.status == "failed":
            assert "ThetaData" in result.metrics.get("error", "") or "potatohedge" in result.metrics.get("error", "")

    def test_model_comparison_runs(self):
        mod = _find_module("model_comparison")
        result = mod.run({
            "ticker": "SPY", "spot": 550.0, "strike": 550.0, "target_years": 0.25,
            "risk_free_rate": 0.05, "dividend_yield": 0.0, "option_type": "call", "sigma": 0.25,
            "synthetic": True, "include_mc": False, "include_heston": False,
        })
        assert result.status == "ok"
        assert "curves" in result.metrics

    def test_models_produce_different_prices(self):
        """Different pricing models should not produce identical outputs for the same inputs."""
        ctx = {
            "ticker": "SPY", "spot": 550.0, "strike": 550.0, "target_years": 0.25,
            "risk_free_rate": 0.05, "dividend_yield": 0.0, "option_type": "call", "sigma": 0.25,
        }
        crr = _find_module("crr").run(ctx).metrics["price"]
        baw = _find_module("baw").run(ctx).metrics["price"]
        lr = _find_module("leisen_reimer").run(ctx).metrics["price"]
        assert len({crr, baw, lr}) > 1
