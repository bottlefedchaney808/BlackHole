"""Lewis (2001) single-integral characteristic-function option pricer.

Shared by all 5 jump-diffusion models -- each model supplies phi(u, T), the
characteristic function of the driftless log-return; this file does the one
piece of numerical work (Fourier inversion) that isn't model-specific.
"""

import numpy as np
from scipy.integrate import quad


def lewis_price(
    model, S0: float, K: float, T: float, r: float, q: float, right: str = "call"
) -> float:
    """European option price via Lewis (2001).

    ``model`` must implement ``phi(u: np.ndarray, T: float) -> np.ndarray``
    per the convention in jump_diffusion/models.py's module docstring.
    """
    k = np.log(S0 / K) + (r - q) * T

    def integrand(u: float) -> float:
        val = np.exp(-1j * u * k) * model.phi(np.array([u - 0.5j]), T)[0]
        return val.real / (u**2 + 0.25)

    integral, _ = quad(integrand, 0.0, 200.0, limit=200)
    call = (
        S0 * np.exp(-q * T)
        - (np.sqrt(S0 * K) * np.exp(-(r + q) * T / 2) / np.pi) * integral
    )

    if right.lower().startswith("p"):
        return call - S0 * np.exp(-q * T) + K * np.exp(-r * T)
    return call
