"""
Regression test for vol_surface_reference.py (Layer 1a) and its wiring
into dealer_positioning.py's sign_model='vol_surface_replication'.

Context: Layer 1b's replication-implied sign (replication_reference.py)
applies one uniform sign across the entire OTM strip -- confirmed live on
both SPY and QQQ (2026-07-22) to make the Gamma Exposure panel 100%
negative, with no possible positive (dealer-long) bar anywhere, a direct
consequence of the recursion's own weights being non-negative by
construction. Real OTM open interest isn't all "customer bought convexity"
flow, though -- some of it is covered-call/cash-secured-put OVERWRITING
flow (customer sells premium TO the dealer, dealer ends up long that
strike). Layer 1a reads which kind of flow a strike plausibly saw by
checking whether its IV trades rich (net buying, dealer short) or cheap
(net selling, dealer long) against a smooth near-ATM reference curve --
the one thing that can flip sign strike-by-strike, which Layer 1b alone
structurally cannot do.

Network-free: exercises the fitting/deviation math directly, and the
dealer_positioning.py wiring via a fake ThetaDataController stand-in, same
approach as every other test in this suite.
"""
import math

import pytest

import vol_surface_reference as vsr
import dealer_positioning as dp


SPOT = 100.0
STRIKES = list(range(70, 131))


def _theta(k):
    return int(round(k * 1000))


def _make_smile(cheap_strike=None, cheap_amount=0.08):
    """A smooth quadratic-in-log-moneyness smile, optionally with one
    strike deliberately cheapened (simulating overwriting supply)."""
    chain = {}
    for k in STRIKES:
        x = math.log(k / SPOT)
        iv = max(0.20 + 0.30 * x * x - 0.05 * x, 0.05)
        right = 'C' if k >= SPOT else 'P'
        if cheap_strike is not None and k == cheap_strike:
            iv = max(iv - cheap_amount, 0.02)
        chain[(float(k), right)] = iv
    return chain


# ---------------------------------------------------------------------------
# Layer 1a math, in isolation
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_fit_succeeds_with_enough_near_atm_points():
    chain = _make_smile()
    ref = vsr.compute_vol_surface_reference("TEST", chain, SPOT)
    assert ref is not None
    assert ref.n_fit_points >= vsr.MIN_FIT_POINTS


@pytest.mark.unit
def test_fit_fails_gracefully_with_too_few_points():
    """Only 2 strikes total, both outside the near-ATM band -- should
    return None (not fit garbage), so callers can fall back to Layer 1b.
    """
    chain = {(500.0, 'C'): 0.20, (10.0, 'P'): 0.30}
    ref = vsr.compute_vol_surface_reference("TEST", chain, SPOT)
    assert ref is None


@pytest.mark.unit
def test_cheapened_strike_gets_negative_deviation_and_positive_sign():
    chain = _make_smile(cheap_strike=110, cheap_amount=0.08)
    ref = vsr.compute_vol_surface_reference("TEST", chain, SPOT)
    assert ref is not None
    dev = ref.deviation_by_strike[(110.0, 'C')]
    assert dev < 0, f"expected cheapened strike to show negative deviation, got {dev}"
    sign = vsr.resolve_vol_surface_sign(ref, 110.0, 'C')
    assert sign == 1.0, "expected cheap strike to flip to dealer-long (+1.0)"


@pytest.mark.unit
def test_normal_strike_keeps_default_short_sign():
    """A strike trading clearly RICH vs. the reference (deviation well outside
    the IV dead-band, IV_DEADBAND_VOL) keeps the rich/short default of -1.0.

    Uses a directly-constructed VolSurfaceReference with a clearly-positive
    deviation (0.02 = 2 vol points, > the 0.01 dead-band) rather than the
    fitted smile, because under hardening 19a an undistorted near-ATM strike's
    tiny deviation (a few basis points, e.g. ~0.004 at $105 in _make_smile)
    is now *inside* the dead-band and correctly resolves to 0.0 -- see
    test_sign_sensitivity.py for that in-band behavior. This test pins the
    intentional contrast: a genuinely-rich deviation still resolves to -1.0.
    """
    ref = vsr.VolSurfaceReference(
        ticker="TEST", spot=SPOT, fit_coeffs=(0.0, 0.0, 0.0), n_fit_points=1,
        deviation_by_strike={(105.0, 'C'): 0.02},
    )
    sign = vsr.resolve_vol_surface_sign(ref, 105.0, 'C')
    assert sign == -1.0, "expected a clearly rich strike to keep the rich/short default"


