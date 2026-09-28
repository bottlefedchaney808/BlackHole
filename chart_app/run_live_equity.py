"""Trade one equity sleeve for real on Robinhood. Dry-run unless --live.

    python -m chart_app.run_live_equity --ticker XE --interval 5m --cap-fraction 0.25         # dry run
    python -m chart_app.run_live_equity --ticker FCEL --interval 5m --cap-fraction 0.25 --live # real orders

Built for XE on 2026-09-21; any ticker with its own saved TICKER|interval
profile since 2026-09-26, so the launch dock can run many at once. It trades
ONLY the agentic account (AGENTIC_ACCOUNT, from `.env`) -- never Jason's own.
The first XE runs defaulted to his personal account; that default is gone and
any other account is refused.

Built 2026-09-21 on the pyhood 0.12.3 API (US_EquityDesk/rh_session.get_client),
mirroring run_live_perp's structure:

  * Same bars, same left edge, same replay as the shadow sleeve
    (run_sleeve._fetch / _flow_rows + perp_sleeve.replay with the equity grace),
    so the live book and the shadow book cannot disagree about the tune.
  * Units -> whole shares at <= 1.00x of min(cap, capital, buying power):
    floor(min(cap, capital, BP) / price) is the full position, and the book's
    units x unit_fraction scales inside it. No leverage, no borrowing: a buy
    is never sent when buying power cannot cover it.
  * LONG ONLY (the saved tune has allow_short=false; the target is clamped to
    >= 0 on top of that, so a future retune cannot silently start shorting).
  * Entries are maker: a GTC limit resting AT the bid, re-quoted when the bid
    moves. Only a risk-REDUCING move ever exits, and only after
    --cross-after seconds without a fill, as a marketable LIMIT sell a couple
    ticks through the bid (Robinhood's API rejects market orders and
    ioc/fok TIF for stocks). An exit can only sell what is held, which
    bounds it by construction and can never open a short.
  * Broker-side stop: a STOP-LIMIT SELL resting at the book's ATR stop
    (limit 5% below it) on whatever is actually held. It lives at the broker
    between polls (and across restarts) -- the sleeve cancels and replaces it
    whenever the stop or the position changes. A runner restart therefore
    never leaves the position unprotected.
  * Kill switch: the sleeve's OWN P&L (its XE fills since launch + the open
    mark) down --max-loss dollars -> cancel every sleeve order, flatten at
    market, clear the stop, exit. P&L is read from the sleeve's own fills and
    the XE position only -- never account equity, which moves with the manual
    NVDA position and with unsettled debits.
  * Restart continuity: --pnl-since (original launch) + --base-capital
    (capital at that launch); the bar store is seeded ONCE from the shadow
    sleeve's store, so the left edge is the same one the tune was scored on.
  * One process per symbol; RTH only (the tape is RTH); a failed tick logs and
    holds, it never ends the night.
"""

from __future__ import annotations

import argparse
import json
import math
import signal
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from chart_app import profiles
from chart_app.bar_cache import BarCache
from chart_app.perp_live import reduces_risk
from chart_app.perp_sleeve import replay
from chart_app.run_sleeve import _ET, _fetch, _flow_rows, _grace
from shared.chart_data import CandleRecord
from shared.config import robinhood_agentic_account

_ART = Path(__file__).resolve().parent.parent / "artifacts" / "equity_live"
_SHADOW_BARS = (
    Path(__file__).resolve().parent.parent
    / "artifacts"
    / "equity_sleeve"
)
_stop = False

# The one Robinhood account this agent is allowed to trade ("Agentic",
# limited_margin). Jason's own account is off limits to the sleeves. The number
# lives in `.env` (ROBINHOOD_AGENTIC_ACCOUNT), not here -- the repo is public.
AGENTIC_ACCOUNT = robinhood_agentic_account()

# A $16 stock ticks in cents; a marketable exit sits a couple ticks through.
CROSS_BAND = 0.02
TICK = 0.01


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


