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

# Notional the percent returns are scaled by for the dollar figures. A round
# number on purpose: it is a readability aid for tuning, not a claim about the
# account. Override per call / per request.
DEFAULT_CAPITAL = 100_000.0

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

    def summary(self) -> str:
        m = self.metrics
        return (
            f"trades={m.get('trades', 0):>4}  "
            f"win={m.get('win_rate', 0):>5.1f}%  "
            f"pf={m.get('profit_factor', 0):>5.2f}  "
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

    # HOW MUCH CAPITAL IS AT RISK PER UNIT.
    #
    # This used to be implicit and wrong: `in_pos` was +1/-1, so every entry
    # was 100% of capital and a 1-unit and a 3-unit conviction were the same
    # bet. Meanwhile the LIVE machine scales 1 -> 3 units, so `add_long` and
    # `trim_long` moved the chart's marks and had exactly zero effect on the
    # reported P&L -- the tester was scoring a different strategy than the one
    # on screen.
    #
    # `max_units` is the cap the live machine uses (3). `unit_fraction` is what
    # one unit costs, as a share of capital:
    #
    #   1/max_units (DEFAULT) -- fractional. First buy is 33%, full conviction
    #                            is 100%, never borrows. A cash account.
    #   1.0                   -- pyramiding. First buy is 100% and adds lever
    #                            to 300%. Needs margin; returns and drawdowns
    #                            both scale by ~3x.
    #
    # It is explicit precisely because it is not a detail: it sets what every
    # return in this module MEANS, and buy-and-hold is 100% invested for
    # comparison.
    max_units = int(cfg.get("max_units", 3))
    unit_fraction = float(cfg.get("unit_fraction") or (1.0 / max_units))
    scale_out = str(cfg.get("exit_style", "full")).lower() == "scale"

    warmup = max(0, min(int(warmup_bars), n - 2))
    equity = [1.0]
    bar_returns: list[float] = []
    trades: list[dict[str, Any]] = []
    units = 0  # >0 long units, <0 short units, 0 flat
    entry_price = 0.0  # size-weighted average entry
    entry_index = 0
    peak_units = 0
    realized = 0.0  # portfolio-return contribution booked by trims
    trail: float | None = None
    last_action_bar = -10_000
    bars_in_market = 0
    unit_bars = 0.0  # sum of |units| per bar, for average-exposure reporting

    # `i` is the DECISION bar. The fill happens at `i+1`'s open, so the loop
    # stops one short of the end -- a signal on the final bar has no bar to
    # fill against and is not counted.
    for i in range(warmup, n - 1):
        nxt = records[i + 1]
        open_next = float(nxt.open)
        low_next, high_next = float(nxt.low), float(nxt.high)
        close_next = float(nxt.close)
        prev_close = float(records[i].close)

        exit_price: float | None = None
        exit_reason = ""
        scale_exit = False

        if units != 0:
            bars_in_market += 1
            unit_bars += abs(units)
            # 1. Stop, checked against the NEXT bar's range and filled at the
            #    stop level -- or at the open when the bar gaps through it.
            if trail is not None:
                if units > 0 and low_next <= trail:
                    exit_price = min(trail, open_next)
                    exit_reason = "atr_stop"
                elif units < 0 and high_next >= trail:
                    exit_price = max(trail, open_next)
                    exit_reason = "atr_stop"
            # 2. Score exit, filled at the next open. With exit_style="scale"
            #    a position bigger than one unit sheds a single unit here
            #    instead of closing -- built in three steps, out in three. The
            #    stop above is deliberately NOT scaled: a stop is a stop.
            if exit_price is None:
                s = float(score[i])
                hit = (
                    units > 0
                    and s <= float(cfg["exit_long"])
                    or units < 0
                    and s >= float(cfg["exit_short"])
                )
                if hit and scale_out and abs(units) > 1:
                    scale_exit = True
                elif hit:
                    exit_price, exit_reason = open_next, "score_exit"

        # --- mark the bar, at the exposure actually held into it -----------
        # `units` here is the position carried INTO the bar, before any of this
        # bar's decisions fill at the next open. Exposure is units * fraction,
        # so max_units at the default 1/max_units is a fully-invested book and
        # directly comparable to buy-and-hold.
        if units != 0:
            mark_to = exit_price if exit_price is not None else close_next
            raw = (mark_to - prev_close) / prev_close * units * unit_fraction
        else:
            raw = 0.0

        if exit_price is not None:
            # Closing costs the whole remaining position.
            raw -= cost * abs(units) * unit_fraction
            side_sign = 1.0 if units > 0 else -1.0
            slice_pnl = ((exit_price - entry_price) / entry_price) * side_sign
            # Contribution to portfolio return: this slice plus anything
            # already booked by trims, net of entry+exit cost on every unit
            # that was ever opened.
            pnl = (
                realized
                + slice_pnl * abs(units) * unit_fraction
                - 2 * cost * peak_units * unit_fraction
            )
            trades.append(
                {
                    "side": "long" if units > 0 else "short",
                    "entry_index": entry_index,
                    "entry_ts": records[entry_index].timestamp.isoformat(),
                    "entry_price": entry_price,
                    "exit_index": i + 1,
                    "exit_ts": nxt.timestamp.isoformat(),
                    "exit_price": exit_price,
                    "exit_reason": exit_reason,
                    "bars": i + 1 - entry_index,
                    # Portfolio-return CONTRIBUTION, not the move on one share:
                    # a 1-unit winner and a 3-unit winner of the same size are
                    # different amounts of money, and the old model called them
                    # equal.
                    "pnl_pct": float(100.0 * pnl),
                    "peak_units": int(peak_units),
                }
            )
            units = 0
            peak_units = 0
            realized = 0.0
            trail = None
            last_action_bar = i

        # 3. Transitions, decided on bar i, filled at i+1 open. Mirrors
        #    `signal_engine.run_state_machine`: add above `add_long`, trim
        #    below `trim_long`, capped at `max_units`, long side only -- the
        #    live machine does not pyramid shorts either.
        can_act = (i - last_action_bar) >= cooldown and ready[i]
        s_now = float(score[i])
        if scale_exit and can_act:
            # Shed one unit of whichever side is open, booking its slice.
            step = -1 if units > 0 else 1
            realized += (
                ((open_next - entry_price) / entry_price)
                * unit_fraction
                * (1.0 if units > 0 else -1.0)
            )
            units += step
            raw -= cost * unit_fraction
            last_action_bar = i
        elif units > 0 and can_act:
            if s_now <= float(cfg["trim_long"]) and units > 1:
                # Book the trimmed unit and shrink the position.
                realized += ((open_next - entry_price) / entry_price) * unit_fraction
                units -= 1
                raw -= cost * unit_fraction
                last_action_bar = i
            elif s_now >= float(cfg["add_long"]) and units < max_units:
                # Size-weighted average entry, so a later add cannot make an
                # earlier unit look like it was bought at the add price.
                entry_price = (entry_price * units + open_next) / (units + 1)
                units += 1
                peak_units = max(peak_units, units)
                raw -= cost * unit_fraction
                last_action_bar = i
        elif units < 0 and can_act:
            # Mirror of the long ladder: a short adds as conviction falls
            # further and trims as it recovers toward zero.
            if s_now >= float(cfg["trim_short"]) and units < -1:
                realized += ((entry_price - open_next) / entry_price) * unit_fraction
                units += 1
                raw -= cost * unit_fraction
                last_action_bar = i
            elif s_now <= float(cfg["add_short"]) and units > -max_units:
                entry_price = (entry_price * abs(units) + open_next) / (abs(units) + 1)
                units -= 1
                peak_units = max(peak_units, abs(units))
                raw -= cost * unit_fraction
                last_action_bar = i
        elif units == 0 and can_act:
            if s_now >= float(cfg["entry_long"]):
                units, entry_price, entry_index = 1, open_next, i + 1
                peak_units = 1
                raw -= cost * unit_fraction
                last_action_bar = i
            elif allow_short and s_now <= float(cfg["entry_short"]):
                units, entry_price, entry_index = -1, open_next, i + 1
                peak_units = 1
                raw -= cost * unit_fraction
                last_action_bar = i

        # 4. Ratchet the trail off the bar we just marked to.
        a = atr[i + 1] if i + 1 < len(atr) else None
        if units != 0 and a:
            if units > 0:
                candidate = close_next - mult * float(a)
                trail = candidate if trail is None else max(trail, candidate)
            else:
                candidate = close_next + mult * float(a)
                trail = candidate if trail is None else min(trail, candidate)

        bar_returns.append(float(raw))
        equity.append(equity[-1] * (1.0 + raw))

    # Force-close anything still open at the last bar, so an open winner is
    # not silently counted as realised.
    if units != 0:
        last = float(records[-1].close)
        side_sign = 1.0 if units > 0 else -1.0
        slice_pnl = ((last - entry_price) / entry_price) * side_sign
        pnl = (
            realized
            + slice_pnl * abs(units) * unit_fraction
            - 2 * cost * peak_units * unit_fraction
        )
        trades.append(
            {
                "side": "long" if units > 0 else "short",
                "entry_index": entry_index,
                "entry_ts": records[entry_index].timestamp.isoformat(),
                "entry_price": entry_price,
                "exit_index": n - 1,
                "exit_ts": records[-1].timestamp.isoformat(),
                "exit_price": last,
                "exit_reason": "end_of_data",
                "bars": n - 1 - entry_index,
                "pnl_pct": float(100.0 * pnl),
                "peak_units": int(peak_units),
            }
        )

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
    # What the returns above MEAN, in capital terms.
    metrics["max_units"] = max_units
    metrics["unit_fraction"] = unit_fraction
    metrics["capital_model"] = (
        "fractional" if unit_fraction * max_units <= 1.0 + 1e-9 else "pyramiding"
    )
    scored_bars = max(1, len(records[warmup:]) - 1)
    # Average share of capital actually invested. Buy-and-hold is 100%, so a
    # return that trails it while this reads 30% is not the same failure as one
    # that trails it while fully invested.
    metrics["avg_exposure_pct"] = float(100.0 * unit_bars * unit_fraction / scored_bars)
    metrics["avg_units_when_in"] = (
        float(unit_bars / bars_in_market) if bars_in_market else 0.0
    )
    return BacktestResult(
        trades=trades, equity=equity, returns=bar_returns, metrics=metrics, config=cfg
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
        # Dollars, so a tuning pass can be read at a glance instead of in
        # percentage points. This is the percent SCALED by `capital` -- one
        # fully-invested unit compounded, not a position-sizing model: there is
        # no share count, no partial fill and no margin here. `capital` is
        # published alongside so the figure can never be read as an account
        # balance that came from somewhere.
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
