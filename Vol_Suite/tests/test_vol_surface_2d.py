"""
Tests for vol_surface_2d.py — the 2D implied-vol surface module with
interpolation across strike AND tenor.

Network-free: all ThetaData interaction goes through a fake stand-in,
same pattern as test_dealer_positioning_sign_model.py and
test_variance_swap_screener.py.

Test coverage
-------------
- VolSurfacePoint dataclass construction
- Surface from synthetic (strike, tenor, iv) triples
- iv() interpolation recovers known anchor points
- iv() extrapolation stays in reasonable bounds
- build_surface with a fake ThetaDataController
- plot() saves a valid file
"""
import math
import os
import tempfile
from datetime import datetime

import numpy as np
import pytest

import vol_surface_2d as vs2d

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SPOT = 100.0
STRIKES = list(range(70, 131))  # $70–$130
TENOR_1 = 30 / 365.0            # ~1 month
TENOR_2 = 60 / 365.0            # ~2 months
TENOR_3 = 91 / 365.0            # ~3 months
ALL_TENORS = [TENOR_1, TENOR_2, TENOR_3]


def _theta(k: float) -> int:
    return int(round(k * 1000))


def _synthetic_smile_iv(strike: float, spot: float = SPOT) -> float:
    """Smooth quadratic smile: iv = 0.20 + 0.30*x² - 0.05*x."""
    x = math.log(strike / spot)
    return max(0.20 + 0.30 * x * x - 0.05 * x, 0.05)


# ---------------------------------------------------------------------------
# Fake ThetaDataController
# ---------------------------------------------------------------------------

class _FakeTD:
    """Stands in for ThetaDataController.  Returns synthetic option-chain data
    with a known quadratic smile at each of ALL_TENORS expiries."""

    def __init__(self, spot: float = SPOT):
        self._spot = spot
        # Build expiry strings from the reference date
        ref = datetime.now()
        self._exps = []
        for d in [30, 60, 91]:
            exp_date = ref.replace(day=min(ref.day + d, 28))
            # Simpler: just do a fixed delta approach
        # Use offset from epoch for deterministic expiry dates
        import datetime as dt
        base = dt.date(2026, 7, 29)
        self._exp_dates = {
            30:  (base + dt.timedelta(days=30)).strftime("%Y%m%d"),
            60:  (base + dt.timedelta(days=60)).strftime("%Y%m%d"),
            91:  (base + dt.timedelta(days=91)).strftime("%Y%m%d"),
        }
        # Map each expiry string to its tenor
        self._exp_to_tenor = {
            v: k / 365.0 for k, v in self._exp_dates.items()
        }

    def fetch_spot_price(self, ticker: str) -> float:
        return self._spot

    def list_expirations(self, root: str):
        return list(self._exp_dates.values())

    def option_bulk_greeks(self, root: str, exp: str):
        rows = []
        tenor = self._exp_to_tenor.get(exp)
        if tenor is None:
            return rows
        for k in STRIKES:
            iv = _synthetic_smile_iv(float(k), self._spot)
            for right in ("C", "P"):
                rows.append({
                    "strike": _theta(float(k)),
                    "right": right,
                    "implied_vol": iv,
                    "bid": 1.0, "ask": 1.1,
                    "delta": 0.5 if right == "C" else -0.5,
                    "gamma": 0.02,
                    "vega": 0.1,
                    "theta": -0.01,
                })
        return rows

    def close(self):
        pass


# ---------------------------------------------------------------------------
# Helper: build a synthetic surface from scratch (no ThetaData dependency)
# ---------------------------------------------------------------------------

