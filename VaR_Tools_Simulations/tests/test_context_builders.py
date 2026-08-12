"""Vol/drift resolution + data_quality reporting in the context-mode builders.

These cover the resolution *order* the sim builders promise: a Vol_Suite-computed
GARCH vol carried in suite_context wins; otherwise VaR fits its own GARCH; only
when both are unavailable does a fixed 0.25 stand in -- and whichever branch was
taken is reported in the result's `data_quality` block rather than being silently
indistinguishable from a real estimate.
"""

import importlib

import main as var_main
import numpy as np
import pytest
from var_engine import data_loader


def _base_payload(garch_vol=None, basket=None):
    payload = {"focus": {"ticker": "AAPL", "garch_conditional_vol": garch_vol},
               "ticker": "AAPL"}
    if basket is not None:
        payload["basket"] = basket
    return payload


@pytest.fixture
def stub_market(monkeypatch):
    """Live-data stubs shared by every case; each test overrides what it varies."""
    monkeypatch.setattr(data_loader, "fetch_spot", lambda tk: 200.0)
    monkeypatch.setattr(data_loader, "estimate_garch_vol", lambda tk: 0.40)
    monkeypatch.setattr(data_loader, "estimate_geometric_return", lambda tk: 0.10)
    return monkeypatch


# ── mc_sim ────────────────────────────────────────────────────────────────

def test_mc_sim_prefers_context_garch_vol(stub_market):
    # estimate_garch_vol returns 0.40 and must NOT be used
    result = var_main._build_mc_sim_from_context(_base_payload(garch_vol=0.22), "AAPL")
    assert result["vol"] == pytest.approx(0.22)
    assert result["data_quality"]["vol_source"] == "context"
    assert result["data_quality"]["expected_return_source"] == "computed"


def test_mc_sim_falls_back_to_garch_fit_when_context_vol_missing(stub_market):
    stub_market.setattr(data_loader, "estimate_garch_vol", lambda tk: 0.35)
    result = var_main._build_mc_sim_from_context(_base_payload(garch_vol=None), "AAPL")
    assert result["vol"] == pytest.approx(0.35)
    assert result["data_quality"]["vol_source"] == "garch_fit"


def test_mc_sim_falls_back_to_default_when_both_missing(stub_market):
    stub_market.setattr(data_loader, "estimate_garch_vol", lambda tk: None)
    stub_market.setattr(data_loader, "estimate_geometric_return", lambda tk: None)
    result = var_main._build_mc_sim_from_context(_base_payload(garch_vol=None), "AAPL")
    assert result["vol"] == pytest.approx(0.25)
    assert result["data_quality"]["vol_source"] == "fallback"
    assert result["data_quality"]["expected_return_source"] == "unavailable"
    assert result["expected_return"] == pytest.approx(0.0)


def test_mc_sim_ignores_non_positive_context_vol(stub_market):
    """A zero/negative context vol is not a usable estimate -- fall through."""
    stub_market.setattr(data_loader, "estimate_garch_vol", lambda tk: 0.31)
    result = var_main._build_mc_sim_from_context(_base_payload(garch_vol=0.0), "AAPL")
    assert result["vol"] == pytest.approx(0.31)
    assert result["data_quality"]["vol_source"] == "garch_fit"


# ── copula ────────────────────────────────────────────────────────────────

def test_copula_prefers_context_garch_vol(stub_market):
    result = var_main._build_copula_from_context(_base_payload(garch_vol=0.22), "AAPL")
    assert result["vol"] == pytest.approx(0.22)
    assert result["data_quality"]["vol_source"] == "context"


def test_copula_falls_back_to_default_when_both_missing(stub_market):
    stub_market.setattr(data_loader, "estimate_garch_vol", lambda tk: None)
    stub_market.setattr(data_loader, "estimate_geometric_return", lambda tk: None)
    result = var_main._build_copula_from_context(_base_payload(garch_vol=None), "AAPL")
    assert result["vol"] == pytest.approx(0.25)
    assert result["data_quality"]["vol_source"] == "fallback"
    assert result["data_quality"]["expected_return_source"] == "unavailable"
    assert result["expected_return"] == pytest.approx(0.0)


# ── corr_sim (focus ticker only takes the context vol; peers refit) ───────

