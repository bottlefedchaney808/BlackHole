from Backtests.data import build_chain_day
from Backtests.backtest_pricing import run_pricing, render_pricing_report
from Backtests.core import FakeController, make_contract_row
from Backtests.data import ChainDay


def _snapshot(spot=100.0):
    strikes = [80, 85, 90, 95, 100, 105, 110, 115, 120]
    call_iv = [0.32, 0.28, 0.24, 0.21, 0.20, 0.205, 0.22, 0.26, 0.31]
    put_iv = [0.31, 0.26, 0.22, 0.205, 0.20, 0.21, 0.24, 0.28, 0.32]
    rows = []
    for k, right, iv in [(k, "C", v) for k, v in zip(strikes, call_iv)] + \
                        [(k, "P", v) for k, v in zip(strikes, put_iv)]:
        rows.append(make_contract_row(k, right, iv, spot,
                                      delta=0.5 if right == "C" else -0.5,
                                      gamma=0.02, theta=-0.05, vega=30.0, rho=-0.3,
                                      vanna=-0.4, charm=0.1, vomma=60.0,
                                      bid=1.0, ask=1.2, expiration="20260918"))
    return rows


def test_build_chain_day_normalizes_and_derives_context():
    td = FakeController(snapshot=_snapshot(), expirations=["20260918"], spot=100.0)
    cd = build_chain_day(td, "TST", "20260918", "20260720", 0.05, 0.0, use_hist=False)
    assert cd is not None
    assert len(cd.rows) == 18
    assert cd.spot > 0
    assert cd.T is not None and cd.T > 0
    assert cd.vv_ctx["atm_vol"] is not None
    assert cd.sabr_cal is not None
    assert cd.heston_ctx is not None and cd.heston_ctx["V0"] > 0
    # historical path (per-day) also works
    cd2 = build_chain_day(td, "TST", "20260918", "20260720", 0.05, 0.0, use_hist=True)
    assert cd2 is not None


def test_run_pricing_produces_all_model_tables():
    td = FakeController(snapshot=_snapshot(), expirations=["20260918"], spot=100.0)
    cd = build_chain_day(td, "TST", "20260918", "20260720", 0.05, 0.0, use_hist=False)
    res = run_pricing([cd])
    assert res["n_days"] == 1
    for m in res["models"]:
        t = res["models"][m]
        assert "summary" in t and "buckets" in t
        assert t["summary"].get("n", 0) >= 0
    # rank list contains every model once
    assert sorted(res["rank_by_rmse"]) == sorted(res["models"].keys())
    # renderer emits lines
    lines = render_pricing_report(res)
    assert any("Pricing accuracy" in ln for ln in lines)
    assert len(lines) > 8


def test_run_pricing_empty_chains_no_crash():
    res = run_pricing([])
    assert res["n_days"] == 0
    assert len(render_pricing_report(res)) > 0


def test_pricing_harness_accepts_current_repo_chain_rows():
    row = {"strike": 100.0, "right": "C", "iv": 0.20, "mid": 10.0}
    chain = ChainDay(
        ticker="TST",
        expiry="20260918",
        date="20260720",
        rows=[row],
        spot=100.0,
        T=0.5,
        r=0.05,
        q=0.0,
        forward=100.0,
    )
    result = run_pricing([chain])
    assert result["models"]["CRR"]["n"] == 1
