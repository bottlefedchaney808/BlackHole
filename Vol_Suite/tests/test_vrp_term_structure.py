"""
Network-free tests for vrp_term_structure.py.

Tests cover:
  - VrpTermPoint and VrpTermStructureResult dataclass construction
  - Shape classification (_classify_term_structure) for all four shapes
  - compute_vrp_term_structure with a FakeTD that simulates multiple expiries
  - plot_vrp_term_structure chart generation with synthetic data
"""

import math
import os
from unittest.mock import patch

import numpy as np
import pytest
import vrp_term_structure as vts

# ---------------------------------------------------------------------------
# Dataclass construction
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_vrp_term_point_default_construction():
    """VrpTermPoint requires all fields; ensure basic construction works."""
    pt = vts.VrpTermPoint(
        expiry_label="1mo",
        expiry_date="20260717",
        T_years=0.0833,
        fair_vol_pct=30.0,
        atm_iv_pct=28.0,
        vrp_pct=2.0,
        rv_30d_pct=25.0,
    )
    assert pt.expiry_label == "1mo"
    assert pt.expiry_date == "20260717"
    assert pt.T_years == pytest.approx(0.0833)
    assert pt.fair_vol_pct == 30.0
    assert pt.atm_iv_pct == 28.0
    assert pt.vrp_pct == 2.0
    assert pt.rv_30d_pct == 25.0


@pytest.mark.unit
def test_vrp_term_point_nan_values():
    """NaN values should be representable (failure / missing-tenor sentinel)."""
    pt = vts.VrpTermPoint(
        expiry_label="6mo",
        expiry_date="",
        T_years=float("nan"),
        fair_vol_pct=float("nan"),
        atm_iv_pct=float("nan"),
        vrp_pct=float("nan"),
        rv_30d_pct=float("nan"),
    )
    assert math.isnan(pt.T_years)
    assert math.isnan(pt.fair_vol_pct)


@pytest.mark.unit
def test_vrp_term_structure_result_construction():
    """VrpTermStructureResult with a list of VrpTermPoints and a shape."""
    pts = [
        vts.VrpTermPoint("1mo", "20260717", 0.0833, 30.0, 28.0, 2.0, 25.0),
        vts.VrpTermPoint("3mo", "20260918", 0.25, 32.0, 29.0, 3.0, 24.0),
    ]
    res = vts.VrpTermStructureResult(
        ticker="SPY",
        timestamp="2026-07-29T12:00:00Z",
        points=pts,
        shape="upward",
        chart_path="/tmp/test_chart.png",
    )
    assert res.ticker == "SPY"
    assert res.shape == "upward"
    assert len(res.points) == 2
    assert res.chart_path == "/tmp/test_chart.png"


@pytest.mark.unit
def test_vrp_term_structure_result_default_chart_path():
    """chart_path should default to None."""
    res = vts.VrpTermStructureResult(
        ticker="AAPL",
        timestamp="now",
        points=[],
        shape="flat",
    )
    assert res.chart_path is None


# ---------------------------------------------------------------------------
# Shape classification
# ---------------------------------------------------------------------------


def _point(vrp: float) -> vts.VrpTermPoint:
    """Helper: build a VrpTermPoint with only vrp_pct varying."""
    return vts.VrpTermPoint(
        expiry_label="x",
        expiry_date="20260717",
        T_years=0.25,
        fair_vol_pct=float("nan"),
        atm_iv_pct=float("nan"),
        vrp_pct=vrp,
        rv_30d_pct=float("nan"),
    )


@pytest.mark.unit
def test_shape_flat_when_fewer_than_3_points():
    """Fewer than 3 valid points should result in 'flat'."""
    pts = [_point(2.0), _point(5.0)]
    assert vts._classify_term_structure(pts) == "flat"


@pytest.mark.unit
def test_shape_flat_when_narrow_range():
    """If max-min VRP < 2.0 pct pts, classify as flat."""
    pts = [_point(1.0), _point(1.5), _point(2.4)]
    # max-min = 1.4 < 2.0
    assert vts._classify_term_structure(pts) == "flat"


