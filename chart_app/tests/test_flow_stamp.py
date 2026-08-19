from datetime import datetime, timedelta

from shared.chart_data import CandleRecord
from chart_app.flow_stamp import apply_whale, stamp_whale


def _bars():
    t0 = datetime(2026, 8, 18, 9, 30)
    return [
        CandleRecord(t0, 1, 1, 1, 1, 1),
        CandleRecord(t0 + timedelta(minutes=15), 1, 1, 1, 1, 1),
        CandleRecord(t0 + timedelta(minutes=30), 1, 1, 1, 1, 1),
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
