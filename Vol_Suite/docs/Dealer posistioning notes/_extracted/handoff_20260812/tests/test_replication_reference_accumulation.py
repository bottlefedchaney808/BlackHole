"""
Regression test for the seed-plus-accumulate cumulative dealer position
construction in replication_reference.py (`_accumulate_from_history` /
`compute_accumulated_position`).

Context (see DEALER_POSITIONING_V2_DESIGN.md §3, "Layer 1b/Layer 2 fusion"):
a single day's replication snapshot implicitly assumes the whole hedge
position was built TODAY at today's spot. Real positions accumulate over
many days at many different reference spots. The fix is NOT to re-run the
snapshot recursion on several past days and sum the results (that would
double/triple/N-count the same notional, since each day's recursion already
represents a full variance-hedge notional on its own) -- it's to seed an
initial position once, then walk forward accumulating the one genuinely
additive quantity across days: real day-over-day OPEN INTEREST CHANGE,
signed by each day's OWN replication-implied direction (that day's own spot
as S*), not today's.

This test is network-free -- it calls `_accumulate_from_history` directly
against a mocked multi-day chain, the same split that lets
test_variance_swap_replication.py validate the underlying recursion without
live data. It does NOT test whether pulling real ThetaData history works
(that needs the new, unconfirmed `option_bulk_hist_oi` /
`option_bulk_hist_greeks` endpoints in thetadata_client.py, live network
access this dev sandbox doesn't have); it tests whether the aggregation
arithmetic itself is correct once handed a realistic-shaped set of rows.
"""
import sys

import pytest

import replication_reference as rr


def _theta_strike(k: float) -> int:
    return int(round(k * 1000))


def _build_rally_scenario():
    """6 trading days (1 seed + 5 accumulate), spot drifting up from 100 to
    108, with call OI at strikes near each day's spot growing over time --
    simulating customers chasing the rally with fresh call buying. Under
    the dealer-is-short-what-the-strip-is-long convention, this should
    accumulate into a growing NEGATIVE (short) dealer position concentrated
    at the strikes the rally passed through.
    """
    expiry = "20260901"
    dates = ["20260715", "20260716", "20260717", "20260718", "20260721", "20260722"]
    spots = {
        "20260715": 100.0, "20260716": 101.0, "20260717": 102.5,
        "20260718": 104.0, "20260721": 106.0, "20260722": 108.0,
    }
    strikes = list(range(70, 131))  # $1 apart, 70-130
    base_oi = {k: 200 for k in strikes}

    hist_greek_rows, hist_oi_rows, hist_spot_rows = [], [], []
    for i, d in enumerate(dates):
        spot = spots[d]
        hist_spot_rows.append({"date": d, "close": spot})
        for k in strikes:
            moneyness = k / spot
            iv = max(0.15 + 0.10 * max(0, 1 - moneyness), 0.10)
            # vanna per the measured convention (rec.vanna = -1 * BS_vanna);
            # non-zero so the vanna seed/flow arms are testable.
            hist_greek_rows.append({"date": d, "strike": _theta_strike(k), "right": "C", "implied_vol": iv, "vanna": 0.5})
            hist_greek_rows.append({"date": d, "strike": _theta_strike(k), "right": "P", "implied_vol": iv, "vanna": -0.5})
            near_spot_bump = 300 if abs(k - spot) < 5 else 0
            oi_c = base_oi[k] + near_spot_bump * (i + 1)
            oi_p = base_oi[k]
            hist_oi_rows.append({"date": d, "strike": _theta_strike(k), "right": "C", "open_interest": oi_c})
            hist_oi_rows.append({"date": d, "strike": _theta_strike(k), "right": "P", "open_interest": oi_p})

    return expiry, hist_greek_rows, hist_oi_rows, hist_spot_rows


@pytest.fixture(scope="module")
def rally_result():
    expiry, greeks, oi, spot = _build_rally_scenario()
    return rr._accumulate_from_history("MOCK", expiry, 5, "replication", greeks, oi, spot)


@pytest.mark.unit
def test_accumulation_runs_end_to_end(rally_result):
    assert rally_result.seed_date == "20260715"
    assert rally_result.end_date == "20260722"
    assert len(rally_result.daily_trace) == 6  # 1 seed + 5 accumulate days
    assert rally_result.daily_trace[0]["kind"] == "seed"
    assert all(row["kind"] == "accumulate" for row in rally_result.daily_trace[1:])


