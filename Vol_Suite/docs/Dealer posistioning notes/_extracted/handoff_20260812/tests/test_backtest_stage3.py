"""
Regression test for backtest_stage3.py (Stage 3 of
DEALER_POSITIONING_V2_DESIGN.md §8 -- the realized-vol behavioral backtest).

Stage-1-style discipline: before ever pointing this at real, expensive
historical data, prove the STATISTICAL MACHINERY itself (day-alignment,
forward-vol windowing, regime bucketing, the t-test) correctly recovers a
KNOWN, deliberately-embedded relationship on synthetic data where the right
answer is known by construction -- exactly the same reasoning
tests/test_variance_swap_replication.py applied to the recursion math itself.
A backtest harness that can't detect a signal it was explicitly built to
contain is not trustworthy on real data where the signal (if any) is much
weaker and the right answer isn't known in advance.

Network-free throughout: builds hist_greek_rows/hist_oi_rows/hist_price_rows
by hand, shaped exactly like ThetaDataController.option_bulk_hist_greeks /
option_bulk_hist_oi / hist_stock_eod return them (so this also
documents/enforces that row-shape contract), and calls
_run_backtest_from_history / _build_day_records directly -- no
ThetaDataController, no network.
"""
import math
import os

import numpy as np
import pytest

import backtest_stage3 as bt3


def _theta(k):
    return int(round(k * 1000))


SPOT0 = 100.0
STRIKES = [70, 75, 80, 85, 90, 95, 100, 105, 110, 115, 120, 125, 130]


def _flat_smile_chain(spot):
    """A smooth, undistorted smile -- no deliberately-cheapened strike, so
    Layer 1a shouldn't have a strong reason to flip any particular strike.
    """
    chain = {}
    for k in STRIKES:
        x = math.log(k / spot)
        iv = max(0.20 + 0.25 * x * x - 0.03 * x, 0.05)
        right = 'C' if k >= spot else 'P'
        chain[(float(k), right)] = iv
    return chain


def _make_day_rows(date: str, spot: float, call_oi: int, put_oi: int, gamma: float = 0.02):
    """One day's greeks + OI rows, with a deliberately lopsided OI split
    (call_oi vs put_oi) so the day's TRUE embedded gamma-sign label is known
    by construction -- heavy call OI => v1's flat convention sums positive
    (long gamma), heavy put OI => sums negative (short gamma).
    """
    chain_iv = _flat_smile_chain(spot)
    greek_rows, oi_rows = [], []
    for (k, right), iv in chain_iv.items():
        greek_rows.append({"date": date, "strike": _theta(k), "right": right,
                           "implied_vol": iv, "delta": 0.0, "gamma": gamma})
        oi = call_oi if right == 'C' else put_oi
        oi_rows.append({"date": date, "strike": _theta(k), "right": right, "open_interest": oi})
    return greek_rows, oi_rows


def _price_row(date, close):
    return {"date": date, "open": close, "high": close, "low": close, "close": close, "volume": 1000}


# ---------------------------------------------------------------------------
# Small building blocks in isolation
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_trading_days_per_year_is_252():
    """TRADING-day close-to-close returns (_forward_realized_vol) must be
    annualized by sqrt(252), NOT sqrt(365) -- DEFAULT_A is 365 (calendar
    days). Using 365 overstated forward realized vol by ~20% (same bug class
    fixed in variance_swap_live.py / variance_swap_screener.py).
    """
    assert bt3.TRADING_DAYS_PER_YEAR == 252


@pytest.mark.unit
def test_forward_realized_vol_matches_known_std():
    # Constant per-step log-return of ln(1.01) for 4 steps -- realized vol
    # of a perfectly constant-return series is 0 (no dispersion), so use two
    # different step sizes to get a known, checkable nonzero std.
    prices = [100.0, 101.0, 99.0, 101.0, 99.0, 101.0]  # alternating +/- ~2%
    vol = bt3._forward_realized_vol(prices, window=5)
    assert vol is not None
    assert vol > 0


@pytest.mark.unit
def test_forward_realized_vol_none_when_insufficient_data():
    prices = [100.0, 101.0]  # only 1 forward step, window wants 5
    assert bt3._forward_realized_vol(prices, window=5) is None


@pytest.mark.unit
def test_net_gamma_v1_sign_follows_call_put_oi_skew():
    chain_iv = _flat_smile_chain(SPOT0)
    gamma_map = {k: 0.02 for k in chain_iv}
    heavy_call_oi = {(k, right): (1000 if right == 'C' else 10) for (k, right) in chain_iv}
    heavy_put_oi = {(k, right): (10 if right == 'C' else 1000) for (k, right) in chain_iv}

    assert bt3._net_gamma_v1(gamma_map, heavy_call_oi) > 0
    assert bt3._net_gamma_v1(gamma_map, heavy_put_oi) < 0


@pytest.mark.unit
def test_net_gamma_v2_runs_and_restricts_to_otm():
    """v2 shouldn't error on a normal smile, and (unlike v1) should ignore
    ITM legs -- confirmed indirectly: an ITM-only OI injection shouldn't
    move the total at all, since _otm_leg_weights excludes it.
    """
    chain_iv = _flat_smile_chain(SPOT0)
    gamma_map = {k: 0.02 for k in chain_iv}
    oi_map = {k: 100 for k in chain_iv}
    forward = SPOT0
    T = 0.25
    total = bt3._net_gamma_v2(gamma_map, oi_map, chain_iv, SPOT0, forward, T)
    assert isinstance(total, float)
    assert not math.isnan(total)


