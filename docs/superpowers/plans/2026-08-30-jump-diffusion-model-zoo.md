# Jump-Diffusion Model Zoo (Vol_Suite, Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 5-model jump-diffusion pricing/calibration engine (VG, Heston, Bates SVJ, Kou,
Merton) to Vol_Suite, wired into `suite_context.json` and additively tied into dealer positioning,
VRP term structure, the strategy recommender, and GARCH.

**Architecture:** New self-contained package `Vol_Suite/jump_diffusion/` — shared Lewis (2001)
characteristic-function pricer, Nelder-Mead calibration in IV space, two-tier execution
(all-5-model comparison mode vs. single-default-model unified-pipeline mode). Hooks into
`volatility_suite.py::_run_core_analysis` and `suite_context.py::build_suite_context` follow the
existing `garch_conditional_vol` call-site pattern exactly. All three downstream ties (dealer
book, VRP, recommender) and both GARCH ties are additive optional parameters — nothing existing
changes behavior when the new value is `None`.

**Tech Stack:** Python 3.12, `scipy` 1.18.0 (`scipy.optimize.minimize` Nelder-Mead,
`scipy.integrate.quad`), `numpy`, `arch` 8.0.0 (existing GARCH fit), `pytest`.

## Global Constraints

- Root venv only (`.venv\Scripts\python.exe`), per CLAUDE.md — this is Vol_Suite, not
  sentiment-scanner's isolated venv.
- Every new/changed downstream call site must remain backward-compatible when the new parameter
  is omitted or `None` — existing callers and existing tests must keep passing unmodified.
- `suite_context.json` stays `schema_version: 1` (additive field, matching precedent set by
  `garch_conditional_vol`) — but both `shared/schemas.py::validate_suite_context` AND
  `Vol_Suite/suite_context.py`'s own `validate_suite_context` must accept the new key (these are
  two different functions with the same name — CLAUDE.md-documented gotcha).
- Never let a calibration failure abort `_run_core_analysis` or a unified run — match the existing
  `GarchModuleResult.error` null-safe pattern (`Vol_Suite/garch_analysis.py:326-347`).
- Run tests via `pytest Vol_Suite/tests/` (or the specific test file) from the repo root, per
  CLAUDE.md; `Vol_Suite/tests/conftest.py` already puts the repo root and `Vol_Suite/` on
  `sys.path` and force-loads Vol_Suite's own `expiry_selector.py` ahead of Options_Suite's
  same-named module — no conftest changes needed.

---

## Reference: characteristic functions used (all tasks)

Every model's `phi(u, T)` returns the characteristic function of **X_T = ln(S_T/S0) − (r−q)T**
(the *driftless, martingale-corrected* log-return — deliberately excluding `ln(S0)` and the
`(r−q)T` drift so every model shares one pricer with no per-model prefactor bookkeeping). `u` may
be a real or complex numpy array/scalar; `1j` is Python's imaginary unit.

- **Merton (1976):** params `sigma, lam, mu_j, sigma_j`. Jump compensator
  `kappa = exp(mu_j + 0.5*sigma_j**2) - 1`.
  `phi(u,T) = exp(T*(1j*u*(-0.5*sigma**2 - lam*kappa) - 0.5*sigma**2*u**2 + lam*(exp(1j*u*mu_j - 0.5*sigma_j**2*u**2) - 1)))`
- **Heston (1993), Gatheral "Little Trap" form:** params `kappa, theta, xi, rho, v0`.
  `d = sqrt((rho*xi*1j*u - kappa)**2 + xi**2*(1j*u + u**2))`
  `g = (kappa - rho*xi*1j*u - d) / (kappa - rho*xi*1j*u + d)`
  `phi(u,T) = exp((kappa*theta/xi**2)*((kappa-rho*xi*1j*u-d)*T - 2*log((1-g*exp(-d*T))/(1-g))) + (v0/xi**2)*(kappa-rho*xi*1j*u-d)*(1-exp(-d*T))/(1-g*exp(-d*T)))`
- **Bates (1996) = Heston × Merton jump factor:** params `kappa, theta, xi, rho, v0, lam, mu_j,
  sigma_j`. `kappa_j = exp(mu_j + 0.5*sigma_j**2) - 1`.
  `phi(u,T) = phi_heston(u,T; kappa,theta,xi,rho,v0) * exp(lam*T*(exp(1j*u*mu_j - 0.5*sigma_j**2*u**2) - 1 - 1j*u*kappa_j))`
- **Kou (2002) double-exponential:** params `sigma, lam, p, eta1, eta2` (`eta1 > 1`, `eta2 > 0`,
  `0 < p < 1`). `kappa = p*eta1/(eta1-1) + (1-p)*eta2/(eta2+1) - 1`.
  `phi(u,T) = exp(T*(1j*u*(-0.5*sigma**2 - lam*kappa) - 0.5*sigma**2*u**2 + lam*(p*eta1/(eta1-1j*u) + (1-p)*eta2/(eta2+1j*u) - 1)))`
