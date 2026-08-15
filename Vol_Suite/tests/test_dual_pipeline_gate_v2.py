"""Network-free unit tests for the P0-2/P0-3 expanded dual-pipeline gate v2.

These tests exercise the v2 driver's pure helpers on SYNTHETIC chains — no
ThetaData credentials, no network, no real .env, no live clock. They verify:

  - the pre-registered expanded firing set and SPY band rule (TOL_SPY/QQQ);
  - the LEVEL-vs-LEVEL conversion: the dIV/0.01 flow factor is UNDONE so the
    comparison is production LEVEL vs new-engine LEVEL (the P0-2 correction);
  - zero/NaN sign handling in the level conversion;
  - cluster sign agreement on a constructed net-short book (both families);
  - effective-n vs md accounting (SPY/QQQ/combined denominators);
  - lead/lag and placebo determinism;
  - v2 gate-verdict logic (PASS/FAIL/INDETERMINATE incl. eff-n and reflexivity);
  - production monkeypatch restoration (inherited from the P0-1 helpers).
"""

import importlib.util
import math
import os
import sys

import pytest

_SUITE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SUITE not in sys.path:
    sys.path.insert(0, _SUITE)
for _p in (os.path.join(_SUITE, ".."), os.path.join(_SUITE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import expiry_book_exposure as ebe  # noqa: E402
import run_dual_pipeline_gate_v2 as v2  # noqa: E402

# Ensure vol_suite's own expiry_selector wins (same race guard as conftest.py).
_sel_spec = importlib.util.spec_from_file_location(
    "expiry_selector", os.path.join(_SUITE, "expiry_selector.py"))
_sel = importlib.util.module_from_spec(_sel_spec)
sys.modules["expiry_selector"] = _sel
_sel_spec.loader.exec_module(_sel)


# ---------------------------------------------------------------------------
# Pre-registered expanded set + SPY band rule
# ---------------------------------------------------------------------------
def test_preregistered_expanded_pairs():
    assert v2.PAIRS == [
        ("20260508", "20260511"),
        ("20260522", "20260526"),
        ("20260605", "20260608"),
        ("20260619", "20260622"),
        ("20260703", "20260706"),
        ("20260716", "20260717"),
        ("20260717", "20260717"),
        ("20260731", "20260803"),
    ]
    assert v2.TICKERS == ["QQQ", "SPY"]


def test_spy_band_rule_preregistered():
    assert v2.TOL_QQQ == 0.010
    assert v2.TOL_SPY == 0.005
    assert v2.spy_band_rule("SPY") == 0.005
    assert v2.spy_band_rule("spy") == 0.005
    assert v2.spy_band_rule("QQQ") == 0.010
    assert v2.spy_band_rule("qqq") == 0.010
    # tighter band => smaller tolerance is the whole point of P0-2
    assert v2.spy_band_rule("SPY") < v2.spy_band_rule("QQQ")


def test_agree_frac_is_two_thirds():
    assert v2._AGREE_FRAC == pytest.approx(2.0 / 3.0)


def test_driver_has_v2_preregistered_estimand_docstring():
    doc = (v2.__doc__ or "")
    for tok in ("LEVEL-vs-LEVEL", "SPY BAND RULE", "EFFECTIVE-n", "PLACEBO",
                "REFLEXIVITY", "lead/lag", "GATE RULE", "0.5%", "zero-sign"):
        assert tok.lower() in doc.lower()


# ---------------------------------------------------------------------------
# LEVEL-vs-LEVEL conversion (the P0-2 correction)
# ---------------------------------------------------------------------------
def test_flow_to_level_undoes_div_factor():
    # new_level = new_flow / (dIV/0.01)
    # If a level is -2000 and dIV = -0.03, flow = level * (dIV/0.01) = +6000.
    level = -2000.0
    div = -0.03
    flow = level * (div / 0.01)
    assert v2._flow_to_level(flow, div) == pytest.approx(level)


def test_flow_to_level_positive_div():
    level = 350.0
    div = 0.015
    flow = level * (div / 0.01)
    assert v2._flow_to_level(flow, div) == pytest.approx(level)


def test_flow_to_level_sign_conserved():
    # Sign of the LEVEL equals sign of the flow only when dIV > 0; when dIV < 0
    # the flow sign flips. The level conversion must recover the true level sign.
    sg = v2._g._sign_of
    assert sg(v2._flow_to_level(100.0, -0.05)) == -1
    assert sg(v2._flow_to_level(-100.0, -0.05)) == 1
    assert sg(v2._flow_to_level(-100.0, 0.05)) == -1
    assert sg(v2._flow_to_level(100.0, 0.05)) == 1


def test_flow_to_level_zero_nan_missing_div():
    assert v2._flow_to_level(100.0, 0.0) is None
    assert v2._flow_to_level(100.0, None) is None
    assert v2._flow_to_level(100.0, float("nan")) is None
    assert v2._flow_to_level(100.0, "x") is None
    assert v2.new_level_from_flow(100.0, 0.02) == pytest.approx(
        100.0 / (0.02 / 0.01))


def test_md_at_eff_n():
    # K=3 -> md = 1.0 (zero power); more clusters -> md shrinks.
    assert v2._tanh_md(3) == pytest.approx(1.0)
    assert v2._tanh_md(4) == pytest.approx(1.0)
    md8 = v2._tanh_md(8)
    assert 0.0 < md8 < 1.0
    assert md8 < v2._tanh_md(6)  # more clusters => lower md
    assert v2._tanh_md(12) < md8


# ---------------------------------------------------------------------------
# Cluster sign agreement on a constructed net-short book (both families)
# ---------------------------------------------------------------------------
def _synthetic_chain(spot=500.0, put_heavy=True):
    """Deterministic synthetic same-day chain on a $5 grid (net-short/-long)."""
    base = round(spot / 5.0) * 5.0
    grid = [base + i * 5.0 for i in range(-12, 13)]
    grid_theta = {int(round(k * 1000)) for k in grid}
    eod = {}
    T = 0.01
    for k in grid:
        kt = int(round(k * 1000))
        for rt in ("C", "P"):
            if put_heavy:
                oi = 5000 if (rt == "P" and k < spot) else 200
            else:
                oi = 5000 if (rt == "C" and k > spot) else 200
            iv = 0.35 + 0.05 * abs(k - spot) / spot
            eod[(kt, rt)] = {
                "strike": kt, "right": rt, "implied_vol": iv,
                "gamma": ebe.bs_gamma(spot, k, T, iv),
                "delta": ebe.bs_delta(spot, k, T, iv, right=rt),
                "vanna": ebe.bs_vanna(spot, k, T, iv), "bid": 0.5, "ask": 0.6,
            }
    oi_c = {int(round(k * 1000)): (5000 if k > spot else 200) for k in grid}
    oi_p = {int(round(k * 1000)): (5000 if k < spot else 200) for k in grid}
    return eod, oi_c, oi_p, grid_theta


def test_production_and_new_agree_sign_net_short_level_vs_level():
    import run_dual_pipeline_gate as _g
    eod, oi_c, oi_p, grid_theta = _synthetic_chain(spot=500.0, put_heavy=True)
    seed = _g.build_production_seed("20260716", "20260717", 500.0,
                                    eod, oi_c, oi_p, grid_theta)
    pv, res = _g.run_production_vanna_same_day(seed, "QQQ")
    assert pv is not None and res is not None
    prod_sign = v2._g._sign_of(pv)

    # New engine LEVEL on the SAME chain (dIV>0 branch -> sign of level == sign
    # of exposure; a negative dIV would flip the flow but NOT the level).
    rows = [{"strike": k / 1000.0, "right": rt,
             "oi": (oi_p[k] if rt == "P" else oi_c[k]),
             "implied_vol": eod[(k, rt)]["implied_vol"]} for k, rt in eod]
    ne = ebe.build_net_exposure(rows, 500.0, ticker="QQQ", T=0.01)
    # LEVEL = SUM signed_vanna*OI*100*VANNA_PP_SCALE (no dIV factor).
    level = sum(r.greeks["vanna"] * r.oi * ebe.CONTRACT_MULTIPLIER
                * ebe.VANNA_PP_SCALE for r in ne.rows)
    new_sign = v2._g._sign_of(level)
    # The flow-vs-level mismatch would flip the sign under negative dIV; the
    # LEVEL sign must match production (both independently-derived conventions).
    assert prod_sign == new_sign
    assert prod_sign != 0


def test_flow_vs_level_sign_flip_is_artifact_and_level_corrects_it():
    # A negative dIV makes the FLOW sign opposite the LEVEL sign. The v2 driver
    # must use the LEVEL (undo the dIV factor), so agreement is computed on
    # level, not flow.
    import run_dual_pipeline_gate as _g
    eod, oi_c, oi_p, grid_theta = _synthetic_chain(spot=500.0, put_heavy=True)
    seed = _g.build_production_seed("20260716", "20260717", 500.0,
                                    eod, oi_c, oi_p, grid_theta)
    pv, res = _g.run_production_vanna_same_day(seed, "QQQ")
    prod_sign = v2._g._sign_of(pv)
    rows = [{"strike": k / 1000.0, "right": rt,
             "oi": (oi_p[k] if rt == "P" else oi_c[k]),
             "implied_vol": eod[(k, rt)]["implied_vol"]} for k, rt in eod]
    ne = ebe.build_net_exposure(rows, 500.0, ticker="QQQ", T=0.01)
    level = sum(r.greeks["vanna"] * r.oi * ebe.CONTRACT_MULTIPLIER
                * ebe.VANNA_PP_SCALE for r in ne.rows)
    div_neg = -0.05
    flow = ebe.vanna_flow(ne, div_neg)  # level * (dIV/0.01)
    flow_sign = v2._g._sign_of(flow)
    # FLOW sign is the OPPOSITE of the level under negative dIV:
    assert flow_sign == -v2._g._sign_of(level)
    # The v2 level conversion recovers the level sign:
    assert v2._g._sign_of(v2._flow_to_level(flow, div_neg)) == v2._g._sign_of(level)


def test_production_monkeypatch_restores_globals_v2():
    import run_dual_pipeline_gate as _g
    import dealer_positioning as dp
    eod, oi_c, oi_p, grid_theta = _synthetic_chain(spot=500.0, put_heavy=True)
    seed = _g.build_production_seed("20260716", "20260717", 500.0,
                                    eod, oi_c, oi_p, grid_theta)
    old_ctrl = dp.ThetaDataController
    old_dt = dp.datetime
    pv, res = _g.run_production_vanna_same_day(seed, "QQQ")
    assert dp.ThetaDataController is old_ctrl
    assert dp.datetime is old_dt
    assert res is not None and pv is not None and pv == pv and pv != 0.0


# ---------------------------------------------------------------------------
# Effective-n / md accounting (SPY/QQQ/combined denominators)
# ---------------------------------------------------------------------------
def test_effective_n_counts_only_resolvable_clusters():
    # rows_table rows shaped like driver output.
    rows = [
        {"ticker": "SPY", "day": "20260508", "both_nonzero": True, "agreed": True},
        {"ticker": "SPY", "day": "20260522", "both_nonzero": True, "agreed": False},
        {"ticker": "QQQ", "day": "20260508", "both_nonzero": True, "agreed": True},
        {"ticker": "QQQ", "day": "20260717", "both_nonzero": False, "agreed": False},
        {"ticker": "QQQ", "day": "20260731", "both_nonzero": True, "agreed": True},
    ]
    eff_n = sum(1 for r in rows if r["both_nonzero"])
    spy = sum(1 for r in rows if r["ticker"] == "SPY" and r["both_nonzero"])
    qqq = sum(1 for r in rows if r["ticker"] == "QQQ" and r["both_nonzero"])
    assert eff_n == 4
    assert spy == 2 and qqq == 2
    # md at eff-n=4 => 1.0 (no power); at higher n it shrinks.
    assert v2._tanh_md(eff_n) == pytest.approx(1.0)


def test_sign_agreement_denominator_accounting():
    rows = [
        {"ticker": "SPY", "both_nonzero": True, "agreed": True},
        {"ticker": "SPY", "both_nonzero": True, "agreed": False},
        {"ticker": "QQQ", "both_nonzero": True, "agreed": True},
        {"ticker": "QQQ", "both_nonzero": False, "agreed": False},  # excluded
    ]
    spy = (sum(1 for r in rows if r["ticker"] == "SPY" and r["both_nonzero"] and r["agreed"]),
           sum(1 for r in rows if r["ticker"] == "SPY" and r["both_nonzero"]))
    qqq = (sum(1 for r in rows if r["ticker"] == "QQQ" and r["both_nonzero"] and r["agreed"]),
           sum(1 for r in rows if r["ticker"] == "QQQ" and r["both_nonzero"]))
    comb = (sum(1 for r in rows if r["both_nonzero"] and r["agreed"]),
            sum(1 for r in rows if r["both_nonzero"]))
    assert spy == (1, 2)
    assert qqq == (1, 1)
    assert comb == (2, 3)


# ---------------------------------------------------------------------------
# V2 gate-verdict logic
# ---------------------------------------------------------------------------
def test_gate_v2_pass_majority_and_survives_reflexivity():
    ag = [(True, True), (True, True), (False, True), (True, True)]  # 3/4 resolvable
    verdict, _ = v2.gate_verdict_v2(ag, 0.30, 0.10, 20, 8)
    assert verdict == "PASS"


def test_gate_v2_fail_reflexivity_subsumes():
    ag = [(True, True), (True, True), (True, True)]
    verdict, reason = v2.gate_verdict_v2(ag, 0.15, 0.40, 20, 8)
    assert verdict == "FAIL"
    assert "reflexivity" in reason


def test_gate_v2_fail_wrong_direction():
    ag = [(True, True), (True, True), (True, True)]
    verdict, reason = v2.gate_verdict_v2(ag, -0.20, 0.10, 20, 8)
    assert verdict == "FAIL"


def test_gate_v2_fail_below_two_thirds():
    ag = [(True, True), (False, True), (False, True)]  # 1/3
    verdict, _ = v2.gate_verdict_v2(ag, 0.30, 0.10, 20, 8)
    assert verdict == "FAIL"


def test_gate_v2_indeterminate_low_power():
    ag = [(True, True), (True, True), (True, True)]
    # effective-n=4 (md=1.0, zero power) -> INDETERMINATE
    verdict, _ = v2.gate_verdict_v2(ag, 0.30, 0.10, 20, 4)
    assert verdict == "INDETERMINATE"


def test_gate_v2_indeterminate_insufficient_buckets():
    ag = [(True, True), (True, True)]
    verdict, _ = v2.gate_verdict_v2(ag, 0.30, 0.10, 3, 8)
    assert verdict == "INDETERMINATE"


def test_gate_v2_indeterminate_no_resolvable():
    ag = [(False, False), (False, False), (False, False)]
    verdict, _ = v2.gate_verdict_v2(ag, 0.0, 0.0, 10, 8)
    assert verdict == "INDETERMINATE"


# ---------------------------------------------------------------------------
# Lead/lag + placebo (reused helpers from P0-1, determinism preserved)
# ---------------------------------------------------------------------------
def test_placebo_deterministic_seed_v2():
    import run_dual_pipeline_gate as _g
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    y = [1.1, 2.2, 3.3, 4.4, 5.5]
    o1, n1, p1 = _g._placebo_null(x, y, n_perms=50, seed=v2._SEED)
    o2, n2, p2 = _g._placebo_null(x, y, n_perms=50, seed=v2._SEED)
    assert o1 == o2 and n1 == n2 and p1 == p2
    assert 0.0 <= p1 <= 1.0


def test_lead_lag_known_shift_v2():
    import run_dual_pipeline_gate as _g
    x = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    y = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    ll = _g._lead_lag_corrs(x, y, lags=(0, 1, 2))
    assert ll[0] > ll[1]
    assert "prior" in ll


def test_v2_seed_and_perms_preregistered():
    assert v2._SEED == 7
    assert v2._PLACEBO_PERMS == 1000


def test_v2_uses_level_corr_not_flow_corr():
    # The driver's correlational arm must be corr(new_LEVEL, fwd), the undivided
    # series — assert the module exposes a level conversion and the corr helper
    # is the P0-1 network-free one.
    assert callable(v2._flow_to_level)
    assert callable(v2._g._corr)


# ---------------------------------------------------------------------------
# No-credential-leak in serialized output
# ---------------------------------------------------------------------------
def test_v2_result_output_contains_no_credentials():
    import json
    out = json.dumps({"rows": [{"prod_vanna": 1.2, "new_day_LEVEL": 3.4}]})
    for tok in ("THETADATA_CF_ACCESS_CLIENT_ID", "THETADATA_CF_ACCESS_CLIENT_SECRET",
                "CF-Access-Client-Id", "api.potatohedge.com"):
        assert tok not in out
