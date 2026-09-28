"""The buy/sell engine: one signed conviction line, and a position state machine.

Why this exists
---------------
The original scoring was a count of five booleans, of which `liquidity` was
hardwired `False` -- so the score could never exceed 4. `apply_position_gate`
then asked for `score == 4` to buy and `score == 5` to add, which made ADD
mathematically unreachable and BUY conditional on whale AND wave3 AND squeeze
AND trend all firing on the same bar. That is why the chart has never shown a
buy or a sell. It was not tuned too tight; it was unreachable.

What replaces it
----------------
A continuous, signed conviction in [-100, +100] built from five weighted
components, gated (not scored) on liquidity, and turned into discrete actions
by an explicit state machine with an ATR trailing stop. One number, one line
on the glass, and transitions -- not a marker on every bar.

Contract with the rest of the app
---------------------------------
- Everything is causal. Component `i` reads bars `0..i` only. Percentile ranks
  use trailing windows. This is enforced by `tests/test_signal_engine.py`'s
  causality test, which recomputes on truncated inputs and demands identical
  values -- the single property that makes the backtest worth anything.
- `conviction_series` is O(n) in the number of bars. The old `price_scores`
  was O(n^2): it re-ran Elliott/Bollinger/ADX over the whole prefix for every
  bar, on every 5-second `/api/state` poll.
- Nothing here raises. A component that cannot be computed contributes 0.0,
  and `components` says so, so a flat line is explainable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from chart_app.elmo import ElmoResult, _percentile_rank
from chart_app.sizing import resolve_sizing
from shared.chart_data import CandleRecord

Series = list[float | None]


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

WEIGHTS: dict[str, float] = {
    "trend": 26.0,      # EMA stack + ADX
    "momentum": 22.0,   # MACD histogram + RSI
    "elmo": 18.0,       # entropy-gated ALMA slope
    "whale": 22.0,      # net option premium, z-scored
    "breakout": 12.0,   # squeeze release direction
}

# These five were originally chosen BY HAND. `elmo` is no longer one of them.
#
# MEASURED 2026-09-19, paired across all 11 cached series (8 equities x 3y
# daily + SPY/QQQ/NVDA 15m), split-adjusted, fixed params throughout -- no
# per-series fitting, the same method that settled liq_window 10 -> 5:
#
# Run TWICE, under both capital models, because the position model changed
# mid-measurement (see `backtest.run_backtest`) and a conclusion that only
# holds under one of them is a conclusion about the model, not the weight:
#
#   elmo weight | position-aware (current)     | binary (superseded)
#               | mean d  | won  | P(d>0)      | mean d   | won  | P(d>0)
#   ------------+---------+------+-------------+----------+------+--------
#           12  | +1.09pp | 5/11 |  0.632      |  -2.17pp | 4/11 |  0.142
#           18  |   --    |  --  | (shipped)   |    --    |  --  | (shipped)
#           26  | -3.16pp | 3/11 |  0.288      |  -2.49pp | 4/11 |  0.275
#           34  | -4.68pp | 4/11 |  0.206      |  -5.65pp | 2/11 |  0.127
#           45  | -9.15pp | 3/11 |  0.140      | -12.06pp | 1/11 |  0.079
#
# Raising it is worse under BOTH models, monotonically, and the per-series
# argmax is scattered (12/18/26/45, no consensus) -- the signature of noise,
# not of a series that "wants" a different weight. ELMo already acts through
# the liquidity gate and the ordered-entropy filter; paying it more only
# crowds out trend and momentum.
#
# Dropping to 12 flips to a positive MEAN under the position-aware model
# (+1.09pp) and that is NOT a mandate: its median is negative (-0.34pp) and
# P(d>0) = 0.632 is a coin flip. The bar that liq_window 10 -> 5 cleared was
# 0.967. A mean dragged positive by one series while the median falls is the
# exact shape of a single outlier, not an effect.
#
# 18 stays. Do not raise it off an eyeball read of one panel on one symbol --
# that is the in-sample view, and it is what this table tested.
#
# They are overridable per run (`config["weights"]`) so they can be CHECKED,
# not so they become another slider -- deliberately absent from the gears and
# the tester. Overriding does NOT renormalise to 100: the denominator is the
# sum of whatever components are live on that bar, so an absolute weight stays
# comparable across runs.
#
# The other four remain hand-picked and untested. Same method, same cache, no
# billed calls -- that is the next honest measurement available here.


def resolve_weights(config: dict[str, Any] | None = None) -> dict[str, float]:
    """`WEIGHTS` with any per-run overrides merged in.

    Unknown keys are rejected rather than ignored: a typo'd component name in
    a sweep grid would otherwise evaluate the shipped weights and report the
    result as if the override had applied.
    """
    override = ((config or {}).get("weights") or {})
    unknown = set(override) - set(WEIGHTS)
    if unknown:
        raise ValueError(f"unknown weight component(s): {sorted(unknown)}")
    return {k: float(override.get(k, v)) for k, v in WEIGHTS.items()}

# WHAT THESE DEFAULTS ARE, AND WHAT THEY ARE NOT
# ----------------------------------------------
# They are calibrated so the conviction line and its marks are *legible* on a
# chart: entries a few times a month on a daily tape, a stop wide enough to
# survive normal noise, and an exit that fires on a real reversal rather than
# a wobble. They were chosen from `chart_app/backtest_runner.py --stage sweep`
# on 8 equities x 3y daily plus 3 intraday series.
#
# They are NOT an edge claim, and nothing here should be traded mechanically.
# Measured on that same universe on 2026-09-18, split-adjusted, 2bp a side:
#   * walk-forward (4 folds, params refit per fold): 3/11 series positive
#     out-of-sample, 33% fold hit rate.
#   * permutation test (60 return-shuffled surrogates per series): median
#     p = 0.598, and 0/12 series beat their own null at the 10% level.
# In other words the engine's P&L is not distinguishable from running the same
# rules on shuffled data. Widening the ATR stop raises returns only by raising
# exposure toward 84% -- i.e. by converging on buy-and-hold, which still beat
# it on 8 of 8 daily series.
#
# Treat the line as a fast read of where trend, momentum, order and flow agree.
# Before changing a default here, re-run the suite and put the new numbers in
# `chart_app/HANDOFF.md`; do not tune against a single symbol.
DEFAULTS: dict[str, Any] = {
    # Entry / exit levels on the -100..+100 conviction line.
    "entry_long": 30.0,
    "add_long": 55.0,
    "trim_long": 12.0,
    "exit_long": -12.0,
    "entry_short": -35.0,
    # Shorts are the mirror of the long ladder, not a stunted version of it.
    # They used to have entry and exit only -- no pyramiding, no scale-out --
    # so a short could never express more or less conviction than "on".
    # Signs flip: a short ADDS as conviction falls further, TRIMS as it
    # recovers toward zero.
    "add_short": -55.0,
    "trim_short": -12.0,
    "exit_short": 8.0,
    "allow_short": False,
    # SIZING -- see `chart_app/sizing.py` for the whole model.
    # The full position as a share of equity. None = resolve an OLD profile's
    # `max_units` x `unit_fraction` ladder exactly (3 x 1/3 when neither is
    # set, which is the same as the new defaults below).
    "position_size": None,
    # "all" buys the full position on entry; "scale" buys `entry_slice` of it
    # per entry/add order until it is all on.
    "entry_style": "scale",
    "entry_slice": 1.0 / 3.0,
    # "all"   -- an exit signal closes the whole position at once (default).
    # "scale" -- an exit signal sells `exit_slice` of the full position per
    #            action. A trim is always one `exit_slice`. A STOP (or a
    #            margin call) always closes everything: a stop is a stop.
    "exit_style": "all",
    "exit_slice": 1.0 / 3.0,
    # Share of each buy that is BORROWED. 0 = cash. Never changes the share
    # count; capped at 80% and at what the venue lends (Reg T 50% on stock).
    "margin_pct": 0.0,
    # Whole shares unless ticked -- for a BRK-A, where one share is an order.
    "fractional": False,
    # Take-profit levels, % gain off the average entry: [5, 10] sells one exit
    # chunk at +5% and another at +10%. Empty = no profit target.
    "take_profit": [],
    # ATR trailing stop (the published ELMo uses "configurable ATR values").
    # 4.0, not 2.5: at 2.5 the trail was doing nearly all the exiting and
    # median return across the daily universe was +4.4% against +30.2% at 4.0.
    "atr_period": 14,
    "atr_stop_mult": 4.0,
    # Minimum bars between two actions, so one wobble across a level does not
    # print a buy/sell pair on adjacent bars.
    "cooldown_bars": 3,
    # Windows
    "whale_z_window": 50,
    "bandwidth_window": 100,
    "squeeze_pct": 20.0,      # bandwidth percentile that counts as a squeeze
    "adx_period": 14,
    "rsi_period": 14,
    # The momentum vote's MACD. These were hard-coded 12/26/9 while the chart's
    # MACD pane took sliders, so a tune set to 6/13/4 on the pane never reached
    # the conviction line or the tester's P&L (found 2026-09-21 on XE/SMR 5m).
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "wave_window": 200,       # cap on the Elliott prefix (keeps it O(n))
    "wave_stride": 5,         # recompute the wave count every N bars
    # Liquidity gate: conviction is multiplied by this when ELMo says the
    # book is thin. Not zero -- a thin book damps conviction, it does not
    # invalidate the tape.
    "illiquid_damp": 0.6,
    # Liquidity percentile below which the book counts as fragile. The bottom
    # quartile, not the bottom half -- see the gate comment in
    # `conviction_series`.
    "fragile_below": 25.0,
}


# ---------------------------------------------------------------------------
# Causal rolling indicators
# ---------------------------------------------------------------------------


def _ema(values: Sequence[float], period: int) -> Series:
    out: Series = [None] * len(values)
    if len(values) < period or period < 1:
        return out
    seed = float(np.mean(values[:period]))
    out[period - 1] = seed
    k = 2.0 / (period + 1)
    prev = seed
    for i in range(period, len(values)):
        prev = float(values[i]) * k + prev * (1.0 - k)
        out[i] = float(prev)
    return out


def atr_series(records: Sequence[CandleRecord], period: int = 14) -> Series:
    """Wilder ATR, `None` until `period` true ranges exist."""
    n = len(records)
    out: Series = [None] * n
    if n <= period or period < 1:
        return out
    trs: list[float] = []
    prev_close = float(records[0].close)
    for record in records[1:]:
        high, low, close = float(record.high), float(record.low), float(record.close)
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        prev_close = close
    atr = float(np.mean(trs[:period]))
    out[period] = atr
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
        out[i + 1] = float(atr)
    return out


def adx_series(
    records: Sequence[CandleRecord], period: int = 14
) -> tuple[Series, Series, Series]:
    """Wilder ADX / +DI / -DI as per-bar series.

    `Direction.trend_engine.adx` returns one scalar for a whole array, so
    scoring every bar with it costs a full re-scan per bar. This is the same
    Wilder recursion carried forward once.
    """
    n = len(records)
    adx: Series = [None] * n
    pdi: Series = [None] * n
    mdi: Series = [None] * n
    if n <= period * 2 or period < 1:
        return adx, pdi, mdi

    tr: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for i in range(1, n):
        high, low = float(records[i].high), float(records[i].low)
        ph, pl = float(records[i - 1].high), float(records[i - 1].low)
        pc = float(records[i - 1].close)
        up, down = high - ph, pl - low
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
        tr.append(max(high - low, abs(high - pc), abs(low - pc)))

    # Wilder smoothing, seeded on the first `period` values.
    str_ = float(sum(tr[:period]))
    spdm = float(sum(plus_dm[:period]))
    smdm = float(sum(minus_dm[:period]))
    dx_values: list[tuple[int, float]] = []

    def _publish(bar: int, s_tr: float, s_p: float, s_m: float) -> float:
        if s_tr <= 0:
            pdi[bar] = mdi[bar] = 0.0
            return 0.0
        p = 100.0 * s_p / s_tr
        m = 100.0 * s_m / s_tr
        pdi[bar], mdi[bar] = float(p), float(m)
        total = p + m
        return 0.0 if total == 0 else 100.0 * abs(p - m) / total

    dx_values.append((period, _publish(period, str_, spdm, smdm)))
    for i in range(period, len(tr)):
        str_ = str_ - str_ / period + tr[i]
        spdm = spdm - spdm / period + plus_dm[i]
        smdm = smdm - smdm / period + minus_dm[i]
        dx_values.append((i + 1, _publish(i + 1, str_, spdm, smdm)))

    if len(dx_values) >= period:
        seed = float(np.mean([v for _, v in dx_values[:period]]))
        adx[dx_values[period - 1][0]] = seed
        prev = seed
        for bar, dx in dx_values[period:]:
            prev = (prev * (period - 1) + dx) / period
            adx[bar] = float(prev)
    return adx, pdi, mdi


def rsi_series(closes: Sequence[float], period: int = 14) -> Series:
    n = len(closes)
    out: Series = [None] * n
    if n <= period or period < 1:
        return out
    gains, losses = [], []
    for i in range(1, n):
        change = float(closes[i]) - float(closes[i - 1])
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))

    def _rsi(g: float, loss: float) -> float:
        if loss == 0:
            return 100.0
        return 100.0 - (100.0 / (1.0 + g / loss))

    out[period] = _rsi(avg_gain, avg_loss)
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i + 1] = _rsi(avg_gain, avg_loss)
    return out


def macd_hist_series(
    closes: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> Series:
    ef, es = _ema(closes, fast), _ema(closes, slow)
    line: Series = [
        None if (a is None or b is None) else float(a - b)
        for a, b in zip(ef, es, strict=True)
    ]
    dense = [(i, v) for i, v in enumerate(line) if v is not None]
    if len(dense) < signal:
        return [None] * len(closes)
    sig_tail = _ema([v for _, v in dense], signal)
    out: Series = [None] * len(closes)
    for (idx, value), sig in zip(dense, sig_tail, strict=True):
        if sig is not None:
            out[idx] = float(value - sig)
    return out


def bandwidth_pct_series(
    closes: Sequence[float], window: int = 20, rank_window: int = 100
) -> tuple[Series, Series, Series, Series]:
    """Bollinger bandwidth, its trailing percentile, and the upper/lower bands.

    The squeeze test here is a *percentile*, not the fixed 2%-of-price cut in
    `Direction.bollinger_analyzer.detect_squeeze`. A fixed cut means "squeeze"
    fires constantly on a 15m SPY tape and essentially never on a daily one --
    the same code reporting two different things depending on timeframe. A
    percentile asks the only question that travels: is this band narrow *for
    this symbol on this timeframe*?
    """
    n = len(closes)
    bw: Series = [None] * n
    pct: Series = [None] * n
    upper: Series = [None] * n
    lower: Series = [None] * n
    if n < window or window < 2:
        return bw, pct, upper, lower
    arr = np.asarray(closes, dtype=float)
    for i in range(window - 1, n):
        win = arr[i - window + 1 : i + 1]
        sma = float(win.mean())
        std = float(win.std())
        upper[i] = sma + 2.0 * std
        lower[i] = sma - 2.0 * std
        bw[i] = 0.0 if sma == 0 else float(4.0 * std / sma)
    for i in range(n):
        if bw[i] is None:
            continue
        lo = max(0, i - rank_window + 1)
        hist = [v for v in bw[lo : i + 1] if v is not None]
        if len(hist) < 2:
            continue
        pct[i] = float(_percentile_rank(hist, float(bw[i])))
    return bw, pct, upper, lower


def _wave_bias_series(
    closes: Sequence[float], *, window: int, stride: int
) -> list[float]:
    """+1 / 0 / -1 Elliott impulse-wave-3 bias, recomputed on a stride.

    `Direction.elliott_wave.count_waves` is O(len(prices)); calling it once per
    bar over the full prefix is the O(n^2) that made the old scorer slow. A
    capped window plus a stride (forward-filled between recomputes) keeps the
    same reading at a fraction of the cost -- the wave count does not change
    meaningfully bar-to-bar anyway.
    """
    from Direction.elliott_wave import count_waves

    n = len(closes)
    out = [0.0] * n
    if n < 10:
        return out
    stride = max(1, int(stride))
    last = 0.0
    for i in range(n):
        if i % stride == 0:
            lo = max(0, i - window + 1)
            try:
                counted = count_waves(list(closes[lo : i + 1]))
                impulse = counted.get("wave_type") == "impulse_wave_3"
            except Exception:  # noqa: BLE001 -- degrade to neutral, never raise
                impulse = False
            if impulse:
                # Unsigned on its own; sign it by the swing it sits in.
                lo2 = max(0, i - 20)
                last = 1.0 if closes[i] >= closes[lo2] else -1.0
            else:
                last = 0.0
        out[i] = last
    return out


def _zscore_series(values: Sequence[float], window: int) -> Series:
    """Trailing z-score. `None` until the window has two points of spread."""
    n = len(values)
    out: Series = [None] * n
    for i in range(n):
        lo = max(0, i - window + 1)
        win = [float(v) for v in values[lo : i + 1]]
        if len(win) < 5:
            continue
        mean = float(np.mean(win))
        std = float(np.std(win))
        if std <= 0:
            out[i] = 0.0
            continue
        out[i] = float((float(values[i]) - mean) / std)
    return out


def _squash(value: float | None, scale: float = 1.0) -> float:
    """Map an unbounded reading into [-1, 1] without a hard clip.

    tanh rather than a clamp so a 6-sigma whale print and a 3-sigma one are
    still distinguishable at the top of the range instead of both pinning.
    """
    if value is None:
        return 0.0
    return float(np.tanh(float(value) / scale))


# ---------------------------------------------------------------------------
# Conviction
# ---------------------------------------------------------------------------


@dataclass
class ConvictionResult:
    score: list[float] = field(default_factory=list)
    components: dict[str, list[float]] = field(default_factory=dict)
    available: dict[str, bool] = field(default_factory=dict)
    liquidity_gate: list[float] = field(default_factory=list)
    atr: Series = field(default_factory=list)
    ready: list[bool] = field(default_factory=list)
    # Per-bar: did the whale component have an input on this bar. `available`
    # is the series-wide summary; this is the honest per-bar answer, and the
    # two disagree whenever the flow window is shorter than the chart window.
    flow_observed: list[bool] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "components": self.components,
            "available": self.available,
            "liquidity_gate": self.liquidity_gate,
            "ready": self.ready,
            # Provenance, house pattern: a bar scored without flow is not the
            # same measurement as a bar scored with it, and the UI must be
            # able to say which it is looking at.
            "flow_observed": self.flow_observed,
            # Published so the chart can offset its buy/sell glyphs by a
            # fraction of ATR instead of a fixed pixel gap, which overlaps the
            # wick on a volatile bar and floats away on a quiet one.
            "atr": self.atr,
        }


def conviction_series(
    records: Sequence[CandleRecord],
    *,
    elmo: ElmoResult | None = None,
    flow_net: Sequence[float] | None = None,
    flow_observed: Sequence[bool] | None = None,
    config: dict[str, Any] | None = None,
) -> ConvictionResult:
    """Signed conviction in [-100, +100], one value per bar.

    `flow_net` is the per-bar net option premium (calls minus puts) from
    `flow_pane.bin_flow`. When it is absent the whale component is not merely
    zeroed, it is **dropped from the denominator**: the score renormalises
    over the components that are actually live.

    That was the opposite of the original choice here, and the original was
    wrong in a way that invalidated the calibration. Leaving whale's 22 points
    in the divisor with nothing to put in the numerator capped the achievable
    score near 44 (measured on SPY daily: max +31.9 in a +15% year with the
    trend component averaging +0.63), so an entry threshold of +35 was
    unreachable in practice -- the same failure mode as the five-boolean score
    it replaced. And because the live chart HAS flow while a backtest does
    not, the two ran on different scales: nothing learned from a sweep would
    have transferred to the glass.

    Note the asymmetry with `breakout`, which is deliberately NOT dropped when
    it reads zero. "No squeeze released on this bar" is a measurement; "there
    is no options tape for this symbol" is a missing input. Only the second
    one earns a smaller denominator.
    """
    cfg = {**DEFAULTS, **(config or {})}
    n = len(records)
    if n == 0:
        return ConvictionResult()

    closes = [float(r.close) for r in records]
    comp: dict[str, list[float]] = {k: [0.0] * n for k in WEIGHTS}
    # Which components have an input at all. See the docstring for why a
    # missing input shrinks the denominator and a zero reading does not.
    available: dict[str, bool] = {k: True for k in WEIGHTS}
    available["whale"] = bool(flow_net)

    # --- trend: EMA stack direction, scaled by ADX -------------------------
    # Slow EMA cascades 200 → 100. 1h with the 30d provider cap is ~162 bars,
    # so EMA200 never defines; dropping the 0.3 close-vs-slow term left the
    # stack silently capped at 0.7 (measured: a monotonic ramp read 0.7 vs
    # 1.0 on a 250-bar tape). EMA100 seeds at bar 99 and is causal (fixed
    # period, not min(200, n) -- a period that depends on series length
    # would fail test_conviction_is_causal).
    #
    # Missing terms renormalise rather than dilute, same rule as a missing
    # whale input. 4h (~42 bars) never seeds EMA50 either; skipping the
    # whole stack then left trend at 0 and ready=False for the entire chart.
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    ema100 = _ema(closes, 100)
    ema200 = _ema(closes, 200)
    adx, pdi, mdi = adx_series(records, int(cfg["adx_period"]))
    for i in range(n):
        terms: list[tuple[float, float]] = []
        a, b = ema20[i], ema50[i]
        if a is not None and b is not None:
            terms.append((0.5, 1.0 if a > b else -1.0))
        slow = ema200[i] if ema200[i] is not None else ema100[i]
        if slow is not None:
            terms.append((0.3, 1.0 if closes[i] > slow else -1.0))
        p, m = pdi[i], mdi[i]
        if p is not None and m is not None:
            terms.append((0.2, 1.0 if p > m else -1.0))
        if not terms:
            continue
        weight_sum = sum(w for w, _ in terms)
        stack = sum(w * s for w, s in terms) / weight_sum
        strength = 0.0 if adx[i] is None else min(1.0, float(adx[i]) / 35.0)
        # Floor the strength at 0.35: a flat ADX means "no trend", which
        # should mute the trend vote, not delete it.
        comp["trend"][i] = float(np.clip(stack, -1.0, 1.0)) * (0.35 + 0.65 * strength)

    # --- momentum: MACD histogram in ATR units + centred RSI ---------------
    hist = macd_hist_series(
        closes, int(cfg["macd_fast"]), int(cfg["macd_slow"]), int(cfg["macd_signal"])
    )
    rsi = rsi_series(closes, int(cfg["rsi_period"]))
    atr = atr_series(records, int(cfg["atr_period"]))
    for i in range(n):
        parts: list[float] = []
        if hist[i] is not None and atr[i]:
            # Normalising by ATR is what makes this comparable across symbols:
            # a 0.30 MACD histogram is huge on SPY and noise on NVDA.
            parts.append(_squash(float(hist[i]) / float(atr[i]), 0.6))
        if rsi[i] is not None:
            parts.append(float(np.clip((float(rsi[i]) - 50.0) / 25.0, -1.0, 1.0)))
        if parts:
            comp["momentum"][i] = float(np.mean(parts))

    # --- ELMo: direction from ALMA slope, conviction from low entropy ------
    if elmo is not None and elmo.alma_slope:
        for i in range(min(n, len(elmo.alma_slope))):
            slope = elmo.alma_slope[i]
            rank = elmo.entropy_rank[i] if i < len(elmo.entropy_rank) else None
            if slope is None or rank is None:
                continue
            direction = _squash(slope, 0.15)
            # Magnitude off the entropy RANK, not the raw level: the raw scale
            # is not portable (a random walk reads ~83, a clean trend ~66), so
            # a fixed divisor made this component structurally tiny on every
            # real tape. Rank 0 (most ordered it has been) -> full weight,
            # rank 100 (pure chop) -> nothing.
            order = float(np.clip((100.0 - float(rank)) / 100.0, 0.0, 1.0))
            ordered = elmo.ordered[i] if i < len(elmo.ordered) else False
            comp["elmo"][i] = direction * order * (1.0 if ordered else 0.6)

    # --- whale: net premium, z-scored against its own recent history -------
    if flow_net:
        net = [float(v) for v in list(flow_net)[:n]] + [0.0] * max(0, n - len(flow_net))
        z = _zscore_series(net, int(cfg["whale_z_window"]))
        for i in range(n):
            comp["whale"][i] = _squash(z[i], 1.5)

    # --- breakout: squeeze release, signed by which band broke -------------
    _bw, bw_pct, up_band, dn_band = bandwidth_pct_series(
        closes, 20, int(cfg["bandwidth_window"])
    )
    for i in range(1, n):
        p_now, p_prev = bw_pct[i], bw_pct[i - 1]
        if p_now is None or p_prev is None:
            continue
        was_squeezed = p_prev <= float(cfg["squeeze_pct"])
        expanding = p_now > p_prev
        if not (was_squeezed and expanding):
            continue
        hi, lo = up_band[i], dn_band[i]
        if hi is None or lo is None or hi <= lo:
            continue
        # %B mapped to [-1, 1]: above the upper band is +1, below lower -1.
        pct_b = (closes[i] - lo) / (hi - lo)
        comp["breakout"][i] = float(np.clip(2.0 * pct_b - 1.0, -1.0, 1.0))

    # --- Elliott wave bias folds into trend, it is not its own weight ------
    waves = _wave_bias_series(
        closes, window=int(cfg["wave_window"]), stride=int(cfg["wave_stride"])
    )
    for i in range(n):
        # A confirmed impulse leg reinforces an agreeing trend vote and is
        # ignored when it disagrees -- it is a corroborator, not a second
        # opinion that can flip the line on its own.
        if waves[i] and comp["trend"][i] * waves[i] > 0:
            comp["trend"][i] = float(np.clip(comp["trend"][i] * 1.25, -1.0, 1.0))

    # --- liquidity gate ----------------------------------------------------
    gate = [1.0] * n
    if elmo is not None and elmo.liquidity:
        damp = float(cfg["illiquid_damp"])
        fragile_below = float(cfg["fragile_below"])
        for i in range(min(n, len(elmo.liquidity))):
            lv = elmo.liquidity[i]
            if lv is None:
                continue  # no volume -> cannot judge -> do not damp
            # Gate on the FRAGILE tail, not on `elmo.liquid` (which is the
            # healthy-book leg at the median). The Hui-Heubel ratio trends, so
            # its trailing rank is not uniform -- gating at the median damped
            # 67-88% of real bars (measured: SPY 33% liquid, NVDA 14%, AMD
            # 12%), which is a flat tax on conviction rather than a filter.
            gate[i] = damp if lv < fragile_below else 1.0

    weights = resolve_weights(cfg)
    live_keys = [k for k in weights if available[k]]
    base_weight = sum(weights[k] for k in live_keys)

    # The denominator is decided PER BAR, not once for the series.
    #
    # `available` answers "did this run have flow at all", which is the right
    # question only when the flow window and the chart window are the same
    # length. They are not: the flow pull is capped at CHART_APP_FLOW_DAYS (5)
    # session dates while a 15m chart spans 20d (14 sessions). Measured on SPY
    # 2026-09-18 -- 266 of 373 bars had no flow input, yet whale's 22 points
    # stayed in their divisor, understating 252 bars by 28.2% and moving 31 of
    # them across an entry/exit level. That is the same failure this function's
    # docstring says it fixed, resurfacing one granularity down.
    #
    # `flow_observed` comes from `flow_stamp.flow_observed_bars`, which keys on
    # session dates the provider actually answered for -- NOT on a non-zero
    # premium, so a covered bar reading zero correctly stays in.
    whale_mask: list[bool] | None = None
    if available["whale"] and flow_observed is not None:
        whale_mask = [
            bool(flow_observed[i]) if i < len(flow_observed) else False
            for i in range(n)
        ]

    score: list[float] = []
    ready: list[bool] = []
    for i in range(n):
        keys = live_keys
        total_weight = base_weight
        if whale_mask is not None and not whale_mask[i]:
            keys = [k for k in live_keys if k != "whale"]
            total_weight = base_weight - weights["whale"]
        total_weight = total_weight or 1.0
        raw = sum(weights[k] * comp[k][i] for k in keys)
        score.append(float(np.clip(raw / total_weight * 100.0 * gate[i], -100.0, 100.0)))
        # "Ready" = ADX has seeded. EMA50 used to be required too, which
        # made every 4h chart (~42 bars under the 30d cap) permanently
        # unready. ADX needs ~28 bars; that is the binding constraint.
        ready.append(adx[i] is not None)

    return ConvictionResult(
        score=score,
        components={k: comp[k] for k in WEIGHTS},
        available=available,
        liquidity_gate=gate,
        atr=atr,
        ready=ready,
        flow_observed=(
            whale_mask if whale_mask is not None else [available["whale"]] * n
        ),
    )


# ---------------------------------------------------------------------------
# Position state machine
# ---------------------------------------------------------------------------

_ACTIONS = ("buy", "add", "trim", "sell", "short", "cover", "none")


@dataclass
class Trade:
    side: str
    entry_index: int
    entry_ts: str
    entry_price: float
    exit_index: int | None = None
    exit_ts: str | None = None
    exit_price: float | None = None
    exit_reason: str | None = None

    @property
    def closed(self) -> bool:
        return self.exit_price is not None

    def pnl_pct(self) -> float | None:
        if self.exit_price is None or self.entry_price == 0:
            return None
        raw = (self.exit_price - self.entry_price) / self.entry_price
        return float(100.0 * (raw if self.side == "long" else -raw))

    def as_dict(self) -> dict[str, Any]:
        return {
            "side": self.side,
            "entry_index": self.entry_index,
            "entry_ts": self.entry_ts,
            "entry_price": self.entry_price,
            "exit_index": self.exit_index,
            "exit_ts": self.exit_ts,
            "exit_price": self.exit_price,
            "exit_reason": self.exit_reason,
            "pnl_pct": self.pnl_pct(),
        }


@dataclass
class SignalRun:
    actions: list[str] = field(default_factory=list)
    position: list[float] = field(default_factory=list)
    stop: Series = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)

    def markers(self) -> list[str]:
        return list(self.actions)


def run_state_machine(
    records: Sequence[CandleRecord],
    conviction: ConvictionResult,
    *,
    config: dict[str, Any] | None = None,
) -> SignalRun:
    """Turn the conviction line into discrete actions with an ATR trail.

    Emits an action only on a *transition*. The old gate labelled every
    qualifying bar `hold`, which put a grey dot under most of the tape and
    buried the two marks that mattered.

    Fill convention: the action is decided on bar `i`'s close and recorded at
    bar `i`. `backtest.py` is the thing that must not cheat, and it fills at
    bar `i+1`'s open. Keeping the two separate means the chart shows you the
    bar that triggered while the P&L assumes the bar you could actually buy.
    """
    cfg = {**DEFAULTS, **(config or {})}
    n = len(records)
    actions = ["none"] * n
    position = [0.0] * n
    stop: Series = [None] * n
    trades: list[Trade] = []
    if n == 0:
        return SignalRun(actions, position, stop, trades)

    atr = conviction.atr or atr_series(records, int(cfg["atr_period"]))
    score = conviction.score or [0.0] * n
    ready = conviction.ready or [True] * n
    mult = float(cfg["atr_stop_mult"])
    cooldown = int(cfg["cooldown_bars"])
    # The same ladder `backtest.run_backtest` walks (`chart_app/sizing.py`),
    # so the marks and the P&L cannot disagree about how big a position gets.
    # `qty` is the signed share of the FULL position; `position` publishes it
    # times `position_scale` (max_units for an old profile, whose live runners
    # multiply by unit_fraction; 1 for a new one).
    sz = resolve_sizing(cfg)
    scale_out = sz.scale_out

    qty = 0.0            # >0 long, <0 short, as a share of the full position
    trail: float | None = None
    last_action_bar = -10_000
    open_trade: Trade | None = None
    avg_entry = 0.0      # size-weighted, at the closes this machine acts on
    tp_fired: set[int] = set()

    def _grow(old: float, new: float, price: float) -> None:
        nonlocal avg_entry
        if abs(new) > abs(old) > 0:
            avg_entry = (avg_entry * abs(old) + price * (abs(new) - abs(old))) / abs(new)

    def _close(i: int, reason: str) -> None:
        nonlocal qty, trail, open_trade
        if open_trade is not None:
            open_trade.exit_index = i
            open_trade.exit_ts = records[i].timestamp.isoformat()
            open_trade.exit_price = float(records[i].close)
            open_trade.exit_reason = reason
        qty = 0.0
        trail = None
        open_trade = None

    for i in range(n):
        price = float(records[i].close)
        low, high = float(records[i].low), float(records[i].high)
        s = float(score[i]) if i < len(score) else 0.0
        a = atr[i] if i < len(atr) else None
        can_act = (i - last_action_bar) >= cooldown and bool(ready[i])

        # 1. Stops first: a stop that was hit intrabar is not overridden by a
        #    score that recovered by the close.
        if qty > 0 and trail is not None and low <= trail:
            actions[i] = "sell"
            _close(i, "atr_stop")
            last_action_bar = i
        elif qty < 0 and trail is not None and high >= trail:
            actions[i] = "cover"
            _close(i, "atr_stop")
            last_action_bar = i

        # 2. Score-driven transitions. Long and short are mirrors.
        elif qty > 0:
            if s <= float(cfg["exit_long"]):
                if scale_out and sz.more_than_one_exit(qty) and can_act:
                    actions[i] = "trim"
                    qty = sz.shed(qty)
                    last_action_bar = i
                elif scale_out and sz.more_than_one_exit(qty):
                    pass          # cooling down; shed the next unit later
                else:
                    actions[i] = "sell"
                    _close(i, "score_exit")
                    last_action_bar = i
            elif s <= float(cfg["trim_long"]) and can_act and sz.more_than_one_exit(qty):
                actions[i] = "trim"
                qty = sz.shed(qty)
                last_action_bar = i
            elif s >= float(cfg["add_long"]) and can_act and sz.can_add(qty):
                actions[i] = "add"
                _grow(qty, sz.add(qty), price)
                qty = sz.add(qty)
                last_action_bar = i
        elif qty < 0:
            if s >= float(cfg["exit_short"]):
                if scale_out and sz.more_than_one_exit(qty) and can_act:
                    actions[i] = "trim"
                    qty = sz.shed(qty)
                    last_action_bar = i
                elif scale_out and sz.more_than_one_exit(qty):
                    pass
                else:
                    actions[i] = "cover"
                    _close(i, "score_exit")
                    last_action_bar = i
            elif s >= float(cfg["trim_short"]) and can_act and sz.more_than_one_exit(qty):
                actions[i] = "trim"
                qty = sz.shed(qty)
                last_action_bar = i
            elif s <= float(cfg["add_short"]) and can_act and sz.can_add(qty):
                actions[i] = "add"
                _grow(qty, sz.add(qty), price)
                qty = sz.add(qty)
                last_action_bar = i
        else:
            if s >= float(cfg["entry_long"]) and can_act:
                actions[i] = "buy"
                qty = sz.first()
                avg_entry = price
                tp_fired.clear()
                open_trade = Trade(
                    side="long",
                    entry_index=i,
                    entry_ts=records[i].timestamp.isoformat(),
                    entry_price=price,
                )
                trades.append(open_trade)
                last_action_bar = i
            elif (
                cfg["allow_short"]
                and s <= float(cfg["entry_short"])
                and can_act
            ):
                actions[i] = "short"
                qty = -sz.first()
                avg_entry = price
                tp_fired.clear()
                open_trade = Trade(
                    side="short",
                    entry_index=i,
                    entry_ts=records[i].timestamp.isoformat(),
                    entry_price=price,
                )
                trades.append(open_trade)
                last_action_bar = i

        # 2b. Take-profit levels ("take profit at 5% and 10%"): a resting order
        #     per level off the average entry, one exit chunk each, once per
        #     trade, cooldown-free. Same rule `backtest.run_backtest` fills;
        #     marked only on a bar with no other action, since a bar carries
        #     one mark.
        if qty != 0 and actions[i] == "none" and sz.take_profit and avg_entry:
            for k, pct in enumerate(sz.take_profit):
                if k in tp_fired or qty == 0:
                    continue
                reached = (
                    high >= avg_entry * (1.0 + pct / 100.0)
                    if qty > 0
                    else low <= avg_entry * (1.0 - pct / 100.0)
                )
                if not reached:
                    break
                tp_fired.add(k)
                new = sz.shed(qty)
                if new == 0:
                    actions[i] = "sell" if qty > 0 else "cover"
                    _close(i, "take_profit")
                else:
                    actions[i] = "trim"
                    qty = new
                last_action_bar = i

        # 3. Ratchet the trail. Only ever in the favourable direction.
        if qty != 0 and a:
            if qty > 0:
                candidate = price - mult * float(a)
                trail = candidate if trail is None else max(trail, candidate)
            else:
                candidate = price + mult * float(a)
                trail = candidate if trail is None else min(trail, candidate)
        position[i] = qty * sz.position_scale
        stop[i] = trail

    return SignalRun(actions, position, stop, trades)


def evaluate(
    records: Sequence[CandleRecord],
    *,
    elmo: ElmoResult | None = None,
    flow_net: Sequence[float] | None = None,
    flow_observed: Sequence[bool] | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[ConvictionResult, SignalRun]:
    """Conviction + state machine in one call. The entry point callers want."""
    conv = conviction_series(
        records,
        elmo=elmo,
        flow_net=flow_net,
        flow_observed=flow_observed,
        config=config,
    )
    run = run_state_machine(records, conv, config=config)
    return conv, run
