import numpy as np
from implied_vol import implied_vol
from jump_diffusion.comparison import run_comparison
from jump_diffusion.models import BatesModel
from jump_diffusion.pricer import lewis_price
from variance_swap_live import ChainData


def test_run_comparison_returns_all_5_models_and_jump_contribution():
    S0, T, r, q = 100.0, 0.5, 0.03, 0.0
    strikes = np.array([80.0, 90.0, 95.0, 100.0, 105.0, 110.0, 120.0])
    true_model = BatesModel(
        kappa=2.0,
        theta=0.04,
        xi=0.5,
        rho=-0.6,
        v0=0.04,
        lam=0.6,
        mu_j=-0.06,
        sigma_j=0.1,
    )
    call_prices = np.array(
        [lewis_price(true_model, S0, k, T, r, q, "call") for k in strikes]
    )
    call_ivs = np.array(
        [implied_vol(p, S0, k, T, r, q, "call") for p, k in zip(call_prices, strikes)]
    )
    chain = ChainData(
        expiry="20270101",
        strikes=strikes,
        call_mid=call_prices,
        put_mid=call_prices,
        r=r,
        q=q,
        call_iv=call_ivs,
        put_iv=call_ivs,
    )

    result = run_comparison(chain, S0, T)

    assert set(result["models"].keys()) == {
        "Merton",
        "Heston",
        "Bates",
        "Kou",
        "VarianceGamma",
    }
    assert result["best_fit"] in result["models"]
    assert len(result["jump_contribution"]["delta_iv"]) == len(strikes)
