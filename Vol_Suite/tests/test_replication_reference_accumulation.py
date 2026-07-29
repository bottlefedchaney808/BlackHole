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
            hist_greek_rows.append({"date": d, "strike": _theta_strike(k), "right": "C", "implied_vol": iv})
            hist_greek_rows.append({"date": d, "strike": _theta_strike(k), "right": "P", "implied_vol": iv})
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
def test_raises_on_insufficient_history():
    with pytest.raises(ValueError):
        rr._accumulate_from_history("MOCK", "20260901", 5, "replication", [], [], [])
