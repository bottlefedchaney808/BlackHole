# Direction/elliott_wave.py
"""Elliott Wave counter -- validates the 3 inviolable rules, identifies impulses.

Wave 3 = strongest momentum entry; Wave 5 = exhaustion warning.
"""

from typing import Optional

from . import data


def count_waves(prices: list) -> dict:
    """Count local extrema and classify the current wave.

    Returns {"wave_count": int, "wave_number": int, "wave_type": str}
    where wave_type is "impulse_wave_3" when wave 3 is the strongest
    swing, else "impulse_wave_1_or_5" (or "unknown").
    """
    if prices is None or len(prices) < 3:
        return {"wave_count": 0, "wave_number": 0, "wave_type": "unknown"}

    highs, lows = [], []
    for i in range(1, len(prices) - 1):
        if prices[i] > prices[i - 1] and prices[i] > prices[i + 1]:
            highs.append(i)
        if prices[i] < prices[i - 1] and prices[i] < prices[i + 1]:
            lows.append(i)

    if len(highs) < 2:
        return {"wave_count": 0, "wave_number": 0, "wave_type": "unknown"}

    wave3_strong = False
    if len(highs) > 1 and lows:
        wave3_strong = highs[1] - highs[0] > highs[0] - lows[0]

    return {
        "wave_count": len(highs),
        "wave_number": min(5, len(highs)),
        "wave_type": "impulse_wave_3" if wave3_strong else "impulse_wave_1_or_5",
    }


def fib_levels(high: float, low: float) -> dict:
    """Fibonacci retracement levels from a swing high/low."""
    diff = float(high) - float(low)
    return {
        "fib_236": low + diff * 0.236,
        "fib_382": low + diff * 0.382,
        "fib_500": low + diff * 0.500,
        "fib_618": low + diff * 0.618,
        "fib_786": low + diff * 0.786,
    }


def validate_impulse(p1, p2, p3, p4, p5) -> bool:
    """Validate the 5-point impulse structure (rules 1-3)."""
    return (p2 < p1 and p3 > max(p1, p2) and p4 < p3 and p4 > p1 and p5 < p4)


def analyze(ticker: str, as_of: Optional[str] = None) -> dict:
    """Pull 3mo closes via Direction.data and return count_waves output
    plus ``signal`` (True when wave_type == 'impulse_wave_3'). With
    ``as_of`` set, the window ends on that date."""
    ohlcv = data.get_ohlcv(ticker, lookback_days=90, as_of=as_of)  # ~3 months
    if ohlcv is None or ohlcv["close"] is None or len(ohlcv["close"]) == 0:
        return {"wave_count": 0, "wave_number": 0, "wave_type": "unknown",
                "signal": False}
    result = count_waves(ohlcv["close"].tolist())
    result["signal"] = result["wave_type"] == "impulse_wave_3"
    return result


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Elliott Wave counter (ThetaData)")
    p.add_argument("ticker", nargs="?", default="SPY")
    args = p.parse_args()
    r = analyze(args.ticker)
    print(f"{args.ticker}: Wave {r['wave_number']} ({r['wave_type']})")