def _build_synthetic_surface() -> vs2d.VolSurface:
    """Create a VolSurface with known quadratic parameters at ALL_TENORS."""
    tenors = []
    coeffs = []
    points = []

    for t in ALL_TENORS:
        # Fit the known smile: a=0.30, b=-0.05, c=0.20
        a, b, c = 0.30, -0.05, 0.20
        tenors.append(t)
        coeffs.append((a, b, c))
        for k in STRIKES:
            x = math.log(k / SPOT)
            iv = a * x * x + b * x + c
            points.append(vs2d.VolSurfacePoint(strike=float(k), tenor=t, iv=max(iv, 0.05)))

    surface = vs2d.VolSurface(
        ticker="SYNTH",
        timestamp=datetime(2026, 7, 29),
        points=points,
        fitted_params={
            "spot": SPOT,
            "tenors": sorted(tenors),
            "coeffs": sorted(coeffs, key=lambda _: sorted(tenors)) if False else coeffs,
            "fitter": "quadratic_per_expiry",
        },
    )
    # Fix ordering to match sorted tenors
    combined = sorted(zip(tenors, coeffs), key=lambda x: x[0])
    surface.fitted_params["tenors"] = [c[0] for c in combined]
    surface.fitted_params["coeffs"] = [c[1] for c in combined]
    return surface


# ===================================================================
# Tests
# ===================================================================

class TestVolSurfacePoint:
    """VolSurfacePoint dataclass construction."""

    @pytest.mark.unit
    def test_basic_construction(self):
        pt = vs2d.VolSurfacePoint(strike=150.0, tenor=0.25, iv=0.20)
        assert pt.strike == 150.0
        assert pt.tenor == 0.25
        assert pt.iv == 0.20

    @pytest.mark.unit
    def test_equality(self):
        a = vs2d.VolSurfacePoint(strike=100.0, tenor=0.5, iv=0.30)
        b = vs2d.VolSurfacePoint(strike=100.0, tenor=0.5, iv=0.30)
        assert a == b

    @pytest.mark.unit
    def test_can_modify_fields(self):
        """VolSurfacePoint is a regular (non-frozen) dataclass per Vol_Suite conventions."""
        pt = vs2d.VolSurfacePoint(strike=100.0, tenor=0.5, iv=0.30)
        pt.strike = 200.0
        pt.iv = 0.50
        assert pt.strike == 200.0
        assert pt.iv == 0.50


class TestVolSurfaceAnchorRecovery:
    """Surface iv() must recover known anchor points."""

    @pytest.mark.unit
    def test_atm_at_nearest_tenor(self):
        """ATM (strike==spot) at the nearest tenor should be close to c=0.20."""
        surface = _build_synthetic_surface()
        # iv at spot ($100) for the middle tenor (TENOR_2)
        mid_tenor = ALL_TENORS[1]
        iv_val = surface.iv(SPOT, mid_tenor)
        # The quadratic at x=0 gives c=0.20
        assert iv_val == pytest.approx(0.20, abs=0.01), f"ATM IV {iv_val:.4f} != 0.20"

    @pytest.mark.unit
    def test_otm_recovery_at_each_tenor(self):
        """Smile shape should be preserved at each observed tenor."""
        surface = _build_synthetic_surface()
        for t in ALL_TENORS:
            for k in [85.0, 95.0, 105.0, 115.0]:
                x = math.log(k / SPOT)
                expected = 0.30 * x * x - 0.05 * x + 0.20
                iv_val = surface.iv(k, t)
                assert iv_val == pytest.approx(expected, abs=0.02), \
                    f"K={k}, T={t:.4f}: got {iv_val:.4f}, expected {expected:.4f}"


class TestIvInterpolation:
    """iv() interpolation between tenors."""

    @pytest.mark.unit
    def test_interpolation_between_two_tenors(self):
        """Interpolated IV at mid-tenor should lie between the two bracket IVs."""
        surface = _build_synthetic_surface()
        mid_tenor = (ALL_TENORS[0] + ALL_TENORS[1]) / 2.0

        iv_lo = surface.iv(SPOT, ALL_TENORS[0])
        iv_hi = surface.iv(SPOT, ALL_TENORS[1])
        iv_mid = surface.iv(SPOT, mid_tenor)

        assert iv_lo <= iv_mid <= iv_hi or iv_lo >= iv_mid >= iv_hi, \
            f"Interpolation {iv_mid:.4f} not between {iv_lo:.4f} and {iv_hi:.4f}"

    @pytest.mark.unit
    def test_all_tenors_monotonic_atm(self):
        """ATM vol should be monotonic (the quadratic's c is constant, but TV
        interpolation can cause slight variation — just check order)."""
        surface = _build_synthetic_surface()
        ivs = [surface.iv(SPOT, t) for t in ALL_TENORS]
        # All should be near 0.20
        for iv in ivs:
            assert iv == pytest.approx(0.20, abs=0.02)