- **Variance Gamma (Madan-Carr-Chang):** params `sigma, nu, theta_vg` (named `theta_vg` to avoid
  clashing with Heston's `theta`). `omega = (1.0/nu) * log(1 - theta_vg*nu - 0.5*sigma**2*nu)`.
  `phi(u,T) = exp(1j*u*omega*T) * (1 - 1j*u*theta_vg*nu + 0.5*sigma**2*nu*u**2) ** (-T/nu)`

**Lewis (2001) pricer**, shared by all 5 (`k = ln(S0/K) + (r-q)*T`):
```
call = S0*exp(-q*T) - (sqrt(S0*K)*exp(-(r+q)*T/2)/pi) * integral_0^inf{ Re[exp(-1j*u*k) * phi(u - 0.5j, T)] / (u**2 + 0.25) } du
put  = call - S0*exp(-q*T) + K*exp(-r*T)   # put-call parity
```

---

## Task 1: Package scaffold + shared model protocol + Merton

**Files:**
- Create: `Vol_Suite/jump_diffusion/__init__.py`
- Create: `Vol_Suite/jump_diffusion/models.py`
- Test: `Vol_Suite/tests/test_jump_diffusion_models.py`

**Interfaces:**
- Produces: `JumpDiffusionModel` (protocol/ABC in `models.py`) with `name: str`,
  `param_names: tuple[str, ...]` (class attr), `params: tuple[float, ...]` (instance),
  `phi(self, u: np.ndarray, T: float) -> np.ndarray`, `to_array(self) -> np.ndarray`,
  `classmethod from_array(cls, arr) -> "JumpDiffusionModel"`, class attr
  `PARAM_BOUNDS: tuple[tuple[float, float], ...]` (one `(lo, hi)` per param, same order as
  `param_names`). Produces `MertonModel(sigma, lam, mu_j, sigma_j)`.

- [ ] **Step 1: Write the failing test**

```python
# Vol_Suite/tests/test_jump_diffusion_models.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/test_jump_diffusion_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jump_diffusion'`

- [ ] **Step 3: Write minimal implementation**

```python
# Vol_Suite/jump_diffusion/__init__.py
"""Jump-diffusion characteristic-function pricing/calibration for Vol_Suite.

See docs/superpowers/specs/2026-08-30-jump-diffusion-model-zoo-design.md.
"""
```

```python
# Vol_Suite/jump_diffusion/models.py
"""Characteristic-function definitions for 5 jump-diffusion models.

Every model's phi(u, T) is the characteristic function of the driftless,
martingale-corrected log-return X_T = ln(S_T/S0) - (r-q)*T -- deliberately
excluding ln(S0) and the (r-q)*T drift so pricer.py's Lewis (2001) inversion
is identical for every model. See the "Reference: characteristic functions"
section of the design spec for the exact formulas and their derivation.
"""

from dataclasses import dataclass, astuple, fields
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
        jump_term = self.lam * (np.exp(1j * u * self.mu_j - 0.5 * self.sigma_j**2 * u**2) - 1)
        return np.exp(T * (1j * u * drift - 0.5 * self.sigma**2 * u**2 + jump_term))

    def to_array(self) -> np.ndarray:
        return np.array(astuple(self), dtype=float)

    @classmethod
    def from_array(cls, arr) -> "MertonModel":
        return cls(**dict(zip(cls.param_names, arr)))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/test_jump_diffusion_models.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add Vol_Suite/jump_diffusion/__init__.py Vol_Suite/jump_diffusion/models.py Vol_Suite/tests/test_jump_diffusion_models.py
git commit -m "feat(vol-suite): scaffold jump_diffusion package, add Merton model"
```

---

## Task 2: Lewis (2001) pricer + Black-Scholes convergence test

**Files:**
- Create: `Vol_Suite/jump_diffusion/pricer.py`
- Test: `Vol_Suite/tests/test_jump_diffusion_pricer.py`

**Interfaces:**
- Consumes: `MertonModel` from Task 1 (`jump_diffusion.models.MertonModel`), `implied_vol.bs_price`
  (`Vol_Suite/implied_vol.py:81`, signature `bs_price(S, K, T, r, q, sigma, right) -> float`).
- Produces: `lewis_price(model, S0: float, K: float, T: float, r: float, q: float, right: str = "call") -> float`.

- [ ] **Step 1: Write the failing test**

```python
# Vol_Suite/tests/test_jump_diffusion_pricer.py
import numpy as np
from jump_diffusion.models import MertonModel
from jump_diffusion.pricer import lewis_price
from implied_vol import bs_price


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/test_jump_diffusion_pricer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jump_diffusion.pricer'`

- [ ] **Step 3: Write minimal implementation**

```python
# Vol_Suite/jump_diffusion/pricer.py
"""Lewis (2001) single-integral characteristic-function option pricer.

Shared by all 5 jump-diffusion models -- each model supplies phi(u, T), the
characteristic function of the driftless log-return; this file does the one
piece of numerical work (Fourier inversion) that isn't model-specific.
"""

import numpy as np
from scipy.integrate import quad


def lewis_price(model, S0: float, K: float, T: float, r: float, q: float, right: str = "call") -> float:
    """European option price via Lewis (2001).

    ``model`` must implement ``phi(u: np.ndarray, T: float) -> np.ndarray``
    per the convention in jump_diffusion/models.py's module docstring.
    """
    k = np.log(S0 / K) + (r - q) * T

    def integrand(u: float) -> float:
        val = np.exp(-1j * u * k) * model.phi(np.array([u - 0.5j]), T)[0]
        return val.real / (u**2 + 0.25)

    integral, _ = quad(integrand, 0.0, 200.0, limit=200)
    call = S0 * np.exp(-q * T) - (np.sqrt(S0 * K) * np.exp(-(r + q) * T / 2) / np.pi) * integral

    if right.lower().startswith("p"):
        return call - S0 * np.exp(-q * T) + K * np.exp(-r * T)
    return call
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/test_jump_diffusion_pricer.py -v`
Expected: PASS (2 tests). If `test_merton_no_jumps_converges_to_black_scholes` fails outside
`atol=1e-2`, widen the `quad` upper limit (try `500.0`) before touching the formula — this is a
numerical-integration truncation issue, not a formula error, if the put-call-parity test still
passes.

- [ ] **Step 5: Commit**

```bash
git add Vol_Suite/jump_diffusion/pricer.py Vol_Suite/tests/test_jump_diffusion_pricer.py
git commit -m "feat(vol-suite): add Lewis (2001) CF pricer, verify BS convergence"
```

---

## Task 3: Heston model + jump models (Bates, Kou, VG)

**Files:**
- Modify: `Vol_Suite/jump_diffusion/models.py`
- Test: `Vol_Suite/tests/test_jump_diffusion_models.py`

**Interfaces:**
- Produces: `HestonModel(kappa, theta, xi, rho, v0)`, `BatesModel(kappa, theta, xi, rho, v0, lam,
  mu_j, sigma_j)`, `KouModel(sigma, lam, p, eta1, eta2)`, `VarianceGammaModel(sigma, nu, theta_vg)`
  — same shape as `MertonModel` (dataclass, `phi`, `to_array`, `from_array`, `PARAM_BOUNDS`,
  `param_names`, `name`).

- [ ] **Step 1: Write the failing tests**

```python
# append to Vol_Suite/tests/test_jump_diffusion_models.py
from jump_diffusion.models import HestonModel, BatesModel, KouModel, VarianceGammaModel


def test_heston_phi_zero_is_one():
    h = HestonModel(kappa=2.0, theta=0.04, xi=0.5, rho=-0.6, v0=0.04)
    val = h.phi(np.array([0.0 + 0j]), T=0.5)
    assert np.isclose(val[0].real, 1.0, atol=1e-8)


def test_bates_reduces_to_heston_when_no_jumps():
    """lam=0 in Bates must exactly match the Heston-only CF."""
    h = HestonModel(kappa=2.0, theta=0.04, xi=0.5, rho=-0.6, v0=0.04)
    b = BatesModel(kappa=2.0, theta=0.04, xi=0.5, rho=-0.6, v0=0.04, lam=0.0, mu_j=0.0, sigma_j=0.01)
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/test_jump_diffusion_models.py -v`
Expected: FAIL with `ImportError: cannot import name 'HestonModel'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to Vol_Suite/jump_diffusion/models.py

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
    PARAM_BOUNDS: ClassVar[tuple] = ((1e-3, 20.0), (1e-4, 4.0), (1e-3, 5.0), (-0.999, 0.999), (1e-4, 4.0))

    def phi(self, u: np.ndarray, T: float) -> np.ndarray:
        u = np.asarray(u, dtype=complex)
        kappa, theta, xi, rho, v0 = self.kappa, self.theta, self.xi, self.rho, self.v0
        d = np.sqrt((rho * xi * 1j * u - kappa) ** 2 + xi**2 * (1j * u + u**2))
        g = (kappa - rho * xi * 1j * u - d) / (kappa - rho * xi * 1j * u + d)
        term1 = (kappa * theta / xi**2) * (
            (kappa - rho * xi * 1j * u - d) * T - 2 * np.log((1 - g * np.exp(-d * T)) / (1 - g))
        )
        term2 = (v0 / xi**2) * (kappa - rho * xi * 1j * u - d) * (1 - np.exp(-d * T)) / (1 - g * np.exp(-d * T))
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
    param_names: ClassVar[tuple] = ("kappa", "theta", "xi", "rho", "v0", "lam", "mu_j", "sigma_j")
    PARAM_BOUNDS: ClassVar[tuple] = (
        (1e-3, 20.0), (1e-4, 4.0), (1e-3, 5.0), (-0.999, 0.999), (1e-4, 4.0),
        (0.0, 20.0), (-2.0, 2.0), (1e-4, 3.0),
    )

    def phi(self, u: np.ndarray, T: float) -> np.ndarray:
        u = np.asarray(u, dtype=complex)
        heston = HestonModel(self.kappa, self.theta, self.xi, self.rho, self.v0)
        kappa_j = np.exp(self.mu_j + 0.5 * self.sigma_j**2) - 1
        jump_factor = np.exp(
            self.lam * T * (np.exp(1j * u * self.mu_j - 0.5 * self.sigma_j**2 * u**2) - 1 - 1j * u * kappa_j)
        )
        return heston.phi(u, T) * jump_factor

    def jump_variance_share(self, T: float) -> float:
        """Fraction of total T-year variance attributable to the jump
        component: Var(jump leg) / (Var(jump leg) + Var(diffusive leg)).
        Diffusive leg variance over [0,T] is approximated by v0*T (Heston's
        instantaneous variance held at its current level -- a reasonable
        short-horizon approximation, not a full integrated-variance model).
        Jump leg variance is lam*T*(sigma_j**2 + mu_j**2) (variance of a
        compound Poisson process with lognormal jump sizes).
        """
        jump_var = self.lam * T * (self.sigma_j**2 + self.mu_j**2)
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
    PARAM_BOUNDS: ClassVar[tuple] = ((1e-4, 3.0), (0.0, 20.0), (0.01, 0.99), (1.01, 50.0), (0.01, 50.0))

    def phi(self, u: np.ndarray, T: float) -> np.ndarray:
        u = np.asarray(u, dtype=complex)
        kappa = self.p * self.eta1 / (self.eta1 - 1) + (1 - self.p) * self.eta2 / (self.eta2 + 1) - 1
        drift = -0.5 * self.sigma**2 - self.lam * kappa
        jump_cf = self.p * self.eta1 / (self.eta1 - 1j * u) + (1 - self.p) * self.eta2 / (self.eta2 + 1j * u) - 1
        return np.exp(T * (1j * u * drift - 0.5 * self.sigma**2 * u**2 + self.lam * jump_cf))

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
        omega = (1.0 / self.nu) * np.log(1 - self.theta_vg * self.nu - 0.5 * self.sigma**2 * self.nu)
        vg_cf = (1 - 1j * u * self.theta_vg * self.nu + 0.5 * self.sigma**2 * self.nu * u**2) ** (-T / self.nu)
        return np.exp(1j * u * omega * T) * vg_cf

    def to_array(self) -> np.ndarray:
        return np.array(astuple(self), dtype=float)

    @classmethod
    def from_array(cls, arr) -> "VarianceGammaModel":
        return cls(**dict(zip(cls.param_names, arr)))


#: Registry used by comparison.py and calibration.py to iterate "all 5 models"
#: without a hardcoded import list at every call site.
ALL_MODELS: ClassVar = (MertonModel, HestonModel, BatesModel, KouModel, VarianceGammaModel)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/test_jump_diffusion_models.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add Vol_Suite/jump_diffusion/models.py Vol_Suite/tests/test_jump_diffusion_models.py
git commit -m "feat(vol-suite): add Heston, Bates, Kou, VG jump-diffusion models"
```

---

## Task 4: Calibration engine (Nelder-Mead in IV space)

**Files:**
- Create: `Vol_Suite/jump_diffusion/calibration.py`
- Test: `Vol_Suite/tests/test_jump_diffusion_calibration.py`

**Interfaces:**
- Consumes: `ALL_MODELS`, all 5 model classes (Task 1/3), `lewis_price` (Task 2),
  `implied_vol.implied_vol` (`Vol_Suite/implied_vol.py:120`, signature
  `implied_vol(price, S, K, T, r, q, right, tol=1e-6, max_iter=100, pricer=None) -> Optional[float]`),
  `variance_swap_live.ChainData` (`Vol_Suite/variance_swap_live.py:41-50`: fields `expiry, strikes,
  call_mid, put_mid, r, q, call_iv, put_iv`).
- Produces: `CalibrationResult` dataclass (`model_name: str`, `params: dict[str, float]`,
  `rmse_iv: float`, `fitted_ivs: np.ndarray`, `market_ivs: np.ndarray`, `strikes: np.ndarray`) and
  `calibrate(model_cls, chain: ChainData, spot: float, T: float, seed: dict | None = None) -> CalibrationResult`.

- [ ] **Step 1: Write the failing test**

```python
# Vol_Suite/tests/test_jump_diffusion_calibration.py
import numpy as np
from jump_diffusion.models import MertonModel
from jump_diffusion.pricer import lewis_price
from jump_diffusion.calibration import calibrate
from implied_vol import implied_vol
from variance_swap_live import ChainData


def _synthetic_chain(model, S0, T, r, q, strikes):
    """Price a synthetic chain from *known* model params, invert to IV --
    the standard 'calibrate what you priced' round-trip fixture."""
    call_prices = np.array([lewis_price(model, S0, k, T, r, q, "call") for k in strikes])
    call_ivs = np.array([implied_vol(p, S0, k, T, r, q, "call") for p, k in zip(call_prices, strikes)])
    return ChainData(
        expiry="20270101", strikes=strikes, call_mid=call_prices,
        put_mid=call_prices, r=r, q=q, call_iv=call_ivs, put_iv=call_ivs,
    )


def test_merton_calibration_recovers_known_params():
    S0, T, r, q = 100.0, 0.5, 0.03, 0.0
    strikes = np.array([80.0, 90.0, 95.0, 100.0, 105.0, 110.0, 120.0])
    true_model = MertonModel(sigma=0.20, lam=0.8, mu_j=-0.08, sigma_j=0.12)
    chain = _synthetic_chain(true_model, S0, T, r, q, strikes)

    result = calibrate(MertonModel, chain, S0, T)

    assert result.rmse_iv < 0.01  # fit is tight in IV space (1 vol point)
    assert 0.10 < result.params["sigma"] < 0.30  # recovered in a sane neighborhood
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/test_jump_diffusion_calibration.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jump_diffusion.calibration'`

- [ ] **Step 3: Write minimal implementation**

```python
# Vol_Suite/jump_diffusion/calibration.py
"""Nelder-Mead calibration of a jump-diffusion model to an observed IV
smile, per expiry slice."""

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize

from implied_vol import implied_vol
from jump_diffusion.pricer import lewis_price


@dataclass
class CalibrationResult:
    model_name: str
    params: dict
    rmse_iv: float
    fitted_ivs: np.ndarray
    market_ivs: np.ndarray
    strikes: np.ndarray


# Default seeds -- rough equity-index-option starting points from the
# literature, tunable per model. Overridable via the ``seed`` argument.
_DEFAULT_SEEDS = {
    "Merton": {"sigma": 0.18, "lam": 1.0, "mu_j": -0.05, "sigma_j": 0.1},
    "Heston": {"kappa": 2.0, "theta": 0.04, "xi": 0.5, "rho": -0.6, "v0": 0.04},
    "Bates": {"kappa": 2.0, "theta": 0.04, "xi": 0.5, "rho": -0.6, "v0": 0.04,
              "lam": 0.5, "mu_j": -0.05, "sigma_j": 0.1},
    "Kou": {"sigma": 0.18, "lam": 1.0, "p": 0.4, "eta1": 10.0, "eta2": 5.0},
    "VarianceGamma": {"sigma": 0.18, "nu": 0.3, "theta_vg": -0.1},
}


def calibrate(model_cls, chain, spot: float, T: float, seed: dict | None = None) -> CalibrationResult:
    """Calibrate ``model_cls`` against ``chain`` via Nelder-Mead on
    sum-of-squared IV errors. Uses call quotes only (``chain.call_iv``,
    ``chain.strikes``) -- put/call smile symmetry means one side is enough
    and it keeps the objective simple; the model itself is priced via
    ``lewis_price`` for both rights identically.
    """
    seed = seed or _DEFAULT_SEEDS[model_cls.name]
    x0 = np.array([seed[p] for p in model_cls.param_names])
    bounds = model_cls.PARAM_BOUNDS

    valid = ~np.isnan(chain.call_iv)
    strikes = np.asarray(chain.strikes)[valid]
    market_ivs = np.asarray(chain.call_iv)[valid]

    def objective(x):
        x_clamped = np.clip(x, [b[0] for b in bounds], [b[1] for b in bounds])
        model = model_cls.from_array(x_clamped)
        errs = []
        for k, iv_mkt in zip(strikes, market_ivs):
            price = lewis_price(model, spot, k, T, chain.r, chain.q, "call")
            iv_fit = implied_vol(price, spot, k, T, chain.r, chain.q, "call")
            errs.append((iv_fit - iv_mkt) if iv_fit is not None else 1.0)  # penalize un-invertible prices
        return float(np.sum(np.square(errs)))

    res = minimize(objective, x0, method="Nelder-Mead",
                    options={"maxiter": 2000, "xatol": 1e-6, "fatol": 1e-8})
    fitted = model_cls.from_array(np.clip(res.x, [b[0] for b in bounds], [b[1] for b in bounds]))

    fitted_ivs = []
    for k in strikes:
        price = lewis_price(fitted, spot, k, T, chain.r, chain.q, "call")
        fitted_ivs.append(implied_vol(price, spot, k, T, chain.r, chain.q, "call") or np.nan)
    fitted_ivs = np.array(fitted_ivs)

    rmse = float(np.sqrt(np.nanmean((fitted_ivs - market_ivs) ** 2)))

    return CalibrationResult(
        model_name=model_cls.name,
        params=dict(zip(model_cls.param_names, fitted.to_array())),
        rmse_iv=rmse,
        fitted_ivs=fitted_ivs,
        market_ivs=market_ivs,
        strikes=strikes,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/test_jump_diffusion_calibration.py -v`
Expected: PASS. If `rmse_iv` doesn't converge under 0.01, raise `maxiter` to 4000 before touching
seeds — Merton's 4-param objective is well-behaved and should converge from the default seed.

- [ ] **Step 5: Commit**

```bash
git add Vol_Suite/jump_diffusion/calibration.py Vol_Suite/tests/test_jump_diffusion_calibration.py
git commit -m "feat(vol-suite): add IV-space Nelder-Mead calibration engine"
```

---

## Task 5: Calibration round-trip tests for remaining 4 models

**Files:**
- Modify: `Vol_Suite/tests/test_jump_diffusion_calibration.py`

**Interfaces:**
- Consumes: everything from Task 4, plus `HestonModel, BatesModel, KouModel, VarianceGammaModel`.

- [ ] **Step 1: Write the failing tests**

```python
# append to Vol_Suite/tests/test_jump_diffusion_calibration.py
from jump_diffusion.models import HestonModel, BatesModel, KouModel, VarianceGammaModel


def test_heston_calibration_recovers_known_params():
    S0, T, r, q = 100.0, 0.5, 0.03, 0.0
    strikes = np.array([80.0, 90.0, 95.0, 100.0, 105.0, 110.0, 120.0])
    true_model = HestonModel(kappa=2.0, theta=0.04, xi=0.5, rho=-0.6, v0=0.045)
    chain = _synthetic_chain(true_model, S0, T, r, q, strikes)
    result = calibrate(HestonModel, chain, S0, T)
    assert result.rmse_iv < 0.01


def test_bates_calibration_recovers_known_params():
    S0, T, r, q = 100.0, 0.5, 0.03, 0.0
    strikes = np.array([80.0, 90.0, 95.0, 100.0, 105.0, 110.0, 120.0])
    true_model = BatesModel(kappa=2.0, theta=0.04, xi=0.5, rho=-0.6, v0=0.04,
                             lam=0.6, mu_j=-0.06, sigma_j=0.1)
    chain = _synthetic_chain(true_model, S0, T, r, q, strikes)
    result = calibrate(BatesModel, chain, S0, T)
    assert result.rmse_iv < 0.015  # 7-param fit -- slightly looser tolerance


def test_kou_calibration_recovers_known_params():
    S0, T, r, q = 100.0, 0.5, 0.03, 0.0
    strikes = np.array([80.0, 90.0, 95.0, 100.0, 105.0, 110.0, 120.0])
    true_model = KouModel(sigma=0.18, lam=0.8, p=0.35, eta1=12.0, eta2=6.0)
    chain = _synthetic_chain(true_model, S0, T, r, q, strikes)
    result = calibrate(KouModel, chain, S0, T)
    assert result.rmse_iv < 0.01


def test_vg_calibration_recovers_known_params():
    S0, T, r, q = 100.0, 0.5, 0.03, 0.0
    strikes = np.array([80.0, 90.0, 95.0, 100.0, 105.0, 110.0, 120.0])
    true_model = VarianceGammaModel(sigma=0.19, nu=0.25, theta_vg=-0.12)
    chain = _synthetic_chain(true_model, S0, T, r, q, strikes)
    result = calibrate(VarianceGammaModel, chain, S0, T)
    assert result.rmse_iv < 0.01
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/test_jump_diffusion_calibration.py -v`
Expected: FAIL (`NameError: name 'HestonModel' is not defined` before the import line runs — actually
collection error) — confirms the new tests don't pass trivially before real work is checked.

- [ ] **Step 3: No new implementation needed**

Task 4's `calibrate()` is model-agnostic (it only calls `model_cls.name`, `.param_names`,
`.PARAM_BOUNDS`, `.from_array`, `.to_array`) — these 4 tests exercise it against the models added
in Task 3. If a specific model's RMSE doesn't converge under tolerance, the fix is a better seed in
`_DEFAULT_SEEDS` (Task 4's file), not new calibration code.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/test_jump_diffusion_calibration.py -v`
Expected: PASS (5 tests total)

- [ ] **Step 5: Commit**

```bash
git add Vol_Suite/tests/test_jump_diffusion_calibration.py Vol_Suite/jump_diffusion/calibration.py
git commit -m "test(vol-suite): verify calibration round-trip for all 5 jump-diffusion models"
```

---

## Task 6: Comparison mode (all 5 models + Heston-vs-Bates jump contribution)

**Files:**
- Create: `Vol_Suite/jump_diffusion/comparison.py`
- Test: `Vol_Suite/tests/test_jump_diffusion_comparison.py`

**Interfaces:**
- Consumes: `ALL_MODELS` (Task 3), `calibrate` (Task 4), `variance_swap_live.ChainData`.
- Produces: `run_comparison(chain: ChainData, spot: float, T: float) -> dict` returning
  `{"models": {model_name: CalibrationResult, ...}, "best_fit": str, "jump_contribution": {"strikes": np.ndarray, "heston_ivs": np.ndarray, "bates_ivs": np.ndarray, "delta_iv": np.ndarray}}`.

- [ ] **Step 1: Write the failing test**

```python
# Vol_Suite/tests/test_jump_diffusion_comparison.py
import numpy as np
from jump_diffusion.models import BatesModel
from jump_diffusion.pricer import lewis_price
from jump_diffusion.comparison import run_comparison
from implied_vol import implied_vol
from variance_swap_live import ChainData


def test_run_comparison_returns_all_5_models_and_jump_contribution():
    S0, T, r, q = 100.0, 0.5, 0.03, 0.0
    strikes = np.array([80.0, 90.0, 95.0, 100.0, 105.0, 110.0, 120.0])
    true_model = BatesModel(kappa=2.0, theta=0.04, xi=0.5, rho=-0.6, v0=0.04,
                             lam=0.6, mu_j=-0.06, sigma_j=0.1)
    call_prices = np.array([lewis_price(true_model, S0, k, T, r, q, "call") for k in strikes])
    call_ivs = np.array([implied_vol(p, S0, k, T, r, q, "call") for p, k in zip(call_prices, strikes)])
    chain = ChainData(expiry="20270101", strikes=strikes, call_mid=call_prices,
                       put_mid=call_prices, r=r, q=q, call_iv=call_ivs, put_iv=call_ivs)

    result = run_comparison(chain, S0, T)

    assert set(result["models"].keys()) == {"Merton", "Heston", "Bates", "Kou", "VarianceGamma"}
    assert result["best_fit"] in result["models"]
    assert len(result["jump_contribution"]["delta_iv"]) == len(strikes)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/test_jump_diffusion_comparison.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jump_diffusion.comparison'`

- [ ] **Step 3: Write minimal implementation**

```python
# Vol_Suite/jump_diffusion/comparison.py
"""All-5-model calibration for manual comparison -- never called from the
unified pipeline (see JUMP_MODEL_DEFAULT in volatility_suite.py for the
single-model fast path). Invoke explicitly from Vol_Suite's interactive
menu or a CLI flag when deciding which model to set as the default."""

import numpy as np

from jump_diffusion.calibration import calibrate
from jump_diffusion.models import ALL_MODELS, HestonModel, BatesModel
from jump_diffusion.pricer import lewis_price
from implied_vol import implied_vol


def run_comparison(chain, spot: float, T: float) -> dict:
    models = {}
    for model_cls in ALL_MODELS:
        try:
            models[model_cls.name] = calibrate(model_cls, chain, spot, T)
        except Exception as exc:  # noqa: BLE001 -- one model's failure must not sink the comparison
            models[model_cls.name] = None
            print(f"  [jump_diffusion] {model_cls.name} calibration failed: {exc}")

    valid = {k: v for k, v in models.items() if v is not None}
    best_fit = min(valid, key=lambda k: valid[k].rmse_iv) if valid else None

    jump_contribution = {"strikes": chain.strikes, "heston_ivs": np.array([]),
                          "bates_ivs": np.array([]), "delta_iv": np.array([])}
    if models.get("Heston") is not None and models.get("Bates") is not None:
        heston_fit = HestonModel(**models["Heston"].params)
        bates_fit = BatesModel(**models["Bates"].params)
        heston_ivs, bates_ivs = [], []
        for k in chain.strikes:
            hp = lewis_price(heston_fit, spot, k, T, chain.r, chain.q, "call")
            bp = lewis_price(bates_fit, spot, k, T, chain.r, chain.q, "call")
            heston_ivs.append(implied_vol(hp, spot, k, T, chain.r, chain.q, "call") or np.nan)
            bates_ivs.append(implied_vol(bp, spot, k, T, chain.r, chain.q, "call") or np.nan)
        heston_ivs, bates_ivs = np.array(heston_ivs), np.array(bates_ivs)
        jump_contribution = {
            "strikes": chain.strikes, "heston_ivs": heston_ivs, "bates_ivs": bates_ivs,
            "delta_iv": bates_ivs - heston_ivs,  # the "visible jump contribution" from the AIGamma teardown
        }

    return {"models": models, "best_fit": best_fit, "jump_contribution": jump_contribution}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/test_jump_diffusion_comparison.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add Vol_Suite/jump_diffusion/comparison.py Vol_Suite/tests/test_jump_diffusion_comparison.py
git commit -m "feat(vol-suite): add all-5-model comparison mode with jump-contribution overlay"
```

---

## Task 7: GARCH tie 1 — Merton-informed jump-day filter

**Files:**
- Create: `Vol_Suite/jump_diffusion/garch_bridge.py`
- Test: `Vol_Suite/tests/test_garch_bridge.py`

**Interfaces:**
- Produces: `jump_filtered_returns(log_returns: np.ndarray, merton_sigma: float, k: float = 4.0) -> tuple[np.ndarray, np.ndarray]`
  returning `(filtered_returns, jump_day_mask)` — a Lee-Mykland-style statistical jump test using
  the *options-implied* diffusive vol (`merton_sigma`, from a Merton calibration) as the threshold
  scale, rather than a naive rolling-std threshold that jumps themselves would contaminate.

- [ ] **Step 1: Write the failing test**

```python
# Vol_Suite/tests/test_garch_bridge.py
import numpy as np
from jump_diffusion.garch_bridge import jump_filtered_returns


def test_jump_filtered_returns_flags_injected_jump():
    rng = np.random.default_rng(42)
    daily_sigma = 0.20 / np.sqrt(252)
    returns = rng.normal(0, daily_sigma, 250)
    returns[100] = 0.15  # inject an obvious one-day jump (15% move)

    filtered, mask = jump_filtered_returns(returns, merton_sigma=0.20, k=4.0)

    assert mask[100] == True  # noqa: E712 -- explicit bool check reads clearer here
    assert mask.sum() < 10  # a handful of days at most should trip a 4-sigma threshold
    assert filtered[100] != returns[100]  # the jump day was actually filtered, not just flagged
    assert len(filtered) == len(returns)


def test_jump_filtered_returns_no_nans():
    rng = np.random.default_rng(7)
    returns = rng.normal(0, 0.01, 100)
    filtered, mask = jump_filtered_returns(returns, merton_sigma=0.18)
    assert not np.any(np.isnan(filtered))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/test_garch_bridge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jump_diffusion.garch_bridge'`

- [ ] **Step 3: Write minimal implementation**

```python
# Vol_Suite/jump_diffusion/garch_bridge.py
"""Two ties between the jump-diffusion model zoo and Vol_Suite's existing
GARCH(1,1) fit (garch_analysis.py):

1. jump_filtered_returns -- flag/winsorize jump-attributable days in the
   historical return series *before* GARCH sees them, using the current
   Merton-implied diffusive vol as a measure-consistent threshold scale (a
   naive rolling-std threshold is contaminated by the jumps it's trying to
   detect).
2. adjust_garch_forecast -- post-hoc scale the GARCH conditional-vol
   forecast by the Bates jump-variance share. NOTE: this is a forecast
   adjustment, not a re-estimated GARCH-X historical fit -- a true
   GARCH-X exogenous-regressor fit would need a *daily historical* series
   of jump-variance-share, which would require calibrating Bates against a
   full option chain for every historical day (not available in this
   pipeline, which only has today's chain). This is a deliberate scope
   narrowing from the design spec's literal "GARCH-X variance equation"
   language -- see design spec's Architecture section.
"""

import numpy as np


def jump_filtered_returns(log_returns: np.ndarray, merton_sigma: float, k: float = 4.0) -> tuple:
    """Flag days where |return| exceeds k * daily-sigma (implied by
    merton_sigma, an annualized vol) as jump days, and replace them with the
    threshold value (winsorize, sign-preserved) so GARCH's fit reflects
    diffusive clustering only. Returns (filtered_returns, jump_day_mask).
    """
    log_returns = np.asarray(log_returns, dtype=float)
    daily_sigma = merton_sigma / np.sqrt(252)
    threshold = k * daily_sigma
    mask = np.abs(log_returns) > threshold
    filtered = log_returns.copy()
    filtered[mask] = np.sign(log_returns[mask]) * threshold
    return filtered, mask


def adjust_garch_forecast(garch_conditional_vol: float, jump_variance_share: float, gamma: float = 0.25) -> float:
    """Scale a GARCH conditional-vol forecast upward by the option-implied
    jump-variance share. gamma is a tunable sensitivity (default 0.25 means
    a jump_variance_share of 1.0 -- all variance is jump-attributable --
    scales the forecast up 25%); this is a heuristic starting point, not a
    fitted coefficient, and is expected to be tuned once live data exists.
    """
    return garch_conditional_vol * (1.0 + gamma * jump_variance_share)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/test_garch_bridge.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add Vol_Suite/jump_diffusion/garch_bridge.py Vol_Suite/tests/test_garch_bridge.py
git commit -m "feat(vol-suite): add Merton jump-day filter for GARCH input"
```

---

## Task 8: GARCH tie 2 — jump-variance-share forecast adjustment test + integration point

**Files:**
- Modify: `Vol_Suite/tests/test_garch_bridge.py`
- Modify: `Vol_Suite/garch_analysis.py`

**Interfaces:**
- Consumes: `garch_bridge.adjust_garch_forecast` (Task 7).
- Produces: `garch_analysis.run_garch_module` gains an optional keyword
  `jump_variance_share: float | None = None`; when provided, the returned annualized conditional
  vol (3rd tuple element) is passed through `adjust_garch_forecast` before being returned. `None`
  (default) leaves current behavior byte-for-byte identical.

- [ ] **Step 1: Write the failing test**

```python
# append to Vol_Suite/tests/test_garch_bridge.py
from jump_diffusion.garch_bridge import adjust_garch_forecast


def test_adjust_garch_forecast_scales_up_with_jump_share():
    base = 0.20
    assert adjust_garch_forecast(base, jump_variance_share=0.0) == base
    adjusted = adjust_garch_forecast(base, jump_variance_share=1.0, gamma=0.25)
    assert np.isclose(adjusted, base * 1.25)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/test_garch_bridge.py -v`
Expected: FAIL — `adjust_garch_forecast` already exists from Task 7, but this asserts its exact
scaling behavior for the first time; if it fails, the bug is in Task 7's formula, not new code.

- [ ] **Step 3: Wire the optional kwarg into `run_garch_module`**

Read `Vol_Suite/garch_analysis.py:349-380` (`run_garch_module`) first — the change is additive at
the return point only, not to the fit itself:

```python
# Vol_Suite/garch_analysis.py -- modify run_garch_module's signature and return
def run_garch_module(ticker: str, start: str = DEFAULT_START, end: str = None,
                      output_dir: str = None, jump_variance_share: float = None) -> tuple:
    # ... existing body unchanged up through computing `annualized_conditional_vol` ...
    # immediately before the final `return GarchModuleResult(files, interp, annualized_conditional_vol)`:
    if jump_variance_share is not None and annualized_conditional_vol is not None:
        from jump_diffusion.garch_bridge import adjust_garch_forecast
        annualized_conditional_vol = adjust_garch_forecast(annualized_conditional_vol, jump_variance_share)
    return GarchModuleResult(files, interp, annualized_conditional_vol)
```

(The import is deliberately local to `run_garch_module`, not top-of-file, to avoid a hard
`jump_diffusion` import in `garch_analysis.py` for the common case where no jump result exists yet
— matches the codebase's existing pattern of narrow, call-site-local imports for optional
integrations.)

- [ ] **Step 4: Run tests to verify they pass, and existing GARCH tests still pass**

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/test_garch_bridge.py -v`
Expected: PASS (3 tests total)

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/ -k garch -v`
Expected: PASS — all pre-existing GARCH tests unaffected (new kwarg defaults to `None`).

- [ ] **Step 5: Commit**

```bash
git add Vol_Suite/garch_analysis.py Vol_Suite/tests/test_garch_bridge.py
git commit -m "feat(vol-suite): wire jump-variance-share GARCH forecast adjustment into run_garch_module"
```

---

## Task 9: `suite_context.json` schema — add `jump_diffusion` block

**Files:**
- Modify: `Vol_Suite/suite_context.py:109-207` (`build_suite_context`)
- Modify: `Vol_Suite/suite_context.py:210` (`validate_suite_context`, the Vol_Suite-local one)
- Modify: `shared/schemas.py:107` (`validate_suite_context`, the shared/consumer-facing one)
- Test: `Vol_Suite/tests/test_context_mode.py` (existing file — add a case)

**Interfaces:**
- Produces: `build_suite_context(..., jump_diffusion: dict | None = None, ...)` writes a new
  top-level `"jump_diffusion"` key into the returned context dict:
  `{"model_name": str, "params": dict, "rmse_iv": float, "jump_variance_share": float | None} | None`.

- [ ] **Step 1: Write the failing test**

First read `Vol_Suite/tests/test_context_mode.py` to match its existing fixture/assertion style,
then add:

```python
# append to Vol_Suite/tests/test_context_mode.py
def test_build_suite_context_includes_jump_diffusion_block():
    from suite_context import build_suite_context

    jd = {"model_name": "Bates", "params": {"kappa": 2.0}, "rmse_iv": 0.008,
          "jump_variance_share": 0.35}
    ctx = build_suite_context(
        output_dir=".", run_id="test-run", ticker="SPY", option_type="call",
        strike=None, target_years=0.5, expiration_date="20270101",
        index_ticker="SPY", basket_tickers=["SPY"], basket_weights=[1.0],
        sentiment_manifest_path=".", jump_diffusion=jd,
    )
    assert ctx["jump_diffusion"] == jd


def test_build_suite_context_jump_diffusion_defaults_to_none():
    from suite_context import build_suite_context

    ctx = build_suite_context(
        output_dir=".", run_id="test-run", ticker="SPY", option_type="call",
        strike=None, target_years=0.5, expiration_date="20270101",
        index_ticker="SPY", basket_tickers=["SPY"], basket_weights=[1.0],
        sentiment_manifest_path=".",
    )
    assert ctx["jump_diffusion"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/test_context_mode.py -k jump_diffusion -v`
Expected: FAIL with `TypeError: build_suite_context() got an unexpected keyword argument 'jump_diffusion'`

- [ ] **Step 3: Add the kwarg and key**

In `Vol_Suite/suite_context.py`, add to `build_suite_context`'s signature (after
`expected_return: Optional[float] = None,`):

