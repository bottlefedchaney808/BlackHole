"""Direction/replay.py -- per-bar historical replay of the Direction suite.

Evaluates the v2 indicator's per-bar signal composition for each bar
timestamp in a chart's visible range so a candlestick chart can show a
buy/sell marker per bar.  One evaluation per BAR TIMESTAMP (v1 evaluated
once per unique calendar date, so every intraday bar of a day carried the
same verdict).  ``generate_fn`` is injectable for tests; a per-bar failure
degrades to a neutral entry, never an invented signal.

Known limitation (current-expiry universe): each bar is evaluated
against the CURRENT expiry universe -- ``data.get_expirations`` returns
today's list, and expiry availability as-of the bar timestamp is not
modeled.  A replay bar whose only candidate expiries postdate the as-of
bar may therefore be evaluated against expiries that did not exist that
day.  This degrades neutrally (unfetchable data -> neutral entry), never
fabricates, but can miss signals that existed at the bar or skew
nearest-expiry selection later than the bar's true nearest expiry.
"""

from __future__ import annotations

from collections.abc import Callable

from .indicator import _SIGNAL_NAMES, _as_datetime, _score_conviction


def _default_generate_fn() -> Callable:
    """Build the default per-bar generate fn: the v2 indicator's real path.

    Returns a closure ``generate_fn(ticker, *, ts)`` that runs the v2
    indicator's per-bar signal composition (``indicator._compose_signals``
    -- the same real path ``indicator.bar_eval`` uses), threading ONE
    liquidity-grid state across the whole replay so the coarse gamma grid
    samples the sequence exactly as ``bar_eval`` would.
    """
    state: dict = {}

    def _generate(ticker: str, *, ts) -> dict:
        from . import indicator

        return indicator._compose_signals(ticker, ts, state=state)

    return _generate


def replay_direction(
    ticker: str,
    bar_timestamps: list[object],
    generate_fn: Callable | None = None,
) -> list[dict]:
    """Return [{ts, conviction, score, signals}] for each bar timestamp.

    ``bar_timestamps`` is a list of datetime or ISO-8601 string
    timestamps (ascending); one evaluation PER BAR TIMESTAMP (v1
    evaluated per unique calendar date).  ``generate_fn`` defaults to the
    v2 indicator's per-bar composition and is called as
    ``generate_fn(ticker, ts=...)``, returning a dict of boolean signal
    keys -- the five canonical whale/wave3/squeeze/trend/liquidity
    (missing keys count as False, extra keys are ignored) -- the same
    contract as ``indicator.bar_eval``'s injectable generate_fn.  A
    failing bar degrades to ``{"ts": ..., "conviction": "NONE", "score":
    0, "signals": {}}`` without raising; the other bars are unaffected.
    """
    if generate_fn is None:
        generate_fn = _default_generate_fn()

    out: list[dict] = []
    for bar_ts in bar_timestamps:
        try:
            ts = _as_datetime(bar_ts).isoformat()
        except (TypeError, ValueError):
            ts = str(bar_ts)
        try:
            raw = generate_fn(ticker, ts=bar_ts)
            signals = {name: bool(raw.get(name, False)) for name in _SIGNAL_NAMES}
            score, conviction = _score_conviction(signals)
        except Exception:
            out.append({"ts": ts, "conviction": "NONE", "score": 0, "signals": {}})
            continue
        out.append(
            {"ts": ts, "conviction": conviction, "score": score, "signals": signals}
        )
    return out