def _client():
    """The shared pyhood client (cached token first; refresh only if it's dead).

    The cached access token is tried first because pyhood.refresh() ROTATES
    the refresh token -- a concurrent probe or second client would invalidate
    whatever we hold in memory.
    """
    import sys as _sys

    from pyhood import urls as _urls

    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "US_EquityDesk"))
    from rh_session import get_client

    # An EXPIRED cached token loads without raising and only fails on the
    # first read (401 "JWT verification failed", non-JSON body), so probe it
    # before trusting it. Found 2026-09-28: a 7-day-old token killed every
    # stock-card launch at get_positions instead of refreshing.
    try:
        client = client_from_disk(_urls)
        client._session.get(f"https://api.robinhood.com/accounts/{AGENTIC_ACCOUNT}/")
        return client, _urls
    except Exception:
        return get_client(), _urls


def client_from_disk(urls) -> "Any":
    """A client on the CURRENT on-disk session token (no rotation).

    Used at startup and for self-heal: if a concurrent probe rotated the
    refresh token, the on-disk access token is the new one -- re-reading it
    heals a runner whose in-memory token was invalidated.
    """
    from pyhood.auth import TokenStore
    from pyhood.http import Session

    cached = TokenStore().load()
    if not cached or not cached.get("access_token"):
        raise RuntimeError("no cached session token on disk")
    session = Session()
    session.set_auth(cached.get("token_type", "bearer"), cached["access_token"])
    from pyhood.client import PyhoodClient

    return PyhoodClient(session)


def seed_from_shadow(store: BarCache, ticker: str, interval: str) -> int:
    if store.last_ts(ticker, interval) is not None:
        return 0
    src = _SHADOW_BARS / f"{ticker}_bars.db"
    if not src.exists():
        return 0
    records = BarCache(src).load(ticker, interval)
    return store.upsert(ticker, interval, records) if records else 0


def _raw_order(client, urls, order_id: str) -> dict[str, Any]:
    """The broker's own view of one order (pyhood's Order drops partials)."""
    return client._session.get(f"{urls.ORDERS}{order_id}/")


def fetch_order_raws(client, urls, since: datetime) -> dict[str, dict[str, Any]]:
    """ONE paginated orders-list call: order_id -> raw broker record (stock
    orders, created >= since). The raw list items already carry created_at,
    state, quantity, price, stop_price -- so P&L + fees need no per-order
    follow-up fetches. (The old path did 2 raw fetches per order per tick and
    tripped Robinhood's rate limits.)
    """
    out: dict[str, dict[str, Any]] = {}
    try:
        cutoff = client._parse_start_date(since)
        items = client._session.get_paginated(
            urls.ORDERS, params=client._start_date_params(cutoff)
        )
        for item in items:
            if "legs" in item or item.get("legs"):
                continue  # options
            oid = item.get("id")
            if oid:
                out[oid] = item
    except Exception:  # noqa: BLE001 - a rate-limit blip is survivable
        pass
    return out


def _fills_from_raw(raw: dict[str, Any]) -> tuple[float, float | None]:
    """(shares actually executed, average fill price) from one raw order.

    Robinhood quirk: executed_quantity / average_filled_price come back
    None even on fully filled orders (state says filled; quantity/price
    are the real values) -- fall back to those when the fill fields are
    absent.
    """
    state = raw.get("state")
    qty = raw.get("executed_quantity")
    if qty in (None, 0) and state in ("filled", "partially_filled"):
        qty = raw.get("quantity")
    avg = raw.get("average_filled_price")
    if avg in (None, 0) and state in ("filled", "partially_filled"):
        avg = raw.get("price")
    return (float(qty) if qty else 0.0), (float(avg) if avg else None)


def _since_ok_raw(raw: dict[str, Any], since: datetime) -> bool:
    created = raw.get("created_at")
    if not created:
        return True
    try:
        ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
        return ts >= since
    except ValueError:
        return False


