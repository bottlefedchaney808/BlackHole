"""Injectable daily OHLC history adapter for inline spot charts."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, time, timedelta
import math
import re
from typing import Any

from shared.chart_data import (
    CandlePayload,
    ChartDataError,
    _parse_timestamp,
    normalize_candles,
    SUPPORTED_INTERVALS,
)
from shared.thetadata import ThetaDataController


RawRows = Iterable[Mapping[str, Any]]
Provider = Callable[[str, Any], RawRows]


def _adapt_thetadata_rows(rows: RawRows) -> RawRows:
    """Map ThetaData EOD date fields to the candle contract's timestamp."""
    adapted: list[Mapping[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping) or not row:
            continue
        if "timestamp" in row:
            adapted.append(row)
            continue
        source_timestamp = row.get("created") or row.get("date") or row.get("Date")
        if source_timestamp is None:
            adapted.append(row)
            continue
        mapped = dict(row)
        mapped["timestamp"] = source_timestamp
        adapted.append(mapped)
    return adapted


_TICKER_RE = re.compile(r"^[A-Z0-9]+(?:[.-][A-Z0-9]+)*$")
_LOOKBACK_RE = re.compile(r"^(?P<amount>\d+)\s*(?P<unit>[dwmy])$", re.IGNORECASE)
_INTRADAY_INTERVAL_MINUTES = {"3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "1h": 60, "4h": 240}


def validate_ticker(ticker: Any) -> str:
    """Normalize a symbol and reject values unsafe for provider URL paths."""
    if not isinstance(ticker, str) or any(
        ord(character) < 32 or ord(character) == 127 for character in ticker
    ):
        raise ChartDataError("ticker must be a valid symbol")
    normalized = ticker.strip().upper()
    if not normalized or not _TICKER_RE.fullmatch(normalized):
        raise ChartDataError("ticker must be a valid symbol")
    return normalized


def validate_lookback(lookback: Any) -> Any:
    """Validate a public lookback while preserving its caller-supplied value."""
    if isinstance(lookback, bool):
        raise ChartDataError("lookback must be a positive day/month/year value")
    if isinstance(lookback, int):
        days = lookback
    elif isinstance(lookback, str):
        match = _LOOKBACK_RE.fullmatch(lookback.strip())
        if not match:
            raise ChartDataError("lookback must use a value such as '6m' or '30d'")
        amount = int(match.group("amount"))
        unit = match.group("unit").lower()
        days = amount * {"d": 1, "w": 7, "m": 30, "y": 365}[unit]
    else:
        raise ChartDataError("lookback must use a value such as '6m' or '30d'")

    if days <= 0:
        raise ChartDataError("lookback must be positive")
    return lookback


def validate_intraday_lookback(lookback: Any) -> Any:
    """Allow bounded day/week windows for dense intraday history."""
    if isinstance(lookback, bool):
        raise ChartDataError("intraday lookback must be a positive day/week value")
    if isinstance(lookback, int) and lookback > 0:
        days = lookback
    elif isinstance(lookback, str):
        match = re.fullmatch(r"(?P<amount>\d+)\s*(?P<unit>[dw])", lookback.strip(), re.I)
        if not match:
            raise ChartDataError("intraday lookback must use a value such as '1d' or '1w'")
        days = int(match.group("amount")) * (7 if match.group("unit").lower() == "w" else 1)
    else:
        raise ChartDataError("intraday lookback must use a value such as '1d' or '1w'")
    if days <= 0 or days > 30:
        raise ChartDataError("intraday lookback must be between 1 and 30 days")
    return lookback


def _lookback_start_end(lookback: Any, *, end: date | None = None) -> tuple[str, str]:
    """Convert the public lookback value into ThetaData's date arguments."""
    validate_lookback(lookback)
    if end is None:
        end = date.today()
        while end.weekday() >= 5:
            end -= timedelta(days=1)

    if isinstance(lookback, int):
        days = lookback
    else:
        match = _LOOKBACK_RE.fullmatch(lookback.strip())
        assert match is not None
        amount = int(match.group("amount"))
        unit = match.group("unit").lower()
        days = amount * {"d": 1, "w": 7, "m": 30, "y": 365}[unit]

    start = end - timedelta(days=days - 1)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def _latest_source_timestamp(rows: Iterable[Mapping[str, Any]]) -> datetime | None:
    """Return the latest parseable source timestamp, without creating one."""
    latest: datetime | None = None
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        value = row.get("timestamp") or row.get("created") or row.get("date") or row.get("Date")
        if value is None:
            continue
        try:
            timestamp = _parse_timestamp(value)
        except ChartDataError:
            continue
        if latest is None or timestamp > latest:
            latest = timestamp
    return latest


def _default_provider(ticker: str, lookback: Any) -> RawRows:
    """Fetch daily OHLCV through the existing stable shared ThetaData method."""
    start_date, end_date = _lookback_start_end(lookback)
    rows = ThetaDataController().hist_stock_eod(ticker, start_date, end_date)
    return _adapt_thetadata_rows(rows)


def fetch_daily_candles(
    ticker: str,
    *,
    lookback: Any = "6m",
    provider: Provider | None = None,
) -> CandlePayload:
    """Fetch and normalize daily candles for ``ticker``.

    ``provider`` receives ``(normalized_ticker, lookback)`` and is intended for
    deterministic, network-free callers and tests. The default uses
    ``ThetaDataController.hist_stock_eod``, the repository's public daily OHLCV
    client method. Provider details are intentionally not included in failures.
    """
    normalized_ticker = validate_ticker(ticker)
    validate_lookback(lookback)

    selected_provider = provider if provider is not None else _default_provider
    try:
        rows = list(selected_provider(normalized_ticker, lookback))
    except Exception as exc:
        raise ChartDataError(
            f"daily spot-history provider failed ({type(exc).__name__})"
        ) from exc

    try:
        return normalize_candles(
            rows,
            ticker=normalized_ticker,
            interval="1d",
            lookback=lookback,
            source="injected" if provider is not None else "thetadata",
            as_of=_latest_source_timestamp(rows),
        )
    except ChartDataError:
        raise
    except Exception as exc:
        raise ChartDataError("daily spot-history normalization failed") from exc


def _intraday_timestamp(row: Mapping[str, Any]) -> datetime:
    """Convert ThetaData's YYYYMMDD + milliseconds-of-day shape."""
    try:
        raw_date = str(row["date"])
        if not re.fullmatch(r"\d{8}", raw_date):
            raise ValueError
        day = datetime.strptime(raw_date, "%Y%m%d").date()
        milliseconds = int(row["ms_of_day"])
        if not 0 <= milliseconds < 86_400_000:
            raise ValueError
        return datetime.combine(day, time.min) + timedelta(milliseconds=milliseconds)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ChartDataError("intraday row has invalid date or ms_of_day") from exc


def _aggregate_intraday_rows(rows: RawRows, *, interval: str) -> tuple[list[Mapping[str, Any]], datetime]:
    minutes = _INTRADAY_INTERVAL_MINUTES.get(interval)
    if minutes is None:
        raise ChartDataError("unsupported intraday interval")
    buckets: dict[datetime, list[tuple[datetime, Mapping[str, Any]]]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ChartDataError(f"intraday row must be a mapping (row {index})")
        timestamp = _intraday_timestamp(row)
        try:
            values = {field: float(row[field]) for field in ("open", "high", "low", "close", "volume")}
            if any(not math.isfinite(value) for value in values.values()):
                raise ValueError
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ChartDataError(f"intraday row has invalid OHLCV (row {index})") from exc
        bucket_ms = (timestamp.hour * 3_600_000 + timestamp.minute * 60_000 + timestamp.second * 1_000 + timestamp.microsecond // 1_000) // (minutes * 60_000) * (minutes * 60_000)
        bucket = datetime.combine(timestamp.date(), time.min) + timedelta(milliseconds=bucket_ms)
        buckets.setdefault(bucket, []).append((timestamp, row))
    if not buckets:
        raise ChartDataError("no intraday candle rows")
    adapted = []
    latest = max(timestamp for entries in buckets.values() for timestamp, _ in entries)
    for bucket in sorted(buckets):
        entries = sorted(buckets[bucket], key=lambda item: item[0])
        source_rows = [item[1] for item in entries]
        adapted.append({
            "timestamp": bucket,
            "open": float(source_rows[0]["open"]),
            "high": max(float(row["high"]) for row in source_rows),
            "low": min(float(row["low"]) for row in source_rows),
            "close": float(source_rows[-1]["close"]),
            "volume": sum(float(row["volume"]) for row in source_rows),
        })
    return adapted, latest


def _default_intraday_provider(ticker: str, lookback: Any) -> RawRows:
    start_date, end_date = _lookback_start_end(lookback)
    return ThetaDataController().hist_stock_ohlc(ticker, start_date, end_date)


def fetch_intraday_candles(
    ticker: str,
    *,
    interval: str = "15m",
    lookback: Any = "1d",
    provider: Provider | None = None,
) -> CandlePayload:
    """Fetch one-minute ThetaData stock history and aggregate it."""
    normalized_ticker = validate_ticker(ticker)
    if interval not in _INTRADAY_INTERVAL_MINUTES:
        raise ChartDataError("interval must be a supported intraday interval")
    validate_intraday_lookback(lookback)
    selected_provider = provider if provider is not None else _default_intraday_provider
    try:
        rows = list(selected_provider(normalized_ticker, lookback))
        adapted, as_of = _aggregate_intraday_rows(rows, interval=interval)
        return normalize_candles(
            adapted, ticker=normalized_ticker, interval=interval, lookback=lookback,
            source="injected" if provider is not None else "thetadata", as_of=as_of,
        )
    except ChartDataError:
        raise
    except Exception as exc:
        raise ChartDataError(f"intraday spot-history provider failed ({type(exc).__name__})") from exc


__all__ = ["fetch_daily_candles", "fetch_intraday_candles", "validate_intraday_lookback"]
