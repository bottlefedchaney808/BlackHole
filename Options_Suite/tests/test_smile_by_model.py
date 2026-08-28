"""Tests for smile_by_model.py -- single-expiry, multi-model IV smile
builder for the Surface Explorer tool's `iv_smile_by_model` mode.

Network-free: SABR/Heston calibration is never actually run here. The two
real seams to the suite's own machinery
(_import_options_main / _import_chain_evaluation / _import_vol_manager_cls)
are monkeypatched with fakes, same pattern
Tools/tests/test_surface_explorer_tool.py uses for surface_grids.
"""

import numpy as np
import pytest
import smile_by_model as sbm

SPOT = 100.0
EXPIRY = "20261016"


class _FakeTD:
    def __init__(self, spot=SPOT, expirations=None, strikes=None, r=0.04):
        self._spot = spot
        self._expirations = expirations if expirations is not None else [EXPIRY]
        self._strikes = (
            strikes if strikes is not None else [90.0, 95.0, 100.0, 105.0, 110.0]
        )
        self._r = r

    def fetch_spot_price(self, ticker):
        return self._spot

    def list_expirations(self, ticker):
        return list(self._expirations)

    def list_strikes(self, ticker, expiry):
        return list(self._strikes)

    def fetch_risk_free_rate(self, T):
        return self._r

    def fetch_dividend_yield(self, ticker):
        return 0.0


class _FakeVolManager:
    def __init__(self):
        self.last_sabr_calibration = None


class _FakeOptionsMain:
    """Stand-in for the loaded Options_Suite/main.py module. Records
    whether get_safe_float was ever invoked with an input() call (it must
    never be -- that's the whole point of the monkeypatch under test) and
    restores cleanly."""

    def __init__(self, models=None, gather_exc=None):
        self.get_safe_float = self._real_get_safe_float
        self._models = (
            models
            if models is not None
            else {
                "CRR": {"price": 1.0, "sigma": 0.2},
                "Leisen-Reimer": {"price": 1.0, "sigma": 0.2},
            }
        )
        self._gather_exc = gather_exc
        self.gather_calls = []

        class _PC:
            simulations = 1000
            steps = 50
            heston_compare_sims = 100
            heston_compare_steps = 10

        self.PricingConfig = _PC

    def _real_get_safe_float(self, prompt, default):
        raise AssertionError(
            "get_safe_float() must never be called for real from a headless "
            "builder -- it calls input() and would hang."
        )

    def _gather_all_models_and_market(
        self,
        ticker,
        S,
        K,
        T,
        r,
        q,
        option_type,
        is_call,
        resolved_exp,
        vol_manager,
        pricing_config,
    ):
        # Assert the monkeypatch is active for the duration of this call.
        assert self.get_safe_float("x", 1.23) == 1.23
        self.gather_calls.append(
            {
                "ticker": ticker,
                "S": S,
                "K": K,
                "T": T,
                "r": r,
                "q": q,
                "option_type": option_type,
                "is_call": is_call,
                "resolved_exp": resolved_exp,
            }
        )
        if self._gather_exc:
            raise self._gather_exc
        return {
            "market_exp": resolved_exp,
            "models": dict(self._models),
            "rr25": 0.01,
            "bf25": 0.005,
            "atm_vol_vv": 0.22,
        }


def _install(
    monkeypatch, options_main=None, comparison=None, models=None, gather_exc=None
):
    fake_main = options_main or _FakeOptionsMain(models=models, gather_exc=gather_exc)
    monkeypatch.setattr(sbm, "_import_options_main", lambda: fake_main)
    monkeypatch.setattr(sbm, "_import_vol_manager_cls", lambda: _FakeVolManager)

    calls = []

    class _FakeChainEval:
        def build_smile_comparison(
            self,
            ticker,
            market_exp,
            S,
            K,
            T,
            r,
            q,
            models,
            vol_manager,
            atm_vol_vv,
            rr25,
            bf25,
        ):
            calls.append(
                dict(
                    ticker=ticker,
                    market_exp=market_exp,
                    S=S,
                    K=K,
                    T=T,
                    r=r,
                    q=q,
                    models=dict(models),
                    atm_vol_vv=atm_vol_vv,
                    rr25=rr25,
                    bf25=bf25,
                )
            )
            if comparison is _MISSING:
                return _default_comparison(models)
            return comparison

    monkeypatch.setattr(sbm, "_import_chain_evaluation", lambda: _FakeChainEval())
    return fake_main, calls


_MISSING = object()


