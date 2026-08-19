"""Fill the local bar cache from injected candle fetchers. No live PH."""

from __future__ import annotations

from collections.abc import Callable

from chart_app.bar_cache import BarCache
from shared.chart_data import CandlePayload


def refresh_cache(
    cache: BarCache,
    ticker: str,
    interval: str,
    lookback: str,
    *,
    daily_fn: Callable[..., CandlePayload],
    intrad_fn: Callable[..., CandlePayload] | None,
) -> int:
    if interval == "1d":
        payload = daily_fn(ticker, lookback=lookback)
    else:
        payload = intrad_fn(ticker, interval=interval, lookback=lookback)
    return cache.upsert(ticker, interval, list(payload.observations))
