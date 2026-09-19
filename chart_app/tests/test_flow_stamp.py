from datetime import datetime, timedelta

from chart_app.flow_stamp import apply_whale, stamp_whale
from shared.chart_data import CandleRecord


def _bars():
    t0 = datetime(2026, 8, 18, 9, 30)
    return [
        CandleRecord(t0, 1, 1, 1, 1, 1),
        CandleRecord(t0 + timedelta(minutes=15), 1, 1, 1, 1, 1),
        CandleRecord(t0 + timedelta(minutes=30), 1, 1, 1, 1, 1),
    ]


def _session(day: str, count: int):
    """`count` 15-minute bars on one session date."""
    t0 = datetime.fromisoformat(f"{day}T09:30:00")
    return [
        CandleRecord(t0 + timedelta(minutes=15 * i), 1, 1, 1, 1, 1)
        for i in range(count)
    ]


def test_stamp_whale_bins_one_print_onto_its_bar():
    records = _bars()
    trades = [
        {"premium": 30_000, "timestamp": "2026-08-18T09:40:00"},
        {"premium": 10_000, "timestamp": "2026-08-18T09:50:00"},
    ]
    assert stamp_whale(records, trades) == [False, True, False]


def test_stamp_whale_empty_and_missing_premium():
    records = _bars()
    assert stamp_whale(records, []) == [False, False, False]
    assert stamp_whale(records, [{"timestamp": "2026-08-18T09:40:00"}]) == [False, False, False]


def test_apply_whale_recomputes_score():
    rows = [{"score": 2, "signals": {"whale": False, "wave3": True, "squeeze": True, "trend": False, "liquidity": False}}]
    apply_whale(rows, [True])
    assert rows[0]["signals"]["whale"] is True
    assert rows[0]["score"] == 3


def test_stamp_whale_accepts_datetime_key():
    records = _bars()
    trades = [{"premium": 30_000, "datetime": "2026-08-18T09:40:00"}]
    assert stamp_whale(records, trades) == [False, True, False]


def test_rows_from_flow_payload_header_tuple():
    from chart_app.flow_stamp import rows_from_flow_payload

    payload = (
        ("datetime", "premium"),
        ("2026-08-18T09:40:00", 30_000),
    )
    assert rows_from_flow_payload(payload) == [
        {"datetime": "2026-08-18T09:40:00", "premium": 30_000}
    ]


def test_flow_observed_bars_keys_on_session_dates_not_on_premium():
    """A covered session with no qualifying print is still a measurement.

    `signal_engine` drops the whale weight from the denominator on bars this
    returns False for, so answering from `net_premium != 0` would silently
    reward quiet tape -- see §3.4. It must answer from the session dates the
    provider actually spoke for.
    """
    from chart_app.flow_stamp import flow_observed_bars

    # Two sessions of bars, flow pulled for the second one only.
    records = _session("2026-09-17", 6) + _session("2026-09-18", 6)
    trades = [
        {"timestamp": "2026-09-18T10:00:00", "premium": 50_000.0, "right": "C"}
    ]

    observed = flow_observed_bars(records, trades)
    assert observed == [False] * 6 + [True] * 6

    # Every bar of the covered session counts, including the ones with no
    # print of their own -- only one trade was supplied, but all six are True.
    assert sum(observed) == 6


def test_flow_observed_bars_is_all_false_without_trades():
    from chart_app.flow_stamp import flow_observed_bars

    records = _session("2026-09-18", 4)
    assert flow_observed_bars(records, []) == [False] * 4
    assert flow_observed_bars(records, None) == [False] * 4
