"""Phase 4 — Structural / regime arm (co-primary "better seed").

Plan v2 §5 Phase 4: a term-structure-weighted, multi-expiry persistent-carry
object that sets the direction of the carry — the improved replacement for
the old single-anchor replication seed. FRESH construction, NO dependency on
the live RP seed. Event-clocked (OpEx crescendo as an accumulated channel).
"""
import pytest

import expiry_book_exposure as ebe


def _book(T, dte, sigma_atm, seed_gamma_sign=-1.0, spot=100.0):
    """A single-expiry book: put-heavy (negative dealer gamma) or call-heavy."""
    strikes = sorted(set(round(spot * (1 + 0.02 * i), 2) for i in range(-20, 21)))
    rows = []
    for k in strikes:
        if k <= 0:
            continue
        for right in ("C", "P"):
            if seed_gamma_sign < 0:
                oi = 1000 if right == "P" else 10
            else:
                oi = 1000 if right == "C" else 10
            rows.append({"strike": k, "right": right, "oi": oi,
                         "implied_vol": sigma_atm})
    return {"expiry": f"2026{T * 1000:.0f}", "spot": spot, "rows": rows,
            "T": T, "dte": dte, "sigma_atm": sigma_atm}


def test_multi_expiry_regime_construction():
    """Two short-gamma expiries -> persistent-short-gamma regime with a
    term-structure flag (contango: far ATM IV > near ATM IV)."""
    books = [_book(0.1, 20, 0.20, -1.0), _book(0.5, 120, 0.24, -1.0)]
    r = ebe.build_structural_regime(books)
    assert r.regime == "persistent-short-gamma"
    assert r.carry < 0
    assert len(r.per_expiry_gamma) == 2
    assert r.per_expiry_carry  # both expiries carry a direction
    assert r.term_structure_flag == "contango"


def test_persistent_regime_does_not_single_day_revert():
    """A persistent regime holds its sign when reconstructed — it does not
    single-day-revert."""
    books = [_book(0.1, 20, 0.20, -1.0), _book(0.5, 120, 0.24, -1.0)]
    r1 = ebe.build_structural_regime(books)
    r2 = ebe.build_structural_regime(books)
    assert r1.regime == r2.regime
    assert r1.carry < 0 and r2.carry < 0
    assert r1.persistence >= 0.5


def test_long_gamma_regime_sign():
    """A call-heavy book produces persistent-long-gamma (positive carry)."""
    books = [_book(0.1, 20, 0.20, +1.0), _book(0.5, 120, 0.24, +1.0)]
    r = ebe.build_structural_regime(books)
    assert r.regime == "persistent-long-gamma"
    assert r.carry > 0


def test_event_clock_gates_near_opex_crescendo():
    """The OpEx crescendo (accumulated charm channel) activates only for books
    within the event window of expiry."""
    near = [_book(0.01, 3, 0.20, -1.0), _book(0.5, 120, 0.24, -1.0)]
    r = ebe.build_structural_regime(near, opex_window_days=5)
    assert r.event_clock["opex_crescendo"] is True
    far = [_book(0.5, 120, 0.24, -1.0)]
    r2 = ebe.build_structural_regime(far, opex_window_days=5)
    assert r2.event_clock["opex_crescendo"] is False


def test_backwardation_flag():
    """Falling ATM vol across tenor -> backwardation."""
    books = [_book(0.1, 20, 0.24, -1.0), _book(0.5, 120, 0.20, -1.0)]
    r = ebe.build_structural_regime(books)
    assert r.term_structure_flag == "backwardation"