@pytest.mark.unit
def test_shape_flat_boundary():
    """Boundary: max-min == 1.99 should be flat."""
    pts = [_point(0.0), _point(1.5), _point(1.99)]
    assert vts._classify_term_structure(pts) == "flat"


@pytest.mark.unit
def test_shape_upward():
    """Monotonically increasing VRP → upward."""
    pts = [_point(-1.0), _point(1.0), _point(3.0), _point(5.0)]
    assert vts._classify_term_structure(pts) == "upward"


@pytest.mark.unit
def test_shape_upward_with_plateau():
    """Non-decreasing (allows equal) → upward."""
    pts = [_point(1.0), _point(2.0), _point(2.0), _point(4.0)]
    assert vts._classify_term_structure(pts) == "upward"


@pytest.mark.unit
def test_shape_downward():
    """Monotonically decreasing VRP → downward."""
    pts = [_point(5.0), _point(3.0), _point(1.0), _point(-1.0)]
    assert vts._classify_term_structure(pts) == "downward"


@pytest.mark.unit
def test_shape_downward_with_plateau():
    """Non-increasing (allows equal) → downward."""
    pts = [_point(5.0), _point(4.0), _point(4.0), _point(2.0)]
    assert vts._classify_term_structure(pts) == "downward"


@pytest.mark.unit
def test_shape_humped():
    """Neither monotonic nor flat → humped."""
    pts = [_point(1.0), _point(4.0), _point(3.0), _point(2.0)]
    assert vts._classify_term_structure(pts) == "humped"


@pytest.mark.unit
def test_shape_humped_inverse():
    """Inverted hump: dip in the middle."""
    pts = [_point(4.0), _point(1.0), _point(2.0), _point(3.0)]
    assert vts._classify_term_structure(pts) == "humped"


@pytest.mark.unit
def test_shape_ignores_nan_points():
    """NaN VRP points are filtered out before classification."""
    pts = [
        _point(float("nan")),
        _point(-1.0),
        _point(1.0),
        _point(float("nan")),
        _point(3.0),
    ]
    assert vts._classify_term_structure(pts) == "upward"


# ---------------------------------------------------------------------------
# compute_vrp_term_structure with FakeTD
# ---------------------------------------------------------------------------


class FakeTD:
    """Stand-in for ThetaDataController that can list multiple expirations."""

    def __init__(self):
        self._expiries = ["20260710", "20260717", "20261016", "20270115", "20270716"]
        self._spot = 100.0
        self._r = 0.05
        self._q = 0.0

    def list_expirations(self, ticker: str):
        return list(self._expiries)

    def list_strikes(self, ticker: str, expiration: str):
        # Return a reasonable strike range given spot~100
        return list(range(50, 160, 5))

    def option_bulk_greeks(self, ticker: str, expiration: str):
        # Build synthetic greeks around a 20% vol level
        spot = self._spot
        Ks = list(range(50, 160, 5))
        rows = []
        for k in Ks:
            # Simple ATM-ish IV: closer to ATM = closer to 0.20, wings higher
            moneyness = abs(k - spot) / spot
            iv = 0.20 + 0.10 * moneyness  # base 20% + skew
            # mid price ~ Black-Scholes approximation for ATM: S * 0.4 * sigma * sqrt(T)
            # We'll use a flat 0.25yr T approximation for simplicity
            T_approx = 0.25
            approx_premium = spot * iv * np.sqrt(T_approx) * 0.4
            mid = max(approx_premium * (1.0 - 0.3 * moneyness), 0.05)
            for right in ("C", "P"):
                rows.append(
                    {
                        "strike": k
                        * 1000,  # milli-dollars (ThetaData integer-scaled strike convention)
                        "right": right,
                        "bid": max(mid * 0.9, 0.01),
                        "ask": mid * 1.1,
                        "implied_vol": iv,
                        "last": mid,
                    }
                )
        return rows

    def fetch_dividend_yield(self, ticker: str):
        return self._q

    def fetch_risk_free_rate(self, T: float):
        return self._r

    def fetch_spot_price(self, ticker: str):
        return self._spot

    def close(self):
        pass


