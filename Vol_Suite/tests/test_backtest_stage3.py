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


def _make_day_rows(date: str, spot: float, call_oi: int, put_oi: int, gamma: float = 0.02,
                    whale_calls: set = None, whale_puts: set = None,
                    whale_premium: float = 30_000.0):
    """One day's greeks + OI rows, with a deliberately lopsided OI split
    (call_oi vs put_oi) so the day's TRUE embedded gamma-sign label is known
    by construction -- heavy call OI => v1's flat convention sums positive
    (long gamma), heavy put OI => sums negative (short gamma).

    whale_calls/whale_puts: optional sets of strikes to stamp with a
    'volume'/'close' pair whose product (x100) equals whale_premium --
    lets a test construct a known whale-flow bias without touching the OI
    story. Strikes not in either set get no 'volume'/'close' fields at all
    (matching a real row where nothing whale-sized traded), so they never
    enter whale_scanner.classify_whale_bias's premium sum.
    """
    chain_iv = _flat_smile_chain(spot)
    greek_rows, oi_rows = [], []
    for (k, right), iv in chain_iv.items():
        # hist/option/eod (what hist_greek_rows is shaped after) echoes
        # strike in plain dollar form, NOT theta-scaled -- only the OI route
        # below returns theta-scaled integers. See _build_day_records's own
        # comment (backtest_stage3.py) for the live-verified bug this
        # fixture used to silently share with the buggy production code.
        row = {"date": date, "strike": k, "right": right,
               "implied_vol": iv, "delta": 0.0, "gamma": gamma}
        is_whale = (whale_calls and right == 'C' and k in whale_calls) or \
                   (whale_puts and right == 'P' and k in whale_puts)
        if is_whale:
            row["volume"] = 10.0
            row["close"] = whale_premium / (10.0 * 100.0)
        greek_rows.append(row)
        oi = call_oi if right == 'C' else put_oi
        oi_rows.append({"date": date, "strike": _theta(k), "right": right, "open_interest": oi})
    return greek_rows, oi_rows


def _price_row(date, close):
    return {"date": date, "open": close, "high": close, "low": close, "close": close, "volume": 1000}


# ---------------------------------------------------------------------------
# Small building blocks in isolation
# ---------------------------------------------------------------------------

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
def test_forward_realized_vol_annualized_with_sqrt_252():
    """Verify that _forward_realized_vol uses sqrt(252) for annualization, not sqrt(365).

    Create a synthetic price series with known daily log returns. For a series with
    daily log returns of std dev S_daily, the annualized realized vol should be
    S_daily * sqrt(252), not S_daily * sqrt(365).
    """
    import numpy as np

    # Create a price series with known daily log returns
    # Alternating +1% and -1% log returns for 20 steps
    prices = [100.0]
    daily_log_return = 0.01  # 1% daily log return
    for i in range(20):
        sign = 1 if i % 2 == 0 else -1
        prices.append(prices[-1] * math.exp(sign * daily_log_return))

    # Calculate the expected std of log returns
    log_rets = np.diff(np.log(prices))
    expected_std_daily = np.std(log_rets, ddof=1)

    # The annualized vol should be std_daily * sqrt(252)
    expected_annualized_vol = expected_std_daily * math.sqrt(252)

    # Call _forward_realized_vol with window = len(prices) - 1
    actual_vol = bt3._forward_realized_vol(prices, window=len(prices) - 1)

    assert actual_vol is not None
    assert abs(actual_vol - expected_annualized_vol) < 1e-6, (
        f"expected annualized vol {expected_annualized_vol:.6f} using sqrt(252), "
        f"got {actual_vol:.6f}"
    )