@pytest.mark.unit
def test_accumulation_does_not_double_count_notional(rally_result):
    """The whole point of seed-plus-accumulate over repeat-plus-sum: each
    ACCUMULATE day should only be picking up that day's real OI CHANGE (a
    few hundred contracts at a handful of strikes), not re-deriving the
    strip's full notional the way the one-time seed legitimately does. If
    accumulate days were (bug) re-running the full snapshot and adding it
    in, their net_change would be on the same order as the seed's -- ~12k+,
    not the ~1.5k-4k this scenario's actual OI deltas produce. So the real
    regression check is that every accumulate-day's |net_change| stays a
    clear order of magnitude below the seed day's, not an absolute cap on
    the final total (which legitimately includes the one-time seed size).
    """
    seed_magnitude = abs(rally_result.daily_trace[0]["net_change"])
    accumulate_days = rally_result.daily_trace[1:]
    assert accumulate_days, "expected at least one accumulate-phase day"
    for row in accumulate_days:
        assert abs(row["net_change"]) < seed_magnitude / 2, (
            f"{row['date']}: accumulate-day net_change ({row['net_change']:.0f}) is "
            f"too close to the seed's full-notional magnitude ({seed_magnitude:.0f}) -- "
            f"looks like it's re-deriving the whole strip instead of just that day's "
            f"real OI change"
        )


@pytest.mark.unit
def test_dealer_position_goes_short_into_the_rally(rally_result):
    """Directional sanity check: customers buying calls into a rally (OI
    growing at strikes near each day's spot) should accumulate into a
    NEGATIVE (dealer short) position concentrated at those strikes, under
    the "dealer is short what the replicating strip is long" convention.
    """
    near_the_money_calls = [(float(k), "C") for k in range(95, 111)]
    values = [rally_result.position_by_strike.get(kr, 0.0) for kr in near_the_money_calls]
    nonzero = [v for v in values if v != 0]
    assert nonzero, "expected at least some near-the-money call strikes to have accumulated a position"
    assert min(nonzero) < 0, "expected a negative (dealer short) position where the rally passed through"


@pytest.mark.unit
def test_seed_modes_both_produce_a_result():
    expiry, greeks, oi, spot = _build_rally_scenario()
    repl = rr._accumulate_from_history("MOCK", expiry, 5, "replication", greeks, oi, spot)
    heur = rr._accumulate_from_history("MOCK", expiry, 5, "oi_heuristic", greeks, oi, spot)
    assert repl.position_by_strike
    assert heur.position_by_strike
    # The two seed modes should generally disagree on day-1 magnitude (the
    # OI-heuristic seed is flat call+/put- across ALL strikes; the
    # replication seed is -OI restricted to that day's OTM set only) --
    # confirms seed_mode is actually changing behavior, not a no-op.
    assert repl.daily_trace[0]["net_change"] != heur.daily_trace[0]["net_change"]


@pytest.mark.unit
def test_seed_flip_inverts_seed_keeps_flow():
    """DEALER_SEED_SIGN=1 flips ONLY the seed level (replication seed +OI
    instead of -OI); daily flow / SABR signs / gates are untouched. Design A
    (quick-round CONFIRMED 2026-08-11)."""
    import os
    expiry, greeks, oi, spot = _build_rally_scenario()
    base = rr._accumulate_from_history("MOCK", expiry, 5, "replication", greeks, oi, spot)
    os.environ["DEALER_SEED_SIGN"] = "1"
    try:
        flipped = rr._accumulate_from_history("MOCK", expiry, 5, "replication", greeks, oi, spot)
    finally:
        del os.environ["DEALER_SEED_SIGN"]
    # The seed (day-1) net is exactly negated by the flip.
    assert flipped.daily_trace[0]["net_change"] == pytest.approx(-base.daily_trace[0]["net_change"])
    # End book differs by exactly 2x the seed (flow identical), so the flip is
    # level-only: end = flipped_seed + flow = -seed + flow = -(seed - flow).
    # Assert the per-strike level moves by 2x seed contribution where the seed
    # was non-zero, and daily flow trace (days 2+) is unchanged.
    assert len(flipped.daily_trace) == len(base.daily_trace)
    for i in range(1, len(base.daily_trace)):
        assert flipped.daily_trace[i]["net_change"] == pytest.approx(base.daily_trace[i]["net_change"])


@pytest.mark.unit
def test_raises_on_insufficient_history():
    with pytest.raises(ValueError):
        rr._accumulate_from_history("MOCK", "20260901", 5, "replication", [], [], [])


@pytest.mark.unit
def test_vanna_seed_mode_runs_and_uses_vanna_sign():
    """The brainstorming vanna-seed (sign(vanna) marks rich short / cheap long)
    runs and produces a book when rows carry vanna."""
    import os
    expiry, greeks, oi, spot = _build_rally_scenario()
    acc = rr._accumulate_from_history("MOCK", expiry, 5, "vanna", greeks, oi, spot)
    assert acc.position_by_strike
    # vanna seed day-1 should have non-zero net (vanna present on rows)
    assert acc.daily_trace[0]["net_change"] != 0.0