```python
    jump_diffusion: Optional[Dict[str, Any]] = None,
```

And inside the `context = {...}` dict literal, after the `"strategies": []` line:

```python
        # Jump-diffusion model-zoo result (Phase 1 of the AIGamma steal-list,
        # docs/superpowers/specs/2026-08-30-jump-diffusion-model-zoo-design.md).
        # None when calibration was skipped or failed -- every downstream
        # consumer (dealer positioning, VRP, strategy recommender) must treat
        # None as "feature unavailable this run" and fall back to pre-Phase-1
        # behavior, matching the null-safe discipline var.positions already
        # documents.
        "jump_diffusion": jump_diffusion,
```

Then update `Vol_Suite/suite_context.py`'s own `validate_suite_context` (line 210) to accept the
new top-level key — read that function first to match its existing key-checking style (likely a
set of required/optional top-level keys) before adding `"jump_diffusion"` to whatever
optional-keys collection it uses.

Then update `shared/schemas.py::validate_suite_context` (line 107) the same way — read it first,
since its checking style may differ from the Vol_Suite-local copy (this is the CLAUDE.md-flagged
same-name-different-function gotcha; both must be updated, neither implies the other).

- [ ] **Step 4: Run tests to verify they pass, plus the full existing context-mode suite**

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/test_context_mode.py -v`
Expected: PASS, including all pre-existing tests in that file (the new kwarg defaults to `None`,
so every existing caller is unaffected).

Run: `..\.venv\Scripts\python.exe -m pytest tests/test_orchestrator_market_signals.py -v` (from
repo root) — Expected: PASS, unaffected by an additive optional field.

- [ ] **Step 5: Commit**

```bash
git add Vol_Suite/suite_context.py shared/schemas.py Vol_Suite/tests/test_context_mode.py
git commit -m "feat(vol-suite): add jump_diffusion block to suite_context.json schema"
```

---

## Task 10: Wire into `_run_core_analysis` — `JUMP_MODEL_DEFAULT` config + default-model calibration

**Files:**
- Modify: `Vol_Suite/volatility_suite.py:948-1300` (`_run_core_analysis`, near the existing GARCH
  call site at `:1258-1281`)
- Modify: `Vol_Suite/volatility_suite.py:1644` (the `build_suite_context(...)` call)
- Test: `Vol_Suite/tests/test_jump_diffusion_default_calibration.py`

**Interfaces:**
- Consumes: `variance_swap_live.fetch_chain_thetadata(td, ticker, expiration, r, q) -> ChainData`
  (`variance_swap_live.py:104`), `calibrate` (Task 4), `BatesModel` (Task 3, for
  `.jump_variance_share(T)`).
- Produces: config value `JUMP_MODEL_DEFAULT` (module-level constant in `volatility_suite.py`,
  reads `os.getenv("JUMP_MODEL_DEFAULT", "Bates")`); `artifacts["jump_diffusion"]` populated with
  the same shape as Task 9's `jump_diffusion` dict, or `None` on any failure.

- [ ] **Step 1: Write the failing test**

This test targets `_calibrate_default_jump_model` directly (it's self-contained per Step 3 below,
so it needs no `_run_core_analysis` harness or fixtures):

```python
# Vol_Suite/tests/test_jump_diffusion_default_calibration.py
def test_calibrate_default_jump_model_returns_none_on_failure(monkeypatch):
    """A chain-fetch failure (bad ticker, no listed expiry, ThetaData down,
    etc.) must return None, never raise -- callers in _run_core_analysis
    depend on this to keep a jump-diffusion outage from aborting the run."""
    import volatility_suite
    import thetadata_client

    class _BoomController:
        def fetch_spot_price(self, ticker):
            raise RuntimeError("ThetaData unavailable")

    # _calibrate_default_jump_model does `from thetadata_client import
    # ThetaDataController` inside its own body (a fresh lookup on every
    # call), so patching the name on thetadata_client itself -- not on
    # volatility_suite -- is what actually intercepts the constructor call.
    monkeypatch.setattr(thetadata_client, "ThetaDataController", _BoomController)

    result = volatility_suite._calibrate_default_jump_model("SPY", "20270101", 0.5)

    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/test_jump_diffusion_default_calibration.py -v`
Expected: FAIL with `AttributeError: module 'volatility_suite' has no attribute '_calibrate_default_jump_model'`

- [ ] **Step 3: Implement the hook**

`_run_core_analysis`'s own parameters are `ticker: str`, `target_years: float`,
`expiration: str` (confirmed at `volatility_suite.py:948-966` — note the parameter is named
`expiration`, not `expiration_date`). It does not carry `spot`/`r`/`q`/`td` as ready-made local
variables shared across its whole ~700-line body (each sub-section, e.g. GARCH, fetches what it
needs independently via its own `fetch_price_history`/`ThetaDataController` calls). Rather than
threading those through, `_calibrate_default_jump_model` fetches its own spot/rate/dividend/chain
data self-contained, exactly like `garch_analysis.run_garch_module` already does for its own
inputs. Add near the top of `volatility_suite.py` (with the other module-level config reads):

```python
JUMP_MODEL_DEFAULT = os.getenv("JUMP_MODEL_DEFAULT", "Bates")


