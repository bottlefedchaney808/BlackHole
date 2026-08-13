"""
Regression tests for the cross-sectional falsifier in
backtest_accumulation_falsifier.py -- the follow-up test HANDOFF.md §5
recommends because the day-level pooled falsifier is mechanically blind to
BETWEEN-ticker signal: the 150d-accumulated dealer read is constant within each
ticker, so ticker-FE demeaning zeroes it regardless of real cross-sectional
variation.

This test exercises the pure cross-sectional machinery over synthetic,
controlled per-ticker signals -- it does NOT assert a particular verdict on a
noisy sample (that would bake a coin flip into a unit test). It verifies:
  1. the machinery runs end-to-end and produces sane types/shapes,
  2. the realized-vol level and dominant-sign reducers are correct,
  3. a constructed case where accumulated sign sorts realized-vol level produces
     the SIGNAL verdict (the cross-sectional discriminator working as intended),
  4. a constant/no-signal case does NOT false-positive.

Network-free: patches _extract_paired_signals to return controlled per-ticker
sign series and builds spot histories directly, so no ThetaData call and no
cached-file dependency.
"""
import numpy as np
import pytest

import backtest_accumulation_falsifier as baf


def _spot_series(level_vol: float, n_days: int = 40, seed: int = 0):
    """Build a spot close series whose log-return std is ~level_vol (annualized),
    matching the scale _realized_vol_level computes (std * sqrt(252))."""
    import datetime
    rng = np.random.RandomState(seed)
    daily = level_vol / np.sqrt(252)
    rets = rng.normal(0.0, daily, n_days)
    closes = 100.0 * np.exp(np.cumsum(rets))
    base = datetime.date(2026, 1, 1)
    rows = []
    for i, c in enumerate(closes):
        d = (base + datetime.timedelta(days=i)).strftime("%Y%m%d")
        rows.append({"date": d, "close": float(c)})
    return rows


def _controlled_extract(per_ticker):
    """Build a fake _extract_paired_signals that returns fixed (snap, acc) sign
    series per ticker. per_ticker: {ticker: (snap_sign_series, acc_sign_series)}.
    The spot rows come from the passed ticker_histories (used for rv level)."""
    def _fake(ticker, expiry, greek_rows, oi_rows, spot_rows,
              lookback_days=150, forward_window_days=5, seed_mode="replication"):
        snap, acc = per_ticker[ticker]
        n = max(len(snap), len(acc), 3)
        dates = [f"20260{i+1:02d}" for i in range(n)]
        return dates, np.array(snap, dtype=float), np.array(acc, dtype=float), np.zeros(n)
    return _fake


@pytest.mark.unit
def test_dominant_sign_reducer():
    assert baf._dominant_sign(np.array([1.0, 1.0, -1.0])) == 1.0   # net long
    assert baf._dominant_sign(np.array([-1.0, -1.0, 1.0])) == -1.0  # net short
    assert baf._dominant_sign(np.array([1.0, -1.0])) == 0.0        # balanced
    assert baf._dominant_sign(np.array([])) == 0.0


@pytest.mark.unit
def test_realized_vol_level_matches_constructed_level():
    level = 0.35
    rows = _spot_series(level, n_days=200)
    got = baf._realized_vol_level(rows)
    assert got is not None
    # Within ~10% of the constructed annualized vol.
    assert abs(got - level) / level < 0.10, f"constructed {level:.3f}, got {got:.3f}"


