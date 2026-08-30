"""Characteristic-function definitions for 5 jump-diffusion models.

Every model's phi(u, T) is the characteristic function of the driftless,
martingale-corrected log-return X_T = ln(S_T/S0) - (r-q)*T -- deliberately
excluding ln(S0) and the (r-q)*T drift so pricer.py's Lewis (2001) inversion
is identical for every model. See the "Reference: characteristic functions"
section of the design spec for the exact formulas and their derivation.
"""

from dataclasses import astuple, dataclass
from typing import ClassVar

import numpy as np


@dataclass
class MertonModel:
    """Merton (1976) jump-diffusion: constant-vol diffusion + lognormal jumps.

    Best for: cheap, simple jump-day detection (see garch_bridge.py) -- fewest
    params among the jump-inclusive models, no stochastic vol.
    """

    sigma: float
    lam: float
    mu_j: float
    sigma_j: float

    name: ClassVar[str] = "Merton"
    param_names: ClassVar[tuple] = ("sigma", "lam", "mu_j", "sigma_j")
    PARAM_BOUNDS: ClassVar[tuple] = ((1e-4, 3.0), (0.0, 20.0), (-2.0, 2.0), (1e-4, 3.0))

    def phi(self, u: np.ndarray, T: float) -> np.ndarray:
        u = np.asarray(u, dtype=complex)
        kappa = np.exp(self.mu_j + 0.5 * self.sigma_j**2) - 1
        drift = -0.5 * self.sigma**2 - self.lam * kappa
        jump_term = self.lam * (
            np.exp(1j * u * self.mu_j - 0.5 * self.sigma_j**2 * u**2) - 1
        )
        return np.exp(T * (1j * u * drift - 0.5 * self.sigma**2 * u**2 + jump_term))

    def to_array(self) -> np.ndarray:
        return np.array(astuple(self), dtype=float)

    @classmethod
    def from_array(cls, arr) -> "MertonModel":
        return cls(**dict(zip(cls.param_names, arr)))