@pytest.mark.unit
def test_compute_vrp_term_structure_with_fake_td():
    """Full integration-style test: compute all four tenors with a fake client."""
    td = FakeTD()

    # Patch fetch_price_history to return something usable
    with patch.object(vts, "fetch_price_history") as mock_fetch:
        # 300 daily price points with roughly 20% annualized vol
        rng = np.random.default_rng(42)
        prices = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, 300)))
        mock_df = type(
            "MockDF",
            (),
            {"__getitem__": lambda self, k: type("MockCol", (), {"values": prices})()},
        )()
        mock_fetch.return_value = mock_df

        result = vts.compute_vrp_term_structure("SPY", td, 100.0, 0.05, 0.0)

    # Basic checks
    assert result.ticker == "SPY"
    assert len(result.points) == len(vts.TARGET_TENORS)  # one point per tenor
    assert result.shape in ("flat", "upward", "downward", "humped")

    # Each point should have valid data (no NaN) since FakeTD works
    for pt in result.points:
        # expiry_label should match one of the TARGET_TENOR labels
        assert pt.expiry_label in dict(vts.TARGET_TENORS)
        assert pt.expiry_date != ""  # should have been resolved
        assert not math.isnan(pt.T_years)
        assert not math.isnan(pt.fair_vol_pct), f"{pt.expiry_label} fair_vol NaN"
        assert not math.isnan(pt.atm_iv_pct), f"{pt.expiry_label} atm_iv NaN"
        assert not math.isnan(pt.vrp_pct), f"{pt.expiry_label} vrp NaN"
        # Fair vol should be positive and reasonable (fits our ~20% skey)
        assert 5.0 < pt.fair_vol_pct < 100.0, (
            f"{pt.expiry_label} fair_vol={pt.fair_vol_pct}"
        )
        # ATM IV also
        assert 5.0 < pt.atm_iv_pct < 100.0, f"{pt.expiry_label} atm_iv={pt.atm_iv_pct}"
        # VRP should be the difference
        assert pt.vrp_pct == pytest.approx(pt.fair_vol_pct - pt.atm_iv_pct)
        # RV should be computed (we gave it 300 data points)
        assert not math.isnan(pt.rv_30d_pct)
        assert 0.0 < pt.rv_30d_pct < 200.0

    # T_years should be larger for longer tenors
    T_values = [pt.T_years for pt in result.points if not math.isnan(pt.T_years)]
    if len(T_values) >= 2:
        assert T_values == sorted(T_values), "T_years should be increasing with tenor"


@pytest.mark.unit
def test_compute_vrp_term_structure_no_price_history():
    """When price history is unavailable, RV should be NaN but VRP still computed."""
    td = FakeTD()

    # Patch fetch_price_history to raise (simulating failure)
    with patch.object(vts, "fetch_price_history", side_effect=RuntimeError("No data")):
        result = vts.compute_vrp_term_structure("SPY", td, 100.0, 0.05, 0.0)

    # All tenors should have NaN RV but valid fair_vol/atm_iv/vrp
    for pt in result.points:
        assert not math.isnan(pt.fair_vol_pct)
        assert not math.isnan(pt.atm_iv_pct)
        assert not math.isnan(pt.vrp_pct)
        assert math.isnan(pt.rv_30d_pct), f"{pt.expiry_label} rv_30d should be NaN"


