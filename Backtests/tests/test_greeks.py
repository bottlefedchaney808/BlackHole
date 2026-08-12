from Backtests.backtest_greeks import run_greeks, render_greeks_report
from Backtests.core import FakeController, make_contract_row
from Backtests.data import ChainDay, build_chain_day


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


def test_run_greeks_two_levels():
    td = FakeController(snapshot=_snapshot(), expirations=["20260918"], spot=100.0)
    cd = build_chain_day(td, "TST", "20260918", "20260720", 0.05, 0.0, use_hist=False)
    res = run_greeks([cd])
    assert res["n_days"] == 1
    for level in ("L1", "L2"):
        for m in res["tables"][level]:
            for g in res["tables"][level][m]:
                s = res["tables"][level][m][g]
                assert "mae" in s
    # L1 ranks cover all 5 first-order greeks
    assert set(res["ranks"]["L1"].keys()) == {"delta", "gamma", "theta", "vega", "rho"}
    assert set(res["ranks"]["L2"].keys()) == {"vanna", "charm", "vomma", "speed", "color"}
    lines = render_greeks_report(res)
    assert any("Level 1" in ln for ln in lines)
    assert any("Level 2" in ln for ln in lines)


def test_run_greeks_empty_no_crash():
    res = run_greeks([])
    assert res["n_days"] == 0
    assert len(render_greeks_report(res)) > 0


def test_greeks_harness_accepts_current_repo_chain_rows():
    row = {
        "strike": 100.0,
        "right": "C",
        "iv": 0.20,
        "mid": 10.0,
        "delta": 0.5,
    }
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
    result = run_greeks([chain])
    assert result["tables"]["L1"]["CRR"]["delta"]["n"] == 1


def test_snapshot_chain_merges_second_order_market_greeks():
    first_order = {
        "strike": 100000,
        "right": "C",
        "implied_vol": "0.20",
        "underlying_price": "100.0",
        "delta": "0.5",
    }
    second_order = {
        "strike": 100000,
        "right": "C",
        "vanna": "-0.4",
        "charm": "0.1",
        "vomma": "60.0",
        "speed": "0.02",
        "color": "-0.03",
    }
    td = FakeController(snapshot=[first_order], expirations=["20260918"], spot=100.0)
    td.option_bulk_greeks_second_order = lambda root, exp: [second_order]

    cd = build_chain_day(td, "TST", "20260918", "20260720", 0.05, 0.0, use_hist=False)

    assert cd is not None
    assert cd.rows[0]["vanna"] == -0.4
    assert cd.rows[0]["charm"] == 0.1
    assert cd.rows[0]["vomma"] == 60.0
    assert cd.rows[0]["speed"] == 0.02
    assert cd.rows[0]["color"] == -0.03


def test_snapshot_chain_surfaces_second_order_endpoint_failure():
    td = FakeController(
        snapshot=[{
            "strike": 100000,
            "right": "C",
            "implied_vol": "0.20",
            "underlying_price": "100.0",
            "delta": "0.5",
        }],
        expirations=["20260918"],
        spot=100.0,
    )

    def fail_second_order(root, exp):
        raise RuntimeError("second-order endpoint unavailable")

    td.option_bulk_greeks_second_order = fail_second_order

    cd = build_chain_day(td, "TST", "20260918", "20260720", 0.05, 0.0, use_hist=False)

    assert cd is not None
    assert cd.l2_status == "unavailable"
    assert "second-order endpoint unavailable" in cd.l2_error


def test_snapshot_chain_marks_unusable_second_order_rows():
    td = FakeController(
        snapshot=[{
            "strike": 100000,
            "right": "C",
            "implied_vol": "0.20",
            "underlying_price": "100.0",
            "delta": "0.5",
        }],
        expirations=["20260918"],
        spot=100.0,
    )
    td.option_bulk_greeks_second_order = lambda root, exp: [{
        "strike": 100000,
        "right": "C",
        "vanna": "not-a-number",
    }]

    cd = build_chain_day(td, "TST", "20260918", "20260720", 0.05, 0.0, use_hist=False)

    assert cd is not None
    assert cd.l2_status == "unusable"
    assert "no usable rows" in cd.l2_error


def test_snapshot_chain_marks_partial_when_second_order_fields_are_incomplete():
    td = FakeController(
        snapshot=[{
            "strike": 100000,
            "right": "C",
            "implied_vol": "0.20",
            "underlying_price": "100.0",
            "delta": "0.5",
        }],
        expirations=["20260918"],
        spot=100.0,
    )
    td.option_bulk_greeks_second_order = lambda root, exp: [{
        "strike": 100000,
        "right": "C",
        "vanna": "-0.4",
    }]

    cd = build_chain_day(td, "TST", "20260918", "20260720", 0.05, 0.0, use_hist=False)

    assert cd is not None
    assert cd.l2_status == "partial"
    assert "partially covered required L2 fields" in cd.l2_error


def test_run_greeks_surfaces_l2_availability_diagnostics():
    chains = []
    for index, (status, error) in enumerate((
        ("unavailable", "second-order endpoint unavailable"),
        ("unusable", "second-order endpoint returned no usable rows"),
        ("partial", None),
    )):
        chains.append(ChainDay(
            ticker="TST",
            expiry="20260918",
            date=f"202607{20 + index}",
            rows=[{
                "strike": 100.0,
                "right": "C",
                "iv": 0.20,
                "mid": 10.0,
                "delta": 0.5,
            }],
            spot=100.0,
            T=0.5,
            r=0.05,
            q=0.0,
            forward=100.0,
            l2_status=status,
            l2_error=error,
        ))

    result = run_greeks(chains)

    assert result["l2_diagnostics"] == [
        {
            "ticker": "TST",
            "expiry": "20260918",
            "date": "20260720",
            "status": "unavailable",
            "error": "second-order endpoint unavailable",
        },
        {
            "ticker": "TST",
            "expiry": "20260918",
            "date": "20260721",
            "status": "unusable",
            "error": "second-order endpoint returned no usable rows",
        },
        {
            "ticker": "TST",
            "expiry": "20260918",
            "date": "20260722",
            "status": "partial",
            "error": None,
        },
    ]
    report = "\n".join(render_greeks_report(result))
    assert "L2 market-data availability:" in report
    assert "TST@20260918 20260720: unavailable" in report
    assert "second-order endpoint unavailable" in report
    assert "TST@20260918 20260721: unusable" in report
    assert "TST@20260918 20260722: partial" in report


def test_historical_chain_surfaces_l2_unavailable_status():
    chain = ChainDay(
        ticker="TST",
        expiry="20260918",
        date="20260720",
        rows=[{
            "strike": 100.0,
            "right": "C",
            "iv": 0.20,
            "mid": 10.0,
            "delta": 0.5,
        }],
        spot=100.0,
        T=0.5,
        r=0.05,
        q=0.0,
        forward=100.0,
        l2_status="unavailable",
        l2_error="historical rows did not include second-order greeks",
    )

    result = run_greeks([chain])

    assert result["l2_diagnostics"] == [{
        "ticker": "TST",
        "expiry": "20260918",
        "date": "20260720",
        "status": "unavailable",
        "error": "historical rows did not include second-order greeks",
    }]
