"""Detect and back-adjust unadjusted stock splits in a daily bar series.

Why this exists
---------------
`shared/spot_history.fetch_daily_candles` serves **unadjusted** prices. On a
one-year window you rarely notice. On three years you get this, measured on
this feed on 2026-09-18:

    NVDA  2024-06-07  close 1208.88
    NVDA  2024-06-10  close  121.79      <- the 10-for-1 split

which the engine reads as a genuine -89.9% session. Everything downstream
then compounds the lie: ATR explodes, the entropy buckets rescale, the
trailing stop fires into a price that never traded, and a backtest reports a
loss that belongs to a corporate action. NVDA's "-92% out-of-sample" in the
first walk-forward run of this engine was entirely this.

Scope
-----
This module guards `chart_app`. The same feed backs other consumers of
multi-year daily history in this repo -- `Vol_Suite/correlation_engine.py`'s
`fetch_price_history` most notably, where an unadjusted split would put a
fabricated -90% observation into a correlation matrix and a realized-vol
estimate. That is flagged, not fixed here: widening a chart change into
shared/ is not this task's call to make.

Detection
---------
A split is a bar-to-bar close ratio that lands on a simple rational factor
AND moves more than `MIN_LOG_MOVE`. Both conditions matter:

- The ratio test alone would catch a real -50% crash (ratio 2.0).
- The magnitude test alone would catch a real -40% earnings gap.

Requiring the move to be both extreme *and* to land within `RATIO_TOL` of a
number an exchange actually uses is what separates the two. A genuine crash
landing within 1.5% of exactly 2.000 is possible; it is also rare enough that
flagging it -- loudly, in `SplitEvent` -- is better than silently trading a
fabricated 90% gap. Nothing here adjusts without saying so.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from shared.chart_data import CandleRecord

# Ratios exchanges actually use, forward and reverse.
_FACTORS: tuple[float, ...] = (
    2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 15.0, 20.0, 25.0, 50.0,
    1.5, 2.5,           # 3-for-2, 5-for-2
)
RATIO_TOL = 0.015       # within 1.5% of the nominal factor
MIN_LOG_MOVE = 0.30     # ~26% -- below this, do not even consider a split


@dataclass(frozen=True)
class SplitEvent:
    index: int          # the bar AFTER which the split took effect
    timestamp: str
    prev_close: float
    close: float
    factor: float       # >1 forward split (price fell), <1 reverse
    direction: str      # "forward" | "reverse"

    def describe(self) -> str:
        return (
            f"{self.timestamp}: {self.prev_close:.2f} -> {self.close:.2f} "
            f"({self.direction} {self.factor:g}-for-1)"
        )


def _nearest_factor(ratio: float) -> float | None:
    for factor in _FACTORS:
        if abs(ratio - factor) / factor <= RATIO_TOL:
            return factor
    return None


def detect_splits(records: Sequence[CandleRecord]) -> list[SplitEvent]:
    """Every bar boundary that looks like an unadjusted split."""
    events: list[SplitEvent] = []
    for i in range(1, len(records)):
        prev = float(records[i - 1].close)
        cur = float(records[i].close)
        if prev <= 0 or cur <= 0:
            continue
        if abs(math.log(cur / prev)) < MIN_LOG_MOVE:
            continue
        forward = _nearest_factor(prev / cur)   # price fell by `factor`
        if forward is not None:
            events.append(
                SplitEvent(
                    index=i,
                    timestamp=records[i].timestamp.isoformat(),
                    prev_close=prev,
                    close=cur,
                    factor=forward,
                    direction="forward",
                )
            )
            continue
        reverse = _nearest_factor(cur / prev)   # price rose by `factor`
        if reverse is not None:
            events.append(
                SplitEvent(
                    index=i,
                    timestamp=records[i].timestamp.isoformat(),
                    prev_close=prev,
                    close=cur,
                    factor=reverse,
                    direction="reverse",
                )
            )
    return events


def back_adjust(
    records: Sequence[CandleRecord],
) -> tuple[list[CandleRecord], list[SplitEvent]]:
    """Return a split-adjusted copy of `records` plus what was adjusted.

    Standard back-adjustment: every bar *before* a split is divided by the
    split factor, so the most recent price stays the real traded price and
    history is restated in today's share terms. Volume is multiplied by the
    same factor, which keeps dollar volume (and therefore the Hui-Heubel
    liquidity ratio) continuous across the boundary.

    Returns the input unchanged when nothing is detected, so it is safe to
    call on every series.
    """
    events = detect_splits(records)
    if not events:
        return list(records), []

    out = [
        CandleRecord(
            timestamp=r.timestamp,
            open=float(r.open),
            high=float(r.high),
            low=float(r.low),
            close=float(r.close),
            volume=None if r.volume is None else float(r.volume),
        )
        for r in records
    ]
    # Apply from the most recent split backwards so earlier bars accumulate
    # every later factor -- a symbol that split twice needs both applied to
    # the oldest section.
    for event in sorted(events, key=lambda e: e.index, reverse=True):
        factor = event.factor if event.direction == "forward" else 1.0 / event.factor
        for i in range(event.index):
            r = out[i]
            out[i] = CandleRecord(
                timestamp=r.timestamp,
                open=r.open / factor,
                high=r.high / factor,
                low=r.low / factor,
                close=r.close / factor,
                volume=None if r.volume is None else r.volume * factor,
            )
    return out, events
