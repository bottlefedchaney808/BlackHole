# Direction/bollinger_analyzer.py
"""Bollinger Bands analyzer -- squeeze, thrust, and regime detection.

Squeeze (width < 2% of price) = volatile expansion imminent.
Band thrust through upper/lower = momentum continuation.
"""

import numpy as np

from . import data


def get_bands(prices, window: int = 20) -> dict:
    """Return {"sma", "upper", "lower", "width"} arrays (aligned, valid region)."""
    if prices is None or len(prices) < window:
        return {"sma": np.array([]), "upper": np.array([]),
                "lower": np.array([]), "width": np.array([])}
    prices = np.asarray(prices, dtype=float)
    sma = np.convolve(prices, np.ones(window) / window, mode="valid")
    std = np.array([np.std(prices[i:i + window])
                    for i in range(len(prices) - window + 1)])
    upper = sma + 2 * std
    lower = sma - 2 * std
    return {"sma": sma, "upper": upper, "lower": lower, "width": upper - lower}


def detect_squeeze(bands: dict, threshold_pct: float = 0.02) -> bool:
    """True when the latest band width is below threshold_pct of price."""
    width = bands.get("width")
    sma = bands.get("sma")
    if width is None or sma is None or len(width) == 0 or len(sma) == 0:
        return False
    if sma[-1] == 0:
        return False
    return bool((width[-1] / sma[-1]) < threshold_pct)


def regime(bands: dict, price: float) -> str:
    """Classify %B position: upper_thrust_bullish | lower_thrust_bearish |
    bullish | bearish | neutral."""
    upper = bands.get("upper")
    lower = bands.get("lower")
    if upper is None or lower is None or len(upper) == 0 or len(lower) == 0:
        return "neutral"
    band_range = upper[-1] - lower[-1]
    if band_range <= 0:
        return "neutral"
    bbp = (price - lower[-1]) / band_range
    if bbp > 0.90:
        return "upper_thrust_bullish"
    if bbp < 0.10:
        return "lower_thrust_bearish"
    if bbp > 0.60:
        return "bullish"
    if bbp < 0.40:
        return "bearish"
    return "neutral"


def analyze(ticker: str) -> dict:
    """3mo closes -> {"squeeze": bool, "regime": str, "signal": bool}.
    signal = squeeze OR regime in (upper_thrust_bullish, lower_thrust_bearish)."""
    ohlcv = data.get_ohlcv(ticker, lookback_days=90)  # ~3 months
    if ohlcv is None or ohlcv["close"] is None or len(ohlcv["close"]) == 0:
        return {"squeeze": False, "regime": "neutral", "signal": False}
    closes = ohlcv["close"]
    bands = get_bands(closes)
    sqz = detect_squeeze(bands)
    reg = regime(bands, float(closes[-1]))
    return {
        "squeeze": sqz,
        "regime": reg,
        "signal": sqz or reg in ("upper_thrust_bullish", "lower_thrust_bearish"),
    }


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Bollinger Bands analyzer (ThetaData)")
    p.add_argument("ticker", nargs="?", default="SPY")
    args = p.parse_args()
    r = analyze(args.ticker)
    print(f"{args.ticker}: {'SQUEEZE' if r['squeeze'] else 'Normal'} | "
          f"Regime: {r['regime']}")
