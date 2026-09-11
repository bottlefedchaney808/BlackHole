"""Regression + stress cover for `_extract_pricing_args` zero handling.

The bug this pins: `r` and `q` were resolved with an `or` chain, so an
explicitly-supplied `0.0` (falsy) was discarded and silently replaced by a
LIVE market fetch. A non-dividend-paying name, a zero-rate scenario, or any
controlled test would be repriced against whatever the vendor happened to
return. It surfaced as the Newton-Raphson closed-loop test recovering
sigma=0.2570 from a price generated at sigma=0.2500 -- a 2.8% vol error
presented as a solved IV.
"""

import itertools
import sys
from pathlib import Path

import pytest

# APPEND, never insert(0). Options_Suite and Vol_Suite both ship an
# `expiry_selector.py` and only Vol_Suite's defines DEFAULT_A, so putting
# Options_Suite FIRST on sys.path makes Vol_Suite's flat
# `import expiry_selector` bind the wrong file -- its whole module_registry
# then fails to import and all 19 Vol_Suite modules silently disappear from
# all_modules(). Options_Suite uniquely owns `market_data`, so appending
# resolves everything this file needs without shadowing a sibling suite.
_OPTIONS_SUITE = str(Path(__file__).resolve().parent.parent)
if _OPTIONS_SUITE not in sys.path:
    sys.path.append(_OPTIONS_SUITE)
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.append(_PROJECT_ROOT)

from Options_Suite.module_registry import _extract_pricing_args, _first_set  # noqa: E402


def _market_data_module():
    """The module object `_extract_pricing_args` will import at call time."""
    import importlib

    return sys.modules.get("market_data") or importlib.import_module("market_data")

LIVE_RATE = 0.0424
LIVE_YIELD = 0.0098


class _SpyMarketData:
    """Records every live lookup and never returns the caller's own number."""

    def __init__(self):
        self.calls = []

    def validate_strike(self, ticker, strike, target_years=None, expiration_date=None):
        # Force the documented offline fallback (K = K_raw) so these tests
        # never need a listed chain.
        self.calls.append("validate_strike")
        raise RuntimeError("offline")

    def fetch_spot_price(self, ticker):
        self.calls.append("fetch_spot_price")
        return 123.45

    def fetch_risk_free_rate(self, T=None):
        self.calls.append("fetch_risk_free_rate")
        return LIVE_RATE

    def fetch_dividend_yield(self, ticker):
        self.calls.append("fetch_dividend_yield")
        return LIVE_YIELD


@pytest.fixture
def spy(monkeypatch):
    s = _SpyMarketData()
    monkeypatch.setattr(
        _market_data_module(), "MarketDataController", lambda *a, **k: s
    )
    return s


def _ctx(**over):
    base = {
        "ticker": "SPY", "spot": 550.0, "strike": 550.0, "target_years": 0.25,
        "option_type": "call",
    }
    base.update(over)
    return base


class TestFirstSet:
    def test_zero_survives(self):
        assert _first_set(0.0, 0.99) == 0.0

    def test_none_falls_through(self):
        assert _first_set(None, 0.0) == 0.0

    def test_all_none_is_none(self):
        assert _first_set(None, None) is None

    def test_order_is_respected(self):
        assert _first_set(None, 0.03, 0.07) == 0.03


class TestExplicitZerosSurvive:
    """A supplied 0.0 must be used AND must not trigger a live fetch."""

    def test_zero_dividend_yield_preserved(self, spy):
        _, _, _, _, _, q, _, _ = _extract_pricing_args(
            _ctx(risk_free_rate=0.05, dividend_yield=0.0)
        )
        assert q == 0.0
        assert "fetch_dividend_yield" not in spy.calls

    def test_zero_risk_free_rate_preserved(self, spy):
        _, _, _, _, r, _, _, _ = _extract_pricing_args(
            _ctx(risk_free_rate=0.0, dividend_yield=0.0)
        )
        assert r == 0.0
        assert "fetch_risk_free_rate" not in spy.calls

    def test_zero_via_short_aliases_preserved(self, spy):
        _, _, _, _, r, q, _, _ = _extract_pricing_args(_ctx(r=0.0, q=0.0))
        assert (r, q) == (0.0, 0.0)
        assert "fetch_risk_free_rate" not in spy.calls
        assert "fetch_dividend_yield" not in spy.calls

    def test_zero_via_focus_block_preserved(self, spy):
        ctx = {"focus": {"ticker": "SPY", "spot": 550.0, "strike": 550.0,
                         "target_years": 0.25, "risk_free_rate": 0.0,
                         "dividend_yield": 0.0}}
        _, _, _, _, r, q, _, _ = _extract_pricing_args(ctx)
        assert (r, q) == (0.0, 0.0)

    @pytest.mark.parametrize("r_in,q_in", list(itertools.product([0.0, 0.05], repeat=2)))
    def test_every_zero_combination_round_trips(self, spy, r_in, q_in):
        _, _, _, _, r, q, _, _ = _extract_pricing_args(
            _ctx(risk_free_rate=r_in, dividend_yield=q_in)
        )
        assert (r, q) == (r_in, q_in)


