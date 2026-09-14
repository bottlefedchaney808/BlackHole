"""Characteristic-function definitions for 5 jump-diffusion models.

Every model's phi(u, T) is the characteristic function of the driftless,
martingale-corrected log-return X_T = ln(S_T/S0) - (r-q)*T -- deliberately
excluding ln(S0) and the (r-q)*T drift so pricer.py's Lewis (2001) inversion
is identical for every model. See the "Reference: characteristic functions"
section of the design spec for the exact formulas and their derivation.
"""

import math
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


@dataclass
class HestonModel:
    """Heston (1993) stochastic volatility, no jumps. Gatheral 'Little Trap'
    form (avoids the branch-cut discontinuity of the original 1993 formula).

    Best for: closed-form cross-check against Options_Suite's Heston MC.
    Weak at: short-dated steep-wing smiles -- no jump term.
    """

    kappa: float
    theta: float
    xi: float
    rho: float
    v0: float

    name: ClassVar[str] = "Heston"
    param_names: ClassVar[tuple] = ("kappa", "theta", "xi", "rho", "v0")
    PARAM_BOUNDS: ClassVar[tuple] = (
        (1e-3, 20.0),
        (1e-4, 4.0),
        (1e-3, 5.0),
        (-0.999, 0.999),
        (1e-4, 4.0),
    )

    def phi(self, u: np.ndarray, T: float) -> np.ndarray:
        u = np.asarray(u, dtype=complex)
        kappa, theta, xi, rho, v0 = self.kappa, self.theta, self.xi, self.rho, self.v0
        d = np.sqrt((rho * xi * 1j * u - kappa) ** 2 + xi**2 * (1j * u + u**2))
        g = (kappa - rho * xi * 1j * u - d) / (kappa - rho * xi * 1j * u + d)
        term1 = (kappa * theta / xi**2) * (
            (kappa - rho * xi * 1j * u - d) * T
            - 2 * np.log((1 - g * np.exp(-d * T)) / (1 - g))
        )
        term2 = (
            (v0 / xi**2)
            * (kappa - rho * xi * 1j * u - d)
            * (1 - np.exp(-d * T))
            / (1 - g * np.exp(-d * T))
        )
        return np.exp(term1 + term2)

    def to_array(self) -> np.ndarray:
        return np.array(astuple(self), dtype=float)

    @classmethod
    def from_array(cls, arr) -> "HestonModel":
        return cls(**dict(zip(cls.param_names, arr)))


@dataclass
class BatesModel:
    """Bates (1996) = Heston stochastic vol + Merton-style lognormal jumps.

    Best for: separating diffusive vol-of-vol from jump risk -- the
    jump-variance share this model reports feeds the GARCH-X tie and the
    dealer-book overlay. Most expensive to calibrate (7 params); primary
    candidate for JUMP_MODEL_DEFAULT.
    """

    kappa: float
    theta: float
    xi: float
    rho: float
    v0: float
    lam: float
    mu_j: float
    sigma_j: float

    name: ClassVar[str] = "Bates"
    param_names: ClassVar[tuple] = (
        "kappa",
        "theta",
        "xi",
        "rho",
        "v0",
        "lam",
        "mu_j",
        "sigma_j",
    )
    PARAM_BOUNDS: ClassVar[tuple] = (
        (1e-3, 20.0),
        (1e-4, 4.0),
        (1e-3, 5.0),
        (-0.999, 0.999),
        (1e-4, 4.0),
        (0.0, 20.0),
        (-2.0, 2.0),
        (1e-4, 3.0),
    )

    def phi(self, u: np.ndarray, T: float) -> np.ndarray:
        u = np.asarray(u, dtype=complex)
        heston = HestonModel(self.kappa, self.theta, self.xi, self.rho, self.v0)
        kappa_j = np.exp(self.mu_j + 0.5 * self.sigma_j**2) - 1
        jump_factor = np.exp(
            self.lam
            * T
            * (
                np.exp(1j * u * self.mu_j - 0.5 * self.sigma_j**2 * u**2)
                - 1
                - 1j * u * kappa_j
            )
        )
        return heston.phi(u, T) * jump_factor

    def jump_variance_share(self, T: float) -> float:
        """Fraction of total T-year variance attributable to the jump
        component: Var(jump leg) / (Var(jump leg) + Var(diffusive leg)).

        Diffusive leg is the Heston integrated expected variance
        ∫E[v_s]ds = θT + (v0-θ)(1-e^{-κT})/κ (κ→0 limit: v0·T).
        Jump leg is lam*T*(sigma_j**2 + mu_j**2) (compound-Poisson
        lognormal jump variance).
        """
        jump_var = self.lam * T * (self.sigma_j**2 + self.mu_j**2)
        if self.kappa > 1e-12:
            diffusive_var = (
                self.theta * T
                + (self.v0 - self.theta)
                * (1.0 - math.exp(-self.kappa * T))
                / self.kappa
            )
        else:
            diffusive_var = self.v0 * T
        total = jump_var + diffusive_var
        return jump_var / total if total > 0 else 0.0

    def to_array(self) -> np.ndarray:
        return np.array(astuple(self), dtype=float)

    @classmethod
    def from_array(cls, arr) -> "BatesModel":
        return cls(**dict(zip(cls.param_names, arr)))


