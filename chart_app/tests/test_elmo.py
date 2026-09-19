"""ELMo: entropy, Hui-Heubel liquidity, ALMA."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from chart_app.elmo import (
    ORDERED_MAX_RANK,
    alma,
    compute_elmo,
    hui_heubel,
    liquidity_score,
    permutation_entropy,
    rank_series,
    return_entropy,
)
from shared.chart_data import CandleRecord


def _bars(closes, volumes=None, minutes=15):
    t0 = datetime(2026, 1, 2, 9, 30)
    out = []
    for i, c in enumerate(closes):
        vol = None if volumes is None else volumes[i]
        out.append(
            CandleRecord(
                timestamp=t0 + timedelta(minutes=minutes * i),
                open=float(closes[i - 1]) if i else float(c),
                high=float(c) * 1.002,
                low=float(c) * 0.998,
                close=float(c),
                volume=vol,
            )
        )
    return out


def _walk(n, drift, sigma, seed):
    rng = np.random.default_rng(seed)
    return list(100 * np.exp(np.cumsum(rng.normal(drift, sigma, n))))


# ---------------------------------------------------------------- entropy


def test_return_entropy_is_bounded_and_warms_up():
    ent = return_entropy(_walk(300, 0.0, 0.01, 1), window=20)
    assert all(v is None for v in ent[:19])
    values = [v for v in ent if v is not None]
    assert values and all(0.0 <= v <= 100.0 for v in values)


def test_entropy_reads_lower_on_a_trend_than_on_a_random_walk():
    """The whole premise of the entropy leg: order is measurable.

    A clean drift concentrates returns into fewer buckets than a driftless
    walk of the same volatility, so its entropy must be materially lower. If
    this ever fails the leg is measuring nothing.
    """
    trend = return_entropy(_walk(800, 0.004, 0.003, 2), window=20)
    noise = return_entropy(_walk(800, 0.000, 0.006, 3), window=20)
    med_trend = float(np.median([v for v in trend if v is not None]))
    med_noise = float(np.median([v for v in noise if v is not None]))
    assert med_trend < med_noise - 8.0, (med_trend, med_noise)


def test_flat_series_is_perfectly_ordered_not_perfectly_random():
    ent = return_entropy([100.0] * 60, window=20)
    assert [v for v in ent if v is not None][-1] == 0.0


def test_permutation_entropy_is_bounded():
    ent = permutation_entropy(_walk(200, 0.0, 0.01, 4), window=20, dim=3)
    values = [v for v in ent if v is not None]
    assert values and all(0.0 <= v <= 100.0 for v in values)


# ------------------------------------------------------------------ rank


def test_rank_series_withholds_a_rank_it_cannot_support():
    """A percentile over three points is not a percentile."""
    ranked = rank_series([float(i) for i in range(30)], window=200)
    assert all(v is None for v in ranked[:19])
    assert ranked[29] is not None


def test_rank_series_is_causal():
    """Recomputing on a prefix must not change earlier values.

    The property the whole backtest rests on: no indicator may see the
    future. Recompute over a truncated series and every shared index must
    be bit-identical.
    """
    values = _walk(200, 0.001, 0.01, 5)
    full = rank_series(values, window=50)
    prefix = rank_series(values[:120], window=50)
    assert full[:120] == prefix


# ------------------------------------------------------------- liquidity


def test_hui_heubel_refuses_to_measure_without_volume():
    bars = _bars(_walk(80, 0.0, 0.01, 6), volumes=None)
    assert all(v is None for v in hui_heubel(bars, window=10))


def test_hui_heubel_reads_higher_when_the_same_move_costs_less_volume():
    """The ratio IS illiquidity: a big range on thin volume scores high."""
    closes = _walk(60, 0.0, 0.01, 7)
    thick = hui_heubel(_bars(closes, [1_000_000.0] * 60), window=10)
    thin = hui_heubel(_bars(closes, [10_000.0] * 60), window=10)
    assert thin[-1] > thick[-1]
    # Exactly 100x less volume for an identical price path.
    assert thin[-1] == pytest.approx(thick[-1] * 100.0, rel=1e-6)


def test_liquidity_score_inverts_the_ratio():
    """High score means LIQUID -- the TradingView author's convention, which
    is the opposite of the textbook ratio this is computed from."""
    lhh = [float(i) for i in range(1, 61)]   # steadily MORE illiquid
    score = liquidity_score(lhh, norm_window=60)
    published = [v for v in score if v is not None]
    # The most illiquid reading is the last one, so it must score lowest.
    assert published[-1] == min(published)


def test_compute_elmo_degrades_without_volume_but_still_returns_entropy():
    bars = _bars(_walk(150, 0.001, 0.01, 8), volumes=None)
    result = compute_elmo(bars)
    assert result.has_volume is False
    assert all(v is None for v in result.liquidity)
    assert not any(result.liquid)
    assert any(v is not None for v in result.entropy)


def test_compute_elmo_never_raises_on_degenerate_input():
    assert compute_elmo([]).entropy == []
    one = compute_elmo(_bars([100.0], [1.0]))
    assert len(one.entropy) == 1


# ------------------------------------------------------------------ alma


def test_alma_lags_less_than_an_sma_of_the_same_length():
    """The reason ALMA is here rather than another SMA: on a ramp, a
    front-weighted kernel sits closer to the current price."""
    ramp = [100.0 + i for i in range(40)]
    a = alma(ramp, window=9, offset=0.85, sigma=6.0)
    sma = float(np.mean(ramp[-9:]))
    assert a[-1] > sma
    assert a[-1] < ramp[-1]


def test_alma_is_none_until_its_window_fills():
    a = alma([100.0] * 20, window=9)
    assert all(v is None for v in a[:8])
    assert a[8] == pytest.approx(100.0)


# ------------------------------------------------------------- integration


def test_ordered_requires_both_a_low_rank_and_a_falling_cross():
    bars = _bars(_walk(500, 0.002, 0.004, 9), [500_000.0] * 500)
    result = compute_elmo(bars)
    for i, flag in enumerate(result.ordered):
        if not flag:
            continue
        assert result.entropy_rank[i] is not None
        assert result.entropy_rank[i] <= ORDERED_MAX_RANK
        assert result.entropy_fast[i] < result.entropy_slow[i]


def test_compute_elmo_aligns_every_series_to_the_bar_count():
    bars = _bars(_walk(120, 0.001, 0.01, 10), [1000.0] * 120)
    r = compute_elmo(bars)
    for name, series in r.as_dict().items():
        if name == "has_volume":
            continue
        assert len(series) == 120, name


def test_liquidity_window_is_the_floor_and_only_ent_and_alma_go_deeper():
    """The three legs are summed with fixed weights, so their horizons have to
    be ordered on purpose. Liquidity is the fastest state in the book and sets
    the floor; entropy and ALMA measure persistence and may go deeper.
    """
    from chart_app.elmo import resolve_windows

    cfg, prov = resolve_windows({"liq_window": 10})
    assert prov["floor"] == 10
    assert prov["floor_key"] == "liq_window"
    # Defaults are entropy 20 / alma 9: alma is BELOW a 10-bar floor and is
    # clamped up, entropy is above it and is left alone.
    assert cfg["alma_window"] == 10
    assert cfg["entropy_window"] == 20
    assert prov["clamped"] == {"alma_window": {"asked": 9, "used": 10}}

    # Deeper is always allowed.
    deep, prov_deep = resolve_windows(
        {"liq_window": 5, "entropy_window": 200, "alma_window": 50}
    )
    assert deep["entropy_window"] == 200
    assert deep["alma_window"] == 50
    assert prov_deep["clamped"] == {}


def test_the_floor_cannot_be_bypassed_by_any_caller():
    """`compute_elmo` is the only entry point, and it must route through the
    resolver -- otherwise the gears UI or a sweep could evaluate an incoherent
    grid point and report it as if it were the one asked for.
    """
    bars = _bars(_walk(300, 0.0, 0.01, 5))
    result = compute_elmo(bars, liq_window=30, alma_window=4, entropy_window=6)
    assert result.windows["floor"] == 30
    assert result.windows["clamped"] == {
        "alma_window": {"asked": 4, "used": 30},
        "entropy_window": {"asked": 6, "used": 30},
    }


def test_shipped_defaults_already_satisfy_the_floor():
    """liq_window 5 vs entropy 20 / alma 9 -- nothing is clamped out of the box,
    so the floor changes no shipped behaviour."""
    from chart_app.elmo import resolve_windows

    _, prov = resolve_windows(None)
    assert prov["clamped"] == {}
