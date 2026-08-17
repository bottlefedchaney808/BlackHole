"""Phase 3 — Scenario-conditional hedge-flow budget (THE ACTIONABLE OUTPUT).

Plan v2 §5 Phase 3: for each named scenario (dS=+-1%, dIV=+-1 vol pt,
dt=1 day toward OpEx) report the forced dealer hedge flow = SUM over strikes
(sensitivity x OI x multiplier x scenario shock) as one signed number per
channel per scenario, weighted by the Phase-2 execution-locus activation. DEX
appears ONLY as a carry descriptor, NEVER as a forecast.
"""
import math

import pytest

import expiry_book_exposure as ebe


def _canned_chain(spot=100.0, T=0.25, wall_call=116.0, wall_put=84.0, n=60):
    strikes = sorted(set(round(spot * (1 + 0.02 * i), 2)
                         for i in range(-n // 2, n // 2 + 1)))
    rows = []
    for k in strikes:
        if k <= 0:
            continue
        m = k / spot
        iv = max(0.20 + 0.25 * max(0, 1 - m), 0.15)
        for right in ("C", "P"):
            oi = 1000 if ((k < spot and right == "P") or
                          (k >= spot and right == "C")) else 10
            if right == "C" and abs(k - wall_call) <= 1.0:
                oi = 50000
            if right == "P" and abs(k - wall_put) <= 1.0:
                oi = 50000
            rows.append({"strike": k, "right": right, "oi": oi,
                         "implied_vol": iv})
    return rows, spot, T


def test_scenario_channel_matrix_shape():
    """Every named scenario reports one signed flow per flow channel."""
    rows, spot, T = _canned_chain()
    b = ebe.scenario_hedge_flow(rows, spot, T=T)
    assert set(b.scenarios.keys()) == set(ebe.SCENARIOS)
    for s in ebe.SCENARIOS:
        assert set(b.scenarios[s].keys()) == set(ebe.FLOW_CHANNELS)
    assert math.isfinite(b.carry_descriptor)


def test_channel_split_exhaustive_non_overlapping():
    """The stock/futures vs options/vol vs carry descriptor split covers all
    six greeks exactly once (exhaustive + non-overlapping)."""
    rows, spot, T = _canned_chain()
    b = ebe.scenario_hedge_flow(rows, spot, T=T)
    all_channels = [g for group in b.channel_split.values() for g in group]
    assert set(all_channels) == set(ebe.GREEKS)
    assert len(all_channels) == len(set(all_channels))


def test_dex_is_carry_descriptor_only_not_forecast():
    """DEX never appears as a scenario flow forecast; it is carried as a
    descriptor equal to the post-multiplier DEX shares."""
    rows, spot, T = _canned_chain()
    ne = ebe.build_net_exposure(rows, spot, T=T)
    b = ebe.scenario_hedge_flow(rows, spot, T=T)
    for s in ebe.SCENARIOS:
        assert "delta" not in b.scenarios[s]
    assert b.carry_descriptor == pytest.approx(ne.dex())


def test_gamma_flow_opposes_spot_move_and_is_gex_scaled():
    """GEX (dollar-gamma-per-1%) is the primary lead channel: an up-move
    triggers buy pressure (+), a down-move sell pressure (-), symmetric."""
    rows, spot, T = _canned_chain()
    ne = ebe.build_net_exposure(rows, spot, T=T)
    b = ebe.scenario_hedge_flow(rows, spot, T=T)
    up = b.flow("up_1pct", "gamma")
    down = b.flow("down_1pct", "gamma")
    assert up == pytest.approx(-down, rel=0.01)
    # Dealer hedge trade opposes the option-book gamma response.
    assert up == pytest.approx(-ne.gex(), rel=0.01)  # activation ~1 at band edge


def test_vanna_flow_uses_one_formula_in_iv_scenarios():
    """The iv scenarios use the ONE vanna flow formula (constraint 6)."""
    rows, spot, T = _canned_chain()
    ne = ebe.build_net_exposure(rows, spot, T=T)
    b = ebe.scenario_hedge_flow(rows, spot, T=T)
    assert b.flow("iv_up_1pt", "vanna") == pytest.approx(ebe.vanna_flow(ne, 0.01))
    assert b.flow("iv_down_1pt", "vanna") == pytest.approx(ebe.vanna_flow(ne, -0.01))
    assert b.flow("iv_up_1pt", "vanna") == pytest.approx(
        -b.flow("iv_down_1pt", "vanna"), rel=1e-9)


def test_locus_activation_weights_spot_scenarios():
    """Each scenario cell is weighted by the Phase-2 locus activation; a 1%
    move at a 1% tolerance band sits right at the band edge (activation ~1)."""
    rows, spot, T = _canned_chain()
    b = ebe.scenario_hedge_flow(rows, spot, T=T, tolerance_pct=0.01)
    assert b.activation["up_1pct"] == pytest.approx(1.0, rel=0.2)
    assert b.activation["down_1pct"] == pytest.approx(1.0, rel=0.2)


def test_charm_only_in_time_scenario():
    """ChaEX (DTE -> OpEx clockwork) fires only in the dt_1d_opex scenario."""
    rows, spot, T = _canned_chain()
    b = ebe.scenario_hedge_flow(rows, spot, T=T)
    assert b.flow("dt_1d_opex", "charm") != 0.0
    for s in ("up_1pct", "down_1pct", "iv_up_1pt", "iv_down_1pt"):
        assert b.flow(s, "charm") == 0.0