@pytest.mark.unit
def test_compute_vrp_term_structure_skips_missing_expiry():
    """If a tenor's expiry can't be found, the point is recorded with NaN fields."""
    td = FakeTD()
    # Override option_bulk_greeks to raise for the 6mo and 12mo tenors,
    # simulating that those expiries don't have valid option chains.
    original_greeks = td.option_bulk_greeks
    call_count = [0]

    def failing_greeks(ticker, expiration):
        call_count[0] += 1
        # 1mo and 3mo will be fetched first (shortest tenors first);
        # the 3rd call (6mo) and 4th (12mo) should fail
        if call_count[0] >= 3:
            raise RuntimeError(f"No greeks for {expiration}")
        return original_greeks(ticker, expiration)

    td.option_bulk_greeks = failing_greeks

    with patch.object(vts, "fetch_price_history") as mock_fetch:
        rng = np.random.default_rng(42)
        prices = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, 300)))
        mock_df = type(
            "MockDF",
            (),
            {"__getitem__": lambda self, k: type("MockCol", (), {"values": prices})()},
        )()
        mock_fetch.return_value = mock_df

        result = vts.compute_vrp_term_structure("SPY", td, 100.0, 0.05, 0.0)

    # Some tenors should resolve, others should fail
    nan_count = sum(1 for p in result.points if math.isnan(p.fair_vol_pct))
    ok_count = sum(1 for p in result.points if not math.isnan(p.fair_vol_pct))
    assert ok_count >= 1, "At least one tenor should resolve"
    assert nan_count >= 1, "At least one tenor should fail with failing greeks"
    assert ok_count + nan_count == len(vts.TARGET_TENORS)
    # The first two (1mo, 3mo) should succeed; later ones should fail
    assert not math.isnan(result.points[0].fair_vol_pct), "1mo should succeed"
    assert not math.isnan(result.points[1].fair_vol_pct), "3mo should succeed"


# ---------------------------------------------------------------------------
# plot_vrp_term_structure
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_plot_vrp_term_structure_creates_file(tmp_path):
    """Chart generation should save a non-empty PNG to the given path."""
    pts = [
        vts.VrpTermPoint("1mo", "20260710", 0.0833, 28.0, 26.0, 2.0, 24.0),
        vts.VrpTermPoint("3mo", "20261016", 0.25, 32.0, 29.0, 3.0, 25.0),
        vts.VrpTermPoint("6mo", "20270115", 0.5, 35.0, 31.0, 4.0, 26.0),
        vts.VrpTermPoint("12mo", "20270716", 1.0, 38.0, 33.0, 5.0, 27.0),
    ]
    result = vts.VrpTermStructureResult(
        ticker="SPY",
        timestamp="2026-07-29T12:00:00Z",
        points=pts,
        shape="upward",
    )

    out_path = os.path.join(str(tmp_path), "vrp_ts.png")
    saved = vts.plot_vrp_term_structure(result, out_path)

    assert saved == out_path
    assert os.path.isfile(out_path)
    assert os.path.getsize(out_path) > 1000  # should be a real PNG


@pytest.mark.unit
def test_plot_vrp_term_structure_with_nan(tmp_path):
    """Chart should handle NaN values gracefully (no crash)."""
    pts = [
        vts.VrpTermPoint("1mo", "20260710", 0.0833, 28.0, 26.0, 2.0, 24.0),
        vts.VrpTermPoint(
            "3mo",
            "",
            float("nan"),
            float("nan"),
            float("nan"),
            float("nan"),
            float("nan"),
        ),
        vts.VrpTermPoint("6mo", "20270115", 0.5, 35.0, 31.0, 4.0, float("nan")),
        vts.VrpTermPoint("12mo", "20270716", 1.0, 38.0, 33.0, 5.0, 27.0),
    ]
    result = vts.VrpTermStructureResult(
        ticker="AAPL",
        timestamp="now",
        points=pts,
        shape="humped",
    )

    out_path = os.path.join(str(tmp_path), "vrp_ts_nan.png")
    saved = vts.plot_vrp_term_structure(result, out_path)

    assert saved == out_path
    assert os.path.isfile(out_path)
    assert os.path.getsize(out_path) > 1000


# ---------------------------------------------------------------------------
# TARGET_TENORS — the expected tuple list
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_target_tenors_have_expected_labels():
    """The 4 standard tenors should be 1mo, 3mo, 6mo, 12mo in order."""
    labels = [label for label, _ in vts.TARGET_TENORS]
    assert labels == ["1mo", "3mo", "6mo", "12mo"]


@pytest.mark.unit
def test_target_tenors_increasing_years():
    """Target years should increase with each tenor."""
    years = [ty for _, ty in vts.TARGET_TENORS]
    assert years == sorted(years)
    assert all(y > 0 for y in years)
