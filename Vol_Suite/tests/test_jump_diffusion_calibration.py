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


from jump_diffusion.models import BatesModel, HestonModel, KouModel


def test_heston_calibration_recovers_known_params():
    """True params deliberately offset from _DEFAULT_SEEDS['Heston'] on
    every dimension (kappa/theta/xi/rho/v0 all differ 20-50%) -- a seed
    planted at (or one param away from) the true answer would let a broken
    characteristic function pass this test by never having to move. See
    CARL R1-F4."""
    S0, T, r, q = 100.0, 0.5, 0.03, 0.0
    strikes = np.array([80.0, 90.0, 95.0, 100.0, 105.0, 110.0, 120.0])
    true_model = HestonModel(kappa=3.2, theta=0.06, xi=0.9, rho=-0.75, v0=0.05)
    chain = _synthetic_chain(true_model, S0, T, r, q, strikes)
    result = calibrate(HestonModel, chain, S0, T)
    assert result.rmse_iv < 0.01


def test_bates_calibration_recovers_known_params():
    """True params offset from _DEFAULT_SEEDS['Bates'] on every dimension --
    see the Heston test's docstring; same rationale, 7-param version."""
    S0, T, r, q = 100.0, 0.5, 0.03, 0.0
    strikes = np.array([80.0, 90.0, 95.0, 100.0, 105.0, 110.0, 120.0])
    true_model = BatesModel(
        kappa=3.0,
        theta=0.06,
        xi=0.8,
        rho=-0.7,
        v0=0.05,
        lam=0.9,
        mu_j=-0.09,
        sigma_j=0.15,
    )
    chain = _synthetic_chain(true_model, S0, T, r, q, strikes)
    result = calibrate(BatesModel, chain, S0, T)
    assert result.rmse_iv < 0.015  # 7-param fit -- slightly looser tolerance


def test_kou_calibration_recovers_known_params():
    """sigma deliberately offset from _DEFAULT_SEEDS['Kou']['sigma'] (0.18)
    -- the original draft had sigma matching the seed exactly, which is the
    same weak-test pattern as Heston/Bates above."""
    S0, T, r, q = 100.0, 0.5, 0.03, 0.0
    strikes = np.array([80.0, 90.0, 95.0, 100.0, 105.0, 110.0, 120.0])
    true_model = KouModel(sigma=0.24, lam=0.8, p=0.35, eta1=12.0, eta2=6.0)
    chain = _synthetic_chain(true_model, S0, T, r, q, strikes)
    result = calibrate(KouModel, chain, S0, T)
    assert result.rmse_iv < 0.01


def test_vg_calibration_recovers_known_params():
    S0, T, r, q = 100.0, 0.5, 0.03, 0.0
    strikes = np.array([80.0, 90.0, 95.0, 100.0, 105.0, 110.0, 120.0])
    true_model = VarianceGammaModel(sigma=0.26, nu=0.25, theta_vg=-0.12)
    chain = _synthetic_chain(true_model, S0, T, r, q, strikes)
    result = calibrate(VarianceGammaModel, chain, S0, T)
    assert result.rmse_iv < 0.01


def test_calibration_on_a_wide_live_sized_chain_completes_quickly_and_returns_full_smile():
    """A real listed chain (e.g. SPY monthly) can carry 100+ strikes.
    calibrate() must bound its per-iteration Nelder-Mead cost regardless of
    chain size (MAX_CALIB_STRIKES caps the strikes actually used inside the
    optimizer's objective) while still reporting fitted_ivs/market_ivs/
    strikes over the FULL input smile, not just the calibration subset --
    a 150-strike chain must not silently shrink the reported output to 25
    points. This is a regression test for a live-run timeout (>1800s)
    discovered during Task 14 end-to-end smoke testing."""
    import time

    S0, T, r, q = 100.0, 0.5, 0.03, 0.0
    strikes = np.linspace(50.0, 200.0, 150)
    true_model = MertonModel(sigma=0.20, lam=0.8, mu_j=-0.08, sigma_j=0.12)
    chain = _synthetic_chain(true_model, S0, T, r, q, strikes)

    n_valid = int(np.sum(~np.isnan(chain.call_iv)))  # deep-wing strikes drop
    # out via implied_vol()'s own None-on-uninvertible-price behavior --
    # unrelated to the calibration-strike cap, so compare against this, not
    # against the raw input strike count.

    start = time.monotonic()
    result = calibrate(MertonModel, chain, S0, T)
    elapsed = time.monotonic() - start

    assert elapsed < 30.0, f"calibrate() took {elapsed:.1f}s on a 150-strike chain"
    assert n_valid > 30  # sanity: the round-trip fixture didn't drop everything
    assert (
        len(result.strikes) == n_valid
    )  # full valid smile, not truncated to the calib cap
    assert len(result.fitted_ivs) == n_valid
    assert len(result.market_ivs) == n_valid
