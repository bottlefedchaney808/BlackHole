#!/usr/bin/env python3
"""SVI-RP reference smile — reusable module.

Calibrates an arbitrage-free SSVI (Surface SVI, Gatheral-Jacquier 2014) reference
implied-vol smile from a single OTM option chain, and marks each strike cheap/rich
against it. The RP (variance-swap replication) strip supplies the skew-integrated
variance-swap level as a *consistency check*; the smile's LEVEL anchor is the
chain's own ATM implied vol (per G-J §5.1: a smile is completely defined by three
observables — ATM vol, ATM skew, and one wing measure).

Reference: docs/superpowers/specs/research-variance-smile-20260811.md
and Gatheral & Jacquier, "Arbitrage-free SVI volatility surfaces" (arXiv:1204.0646).

Usage (importable):
    from svi_rp import calibrate_ssvi
    ref = calibrate_ssvi(chain_iv, spot, T, oi_by=oi_by, r=0.04, q=0.012)
    sig_ref = ref.sigma_ref(750.0)          # reference IV at a strike
    marks   = ref.mark_chain(chain_iv, oi_by)  # per-strike rich/SHORT vs cheap/LONG

Pure functions (no network): all calibration takes pre-fetched chain_iv /
oi_by dicts keyed by (strike, right) -> vol / open-interest.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np


# Default equity risk assumptions (same as backtest_stage3).
R_DEFAULT = 0.04
Q_DEFAULT = 0.012
# OI floor below which a strike is treated as an illiquid wing (skipped when
# computing the variance-swap level and the wing slopes).
OI_ILLIQUID = 10
# Tight ATM window for the ATM-skew estimate (log-moneyness units).
ATM_SKEW_WINDOW = 0.05


def bs_price(S: float, K: float, T: float, sig: float,
             r: float, q: float, right: str) -> float:
    """Black-Scholes European option price."""
    if sig <= 0 or T <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r - q + 0.5 * sig * sig) * T) / (sig * math.sqrt(T))
    d2 = d1 - sig * math.sqrt(T)
    n = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
    if right == "C":
        return S * math.exp(-q * T) * n(d1) - K * math.exp(-r * T) * n(d2)
    return K * math.exp(-r * T) * n(-d2) - S * math.exp(-q * T) * n(-d1)


def bs_vega(S: float, K: float, T: float, sig: float,
            r: float, q: float) -> float:
    """Black-Scholes vega."""
    if sig <= 0 or T <= 0:
        return 1.0
    d1 = (math.log(S / K) + (r - q + 0.5 * sig * sig) * T) / (sig * math.sqrt(T))
    return S * math.exp(-q * T) * math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) * math.sqrt(T)


def svi_w(k: np.ndarray, a: float, b: float, rho: float,
          m: float, sigma: float) -> np.ndarray:
    """Raw SVI total implied variance (eq 3.1)."""
    return a + b * (rho * (k - m) + np.sqrt((k - m) ** 2 + sigma ** 2))


def ssvi_w(k: np.ndarray, theta_t: float, phi: float, rho: float) -> np.ndarray:
    """SSVI total implied variance (eq 4.1). theta_t = ATM total variance."""
    return 0.5 * theta_t * (1.0 + rho * phi * k
                            + np.sqrt((phi * k + rho) ** 2 + (1.0 - rho ** 2)))


@dataclass
class SviRpReference:
    """Calibrated SSVI reference smile + cheap/rich marking.

    Fields:
      sigma_atm   ATM implied vol (the level anchor)
      theta_t     ATM total variance = sigma_atm^2 * T
      psi_t       ATM vol skew (d sigma/dk at k=0)
      p_t         put-wing asymptotic vol slope
      phi, rho    SSVI shape params (eq 4.1)
      sigma_swap  variance-swap implied vol (skew-integrated; RP consistency check)
      K_var       fair variance from the RP strip (2/T) sum w_K Price
      F0          forward
      T           time to expiry (years)
    """
    sigma_atm: float
    theta_t: float
    psi_t: float
    p_t: float
    phi: float
    rho: float
    sigma_swap: float
    K_var: float
    F0: float
    T: float
    butterfly_clamped: bool = False

    def sigma_ref(self, strike: float) -> float:
        """Reference implied vol at a given strike."""
        k = math.log(strike / self.F0)
        w = ssvi_w(np.array([k]), self.theta_t, self.phi, self.rho)[0]
        return math.sqrt(max(w, 0.0) / self.T)

    def sigma_ref_batch(self, strikes) -> np.ndarray:
        ks = np.log(np.asarray(strikes, dtype=float) / self.F0)
        w = ssvi_w(ks, self.theta_t, self.phi, self.rho)
        return np.sqrt(np.maximum(w, 0.0) / self.T)

    def mark_chain(self, chain_iv: Dict[Tuple[float, str], float],
                   oi_by: Optional[Dict[Tuple[float, str], int]] = None,
                   ) -> List[Tuple[float, str, float, float, float, str, int]]:
        """Per-strike cheap/rich marking.

        Returns rows (strike, right, market_iv, ref_iv, diff, mark, oi) with
        mark='SHORT' (rich, market_iv > ref) or 'LONG' (cheap).
        """
        rows: List[Tuple[float, str, float, float, float, str, int]] = []
        for (k, right), sig in chain_iv.items():
            ref = self.sigma_ref(k)
            diff = sig - ref
            mark = "SHORT" if diff > 0 else "LONG"
            oi = int((oi_by or {}).get((k, right), 0))
            rows.append((k, right, sig, ref, diff, mark, oi))
        rows.sort(key=lambda r_: r_[0])
        return rows

    def seed(self, chain_iv: Dict[Tuple[float, str], float],
             oi_by: Optional[Dict[Tuple[float, str], int]] = None,
             ) -> Tuple[int, int, float]:
        """Dealer seed: sum of sign*OI over the OTM set.

        Returns (seed_short_oi, seed_long_oi, net_seed). SHORT (rich) strikes
        carry +OI short; LONG (cheap) carry +OI long; net = long - short.
        """
        marks = self.mark_chain(chain_iv, oi_by)
        short_oi = sum(r[6] for r in marks if r[5] == "SHORT")
        long_oi = sum(r[6] for r in marks if r[5] == "LONG")
        return short_oi, long_oi, float(long_oi - short_oi)

    def summary(self) -> str:
        return (f"sigma_atm={self.sigma_atm:.4f} theta_t={self.theta_t:.5f} "
                f"psi_t={self.psi_t:.4f} p_t={self.p_t:.4f} "
                f"phi={self.phi:.4f} rho={self.rho:.3f} "
                f"sigma_swap={self.sigma_swap:.4f} K_var={self.K_var:.5f}")


def _linear_slope(x: np.ndarray, y: np.ndarray) -> Tuple[Optional[float], Optional[float]]:
    """Slope + intercept of OLS y~x. Returns (slope, intercept) or (None,None)."""
    if x.size < 3:
        return None, None
    A = np.column_stack([np.ones(x.size), x])
    coeff, *_ = np.linalg.lstsq(A, y, rcond=None)
    return float(coeff[1]), float(coeff[0])


def calibrate_ssvi(
    chain_iv: Dict[Tuple[float, str], float],
    spot: float,
    T: float,
    oi_by: Optional[Dict[Tuple[float, str], int]] = None,
    otm_weights: Optional[Dict[Tuple[float, str], float]] = None,
    r: float = R_DEFAULT,
    q: float = Q_DEFAULT,
) -> SviRpReference:
    """Calibrate the SSVI reference smile from one OTM chain.

    chain_iv:  {(strike, right): implied_vol} restricted to the OTM set
               (calls above spot, puts below), per `_otm_leg_weights`.
    spot:      underlying spot.
    T:         time to expiry in years.
    oi_by:     {(strike, right): open_interest} for OI-weighting (illiquid wings
               skipped when computing the variance-swap level / wing slopes).
    otm_weights: {(strike, right): replication weight} from the RP strip. If
               omitted, computed from replication_reference._otm_leg_weights.
    r, q:      risk-free rate and dividend yield.

    Steps (Gatheral-Jacquier §4 / §5.1):
      1. theta_t = sigma_atm^2 * T   (level anchor, from the chain's ATM IV)
      2. psi_t   = ATM vol skew       (d sigma/dk at k=0, tight ATM window)
      3. p_t     = put-wing slope     (far OTM puts, equity default 3rd observable)
      4. SSVI (eq 4.1) fully determined from theta_t, psi_t, p_t; butterfly
         clamp to the Lee upper bound phi*(1+|rho|)<=4 if needed.
      5. K_var / sigma_swap computed from the RP strip as a consistency check.
    """
    oi_by = oi_by or {}
    F0 = spot * math.exp((r - q) * T)
    ks_arr = np.array([math.log(k / F0) for (k, right) in chain_iv])
    sigs_arr = np.array([chain_iv[(k, right)] for (k, right) in chain_iv])
    sides = [right for (k, right) in chain_iv]

    # --- level anchor: ATM implied vol (mean of strikes nearest spot) ---
    near = sorted(chain_iv.keys(), key=lambda kv: abs(kv[0] - spot))[:6]
    sigma_atm = float(np.mean([chain_iv[(k, right)] for (k, right) in near]))
    theta_t = max(sigma_atm ** 2 * T, 0.0)

    # --- ATM skew psi_t (tight window, few strikes each side of ATM) ---
    atm_mask = (np.abs(ks_arr) < ATM_SKEW_WINDOW)
    slope, intercept = _linear_slope(ks_arr[atm_mask], sigs_arr[atm_mask])
    if slope is None:
        atm_mask = (np.abs(ks_arr) < 0.15)
        slope, intercept = _linear_slope(ks_arr[atm_mask], sigs_arr[atm_mask])
    psi_t = slope if slope is not None else 0.0

    # --- put-wing slope p_t (far OTM puts) ---
    put_mask = (ks_arr < -0.5) & (np.array(sides) == "P")
    p_slope, _ = _linear_slope(ks_arr[put_mask], sigs_arr[put_mask])
    p_t = p_slope if p_slope is not None else psi_t

    # --- SSVI shape from the 3 observables (skew identities) ---
    #   psi_t = sigma_atm * rho * phi / 4
    #   p_t   = sigma_atm * phi * (rho-1) / 4
    # => phi*rho = 4 psi_t / sigma_atm ; phi*(rho-1) = 4 p_t / sigma_atm
    phi_rho = 4.0 * psi_t / max(sigma_atm, 1e-9)
    phi_rm1 = 4.0 * p_t / max(sigma_atm, 1e-9)
    phi = phi_rho - phi_rm1
    rho = (phi_rho / phi) if abs(phi) > 1e-12 else 0.0
    rho = max(-0.999, min(0.999, rho))

    # --- butterfly-arb clamp (Corollary 4.1 cond 1): phi*(1+|rho|) <= 4 ---
    butterfly_clamped = False
    if phi * (1.0 + abs(rho)) > 4.0:
        phi = 4.0 / max(1.0 + abs(rho), 1e-9)
        butterfly_clamped = True

    # --- RP strip fair variance (variance-swap level, skew-integrated) ---
    #   K_var = (2/T) sum_OTM w_K Price(K); skip illiquid wings.
    if otm_weights is None:
        try:
            import replication_reference as rr
            otm_weights = rr._otm_leg_weights(chain_iv, spot, T)
        except Exception:
            otm_weights = {
                (k, right): 1.0 / max(k * k, 1e-9)
                for (k, right) in chain_iv
                if (right == "C" and k > spot) or (right == "P" and k < spot)
            }
    var = 0.0
    for (k, right), ww in otm_weights.items():
        if oi_by.get((k, right), 0) < OI_ILLIQUID:
            continue
        var += (2.0 / T) * ww * bs_price(spot, k, T, chain_iv[(k, right)], r, q, right)
    K_var = max(var, 0.0)
    sigma_swap = math.sqrt(K_var)

    return SviRpReference(
        sigma_atm=sigma_atm, theta_t=theta_t, psi_t=psi_t, p_t=p_t,
        phi=phi, rho=rho, sigma_swap=sigma_swap, K_var=K_var,
        F0=F0, T=T, butterfly_clamped=butterfly_clamped,
    )
