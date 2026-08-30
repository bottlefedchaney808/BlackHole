"""All-5-model calibration for manual comparison -- never called from the
unified pipeline (see JUMP_MODEL_DEFAULT in volatility_suite.py for the
single-model fast path). Invoke explicitly from Vol_Suite's interactive
menu or a CLI flag when deciding which model to set as the default."""

import numpy as np
from implied_vol import implied_vol

from jump_diffusion.calibration import calibrate
from jump_diffusion.models import ALL_MODELS, BatesModel, HestonModel
from jump_diffusion.pricer import lewis_price


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

    jump_contribution = {
        "strikes": chain.strikes,
        "heston_ivs": np.array([]),
        "bates_ivs": np.array([]),
        "delta_iv": np.array([]),
    }
    if models.get("Heston") is not None and models.get("Bates") is not None:
        heston_fit = HestonModel(**models["Heston"].params)
        bates_fit = BatesModel(**models["Bates"].params)
        heston_ivs, bates_ivs = [], []
        for k in chain.strikes:
            hp = lewis_price(heston_fit, spot, k, T, chain.r, chain.q, "call")
            bp = lewis_price(bates_fit, spot, k, T, chain.r, chain.q, "call")
            heston_ivs.append(
                implied_vol(hp, spot, k, T, chain.r, chain.q, "call") or np.nan
            )
            bates_ivs.append(
                implied_vol(bp, spot, k, T, chain.r, chain.q, "call") or np.nan
            )
        heston_ivs, bates_ivs = np.array(heston_ivs), np.array(bates_ivs)
        jump_contribution = {
            "strikes": chain.strikes,
            "heston_ivs": heston_ivs,
            "bates_ivs": bates_ivs,
            "delta_iv": bates_ivs
            - heston_ivs,  # the "visible jump contribution" from the AIGamma teardown
        }

    return {
        "models": models,
        "best_fit": best_fit,
        "jump_contribution": jump_contribution,
    }
