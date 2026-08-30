import numpy as np
from jump_diffusion.models import MertonModel


def test_merton_phi_zero_is_one():
    """phi(0, T) must always be 1 -- it's E[exp(0)] for any distribution."""
    m = MertonModel(sigma=0.2, lam=0.5, mu_j=-0.05, sigma_j=0.1)
    val = m.phi(np.array([0.0 + 0j]), T=0.5)
    assert np.isclose(val[0].real, 1.0, atol=1e-10)
    assert np.isclose(val[0].imag, 0.0, atol=1e-10)


def test_merton_to_array_from_array_round_trip():
    m = MertonModel(sigma=0.2, lam=0.5, mu_j=-0.05, sigma_j=0.1)
    arr = m.to_array()
    m2 = MertonModel.from_array(arr)
    assert np.allclose(arr, m2.to_array())
    assert m2.sigma == 0.2 and m2.lam == 0.5
