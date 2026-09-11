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
        """ATM puts round-trip as tightly as calls now.

        This used to allow 5e-3 and blame "American early-exercise tree
        error". It was not the tree: the solver inverted against
        Barone-Adesi-Whaley while the price came from Leisen-Reimer, so the
        gap was the model difference. With LR on both sides the measured
        error across the whole grid is 4e-05 at worst.
        """
        _, result = self._solve(550.0, 550.0, 0.25, 0.05, sigma, q, False)
        assert result.status == "ok", result.metrics
        assert result.metrics["sigma"] == pytest.approx(sigma, rel=1e-3)

    def test_zero_yield_does_not_reach_the_network(self, spy):
        self._solve(550.0, 550.0, 0.25, 0.05, 0.25, 0.0, True)
        assert "fetch_dividend_yield" not in spy.calls
        assert "fetch_risk_free_rate" not in spy.calls


class TestAmericanPutIVConditioning:
    """Deep-ITM American put IV: unidentifiable, and now reported as such.

    Found by the stress grid above, and FIXED in NewtonRaphsonIV.py. The
    option sits on the early-exercise boundary, so its price is exactly
    intrinsic for every sigma below ~0.156 -- Leisen-Reimer and
    Barone-Adesi-Whaley both return 55.00000 at 0.10, 0.12 and 0.15. There is
    no unique IV to find. The solver used to answer 0.1559 with
    converged=True (a 30% error on a 0.12 input, presented as solved); it now
    reports converged=False and the returned number is an upper bound.

    The mechanism-level tests live next to the solver in
    tests/test_iv_solvers.py::TestAmericanIVIdentifiability. These two check
    the behaviour survives all the way out through the widget adapter.
    """

    def test_deep_itm_put_is_reported_as_unconverged(self, spy):
        _, result = TestNewtonRaphsonClosedLoopStress._solve(
            550.0, 605.0, 0.25, 0.05, 0.12, 0.0, False
        )
        assert result.status == "ok"
        assert result.metrics["converged"] is False, (
            "a price pinned at intrinsic carries no vol information -- the "
            "module must not report it as a converged solve"
        )

    def test_identifiable_put_still_reports_converged(self, spy):
        """The guard must not make every put look unsolvable."""
        _, result = TestNewtonRaphsonClosedLoopStress._solve(
            550.0, 550.0, 0.25, 0.05, 0.25, 0.0, False
        )
        assert result.status == "ok"
        assert result.metrics["converged"] is True
        assert result.metrics["sigma"] == pytest.approx(0.25, rel=5e-3)
