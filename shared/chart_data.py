"""Validated, renderer-independent OHLC candle data contracts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
import math
from typing import Any


class ChartDataError(ValueError):
    """Raised when raw candle data cannot satisfy the chart data contract."""


@dataclass(frozen=True, slots=True)
class CandleRecord:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


@dataclass(frozen=True, slots=True)
class CandleQuality:
    """Validation metadata retained with a normalized payload."""

    row_count: int
    missing_fields: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CandlePayload:
    ticker: str
    interval: str
    lookback: Any
    source: str
    observations: tuple[CandleRecord, ...]
    as_of: datetime | None = None
    quality: CandleQuality | None = None

    def __post_init__(self) -> None:
        if self.quality is None:
            object.__setattr__(
                self,
                "quality",
                CandleQuality(row_count=len(self.observations)),
            )


_REQUIRED_FIELDS = ("timestamp", "open", "high", "low", "close")
SUPPORTED_INTERVALS = ("3m", "5m", "10m", "15m", "30m", "1h", "4h", "1d")


def _parse_timestamp(value: Any, *, field_name: str = "timestamp") -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(text)
        except (TypeError, ValueError) as exc:
            raise ChartDataError(f"invalid {field_name}: {value!r}") from exc
    else:
        raise ChartDataError(f"invalid {field_name}: {value!r}")

    if result.tzinfo is not None:
        result = result.astimezone(timezone.utc).replace(tzinfo=None)
    return result


def _finite_number(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise ChartDataError(f"{field_name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ChartDataError(f"{field_name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ChartDataError(f"{field_name} must be a finite number")
    return number


def normalize_candles(
    rows: Iterable[Mapping[str, Any]],
    *,
    ticker: str,
    interval: str = "1d",
    lookback: Any = None,
    source: str = "unknown",
    as_of: datetime | date | str | None = None,
) -> CandlePayload:
    """Validate and chronologically normalize provider rows into a payload.

    Required fields are ``timestamp``, ``open``, ``high``, ``low``, and
    ``close``. Invalid rows raise ``ChartDataError`` rather than being dropped.
    Duplicate timestamps are rejected because they are ambiguous to render.
    """
    normalized_ticker = ticker.strip().upper() if isinstance(ticker, str) else ""
    if not normalized_ticker:
        raise ChartDataError("ticker must be a non-empty string")
    if interval not in SUPPORTED_INTERVALS:
        raise ChartDataError("interval must be one of the supported chart intervals")

    if rows is None:
        raise ChartDataError("no candle rows")
    try:
        raw_rows = list(rows)
    except TypeError as exc:
        raise ChartDataError("rows must be iterable") from exc
    if not raw_rows:
        raise ChartDataError("no candle rows")

    records: list[CandleRecord] = []
    seen: set[datetime] = set()
    for index, row in enumerate(raw_rows):
        if not isinstance(row, Mapping):
            raise ChartDataError(f"row must be a mapping (row {index})")
        missing = [field for field in _REQUIRED_FIELDS if field not in row]
        if missing:
            raise ChartDataError(f"missing required field: {missing[0]}")

        timestamp = _parse_timestamp(row["timestamp"])
        if timestamp in seen:
            raise ChartDataError(f"duplicate timestamp: {timestamp.isoformat()}")
        seen.add(timestamp)

        values = {
            field: _finite_number(row[field], field_name=field)
            for field in ("open", "high", "low", "close")
        }
        volume = None
        if row.get("volume") is not None:
            volume = _finite_number(row["volume"], field_name="volume")

        if not (
            values["low"] <= values["open"] <= values["high"]
            and values["low"] <= values["close"] <= values["high"]
        ):
            raise ChartDataError(f"OHLC invariant violated at {timestamp.isoformat()}")

        records.append(CandleRecord(timestamp=timestamp, volume=volume, **values))

    parsed_as_of = _parse_timestamp(as_of, field_name="as_of") if as_of is not None else None
    records.sort(key=lambda record: record.timestamp)
    quality = CandleQuality(row_count=len(records))
    return CandlePayload(
        ticker=normalized_ticker,
        interval=interval,
        lookback=lookback,
        source=source,
        observations=tuple(records),
        as_of=parsed_as_of,
        quality=quality,
    )


__all__ = [
    "CandlePayload",
    "CandleQuality",
    "CandleRecord",
    "ChartDataError",
    "normalize_candles",
    "SUPPORTED_INTERVALS",
]