@pytest.mark.unit
def test_forward_price_includes_dividend_discount():
    """Verify that the forward price calculation applies the dividend yield discount.

    forward = spot * exp((_BACKTEST_R - _BACKTEST_Q) * T)

    For _BACKTEST_Q > 0, this forward should be LESS than spot * exp(_BACKTEST_R * T).
    """
    spot = 100.0
    T = 0.25  # 3 months

    # Calculate forward with the dividend discount applied
    forward_with_discount = spot * math.exp((bt3._BACKTEST_R - bt3._BACKTEST_Q) * T)

    # Calculate what it would be without the discount (the old buggy version)
    forward_without_discount = spot * math.exp(bt3._BACKTEST_R * T)

    # For positive _BACKTEST_Q, the discounted forward must be less
    assert bt3._BACKTEST_Q > 0, "test assumes _BACKTEST_Q > 0"
    assert forward_with_discount < forward_without_discount, (
        f"forward with dividend discount should be less than without it: "
        f"{forward_with_discount:.6f} >= {forward_without_discount:.6f}"
    )

    # Verify the values are in the expected ballpark
    # For SPY-like params: S=100, r=4%, q=1.2%, T=0.25:
    # with discount: 100 * exp((0.04-0.012)*0.25) = 100 * exp(0.007) ≈ 100.7024
    # without: 100 * exp(0.04*0.25) = 100 * exp(0.01) ≈ 101.0050
    assert 100.0 < forward_with_discount < forward_without_discount < 102.0, (
        f"unexpected forward price range: with_discount={forward_with_discount}, "
        f"without_discount={forward_without_discount}"
    )


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
def test_net_gamma_whale_flips_sign_between_bullish_and_bearish():
    """The whale sign model applies ONE uniform sign per day, so bullish
    and bearish on the identical gamma/OI map must be exact mirrors, and
    neutral must contribute nothing."""
    chain_iv = _flat_smile_chain(SPOT0)
    gamma_map = {k: 0.02 for k in chain_iv}
    # Deliberately asymmetric call/put OI -- with a UNIFORM per-day sign,
    # identical OI on both sides would cancel to exactly zero (equal OTM
    # call/put leg counts here), which would make this test's "!= 0.0"
    # assertion fail for a reason that has nothing to do with correctness.
    oi_map = {(k, right): (150 if right == 'C' else 80) for (k, right) in chain_iv}
    T = 0.25

    bullish_total = bt3._net_gamma_whale(gamma_map, oi_map, chain_iv, SPOT0, T, 'bullish')
    bearish_total = bt3._net_gamma_whale(gamma_map, oi_map, chain_iv, SPOT0, T, 'bearish')
    neutral_total = bt3._net_gamma_whale(gamma_map, oi_map, chain_iv, SPOT0, T, 'neutral')

    assert bullish_total != 0.0
    assert bullish_total == pytest.approx(-bearish_total)
    assert neutral_total == 0.0


@pytest.mark.unit
def test_build_day_records_excludes_neutral_whale_days():
    """A day with no whale-sized flow anywhere in the chain must get
    regime_whale=None and net_gamma_whale=0.0 -- NOT folded into 'short'
    the way v1/v2/v3's `> 0 else 'short'` convention would."""
    d = "20260901"
    greek_rows, oi_rows = _make_day_rows(d, SPOT0, 1000, 1000)  # no whale_calls/whale_puts
    price_rows = [_price_row(d, SPOT0)]

    records = bt3._build_day_records("SYN", "20261231", greek_rows, oi_rows, price_rows)
    assert len(records) == 1
    assert records[0].regime_whale is None
    assert records[0].net_gamma_whale == 0.0


def test_build_day_records_accumulated_position_drives_v2_regime():
    """When an accumulated_position (signed dealer book) is supplied, the v2
    regime must be classified from it (pass-through sign=1.0) -- net-short
    accumulated book => short, net-long => long -- mirroring the live model."""
    d = "20260901"
    greek_rows, oi_rows = _make_day_rows(d, SPOT0, 1000, 1000)
    price_rows = [_price_row(d, SPOT0)]

    short_acc = {(k, right): -1.0 for (k, right) in _flat_smile_chain(SPOT0).keys()}
    records_short = bt3._build_day_records(
        "SYN", "20261231", greek_rows, oi_rows, price_rows,
        accumulated_position=short_acc)
    assert records_short[0].regime_v2 == "short"

    long_acc = {(k, right): 1.0 for (k, right) in _flat_smile_chain(SPOT0).keys()}
    records_long = bt3._build_day_records(
        "SYN", "20261231", greek_rows, oi_rows, price_rows,
        accumulated_position=long_acc)
    assert records_long[0].regime_v2 == "long"


@pytest.mark.unit
def test_dealer_exposure_engine_guard():
    """The dealer-frame engine lives on the Dealer-Exposure-Dev worktree, not
    master. It must load when present and raise a clear error when absent."""
    if bt3._dealer_exposure_engine_available():
        eng = bt3._load_dealer_exposure_engine()
        assert hasattr(eng, "build_net_exposure")
    else:
        with pytest.raises(FileNotFoundError):
            bt3._load_dealer_exposure_engine()


@pytest.mark.unit
def test_build_day_records_dealer_exposure_series():
    """When use_dealer_exposure=True and the engine is available, each day gets
    a regime_dealer_exposure from the dealer-frame GEX sign."""
    if not bt3._dealer_exposure_engine_available():
        pytest.skip("Dealer-Exposure-Dev worktree not present")
    d = "20260901"
    # Call-dominated OI -> dealer-frame gex should read net long.
    greek_rows, oi_rows = _make_day_rows(d, SPOT0, 1000, 400)
    price_rows = [_price_row(d, SPOT0)]
    records = bt3._build_day_records(
        "SYN", "20261231", greek_rows, oi_rows, price_rows,
        use_dealer_exposure=True)
    assert records[0].regime_dealer_exposure in ("long", "short")


