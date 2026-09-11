"""Local Direction price scores and classic overlays. No live PH."""

from __future__ import annotations

from typing import Any

import numpy as np

from Direction.bollinger_analyzer import detect_squeeze, get_bands
from Direction.elliott_wave import count_waves
from Direction.trend_engine import adx, ma_alignment
from shared.candlestick_chart import apply_position_gate
from shared.chart_data import CandleRecord

_ADX_TREND_THRESHOLD = 25.0
_MIN_SCORE_BARS = 50
_BB_WINDOW = 20


def _neutral_signals() -> dict[str, bool]:
    return {
        "whale": False,
        "wave3": False,
        "squeeze": False,
        "trend": False,
        "liquidity": False,
    }


def _closes(records: list[CandleRecord]) -> list[float]:
    return [float(record.close) for record in records]


def _ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    seed = float(np.mean(values[:period]))
    out[period - 1] = seed
    multiplier = 2.0 / (period + 1)
    prev = seed
    for index in range(period, len(values)):
        prev = values[index] * multiplier + prev * (1.0 - multiplier)
        out[index] = float(prev)
    return out


def _vwap(records: list[CandleRecord]) -> list[float | None]:
    out: list[float | None] = []
    cum_tpv = 0.0
    cum_vol = 0.0
    for record in records:
        volume = 1.0 if record.volume is None else float(record.volume)
        typical = (float(record.high) + float(record.low) + float(record.close)) / 3.0
        cum_tpv += typical * volume
        cum_vol += volume
        out.append(None if cum_vol == 0 else float(cum_tpv / cum_vol))
    return out


def _bb_overlays(closes: list[float], n: int) -> dict[str, list[float | None]]:
    mid: list[float | None] = [None] * n
    upper: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n
    bands = get_bands(closes, window=_BB_WINDOW)
    sma = bands.get("sma")
    up = bands.get("upper")
    lo = bands.get("lower")
    if sma is None or up is None or lo is None:
        return {"bb_mid": mid, "bb_upper": upper, "bb_lower": lower}
    offset = n - len(sma)
    for i, value in enumerate(sma):
        idx = offset + i
        if 0 <= idx < n:
            mid[idx] = float(value)
            upper[idx] = float(up[i])
            lower[idx] = float(lo[i])
    return {"bb_mid": mid, "bb_upper": upper, "bb_lower": lower}


def _vwap_bands(
    records: list[CandleRecord], vwap: list[float | None], mult: float = 1.0
) -> dict[str, list[float | None]]:
    """Volume-weighted standard-deviation envelope around session VWAP.

    The band the intraday book actually trades against: price outside +/-1
    sigma is the mean-reversion signal VWAP alone does not give you.
    """
    up: list[float | None] = []
    dn: list[float | None] = []
    cum_vol = 0.0
    cum_sq = 0.0
    for record, mean in zip(records, vwap, strict=False):
        volume = 1.0 if record.volume is None else float(record.volume)
        typical = (float(record.high) + float(record.low) + float(record.close)) / 3.0
        cum_vol += volume
        if mean is None or cum_vol <= 0:
            up.append(None)
            dn.append(None)
            continue
        cum_sq += volume * (typical - mean) ** 2
        sigma = (cum_sq / cum_vol) ** 0.5
        up.append(float(mean + mult * sigma))
        dn.append(float(mean - mult * sigma))
    return {"vwap_up": up, "vwap_dn": dn}


def _atr(records: list[CandleRecord], period: int = 14) -> list[float | None]:
    """Wilder ATR. None until `period` true ranges are available."""
    out: list[float | None] = [None] * len(records)
    if len(records) <= period:
        return out
    trs: list[float] = []
    prev_close = float(records[0].close)
    for record in records[1:]:
        high, low, close = float(record.high), float(record.low), float(record.close)
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        prev_close = close
    atr = float(np.mean(trs[:period]))
    out[period] = atr
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
        out[i + 1] = float(atr)
    return out