def _default_comparison(models):
    curves = {}
    if "CRR" in models:
        curves["CRR"] = (np.array([90.0, 100.0, 110.0]), np.array([0.21, 0.20, 0.22]))
    if "Leisen-Reimer" in models:
        curves["Leisen-Reimer"] = (np.array([90.0, 100.0]), np.array([0.205, 0.198]))
    if "MC" in models:
        curves["MC"] = (np.array([90.0, 100.0, 110.0]), np.array([0.19, 0.20, 0.21]))
    if "Heston" in models:
        curves["Heston"] = (
            np.array([90.0, 100.0, 110.0]),
            np.array([0.18, 0.19, 0.20]),
        )
    return {
        "market_strikes": np.array([90.0, 100.0, 110.0]),
        "market_ivs": np.array([0.22, 0.21, 0.23]),
        "market_sources": ["vendor", "vendor", "vendor"],
        "market_rights": ["P", "C", "C"],
        "curves": curves,
        "flats": {},
    }


@pytest.mark.unit
def test_missing_ticker_raises(monkeypatch):
    _install(monkeypatch, comparison=_MISSING)
    with pytest.raises(ValueError, match="requires a ticker"):
        sbm.build_iv_smile_by_model("", td=_FakeTD())


@pytest.mark.unit
def test_no_spot_raises(monkeypatch):
    _install(monkeypatch, comparison=_MISSING)
    with pytest.raises(ValueError, match="no usable spot"):
        sbm.build_iv_smile_by_model("AAPL", td=_FakeTD(spot=0.0))


@pytest.mark.unit
def test_no_listed_expiries_raises(monkeypatch):
    _install(monkeypatch, comparison=_MISSING)
    with pytest.raises(ValueError, match="no listed expiries"):
        sbm.build_iv_smile_by_model("AAPL", td=_FakeTD(expirations=[]))


@pytest.mark.unit
def test_comparison_failure_raises(monkeypatch):
    _install(monkeypatch, comparison=None)  # build_smile_comparison -> None
    with pytest.raises(ValueError, match="could not build a smile comparison"):
        sbm.build_iv_smile_by_model("AAPL", td=_FakeTD())


@pytest.mark.unit
def test_basic_build_returns_curves_and_market(monkeypatch):
    fake_main, calls = _install(monkeypatch, comparison=_MISSING)
    result = sbm.build_iv_smile_by_model("aapl", td=_FakeTD())
    assert result["ticker"] == "AAPL"
    assert result["expiry"] == EXPIRY
    assert result["spot"] == SPOT
    assert set(result["curves"]) == {"CRR", "Leisen-Reimer"}
    assert result["curves"]["CRR"]["strikes"] == [90.0, 100.0, 110.0]
    assert result["market"]["strikes"] == [90.0, 100.0, 110.0]
    assert result["meta"]["models_present"] == ["CRR", "Leisen-Reimer"]
    assert len(calls) == 1
    # get_safe_float was restored after the call (not left monkeypatched):
    # calling it now should hit the real (input()-raising) implementation
    # again, not the lambda substituted for the duration of the call.
    with pytest.raises(AssertionError, match="must never be called"):
        fake_main.get_safe_float("x", 1.0)


@pytest.mark.unit
def test_curve_with_too_few_points_is_absent_not_fabricated(monkeypatch):
    """build_smile_comparison itself is the thing that drops <3-point
    curves (see chain_evaluation._solve_chain/_solve_chain_batch) -- this
    test just confirms build_iv_smile_by_model passes that through
    faithfully rather than filling in a fabricated curve."""

    def comparison_with_gap(models):
        c = _default_comparison(models)
        c["curves"].pop("Leisen-Reimer", None)  # simulate <3 usable points
        return c

    fake_main, calls = _install(monkeypatch, comparison=_MISSING)
    # swap in a comparison variant that drops Leisen-Reimer
    monkeypatch.setattr(
        sbm,
        "_import_chain_evaluation",
        lambda: type(
            "X",
            (),
            {
                "build_smile_comparison": staticmethod(
                    lambda *a, **kw: comparison_with_gap(a[7])
                )
            },
        )(),
    )
    result = sbm.build_iv_smile_by_model("AAPL", td=_FakeTD())
    assert "Leisen-Reimer" not in result["curves"]
    assert "CRR" in result["curves"]