def _calibrate_default_jump_model(ticker: str, expiration: str, target_years: float):
    """Calibrate JUMP_MODEL_DEFAULT against the focus ticker's chain at
    *expiration*. Returns a dict shaped for suite_context.json's
    jump_diffusion key, or None on any failure -- never raises (matches the
    GARCH call site's error-swallowing discipline at this same call level).
    Self-contained: fetches its own spot/rate/dividend/chain data, the same
    way garch_analysis.run_garch_module fetches its own price history,
    rather than depending on _run_core_analysis's internal variable soup.
    """
    from jump_diffusion.calibration import calibrate
    from jump_diffusion.models import ALL_MODELS, BatesModel
    from variance_swap_live import fetch_chain_thetadata
    from thetadata_client import ThetaDataController

    model_cls = next((m for m in ALL_MODELS if m.name == JUMP_MODEL_DEFAULT), BatesModel)
    try:
        td = ThetaDataController()
        spot = float(td.fetch_spot_price(ticker))
        r = float(td.fetch_risk_free_rate(target_years))
        q = float(td.fetch_dividend_yield(ticker, spot))
        chain = fetch_chain_thetadata(td, ticker, expiration, r, q)
        result = calibrate(model_cls, chain, spot, target_years)
        jump_variance_share = None
        if model_cls is BatesModel:
            jump_variance_share = model_cls.from_array(
                [result.params[p] for p in model_cls.param_names]
            ).jump_variance_share(target_years)
        return {
            "model_name": result.model_name,
            "params": result.params,
            "rmse_iv": result.rmse_iv,
            "jump_variance_share": jump_variance_share,
        }
    except Exception as exc:
        print(f"  [jump_diffusion] {JUMP_MODEL_DEFAULT} calibration failed: {exc}")
        return None