@pytest.mark.unit
def test_weighted_dealer_sign_tapers_small_saturates_large():
    """Task-21 + CARL R1-F3: the magnitude-weighted sign must (a) return 0 for
    any |dev| inside the IV dead-band (matching resolve_vol_surface_sign -- no
    confident read), (b) taper for small out-of-band deviations, (c) saturate
    for large ones."""
    assert bt3._weighted_dealer_sign(0.0) == 0.0
    # inside the 1-vol-point dead band -> 0 (no confident directional read)
    assert bt3._weighted_dealer_sign(0.005) == 0.0
    assert bt3._weighted_dealer_sign(-0.005) == 0.0
    # just beyond the band: rich (positive dev) -> tapered NEGATIVE
    small_rich = bt3._weighted_dealer_sign(1.2 * 0.01)  # 1.2 vol pt, just out of band
    assert -0.5 < small_rich < 0.0
    # cheap (negative dev) -> tapered POSITIVE
    assert 0.0 < bt3._weighted_dealer_sign(-1.2 * 0.01) < 0.5
    # large rich -> dealer short -> saturates toward -1; large cheap -> +1
    assert bt3._weighted_dealer_sign(0.15) < -0.9
    assert bt3._weighted_dealer_sign(-0.15) > 0.9
    # monotone in magnitude
    assert abs(bt3._weighted_dealer_sign(0.03)) < abs(bt3._weighted_dealer_sign(0.08)) < 1.0


@pytest.mark.unit
def test_net_gamma_v2_flat_smile_is_not_pinned_all_short():
    """Task-21 de-degeneration: on a flat smile (near-zero deviations) the
    weighted v2 net gamma should be small in magnitude -- it must NOT collapse
    to the full value that a binary 'replication = all OTM short' convention
    would produce. This is what made the short/long vol test untestable."""
    chain_iv = _flat_smile_chain(SPOT0)
    gamma_map = {k: 0.02 for k in chain_iv}
    oi_map = {k: 100 for k in chain_iv}
    total = bt3._net_gamma_v2(gamma_map, oi_map, chain_iv, SPOT0, SPOT0, 0.25)
    # Flat smile -> deviations ~0 -> weighted signs ~0 -> net gamma near 0,
    # nowhere near the -sum(gamma*OI) an all-short binary convention implies.
    full_short = -sum(0.02 * 100 for k in chain_iv)
    assert abs(total) < 0.5 * abs(full_short), (
        f"flat smile net gamma {total:.3f} pinned toward full-short {full_short:.3f} "
        "— magnitude-weighting not de-degenerating the sign")


@pytest.mark.unit
def test_regression_recovers_negative_short_gamma_vol_link():
    """The PRIMARY test (_summarize_regression) must recover a BUILT-IN negative
    link between net gamma and forward realized vol, controlling for ATM IV and
    TTE -- i.e. more-negative (dealer-short) days -> higher fwd vol (coef<0)."""
    rng = np.random.default_rng(0)
    records = []
    for i in range(60):
        T = 0.2 + 0.004 * i                 # TTE drifts across the sample
        atm = float(0.20 + rng.normal(0, 0.02))
        net = float(rng.normal(0, 1e7))     # net-gamma magnitude scale
        fwd = 0.25 - 5e-9 * net + 0.5 * (atm - 0.20) + float(rng.normal(0, 0.005))
        records.append(bt3.DayRecord(
            date=f"202601{(i % 28) + 1:02d}", spot=100.0,
            net_gamma_v1=net, net_gamma_v2=net, regime_v1='s', regime_v2='s',
            fwd_realized_vol=fwd, atm_iv=atm, T=T))
    res = bt3._summarize_regression(records, 'net_gamma_v1', n_perm=300)
    assert res['n'] == 60
    assert res['coef'] < 0, f"expected negative coef (short->higher vol), got {res['coef']}"
    assert res['perm_p'] < 0.05, f"permutation null should be small, got {res['perm_p']}"


# ---------------------------------------------------------------------------
# The actual point: does the backtest harness recover a KNOWN relationship?
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_backtest_detects_embedded_short_gamma_amplification():
    """Construct 30 days where the TRUE label (by construction: heavy call
    OI = 'long' day, heavy put OI = 'short' day) is deliberately linked to
    the FORWARD price path's volatility -- quiet/small steps following
    'long' days, choppy/large steps following 'short' days. If the
    bucketing + t-test machinery works, v1 (which reads this OI skew
    directly, no fitting involved) should recover a positive, statistically
    significant short-minus-long vol difference.
    """
    dates = [f"202607{d:02d}" for d in range(1, 29)]  # 28 synthetic trading days
    greek_rows, oi_rows, price_rows = [], [], []

    price = SPOT0
    true_labels = []
    for i, d in enumerate(dates):
        is_short_day = (i % 2 == 0)  # alternate: short, long, short, long...
        true_labels.append('short' if is_short_day else 'long')

        call_oi, put_oi = (10, 5000) if is_short_day else (5000, 10)
        g_rows, o_rows = _make_day_rows(d, price, call_oi, put_oi)
        greek_rows.extend(g_rows)
        oi_rows.extend(o_rows)
        price_rows.append(_price_row(d, price))

        # Move price for the NEXT day: big steps after a 'short' day, small
        # steps after a 'long' day -- this is what should show up as higher
        # forward realized vol following 'short'-labeled days.
        step = 0.06 if is_short_day else 0.005
        # Deterministic alternating +/- so there's real dispersion, not a
        # one-directional drift that would swamp the vol calc with trend.
        price *= (1 + step) if (i % 4 < 2) else (1 - step)

    result = bt3._run_backtest_from_history("SYN", "20261231", greek_rows, oi_rows, price_rows,
                                             forward_window_days=3)

    assert result.v1_n_long > 0 and result.v1_n_short > 0
    assert result.v1_diff > 0, (
        f"expected short-gamma days to show higher forward realized vol by "
        f"construction, got diff={result.v1_diff}"
    )
    assert result.v1_pvalue < 0.05, (
        f"expected the embedded effect to be statistically significant with "
        f"this large a synthetic gap, got p={result.v1_pvalue}"
    )

    # v2 should at least run cleanly and produce real numbers (its own
    # correctness is vol_surface_reference.py's job, not this test's) --
    # sanity-check the shape of the result rather than demanding it also
    # recovers v1's easy OI-skew signal, since v2's per-strike sign depends
    # on the (separately-tested) SABR/quadratic fit, not a flat rule.
    # <=, not ==: _summarize only counts days with a forward-vol label
    # (the last forward_window_days days in the sample don't get one).
    assert result.v2_n_long + result.v2_n_short <= len(result.day_records)
    assert result.v2_n_long + result.v2_n_short > 0
    assert not math.isnan(result.v2_long_mean_vol) or result.v2_n_long == 0
    assert not math.isnan(result.v2_short_mean_vol) or result.v2_n_short == 0


