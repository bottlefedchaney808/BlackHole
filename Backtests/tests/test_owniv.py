from Backtests.backtest_owniv import render_owniv_report, run_owniv
from Backtests.core import FakeController, make_contract_row
from Backtests.data import build_chain_day


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


def test_run_owniv_produces_all_model_tables():
    td = FakeController(snapshot=_snapshot(), expirations=["20260918"], spot=100.0)
    cd = build_chain_day(td, "TST", "20260918", "20260720", 0.05, 0.0, use_hist=False)
    res = run_owniv([cd])
    assert res["n_days"] == 1
    for m in res["models"]:
        assert "summary" in res["models"][m]
    assert sorted(res["rank_by_iv_rmse"]) == sorted(res["models"].keys())
    lines = render_owniv_report(res)
    assert any("Model-implied vol" in ln for ln in lines)
    assert len(lines) > 8


def test_run_owniv_empty_no_crash():
    res = run_owniv([])
    assert res["n_days"] == 0
    assert len(render_owniv_report(res)) > 0
