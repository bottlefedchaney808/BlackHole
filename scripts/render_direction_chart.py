"""Render a candlestick chart with the Direction buy/sell overlay.

Usage:
  python scripts/render_direction_chart.py SPY --interval 15m --lookback 5d
  python scripts/render_direction_chart.py QQQ --interval 1h --lookback 30d --out artifacts/qqq_dir.png

The chart shows one marker per bar (HIGH=▲ buy, MEDIUM=△ weak buy,
NONE=▼ sell) computed by replaying the Direction suite as-of each bar's
date, plus a live conviction stamp on the last bar.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
    bar_dates = sorted({obs.timestamp.date().isoformat() for obs in payload.observations})

    # 2. Replay the Direction suite as-of each bar date.
    overlay = replay_direction(args.ticker, bar_dates)

    # 3. Live stamp: current conviction + score.
    from Direction.signal_generator import generate
    live = generate(args.ticker)
    live_note = f"LIVE: {live['conviction']} ({live['score']}/5)"

    # 4. Render.
    art = render_spot_chart(args.ticker, interval=args.interval,
                            lookback=args.lookback, output_path=out,
                            direction_overlay=overlay, live_note=live_note)
    print(f"saved {art.path} | {art.row_count} bars | {bar_dates[0]} -> {bar_dates[-1]}")
    print(f"live: {live_note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