```

In `_run_core_analysis`, immediately after the existing GARCH block (`:1258-1281`, right after
`artifacts["garch_conditional_vol"] = garch_conditional_vol` at line 1281), add:

```python
    artifacts["jump_diffusion"] = _calibrate_default_jump_model(ticker, expiration, target_years)
```

Then at the `build_suite_context(...)` call site (`:1644`), add:

```python
        jump_diffusion=artifacts.get("jump_diffusion"),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/test_jump_diffusion_default_calibration.py -v`
Expected: PASS

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/test_context_mode.py -v`
Expected: PASS, all pre-existing tests unaffected.

- [ ] **Step 5: Commit**

```bash
git add Vol_Suite/volatility_suite.py Vol_Suite/tests/test_jump_diffusion_default_calibration.py
git commit -m "feat(vol-suite): calibrate JUMP_MODEL_DEFAULT in _run_core_analysis, thread into suite_context"
```

---

## Task 11: Dealer positioning tie — `jump_variance_share` on `ProductionDealerExposure`

**Files:**
- Modify: `Vol_Suite/expiry_book_production.py:85-100` (`ProductionDealerExposure` dataclass)
- Modify: `Vol_Suite/expiry_book_production.py:201` (`fetch_production_result`)
- Test: `Vol_Suite/tests/test_expiry_book_phase1_greeks.py` or a new
  `Vol_Suite/tests/test_jump_diffusion_dealer_tie.py` (create if no existing file is a natural fit
  — check the phase-numbered test files first for one that already constructs
  `ProductionDealerExposure` directly, and extend that one instead of duplicating fixtures)

