"""Backtest the chart's conviction engine. Honest fills, no lookahead.

Design rules, in order of how badly violating them would lie to you:

1. **Fill on the next bar's open.** The state machine decides on bar `i`'s
   close. You cannot trade that close; you can trade bar `i+1`'s open. Every
   backtest that fills at the signal bar's own close is reporting a return no
   one could have taken, and it flatters a mean-reversion component most --
   which this engine has (RSI, %B).

2. **Costs are on by default.** `cost_bps` is charged on both sides. A 15m
   strategy that trades 200 times a year dies or lives entirely on this
   number, so making it opt-in would be a way of hiding the answer.

3. **The ATR stop is checked intrabar against the low/high**, and filled at
   the stop level, not the close. Filling a stop at the close makes a trailing
   stop look far better than it is on a gap.

4. **Every result is compared against buy-and-hold on the same bars**, and
   against a null built by shuffling the returns. A strategy that beats
   neither is not a strategy.

Entry points
------------
`run_backtest(records, ...)`   one symbol, one parameter set
`sweep(records, grid, ...)`    cartesian sweep, ranked
`walk_forward(records, grid)`  rolling in-sample fit, out-of-sample score
`permutation_test(records)`    is the edge distinguishable from luck
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from chart_app.elmo import ElmoResult, compute_elmo
from chart_app.flow_pane import bin_flow
from chart_app.signal_engine import (
    DEFAULTS,
    atr_series,
    conviction_series,
)
from chart_app.sizing import MAX_BORROW, order_quantity, resolve_sizing, venue_for
from shared.chart_data import CandleRecord

# Bars per year per interval, for annualising Sharpe and CAGR. A US equity
# session is 6.5 hours; 252 sessions a year. This table is the FALLBACK --
# `_bars_per_year` measures the real thing off the timestamps. See there.
_SESSIONS = 252
BARS_PER_YEAR: dict[str, float] = {
    "3m": 130 * _SESSIONS,
    "5m": 78 * _SESSIONS,
    "10m": 39 * _SESSIONS,
    "15m": 26 * _SESSIONS,
    "30m": 13 * _SESSIONS,
    "1h": 6.5 * _SESSIONS,
    "4h": 2 * _SESSIONS,
    "1d": float(_SESSIONS),
}


def _bars_per_year(records: Sequence[CandleRecord], interval: str) -> float:
    """Bars a year, MEASURED off the timestamps, with `BARS_PER_YEAR` as fallback.

    The table assumes a 6.5-hour US equity session. A perpetual swap trades
    24/7, so `15m` is 96 bars a day there and 26 here -- a 3.7x error in the
    denominator of every annualised figure. Measured live 2026-09-20 on the
    cached BTC-PERP 15m window (35,178 bars, 2025-09-19 -> 2026-09-21):

        span              1.003 years (the bars say so)
        table says        35178 / 6552 = 5.37 years
        so CAGR read      21.9%  on a year whose total return was +189%
        and Sharpe read   1 / sqrt(5.35) = 0.43x of the true figure

    Both errors are conservative, which is why this survived a year of use --
    but it makes a crypto number and an equity number incomparable, and CAGR
    is the field a tune gets judged on.

    Measuring instead of extending the table is the point: the bars already
    know their own spacing, and a lookup keyed on interval alone cannot tell a
    24/7 tape from a session one. It self-checks on the instruments where the
    table was right -- SPY 15m measures 6,697/yr and QQQ 6,543/yr against a
    tabled 6,552, inside 2.3%.

    Falls back when the span cannot carry the claim: fewer than two bars, a
    non-positive span, or under a day of history (where one gap would swing
    the estimate wildly).
    """
    if len(records) < 2:
        return BARS_PER_YEAR.get(interval, float(_SESSIONS))
    span_s = (records[-1].timestamp - records[0].timestamp).total_seconds()
    if span_s < 86_400.0:
        return BARS_PER_YEAR.get(interval, float(_SESSIONS))
    # n-1 intervals span n bars.
    return float((len(records) - 1) * 365.25 * 86_400.0 / span_s)


# Round-trip cost in basis points of notional, charged on each side. 2bp a
# side is a realistic all-in for a liquid ETF through a retail broker once
# spread crossing is included; single names are worse.
DEFAULT_COST_BPS = 2.0

# The backtest pool: what the ledger starts with, in dollars. Orders are sized
# off it (and off equity as it compounds), so it is a real starting balance
# now, not a label on a percent. Override per call / per request.
DEFAULT_CAPITAL = 1_000_000.0

# Bars an indicator set needs before it is fully seeded: EMA200 plus the
# 200-bar percentile ranks in `elmo.rank_series`, with a little headroom.
WARMUP_BARS = 210


@dataclass
class BacktestResult:
    trades: list[dict[str, Any]] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    returns: list[float] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    # Every fill: action, side, quantity, price, dollars, fee, loan taken.
    orders: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> str:
        m = self.metrics
        return (
            f"trades={m.get('trades', 0):>4}  "
            f"win={m.get('win_rate', 0):>5.1f}%  "
            f"pf={m.get('profit_factor') or 0:>5.2f}  "
            f"ret={m.get('total_return_pct', 0):>7.2f}%  "
            f"bh={m.get('buy_hold_pct', 0):>7.2f}%  "
            f"mdd={m.get('max_drawdown_pct', 0):>6.2f}%  "
            f"sharpe={m.get('sharpe', 0):>5.2f}  "
            f"expo={m.get('exposure_pct', 0):>5.1f}%"
        )


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------


def _max_drawdown(equity: Sequence[float]) -> float:
    peak = -math.inf
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return float(100.0 * worst)


def run_backtest(
    records: Sequence[CandleRecord],
    *,
    interval: str = "15m",
    config: dict[str, Any] | None = None,
    flow_net: Sequence[float] | None = None,
    flow_observed: Sequence[bool] | None = None,
    cost_bps: float = DEFAULT_COST_BPS,
    elmo_overrides: dict[str, Any] | None = None,
    elmo: ElmoResult | None = None,
    warmup_bars: int = 0,
    capital: float = DEFAULT_CAPITAL,
    ticker: str | None = None,
) -> BacktestResult:
    """One symbol, one parameter set, next-bar-open fills.

    `warmup_bars` seeds the indicators without trading. Indicators are
    computed over the WHOLE slice, but no position is opened before
    `warmup_bars` and the equity curve starts there. This is what lets a
    walk-forward fold hand the engine 210 bars of already-seen history so
    EMA200 and the 200-bar percentile ranks are warm at the fold boundary,
    without those bars contributing a trade or a return. Scoring them would
    make the fold in-sample, which is the whole thing a walk-forward exists
    to avoid.

    Re-implements the state machine rather than reusing
    `signal_engine.run_state_machine` on purpose: the live machine records a
    decision at the bar that triggered it (which is what you want on the
    chart), while this one must separate the decision bar from the fill bar.
    Sharing the loop would force one of the two to lie.
    """
    cfg = {**DEFAULTS, **(config or {})}
    n = len(records)
    if n < 60:
        return BacktestResult(
            metrics={"error": f"need >=60 bars, have {n}"}, config=cfg
        )

    # A caller that has ALREADY computed ELMo over these same bars passes it
    # in rather than paying for it twice. `/api/backtest` is the case that
    # matters: it computes ELMo to drive the chart's conviction line and then
    # called this function, which recomputed the identical series from the
    # identical records -- ~2.3s of the ~5.7s a slider drag cost on a 35k-bar
    # chart, spent reproducing a result already in memory.
    #
    # `elmo_overrides` stays for callers that have no result to hand (the
    # walk-forward runner sweeps parameters, so it wants the recompute). When
    # both are given the object wins, because it is the thing actually scored.
    if elmo is None:
        elmo = compute_elmo(records, **(elmo_overrides or {}))
    # `flow_observed` decides the denominator PER BAR. Without it the whale
    # weight sits in the divisor for every bar of the window while the flow
    # pull only ever covers a handful of session dates -- on SPY 15m over a
    # year that is ~5 covered sessions out of ~250, and the dilution inflated a
    # hand-tuned result from +13.7% to +44.9%. Same failure as §3.4, one
    # granularity down; the live chart was fixed first and this path was not.
    conv = conviction_series(
        records,
        elmo=elmo,
        flow_net=flow_net,
        flow_observed=flow_observed,
        config=cfg,
    )
    score = conv.score
    ready = conv.ready
    atr = conv.atr or atr_series(records, int(cfg["atr_period"]))

    cost = cost_bps / 10_000.0
    mult = float(cfg["atr_stop_mult"])
    cooldown = int(cfg["cooldown_bars"])
    allow_short = bool(cfg["allow_short"])

    # HOW BIG, HOW IN, HOW OUT, HOW PAID FOR -- see `chart_app/sizing.py`.
    #
    # This is a LEDGER now: cash, shares and a margin loan, in dollars, from a
    # `capital` pool. It used to be a percent-return series where a position
    # was N "units" of 1/N of capital each. That made an order a fraction of a
    # position instead of an amount of money, so a 5-unit cap meant every buy
    # was a fifth of the position -- and on a small book, a fifth of a share.
    # It also made pyramiding into silent leverage: each unit above the book
    # was exposure nobody paid for, with no loan and no interest.
    #
    # `level` is the ladder the signal walks, a signed share of the FULL
    # position in [-1, 1]. `qty` is what the ledger actually holds. An order's
    # size comes from equity at the moment it fills:
    #
    #   entry/add   buys  entry_slice x position_size x equity, in whole
    #               shares unless `fractional` is ticked
    #   trim/scale  sells exit_slice / |level| of what is held (equal chunks)
    #   exit/stop   sells everything
    #
    # Borrowing (`margin_pct`) never changes the share count, only how it is
    # paid for: the borrowed share of each buy is a loan, charged the venue's
    # rate every bar, and repaid pro rata as the position comes off.
    venue = venue_for(ticker)
    sz = resolve_sizing(cfg, venue)
    scale = sz.position_scale
    borrow = sz.margin_pct
    maint = venue.maintenance

    warmup = max(0, min(int(warmup_bars), n - 2))
    cash = float(capital)
    qty = 0.0  # signed shares/contracts held
    loan = 0.0  # margin loan outstanding (long side)
    level = 0.0  # signed share of the full position the ladder is on
    peak_level = 0.0
    peak_qty = 0.0
    peak_notional = 0.0
    entry_index = 0
    trade_start_equity = 0.0
    trade_orders = 0
    trail: float | None = None
    last_action_bar = -10_000
    bars_in_market = 0
    level_bars = 0.0  # sum of |level| x scale per in-market bar
    gross_frac_sum = 0.0  # sum of gross notional / equity per scored bar
    max_gross = 0.0
    fees = 0.0
    interest_paid = 0.0
    margin_calls = 0
    busted = False
    equity = [1.0]
    bar_returns: list[float] = []
    trades: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    prev_equity = float(capital)

    def _equity_at(price: float) -> float:
        return cash + qty * price - loan

    def _fill(
        target_qty: float,
        price: float,
        action: str,
        reason: str,
        *,
        mark: int,
        fill: int,
    ) -> None:
        """Move the ledger from `qty` to `target_qty` at `price`."""
        nonlocal cash, qty, loan, fees, trade_orders, peak_qty, peak_notional
        delta = target_qty - qty
        if abs(delta) < 1e-12:
            return
        notional = abs(delta) * price
        fee = cost * notional
        borrowed = 0.0
        if abs(target_qty) > abs(qty) and qty * target_qty >= 0:
            # Opening or adding. A long pays cash for the unborrowed share and
            # takes a loan for the rest; a short receives the proceeds.
            if target_qty > 0:
                borrowed = notional * borrow
                cash -= notional - borrowed
                loan += borrowed
            else:
                cash += notional
        else:
            # Reducing. Repay the loan in proportion to the shares sold, so a
            # half-closed position carries half its loan.
            share = abs(delta) / abs(qty) if qty else 1.0
            if qty > 0:
                repay = loan * share
                cash += notional - repay
                loan -= repay
            else:
                cash -= notional
        cash -= fee
        fees += fee
        qty = 0.0 if abs(target_qty) < 1e-12 else target_qty
        if qty == 0.0:
            loan = 0.0
        trade_orders += 1
        peak_qty = max(peak_qty, abs(qty))
        peak_notional = max(peak_notional, abs(qty) * price)
        orders.append(
            {
                "bar": mark,
                "fill_index": fill,
                "ts": records[fill].timestamp.isoformat(),
                "action": action,
                "side": "buy" if delta > 0 else "sell",
                "qty": float(abs(delta)),
                "price": float(price),
                "notional": float(notional),
                "fee": float(fee),
                "borrowed": float(borrowed),
                "reason": reason,
                "position_qty": float(qty),
                "level": float(level),
            }
        )

    def _buy_qty(new_level: float, price: float) -> float:
        """Target holding for `new_level`, sized off equity, capped at buying power."""
        eq = max(0.0, _equity_at(price))
        want = abs(new_level) * sz.position_size * eq
        cap = eq * sz.buying_power
        dollars_more = min(want, cap) - abs(qty) * price
        extra = order_quantity(dollars_more, price, sz.fractional)
        side = 1.0 if new_level > 0 else -1.0
        return qty + side * extra

    def _shed_qty(old_level: float, new_level: float) -> float:
        """Holding after selling (old-new)/old of it -- equal chunks of the
        full position, whole shares where the venue fills whole shares."""
        if abs(new_level) < 1e-12:
            return 0.0
        sell = abs(qty) * (abs(old_level) - abs(new_level)) / abs(old_level)
        if not sz.fractional:
            sell = float(math.floor(sell + 1e-9))
        side = 1.0 if qty > 0 else -1.0
        return side * max(0.0, abs(qty) - sell)

    def _close_trade(exit_i: int, exit_price: float, reason: str) -> None:
        nonlocal level, peak_level, trail, trade_orders, peak_qty, peak_notional
        side = "long" if level > 0 else "short"
        avg_entry = float(trades_entry_price[0])
        pnl = _equity_at(exit_price) - trade_start_equity
        trades.append(
            {
                "side": side,
                "entry_index": entry_index,
                "entry_ts": records[entry_index].timestamp.isoformat(),
                "entry_price": avg_entry,
                "exit_index": exit_i,
                "exit_ts": records[exit_i].timestamp.isoformat(),
                "exit_price": float(exit_price),
                "exit_reason": reason,
                "bars": exit_i - entry_index,
                # Portfolio-return CONTRIBUTION: the trade's dollars (fees and
                # margin interest included) over equity when it opened.
                "pnl_pct": float(100.0 * pnl / trade_start_equity)
                if trade_start_equity > 0
                else 0.0,
                "pnl_dollars": float(pnl),
                "peak_units": float(peak_level * scale),
                "peak_qty": float(peak_qty),
                "peak_notional": float(peak_notional),
                "orders": int(trade_orders),
            }
        )
        level = 0.0
        peak_level = 0.0
        peak_qty = 0.0
        peak_notional = 0.0
        trade_orders = 0
        trail = None

    # Size-weighted average entry of the open trade, held in a one-slot list so
    # the closures above can read it without another nonlocal.
    trades_entry_price = [0.0]
    tp_fired: set[int] = set()  # take-profit levels already taken this trade

    def _record_entry_price(before_qty: float, price: float) -> None:
        added = abs(qty) - abs(before_qty)
        if added <= 0:
            return
        held = abs(qty)
        trades_entry_price[0] = (
            price
            if abs(before_qty) < 1e-12
            else (trades_entry_price[0] * abs(before_qty) + price * added) / held
        )

    # `i` is the DECISION bar. The fill happens at `i+1`'s open, so the loop
    # stops one short of the end -- a signal on the final bar has no bar to
    # fill against and is not counted.
    for i in range(warmup, n - 1):
        nxt = records[i + 1]
        open_next = float(nxt.open)
        low_next, high_next = float(nxt.low), float(nxt.high)
        close_next = float(nxt.close)

        exit_price: float | None = None
        exit_reason = ""
        scale_exit = False

        if level != 0:
            bars_in_market += 1
            level_bars += abs(level) * scale
            # 1. Stop and margin call, checked against the NEXT bar's range.
            #    Whichever trigger a moving price reaches first wins; a gap
            #    through it fills at the open.
            triggers: list[tuple[float, str]] = []
            if trail is not None:
                triggers.append((trail, "atr_stop"))
            if qty > 0 and loan - cash > 0:
                triggers.append(((loan - cash) / (qty * (1.0 - maint)), "margin_call"))
            elif qty < 0 and cash > 0:
                triggers.append((cash / (abs(qty) * (1.0 + maint)), "margin_call"))
            if level > 0:
                hit = [t for t in triggers if low_next <= t[0]]
                if hit:
                    price, exit_reason = max(hit)
                    exit_price = min(price, open_next)
            else:
                hit = [t for t in triggers if high_next >= t[0]]
                if hit:
                    price, exit_reason = min(hit)
                    exit_price = max(price, open_next)
            # 2. Score exit, filled at the next open. With exit_style="scale"
            #    a position bigger than one exit order sheds one order's worth
            #    here instead of closing. The stop above is deliberately NOT
            #    scaled: a stop is a stop, and a margin call is not optional.
            if exit_price is None:
                s = float(score[i])
                hit_exit = (
                    level > 0
                    and s <= float(cfg["exit_long"])
                    or level < 0
                    and s >= float(cfg["exit_short"])
                )
                if hit_exit and sz.scale_out and sz.more_than_one_exit(level):
                    scale_exit = True
                elif hit_exit:
                    exit_price, exit_reason = open_next, "score_exit"

        if exit_price is not None:
            action = "sell" if level > 0 else "cover"
            _fill(
                0.0,
                exit_price,
                action,
                exit_reason,
                mark=i + 1 if exit_reason != "score_exit" else i,
                fill=i + 1,
            )
            if exit_reason == "margin_call":
                margin_calls += 1
            _close_trade(i + 1, exit_price, exit_reason)
            last_action_bar = i

        # 3. Transitions, decided on bar i, filled at i+1 open. Mirrors
        #    `signal_engine.run_state_machine` step for step, using the same
        #    `Sizing` ladder so the two cannot disagree about size.
        can_act = (i - last_action_bar) >= cooldown and ready[i] and not busted
        s_now = float(score[i])
        before = qty
        if scale_exit and can_act:
            new = sz.shed(level)
            target = _shed_qty(level, new)
            level = new
            _fill(target, open_next, "trim", "scale_out", mark=i, fill=i + 1)
            last_action_bar = i
        elif level > 0 and can_act:
            if s_now <= float(cfg["trim_long"]) and sz.more_than_one_exit(level):
                new = sz.shed(level)
                target = _shed_qty(level, new)
                level = new
                _fill(target, open_next, "trim", "trim", mark=i, fill=i + 1)
                last_action_bar = i
            elif s_now >= float(cfg["add_long"]) and sz.can_add(level):
                level = sz.add(level)
                peak_level = max(peak_level, abs(level))
                _fill(
                    _buy_qty(level, open_next),
                    open_next,
                    "add",
                    "add",
                    mark=i,
                    fill=i + 1,
                )
                _record_entry_price(before, open_next)
                last_action_bar = i
        elif level < 0 and can_act:
            # Mirror of the long ladder: a short adds as conviction falls
            # further and trims as it recovers toward zero.
            if s_now >= float(cfg["trim_short"]) and sz.more_than_one_exit(level):
                new = sz.shed(level)
                target = _shed_qty(level, new)
                level = new
                _fill(target, open_next, "trim", "trim", mark=i, fill=i + 1)
                last_action_bar = i
            elif s_now <= float(cfg["add_short"]) and sz.can_add(level):
                level = sz.add(level)
                peak_level = max(peak_level, abs(level))
                _fill(
                    _buy_qty(level, open_next),
                    open_next,
                    "add",
                    "add",
                    mark=i,
                    fill=i + 1,
                )
                _record_entry_price(before, open_next)
                last_action_bar = i
        elif level == 0 and can_act:
            side = 0.0
            if s_now >= float(cfg["entry_long"]):
                side = 1.0
            elif allow_short and s_now <= float(cfg["entry_short"]):
                side = -1.0
            if side:
                trade_start_equity = _equity_at(open_next)
                entry_index = i + 1
                level = side * sz.first()
                peak_level = abs(level)
                trades_entry_price[0] = open_next
                tp_fired.clear()
                _fill(
                    _buy_qty(level, open_next),
                    open_next,
                    "buy" if side > 0 else "short",
                    "entry",
                    mark=i,
                    fill=i + 1,
                )
                last_action_bar = i

        # 3b. Take-profit levels: resting limit orders, "take profit at 5% and
        #     10%" off the average entry. Each sells one exit chunk, fires once
        #     per trade, fills at its price (or the open if the bar gaps
        #     through it), and ignores cooldown -- a resting order does not
        #     wait. Checked after the open's fills because it is intrabar, and
        #     only when no stop fired this bar: with both inside one bar the
        #     order is unknowable, and assuming the stop is the safe reading.
        if level != 0 and qty != 0 and exit_price is None and sz.take_profit:
            avg = trades_entry_price[0]
            for k, pct in enumerate(sz.take_profit):
                if k in tp_fired or level == 0:
                    continue
                if level > 0:
                    target_px = avg * (1.0 + pct / 100.0)
                    if high_next < target_px:
                        break
                    px = max(target_px, open_next)
                else:
                    target_px = avg * (1.0 - pct / 100.0)
                    if low_next > target_px:
                        break
                    px = min(target_px, open_next)
                tp_fired.add(k)
                new = sz.shed(level)
                closing = new == 0
                action = ("sell" if level > 0 else "cover") if closing else "trim"
                _fill(
                    _shed_qty(level, new),
                    px,
                    action,
                    "take_profit",
                    mark=i + 1,
                    fill=i + 1,
                )
                if closing:
                    _close_trade(i + 1, px, "take_profit")
                else:
                    level = new

        # 4. Margin interest for the time this bar spans. Calendar time, not
        #    bar count: a loan accrues over a weekend the tape does not see.
        if loan > 0:
            years = (nxt.timestamp - records[i].timestamp).total_seconds() / (
                365.25 * 86_400.0
            )
            charge = venue.interest(loan, years)
            cash -= charge
            interest_paid += charge

        # 5. Ratchet the trail off the bar we just marked to.
        a = atr[i + 1] if i + 1 < len(atr) else None
        if level != 0 and a:
            if level > 0:
                candidate = close_next - mult * float(a)
                trail = candidate if trail is None else max(trail, candidate)
            else:
                candidate = close_next + mult * float(a)
                trail = candidate if trail is None else min(trail, candidate)

        # 6. Mark to the close, in dollars.
        eq_now = _equity_at(close_next)
        if eq_now > 0:
            gross = abs(qty) * close_next / eq_now
            gross_frac_sum += gross
            max_gross = max(max_gross, gross)
        elif not busted:
            # The account is gone. A real broker would have liquidated before
            # this; a gap can still carry it through zero. Stop trading.
            busted = True
        bar_returns.append(
            float(eq_now / prev_equity - 1.0) if prev_equity > 0 else 0.0
        )
        prev_equity = eq_now
        equity.append(eq_now / capital)

    # Force-close anything still open at the last bar, so an open winner is
    # not silently counted as realised. Priced at the close, no fee: it is a
    # mark, not a trade anyone placed.
    if level != 0:
        last = float(records[-1].close)
        _close_trade(n - 1, last, "end_of_data")

    metrics = _metrics(
        records[warmup:],
        trades,
        equity,
        bar_returns,
        bars_in_market,
        interval=interval,
        capital=capital,
    )
    metrics["warmup_bars"] = warmup
    scored_bars = max(1, len(records[warmup:]) - 1)
    # What the returns above MEAN, in capital terms.
    metrics["sizing"] = {
        "position_size": sz.position_size,
        "entry_slice": sz.entry_slice,
        "exit_slice": sz.exit_slice,
        "scale_out": sz.scale_out,
        "fractional": sz.fractional,
        "take_profit": list(sz.take_profit),
        "margin_pct": sz.margin_pct,
        "margin_pct_asked": sz.margin_pct_asked,
        "legacy_units": sz.legacy_units,
    }
    metrics["venue"] = {
        "name": venue.name,
        "max_borrow_pct": 100.0
        * min(MAX_BORROW, max(0.0, 1.0 - 1.0 / max(1.0, venue.max_leverage))),
        "maintenance_pct": 100.0 * maint,
        "note": venue.note,
    }
    metrics["capital_model"] = (
        "cash" if sz.margin_pct <= 0 else f"margin {100 * sz.margin_pct:.0f}% borrowed"
    )
    metrics["margin_capped"] = sz.margin_pct < sz.margin_pct_asked - 1e-9
    metrics["fees_dollars"] = float(fees)
    metrics["interest_dollars"] = float(interest_paid)
    metrics["margin_calls"] = int(margin_calls)
    metrics["orders"] = len(orders)
    metrics["max_gross_exposure_pct"] = float(100.0 * max_gross)
    # Average share of EQUITY actually invested. Buy-and-hold is 100%, so a
    # return that trails it while this reads 30% is not the same failure as one
    # that trails it while fully invested.
    metrics["avg_exposure_pct"] = float(100.0 * gross_frac_sum / scored_bars)
    metrics["avg_units_when_in"] = (
        float(level_bars / bars_in_market) if bars_in_market else 0.0
    )
    return BacktestResult(
        trades=trades,
        equity=equity,
        returns=bar_returns,
        metrics=metrics,
        config=cfg,
        orders=orders,
    )


def _metrics(
    records: Sequence[CandleRecord],
    trades: Sequence[dict[str, Any]],
    equity: Sequence[float],
    bar_returns: Sequence[float],
    bars_in_market: int,
    *,
    interval: str,
    capital: float = DEFAULT_CAPITAL,
) -> dict[str, Any]:
    n = len(records)
    pnls = [float(t["pnl_pct"]) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    total_return = 100.0 * (equity[-1] - 1.0) if equity else 0.0
    first, last = float(records[0].close), float(records[-1].close)
    buy_hold = 100.0 * (last / first - 1.0) if first else 0.0

    bpy = _bars_per_year(records, interval)
    arr = np.asarray(bar_returns, dtype=float)
    sharpe = 0.0
    if arr.size > 1 and arr.std() > 0:
        sharpe = float(arr.mean() / arr.std() * math.sqrt(bpy))
    years = n / bpy if bpy else 0.0
    cagr = 0.0
    if years > 0 and equity and equity[-1] > 0:
        cagr = float(100.0 * (equity[-1] ** (1.0 / years) - 1.0))

    return {
        "bars": n,
        "trades": len(trades),
        "win_rate": float(100.0 * len(wins) / len(pnls)) if pnls else 0.0,
        "avg_win_pct": float(np.mean(wins)) if wins else 0.0,
        "avg_loss_pct": float(np.mean(losses)) if losses else 0.0,
        "expectancy_pct": float(np.mean(pnls)) if pnls else 0.0,
        # A profit factor with no losing trades is not infinite, it is
        # unmeasured -- report it as None so a sweep cannot rank on it.
        "profit_factor": (
            float(gross_win / gross_loss)
            if gross_loss > 0
            else (None if gross_win else 0.0)
        ),
        "total_return_pct": float(total_return),
        "buy_hold_pct": float(buy_hold),
        "excess_vs_bh_pct": float(total_return - buy_hold),
        # Dollars off the ledger's own starting pool: `run_backtest` sizes
        # every order from `capital` and the equity it compounds into, so
        # these are the balance the simulated account actually ended on.
        "capital": float(capital),
        "pnl_dollars": float(capital * total_return / 100.0),
        "buy_hold_dollars": float(capital * buy_hold / 100.0),
        "excess_vs_bh_dollars": float(capital * (total_return - buy_hold) / 100.0),
        "max_drawdown_dollars": float(capital * _max_drawdown(equity) / 100.0),
        "cagr_pct": cagr,
        # Published so a reader can audit the annualisation rather than trust
        # it: a 24/7 perp and a 6.5h equity session disagree by 3.7x on what
        # one 15m bar is worth, and `cagr_pct`/`sharpe` both divide by this.
        "bars_per_year": float(bpy),
        "years": float(years),
        "max_drawdown_pct": _max_drawdown(equity),
        "sharpe": sharpe,
        "exposure_pct": float(100.0 * bars_in_market / n) if n else 0.0,
        "avg_bars_held": float(np.mean([t["bars"] for t in trades])) if trades else 0.0,
        "exit_reasons": {
            reason: sum(1 for t in trades if t["exit_reason"] == reason)
            for reason in sorted({t["exit_reason"] for t in trades})
        },
    }


# ---------------------------------------------------------------------------
# Sweeps
# ---------------------------------------------------------------------------


def expand_grid(grid: dict[str, Sequence[Any]]) -> list[dict[str, Any]]:
    keys = list(grid)
    return [
        dict(zip(keys, combo, strict=True))
        for combo in itertools.product(*(grid[k] for k in keys))
    ]


def _objective(metrics: dict[str, Any], *, min_trades: int = 8) -> float:
    """What a sweep ranks on.

    Deliberately NOT total return. Ranking a sweep on return picks the run
    that got one lucky trade in a lucky window; every walk-forward then shows
    it collapsing. This ranks on return-per-unit-drawdown and refuses to score
    a run with too few trades at all, which is the cheapest defence against
    fitting a parameter set to three events.
    """
    if metrics.get("trades", 0) < min_trades:
        return float("-inf")
    ret = float(metrics.get("total_return_pct", 0.0))
    dd = abs(float(metrics.get("max_drawdown_pct", 0.0)))
    sharpe = float(metrics.get("sharpe", 0.0))
    calmar = ret / dd if dd > 0.5 else ret
    return float(0.6 * calmar + 0.4 * sharpe)


def sweep(
    records: Sequence[CandleRecord],
    grid: dict[str, Sequence[Any]],
    *,
    interval: str = "15m",
    flow_net: Sequence[float] | None = None,
    cost_bps: float = DEFAULT_COST_BPS,
    min_trades: int = 8,
) -> list[tuple[dict[str, Any], dict[str, Any], float]]:
    """Every combination in `grid`, ranked by `_objective`, best first."""
    out: list[tuple[dict[str, Any], dict[str, Any], float]] = []
    for params in expand_grid(grid):
        res = run_backtest(
            records,
            interval=interval,
            config=params,
            flow_net=flow_net,
            cost_bps=cost_bps,
        )
        out.append(
            (params, res.metrics, _objective(res.metrics, min_trades=min_trades))
        )
    out.sort(key=lambda row: row[2], reverse=True)
    return out


def walk_forward(
    records: Sequence[CandleRecord],
    grid: dict[str, Sequence[Any]],
    *,
    interval: str = "15m",
    folds: int = 4,
    train_frac: float = 0.6,
    cost_bps: float = DEFAULT_COST_BPS,
    min_trades: int = 4,
) -> dict[str, Any]:
    """Rolling anchored walk-forward: fit on a window, score on the next one.

    This is the only number in this module worth defending in an argument. An
    in-sample sweep result says a parameter set *can* be found that fits the
    past; a walk-forward says whether the set you would actually have chosen,
    knowing only the past, made money on data it never saw.
    """
    n = len(records)
    fold_size = n // (folds + 1)
    if fold_size < 80:
        return {"error": f"need >={80 * (folds + 1)} bars for {folds} folds, have {n}"}

    results: list[dict[str, Any]] = []
    for fold in range(folds):
        train_end = fold_size * (fold + 1)
        test_end = min(n, train_end + fold_size)
        train = records[:train_end]
        # The test slice is back-extended by `WARMUP_BARS` so EMA200, ADX and
        # the 200-bar percentile ranks are seeded at the fold boundary -- but
        # `run_backtest(warmup_bars=...)` refuses to trade or score those
        # bars. Passing them WITHOUT that guard (as this first did) makes the
        # fold in-sample whenever `fold_size < WARMUP_BARS`, which at 752
        # daily bars over 4 folds it always is: fold 0's "test" slice was
        # records[0:300], the entire training set included.
        warm_start = max(0, train_end - WARMUP_BARS)
        test = records[warm_start:test_end]
        warmup = train_end - warm_start
        ranked = sweep(
            train, grid, interval=interval, cost_bps=cost_bps, min_trades=min_trades
        )
        best = next((row for row in ranked if row[2] > float("-inf")), None)
        if best is None:
            results.append(
                {"fold": fold, "error": "no parameter set cleared min_trades"}
            )
            continue
        params = best[0]
        oos = run_backtest(
            test,
            interval=interval,
            config=params,
            cost_bps=cost_bps,
            warmup_bars=warmup,
        )
        results.append(
            {
                "fold": fold,
                "params": params,
                "train_bars": len(train),
                "test_bars": test_end - train_end,
                "warmup_bars": warmup,
                "in_sample": best[1],
                "out_of_sample": oos.metrics,
            }
        )

    scored = [r for r in results if "out_of_sample" in r]
    oos_returns = [float(r["out_of_sample"]["total_return_pct"]) for r in scored]
    oos_trades = sum(int(r["out_of_sample"]["trades"]) for r in scored)
    # Compounded, not summed. Folds run back to back on one notional account,
    # so their returns multiply. Summing them let four -45% folds report
    # -181%, which is not a number a long-only book can produce.
    compounded = 1.0
    for value in oos_returns:
        compounded *= 1.0 + value / 100.0
    return {
        "folds": results,
        "oos_total_return_pct": float(100.0 * (compounded - 1.0)),
        "oos_mean_return_pct": float(np.mean(oos_returns)) if oos_returns else 0.0,
        "oos_worst_fold_pct": float(min(oos_returns)) if oos_returns else 0.0,
        "oos_positive_folds": sum(1 for v in oos_returns if v > 0),
        "oos_folds_scored": len(scored),
        "oos_trades": oos_trades,
    }


def permutation_test(
    records: Sequence[CandleRecord],
    *,
    interval: str = "15m",
    config: dict[str, Any] | None = None,
    trials: int = 50,
    cost_bps: float = DEFAULT_COST_BPS,
    seed: int = 11,
) -> dict[str, Any]:
    """Is the result distinguishable from the same strategy on shuffled data?

    Shuffling the *returns* destroys every temporal structure the engine
    claims to read (trend, entropy, squeeze) while preserving the return
    distribution exactly -- same mean, same fat tails, same volatility. If the
    real run does not sit in the top decile of the shuffled runs, the engine
    is reading noise and the backtest is a story about one path.
    """
    rng = np.random.default_rng(seed)
    real = run_backtest(records, interval=interval, config=config, cost_bps=cost_bps)
    closes = np.array([float(r.close) for r in records])
    rets = np.diff(np.log(closes))
    base = float(closes[0])

    null: list[float] = []
    for _ in range(trials):
        shuffled = rng.permutation(rets)
        path = base * np.exp(np.concatenate([[0.0], np.cumsum(shuffled)]))
        fake: list[CandleRecord] = []
        for i, price in enumerate(path):
            src = records[i]
            # Rescale the original bar's shape onto the synthetic close, so
            # the surrogate keeps a realistic high/low/volume envelope rather
            # than becoming a line chart the ATR stop can never trigger on.
            scale = price / float(src.close) if src.close else 1.0
            fake.append(
                CandleRecord(
                    timestamp=src.timestamp,
                    open=float(src.open) * scale,
                    high=float(src.high) * scale,
                    low=float(src.low) * scale,
                    close=float(price),
                    volume=src.volume,
                )
            )
        null.append(
            float(
                run_backtest(
                    fake, interval=interval, config=config, cost_bps=cost_bps
                ).metrics.get("total_return_pct", 0.0)
            )
        )

    observed = float(real.metrics.get("total_return_pct", 0.0))
    beaten = sum(1 for v in null if v >= observed)
    return {
        "observed_return_pct": observed,
        "null_mean_pct": float(np.mean(null)) if null else 0.0,
        "null_p95_pct": float(np.percentile(null, 95)) if null else 0.0,
        "trials": trials,
        # Empirical one-sided p-value with the +1 correction, so a run that
        # beats every surrogate reports 1/(trials+1) rather than an
        # unsupportable 0.
        "p_value": float((beaten + 1) / (trials + 1)),
        "real_metrics": real.metrics,
    }


def flow_net_for(
    records: Sequence[CandleRecord], trades: Iterable[dict[str, Any]] | None
) -> list[float] | None:
    """Per-bar net option premium for a backtest, or None when there is none."""
    rows = list(trades or [])
    if not rows:
        return None
    return bin_flow(records, rows)["net"]


# ---------------------------------------------------------------------------
# Rank transfer
# ---------------------------------------------------------------------------
#
# A sweep answers "which parameter set fit these bars best". It cannot answer
# the question that decides whether tuning is worth doing at all: does the
# ORDERING it produces mean anything on symbols it never saw? Two separate
# claims hide inside "the tune worked", and they fail independently:
#
#   level   -- the return the winner posted in-sample. This never transfers.
#              It is the maximum of a noisy sample, biased upward by
#              construction, and the bias GROWS with the number of combos
#              tried. Reading it as an expectation is the mistake HANDOFF
#              §3.17 caught after the fact.
#   ranking -- the order the combos came in. This MAY transfer. If it does,
#              tuning on one universe is a legitimate way to choose a default
#              for another, even though the in-sample number is a fiction.
#
# These helpers measure the second claim. `spearman_rho` is the whole-surface
# view; `percentile_of` is the one actually experienced, because nobody
# deploys a whole ranking -- they deploy the top of it.


def rank_average(values: Sequence[float]) -> list[float]:
    """1-based ascending ranks, ties sharing their average rank.

    Midranks rather than `argsort` positions, because a sweep produces real
    ties: every combo disqualified by `min_trades` scores `-inf` together.
    Breaking those ties by array order would invent an ordering the data does
    not contain, and any correlation computed from it would be reading the
    order the grid happened to be expanded in.
    """
    arr = np.asarray(list(values), dtype=float)
    if arr.size == 0:
        return []
    if np.isnan(arr).any():
        # A NaN objective is a broken metric, not a rank. Sorting it silently
        # to one end would turn the breakage into a quiet score.
        raise ValueError("rank_average: NaN in values")
    order = np.argsort(arr, kind="mergesort")
    ordered = arr[order]
    ranks = np.empty(arr.size, dtype=float)
    i = 0
    while i < arr.size:
        j = i
        while j + 1 < arr.size and ordered[j + 1] == ordered[i]:
            j += 1
        ranks[order[i : j + 1]] = 0.5 * (i + j) + 1.0
        i = j + 1
    return [float(v) for v in ranks]


def spearman_rho(a: Sequence[float], b: Sequence[float]) -> float | None:
    """Tie-aware Spearman rank correlation, or None when it is undefined.

    None rather than 0.0 when either side is constant: "every combo scored
    the same" and "the two rankings are unrelated" are different findings,
    and collapsing them would let a degenerate sweep read as a real null.
    """
    ra = np.asarray(rank_average(a), dtype=float)
    rb = np.asarray(rank_average(b), dtype=float)
    if ra.size != rb.size:
        raise ValueError(f"spearman_rho: length mismatch {ra.size} vs {rb.size}")
    if ra.size < 3:
        return None
    sa = float(ra.std())
    sb = float(rb.std())
    if sa == 0.0 or sb == 0.0:
        return None
    return float(np.mean((ra - ra.mean()) * (rb - rb.mean())) / (sa * sb))


def percentile_of(value: float, population: Sequence[float]) -> float | None:
    """Where `value` sits inside `population`, 0-100, ties at half weight.

    The midrank convention is load-bearing: a combo tied with the whole
    disqualified half of a grid should read ~50, not 100. A member chosen at
    random scores 50 in expectation -- that is the null this number is read
    against, and the only reason it is interpretable at all.
    """
    pop = [float(v) for v in population]
    if not pop:
        return None
    target = float(value)
    below = sum(1 for v in pop if v < target)
    ties = sum(1 for v in pop if v == target)
    return float(100.0 * (below + 0.5 * ties) / len(pop))
