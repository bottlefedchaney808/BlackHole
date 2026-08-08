# Direction/trend_engine.py
"""Multi-timeframe trend engine -- ADX + MA20/MA50 alignment (daily/weekly/monthly).

ADX > 25 = confirmed trend. Price > MA20 > MA50 = uptrend confirmed.
Weekly/Daily must align for high conviction.
"""

import numpy as np

from . import data


def adx(high, low, close, period: int = 14) -> float:
    """ADX-style directional strength in [0, 100].

    plus_dm  = max(high - prev_high, 0)
    minus_dm = max(prev_low - low, 0)
    tr       = max(high - low, |close - prev_close|)
    plus_di  = 100 * mean(plus_dm[-period:]) / (mean(tr[-period:]) + 1e-9)
    minus_di = 100 * mean(minus_dm[-period:]) / (mean(tr[-period:]) + 1e-9)
    adx      = plus_di / (plus_di + minus_di + 1e-9) * 100
    """
    if high is None or low is None or close is None:
        return 0.0
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    if len(high) < 2 or len(high) != len(low) or len(high) != len(close):
        return 0.0

    plus_dm = np.maximum(high[1:] - high[:-1], 0)
    minus_dm = np.maximum(low[:-1] - low[1:], 0)
    tr = np.maximum(high[1:] - low[1:], np.abs(close[1:] - close[:-1]))
    plus_di = 100 * np.mean(plus_dm[-period:]) / (np.mean(tr[-period:]) + 1e-9)
    minus_di = 100 * np.mean(minus_dm[-period:]) / (np.mean(tr[-period:]) + 1e-9)
    return float(plus_di / (plus_di + minus_di + 1e-9) * 100)


def ma_alignment(prices, ma20, ma50) -> str:
    """'bullish' | 'bearish' | 'mixed' from price > ma20 > ma50 ordering."""
    if prices is None or ma20 is None or ma50 is None:
        return "mixed"
    if len(prices) == 0 or len(ma20) == 0 or len(ma50) == 0:
        return "mixed"
    if prices[-1] > ma20[-1] > ma50[-1]:
        return "bullish"
    if prices[-1] < ma20[-1] < ma50[-1]:
        return "bearish"
    return "mixed"


def _timeframe(d) -> dict:
    """One timeframe's {"adx": float, "ma": str} from an OHLCV dict (or None)."""
    if d is None or d["close"] is None or len(d["close"]) == 0:
        return {"adx": 0.0, "ma": "mixed"}
    closes = np.asarray(d["close"], dtype=float)
    a = adx(d["high"], d["low"], d["close"])
    ma20 = (np.convolve(closes, np.ones(20) / 20, mode="valid")
            if len(closes) >= 20 else np.array([]))
    ma50 = (np.convolve(closes, np.ones(50) / 50, mode="valid")
            if len(closes) >= 50 else np.array([]))
    return {"adx": float(a), "ma": ma_alignment(closes, ma20, ma50)}


def _neutral_trend() -> dict:
    """Zeroed result used for graceful degradation (fetch failure / no data)."""
    return {
        "daily": {"adx": 0.0, "ma": "mixed"},
        "weekly": {"adx": 0.0, "ma": "mixed"},
        "monthly": {"adx": 0.0, "ma": "mixed"},
        "adx_ok": False,
        "aligned": False,
        "signal": False,
    }


def analyze_trend(ticker: str) -> dict:
    """Resample daily ThetaData OHLCV to daily/weekly/monthly and return
    {"daily": {...}, "weekly": {...}, "monthly": {...}, "adx_ok": bool,
     "aligned": bool, "signal": bool} where aligned = adx_ok AND daily/weekly
     agree."""
    daily_data = data.get_ohlcv(ticker, lookback_days=180)
    if daily_data is None or daily_data["close"] is None or len(daily_data["close"]) == 0:
        return _neutral_trend()

    weekly_data = data.resample_ohlcv(daily_data, "W")
    monthly_data = data.resample_ohlcv(daily_data, "M")

    daily = _timeframe(daily_data)
    weekly = _timeframe(weekly_data)
    monthly = _timeframe(monthly_data)

    adx_ok = daily["adx"] > 25
    aligned = adx_ok and daily["ma"] != "mixed" and weekly["ma"] == daily["ma"]
    return {"daily": daily, "weekly": weekly, "monthly": monthly,
            "adx_ok": adx_ok, "aligned": aligned, "signal": aligned}


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Multi-timeframe trend engine (ThetaData)")
    p.add_argument("ticker", nargs="?", default="SPY")
    args = p.parse_args()
    r = analyze_trend(args.ticker)
    for tf in ("daily", "weekly", "monthly"):
        print(f"{tf.upper()}: ADX={r[tf]['adx']:.1f} | MA={r[tf]['ma']}")
