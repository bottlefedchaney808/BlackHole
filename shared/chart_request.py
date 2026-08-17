"""Assistant-facing orchestration seam for inline daily spot charts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from os import PathLike
from typing import Any

from shared.candlestick_chart import render_candlestick
from shared.chart_data import CandlePayload, ChartDataError
from shared.spot_history import (
    fetch_daily_candles,
    fetch_intraday_candles,
    validate_intraday_lookback,
    validate_lookback,
    validate_ticker,
)


@dataclass(frozen=True, slots=True)
class ChartRequest:
    """Validated parameters for an assistant-facing spot chart request."""

    ticker: str
    lookback: Any = "6m"
    interval: str = "1d"


@dataclass(frozen=True, slots=True)
class ChartArtifact:
    """Rendered chart path and metadata suitable for inline caption generation."""

    path: Path
    ticker: str
    interval: str
    lookback: Any
    source: str
    observation_range: tuple[datetime, datetime]
    as_of: datetime | None
    row_count: int
    warnings: tuple[str, ...] = ()

    @property
    def observation_start(self) -> datetime:
        """Return the first observation timestamp."""
        return self.observation_range[0]

    @property
    def observation_end(self) -> datetime:
        """Return the last observation timestamp."""
        return self.observation_range[1]


Renderer = Callable[[CandlePayload, str | PathLike[str]], Path]


def _validate_request(ticker: str, *, lookback: Any, interval: str) -> ChartRequest:
    normalized_ticker = validate_ticker(ticker)
    if interval == "1d":
        selected_lookback = "6m" if lookback is None else lookback
        validate_lookback(selected_lookback)
    else:
        selected_lookback = "1d" if lookback is None else lookback
        validate_intraday_lookback(selected_lookback)
    if interval not in ("3m", "5m", "10m", "15m", "30m", "1h", "4h", "1d"):
        raise ChartDataError("interval must be one of the supported chart intervals")
    return ChartRequest(
        ticker=normalized_ticker,
        lookback=selected_lookback,
        interval=interval,
    )


def build_spot_chart_request(
    ticker: str,
    *,
    lookback: Any = None,
    interval: str = "1d",
) -> ChartRequest:
    """Validate and construct a daily spot-chart request."""
    return _validate_request(ticker, lookback=lookback, interval=interval)


def render_spot_chart(
    ticker: str,
    *,
    lookback: Any = None,
    interval: str = "1d",
    output_path: str | PathLike[str],
    provider=None,
    renderer: Renderer | None = None,
) -> ChartArtifact:
    """Fetch, normalize, and render a daily spot chart.

    Request validation happens before ``fetch_daily_candles`` is called, so an
    unsupported interval cannot invoke a provider. ``provider`` and ``renderer``
    are injectable to keep callers and tests independent of network access and
    the matplotlib implementation.
    """
    request = build_spot_chart_request(
        ticker,
        lookback=lookback,
        interval=interval,
    )
    if not isinstance(output_path, (str, PathLike)):
        raise ChartDataError("output_path must be a path")
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if request.interval == "1d":
        payload = fetch_daily_candles(request.ticker, lookback=request.lookback, provider=provider)
    else:
        payload = fetch_intraday_candles(
            request.ticker, interval=request.interval, lookback=request.lookback, provider=provider
        )
    selected_renderer = renderer if renderer is not None else render_candlestick
    selected_renderer(payload, path)

    observations = payload.observations
    if not observations:
        raise ChartDataError("chart payload must contain observations")
    quality = payload.quality
    warnings = quality.warnings if quality is not None else ()
    return ChartArtifact(
        path=path,
        ticker=payload.ticker,
        interval=payload.interval,
        lookback=payload.lookback,
        source=payload.source,
        observation_range=(observations[0].timestamp, observations[-1].timestamp),
        as_of=payload.as_of,
        row_count=len(observations),
        warnings=tuple(warnings),
    )


__all__ = [
    "ChartArtifact",
    "ChartRequest",
    "build_spot_chart_request",
    "render_spot_chart",
]
