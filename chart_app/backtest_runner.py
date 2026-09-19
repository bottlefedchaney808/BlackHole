"""CLI that pulls history once, caches it, and runs the whole backtest suite.

    .venv\\Scripts\\python.exe -m chart_app.backtest_runner --stage all

Stages
------
``pull``   fetch history into `artifacts/chart_app_backtest.db` (billed calls,
           rate-limited, cached -- re-running is free)
``base``   one run per symbol/interval at the shipped defaults
``sweep``  parameter sweep on the pooled universe, ranked
``wf``     walk-forward: fit in-sample, score out-of-sample
``perm``   permutation test against return-shuffled surrogates
``rank``   rank transfer: does a tune done on some symbols mean anything
           on the others (see `stage_rank_transfer`)
``all``    every stage in order

The cache is a **separate** database from the live chart's
(`artifacts/chart_app_bars.db`) on purpose: a backtest sweep pulls symbols and
lookbacks the chart never asked for, and the running app reloads its cache on
a timer. Sharing one file would have a sweep silently repoint the window Jason
is looking at.

Provider discipline (`rate-limit-options` skill): one symbol at a time, a
sleep between pulls, and a retry on the transient timeouts a large aggregated
pull produces under load. Never parallel -- this repo's rule.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from chart_app.backtest import (
    DEFAULT_COST_BPS,
    _objective,
    expand_grid,
    percentile_of,
    permutation_test,
    run_backtest,
    spearman_rho,
    walk_forward,
)
from chart_app.bar_cache import BarCache
from chart_app.signal_engine import DEFAULTS
from chart_app.split_guard import back_adjust
from shared.chart_data import CandleRecord
from shared.spot_history import fetch_daily_candles, fetch_intraday_candles

CACHE_PATH = Path("artifacts/chart_app_backtest.db")
REPORT_PATH = Path("artifacts/chart_app_backtest_report.json")

# (ticker, interval, lookback). Intraday lookback is capped at 30d by
# shared/spot_history.py -- it is aggregated from one-minute rows.
UNIVERSE: list[tuple[str, str, str]] = [
    ("SPY", "1d", "3y"),
    ("QQQ", "1d", "3y"),
    ("IWM", "1d", "3y"),
    ("NVDA", "1d", "3y"),
    ("AAPL", "1d", "3y"),
    ("MSFT", "1d", "3y"),
    ("TSLA", "1d", "3y"),
    ("AMD", "1d", "3y"),
    ("SPY", "15m", "30d"),
    ("QQQ", "15m", "30d"),
    ("NVDA", "15m", "30d"),
    ("SPY", "1h", "30d"),
]

_SLEEP_BETWEEN_PULLS_S = 0.5
# Retries stay, but NOT for the reason originally written here. "Two intraday
# pulls failed on the first run and succeeded on retry -- that is rate-limit
# noise" (HANDOFF §9) was, at least in part, httpx's 5.0s default timeout on
# our side, fixed 2026-09-19 in shared/thetadata.py::_apply_http_timeout.
# What remains genuinely retry-worthy: the vendor LARGE_REQUEST ceiling,
# real 502s under load, and network blips. Diagnose with the clock -- a
# failure at ~5.0s exactly means the timeout is not being applied.
_RETRY_DELAYS_S = (2.0, 6.0)

# How much of a requested lookback the cache must already hold to count as a
# hit. Not 1.0: a window starting on a weekend or a holiday can never be met
# exactly, and demanding it would re-pull the whole universe on every run.
_COVERAGE_FRACTION = 0.95
_COVERAGE_GRACE_DAYS = 5


def _lookback_days(lookback: str) -> int | None:
    """`"3y"` -> 1095, `"30d"` -> 30. None when the shape is unrecognised."""
    text = str(lookback).strip().lower()
    try:
        value = int(text[:-1])
    except (ValueError, IndexError):
        return None
    unit = text[-1:]
    if unit == "d":
        return value
    if unit == "w":
        return value * 7
    if unit == "m":
        return value * 30
    if unit == "y":
        return value * 365
    return None


def _covers(records: Sequence[CandleRecord], lookback: str) -> bool:
    """Does the cached span actually reach back as far as `lookback` asks?

    The old test was `len(records) >= 60`, which answers a different question.
    Raising a lookback from 3y to 5y left 752 cached daily bars sitting there,
    passed the count check, printed "cached", and scored three years while the
    caller believed it had asked for five. A cache hit has to be about SPAN.
    """
    want = _lookback_days(lookback)
    if want is None:
        return len(records) >= 60
    have_days = (records[-1].timestamp.date() - records[0].timestamp.date()).days
    floor = want * _COVERAGE_FRACTION - _COVERAGE_GRACE_DAYS
    return len(records) >= 60 and have_days >= floor


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


def pull(universe: Sequence[tuple[str, str, str]] = UNIVERSE, *, force: bool = False) -> dict[str, int]:
    """Fetch each (ticker, interval) into the backtest cache. Idempotent."""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    cache = BarCache(CACHE_PATH)
    counts: dict[str, int] = {}
    for ticker, interval, lookback in universe:
        key = f"{ticker}:{interval}"
        if not force:
            have = cache.load(ticker, interval)
            if _covers(have, lookback):
                counts[key] = len(have)
                span = (
                    f"{have[0].timestamp.date()} -> {have[-1].timestamp.date()}"
                    if have else "empty"
                )
                stale = (datetime.now(UTC).date() - have[-1].timestamp.date()).days if have else 0
                # Staleness is REPORTED, never auto-refreshed: every refresh is
                # a billed call, and a research sweep does not need today's bar.
                flag = f"  [stale {stale}d]" if stale > 3 else ""
                print(f"  {key:<14} cached ({len(have)} bars, {span}){flag}")
                continue
            if have:
                print(
                    f"  {key:<14} cache too short for {lookback} "
                    f"({len(have)} bars, {(have[-1].timestamp.date() - have[0].timestamp.date()).days}d) -- refetching"
                )
        payload = None
        for attempt in range(len(_RETRY_DELAYS_S) + 1):
            try:
                if interval == "1d":
                    payload = fetch_daily_candles(ticker, lookback=lookback)
                else:
                    payload = fetch_intraday_candles(
                        ticker, interval=interval, lookback=lookback
                    )
                break
            except Exception as exc:  # noqa: BLE001
                if attempt >= len(_RETRY_DELAYS_S):
                    print(f"  {key:<14} FAILED {type(exc).__name__}: {str(exc)[:70]}")
                    payload = None
                    break
                time.sleep(_RETRY_DELAYS_S[attempt])
        if payload is None:
            counts[key] = 0
            continue
        records = list(payload.observations)
        cache.upsert(ticker, interval, records)
        counts[key] = len(records)
        print(f"  {key:<14} pulled {len(records)} bars")
        time.sleep(_SLEEP_BETWEEN_PULLS_S)
    return counts


def load(ticker: str, interval: str) -> list[CandleRecord]:
    return BarCache(CACHE_PATH).load(ticker, interval)


def _loaded(
    universe: Sequence[tuple[str, str, str]], *, adjust: bool = True
) -> list[tuple[str, str, list[CandleRecord]]]:
    """Load each series, split-adjusted, announcing every adjustment.

    Adjusting is not optional for a multi-year daily backtest on this feed --
    `shared/spot_history` serves unadjusted prices, so NVDA's 2024 10-for-1
    shows up as a real -89.9% session (see `chart_app/split_guard.py`). It is
    announced rather than applied quietly, because a symbol needing an
    adjustment here means every other consumer of the same lookback has the
    same raw bar.
    """
    out = []
    for ticker, interval, _lb in universe:
        records = load(ticker, interval)
        if len(records) < 120:
            continue
        if adjust:
            records, events = back_adjust(records)
            for event in events:
                print(f"  [split-adjusted] {ticker} {interval} {event.describe()}")
        out.append((ticker, interval, records))
    return out


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------


def stage_base(
    universe: Sequence[tuple[str, str, str]] = UNIVERSE,
    *,
    config: dict[str, Any] | None = None,
    cost_bps: float = DEFAULT_COST_BPS,
) -> dict[str, Any]:
    print("\n=== BASELINE (shipped defaults) ===")
    rows: list[dict[str, Any]] = []
    for ticker, interval, records in _loaded(universe):
        res = run_backtest(
            records, interval=interval, config=config, cost_bps=cost_bps
        )
        print(f"  {ticker:<5} {interval:<4} n={len(records):<5} {res.summary()}")
        rows.append({"ticker": ticker, "interval": interval, **res.metrics})
    return {"rows": rows, "aggregate": _aggregate(rows)}


def _aggregate(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    ret = [float(r.get("total_return_pct", 0.0)) for r in rows]
    bh = [float(r.get("buy_hold_pct", 0.0)) for r in rows]
    sh = [float(r.get("sharpe", 0.0)) for r in rows]
    return {
        "series": len(rows),
        "total_trades": int(sum(int(r.get("trades", 0)) for r in rows)),
        "mean_return_pct": float(np.mean(ret)),
        "median_return_pct": float(np.median(ret)),
        "mean_buy_hold_pct": float(np.mean(bh)),
        "beat_buy_hold": int(sum(1 for a, b in zip(ret, bh, strict=True) if a > b)),
        "profitable_series": int(sum(1 for v in ret if v > 0)),
        "mean_sharpe": float(np.mean(sh)),
        "mean_max_dd_pct": float(
            np.mean([float(r.get("max_drawdown_pct", 0.0)) for r in rows])
        ),
    }


# The sweep grid. Kept small on purpose: every extra axis multiplies the run
# and, far worse, multiplies the chance that the winning cell is noise. These
# four are the ones that change behaviour rather than shade it.
SWEEP_GRID: dict[str, Sequence[Any]] = {
    "entry_long": [15.0, 20.0, 25.0, 30.0, 35.0],
    "exit_long": [-20.0, -12.0, -6.0, 0.0],
    "atr_stop_mult": [1.5, 2.5, 3.5],
    "cooldown_bars": [3],
}


def stage_sweep(
    universe: Sequence[tuple[str, str, str]] = UNIVERSE,
    *,
    grid: dict[str, Sequence[Any]] | None = None,
    cost_bps: float = DEFAULT_COST_BPS,
    top: int = 10,
) -> dict[str, Any]:
    """Pooled sweep: score each parameter set across EVERY series at once.

    Sweeping one symbol at a time and reading off its winner is how you get
    twelve different "optimal" parameter sets and no way to choose. Pooling
    asks the question that matters for a shipped default: which single set was
    the least bad everywhere?
    """
    grid = grid or SWEEP_GRID
    series = _loaded(universe)
    combos = expand_grid(grid)
    print(f"\n=== SWEEP ({len(combos)} sets x {len(series)} series) ===")
    scored: list[dict[str, Any]] = []
    for params in combos:
        per_series = []
        for ticker, interval, records in series:
            res = run_backtest(
                records, interval=interval, config=params, cost_bps=cost_bps
            )
            per_series.append(res.metrics)
        rets = [float(m["total_return_pct"]) for m in per_series]
        bhs = [float(m["buy_hold_pct"]) for m in per_series]
        trades = sum(int(m["trades"]) for m in per_series)
        scored.append(
            {
                "params": params,
                "trades": trades,
                "mean_return_pct": float(np.mean(rets)),
                "median_return_pct": float(np.median(rets)),
                "profitable_series": int(sum(1 for v in rets if v > 0)),
                "beat_buy_hold": int(
                    sum(1 for a, b in zip(rets, bhs, strict=True) if a > b)
                ),
                "mean_sharpe": float(np.mean([float(m["sharpe"]) for m in per_series])),
                "mean_max_dd_pct": float(
                    np.mean([float(m["max_drawdown_pct"]) for m in per_series])
                ),
            }
        )
    # Rank on median return across series, not mean: one runaway symbol should
    # not be able to carry a parameter set that lost on the other eleven.
    scored.sort(key=lambda r: (r["median_return_pct"], r["mean_sharpe"]), reverse=True)
    print(f"  {'entry':>6} {'exit':>6} {'atr':>5} | {'trades':>6} {'medRet':>7} "
          f"{'meanRet':>8} {'prof':>5} {'>bh':>4} {'sharpe':>7} {'mdd':>7}")
    for row in scored[:top]:
        p = row["params"]
        print(
            f"  {p['entry_long']:>6.1f} {p['exit_long']:>6.1f} {p['atr_stop_mult']:>5.1f} | "
            f"{row['trades']:>6} {row['median_return_pct']:>7.2f} "
            f"{row['mean_return_pct']:>8.2f} {row['profitable_series']:>5} "
            f"{row['beat_buy_hold']:>4} {row['mean_sharpe']:>7.2f} "
            f"{row['mean_max_dd_pct']:>7.2f}"
        )
    return {"ranked": scored[: max(top, 25)], "series": len(series), "combos": len(combos)}


def stage_walk_forward(
    universe: Sequence[tuple[str, str, str]] = UNIVERSE,
    *,
    grid: dict[str, Sequence[Any]] | None = None,
    folds: int = 4,
    cost_bps: float = DEFAULT_COST_BPS,
) -> dict[str, Any]:
    grid = grid or SWEEP_GRID
    print(f"\n=== WALK-FORWARD ({folds} folds) ===")
    out: list[dict[str, Any]] = []
    for ticker, interval, records in _loaded(universe):
        wf = walk_forward(
            records, grid, interval=interval, folds=folds, cost_bps=cost_bps
        )
        if "error" in wf:
            print(f"  {ticker:<5} {interval:<4} skipped: {wf['error']}")
            continue
        print(
            f"  {ticker:<5} {interval:<4} oos_total={wf['oos_total_return_pct']:>7.2f}% "
            f"mean={wf['oos_mean_return_pct']:>6.2f}% "
            f"positive={wf['oos_positive_folds']}/{wf['oos_folds_scored']} "
            f"trades={wf['oos_trades']}"
        )
        out.append({"ticker": ticker, "interval": interval, **wf})
    totals = [float(r["oos_total_return_pct"]) for r in out]
    pos = sum(int(r["oos_positive_folds"]) for r in out)
    scored = sum(int(r["oos_folds_scored"]) for r in out)
    summary = {
        "series": len(out),
        "mean_oos_total_pct": float(np.mean(totals)) if totals else 0.0,
        "median_oos_total_pct": float(np.median(totals)) if totals else 0.0,
        "positive_series": int(sum(1 for v in totals if v > 0)),
        "positive_folds": pos,
        "folds_scored": scored,
        "fold_hit_rate_pct": float(100.0 * pos / scored) if scored else 0.0,
    }
    print(f"  --> {summary['positive_series']}/{summary['series']} series positive OOS, "
          f"fold hit rate {summary['fold_hit_rate_pct']:.1f}%")
    return {"per_series": out, "summary": summary}


def stage_permutation(
    universe: Sequence[tuple[str, str, str]] = UNIVERSE,
    *,
    config: dict[str, Any] | None = None,
    trials: int = 40,
    cost_bps: float = DEFAULT_COST_BPS,
) -> dict[str, Any]:
    print(f"\n=== PERMUTATION TEST ({trials} surrogates per series) ===")
    rows: list[dict[str, Any]] = []
    for ticker, interval, records in _loaded(universe):
        res = permutation_test(
            records,
            interval=interval,
            config=config,
            trials=trials,
            cost_bps=cost_bps,
        )
        print(
            f"  {ticker:<5} {interval:<4} real={res['observed_return_pct']:>7.2f}% "
            f"null_mean={res['null_mean_pct']:>7.2f}% "
            f"null_p95={res['null_p95_pct']:>7.2f}% p={res['p_value']:.3f}"
        )
        rows.append(
            {
                "ticker": ticker,
                "interval": interval,
                **{k: v for k, v in res.items() if k != "real_metrics"},
            }
        )
    ps = [float(r["p_value"]) for r in rows]
    summary = {
        "series": len(rows),
        "median_p": float(np.median(ps)) if ps else 1.0,
        "significant_at_10pct": int(sum(1 for p in ps if p <= 0.10)),
        "significant_at_5pct": int(sum(1 for p in ps if p <= 0.05)),
    }
    print(
        f"  --> median p={summary['median_p']:.3f}, "
        f"{summary['significant_at_10pct']}/{summary['series']} series beat the null at 10%"
    )
    return {"per_series": rows, "summary": summary}


# The rank-transfer universe is DAILY ONLY, and one series per symbol. Two
# reasons, both about leakage rather than taste: SPY 1d and SPY 15m are the
# same asset, so a cut that puts one in each half is not out-of-sample in any
# useful sense; and the 30d intraday series are a single month of one regime,
# where "transfer" would be measuring whether September looks like September.
RANK_TRANSFER_UNIVERSE: list[tuple[str, str, str]] = [
    row for row in UNIVERSE if row[1] == "1d"
]


def _score_matrix(
    series: Sequence[tuple[str, str, list[CandleRecord]]],
    combos: Sequence[dict[str, Any]],
    *,
    cost_bps: float,
    min_trades: int,
) -> list[dict[str, Any]]:
    """Every combo scored on every series, once: combos x series backtests.

    Scored once and reused for every cut. The alternative -- re-running the
    grid inside each of the 35 cuts -- would be 35x the work for bit-identical
    numbers, and would invite the two paths to drift apart, which is the
    failure this file keeps rediscovering (HANDOFF 3.15, 3.17).
    """
    rows: list[dict[str, Any]] = []
    for params in combos:
        obj: dict[str, float] = {}
        ret: dict[str, float] = {}
        trades: dict[str, int] = {}
        for ticker, interval, records in series:
            res = run_backtest(
                records, interval=interval, config=params, cost_bps=cost_bps
            )
            obj[ticker] = _objective(res.metrics, min_trades=min_trades)
            ret[ticker] = float(res.metrics.get("total_return_pct", 0.0))
            trades[ticker] = int(res.metrics.get("trades", 0))
        rows.append(
            {"params": params, "objective": obj, "return_pct": ret, "trades": trades}
        )
    return rows


def _sign_test_p(successes: int, trials: int) -> float | None:
    """One-sided binomial tail P(X >= successes) at p = 0.5, or None if n = 0."""
    if trials <= 0:
        return None
    tail = sum(math.comb(trials, k) for k in range(successes, trials + 1))
    return float(tail / (2**trials))


def _half_splits(names: Sequence[str]) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    """Every way to cut the symbols into two disjoint halves, each counted once.

    For an even count, {A,B} and {B,A} are the same cut, so anchoring the first
    symbol into the left half dedupes them. Without that, every cut appears
    twice and the spread of the result reads half as noisy as it is.
    """
    n = len(names)
    if n < 4:
        return []
    out = []
    for combo in itertools.combinations(names, n // 2):
        if n % 2 == 0 and names[0] not in combo:
            continue
        out.append((tuple(combo), tuple(t for t in names if t not in combo)))
    return out


def stage_rank_transfer(
    universe: Sequence[tuple[str, str, str]] = (),
    *,
    grid: dict[str, Sequence[Any]] | None = None,
    cost_bps: float = DEFAULT_COST_BPS,
    min_trades: int = 8,
) -> dict[str, Any]:
    """Does a tune done on one set of symbols mean anything on the others?

    The sweep stage ranks parameter sets on a pooled universe and prints the
    winner. This stage asks whether that ranking is information at all. It
    cuts the symbols into two disjoint halves, ranks the whole grid inside
    each half independently, and reports two things per cut:

      rho        -- Spearman correlation between the two halves' rankings of
                    the same combos. The whole-surface view.
      percentile -- where the combo chosen on half A lands in half B's own
                    ranking. This is the number that matters, because it is
                    what choosing on A and deploying on B actually buys. A
                    coin-flip pick scores 50; B's own winner scores 100.

    And, kept separate, the level: the same combo's in-group return against
    its out-of-group return. Ranking and level are different claims and they
    fail independently -- see the note in `chart_app.backtest`.
    """
    universe = list(universe) or RANK_TRANSFER_UNIVERSE
    grid = grid or SWEEP_GRID
    series = _loaded(universe)
    names = [t for t, _i, _r in series]
    if len(names) != len(set(names)):
        raise ValueError(f"rank transfer needs one series per symbol, got {names}")
    combos = expand_grid(grid)
    splits = _half_splits(names)
    if not splits:
        return {"error": f"need >=4 symbols to cut, have {len(names)}"}

    print(
        f"\n=== RANK TRANSFER ({len(combos)} combos x {len(names)} symbols, "
        f"{len(splits)} disjoint cuts) ==="
    )
    matrix = _score_matrix(series, combos, cost_bps=cost_bps, min_trades=min_trades)

    def group_obj(row: dict[str, Any], group: Sequence[str]) -> float:
        return float(np.median([row["objective"][t] for t in group]))

    def group_ret(row: dict[str, Any], group: Sequence[str]) -> float:
        return float(np.median([row["return_pct"][t] for t in group]))

    rhos: list[float] = []
    directions: list[dict[str, Any]] = []
    for left, right in splits:
        left_obj = [group_obj(row, left) for row in matrix]
        right_obj = [group_obj(row, right) for row in matrix]
        rho = spearman_rho(left_obj, right_obj)
        if rho is not None:
            rhos.append(rho)
        for fit, test, fit_obj, test_obj in (
            (left, right, left_obj, right_obj),
            (right, left, right_obj, left_obj),
        ):
            chosen = int(np.argmax(fit_obj))
            row = matrix[chosen]
            directions.append(
                {
                    "fit_on": list(fit),
                    "tested_on": list(test),
                    "params": row["params"],
                    "rho": rho,
                    "percentile_in_test": percentile_of(test_obj[chosen], test_obj),
                    "in_group_return_pct": group_ret(row, fit),
                    "out_group_return_pct": group_ret(row, test),
                    # The ceiling a perfect chooser would have hit on the test
                    # half, and what a blind pick would have made. The chosen
                    # combo lives somewhere between them -- where, is the
                    # entire question.
                    "test_best_return_pct": group_ret(
                        matrix[int(np.argmax(test_obj))], test
                    ),
                    "test_median_combo_return_pct": float(
                        np.median([group_ret(r, test) for r in matrix])
                    ),
                }
            )

    # Leave-one-symbol-out. The 70 half-cut directions above are NOT 70
    # independent observations -- they are 35 overlapping cuts of the same 8
    # symbols, so SPY sits on the test side of 17 of them and its behaviour is
    # counted again and again. Quoting a p-value over them would be quoting
    # the overlap. Here each symbol is the held-out test exactly once, so the
    # test sides are disjoint and the 8 numbers can be read at face value --
    # and this is also the procedure an actual default-picker would follow:
    # fit on everything you have, deploy on the name you do not.
    loo: list[dict[str, Any]] = []
    for held in names:
        rest = [t for t in names if t != held]
        fit_obj = [group_obj(row, rest) for row in matrix]
        held_obj = [row["objective"][held] for row in matrix]
        held_ret = [row["return_pct"][held] for row in matrix]
        chosen = int(np.argmax(fit_obj))
        loo.append(
            {
                "held_out": held,
                "params": matrix[chosen]["params"],
                "percentile_in_held_out": percentile_of(held_obj[chosen], held_obj),
                "held_out_return_pct": float(held_ret[chosen]),
                "held_out_best_return_pct": float(held_ret[int(np.argmax(held_obj))]),
                "held_out_median_combo_return_pct": float(np.median(held_ret)),
                "trades": int(matrix[chosen]["trades"][held]),
            }
        )

    pcts = [
        d["percentile_in_test"] for d in directions if d["percentile_in_test"] is not None
    ]
    gaps = [d["in_group_return_pct"] - d["out_group_return_pct"] for d in directions]
    lifts = [
        d["out_group_return_pct"] - d["test_median_combo_return_pct"] for d in directions
    ]
    summary = {
        "combos": len(combos),
        "symbols": len(names),
        "cuts": len(splits),
        "rho_median": float(np.median(rhos)) if rhos else None,
        "rho_p10": float(np.percentile(rhos, 10)) if rhos else None,
        "rho_p90": float(np.percentile(rhos, 90)) if rhos else None,
        "rho_negative_cuts": int(sum(1 for r in rhos if r < 0)),
        "percentile_median": float(np.median(pcts)) if pcts else None,
        "percentile_p10": float(np.percentile(pcts, 10)) if pcts else None,
        "percentile_worse_than_coinflip": int(sum(1 for p in pcts if p < 50.0)),
        "directions": len(directions),
        "level_gap_median_pp": float(np.median(gaps)),
        "lift_over_blind_pick_median_pp": float(np.median(lifts)),
        "loo_percentile_median": float(
            np.median([d["percentile_in_held_out"] for d in loo])
        ),
        "loo_above_coinflip": int(
            sum(1 for d in loo if d["percentile_in_held_out"] > 50.0)
        ),
        "loo_lift_over_blind_pick_median_pp": float(
            np.median(
                [
                    d["held_out_return_pct"] - d["held_out_median_combo_return_pct"]
                    for d in loo
                ]
            )
        ),
        # One-sided sign test on the leave-one-out results: under "tuning
        # tells you nothing", each held-out percentile is equally likely to
        # land above or below 50. Reported because it is the only honest
        # significance statement available here, and it is reported WITH its
        # ceiling: n is the symbol count, so with 8 symbols even a clean
        # sweep of 8/8 only reaches p = 0.004, and 6/8 cannot clear 5%. A
        # result that looks strong here is a reason to widen the universe,
        # not a reason to stop.
        "loo_sign_test_p": _sign_test_p(
            sum(1 for d in loo if d["percentile_in_held_out"] > 50.0), len(loo)
        ),
    }

    if rhos:
        print(
            f"  ranking   rho median {summary['rho_median']:+.3f} "
            f"(p10 {summary['rho_p10']:+.3f}, p90 {summary['rho_p90']:+.3f}), "
            f"{summary['rho_negative_cuts']}/{len(rhos)} cuts negative"
        )
    else:
        # `spearman_rho` returns None on a grid too small to rank (<3 combos)
        # or a degenerate one where every combo scored the same. Saying so is
        # the point -- printing 0.000 here would read as "no transfer found"
        # when nothing was measured at all.
        print("  ranking   rho undefined -- need >=3 combos with distinct scores")
    print(
        f"  selection percentile median {summary['percentile_median']:.1f} "
        f"(p10 {summary['percentile_p10']:.1f}) -- coin-flip is 50.0, "
        f"{summary['percentile_worse_than_coinflip']}/{len(pcts)} directions below it"
    )
    print(
        f"  level     in-group minus out-group return {summary['level_gap_median_pp']:+.2f}pp "
        f"median -- the part that does NOT transfer"
    )
    print(
        f"  payoff    chosen combo vs a blind pick, out of group: "
        f"{summary['lift_over_blind_pick_median_pp']:+.2f}pp median"
    )
    print(
        f"\n  leave-one-symbol-out (the {len(loo)} disjoint tests -- read these, "
        f"not the {len(directions)} overlapping ones)"
    )
    print(
        f"  {'held out':>8} {'pctile':>7} {'ret%':>8} {'best%':>8} {'blind%':>8} {'trades':>7}"
    )
    for row in loo:
        print(
            f"  {row['held_out']:>8} {row['percentile_in_held_out']:>7.1f} "
            f"{row['held_out_return_pct']:>8.2f} {row['held_out_best_return_pct']:>8.2f} "
            f"{row['held_out_median_combo_return_pct']:>8.2f} {row['trades']:>7}"
        )
    print(
        f"  --> median percentile {summary['loo_percentile_median']:.1f}, "
        f"{summary['loo_above_coinflip']}/{len(loo)} above coin-flip, "
        f"{summary['loo_lift_over_blind_pick_median_pp']:+.2f}pp median vs a blind pick"
    )
    return {"summary": summary, "directions": directions, "leave_one_out": loo}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="chart_app conviction-engine backtests")
    parser.add_argument(
        "--stage",
        default="all",
        choices=["pull", "base", "sweep", "wf", "perm", "rank", "all"],
    )
    parser.add_argument("--force-pull", action="store_true")
    parser.add_argument("--cost-bps", type=float, default=DEFAULT_COST_BPS)
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--trials", type=int, default=40)
    parser.add_argument("--tickers", default="", help="comma list to narrow the universe")
    parser.add_argument("--report", default=str(REPORT_PATH))
    args = parser.parse_args(argv)

    universe = UNIVERSE
    if args.tickers:
        wanted = {t.strip().upper() for t in args.tickers.split(",") if t.strip()}
        universe = [row for row in UNIVERSE if row[0] in wanted]

    report: dict[str, Any] = {
        "defaults": {k: v for k, v in DEFAULTS.items()},
        "cost_bps": args.cost_bps,
    }
    stage = args.stage
    if stage in ("pull", "all"):
        print("=== PULL ===")
        report["pull"] = pull(universe, force=args.force_pull)
    if stage in ("base", "all"):
        report["baseline"] = stage_base(universe, cost_bps=args.cost_bps)
    if stage in ("sweep", "all"):
        report["sweep"] = stage_sweep(universe, cost_bps=args.cost_bps)
    if stage in ("wf", "all"):
        report["walk_forward"] = stage_walk_forward(
            universe, folds=args.folds, cost_bps=args.cost_bps
        )
    if stage in ("rank", "all"):
        report["rank_transfer"] = stage_rank_transfer(cost_bps=args.cost_bps)
    if stage in ("perm", "all"):
        report["permutation"] = stage_permutation(
            universe, trials=args.trials, cost_bps=args.cost_bps
        )

    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nreport -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