@pytest.mark.unit
def test_nan_inf_scrubbed(monkeypatch):
    def comparison_with_nan(models):
        return {
            "market_strikes": np.array([90.0, 100.0]),
            "market_ivs": np.array([0.2, float("nan")]),
            "market_sources": ["vendor", "vendor"],
            "market_rights": ["P", "C"],
            "curves": {
                "CRR": (np.array([90.0, 100.0]), np.array([float("inf"), 0.2])),
            },
            "flats": {},
        }

    monkeypatch.setattr(
        sbm,
        "_import_options_main",
        lambda: _FakeOptionsMain(),
    )
    monkeypatch.setattr(sbm, "_import_vol_manager_cls", lambda: _FakeVolManager)
    monkeypatch.setattr(
        sbm,
        "_import_chain_evaluation",
        lambda: type(
            "X",
            (),
            {
                "build_smile_comparison": staticmethod(
                    lambda *a, **kw: comparison_with_nan(a[7])
                )
            },
        )(),
    )
    result = sbm.build_iv_smile_by_model("AAPL", td=_FakeTD())
    assert result["market"]["ivs"] == [0.2, None]
    assert result["curves"]["CRR"]["ivs"] == [None, 0.2]
    import json

    raw = json.dumps(result)
    assert "NaN" not in raw and "Infinity" not in raw


@pytest.mark.unit
def test_include_mc_false_drops_mc_curve(monkeypatch):
    models = {
        "CRR": {"price": 1.0, "sigma": 0.2},
        "MC": {"price": 1.0, "sigma": 0.2},
    }
    fake_main, calls = _install(monkeypatch, comparison=_MISSING, models=models)
    result = sbm.build_iv_smile_by_model(
        "AAPL",
        td=_FakeTD(),
        include_mc=False,
    )
    assert "MC" not in result["curves"]
    assert "CRR" in result["curves"]
    assert result["meta"]["include_mc"] is False
    # models dict passed into build_smile_comparison must not carry MC.
    assert "MC" not in calls[0]["models"]


@pytest.mark.unit
def test_include_heston_false_drops_heston_curve(monkeypatch):
    models = {
        "CRR": {"price": 1.0, "sigma": 0.2},
        "Heston": {"price": 1.0, "greeks": {}, "calib": {"v0": 0.04}},
    }
    fake_main, calls = _install(monkeypatch, comparison=_MISSING, models=models)
    result = sbm.build_iv_smile_by_model(
        "AAPL",
        td=_FakeTD(),
        include_heston=False,
    )
    assert "Heston" not in result["curves"]
    assert result["meta"]["include_heston"] is False
    assert "Heston" not in calls[0]["models"]


@pytest.mark.unit
def test_default_both_included(monkeypatch):
    models = {
        "CRR": {"price": 1.0, "sigma": 0.2},
        "MC": {"price": 1.0, "sigma": 0.2},
        "Heston": {"price": 1.0, "greeks": {}, "calib": {"v0": 0.04}},
    }
    _install(monkeypatch, comparison=_MISSING, models=models)
    result = sbm.build_iv_smile_by_model("AAPL", td=_FakeTD())
    assert {"CRR", "MC", "Heston"} <= set(result["curves"])
    assert result["meta"]["include_mc"] is True
    assert result["meta"]["include_heston"] is True


@pytest.mark.unit
def test_explicit_strike_and_expiry_used(monkeypatch):
    fake_main, calls = _install(monkeypatch, comparison=_MISSING)
    result = sbm.build_iv_smile_by_model(
        "AAPL",
        td=_FakeTD(),
        expiry="2026-10-16",
        strike=103.5,
    )
    assert result["strike"] == 103.5
    assert result["expiry"] == "20261016"
    assert fake_main.gather_calls[0]["K"] == 103.5
    assert fake_main.gather_calls[0]["resolved_exp"] == "20261016"


@pytest.mark.unit
def test_atm_strike_snaps_to_nearest_listed(monkeypatch):
    fake_main, calls = _install(monkeypatch, comparison=_MISSING)
    td = _FakeTD(spot=101.3, strikes=[90.0, 95.0, 100.0, 105.0, 110.0])
    result = sbm.build_iv_smile_by_model("AAPL", td=td)
    assert result["strike"] == 100.0  # nearest listed strike to 101.3


@pytest.mark.unit
def test_put_option_type_normalized(monkeypatch):
    fake_main, calls = _install(monkeypatch, comparison=_MISSING)
    result = sbm.build_iv_smile_by_model("AAPL", td=_FakeTD(), option_type="PUT")
    assert result["option_type"] == "put"
    assert fake_main.gather_calls[0]["option_type"] == "put"
    assert fake_main.gather_calls[0]["is_call"] is False


@pytest.mark.unit
def test_result_is_json_serializable(monkeypatch):
    _install(monkeypatch, comparison=_MISSING)
    result = sbm.build_iv_smile_by_model("AAPL", td=_FakeTD())
    import json

    raw = json.dumps(result)
    assert "NaN" not in raw and "Infinity" not in raw