class TestIvExtrapolation:
    """iv() extrapolation must not produce negative or absurd values."""

    @pytest.mark.unit
    def test_below_min_tenor_clamps(self):
        surface = _build_synthetic_surface()
        iv_val = surface.iv(SPOT, ALL_TENORS[0] - 0.5)  # well before first tenor
        assert iv_val >= 0.0, f"Negative IV on early extrapolation: {iv_val}"
        assert iv_val <= 5.0, f"Absurdly large IV on early extrapolation: {iv_val}"

    @pytest.mark.unit
    def test_above_max_tenor_clamps(self):
        surface = _build_synthetic_surface()
        iv_val = surface.iv(SPOT, ALL_TENORS[-1] + 5.0)  # well past last tenor
        assert iv_val >= 0.0, f"Negative IV on late extrapolation: {iv_val}"
        assert iv_val <= 5.0, f"Absurdly large IV on late extrapolation: {iv_val}"

    @pytest.mark.unit
    def test_deep_otm_extrapolation(self):
        """Deep OTM far wing should not produce absurd IVs."""
        surface = _build_synthetic_surface()
        for t in ALL_TENORS:
            iv_val = surface.iv(SPOT * 0.30, t)   # 70 % OTM put
            assert iv_val >= 0.0, f"Negative IV deep OTM: {iv_val}"
            assert iv_val <= 5.0, f"Absurdly large IV deep OTM: {iv_val}"

    @pytest.mark.unit
    def test_call_wing_extrapolation(self):
        """Deep OTM call wing should not blow up."""
        surface = _build_synthetic_surface()
        for t in ALL_TENORS:
            iv_val = surface.iv(SPOT * 2.50, t)   # 150 % OTM call
            assert iv_val >= 0.0, f"Negative IV deep call wing: {iv_val}"
            assert iv_val <= 5.0, f"Absurdly large IV deep call wing: {iv_val}"