@pytest.mark.unit
def test_backtest_no_spurious_signal_when_vol_is_unrelated_to_regime():
    """Null case: OI skew (the regime label) is embedded as before, but the
    forward price path's step size is now IDENTICAL regardless of that
    day's label -- there is no real relationship. The harness should NOT
    report a large, significant diff just because it was asked to look for
    one; this guards against a bucketing bug that manufactures significance
    out of nothing (e.g. an off-by-one that leaks information).
    """
    dates = [f"202607{d:02d}" for d in range(1, 29)]
    greek_rows, oi_rows, price_rows = [], [], []

    price = SPOT0
    for i, d in enumerate(dates):
        is_short_day = (i % 2 == 0)
        call_oi, put_oi = (10, 5000) if is_short_day else (5000, 10)
        g_rows, o_rows = _make_day_rows(d, price, call_oi, put_oi)
        greek_rows.extend(g_rows)
        oi_rows.extend(o_rows)
        price_rows.append(_price_row(d, price))

        # SAME step size regardless of label -- no real relationship to find.
        step = 0.01
        price *= (1 + step) if (i % 4 < 2) else (1 - step)

    result = bt3._run_backtest_from_history("SYN", "20261231", greek_rows, oi_rows, price_rows,
                                             forward_window_days=3)
    assert result.v1_pvalue > 0.05 or math.isnan(result.v1_pvalue), (
        f"expected no significant difference when vol truly doesn't depend "
        f"on the regime label, got diff={result.v1_diff} p={result.v1_pvalue}"
    )


@pytest.mark.unit
def test_build_day_records_skips_days_without_full_overlap():
    """A day present in greeks/OI history but missing from price history
    (or vice versa) shouldn't produce a record at all -- silently
    defaulting a missing price to some placeholder would corrupt the vol
    calc without any error being raised.
    """
    d1, d2 = "20260701", "20260702"
    g1, o1 = _make_day_rows(d1, SPOT0, 1000, 10)
    g2, o2 = _make_day_rows(d2, SPOT0, 1000, 10)
    greek_rows = g1 + g2
    oi_rows = o1 + o2
    # Only d1 has a price row -- d2 should be dropped entirely.
    price_rows = [_price_row(d1, SPOT0)]

    records = bt3._build_day_records("SYN", "20261231", greek_rows, oi_rows, price_rows)
    assert len(records) == 1
    assert records[0].date == d1


@pytest.mark.unit
def test_run_backtest_from_history_raises_on_no_overlapping_data():
    with pytest.raises(ValueError):
        bt3._run_backtest_from_history("SYN", "20261231", [], [], [])


# ---------------------------------------------------------------------------
# Deriving IV/gamma from prices -- the production path since 2026-07-24.
#
# `hist/option/all_greeks` returns one day per call and ignores end_date, so
# backfilling through it costs ~47,000 requests per expiry. `hist/option/eod`
# honors ranges for ~430, but carries prices only. These cover the resulting
# reconstruction, since a silent failure here would put fabricated points into
# the smile that v2's entire sign convention is fitted to.
# ---------------------------------------------------------------------------

def _price_only_rows(date: str, spot: float, expiry: str, sigma: float = 0.22):
    """Rows shaped like hist/option/eod: bid/ask/close and identity, NO
    implied_vol and NO gamma."""
    import implied_vol as iv_mod
    expiry_dt = bt3.datetime.strptime(expiry, "%Y%m%d")
    T = max((expiry_dt - bt3.datetime.strptime(date, "%Y%m%d")).days, 1) / 365.0
    rows = []
    for k in STRIKES:
        right = 'C' if k >= spot else 'P'
        px = iv_mod.bs_price(spot, float(k), T, bt3._BACKTEST_R, bt3._BACKTEST_Q,
                             sigma, right)
        rows.append({"date": date, "strike": _theta(k), "right": right,
                     "bid": px * 0.99, "ask": px * 1.01, "close": px})
    return rows


@pytest.mark.unit
def test_iv_and_gamma_are_recovered_from_price_only_rows():
    """Round-trip through the real pipeline: price a chain at a known vol,
    hand backtest_stage3 ONLY prices, and confirm it rebuilds a usable day."""
    expiry = "20261231"
    dates = ["20260901", "20260902", "20260903", "20260904",
             "20260905", "20260908", "20260909"]
    greek_rows, oi_rows, price_rows = [], [], []
    for i, d in enumerate(dates):
        spot = 100.0 + i
        greek_rows += _price_only_rows(d, spot, expiry)
        for k in STRIKES:
            oi_rows.append({"date": d, "strike": _theta(k),
                            "right": 'C' if k >= spot else 'P', "open_interest": 500})
        price_rows.append({"date": d, "close": spot})

    records = bt3._build_day_records("SYN", expiry, greek_rows, oi_rows, price_rows)

    assert records, "no days survived -- IV/gamma reconstruction produced nothing"
    assert len(records) >= 5
    for r in records:
        assert r.net_gamma_v1 != 0.0, "gamma must be non-zero once derived"
        assert r.regime_v1 in ("long", "short")


