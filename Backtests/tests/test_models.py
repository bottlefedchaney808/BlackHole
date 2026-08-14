from Backtests.models import (
    build_heston_context,
    build_sabr_context,
    build_vv_context,
    evaluate_model,
    greeks_model,
    price_model,
)
from Backtests.core import make_contract_row, normalize_rows


_STRIKES = [80, 85, 90, 95, 100, 105, 110, 115, 120]
_CALL_IV = [0.32, 0.28, 0.24, 0.21, 0.20, 0.205, 0.22, 0.26, 0.31]
_PUT_IV = [0.31, 0.26, 0.22, 0.205, 0.20, 0.21, 0.24, 0.28, 0.32]


def _chain_rows(spot=100.0):
    # A few strikes around 100 with a mild skew; all greeks populated.
    rows = []
    for k, iv, right in [(k, iv, "C") for k, iv in zip(_STRIKES, _CALL_IV)] + \
                        [(k, iv, "P") for k, iv in zip(_STRIKES, _PUT_IV)]:
        rows.append(make_contract_row(k, right, iv, spot,
                                      delta=0.5 if right == "C" else -0.5,
                                      gamma=0.02, theta=-0.05, vega=30.0, rho=-0.3,
                                      vanna=-0.4, charm=0.1, vomma=60.0,
                                      bid=1.0, ask=1.2))
    return rows


def test_price_model_known_answer_trees():
    # American call with q=0 converges to BS European: ~10.45
    for name in ("CRR", "LR"):
        p = price_model(name, 100, 100, 1.0, 0.05, 0.0, 0.2, True)
        assert p is not None
        assert abs(p - 10.45) < 0.15
    # BAW American call q=0 ~ BS (early-exercise premium ~0 for calls)
    p = price_model("BAW", 100, 100, 1.0, 0.05, 0.0, 0.2, True)
    assert p is not None
    assert abs(p - 10.45) < 0.2


def test_price_model_put_parity():
    c = price_model("CRR", 100, 100, 1.0, 0.05, 0.0, 0.2, True)
    p = price_model("CRR", 100, 100, 1.0, 0.05, 0.0, 0.2, False)
    assert c is not None and p is not None
    # Tree parity breaks by the American early-exercise premium on the put
    # (q=0 => call=European, put>European), so a loose bound only.
    assert abs((c - p) - (100 - 100 * __exp(-0.05))) < 2.0


def test_greeks_known_answer_lr():
    # BS call: delta=N(d1)=0.6368, gamma=phi(d1)/(S*sigma*sqrtT)=0.3752/20=0.01876
    g = greeks_model("LR", 100, 100, 1.0, 0.05, 0.0, 0.2, True)
    assert g["delta"] is not None
    assert abs(g["delta"] - 0.6368) < 0.01
    assert g["gamma"] is not None
    assert abs(g["gamma"] - 0.01876) < 0.002


def test_greeks_smiles_finite():
    rows = normalize_rows(_chain_rows())
    vv = build_vv_context(rows, 100.0 * __exp(0.05), 1.0, 0.05, 0.0)
    assert vv["atm_vol"] is not None
    cal = build_sabr_context(rows, 100.0 * __exp(0.05), 1.0)
    assert cal is not None and "alpha" in cal
    h = build_heston_context(rows, 100.0, 100.0 * __exp(0.05), 1.0, 0.05, 0.0)
    # Canonical Heston now CALIBRATES to the chain (fits V0 freely); assert it
    # ran and produced a sane calibrated variance level, not a fixed 0.04.
    assert h["calibrated"] is True
    assert h["V0"] > 0 and h["kappa"] > 0 and "vol_sigma" in h and "rho" in h
    # SABR + VV greeks run without exception at ATM
    gs = greeks_model("SABR", 100, 100, 1.0, 0.05, 0.0, 0.20, True, sabr_cal=cal)
    assert gs["delta"] is not None
    gv = greeks_model("VV", 100, 100, 1.0, 0.05, 0.0, 0.20, True, vv_ctx=vv)
    assert gv["delta"] is not None


def test_bad_inputs_no_crash():
    assert price_model("CRR", 100, 100, 1.0, 0.05, 0.0, 0.2, True) is not None
    # missing VV context -> None price, empty greeks (never crash)
    assert price_model("VV", 100, 100, 1.0, 0.05, 0.0, 0.2, True, vv_ctx={}) is None
    g = greeks_model("SABR", 100, 100, 1.0, 0.05, 0.0, 0.2, True, sabr_cal=None)
    assert g["delta"] is None
    assert price_model("BOGUS", 100, 100, 1.0, 0.05, 0.0, 0.2, True) is None


def test_evaluate_model_prefers_canonical_mid_over_bid_ask():
    rows = [{
        "strike": 100.0,
        "right": "C",
        "iv": 0.20,
        "mid": 7.0,
        "bid": 1.0,
        "ask": 2.0,
        "delta": 0.5,
    }]

    result = evaluate_model("CRR", rows, 100.0, 1.0, 0.05, 0.0)

    assert result["pricing"]["n"] == 1
    assert result["pricing"]["pairs"][0][1] == 7.0


def __exp(x):
    import math
    return math.exp(x)