def sleeve_pnl(
    order_ids: dict[str, str],
    raws: dict[str, dict[str, Any]],
    xeqty: float,
    mark_price: float | None,
    since: datetime,
    cost_bps: float,
) -> float:
    """The sleeve's own P&L since launch: realized from its fills, marked open.

    Independent of account equity by construction: it only sees XE fills the
    sleeve placed (order ids it chose) and the XE position's mark. A manual
    trade in NVDA, an unsettled debit, or a transfer between accounts cannot
    move it. `xeqty` is the CURRENT XE share count; the average cost is
    rebuilt from the sleeve's own fills, and the open position is marked at
    the live quote (reused from the tick -- no extra API call).
    """
    realized = 0.0
    units = 0.0  # shares the sleeve's own buys still hold
    avg = 0.0
    fees = 0.0
    for oid, side in order_ids.items():
        raw = raws.get(oid)
        if raw is None or not _since_ok_raw(raw, since):
            continue
        qty, avgp = _fills_from_raw(raw)
        if qty <= 0 or avgp is None:
            continue
        fees += qty * avgp * cost_bps / 1e4
        if side == "buy":
            avg = (avg * units + avgp * qty) / (units + qty) if units > 0 else avgp
            units += qty
        else:  # sell: realized against the average cost
            if units > 0 and avg:
                realized += (avgp - avg) * min(qty, units)
            units -= qty
            if units <= 1e-9:
                units, avg = 0.0, 0.0
    open_mark = 0.0
    if xeqty > 0 and avg and mark_price is not None:
        open_mark = (mark_price - avg) * xeqty
    return realized + open_mark - fees