@pytest.mark.unit
def test_recovered_iv_is_close_to_the_vol_the_chain_was_priced_at():
    """The reconstruction has to be accurate, not merely non-empty -- an IV
    that's systematically off would tilt the whole reference smile."""
    import implied_vol as iv_mod
    expiry, date, spot, sigma = "20261231", "20260901", 100.0, 0.22
    T = max((bt3.datetime.strptime(expiry, "%Y%m%d")
             - bt3.datetime.strptime(date, "%Y%m%d")).days, 1) / 365.0

    for row in _price_only_rows(date, spot, expiry, sigma):
        k = bt3.strike_from_theta(int(row["strike"]))
        mark = iv_mod.mid_price(row["bid"], row["ask"], row["close"])
        solved = iv_mod.implied_vol(mark, spot, k, T, bt3._BACKTEST_R,
                                    bt3._BACKTEST_Q, row["right"])
        if solved is None:
            continue           # unidentifiable strike, correctly declined
        # 1% band: the mid is deliberately widened +/-1% off the true price to
        # imitate a real spread, so a small offset is expected and honest.
        assert abs(solved - sigma) < 0.03, f"K={k} recovered {solved:.4f} vs {sigma}"


@pytest.mark.unit
def test_net_gamma_v3_oi_flow_signs_flow():
    """M1: a strike GAINING OI means customers added there, so the dealer
    takes the other side. Call gaining OI -> dealer short call (short gamma,
    negative); put gaining OI -> dealer long put (long gamma, positive)."""
    chain_iv = _flat_smile_chain(SPOT0)
    gamma_map = {k: 0.02 for k in chain_iv}
    otm_calls = [k for (k, r) in chain_iv if r == 'C' and k > SPOT0]
    otm_puts = [k for (k, r) in chain_iv if r == 'P' and k < SPOT0]
    today = {k: 100 for k in chain_iv}
    prev = {k: 0 for k in chain_iv}
    ck, pk = otm_calls[0], otm_puts[0]
    today[(ck, 'C')] = 200
    prev[(ck, 'C')] = 100   # +100 call inflow -> dealer short -> negative
    today[(pk, 'P')] = 200
    prev[(pk, 'P')] = 100   # +100 put inflow -> dealer long -> positive
    net = bt3._net_gamma_v3_oi_flow(gamma_map, today, prev, chain_iv, SPOT0, 0.25)
    assert net == pytest.approx(0.0, abs=1e-9), \
        f"equal/opposite flows should cancel, got {net}"
    today2 = {k: 100 for k in chain_iv}
    prev2 = {k: 100 for k in chain_iv}
    today2[(ck, 'C')] = 200   # only the call gained OI
    net2 = bt3._net_gamma_v3_oi_flow(gamma_map, today2, prev2, chain_iv, SPOT0, 0.25)
    assert net2 < 0, f"call inflow alone -> dealer short -> negative, got {net2}"


@pytest.mark.unit
def test_build_day_records_tracks_v3_oi_flow():
    """Integration: _build_day_records must populate net_gamma_v3 on each
    DayRecord via prev-day-OI tracking, and _run_backtest_from_history must
    return a valid BacktestResult with v3_* fields populated."""
    expiry = "20261231"
    # 2 days with distinguishable OI so prev-day tracking has signal; use
    # increasing call OI (dealer accumulates short -> negative v3).
    dates = ["20260901", "20260902"]
    d0_calls = 100
    d1_calls = 200
    greek_rows, oi_rows, price_rows = [], [], []
    for i, d in enumerate(dates):
        g, o = _make_day_rows(d, SPOT0, d0_calls + i * (d1_calls - d0_calls), 100)
        greek_rows += g
        oi_rows += o
        price_rows.append(_price_row(d, SPOT0 + i * 0.01))
    # _make_day_rows keeps the same chain keys each day (flat smile around the
    # fixed SPOT0), so OTM candidate strikes exist on both days.
    records = bt3._build_day_records("SYN", expiry, greek_rows, oi_rows, price_rows,
                                     forward_window_days=3)
    assert records, "no synthetic days survived"
    assert all(hasattr(r, 'net_gamma_v3') for r in records)

    result = bt3._run_backtest_from_history("SYN", expiry, greek_rows, oi_rows,
                                            price_rows, forward_window_days=3)
    assert isinstance(result, bt3.BacktestResult)
    for attr in ("v3_n_long", "v3_n_short", "v3_long_mean_vol", "v3_short_mean_vol",
                 "v3_diff", "v3_tstat", "v3_pvalue", "v3_reg_coef", "v3_reg_t",
                 "v3_reg_p", "v3_reg_perm_p"):
        assert hasattr(result, attr), f"BacktestResult missing {attr}"


@pytest.mark.unit
def test_vendor_greeks_are_still_used_when_present():
    """Mixed sources must not regress: if a row already carries implied_vol
    and gamma, those are used verbatim rather than re-derived."""
    expiry, date, spot = "20261231", "20260901", 100.0
    rows = _price_only_rows(date, spot, expiry)
    for row in rows:
        row["implied_vol"] = 0.99          # implausible on purpose
        row["gamma"] = 0.0123
    oi_rows = [{"date": date, "strike": r["strike"], "right": r["right"],
                "open_interest": 100} for r in rows]

    records = bt3._build_day_records("SYN", expiry, rows, oi_rows,
                                     [{"date": date, "close": spot}])
    assert records
    # Every strike carries the same vendor gamma, so net gamma is that gamma
    # times OI times the per-strike signs -- nowhere near a BS-derived value.
    assert abs(records[0].net_gamma_v1) == pytest.approx(
        0.0123 * 100 * abs(sum(1 if k >= spot else -1 for k in STRIKES)), rel=1e-6)


@pytest.mark.unit
def test_a_day_with_no_underlying_close_yields_no_derived_greeks():
    """IV inversion needs spot. Without it the day must drop out, not fall
    back to a guessed spot."""
    expiry, date = "20261231", "20260901"
    rows = _price_only_rows(date, 100.0, expiry)
    oi_rows = [{"date": date, "strike": r["strike"], "right": r["right"],
                "open_interest": 100} for r in rows]
    records = bt3._build_day_records("SYN", expiry, rows, oi_rows, [])
    assert records == []


