"""Bin the full options tape onto bars for the flow pane. No I/O.

Shares one binning implementation with `flow_stamp` (`bar_bounds` + the
bisect placement) rather than keeping a second copy of the interval rule.
They had drifted: this module was session-gap aware and `flow_stamp` was not,
so a print in the overnight gap counted toward the flow pane and the whale
dot on two different bars.

Adds `cum_net` alongside the per-bar series. Per-bar net premium is spiky
enough that the eye reads only the two biggest bars of a session; the running
total is what actually shows whether the tape has been accumulating calls or
puts all day.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from chart_app.flow_stamp import _place, _premium, _right, _trade_ts, bar_bounds
from shared.chart_data import CandleRecord


def bin_flow(
    records: Sequence[CandleRecord], trades: Sequence[dict[str, Any]]
) -> dict[str, list[float]]:
    """Per-bar option premium aggregates, aligned 1:1 with `records`.

    `{"call", "put", "net", "cum_net"}`, zero-filled where nothing traded. A
    trade with no parsable timestamp is attributed to the first bar (the
    provider was asked for this window, so the print is in it).
    """
    n = len(records)
    calls: list[float] = [0.0] * n
    puts: list[float] = [0.0] * n
    if n == 0 or not trades:
        return {"call": calls, "put": puts, "net": [0.0] * n, "cum_net": [0.0] * n}

    bounds = bar_bounds(records)
    ends = [b[1] for b in bounds]
    for row in trades:
        if not isinstance(row, dict):
            continue
        premium = _premium(row)
        if premium <= 0:
            continue
        ts = _trade_ts(row)
        if ts is None:
            index = 0
        else:
            placed = _place(bounds, ends, ts)
            if placed is None:
                continue
            index = placed
        right = _right(row)
        if right == "C":
            calls[index] += premium
        elif right == "P":
            puts[index] += premium

    nets = [c - p for c, p in zip(calls, puts, strict=True)]
    cum: list[float] = []
    running = 0.0
    for value in nets:
        running += value
        cum.append(running)
    return {"call": calls, "put": puts, "net": nets, "cum_net": cum}
