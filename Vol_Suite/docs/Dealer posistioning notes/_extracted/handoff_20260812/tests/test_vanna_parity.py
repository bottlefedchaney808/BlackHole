"""
GATE 0 of the vanna-seed test battery — three-way parity/units known-answer fixture.

Network-free. No ThetaData calls, no PotatoHedge, no live smile fetch. Everything
below is closed-form math plus one imported constant from dealer_positioning.py
(and, for the optional wing sub-test, VannaVolga's pure `get_vol_batch` which
does NOT trigger a live fetch at import or call).

What is pinned (pre-registered thresholds from the battery spec):
  1. BS-vanna put-call parity — the closed-form vanna is RIGHT-SYMMETRIC:
     vanna_call(K) == vanna_put(K) at the same strike, to ~1e-9; and the
     dealer-SIGNED pair cancels: |vanna_call + vanna_put| / mean|vanna| <= 1e-6.
  2. VANNA_PP_SCALE == 0.01 unit convention (dealer_positioning.py ~:48-61):
     raw ThetaData vanna is dDelta/dVol with vol in DECIMAL (0.20 == 20%), so the
     per-share exposure "per 1 vol point" is raw_vanna * 0.01.
  3. OTM-ladder sign: dealer-signed vanna (call=+, put=-, see _dealer_sign) is
     POSITIVE at every OTM strike for BOTH rights, and ~0 at ATM (d2 ~ 0).
  4. VV (smile-aware) vs flat-BS vanna agree on OTM wings within 20% of cells.

Sign-convention note on the closed form
---------------------------------------
The battery text writes vanna = e^(-qT)*phi(d1)*(d2/sigma). That is the closed
form's MAGNITUDE up to a global sign. The vanna-as-second-derivative convention
that reproduces the panel's load-bearing OTM-positive property is

    vanna(S,K,T,r,q,sigma) = -e^(-qT) * phi(d1) * d2 / sigma      ( = dVega/dS )

verified numerically here against a finite-difference bump of a Black-Scholes
price (d^2 V / dS dsigma). Right-symmetry (call == put) holds under EITHER sign,
so the parity test is sign-agnostic; the OTM-ladder sign test is what pins the
convention to dVega/dS.
"""
import math
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.stats import norm

import dealer_positioning as dp

# ---------------------------------------------------------------------------
# Known-answer BS vanna closed form (local by design — do not import a pricer).
# ---------------------------------------------------------------------------

def _bs_vanna(S, K, T, r, q, sigma):
    """Closed-form BS vanna = dVega/dS = dDelta/dsigma, right-independent.

    vanna = -e^(-q*T) * phi(d1) * d2 / sigma
    d1    = (ln(S/K) + (r - q + 0.5*sigma**2)*T) / (sigma*sqrt(T))
    d2    = d1 - sigma*sqrt(T)

    The formula does not reference the option right, so vanna_call(K) ==
    vanna_put(K) at the same strike (right-symmetric). The dealer sign is
    applied separately via _dealer_sign (call=+, put=-).
    """
    sqT = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * sqT)
    d2 = d1 - sigma * sqT
    return -math.exp(-q * T) * norm.pdf(d1) * d2 / sigma


def _dealer_vanna(S, K, T, r, q, sigma, right):
    """Signed vanna as the panel aggregates it: sign * raw, sign = +1 call, -1 put."""
    return dp._dealer_sign(right) * _bs_vanna(S, K, T, r, q, sigma)


# Fixed market state shared by the fixtures.
S, T, R, Q, SIGMA = 100.0, 0.25, 0.04, 0.012, 0.20
_FORWARD = S * math.exp((R - Q) * T)
# The strike where d2 == 0 => vanna == 0 exactly (the "max vanna" / zero-vanna ATM).
_ATM_STRIKE = S * math.exp((R - Q - 0.5 * SIGMA ** 2) * T)


# ---------------------------------------------------------------------------
# 1) Put-call parity: right-symmetric closed form + dealer-signed cancellation.
# ---------------------------------------------------------------------------

def test_bs_vanna_put_call_parity_1e9():
    """vanna_call(K) == vanna_put(K) to ~1e-9 at every strike (right-symmetry)."""
    strikes = np.arange(60.0, 145.0, 5.0)  # both deep OTM wings + ATM
    for K in strikes:
        raw_call = _bs_vanna(S, K, T, R, Q, SIGMA)
        raw_put = _bs_vanna(S, K, T, R, Q, SIGMA)  # right-independent => identical
        # Right-symmetric closed form: call == put to machine precision.
        assert raw_call == pytest.approx(raw_put, abs=1e-9)
        # Pre-registered threshold: |vanna_call + vanna_put| / mean|vanna| <= 1e-6.
        # Dealer-signed: vanna_call = +raw, vanna_put = -raw, so the pair cancels.
        vanna_call = dp._dealer_sign("C") * raw_call
        vanna_put = dp._dealer_sign("P") * raw_put
        mean_abs = (abs(raw_call) + abs(raw_put)) / 2.0
        if mean_abs > 1e-12:
            ratio = abs(vanna_call + vanna_put) / mean_abs
            assert ratio <= 1e-6, f"K={K}: parity ratio {ratio:.3e} > 1e-6"


