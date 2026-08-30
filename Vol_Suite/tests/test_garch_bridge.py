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
