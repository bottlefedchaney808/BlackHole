"""ELMo -- Entropy, Liquidity and Momentum.

A local, OHLCV-only implementation of the idea behind the TradingView strategy
of the same name ("Entropy, Liquidity and Momentum (ELMo)", source protected).
Nothing is copied: the published description names the three ingredients and
how they combine, and each is implemented here from the standard literature so
every number is one we can explain and test.

The three legs
--------------
**Entropy** is the *unsigned* trend-strength meter. Shannon entropy of the
recent return distribution is low when returns pile into one bucket (an
ordered, trending tape) and high when they spread out (a random, choppy one).
It says *whether* to trust a direction, never *which* direction -- that is the
momentum leg's job.

**Liquidity** is the Hui-Heubel ratio: how much price had to move to absorb
the volume that traded. A big range on thin volume is a fragile book. Note the
sign convention: the textbook ratio is an *illiquidity* measure (higher =
worse), while the TradingView author's `lhh` reads the other way ("low values
= illiquid/fragile, high = liquid/resilient"). We compute the textbook ratio
in `hui_heubel` and publish the inverted percentile as `liquidity_score`,
matching the author's convention on the glass.

**Momentum** is direction: an ALMA (Arnaud Legoux MA -- Gaussian-weighted and
offset toward the recent end, so it turns earlier than an EMA at equal
smoothing) plus its slope.

What this module deliberately does NOT do
-----------------------------------------
Fabricate a liquidity reading when there is no volume. Several symbols on this
feed (cash indices especially) come back with `volume=None`; the rest of the
app treats that as `1.0` for VWAP purposes, which is harmless there and would
be a lie here -- a made-up share count is the whole denominator of the
Hui-Heubel ratio. Those bars return `None` and the UI says "no volume", which
is the same discipline as `vol_source`/`corr_source` elsewhere in the repo: a
fallback must never be readable as a measurement.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from shared.chart_data import CandleRecord

Series = list[float | None]

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULTS: dict[str, Any] = {
    # Entropy
    "entropy_window": 20,          # bars in the return histogram
    "entropy_scale_window": 100,   # bars used to size the buckets (sigma)
    "entropy_fast": 5,             # EMA of entropy -- fast
    "entropy_slow": 20,            # EMA of entropy -- slow
    "entropy_method": "returns",   # "returns" | "permutation"
    "entropy_rank_window": 200,    # bars the entropy percentile is taken over
    # Liquidity (Hui-Heubel)
    #
    # 5, not 10. Measured 2026-09-18 by walk-forward on 8 equities x 3y daily
    # (3 folds, fixed params throughout -- no per-fold fitting): a 5-bar window
    # returned mean +3.98pp and median +2.51pp more than a 10-bar one out of
    # sample, winning on 5 of 8 series, bootstrap P(mean improvement > 0) =
    # 0.967. The mechanism is plausible rather than fitted: book depth changes
    # within a session, and a 10-bar average of range-per-share smears a
    # liquidity event across more bars than it lasts.
    #
    # The SAME sweep found that tuning this per symbol does NOT survive out of
    # sample (4/8 series, +0.55pp median, against +11.5pp in sample). Change
    # it globally on evidence like the above; do not tune it per ticker.
    "liq_window": 5,               # bars in the range/volume measurement
    # Left at 200 deliberately. A 60-bar rank won 11 of 18 per-fold picks,
    # which looks like a mandate and is not one: as a fixed default it scored
    # WORSE out of sample (mean 9.27 vs 12.15). Pick-frequency across folds
    # and quality as a default are different questions.
    "liq_norm_window": 200,        # bars the percentile rank is taken over
    # Momentum (ALMA)
    "alma_window": 9,
    "alma_offset": 0.85,
    "alma_sigma": 6.0,
}

# ---------------------------------------------------------------------------
# Window coherence: liquidity is the floor
# ---------------------------------------------------------------------------
#
# The three legs are summed into one score with fixed weights, which silently
# assumes they are commensurable. They are not, unless their horizons are
# ordered on purpose. Liquidity is the fastest state in the book -- depth
# changes within a session -- so it sets the FLOOR, and the two legs that
# measure persistence rather than state are the only ones allowed to go deeper.
#
# This is a structural constraint, not a fitted one: it removes degrees of
# freedom from a 43-knob space that only has ~180 trades of evidence behind it.
# The direction it encodes is the one measurement here that replicated out of
# sample (liq_window 10 -> 5, P(improvement>0) = 0.967).
#
# `entropy_scale_window` is NOT floored: it sizes the sigma buckets rather than
# measuring anything, and a bucket scale shorter than the histogram it buckets
# is a different (legitimate) choice.
WINDOW_FLOOR = "liq_window"
FLOORED_WINDOWS = ("entropy_window", "alma_window")


def resolve_windows(config: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply the liquidity floor. Returns `(cfg, provenance)`.

    Every path into `compute_elmo` comes through here, so a caller cannot set
    an entropy or ALMA window faster than the liquidity window by any route --
    including the gears UI and a backtest sweep. A violating value is clamped
    UP to the floor rather than raising: a sweep must not die on a corner of
    its own grid, but it must not silently evaluate an incoherent one either,
    which is what the returned provenance records.
    """
    cfg = {**DEFAULTS, **{k: v for k, v in (config or {}).items() if v is not None}}
    floor = int(cfg[WINDOW_FLOOR])
    clamped: dict[str, dict[str, int]] = {}
    for key in FLOORED_WINDOWS:
        asked = int(cfg[key])
        if asked < floor:
            cfg[key] = floor
            clamped[key] = {"asked": asked, "used": floor}
    return cfg, {"floor": floor, "floor_key": WINDOW_FLOOR, "clamped": clamped}