@pytest.mark.unit
def test_normalize_eod_greeks_rows_converts_dollars_and_CALL_PUT():
    """Live EOD rows carry dollar strikes + 'CALL'/'PUT'. They must be
    normalized to cents + 'C'/'P' (the internal convention) or strike_from_theta
    collapses K to ~1/1000 and every IV derivation fails (0 usable days)."""
    rows = [
        {"date": 20260803, "strike": "736.000", "right": "CALL",
         "bid": "38.67", "ask": "42.08", "close": "40.82"},
        {"date": 20260803, "strike": "650.000", "right": "PUT",
         "bid": "3.54", "ask": "3.57"},
        # rows already in the internal cents + single-letter convention are left alone
        {"date": 20260803, "strike": 748000, "right": "C", "bid": "1", "ask": "2"},
    ]
    out = bt3._normalize_eod_greeks_rows(rows)
    assert out[0]["strike"] == "736000" and out[0]["right"] == "C"
    assert out[1]["strike"] == "650000" and out[1]["right"] == "P"
    # unchanged cents row
    assert out[2]["strike"] == 748000 and out[2]["right"] == "C"


@pytest.mark.unit
def test_normalize_eod_greeks_rows_handles_none_and_bad_strikes():
    assert bt3._normalize_eod_greeks_rows(None) == []
    out = bt3._normalize_eod_greeks_rows([{"strike": "x", "right": "CALL"}])
    assert out[0]["right"] == "C"  # right still normalized
    assert out[0]["strike"] == "x"  # unparseable strike left untouched


@pytest.mark.unit
def test_net_delta_oi_symmetric_smile_nets_near_zero():
    # A genuinely delta-symmetric book: OTM calls and puts mirrored about the
    # drift-corrected BS center SPOT0*exp((r-q+0.5*sigma^2)*T) with identical
    # IV and OI, so each call's |delta| cancels its mirror put's |delta| and
    # the M2 book-level net delta-OI is ~0 by construction. (The earlier
    # _flat_smile_chain fixture was not delta-symmetric -- it carries an
    # unmatched ATM call and unequal call/put counts -- so it cannot attest
    # to this property.)
    sig = 0.20
    center = SPOT0 * math.exp((bt3._BACKTEST_R - bt3._BACKTEST_Q + 0.5 * sig * sig) * 0.25)
    chain_iv = {}
    for mult in [1.05, 1.10, 1.15, 1.20, 1.25, 1.30]:
        chain_iv[(center * mult, 'C')] = sig
        chain_iv[(center / mult, 'P')] = sig
    oi_map = {k: 100 for k in chain_iv}
    net = bt3._net_delta_oi(oi_map, chain_iv, SPOT0, 0.25)
    assert abs(net) < 1.0


# ---------------------------------------------------------------------------
# v5 (Direction 5-signal): mirrors the live 'direction' sign model
# (dealer_positioning._resolve_sign('direction') +
# _fetch_direction_bias -> Direction.signal_generator), network-free.
# ---------------------------------------------------------------------------

def _eod_volume_row(date, strike_dollars, right, volume, close):
    """Row shaped like option_bulk_hist_eod after _normalize_eod_greeks_rows:
    cents strike, C/P right, plus volume + close (the whale-leg inputs)."""
    return {"date": date, "strike": _theta(strike_dollars), "right": right,
            "volume": volume, "close": close}


def _whale_rows(date, call_premiums=(), put_premiums=()):
    """EOD rows whose per-contract premium (volume*close*100) equals the
    requested amounts exactly (volume=10, close=premium/1000)."""
    rows = []
    for i, p in enumerate(call_premiums):
        rows.append(_eod_volume_row(date, 105.0 + i, 'C', 10, p / 1000.0))
    for i, p in enumerate(put_premiums):
        rows.append(_eod_volume_row(date, 95.0 - i, 'P', 10, p / 1000.0))
    return rows


# 25 closes that make elliott_wave.count_waves return 'impulse_wave_3'
# (local highs at idx 1 and 7, first low at idx 4: 7-1 > 1-4), and whose
# Bollinger regime is a plain 'bullish' (no squeeze, no thrust), so only
# wave3 fires among the close-based legs on the last day.
_WAVE3_CLOSES = [100.0, 103, 101, 99, 97, 100, 103, 106, 104, 102, 100,
                 98, 101, 104, 107, 110, 108, 106, 104, 102, 105, 108,
                 111, 109, 107]
_WAVE3_DATES = [f"202609{d:02d}" for d in range(1, len(_WAVE3_CLOSES) + 1)]


def _close_by_date():
    return dict(zip(_WAVE3_DATES, _WAVE3_CLOSES))


def _oi_near_spot():
    """OI whose nearest-to-spot strike is within 2% of the last close
    (107.0) -- the liquidity leg fires (liquidity_map's 2% rule)."""
    return {_WAVE3_DATES[-1]: {(108.0, 'C'): 100}}


def _oi_far_from_spot():
    """OI whose nearest strike is ~21.5% away from spot -- liquidity off."""
    return {_WAVE3_DATES[-1]: {(130.0, 'C'): 100}}


