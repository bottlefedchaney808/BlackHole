# Direction/signal_generator.py
"""signal_generator -- unified Direction signal: all 5 inputs, one conviction.

Runs the five independent Direction modules (whale flow, Elliott Wave,
Bollinger, multi-timeframe trend, liquidity zones) and combines them into a
single conviction call. ThetaData is the only data source.

Conviction rules (EXACT):
  HIGH   = whale AND wave3 AND (squeeze OR trend) AND score >= 3
  MEDIUM = whale AND score >= 3 AND not HIGH
  NONE   = otherwise (no whale signal = no trade)

`score` is the number of the five signals that fired (0-5).
"""

from __future__ import annotations

import argparse
from typing import Optional

from . import bollinger_analyzer, data, elliott_wave, liquidity_map, trend_engine, whale_scanner


def _sig(result) -> bool:
    """Defensively map a module output dict to its boolean signal."""
    if isinstance(result, dict):
        return bool(result.get("signal", False))
    return False


def generate(ticker: str, as_of: Optional[str] = None) -> dict:
    """Run all five modules and combine into a conviction call.

    Returns {"ticker", "price", "signals": {"whale","wave3","squeeze",
    "trend","liquidity": bool}, "score": int (0-5), "conviction":
    "HIGH"|"MEDIUM"|"NONE", "as_of": str|None, "details": {...}}.
    With ``as_of`` set, every module evaluates that day's data.
    """
    try:
        whale = whale_scanner.scan(ticker, as_of=as_of)
    except Exception:
        whale = {}
    try:
        elliott = elliott_wave.analyze(ticker, as_of=as_of)
    except Exception:
        elliott = {}
    try:
        bollinger = bollinger_analyzer.analyze(ticker, as_of=as_of)
    except Exception:
        bollinger = {}
    try:
        trend = trend_engine.analyze_trend(ticker, as_of=as_of)
    except Exception:
        trend = {}
    try:
        liquidity = liquidity_map.get_liquidity(ticker, as_of=as_of)
    except Exception:
        liquidity = {}

    signals = {
        "whale": _sig(whale),
        "wave3": _sig(elliott),
        "squeeze": _sig(bollinger),
        "trend": _sig(trend),
        "liquidity": _sig(liquidity),
    }
    score = sum(1 for v in signals.values() if v)

    price = None
    if isinstance(whale, dict) and whale.get("price") is not None:
        try:
            price = float(whale["price"])
        except (TypeError, ValueError):
            price = None
    if price is None:
        try:
            price = data.get_price(ticker)
        except Exception:
            price = None

    if (signals["whale"] and signals["wave3"]
            and (signals["squeeze"] or signals["trend"]) and score >= 3):
        conviction = "HIGH"
    elif signals["whale"] and score >= 3:
        conviction = "MEDIUM"
    else:
        conviction = "NONE"

    details = {
        "whale": whale if isinstance(whale, dict) else {},
        "elliott": elliott if isinstance(elliott, dict) else {},
        "bollinger": bollinger if isinstance(bollinger, dict) else {},
        "trend": trend if isinstance(trend, dict) else {},
        "liquidity": liquidity if isinstance(liquidity, dict) else {},
    }

    return {
        "ticker": ticker,
        "price": price,
        "signals": signals,
        "score": score,
        "conviction": conviction,
        "as_of": as_of,
        "details": details,
    }


def main(argv=None) -> int:
    """CLI: python Direction.py NVDA | python -m Direction.signal_generator --ticker NVDA"""
    p = argparse.ArgumentParser(
        description="Unified Direction signal (ThetaData)")
    p.add_argument("ticker", nargs="?", default=None,
                   help="equity symbol (positional, e.g. NVDA)")
    p.add_argument("--ticker", dest="ticker_flag", default=None,
                   help="equity symbol (flag, default: SPY)")
    args = p.parse_args(argv)
    ticker = args.ticker_flag or args.ticker or "SPY"

    r = generate(ticker)
    price_str = f" | price ${r['price']:.2f}" if r["price"] is not None else ""
    print(f"{r['ticker']}: {r['conviction']} | score {r['score']}/5{price_str}")
    for name in ("whale", "wave3", "squeeze", "trend", "liquidity"):
        print(f"  {name:<9} {'ON' if r['signals'][name] else 'off'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
