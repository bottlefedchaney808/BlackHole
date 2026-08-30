import numpy as np
from implied_vol import bs_price
from jump_diffusion.models import MertonModel
from jump_diffusion.pricer import lewis_price


def test_merton_no_jumps_converges_to_black_scholes():
    """lam=0 collapses Merton to pure diffusion -- must match BS exactly."""
    S0, K, T, r, q, sigma = 100.0, 105.0, 0.5, 0.03, 0.0, 0.22
    m = MertonModel(sigma=sigma, lam=0.0, mu_j=0.0, sigma_j=0.01)
    got = lewis_price(m, S0, K, T, r, q, right="call")
    want = bs_price(S0, K, T, r, q, sigma, "call")
    assert np.isclose(got, want, atol=1e-2)


def test_lewis_price_put_call_parity():
    S0, K, T, r, q = 100.0, 100.0, 0.25, 0.03, 0.01
    m = MertonModel(sigma=0.2, lam=0.5, mu_j=-0.05, sigma_j=0.1)
    call = lewis_price(m, S0, K, T, r, q, right="call")
    put = lewis_price(m, S0, K, T, r, q, right="put")
    assert np.isclose(call - put, S0 * np.exp(-q * T) - K * np.exp(-r * T), atol=1e-6)
