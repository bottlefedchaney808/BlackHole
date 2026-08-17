"""test_broker_book_tool.py -- covers Tools/tools/broker_book.py: the convention rules,
aggregation, forward-return join, and the pooled + cross-sectional backtest arms.

All network-free: aggregation and stats operate on synthetic chain-scan frames; the
orchestrated run injects a fake closes fetcher.
"""

import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from Tools.tools import broker_book  # noqa: E402


def _chain_df():
    return pd.DataFrame(
        [
            {
                "strike": 100,
                "right": "C",
                "delta": 0.5,
                "gamma": 0.02,
                "vanna": 10.0,
                "theta": -1.0,
                "vega": 3.0,
                "charm": 0.5,
                "oi": 100,
            },
            {
                "strike": 100,
                "right": "P",
                "delta": -0.4,
                "gamma": 0.03,
                "vanna": -5.0,
                "theta": -0.8,
                "vega": 2.0,
                "charm": -0.2,
                "oi": 200,
            },
        ]
    )


# --------------------------------------------------------------------------- #
# Convention rules
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_delta_vanna_charm_taken_as_is_no_direction():
    nets = broker_book.compute_nets(_chain_df())
    # call delta +0.5*100 + put delta -0.4*200 = 50 - 80 = -30 (as-is, NOT negated)
    assert nets["net_delta"] == pytest.approx(-30.0)
    # vanna: +10*100 + (-5)*200 = 1000 - 1000 = 0 (as-is)
    assert nets["net_vanna"] == pytest.approx(0.0)
    # charm: +0.5*100 + (-0.2)*200 = 50 - 40 = 10
    assert nets["net_charm"] == pytest.approx(10.0)


@pytest.mark.unit
def test_gamma_as_is_always_positive_and_gex_reference():
    nets = broker_book.compute_nets(_chain_df())
    # net_gamma (as-is): 0.02*100 + 0.03*200 = 8 -> always positive
    assert nets["net_gamma"] == pytest.approx(8.0)
    assert nets["net_gamma"] > 0
    # GEX reference (calls+ puts-): 2 - 6 = -4 (imported sign, allowed only on gamma)
    assert nets["net_gamma_gex"] == pytest.approx(-4.0)


@pytest.mark.unit
def test_gamma_gex_calls_plus_puts_minus():
    # Puts dominate -> GEX reference is negative even though as-is gamma stays positive.
    nets = broker_book.compute_nets(_chain_df())
    assert nets["net_gamma"] > 0 and nets["net_gamma_gex"] < 0


@pytest.mark.unit
def test_rows_without_oi_excluded():
    extra = pd.DataFrame(
        [
            {
                "strike": 101,
                "right": "C",
                "delta": 9.0,
                "gamma": 9.0,
                "vanna": 9.0,
                "theta": 9.0,
                "vega": 9.0,
                "charm": 9.0,
                "oi": 0,
            }
        ]
    )
    df = pd.concat([_chain_df(), extra], ignore_index=True)
    nets = broker_book.compute_nets(df)
    # oi=0 row must not contribute (net_delta unchanged from the 2-row case)
    assert nets["net_delta"] == pytest.approx(-30.0)


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_parse_chain_filename():
    meta = broker_book.parse_chain_filename(
        "TGB_20261120_chain_scan_20260813_133537.csv"
    )
    assert meta["ticker"] == "TGB"
    assert meta["expiry"] == "20261120"
    assert meta["scan_dt"].year == 2026
    assert broker_book.parse_chain_filename("not_a_scan.csv") is None


@pytest.mark.unit
def test_aggregate_corpus_reads_chain_scans(tmp_path):
    # write two chain scans matching the filename convention
    (tmp_path / "TGB_20261120_chain_scan_20260813_133537.csv").write_text(
        _chain_df().to_csv(index=False), encoding="utf-8"
    )
    (tmp_path / "SPY_20261120_chain_scan_20260813_133540.csv").write_text(
        _chain_df().to_csv(index=False), encoding="utf-8"
    )
    rows = broker_book.aggregate_corpus([str(tmp_path)])
    assert len(rows) == 2
    tickers = {r["ticker"] for r in rows}
    assert tickers == {"TGB", "SPY"}
    assert rows[0]["date"] == dt.date(2026, 8, 13)
    # nets computed per the rules
    assert rows[0]["net_delta"] == pytest.approx(-30.0)
    assert rows[0]["net_gamma_gex"] == pytest.approx(-4.0)


# --------------------------------------------------------------------------- #
# Forward-return join
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_forward_returns_from_closes():
    closes = {
        dt.date(2026, 8, 13): 100.0,
        dt.date(2026, 8, 14): 105.0,
        dt.date(2026, 8, 17): 110.0,
    }
    fr = broker_book.forward_returns_from_closes(closes, dt.date(2026, 8, 13), [1, 2])
    assert fr[1] == pytest.approx(0.05)
    assert fr[2] == pytest.approx(0.10)


