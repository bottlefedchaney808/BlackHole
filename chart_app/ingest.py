"""Fill the local bar cache from injected candle fetchers. No live PH."""

from __future__ import annotations

import time
from collections.abc import Callable

from chart_app.bar_cache import BarCache
from chart_app.crypto_source import fetch_crypto_candles, is_crypto
from shared.chart_data import CandlePayload

# CORRECTED 2026-09-19. This used to read "a 20-30 day pull is a large request
# and the provider intermittently times out under load". That was wrong about
# the cause, and the retry was papering over OUR bug: PHClient built its
# httpx.Client with no timeout and inherited httpx's 5.0s default, so anything
# needing more than five seconds failed and got filed as provider load. With
# the timeout raised (shared/thetadata.py::_apply_http_timeout) the same spans
# return in 0.3-0.5s. See CLAUDE.md, "the five-second lie".
#
# The retry STAYS, for the reasons that are still real: the vendor's
# LARGE_REQUEST ceiling, genuine 502s under load, and ordinary network blips.
# A failure used to end the refresh with zero bars cached, which on a
# timeframe with no prior cache is indistinguishable from "this interval has
# no data" -- an empty chart with no explanation. It should just fire far less
# often now. If you see it firing constantly, check the elapsed time before
# blaming the vendor: ~5.0s means the timeout is not being applied.
_RETRY_DELAYS_S = (1.5, 4.0)


def _is_transient(exc: BaseException) -> bool:
    """Whether an exception is worth retrying rather than reporting.

    Matched on the message because the provider error (`PHTimeoutError`) is
    wrapped in `ChartDataError` by shared/spot_history.py before it gets
    here, so the concrete type is gone by this point. A validation failure
    (bad ticker, unsupported interval, non-positive lookback) must NOT be
    retried -- it will fail identically every time and just delays the real
    error reaching the caller.

    Note "transient" is NOT a synonym for "the provider is busy". A timeout
    here was, for a long time, our own 5.0s httpx default; retrying it three
    times just meant waiting 5.5s longer to report the wrong cause.
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
        # Crypto and perps bypass the injected ThetaData fetchers entirely.
        # ThetaData is an equities feed and will answer for a symbol like
        # `BTC` with the Grayscale Mini Trust ETF -- a real instrument at a
        # real price that is simply not bitcoin. Routing on the ticker shape
        # (`BTC-PERP`, `ETH-USD`) keeps that confusion impossible rather than
        # merely unlikely. See chart_app/crypto_source.py.
        if is_crypto(ticker):
            return fetch_crypto_candles(ticker, interval=interval, lookback=lookback)
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
