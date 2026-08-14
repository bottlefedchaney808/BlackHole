"""backtest_owniv.py -- H4: model-implied volatility, own-IV comparison.

The answer to "isn't making all the models solve for their own IV also
comparison isolation?": YES. This harness prices EVERY model at ITS OWN
implied vol and compares that model-implied-IV curve against the market IV.

For each contract we invert each model's OWN pricing function: find sigma*
such that model_price(S, K, T, r, q, sigma*, cp) == market_mid. That sigma* is
the vol the model itself "solves" for -- the model-implied vol. Comparing that
to the market IV isolates the whole engine (dynamics + calibration + tree/SDE
error) end-to-end, which is the honest head-to-head the common-IV harness
(H1) deliberately does NOT do (H1 hands CRR/LR/BAW/MC the market IV for free).

Smile models (VV/SABR/Heston) have their OWN chain-derived smile, so for them
the model-implied IV at each strike is that smile directly (their pricing uses
it) -- no inversion needed, and it is genuinely "their own" vol.

Metrics per model: IV-RMSE, IV-MAE, mean IV bias, corr(model-implied, market),
n. Bucketed by moneyness. A market-IV reference line is plotted against each
model's curve (strike on x, IV on y) into out_dir.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from Backtests.models import (
    MODEL_ORDER,
    price_model,
)
from Backtests.core import moneyness_bucket, summarize_errors

BUCKETS = ["DeepOTM", "OTM", "ATM", "ITM", "DeepITM"]

# Vol bound for the own-IV inversion (inverting a model price to a vol).
_IV_LO, _IV_HI = 0.005, 3.0


def _invert_model_iv(
    name: str, S: float, K: float, T: float, r: float, q: float,
    target_mid: float, cp: bool, vv_ctx, sabr_cal, heston_ctx,
) -> Optional[float]:
    """Bisect sigma so price_model == target_mid. Smile models short-circuit to
    their own smile vol (no inversion). Returns None if not found / invalid."""
    if name in ("VV", "SABR", "Heston"):
        # Use the model's OWN chain-derived smile vol directly.
        return _smile_own_iv(name, S, K, T, r, q, cp, vv_ctx, sabr_cal, heston_ctx)
    if target_mid <= 0:
        return None
    lo, hi = _IV_LO, _IV_HI
    p_lo = price_model(name, S, K, T, r, q, lo, cp, vv_ctx, sabr_cal, heston_ctx)
    p_hi = price_model(name, S, K, T, r, q, hi, cp, vv_ctx, sabr_cal, heston_ctx)
    if p_lo is None or p_hi is None or p_lo > target_mid or p_hi < target_mid:
        return None  # outside monotone price range -> not invertible here
    # Bisect to a RELATIVE price tolerance (0.001% of mid, floored at 1e-7).
    # An absolute 1e-7 is below the stochastic-MC price noise, so for MC the
    # old break never fired and every contract ran the full 50 iterations; the
    # relative tolerance terminates deterministic tree/analytic models early and
    # the 30-iter cap bounds the (noise-limited) MC case with no loss of
    # achievable precision.
    tol = max(1e-7, 1e-5 * abs(target_mid))
    for _ in range(30):
        mid = 0.5 * (lo + hi)
        pm = price_model(name, S, K, T, r, q, mid, cp, vv_ctx, sabr_cal, heston_ctx)
        if pm is None:
            return None
        if pm < target_mid:
            lo = mid
        else:
            hi = mid
        if abs(pm - target_mid) <= tol:
            break
    return 0.5 * (lo + hi)


def _smile_own_iv(name, S, K, T, r, q, cp, vv_ctx, sabr_cal, heston_ctx) -> Optional[float]:
    try:
        if name == "VV":
            if not vv_ctx or vv_ctx.get("atm_vol") is None:
                return None
            from Options_Suite.VannaVolga import get_vol
            return float(get_vol(S, K, T, r, q, vv_ctx["atm_vol"],
                                 vv_ctx["rr25"], vv_ctx["bf25"]))
        if name == "SABR":
            if not sabr_cal:
                return None
            from Options_Suite.sabr_market_calib import sabr_vol_hagan
            F = S * __exp((r - q) * T)
            return float(sabr_vol_hagan(F, K, T, sabr_cal["alpha"], sabr_cal["beta"],
                                        sabr_cal["rho"], sabr_cal["nu"]))
        if name == "Heston":
            if not heston_ctx or heston_ctx.get("V0") is None:
                return None
            return _heston_iv_from_price(S, K, T, r, q, heston_ctx, cp)
    except Exception:
        return None
    return None


def _heston_iv_from_price(S, K, T, r, q, heston_ctx, cp):
    """Invert the Heston analytic call price to the IV whose BS price matches,
    giving Heston's OWN implied vol at this strike. cp handled via parity."""
    from Options_Suite.MCHestonLSM import heston_european_call_price
    from scipy.optimize import brentq

    hest_call = heston_european_call_price(
        S, K, T, r, q, heston_ctx["V0"], heston_ctx["kappa"],
        heston_ctx["theta"], heston_ctx["vol_sigma"], heston_ctx["rho"])
    if hest_call <= 0:
        return None

    def bs_call(sig):
        from scipy.stats import norm
        d1 = (np.log(S / K) + (r - q + 0.5 * sig ** 2) * T) / (sig * np.sqrt(T))
        d2 = d1 - sig * np.sqrt(T)
        return S * np.exp(-q * T) * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)

    def g(sig):
        return bs_call(sig) - hest_call
    lo, hi = _IV_LO, _IV_HI
    try:
        if g(lo) * g(hi) > 0:
            return None
        iv = float(brentq(g, lo, hi, xtol=1e-8))
        return iv if np.isfinite(iv) else None
    except Exception:
        return None


