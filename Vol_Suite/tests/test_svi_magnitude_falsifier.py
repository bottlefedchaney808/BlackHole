"""
Regression tests for the SVI-magnitude cross-sectional axis in
backtest_accumulation_falsifier.py -- Jason's idea (2026-08-13): since the
accumulated SIGN axis is degenerate (every ticker accumulates the same sign =
BASE), use the SVI cheap/rich marking as a MAGNITUDE signal and test whether
it sorts realized-vol level cross-sectionally.

Network-free: builds synthetic per-ticker chains + spot histories and calls the
pure _run_svi_magnitude_cross_sectional_from_histories directly.
"""
import datetime

import numpy as np
import pytest

import backtest_accumulation_falsifier as baf


def _spot_rows(level_vol: float, n_days: int = 60, seed: int = 0, end: float = 100.0):
    rng = np.random.RandomState(seed)
    daily = level_vol / np.sqrt(252)
    rets = rng.normal(0.0, daily, n_days)
    closes = 100.0 * np.exp(np.cumsum(rets))
    # Normalize so the LAST close == `end` -- the SVI-magnitude axis reads the
    # chain-date spot from the final close, so a deterministic end keeps the
    # outlier strikes at a fixed moneyness regardless of the random walk.
    closes = closes / closes[-1] * end
    base = datetime.date(2026, 1, 1)
    return [{"date": (base + datetime.timedelta(days=i)).strftime("%Y%m%d"),
             "close": float(c)} for i, c in enumerate(closes)]


def _synthetic_chain(spot, skew, mag):
    """Build one date's OTM chain with a clean SSVI-fittable base curve PLUS a
    few mispriced outlier strikes whose IV deviation grows with `mag` AND whose
    OI is 100x the rest -- so the OI-weighted |IV - ref| magnitude is dominated
    by the outliers and genuinely scales with `mag`. (A uniform level shift or a
    small-OI wing bump gets absorbed by SSVI's fit and produces ~zero distortion,
    so the outliers must be both body-placed and OI-heavy.)"""
    expiry = "20261201"
    d = "20260101"
    iv = {}
    for k in range(40, 99):  # OTM puts
        iv[(float(k), "P")] = 0.30 + skew * (k - spot) / 100.0
    for k in range(101, 130):  # OTM calls
        iv[(float(k), "C")] = 0.30 + 0.03 * (k - spot) / 100.0
    outliers = [(55.0, "P"), (65.0, "P"), (115.0, "C")]
    for (k, r) in outliers:
        if (k, r) in iv:
            iv[(k, r)] += mag  # IV bump
    greek_rows = [{"date": d, "strike": f"{int(k*1000)}", "right": r,
                   "implied_vol": v} for (k, r), v in iv.items()]
    oi_rows = [{"date": d, "strike": f"{int(k*1000)}", "right": r,
                "open_interest": 50000 if (k, r) in outliers else 500} for (k, r) in iv]
    return greek_rows, oi_rows, expiry


@pytest.mark.unit
def test_svi_magnitude_runs_and_produces_per_ticker_values():
    """End-to-end on synthetic tickers: returns a SviMagnitudeFalsifierResult
    with per-ticker magnitude/net-seed/rv populated (no exception)."""
    histories = {}
    for i in range(10):
        t = f"T{i:02d}"
        greek_rows, oi_rows, expiry = _synthetic_chain(100.0, skew=-0.5, mag=0.02 * (i % 2))
        spot_rows = _spot_rows(0.20 + 0.01 * i, seed=i)
        histories[t] = (expiry, greek_rows, oi_rows, spot_rows)
    res = baf._run_svi_magnitude_cross_sectional_from_histories(histories, n_perms=100)
    assert res.n_tickers == 10
    assert len(res.per_ticker_magnitude) == 10
    assert len(res.per_ticker_net_seed) == 10
    assert res.verdict in ("SVI_MAGNITUDE_CROSS_SECTIONAL_SIGNAL", "INCONCLUSIVE")


@pytest.mark.unit
def test_svi_magnitude_signals_when_distortion_sorts_rv():
    """Constructed: tickers with LARGER SVI magnitude (more smile distortion)
    have HIGHER realized-vol level. The magnitude axis should flag SIGNAL."""
    histories = {}
    for i in range(10):
        t = f"T{i:02d}"
        # monotone: high-vol tickers get big mag, low-vol get small mag
        high_vol = i >= 5
        level = 0.45 if high_vol else 0.18
        mag = 0.80 if high_vol else 0.001
        greek_rows, oi_rows, expiry = _synthetic_chain(100.0, skew=-0.5, mag=mag)
        spot_rows = _spot_rows(level, seed=i)
        histories[t] = (expiry, greek_rows, oi_rows, spot_rows)
    res = baf._run_svi_magnitude_cross_sectional_from_histories(histories, n_perms=200)
    assert res.verdict == "SVI_MAGNITUDE_CROSS_SECTIONAL_SIGNAL", res.verdict
    assert res.rho_mag_rv > 0  # more distortion => higher rv


@pytest.mark.unit
def test_svi_magnitude_inconclusive_when_magnitude_unrelated_to_rv():
    """Anti-case: SVI magnitude unrelated to realized-vol level (shuffled by
    parity). Must NOT false-positive."""
    histories = {}
    for i in range(10):
        t = f"T{i:02d}"
        level = 0.18 + 0.02 * i
        mag = 0.05 if i % 2 == 0 else 0.04  # parity, uncorrelated with level
        greek_rows, oi_rows, expiry = _synthetic_chain(100.0, skew=-0.5, mag=mag)
        spot_rows = _spot_rows(level, seed=100 + i)
        histories[t] = (expiry, greek_rows, oi_rows, spot_rows)
    res = baf._run_svi_magnitude_cross_sectional_from_histories(histories, n_perms=200)
    assert res.verdict != "SVI_MAGNITUDE_CROSS_SECTIONAL_SIGNAL"


@pytest.mark.unit
def test_svi_magnitude_inconclusive_below_floor():
    n = baf._MIN_CROSS_TICKERS - 1
    histories = {}
    for i in range(n):
        t = f"T{i:02d}"
        greek_rows, oi_rows, expiry = _synthetic_chain(100.0, skew=-0.5, mag=0.05)
        spot_rows = _spot_rows(0.30, seed=i)
        histories[t] = (expiry, greek_rows, oi_rows, spot_rows)
    res = baf._run_svi_magnitude_cross_sectional_from_histories(histories, n_perms=100)
    assert res.verdict == "INCONCLUSIVE"


@pytest.mark.unit
def test_svi_magnitude_raises_when_no_ticker_usable():
    with pytest.raises(ValueError):
        baf._run_svi_magnitude_cross_sectional_from_histories({}, n_perms=100)
