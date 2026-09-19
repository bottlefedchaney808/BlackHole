from datetime import datetime, timedelta

from chart_app.flow_pane import bin_flow
from shared.chart_data import CandleRecord


def _bars():
    t0 = datetime(2026, 8, 18, 9, 30)
    return [
        CandleRecord(t0, 1, 1, 1, 1, 1),
        CandleRecord(t0 + timedelta(minutes=15), 1, 1, 1, 1, 1),
        CandleRecord(t0 + timedelta(minutes=30), 1, 1, 1, 1, 1),
    ]


def _trade(right, premium, ts):
    return {"trade_right": right, "premium": premium, "datetime": ts}


def test_bin_flow_splits_call_put_net_per_bar():
    records = _bars()
    trades = [
        _trade("C", 1000.0, "2026-08-18T09:30:00"),
        _trade("P", 500.0, "2026-08-18T09:30:00"),
        _trade("C", 2000.0, "2026-08-18T09:45:00"),
    ]
    out = bin_flow(records, trades)
    assert out["call"] == [1000.0, 2000.0, 0.0]
    assert out["put"] == [500.0, 0.0, 0.0]
    assert out["net"] == [500.0, 2000.0, 0.0]


def test_bin_flow_boundary_lands_in_bar():
    # Trade exactly at a bar's ts lands in THAT bar, not the next.
    records = _bars()
    trades = [_trade("C", 100.0, "2026-08-18T09:45:00")]
    out = bin_flow(records, trades)
    assert out["call"] == [0.0, 100.0, 0.0]


def test_bin_flow_zero_fill_and_empty():
    records = _bars()
    zeros = {
        "call": [0.0, 0.0, 0.0],
        "put": [0.0, 0.0, 0.0],
        "net": [0.0, 0.0, 0.0],
        "cum_net": [0.0, 0.0, 0.0],
    }
    assert bin_flow(records, []) == zeros
    assert bin_flow(records, None) == zeros
    assert bin_flow([], []) == {"call": [], "put": [], "net": [], "cum_net": []}


def test_bin_flow_cum_net_is_a_running_total():
    records = _bars()
    trades = [
        _trade("C", 500.0, "2026-08-18T09:30:00"),
        _trade("P", 200.0, "2026-08-18T09:40:00"),
        _trade("C", 100.0, "2026-08-18T09:55:00"),
    ]
    out = bin_flow(records, trades)
    assert out["net"] == [500.0, -200.0, 100.0]
    assert out["cum_net"] == [500.0, 300.0, 400.0]


def test_bin_flow_skips_unknown_right_and_nonpositive_premium():
    records = _bars()
    trades = [
        _trade("X", 500.0, "2026-08-18T09:30:00"),
        _trade("C", -10.0, "2026-08-18T09:30:00"),
        {"trade_right": "C", "datetime": "2026-08-18T09:30:00"},  # no premium
    ]
    out = bin_flow(records, trades)
    assert out["call"] == [0.0, 0.0, 0.0]


def test_bin_flow_missing_ts_goes_to_first_bar():
    records = _bars()
    trades = [{"trade_right": "P", "premium": 75.0}]
    out = bin_flow(records, trades)
    assert out["put"] == [75.0, 0.0, 0.0]
    assert out["net"] == [-75.0, 0.0, 0.0]


def test_bin_flow_gap_aware_multi_day():
    # Overnight gap: 8/17 bars then 8/18 bars. A trade on 8/17 must bin to
    # 8/17 bars, never the 8/18 first bar.
    d17 = datetime(2026, 8, 17)
    d18 = datetime(2026, 8, 18)
    records = [
        CandleRecord(d17.replace(hour=9, minute=30), 1, 1, 1, 1, 1),
        CandleRecord(d17.replace(hour=9, minute=45), 1, 1, 1, 1, 1),
        CandleRecord(d18.replace(hour=9, minute=30), 1, 1, 1, 1, 1),
        CandleRecord(d18.replace(hour=9, minute=45), 1, 1, 1, 1, 1),
    ]
    trades = [
        _trade("C", 100.0, "2026-08-17T09:30:00"),  # 8/17 bar 0
        _trade("P", 200.0, "2026-08-17T09:45:00"),  # 8/17 bar 1
        _trade("C", 300.0, "2026-08-18T09:30:00"),  # 8/18 bar 2
    ]
    out = bin_flow(records, trades)
    assert out["call"] == [100.0, 0.0, 300.0, 0.0]
    assert out["put"] == [0.0, 200.0, 0.0, 0.0]
    assert out["net"] == [100.0, -200.0, 300.0, 0.0]