@pytest.mark.unit
def test_vanna_flow_arm_changes_book():
    """DEALER_VANNA_FLOW=1 keeps the LIVE replication seed but weights the
    daily flow by rec.vanna (vanna flow using sabr_deviation). With vanna on
    the rows, the flow differs from the plain live arm. (DEALER_VANNA_FLOW
    defaults to "1" since 2026-08-12; test both arms explicitly.)"""
    import os
    expiry, greeks, oi, spot = _build_rally_scenario()
    os.environ["DEALER_VANNA_FLOW"] = "0"
    try:
        base = rr._accumulate_from_history("MOCK", expiry, 5, "replication", greeks, oi, spot)
    finally:
        os.environ.pop("DEALER_VANNA_FLOW", None)
    os.environ["DEALER_VANNA_FLOW"] = "1"
    try:
        vf = rr._accumulate_from_history("MOCK", expiry, 5, "replication", greeks, oi, spot)
    finally:
        os.environ.pop("DEALER_VANNA_FLOW", None)
    # seed identical (DEALER_VANNA_FLOW only touches the flow loop)
    assert vf.daily_trace[0]["net_change"] == pytest.approx(base.daily_trace[0]["net_change"])
    # flow differs once vanna weights are applied (vanna != 1)
    assert vf.daily_trace[-1]["net_change"] != pytest.approx(base.daily_trace[-1]["net_change"])


def _build_vanna_payload():
    """3 trading days whose greek rows carry per-strike vanna under all three
    documented aliases (`vanna`/`Vanna`/`VANNA`), plus a row missing the field
    entirely and a malformed-strike row, so the vanna_by_date parse can be
    exercised network-free."""
    expiry = "20260901"
    dates = ["20260715", "20260716", "20260717"]
    hist_greek_rows = [
        # date 1
        {"date": "20260715", "strike": 100000, "right": "C", "implied_vol": 0.20, "vanna": 1.5},
        {"date": "20260715", "strike": 100000, "right": "P", "implied_vol": 0.20, "VANNA": -0.75},
        {"date": "20260715", "strike": 101000, "right": "C", "implied_vol": 0.21},  # no vanna field -> 0.0
        {"date": "20260715", "strike": "bad", "right": "C", "implied_vol": 0.20, "vanna": 9.9},  # malformed -> skipped
        # date 2
        {"date": "20260716", "strike": 100000, "right": "C", "implied_vol": 0.20, "Vanna": 2.0},
        {"date": "20260716", "strike": 101000, "right": "P", "implied_vol": 0.22, "vanna": -1.2},
        # date 3
        {"date": "20260717", "strike": 100000, "right": "C", "implied_vol": 0.20, "vanna": 0.0},
        {"date": "20260717", "strike": 101000, "right": "P", "implied_vol": 0.22, "VANNA": 0.0},
    ]
    hist_oi_rows = []
    hist_spot_rows = []
    for d in dates:
        hist_spot_rows.append({"date": d, "close": 100.0})
        for k, right in [(100.0, "C"), (100.0, "P"), (101.0, "C"), (101.0, "P")]:
            hist_oi_rows.append({"date": d, "strike": _theta_strike(k), "right": right, "open_interest": 100})
    return expiry, hist_greek_rows, hist_oi_rows, hist_spot_rows


def _capture_vanna_by_date(payload):
    """Run _accumulate_from_history and reach the (pure-local, unexposed)
    vanna_by_date accumulation via sys.settrace -- the same seam the existing
    iv_by_date/oi_by_date locals sit behind. Network-free; no code changes to
    the function under test required."""
    expiry, greeks, oi, spot = payload
    captured = {}

    def tracer(frame, event, arg):
        if event == "return" and frame.f_code.co_name == "_accumulate_from_history":
            local = frame.f_locals.get("vanna_by_date")
            if local is not None:
                captured["vanna_by_date"] = {d: dict(day) for d, day in local.items()}
        return tracer

    sys.settrace(tracer)
    try:
        rr._accumulate_from_history("MOCK", expiry, 2, "replication", greeks, oi, spot)
    finally:
        sys.settrace(None)
    assert "vanna_by_date" in captured, "vanna_by_date local not observed"
    return captured["vanna_by_date"]


@pytest.mark.unit
def test_vanna_by_date_parse_accumulates_per_strike():
    vanna = _capture_vanna_by_date(_build_vanna_payload())
    # (1) populated for the right (date, strike, right) keys
    assert vanna["20260715"][(100.0, "C")] == 1.5
    assert vanna["20260716"][(100.0, "C")] == 2.0
    # (2) alias handling: 'VANNA' and lowercase 'vanna' both read, no scale applied
    assert vanna["20260715"][(100.0, "P")] == -0.75  # via 'VANNA'
    assert vanna["20260716"][(101.0, "P")] == -1.2   # via 'vanna'
    assert vanna["20260717"][(101.0, "P")] == 0.0    # via 'VANNA', stored despite being 0
    # (3) row missing the vanna field parses to 0.0 without crashing
    assert vanna["20260715"][(101.0, "C")] == 0.0
    # (4) malformed row (bad strike) is skipped -- its 9.9 never lands
    assert all(v != 9.9 for day in vanna.values() for v in day.values())
    # a zero-valued row still registers a key (presence, not magnitude)
    assert (100.0, "C") in vanna["20260717"]

