"""Pure function to bin options flow (from scanner_trades full tape) onto bars.

Implements call/put/net premium series for the third chart pane.
No I/O. Uses identical (prev_ts, ts] binning rule (first bar [ts0, ts0])
and timestamp/premium parsers as flow_stamp.py (kept unchanged).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from shared.chart_data import CandleRecord

from chart_app.flow_stamp import _premium, _trade_ts

# Bars closer than this are intraday-continuous; a larger gap is a session
# boundary (overnight/weekend). A trade in the gap belongs to the next
# session's first bar, not the previous session's last bar.
_GAP_MAX = timedelta(minutes=45)


def _bar_start(records: list[CandleRecord], index: int) -> tuple[datetime, bool]:
    """(interval_start, is_first_of_session) for bar ``index``.

    First bar: [ts0, ts0]. Later bars: (prev_ts, ts] — but when the gap from
    the previous bar exceeds _GAP_MAX (session boundary), the bar starts at
    its own ts and trades in the gap attribute here.
    """
    if index == 0:
        return records[0].timestamp, True
    prev = records[index - 1].timestamp
    if records[index].timestamp - prev > _GAP_MAX:
        return records[index].timestamp, True
    return prev, False


def bin_flow(
    records: list[CandleRecord], trades: list[dict[str, Any]]
) -> dict[str, list[float]]:
    """Return per-bar aggregates aligned 1:1 with records.

    {"call": [float, ...], "put": [...], "net": [...]}
    - call += premium for trade_right=="C"
    - put += premium for "P"
    - net = call - put
    - zero fill for bars with no matching trades
    - trades with no parsable timestamp attributed to first bar
      (trust-the-window fallback, same as stamp_whale)
    """
    n = len(records)
    calls: list[float] = [0.0] * n
    puts: list[float] = [0.0] * n
    if not records or not trades:
        return {"call": calls[:], "put": puts[:], "net": [0.0] * n}

    for row in trades:
        if not isinstance(row, dict):
            continue
        prem = _premium(row)
        if prem <= 0:
            continue
        ts = _trade_ts(row)
        if ts is None:
            # same fallback as flow_stamp
            if n > 0:
                tr = str(row.get("trade_right", "")).strip().upper()
                if tr == "C":
                    calls[0] += prem
                elif tr == "P":
                    puts[0] += prem
            continue
        for index, record in enumerate(records):
            start, is_first = _bar_start(records, index)
            end = record.timestamp
            inside = start <= ts <= end if is_first else start < ts <= end
            if inside:
                tr = str(row.get("trade_right", "")).strip().upper()
                if tr == "C":
                    calls[index] += prem
                elif tr == "P":
                    puts[index] += prem
                break

    nets = [c - p for c, p in zip(calls, puts)]
    return {"call": calls, "put": puts, "net": nets}
