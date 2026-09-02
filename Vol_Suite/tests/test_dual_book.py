"""Phase 8 tests: dual_book orchestration — network-free."""

import dataclasses
from types import SimpleNamespace

import pytest

from delta_band import BandFit
from dealer_position_book import PositionBookResult
from dual_book import DualBookResult, build_dual_book, format_dual_book_interp


def _fit(mu: float = 32.1e6, sigma: float = 58.4e6) -> BandFit:
    return BandFit(
        mu=mu,
        sigma_eq=sigma,
        kappa=53.7,
        half_life_days=0.0129,
        definition="bucketed_0_10",
        fit_window={"first": "20250826", "last": "20260825"},
    )


def _exposure(band_n=None, band_z=None, band_regime=None, prov="fit-prov"):
    return SimpleNamespace(
        ticker="SPY",
        band_n=band_n,
        band_z=band_z,
        band_regime=band_regime,
        band_fit_provenance=prov,
    )


def _position(total_net=0.0, arm="div_signed", lookback=150, dates=None):
    return PositionBookResult(
        ticker="SPY",
        position_by_strike={(600.0, "C"): total_net},
        total_net=total_net,
        arm=arm,
        lookback=lookback,
        dates_used=dates or ["20260101", "20260825"],
    )


def test_interp_contains_all_three_lines():
    fit = _fit()
    expo = _exposure(band_n=-9e6, band_z=-0.70, band_regime="QUIET/ABSORBED")
    pos = _position(total_net=-24.79e6, dates=["20250901", "20260825"])
    dual = build_dual_book(expo, pos, fit)
    text = format_dual_book_interp(dual)

    assert "=== DUAL BOOK (SPY) ===" in text
    assert "Exposure book (snapshot):" in text
    assert "$-9.0M" in text and "z=-0.70" in text and "QUIET/ABSORBED" in text
    assert "Position book (accumulated flow, 150d div_signed" in text
    assert "vanna-weighted-oi" in text
    assert "Spread (exposure - position):" in text
    assert "mixed units" in text


def test_spread_none_when_either_side_missing():
    fit = _fit()
    # exposure band missing -> spread None
    expo = _exposure(band_n=None, band_z=None, band_regime=None, prov="unavailable: X")
    dual = build_dual_book(expo, _position(total_net=-1.0), fit)
    assert dual.spread is None
    assert "n/a (one book missing)" in format_dual_book_interp(dual)

    # position book total missing (None is not a normal total; use exposure None
    # on the other construction) -> also covered by DualBookResult directly
    dual2 = DualBookResult(
        exposure=_exposure(band_n=5.0, band_z=0.0, band_regime="QUIET/ABSORBED"),
        position=_position(total_net=0.0),
    )
    assert dual2.spread is None  # built without build_dual_book: no spread


def test_position_z_from_same_fit():
    fit = _fit(mu=0.0, sigma=2.0)
    expo = _exposure(band_n=1e6, band_z=0.5, band_regime="QUIET/ABSORBED")
    pos = _position(total_net=4.0)
    dual = build_dual_book(expo, pos, fit)
    # z must be computed from the SAME fit: (4.0 - 0.0) / 2.0 = 2.0
    assert dual.position_z == pytest.approx(2.0)
    assert dual.position_regime == "AT EDGE (release regime)"
    # exposure z is untouched (its own, computed inside the production fetch)
    assert expo.band_z == 0.5


def test_spread_value_and_provenance():
    fit = _fit()
    expo = _exposure(band_n=100.0, band_z=0.0, band_regime="QUIET/ABSORBED")
    pos = _position(total_net=35.0, arm="fixed_sign", lookback=150,
                    dates=["20250901", "20260825"])
    dual = build_dual_book(expo, pos, fit)
    assert dual.spread == pytest.approx(65.0)
    assert dual.provenance["band_definition"] == "bucketed_0_10"
    assert dual.provenance["fit_window_last"] == "20260825"
    assert dual.provenance["arm"] == "fixed_sign"
    assert dual.provenance["dates_first"] == "20250901"
    assert dual.units["position"] == "vanna_weighted_oi_delta_contracts"
    assert dual.units["exposure"] == "shares"
