"""Fill the local bar cache from injected candle fetchers. No live PH."""

from __future__ import annotations

import time
from collections.abc import Callable

from chart_app.bar_cache import BarCache
from shared.chart_data import CandlePayload

# Coarse intervals are aggregated from one-minute rows, so a 20-30 day pull is
# a large request and the provider intermittently times out under load -- see
# the repo's `rate-limit-options` guidance on distinguishing rate-limit noise
# from genuinely missing data. A timeout used to end the refresh with zero
# bars cached, which on a timeframe with no prior cache is indistinguishable
# from "this interval has no data": an empty chart, no explanation. Retrying a
# couple of times with backoff turns most of those into a normal load.
_RETRY_DELAYS_S = (1.5, 4.0)


def _is_transient(exc: BaseException) -> bool:
    """Whether an exception looks like provider load rather than bad input.

    Matched on the message because the provider error (`PHTimeoutError`) is
    wrapped in `ChartDataError` by shared/spot_history.py before it gets
    here, so the concrete type is gone by this point. A validation failure
    (bad ticker, unsupported interval, out-of-range lookback) must NOT be
    retried -- it will fail identically every time and just delays the real
    error reaching the caller.
    """
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(
        marker in text
        for marker in ("timeout", "timed out", "502", "503", "504", "rate limit")
    )


def refresh_cache(
    cache: BarCache,
    ticker: str,
    interval: str,
    lookback: str,
    *,
    daily_fn: Callable[..., CandlePayload],
    intrad_fn: Callable[..., CandlePayload] | None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Fetch history for (ticker, interval) and upsert it into the cache.

    Retries a transient provider failure with backoff; a non-transient one
    (or a final timeout) is raised for the caller to report.
    """

    def fetch() -> CandlePayload:
        if interval == "1d":
            return daily_fn(ticker, lookback=lookback)
        return intrad_fn(ticker, interval=interval, lookback=lookback)

    last: BaseException | None = None
    for attempt in range(len(_RETRY_DELAYS_S) + 1):
        try:
            payload = fetch()
        except Exception as exc:
            last = exc
            if attempt >= len(_RETRY_DELAYS_S) or not _is_transient(exc):
                raise
            sleep(_RETRY_DELAYS_S[attempt])
            continue
        return cache.upsert(ticker, interval, list(payload.observations))

    raise last  # pragma: no cover -- the loop either returns or raises above