@dataclass
class KouModel:
    """Kou (2002) double-exponential jump-diffusion, no stochastic vol.

    Best for: asymmetric tail risk (SPX-style crash risk >> melt-up risk) via
    separate up/down jump-size distributions (eta1, eta2).
    Weak at: vol term structure -- vol is constant, no mean reversion.
    eta1 must exceed 1.0 for E[e^jump] to be finite (enforced by PARAM_BOUNDS).
    """

    sigma: float
    lam: float
    p: float
    eta1: float
    eta2: float

    name: ClassVar[str] = "Kou"
    param_names: ClassVar[tuple] = ("sigma", "lam", "p", "eta1", "eta2")
    PARAM_BOUNDS: ClassVar[tuple] = (
        (1e-4, 3.0),
        (0.0, 20.0),
        (0.01, 0.99),
        (1.01, 50.0),
        (0.01, 50.0),
    )

    def phi(self, u: np.ndarray, T: float) -> np.ndarray:
        u = np.asarray(u, dtype=complex)
        kappa = (
            self.p * self.eta1 / (self.eta1 - 1)
            + (1 - self.p) * self.eta2 / (self.eta2 + 1)
            - 1
        )
        drift = -0.5 * self.sigma**2 - self.lam * kappa
        jump_cf = (
            self.p * self.eta1 / (self.eta1 - 1j * u)
            + (1 - self.p) * self.eta2 / (self.eta2 + 1j * u)
            - 1
        )
        return np.exp(
            T * (1j * u * drift - 0.5 * self.sigma**2 * u**2 + self.lam * jump_cf)
        )

    def to_array(self) -> np.ndarray:
        return np.array(astuple(self), dtype=float)

    @classmethod
    def from_array(cls, arr) -> "KouModel":
        return cls(**dict(zip(cls.param_names, arr)))


@dataclass
class VarianceGammaModel:
    """Variance Gamma (Madan-Carr-Chang): pure jump process, no diffusion
    (Brownian motion time-changed by a Gamma process). Cheapest/fastest of
    the 5 to calibrate (3 params) -- good baseline or frequent-slice default.
    Weak at: no stochastic vol, so it misses term-structure evolution.
    """

    sigma: float
    nu: float
    theta_vg: float

    name: ClassVar[str] = "VarianceGamma"
    param_names: ClassVar[tuple] = ("sigma", "nu", "theta_vg")
    PARAM_BOUNDS: ClassVar[tuple] = ((1e-4, 3.0), (1e-4, 5.0), (-2.0, 2.0))

    def phi(self, u: np.ndarray, T: float) -> np.ndarray:
        u = np.asarray(u, dtype=complex)
        omega = (1.0 / self.nu) * np.log(
            1 - self.theta_vg * self.nu - 0.5 * self.sigma**2 * self.nu
        )
        vg_cf = (
            1 - 1j * u * self.theta_vg * self.nu + 0.5 * self.sigma**2 * self.nu * u**2
        ) ** (-T / self.nu)
        return np.exp(1j * u * omega * T) * vg_cf

    def to_array(self) -> np.ndarray:
        return np.array(astuple(self), dtype=float)

    @classmethod
    def from_array(cls, arr) -> "VarianceGammaModel":
        return cls(**dict(zip(cls.param_names, arr)))


#: Registry used by comparison.py and calibration.py to iterate "all 5 models"
#: without a hardcoded import list at every call site. (Plain tuple, not
#: typing.ClassVar -- ClassVar is only valid as a class-body annotation;
#: using it on a module-level name is a static-analysis error under ruff/
#: pyright even though it doesn't fail at runtime.)
ALL_MODELS = (MertonModel, HestonModel, BatesModel, KouModel, VarianceGammaModel)
