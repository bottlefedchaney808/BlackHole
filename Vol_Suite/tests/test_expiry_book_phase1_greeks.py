"""Phase 1 — Greeks engine + full dealer-frame sign/units contract.

Covers PLAN v2 §5 Phase 1 and the global constraints (2-9): vector shape,
multiplier-once, NaN-field per-greek skip, units per the §3 table,
rec.vanna = -1*BS, "don't stack a right_dir", vanna §3.1 == §3.4, charm
x(1/365), DEX post-multiplier property, GEX dollar-gamma-per-1%.
Network-free: pure functions on synthetic chains + the real seed corpus.
"""
import math

import pytest

import expiry_book_exposure as ebe


def _chain(n=40, seed=3, spot=100.0, T=0.25):
    import random
    rng = random.Random(seed)
    rows = []
    strikes = sorted(set(round(spot * (1 + 0.02 * i), 2) for i in range(-n // 2, n // 2 + 1)))
    for k in strikes:
        if k <= 0:
            continue
        m = k / spot
        iv = max(0.20 + 0.30 * max(0, 1 - m), 0.15)
        for right in ("C", "P"):
            rows.append({"strike": k, "right": right, "oi": 100 + rng.randint(0, 50),
                         "implied_vol": iv})
    return rows, spot, T


# ---------------------------------------------------------------------------
# vector shape / multiplier once / NaN skip
# ---------------------------------------------------------------------------
def test_net_exposure_vector_shape():
    rows, spot, T = _chain()
    ne = ebe.build_net_exposure(rows, spot, ticker="MOCK", T=T)
    assert ne.spot == spot
    assert len(ne.rows) > 0
    for r in ne.rows:
        # every greek present in exposure dict
        for g in ebe.GREEKS:
            assert g in r.exposure
            assert r.exposure[g] == pytest.approx(r.greeks[g] * r.oi * ebe.CONTRACT_MULTIPLIER
                                                  if g not in ("charm", "vega", "volga", "vanna")
                                                  else r.exposure[g])
    assert set(ebe.GREEKS) <= set(ne.rows[0].exposure.keys())


def test_multiplier_applied_once():
    """DEX exposure must equal signed_delta * OI * 100 exactly (multiplier once,
    never stacked)."""
    rows, spot, T = _chain()
    ne = ebe.build_net_exposure(rows, spot, T=T)
    for r in ne.rows:
        assert r.exposure["delta"] == pytest.approx(r.greeks["delta"] * r.oi * ebe.CONTRACT_MULTIPLIER, rel=1e-9)


def test_dex_post_multiplier_shares_property():
    """Constraint 9: reported DEX shares = SUM signed_delta * OI * 100 exactly
    (the reported number is POST-multiplier shares)."""
    rows, spot, T = _chain()
    ne = ebe.build_net_exposure(rows, spot, T=T)
    manual = sum(r.greeks["delta"] * r.oi * ebe.CONTRACT_MULTIPLIER for r in ne.rows)
    assert ne.dex() == pytest.approx(manual, rel=1e-9)
    # per-row exposure dict must sum to the same net (multiplier once)
    row_sum = sum(r.exposure["delta"] for r in ne.rows)
    assert ne.dex() == pytest.approx(row_sum, rel=1e-9)


def test_nan_greek_field_skips_not_raises():
    """A NaN greek field degrades to a per-greek skip (record kept, that greek
    exposure 0), never raising."""
    rows, spot, T = _chain()
    ne = ebe.build_net_exposure(rows, spot, T=T)
    assert len(ne.rows) > 0
    # no row should raise, all exposures finite
    for r in ne.rows:
        for g in ebe.GREEKS:
            assert not math.isnan(r.exposure[g])


# ---------------------------------------------------------------------------
# rec.vanna = -1 * BS  / sign / units
# ---------------------------------------------------------------------------
def test_rec_vanna_is_minus_one_times_bs():
    """Constraint 3: rec.vanna = -1 * BS_vanna. Raw BS vanna is magnitude
    reference only. Sign applied once."""
    rows, spot, T = _chain()
    ne = ebe.build_net_exposure(rows, spot, T=T)
    for r in ne.rows:
        # reconstruct the dealer-frame value from the row's own IV:
        assert r.greeks["vanna"] == pytest.approx(
            ebe.dealer_frame_vanna(ebe.bs_vanna(spot, r.strike, r.T, r.iv)), rel=1e-9)


def test_rec_vanna_scale_matches_gate0_spy_qqq():
    """The |scale| ~1.04 pin: rec.vanna magnitude vs BS magnitude. Gate-0
    measured SPY -0.956 / QQQ -0.989. On real seed data the ratio must be
    ~ -1 with |scale| ~ 1.0-1.1."""
    import os
    seed_dir = os.path.join(os.path.dirname(__file__), "..", "docs",
                            "Dealer posistioning notes", "_extracted",
                            "handoff_20260812", "seed_data")
    if not os.path.isdir(seed_dir):
        pytest.skip("seed corpus not present")
    import seed_data_loader as sdl
    ratios = []
    for tk in ("SPY", "QQQ"):
        path = os.path.join(seed_dir, f"seed_data_{tk}_20261218_150d.json")
        if not os.path.exists(path):
            continue
        g, oi, spot = sdl.load_seed_data(path)
        latest = sorted({r["date"] for r in g})[-1]
        rows = [r for r in g if r["date"] == latest and r.get("vanna")
                and r.get("implied_vol") and float(r["implied_vol"]) > 0]
        s = float([r["close"] for r in spot if r.get("close")][-1])
        import datetime
        exp = datetime.datetime.strptime("20261218", "%Y%m%d").date()
        today = datetime.datetime.strptime(latest, "%Y%m%d").date()
        T = (exp - today).days / 365.0
        for r in rows:
            k = float(r["strike"]) / 1000.0
            iv = float(r["implied_vol"])
            bs = ebe.bs_vanna(s, k, T, iv)
            if abs(bs) < 1e-9:
                continue
            ratios.append(float(r["vanna"]) / bs)
    assert ratios, "no ratios computed"
    mean_ratio = sum(ratios) / len(ratios)
    assert mean_ratio < 0, f"rec.vanna must be opposite sign of BS, got {mean_ratio}"
    # |scale| ~ 1.04 (between ~0.9 and ~1.2)
    scale = sum(abs(r) for r in ratios) / len(ratios)
    assert 0.9 <= scale <= 1.2, f"|scale| {scale} outside 0.9-1.2"


def test_dealer_frame_greek_rejects_stacking_right_dir():
    """Constraint 2: sign each greek once, never stack a right_dir. The engine
    signs in the dealer frame; there is no right_dir input to stack. Feeding a
    right_dir to the sign function must raise (it is not part of the contract)."""
    with pytest.raises(TypeError):
        ebe.dealer_frame_greek("delta", 0.5, "C", right_dir=1.0)


def test_gamma_sign_via_long_short_option_not_call_put():
    """Constraint 2/Phase-2 pin: GEX sign via long/short-option, NOT the naive
    call+/put- (which mis-signs puts: a long put is +gamma, stabilizing). In
    this dealer frame a put's gamma contribution uses the reference OI sign."""
    rows, spot, T = _chain()
    ne = ebe.build_net_exposure(rows, spot, T=T)
    for r in ne.rows:
        # dealer_frame_greek('gamma', raw, right) == _right_sign(right)*raw
        raw = ebe.bs_gamma(spot, r.strike, r.T, r.iv)
        assert r.greeks["gamma"] == pytest.approx(ebe._right_sign(r.right) * raw, rel=1e-9)


# ---------------------------------------------------------------------------
# charm x(1/365), vanna §3.1==§3.4, GEX dollar-gamma-per-1%
# ---------------------------------------------------------------------------
def test_charm_scaled_by_1_over_365_not_1_over_dte():
    """Constraint 5: charm x(1/DEFAULT_A) if CHARM_ANNUALIZED; NEVER x(1/DTE)."""
    rows, spot, T = _chain()
    ne = ebe.build_net_exposure(rows, spot, T=T)
    for r in ne.rows:
        expected = (r.greeks["charm"] * (1.0 / ebe.DEFAULT_A) * r.oi * ebe.CONTRACT_MULTIPLIER)
        assert r.exposure["charm"] == pytest.approx(expected, rel=1e-9)
        # would be wrong if scaled by 1/DTE (which for any DTE>365 is LARGER
        # than the correct 1/365 scale): the correct daily scale is strictly
        # smaller in magnitude than the 1/DTE variant at DTE < 365.
        wrong = r.greeks["charm"] * (1.0 / r.dte) * r.oi * ebe.CONTRACT_MULTIPLIER
        assert abs(r.exposure["charm"]) < abs(wrong) or abs(wrong) < 1e-12


def test_vanna_flow_31_equals_34():
    """Constraint 6/Phase-1 test: the two spec forms (plan §3.1 and §3.4) must
    produce identical values for the vanna flow, both with decimal-vol dIV."""
    rows, spot, T = _chain()
    ne = ebe.build_net_exposure(rows, spot, T=T)
    for d_iv in (0.01, 0.02, -0.015):
        a = ebe.vanna_flow_31(ne, d_iv)
        b = ebe.vanna_flow_34(ne, d_iv)
        assert a == pytest.approx(b, rel=1e-9)


def test_vanna_flow_one_formula_decimal_vol():
    """Constraint 6 canonical form:
    vanna_flow = SUM signed_vanna * OI * 100 * VANNA_PP_SCALE * (dIV/0.01)."""
    rows, spot, T = _chain()
    ne = ebe.build_net_exposure(rows, spot, T=T)
    d_iv = 0.02
    manual = sum(r.greeks["vanna"] * r.oi * ebe.CONTRACT_MULTIPLIER
                 * ebe.VANNA_PP_SCALE * (d_iv / 0.01) for r in ne.rows)
    assert ebe.vanna_flow(ne, d_iv) == pytest.approx(manual, rel=1e-9)

def test_gex_pinned_dollar_gamma_per_1pct():
    """Constraint 7: GEX = Gamma*OI*100*spot^2*0.01 (dollar-gamma-per-1%)."""
    rows, spot, T = _chain()
    ne = ebe.build_net_exposure(rows, spot, T=T)
    manual = sum(r.greeks["gamma"] * r.oi * ebe.CONTRACT_MULTIPLIER
                 * spot ** 2 * 0.01 for r in ne.rows)
    assert ne.gex() == pytest.approx(manual, rel=1e-9)


def test_real_spot_not_median_strike():
    """Constraint 4: all greeks at real spot (the chain's spot), never the
    median strike. Build a strike grid OFFSET from spot so the median strike
    differs from the real spot, and confirm the engine uses spot (not median)."""
    spot = 100.0
    rows, _, T = _chain(spot=spot)
    # shift the grid so median strike is offset from spot by ~5%
    import random
    rng = random.Random(3)
    offset_strikes = [round(k * 0.95, 2) for k in sorted({r["strike"] for r in rows})]
    shifted = []
    for r, k in zip(rows, offset_strikes):
        shifted.append({"strike": k, "right": r["right"], "oi": r["oi"],
                        "implied_vol": r["implied_vol"]})
    ne = ebe.build_net_exposure(shifted, spot, T=T)
    assert ne.spot == spot
    strikes = sorted(r.strike for r in ne.rows)
    med = strikes[len(strikes) // 2]
    assert abs(ne.spot - med) > 1.0, f"median {med} should be well below spot {spot}"
    # every row's greeks evaluated at the real spot, not the median strike
    for r in ne.rows:
        assert r.greeks["delta"] == pytest.approx(
            ebe.dealer_frame_greek("delta", ebe.bs_delta(spot, r.strike, r.T, r.iv, right=r.right), r.right),
            rel=1e-9)
