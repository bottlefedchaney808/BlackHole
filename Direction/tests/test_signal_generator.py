# Direction/tests/test_signal_generator.py
"""Tests for Direction/signal_generator.py's conviction-combination logic
-- network-free, monkeypatches each of the five submodules' entry points
directly so only the combination logic (score/conviction rules) is under
test, not any submodule's own behavior (already covered by their own test
files).
"""
import pytest

from Direction import (
    bollinger_analyzer, data, elliott_wave, liquidity_map,
    signal_generator as sg, trend_engine, whale_scanner,
)


def _patch_all(monkeypatch, *, whale_sig, wave_sig, squeeze_sig, trend_sig, liq_sig):
    monkeypatch.setattr(whale_scanner, "scan",
                         lambda ticker, **kw: {"signal": whale_sig, "price": 100.0})
    monkeypatch.setattr(elliott_wave, "analyze", lambda ticker, **kw: {"signal": wave_sig})
    monkeypatch.setattr(bollinger_analyzer, "analyze", lambda ticker, **kw: {"signal": squeeze_sig})
    monkeypatch.setattr(trend_engine, "analyze_trend", lambda ticker, **kw: {"signal": trend_sig})
    monkeypatch.setattr(liquidity_map, "get_liquidity", lambda ticker, **kw: {"signal": liq_sig})


@pytest.mark.unit
def test_generate_high_conviction_when_whale_wave3_and_squeeze_and_score_ge_3(monkeypatch):
    _patch_all(monkeypatch, whale_sig=True, wave_sig=True, squeeze_sig=True,
               trend_sig=False, liq_sig=False)
    result = sg.generate("NVDA")
    assert result["score"] == 3
    assert result["conviction"] == "HIGH"


@pytest.mark.unit
def test_generate_medium_conviction_when_whale_and_score_ge_3_but_not_high(monkeypatch):
    _patch_all(monkeypatch, whale_sig=True, wave_sig=False, squeeze_sig=True,
               trend_sig=True, liq_sig=False)
    result = sg.generate("NVDA")
    assert result["score"] == 3
    assert result["conviction"] == "MEDIUM"  # whale AND score>=3, but wave3 is False so not HIGH


@pytest.mark.unit
def test_generate_none_conviction_without_whale_signal(monkeypatch):
    _patch_all(monkeypatch, whale_sig=False, wave_sig=True, squeeze_sig=True,
               trend_sig=True, liq_sig=True)
    result = sg.generate("NVDA")
    assert result["score"] == 4
    assert result["conviction"] == "NONE"  # no whale = no trade, regardless of score


@pytest.mark.unit
def test_generate_none_conviction_when_whale_true_but_score_below_3(monkeypatch):
    _patch_all(monkeypatch, whale_sig=True, wave_sig=False, squeeze_sig=False,
               trend_sig=False, liq_sig=False)
    result = sg.generate("NVDA")
    assert result["score"] == 1
    assert result["conviction"] == "NONE"


@pytest.mark.unit
def test_generate_falls_back_to_data_get_price_when_whale_has_no_price(monkeypatch):
    monkeypatch.setattr(whale_scanner, "scan", lambda ticker, **kw: {"signal": False, "price": None})
    monkeypatch.setattr(elliott_wave, "analyze", lambda ticker, **kw: {"signal": False})
    monkeypatch.setattr(bollinger_analyzer, "analyze", lambda ticker, **kw: {"signal": False})
    monkeypatch.setattr(trend_engine, "analyze_trend", lambda ticker, **kw: {"signal": False})
    monkeypatch.setattr(liquidity_map, "get_liquidity", lambda ticker, **kw: {"signal": False})
    monkeypatch.setattr(data, "get_price", lambda ticker: 55.5)
    result = sg.generate("NVDA")
    assert result["price"] == pytest.approx(55.5)


@pytest.mark.unit
def test_generate_details_carries_all_five_module_outputs(monkeypatch):
    _patch_all(monkeypatch, whale_sig=True, wave_sig=True, squeeze_sig=True,
               trend_sig=True, liq_sig=True)
    result = sg.generate("NVDA")
    assert set(result["details"].keys()) == {"whale", "elliott", "bollinger", "trend", "liquidity"}


def test_generate_passes_as_of_to_all_modules(monkeypatch):
    from Direction import signal_generator as sg
    from Direction import (whale_scanner, elliott_wave, bollinger_analyzer,
                           trend_engine, liquidity_map)
    seen = {}

    def _watch(name, ret):
        def fn(ticker, as_of=None):
            seen[name] = as_of
            return ret
        return fn

    monkeypatch.setattr(whale_scanner, "scan", _watch("whale", {}))
    monkeypatch.setattr(elliott_wave, "analyze", _watch("wave3", {}))
    monkeypatch.setattr(bollinger_analyzer, "analyze", _watch("squeeze", {}))
    monkeypatch.setattr(trend_engine, "analyze_trend", _watch("trend", {}))
    monkeypatch.setattr(liquidity_map, "get_liquidity", _watch("liquidity", {}))

    out = sg.generate("SPY", as_of="2026-08-14")
    assert seen == {"whale": "2026-08-14", "wave3": "2026-08-14",
                    "squeeze": "2026-08-14", "trend": "2026-08-14",
                    "liquidity": "2026-08-14"}
    assert out["as_of"] == "2026-08-14"
