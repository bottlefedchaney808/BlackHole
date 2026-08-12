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
