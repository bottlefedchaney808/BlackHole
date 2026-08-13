"""
Network-free tests for expiry_selector.py.

This is a small module carrying outsized responsibility: backtest_stage3,
dealer_positioning, replication_reference, variance_swap_live/screener and
volatility_suite all resolve their expiry through it, so "which contract are
we actually analyzing" is decided here for the entire suite. It had no test
coverage.

Everything below is pure calendar arithmetic with `today` injected, so none of
it touches the network or the clock. The only functions needing a client take
a fake exposing just `list_expirations`.
"""
from datetime import date, datetime, timezone

import pytest

import expiry_selector as es


# A realistic SPY-style listing set around a fixed "today" of Mon 2026-07-06.
TODAY = date(2026, 7, 6)
EXPIRIES = [
    "20260703",   # last Friday -- already expired, must be ignored
    "20260710",   # weekly  (Fri, +4d)
    "20260717",   # MONTHLY (3rd Friday of July 2026, +11d)
    "20260724",   # weekly  (+18d)
    "20260731",   # weekly  (+25d)
    "20260821",   # MONTHLY (3rd Friday of August 2026, +46d)
    "20260916",   # Wednesday -- "other" (+72d)
    "20261016",   # Friday, 3rd Friday of Oct 2026 -> MONTHLY (+102d)
]


class FakeTD:
    def __init__(self, expiries=None):
        self.expiries = EXPIRIES if expiries is None else expiries
        self.calls = 0

    def list_expirations(self, ticker):
        self.calls += 1
        return list(self.expiries)


# ---------------------------------------------------------------------------
# Calendar conventions
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("year,month,expected", [
    (2026, 7, date(2026, 7, 17)),
    (2026, 8, date(2026, 8, 21)),
    (2026, 10, date(2026, 10, 16)),
    (2026, 1, date(2026, 1, 16)),
    (2027, 5, date(2027, 5, 21)),
])
def test_third_friday_matches_the_opex_convention(year, month, expected):
    got = es.third_friday(year, month)
    assert got == expected
    assert got.weekday() == 4


@pytest.mark.unit
def test_third_friday_when_the_month_starts_on_a_friday():
    """Edge case the naive "first Friday + 14 days" shortcut gets wrong
    depending on how the first partial week is counted."""
    assert date(2026, 5, 1).weekday() == 4
    assert es.third_friday(2026, 5) == date(2026, 5, 15)


@pytest.mark.unit
@pytest.mark.parametrize("d,expected", [
    (date(2026, 7, 17), "monthly"),   # 3rd Friday
    (date(2026, 7, 10), "weekly"),    # another Friday
    (date(2026, 7, 24), "weekly"),
    (date(2026, 9, 16), "other"),     # Wednesday
    (date(2026, 7, 6), "other"),      # Monday
])
def test_classify_expiry(d, expected):
    assert es.classify_expiry(d) == expected


# ---------------------------------------------------------------------------
# Parsing / filtering
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_expired_dates_are_dropped():
    parsed = es._parse_expiries(EXPIRIES, today=TODAY)
    assert "20260703" not in [p[0] for p in parsed]
    assert all(p[2] >= 0 for p in parsed), "no negative days-to-expiry"


@pytest.mark.unit
def test_todays_expiry_is_kept_not_dropped():
    """0 DTE is still tradable -- the filter is on past dates, not on today."""
    parsed = es._parse_expiries(["20260706"], today=TODAY)
    assert [p[0] for p in parsed] == ["20260706"]
    assert parsed[0][2] == 0


@pytest.mark.unit
def test_unparseable_entries_are_skipped_rather_than_raising():
    """A junk entry in a listing response shouldn't take down every caller."""
    parsed = es._parse_expiries(["garbage", "", "2026-07-10", "20260710", None],
                                today=TODAY)
    assert [p[0] for p in parsed] == ["20260710"]


@pytest.mark.unit
def test_results_are_sorted_nearest_first():
    parsed = es._parse_expiries(list(reversed(EXPIRIES)), today=TODAY)
    assert [p[2] for p in parsed] == sorted(p[2] for p in parsed)