**Interfaces:**
- Consumes: nothing new at the dataclass level — just a new optional field.
- Produces: `ProductionDealerExposure.jump_variance_share: float | None = None` (new field, added
  after `vanna_flow_provenance` to match the dataclass's existing pattern of trailing optional
  fields with defaults); `fetch_production_result` gains an optional keyword
  `jump_variance_share: float | None = None`, stored straight into the returned object.

- [ ] **Step 1: Write the failing test**

```python
# Vol_Suite/tests/test_jump_diffusion_dealer_tie.py
def test_production_dealer_exposure_carries_jump_variance_share():
    from expiry_book_production import ProductionDealerExposure

    # Construct with the minimum required fields plus the new optional one --
    # match whatever the dataclass's other required fields are (snapshot,
    # execution_locus, scenario_budget, structural, units, provenance) using
    # the same trivial/mock values the phase-test files already use for these
    # (read test_expiry_book_phase1_greeks.py's fixture construction first).
    exposure = ProductionDealerExposure(
        ticker="SPY", expiry="20270101", spot=500.0, status="ok",
        snapshot=None, execution_locus=None, scenario_budget=None,
        structural=None, units={}, provenance={}, jump_variance_share=0.35,
    )
    assert exposure.jump_variance_share == 0.35


def test_production_dealer_exposure_jump_variance_share_defaults_to_none():
    from expiry_book_production import ProductionDealerExposure

    exposure = ProductionDealerExposure(
        ticker="SPY", expiry="20270101", spot=500.0, status="ok",
        snapshot=None, execution_locus=None, scenario_budget=None,
        structural=None, units={}, provenance={},
    )
    assert exposure.jump_variance_share is None
```

