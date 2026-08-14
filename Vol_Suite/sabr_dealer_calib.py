"""sabr_dealer_calib.py -- the DEALER-facing SABR calibration.

This is the calibration the Vol_Suite dealer sign layer uses
(Vol_Suite/vol_surface_reference.fit_sabr_reference): a deliberately
lightweight, equal-weight (in IV space), fixed-beta, coarse-3x3-grid fitter
tuned for reading deep-OTM deviations (rich/cheap) rather than for pricing.

It is intentionally a THIN RE-EXPORT of the existing function so that
nothing in the dealer path is disturbed by the SABR calibration split.
Options_Suite/Backtests pricing uses the separate, market-grade
Options_Suite/sabr_market_calib.fit_sabr_market (vega-weighted, 5x5 grid,
free-beta) -- see its module docstring. Changing the dealer fitter here would
change the canonical sign model; do not.

    from sabr_dealer_calib import fit_sabr_dealer   # == fit_sabr_reference
"""
from __future__ import annotations

from Vol_Suite.vol_surface_reference import fit_sabr_reference  # type: ignore

# Canonical dealer-facing entry point (identical behaviour to the original).
fit_sabr_dealer = fit_sabr_reference


__all__ = ["fit_sabr_dealer", "fit_sabr_reference"]