@pytest.mark.unit
def test_target_date_uses_365_calendar_day_convention():
    """DEFAULT_A=365 is deliberate and shared suite-wide -- a 252 convention
    here would silently disagree with every T used downstream."""
    assert es.DEFAULT_A == 365
    # 0.25 * 365 = 91.25, rounded to 91 calendar days
    assert es.target_date_from_years(0.25, today=TODAY) == date(2026, 10, 5)
    assert es.target_date_from_years(1.0, today=TODAY) == date(2027, 7, 6)
    assert es.target_date_from_years(0.5, today=TODAY) == date(2027, 1, 4)


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_offers_both_a_weekly_and_a_monthly_even_when_one_is_not_closest():
    """The point of the picker: a monthly/OPEX date carries different
    liquidity and pinning behavior than a weekly, so both belong on the table
    rather than only whichever is numerically nearest."""
    cands = es.find_candidate_expiries(EXPIRIES, target_years=20 / 365, today=TODAY)
    kinds = {c.kind for c in cands}
    assert "weekly" in kinds and "monthly" in kinds


@pytest.mark.unit
def test_candidates_are_deduplicated_and_capped_at_three():
    cands = es.find_candidate_expiries(EXPIRIES, target_years=0.25, today=TODAY)
    assert 1 <= len(cands) <= 3
    assert len({c.exp_str for c in cands}) == len(cands)


@pytest.mark.unit
def test_candidates_are_ordered_by_closeness_to_target():
    cands = es.find_candidate_expiries(EXPIRIES, target_years=0.25, today=TODAY)
    assert [c.diff_days for c in cands] == sorted(c.diff_days for c in cands)


@pytest.mark.unit
def test_closest_candidate_really_is_the_closest_available():
    target_years = 100 / 365
    cands = es.find_candidate_expiries(EXPIRIES, target_years, today=TODAY)
    all_dte = [p[2] for p in es._parse_expiries(EXPIRIES, today=TODAY)]
    best_possible = min(abs(d - round(target_years * es.DEFAULT_A)) for d in all_dte)
    assert cands[0].diff_days == best_possible


@pytest.mark.unit
def test_t_years_is_consistent_with_the_reported_dte():
    for c in es.find_candidate_expiries(EXPIRIES, 0.25, today=TODAY):
        assert c.T_years == pytest.approx(c.dte / es.DEFAULT_A)
        assert c.exp_date == date(int(c.exp_str[:4]), int(c.exp_str[4:6]), int(c.exp_str[6:]))


@pytest.mark.unit
def test_no_future_expiries_yields_no_candidates():
    assert es.find_candidate_expiries(["20260101"], 0.25, today=TODAY) == []


@pytest.mark.unit
def test_falls_back_gracefully_when_no_expiry_of_a_given_kind_exists():
    """A ticker listing only non-Friday dates has no weekly and no monthly;
    the picker should still return something rather than blowing up."""
    cands = es.find_candidate_expiries(["20260916"], 0.25, today=TODAY)
    assert len(cands) == 1 and cands[0].kind == "other"


# ---------------------------------------------------------------------------
# nearest_expiry / resolve_expiration
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_nearest_expiry_returns_a_listed_date_and_matching_t():
    exp, T = es.nearest_expiry(FakeTD(), "SPY", 0.25)
    assert exp in EXPIRIES
    assert T > 0


@pytest.mark.unit
def test_nearest_expiry_raises_when_the_ticker_has_no_options():
    with pytest.raises(ValueError, match="No options found"):
        es.nearest_expiry(FakeTD([]), "NOPE", 0.25)


@pytest.mark.unit
def test_resolve_expiration_honors_an_explicitly_requested_expiry():
    """volatility_suite pins one expiry across every module in a run; that
    pin must be respected exactly, not re-derived."""
    exp, T = es.resolve_expiration(FakeTD(), "SPY", "20261016", 0.25)
    assert exp == "20261016"
    assert T == pytest.approx(max((date(2026, 10, 16) - datetime.now(timezone.utc).date()).days, 0) / 365)


@pytest.mark.unit
def test_resolve_expiration_falls_back_when_the_pin_is_not_listed(capsys):
    """A focus ticker and its index leg don't always share every listing.
    Falling back beats raising -- but it must say so, since a silent
    substitution means two modules in the same run are quietly analyzing
    different contracts."""
    exp, T = es.resolve_expiration(FakeTD(), "SPY", "20991231", 0.25)
    assert exp in EXPIRIES
    assert "isn't listed" in capsys.readouterr().out


@pytest.mark.unit
def test_resolve_expiration_without_a_pin_uses_nearest():
    assert es.resolve_expiration(FakeTD(), "SPY", None, 0.25)[0] in EXPIRIES
