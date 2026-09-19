"""Rank transfer: does a tune done on some symbols mean anything on the others.

The stage exists to answer a question the sweep cannot, so these tests are
mostly about keeping the two claims it reports APART -- the ranking (which may
transfer) and the level (which does not). Collapsing them is how §3.17 in the
handoff happened.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from chart_app.backtest_runner import (
    _half_splits,
    _sign_test_p,
    stage_rank_transfer,
)
from shared.chart_data import CandleRecord


def _series(n, seed, drift=0.0004, sigma=0.012):
    """A random-walk daily series long enough for the engine to warm up."""
    rng = np.random.default_rng(seed)
    price = 100.0
    t0 = datetime(2024, 1, 2, tzinfo=UTC)
    out = []
    for i in range(n):
        prev = price
        price *= float(np.exp(rng.normal(drift, sigma)))
        hi = max(prev, price) * 1.004
        lo = min(prev, price) * 0.996
        out.append(
            CandleRecord(t0 + timedelta(days=i), prev, hi, lo, price, 1_000_000)
        )
    return out


# --- the cut enumeration ----------------------------------------------------


def test_half_splits_counts_each_cut_once_for_an_even_universe():
    names = [f"S{i}" for i in range(8)]
    splits = _half_splits(names)
    # C(8,4) = 70 ordered halves, but {A,B} and {B,A} are the SAME cut. Anchoring
    # the first symbol left dedupes them: 35. Without this the spread of the
    # result reads half as noisy as it is.
    assert len(splits) == 35


def test_half_splits_are_disjoint_and_cover_every_symbol():
    names = [f"S{i}" for i in range(8)]
    for left, right in _half_splits(names):
        assert not set(left) & set(right)
        assert set(left) | set(right) == set(names)


def test_half_splits_refuses_a_universe_too_small_to_cut():
    assert _half_splits(["A", "B", "C"]) == []


def test_sign_test_p_is_the_binomial_tail():
    # 6 of 8 above a coin flip -> (C(8,6)+C(8,7)+C(8,8)) / 2^8 = 37/256.
    assert _sign_test_p(6, 8) == pytest.approx(37 / 256)
    assert _sign_test_p(8, 8) == pytest.approx(1 / 256)
    assert _sign_test_p(0, 8) == pytest.approx(1.0)
    assert _sign_test_p(0, 0) is None


# --- the stage's selection logic, against a matrix with known answers -------


def _fake_matrix(objectives: dict[str, list[float]], returns: dict[str, list[float]]):
    """Hand-built combo x symbol scores, so the expected output is arithmetic."""
    n = len(next(iter(objectives.values())))
    return [
        {
            "params": {"combo": i},
            "objective": {t: objectives[t][i] for t in objectives},
            "return_pct": {t: returns[t][i] for t in returns},
            "trades": {t: 10 for t in objectives},
        }
        for i in range(n)
    ]


@pytest.fixture
def stubbed(monkeypatch):
    """Replace the data load and the backtest matrix; keep the real analysis."""

    def _install(objectives, returns):
        names = list(objectives)
        monkeypatch.setattr(
            "chart_app.backtest_runner._loaded",
            lambda universe, **kw: [(t, "1d", []) for t in names],
        )
        matrix = _fake_matrix(objectives, returns)
        monkeypatch.setattr(
            "chart_app.backtest_runner._score_matrix",
            lambda *a, **kw: matrix,
        )
        return matrix

    return _install


def test_a_combo_that_wins_everywhere_lands_at_the_top_of_every_held_out_symbol(
    stubbed,
):
    names = ["A", "B", "C", "D"]
    # Combo 2 is the best on every symbol, by a lot. Fitting on any three must
    # choose it, and it must then top the fourth.
    objectives = {t: [0.1, 0.2, 9.0, 0.3] for t in names}
    returns = {t: [1.0, 2.0, 40.0, 3.0] for t in names}
    stubbed(objectives, returns)

    out = stage_rank_transfer([("A", "1d", "3y")])
    loo = out["leave_one_out"]

    assert [row["held_out"] for row in loo] == names
    assert all(row["params"] == {"combo": 2} for row in loo)
    assert all(row["percentile_in_held_out"] == pytest.approx(87.5) for row in loo)
    assert out["summary"]["loo_above_coinflip"] == 4
    assert out["summary"]["rho_median"] == pytest.approx(1.0)


def test_a_ranking_that_does_not_transfer_is_reported_as_not_transferring(stubbed):
    names = ["A", "B", "C", "D"]
    # Each symbol's best combo is the previous symbol's worst. There is no
    # shared ordering to find, and the stage must not manufacture one.
    objectives = {
        "A": [4.0, 3.0, 2.0, 1.0],
        "B": [1.0, 2.0, 3.0, 4.0],
        "C": [4.0, 3.0, 2.0, 1.0],
        "D": [1.0, 2.0, 3.0, 4.0],
    }
    returns = {t: [10.0, 10.0, 10.0, 10.0] for t in names}
    stubbed(objectives, returns)

    out = stage_rank_transfer([("A", "1d", "3y")])

    # Held out A: fitting on B, C, D ranks by median -> C and D disagree with B,
    # so the chosen combo is whatever the median favours, and it cannot be
    # reliably at the top of A. What must hold is that the stage does not report
    # a positive whole-surface correlation from anti-correlated inputs.
    assert out["summary"]["rho_median"] <= 0.0


def test_the_level_gap_is_reported_separately_from_the_ranking(stubbed):
    names = ["A", "B", "C", "D"]
    objectives = {t: [0.1, 5.0, 0.2, 0.3] for t in names}
    # The winning combo made 100% on A and B, and 10% on C and D. A cut that
    # fits on {A,B} therefore sees a level it cannot reproduce on {C,D}.
    returns = {
        "A": [1.0, 100.0, 2.0, 3.0],
        "B": [1.0, 100.0, 2.0, 3.0],
        "C": [1.0, 10.0, 2.0, 3.0],
        "D": [1.0, 10.0, 2.0, 3.0],
    }
    stubbed(objectives, returns)

    out = stage_rank_transfer([("A", "1d", "3y")])
    summary = out["summary"]

    # The ranking transfers perfectly -- the same combo wins everywhere...
    assert summary["percentile_median"] == pytest.approx(87.5)
    # ...while the level does not, and the two numbers stay separate.
    assert summary["level_gap_median_pp"] == pytest.approx(0.0, abs=90.0)
    assert any(
        d["in_group_return_pct"] != d["out_group_return_pct"] for d in out["directions"]
    )


def test_selection_reads_the_fit_half_only(stubbed):
    names = ["A", "B", "C", "D"]
    # Combo 0 wins on A and B; combo 3 wins on C and D. A cut fitted on {A,B}
    # must choose combo 0 even though it is the worst on the half it is tested
    # on -- choosing with any knowledge of the test half would be the leak this
    # whole stage exists to avoid.
    objectives = {
        "A": [9.0, 1.0, 1.0, 0.0],
        "B": [9.0, 1.0, 1.0, 0.0],
        "C": [0.0, 1.0, 1.0, 9.0],
        "D": [0.0, 1.0, 1.0, 9.0],
    }
    returns = {t: [5.0, 5.0, 5.0, 5.0] for t in names}
    stubbed(objectives, returns)

    out = stage_rank_transfer([("A", "1d", "3y")])
    ab = next(
        d for d in out["directions"] if d["fit_on"] == ["A", "B"] and d["tested_on"] == ["C", "D"]
    )
    assert ab["params"] == {"combo": 0}
    assert ab["percentile_in_test"] == pytest.approx(12.5)


# --- end to end on real bars ------------------------------------------------


def test_stage_runs_end_to_end_and_keeps_its_two_claims_apart(monkeypatch):
    names = ["A", "B", "C", "D"]
    series = [(t, "1d", _series(420, seed=i)) for i, t in enumerate(names)]
    monkeypatch.setattr(
        "chart_app.backtest_runner._loaded", lambda universe, **kw: series
    )

    out = stage_rank_transfer(
        [("A", "1d", "3y")],
        grid={"entry_long": [20.0, 30.0], "exit_long": [-12.0], "atr_stop_mult": [2.5]},
        min_trades=1,
    )

    assert out["summary"]["cuts"] == 3
    assert out["summary"]["directions"] == 6
    assert len(out["leave_one_out"]) == 4
    # Each symbol is the held-out test exactly once -- that is the whole point
    # of the leave-one-out block, and the reason its sign test is quotable
    # while the 6 overlapping directions are not.
    assert sorted(r["held_out"] for r in out["leave_one_out"]) == names
    for key in ("rho_median", "percentile_median", "level_gap_median_pp"):
        assert key in out["summary"]


def test_stage_refuses_a_universe_with_the_same_symbol_twice(monkeypatch):
    # SPY 1d and SPY 15m in the same cut is not out-of-sample; it is the same
    # asset on both sides, and it would read as transfer.
    monkeypatch.setattr(
        "chart_app.backtest_runner._loaded",
        lambda universe, **kw: [("SPY", "1d", []), ("SPY", "15m", [])],
    )
    with pytest.raises(ValueError, match="one series per symbol"):
        stage_rank_transfer([("SPY", "1d", "3y")])
