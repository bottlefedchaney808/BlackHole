from datetime import datetime, timedelta

from chart_app.score_engine import (
    classic_overlays,
    gated_markers,
    oscillators,
    price_scores,
)
from shared.chart_data import CandleRecord


def _series(n, start=100.0):
    t0 = datetime(2026, 1, 2)
    out = []
    px = start
    for i in range(n):
        px = start + i * 0.2
        out.append(
            CandleRecord(t0 + timedelta(days=i), px, px + 0.5, px - 0.5, px, 1000)
        )
    return out


def test_classic_overlays_length_and_warmup():
    recs = _series(60)
    ov = classic_overlays(recs)
    assert set(ov) == {
        "ema20",
        "ema50",
        "vwap",
        "bb_mid",
        "bb_upper",
        "bb_lower",
        # Added indicators (default off in the UI, always computed here).
        "ema200",
        "vwap_up",
        "vwap_dn",
        "atr_up",
        "atr_dn",
        "pdh",
        "pdl",
    }
    assert all(len(ov[k]) == 60 for k in ov)
    assert ov["ema20"][18] is None and ov["ema20"][19] is not None
    assert ov["ema50"][48] is None and ov["ema50"][49] is not None


def test_ema200_is_all_none_before_its_warmup():
    """200-period EMA on a 60-bar series has nothing to report -- it must say
    so with None rather than seeding off a short window."""
    ov = classic_overlays(_series(60))
    assert all(v is None for v in ov["ema200"])
    ov_long = classic_overlays(_series(240))
    assert ov_long["ema200"][198] is None and ov_long["ema200"][199] is not None


def test_atr_channel_brackets_the_ema_basis():
    ov = classic_overlays(_series(60))
    for up, mid, dn in zip(ov["atr_up"], ov["ema20"], ov["atr_dn"]):
        if up is None or mid is None or dn is None:
            continue
        assert dn < mid < up


def test_vwap_bands_bracket_vwap():
    ov = classic_overlays(_series(60))
    for up, mid, dn in zip(ov["vwap_up"], ov["vwap"], ov["vwap_dn"]):
        if up is None or mid is None or dn is None:
            continue
        assert dn <= mid <= up


def test_prior_day_levels_are_empty_on_a_one_bar_per_day_series():
    """_series() is daily, so every bar is its own session and there is no
    intraday prior-session high/low to carry forward."""
    ov = classic_overlays(_series(60))
    assert all(v is None for v in ov["pdh"])
    assert all(v is None for v in ov["pdl"])


def test_prior_day_levels_carry_the_previous_session_intraday():
    t0 = datetime(2026, 1, 2, 9, 30)
    recs = []
    # Day 1: two bars spanning 100-110. Day 2: two bars, should see 110 / 100.
    for hours, (lo, hi) in enumerate([(100.0, 105.0), (102.0, 110.0)]):
        recs.append(CandleRecord(t0 + timedelta(hours=hours), lo, hi, lo, hi, 1000))
    t1 = t0 + timedelta(days=1)
    for hours in range(2):
        recs.append(
            CandleRecord(t1 + timedelta(hours=hours), 106.0, 108.0, 104.0, 107.0, 1000)
        )
    ov = classic_overlays(recs)
    assert ov["pdh"][0] is None and ov["pdl"][0] is None  # no prior session
    assert ov["pdh"][2] == 110.0 and ov["pdl"][2] == 100.0
    assert ov["pdh"][3] == 110.0 and ov["pdl"][3] == 100.0


def test_price_scores_whale_and_liq_stay_false():
    recs = _series(80)
    rows = price_scores(recs)
    assert len(rows) == 80
    assert all(
        r["signals"]["whale"] is False and r["signals"]["liquidity"] is False
        for r in rows
    )
    assert all(0 <= r["score"] <= 3 for r in rows)  # only 3 price legs can be True


def test_gated_markers_need_a_long():
    entries = [{"score": s} for s in [0, 3, 4, 3, 0, 0]]
    assert gated_markers(entries) == ["none", "none", "buy", "hold", "sell", "none"]


def test_rsi_is_bounded_and_warms_up():
    ov = oscillators(_series(120))
    rsi = ov["rsi"]
    assert len(rsi) == 120
    assert rsi[13] is None and rsi[14] is not None  # Wilder 14 warm-up
    assert all(0.0 <= v <= 100.0 for v in rsi if v is not None)


def test_cci_is_zero_on_a_flat_window_not_infinite():
    """Mean deviation is 0 on a flat series, so CCI is 0/0 -- it must report
    0, not inf/nan, or the pane autoscales to nothing."""
    t0 = datetime(2026, 1, 2)
    flat = [
        CandleRecord(t0 + timedelta(days=i), 100.0, 100.0, 100.0, 100.0, 1000)
        for i in range(40)
    ]
    cci = oscillators(flat)["cci"]
    defined = [v for v in cci if v is not None]
    assert defined and all(v == 0.0 for v in defined)


def test_macd_histogram_is_line_minus_signal():
    ov = oscillators(_series(120))
    for line, sig, hist in zip(ov["macd"], ov["macd_signal"], ov["macd_hist"]):
        if line is None or sig is None:
            assert hist is None
        else:
            assert abs(hist - (line - sig)) < 1e-9


def test_oscillators_are_not_in_classic_overlays():
    """They have their own y-scales and must not land on the price pane."""
    ov = classic_overlays(_series(60))
    assert not {"rsi", "cci", "macd"} & set(ov)


def test_direction_coverage_reports_shortfall():
    from chart_app.score_engine import direction_coverage

    short = direction_coverage(_series(42))   # ~what 4h/30d yields
    assert short["active"] is False
    assert short["bars"] == 42
    assert short["shortfall"] == short["min_bars"] - 42

    ok = direction_coverage(_series(120))
    assert ok["active"] is True and ok["shortfall"] == 0
