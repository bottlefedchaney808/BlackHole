import numpy as np
from jump_diffusion.garch_bridge import jump_filtered_returns


def test_jump_filtered_returns_flags_injected_jump():
    rng = np.random.default_rng(42)
    daily_sigma = 0.20 / np.sqrt(252)
    returns = rng.normal(0, daily_sigma, 250)
    returns[100] = 0.15  # inject an obvious one-day jump (15% move)

    filtered, mask = jump_filtered_returns(returns, merton_sigma=0.20, k=4.0)

    assert mask[100] == True
    assert mask.sum() < 10  # a handful of days at most should trip a 4-sigma threshold
    assert (
        filtered[100] != returns[100]
    )  # the jump day was actually filtered, not just flagged
    assert len(filtered) == len(returns)


def test_jump_filtered_returns_no_nans():
    rng = np.random.default_rng(7)
    returns = rng.normal(0, 0.01, 100)
    filtered, mask = jump_filtered_returns(returns, merton_sigma=0.18)
    assert not np.any(np.isnan(filtered))


import garch_analysis
import pandas as pd


class _FakeGarchResult:
    """Stand-in for the arch_model fit result run_garch_analysis returns --
    only the two attributes run_garch_module actually reads."""

    def __init__(self, vol_pct: float):
        self.conditional_volatility = pd.Series([vol_pct])
        self.params = {"alpha[1]": 0.05, "beta[1]": 0.90, "nu": 6.0}
        self.aic = 100.0
        self.bic = 105.0


def test_run_garch_module_applies_jump_variance_share_scaling(monkeypatch, tmp_path):
    """End-to-end: stub the actual fit run_garch_module calls internally,
    and assert the jump_variance_share kwarg measurably changes the
    returned conditional vol via adjust_garch_forecast -- not just that
    adjust_garch_forecast works in isolation (that's Task 7's test)."""
    daily_vol_pct = (
        20.0 / (garch_analysis.ANNUALIZE**0.5) * 100
    )  # back out a known annualized vol

    def _fake_run_garch_analysis(ticker, start=None, end=None, merton_sigma=None):
        return _FakeGarchResult(daily_vol_pct)

    monkeypatch.setattr(garch_analysis, "run_garch_analysis", _fake_run_garch_analysis)

    baseline = garch_analysis.run_garch_module("SPY", output_dir=str(tmp_path))
    with_jump = garch_analysis.run_garch_module(
        "SPY", output_dir=str(tmp_path), jump_variance_share=1.0
    )

    assert with_jump[2] > baseline[2]  # 3rd tuple element is garch_conditional_vol
    assert np.isclose(
        with_jump[2], baseline[2] * 1.25
    )  # gamma=0.25 default from Task 7


def test_run_garch_module_passes_merton_sigma_through(monkeypatch, tmp_path):
    """merton_sigma must reach run_garch_analysis's own kwarg -- proves tie 1
    is actually wired, not just tie 2."""
    received = {}

    def _fake_run_garch_analysis(ticker, start=None, end=None, merton_sigma=None):
        received["merton_sigma"] = merton_sigma
        return _FakeGarchResult(20.0)

    monkeypatch.setattr(garch_analysis, "run_garch_analysis", _fake_run_garch_analysis)

    garch_analysis.run_garch_module("SPY", output_dir=str(tmp_path), merton_sigma=0.22)

    assert received["merton_sigma"] == 0.22


def test_jump_filtered_returns_actually_called_inside_run_garch_analysis(monkeypatch):
    """Unit-level check that run_garch_analysis calls jump_filtered_returns
    on its log-return series when merton_sigma is given -- isolates tie 1
    from the ADF/ARCH pre-tests and the real arch_model fit, which this
    plan does not want to mock end-to-end."""
    from jump_diffusion import garch_bridge

    calls = []
    original = garch_bridge.jump_filtered_returns

    def _spy(log_returns, merton_sigma, k=4.0):
        calls.append(merton_sigma)
        return original(log_returns, merton_sigma, k)

    monkeypatch.setattr(garch_bridge, "jump_filtered_returns", _spy)

    prices = pd.Series(
        100 * np.cumprod(1 + np.random.default_rng(3).normal(0, 0.01, 300)),
        index=pd.date_range("2025-01-01", periods=300, freq="B"),
    )
    monkeypatch.setattr(
        garch_analysis,
        "fetch_price_history",
        lambda tickers, period: pd.DataFrame({"SPY": prices}),
    )

    try:
        garch_analysis.run_garch_analysis("SPY", merton_sigma=0.20)
    except Exception:
        pass  # the real ADF/ARCH/arch_model pipeline may still fail on this synthetic series -- irrelevant to this test

    assert calls == [0.20]
