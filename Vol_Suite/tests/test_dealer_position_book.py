"""Phase 7 tests: dealer_position_book.py -- network-free, synthetic DayData."""

import gzip
import json
import os

import pytest

import dealer_position_book as dpb


def make_row(strike, right, oi, iv, vanna=1.0):
    return {"strike": strike, "right": right, "oi": oi, "iv": iv, "vanna": vanna}


def make_day(date, spot, per_expiry):
    return {"date": date, "spot": spot, "per_expiry": per_expiry}


def one_expiry(rows, expiry="20260115"):
    return [{"expiry": expiry, "rows": rows}]


# ---------------------------------------------------------------------------
# Hand-computed 3-day synthetic accumulation (PRIMARY div_signed arm)
# ---------------------------------------------------------------------------


def _three_day_days():
    """3 days, one expiry, one strike (put at spot so atm_iv_otm reads it).

    day1: seed (no contribution)
    day2: d_iv = 0.80 - 0.83 = -0.03 (vol-down) -> day_sign=+1,
          delta_oi=+100, vanna 2.0 / mean|vanna| 2.0 -> +1*100*1.0 = +100
    day3: d_iv = 0.82 - 0.80 = +0.02 (vol-up) -> day_sign=-1,
          delta_oi=-50, vanna 1.0 / mean|vanna| 1.0 -> -1*-50*1.0 = +50
    """
    rows1 = [make_row(100.0, "P", 1000, 0.83, 2.0)]
    rows2 = [make_row(100.0, "P", 1100, 0.80, 2.0)]
    rows3 = [make_row(100.0, "P", 1050, 0.82, 1.0)]
    return [
        make_day("20260101", 100.0, one_expiry(rows1)),
        make_day("20260102", 100.0, one_expiry(rows2)),
        make_day("20260103", 100.0, one_expiry(rows3)),
    ]


def test_div_signed_hand_computed():
    res = dpb.accumulate_position_book(_three_day_days(), lookback=150)
    assert res.arm == "div_signed"
    assert res.position_by_strike[(100.0, "P")] == pytest.approx(150.0)
    assert res.total_net == pytest.approx(150.0)
    assert [r["kind"] for r in res.daily_trace] == ["seed", "accumulate", "accumulate"]
    # day2 sign flip visible in trace: vol-down -> +1, vol-up -> -1
    assert res.daily_trace[1]["day_sign"]["20260115"] == 1.0
    assert res.daily_trace[2]["day_sign"]["20260115"] == -1.0


def test_div_signed_book_can_go_negative():
    """Vol-up day with POSITIVE delta_oi subtracts -> negative book."""
    rows1 = [make_row(100.0, "P", 1000, 0.80, 1.0)]
    rows2 = [make_row(100.0, "P", 1200, 0.82, 1.0)]  # d_iv=+0.02 -> sign=-1
    days = [
        make_day("20260101", 100.0, one_expiry(rows1)),
        make_day("20260102", 100.0, one_expiry(rows2)),
    ]
    res = dpb.accumulate_position_book(days, lookback=150)
    assert res.position_by_strike[(100.0, "P")] == pytest.approx(-200.0)
    assert res.total_net < 0


def test_deadband_day_contributes_zero_exactly():
    """|d_iv| = 0.005 <= 0.01 -> day contributes 0 for ALL strikes."""
    rows1 = [make_row(100.0, "P", 1000, 0.80, 1.0), make_row(99.0, "C", 500, 0.81, 1.0)]
    rows2 = [make_row(100.0, "P", 1300, 0.805, 1.0), make_row(99.0, "C", 900, 0.805, 1.0)]
    days = [
        make_day("20260101", 100.0, one_expiry(rows1)),
        make_day("20260102", 100.0, one_expiry(rows2)),
    ]
    res = dpb.accumulate_position_book(days, lookback=150)
    assert res.position_by_strike == {}
    assert res.total_net == 0.0
    assert res.daily_trace[-1]["n_strikes_included"] == 0


def test_deadband_boundary_exact():
    """Boundary semantics: |d_iv| == deadband is IN the deadband -> 0.

    No binary-float IV pair diffs to exactly 0.01, so pin the boundary by
    passing the exact computed difference as the deadband (exercises the
    same `abs(d_iv) <= iv_deadband` inclusive comparison).
    """
    lo, hi = 0.80, 0.81
    exact_d = hi - lo  # 0.009999999999999898
    rows1 = [make_row(100.0, "P", 1000, lo, 1.0)]
    rows2 = [make_row(100.0, "P", 1200, hi, 1.0)]
    days = [
        make_day("20260101", 100.0, one_expiry(rows1)),
        make_day("20260102", 100.0, one_expiry(rows2)),
    ]
    # d_iv == deadband exactly -> inclusive deadband -> 0 contribution.
    res = dpb.accumulate_position_book(days, lookback=150, iv_deadband=exact_d)
    assert res.position_by_strike == {}
    # A deadband epsilon tighter than d_iv flips it to a signed contribution.
    res2 = dpb.accumulate_position_book(
        days, lookback=150, iv_deadband=exact_d * 0.999
    )
    assert res2.position_by_strike[(100.0, "P")] != 0.0