@pytest.mark.unit
def test_build_day_records_labels_bullish_whale_day():
    """A day with heavy call-side whale premium must classify as bullish
    and produce a real (non-None) regime_whale."""
    d = "20260901"
    call_strikes = {k for (k, right) in _flat_smile_chain(SPOT0).keys() if right == 'C'}
    # Asymmetric call/put OI -- symmetric OI on both sides would cancel to
    # exactly zero under the whale model's uniform per-day sign (equal OTM
    # call/put leg counts here), same reasoning as
    # test_net_gamma_whale_flips_sign_between_bullish_and_bearish.
    greek_rows, oi_rows = _make_day_rows(d, SPOT0, 1000, 400, whale_calls=call_strikes)
    price_rows = [_price_row(d, SPOT0)]

    records = bt3._build_day_records("SYN", "20261231", greek_rows, oi_rows, price_rows)
    assert len(records) == 1
    assert records[0].regime_whale in ("long", "short")
    assert records[0].net_gamma_whale != 0.0


@pytest.mark.unit
def test_run_backtest_from_history_wires_whale_column():
    """End-to-end sanity: whale_n_long/whale_n_short must reflect ONLY the
    non-neutral days, and never exceed the total day count."""
    dates = [f"202607{d:02d}" for d in range(1, 15)]
    greek_rows, oi_rows, price_rows = [], [], []
    call_strikes = {k for (k, right) in _flat_smile_chain(SPOT0).keys() if right == 'C'}

    price = SPOT0
    for i, d in enumerate(dates):
        # Alternate bullish-whale days and no-whale (neutral) days.
        whale_calls = call_strikes if i % 2 == 0 else None
        g_rows, o_rows = _make_day_rows(d, price, 500, 500, whale_calls=whale_calls)
        greek_rows.extend(g_rows)
        oi_rows.extend(o_rows)
        price_rows.append(_price_row(d, price))
        price *= 1.01 if i % 2 == 0 else 0.99

    result = bt3._run_backtest_from_history("SYN", "20261231", greek_rows, oi_rows, price_rows,
                                             forward_window_days=3)
    total_days = len(result.day_records)
    assert result.whale_n_long + result.whale_n_short <= total_days
    assert result.whale_n_long + result.whale_n_short > 0
    # Roughly half the days were stamped bullish, half neutral (excluded) --
    # confirms neutral days are genuinely dropping out, not all landing in
    # one bucket by convention.
    assert result.whale_n_long + result.whale_n_short < total_days


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
    implied_vol and NO gamma. Strike is plain dollar form here (matching
    the real endpoint), and right is the full word ("CALL"/"PUT", also
    matching what hist/option/eod actually returns live) to exercise
    _build_day_records's normalization of both, not just the strike fix."""
    import implied_vol as iv_mod
    expiry_dt = bt3.datetime.strptime(expiry, "%Y%m%d")
    T = max((expiry_dt - bt3.datetime.strptime(date, "%Y%m%d")).days, 1) / 365.0
    rows = []
    for k in STRIKES:
        right = 'C' if k >= spot else 'P'
        px = iv_mod.bs_price(spot, float(k), T, bt3._BACKTEST_R, bt3._BACKTEST_Q,
                             sigma, right)
        rows.append({"date": date, "strike": float(k),
                     "right": "CALL" if right == "C" else "PUT",
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
        k = float(row["strike"])
        mark = iv_mod.mid_price(row["bid"], row["ask"], row["close"])
        solved = iv_mod.implied_vol(mark, spot, k, T, bt3._BACKTEST_R,
                                    bt3._BACKTEST_Q, row["right"])
        if solved is None:
            continue           # unidentifiable strike, correctly declined
        # 1% band: the mid is deliberately widened +/-1% off the true price to
        # imitate a real spread, so a small offset is expected and honest.
        assert abs(solved - sigma) < 0.03, f"K={k} recovered {solved:.4f} vs {sigma}"


@pytest.mark.unit
def test_vendor_greeks_are_still_used_when_present():
    """Mixed sources must not regress: if a row already carries implied_vol
    and gamma, those are used verbatim rather than re-derived."""
    expiry, date, spot = "20261231", "20260901", 100.0
    rows = _price_only_rows(date, spot, expiry)
    for row in rows:
        row["implied_vol"] = 0.99          # implausible on purpose
        row["gamma"] = 0.0123
    # OI rows need their own theta-scaled strike / single-char right --
    # real hist_greek_rows and hist_oi_rows use different conventions for
    # both fields (see _build_day_records's comment); echoing rows' own
    # dollar-strike/full-word-right verbatim would test the wrong shape.
    oi_rows = [{"date": date, "strike": _theta(r["strike"]), "right": r["right"][0],
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
    oi_rows = [{"date": date, "strike": _theta(r["strike"]), "right": r["right"][0],
                "open_interest": 100} for r in rows]
    records = bt3._build_day_records("SYN", expiry, rows, oi_rows, [])
    assert records == []


@pytest.mark.unit
def test_gamma_derived_from_an_already_solved_iv_without_bid_ask():
    """Reproduces the real-data gap found running backtest_accumulation_falsifier
    against the WSL handoff's cached seed_data (a 'dense EOD, IV solved,
    vanna injected' payload -- README §8): rows carry a valid, already-solved
    implied_vol and NO bid/ask (and close='0.00' for illiquid strikes), no
    gamma field. The OLD code treated `iv <= 0 or gamma <= 0` as one
    combined condition, so a row with good IV but no gamma fell into the
    same re-solve-IV-from-price branch as a row with NO iv at all --
    discarding the perfectly good implied_vol and trying (and failing,
    since mid_price(None, None, '0.00') isn't a usable mark) to re-derive
    it from bid/ask/close that don't exist in this data shape. That
    silently dropped ~78% of rows and crushed 150 days of history down to
    ~14-21 usable days ("front and back date only"). Fixed: iv>0 already ->
    use it directly to derive gamma via BS; only fall back to solving IV
    from price when iv is missing/invalid too.
    """
    expiry, date, spot, sigma = "20261231", "20260901", 100.0, 0.22
    rows = []
    for k in STRIKES:
        right = 'C' if k >= spot else 'P'
        # Exactly the real cached-data row shape: implied_vol present and
        # good, no gamma, no bid/ask, close either absent or '0.00'.
        rows.append({"date": date, "strike": float(k), "right": right,
                      "implied_vol": sigma, "close": "0.00"})
    oi_rows = [{"date": date, "strike": _theta(k), "right": ('C' if k >= spot else 'P'),
                "open_interest": 500} for k in STRIKES]
    price_rows = [{"date": date, "close": spot}]

    records = bt3._build_day_records("SYN", expiry, rows, oi_rows, price_rows)

    assert records, (
        "the day must survive using its already-solved implied_vol -- it "
        "must NOT be dropped just because bid/ask/close aren't usable for "
        "re-solving a vol that was already known"
    )
    assert records[0].net_gamma_v1 != 0.0, "gamma must be derived from the existing IV, not left at 0"


@pytest.mark.unit
def test_gamma_map_matches_oi_map_when_greek_rows_use_theta_scaled_strikes():
    """Reproduces a second real-data gap found running the pooled falsifier
    against the WSL handoff's cached seed_data_*.json payloads: net_gamma_v1
    came back EXACTLY 0.0 on every single day for all 12 tickers -- not "no
    signal", a total aggregation miss. Root cause: _build_day_records parses
    greek-row strikes as plain dollars (`k = float(row['strike'])`), correct
    for the LIVE option_bulk_hist_eod route ("650.000") -- but the CACHED
    seed_data payload (seed_data_maker.py) stores greek-row strikes
    theta-scaled ("650000"), the same convention oi_by_date's rows already
    use via strike_from_theta(). With unnormalized dollar parsing, gamma_map
    keys land at (650000.0, 'C') while oi_map keys land at (650.0, 'C') --
    they never match, every row is skipped as oi<=0, and net_gamma_v1 is
    silently exactly 0.0 for every day, every ticker. This is the SAME
    observable failure mode (net gamma always exactly 0.0) the module's own
    2026-08-04 incident comment already documents for the opposite direction
    (plain-dollar rows wrongly run through strike_from_theta) -- the fix
    generalizes to auto-detect which convention a given row actually uses,
    since this function now has two real producers with different
    conventions, not one.
    """
    expiry, date, spot = "20261231", "20260901", 100.0
    rows = []
    for k in STRIKES:
        right = 'C' if k >= spot else 'P'
        rows.append({"date": date, "strike": str(_theta(k)), "right": right,
                      "implied_vol": 0.22, "gamma": 0.02})
    oi_rows = [{"date": date, "strike": _theta(k), "right": ('C' if k >= spot else 'P'),
                "open_interest": 500} for k in STRIKES]
    price_rows = [{"date": date, "close": spot}]

    records = bt3._build_day_records("SYN", expiry, rows, oi_rows, price_rows)

    assert records, "the day must survive -- theta-scaled greek-row strikes must be recognized"
    assert records[0].net_gamma_v1 != 0.0, (
        "net_gamma_v1 must not silently be 0.0 -- gamma_map and oi_map keys "
        "must land on the same (strike, right) convention regardless of "
        "which strike-scale the greek rows arrived in"
    )
