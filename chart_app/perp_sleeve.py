"""An overnight shadow book for one perpetual swap, running a saved profile.

What this is
------------
A sleeve that watches a perp with EXACTLY the profile the chart is drawing --
same `profiles.resolve`, same `conviction_series`, same `run_state_machine` --
and writes down what that algo would have done, with fills, a stop and a P&L,
so the morning reads as a book rather than as a screenshot.

What this is NOT
----------------
It does not trade. There is no order path in this repo to reach for: every
venue in `crypto_source` is a public, unauthenticated, read-only market-data
endpoint, no key and no signing. That is a deliberate property, not a gap --
the conviction engine's out-of-sample record does not support an autotrader
(README, "the evidence against it"), and a profile saved from a slider drag is
an in-sample fit that says so in its own `validation` field. So the sleeve
produces a decision log a human acts on, or does not.

Why it replays instead of accumulating
--------------------------------------
The obvious build is a long-lived object that remembers its position and
mutates it each tick. That object drifts: a missed poll, a venue timeout, a
restart at 3am, and the book and the chart disagree with nothing to say which
is right.

So the book is a PURE FUNCTION of (bar history, profile). Every tick replays
the whole window from scratch and re-derives every fill. A restart reproduces
the identical book; a missed tick costs nothing, because the bars that arrived
while we were away are still there to be replayed. The journal is then just a
diff -- fills we have not written down yet -- which makes "the process died and
came back" indistinguishable from "it never died".

The fill convention, which is the honest part
---------------------------------------------
`run_state_machine` decides on bar `i`'s CLOSE (that is what you want on a
chart -- the mark sits on the bar that triggered). You cannot trade that close;
it is gone by the time you have seen it. So a decision on bar `i` fills at bar
`i+1`'s OPEN, which is `backtest.py`'s convention and the reason its P&L is
worth anything.

Live, that has a consequence worth stating plainly: the newest closed bar is a
DECISION, not yet a fill. The sleeve reports it as `pending` and fills it on
the next bar. Anything else would be reading a price we could not have got.

And the bar still forming is dropped entirely. A 15m candle at minute 3 can
print any close it likes by minute 15; acting on it is repainting.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from chart_app.backtest import run_backtest
from chart_app.elmo import compute_elmo
from chart_app.signal_engine import DEFAULTS, conviction_series, run_state_machine
from shared.chart_data import CandleRecord

# Minutes per bar, for deciding whether the newest bar has closed.
_INTERVAL_MIN: dict[str, int] = {
    "1m": 1,
    "3m": 3,
    "5m": 5,
    "10m": 10,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
}

# Round-trip cost in bp of notional per side. Perp takers on OKX pay ~5bp;
# `backtest.DEFAULT_COST_BPS` is 2bp, which is an equity-ETF number and too
# kind to a venue that also charges funding. Stated here rather than imported
# so the sleeve's P&L cannot silently inherit the wrong venue's fees.
COST_BPS = 5.0
MAKER_COST_BPS = 2.0


def bar_has_closed(
    record: CandleRecord,
    interval: str,
    now: datetime,
    *,
    tz: tzinfo = UTC,
    grace_s: float = 0.0,
) -> bool:
    """Whether this candle is complete as of `now`.

    A bar stamped 00:15 on a 15m tape covers 00:15-00:30 and is final only
    once 00:30 has passed.

    `tz` is the zone a NAIVE timestamp is in. Crypto bars are UTC; ThetaData
    equity bars are naive US/Eastern wall-clock. Reading an ET 10:00 bar as
    10:00 UTC puts it four hours in the past, so the still-forming bar would
    count as closed -- repainting, the one thing this check exists to stop.

    `grace_s` covers a feed that builds the bar from late-arriving pieces:
    an equity 5m bar is aggregated from 1m rows, and the last 1m row can land
    after the 5m boundary. Calling the bar final before then would act on a
    close that is still going to move.
    """
    minutes = _INTERVAL_MIN.get(interval)
    if minutes is None:
        return True
    ts = record.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=tz)
    return (ts + timedelta(minutes=minutes, seconds=grace_s)) <= now


def closed_bars(
    records: list[CandleRecord],
    interval: str,
    now: datetime,
    *,
    tz: tzinfo = UTC,
    grace_s: float = 0.0,
) -> list[CandleRecord]:
    """`records` with any still-forming tail bar removed."""
    out = list(records)
    while out and not bar_has_closed(out[-1], interval, now, tz=tz, grace_s=grace_s):
        out.pop()
    return out


@dataclass
class Fill:
    """One executed change in position, priced at the bar we could have got."""

    ts: str  # the bar whose OPEN we filled at
    decision_ts: str  # the bar whose CLOSE triggered it
    action: str  # enter_long / add / trim / exit / reverse
    side: str  # long | short | flat
    price: float
    units_before: float
    units_after: float
    reason: str
    stop: float | None
    score: float

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class Book:
    """The sleeve's state -- derived every tick, never accumulated.

    P&L is NOT computed here. It comes from `backtest.run_backtest` over the
    same bars and config, and the reason is a measured one. The sleeve first
    kept its own trade-level book -- P&L booked at each reduction against an
    average entry -- and on the saved BTC-PERP tune's own year it read
    +133.8%, then +113.2% once compounded, where the tester at the same 5bp
    cost says +49.5%.

    The residual is not an arithmetic slip, it is volatility drag. The tester
    marks the position EVERY BAR at the leverage actually held, so variance
    compounds against a 5x book roughly as -(sigma^2/2)(L^2 - L) per bar.
    Trade-level accounting never sees it, because it only looks at entry and
    exit. At `unit_fraction=1.0, max_units=5` that gap is most of the number.

    So there is one P&L implementation in this repo and the sleeve quotes it.
    What the sleeve owns is the things a backtest has no concept of: where the
    book is RIGHT NOW, what its stop is, and what it has not filled yet.
    """

    units: float = 0.0
    avg_entry: float = 0.0
    stop: float | None = None
    fills: list[Fill] = field(default_factory=list)
    pending: dict[str, Any] | None = None  # decided, not yet fillable
    last_close: float = 0.0
    last_ts: str = ""
    score: float = 0.0
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def side(self) -> str:
        return "long" if self.units > 0 else "short" if self.units < 0 else "flat"

    def total_pct(self) -> float:
        """Whole-window return, marked every bar, open position included."""
        return float(self.metrics.get("total_return_pct", 0.0))

    def open_move_pct(self) -> float:
        """The OPEN position's own move, entry to last close, at its leverage.

        Deliberately not called "unrealized P&L": it is the move on the
        position, not a decomposition of `total_pct`, which is marked per bar
        and already contains it.
        """
        if not self.units or not self.avg_entry:
            return 0.0
        return 100.0 * (self.last_close - self.avg_entry) / self.avg_entry * self.units

    def as_dict(self) -> dict[str, Any]:
        m = self.metrics
        return {
            "units": self.units,
            "side": self.side,
            "notional_x": abs(self.units),
            "avg_entry": self.avg_entry,
            "stop": self.stop,
            "last_close": self.last_close,
            "last_ts": self.last_ts,
            "score": self.score,
            "open_move_pct": self.open_move_pct(),
            "total_pct": self.total_pct(),
            "buy_hold_pct": float(m.get("buy_hold_pct", 0.0)),
            "max_drawdown_pct": float(m.get("max_drawdown_pct", 0.0)),
            "sharpe": float(m.get("sharpe", 0.0)),
            "trades": int(m.get("trades", 0)),
            "cost_bps": COST_BPS,
            "fills": len(self.fills),
            "pending": self.pending,
        }


def _action_name(before: float, after: float) -> str:
    """Name a units transition, in the vocabulary the chart already uses."""
    if before == 0 and after != 0:
        return "enter_long" if after > 0 else "enter_short"
    if after == 0 and before != 0:
        return "exit"
    if before * after < 0:
        return "reverse"
    return "add" if abs(after) > abs(before) else "trim"


def replay(
    records: list[CandleRecord],
    profile: dict[str, Any],
    *,
    interval: str = "15m",
    now: datetime | None = None,
    tz: tzinfo = UTC,
    grace_s: float = 0.0,
    flow_rows: list[dict[str, Any]] | None = None,
) -> Book:
    """Rebuild the whole book from bars. Deterministic; the only state there is.

    Returns an empty book rather than raising when there is not enough history
    to seed the indicators -- a sleeve with nothing to say must still report.

    `flow_rows` is the raw whale tape (`scanner_trades` rows) for an equity.
    Binned HERE, against the closed bars actually scored, so the whale
    component sees exactly what the chart and tester feed it. Omitting it
    (every perp) drops whale from the score's denominator, as before.
    """
    now = now or datetime.now(UTC)
    bars = closed_bars(list(records), interval, now, tz=tz, grace_s=grace_s)
    book = Book()
    if len(bars) < 60:
        return book

    cfg = {**DEFAULTS, **(profile.get("config") or {})}
    elmo = compute_elmo(bars, **(profile.get("elmo") or {}))
    flow_net = flow_observed = None
    if flow_rows:
        from chart_app.flow_pane import bin_flow
        from chart_app.flow_stamp import flow_observed_bars

        flow_net = bin_flow(bars, flow_rows)["net"]
        flow_observed = flow_observed_bars(bars, flow_rows)
    conv = conviction_series(
        bars, elmo=elmo, flow_net=flow_net, flow_observed=flow_observed, config=cfg
    )
    run = run_state_machine(bars, conv, config=cfg)

    pos = run.position
    n = len(bars)
    units = 0.0
    avg = 0.0

    def _score(i: int) -> float:
        return float(conv.score[i]) if i < len(conv.score) else 0.0

    # Decision on bar i, fill at bar i+1's open. The final closed bar is a
    # decision with no fill bar yet -- that is `pending`, below.
    for i in range(n - 1):
        target = float(pos[i])
        if target == units:
            continue
        fill_bar = bars[i + 1]
        price = float(fill_bar.open)
        action = _action_name(units, target)
        reason = "atr_stop" if "stop" in str(run.actions[i]) else "signal"

        # Average entry only -- P&L is the tester's job, see `Book`.
        if units * target < 0:  # reversal: the new side opens fresh
            avg = price
        elif target == 0:
            avg = 0.0
        elif abs(target) > abs(units):
            added = target - units
            avg = price if units == 0 else (avg * units + price * added) / target

        book.fills.append(
            Fill(
                ts=fill_bar.timestamp.isoformat(),
                decision_ts=bars[i].timestamp.isoformat(),
                action=action,
                side="long" if target > 0 else "short" if target < 0 else "flat",
                price=price,
                units_before=units,
                units_after=target,
                reason=reason,
                stop=(run.stop[i] if i < len(run.stop) else None),
                score=_score(i),
            )
        )
        units = target

    book.units = units
    book.avg_entry = avg
    book.stop = run.stop[n - 1] if run.stop else None
    book.last_close = float(bars[-1].close)
    book.last_ts = bars[-1].timestamp.isoformat()
    book.score = _score(n - 1)
    # One P&L implementation, reusing the ELMo we already paid for.
    book.metrics = run_backtest(
        bars,
        interval=interval,
        config=cfg,
        elmo=elmo,
        cost_bps=COST_BPS,
        flow_net=flow_net,
        flow_observed=flow_observed,
    ).metrics
    # The tester scores at backtest's 2bp default, which is also the Kalshi
    # maker fee live entries pay. Reported beside COST_BPS so the log and the
    # tester can be compared number for number (they looked like a bug at
    # +1.45% vs +17.7% on a 692-trade tune -- same P&L, different fee).
    book.metrics["total_pct_maker"] = run_backtest(
        bars,
        interval=interval,
        config=cfg,
        elmo=elmo,
        cost_bps=MAKER_COST_BPS,
        flow_net=flow_net,
        flow_observed=flow_observed,
    ).metrics["total_return_pct"]

    # The newest closed bar decided something we cannot fill until the next
    # bar opens. Report it rather than filling at a price we could not reach.
    if float(pos[n - 1]) != units:
        book.pending = {
            "decision_ts": bars[-1].timestamp.isoformat(),
            "action": _action_name(units, float(pos[n - 1])),
            "units_after": float(pos[n - 1]),
            "score": book.score,
        }
    return book


def session_pnl(
    book: Book,
    since: datetime,
    carry_price: float | None,
    *,
    capital: float,
    unit_fraction: float,
    cost_bps: float,
) -> float:
    """Dollars the sleeve made since `since`: its fills, plus the open mark.

    The equity analogue of `perp_live.sleeve_pnl` (2026-09-21). The book line
    used to print only the WHOLE REPLAY WINDOW backtest -- months of history
    the sleeve never ran through -- so there was no number for "what has it
    done since I turned it on". This walks the book's own fills after `since`
    at 1x (`unit_fraction` of `capital` per unit), charges `cost_bps` on every
    unit that changes hands, and marks what is left at the last close.

    A position already open at `since` is carried in at `carry_price` (the
    last close before `since`), so a restart mid-trade does not book the whole
    trade's P&L as if it happened tonight. `since` is naive, in the bars' zone.
    """
    per_unit = capital * unit_fraction
    units = 0.0
    ref = carry_price
    pnl = 0.0
    for fill in book.fills:
        ts = datetime.fromisoformat(fill.ts).replace(tzinfo=None)
        if ts < since:
            units = fill.units_after
            continue
        if units and ref:
            pnl += units * per_unit * (fill.price / ref - 1.0)
        pnl -= abs(fill.units_after - units) * per_unit * cost_bps / 1e4
        units = fill.units_after
        ref = fill.price
    if units and ref and book.last_close:
        pnl += units * per_unit * (book.last_close / ref - 1.0)
    return pnl


class Journal:
    """Append-only JSONL, deduped by (decision_ts, ts).

    Dedupe is what lets `replay` re-run from scratch every tick without the
    log growing a copy of history each time -- and it is why a restart is
    invisible in the output rather than a block of repeated fills.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seen: set[tuple[str, str]] = set()
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("kind") == "fill":
                    self._seen.add((row.get("decision_ts", ""), row.get("ts", "")))

    def write(self, kind: str, payload: dict[str, Any]) -> None:
        row = {
            "kind": kind,
            "logged_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        row.update(payload)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")

    def new_fills(self, book: Book) -> list[Fill]:
        """Fills not yet journalled, marking them seen."""
        out = [f for f in book.fills if (f.decision_ts, f.ts) not in self._seen]
        for fill in out:
            self._seen.add((fill.decision_ts, fill.ts))
        return out

    def seed(self, book: Book, started_at: datetime, *, tz: tzinfo = UTC) -> int:
        """Record fills that predate the session as history, not as news.

        `replay` needs the whole lookback window to seed EMA200 and the
        200-bar ranks, so the first tick legitimately re-derives a month of
        fills. Announcing those as live events would make the morning log
        open with thirty days of trades stamped 00:42 -- true as history,
        false as a claim about tonight. They are written once under
        `prior_fill` and marked seen, so tonight's log contains tonight.
        """
        # Compared in the zone the bars are stamped in (see `bar_has_closed`).
        cut = started_at.astimezone(tz).replace(tzinfo=None)
        count = 0
        for fill in book.fills:
            key = (fill.decision_ts, fill.ts)
            if key in self._seen:
                continue
            ts = datetime.fromisoformat(fill.ts)
            if ts.tzinfo is not None:
                ts = ts.astimezone(tz).replace(tzinfo=None)
            if ts >= cut:
                continue
            self._seen.add(key)
            self.write("prior_fill", fill.as_dict())
            count += 1
        return count
