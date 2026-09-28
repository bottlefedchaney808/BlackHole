"""Trade the BTC-PERP sleeve for real on Kalshi (KXBTCPERP). Dry-run unless --live.

    python -m chart_app.run_live_perp --cap-dollars 100            # dry run: logs orders, sends none
    python -m chart_app.run_live_perp --cap-dollars 100 --live     # real orders

What it does every `--tick-seconds`:
  1. extends its own append-only bar store from OKX and replays the saved tune
     (`perp_sleeve.replay`) -- the SAME function and the SAME left edge as the
     shadow sleeve, so the two cannot disagree about what the tune holds;
  2. converts the book's units into a target contract count at <= 1.00x of
     min(cap, equity)  (`perp_live`);
  3. works one post-only maker order at the touch toward that target,
     re-quoting when the touch moves; a risk-REDUCING move that has not filled
     after `--cross-after` seconds crosses the spread reduce-only;
  4. keeps a Kalshi stop-loss trigger at the book's ATR stop on whatever
     position is actually held (the backtest fills 87% of its exits at that
     stop, intrabar -- polling at bar close would not be the same strategy);
  5. kill switch: the sleeve's OWN P&L down `--max-loss` dollars -> cancel
     everything, flatten reduce-only, clear the stop, exit.

Sizing and the kill switch read the sleeve's own capital -- launch capital
plus P&L from its subaccount's fills -- never the subaccount's equity. That
equity also moves when collateral moves between subaccounts: an isolated
manual trade in 64 pulls its margin out of 0 (measured 2026-09-21, five times,
$5-$25 each). Reading it, a big enough manual buy would have shrunk the
sleeve's target or tripped its kill switch with no sleeve trade at all.
A restart keeps the books continuous with `--pnl-since` (the original launch
time) and `--base-capital` (the capital at that launch).

The left edge of the bar store matters (see run_sleeve's docstring): it is
seeded once from the shadow sleeve's store, never re-fetched from scratch.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chart_app import profiles
from chart_app.bar_cache import BarCache
from chart_app.kalshi_perps import KalshiPerpsError, PerpsClient
from launch_dock.launch import KALSHI
from chart_app.perp_live import (
    book_target_units,
    full_contracts,
    kalshi_stop,
    plan_order,
    reduces_risk,
    sleeve_pnl,
    target_contracts,
)
from chart_app.perp_sleeve import replay
from chart_app.run_sleeve import _fetch

_ART = Path(__file__).resolve().parent.parent / "artifacts" / "perp_live"
_SHADOW_BARS = (
    Path(__file__).resolve().parent.parent
    / "artifacts"
    / "perp_sleeve"
    / "sleeve_bars.db"
)
_stop = False


def _handle_signal(signum, frame) -> None:  # pragma: no cover - signal path
    global _stop
    _stop = True


def _say(line: str) -> None:
    stamp = datetime.now(UTC).strftime("%m-%d %H:%M:%SZ")
    sys.stdout.write(f"{stamp}  {line}\n")
    sys.stdout.flush()


class Log:
    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, kind: str, payload: dict[str, Any]) -> None:
        row = {"type": kind, "at": datetime.now(UTC).isoformat(), **payload}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")


def seed_from_shadow(store: BarCache, ticker: str, interval: str) -> int:
    if store.last_ts(ticker, interval) is not None or not _SHADOW_BARS.exists():
        return 0
    records = BarCache(_SHADOW_BARS).load(ticker, interval)
    return store.upsert(ticker, interval, records) if records else 0


class Executor:
    """Owns the one working order and the stop. Everything else is recomputed."""

    def __init__(
        self, client: PerpsClient | None, log: Log, *, live: bool, cross_after: float
    ):
        self.client = client
        self.log = log
        self.live = live
        self.cross_after = cross_after
        self.working: dict[str, Any] | None = (
            None  # {order_id, target, price, side, since}
        )
        self.target_since: tuple[int, float] | None = None
        self.stop_set: float | None = None
        self.paper_pos = 0  # dry-run position, so the plan evolves sensibly

    def _send(self, plan) -> dict[str, Any] | None:
        self.log.write("order", {"plan": plan.__dict__, "live": self.live})
        _say(
            f"{'>> ORDER' if self.live else '.. would'} {plan.side.upper():<3} {plan.count} @ "
            f"{plan.price:.4f}  ({plan.why}, {plan.tif}{', post-only' if plan.post_only else ''}"
            f"{', reduce-only' if plan.reduce_only else ''})"
        )
        if not self.live:
            return None
        try:
            return self.client.place(
                plan.side,
                plan.count,
                plan.price,
                post_only=plan.post_only,
                tif=plan.tif,
                reduce_only=plan.reduce_only,
            )
        except KalshiPerpsError as exc:
            self.log.write("order_error", {"error": str(exc), "body": exc.body})
            _say(f"!! order rejected: {exc}")
            return None

    def _cancel_working(self) -> None:
        if self.working and self.live:
            try:
                self.client.cancel(self.working["order_id"])
            except KalshiPerpsError as exc:
                if exc.status != 404:
                    _say(f"!! cancel failed: {exc}")
        self.working = None

    def step(self, position: int, target: int, bid: float, ask: float) -> None:
        now = time.time()
        if self.target_since is None or self.target_since[0] != target:
            self.target_since = (target, now)

        # Reconcile the resting order with the exchange before planning anew.
        if self.working and self.live:
            try:
                order = self.client.order(self.working["order_id"])
                if float(order.get("remaining_count") or 0) <= 0:
                    self.log.write("order_done", {"order": order})
                    self.working = None
            except KalshiPerpsError as exc:
                _say(f"!! order lookup failed: {exc}")

        if position == target:
            if self.working:
                self._cancel_working()
            return

        cross = (
            reduces_risk(position, target)
            and now - self.target_since[1] >= self.cross_after
        )
        plan = plan_order(position, target, bid, ask, cross=cross)
        if plan is None:
            return
        if self.working:
            same = (
                self.working["target"] == target
                and self.working["side"] == plan.side
                and abs(self.working["price"] - plan.price) < 1e-9
                and not cross
            )
            if same:
                return  # still resting at the touch -- leave it queued
            self._cancel_working()

        resp = self._send(plan)
        if not self.live:
            # Dry run: assume a maker order fills, so the next plan is the next step.
            self.paper_pos = target
            return
        if (
            resp
            and float(resp.get("remaining_count") or 0) > 0
            and plan.tif == "good_till_canceled"
        ):
            self.working = {
                "order_id": resp["order_id"],
                "target": target,
                "price": plan.price,
                "side": plan.side,
                "since": now,
            }
        elif resp:
            self.log.write("order_done", {"order": resp})

    def sync_stop(self, position: int, book_stop: float | None) -> None:
        want = kalshi_stop(book_stop) if position else None
        if want == self.stop_set:
            return
        if want is None:
            _say(f"{'>> STOP' if self.live else '.. would'} clear stop")
            if self.live:
                try:
                    self.client.clear_stop()
                except KalshiPerpsError as exc:
                    _say(f"!! clear stop failed: {exc}")
                    return
        else:
            _say(
                f"{'>> STOP' if self.live else '.. would'} set stop-loss {want:.4f} on {position:+d}"
            )
            if self.live:
                try:
                    self.client.set_stop(want)
                except KalshiPerpsError as exc:
                    self.log.write("stop_error", {"error": str(exc), "body": exc.body})
                    _say(f"!! stop rejected: {exc}")
                    return
        self.log.write("stop", {"price": want, "position": position})
        self.stop_set = want

    def flatten(self, position: int, bid: float, ask: float) -> None:
        self._cancel_working()
        if self.live:
            try:
                self.client.cancel_all()
            except KalshiPerpsError as exc:
                _say(f"!! cancel_all failed: {exc}")
        if position:
            plan = plan_order(position, 0, bid, ask, cross=True)
            self._send(plan)
        self.sync_stop(0, None)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--ticker", default="BTC-PERP")
    ap.add_argument("--interval", default="15m")
    ap.add_argument(
        "--cap-dollars", type=float, help="fixed max notional at 1.00x"
    )
    ap.add_argument(
        "--cap-fraction",
        type=float,
        help="share of live sleeve capital to deploy, 0-1, recomputed every tick",
    )
    ap.add_argument(
        "--leverage",
        type=float,
        default=1.0,
        help="notional multiple on min(cap, sleeve capital). 1 = no leverage.",
    )
    ap.add_argument(
        "--max-loss",
        type=float,
        default=0.0,
        help="kill switch, $ below launch equity (default 25%% of min(cap, equity))",
    )
    ap.add_argument(
        "--live",
        action="store_true",
        help="send real orders; without it nothing is sent",
    )
    ap.add_argument("--tick-seconds", type=int, default=20)
    ap.add_argument(
        "--bars-every", type=int, default=60, help="seconds between OKX bar fetches"
    )
    ap.add_argument(
        "--cross-after",
        type=float,
        default=300.0,
        help="seconds before a risk-reducing move may cross",
    )
    ap.add_argument(
        "--subaccount",
        type=int,
        default=0,
        help="margin subaccount the sleeve trades (0 = primary; 64 holds manual trades)",
    )
    ap.add_argument("--hours", type=float, default=0.0, help="0 = until stopped")
    ap.add_argument(
        "--pnl-since",
        default="",
        help="ISO UTC time the sleeve's P&L counts from (default: now). Pass the "
        "original launch time on a restart so the books stay continuous.",
    )
    ap.add_argument(
        "--base-capital",
        type=float,
        default=0.0,
        help="the sleeve's capital at --pnl-since (default: subaccount equity now)",
    )
    args = ap.parse_args(argv)

    _ART.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M")
    log = Log(_ART / f"{args.ticker}_{'live' if args.live else 'dry'}_{stamp}.jsonl")
    store = BarCache(_ART / "live_bars.db")
    profile = profiles.resolve(args.ticker, args.interval)
    cfg = profile.get("config") or {}
    max_units = float(cfg.get("max_units") or 1) or 1.0
    raw_uf = cfg.get("unit_fraction")
    unit_fraction = float(raw_uf) if raw_uf is not None else (1.0 / max_units)
    notional_x = max_units * unit_fraction
    if notional_x > 1.0001:
        raise SystemExit(
            f"profile is {notional_x:.2f}x notional; live trading runs at 1.00x max "
            "(set unit_fraction = 1/max_units)"
        )

    kalshi = KALSHI.get(args.ticker.upper())
    if not kalshi:
        raise SystemExit(f"no Kalshi perp mapped for {args.ticker}")
    client = PerpsClient(subaccount=args.subaccount, ticker=kalshi)
    equity0 = args.base_capital or client.equity()
    since = (
        datetime.fromisoformat(args.pnl_since)
        if args.pnl_since
        else datetime.now(UTC)
    )
    pnl0 = sleeve_pnl(client.fills(), since, client.unrealized_pnl(), ticker=kalshi)
    position = round(client.position())
    bid, ask = client.top_of_book()
    if (args.cap_dollars is None) == (args.cap_fraction is None):
        raise SystemExit("pass exactly one of --cap-dollars or --cap-fraction")
    if args.cap_fraction is not None and not 0.0 < args.cap_fraction <= 1.0:
        raise SystemExit(f"--cap-fraction must be in (0, 1], got {args.cap_fraction}")
    if args.leverage < 1.0:
        raise SystemExit(f"--leverage must be >= 1, got {args.leverage}")

    def cap_for(capital: float) -> float:
        if args.cap_fraction is not None:
            return max(0.0, capital) * args.cap_fraction
        return float(args.cap_dollars)

    # 25% of what is actually at risk -- the cap is a ceiling, not the account.
    max_loss = args.max_loss or 0.25 * min(cap_for(equity0), equity0)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    _say("=" * 96)
    _say(
        f"PERP SLEEVE LIVE  {args.ticker} {args.interval} -> {kalshi}  "
        f"{'*** REAL ORDERS ***' if args.live else 'DRY RUN, nothing sent'}"
    )
    _say(
        f"profile {profile.get('source')} saved {profile.get('saved_at')}  "
        f"max_units={cfg.get('max_units')} x unit_fraction={unit_fraction} = {notional_x:.2f}x"
    )
    _say(
        "walk-forward (1x, 4 folds, measured 2026-09-20): OOS +27.1% at 2bp maker, "
        "+14.7% at 5bp, -9.7% at 12bp taker -> entries post-only"
    )
    _say(
        f"sleeve capital ${equity0:,.2f} at {since:%m-%d %H:%MZ}, P&L since ${pnl0:+,.2f}  "
        + (
            f"cap {args.cap_fraction:.0%} of book (${cap_for(equity0):,.2f} now)  "
            if args.cap_fraction is not None
            else f"cap ${args.cap_dollars:,.2f}  "
        )
        + f"kill at P&L -${max_loss:,.2f}  "
        f"position {position:+d}  touch {bid:.4f}/{ask:.4f}"
    )
    if args.live and equity0 < 1.0:
        _say("!! margin account is empty -- move funds to Perps first. Not starting.")
        return 2
    seeded = seed_from_shadow(store, args.ticker, args.interval)
    _say(
        f"bar store live_bars.db: {'seeded ' + str(seeded) + ' bars from the shadow sleeve' if seeded else 'left edge already fixed'}"
    )
    _say(f"journal {log.path}")
    _say("=" * 96)
    log.write(
        "session",
        {
            "live": args.live,
            "cap": cap_for(equity0),
            "cap_fraction": args.cap_fraction,
            "max_loss": max_loss,
            "equity": equity0,
            "pnl_since": since.isoformat(),
            "pnl0": pnl0,
            "position": position,
            "config": cfg,
        },
    )

    ex = Executor(client, log, live=args.live, cross_after=args.cross_after)
    ex.paper_pos = position
    deadline = time.time() + args.hours * 3600 if args.hours else None
    book: dict[str, Any] | None = None
    last_bars = 0.0
    last_line = ""
    while not _stop and not (deadline and time.time() >= deadline):
        try:
            if time.time() - last_bars >= args.bars_every or book is None:
                store.upsert(
                    args.ticker, args.interval, _fetch(args.ticker, args.interval, "2d")
                )
                book = replay(
                    store.load(args.ticker, args.interval),
                    profile,
                    interval=args.interval,
                ).as_dict()
                last_bars = time.time()
            bid, ask = client.top_of_book()
            equity = client.equity()
            pnl = sleeve_pnl(client.fills(), since, client.unrealized_pnl(), ticker=kalshi)
            # Its own capital, and never more than the money that exists across
            # every subaccount -- 1x must stay 1x even if a manual trade loses.
            capital = min(equity0 + pnl, client.total_equity())
            position = round(client.position()) if args.live else ex.paper_pos
            full = full_contracts(
                cap_for(capital),
                capital,
                (bid + ask) / 2,
                args.leverage,
            )
            units = book_target_units(book)
            target = target_contracts(units, unit_fraction, full)

            if args.live and -pnl >= max_loss:
                _say(f"!! KILL SWITCH: sleeve P&L ${pnl:+,.2f} since {since:%m-%d %H:%MZ}")
                log.write(
                    "kill",
                    {"pnl": pnl, "equity": equity, "equity0": equity0, "position": position},
                )
                ex.flatten(position, bid, ask)
                break

            ex.step(position, target, bid, ask)
            ex.sync_stop(position if args.live else ex.paper_pos, book.get("stop"))

            line = (
                f"bar {str(book.get('last_ts'))[-14:]}  score {book.get('score', 0):5.1f}  "
                f"book {units:+.0f}u -> target {target:+d}c (full {full})  held {position:+d}c  "
                f"sleeve pnl ${pnl:+,.2f}  capital ${capital:,.2f}  touch {bid:.4f}/{ask:.4f}"
            )
            if line != last_line:
                _say(line)
                last_line = line
            log.write(
                "mark",
                {
                    "units": units,
                    "target": target,
                    "position": position,
                    "equity": equity,
                    "pnl": pnl,
                    "capital": capital,
                    "bid": bid,
                    "ask": ask,
                    "stop": book.get("stop"),
                    "bar": book.get("last_ts"),
                },
            )
        except Exception as exc:  # noqa: BLE001 - a bad tick must not end the night
            log.write("error", {"error": f"{type(exc).__name__}: {exc}"})
            _say(f"!! tick failed ({type(exc).__name__}: {exc}) -- holding, will retry")
        waited = 0.0
        while waited < args.tick_seconds and not _stop:
            time.sleep(1.0)
            waited += 1.0

    if _stop and ex.working:
        ex._cancel_working()
    _say(
        "stopped. Position and stop-loss are left as they are on Kalshi -- the stop still protects it."
    )
    log.write("session_end", {})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