# Bucket edges in units of rolling sigma. Five buckets: strong down, mild
# down, flat, mild up, strong up. Symmetric, so a pure drift does not read as
# order just because it happens to be positive.
_Z_EDGES = (-1.5, -0.5, 0.5, 1.5)
_N_BINS = len(_Z_EDGES) + 1


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _ema(values: Sequence[float | None], period: int) -> Series:
    """EMA that tolerates a `None` prefix (indicators seeded mid-series).

    `score_engine._ema` assumes a dense float list; entropy and liquidity are
    both undefined for their first N bars, so feeding them to that one would
    seed the average off `None` and poison the whole tail.
    """
    out: Series = [None] * len(values)
    dense = [(i, float(v)) for i, v in enumerate(values) if v is not None]
    if len(dense) < period or period < 1:
        return out
    seed = float(np.mean([v for _, v in dense[:period]]))
    out[dense[period - 1][0]] = seed
    k = 2.0 / (period + 1)
    prev = seed
    for i, v in dense[period:]:
        prev = v * k + prev * (1.0 - k)
        out[i] = float(prev)
    return out


def _log_returns(closes: Sequence[float]) -> list[float]:
    out = [0.0]
    for i in range(1, len(closes)):
        prev, cur = closes[i - 1], closes[i]
        out.append(math.log(cur / prev) if (prev > 0 and cur > 0) else 0.0)
    return out


def _percentile_rank(window: Sequence[float], value: float) -> float:
    """Fraction of `window` at or below `value`, as 0-100.

    Ties count as half, so a constant window ranks 50 rather than 100 -- it
    carries no information about where in the distribution we are.
    """
    if not window:
        return 50.0
    below = sum(1 for v in window if v < value)
    equal = sum(1 for v in window if v == value)
    return 100.0 * (below + 0.5 * equal) / len(window)


# A trailing rank over three or four points is not a percentile, it is a
# coin flip dressed as one. Below this many observations the rank is withheld
# (`None`) rather than published at a confidence it does not have.
_MIN_RANK_HISTORY = 20


def rank_series(values: Sequence[float | None], window: int) -> Series:
    """Trailing percentile rank (0-100) of each value within its own history.

    The workhorse of this module. Both entropy and the Hui-Heubel ratio are in
    units that mean nothing across symbols or timeframes -- a raw entropy of
    83 is an ordinary random walk on a 15m tape and something else entirely on
    a daily one (measured: a pure Gaussian random walk reads a median of 83.5,
    a strong clean trend 66.3). Ranking each against its own recent history is
    what makes a single threshold portable.
    """
    n = len(values)
    out: Series = [None] * n
    for i in range(n):
        value = values[i]
        if value is None:
            continue
        lo = max(0, i - window + 1)
        hist = [v for v in values[lo : i + 1] if v is not None]
        if len(hist) < _MIN_RANK_HISTORY:
            continue
        out[i] = float(_percentile_rank(hist, float(value)))
    return out


