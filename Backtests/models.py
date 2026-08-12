"""models.py — uniform per-model price/greeks adapters for the tournament.

Every model in the lot exposes an own-dynamics engine (see Options_Suite
docstrings: each greeks function bumps through THAT model's dynamics — tree,
smile, or SDE). This module maps the uniform tournament interface to each
engine and supplies the per-expiry market-derived contexts the smile models
need (VV pillars from the chain, SABR calibration from the chain, Heston
variance level from ATM IV). No math is reimplemented here; engines are the
source of truth.
"""
from __future__ import annotations

import os

# MC.py auto-selects the CuPy GPU backend when a CUDA device is present, but
# the tournament makes thousands of SMALL per-contract MC calls where GPU
# kernel-launch / host<->device transfer overhead (~15s/contract) dominates
# and makes runs minutes long. The MC docstring warns GPU is only faster for
# ONE large 50k-sim simulation. Force CPU for the tournament.
os.environ.setdefault("OPTIONS_SUITE_FORCE_CPU", "1")

from typing import Any, Callable, Dict, List, Optional, Tuple

# --- own-dynamics engines (Options_Suite) --------------------------------
from Options_Suite.american_binomial import (  # type: ignore
    crr_american_price,
    crr_all_greeks,
    leisen_reimer_american_price,
    lr_all_greeks,
)
from Options_Suite.MC import AmericanLSMPricer, mc_all_greeks  # type: ignore
from Options_Suite.barone_adesi_whaley import baw_american_price, baw_all_greeks  # type: ignore
from Options_Suite.VannaVolga import get_vol, vv_all_greeks  # type: ignore
from Options_Suite.SABRModel import sabr_all_greeks, sabr_vol_hagan  # type: ignore
from Options_Suite.MCHestonLSM import heston_all_greeks, heston_european_call_price  # type: ignore
# --- network-free SABR fitter (Vol_Suite) ---------------------------------
from Vol_Suite.vol_surface_reference import fit_sabr_reference  # type: ignore

from Backtests.core import (
    ALL_GREEK_FIELDS,
    _to_float,
    bs_price,
    strike_for_forward_delta,
    summarize_errors,
)

# Tournament runtime knobs: the MC-family engines are the slow ones; the
# tournament lowers their sim/step budgets (documented in the report) and
# evaluates them on a sampled sub-chain (see sample_slow_rows) so a whole
# chain backtests in minutes instead of hours. The five fast engines
# (CRR/LR/BAW/VV/SABR) always run the FULL chain.
MC_SIMS = int(os.environ.get("BT_MC_SIMS", "1500"))
MC_STEPS = int(os.environ.get("BT_MC_STEPS", "100"))
HESTON_SIMS = int(os.environ.get("BT_HESTON_SIMS", "1000"))
HESTON_STEPS = int(os.environ.get("BT_HESTON_STEPS", "50"))

MODEL_ORDER: List[str] = ["CRR", "LR", "BAW", "MC", "VV", "SABR", "Heston"]
SLOW_MODELS = ("MC", "Heston")

# Slow-model strike sampling: keep the ATM band plus every Nth wing strike.
SLOW_ATM_BAND = float(os.environ.get("BT_SLOW_ATM_BAND", "0.05"))   # +-5% of spot
SLOW_WING_STRIDE = int(os.environ.get("BT_SLOW_WING_STRIDE", "6"))

HESTON_FIXED = {"kappa": 2.0, "vol_sigma": 0.40, "rho": -0.5}


def _row_iv(row: Dict[str, Any]) -> Optional[float]:
    """Read IV from raw ThetaData or current normalized chain rows."""
    for field in ("implied_vol", "iv", "impliedVolatility", "impliedvol"):
        value = _to_float(row.get(field))
        if value is not None:
            return value
    return None

# ---------------------------------------------------------------------------
# Per-expiry market-derived contexts (built once per (ticker, expiry, date))
# ---------------------------------------------------------------------------

