"""Stamp whale flags from ONE flow range query. Never call PH per bar."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from shared.chart_data import CandleRecord

try:
    from Direction.whale_scanner import WHALE_THRESHOLD as WHALE_PREMIUM
except Exception:  # pragma: no cover
    WHALE_PREMIUM = 25_000.0

_PREMIUM_KEYS = ("premium", "premium_paid", "premiumPaid")
_TS_KEYS = ("datetime", "timestamp", "detection_timestamp", "execution_timestamp", "created")


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


def _trade_ts(row: dict[str, Any]) -> datetime | None:
    for key in _TS_KEYS:
        value = row.get(key)
        if value is None:
            continue
        if isinstance(value, datetime):
            return value.replace(tzinfo=None) if value.tzinfo else value
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            continue
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    return None


def rows_from_flow_payload(data) -> list[dict[str, Any]]:
    """Normalize PH flow payloads (header-first tuples or dict lists) to dict rows."""
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


def stamp_whale(
    records: list[CandleRecord],
    trades: list[dict[str, Any]] | None,
    *,
    min_premium: float = WHALE_PREMIUM,
) -> list[bool]:
    """Bin range-query prints onto closed bars: (prev_ts, ts]. First bar is [ts0, ts0]."""
    flags = [False] * len(records)
    if not records or not trades:
        return flags
    parsed: list[datetime] = []
    for row in trades:
        if not isinstance(row, dict):
            continue
        if _premium(row) < min_premium:
            continue
        ts = _trade_ts(row)
        if ts is None:
            # Provider was asked for the window — trust as first-bar hit.
            parsed.append(records[0].timestamp)
            continue
        parsed.append(ts)
    for trade_ts in parsed:
        for index, record in enumerate(records):
            start = records[index - 1].timestamp if index else record.timestamp
            end = record.timestamp
            inside = start <= trade_ts <= end if index == 0 else start < trade_ts <= end
            if inside:
                flags[index] = True
                break
    return flags


def apply_whale(rows: list[dict[str, Any]], flags: list[bool]) -> list[dict[str, Any]]:
    for row, flag in zip(rows, flags, strict=False):
        signals = row.setdefault("signals", {})
        signals["whale"] = bool(flag)
        row["score"] = int(sum(1 for value in signals.values() if value))
    return rows
