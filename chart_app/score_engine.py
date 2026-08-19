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


def classic_overlays(records: list[CandleRecord]) -> dict[str, list[float | None]]:
    n = len(records)
    closes = _closes(records)
    overlays = {
        "ema20": _ema(closes, 20),
        "ema50": _ema(closes, 50),
        "vwap": _vwap(records),
    }
    overlays.update(_bb_overlays(closes, n))
    return overlays


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
