"""
Regression test for the cross-sectional POOLED falsifier
(backtest_accumulation_falsifier._run_pooled_falsifier_from_histories).

Context: of the 12 cached tickers in the WSL handoff's seed_data/ package,
10 have 117-163 real trading days of option-chain history but SPY (62) and
QQQ (21) are thin -- their per-ticker falsifier verdict is INCONCLUSIVE
purely on sample size (n < 20/30), not because the signal was tested and
found absent. Rather than re-pulling live data, pool across all 12 tickers
(~1000+ ticker-days combined) via a ticker-fixed-effects within-
transformation (demean each ticker's own signal/RV series before pooling),
so the block-permutation test has enough pooled cells to reach a real
verdict, while still reporting each ticker's own day count so the SPY/QQQ
thinness stays visible rather than hidden inside an aggregate number.

Network-free: reuses the same synthetic multi-day scenario builder as
test_backtest_accumulation_falsifier.py, run for a handful of synthetic
"tickers" with deliberately different day counts (mimicking the real
10-thick/2-thin split) to prove the pooling machinery -- not to assert a
particular statistical verdict on real data.
"""
import pytest

import backtest_accumulation_falsifier as baf


def _theta(k):
    return int(round(k * 1000))


def _build_multiday_scenario(n_days=24):
    """Same construction as test_backtest_accumulation_falsifier.py's helper
    -- duplicated rather than imported (tests/ isn't a package here; see
    pytest.ini's pythonpath note) so this file has no cross-test-module
    coupling."""
    import random
    rng = random.Random(42)
    expiry = "20261201"
    strikes = list(range(70, 131))
    dates = [f"202607{15 + i:02d}" if 15 + i <= 31 else f"202608{15 + i - 31:02d}"
             for i in range(n_days)]

    spot = 100.0
    spots = {}
    for d in dates:
        spot += rng.uniform(-1.5, 1.8)
        spots[d] = spot

    hist_greek_rows, hist_oi_rows, hist_spot_rows = [], [], []
    base_oi = {k: 150 for k in strikes}
    for d in dates:
        s = spots[d]
        hist_spot_rows.append({"date": d, "close": s})
        for k in strikes:
            moneyness = k / s
            iv = max(0.15 + 0.10 * max(0, 1 - moneyness) + rng.uniform(-0.01, 0.01), 0.08)
            gamma = 0.02 * (1.0 / (1 + abs(k - s) / 10))
            for right in ("C", "P"):
                hist_greek_rows.append({"date": d, "strike": _theta(k), "right": right,
                                         "implied_vol": iv, "gamma": gamma})
            near_spot_bump = rng.randint(-50, 400) if abs(k - s) < 8 else rng.randint(-20, 20)
            hist_oi_rows.append({"date": d, "strike": _theta(k), "right": "C",
                                  "open_interest": max(base_oi[k] + near_spot_bump, 0)})
            hist_oi_rows.append({"date": d, "strike": _theta(k), "right": "P",
                                  "open_interest": base_oi[k]})
            base_oi[k] = max(base_oi[k] + near_spot_bump // 3, 50)

    return expiry, hist_greek_rows, hist_oi_rows, hist_spot_rows


@pytest.mark.unit
def test_pooled_falsifier_runs_across_multiple_tickers_of_different_lengths():
    thick_expiry, thick_g, thick_o, thick_s = _build_multiday_scenario(n_days=24)
    thin_expiry, thin_g, thin_o, thin_s = _build_multiday_scenario(n_days=10)

    ticker_histories = {
        "THICK1": (thick_expiry, thick_g, thick_o, thick_s),
        "THICK2": (thick_expiry, thick_g, thick_o, thick_s),
        "THIN1": (thin_expiry, thin_g, thin_o, thin_s),
    }

    result = baf._run_pooled_falsifier_from_histories(
        ticker_histories, lookback_days=10, forward_window_days=3, seed_mode="replication",
    )

    assert set(result.tickers) == {"THICK1", "THICK2", "THIN1"}
    assert result.n_tickers == 3
    assert result.per_ticker_n_days["THICK1"] > result.per_ticker_n_days["THIN1"]
    assert result.n_pooled_days == sum(result.per_ticker_n_days.values())
    assert result.verdict in ("ACCUMULATION_ADDS_SIGNAL", "REDUNDANT", "INCONCLUSIVE")


@pytest.mark.unit
def test_pooled_falsifier_reports_inconclusive_below_the_pooled_cell_floor():
    """Two tickers with only a handful of days each shouldn't be able to
    clear the pooled-cells floor -- proves the threshold is real, not
    decorative."""
    expiry, g, o, s = _build_multiday_scenario(n_days=10)
    ticker_histories = {
        "A": (expiry, g, o, s),
        "B": (expiry, g, o, s),
    }
    result = baf._run_pooled_falsifier_from_histories(
        ticker_histories, lookback_days=10, forward_window_days=3, seed_mode="replication",
    )
    assert result.verdict == "INCONCLUSIVE"


@pytest.mark.unit
def test_pooled_falsifier_flags_tickers_with_a_constant_within_ticker_signal():
    """Real-data finding (2026-08-13 run against the WSL handoff's 12-ticker
    cached dataset, post strike-scale fix): every ticker's ACCUMULATED sign
    was constant across its whole sample window (never flipped within a
    ticker), so after ticker-fixed-effects demeaning it collapses to exactly
    zero for every ticker -- the pooled regression can't detect anything,
    not because there's no signal but because a within-ticker-constant
    series has no within-ticker variance for a within-transform to use. The
    pooled result must say so explicitly (constant_signal_tickers) instead
    of silently reporting delta_r2=0.0000 with no explanation, which is
    what happened before this field existed and took real investigation to
    diagnose.
    """
    expiry, g, o, s = _build_multiday_scenario(n_days=24)
    ticker_histories = {"CONST": (expiry, g, o, s)}

    _, snap, acc, _ = baf._extract_paired_signals(
        "CONST", expiry, g, o, s, lookback_days=10, forward_window_days=3,
        seed_mode="replication",
    )
    # This fixture may or may not itself be within-ticker-constant; force
    # the condition under test directly rather than relying on incidental
    # fixture behavior, by monkeypatching the per-ticker extractor.
    import backtest_accumulation_falsifier as baf_mod
    orig = baf_mod._extract_paired_signals

    def _constant_acc(*a, **k):
        dates, snap_, acc_, rv_ = orig(*a, **k)
        import numpy as np
        return dates, snap_, np.full_like(acc_, -1.0), rv_

    import pytest as _pytest
    mp = _pytest.MonkeyPatch()
    mp.setattr(baf_mod, "_extract_paired_signals", _constant_acc)
    try:
        result = baf._run_pooled_falsifier_from_histories(
            ticker_histories, lookback_days=10, forward_window_days=3, seed_mode="replication",
        )
    finally:
        mp.undo()

    assert "CONST" in result.constant_signal_tickers
    assert result.delta_r2 == 0.0


@pytest.mark.unit
def test_pooled_falsifier_skips_tickers_that_fail_without_crashing():
    expiry, g, o, s = _build_multiday_scenario(n_days=24)
    ticker_histories = {
        "GOOD": (expiry, g, o, s),
        "EMPTY": (expiry, [], [], []),
    }
    result = baf._run_pooled_falsifier_from_histories(
        ticker_histories, lookback_days=10, forward_window_days=3, seed_mode="replication",
    )
    assert result.tickers == ["GOOD"]
    assert result.n_tickers == 1