class TestMissingValuesStillFetch:
    """The fallback must still work -- this fix must not disable live lookup."""

    def test_absent_rate_and_yield_are_fetched(self, spy):
        _, _, _, _, r, q, _, _ = _extract_pricing_args(_ctx())
        assert r == LIVE_RATE
        assert q == LIVE_YIELD
        assert "fetch_risk_free_rate" in spy.calls
        assert "fetch_dividend_yield" in spy.calls

    def test_explicit_none_is_fetched(self, spy):
        _, _, _, _, r, q, _, _ = _extract_pricing_args(
            _ctx(risk_free_rate=None, dividend_yield=None)
        )
        assert (r, q) == (LIVE_RATE, LIVE_YIELD)


class TestNewtonRaphsonClosedLoopStress:
    """Price with Leisen-Reimer, solve with NR, demand the vol back.

    Any silent substitution of r, q, T or K breaks recovery, so this grid is
    the broad net: it fails for the whole class of bug, not just the one
    instance that was found.

    Calls and puts are split deliberately. The call path round-trips to ~1e-6.
    The American PUT path does not, and the tolerances below are measured
    behaviour, not aspiration -- see TestAmericanPutIVConditioning.
    """

    @staticmethod
    def _solve(S, K, T, r, sigma, q, is_call):
        from american_binomial import leisen_reimer_american_price

        from shared.module_registry import all_modules

        price = float(leisen_reimer_american_price(S, K, T, r, sigma, q, is_call))
        mod = next(m for m in all_modules() if m.slug == "newton_raphson_iv")
        result = mod.run({
            "ticker": "SPY", "spot": S, "strike": K, "target_years": T,
            "risk_free_rate": r, "dividend_yield": q,
            "option_type": "call" if is_call else "put",
            "market_price": price,
        })
        return price, result

    @pytest.mark.parametrize("sigma,strike,q", list(itertools.product(
        [0.12, 0.25, 0.45], [495.0, 550.0, 605.0], [0.0, 0.02],
    )))
    def test_call_solver_recovers_input_vol(self, spy, sigma, strike, q):
        _, result = self._solve(550.0, strike, 0.25, 0.05, sigma, q, True)
        assert result.status == "ok", result.metrics
        assert result.metrics["converged"] is True
        assert result.metrics["sigma"] == pytest.approx(sigma, rel=1e-3)

    @pytest.mark.parametrize("sigma,q", list(itertools.product(
        [0.12, 0.25, 0.45], [0.0, 0.02],
    )))
    def test_atm_put_recovers_input_vol(self, spy, sigma, q):
        """ATM puts round-trip to ~2e-3 -- American early-exercise tree error."""
        _, result = self._solve(550.0, 550.0, 0.25, 0.05, sigma, q, False)
        assert result.status == "ok", result.metrics
        assert result.metrics["sigma"] == pytest.approx(sigma, rel=5e-3)

    def test_zero_yield_does_not_reach_the_network(self, spy):
        self._solve(550.0, 550.0, 0.25, 0.05, 0.25, 0.0, True)
        assert "fetch_dividend_yield" not in spy.calls
        assert "fetch_risk_free_rate" not in spy.calls


class TestAmericanPutIVConditioning:
    """Documents a REAL, pre-existing defect in the American-put IV solve.

    Away from the money the put solve degrades badly while still reporting
    `converged=True`: a deep-ITM put priced at sigma=0.12 comes back as
    sigma~0.156 -- a 30% vol error presented as a converged solution. Vega
    collapses near the early-exercise boundary, so the price carries almost
    no vol information; the solver should say so instead of returning a
    confident number.

    `NewtonRaphsonIV.py` is untouched by the 2026-09-11 widget work -- this
    predates it and was found by the stress grid, not caused by it.

    Unblocking condition: make the NR solver report `converged=False` (or a
    `vega_floor` / `iv_quality` marker) when vega at the solution is below a
    usable threshold. Delete the xfail when that lands.
    """

    @pytest.mark.xfail(
        strict=True,
        reason="Deep-ITM American put IV is ill-conditioned; solver still "
               "reports converged=True with ~30% vol error. Unblocks when the "
               "NR solver gates on vega / reports iv_quality.",
    )
    def test_deep_itm_put_either_recovers_vol_or_admits_failure(self, spy):
        _, result = TestNewtonRaphsonClosedLoopStress._solve(
            550.0, 605.0, 0.25, 0.05, 0.12, 0.0, False
        )
        if result.metrics.get("converged"):
            assert result.metrics["sigma"] == pytest.approx(0.12, rel=1e-2)

    def test_deep_itm_put_error_has_not_silently_grown(self, spy):
        """Characterisation lock: pins TODAY's error so it cannot worsen unnoticed."""
        _, result = TestNewtonRaphsonClosedLoopStress._solve(
            550.0, 605.0, 0.25, 0.05, 0.12, 0.0, False
        )
        assert result.status == "ok"
        rel_err = abs(result.metrics["sigma"] - 0.12) / 0.12
        assert rel_err < 0.35, f"deep-ITM put IV error grew to {rel_err:.1%}"
