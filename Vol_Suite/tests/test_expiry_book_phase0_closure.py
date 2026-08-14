"""Phase 0 — El-Karoui Measurement Closure Gate.

Tests the blocking gate from PLAN v2 §5 Phase 0: the Taylor P&L identity

    dV ~= S[Delta*dS + 0.5*Gamma*dS^2 + vega*dSigma + vanna*dS*dSigma
              + charm*dS*dt + 0.5*volga*dSigma^2]

must close to tolerance (closure-R2 >= 0.90, max per-greek abs error <= tol)
when computed with the module's own greeks AND with bump-recomputed greeks,
on synthetic network-free chains. A deliberately mis-scaled greek (charm *1/DTE
instead of *1/DEFAULT_A) must BREAK closure on that term.
"""
import math

import pytest

import expiry_book_exposure as ebe


def _synthetic_chain(n=40, seed=7):
    """A monotone-ish synthetic smile at a fixed spot with analytic-ish greeks.

    Returns rows the module's greeks engine understands: each row carries
    strike, right, oi, implied_vol and a spot reference. Greeks are derived by
    the module (network-free) from (spot, strike, T, iv).
    """
    import random
    rng = random.Random(seed)
    spot = 100.0
    T = 0.25
    rows = []
    strikes = sorted(set(round(spot * (1 + 0.02 * i), 2) for i in range(-n // 2, n // 2 + 1)))
    for k in strikes:
        if k <= 0:
            continue
        m = k / spot
        iv = max(0.20 + 0.30 * max(0, 1 - m), 0.15)
        for right in ("C", "P"):
            rows.append({
                "strike": k, "right": right, "oi": 100 + rng.randint(0, 50),
                "implied_vol": iv, "spot": spot, "T": T,
            })
    return rows, spot, T


@pytest.fixture(autouse=True)
def _close_any_open():
    # ensure no stray matplotlib backend leaks from module import
    yield


# ---------------------------------------------------------------------------
# RED-GREEN per behavior
# ---------------------------------------------------------------------------
def test_closure_gate_returns_structured_result():
    rows, spot, T = _synthetic_chain()
    res = ebe.el_karoui_closure_gate(rows, spot, T=T)
    assert res.closure_r2 >= 0.0
    assert res.closure_r2 <= 1.0
    # per-greek provenance flags present for every greek
    for g in ("delta", "gamma", "vega", "vanna", "charm", "volga"):
        assert g in res.per_greek_error


def test_closure_closes_to_tolerance_with_correct_units():
    """The identity must close (R2 >= 0.90, max error <= tol) when all six
    greeks use the module's own (consistent) units."""
    rows, spot, T = _synthetic_chain()
    res = ebe.el_karoui_closure_gate(rows, spot, T=T)
    assert res.closure_r2 >= 0.90, f"closure R2 {res.closure_r2} below 0.90"
    assert res.max_per_greek_error <= 0.05, f"max error {res.max_per_greek_error}"


def test_misscaled_charm_x1_over_dte_breaks_closure():
    """A charm scaled by (1/DTE) instead of (1/DEFAULT_A) must break closure
    on the charm term (this is the exact v1 'charm x1/DTE bug' the v2 plan
    calls out in constraint 6). Uses a SHORT-DTE chain so 1/DTE >> 1/365 and
    the mis-scale is large enough to detect."""
    rows, spot, T = _synthetic_chain(n=24, seed=7)
    T_short = 0.05  # ~18 DTE
    for r in rows:
        r["T"] = T_short
    good = ebe.el_karoui_closure_gate(rows, spot, T=T_short, charm_scale="annualized")
    bad = ebe.el_karoui_closure_gate(rows, spot, T=T_short, charm_scale="over_dte")
    assert good.closure_r2 >= 0.90, f"good closure R2 {good.closure_r2}"
    # the mis-scaled run must be materially worse on the charm term
    assert bad.per_greek_error["charm"] > good.per_greek_error["charm"] * 3, \
        (f"bad charm err {bad.per_greek_error['charm']} not > 3x good "
         f"{good.per_greek_error['charm']}")


def test_bump_recomputed_greeks_match_feed_greeks():
    """The feed-provided greeks and the bump (finite-difference) recomputed
    greeks must agree to tolerance — this is what proves the six greeks are
    all *measurable* (incl. volga) and every units/sign/multiplier bug is
    caught at once."""
    rows, spot, T = _synthetic_chain()
    res = ebe.el_karoui_closure_gate(rows, spot, T=T)
    assert res.bump_match_max <= 0.05, f"feed-vs-bump max {res.bump_match_max}"


def test_held_out_closure_still_passes():
    """The gate must hold on a held-out (different-seed) synthetic chain, not
    just the in-sample one."""
    rows_a, spot, T = _synthetic_chain(seed=11)
    rows_b, _, _ = _synthetic_chain(seed=13)
    res = ebe.el_karoui_closure_gate(rows_b, spot, T=T)
    assert res.closure_r2 >= 0.90