def test_div_signed_day3_contributes_zero():
    """|d_iv| = 0.005 <= 0.01 default deadband -> day contributes 0 exactly."""
    rows1 = [make_row(100.0, "P", 1000, 0.80, 1.0)]
    rows2 = [make_row(100.0, "P", 1300, 0.805, 1.0)]
    days = [
        make_day("20260101", 100.0, one_expiry(rows1)),
        make_day("20260102", 100.0, one_expiry(rows2)),
    ]
    res = dpb.accumulate_position_book(days, lookback=150)
    assert res.position_by_strike == {}


# ---------------------------------------------------------------------------
# FIXED_SIGN arm
# ---------------------------------------------------------------------------


def test_fixed_sign_stubbed_signs(monkeypatch):
    """Per-strike sign from resolve_vol_surface_sign; 0.0 -> -1.0 fallback."""

    class FakeRef:
        deviation_by_strike = {
            (100.0, "P"): +0.05,  # rich -> -1.0
            (105.0, "P"): -0.05,  # cheap -> +1.0
            (110.0, "P"): 0.0,    # in deadband -> 0.0 -> fallback -1.0
        }

    monkeypatch.setattr(
        dpb.vol_surface_reference,
        "compute_vol_surface_reference",
        lambda *a, **k: FakeRef(),
    )
    def _fake_resolve(ref, k, right):
        dev = ref.deviation_by_strike.get((k, right), 0.0)
        if dev > 0.01:
            return -1.0
        if dev < -0.01:
            return 1.0
        return 0.0

    monkeypatch.setattr(
        dpb.vol_surface_reference,
        "resolve_vol_surface_sign",
        _fake_resolve,
    )

    rows1 = [
        make_row(100.0, "P", 1000, 0.20, 1.0),
        make_row(105.0, "P", 1000, 0.20, 1.0),
        make_row(110.0, "P", 1000, 0.20, 1.0),
    ]
    rows2 = [
        make_row(100.0, "P", 1100, 0.20, 1.0),  # +100 * -1 = -100
        make_row(105.0, "P", 1100, 0.20, 1.0),  # +100 * +1 = +100
        make_row(110.0, "P", 1100, 0.20, 1.0),  # +100 * -1 = -100 (fallback)
    ]
    days = [
        make_day("20260101", 100.0, one_expiry(rows1)),
        make_day("20260102", 100.0, one_expiry(rows2)),
    ]
    res = dpb.accumulate_position_book(days, lookback=150, arm="fixed_sign")
    assert res.arm == "fixed_sign"
    assert res.position_by_strike[(100.0, "P")] == pytest.approx(-100.0)
    assert res.position_by_strike[(105.0, "P")] == pytest.approx(+100.0)
    # deadband-0 row used the -1.0 fallback, NOT 0
    assert res.position_by_strike[(110.0, "P")] == pytest.approx(-100.0)


def test_fixed_sign_no_surface_falls_back_flat_minus_one(monkeypatch):
    monkeypatch.setattr(
        dpb.vol_surface_reference,
        "compute_vol_surface_reference",
        lambda *a, **k: None,
    )
    rows1 = [make_row(100.0, "P", 1000, 0.20, 1.0)]
    rows2 = [make_row(100.0, "P", 1150, 0.20, 1.0)]
    days = [
        make_day("20260101", 100.0, one_expiry(rows1)),
        make_day("20260102", 100.0, one_expiry(rows2)),
    ]
    res = dpb.accumulate_position_book(days, lookback=150, arm="fixed_sign")
    assert res.position_by_strike[(100.0, "P")] == pytest.approx(-150.0)


# ---------------------------------------------------------------------------
# Sparse gaps / vanna normalization
# ---------------------------------------------------------------------------


def test_sparse_gap_strike_solved():
    """Strike in today but not prev day -> solved, not skipped.

    NEW-STRIKE RESOLUTION (dealer_position_book.py, commit 1018a70): absence
    in prev day is ambiguous (brand-new listing vs vendor dropped it), so the
    strike's last observed OI is carried forward as the baseline and the whole
    observed change is attributed as flow. Never-observed -> baseline 0.
    """
    rows1 = [make_row(100.0, "P", 1000, 0.80, 1.0)]
    rows2 = [
        make_row(100.0, "P", 1100, 0.78, 1.0),
        make_row(105.0, "P", 400, 0.79, 1.0),  # new today: baseline 0 -> delta = full OI
    ]
    days = [
        make_day("20260101", 100.0, one_expiry(rows1)),
        make_day("20260102", 100.0, one_expiry(rows2)),
    ]
    res = dpb.accumulate_position_book(days, lookback=150)
    assert res.position_by_strike[(105.0, "P")] == pytest.approx(400.0)
    assert res.position_by_strike[(100.0, "P")] == pytest.approx(100.0)
    assert res.daily_trace[-1]["n_new_strikes"] == 1