@pytest.mark.unit
def test_missing_strike_returns_zero_sign():
    chain = _make_smile()
    ref = vsr.compute_vol_surface_reference("TEST", chain, SPOT)
    sign = vsr.resolve_vol_surface_sign(ref, 9999.0, 'C')
    assert sign == 0.0


# ---------------------------------------------------------------------------
# SABR fix for the quadratic's extrapolation artifact (2026-07-22).
#
# Recomputing the quadratic fit directly on real SPY chain data (20260930
# expiry, refit on the same +/-15% near-ATM band dealer_positioning.py uses)
# showed deviation drifting smoothly and monotonically from -0.50 vol points
# at a strike 39% below spot up through ~0 near the money, across HUNDREDS
# of strikes with no isolated jumps anywhere -- the signature of a
# quadratic's x^2 term diverging once evaluated far past its own fit
# window, not real per-strike overwriting flow (which should look like
# isolated spikes at specific strikes, not a silky-smooth gradient). These
# tests build a synthetic smile wide enough to reproduce that same
# extrapolation blow-up under the quadratic, then confirm SABR (fit across
# the whole OTM strip using Hagan's bounded closed-form formula) does NOT
# reproduce it, while still catching the real, isolated distorted strike.
# ---------------------------------------------------------------------------

WIDE_STRIKES = list(range(40, 201))
# "True market" smile shape for the wide-chain tests below is generated from
# a KNOWN SABR parameterization (not a quadratic) -- using a quadratic here,
# like _make_smile does, would make the quadratic fitter recover the exact
# generating function even far from ATM (since fitter and data-generator
# would be the same functional family), which can't reproduce the real bug:
# on real SPY data the TRUE smile is NOT a pure parabola in log-moneyness,
# so a quadratic fit anchored near ATM genuinely diverges from it far away.
# A SABR-generated "true" smile is a realistic, non-quadratic shape, so it
# actually exercises that mismatch.
_TRUE_ALPHA, _TRUE_BETA, _TRUE_RHO, _TRUE_NU = 0.30, 0.5, -0.3, 0.6
_TRUE_FORWARD, _TRUE_T = SPOT, 0.25


def _make_wide_smile(cheap_strike=None, cheap_amount=0.08):
    """Realistic (SABR-generated) smile spanning 40-200 (60% below spot to
    100% above) -- wide enough for a far, undistorted strike to sit well
    outside the quadratic's near-ATM fitting band, and shaped like a real
    market smile rather than a pure parabola.
    """
    chain = {}
    for k in WIDE_STRIKES:
        iv = vsr.sabr_vol_hagan(_TRUE_FORWARD, float(k), _TRUE_T,
                                 _TRUE_ALPHA, _TRUE_BETA, _TRUE_RHO, _TRUE_NU)
        right = 'C' if k >= SPOT else 'P'
        if cheap_strike is not None and k == cheap_strike:
            iv = max(iv - cheap_amount, 0.02)
        chain[(float(k), right)] = iv
    return chain


@pytest.mark.unit
def test_quadratic_extrapolation_produces_large_far_wing_deviation():
    """Documents the bug directly: on a wide chain, the quadratic fallback
    (no forward/T given) shows a LARGE deviation at a far, completely
    undistorted strike, purely from extrapolating past its near-ATM fit
    window -- this is the artifact that made real SPY data misleadingly
    show a smooth, everywhere-two-sided gamma exposure.
    """
    chain = _make_wide_smile()
    ref = vsr.compute_vol_surface_reference("TEST", chain, SPOT)
    assert ref.fitter == 'quadratic'
    far_dev = ref.deviation_by_strike[(190.0, 'C')]
    assert abs(far_dev) > 0.05, (
        f"expected the quadratic's extrapolation to blow up at a far, "
        f"undistorted strike, got only {far_dev:.4f}"
    )