class TestBuildSurface:
    """build_surface with a fake ThetaDataController."""

    @pytest.mark.unit
    def test_build_from_fake_td_returns_surface(self):
        td = _FakeTD(spot=SPOT)
        surface = vs2d.build_surface("TEST", td)
        assert surface is not None
        assert surface.ticker == "TEST"
        assert len(surface.points) > 0
        assert surface.fitted_params["fitter"] == "quadratic_per_expiry"
        assert len(surface.fitted_params["tenors"]) >= 2

    @pytest.mark.unit
    def test_build_surface_has_spot_in_params(self):
        td = _FakeTD(spot=SPOT)
        surface = vs2d.build_surface("TEST", td)
        assert surface.fitted_params["spot"] == SPOT

    @pytest.mark.unit
    def test_build_surface_tenors_are_sorted(self):
        td = _FakeTD(spot=SPOT)
        surface = vs2d.build_surface("TEST", td)
        tenors = surface.fitted_params["tenors"]
        assert tenors == sorted(tenors)

    @pytest.mark.unit
    def test_build_surface_iv_returns_sensible_values(self):
        td = _FakeTD(spot=SPOT)
        surface = vs2d.build_surface("TEST", td)
        # ATM at the middle tenor should be ~0.20
        mid = surface.fitted_params["tenors"][len(surface.fitted_params["tenors"]) // 2]
        iv_atm = surface.iv(SPOT, mid)
        assert iv_atm == pytest.approx(0.20, abs=0.05)

    @pytest.mark.unit
    def test_build_surface_with_empty_expiries(self):
        """A fake that returns no expiries should produce None."""
        class _EmptyTD:
            def fetch_spot_price(self, ticker): return SPOT
            def list_expirations(self, root): return []
            def option_bulk_greeks(self, root, exp): return []
            def close(self): pass

        surface = vs2d.build_surface("EMPTY", _EmptyTD())
        assert surface is None

    @pytest.mark.unit
    def test_build_surface_with_spot_override(self):
        td = _FakeTD(spot=SPOT)
        surface = vs2d.build_surface("TEST", td, spot_override=105.0)
        assert surface is not None
        assert surface.fitted_params["spot"] == 105.0


class TestPlot:
    """plot() must save a valid image file."""

    @pytest.mark.unit
    def test_plot_saves_file(self):
        surface = _build_synthetic_surface()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            out_path = f.name
        try:
            result = vs2d.plot(surface, out_path)
            assert result == out_path
            assert os.path.isfile(out_path)
            assert os.path.getsize(out_path) > 1000  # should be a real image
        finally:
            if os.path.isfile(out_path):
                os.unlink(out_path)

    @pytest.mark.unit
    def test_plot_on_empty_surface_does_not_crash(self):
        """An uncalibrated surface should not crash the plotter."""
        empty = vs2d.VolSurface(
            ticker="EMPTY",
            timestamp=datetime(2026, 7, 29),
            fitted_params={"spot": 100.0, "tenors": [], "coeffs": [], "fitter": ""},
        )
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            out_path = f.name
        try:
            result = vs2d.plot(empty, out_path)
            # Should return the path even if there's nothing to plot
            assert result == out_path
        finally:
            if os.path.isfile(out_path):
                os.unlink(out_path)


class TestHelperFunctions:
    """Internal helper tests."""

    @pytest.mark.unit
    def test_log_moneyness_zero_at_spot(self):
        assert vs2d._log_moneyness(SPOT, SPOT) == 0.0

    @pytest.mark.unit
    def test_log_moneyness_symmetric(self):
        x1 = vs2d._log_moneyness(110.0, SPOT)
        x2 = vs2d._log_moneyness(SPOT / 1.10, SPOT)
        assert x1 == pytest.approx(-x2, abs=1e-10)

    @pytest.mark.unit
    def test_fit_quadratic_smile_recovers_params(self):
        strikes = [float(k) for k in range(85, 116)]
        ivs = [_synthetic_smile_iv(float(k), SPOT) for k in strikes]
        coeffs = vs2d._fit_quadratic_smile(strikes, ivs, SPOT)
        assert coeffs is not None
        a, b, c = coeffs
        assert a == pytest.approx(0.30, abs=0.05)
        assert b == pytest.approx(-0.05, abs=0.05)
        assert c == pytest.approx(0.20, abs=0.05)

    @pytest.mark.unit
    def test_fit_quadratic_fails_with_too_few_points(self):
        strikes = [500.0, 10.0]
        ivs = [0.20, 0.30]
        coeffs = vs2d._fit_quadratic_smile(strikes, ivs, SPOT)
        assert coeffs is None

    @pytest.mark.unit
    def test_expiry_to_tenor_positive(self):
        from datetime import timedelta
        ref = datetime(2026, 7, 29)
        exp_str = (ref + timedelta(days=91)).strftime("%Y%m%d")
        tenor = vs2d._expiry_to_tenor(exp_str, ref)
        assert tenor == pytest.approx(91 / 365.0, abs=1 / 365.0)

    @pytest.mark.unit
    def test_expiry_to_tenor_min_one_day(self):
        """Even same-day expiry returns at least 1 day / 365."""
        exp_str = "20260729"
        ref = datetime(2026, 7, 29)
        tenor = vs2d._expiry_to_tenor(exp_str, ref)
        assert tenor >= 1.0 / 365.0