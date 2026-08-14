from Options_Suite.sabr_market_calib import fit_sabr_market, sabr_vol_hagan


def _smile_chain(spot=100.0, base=0.20, skew=-0.05, curv=0.30):
    """Synthetic OTM-collapsed smile with a mild skew + curvature."""
    import math
    chain = {}
    for k in range(80, 121, 2):
        x = math.log(k / spot)
        iv = base + curv * x * x + skew * x
        chain[(float(k), 'C' if k > spot else 'P')] = max(iv, 0.05)
    return chain


def test_fit_sabr_market_recovers_smooth_smile():
    forward = 100.0
    T = 0.25
    cal = fit_sabr_market(_smile_chain(), forward, T)
    assert cal is not None
    assert all(k in cal for k in ("alpha", "beta", "rho", "nu", "rmse"))
    # ATM-pinned: Hagan ATM should match the true smile at the forward strike
    atm = sabr_vol_hagan(forward, forward, T, cal["alpha"], cal["beta"],
                         cal["rho"], cal["nu"])
    assert abs(atm - 0.20) < 0.02
    # SABR fits a quadratic-skew smile with fixed/free beta; allow a tolerant
    # RMSE (shape mismatch between Hagan vol-of-vol and a pure quadratic).
    assert cal["rmse"] < 0.10


def test_fit_sabr_market_none_for_sparse_chain():
    chain = {(float(k), 'C'): 0.2 for k in (100.0, 105.0)}  # only 2 strikes
    assert fit_sabr_market(chain, 100.0, 0.25) is None


def test_fit_sabr_market_free_beta_contract():
    cal = fit_sabr_market(_smile_chain(), 100.0, 0.25, calibrate_beta=True)
    assert cal is not None
    assert 0.1 <= cal["beta"] <= 1.0


def test_fit_sabr_market_returns_none_on_bad_inputs():
    assert fit_sabr_market({}, 100.0, 0.25) is None
    assert fit_sabr_market(_smile_chain(), 100.0, 0.0) is None