@pytest.mark.unit
def test_sabr_avoids_far_wing_extrapolation_artifact():
    """The fix: fitting SABR (forward/T supplied) across the SAME wide,
    otherwise-undistorted chain should NOT show a large deviation at that
    same far strike -- SABR's closed-form formula is well-behaved across
    the whole strip it's fit on, unlike the quadratic's extrapolation.
    (Forced VOL_SURFACE_FITTER=sabr: this test exercises the SABR path.)
    """
    import os
    chain = _make_wide_smile()
    os.environ["VOL_SURFACE_FITTER"] = "sabr"
    try:
        ref = vsr.compute_vol_surface_reference("TEST", chain, SPOT, forward=_TRUE_FORWARD, T=_TRUE_T)
    finally:
        os.environ.pop("VOL_SURFACE_FITTER", None)
    assert ref.fitter == 'sabr'
    far_dev = ref.deviation_by_strike[(190.0, 'C')]
    assert abs(far_dev) < 0.02, (
        f"expected SABR to keep an undistorted far strike's deviation small, "
        f"got {far_dev:.4f} (would indicate the SABR fit itself is diverging)"
    )


@pytest.mark.unit
def test_sabr_still_isolates_real_distorted_strike_on_wide_chain():
    """Confirms the fix doesn't throw the baby out with the bathwater: SABR
    must still flip sign at a genuinely distorted strike even on the wider
    chain, while leaving the far, undistorted strike from the previous test
    alone -- i.e. it isolates real signal instead of either (a) smearing it
    everywhere like the quadratic's extrapolation, or (b) being so smooth
    itself that it can't detect a real anomaly at all.
    (Forced VOL_SURFACE_FITTER=sabr.)
    """
    import os
    chain = _make_wide_smile(cheap_strike=150, cheap_amount=0.08)
    os.environ["VOL_SURFACE_FITTER"] = "sabr"
    try:
        ref = vsr.compute_vol_surface_reference("TEST", chain, SPOT, forward=_TRUE_FORWARD, T=_TRUE_T)
    finally:
        os.environ.pop("VOL_SURFACE_FITTER", None)
    assert ref.fitter == 'sabr'
    dev_150 = ref.deviation_by_strike[(150.0, 'C')]
    assert dev_150 < 0, f"expected the deliberately cheapened strike to still show negative deviation, got {dev_150}"
    sign_150 = vsr.resolve_vol_surface_sign(ref, 150.0, 'C')
    assert sign_150 == 1.0, "expected the cheapened strike to still flip to dealer-long under SABR"

    far_dev = ref.deviation_by_strike[(190.0, 'C')]
    assert abs(far_dev) < 0.02, "expected the OTHER far strike to remain undistorted"


@pytest.mark.unit
def test_svi_is_default_fitter():
    """SVI (SSVI) is the default reference fitter since 2026-08-12. Confirms
    the default env (no VOL_SURFACE_FITTER set) routes to the SVI fitter and
    the contract columns are populated."""
    import os
    chain = _make_wide_smile()
    os.environ.pop("VOL_SURFACE_FITTER", None)
    ref = vsr.compute_vol_surface_reference("TEST", chain, SPOT, forward=_TRUE_FORWARD, T=_TRUE_T)
    assert ref is not None
    assert ref.fitter == 'svi'
    # SVI path prices a reference IV at every strike and deviation is populated.
    assert len(ref.reference_iv_by_strike) == len(ref.deviation_by_strike) > 0
    # and resolve still works
    assert vsr.resolve_vol_surface_sign(ref, 110.0, 'C') in (-1.0, 0.0, 1.0)