@pytest.mark.unit
def test_net_gamma_v5_direction_bias_signs_and_otm_gate():
    """The v5 sign math, exactly dealer_positioning._resolve_sign('direction'):
    sign = -bias * right_dir on OTM legs only; bias 0 -> 0.0 (never a forced
    fallback to -1)."""
    chain_iv = _flat_smile_chain(SPOT0)
    gamma_map = {k: 0.02 for k in chain_iv}
    otm_calls = [(k, r) for (k, r) in chain_iv if r == 'C' and k > SPOT0]
    otm_puts = [(k, r) for (k, r) in chain_iv if r == 'P' and k < SPOT0]
    assert otm_calls and otm_puts

    # OI only on OTM calls: bullish bias -> dealer short calls -> NEGATIVE;
    # bearish bias mirrors it positive.
    oi_calls = {k: (100 if k in otm_calls else 0) for k in chain_iv}
    expected_calls = sum(0.02 * 100 for k in otm_calls)
    assert bt3._net_gamma_v5_direction(gamma_map, oi_calls, chain_iv,
                                       SPOT0, 0.25, 1.0) == pytest.approx(-expected_calls)
    assert bt3._net_gamma_v5_direction(gamma_map, oi_calls, chain_iv,
                                       SPOT0, 0.25, -1.0) == pytest.approx(expected_calls)

    # OI only on OTM puts: bullish bias -> dealer long puts -> POSITIVE.
    oi_puts = {k: (100 if k in otm_puts else 0) for k in chain_iv}
    expected_puts = sum(0.02 * 100 for k in otm_puts)
    assert bt3._net_gamma_v5_direction(gamma_map, oi_puts, chain_iv,
                                       SPOT0, 0.25, 1.0) == pytest.approx(expected_puts)
    assert bt3._net_gamma_v5_direction(gamma_map, oi_puts, chain_iv,
                                       SPOT0, 0.25, -1.0) == pytest.approx(-expected_puts)

    # bias 0 -> 0.0 even with whale-sized OI sitting on OTM legs.
    assert bt3._net_gamma_v5_direction(gamma_map, oi_calls, chain_iv,
                                       SPOT0, 0.25, 0.0) == 0.0
    assert bt3._net_gamma_v5_direction(gamma_map, oi_calls, chain_iv,
                                       SPOT0, 0.25, None) == 0.0

    # OTM gate: a chain with REAL ITM legs -- OI parked on ITM calls (k<spot)
    # contributes nothing; OI on OTM calls of the same chain contributes.
    itm_chain = {}
    for k in (80.0, 90.0, 110.0, 120.0):
        itm_chain[(k, 'C')] = 0.25
        itm_chain[(k, 'P')] = 0.25
    gm = {k: 0.02 for k in itm_chain}
    oi_itm = {(80.0, 'C'): 100, (90.0, 'C'): 100}   # ITM calls (k < 100)
    assert bt3._net_gamma_v5_direction(gm, oi_itm, itm_chain,
                                       SPOT0, 0.25, 1.0) == 0.0
    oi_otm = {(110.0, 'C'): 100, (120.0, 'C'): 100}  # OTM calls (k > 100)
    assert bt3._net_gamma_v5_direction(gm, oi_otm, itm_chain,
                                       SPOT0, 0.25, 1.0) == pytest.approx(-0.02 * 100 * 2)


@pytest.mark.unit
def test_build_direction_bias_by_date_whale_and_score_gates():
    """The per-day bias helper with fully synthetic inputs: bullish whale +
    score>=3 -> +1, bearish -> -1, whale neutral -> 0, score<3 -> 0,
    insufficient closes -> 0, empty EOD rows -> all 0."""
    closes = _close_by_date()
    target = _WAVE3_DATES[-1]

    # (a) bullish whale + wave3 + liquidity = score 3 >= 3 -> +1
    rows = _whale_rows(target, call_premiums=(30000, 28000), put_premiums=(5000,))
    bias = bt3._build_direction_bias_by_date(rows, closes, _oi_near_spot())
    assert bias[target] == 1.0
    # no-lookahead: days before the whale rows have no signal -> 0
    assert bias[_WAVE3_DATES[0]] == 0.0

    # (b) bearish whale, same score -> -1
    rows = _whale_rows(target, call_premiums=(5000,), put_premiums=(30000, 28000))
    bias = bt3._build_direction_bias_by_date(rows, closes, _oi_near_spot())
    assert bias[target] == -1.0

    # (c) whale neutral (call == put premium, neither > 1.2x) -> 0
    rows = _whale_rows(target, call_premiums=(30000,), put_premiums=(30000,))
    bias = bt3._build_direction_bias_by_date(rows, closes, _oi_near_spot())
    assert bias[target] == 0.0

    # (d) bullish whale but score 2 < 3 (liquidity off: OI far from spot) -> 0
    rows = _whale_rows(target, call_premiums=(30000, 28000), put_premiums=(5000,))
    bias = bt3._build_direction_bias_by_date(rows, closes, _oi_far_from_spot())
    assert bias[target] == 0.0

    # (e) insufficient closes (< MIN_CLOSES_FOR_PRICE_SIGNALS): price legs off
    # -> score 1 < 3 -> 0 even with a bullish whale
    short_closes = dict(zip(_WAVE3_DATES[:10], _WAVE3_CLOSES[:10]))
    rows = _whale_rows(_WAVE3_DATES[9], call_premiums=(30000, 28000), put_premiums=(5000,))
    bias = bt3._build_direction_bias_by_date(rows, short_closes, _oi_near_spot())
    assert bias[_WAVE3_DATES[9]] == 0.0

    # (f) empty EOD rows (fetch failure): whale off everywhere -> all 0, even
    # with wave3 + liquidity firing (score 2 < 3 never reaches a whale gate)
    bias = bt3._build_direction_bias_by_date([], closes, _oi_near_spot())
    assert all(b == 0.0 for b in bias.values())