@pytest.mark.unit
def test_cross_sectional_signal_detects_accum_sign_sorting_rv(monkeypatch):
    """Constructed case: 12 tickers where accumulated sign is short for the
    high-vol tickers and long for the low-vol tickers (the dealer-hedge pattern
    the test is designed to catch). Snapshot sign is held flat (does NOT sort),
    so only the accumulated arm should drive the signal."""
    n = 12
    ticker_histories = {}
    acc_sign_series = {}
    snap_flat = [1.0, 1.0, -1.0, 1.0, -1.0]
    # 6 low-vol tickers => accum LONG (+1), 6 high-vol tickers => accum SHORT (-1).
    for i in range(n):
        ticker = f"T{i:02d}"
        low_vol = (i < 6)
        level = 0.20 if low_vol else 0.55
        ticker_histories[ticker] = ("20261201", [], [], _spot_series(level, seed=i))
        acc_sign = 1.0 if low_vol else -1.0
        acc_sign_series[ticker] = (snap_flat, [acc_sign] * 5)

    monkeypatch.setattr(baf, "_extract_paired_signals", _controlled_extract(acc_sign_series))

    result = baf._run_cross_sectional_falsifier_from_histories(
        ticker_histories, lookback_days=10, forward_window_days=3, n_perms=200)
    assert result.n_tickers == n
    assert result.verdict == "ACCUMULATION_CROSS_SECTIONAL_SIGNAL", result.verdict
    # Short tickers must have higher mean rv than long tickers.
    assert result.mean_rv_short > result.mean_rv_long
    assert result.n_short == 6 and result.n_long == 6
    assert result.rho_acc_rv < 0  # short (-1) associates with high rv => negative corr


@pytest.mark.unit
def test_cross_sectional_no_signal_when_accum_does_not_sort_rv(monkeypatch):
    """Constructed case: accumulated sign is unrelated to realized-vol level
    (shuffled pairing). The cross-sectional test must NOT false-positive --
    it should come back INCONCLUSIVE (not REDUNDANT, since R^2 can be non-trivial
    by noise, and not SIGNAL)."""
    n = 12
    ticker_histories = {}
    acc_sign_series = {}
    snap_flat = [1.0, -1.0, 1.0, -1.0, 1.0]
    # Every ticker gets a distinct vol level; acc signs assigned by parity, which
    # does not correlate with the (seed-randomized) vol ordering.
    for i in range(n):
        ticker = f"T{i:02d}"
        level = 0.20 + 0.03 * i
        ticker_histories[ticker] = ("20261201", [], [], _spot_series(level, seed=100 + i))
        acc_sign = 1.0 if i % 2 == 0 else -1.0
        acc_sign_series[ticker] = (snap_flat, [acc_sign] * 5)

    monkeypatch.setattr(baf, "_extract_paired_signals", _controlled_extract(acc_sign_series))

    result = baf._run_cross_sectional_falsifier_from_histories(
        ticker_histories, lookback_days=10, forward_window_days=3, n_perms=200)
    assert result.n_tickers == n
    assert result.verdict != "ACCUMULATION_CROSS_SECTIONAL_SIGNAL"


@pytest.mark.unit
def test_cross_sectional_underpowered_below_floor(monkeypatch):
    """n < _MIN_CROSS_TICKERS must be INCONCLUSIVE regardless of signal."""
    n = baf._MIN_CROSS_TICKERS - 1
    ticker_histories = {}
    acc_sign_series = {}
    snap_flat = [1.0, -1.0, 1.0]
    for i in range(n):
        ticker = f"T{i:02d}"
        level = 0.20 if i < (n // 2) else 0.55
        ticker_histories[ticker] = ("20261201", [], [], _spot_series(level, seed=200 + i))
        acc_sign = 1.0 if i < (n // 2) else -1.0
        acc_sign_series[ticker] = (snap_flat, [acc_sign] * 5)
    monkeypatch.setattr(baf, "_extract_paired_signals", _controlled_extract(acc_sign_series))

    result = baf._run_cross_sectional_falsifier_from_histories(
        ticker_histories, lookback_days=10, forward_window_days=3, n_perms=200)
    assert result.verdict == "INCONCLUSIVE"


@pytest.mark.unit
def test_cross_sectional_raises_when_no_ticker_usable():
    with pytest.raises(ValueError):
        baf._run_cross_sectional_falsifier_from_histories({}, lookback_days=10)