@pytest.mark.unit
def test_forward_returns_none_when_insufficient_future():
    closes = {dt.date(2026, 8, 13): 100.0}
    fr = broker_book.forward_returns_from_closes(closes, dt.date(2026, 8, 13), [1])
    assert fr[1] is None


# --------------------------------------------------------------------------- #
# Pooled arm
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_run_pooled_reports_independence_counts():
    rows = [
        {"ticker": "A", "date": dt.date(2026, 8, 13), "net_delta": 1.0, "fwd_1": 0.01},
        {"ticker": "B", "date": dt.date(2026, 8, 13), "net_delta": 2.0, "fwd_1": 0.02},
        {"ticker": "C", "date": dt.date(2026, 8, 14), "net_delta": 3.0, "fwd_1": 0.03},
        {"ticker": "D", "date": dt.date(2026, 8, 14), "net_delta": 4.0, "fwd_1": 0.04},
    ]
    res = broker_book.run_pooled(rows, nets=["net_delta"], horizons=[1])
    assert res["nominal_snapshots"] == 4
    assert res["unique_ticker_date"] == 4
    assert res["unique_dates"] == 2
    cell = res["cells"][0]
    assert cell["net"] == "net_delta" and cell["horizon"] == 1
    assert cell["r"] == pytest.approx(1.0, abs=1e-9)  # perfectly monotonic
    assert cell["n"] == 4


# --------------------------------------------------------------------------- #
# Cross-sectional arm
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_run_cross_sectional_top_vs_bottom():
    # Same date, two tickers: higher net_delta -> higher forward return.
    rows = [
        {"ticker": "A", "date": dt.date(2026, 8, 13), "net_delta": 1.0, "fwd_1": 0.01},
        {"ticker": "B", "date": dt.date(2026, 8, 13), "net_delta": 3.0, "fwd_1": 0.05},
    ]
    res = broker_book.run_cross_sectional(rows, nets=["net_delta"], horizon=1)
    info = res["nets"]["net_delta"]
    assert info["dates"] == 1
    assert info["positive_dates"] == 1  # top half outperformed
    assert info["mean_diff"] > 0


@pytest.mark.unit
def test_run_cross_sectional_no_date_with_2_tickers():
    rows = [
        {"ticker": "A", "date": dt.date(2026, 8, 13), "net_delta": 1.0, "fwd_1": 0.01}
    ]
    res = broker_book.run_cross_sectional(rows, nets=["net_delta"], horizon=1)
    assert res["nets"]["net_delta"]["dates"] == 0
    assert res["nets"]["net_delta"]["mean_diff"] is None


# --------------------------------------------------------------------------- #
# Orchestration (network-free via injected fetcher)
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_run_backtest_orchestration(tmp_path):
    (tmp_path / "TGB_20261120_chain_scan_20260813_133537.csv").write_text(
        _chain_df().to_csv(index=False), encoding="utf-8"
    )
    (tmp_path / "SPY_20261120_chain_scan_20260813_133540.csv").write_text(
        _chain_df().to_csv(index=False), encoding="utf-8"
    )

    def fake_fetch(ticker, start, end):
        return {dt.date(2026, 8, 13): 100.0, dt.date(2026, 8, 14): 101.0}

    ctx = {"roots": [str(tmp_path)], "fetch_closes": fake_fetch}
    out = broker_book.run_backtest(ctx)
    assert out["n_snapshots"] == 2
    assert out["n_tickers"] == 2
    assert out["pooled"]["cells"]
    assert out["cross_sectional"]["fwd_1d"]["nets"]["net_delta"]["dates"] == 1
    assert isinstance(out["report"], str)
    assert "POOLED" in out["report"]
    assert "CROSS-SECTIONAL" in out["report"]


@pytest.mark.unit
def test_run_backtest_empty_corpus(tmp_path):
    ctx = {"roots": [str(tmp_path)], "fetch_closes": lambda t, s, e: {}}
    out = broker_book.run_backtest(ctx)
    assert out["n_snapshots"] == 0
    assert "no chain-scan snapshots" in out["report"]


@pytest.mark.unit
def test_run_returns_json_serializable(tmp_path):
    (tmp_path / "TGB_20261120_chain_scan_20260813_133537.csv").write_text(
        _chain_df().to_csv(index=False), encoding="utf-8"
    )
    ctx = {"roots": [str(tmp_path)], "fetch_closes": lambda t, s, e: {}}
    out = broker_book.run_backtest(ctx)
    json.dumps(out)  # must not raise