def test_vanna_scale_invariance():
    """Doubling all vannas that day does not change the position."""
    totals = []
    for v in (1.0, 2.0, 4.0):
        rows1 = [make_row(100.0, "P", 1000, 0.80, 1.0)]
        rows2 = [make_row(100.0, "P", 1100, 0.78, v)]
        days = [
            make_day("20260101", 100.0, one_expiry(rows1)),
            make_day("20260102", 100.0, one_expiry(rows2)),
        ]
        res = dpb.accumulate_position_book(days, lookback=150)
        totals.append(res.position_by_strike[(100.0, "P")])
    assert totals[0] == pytest.approx(100.0)  # vanna/mean|vanna| = 1.0
    assert totals[1] == pytest.approx(totals[0])
    assert totals[2] == pytest.approx(totals[0])


def test_vanna_missing_falls_back_to_static_sign(monkeypatch):
    class FakeRef:
        deviation_by_strike = {(100.0, "P"): -0.05}  # cheap -> +1.0

    monkeypatch.setattr(
        dpb.vol_surface_reference,
        "compute_vol_surface_reference",
        lambda *a, **k: FakeRef(),
    )
    monkeypatch.setattr(
        dpb.vol_surface_reference,
        "resolve_vol_surface_sign",
        lambda ref, k, right: 1.0,  # cheap -> +1.0
    )
    rows1 = [make_row(100.0, "P", 1000, 0.80, None)]  # vanna missing
    rows2 = [make_row(100.0, "P", 1120, 0.78, None)]
    days = [
        make_day("20260101", 100.0, one_expiry(rows1)),
        make_day("20260102", 100.0, one_expiry(rows2)),
    ]
    res = dpb.accumulate_position_book(days, lookback=150, arm="div_signed")
    # d_iv = -0.02 -> day_sign +1, but vanna missing -> static sign +1.0 * 120
    assert res.position_by_strike[(100.0, "P")] == pytest.approx(+120.0)


# ---------------------------------------------------------------------------
# 150d window rollover
# ---------------------------------------------------------------------------


def test_window_rollover_drops_first_five():
    days = []
    for i in range(155):
        rows = [make_row(100.0, "P", 1000 + i, 0.80, 1.0)]
        days.append(make_day(f"{20260000 + i + 1:08d}", 100.0, one_expiry(rows)))
    res = dpb.accumulate_position_book(days, lookback=150)
    assert len(res.dates_used) == 150
    assert res.dates_used[0] == f"{20260000 + 6:08d}"
    assert res.dates_used[-1] == f"{20260000 + 155:08d}"
    assert len(res.daily_trace) == 150  # seed + 149 accumulate days


def test_bad_arm_raises():
    with pytest.raises(ValueError):
        dpb.accumulate_position_book([], arm="bogus")


# ---------------------------------------------------------------------------
# Loader: real cache day pair (reads local Phase-1 cache; no network)
# ---------------------------------------------------------------------------

_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(dpb.__file__)),
    "_expiry_falsifier_cache",
    "opex_full_book",
)


def test_loader_real_cache_day_pair():
    days = dpb.load_history_days(_CACHE_DIR, lookback=2)
    assert len(days) == 2
    d0, d1 = days
    assert d1["date"] > d0["date"]
    assert d1["spot"] is not None and d1["spot"] > 0

    g_path = os.path.join(_CACHE_DIR, "greeks", f"{d1['date']}_all.json.gz")
    o_path = os.path.join(_CACHE_DIR, "oi", f"{d1['date']}_all.json.gz")
    with gzip.open(g_path, "rt") as f:
        greeks = json.load(f)
    with gzip.open(o_path, "rt") as f:
        oi_rows = json.load(f)
    oi_keys = {
        (str(int(r["expiration"])), int(r["strike"]), str(r["right"])) for r in oi_rows
    }

    # Right join: exactly the greeks rows that have a matching OI row survive.
    joined_expected = sum(
        1
        for r in greeks
        if (str(int(r["expiration"])), int(r["strike"]), str(r["right"])) in oi_keys
    )
    all_rows = [r for e in d1["per_expiry"] for r in e["rows"]]
    assert len(all_rows) == joined_expected

    # Dollar scaling: ThetaData thousandths-int strikes -> /1000.
    strikes = {r["strike"] for r in all_rows}
    assert max(strikes) < 2000.0
    raw_strikes = {int(r["strike"]) for r in greeks}
    sample_raw = sorted(raw_strikes)[len(raw_strikes) // 2]
    assert sample_raw / 1000.0 in strikes


def test_theta_strike_to_dollars():
    assert dpb._theta_strike_to_dollars(655000) == pytest.approx(655.0)
    assert dpb._theta_strike_to_dollars(13850) == pytest.approx(13.85)
    assert dpb._theta_strike_to_dollars(500) == pytest.approx(500.0)