def _interp_iv(strikes: List[float], ivs: List[float], k: float) -> Optional[float]:
    """Linear interpolation of IV at strike k over the given (K, IV) pairs."""
    if not strikes or k <= 0:
        return None
    if k <= strikes[0]:
        return ivs[0]
    if k >= strikes[-1]:
        return ivs[-1]
    for i in range(len(strikes) - 1):
        k0, k1 = strikes[i], strikes[i + 1]
        if k0 <= k <= k1:
            if k1 == k0:
                return ivs[i]
            w = (k - k0) / (k1 - k0)
            return ivs[i] + w * (ivs[i + 1] - ivs[i])
    return None


def build_vv_context(
    rows: List[Dict[str, Any]], forward: float, T: float, r: float, q: float
) -> Dict[str, Any]:
    """VV smile pillars (atm_vol, rr25, bf25) derived from the chain.

    atm_vol = IV at the strike closest to the forward; rr25/bf25 = risk
    reversal / butterfly at the 25-delta strikes (forward-delta formula,
    IV interpolated on the OTM side per right). Same convention
    vv_all_greeks/get_vol expect: atm_vol decimal, rr25/bf25 in vol POINTS.
    """
    atm_vol: Optional[float] = None
    call_strikes: List[Tuple[float, float]] = []  # (K, iv)
    put_strikes: List[Tuple[float, float]] = []
    for row in rows:
        iv = _row_iv(row)
        if iv is None or iv <= 0:
            continue
        if row["right"] == "C":
            call_strikes.append((row["strike"], iv))
        else:
            put_strikes.append((row["strike"], iv))
    call_strikes.sort()
    put_strikes.sort()
    if call_strikes and put_strikes and T > 0 and forward > 0:
        atm_k = min(call_strikes + put_strikes, key=lambda kv: abs(kv[0] - forward))[0]
        for kv in call_strikes + put_strikes:
            if abs(kv[0] - forward) == abs(atm_k - forward):
                atm_vol = kv[1]
                break
    if atm_vol is None:
        return {"atm_vol": None, "rr25": None, "bf25": None}

    k25c = strike_for_forward_delta(forward, atm_vol, T, 0.25)
    k25p = strike_for_forward_delta(forward, atm_vol, T, 0.75)
    iv25c = _interp_iv([kv[0] for kv in call_strikes], [kv[1] for kv in call_strikes], k25c)
    iv25p = _interp_iv([kv[0] for kv in put_strikes], [kv[1] for kv in put_strikes], k25p)
    rr25 = (iv25c - iv25p) * 100.0 if iv25c is not None and iv25p is not None else None
    bf25 = ((iv25c + iv25p) / 2.0 - atm_vol) * 100.0 if iv25c is not None and iv25p is not None else None
    return {"atm_vol": atm_vol, "rr25": rr25, "bf25": bf25}


def build_sabr_context(rows: List[Dict[str, Any]], forward: float, T: float) -> Optional[Dict[str, Any]]:
    """SABR calibration dict {alpha, beta, rho, nu, rmse} from the chain."""
    chain_iv: Dict[Tuple[float, str], float] = {}
    for row in rows:
        iv = _row_iv(row)
        if iv is not None and iv > 0:
            chain_iv[(row["strike"], row["right"])] = iv
    if not chain_iv:
        return None
    try:
        return fit_sabr_reference(chain_iv, forward, T)
    except Exception:
        return None


def build_heston_context(atm_iv: Optional[float]) -> Dict[str, Any]:
    """Heston variance-level params: V0 pinned to ATM IV^2, structural
    params fixed (documented in the report; no per-expiry Heston calibration
    in tournament mode)."""
    v0 = (atm_iv ** 2) if atm_iv and atm_iv > 0 else 0.04
    return {"V0": v0, "theta": v0, **HESTON_FIXED}


