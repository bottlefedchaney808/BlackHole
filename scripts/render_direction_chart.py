"""Render a candlestick chart with the Direction buy/sell overlay.

Usage:
  python scripts/render_direction_chart.py SPY --interval 15m --lookback 5d
  python scripts/render_direction_chart.py QQQ --interval 1h --lookback 30d --out artifacts/qqq_dir.png

The chart shows one marker per bar (HIGH=▲ buy, MEDIUM=△ weak buy,
NONE=▼ sell) computed by replaying the Direction v2 indicator AS-OF EACH
BAR'S OWN TIMESTAMP (per-bar intraday whale-flow + coarse-grid dealer gamma
via ``Direction.replay.replay_direction``), plus a live conviction stamp on
the last bar.  v1 replayed once per unique calendar date, so every intraday
bar of a day carried the same verdict; v2 evaluates every bar timestamp, so
markers change intraday when the signals actually change.

Live note: the v2 indicator's evaluation of the LAST bar -- the same
per-bar compose path the overlay uses (``_compose_signals`` with one shared
liquidity-grid session), read from the replay's final entry so the stamp
always equals the marker drawn on the newest bar.  Not the v1
``signal_generator.generate`` path.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.candlestick_chart import apply_position_gate  # noqa: E402
from shared.chart_request import render_spot_chart  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Direction-overlay candlestick chart")
    p.add_argument("ticker", help="equity symbol, e.g. SPY")
    p.add_argument("--interval", default="15m",
                   choices=["3m", "5m", "10m", "15m", "30m", "1h", "4h", "1d"])
    p.add_argument("--lookback", default="5d", help="e.g. 5d, 30d, 6m")
    p.add_argument("--out", default=None, help="output PNG path")
    args = p.parse_args(argv)

    out = args.out or f"artifacts/{args.ticker}_{args.lookback}_{args.interval}_direction.png"

    from Direction.replay import replay_direction
    from shared import spot_history

    # 1. Get the bars first so the overlay covers exactly the visible range.
    if args.interval == "1d":
        payload = spot_history.fetch_daily_candles(args.ticker, lookback=args.lookback)
    else:
        payload = spot_history.fetch_intraday_candles(
            args.ticker, interval=args.interval, lookback=args.lookback)
    # EVERY bar timestamp, ascending (v1 deduped to unique dates here).
    bar_ts = [obs.timestamp.isoformat() for obs in payload.observations]

    # 2. Replay the v2 indicator as-of EACH bar timestamp (per-bar verdicts).
    overlay = replay_direction(args.ticker, bar_ts)

    # 3. Live stamp: the v2 indicator's evaluation of the LAST bar (the
    #    replay's final entry -- one shared liquidity-grid session, so the
    #    stamp matches the newest bar's marker exactly).
    last = overlay[-1] if overlay else {"conviction": "NONE", "score": 0}
    live_note = f"LIVE: {last['conviction']} ({last['score']}/5)"

    # 4. Render.
    art = render_spot_chart(args.ticker, interval=args.interval,
                            lookback=args.lookback, output_path=out,
                            direction_overlay=overlay, live_note=live_note)
    scores = [entry.get("score", 0) for entry in overlay]
    markers = apply_position_gate(overlay)
    print(f"saved {art.path} | {art.row_count} bars | {bar_ts[0]} -> {bar_ts[-1]}")
    print(f"scores: {scores}")
    print(f"markers: {markers}")
    print(f"live: {live_note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