@pytest.fixture
def capture_corr_inputs(monkeypatch):
    """Record the CorrSimInputs the builder assembles, so the per-ticker vol
    vector can be asserted on directly rather than inferred from sim output."""
    # `from var_engine import corr_sim` yields var_engine's re-exported `corr_sim`
    # *function*, not the module -- import the module explicitly.
    corr_sim = importlib.import_module("var_engine.corr_sim")

    seen = {}
    real_run = corr_sim.run

    def spy(inputs):
        seen['inputs'] = inputs
        return real_run(inputs)

    monkeypatch.setattr(corr_sim, "run", spy)
    return seen


def test_corr_sim_uses_context_vol_for_focus_only(stub_market, capture_corr_inputs):
    stub_market.setattr(data_loader, "estimate_garch_vol",
                        lambda tk: {"AAPL": 0.40, "MSFT": 0.30}.get(tk))
    stub_market.setattr(data_loader, "default_date_range", lambda: ("2024-01-01", "2024-12-31"))
    stub_market.setattr(data_loader, "fetch_log_returns",
                        lambda tk, s, e: np.zeros(5))  # too short -> identity corr

    payload = _base_payload(garch_vol=0.22, basket={"tickers": ["AAPL", "MSFT"]})
    result = var_main._build_corr_sim_peer_from_context(payload, "AAPL")

    assert result["tickers"] == ["AAPL", "MSFT"]
    # focus takes the context vol; the peer still refits its own GARCH
    assert list(capture_corr_inputs['inputs'].volatilities) == pytest.approx([0.22, 0.30])
    assert result["data_quality"]["vol_source"] == "context"


def test_corr_sim_peer_without_garch_fit_uses_default(stub_market, capture_corr_inputs):
    stub_market.setattr(data_loader, "estimate_garch_vol",
                        lambda tk: 0.35 if tk == "AAPL" else None)
    stub_market.setattr(data_loader, "default_date_range", lambda: ("2024-01-01", "2024-12-31"))
    stub_market.setattr(data_loader, "fetch_log_returns",
                        lambda tk, s, e: np.zeros(5))

    payload = _base_payload(garch_vol=None, basket={"tickers": ["AAPL", "MSFT"]})
    result = var_main._build_corr_sim_peer_from_context(payload, "AAPL")

    assert list(capture_corr_inputs['inputs'].volatilities) == pytest.approx([0.35, 0.25])
    assert result["data_quality"]["vol_source"] == "garch_fit"


# ── corr_sim horizon defaulting ───────────────────────────────────────────
# `var.horizon_days` used to be mandatory, so a context that only carried a
# confidence level aborted the whole module. It now defaults to a 1-year
# (252 trading day) horizon, matching the MC sim, and an explicit top-level
# `corr_sim_days` overrides whatever `var.horizon_days` says.

@pytest.fixture
def stub_live_price(monkeypatch):
    """`live_price` lives on main itself, not on data_loader."""
    monkeypatch.setattr(var_main, "live_price", lambda tk: 100.0)
    return monkeypatch


def _corr_payload(**extra):
    payload = {"ticker": "AAPL", "weights": [1.0], "var": {"confidence": 0.99}}
    payload.update(extra)
    return payload


def test_corr_sim_defaults_to_252_days_when_horizon_omitted(stub_live_price):
    result = var_main._build_corr_sim_from_context(_corr_payload())
    assert result["horizon_days"] == 252


def test_corr_sim_uses_var_horizon_days_when_given(stub_live_price):
    payload = _corr_payload(var={"confidence": 0.99, "horizon_days": 10})
    result = var_main._build_corr_sim_from_context(payload)
    assert result["horizon_days"] == 10


def test_corr_sim_respects_explicit_corr_sim_days_override(stub_live_price):
    payload = _corr_payload(var={"confidence": 0.99, "horizon_days": 10},
                            corr_sim_days=30)
    result = var_main._build_corr_sim_from_context(payload)
    assert result["horizon_days"] == 30


def test_corr_sim_rejects_non_positive_horizon(stub_live_price):
    with pytest.raises(var_main.ContextModeError):
        var_main._build_corr_sim_from_context(_corr_payload(corr_sim_days=0))


def test_corr_sim_rejects_non_integer_horizon(stub_live_price):
    with pytest.raises(var_main.ContextModeError):
        var_main._build_corr_sim_from_context(_corr_payload(corr_sim_days="ten"))