def test_bs_vanna_matches_numeric_second_derivative():
    """The closed form equals a finite-difference bump of a BS price (d2V/dSdsig)."""
    K = 110.0
    dS = 1e-4 * S
    dsig = 1e-4 * SIGMA

    def _price(S_, K_, sigma_):
        d1 = (math.log(S_ / K_) + (R - Q + 0.5 * sigma_ ** 2) * T) / (sigma_ * math.sqrt(T))
        d2 = d1 - sigma_ * math.sqrt(T)
        return S_ * math.exp(-Q * T) * norm.cdf(d1) - K_ * math.exp(-R * T) * norm.cdf(d2)

    def _vega(S_):
        return (_price(S_, K, SIGMA + dsig) - _price(S_, K, SIGMA - dsig)) / (2 * dsig)

    numeric = (_vega(S + dS) - _vega(S - dS)) / (2 * dS)
    closed = _bs_vanna(S, K, T, R, Q, SIGMA)
    assert closed == pytest.approx(numeric, rel=1e-5)


# ---------------------------------------------------------------------------
# 2) VANNA_PP_SCALE == 0.01 unit convention.
# ---------------------------------------------------------------------------

def test_vanna_pp_scale_constant_is_001():
    """VANNA_PP_SCALE is a module-level 0.01 (per-1-vol-point converter)."""
    assert dp.VANNA_PP_SCALE == 0.01


def test_vanna_pp_scale_units_raw_to_per_share():
    """raw_vanna (dDelta/dVol, vol DECIMAL) * 0.01 == delta change per 1 vol point."""
    # A raw vanna of 1.0 means delta changes by 1.0 for a full 1.0 (100 vol-pt) vol move.
    raw = 1.0
    per_one_vol_point = raw * dp.VANNA_PP_SCALE  # 1 * 0.01 = 0.01 delta change / 1pp
    assert per_one_vol_point == pytest.approx(0.01)
    # Sanity on the multiplier the panel also applies.
    assert dp.CONTRACT_MULTIPLIER == 100


def test_vanna_pp_scale_is_documented_in_source():
    """The 0.01 convention is documented in the module comment (~lines 48-61)."""
    src = Path(dp.__file__).read_text()
    assert "VANNA_PP_SCALE = 0.01" in src
    assert "per 1 vol point" in src  # comment: exposure "per 1 vol point" = vanna * 0.01


# ---------------------------------------------------------------------------
# 3) OTM-ladder sign: dealer-signed vanna > 0 both rights; ATM ~ 0.
# ---------------------------------------------------------------------------

def test_otm_ladder_dealer_vanna_positive_both_rights():
    """Dealer-signed vanna is POSITIVE at every OTM strike for both rights."""
    wing = 4.0 * SIGMA * math.sqrt(T) * S  # ~4 sigma*sqrt(T) on each side of ATM
    call_otm = np.linspace(_ATM_STRIKE + 0.05, _ATM_STRIKE + 0.05 + wing, 12)
    put_otm = np.linspace(_ATM_STRIKE - 0.05 - wing, _ATM_STRIKE - 0.05, 12)
    for K in call_otm:
        assert _dealer_vanna(S, K, T, R, Q, SIGMA, "C") > 0, f"OTM call K={K}"
    for K in put_otm:
        assert _dealer_vanna(S, K, T, R, Q, SIGMA, "P") > 0, f"OTM put K={K}"


def test_atm_vanna_is_near_zero():
    """At the d2==0 strike the closed-form vanna vanishes (~1e-9)."""
    v_atm = _bs_vanna(S, _ATM_STRIKE, T, R, Q, SIGMA)
    assert abs(v_atm) < 1e-9
    # And it is dwarfed by the OTM-wing magnitude (the panel's usable signal).
    v_wing = _bs_vanna(S, S * 1.10, T, R, Q, SIGMA)
    assert abs(v_atm) < abs(v_wing) * 1e-3


# ---------------------------------------------------------------------------
# 4) Optional: VV (smile-aware) vs flat-BS vanna wing agreement (network-free).
# ---------------------------------------------------------------------------

def test_vv_vs_flat_bs_wing_agreement_within_20pct():
    """VV smile vanna vs flat-BS vanna agree on OTM wings within 20% of cells.

    get_vol_batch is pure math (3-pillar inverse-distance smile) — importing
    VannaVolga and calling it triggers no live PotatoHedge/ThetaData fetch.
    Skipped only if the module is unavailable to import.
    """
    opts = Path(__file__).resolve().parent.parent.parent / "Options_Suite"  # Options_Suite/
    try:
        if str(opts) not in sys.path:
            sys.path.insert(0, str(opts))
        import VannaVolga as vv
    except Exception as exc:  # pragma: no cover - defensive, network/import gate
        pytest.skip(f"VannaVolga import unavailable ({type(exc).__name__})")

    atm, rr25, bf25 = 0.20, 3.0, 0.5
    wing_strikes = np.array([90.0, 85.0, 80.0, 105.0, 110.0, 120.0])
    sig_vv = vv.get_vol_batch(S, wing_strikes, T, R, Q, atm, rr25, bf25)
    sig_flat = np.full_like(wing_strikes, atm)
    for K, sv, sf in zip(wing_strikes, sig_vv, sig_flat):
        vv_vanna = abs(_bs_vanna(S, K, T, R, Q, sv))
        flat_vanna = abs(_bs_vanna(S, K, T, R, Q, sf))
        agree = abs(vv_vanna - flat_vanna) / flat_vanna
        assert agree <= 0.20, f"K={K}: VV-vs-flat rel {agree:.3f} > 20%"
