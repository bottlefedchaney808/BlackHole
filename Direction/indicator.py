# Direction/indicator.py
"""Per-bar price signals for the v2 direction indicator.

Task 2 of the v2 plan: evaluate the three price-series signals -- Elliott
wave3, Bollinger squeeze, multi-TF trend -- on the intraday series as of a
bar timestamp, using ONLY the PURE functions of the Direction modules
(``count_waves``, ``get_bands``/``detect_squeeze``, ``adx``/``ma_alignment``).
The module entry points (``analyze``/``analyze_trend``) are intentionally
NOT used, and ``elliott_wave.py``/``bollinger_analyzer.py``/``trend_engine.py``
are not modified.

Every bar of a chart is evaluated with the series as it existed up to that
bar (closed-bar semantics via ``shared.spot_history.intraday_bars_as_of``),
so verdicts move bar-to-bar -- the whole point of v2.
"""

from __future__ import annotations

from typing import Callable

import numpy as np

from shared.chart_data import CandleRecord
from shared.spot_history import intraday_bars_as_of

from . import bollinger_analyzer as boll
from . import elliott_wave as ew
from . import trend_engine as te

# Same thresholds the module entry points use:
#   bollinger_analyzer.analyze: detect_squeeze default threshold_pct=0.02
#   trend_engine.analyze_trend: adx_ok = daily["adx"] > 25
_ADX_TREND_THRESHOLD = 25.0

# Below this many bars nothing meaningful computes (count_waves needs >= 3;
# bands need 20; MA50 needs 50) -- degrade to neutral instead of fabricating.
_MIN_BARS = 3

OHLCVFn = Callable[[str, str, object], list[CandleRecord]]


def _price_signals(ticker, bar_ts, interval: str = "15m",
                   ohlcv_fn: OHLCVFn | None = None) -> dict:
    """Evaluate the three price-series signals on the intraday series as of
    ``bar_ts``.

    Returns ``{"wave3": bool, "squeeze": bool, "trend": bool}`` where:
      wave3   -- ``count_waves(closes)["wave_type"] == "impulse_wave_3"``
                 (the strongest swing is wave 3; same definition
                 ``elliott_wave.analyze`` uses for its ``signal`` key).
      squeeze -- ``detect_squeeze(get_bands(closes))``: latest band width
                 below 2% of price (default threshold, as the entry point).
      trend   -- ADX > 25 AND ``ma_alignment`` == "bullish" (price >
                 ma20 > ma50); the intraday single-timeframe analogue of
                 ``trend_engine.analyze_trend``'s ``adx_ok`` + alignment.

    ``ohlcv_fn`` is injectable for deterministic, network-free tests and
    defaults to ``shared.spot_history.intraday_bars_as_of`` (bars with
    ``timestamp <= bar_ts``, RTH-filtered, ascending). It is called as
    ``ohlcv_fn(ticker, interval, bar_ts)``.

    Degrades to all-False -- NEVER raises, NEVER fabricates -- when the
    provider fails, returns no bars, or the series is too short for the
    indicator in question (bands need >= 20 bars, MA50 needs >= 50; each
    pure function already reports neutral on short input).
    """
    provider = ohlcv_fn if ohlcv_fn is not None else intraday_bars_as_of
    try:
        bars = provider(ticker, interval, bar_ts)
    except Exception:
        bars = []
    if not bars or len(bars) < _MIN_BARS:
        return {"wave3": False, "squeeze": False, "trend": False}

    closes = [float(bar.close) for bar in bars]
    highs = [float(bar.high) for bar in bars]
    lows = [float(bar.low) for bar in bars]

    wave3 = ew.count_waves(closes).get("wave_type") == "impulse_wave_3"
    squeeze = boll.detect_squeeze(boll.get_bands(closes))

    adx_value = te.adx(highs, lows, closes)
    ma20 = (np.convolve(closes, np.ones(20) / 20, mode="valid")
            if len(closes) >= 20 else np.array([]))
    ma50 = (np.convolve(closes, np.ones(50) / 50, mode="valid")
            if len(closes) >= 50 else np.array([]))
    alignment = te.ma_alignment(closes, ma20, ma50)
    trend = adx_value > _ADX_TREND_THRESHOLD and alignment == "bullish"

    return {"wave3": wave3, "squeeze": squeeze, "trend": trend}