class Executor:
    """Owns the working entry order and the broker-side stop."""

    def __init__(
        self,
        client,
        urls,
        log: Log,
        *,
        live: bool,
        account: str,
        cross_after: float,
        symbol: str,
    ):
        self.symbol = symbol
        self.client = client
        self.urls = urls
        self.log = log
        self.live = live
        self.account = account
        self.cross_after = cross_after
        self.working: dict[str, Any] | None = None  # {order_id, target, price, side, since}
        self.target_since: float | None = None
        self._last_target: int | None = None
        self.stop_id: str | None = None
        self.stop_set: float | None = None
        self.stop_qty: int = 0
        self.paper_pos = 0
        self.order_ids: dict[str, str] = {}  # order_id -> side

    # -- order plumbing -------------------------------------------------

    def _send(self, side: str, qty: int, price: float | None, *, stop: float | None, tif: str, why: str) -> str | None:
        # Robinhood rejects a stop-MARKET sell that carries a price field
        # ("Stop market sell order requested, but price provided" -- pyhood
        # always sends price=stop_price for stop markets). So a resting stop
        # is a stop-LIMIT: trigger at the stop, limit 5% below it (the same
        # collar the app uses on market orders) so a gap through the stop
        # still fills at the limit or better.
        stop_limit_price = None
        if stop is not None and price is None and side == "sell":
            price = round(stop * 0.95, 2)
            stop_limit_price = price
        self.log.write(
            "order",
            {
                "live": self.live,
                "side": side,
                "qty": qty,
                "price": price,
                "stop": stop,
                "tif": tif,
                "why": why,
            },
        )
        verb = ">> ORDER" if self.live else ".. would"
        extra = f" stop@{stop:.2f} limit@{price:.2f}" if stop_limit_price else (f" stop@{stop:.2f}" if stop else "")
        _say(f"{verb} {side.upper():<4} {qty} {self.symbol} @ {price if price else 'MKT'}{extra}  ({why}, {tif})")
        if not self.live:
            return None
        try:
            order = self.client.order_stock(
                self.symbol,
                float(qty),
                side,
                price=price,
                stop_price=stop,
                time_in_force=tif,
                account_number=self.account,
            )
            self.order_ids[order.order_id] = side
            return order.order_id
        except Exception as exc:  # noqa: BLE001 - a reject is survivable
            self.log.write("order_error", {"error": str(exc)})
            _say(f"!! order rejected: {exc}")
            return None

    def _reconcile_working(self, held: int) -> None:
        if not (self.working and self.live):
            return
        try:
            raw = _raw_order(self.client, self.urls, self.working["order_id"])
        except Exception as exc:  # noqa: BLE001
            _say(f"!! order lookup failed: {exc}")
            return
        state = raw.get("state")
        if state in ("filled", "cancelled", "canceled", "rejected", "expired"):
            self.log.write("order_done", {"order_id": self.working["order_id"], "state": state})
            self.working = None

    def _cancel(self, order_id: str | None) -> None:
        if order_id and self.live:
            try:
                self.client.cancel_order(order_id)
            except Exception as exc:  # noqa: BLE001
                _say(f"!! cancel failed: {exc}")

    # -- the tick --------------------------------------------------------

    def step(self, held: int, target: int, bid: float, ask: float, now: float) -> None:
        if self.target_since is None or self._last_target != target:
            self.target_since = now
            self._last_target = target

        self._reconcile_working(held)

        if held == target:
            if self.working:
                _say(f"   (target reached -- cancelling resting {self.working['side']} order)")
                self._cancel(self.working["order_id"])
                self.working = None
            return

        delta = target - held
        cross = (
            reduces_risk(held, target)
            and self.target_since is not None
            and now - self.target_since >= self.cross_after
        )
        qty = min(abs(delta), held) if delta < 0 else abs(delta)
        if qty <= 0:
            return

        if delta > 0:
            # Maker entry at the bid. Robinhood fills whole shares; the
            # quantity is already floored by the caller.
            price = round(bid, 2)
            if self.working:
                same = (
                    self.working["target"] == target
                    and self.working["side"] == "buy"
                    and abs(self.working["price"] - price) < TICK / 2
                )
                if same:
                    return  # still resting at the touch
                self._cancel(self.working["order_id"])
                self.working = None
            oid = self._send("buy", qty, price, stop=None, tif="gtc", why="maker-entry")
            if self.live and oid:
                self.working = {
                    "order_id": oid,
                    "target": target,
                    "price": price,
                    "side": "buy",
                    "since": now,
                }
            else:
                self.paper_pos = target
        else:
            # Exit: risk-REDUCING sell. Robinhood's API rejects MARKET orders
            # (and ioc/fok TIF) -- the one order type proven to work here is
            # limit+gtc (the entry buy filled at the bid). So an exit is a
            # marketable LIMIT sell a couple ticks through the bid: it fills
            # immediately while the market is at/above it, and it rests
            # (never force-fills at a worse price) if the tape drops. It only
            # ever sells shares the sleeve holds, so it can never open a
            # short. Tracked in self.working so a resting exit is never
            # duplicated.
            if not cross:
                if not self.working:
                    _say(f"   (exit of {qty} armed, crossing not allowed until +{self.cross_after:.0f}s)")
                return
            price = max(TICK, round(bid - CROSS_BAND, 2))
            # A resting stop COMMITs its shares at the broker; a second sell
            # for the same shares is rejected "Not enough shares to sell".
            # Free the stop before the exit, then re-place it on the shares
            # left (sync_stop sizes it on held - the resting exit qty).
            if self.stop_id:
                self._cancel(self.stop_id)
                self.stop_id = None
                self.stop_set = None
                self.stop_qty = 0
            if self.working:
                same = (
                    self.working["side"] == "sell"
                    and self.working["target"] == target
                    and abs(self.working["price"] - price) < TICK / 2
                )
                if same:
                    return  # still resting at the marketable touch
                self._cancel(self.working["order_id"])
                self.working = None
            oid = self._send("sell", qty, price, stop=None, tif="gtc", why="exit")
            if self.live and oid:
                self.working = {
                    "order_id": oid,
                    "target": target,
                    "price": price,
                    "side": "sell",
                    "qty": qty,
                    "since": now,
                }
            else:
                self.paper_pos = target

    def sync_stop(self, held: int, book_stop: float | None) -> None:
        # A resting exit sell commits its shares too, so the stop can only
        # protect the remainder (held - resting exit qty).
        exit_qty = 0
        if self.working and self.working.get("side") == "sell":
            exit_qty = int(self.working.get("qty") or 0)
        protected = max(0, held - exit_qty)
        want = round(book_stop, 2) if (protected > 0 and book_stop) else None
        # Re-place when the price OR the protected share count changes: a stop
        # resting for 2 shares does not protect a 3rd that filled meanwhile.
        if want == self.stop_set and protected == self.stop_qty:
            return
        if want is None:
            _say(f"{'>> STOP' if self.live else '.. would'} clear stop")
            self._cancel(self.stop_id)
            self.stop_id = None
            self.stop_qty = 0
        else:
            _say(f"{'>> STOP' if self.live else '.. would'} set stop-limit SELL {want:.2f} (limit {round(want * 0.95, 2)}) on {protected} sh")
            self._cancel(self.stop_id)
            self.stop_id = None
            oid = self._send("sell", protected, None, stop=want, tif="gtc", why="broker-side stop")
            if self.live:
                self.stop_id = oid
            self.stop_qty = protected
        self.log.write("stop", {"price": want, "position": protected})
        self.stop_set = want

    def flatten(self, held: int, bid: float, ask: float) -> None:
        """Kill switch: cancel everything the sleeve owns, exit fast.

        A marketable limit sell through the bid -- Robinhood's API rejects
        market orders, and a limit at bid-CROSS_BAND fills immediately while
        still protecting against a tape gap.
        """
        self._cancel(self.working["order_id"] if self.working else None)
        self.working = None
        if self.live:
            # Cancel every resting XE order the sleeve knows about.
            for oid in list(self.order_ids):
                self._cancel(oid)
        if held > 0:
            price = max(TICK, round(bid - CROSS_BAND, 2))
            self._send("sell", held, price, stop=None, tif="gtc", why="kill-flatten (marketable limit)")
        self.sync_stop(0, None)