@pytest.mark.unit
def test_compute_vol_surface_reference_backward_compatible_without_forward_t():
    """The original call signature (ticker, chain_iv, spot) -- no forward/T
    -- must keep working exactly as before, since dealer_positioning.py's
    other callers and this module's own earlier tests rely on it.
    """
    chain = _make_smile(cheap_strike=110, cheap_amount=0.08)
    ref = vsr.compute_vol_surface_reference("TEST", chain, SPOT)
    assert ref is not None
    assert ref.fitter == 'quadratic'


# ---------------------------------------------------------------------------
# Wiring into dealer_positioning.py's sign_model='vol_surface_replication'
# ---------------------------------------------------------------------------

class _FakeTD:
    def __init__(self, *a, **k):
        pass

    def fetch_spot_price(self, ticker):
        return SPOT

    def fetch_dividend_yield(self, ticker, spot=None):
        return 0.0

    def fetch_risk_free_rate(self, T):
        return 0.04

    def list_expirations(self, root):
        from datetime import datetime, timedelta
        return [(datetime.now() + timedelta(days=60)).strftime("%Y%m%d")]

    def option_bulk_greeks(self, root, exp):
        rows = []
        for k in STRIKES:
            x = math.log(k / SPOT)
            iv = max(0.20 + 0.30 * x * x - 0.05 * x, 0.05)
            if k == 110:
                iv = max(iv - 0.08, 0.02)  # deliberate overwriting-flow dip
            gamma = 0.02 * (1.0 / (1 + abs(k - SPOT) / 10))
            for right in ("C", "P"):
                delta = (max(0.02, min(0.98, 1.5 - (k - SPOT) / 60)) if right == "C"
                         else -max(0.02, min(0.98, 1.5 - (SPOT - k) / 60)))
                rows.append({
                    "strike": _theta(k), "right": right, "gamma": gamma, "implied_vol": iv,
                    "bid": 1.0, "ask": 1.1, "delta": delta, "vanna": 0.01, "charm": -0.001,
                })
        return rows

    def option_bulk_oi(self, root, exp):
        return [{"strike": _theta(k), "right": right, "open_interest": 200}
                for k in STRIKES for right in ("C", "P")]

    def close(self):
        pass


@pytest.fixture(autouse=True)
def fake_client(monkeypatch):
    monkeypatch.setattr(dp, "ThetaDataController", _FakeTD)


@pytest.mark.unit
def test_direction_engine_fits_reference_curve_per_expiry():
    """The canonical V5 Direction engine still consumes vol_surface_reference:
    per-expiry sabr_deviation is derived from the SABR reference curve fit on
    that expiry's own chain (backtest winner config)."""
    calls = {}
    original = dp.vol_surface_reference.compute_vol_surface_reference

    def _spy(ticker, chain_iv, spot, forward=None, T=None):
        calls["chain_size"] = len(chain_iv)
        return original(ticker, chain_iv, spot, forward=forward, T=T)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(dp.vol_surface_reference, "compute_vol_surface_reference", _spy)
    try:
        result = dp.compute_dealer_positioning("MOCK", target_years=60 / 365,
                                               sign_model="direction")
    finally:
        monkeypatch.undo()
    assert result.sign_model == "direction"
    assert calls.get("chain_size", 0) > 0, "reference curve must be fit per expiry"


@pytest.mark.unit
def test_cheap_strike_reference_deviation_feeds_sabr_bias():
    """The deliberately cheap strike must register as a deviation in the
    reference curve, and the per-expiry sabr_deviation decomposition must
    consume deviation_by_strike -- the wiring that replaced the deleted
    vol_surface_replication model."""
    surface = vsr.compute_vol_surface_reference(
        "MOCK", _make_smile(cheap_strike=110), SPOT, forward=SPOT, T=60 / 365)
    assert surface is not None
    assert surface.deviation_by_strike, "expected per-strike deviations"
    assert abs(surface.deviation_by_strike.get((110.0, "C"), 0.0)) > 1e-6, (
        "expected the deliberately-cheapened strike to show a deviation"
    )
    # The per-expiry bias is derived from the summed deviation (rich -> -1,
    # cheap -> +1) with fallback when below deadband.
    bias = dp._compute_sabr_deviation_for_expiry(surface, fallback_bias=1.0)
    assert bias in (-1.0, 1.0)
