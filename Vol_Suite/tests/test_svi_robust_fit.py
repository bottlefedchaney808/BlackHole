"""
Regression tests for the robust SVI fit (the flat-smile fix, 2026-08-13).

The exact 3-observable SSVI construction in calibrate_ssvi is structurally
over-constrained on a steep equity put skew: psi_t (ATM skew) is so large that
the butterfly-arbitrage clamp caps phi <= 4/(1+|rho|), rho saturates at +-1,
and the fit collapses to the flattest arbitrage-free curve -- badly underfitting
the steep market put wing (measured on real SPY: ref 0.26 vs market 0.65 at
K=300). These tests assert the robust least-squares SVI fit does NOT do that:
it must track a steep skew/wing, not flatten to sigma_atm.

Network-free: synthetic steep-skew chains.
"""
import math

import numpy as np
import pytest

import svi_rp


def _steep_skew_chain(spot=100.0, T=0.28, sig_atm=0.20, skew=-0.9):
    """A realistic steep equity put skew: IV ~ sig_atm + skew*log(K/spot),
    with an extra far-put steepening so the wing is pronounced (like SPY)."""
    chain = {}
    for k in range(55, 100):  # OTM puts, including far OTM
        iv = sig_atm + skew * math.log(k / spot) + 0.15 * (1 - k / 100.0)
        chain[(float(k), "P")] = max(iv, 0.05)
    for k in range(101, 130):  # OTM calls (mild positive wing)
        iv = sig_atm + 0.08 * math.log(k / spot)
        chain[(float(k), "C")] = max(iv, 0.05)
    oi = {kv: 1000 for kv in chain}
    return chain, oi


@pytest.mark.unit
def test_robust_svi_tracks_steep_put_wing():
    """The robust SVI fit must NOT flatten on a steep put skew: reference IV at
    the far OTM put should be meaningfully ABOVE sigma_atm (tracking the market
    wing), not pinned flat at the ATM level."""
    chain, oi = _steep_skew_chain()
    ref = svi_rp.calibrate_svi(chain, 100.0, 0.28, oi_by=oi)
    # Far-OTM put reference must be substantially above ATM (the wing is steep).
    far_put = ref.sigma_ref(60.0)
    atm = ref.sigma_atm
    assert far_put > atm + 0.05, f"far-OTM put ref {far_put:.3f} not above ATM {atm:.3f} -- smile flattened"


@pytest.mark.unit
def test_robust_svi_runs_and_has_sane_shape():
    chain, oi = _steep_skew_chain()
    ref = svi_rp.calibrate_svi(chain, 100.0, 0.28, oi_by=oi)
    # ATM passes through
    assert ref.sigma_ref(100.0) == pytest.approx(ref.sigma_atm, abs=0.03)
    # monotone declining into the put wing
    assert ref.sigma_ref(70.0) > ref.sigma_ref(95.0)
    assert ref.sigma_ref(105.0) > ref.sigma_ref(115.0)
    # finite, positive
    assert 0.05 < ref.sigma_ref(55.0) < 1.0
    assert 0.05 < ref.sigma_ref(130.0) < 1.0


@pytest.mark.unit
def test_robust_svi_mark_chain_still_consistent():
    chain, oi = _steep_skew_chain()
    ref = svi_rp.calibrate_svi(chain, 100.0, 0.28, oi_by=oi)
    marks = ref.mark_chain(chain, oi)
    assert len(marks) == len(chain)
    for (_k, _r, sig, refv, diff, mark, oiv) in marks:
        assert (mark == "SHORT") == (diff > 0)
        assert (mark == "LONG") == (diff <= 0)
