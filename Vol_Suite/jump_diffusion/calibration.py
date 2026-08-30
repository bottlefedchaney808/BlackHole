"""Nelder-Mead calibration of a jump-diffusion model to an observed IV
smile, per expiry slice."""

from dataclasses import dataclass

import numpy as np
from implied_vol import implied_vol
from scipy.optimize import minimize

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
    "Bates": {
        "kappa": 2.0,
        "theta": 0.04,
        "xi": 0.5,
        "rho": -0.6,
        "v0": 0.04,
        "lam": 0.5,
        "mu_j": -0.05,
        "sigma_j": 0.1,
    },
    "Kou": {"sigma": 0.18, "lam": 1.0, "p": 0.4, "eta1": 10.0, "eta2": 5.0},
    "VarianceGamma": {"sigma": 0.18, "nu": 0.3, "theta_vg": -0.1},
}


def calibrate(
    model_cls, chain, spot: float, T: float, seed: dict | None = None
) -> CalibrationResult:
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
            # Some model/param combinations (e.g. VarianceGamma's omega
            # taking log() of a non-positive argument) are individually
            # inside PARAM_BOUNDS but numerically infeasible and hand back
            # a non-finite price. Guard here, before implied_vol(), rather
            # than letting NaN propagate into its Newton-Raphson/bisection
            # loop -- same penalty path as an un-invertible price.
            if not np.isfinite(price):
                errs.append(1.0)  # penalize non-finite prices
                continue
            iv_fit = implied_vol(price, spot, k, T, chain.r, chain.q, "call")
            errs.append(
                (iv_fit - iv_mkt) if iv_fit is not None else 1.0
            )  # penalize un-invertible prices
        return float(np.sum(np.square(errs)))

    res = minimize(
        objective,
        x0,
        method="Nelder-Mead",
        options={"maxiter": 2000, "xatol": 1e-6, "fatol": 1e-8},
    )
    fitted = model_cls.from_array(
        np.clip(res.x, [b[0] for b in bounds], [b[1] for b in bounds])
    )

    fitted_ivs = []
    for k in strikes:
        price = lewis_price(fitted, spot, k, T, chain.r, chain.q, "call")
        if not np.isfinite(price):
            fitted_ivs.append(np.nan)
            continue
        fitted_ivs.append(
            implied_vol(price, spot, k, T, chain.r, chain.q, "call") or np.nan
        )
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