def _atr_channel(
    records: list[CandleRecord], basis: list[float | None], mult: float = 1.5
) -> dict[str, list[float | None]]:
    """EMA20 +/- 1.5 ATR -- a volatility-scaled channel that, unlike Bollinger,
    widens on gaps rather than only on close-to-close dispersion."""
    atr = _atr(records)
    up: list[float | None] = []
    dn: list[float | None] = []
    for mid, a in zip(basis, atr, strict=False):
        if mid is None or a is None:
            up.append(None)
            dn.append(None)
        else:
            up.append(float(mid + mult * a))
            dn.append(float(mid - mult * a))
    return {"atr_up": up, "atr_dn": dn}


def _prior_day_levels(records: list[CandleRecord]) -> dict[str, list[float | None]]:
    """Prior-session high/low carried forward as flat lines.

    Intraday only: on a daily/weekly series every bar is its own session, so
    the levels would just trace the previous bar and are returned empty.
    """
    pdh: list[float | None] = [None] * len(records)
    pdl: list[float | None] = [None] * len(records)
    if not records:
        return {"pdh": pdh, "pdl": pdl}
    dates = [record.timestamp.date() for record in records]
    sessions = len(set(dates))
    if sessions < 2 or sessions == len(dates):
        # < 2: a single session, so there is no prior one.
        # == len: one bar per session, i.e. a daily-or-slower series -- the
        # "prior session high/low" would just be the previous bar's own high
        # and low, which is noise dressed up as a level.
        return {"pdh": pdh, "pdl": pdl}
    # Running high/low per session, then look up the previous session's.
    session_hi: dict[Any, float] = {}
    session_lo: dict[Any, float] = {}
    order: list[Any] = []
    for record, day in zip(records, dates, strict=True):
        if day not in session_hi:
            session_hi[day] = float(record.high)
            session_lo[day] = float(record.low)
            order.append(day)
        else:
            session_hi[day] = max(session_hi[day], float(record.high))
            session_lo[day] = min(session_lo[day], float(record.low))
    prev_of = {day: order[i - 1] for i, day in enumerate(order) if i > 0}
    for i, day in enumerate(dates):
        prev = prev_of.get(day)
        if prev is not None:
            pdh[i] = session_hi[prev]
            pdl[i] = session_lo[prev]
    return {"pdh": pdh, "pdl": pdl}


def classic_overlays(records: list[CandleRecord]) -> dict[str, list[float | None]]:
    n = len(records)
    closes = _closes(records)
    ema20 = _ema(closes, 20)
    vwap = _vwap(records)
    overlays: dict[str, list[float | None]] = {
        "ema20": ema20,
        "ema50": _ema(closes, 50),
        # Added alongside the original three so the chart has trend context
        # beyond 50 bars and a volatility frame that is not just Bollinger.
        "ema200": _ema(closes, 200),
        "vwap": vwap,
    }
    overlays.update(_bb_overlays(closes, n))
    overlays.update(_vwap_bands(records, vwap))
    overlays.update(_atr_channel(records, ema20))
    overlays.update(_prior_day_levels(records))
    return overlays


# ---------------------------------------------------------------------------
# Oscillators
# ---------------------------------------------------------------------------
#
# These do NOT belong in classic_overlays: every value there is a price and
# shares the candle pane's y-axis. RSI is 0-100, CCI swings around zero in
# the hundreds, and MACD is in price-difference units -- plotting any of them
# against price would flatten the candles into a line. They ship as a
# separate `oscillators` block so the client can give each its own pane.


def _rsi(closes: list[float], period: int = 14) -> list[float | None]:
    """Wilder RSI. None until `period` changes are available."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))

    def rsi_of(gain: float, loss: float) -> float:
        # An all-up window has no downside to divide by; RSI is 100 there by
        # definition rather than a division error.
        if loss == 0:
            return 100.0
        rs = gain / loss
        return 100.0 - (100.0 / (1.0 + rs))

    out[period] = rsi_of(avg_gain, avg_loss)
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i + 1] = rsi_of(avg_gain, avg_loss)
    return out


def _cci(records: list[CandleRecord], period: int = 20) -> list[float | None]:
    """Commodity Channel Index over typical price, Lambert's 0.015 scaling."""
    out: list[float | None] = [None] * len(records)
    typical = [
        (float(r.high) + float(r.low) + float(r.close)) / 3.0 for r in records
    ]
    for i in range(period - 1, len(typical)):
        window = typical[i - period + 1 : i + 1]
        sma = float(np.mean(window))
        mean_dev = float(np.mean([abs(v - sma) for v in window]))
        # A perfectly flat window has zero mean deviation: CCI is undefined
        # (0/0), not infinite -- report 0 rather than a spike.
        out[i] = 0.0 if mean_dev == 0 else (typical[i] - sma) / (0.015 * mean_dev)
    return out


