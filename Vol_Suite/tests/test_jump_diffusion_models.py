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


from jump_diffusion.models import BatesModel, HestonModel, KouModel, VarianceGammaModel


def test_heston_phi_zero_is_one():
    h = HestonModel(kappa=2.0, theta=0.04, xi=0.5, rho=-0.6, v0=0.04)
    val = h.phi(np.array([0.0 + 0j]), T=0.5)
    assert np.isclose(val[0].real, 1.0, atol=1e-8)


def test_bates_reduces_to_heston_when_no_jumps():
    """lam=0 in Bates must exactly match the Heston-only CF."""
    h = HestonModel(kappa=2.0, theta=0.04, xi=0.5, rho=-0.6, v0=0.04)
    b = BatesModel(
        kappa=2.0,
        theta=0.04,
        xi=0.5,
        rho=-0.6,
        v0=0.04,
        lam=0.0,
        mu_j=0.0,
        sigma_j=0.01,
    )
    u = np.array([0.3 - 0.2j, 1.0 + 0j])
    assert np.allclose(h.phi(u, 0.5), b.phi(u, 0.5), atol=1e-10)


def test_kou_phi_zero_is_one():
    k = KouModel(sigma=0.2, lam=0.5, p=0.4, eta1=10.0, eta2=5.0)
    val = k.phi(np.array([0.0 + 0j]), T=0.5)
    assert np.isclose(val[0].real, 1.0, atol=1e-8)


def test_vg_phi_zero_is_one():
    vg = VarianceGammaModel(sigma=0.2, nu=0.3, theta_vg=-0.1)
    val = vg.phi(np.array([0.0 + 0j]), T=0.5)
    assert np.isclose(val[0].real, 1.0, atol=1e-8)