# ── price_dist (new context-mode builder) ─────────────────────────────────
# price_dist.py had no context-mode builder at all, so its lognormal table and
# MC probability engine were unreachable outside the interactive menu.

def test_price_dist_builder_returns_distribution_table_and_histogram(stub_market):
    result = var_main._build_price_dist_from_context(_base_payload(garch_vol=0.30), "AAPL")
    assert result["status"] == "ok"
    assert result["module"] == "price_dist_1yr"
    assert result["ticker"] == "AAPL"
    assert result["spot"] == pytest.approx(200.0)
    assert result["vol"] == pytest.approx(0.30)
    assert len(result["distribution_table"]) > 0
    row = result["distribution_table"][0]
    assert set(row) == {"price", "prob_at", "prob_below", "prob_above"}
    assert len(result["terminal_price_histogram"]) > 0
    assert sum(b["count"] for b in result["terminal_price_histogram"]) == result["n_sims"]
    assert result["avg_end_price"] > 0


def test_price_dist_builder_reports_vol_and_drift_sources(stub_market):
    stub_market.setattr(data_loader, "estimate_garch_vol", lambda tk: None)
    stub_market.setattr(data_loader, "estimate_geometric_return", lambda tk: None)
    result = var_main._build_price_dist_from_context(_base_payload(garch_vol=None), "AAPL")
    assert result["vol"] == pytest.approx(0.25)
    assert result["data_quality"]["vol_source"] == "fallback"
    assert result["data_quality"]["expected_return_source"] == "unavailable"


def test_price_dist_builder_rejects_unfetchable_spot(stub_market):
    stub_market.setattr(data_loader, "fetch_spot", lambda tk: 0.0)
    with pytest.raises(var_main.ContextModeError):
        var_main._build_price_dist_from_context(_base_payload(garch_vol=0.30), "AAPL")


def test_price_dist_builder_is_json_serializable(stub_market):
    import json
    raw = json.dumps(var_main._build_price_dist_from_context(_base_payload(garch_vol=0.30), "AAPL"))
    assert "NaN" not in raw and "Infinity" not in raw


# ── terminal_price_histogram on every sim builder ─────────────────────────
# Task 11's renderer draws the same histogram for all four builders, so each
# one must expose the same field, with every simulated draw accounted for.

def _assert_histogram(hist, n_sims):
    assert len(hist) == 20
    for b in hist:
        assert set(b) == {"low", "high", "count"}
        assert b["high"] >= b["low"]
        assert isinstance(b["count"], int)
    assert sum(b["count"] for b in hist) == n_sims


def test_histogram_bins_accounts_for_every_value():
    hist = var_main._histogram_bins(np.array([1.0, 2.0, 3.0, 4.0]), n_bins=4)
    assert len(hist) == 4
    assert sum(b["count"] for b in hist) == 4
    assert hist[0]["low"] == pytest.approx(1.0)
    assert hist[-1]["high"] == pytest.approx(4.0)


def test_mc_sim_exposes_terminal_price_histogram(stub_market):
    result = var_main._build_mc_sim_from_context(_base_payload(garch_vol=0.22), "AAPL")
    _assert_histogram(result["terminal_price_histogram"], result["n_sims"])
    assert result["n_sims"] == 10_000


def test_copula_exposes_terminal_price_histogram(stub_market):
    result = var_main._build_copula_from_context(_base_payload(garch_vol=0.22), "AAPL")
    _assert_histogram(result["terminal_price_histogram"], result["n_sims"])
    assert result["n_sims"] == 50_000


def test_corr_sim_peer_exposes_terminal_portfolio_histogram(stub_market, capture_corr_inputs):
    stub_market.setattr(data_loader, "estimate_garch_vol", lambda tk: 0.30)
    stub_market.setattr(data_loader, "default_date_range", lambda: ("2024-01-01", "2024-12-31"))
    stub_market.setattr(data_loader, "fetch_log_returns", lambda tk, s, e: np.zeros(5))

    payload = _base_payload(garch_vol=0.22, basket={"tickers": ["AAPL", "MSFT"]})
    result = var_main._build_corr_sim_peer_from_context(payload, "AAPL")
    _assert_histogram(result["terminal_price_histogram"], result["n_sims"])
    assert result["n_sims"] == 10_000