# Robinhood order states that can still fill.
OPEN_STATES = ("queued", "unconfirmed", "confirmed", "partially_filled", "pending")


def resting_entries(
    raws: dict[str, dict[str, Any]], instrument_url: str, account: str
) -> list[str]:
    """Order ids to cancel when a sleeve is stopped: this ticker's open
    orders in this account, EXCEPT the protective stop.

    The stop stays at the broker on purpose -- Stop never flattens, and the
    held position must keep its bracket, same as a perp. Everything else a
    killed runner leaves behind (a GTC maker buy resting at the bid, a
    marketable exit) would otherwise sit there and fill with nobody watching.
    """
    out = []
    for oid, raw in raws.items():
        if raw.get("instrument") != instrument_url:
            continue
        if not str(raw.get("account") or "").rstrip("/").endswith(f"/{account}"):
            continue
        if raw.get("state") not in OPEN_STATES:
            continue
        if raw.get("trigger") == "stop" or raw.get("stop_price"):
            continue
        out.append(oid)
    return out


def cancel_resting(ticker: str, account: str = AGENTIC_ACCOUNT, days: int = 30) -> int:
    """Cancel a stopped sleeve's resting non-stop orders. Used by the dock."""
    if account != AGENTIC_ACCOUNT:
        raise ValueError(f"refusing account {account}: sleeves trade only {AGENTIC_ACCOUNT}")
    client, urls = _client()
    since = datetime.now(UTC) - timedelta(days=days)
    inst = client._get_instrument_url(ticker.strip().upper())
    # Not fetch_order_raws: it swallows a failed read as "no orders", which
    # here would let the dock kill the runner with its buy still resting.
    items = client._session.get_paginated(
        urls.ORDERS, params=client._start_date_params(client._parse_start_date(since))
    )
    raws = {i["id"]: i for i in items if i.get("id") and not i.get("legs")}
    ids = resting_entries(raws, inst, account)
    for oid in ids:
        client.cancel_order(oid)
    return len(ids)


def _account_equity(client, account: str, positions) -> float:
    """Cash plus every position's equity in the account, read now."""
    acct = client._session.get(f"https://api.robinhood.com/accounts/{account}/")
    return float(acct.get("portfolio_cash") or 0) + sum(float(p.equity or 0) for p in positions)


def _held_value(positions, ticker: str) -> float:
    return sum(float(p.equity or 0) for p in positions if p.symbol == ticker)


def _cap(args, equity: float) -> float:
    """The sleeve's dollar cap: its share of live equity, or the fixed cap."""
    if args.cap_fraction is not None:
        return max(0.0, args.cap_fraction * equity)
    return float(args.cap_dollars)


