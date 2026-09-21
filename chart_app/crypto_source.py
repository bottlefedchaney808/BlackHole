"""Crypto and perpetual-swap candles, for symbols ThetaData does not carry.

ThetaData is an equities/options feed. It will happily answer for the ticker
`BTC` -- and the answer is wrong for our purposes: `BTC` is the Grayscale
Bitcoin Mini Trust ETF, which printed $33.82 on 2026-09-17. Charting that and
calling it bitcoin is exactly the class of silent-wrong-number this repo keeps
getting bitten by, so crypto gets its own route and its own naming.

Venue choice (probed from this host, 2026-09-18)
-----------------------------------------------
    binance perp/spot   HTTP 451   geo-blocked
    bybit    perp       HTTP 403   geo-blocked
    okx      swap       OK         <- real perpetuals
    coinbase spot       OK
    binance.us spot     OK
    kraken   spot       OK

So **OKX is the perp source** (`*-SWAP` instruments are genuine perpetuals)
and Coinbase is the spot fallback. All are public, unauthenticated, read-only
market-data endpoints -- no key, no order path, nothing that can place a
trade. Venues are tried in order and the first that answers wins; the payload
records which one did in `CandlePayload.source`, because "BTC-PERP was priced
off Coinbase spot" is a materially different chart from "off the OKX swap".

Symbol convention
-----------------
    BTC-PERP            perpetual swap, quoted in USDT   (OKX BTC-USDT-SWAP)
    ETH-PERP, SOL-PERP, ...
    BTC-USD             spot
    BTC-USDT-SWAP       pass-through, any OKX instrument id

`-PERP` and `-USD` both satisfy `shared/spot_history.validate_ticker`'s
`^[A-Z0-9]+(?:[.-][A-Z0-9]+)*$`, so these drop into the existing ticker box
with no validator change.

Crypto trades 24/7. Session-based logic elsewhere in the app (prior-day
high/low, the 45-minute session-gap rule in `flow_pane`) degrades gracefully
rather than breaking: with no gaps larger than the bar interval, every bar is
simply treated as continuous, which for a 24/7 tape is correct.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from shared.chart_data import (
    CandlePayload,
    CandleQuality,
    CandleRecord,
    ChartDataError,
)

_UA = {"User-Agent": "Mozilla/5.0 (chart_app crypto_source)"}
_TIMEOUT_S = 15.0

# Paging budget. 300 rows a page is what both OKX candle routes serve, and 500
# pages is 150,000 bars -- about 4 years of 15m. This is a runaway guard, not a
# data limit: when it binds, the payload says so in `quality.warnings` instead
# of returning a short series that looks complete.
_PAGE_LIMIT = 300
_MAX_PAGES = 500
_PAGE_SLEEP_S = 0.12

# Our interval -> (OKX bar, Coinbase granularity seconds, minutes)
_INTERVALS: dict[str, tuple[str, int, int]] = {
    "3m": ("3m", 180, 3),
    "5m": ("5m", 300, 5),
    "10m": ("5m", 300, 10),   # OKX has no 10m; aggregated 2:1 from 5m
    "15m": ("15m", 900, 15),
    "30m": ("30m", 1800, 30),
    "1h": ("1H", 3600, 60),
    "4h": ("4H", 14400, 240),
    "1d": ("1D", 86400, 1440),
}

# Bare coin -> the OKX perp instrument. Extend freely; anything not here can
# still be charted by typing the full instrument id.
_PERP_ALIASES: dict[str, str] = {
    "BTC": "BTC-USDT-SWAP",
    "ETH": "ETH-USDT-SWAP",
    "SOL": "SOL-USDT-SWAP",
    "XRP": "XRP-USDT-SWAP",
    "DOGE": "DOGE-USDT-SWAP",
    "AVAX": "AVAX-USDT-SWAP",
    "LINK": "LINK-USDT-SWAP",
    "LTC": "LTC-USDT-SWAP",
    "BNB": "BNB-USDT-SWAP",
    "ADA": "ADA-USDT-SWAP",
    "SUI": "SUI-USDT-SWAP",
    "TON": "TON-USDT-SWAP",
}

_QUOTES = ("USD", "USDT", "USDC")


def is_crypto(ticker: str) -> bool:
    """Whether this ticker should be routed away from ThetaData."""
    t = (ticker or "").strip().upper()
    if not t:
        return False
    if t.endswith(("-PERP", "-SWAP")):
        return True
    if "-" in t:
        head, _, tail = t.rpartition("-")
        return bool(head) and tail in _QUOTES
    return False


def resolve(ticker: str) -> dict[str, str]:
    """Split a chart ticker into what each venue needs.

    Returns `{"kind", "base", "okx", "coinbase"}`. `kind` is `"perp"` or
    `"spot"`; either venue key may be empty when that venue cannot serve it.
    """
    t = (ticker or "").strip().upper()
    if t.endswith("-SWAP"):
        base = t.split("-")[0]
        return {"kind": "perp", "base": base, "okx": t, "coinbase": f"{base}-USD"}
    if t.endswith("-PERP"):
        base = t[: -len("-PERP")]
        return {
            "kind": "perp",
            "base": base,
            "okx": _PERP_ALIASES.get(base, f"{base}-USDT-SWAP"),
            "coinbase": f"{base}-USD",
        }
    head, _, quote = t.rpartition("-")
    base = head or t
    return {
        "kind": "spot",
        "base": base,
        "okx": f"{base}-{'USDT' if quote in ('USDT', '') else quote}",
        "coinbase": f"{base}-USD" if quote in ("USD", "USDC", "") else f"{base}-{quote}",
    }


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _get_json(url: str) -> Any:
    request = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
        return json.loads(response.read().decode("utf-8"))


def _bars_wanted(interval: str, lookback: str) -> int:
    """How many bars a lookback string implies, so paging knows when to stop."""
    amount, unit = 30, "d"
    raw = (lookback or "30d").strip().lower()
    for suffix in ("d", "w", "m", "y"):
        if raw.endswith(suffix):
            try:
                amount, unit = int(raw[:-1]), suffix
            except ValueError:
                amount, unit = 30, "d"
            break
    days = {"d": 1, "w": 7, "m": 30, "y": 365}[unit] * amount
    minutes = _INTERVALS.get(interval, ("", 0, 15))[2]
    # 24/7: 1440 minutes a day, not 390.
    #
    # There used to be a `min(6000, ...)` here, uncommented. It meant every
    # crypto request capped at 6,000 bars: asking for 90d and asking for 1y
    # both returned the same 62 days of 15m, and nothing said so -- a backtest
    # would simply score a window it never asked for. The venue was never the
    # limit; driven by hand, OKX paged straight back through `history-candles`
    # 300 rows at a time. Same shape as the httpx 5s default (CLAUDE.md): our
    # own ceiling, read as the provider's. The runaway guard now lives in
    # `_okx_rows` as an explicit page budget, and a short answer is REPORTED
    # via `CandleQuality.warnings` rather than passed off as a full one.
    return max(60, int(days * 1440 / max(1, minutes)))


# ---------------------------------------------------------------------------
# Venues
# ---------------------------------------------------------------------------


def _okx_rows(inst: str, interval: str, want: int) -> list[dict[str, Any]]:
    """OKX candles, paged backwards through `history-candles`.

    Rows are `[ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]`, newest
    first. `confirm == "0"` marks a bar that is still forming; it is dropped,
    because a partially-formed bar has a high and low that will still move and
    would make the last candle on the chart lie about its own range.

    `volCcy` (base-currency volume) is used, not `vol` (contract count): the
    Hui-Heubel liquidity ratio wants units of the thing being traded, and a
    contract multiplier differs per instrument.
    """
    bar = _INTERVALS[interval][0]
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    after = ""
    base = "https://www.okx.com/api/v5"
    endpoint = "candles"
    pages = 0
    while len(out) < want and pages < _MAX_PAGES:
        url = f"{base}/market/{endpoint}?instId={inst}&bar={bar}&limit={_PAGE_LIMIT}"
        if after:
            url += f"&after={after}"
        payload = _get_json(url)
        if str(payload.get("code")) != "0":
            raise ChartDataError(f"okx {inst}: {payload.get('msg') or payload.get('code')}")
        rows = payload.get("data") or []
        if not rows:
            break
        before = len(out)
        for row in rows:
            ts_ms = int(row[0])
            if ts_ms in seen:
                continue
            if len(row) > 8 and str(row[8]) == "0":
                continue  # unconfirmed, still-forming bar
            seen.add(ts_ms)
            out.append(
                {
                    "timestamp": datetime.fromtimestamp(
                        ts_ms / 1000.0, tz=UTC
                    ).replace(tzinfo=None),
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[6]) if len(row) > 6 else float(row[5]),
                }
            )
        pages += 1
        # Nothing new on a full page means the venue is repeating itself or has
        # run out of history. Stopping on "fewer rows than asked for" instead
        # (what this did) breaks a pull the moment a page comes back short for
        # any reason, which is how a deep request quietly became a shallow one.
        if len(out) == before:
            break
        after = str(int(rows[-1][0]))
        # `/candles` serves only the recent window; deeper pages come from
        # `/history-candles`. Both accept limit=300 -- measured 2026-09-19,
        # 300 rows returned from each.
        endpoint = "history-candles"
        # Paced, not parallel. OKX allows ~20 requests / 2s on this route and a
        # deep pull is hundreds of pages; the repo rule against fanning out
        # applies to free endpoints too, because getting throttled mid-pull
        # produces a short series, and a short series here is a wrong backtest.
        time.sleep(_PAGE_SLEEP_S)
    return out


def _coinbase_rows(product: str, interval: str, want: int) -> list[dict[str, Any]]:
    """Coinbase Exchange candles: `[time, low, high, open, close, volume]`.

    Note the column order -- low and high come *before* open and close, which
    is not the order any other venue uses and is a reliable source of silently
    transposed candles.
    """
    granularity = _INTERVALS[interval][1]
    if granularity not in (60, 300, 900, 3600, 21600, 86400):
        raise ChartDataError(f"coinbase has no {interval} granularity")
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    end = datetime.now(UTC)
    while len(out) < want:
        start = end - timedelta(seconds=granularity * 300)
        url = (
            f"https://api.exchange.coinbase.com/products/{product}/candles"
            f"?granularity={granularity}"
            f"&start={start.isoformat()}&end={end.isoformat()}"
        )
        rows = _get_json(url)
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            ts = int(row[0])
            if ts in seen:
                continue
            seen.add(ts)
            out.append(
                {
                    "timestamp": datetime.fromtimestamp(ts, tz=UTC).replace(
                        tzinfo=None
                    ),
                    "low": float(row[1]),
                    "high": float(row[2]),
                    "open": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]),
                }
            )
        end = start
        if len(rows) < 2:
            break
    return out


def _aggregate(rows: Sequence[dict[str, Any]], factor: int) -> list[dict[str, Any]]:
    """Fold `factor` consecutive bars into one (for 10m off a 5m feed)."""
    if factor <= 1:
        return list(rows)
    ordered = sorted(rows, key=lambda r: r["timestamp"])
    out: list[dict[str, Any]] = []
    for i in range(0, len(ordered) - factor + 1, factor):
        chunk = ordered[i : i + factor]
        volumes = [c.get("volume") for c in chunk]
        out.append(
            {
                "timestamp": chunk[-1]["timestamp"],
                "open": chunk[0]["open"],
                "high": max(c["high"] for c in chunk),
                "low": min(c["low"] for c in chunk),
                "close": chunk[-1]["close"],
                "volume": None if any(v is None for v in volumes) else sum(volumes),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Public entry points -- signature-compatible with shared.spot_history
# ---------------------------------------------------------------------------


def fetch_crypto_candles(
    ticker: str, *, interval: str = "15m", lookback: str = "30d"
) -> CandlePayload:
    """Candles for a crypto/perp ticker, first venue that answers.

    Signature matches `shared.spot_history.fetch_intraday_candles` so it drops
    straight into `chart_app.ingest.refresh_cache`'s injected-fetcher slot.
    """
    if interval not in _INTERVALS:
        raise ChartDataError(f"unsupported crypto interval {interval!r}")
    target = resolve(ticker)
    want = _bars_wanted(interval, lookback)
    factor = 2 if interval == "10m" else 1
    fetch_interval = "5m" if interval == "10m" else interval

    errors: list[str] = []
    attempts: list[tuple[str, Any]] = []
    if target["okx"]:
        attempts.append(("okx", lambda: _okx_rows(target["okx"], fetch_interval, want * factor)))
    if target["coinbase"]:
        attempts.append(
            ("coinbase", lambda: _coinbase_rows(target["coinbase"], fetch_interval, want * factor))
        )

    for source, call in attempts:
        try:
            rows = call()
        except (urllib.error.URLError, urllib.error.HTTPError, ChartDataError, ValueError, KeyError, TimeoutError) as exc:
            errors.append(f"{source}: {type(exc).__name__}: {str(exc)[:90]}")
            continue
        if not rows:
            errors.append(f"{source}: no rows")
            continue
        rows = _aggregate(rows, factor)
        rows.sort(key=lambda r: r["timestamp"])
        observations = tuple(
            CandleRecord(
                timestamp=r["timestamp"],
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                volume=None if r.get("volume") is None else float(r["volume"]),
            )
            for r in rows[-want:]
        )
        if not observations:
            errors.append(f"{source}: empty after trim")
            continue
        # A venue that simply does not go back as far as the caller asked is a
        # normal outcome -- a perp listed last year cannot serve three. What is
        # NOT acceptable is returning it silently, because the consumer is a
        # backtest that will report the short window's result as the answer to
        # the question it asked. Under-delivery is stated here, in the payload.
        warnings: tuple[str, ...] = ()
        if len(observations) < want:
            span_days = (
                observations[-1].timestamp - observations[0].timestamp
            ).days
            warnings = (
                (
                    f"asked {want} bars for {lookback}, served "
                    f"{len(observations)} ({span_days}d from "
                    f"{observations[0].timestamp.date()}) -- {source} has no "
                    f"more history at {interval}"
                ),
            )
        return CandlePayload(
            quality=CandleQuality(row_count=len(observations), warnings=warnings),
            ticker=ticker.strip().upper(),
            interval=interval,
            lookback=lookback,
            # Naming the venue matters: "BTC-PERP off coinbase spot" and
            # "off the OKX swap" are different instruments with different
            # basis, and the UI shows this string.
            source=f"crypto:{source}:{target['okx'] if source == 'okx' else target['coinbase']}",
            observations=observations,
        )

    raise ChartDataError(
        f"no crypto venue served {ticker} {interval}: " + "; ".join(errors)
    )


def fetch_crypto_daily(ticker: str, *, lookback: str = "1y") -> CandlePayload:
    """Daily candles. Mirrors `shared.spot_history.fetch_daily_candles`."""
    return fetch_crypto_candles(ticker, interval="1d", lookback=lookback)


def known_symbols() -> list[str]:
    """Perp tickers the UI can offer without the user knowing OKX ids."""
    return [f"{base}-PERP" for base in sorted(_PERP_ALIASES)]
