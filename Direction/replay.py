"""Direction/replay.py -- per-bar historical replay of the Direction suite.

Evaluates ``signal_generator.generate(ticker, as_of=date)`` for each bar
date in a chart's visible range so a candlestick chart can show a
buy/sell marker per bar. ``generate_fn`` is injectable for tests; a
per-date failure degrades to a neutral entry, never an invented signal.

Known limitation (current-expiry universe): each bar is evaluated
against the CURRENT expiry universe -- ``data.get_expirations`` returns
today's list, and expiry availability as-of the bar date is not modeled.
A replay bar whose only candidate expiries postdate the as-of bar date
may therefore be evaluated against expiries that did not exist that day.
This degrades neutrally (unfetchable data -> neutral entry), never
fabricates, but can miss signals that existed at the bar date or skew
nearest-expiry selection later than the bar's true nearest expiry.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional


def replay_direction(
    ticker: str,
    bar_dates: List[str],
    generate_fn: Optional[Callable] = None,
) -> List[Dict]:
    """Return [{date, conviction, score, signals}] for each bar date.

    ``bar_dates`` are canonical 'YYYY-MM-DD' strings. ``generate_fn``
    defaults to ``signal_generator.generate`` and is called as
    ``generate_fn(ticker, as_of=date)``. A failing evaluation degrades to
    ``{"conviction": "NONE", "score": 0, "signals": {}}``.
    """
    if generate_fn is None:
        from .signal_generator import generate as _generate
        generate_fn = _generate

    out: List[Dict] = []
    for d in bar_dates:
        try:
            r = generate_fn(ticker, as_of=d)
            out.append({
                "date": d,
                "conviction": r.get("conviction", "NONE"),
                "score": int(r.get("score", 0) or 0),
                "signals": r.get("signals", {}),
            })
        except Exception:
            out.append({"date": d, "conviction": "NONE", "score": 0, "signals": {}})
    return out