# ---------------------------------------------------------------------------
# Entropy
# ---------------------------------------------------------------------------


def _shannon(counts: Sequence[int]) -> float:
    total = sum(counts)
    if total <= 0:
        return 0.0
    h = 0.0
    for c in counts:
        if c <= 0:
            continue
        p = c / total
        h -= p * math.log2(p)
    return h


def return_entropy(
    closes: Sequence[float],
    *,
    window: int = DEFAULTS["entropy_window"],
    scale_window: int = DEFAULTS["entropy_scale_window"],
) -> Series:
    """Normalised (0-100) Shannon entropy of the bucketed return histogram.

    Returns are bucketed by how many rolling sigma they are, not by raw size,
    so the reading is comparable across symbols and across a volatility regime
    change -- a 0.4% bar is "flat" on NVDA and "strong" on TLT.

    The normaliser is `log2(min(bins, window))`, not `log2(bins)`. With fewer
    samples than buckets the histogram *cannot* be uniform, so dividing by the
    full `log2(bins)` would report a structurally low entropy for a short
    window and read as a trend that is not there.
    """
    n = len(closes)
    out: Series = [None] * n
    if n == 0 or window < 2:
        return out
    rets = _log_returns(closes)
    denom = math.log2(min(_N_BINS, window))
    if denom <= 0:
        return out
    for i in range(n):
        if i + 1 < window:
            continue
        lo = max(1, i - scale_window + 1)
        scale_slice = rets[lo : i + 1]
        sigma = float(np.std(scale_slice)) if len(scale_slice) > 1 else 0.0
        win = rets[i - window + 1 : i + 1]
        if sigma <= 0:
            # A dead-flat window is perfectly ordered, not perfectly random.
            out[i] = 0.0
            continue
        counts = [0] * _N_BINS
        for r in win:
            z = r / sigma
            idx = _N_BINS - 1
            for b, edge in enumerate(_Z_EDGES):
                if z < edge:
                    idx = b
                    break
            counts[idx] += 1
        out[i] = float(min(100.0, 100.0 * _shannon(counts) / denom))
    return out


def permutation_entropy(
    closes: Sequence[float],
    *,
    window: int = DEFAULTS["entropy_window"],
    dim: int = 3,
) -> Series:
    """Bandt-Pompe permutation entropy, 0-100.

    Scale-free by construction (it reads only the *ordering* of consecutive
    closes), so unlike `return_entropy` it needs no sigma estimate and behaves
    at very short windows. Offered as an alternative because the published
    ELMo default lookback is 10 bars, which is thin for a histogram.
    """
    n = len(closes)
    out: Series = [None] * n
    if n == 0 or dim < 2 or window < dim + 1:
        return out
    max_h = math.log2(math.factorial(dim))
    for i in range(n):
        if i + 1 < window:
            continue
        win = closes[i - window + 1 : i + 1]
        counts: dict[tuple[int, ...], int] = {}
        for j in range(len(win) - dim + 1):
            chunk = win[j : j + dim]
            pattern = tuple(int(k) for k in np.argsort(chunk, kind="stable"))
            counts[pattern] = counts.get(pattern, 0) + 1
        vectors = len(win) - dim + 1
        denom = min(max_h, math.log2(vectors)) if vectors > 1 else 0.0
        out[i] = 0.0 if denom <= 0 else float(
            min(100.0, 100.0 * _shannon(list(counts.values())) / denom)
        )
    return out


# ---------------------------------------------------------------------------
# Liquidity (Hui-Heubel)
# ---------------------------------------------------------------------------


