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

    def test_mc_heston_lsm_runs(self, monkeypatch):
        """Heston calibration fetches a live market smile from ThetaData /
        PotatoHedge -- mock that network boundary so the test runs offline,
        but keep the local Heston-under-Heston CRN greeks engine real."""
        import MCHestonLSM

        def _fake_run_heston_full(ticker, S, K, T, r, q, initial_sigma, **kwargs):
            return {
                "calib": {"v0": 0.0625, "kappa": 1.5, "theta": 0.0625, "xi": 0.3, "rho": -0.3, "rmse": 0.05},
                "price": 28.5,
            }

        monkeypatch.setattr(MCHestonLSM, "run_heston_full", _fake_run_heston_full)
        mod = _find_module("mc_heston_lsm")
        result = mod.run({
            "ticker": "SPY", "spot": 550.0, "strike": 550.0, "target_years": 0.25,
            "risk_free_rate": 0.05, "dividend_yield": 0.0, "option_type": "call", "sigma": 0.25,
            "expiry": "20261016",
        })
        assert result.status == "ok"
        assert result.metrics["model"] == "mc_heston_lsm"
        assert result.metrics["price"] == pytest.approx(28.5)  # calibration result flowed through
        assert result.metrics["calib"]["kappa"] == pytest.approx(1.5)
        # The real (non-mocked) Heston LSM greeks engine must have run: for an
        # ATM call, 0 < delta < 1, gamma > 0, and vega is a meaningful dollar
        # sensitivity, not a zero-filled placeholder.
        greeks = result.metrics["greeks"]
        assert isinstance(greeks, dict)
        assert 0.0 < greeks["delta"] < 1.0
        assert greeks["gamma"] > 0.0
        assert abs(greeks["vega"]) > 1.0

    def test_model_comparison_runs(self):
        mod = _find_module("model_comparison")
        result = mod.run({
            "ticker": "SPY", "spot": 550.0, "strike": 550.0, "target_years": 0.25,
            "risk_free_rate": 0.05, "dividend_yield": 0.0, "option_type": "call", "sigma": 0.25,
            "synthetic": True, "include_mc": False, "include_heston": False,
        })
        assert result.status == "ok"
        assert "curves" in result.metrics
        # Correctness: three independent models are wired, not aliased, and
        # they agree on magnitude while remaining numerically distinct.
        curves = result.metrics["curves"]
        assert set(curves) == {"CRR", "Leisen-Reimer", "BAW"}
        for label in curves:
            assert len(curves[label]["strikes"]) == 21
        atm_prices = [curves[label]["prices"][10] for label in curves]  # strike == spot
        assert atm_prices[0] == pytest.approx(atm_prices[1], rel=1e-2)  # CRR ~ LR magnitude
        assert len({round(p, 6) for p in atm_prices}) >= 2  # ...but not identical

    def test_newton_raphson_iv_recovers_input_volatility(self):
        """Closed-loop correctness: price with Leisen-Reimer at sigma=0.25, feed
        that price to the NR solver -- it must recover sigma ~ 0.25 and reprice
        to the original market price (converged)."""
        from american_binomial import leisen_reimer_american_price

        ctx = {
            "ticker": "SPY", "spot": 550.0, "strike": 550.0, "target_years": 0.25,
            "risk_free_rate": 0.05, "dividend_yield": 0.0, "option_type": "call",
        }
        market_price = float(leisen_reimer_american_price(550.0, 550.0, 0.25, 0.05, 0.25, 0.0, True))
        mod = _find_module("newton_raphson_iv")
        result = mod.run({**ctx, "market_price": market_price})
        assert result.status == "ok"
        assert result.metrics["converged"] is True
        assert result.metrics["sigma"] == pytest.approx(0.25, rel=1e-3)
        assert result.metrics["price"] == pytest.approx(market_price, rel=1e-3)

    def test_models_produce_different_prices(self):
        """Different pricing models should not produce identical outputs for the
        same inputs -- and they must agree on magnitude (all approximate the
        same American value), proving each is a real, independent implementation."""
        ctx = {
            "ticker": "SPY", "spot": 550.0, "strike": 550.0, "target_years": 0.25,
            "risk_free_rate": 0.05, "dividend_yield": 0.0, "option_type": "call", "sigma": 0.25,
        }
        crr = _find_module("crr").run(ctx).metrics["price"]
        baw = _find_module("baw").run(ctx).metrics["price"]
        lr = _find_module("leisen_reimer").run(ctx).metrics["price"]
        assert len({crr, baw, lr}) > 1
        # Independent binomial-tree / closed-form approximations of the same
        # American call must land near each other (correct magnitudes), even
        # though they are not bit-identical.
        assert crr == pytest.approx(lr, rel=1e-2)
        assert baw == pytest.approx(lr, rel=1e-2)
