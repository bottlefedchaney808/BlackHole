# Direction/liquidity_map.py
"""Liquidity zones -- max pain, OI strike walls, put/call ratio, GEX proximity.

4-layer strike walls: OI concentration + GEX + PCR + Max Pain proximity.
Support = cluster of put OI below price; Resistance = call OI above.

All market data comes from the package's ThetaData adapter (``Direction.data``).
Every fetch may return None/empty on missing data or network failure, so this
module degrades gracefully: no chain/price -> ``max_pain = call_wall =
put_wall = price`` (or None if no price at all), ``pcr = 0.0``,
``signal = False``. The ``dealer`` layer (GEX) is best-effort and may be
None -- it never takes the rest of the map down.

Max-pain fix vs. a naive port: an external research package's original
implementation picked "the strike nearest to spot" (self-labeled "original
argmin approximation") -- that is a spot-proximity heuristic, not max pain.
True max pain is the strike that MINIMIZES the aggregate dollar payout to
ITM option holders at expiry across the WHOLE chain. A call at strike ``s``
is in the money when settlement ``K`` is ABOVE the strike, paying
``max(0, K - s)``; a put at strike ``s`` is in the money when settlement
``K`` is BELOW the strike, paying ``max(0, s - K)``. So the aggregate payout
is the sum over every strike of call_oi * max(0, K - s) + put_oi *
max(0, s - K), argmin over candidate K. ``_max_pain`` below implements that
directly. (sentiment-scanner/scanner/max_pain_scanner.py's own
``_compute_pain_for_strike`` has this same calls/puts payout term swapped,
so it is not a correct reference for the formula either -- separately, that
module also selects via argmax instead of argmin, picking the wrong side of
the curve.)
"""

from __future__ import annotations

from datetime import date

from . import data


def _pick_expiry(expirations) -> "str | None":
    """Nearest expiry: first >= today (YYYYMMDD string compare), else the
    closest past one. Returns None when the list is empty."""
    if not expirations:
        return None
    today = date.today().strftime("%Y%m%d")
    future = [e for e in expirations if e >= today]
    if future:
        return min(future)
    return max(expirations)


def _sum_oi(rows, right: str) -> float:
    """Total open interest across all rows of one right (C/P)."""
    return sum(
        float(r.get("oi", 0.0) or 0.0)
        for r in rows
        if r.get("right") == right
    )


def _max_pain(chain, default: float) -> float:
    """True max-pain strike: argmin of aggregate ITM payout across the chain.

    Returns ``default`` (typically spot price) when the chain is empty.
    """
    strikes = sorted({float(r["strike"]) for r in chain})
    if not strikes:
        return default

    call_oi = {
        k: sum(float(r.get("oi", 0.0) or 0.0) for r in chain
               if r.get("right") == "C" and float(r["strike"]) == k)
        for k in strikes
    }
    put_oi = {
        k: sum(float(r.get("oi", 0.0) or 0.0) for r in chain
               if r.get("right") == "P" and float(r["strike"]) == k)
        for k in strikes
    }

    def _payout(K: float) -> float:
        return sum(
            call_oi[s] * max(0.0, K - s) + put_oi[s] * max(0.0, s - K)
            for s in strikes
        )

    return min(strikes, key=_payout)


def get_liquidity(ticker: str) -> dict:
    """Return {"price", "expiry", "max_pain", "call_wall", "put_wall",
    "pcr", "signal": bool, "dealer"}.

    - ``expiry``: nearest expiry (>= today preferred, else closest past).
    - ``max_pain``: strike minimizing aggregate ITM option-holder payout,
      defaults to price when no chain is available.
    - ``call_wall``/``put_wall``: strikes with the largest call/put OI,
      defaulting to price when that side has no contracts.
    - ``pcr``: total put OI / total call OI (0.0 when no call OI).
    - ``signal``: True when max pain sits within 2% of spot (gravity
      proximity); always False on missing chain/price.
    - ``dealer``: raw dealer-positioning payload (GEX layer), may be None.
    """
    price = data.get_price(ticker)
    expirations = data.get_expirations(ticker) or []
    exp = _pick_expiry(expirations)

    chain = None
    if exp is not None:
        chain = data.get_chain_oi(ticker, exp)

    try:
        dealer = data.get_dealer_gamma(ticker)
    except Exception:
        dealer = None

    if price is None or price <= 0 or not chain:
        return {
            "price": price,
            "expiry": exp,
            "max_pain": price,
            "call_wall": price,
            "put_wall": price,
            "pcr": 0.0,
            "signal": False,
            "dealer": dealer,
        }

    max_pain = _max_pain(chain, price)

    calls = [r for r in chain if r.get("right") == "C"]
    puts = [r for r in chain if r.get("right") == "P"]
    call_wall = max(calls, key=lambda r: r.get("oi", 0.0))["strike"] if calls else price
    put_wall = max(puts, key=lambda r: r.get("oi", 0.0))["strike"] if puts else price

    call_oi = _sum_oi(chain, "C")
    put_oi = _sum_oi(chain, "P")
    pcr = put_oi / call_oi if call_oi > 0 else 0.0

    signal = abs(max_pain - price) / price <= 0.02

    return {
        "price": price,
        "expiry": exp,
        "max_pain": max_pain,
        "call_wall": call_wall,
        "put_wall": put_wall,
        "pcr": pcr,
        "signal": bool(signal),
        "dealer": dealer,
    }


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Liquidity zones map (ThetaData)")
    p.add_argument("ticker", nargs="?", default="SPY")
    args = p.parse_args()
    z = get_liquidity(args.ticker)
    pct = (z["max_pain"] - z["price"]) / z["price"] * 100
    print(f"{args.ticker}: Price={z['price']:.2f} | MaxPain={z['max_pain']:.2f} "
          f"({pct:+.2f}%) | PCR={z['pcr']:.2f} | Expires: {z['expiry']}")