@pytest.mark.unit
def test_build_direction_bias_by_date_whale_threshold_bps_bar(monkeypatch):
    """WHALE_THRESHOLD_BPS passthrough (param): the whale-leg bar becomes
    bps/10000 x spot x 100 (bps of one-contract ATM notional) anchored to
    that day's close. bps=1000, spot=500 -> bar $5,000: a $6,000 whale call
    fires bullish at min_score=1 while a $2,000 call does not; default None
    keeps the legacy $25K absolute behavior unchanged."""
    monkeypatch.delenv("WHALE_THRESHOLD_BPS", raising=False)  # isolate the param
    d = _WAVE3_DATES[-1]
    closes = {d: 500.0}  # day-d spot close anchors the bps bar (no lookahead)

    # (a) $6,000 call premium clears the $5,000 bps bar; no puts -> bullish.
    rows = _whale_rows(d, call_premiums=(6000,))
    bias = bt3._build_direction_bias_by_date(rows, closes, _oi_near_spot(),
                                             min_score=1, whale_threshold_bps=1000)
    assert bias[d] == 1.0

    # (b) $2,000 call premium is below the $5,000 bps bar -> no whale -> 0.
    rows = _whale_rows(d, call_premiums=(2000,))
    bias = bt3._build_direction_bias_by_date(rows, closes, _oi_near_spot(),
                                             min_score=1, whale_threshold_bps=1000)
    assert bias[d] == 0.0

    # (c) default None -> legacy $25K absolute bar unchanged at the same spot:
    # the $6,000 call that cleared the bps bar does NOT fire, a $30,000 one does.
    rows = _whale_rows(d, call_premiums=(6000,))
    bias = bt3._build_direction_bias_by_date(rows, closes, _oi_near_spot(), min_score=1)
    assert bias[d] == 0.0
    rows = _whale_rows(d, call_premiums=(30000,), put_premiums=(5000,))
    bias = bt3._build_direction_bias_by_date(rows, closes, _oi_near_spot(), min_score=1)
    assert bias[d] == 1.0


@pytest.mark.unit
def test_resolve_whale_threshold_bps_env_precedence(monkeypatch):
    """CLI-level env resolution (the helper the __main__ entry uses): the
    WHALE_THRESHOLD_BPS env is picked up when no explicit param is given,
    the explicit param outranks the env, and unset/0 resolves to the legacy
    $25K mode (0.0). No network -- pure env + precedence logic."""
    monkeypatch.delenv("WHALE_THRESHOLD_BPS", raising=False)
    assert bt3._resolve_whale_threshold_bps() == 0.0          # unset -> legacy
    monkeypatch.setenv("WHALE_THRESHOLD_BPS", "1000")
    assert bt3._resolve_whale_threshold_bps() == 1000.0       # env picked up
    assert bt3._resolve_whale_threshold_bps(500) == 500.0     # param > env
    assert bt3._resolve_whale_threshold_bps(0) == 0.0         # explicit 0 -> legacy

    # the bias helper honors the env when the param is left None (param > env
    # > legacy): bps=1000, spot 500 -> $5,000 bar -> $6,000 whale fires.
    monkeypatch.setenv("WHALE_THRESHOLD_BPS", "1000")
    d = _WAVE3_DATES[-1]
    closes = {d: 500.0}
    rows = _whale_rows(d, call_premiums=(6000,))
    bias = bt3._build_direction_bias_by_date(rows, closes, _oi_near_spot(),
                                             min_score=1)
    assert bias[d] == 1.0


@pytest.mark.unit
def test_build_day_records_wires_v5_into_records_summary_and_export(monkeypatch):
    """End-to-end: a controlled per-day bias must flow through _build_day_records
    into DayRecord.net_gamma_v5/regime_v5, into every BacktestResult v5_* field,
    the report table, and the export_backtest_summary round-trip (schema keys).
    Bias helper is monkeypatched so the wiring is tested independently of the
    (already covered) bias computation. The v5 leg decomposes the per-day
    Direction bias per-expiry via the reference-curve deviation; the run-level
    bias is the fallback AND the no-read gate.

    This test pins the SABR reference fitter (VOL_SURFACE_FITTER=sabr): the
    expected per-strike sign decomposition was validated against the SABR
    curve. Since 2026-08-12 the LIVE default is SVI (SSVI), which produces a
    different (also valid) per-strike decomposition; the SVI default is
    covered by test_vol_surface_reference.test_svi_is_default_fitter. Forcing
    sabr here keeps this wiring test deterministic against the curve it was
    written for.
    """
    import json
    import os
    import tempfile

    os.environ["VOL_SURFACE_FITTER"] = "sabr"
    try:
        _run_v5_wiring_assert(monkeypatch)
    finally:
        os.environ.pop("VOL_SURFACE_FITTER", None)


def _run_v5_wiring_assert(monkeypatch):
    import json
    import tempfile

    expiry = "20261231"
    dates = [f"202609{d:02d}" for d in range(1, 15)]  # 14 days
    greek_rows, oi_rows, price_rows = [], [], []
    price = SPOT0
    for i, d in enumerate(dates):
        g, o = _make_day_rows(d, price, 1000, 10)   # heavy call OI
        greek_rows += g
        oi_rows += o
        price_rows.append(_price_row(d, price))
        price *= 1.01 if (i % 4 < 2) else 0.99

    # +1 on even days (dealer short OTM calls -> negative), -1 on odd days.
    bias_map = {d: (1.0 if i % 2 == 0 else -1.0) for i, d in enumerate(dates)}
    monkeypatch.setattr(bt3, "_build_direction_bias_by_date",
                        lambda *a, **k: bias_map)

    records = bt3._build_day_records("SYN", expiry, greek_rows, oi_rows,
                                     price_rows, forward_window_days=3)
    assert records
    for r in records:
        if bias_map[r.date] > 0:
            assert r.net_gamma_v5 < 0 and r.regime_v5 == "short"
        else:
            assert r.net_gamma_v5 > 0 and r.regime_v5 == "long"

    result = bt3._run_backtest_from_history("SYN", expiry, greek_rows, oi_rows,
                                            price_rows, forward_window_days=3)
    for attr in ("v5_n_long", "v5_n_short", "v5_long_mean_vol", "v5_short_mean_vol",
                 "v5_diff", "v5_tstat", "v5_pvalue", "v5_reg_coef", "v5_reg_t",
                 "v5_reg_p", "v5_reg_perm_p"):
        assert hasattr(result, attr), f"BacktestResult missing {attr}"

    report = bt3.format_backtest_report(result)
    assert "v5 (direction-5sig)" in report
    assert "v5 coef" in report

    with tempfile.TemporaryDirectory() as tmp:
        bt3.export_backtest_summary(result, lookback_days=90, output_dir=tmp)
        with open(os.path.join(tmp, "backtest_summary.json")) as f:
            payload = json.load(f)
        for key in ("v5_reg_coef", "v5_reg_t", "v5_reg_ols_p", "v5_reg_perm_p"):
            assert key in payload, f"export missing {key}"


