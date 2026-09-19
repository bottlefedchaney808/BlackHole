"""Bin options-flow prints onto bars. One range query, never PH per bar.

Three things changed here after "whale symbols hardly ever show up":

1. **Binning is O(T log T), not O(T x B).** The old version walked every bar
   for every trade. A full SPY tape is six figures of prints; at 200 bars that
   is tens of millions of comparisons on a path that runs behind a 5-second
   poll. Trades are now sorted once and placed with `bisect`.

2. **A whale is a magnitude, not a bit.** `whale_bars` keeps the count, the
   largest print, and the call/put split per bar, so the chart can size the
   dot by how big the print actually was. `stamp_whale` survives unchanged as
   the boolean view of the same thing.

3. **The threshold adapts to the tape.** A flat $25k cut means something
   different on every symbol: measured on SPY it is the ~99.3rd percentile and
   clears about 9,500 prints a day, which across 26 bars is a dot on every
   bar; on a thin single name nothing ever clears it. `resolve_min_premium`
   instead targets a mark DENSITY -- roughly one dot every other bar -- with
   the $25k figure kept as a hard floor.

The bar interval rule is the session-aware one from `flow_pane`: `(prev, ts]`
within a session, `[ts, ts]` for the first bar of one. A print in the
overnight gap belongs to the session that follows it, not the one that closed
before it.
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from shared.chart_data import CandleRecord

try:
    from Direction.whale_scanner import WHALE_THRESHOLD as WHALE_PREMIUM
except Exception:  # noqa: BLE001 -- pragma: no cover; Vol_Suite may be absent
    WHALE_PREMIUM = 25_000.0

_PREMIUM_KEYS = ("premium", "premium_paid", "premiumPaid")
_TS_KEYS = (
    "datetime",
    "timestamp",
    "detection_timestamp",
    "execution_timestamp",
    "created",
)
_RIGHT_KEYS = ("trade_right", "right", "option_right", "call_put", "cp")

# Bars closer together than this are intraday-continuous; a larger gap is a
# session boundary (overnight or weekend).
_GAP_MAX = timedelta(minutes=45)

# When the adaptive threshold is in use, mark roughly this many prints per
# bar. A count target rather than a percentile, because a percentile does not
# control what you actually care about -- how crowded the chart looks.
#
# Measured on SPY 2026-09-17: the 99th percentile of the full tape is $17,864
# and would mark ~13,900 prints a day across 26 bars. The 99.9th is $329,600
# and still marks ~1,390. Neither number means "unusually large" on a
# different symbol or a quieter day, but "the biggest print in this bar" does.
ADAPTIVE_MARKS_PER_BAR = 0.5
MIN_ADAPTIVE_MARKS = 12


def _premium(row: dict[str, Any]) -> float:
    for key in _PREMIUM_KEYS:
        value = row.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _right(row: dict[str, Any]) -> str:
    for key in _RIGHT_KEYS:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip().upper()
        if text.startswith("C"):
            return "C"
        if text.startswith("P"):
            return "P"
    return ""


def _trade_ts(row: dict[str, Any]) -> datetime | None:
    for key in _TS_KEYS:
        value = row.get(key)
        if value is None:
            continue
        if isinstance(value, datetime):
            return value.replace(tzinfo=None) if value.tzinfo else value
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            continue
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    return None


def rows_from_flow_payload(data) -> list[dict[str, Any]]:
    """Normalize PH flow payloads (header-first tuples or dict lists) to dicts."""
    if not data:
        return []
    seq = list(data)
    first = seq[0]
    if isinstance(first, (list, tuple)) and first and isinstance(first[0], str):
        header = [str(key) for key in first]
        out: list[dict[str, Any]] = []
        for row in seq[1:]:
            if isinstance(row, dict):
                out.append(dict(row))
            elif isinstance(row, (list, tuple)):
                out.append(dict(zip(header, row, strict=False)))
        return out
    if isinstance(first, dict):
        return [dict(row) for row in seq if isinstance(row, dict)]
    return []


# ---------------------------------------------------------------------------
# Bar index
# ---------------------------------------------------------------------------


def bar_bounds(records: Sequence[CandleRecord]) -> list[tuple[datetime, datetime, bool]]:
    """`(start, end, first_of_session)` per bar, session-gap aware."""
    out: list[tuple[datetime, datetime, bool]] = []
    for index, record in enumerate(records):
        end = record.timestamp
        if index == 0:
            out.append((end, end, True))
            continue
        prev = records[index - 1].timestamp
        if end - prev > _GAP_MAX:
            out.append((end, end, True))
        else:
            out.append((prev, end, False))
    return out


def _place(
    bounds: Sequence[tuple[datetime, datetime, bool]],
    ends: Sequence[datetime],
    ts: datetime,
) -> int | None:
    """Index of the bar `ts` belongs to, or `None` if it falls outside.

    `bisect_left` on the bar end times finds the first bar that closes at or
    after the print. That bar is the only candidate: any earlier one closed
    before the print happened, and any later one opens after it.
    """
    i = bisect.bisect_left(ends, ts)
    if i >= len(bounds):
        return None
    start, end, first = bounds[i]
    if first:
        return i if start <= ts <= end else None
    return i if start < ts <= end else None


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------


def resolve_min_premium(
    trades: Sequence[dict[str, Any]] | None,
    *,
    mode: str = "adaptive",
    floor: float = WHALE_PREMIUM,
    bars: int = 0,
) -> float:
    """The premium above which a print counts as a whale on THIS tape.

    `"fixed"` returns `floor` -- the historical flat $25k.

    `"adaptive"` returns whatever threshold marks about `ADAPTIVE_MARKS_PER_BAR`
    prints per bar, never below `floor`. Two failure modes it exists to avoid,
    both real on this feed:

    - On SPY, $25k is roughly the 99.3rd percentile and clears ~9,500 prints a
      day. Spread over 26 bars that is a dot on every single bar, which is the
      same as no information.
    - On a thin single name, nothing ever clears $25k and the leg is dark for
      a structural reason that looks identical to "no whales today".

    The floor is what stops a dead tape manufacturing a whale out of a $900
    print merely because it was the largest one available.
    """
    if mode != "adaptive" or not trades:
        return float(floor)
    premiums = sorted(
        (p for p in (_premium(r) for r in trades if isinstance(r, dict)) if p > 0),
        reverse=True,
    )
    if not premiums:
        return float(floor)
    want = max(MIN_ADAPTIVE_MARKS, round(bars * ADAPTIVE_MARKS_PER_BAR))
    if want >= len(premiums):
        return float(floor)
    return float(max(floor, premiums[want - 1]))


# ---------------------------------------------------------------------------
# Stamping
# ---------------------------------------------------------------------------


def whale_bars(
    records: Sequence[CandleRecord],
    trades: Sequence[dict[str, Any]] | None,
    *,
    min_premium: float = WHALE_PREMIUM,
) -> list[dict[str, Any]]:
    """Per-bar whale detail, aligned 1:1 with `records`.

    Each entry is `{count, max_premium, total_premium, call_premium,
    put_premium, net_premium}`. A bar with no qualifying print is all zeros,
    which is a measurement (we looked, nothing cleared the bar), not a gap.
    """
    n = len(records)
    out = [
        {
            "count": 0,
            "max_premium": 0.0,
            "total_premium": 0.0,
            "call_premium": 0.0,
            "put_premium": 0.0,
            "net_premium": 0.0,
        }
        for _ in range(n)
    ]
    if n == 0 or not trades:
        return out

    bounds = bar_bounds(records)
    ends = [b[1] for b in bounds]
    for row in trades:
        if not isinstance(row, dict):
            continue
        premium = _premium(row)
        if premium < min_premium:
            continue
        ts = _trade_ts(row)
        if ts is None:
            # The provider was asked for exactly this window, so an unparsable
            # timestamp is still a print inside it -- attribute it to the
            # first bar rather than discard a real whale.
            index = 0
        else:
            placed = _place(bounds, ends, ts)
            if placed is None:
                continue
            index = placed
        slot = out[index]
        slot["count"] += 1
        slot["max_premium"] = max(slot["max_premium"], premium)
        slot["total_premium"] += premium
        right = _right(row)
        if right == "C":
            slot["call_premium"] += premium
        elif right == "P":
            slot["put_premium"] += premium
        slot["net_premium"] = slot["call_premium"] - slot["put_premium"]
    return out


def stamp_whale(
    records: Sequence[CandleRecord],
    trades: Sequence[dict[str, Any]] | None,
    *,
    min_premium: float = WHALE_PREMIUM,
) -> list[bool]:
    """Boolean view of `whale_bars` -- did any qualifying print land here."""
    return [bar["count"] > 0 for bar in whale_bars(records, trades, min_premium=min_premium)]


def _covered_session_dates(
    records: Sequence[CandleRecord], trades: Sequence[dict[str, Any]] | None
) -> tuple[list, set]:
    """(charted session dates, the subset the flow query actually reached).

    Keyed on the session DATE of a print, never on whether a bar ended up with
    a non-zero premium. Those are different questions: a covered session with
    no qualifying print is a real measurement reading zero, while an uncovered
    session is no measurement at all. `flow_observed_bars` and
    `whale_coverage` must answer from the same set, so both come through here.
    """
    bar_days = sorted({r.timestamp.date() for r in records})
    trade_days: set = set()
    for row in trades or []:
        if not isinstance(row, dict):
            continue
        ts = _trade_ts(row)
        if ts is not None:
            trade_days.add(ts.date())
    return bar_days, trade_days


def flow_observed_bars(
    records: Sequence[CandleRecord], trades: Sequence[dict[str, Any]] | None
) -> list[bool]:
    """Per-bar: did the flow pull actually cover this bar's session?

    The conviction engine needs this to decide, PER BAR, whether the whale
    component has an input at all -- see `signal_engine.conviction_series`.
    A single series-wide flag was correct only while the chart window and the
    flow window were the same length. They no longer are: the flow pull is
    capped at `CHART_APP_FLOW_DAYS` (5) session dates while 15m charts 20d
    (14 sessions), so measured on SPY 2026-09-18, 266 of 373 bars had no flow
    input while whale's 22 weight points stayed in their denominator --
    understating 252 bars by 28.2% and putting 31 of them on the wrong side of
    an entry/exit level.

    Note this is deliberately NOT `net_premium != 0`: a covered session bar
    with no qualifying print is present-and-reading-zero and must STAY in the
    denominator. Only a bar the provider never spoke for leaves it.
    """
    _bar_days, trade_days = _covered_session_dates(records, trades)
    if not trade_days:
        return [False] * len(records)
    return [r.timestamp.date() in trade_days for r in records]


def whale_coverage(
    records: Sequence[CandleRecord], trades: Sequence[dict[str, Any]] | None
) -> dict[str, Any]:
    """Which charted sessions the flow query actually reached.

    This is the missing piece behind "whales hardly ever show up". The flow
    provider is capped at a handful of session dates per refresh, but the
    chart happily shows twenty sessions -- so nineteen of them were dark for a
    structural reason no one could see. Reporting sessions-covered against
    sessions-charted turns a silent gap into a stated one.
    """
    bar_days, trade_days = _covered_session_dates(records, trades)
    covered = sorted(d for d in bar_days if d in trade_days)
    return {
        "sessions": len(bar_days),
        "covered": len(covered),
        "first_covered": covered[0].isoformat() if covered else None,
        "last_covered": covered[-1].isoformat() if covered else None,
        "partial": bool(bar_days) and len(covered) < len(bar_days),
    }


def apply_whale(
    rows: list[dict[str, Any]], flags: Sequence[bool]
) -> list[dict[str, Any]]:
    for row, flag in zip(rows, flags, strict=False):
        signals = row.setdefault("signals", {})
        signals["whale"] = bool(flag)
        row["score"] = int(sum(1 for value in signals.values() if value))
    return rows