Note: if `snapshot`/`execution_locus`/`scenario_budget`/`structural` are typed as non-Optional
dataclasses that reject `None` at construction (check `ebe.NetExposure`, `ebe.ExecutionLocus`,
`ebe.ScenarioBudget`, `StructuralStatus` definitions first), use the same minimal-fixture
construction pattern the existing phase-test files already use for these — do not invent new
fixture shapes.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/test_jump_diffusion_dealer_tie.py -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'jump_variance_share'`

- [ ] **Step 3: Add the field**

In `Vol_Suite/expiry_book_production.py`, add to `ProductionDealerExposure` as the new last field
(after `prior_asof: str | None = None`, line 111):

```python
    jump_variance_share: float | None = None
```

And in `fetch_production_result`'s signature (line 201), add `jump_variance_share: float | None =
None`. The function's `return ProductionDealerExposure(...)` call is at line 548 — add
`jump_variance_share=jump_variance_share,` as a new line in that call (e.g. right after
`flow_volume_rows=int(n_vol),` at line 568).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/test_jump_diffusion_dealer_tie.py -v`
Expected: PASS (2 tests)

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/ -k expiry_book -v`
Expected: PASS — all pre-existing expiry_book/dealer-positioning tests unaffected (new field is
optional with a default).

- [ ] **Step 5: Commit**

```bash
git add Vol_Suite/expiry_book_production.py Vol_Suite/tests/test_jump_diffusion_dealer_tie.py
git commit -m "feat(vol-suite): add optional jump_variance_share field to dealer-book exposure"
```

---

## Task 12: VRP term structure tie — `model_implied_vrp_pct` per tenor

**Files:**
- Modify: `Vol_Suite/vrp_term_structure.py:52-63` (`VrpTermPoint` dataclass)
- Modify: `Vol_Suite/vrp_term_structure.py:117` (`compute_vrp_term_structure`)
- Test: `Vol_Suite/tests/test_jump_diffusion_vrp_tie.py`

**Interfaces:**
- Consumes: `calibrate`, `BatesModel` (or `JUMP_MODEL_DEFAULT`'s model class).
- Produces: `VrpTermPoint.model_implied_vrp_pct: float | None = None` (new trailing optional
  field); `compute_vrp_term_structure` gains an optional keyword `jump_model_cls=None` — when
  provided, calibrates that model per tenor and computes
  `model_implied_vrp_pct = 100 * sqrt(v0-or-equivalent-implied-variance) - atm_iv_pct`
  as a cross-check column against the existing Carr-Madan `fair_vol_pct`.

- [ ] **Step 1: Write the failing test**

```python
# Vol_Suite/tests/test_jump_diffusion_vrp_tie.py
def test_vrp_term_point_has_model_implied_vrp_field_defaulting_to_none():
    from vrp_term_structure import VrpTermPoint

    point = VrpTermPoint(
        expiry_label="1mo", expiry_date="20270101", T_years=1 / 12,
        fair_vol_pct=20.0, atm_iv_pct=19.0, vrp_pct=1.0, rv_30d_pct=18.0,
    )
    assert point.model_implied_vrp_pct is None


def test_vrp_term_point_accepts_model_implied_vrp():
    from vrp_term_structure import VrpTermPoint

    point = VrpTermPoint(
        expiry_label="1mo", expiry_date="20270101", T_years=1 / 12,
        fair_vol_pct=20.0, atm_iv_pct=19.0, vrp_pct=1.0, rv_30d_pct=18.0,
        model_implied_vrp_pct=1.3,
    )
    assert point.model_implied_vrp_pct == 1.3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/test_jump_diffusion_vrp_tie.py -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'model_implied_vrp_pct'`

- [ ] **Step 3: Add the field and wire calibration in**

In `Vol_Suite/vrp_term_structure.py`, add to `VrpTermPoint` (after `rv_30d_pct: float`, line 62):

```python
    model_implied_vrp_pct: float | None = None  # cross-check against fair_vol_pct via a jump-diffusion model
```

In `compute_vrp_term_structure`'s signature (line 117), add an optional keyword
`jump_model_cls=None`. Inside its per-tenor `for label, target_years in TARGET_TENORS:` loop
(line 155), the chain is already fetched at line 163
(`chain = fetch_chain_thetadata(td, ticker, expiry_str, r, q)`) and `fair_vol_pct`/`atm_iv_pct`
are computed at lines 168-169. Insert the new calibration block immediately after line 170
(`vrp = fair_vol_pct - atm_iv_pct`), before the realized-vol block at line 173:

```python
            model_implied_vrp_pct = None
            if jump_model_cls is not None:
                try:
                    from jump_diffusion.calibration import calibrate
                    jd_result = calibrate(jump_model_cls, chain, spot, actual_T)
                    # ATM fitted IV is the model's own read on the ATM point --
                    # closest strike to spot in the calibrated chain.
                    atm_idx = int(np.argmin(np.abs(jd_result.strikes - spot)))
                    model_iv_pct = float(jd_result.fitted_ivs[atm_idx]) * 100
                    model_implied_vrp_pct = model_iv_pct - atm_iv_pct
                except Exception as exc:
                    print(f"  [jump_diffusion] VRP tie calibration failed for {label}: {exc}")