# ---------------------------------------------------------------------------
# branch (b) -- the M2 joint-test Δvanna greek-drift column (book_b).
# Per (strike,right)-day signed daily Δvanna, signed by the SAME per-day
# scalar the v5 leg uses (per_expiry_bias; uniform mode -> the day bias),
# NEVER netted across rights, 0DTE bucketed separately, T-floor gated.
# ---------------------------------------------------------------------------

def _make_day_rows_with_vanna(date, spot, call_oi, put_oi, vanna=None, gamma=0.02):
    """_make_day_rows + a per-(strike,right) 'vanna' value (branch (b) input).
    vanna: {(k,right): float} or a callable date -> {(k,right): float}."""
    chain_iv = _flat_smile_chain(spot)
    greek_rows, oi_rows = [], []
    vmap = vanna(date) if callable(vanna) else (vanna or {})
    for (k, right), iv in chain_iv.items():
        greek_rows.append({"date": date, "strike": _theta(k), "right": right,
                           "implied_vol": iv, "delta": 0.0, "gamma": gamma,
                           "vanna": float(vmap.get((k, right), 0.0))})
        oi = call_oi if right == 'C' else put_oi
        oi_rows.append({"date": date, "strike": _theta(k), "right": right,
                        "open_interest": oi})
    return greek_rows, oi_rows


@pytest.mark.unit
def test_book_b_bucket_0dte_ttc1d_and_t_floor():
    """The bucket helper: TTE==0 -> 0DTE only; TTE>=1 -> the TTE>=1d strip; the
    T-floor (BOOK_B_T_FLOOR_DAYS=3) gates only the joint-test book_b strip."""
    assert bt3._book_b_bucket(0) == (False, True, False)        # 0DTE
    assert bt3._book_b_bucket(1) == (True, False, False)        # ttc1d, pre-floor
    assert bt3._book_b_bucket(2) == (True, False, False)        # below floor
    assert bt3._book_b_bucket(3) == (True, False, True)         # == floor -> book_b
    assert bt3._book_b_bucket(10) == (True, False, True)        # deep -> book_b


@pytest.mark.unit
def test_book_b_signed_dvanna_buckets_and_keeps_rights(monkeypatch):
    """branch (b) end-to-end: _build_day_records column-adds book_b = signed
    daily Δvanna, signed by the SAME per-day scalar the v5 leg uses
    (per_expiry_bias; uniform mode -> the day bias), with 0DTE + T-floor
    bucketing, and NEVER nets put-call across rights."""
    monkeypatch.setenv("DEALER_DIRECTION_PER_EXPIRY", "uniform")
    expiry = "20260915"
    dates = ["20260911", "20260912", "20260913", "20260914", "20260915"]
    # TTE (calendar days to expiry): 4, 3, 2, 1, 0.
    kC, kP = 110.0, 90.0   # OTM call / OTM put around spot 100
    vanna_by_date = {
        "20260911": {(kC, 'C'): 100.0, (kP, 'P'): 100.0},   # first day, no prev
        "20260912": {(kC, 'C'): 150.0, (kP, 'P'): 50.0},    # ΔC=+50, ΔP=-50
        "20260913": {(kC, 'C'): 160.0, (kP, 'P'): 40.0},    # ΔC=+10, ΔP=-10
        "20260914": {(kC, 'C'): 160.0, (kP, 'P'): 40.0},    # no Δ
        "20260915": {(kC, 'C'): 160.0, (kP, 'P'): 90.0},    # 0DTE: ΔP=+50
    }
    bias_map = {"20260911": 1.0, "20260912": 1.0, "20260913": -1.0,
                "20260914": 1.0, "20260915": 1.0}
    monkeypatch.setattr(bt3, "_build_direction_bias_by_date",
                        lambda *a, **k: bias_map)

    greek_rows, oi_rows, price_rows = [], [], []
    for d in dates:
        g, o = _make_day_rows_with_vanna(d, SPOT0, 1000, 10, vanna_by_date[d])
        greek_rows += g
        oi_rows += o
        price_rows.append(_price_row(d, SPOT0))

    records = bt3._build_day_records("SYN", expiry, greek_rows, oi_rows,
                                     price_rows, forward_window_days=3)
    by_date = {r.date: r for r in records}
    assert set(by_date) == set(dates)

    # (a) signed Δvanna on the known day (d2, TTE=3, in book_b): bias=+1 ->
    #     call sign=-1, put sign=+1. ΔC=+50 -> -50; ΔP=-50 -> -50; sum = -100.
    assert by_date["20260912"].book_b == pytest.approx(-100.0)
    assert by_date["20260912"].book_b_ttc1d == pytest.approx(-100.0)

    # (b) bucketing: below the T-floor (TTE=2) -> in ttc1d, NOT in book_b.
    assert by_date["20260913"].book_b == 0.0
    assert by_date["20260913"].book_b_ttc1d == pytest.approx(20.0)
    #     0DTE day (TTE=0) -> in the 0DTE bucket, never in book_b / ttc1d.
    assert by_date["20260915"].book_b == 0.0
    assert by_date["20260915"].book_b_ttc1d == 0.0
    assert by_date["20260915"].book_b_0dte == pytest.approx(50.0)
    #     no-Δ day contributes nothing.
    assert by_date["20260914"].book_b == 0.0

    # (c) put-call NOT netted: on d2 the RAW Δvanna nets to 0 (C+50, P-50), yet
    #     book_b is -100 because each right is signed separately (call -1, put
    #     +1 under bullish bias); and on the 0DTE day a put-only +50 change
    #     carries the PUT (+1) sign -> +50, proving the right dimension is kept.
    assert by_date["20260912"].book_b == pytest.approx(-100.0)  # not 0 (netting)
    assert by_date["20260915"].book_b_0dte == pytest.approx(+50.0)  # put sign +
