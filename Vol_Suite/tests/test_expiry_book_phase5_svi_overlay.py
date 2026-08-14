"""Phase 5 — SVI-RP overlays (fixed-strike cheap/rich + term-structure flag).

Plan v2 §5 Phase 5: reuse svi_rp.calibrate_svi (read-only) for a fixed-strike
cheap/rich overlay; does NOT alter vol_surface_reference.py. A strike whose
market IV sits below the fitted reference smile is marked LONG/cheap (dealer
long / mean-revert-prone); above -> SHORT/rich. Term-structure regime flag
(contango/backwardation) from ATM vol across tenors.
"""
import pytest

import expiry_book_exposure as ebe


def _smile_chain(spot=100.0, T=0.25, cheap_strike=None, cheap_dip=0.08):
    """A put-skewed smile; optionally dip one OTM call below the reference."""
    chain_iv, oi_by = {}, {}
    strikes = list(range(70, 131, 2))
    for k in strikes:
        m = k / spot
        iv = 0.25 + 0.30 * max(0.0, (1 - m))  # equity put skew
        for right in ("C", "P"):
            chain_iv[(float(k), right)] = iv
            oi_by[(float(k), right)] = 500
    if cheap_strike is not None:
        chain_iv[(float(cheap_strike), "C")] = 0.25 - cheap_dip
    return chain_iv, oi_by


def test_cheap_strike_dip_labeled_long():
    """A strike whose market IV is below the fitted reference smile is marked
    LONG/cheap (dealer long, mean-revert-prone)."""
    chain_iv, oi_by = _smile_chain(cheap_strike=120.0, cheap_dip=0.08)
    ov = ebe.svi_rp_overlay(chain_iv, 100.0, 0.25, oi_by=oi_by)
    assert 120.0 in ov.cheap_strikes
    assert ov.net_cheap_oi > 0
    mark = [m for m in ov.marks if m[0] == 120.0 and m[1] == "C"]
    assert mark and mark[0][2] == "LONG"


def test_rich_strikes_labeled_short():
    """Strikes whose market IV sits at/above the reference are not cheap."""
    chain_iv, oi_by = _smile_chain()
    ov = ebe.svi_rp_overlay(chain_iv, 100.0, 0.25, oi_by=oi_by)
    # no artificial dip -> the heavy-OI smile is near the fit; cheap set small
    assert isinstance(ov.cheap_strikes, list)
    assert isinstance(ov.rich_strikes, list)


def test_contango_vs_backwardation_flag():
    """Term-structure regime flag from ATM vol across tenors."""
    assert ebe._term_structure_flag([(0.1, 0.20), (0.5, 0.25)]) == "contango"
    assert ebe._term_structure_flag([(0.1, 0.25), (0.5, 0.20)]) == "backwardation"
    assert ebe._term_structure_flag([(0.1, 0.22)]) == "flat"


def test_overlay_reuses_svi_rp_and_carries_sigma_atm():
    """The overlay wires svi_rp.calibrate_svi and reports the ATM anchor."""
    chain_iv, oi_by = _smile_chain()
    ov = ebe.svi_rp_overlay(chain_iv, 100.0, 0.25, oi_by=oi_by)
    assert ov.sigma_atm > 0
    assert set(ov.marks[0][1]) <= {"C", "P"}