```

Then add `model_implied_vrp_pct=model_implied_vrp_pct,` to the `VrpTermPoint(...)` construction at
lines 181-190 (the success-path one only — the `except Exception:` fallback path at lines 195-205
stays `None` by the field's own default, no change needed there).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/test_jump_diffusion_vrp_tie.py -v`
Expected: PASS (2 tests)

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/ -k vrp -v`
Expected: PASS — pre-existing VRP tests unaffected (`jump_model_cls` defaults to `None`, skipping
the new code path entirely).

- [ ] **Step 5: Commit**

```bash
git add Vol_Suite/vrp_term_structure.py Vol_Suite/tests/test_jump_diffusion_vrp_tie.py
git commit -m "feat(vol-suite): add optional model-implied VRP cross-check per tenor"
```

---

## Task 13: Strategy recommender tie — `jump_risk_signal` biases toward convexity

**Files:**
- Modify: `Vol_Suite/strategy_recommender.py:43-103` (`StrategyRecommender.__init__`)
- Modify: `Vol_Suite/strategy_recommender.py:537-576` (`_rank_strategies`)
- Test: `Vol_Suite/tests/test_jump_diffusion_recommender_tie.py`

**Interfaces:**
- Consumes: nothing new at the type level — a plain `dict`.
- Produces: `StrategyRecommender.__init__` gains an optional keyword
  `jump_risk_signal: dict | None = None` (expects `{"jump_variance_share": float}` when present);
  `_rank_strategies` adds a convexity bonus to `rank_score` for gamma-positive legs
  (straddle/strangle/reverse_strangle) when `jump_variance_share > 0.3`.

- [ ] **Step 1: Write the failing test**

```python
# Vol_Suite/tests/test_jump_diffusion_recommender_tie.py
from strategy_recommender import StrategyRecommender


def _chain_data():
    return {
        "strikes": [95.0, 100.0, 105.0],
        "delta": [0.6, 0.5, 0.4],
        "gamma": [0.05, 0.06, 0.05],
        "theta": [-0.03, -0.03, -0.03],
        "vega": [0.15, 0.16, 0.15],
        "vanna": [0.01, 0.01, 0.01],
        "bid_ask_spread": [0.05, 0.05, 0.05],
        "open_interest": [500, 500, 500],
    }


def test_recommender_accepts_jump_risk_signal_and_defaults_to_none():
    rec = StrategyRecommender(
        chain_data=_chain_data(),
        edge_strikes=[{"strike": 100.0, "edge_type": "BUY"}],
        vol_regime="FAIR", current_price=100.0, expiry_days=30,
    )
    assert rec.jump_risk_signal is None


def test_recommender_elevated_jump_share_boosts_gamma_strategy_rank():
    kwargs = dict(
        chain_data=_chain_data(),
        edge_strikes=[{"strike": 100.0, "edge_type": "BUY"}],
        vol_regime="FAIR", current_price=100.0, expiry_days=30,
    )
    baseline = StrategyRecommender(**kwargs).recommend()
    with_jump_risk = StrategyRecommender(
        **kwargs, jump_risk_signal={"jump_variance_share": 0.5},
    ).recommend()

    baseline_straddle = next(s for s in baseline if s["strategy_type"] == "straddle")
    boosted_straddle = next(s for s in with_jump_risk if s["strategy_type"] == "straddle")
    assert boosted_straddle["rank_score"] > baseline_straddle["rank_score"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/test_jump_diffusion_recommender_tie.py -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'jump_risk_signal'`

- [ ] **Step 3: Wire the optional param through**

In `StrategyRecommender.__init__` (`strategy_recommender.py:65-87`), add
`jump_risk_signal: dict | None = None` to the signature and `self.jump_risk_signal =
jump_risk_signal` in the body.

In `_rank_strategies` (`strategy_recommender.py:537-576`), after the existing per-regime scoring
block (the `if self.vol_regime == "RICH": ... elif ...` chain), add:

```python
            # Jump-risk bias: elevated option-implied jump-variance share
            # favors convexity (long-gamma) legs regardless of regime --
            # additive to the regime-driven score above, never a replacement.
            if self.jump_risk_signal and self.jump_risk_signal.get("jump_variance_share", 0.0) > 0.3:
                if strategy.strategy_type in ("straddle", "strangle", "reverse_strangle"):
                    score += abs(strategy.greeks_summary["gamma"]) * 5.0

            strategy.rank_score = score
```

(Locate the exact line where `strategy.rank_score = score` — or equivalent — currently gets set at
the end of the per-strategy loop in `_rank_strategies`, and insert the jump-risk block immediately
before it, so it applies after every regime branch.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/test_jump_diffusion_recommender_tie.py -v`
Expected: PASS (2 tests)

Run: `cd Vol_Suite && ..\.venv\Scripts\python.exe -m pytest tests/ -k strategy_recommender -v` (or
whatever the existing recommender test file is named — check `Vol_Suite/tests/` for it)
Expected: PASS — pre-existing recommender tests unaffected.

- [ ] **Step 5: Commit**

```bash
git add Vol_Suite/strategy_recommender.py Vol_Suite/tests/test_jump_diffusion_recommender_tie.py
git commit -m "feat(vol-suite): bias strategy recommender toward convexity on elevated jump risk"
```

---

## Task 14: End-to-end verification

**Files:**
- None created — manual verification + full test suite run.

- [ ] **Step 1: Run the full Vol_Suite test suite**

Run: `..\.venv\Scripts\python.exe -m pytest Vol_Suite/tests/ -v` (from repo root)
Expected: PASS, all tests including every test file touched/created in Tasks 1-13.

- [ ] **Step 2: Run the full repo test suite**

Run: `..\.venv\Scripts\python.exe -m pytest` (from repo root)
Expected: PASS — confirms nothing outside Vol_Suite (Options_Suite, VaR_Tools_Simulations,
orchestrator, dashboard) regressed from the `suite_context.json` schema addition.

- [ ] **Step 3: Manual unified-run smoke test**

Run: `orchestrator.bat --suite vol --ticker SPY --expiry <next-monthly-expiry>` (Windows; use a
real upcoming SPY monthly expiry date). Confirm:
- The run completes without raising.
- The resulting `suite_context.json` (in the run's output dir) contains a top-level
  `"jump_diffusion"` key, either populated (`model_name: "Bates"`, `params`, `rmse_iv`,
  `jump_variance_share`) or explicitly `null` if calibration failed for a data reason (bad
  chain data, no expiry match) — never a missing key, never a raised exception surfacing to the
  orchestrator.
- Console output shows a `[jump_diffusion]` log line either way (success is silent by design —
  only failures print, per Task 10's `_calibrate_default_jump_model`; add a one-line success print
  there too if that turns out to make this smoke test harder to verify manually than intended).

- [ ] **Step 4: Manual comparison-mode smoke test**

From a Python shell with the venv active (`.venv\Scripts\python.exe`, `cd Vol_Suite`):

```python
from thetadata_client import ThetaDataController
from variance_swap_live import fetch_chain_thetadata
from jump_diffusion.comparison import run_comparison

td = ThetaDataController()
chain = fetch_chain_thetadata(td, "SPY", "20261218", r=0.04, q=0.0)  # use a real live expiry
result = run_comparison(chain, spot=<current SPY spot>, T=<years to that expiry>)
print(result["best_fit"])
for name, r in result["models"].items():
    print(name, r.rmse_iv if r else "FAILED")
```

Confirm: all 5 models return a `CalibrationResult` (or a clearly logged failure, not a silent
`None` with no explanation), RMSE values are in a sane range (well under 0.05, i.e. under 5 vol
points, for a liquid SPY chain), and `jump_contribution.delta_iv` shows a plausible wing-shaped
pattern (larger Bates-vs-Heston IV gap away from ATM, near-zero at ATM) — this is the qualitative
check that the Lewis pricer and calibration are producing sane real-market fits, not just passing
synthetic round-trip tests.

- [ ] **Step 5: Commit any smoke-test fixes, then final commit**

If Steps 3-4 surface bugs, fix them with their own focused commits (test + fix, following the same
TDD pattern as Tasks 1-13). Once clean:

```bash
git log --oneline -15  # confirm the full Task 1-13 commit sequence is present
```

No new commit needed for this task if Steps 1-4 pass clean — this task is verification-only.