def hui_heubel(
    records: Sequence[CandleRecord],
    *,
    window: int = DEFAULTS["liq_window"],
) -> Series:
    """Hui-Heubel illiquidity ratio per bar (higher = more illiquid).

    Textbook form is `((Pmax-Pmin)/Pmin) / (V / (S * Pbar))` where `S` is
    shares outstanding. We do not have a share count on this feed, and we do
    not need one: `S` is constant for a symbol over any window we chart, so it
    only rescales the series -- and the series is consumed as a *percentile
    rank* (`liquidity_score`), which is invariant to a constant factor. What
    remains is range-per-share-traded, i.e. an Amihud-style impact measure
    expressed in the Hui-Heubel arrangement.

    `None` for any bar whose window has no usable volume. That is a refusal to
    measure, not a zero.
    """
    n = len(records)
    out: Series = [None] * n
    if n == 0 or window < 1:
        return out
    for i in range(n):
        if i + 1 < window:
            continue
        win = records[i - window + 1 : i + 1]
        vols = [r.volume for r in win]
        if any(v is None for v in vols):
            continue
        share_vol = float(sum(float(v) for v in vols))
        if share_vol <= 0:
            continue
        lo = min(float(r.low) for r in win)
        hi = max(float(r.high) for r in win)
        if lo <= 0:
            continue
        range_pct = (hi - lo) / lo
        # Scale volume by the bar count so the ratio is per-bar: changing
        # `window` then shifts the level, not the shape.
        out[i] = float(range_pct / (share_vol / len(win)))
    return out


def liquidity_score(
    lhh: Sequence[float | None],
    *,
    norm_window: int = DEFAULTS["liq_norm_window"],
) -> Series:
    """0-100 where **high means liquid** (the TradingView author's convention).

    Percentile-rank the illiquidity ratio over a trailing window and invert.
    Ranking rather than thresholding is what makes this portable: the raw
    Hui-Heubel level is in units of "percent per share" and differs by orders
    of magnitude between SPY and a $4 biotech, but "this bar sits in the 20th
    percentile of its own last 200" means the same thing everywhere.
    """
    n = len(lhh)
    out: Series = [None] * n
    for i in range(n):
        value = lhh[i]
        if value is None:
            continue
        lo = max(0, i - norm_window + 1)
        hist = [v for v in lhh[lo : i + 1] if v is not None]
        if len(hist) < _MIN_RANK_HISTORY:
            continue
        out[i] = float(100.0 - _percentile_rank(hist, float(value)))
    return out


# ---------------------------------------------------------------------------
# Momentum (ALMA)
# ---------------------------------------------------------------------------


def alma(
    values: Sequence[float],
    *,
    window: int = DEFAULTS["alma_window"],
    offset: float = DEFAULTS["alma_offset"],
    sigma: float = DEFAULTS["alma_sigma"],
) -> Series:
    """Arnaud Legoux Moving Average.

    A Gaussian kernel whose peak sits `offset` of the way toward the newest
    bar (0.85 by default), so it lags far less than an SMA of the same length
    while smoothing more than an EMA of equivalent responsiveness. The
    published ELMo notes call out "close ALMAs for additional trend
    filtering", which is what this feeds.
    """
    n = len(values)
    out: Series = [None] * n
    if n == 0 or window < 1 or sigma <= 0:
        return out
    m = offset * (window - 1)
    s = window / sigma
    weights = np.array(
        [math.exp(-((i - m) ** 2) / (2.0 * s * s)) for i in range(window)],
        dtype=float,
    )
    total = float(weights.sum())
    if total <= 0:
        return out
    weights /= total
    arr = np.asarray(values, dtype=float)
    for i in range(window - 1, n):
        out[i] = float(np.dot(arr[i - window + 1 : i + 1], weights))
    return out


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


@dataclass
class ElmoResult:
    """Everything ELMo publishes, aligned 1:1 with the input records."""

    entropy: Series = field(default_factory=list)
    entropy_rank: Series = field(default_factory=list)
    entropy_fast: Series = field(default_factory=list)
    entropy_slow: Series = field(default_factory=list)
    lhh: Series = field(default_factory=list)
    liquidity: Series = field(default_factory=list)
    alma: Series = field(default_factory=list)
    alma_slope: Series = field(default_factory=list)
    # Derived booleans, per bar.
    ordered: list[bool] = field(default_factory=list)   # entropy low & falling
    liquid: list[bool] = field(default_factory=list)    # liquidity above gate
    has_volume: bool = True
    # Which floor was applied and what it clamped -- see `resolve_windows`.
    # Published so a tuning run cannot mistake a clamped grid point for the
    # one it asked for.
    windows: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "entropy": self.entropy,
            "entropy_rank": self.entropy_rank,
            "entropy_fast": self.entropy_fast,
            "entropy_slow": self.entropy_slow,
            "lhh": self.lhh,
            "liquidity": self.liquidity,
            "alma": self.alma,
            "alma_slope": self.alma_slope,
            "ordered": self.ordered,
            "liquid": self.liquid,
            "has_volume": self.has_volume,
        }


