"""Tests for garch_analysis.run_garch_module's contract.

The module wrapper is the only part of garch_analysis.py the rest of the suite
calls, and its third return value (annualized conditional vol) is what
suite_context.json / vol_result.json publish downstream. These tests stub the
actual GARCH fit -- fitting a real model needs network price history and is
covered by the suite's live runs, not by unit tests.
"""

import garch_analysis as ga
import numpy as np
import pandas as pd
import pytest


class _FakeResult:
    """Minimal stand-in for an arch ARCHModelResult.

    `conditional_volatility` is daily and on the percent scale, matching
    run_garch_analysis's `log_returns * 100` fit input.
    """

    def __init__(self):
        self.params = {"omega": 0.01, "alpha[1]": 0.08, "beta[1]": 0.9, "nu": 6.0}
        self.conditional_volatility = pd.Series([1.1, 1.2, 1.05])


@pytest.mark.unit
def test_run_garch_module_returns_annualized_conditional_vol(monkeypatch, tmp_path):
    monkeypatch.setenv("VS_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(ga, "run_garch_analysis",
                        lambda ticker, start=None, end=None: _FakeResult())
    files, interp, vol = ga.run_garch_module("AAPL", output_dir=str(tmp_path))
    assert vol == pytest.approx(1.05 / 100.0 * np.sqrt(252.0), rel=1e-9)
    assert "Annualized conditional vol" in interp
    assert files == []


@pytest.mark.unit
def test_run_garch_module_vol_is_none_on_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("VS_OUTPUT_DIR", str(tmp_path))

    def _raise(*args, **kwargs):
        raise RuntimeError("fit failed")

    monkeypatch.setattr(ga, "run_garch_analysis", _raise)
    files, interp, vol = ga.run_garch_module("AAPL", output_dir=str(tmp_path))
    assert vol is None
    assert files == []
    assert "failed" in interp


@pytest.mark.unit
def test_run_garch_module_vol_is_none_when_series_empty(monkeypatch, tmp_path):
    """A fit that converged but carries no conditional vol must not crash the
    wrapper -- the other two return values are still useful."""
    monkeypatch.setenv("VS_OUTPUT_DIR", str(tmp_path))
    res = _FakeResult()
    res.conditional_volatility = pd.Series(dtype=float)
    monkeypatch.setattr(ga, "run_garch_analysis",
                        lambda ticker, start=None, end=None: res)
    _files, interp, vol = ga.run_garch_module("AAPL", output_dir=str(tmp_path))
    assert vol is None
    assert "GARCH(1,1) params" in interp
