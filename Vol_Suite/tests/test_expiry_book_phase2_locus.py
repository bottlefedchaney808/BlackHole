"""Phase 2 — Execution-locus futures-flow map.

Plan v2 §5 Phase 2: collapse the per-strike vector to the zero-gamma level,
nearest call/put walls (concentrated OI), and the delta-hedge tolerance band
around spot. Model flow firing in threshold-gated bursts (not continuity) when
accumulated delta breaches the tolerance band at the zero-gamma/wall, sized by
local GEX slope. Output granularity = levels, not strikes. Carries residual
delta.
"""
import math

import pytest

import expiry_book_exposure as ebe


def _canned_chain(spot=100.0, T=0.25, wall_call=116.0, wall_put=84.0, n=60):
    """Canned chain with deliberate OI concentration at two wall strikes and a
    clean zero-gamma near the spot.

    OI is skewed so cumulative dollar-gamma is NEGATIVE below spot (heavy
    OTM put OI) and POSITIVE above spot (heavy OTM call OI), guaranteeing a
    zero-gamma crossing near the ATM. Wall strikes are put ON the 2% grid.
    """
    strikes = sorted(set(round(spot * (1 + 0.02 * i), 2) for i in range(-n // 2, n // 2 + 1)))
    rows = []
    for k in strikes:
        if k <= 0:
            continue
        m = k / spot
        iv = max(0.20 + 0.25 * max(0, 1 - m), 0.15)
        for right in ("C", "P"):
            if k < spot:
                # below spot: heavy puts (negative gamma contribution)
                oi = 1000 if right == "P" else 10
            else:
                # at/above spot: heavy calls (positive gamma contribution)
                oi = 1000 if right == "C" else 10
            if right == "C" and abs(k - wall_call) <= 1.0:
                oi = 50000
            if right == "P" and abs(k - wall_put) <= 1.0:
                oi = 50000
            rows.append({"strike": k, "right": right, "oi": oi, "implied_vol": iv})
    return rows, spot, T


# ---------------------------------------------------------------------------
# RED-GREEN behaviors
# ---------------------------------------------------------------------------
def test_execution_locus_returns_levels_not_strikes():
    rows, spot, T = _canned_chain()
    locus = ebe.execution_locus(rows, spot, T=T)
    # zero-gamma, walls, tolerance band, residual delta are single scalar levels
    assert isinstance(locus.zero_gamma, float)
    assert isinstance(locus.call_wall, float)
    assert isinstance(locus.put_wall, float)
    assert locus.band_lower < locus.zero_gamma < locus.band_upper
    assert locus.tolerance_pct > 0
    # no per-strike array required; output granularity is levels
    assert not hasattr(locus, "by_strike") or locus.by_strike is None


def test_zero_gamma_level_detected():
    rows, spot, T = _canned_chain()
    locus = ebe.execution_locus(rows, spot, T=T)
    assert spot * 0.8 <= locus.zero_gamma <= spot * 1.2


def test_call_put_walls_detected_at_concentrated_oi():
    rows, spot, T = _canned_chain(wall_call=116.0, wall_put=84.0)
    locus = ebe.execution_locus(rows, spot, T=T)
    assert abs(locus.call_wall - 116.0) <= 2.0
    assert abs(locus.put_wall - 84.0) <= 2.0


def test_delta_hedge_tolerance_band_around_spot():
    rows, spot, T = _canned_chain()
    locus = ebe.execution_locus(rows, spot, T=T)
    # band straddles spot, width ~ 2 * tolerance_pct
    assert locus.band_lower <= spot <= locus.band_upper
    assert locus.band_upper - locus.band_lower == pytest.approx(
        2 * spot * locus.tolerance_pct, rel=0.3)


def test_flow_fires_in_threshold_gated_bursts():
    """Flow fires in bursts when accumulated delta breaches the tolerance band
    (not continuously). Inside the band no flow; breaching the band triggers a
    burst sized by local GEX slope."""
    rows, spot, T = _canned_chain()
    locus = ebe.execution_locus(rows, spot, T=T)
    # inside the band -> no burst
    inside = ebe.hedge_flow_at(locus, spot)
    assert inside == 0.0
    # breach the upper band -> burst (nonzero) sized by local GEX slope
    breach = ebe.hedge_flow_at(locus, locus.band_upper * 1.02)
    assert breach != 0.0
    # burst sign flips with side (buy vs sell)
    below = ebe.hedge_flow_at(locus, locus.band_lower * 0.98)
    assert below != 0.0
    assert (breach > 0) != (below > 0)


def test_burst_sized_by_local_gex_slope():
    """A steeper local GEX slope -> a larger burst magnitude."""
    rows_a, spot, T = _canned_chain()
    # crank up OI near the call wall to steepen GEX slope
    rows_b = []
    for r in rows_a:
        r2 = dict(r)
        if abs(r2["strike"] - 116.0) <= 1.0:
            r2["oi"] = r2["oi"] * 20
        rows_b.append(r2)
    locus_a = ebe.execution_locus(rows_a, spot, T=T)
    locus_b = ebe.execution_locus(rows_b, spot, T=T)
    fa = abs(ebe.hedge_flow_at(locus_a, locus_a.band_upper * 1.02))
    fb = abs(ebe.hedge_flow_at(locus_b, locus_b.band_upper * 1.02))
    assert fb > fa


def test_residual_delta_carried():
    rows, spot, T = _canned_chain()
    locus = ebe.execution_locus(rows, spot, T=T)
    assert isinstance(locus.residual_delta, float)
    assert math.isfinite(locus.residual_delta)
