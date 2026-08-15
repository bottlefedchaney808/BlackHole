"""Injectable daily OHLC history adapter for inline spot charts."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, timedelta
import re
from typing import Any

from shared.chart_data import (
    CandlePayload,
    ChartDataError,
    _parse_timestamp,
    normalize_candles,
)
from shared.thetadata import ThetaDataController


RawRows = Iterable[Mapping[str, Any]]
Provider = Callable[[str, Any], RawRows]


def _adapt_thetadata_rows(rows: RawRows) -> RawRows:
    """Map ThetaData's ``created`` field to the candle contract's timestamp."""
    adapted: list[Mapping[str, Any]] = []
    for row in rows:
        if "timestamp" in row or "created" not in row:
            adapted.append(row)
            continue
        mapped = dict(row)
        mapped["timestamp"] = mapped["created"]
        adapted.append(mapped)
    return adapted


_TICKER_RE = re.compile(r"^[A-Z0-9]+(?:[.-][A-Z0-9]+)*$")
_LOOKBACK_RE = re.compile(r"^(?P<amount>\d+)\s*(?P<unit>[dwmy])$", re.IGNORECASE)


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


def _lookback_start_end(lookback: Any, *, end: date | None = None) -> tuple[str, str]:
    """Convert the public lookback value into ThetaData's date arguments."""
    validate_lookback(lookback)
    if end is None:
        end = date.today()

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
        value = row.get("timestamp")
        if value is None:
            value = row.get("created")
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


__all__ = ["fetch_daily_candles"]
