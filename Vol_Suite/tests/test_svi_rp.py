"""Known-answer tests for the reusable SVI-RP reference-smile module.

The SSVI calibration is pure math (no network): given a synthetic OTM chain,
theta_t = sigma_atm^2*T, the SSVI curve passes through sigma_atm at ATM, and
marking is internally consistent. These assert the contract.
"""
import math

import numpy as np
import pytest

import svi_rp
from svi_rp import ssvi_w, calibrate_ssvi, bs_price, bs_vega


def _synthetic_chain(spot=100.0, T=0.25, sig_atm=0.20, skew=-0.15, wing=0.05,
                     steep_put=False):
    """Build a synthetic OTM chain: puts below spot, calls above, linear IV.

    steep_put=True gives the OTM puts an EXTRA steepening so ATM skew (psi_t)
    differs from the far-put-wing slope (p_t), avoiding the uniform-slope
    degenerate phi=0 case.
    """
    chain = {}
    for k in range(40, 100):  # OTM puts (deep enough that far-put wing slope is measurable)
        iv = sig_atm + skew * math.log(k / spot)
        if steep_put and k < 90:
            iv += 0.25 * (90 - k) / 20.0  # steepen deep puts
        chain[(float(k), "P")] = max(iv, 0.05)
    for k in range(102, 130):  # OTM calls
        chain[(float(k), "C")] = sig_atm + wing * math.log(k / spot)
    oi = {kv: 1000 for kv in chain}
    return chain, oi


@pytest.mark.unit
def test_ssvi_atm_level():
    """SSVI curve equals sigma_atm at the forward (k=0)."""
    theta_t = 0.20 ** 2 * 0.25  # sigma_atm=0.20, T=0.25
    w_atm = ssvi_w(np.array([0.0]), theta_t, 1.0, -0.3)[0]
    assert math.sqrt(w_atm / 0.25) == pytest.approx(0.20, abs=1e-9)


@pytest.mark.unit
def test_calibrate_ssvi_passes_atm():
    """calibrate_ssvi returns theta_t = sigma_atm^2*T and sigma_ref(spot)=sigma_atm."""
    chain, oi = _synthetic_chain()
    spot = 100.0
    T = 0.25
    ref = calibrate_ssvi(chain, spot, T, oi_by=oi)
    # theta_t level anchor
    assert ref.theta_t == pytest.approx(ref.sigma_atm ** 2 * T, rel=1e-6)
    # reference at the forward equals sigma_atm (within tolerance)
    ref_atm = ref.sigma_ref(spot)
    assert ref_atm == pytest.approx(ref.sigma_atm, abs=0.03)


@pytest.mark.unit
def test_calibrate_ssvi_negative_skew_gives_negative_rho():
    """A downward-sloping put wing (negative skew) yields rho<0 (equity convention)."""
    chain, oi = _synthetic_chain(skew=-0.20, steep_put=True)
    ref = calibrate_ssvi(chain, 100.0, 0.25, oi_by=oi)
    assert ref.rho < 0


@pytest.mark.unit
def test_mark_chain_consistency():
    """mark_chain: SHORT iff market_iv > ref; LONG iff below; OI carried."""
    chain, oi = _synthetic_chain()
    ref = calibrate_ssvi(chain, 100.0, 0.25, oi_by=oi)
    marks = ref.mark_chain(chain, oi)
    assert len(marks) == len(chain)
    for (k, right, sig, refv, diff, mark, oi_v) in marks:
        assert (mark == "SHORT") == (diff > 0)
        assert (mark == "LONG") == (diff <= 0)
        assert oi_v == 1000


@pytest.mark.unit
def test_seed_is_oi_balanced():
    """seed returns (short_oi, long_oi, net=long-short) consistent with marks."""
    chain, oi = _synthetic_chain()
    ref = calibrate_ssvi(chain, 100.0, 0.25, oi_by=oi)
    short_oi, long_oi, net = ref.seed(chain, oi)
    marks = ref.mark_chain(chain, oi)
    assert short_oi == sum(m[6] for m in marks if m[5] == "SHORT")
    assert long_oi == sum(m[6] for m in marks if m[5] == "LONG")
    assert net == pytest.approx(long_oi - short_oi)


@pytest.mark.unit
def test_bs_helpers_known_answers():
    """BS price/vega known answers (ATM call ~ 0.5*S + vol term; vega>0)."""
    S, K, T, sig = 100.0, 100.0, 0.25, 0.20
    c = bs_price(S, K, T, sig, 0.0, 0.0, "C")
    p = bs_price(S, K, T, sig, 0.0, 0.0, "P")
    # put-call parity with zero rates: C - P = S - K = 0
    assert c - p == pytest.approx(0.0, abs=1e-9)
    assert bs_vega(S, K, T, sig, 0.0, 0.0) > 0
    # ATM call is worth more than intrinsic (positive time value)
    assert c > 0 and c > 0.0
