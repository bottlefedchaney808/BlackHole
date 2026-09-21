"""Run the perp shadow sleeve until told to stop.

    python -m chart_app.run_sleeve --ticker BTC-PERP --interval 15m --hours 14

Polls the venue, replays the book, journals anything new, and prints a line a
human can read at a glance. It never places an order -- see `perp_sleeve`.

Poll cadence vs bar cadence
---------------------------
Bars close every `interval`; polling faster than that only re-reads a bar that
has not changed. But polling at exactly the bar period drifts into the close
and spends half its life looking at a bar that is one second old. So the
default poll is a third of the bar -- 5 minutes on a 15m tape -- which bounds
"how stale can the log be" at 5 minutes for a cost of 3 free HTTP calls an
hour against a public endpoint.

The window's LEFT edge must not move (measured 2026-09-20)
----------------------------------------------------------
The first build re-fetched a rolling `--lookback` window every poll and
replayed that. `perp_sleeve.replay` is deterministic given its bars, so this
looked safe. It is not, and the measurement is unambiguous:

    same tape, left edge slid forward by ONE bar   ->  14 of ~135 fills change
    same tape, left edge fixed, one bar appended   ->   0 fills change

The indicators are properly causal -- appending on the right cannot disturb
anything to its left, which is what the second line proves. But a rolling
fetch DROPS a bar on the left every time the window slides, which re-seeds the
200-bar ranks and cascades into different decisions weeks downstream. The
sleeve then discovered those revisions as "new" fills and announced them at
3am as though they had just happened.

So the sleeve keeps its own append-only bar store and replays from that: the
left edge is fixed at the first bar it ever saw, and each poll only extends
the right. The store is separate from `chart_app_bars.db` on purpose -- that
file is being written by a running dashboard, and two processes racing one
SQLite file is the "database is locked" failure this repo already documents.
It is seeded ONCE from that file if the symbol is there, which buys a year of
warm-up instead of thirty days.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chart_app import profiles
from chart_app.bar_cache import BarCache
from chart_app.crypto_source import fetch_crypto_candles, is_crypto
from chart_app.perp_sleeve import Journal, replay
from shared.chart_data import CandleRecord

_ART = Path(__file__).resolve().parent.parent / "artifacts" / "perp_sleeve"
_CHART_BARS = Path(__file__).resolve().parent.parent / "artifacts" / "chart_app_bars.db"

_stop = False


def _handle_signal(signum, frame) -> None:  # pragma: no cover - signal path
    global _stop
    _stop = True


def _say(line: str) -> None:
    """One timestamped line, flushed -- a log tailed at 3am must not buffer."""
    stamp = datetime.now(UTC).strftime("%m-%d %H:%M:%SZ")
    sys.stdout.write(f"{stamp}  {line}\n")
    sys.stdout.flush()


def _fetch(ticker: str, interval: str, lookback: str) -> list[CandleRecord]:
    if not is_crypto(ticker):
        raise SystemExit(
            f"{ticker!r} is not a crypto/perp symbol. This sleeve only routes "
            "through crypto_source (OKX/Coinbase); an equity would need a "
            "ThetaData fetcher and a session calendar."
        )
    payload = fetch_crypto_candles(ticker, interval=interval, lookback=lookback)
    return list(payload.observations)


def _fmt_stop(value: float | None) -> str:
    return f"{value:,.0f}" if value else "--"


def seed_store(store: BarCache, ticker: str, interval: str) -> int:
    """Copy the chart's history in once, so the window starts long, not 30d.

    Read-only against `chart_app_bars.db` -- the dashboard owns that file for
    writing. Returns how many bars were carried over (0 if it has none, which
    is fine: the first fetch then sets the left edge instead).
    """
    if store.last_ts(ticker, interval) is not None:
        return 0  # already anchored; never re-seed, that would move the edge
    if not _CHART_BARS.exists():
        return 0
    try:
        records = BarCache(_CHART_BARS).load(ticker, interval)
    except Exception as exc:  # noqa: BLE001 - a locked/absent chart db is fine
        _say(f"   (could not read {_CHART_BARS.name}: {exc}; starting from the fetch)")
        return 0
    return store.upsert(ticker, interval, records) if records else 0


def tick(
    ticker: str,
    interval: str,
    lookback: str,
    profile: dict[str, Any],
    journal: Journal,
    store: BarCache,
    started: datetime,
    *,
    first: bool = False,
) -> dict[str, Any] | None:
    """One poll. Returns the book as a dict, or None when the venue failed.

    A venue failure must not end the night: it logs and returns, and the next
    poll replays from whatever bars are already in the store. Acting on stale
    bars would be worse than saying nothing.
    """
    try:
        fetched = _fetch(ticker, interval, lookback)
    except Exception as exc:  # noqa: BLE001 - any venue failure is survivable
        journal.write("error", {"error": f"{type(exc).__name__}: {exc}"})
        _say(f"!! fetch failed ({type(exc).__name__}: {exc}) -- holding, will retry")
        return None

    store.upsert(ticker, interval, fetched)
    records = store.load(ticker, interval)
    book = replay(records, profile, interval=interval)

    if first:
        seeded = journal.seed(book, started)
        _say(
            f"anchored on {len(records)} bars from {records[0].timestamp.date()}; "
            f"replayed {seeded} prior fills as history (prior_fill, not tonight)"
        )

    # Only a fill on a bar at or after the session start is news. Anything
    # older that turns up now is a revision of history -- it gets written for
    # the audit trail, but it is not announced as if it just happened.
    cut = started.replace(tzinfo=None)
    announced = []
    for fill in journal.new_fills(book):
        ts = datetime.fromisoformat(fill.ts)
        ts = (
            ts.replace(tzinfo=None)
            if ts.tzinfo is None
            else ts.astimezone(UTC).replace(tzinfo=None)
        )
        if ts < cut:
            journal.write("revision", fill.as_dict())
            _say(
                f"   (revised history: {fill.action} at {fill.ts[-14:]}, logged not traded)"
            )
            continue
        announced.append(fill)
        journal.write("fill", fill.as_dict())
        arrow = "BUY " if fill.units_after > fill.units_before else "SELL"
        _say(
            f"** {arrow} {fill.action.upper():<12} {ticker} @ {fill.price:>10,.2f}  "
            f"units {fill.units_before:+.0f} -> {fill.units_after:+.0f}  "
            f"({fill.reason}, score {fill.score:.0f}, stop {_fmt_stop(fill.stop)})"
        )

    state = book.as_dict()
    journal.write("mark", state)
    # Carried back to the runner for the morning summary, deliberately AFTER
    # the mark is written so the journal's marks stay one flat shape.
    state["session_fills"] = [f.as_dict() for f in announced]
    pending = ""
    if book.pending:
        pending = (
            f"  | PENDING {book.pending['action']} "
            f"-> {book.pending['units_after']:+.0f} at next open"
        )
    _say(
        f"{ticker} {book.last_ts[-14:]}  px {book.last_close:>10,.2f}  "
        f"score {book.score:>5.1f}  {book.side:<5} {abs(book.units):.0f}x  "
        f"stop {_fmt_stop(book.stop):>8}  "
        f"pnl {book.total_pct():+7.2f}% since {book.metrics.get('bars', 0)} bars ago"
        f"{pending}"
    )
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", default="BTC-PERP")
    parser.add_argument("--interval", default="15m")
    parser.add_argument("--lookback", default="30d")
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=0,
        help="0 = a third of the bar interval (5 min on a 15m tape)",
    )
    parser.add_argument(
        "--hours", type=float, default=14.0, help="stop after this long (0 = forever)"
    )
    parser.add_argument("--journal", default="")
    parser.add_argument(
        "--bars-db",
        default="",
        help="append-only bar store; default artifacts/perp_sleeve/sleeve_bars.db",
    )
    args = parser.parse_args(argv)

    from chart_app.perp_sleeve import _INTERVAL_MIN

    poll = args.poll_seconds or max(60, _INTERVAL_MIN.get(args.interval, 15) * 20)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M")
    path = Path(args.journal) if args.journal else _ART / f"{args.ticker}_{stamp}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    journal = Journal(path)
    bars_db = Path(args.bars_db) if args.bars_db else _ART / "sleeve_bars.db"
    bars_db.parent.mkdir(parents=True, exist_ok=True)
    store = BarCache(bars_db)

    profile = profiles.resolve(args.ticker, args.interval)
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    _say("=" * 96)
    _say(
        f"PERP SLEEVE  {args.ticker} {args.interval}  -- SHADOW BOOK, NO ORDERS PLACED"
    )
    _say(
        f"profile {profile.get('source')}  validation={profile.get('validation')}  "
        f"saved={profile.get('saved_at')}"
    )
    cfg = profile.get("config") or {}
    _say(
        f"entry_long={cfg.get('entry_long')} exit_long={cfg.get('exit_long')} "
        f"allow_short={cfg.get('allow_short')} atr_stop_mult={cfg.get('atr_stop_mult')} "
        f"max_units={cfg.get('max_units')} unit_fraction={cfg.get('unit_fraction')}"
    )
    # Leverage is `max_units * unit_fraction`, and it is worth saying out loud
    # every launch rather than leaving it to be read off two config keys. A
    # profile saved from the tester defaults `unit_fraction` to 1.0, which
    # turns max_units into a leverage multiplier -- 5x on the BTC-PERP tune as
    # first saved. Jason's standing instruction (2026-09-20) is no leverage.
    notional = float(cfg.get("max_units", 1)) * float(cfg.get("unit_fraction") or 1.0)
    if notional > 1.0001:
        _say(
            f"WARNING: this profile runs {notional:.1f}x NOTIONAL "
            f"(max_units={cfg.get('max_units')} x unit_fraction="
            f"{cfg.get('unit_fraction')}). Set unit_fraction=1/max_units for a "
            "cash account that never borrows."
        )
    else:
        _say(f"sizing: {notional:.2f}x max notional -- no leverage, never borrows")
    if profile.get("validation") != "walk_forward":
        _say(
            "NOTE: this profile is NOT walk-forward validated. Every number below "
            "is what an in-sample fit would have done."
        )
    _say(f"journal {path}")
    carried = seed_store(store, args.ticker, args.interval)
    if carried:
        _say(f"bar store {bars_db.name}: seeded {carried} bars from the chart's cache")
    else:
        _say(f"bar store {bars_db.name}: using what it already holds (left edge fixed)")
    _say(f"poll every {poll}s, running {args.hours or 'forever'}h")
    _say("=" * 96)
    journal.write(
        "session",
        {
            "ticker": args.ticker,
            "interval": args.interval,
            "profile_source": profile.get("source"),
            "validation": profile.get("validation"),
            "config": cfg,
            "elmo": profile.get("elmo") or {},
            "executes_orders": False,
        },
    )

    started = datetime.now(UTC)
    deadline = time.time() + args.hours * 3600 if args.hours else None
    ticks = 0
    live_fills: list[dict[str, Any]] = []
    last_state: dict[str, Any] | None = None
    while not _stop:
        state = tick(
            args.ticker,
            args.interval,
            args.lookback,
            profile,
            journal,
            store,
            started,
            first=(ticks == 0),
        )
        if state:
            live_fills.extend(state.get("session_fills") or [])
            last_state = state
        ticks += 1
        if deadline and time.time() >= deadline:
            break
        # Sleep in slices so a stop signal is honoured promptly.
        waited = 0.0
        while waited < poll and not _stop:
            if deadline and time.time() >= deadline:
                break
            time.sleep(min(2.0, poll - waited))
            waited += 2.0

    _summary(args.ticker, started, ticks, live_fills, last_state, profile, path)
    journal.write(
        "session_end",
        {"ticks": ticks, "session_fills": len(live_fills), "final": last_state},
    )
    return 0


def _summary(
    ticker: str,
    started: datetime,
    ticks: int,
    fills: list[dict[str, Any]],
    state: dict[str, Any] | None,
    profile: dict[str, Any],
    path: Path,
) -> None:
    """The morning read. Position first, then what it did to get there."""
    hours = (datetime.now(UTC) - started).total_seconds() / 3600.0
    _say("=" * 96)
    _say(f"OVERNIGHT SUMMARY  {ticker}  {hours:.1f}h, {ticks} polls")
    if state is None:
        _say("no successful poll -- the venue never answered. Nothing to report.")
        _say("=" * 96)
        return

    if fills:
        _say(f"{len(fills)} fill(s) while the sleeve was running:")
        for fill in fills:
            arrow = "BUY " if fill["units_after"] > fill["units_before"] else "SELL"
            _say(
                f"   {fill['ts'][-14:]}  {arrow} {fill['action'].upper():<12} "
                f"@ {fill['price']:>10,.2f}  "
                f"{fill['units_before']:+.0f} -> {fill['units_after']:+.0f}  "
                f"({fill['reason']})"
            )
    else:
        _say("no fills while the sleeve was running -- it sat on its hands.")

    _say(
        f"POSITION NOW: {state['side'].upper()} {abs(state['units']):.0f} unit(s) "
        f"= {state['notional_x']:.0f}x notional"
        + (f", avg {state['avg_entry']:,.2f}" if state["units"] else "")
    )
    if state["units"]:
        _say(
            f"   stop {_fmt_stop(state['stop'])}   last {state['last_close']:,.2f}   "
            f"position move {state['open_move_pct']:+.2f}% at {state['notional_x']:.0f}x"
        )
    if state.get("pending"):
        pend = state["pending"]
        _say(
            f"   PENDING: {pend['action']} -> {pend['units_after']:+.0f} units, "
            "fills at the next bar's open"
        )
    _say(
        f"WHOLE REPLAY WINDOW ({state['trades']} trades, {state['cost_bps']:.0f}bp/side): "
        f"{state['total_pct']:+.2f}% vs buy-hold {state['buy_hold_pct']:+.2f}%, "
        f"maxDD {state['max_drawdown_pct']:.1f}%, Sharpe {state['sharpe']:.2f}"
    )
    _say(
        "   (that window is mostly history, not tonight -- it is the tune's "
        "record on the bars the sleeve holds, priced by the same backtester)"
    )
    if profile.get("validation") != "walk_forward":
        _say(
            f"REMINDER: profile {profile.get('source')} is '{profile.get('validation')}'. "
            "These are in-sample parameters; this log is evidence about the tune, "
            "not a track record."
        )
    _say(f"journal: {path}")
    _say("=" * 96)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