def sample_slow_rows(rows: List[Dict[str, Any]], spot: float) -> List[Dict[str, Any]]:
    """Sub-sample the chain for the slow MC-family models.

    Keeps every strike within +-SLOW_ATM_BAND of spot (the money region the
    tournament cares about) plus every SLOW_WING_STRIDE-th wing strike on
    each side, so the slow models stay representative without pricing the
    entire wing. Fast engines always run the full chain.
    """
    lo, hi = spot * (1 - SLOW_ATM_BAND), spot * (1 + SLOW_ATM_BAND)
    atm = [r for r in rows if lo <= r["strike"] <= hi]
    wings_c = sorted([r for r in rows if r["strike"] > hi], key=lambda r: r["strike"])
    wings_p = sorted([r for r in rows if r["strike"] < lo], key=lambda r: -r["strike"])
    sampled = list(atm)
    for wing in (wings_c, wings_p):
        sampled.extend(wing[i] for i in range(0, len(wing), SLOW_WING_STRIDE))
    # de-dup (strike, right) just in case
    seen = set()
    out = []
    for r in sampled:
        k = (r["strike"], r["right"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


# ---------------------------------------------------------------------------
# Uniform price adapter
# ---------------------------------------------------------------------------

def price_model(
    name: str,
    S: float,
    K: float,
    T: float,
    r: float,
    q: float,
    sigma: float,
    cp: bool,
    vv_ctx: Optional[Dict[str, Any]] = None,
    sabr_cal: Optional[Dict[str, Any]] = None,
    heston_ctx: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    try:
        if name == "CRR":
            return crr_american_price(S, K, T, r, sigma, q, cp, steps=200)
        if name == "LR":
            return leisen_reimer_american_price(S, K, T, r, sigma, q, cp, steps=200)
        if name == "BAW":
            return baw_american_price(S, K, T, r, sigma, q, cp)
        if name == "MC":
            pricer = AmericanLSMPricer(S, K, T, r, q, sigma, MC_SIMS, MC_STEPS,
                                       "call" if cp else "put")
            return pricer.price()
        if name == "VV":
            if not vv_ctx or vv_ctx.get("atm_vol") is None:
                return None
            sig = float(get_vol(S, K, T, r, q, vv_ctx["atm_vol"], vv_ctx["rr25"], vv_ctx["bf25"]))
            return bs_price(S, K, T, r, q, max(sig, 1e-4), cp)
        if name == "SABR":
            if not sabr_cal:
                return None
            F = S * math_exp((r - q) * T)
            sig = sabr_vol_hagan(F, K, T, sabr_cal["alpha"], sabr_cal["beta"],
                                 sabr_cal["rho"], sabr_cal["nu"])
            return bs_price(S, K, T, r, q, max(float(sig), 1e-4), cp)
        if name == "Heston":
            if not heston_ctx:
                return None
            c = heston_european_call_price(
                S, K, T, r, q, heston_ctx["V0"], heston_ctx["kappa"],
                heston_ctx["theta"], heston_ctx["vol_sigma"], heston_ctx["rho"],
            )
            if cp:
                return c
            # put via call-put parity
            return c + K * math_exp(-r * T) - S * math_exp(-q * T)
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Uniform greeks adapter
# ---------------------------------------------------------------------------

def greeks_model(
    name: str,
    S: float,
    K: float,
    T: float,
    r: float,
    q: float,
    sigma: float,
    cp: bool,
    vv_ctx: Optional[Dict[str, Any]] = None,
    sabr_cal: Optional[Dict[str, Any]] = None,
    heston_ctx: Optional[Dict[str, Any]] = None,
) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {g: None for g in ALL_GREEK_FIELDS}
    try:
        if name == "CRR":
            g = crr_all_greeks(S, K, T, r, sigma, q, cp, steps=401)
        elif name == "LR":
            g = lr_all_greeks(S, K, T, r, sigma, q, cp, steps=401)
        elif name == "BAW":
            g = baw_all_greeks(S, K, T, r, sigma, q, cp)
        elif name == "MC":
            g = mc_all_greeks(S, K, T, r, q, sigma, sims=MC_SIMS, steps=MC_STEPS,
                              option="call" if cp else "put", seed=42)
        elif name == "VV":
            if not vv_ctx or vv_ctx.get("atm_vol") is None:
                return out
            g = vv_all_greeks(S, K, T, r, q, cp, vv_ctx["atm_vol"], vv_ctx["rr25"], vv_ctx["bf25"], steps=401)
        elif name == "SABR":
            if not sabr_cal:
                return out
            g = sabr_all_greeks(S, K, T, r, q, cp, sabr_cal, steps=401)
        elif name == "Heston":
            if not heston_ctx:
                return out
            g = heston_all_greeks(
                S, K, T, r, q, heston_ctx["V0"], heston_ctx["kappa"],
                heston_ctx["theta"], heston_ctx["vol_sigma"], heston_ctx["rho"],
                sims=HESTON_SIMS, steps=HESTON_STEPS,
                option="call" if cp else "put", seed=42,
            )
        else:
            return out
    except Exception:
        return out
    if not isinstance(g, dict):
        return out
    for field in ALL_GREEK_FIELDS:
        v = g.get(field)
        if v is not None:
            try:
                out[field] = float(v)
            except (TypeError, ValueError):
                out[field] = None
    return out


def math_exp(x: float) -> float:
    import math
    return math.exp(x)


# ---------------------------------------------------------------------------
# Batch evaluation helpers
# ---------------------------------------------------------------------------

def evaluate_model(
    name: str,
    rows: List[Dict[str, Any]],
    S: float,
    T: float,
    r: float,
    q: float,
    vv_ctx: Optional[Dict[str, Any]] = None,
    sabr_cal: Optional[Dict[str, Any]] = None,
    heston_ctx: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run one model over every contract row.

    Rows must be normalized (floats). Returns:
      {
        'pricing': {'n': int, 'failures': int, 'pairs': [(model, mid), ...],
                    'buckets': {bucket: [(model, mid), ...]}},
        'greeks': {'delta': [(model, market), ...], ...},
      }
    """
    from Backtests.core import moneyness_bucket
    result: Dict[str, Any] = {"pricing": {"n": 0, "failures": 0, "pairs": [], "buckets": {}},
                              "greeks": {g: [] for g in ALL_GREEK_FIELDS}}
    eval_rows = rows if name not in SLOW_MODELS else sample_slow_rows(rows, S)
    for row in eval_rows:
        K = row["strike"]
        cp = row["right"] == "C"
        iv = _row_iv(row)
        if iv is None or iv <= 0 or T is None:
            continue
        # pricing: market mid vs model at common market IV
        bid, ask = _to_float(row.get("bid")), _to_float(row.get("ask"))
        mid = _to_float(row.get("mid"))
        if (mid is None or mid <= 0) and bid is not None and ask is not None and ask >= bid >= 0:
            mid = (bid + ask) / 2.0
        elif (mid is None or mid <= 0) and row.get("close") is not None:
            mid = _to_float(row.get("close"))
        if mid is not None and mid > 0:
            pm = price_model(name, S, K, T, r, q, iv, cp, vv_ctx, sabr_cal, heston_ctx)
            if pm is None or pm < 0:
                result["pricing"]["failures"] += 1
            else:
                bucket = moneyness_bucket(K, S, row["right"])
                result["pricing"]["n"] += 1
                result["pricing"]["pairs"].append((pm, mid))
                result["pricing"]["buckets"].setdefault(bucket, []).append((pm, mid))
        # greeks: model vs market
        gm = greeks_model(name, S, K, T, r, q, iv, cp, vv_ctx, sabr_cal, heston_ctx)
        for g in ALL_GREEK_FIELDS:
            mv = row.get(g)
            if mv is not None:
                result["greeks"][g].append((gm.get(g), mv))
    return result


def aggregate_metrics(eval_result: Dict[str, Any]) -> Dict[str, Any]:
    """Turn an evaluate_model() result into summary dicts."""
    agg: Dict[str, Any] = {"pricing": {}, "greeks": {}}
    p = eval_result["pricing"]
    agg["pricing"]["n"] = p["n"]
    agg["pricing"]["failures"] = p["failures"]
    if p["n"]:
        agg["pricing"]["summary"] = summarize_errors(p["pairs"])
        agg["pricing"]["buckets"] = {
            b: summarize_errors(v) for b, v in p["buckets"].items()
        }
    for g in ALL_GREEK_FIELDS:
        agg["greeks"][g] = summarize_errors(eval_result["greeks"][g])
    return agg


__all__ = [
    "MODEL_ORDER", "SLOW_MODELS", "price_model", "greeks_model", "evaluate_model",
    "aggregate_metrics", "build_vv_context", "build_sabr_context",
    "build_heston_context", "sample_slow_rows",
    "MC_SIMS", "MC_STEPS", "HESTON_SIMS", "HESTON_STEPS",
]
