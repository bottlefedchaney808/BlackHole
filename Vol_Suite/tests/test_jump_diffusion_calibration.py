import numpy as np
from implied_vol import implied_vol
from jump_diffusion.calibration import calibrate
from jump_diffusion.models import MertonModel, VarianceGammaModel
from jump_diffusion.pricer import lewis_price
from variance_swap_live import ChainData


def _synthetic_chain(model, S0, T, r, q, strikes):
    """Price a synthetic chain from *known* model params, invert to IV --
    the standard 'calibrate what you priced' round-trip fixture.

    implied_vol() returns Optional[float] (None on an uninvertible price,
    not NaN -- see implied_vol.py:120) -- guard with `or np.nan` so
    np.array(...) stays float dtype. Without this guard a single None makes
    the array object-dtype, and calibrate()'s `~np.isnan(chain.call_iv)`
    filter raises TypeError instead of skipping the point.
    """
    call_prices = np.array(
        [lewis_price(model, S0, k, T, r, q, "call") for k in strikes]
    )
    call_ivs = np.array(
        [
            implied_vol(p, S0, k, T, r, q, "call") or np.nan
            for p, k in zip(call_prices, strikes)
        ]
    )
    return ChainData(
        expiry="20270101",
        strikes=strikes,
        call_mid=call_prices,
        put_mid=call_prices,
        r=r,
        q=q,
        call_iv=call_ivs,
        put_iv=call_ivs,
    )


def test_merton_calibration_recovers_known_params():
    S0, T, r, q = 100.0, 0.5, 0.03, 0.0
    strikes = np.array([80.0, 90.0, 95.0, 100.0, 105.0, 110.0, 120.0])
    true_model = MertonModel(sigma=0.20, lam=0.8, mu_j=-0.08, sigma_j=0.12)
    chain = _synthetic_chain(true_model, S0, T, r, q, strikes)

    result = calibrate(MertonModel, chain, S0, T)

    assert result.rmse_iv < 0.01  # fit is tight in IV space (1 vol point)
    assert 0.10 < result.params["sigma"] < 0.30  # recovered in a sane neighborhood


def test_variance_gamma_calibration_survives_infeasible_log_domain():
    """VarianceGammaModel.phi() computes
    omega = (1/nu)*log(1 - theta_vg*nu - 0.5*sigma**2*nu), which takes the
    log of a non-positive number for parameter combinations that are each
    individually inside PARAM_BOUNDS (e.g. sigma=3.0, nu=5.0, theta_vg=2.0
    gives log_arg=-31.5). If Nelder-Mead's search walks into that region,
    lewis_price() returns NaN/complex-NaN.

    calibrate() must guard against a non-finite price before handing it to
    implied_vol() -- not let a NaN propagate into np.log inside implied_vol
    or its pricer -- and complete with a CalibrationResult rather than
    raising, even seeded right at the edge of the infeasible region.
    """
    S0, T, r, q = 100.0, 0.5, 0.03, 0.0
    strikes = np.array([80.0, 90.0, 100.0, 110.0, 120.0])
    true_model = VarianceGammaModel(sigma=0.18, nu=0.3, theta_vg=-0.1)
    chain = _synthetic_chain(true_model, S0, T, r, q, strikes)

    # Seed right at the edge of the infeasible region for omega's log
    # argument (1 - theta_vg*nu - 0.5*sigma**2*nu <= 0), all individually
    # within PARAM_BOUNDS, so Nelder-Mead's initial simplex probes it.
    infeasible_seed = {"sigma": 3.0, "nu": 5.0, "theta_vg": 2.0}

    result = calibrate(VarianceGammaModel, chain, S0, T, seed=infeasible_seed)

    assert isinstance(result.rmse_iv, float)
    assert result.model_name == "VarianceGamma"