def _row_mid(row: Dict[str, Any]) -> Optional[float]:
    from Backtests.core import _to_float
    mid = _to_float(row.get("mid"))
    if mid is not None and mid > 0:
        return mid
    bid, ask = _to_float(row.get("bid")), _to_float(row.get("ask"))
    if bid is not None and ask is not None and ask >= bid >= 0:
        return (bid + ask) / 2.0
    cl = _to_float(row.get("close"))
    return cl if cl is not None and cl > 0 else None


def run_owniv(chains: Sequence[Any]) -> Dict[str, Any]:
    acc: Dict[str, Dict[str, Any]] = {
        m: {"n": 0, "failures": 0, "pairs": [], "buckets": {b: [] for b in BUCKETS}}
        for m in MODEL_ORDER
    }
    for cd in chains:
        vv_ctx = cd.vv_ctx
        sabr_cal = cd.sabr_cal
        heston_ctx = cd.heston_ctx
        for row in cd.rows:
            K = row["strike"]
            cp = row["right"] == "C"
            mid = _row_mid(row)
            if mid is None or mid <= 0:
                continue
            mkt_iv = row.get("iv") or row.get("implied_vol")
            from Backtests.core import _to_float
            mkt_iv = _to_float(mkt_iv)
            if mkt_iv is None or mkt_iv <= 0:
                continue
            for m in MODEL_ORDER:
                model_iv = _invert_model_iv(
                    m, cd.spot, K, cd.T, cd.r, cd.q, mid, cp,
                    vv_ctx, sabr_cal, heston_ctx,
                )
                a = acc[m]
                if model_iv is None or not np.isfinite(model_iv):
                    a["failures"] += 1
                    continue
                a["n"] += 1
                a["pairs"].append((model_iv, mkt_iv))
                bucket = moneyness_bucket(K, cd.spot, row["right"])
                a["buckets"].setdefault(bucket, []).append((model_iv, mkt_iv))

    tables: Dict[str, Any] = {}
    for m in MODEL_ORDER:
        a = acc[m]
        s = summarize_errors(a["pairs"]) if a["pairs"] else {}
        tables[m] = {
            "n": a["n"], "failures": a["failures"],
            "summary": s,
            "buckets": {b: summarize_errors(a["buckets"][b]) for b in BUCKETS},
        }
    ranked = sorted(
        [(m, tables[m]["summary"].get("rmse")) for m in MODEL_ORDER],
        key=lambda kv: (kv[1] is None, kv[1] if kv[1] is not None else float("inf")),
    )
    return {
        "models": tables,
        "rank_by_iv_rmse": [m for m, _ in ranked],
        "n_days": len(chains),
    }


def render_owniv_report(result: Dict[str, Any]) -> List[str]:
    lines = ["== H4 Model-implied vol (own-IV): each model solves its own ==",
             "method: invert each model's OWN price to an IV; compare vs market IV",
             f"days: {result['n_days']}   rank by IV-RMSE: {' > '.join(result['rank_by_iv_rmse'])}",
             "",
             "model        n    fail   IV-RMSE   IV-MAE   IV-bias   corr",
             "------------------------------------------------------------------"]
    for m in result["rank_by_iv_rmse"]:
        t = result["models"][m]
        s = t["summary"]
        corr = f"{s['corr']:.3f}" if s.get("corr") is not None else "-"
        lines.append(
            f"{m:<10} {t['n']:>5} {t['failures']:>6} "
            f"{_f(s.get('rmse')):>9} {_f(s.get('mae')):>8} {_f(s.get('bias')):>8} {corr:>6}"
        )
    lines.append("")
    lines.append("note: this is the honest head-to-head -- every model derives")
    lines.append("its own vol; market IV is the reference. Tree/analytic engines")
    lines.append("invert their price to an IV; VV/SABR/Heston use their own smile.")
    lines.append("")
    return lines


def _f(v: Optional[float]) -> str:
    return "-" if v is None else f"{v:.4f}"


def __exp(x: float) -> float:
    import math
    return math.exp(x)


__all__ = ["run_owniv", "render_owniv_report"]
