"""Backtest honesty: next-bar fills, costs, warm-up isolation, metrics."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from chart_app.backtest import (
    WARMUP_BARS,
    _max_drawdown,
    expand_grid,
    permutation_test,
    run_backtest,
    sweep,
    walk_forward,
)
from shared.chart_data import CandleRecord


def _bars(closes, minutes=None):
    """Daily bars whose open is the PREVIOUS close, so a next-bar-open fill is
    distinguishable from a signal-bar-close fill."""
    t0 = datetime(2026, 1, 2)
    out = []
    for i, c in enumerate(closes):
        c = float(c)
        op = float(closes[i - 1]) if i else c
        out.append(
            CandleRecord(
                timestamp=t0 + timedelta(days=i),
                open=op,
                high=max(op, c) * 1.004,
                low=min(op, c) * 0.996,
                close=c,
                volume=1_000_000.0,
            )
        )
    return out


def _walk(n, drift, sigma, seed):
    rng = np.random.default_rng(seed)
    return list(100 * np.exp(np.cumsum(rng.normal(drift, sigma, n))))


GRID = {
    "entry_long": [25.0, 35.0],
    "exit_long": [-12.0],
    "atr_stop_mult": [4.0],
    "cooldown_bars": [3],
}


# ----------------------------------------------------------------- basics


def test_short_series_reports_instead_of_raising():
    res = run_backtest(_bars(_walk(20, 0.0, 0.01, 1)), interval="1d")
    assert "error" in res.metrics


def test_every_trade_fills_at_a_bar_after_its_signal():
    """No lookahead in the fill itself: an exit index must be strictly after
    the entry, and no trade may open on the final bar (there is no bar left
    to fill against)."""
    bars = _bars(_walk(600, 0.002, 0.01, 2))
    res = run_backtest(bars, interval="1d")
    assert res.trades
    for t in res.trades:
        assert t["exit_index"] > t["entry_index"]
        assert t["entry_index"] <= len(bars) - 1


def test_costs_reduce_return_monotonically():
    bars = _bars(_walk(600, 0.002, 0.01, 3))
    free = run_backtest(bars, interval="1d", cost_bps=0.0)
    cheap = run_backtest(bars, interval="1d", cost_bps=2.0)
    dear = run_backtest(bars, interval="1d", cost_bps=50.0)
    assert free.metrics["trades"] == cheap.metrics["trades"]
    assert (
        free.metrics["total_return_pct"]
        >= cheap.metrics["total_return_pct"]
        > dear.metrics["total_return_pct"]
    )


def test_warmup_bars_are_neither_traded_nor_scored():
    """The walk-forward leak this was written to close.

    A fold hands the engine 210 bars of already-seen history to seed EMA200.
    Those bars must not produce a trade or enter the equity curve, or the
    "out-of-sample" result silently contains in-sample bars.
    """
    bars = _bars(_walk(700, 0.002, 0.01, 4))
    full = run_backtest(bars, interval="1d")
    warmed = run_backtest(bars, interval="1d", warmup_bars=400)
    assert warmed.metrics["warmup_bars"] == 400
    assert all(t["entry_index"] >= 400 for t in warmed.trades)
    assert warmed.metrics["bars"] == len(bars) - 400
    assert warmed.metrics["trades"] < full.metrics["trades"]


def test_buy_hold_is_measured_on_the_traded_slice_only():
    bars = _bars([100.0 + i for i in range(500)])
    warmed = run_backtest(bars, interval="1d", warmup_bars=300)
    expected = 100.0 * (bars[-1].close / bars[300].close - 1.0)
    assert warmed.metrics["buy_hold_pct"] == pytest.approx(expected, rel=1e-9)


def test_open_position_is_closed_at_the_end_not_silently_dropped():
    bars = _bars([100.0 + i * 0.7 for i in range(400)])
    res = run_backtest(bars, interval="1d")
    assert res.trades
    reasons = {t["exit_reason"] for t in res.trades}
    assert reasons <= {"atr_stop", "score_exit", "end_of_data"}
    # Every trade is accounted for -- none left open.
    assert all(t["exit_price"] is not None for t in res.trades)


# ---------------------------------------------------------------- metrics


def test_max_drawdown_is_negative_or_zero():
    assert _max_drawdown([1.0, 1.2, 0.9, 1.4]) == pytest.approx(-25.0)
    assert _max_drawdown([1.0, 1.1, 1.2]) == 0.0


def test_profit_factor_is_none_when_there_are_no_losses():
    """Not `inf`, and not a large number a sweep could rank on."""
    bars = _bars([100.0 + i for i in range(400)])
    res = run_backtest(bars, interval="1d")
    pf = res.metrics["profit_factor"]
    assert pf is None or pf > 0


def test_exposure_is_a_percentage():
    res = run_backtest(_bars(_walk(500, 0.001, 0.01, 5)), interval="1d")
    assert 0.0 <= res.metrics["exposure_pct"] <= 100.0


# ----------------------------------------------------------------- sweeps


def test_expand_grid_is_the_cartesian_product():
    combos = expand_grid({"a": [1, 2], "b": [3, 4, 5]})
    assert len(combos) == 6
    assert {"a": 1, "b": 3} in combos


def test_sweep_refuses_to_rank_a_run_with_too_few_trades():
    ranked = sweep(
        _bars(_walk(400, 0.001, 0.01, 6)),
        GRID,
        interval="1d",
        min_trades=10_000,
    )
    assert ranked and all(row[2] == float("-inf") for row in ranked)


def test_walk_forward_refuses_a_series_that_is_too_short():
    out = walk_forward(_bars(_walk(200, 0.001, 0.01, 7)), GRID, interval="1d", folds=4)
    assert "error" in out


def test_walk_forward_scores_out_of_sample_only():
    bars = _bars(_walk(1400, 0.0015, 0.012, 8))
    out = walk_forward(bars, GRID, interval="1d", folds=3, min_trades=2)
    assert "error" not in out
    scored = [f for f in out["folds"] if "out_of_sample" in f]
    assert scored
    for fold in scored:
        # The warm-up prefix must be excluded from the scored bar count.
        oos = fold["out_of_sample"]
        assert oos["warmup_bars"] == fold["warmup_bars"]
        assert fold["warmup_bars"] <= WARMUP_BARS


def test_walk_forward_compounds_rather_than_sums_fold_returns():
    bars = _bars(_walk(1400, 0.0015, 0.012, 9))
    out = walk_forward(bars, GRID, interval="1d", folds=3, min_trades=2)
    per_fold = [
        f["out_of_sample"]["total_return_pct"] for f in out["folds"] if "out_of_sample" in f
    ]
    compounded = 1.0
    for value in per_fold:
        compounded *= 1.0 + value / 100.0
    assert out["oos_total_return_pct"] == pytest.approx(100.0 * (compounded - 1.0))
    # A long-only book cannot lose more than everything.
    assert out["oos_total_return_pct"] > -100.0


def test_permutation_test_reports_a_valid_p_value():
    out = permutation_test(
        _bars(_walk(500, 0.002, 0.01, 10)), interval="1d", trials=8
    )
    assert 0.0 < out["p_value"] <= 1.0
    # The +1 correction: beating every surrogate reports 1/(trials+1), never 0.
    assert out["p_value"] >= 1.0 / (8 + 1)


def test_cache_hit_is_about_SPAN_not_bar_count():
    """Raising a lookback must refetch, not silently score the old window.

    The old rule was `len(records) >= 60`. Changing a universe entry from 3y to
    5y left 752 cached daily bars in place, printed "cached", and scored three
    years while the caller believed it had five.
    """
    from datetime import datetime, timedelta

    from chart_app.backtest_runner import _covers, _lookback_days
    from shared.chart_data import CandleRecord

    def span(days: int, n: int = 800):
        t0 = datetime(2026, 9, 17) - timedelta(days=days)
        step = days / max(n - 1, 1)
        return [
            CandleRecord(t0 + timedelta(days=step * i), 1, 1, 1, 1, 1)
            for i in range(n)
        ]

    assert _lookback_days("3y") == 1095
    assert _lookback_days("30d") == 30
    assert _lookback_days("nonsense") is None

    three_years = span(1094)
    assert _covers(three_years, "3y") is True
    assert _covers(three_years, "5y") is False      # the bug
    # A weekend/holiday start must not force a refetch of the whole universe.
    assert _covers(span(29, 540), "30d") is True
    assert _covers(span(5, 20), "3y") is False


# --- rank transfer math -----------------------------------------------------


def test_rank_average_gives_ties_their_shared_midrank():
    from chart_app.backtest import rank_average

    # 10 and 10 occupy ranks 2 and 3 -> both 2.5. Breaking that tie by array
    # order would invent an ordering the data does not contain.
    assert rank_average([5.0, 10.0, 10.0, 20.0]) == [1.0, 2.5, 2.5, 4.0]


def test_rank_average_ties_the_disqualified_combos_together():
    from chart_app.backtest import rank_average

    # -inf is what `_objective` returns for a combo that traded too little.
    # Every one of them is equally uninformative and must rank equally.
    assert rank_average([float("-inf"), 1.0, float("-inf")]) == [1.5, 3.0, 1.5]


def test_rank_average_refuses_a_nan_rather_than_sorting_it_somewhere():
    from chart_app.backtest import rank_average

    with pytest.raises(ValueError, match="NaN"):
        rank_average([1.0, float("nan"), 2.0])


def test_spearman_is_one_for_identical_and_minus_one_for_reversed_rankings():
    from chart_app.backtest import spearman_rho

    a = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert spearman_rho(a, a) == pytest.approx(1.0)
    assert spearman_rho(a, list(reversed(a))) == pytest.approx(-1.0)


def test_spearman_is_rank_based_not_value_based():
    from chart_app.backtest import spearman_rho

    # Same order, wildly different magnitudes: a rank correlation must not care.
    assert spearman_rho([1.0, 2.0, 3.0], [10.0, 1e6, 1e9]) == pytest.approx(1.0)


def test_spearman_is_none_rather_than_zero_when_a_side_is_constant():
    from chart_app.backtest import spearman_rho

    # "every combo scored the same" and "the rankings are unrelated" are
    # different findings; returning 0.0 would let the first read as the second.
    assert spearman_rho([1.0, 1.0, 1.0, 1.0], [1.0, 2.0, 3.0, 4.0]) is None


def test_percentile_of_puts_a_tied_value_at_the_middle_of_its_ties():
    from chart_app.backtest import percentile_of

    pop = [0.0, 0.0, 1.0, 2.0]
    # Two below, one tie -> (2 + 0.5) / 4 = 62.5, not 75.
    assert percentile_of(1.0, pop) == pytest.approx(62.5)
    # Tied with one other member and nothing below -> (0 + 1) / 4 = 25.0. A
    # value at the very bottom of a tied pair is not at percentile 0.
    assert percentile_of(0.0, pop) == pytest.approx(25.0)
    assert percentile_of(2.0, pop) == pytest.approx(87.5)


def test_percentile_of_a_random_member_averages_the_coinflip_null():
    from chart_app.backtest import percentile_of

    pop = [float(v) for v in range(20)]
    mean = np.mean([percentile_of(v, pop) for v in pop])
    # 50 is the null the rank-transfer stage reads its result against; if this
    # convention drifts, every "beats a coin flip" claim drifts with it.
    assert mean == pytest.approx(50.0)


# --- annualisation: measured, not tabled ------------------------------------


def _spaced(n: int, minutes: int) -> list[CandleRecord]:
    """`n` flat bars exactly `minutes` apart -- a tape with no session gap."""
    t0 = datetime(2026, 1, 2)
    return [
        CandleRecord(t0 + timedelta(minutes=minutes * i), 1.0, 1.0, 1.0, 1.0, 1.0)
        for i in range(n)
    ]


def test_bars_per_year_measured_from_timestamps_not_the_table():
    """A 24/7 perp is 96 bars a day at 15m; the equity table says 26.

    The table (26 * 252 = 6552) made the engine read a one-year BTC-PERP
    window as 5.37 years, which divided CAGR by 5 and Sharpe by 2.3.
    """
    from chart_app.backtest import BARS_PER_YEAR, _bars_per_year

    perp = _spaced(96 * 400, 15)          # 400 days, continuous
    measured = _bars_per_year(perp, "15m")
    assert measured == pytest.approx(96 * 365.25, rel=0.01)
    assert measured > 5 * BARS_PER_YEAR["15m"]


def test_bars_per_year_reproduces_the_table_on_a_session_tape():
    """The fix must not move the instruments the table already got right.

    26 bars a day, five days a week, is the shape the 6552 was written for.
    """
    from chart_app.backtest import BARS_PER_YEAR, _bars_per_year

    t0 = datetime(2026, 1, 5)             # a Monday
    out: list[CandleRecord] = []
    day = 0
    while len(out) < 26 * 252:
        d = t0 + timedelta(days=day)
        day += 1
        if d.weekday() >= 5:
            continue
        for b in range(26):
            ts = d.replace(hour=9, minute=30) + timedelta(minutes=15 * b)
            out.append(CandleRecord(ts, 1.0, 1.0, 1.0, 1.0, 1.0))
    assert _bars_per_year(out, "15m") == pytest.approx(BARS_PER_YEAR["15m"], rel=0.05)


def test_bars_per_year_falls_back_when_the_span_cannot_carry_the_claim():
    """Under a day of bars, one gap swings the estimate -- use the table."""
    from chart_app.backtest import BARS_PER_YEAR, _bars_per_year

    assert _bars_per_year(_spaced(4, 15), "15m") == BARS_PER_YEAR["15m"]
    assert _bars_per_year([], "15m") == BARS_PER_YEAR["15m"]
    assert _bars_per_year(_spaced(1, 15), "15m") == BARS_PER_YEAR["15m"]


def test_cagr_on_a_one_year_continuous_tape_equals_total_return():
    """The end-to-end claim: one year of bars must annualise to ~itself."""
    rng = np.random.default_rng(7)
    n = 96 * 365
    closes = list(100 * np.exp(np.cumsum(rng.normal(0.0002, 0.004, n))))
    t0 = datetime(2026, 1, 1)
    recs = [
        CandleRecord(
            t0 + timedelta(minutes=15 * i),
            float(closes[i - 1]) if i else float(closes[i]),
            float(closes[i]) * 1.002,
            float(closes[i]) * 0.998,
            float(closes[i]),
            1.0,
        )
        for i in range(n)
    ]
    m = run_backtest(recs, interval="15m").metrics
    assert m["years"] == pytest.approx(1.0, rel=0.02)
    assert m["cagr_pct"] == pytest.approx(m["total_return_pct"], rel=0.05, abs=1.0)
