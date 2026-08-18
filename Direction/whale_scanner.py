# Direction/whale_scanner.py
"""Direction/whale_scanner.py -- live ThetaData-backed whale-flow scanner.

Live wrapper around Vol_Suite/whale_scanner.py's classify_whale_bias -- the
same pure classification function backtest_stage3.py's whale-flow backtest
column already uses and Vol_Suite/tests/test_whale_scanner.py already
exercises -- rather than a second copy of the threshold/ratio math. This
module's only job is to fetch one day's chain rows via Direction.data and
hand them to that already-tested classifier, then reshape the result into
this package's scan() contract (ticker/price/whale_calls/whale_puts/
call_premium/put_premium/direction/expiry/signal) so it matches the other
four Direction modules' analyze()-shaped output.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Optional

from . import data

_VOL_SUITE_ROOT = Path(__file__).resolve().parent.parent / "Vol_Suite"
if str(_VOL_SUITE_ROOT) not in sys.path:
    sys.path.append(str(_VOL_SUITE_ROOT))

import whale_scanner as _vs_whale_scanner  # noqa: E402 -- Vol_Suite's already-tested classifier

WHALE_THRESHOLD = _vs_whale_scanner.WHALE_THRESHOLD


def _neutral_result(ticker: str) -> dict:
    return {
        "ticker": ticker,
        "price": None,
        "whale_calls": 0,
        "whale_puts": 0,
        "call_premium": 0.0,
        "put_premium": 0.0,
        "direction": "neutral",
        "expiry": None,
        "signal": False,
    }


def scan(ticker: str, min_premium: float = WHALE_THRESHOLD,
         threshold_bps: Optional[float] = None,
         as_of: Optional[str] = None) -> dict:
    """Scan the nearest expiry's chain for whale-sized flow (live ThetaData).

    Any fetch failure or empty chain degrades to a zeroed neutral result
    (signal False) -- never raises. Classification is delegated to
    Vol_Suite.whale_scanner.classify_whale_bias. With ``as_of`` set, uses
    that day's EOD volume and close instead of today's.
    """
    result = _neutral_result(ticker)
    try:
        exps = data.get_expirations(ticker)
        if not exps:
            return result
        today = (as_of or date.today().strftime("%Y%m%d"))
        future = [e for e in exps if e >= today]
        expiry = future[0] if future else exps[-1]

        rows = data.get_chain_eod_volume(ticker, expiry, as_of=as_of)
        if as_of:
            price = data.get_close_asof(ticker, as_of=as_of)
        else:
            price = data.get_price(ticker)
    except Exception:
        return result

    if not rows:
        return result

    direction, stats = _vs_whale_scanner.classify_whale_bias(
        rows, min_premium=min_premium, threshold_bps=threshold_bps, price=price,
    )

    result.update({
        "price": price,
        "whale_calls": stats["whale_calls"],
        "whale_puts": stats["whale_puts"],
        "call_premium": stats["call_premium"],
        "put_premium": stats["put_premium"],
        "direction": direction,
        "expiry": expiry,
        "signal": direction != "neutral",
    })
    return result


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Whale flow scanner (ThetaData, live)")
    p.add_argument("ticker", nargs="?", default="SPY")
    args = p.parse_args()
    r = scan(args.ticker)
    print(f"{r['ticker']}: {r['direction']} | Calls: {r['whale_calls']} | "
          f"Puts: {r['whale_puts']} | Call$: ${r['call_premium']:,.0f} | "
          f"Put$: ${r['put_premium']:,.0f}")