# An "ordered" (trend-worthy) tape is one whose entropy is BOTH falling and in
# the bottom `ORDERED_MAX_RANK` percent of its own recent history.
#
# This is a rank, not an absolute level, because the absolute level is not a
# portable statement. Measured on synthetic series at 800 bars: a pure Gaussian
# random walk reads a median raw entropy of 83.5 and a strong clean trend 66.3
# -- so an absolute cut anywhere between them is a different filter on every
# symbol, timeframe and volatility regime, and one placed at 62 (as it first
# was here) called only 8% of bars ordered even in a clean trend. The rank asks
# the only question that travels: is the tape more ordered than it usually is?
ORDERED_MAX_RANK = 35.0
LIQUID_MIN_SCORE = 50.0


def compute_elmo(records: Sequence[CandleRecord], **overrides: Any) -> ElmoResult:
    """Run all three legs over `records`.

    Never raises: any leg that cannot be computed comes back all-`None` and
    its boolean leg all-`False`, so a caller can always zip it against bars.
    """
    cfg, windows = resolve_windows(overrides)
    n = len(records)
    if n == 0:
        return ElmoResult(has_volume=True, windows=windows)
    empty: Series = [None] * n

    closes = [float(r.close) for r in records]

    if cfg["entropy_method"] == "permutation":
        ent = permutation_entropy(closes, window=int(cfg["entropy_window"]))
    else:
        ent = return_entropy(
            closes,
            window=int(cfg["entropy_window"]),
            scale_window=int(cfg["entropy_scale_window"]),
        )
    ent_rank = rank_series(ent, int(cfg["entropy_rank_window"]))
    ent_fast = _ema(ent, int(cfg["entropy_fast"]))
    ent_slow = _ema(ent, int(cfg["entropy_slow"]))

    has_volume = any(r.volume is not None for r in records)
    if has_volume:
        raw_lhh = hui_heubel(records, window=int(cfg["liq_window"]))
        liq = liquidity_score(raw_lhh, norm_window=int(cfg["liq_norm_window"]))
    else:
        raw_lhh, liq = list(empty), list(empty)

    al = alma(
        closes,
        window=int(cfg["alma_window"]),
        offset=float(cfg["alma_offset"]),
        sigma=float(cfg["alma_sigma"]),
    )
    slope: Series = [None] * n
    for i in range(1, n):
        prev, cur = al[i - 1], al[i]
        if prev is None or cur is None or prev == 0:
            continue
        # Percent change, so the slope is comparable across price levels.
        slope[i] = float(100.0 * (cur - prev) / abs(prev))

    ordered: list[bool] = []
    liquid: list[bool] = []
    for i in range(n):
        fast_v, slow_v, rank_v = ent_fast[i], ent_slow[i], ent_rank[i]
        # Both conditions matter and they are not the same thing: the rank says
        # the tape is unusually ordered, the EMA cross says it is *becoming*
        # more ordered. The published ELMo takes its entries from "cross events
        # in the displayed entropy moving averages"; the rank is what stops a
        # cross inside permanent chop from counting.
        ordered.append(
            bool(
                fast_v is not None
                and slow_v is not None
                and rank_v is not None
                and fast_v < slow_v
                and rank_v <= ORDERED_MAX_RANK
            )
        )
        lv = liq[i]
        liquid.append(bool(lv is not None and lv >= LIQUID_MIN_SCORE))

    return ElmoResult(
        entropy=ent,
        entropy_rank=ent_rank,
        entropy_fast=ent_fast,
        entropy_slow=ent_slow,
        lhh=raw_lhh,
        liquidity=liq,
        alma=al,
        alma_slope=slope,
        ordered=ordered,
        liquid=liquid,
        has_volume=has_volume,
        windows=windows,
    )