def _macd(
    closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> dict[str, list[float | None]]:
    """MACD line, signal line and histogram, all in price units."""
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    line: list[float | None] = [
        None if (f is None or sl is None) else float(f - sl)
        for f, sl in zip(ema_fast, ema_slow, strict=False)
    ]
    # The signal EMA runs on the MACD line itself, so it can only start once
    # that line does; feed it the defined tail and pad the front back out.
    defined = [v for v in line if v is not None]
    offset = len(line) - len(defined)
    sig_tail = _ema(defined, signal)
    sig: list[float | None] = [None] * offset + list(sig_tail)
    hist: list[float | None] = [
        None if (m is None or g is None) else float(m - g)
        for m, g in zip(line, sig, strict=False)
    ]
    return {"macd": line, "macd_signal": sig, "macd_hist": hist}


def oscillators(records: list[CandleRecord]) -> dict[str, list[float | None]]:
    """RSI(14), CCI(20) and MACD(12,26,9) for the bar series."""
    closes = _closes(records)
    out: dict[str, list[float | None]] = {
        "rsi": _rsi(closes),
        "cci": _cci(records),
    }
    out.update(_macd(closes))
    return out


def _price_legs(window: list[CandleRecord]) -> dict[str, bool]:
    if len(window) < _MIN_SCORE_BARS:
        return {"wave3": False, "squeeze": False, "trend": False}
    try:
        closes = _closes(window)
        highs = [float(record.high) for record in window]
        lows = [float(record.low) for record in window]
        wave3 = count_waves(closes).get("wave_type") == "impulse_wave_3"
        squeeze = detect_squeeze(get_bands(closes))
        adx_value = adx(highs, lows, closes)
        ma20 = (
            np.convolve(closes, np.ones(20) / 20, mode="valid")
            if len(closes) >= 20
            else np.array([])
        )
        ma50 = (
            np.convolve(closes, np.ones(50) / 50, mode="valid")
            if len(closes) >= 50
            else np.array([])
        )
        alignment = ma_alignment(closes, ma20, ma50)
        trend = adx_value > _ADX_TREND_THRESHOLD and alignment == "bullish"
        return {"wave3": bool(wave3), "squeeze": bool(squeeze), "trend": bool(trend)}
    except Exception:  # noqa: BLE001 — degrade to all-False; never raise
        return {"wave3": False, "squeeze": False, "trend": False}


def direction_coverage(records: list[CandleRecord]) -> dict[str, Any]:
    """Whether the Direction legs have enough bars to mean anything here.

    The legs are bar-count based, not time based: MA20/MA50, Bollinger(20),
    ADX and the Elliott wave count all need `_MIN_SCORE_BARS` bars before
    `_price_legs` will compute anything, and below that it returns all-False.
    That is indistinguishable on screen from "computed, and nothing fired".

    It bites hardest on the coarse timeframes, because intraday history is
    capped at 30 days (it is aggregated from one-minute rows): 4h yields only
    about two bars a session, so ~21 sessions is ~42 bars -- under the
    threshold, and every Direction leg is permanently dark. Reporting the
    shortfall lets the UI say "not enough history" instead of "no signal".
    """
    have = len(records)
    return {
        "bars": have,
        "min_bars": _MIN_SCORE_BARS,
        "active": have >= _MIN_SCORE_BARS,
        "shortfall": max(0, _MIN_SCORE_BARS - have),
    }


def price_scores(records: list[CandleRecord]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        signals = _neutral_signals()
        legs = _price_legs(records[: index + 1])
        signals["wave3"] = bool(legs["wave3"])
        signals["squeeze"] = bool(legs["squeeze"])
        signals["trend"] = bool(legs["trend"])
        rows.append(
            {
                "ts": record.timestamp.isoformat(),
                "score": int(sum(1 for value in signals.values() if value)),
                "signals": signals,
            }
        )
    return rows


def gated_markers(entries) -> list[str]:
    return apply_position_gate(entries)