def sleeve_budget(cap: float, buying_power: float, held: int, price: float) -> float:
    """What this sleeve may hold, in dollars: its cap, bounded by the money it
    can actually reach -- the shares it already holds plus free buying power.
    """
    return min(cap, max(0.0, buying_power) + max(0, held) * max(0.0, price))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--ticker", default="XE")
    ap.add_argument("--interval", default="5m")
    size = ap.add_mutually_exclusive_group(required=True)
    size.add_argument(
        "--cap-fraction",
        type=float,
        help="share of the agentic account's LIVE equity this sleeve may hold, re-read "
        "every tick; shares across running stock sleeves should sum to <= 1.0",
    )
    size.add_argument("--cap-dollars", type=float, help="fixed max notional at 1.00x")
    ap.add_argument(
        "--max-loss",
        type=float,
        default=0.0,
        help="kill switch, $ below launch capital (default 25%% of min(cap, capital))",
    )
    ap.add_argument("--live", action="store_true", help="send real orders; without it nothing is sent")
    ap.add_argument(
        "--account", default=AGENTIC_ACCOUNT, help="Robinhood account; only the agentic one is accepted"
    )
    ap.add_argument("--tick-seconds", type=int, default=20)
    ap.add_argument("--bars-every", type=int, default=60, help="seconds between bar re-fetches")
    ap.add_argument(
        "--cross-after", type=float, default=60.0, help="seconds before an exit may cross"
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
        help="the sleeve's capital at --pnl-since (default: account equity now)",
    )
    args = ap.parse_args(argv)

    args.ticker = args.ticker.strip().upper()
    if not AGENTIC_ACCOUNT:
        raise SystemExit(
            "ROBINHOOD_AGENTIC_ACCOUNT is not set in .env: refusing to guess which "
            "account the sleeve may trade (it only ever trades the agentic one)"
        )
    if args.account != AGENTIC_ACCOUNT:
        raise SystemExit(
            f"refusing account {args.account}: the sleeves trade only the agentic "
            f"account {AGENTIC_ACCOUNT}"
        )
    if args.cap_fraction is not None and not 0.0 < args.cap_fraction <= 1.0:
        raise SystemExit(f"--cap-fraction must be in (0, 1], got {args.cap_fraction}")

    _ART.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M")
    log = Log(_ART / f"{args.ticker}_{'live' if args.live else 'dry'}_{stamp}.jsonl")
    # One store per ticker: several sleeves run at once, and two processes
    # racing one SQLite file is the "database is locked" failure.
    store_name = f"{args.ticker}_bars.db"
    store = BarCache(_ART / store_name)

    client, urls = _client()
    profile = profiles.resolve(args.ticker, args.interval)
    want = f"{args.ticker}|{args.interval}"
    if profile.get("source") != want:
        # resolve() falls back to *|interval: a sleeve must never trade a
        # ticker on somebody else's tune.
        raise SystemExit(f"no saved profile {want} (would run {profile.get('source')}); not starting")
    cfg = profile.get("config") or {}
    max_units = int(cfg.get("max_units", 3))
    unit_fraction = float(cfg.get("unit_fraction") or (1.0 / max_units))
    notional_x = max_units * unit_fraction
    if notional_x > 1.0001:
        raise SystemExit(
            f"profile is {notional_x:.2f}x notional; live trading runs at 1.00x max "
            "(set unit_fraction = 1/max_units)"
        )
    if cfg.get("allow_short"):
        _say("!! profile has allow_short=true -- this runner is LONG ONLY and will clamp it")

    positions0 = client.get_positions(nonzero=True, account_number=args.account)
    equity0 = _account_equity(client, args.account, positions0)
    base_capital = args.base_capital or equity0
    since = (
        datetime.fromisoformat(args.pnl_since)
        if args.pnl_since
        else datetime.now(UTC)
    )
    bp0 = client.get_buying_power(args.account)
    # Kill switch sized to the capital the sleeve can ACTUALLY deploy now
    # (live buying power), not the unfunded cap -- with $2k still pending the
    # cap would make the backstop unreachable. Pass --max-loss to override.
    cap0 = _cap(args, equity0)
    max_loss = args.max_loss or 0.25 * min(
        cap0, max(0.0, bp0) + _held_value(positions0, args.ticker)
    )
    held0 = 0
    for p in positions0:
        if p.symbol == args.ticker:
            held0 = int(round(float(p.quantity or 0)))

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    _say("=" * 96)
    _say(
        f"EQUITY SLEEVE LIVE  {args.ticker} {args.interval} -> Robinhood {args.account}  "
        f"{'*** REAL ORDERS ***' if args.live else 'DRY RUN, nothing sent'}   LONG ONLY"
    )
    _say(
        f"profile {profile.get('source')} saved {profile.get('saved_at')}  "
        f"validation={profile.get('validation')}  max_units={max_units} x unit_fraction={unit_fraction} = {notional_x:.2f}x"
    )
    _say(
        f"account equity ${base_capital:,.2f} at {since:%m-%d %H:%MZ}  cap ${cap0:,.2f}"
        f"{f' ({args.cap_fraction:.0%} of equity, re-read every tick)' if args.cap_fraction else ''}  "
        f"kill at P&L -${max_loss:,.2f}  maker entries, marketable limit exits, broker-side stop-limit"
    )
    if args.live and max(0.0, bp0) < TICK and not held0:
        _say(f"!! account can't buy even one share (BP ${bp0:,.2f}). Not starting.")
        return 2
    seeded = seed_from_shadow(store, args.ticker, args.interval)
    _say(
        f"bar store {store_name}: {'seeded ' + str(seeded) + ' bars from the shadow sleeve' if seeded else 'left edge already fixed'}"
    )
    _say(f"journal {log.path}")
    _say("=" * 96)
    log.write(
        "session",
        {
            "live": args.live,
            "account": args.account,
            "cap": cap0,
            "cap_fraction": args.cap_fraction,
            "ticker": args.ticker,
            "max_loss": max_loss,
            "base_capital": base_capital,
            "pnl_since": since.isoformat(),
            "config": cfg,
            "elmo": profile.get("elmo") or {},
        },
    )

    ex = Executor(
        client,
        urls,
        log,
        live=args.live,
        account=args.account,
        cross_after=args.cross_after,
        symbol=args.ticker,
    )
    if args.live:
        # Restart continuity: register the sleeve's XE orders that already
        # exist at the broker and adopt a resting stop order. The orders
        # endpoint returns no symbol field (only the instrument URL), so
        # match on that.
        try:
            inst = client._get_instrument_url(args.ticker)
            for o in client.get_stock_orders(start_date=since):
                if o.side not in ("buy", "sell"):
                    continue
                raw = _raw_order(client, urls, o.order_id)
                if raw.get("instrument") != inst:
                    continue
                ex.order_ids[o.order_id] = o.side
                if (
                    o.side == "sell"
                    and o.stop_price
                    and o.status in ("confirmed", "pending")
                ):
                    ex.stop_id = o.order_id
                    ex.stop_set = round(o.stop_price, 2)
                    ex.stop_qty = int(round(o.quantity))
                    _say(
                        f"adopted resting stop {ex.stop_set:.2f} on {ex.stop_qty} sh "
                        f"({o.order_id[:8]})"
                    )
            if ex.order_ids:
                _say(f"seeded {len(ex.order_ids)} prior {args.ticker} order(s) from the broker")
        except Exception as exc:  # noqa: BLE001 - a read failure is survivable
            _say(f"!! could not seed prior orders: {exc}")
    deadline = time.time() + args.hours * 3600 if args.hours else None
    book: dict[str, Any] | None = None
    last_bars = 0.0
    last_line = ""
    market_open: bool | None = None
    last_open_check = 0.0
    fails = 0
    while not _stop and not (deadline and time.time() >= deadline):
        try:
            now = time.time()
            if now - last_bars >= args.bars_every or book is None:
                fetched: list[CandleRecord] = _fetch(args.ticker, args.interval, "5d")
                store.upsert(args.ticker, args.interval, fetched)
                records = store.load(args.ticker, args.interval)
                flow = _flow_rows(args.ticker, records)
                book = replay(
                    records,
                    profile,
                    interval=args.interval,
                    tz=_ET,
                    grace_s=_grace(args.ticker),
                    flow_rows=flow,
                ).as_dict()
                last_bars = now

            quote = client.get_quote(args.ticker)
            bid, ask = float(quote.bid or quote.price), float(quote.ask or quote.price)
            positions = client.get_positions(nonzero=True, account_number=args.account)
            xepos = [p for p in positions if p.symbol == args.ticker]
            held = int(round(float(xepos[0].quantity))) if xepos else 0
            bp = client.get_buying_power(args.account)
            # Market open changes ~never; re-check at most every 15 min.
            if market_open is None or now - last_open_check >= 900:
                market_open = client.is_market_open()
                last_open_check = now
            # One batched orders-list fetch per tick feeds P&L + fees (the
            # old path fetched each order 2x per tick and tripped limits).
            raws = fetch_order_raws(client, urls, since)
            # The sleeve's own P&L: its XE fills since launch + the open mark.
            # Independent of account equity (which moves with NVDA, debits).
            own = sleeve_pnl(
                ex.order_ids,
                raws,
                float(xepos[0].quantity) if xepos else 0.0,
                quote.price,
                since,
                2.0,
            )
            # Sleeve capital: the cap bounded by the money that ACTUALLY exists
            # right now (live buying power) plus the sleeve's own P&L. Buying
            # power is the binding constraint while the $2k is still coming in,
            # so the position scales up on its own as the deposit lands -- and
            # a buy is never sent for more than the broker will let it buy.
            #
            # Fixed 2026-09-26: the budget used to be `bp + own`, which leaves
            # out the shares already held. Once a sleeve was fully in, BP fell
            # to ~0, `full` fell to 0 and the book's target said SELL what it
            # had just bought. What the sleeve can hold is its own position's
            # value plus the free cash -- and several sleeves now share that
            # cash, so the cap (a share of live account equity) is what keeps
            # each one to its slice.
            if not args.live:
                held = ex.paper_pos
            cap = _cap(args, _account_equity(client, args.account, positions))
            capital = sleeve_budget(cap, bp, held, bid)
            full = math.floor(max(0.0, capital) / bid) if bid > 0 else 0
            units = float(book.get("pending") and book["pending"]["units_after"] or book.get("units") or 0.0)
            units = max(0.0, units)  # LONG ONLY
            target = int(max(0, min(full, round(units * unit_fraction * full))))

            if args.live and -own >= max_loss:
                _say(f"!! KILL SWITCH: sleeve P&L ${own:+,.2f} since {since:%m-%d %H:%MZ}")
                log.write("kill", {"pnl": own, "position": held, "bid": bid, "ask": ask})
                ex.flatten(held, bid, ask)
                break

            if market_open:
                ex.step(held, target, bid, ask, now)
                ex.sync_stop(held, book.get("stop"))
            else:
                _say("   (market closed -- holding; broker-side stop stays resting)")

            line = (
                f"bar {str(book.get('last_ts'))[-14:]}  score {book.get('score', 0):5.1f}  "
                f"book {units:+.0f}u -> target {target:+d} sh (full {full}, BP ${bp:,.2f})  held {held:+d} sh  "
                f"sleeve pnl ${own:+,.2f}  stop {book.get('stop') or 0:,.2f}  bid {bid:.2f}/ask {ask:.2f}"
            )
            if line != last_line:
                _say(line)
                last_line = line
            log.write(
                "mark",
                {
                    "units": units,
                    "target": target,
                    "held": held,
                    "buying_power": bp,
                    "pnl": own,
                    "bid": bid,
                    "ask": ask,
                    "stop": book.get("stop"),
                    "bar": book.get("last_ts"),
                },
            )
        except Exception as exc:  # noqa: BLE001 - a bad tick must not end the night
            fails += 1
            log.write("error", {"error": f"{type(exc).__name__}: {exc}"})
            _say(f"!! tick failed ({type(exc).__name__}: {exc}) -- holding, will retry")
            # Self-heal: if failures persist, the on-disk session token may have
            # been rotated out from under us -- rebuild the client from disk.
            if fails >= 3:
                try:
                    client = client_from_disk(urls)
                    ex.client = client
                    _say("   (rebuilt client from on-disk session token)")
                    fails = 0
                except Exception as re:  # noqa: BLE001
                    _say(f"   (rebuild failed: {re})")
            # Robinhood rate-limits with empty responses; back off so the
            # session can recover instead of hammering it every 20s.
            time.sleep(min(60.0, 10.0 * fails))
            continue
        fails = 0
        waited = 0.0
        while waited < args.tick_seconds and not _stop:
            time.sleep(1.0)
            waited += 1.0

    if _stop and ex.working:
        ex._cancel(ex.working["order_id"])
    _say(
        "stopped. The broker-side stop remains resting at the broker on any open position."
    )
    log.write("session_end", {})
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
