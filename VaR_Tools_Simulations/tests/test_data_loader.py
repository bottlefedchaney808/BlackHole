"""Tests for var_engine.data_loader failure signalling.

estimate_garch_vol / estimate_geometric_return must return None (not 0.0)
when the underlying fetch or fit fails, so callers can distinguish a real
failure from a legitimately-computed zero.
"""
import numpy as np
import pytest

from var_engine import data_loader


def test_estimate_garch_vol_returns_none_on_fetch_failure(monkeypatch, caplog):
    def _raise(*a, **k):
        raise RuntimeError("ThetaData 502")

    monkeypatch.setattr(data_loader, "fetch_log_returns", _raise)
    with caplog.at_level("WARNING"):
        result = data_loader.estimate_garch_vol("AAPL")
    assert result is None
    assert any("AAPL" in r.message for r in caplog.records)


def test_estimate_garch_vol_returns_none_on_insufficient_data(monkeypatch):
    monkeypatch.setattr(data_loader, "fetch_log_returns", lambda *a, **k: np.zeros(10))
    assert data_loader.estimate_garch_vol("AAPL") is None


def test_estimate_geometric_return_returns_none_on_fetch_failure(monkeypatch, caplog):
    def _raise(*a, **k):
        raise RuntimeError("ThetaData 502")

    monkeypatch.setattr(data_loader, "fetch_price_series", _raise)
    with caplog.at_level("WARNING"):
        result = data_loader.estimate_geometric_return("AAPL")
    assert result is None
    assert any("AAPL" in r.message for r in caplog.records)


def test_estimate_geometric_return_returns_none_on_insufficient_data(monkeypatch):
    monkeypatch.setattr(data_loader, "fetch_price_series", lambda *a, **k: np.linspace(100.0, 110.0, 10))
    assert data_loader.estimate_geometric_return("AAPL") is None


def test_estimate_geometric_return_computes_real_value(monkeypatch):
    px = np.linspace(100.0, 120.0, 200)
    monkeypatch.setattr(data_loader, "fetch_price_series", lambda *a, **k: px)
    result = data_loader.estimate_geometric_return("AAPL")
    assert result is not None
    assert result == pytest.approx((px[-1] / px[0]) ** (252.0 / len(px)) - 1.0)
