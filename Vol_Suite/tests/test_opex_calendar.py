from copy import deepcopy
from datetime import date

import pytest

from Vol_Suite.opex_calendar import (
    CalendarGapError,
    calendar_for_probe,
    calendar_hash,
    canonical_calendar_bytes,
    load_snapshot,
    resolve_event_window,
    resolve_opex,
    standard_monthly_candidate,
)

H = "a" * 64


def payload():
    return {
        "schema_version": 1,
        "calendar_policy_version": "us-options-v1",
        "resolver_code_version": "test-v1",
        "knowledge_cutoff": "2026-01-01T00:00:00Z",
        "timezone": "America/New_York",
        "venue_scope": "US_OPTIONS_REGULAR",
        "source_records": [{
            "source_id": "rule", "source_kind": "venue_rule", "publisher": "test",
            "source_version": "1", "effective_start": None, "effective_end": None,
            "retrieved_at": "2025-01-01T00:00:00Z", "available_at": "2025-01-01T00:00:00Z",
            "content_sha256": H, "parser_version": "1", "selection_reason": "authoritative",
        }, {
            "source_id": "listing", "source_kind": "listing", "publisher": "test",
            "source_version": "1", "available_at": "2025-01-01T00:00:00Z",
            "content_sha256": "b" * 64, "selection_reason": "authoritative listing",
        }],
        "holidays": [],
        "sessions": [{
            "session_id": "S-2025-01-17", "session_date": "2025-01-17", "status": "OPEN",
            "regular_open": "2025-01-17T09:30:00-05:00", "regular_close": "2025-01-17T16:00:00-05:00",
            "early_close": False, "close_reason": None, "source_ref": "rule",
        }, {
            "session_id": "S-2025-01-16", "session_date": "2025-01-16", "status": "OPEN",
            "regular_open": "2025-01-16T09:30:00-05:00", "regular_close": "2025-01-16T16:00:00-05:00",
            "early_close": False, "close_reason": None, "source_ref": "rule",
        }, {
            "session_id": "S-2025-01-20", "session_date": "2025-01-20", "status": "OPEN",
            "regular_open": "2025-01-20T09:30:00-05:00", "regular_close": "2025-01-20T16:00:00-05:00",
            "early_close": False, "close_reason": None, "source_ref": "rule",
        }],
        "monthly_rules": [{
            "product_family": "EQUITY_ETF", "nominal_date": "2025-01-17",
            "observed_expiry_date": "2025-01-17", "settlement_style": "PM_CLOSE",
            "settlement_timestamp": "2025-01-17T16:00:00-05:00", "source_ref": "rule",
            "listing_source_ref": "listing", "venue_rule_date": "2025-01-17", "listing_expiry_date": "2025-01-17",
        }],
        "event_records": [{
            "event_id": "fomc-1", "event_type": "FOMC", "event_day": "2025-01-17",
            "window_start": "2025-01-17T09:30:00-05:00", "window_end": "2025-01-17T16:00:00-05:00",
            "anchor": "SCHEDULED", "source_ref": "rule", "surprise_status": "NOT_APPLICABLE",
            "causal_surprise_eligible": False,
        }],
    }


def test_third_friday_is_arithmetic_only():
    assert standard_monthly_candidate(2025, 1) == date(2025, 1, 17)


def test_snapshot_is_hash_bound_and_permutation_stable():
    first = load_snapshot(payload())
    p = payload()
    p["sessions"] = list(reversed(p["sessions"]))
    second = load_snapshot(p)
    assert canonical_calendar_bytes(first) == canonical_calendar_bytes(second)
    assert calendar_hash(first) == calendar_hash(second) == first.snapshot_hash
    with pytest.raises((AttributeError, TypeError)):
        first.sessions = ()


def test_resolve_opex_and_probe_are_exact():
    snapshot = load_snapshot(payload())
    result = resolve_opex(snapshot, product_family="EQUITY_ETF", nominal_or_observed="2025-01-17", as_of="2025-01-01T00:00:00Z")
    assert result.observed_expiry_date == "2025-01-17"
    assert result.settlement_style == "PM_CLOSE"
    probe = calendar_for_probe(snapshot, ticker="ABC", calendar_day="2025-01-17", expiry="2025-01-17", dte=0, as_of="2025-01-01T00:00:00Z")
    assert probe["calendar_hash"] == snapshot.snapshot_hash
    assert probe["session_status"] == "OPEN"


def test_event_window_is_point_in_time_and_hash_bound():
    snapshot = load_snapshot(payload())
    event = resolve_event_window(snapshot, event_type="FOMC", event_day="2025-01-17", window_policy="OPEX_DAY", as_of="2025-01-01T00:00:00Z")
    assert event.event_id == "fomc-1"
    assert event.calendar_hash == snapshot.snapshot_hash


def test_unknown_conflict_holiday_and_cutoff_fail_closed():
    p = payload()
    p["sessions"][0]["status"] = "UNKNOWN"
    with pytest.raises(CalendarGapError, match="HARD_GAP"):
        resolve_opex(load_snapshot(p), product_family="EQUITY_ETF", nominal_or_observed="2025-01-17", as_of="2025-01-01T00:00:00Z")
    p = payload(); p["monthly_rules"].append(dict(p["monthly_rules"][0], observed_expiry_date="2025-01-18", settlement_timestamp="2025-01-18T16:00:00-05:00", source_ref="other"))
    with pytest.raises(CalendarGapError, match="CONFLICT"):
        resolve_opex(load_snapshot(p), product_family="EQUITY_ETF", nominal_or_observed="2025-01-17", as_of="2025-01-01T00:00:00Z")
    with pytest.raises(CalendarGapError, match="cutoff"):
        resolve_event_window(load_snapshot(payload()), event_type="FOMC", event_day="2025-01-17", window_policy="OPEX_DAY", as_of="2026-01-02T00:00:00Z")


def test_naive_and_wrong_local_day_timestamps_rejected():
    p = payload(); p["sessions"][0]["regular_open"] = "2025-01-17T09:30:00"
    with pytest.raises(ValueError, match="timezone"):
        load_snapshot(p)
    p = payload(); p["sessions"][0]["regular_open"] = "2025-01-17T04:30:00+00:00"
    with pytest.raises(ValueError, match="local"):
        load_snapshot(p)


def test_non_shifting_holiday_does_not_snap_neighbor():
    p = payload()
    p["holidays"] = [{"holiday_date": "2025-01-17", "name": "closed", "source_ref": "rule", "shift_policy": "NONE"}]
    p["sessions"][0]["status"] = "HOLIDAY_CLOSED"
    with pytest.raises(CalendarGapError, match="holiday"):
        resolve_opex(load_snapshot(p), product_family="EQUITY_ETF", nominal_or_observed="2025-01-17", as_of="2025-01-01T00:00:00Z")


def test_source_hash_and_nan_are_strict():
    p = payload(); p["source_records"][0]["content_sha256"] = "bad"
    with pytest.raises(ValueError): load_snapshot(p)
    p = payload(); p["event_records"][0]["causal_surprise_eligible"] = float("nan")
    with pytest.raises(ValueError): load_snapshot(p)


def test_window_policy_unknown_is_gap():
    with pytest.raises(CalendarGapError, match="window policy"):
        resolve_event_window(load_snapshot(payload()), event_type="FOMC", event_day="2025-01-17", window_policy="LATEST", as_of="2025-01-01T00:00:00Z")


def test_month_boundaries():
    assert standard_monthly_candidate(2024, 2) == date(2024, 2, 16)
    assert standard_monthly_candidate(2025, 2) == date(2025, 2, 21)
    with pytest.raises(ValueError): standard_monthly_candidate(2025, 13)


def test_dte_must_match_exact_local_date():
    with pytest.raises(CalendarGapError, match="DTE"):
        calendar_for_probe(load_snapshot(payload()), ticker="ABC", calendar_day="2025-01-16", expiry="2025-01-17", dte=0, as_of="2025-01-01T00:00:00Z")


def test_am_settlement_distinct_from_close():
    p = payload(); p["monthly_rules"][0].update(settlement_style="AM_SETTLEMENT", settlement_timestamp="2025-01-17T09:30:00-05:00")
    result = resolve_opex(load_snapshot(p), product_family="EQUITY_ETF", nominal_or_observed="2025-01-17", as_of="2025-01-01T00:00:00Z")
    assert result.settlement_timestamp != result.regular_close


def test_source_unavailable_at_as_of_is_gap():
    p = payload(); p["source_records"][0]["available_at"] = "2025-12-01T00:00:00Z"
    with pytest.raises(CalendarGapError, match="available"):
        resolve_opex(load_snapshot(p), product_family="EQUITY_ETF", nominal_or_observed="2025-01-17", as_of="2025-01-01T00:00:00Z")


def test_event_type_validation():
    p = payload(); p["event_records"][0]["event_type"] = "NEWS"
    with pytest.raises(ValueError):
        load_snapshot(p)


def test_snapshot_self_hash_tamper_rejected():
    p = payload(); p["snapshot_hash"] = "b" * 64
    with pytest.raises(ValueError, match="snapshot_hash"):
        load_snapshot(p)


def test_early_close_is_not_holiday():
    p = payload(); p["sessions"][0].update(early_close=True, regular_close="2025-01-17T13:00:00-05:00", close_reason="holiday eve")
    result = resolve_opex(load_snapshot(p), product_family="EQUITY_ETF", nominal_or_observed="2025-01-17", as_of="2025-01-01T00:00:00Z")
    assert result.early_close is True and result.session_status == "OPEN"


def test_empty_and_incomplete_source_provenance_rejected():
    p = payload(); p["source_records"] = []
    with pytest.raises(ValueError, match="source_records"):
        load_snapshot(p)
    p = payload(); del p["source_records"][0]["selection_reason"]
    with pytest.raises(ValueError, match="source_records"):
        load_snapshot(p)


def test_standard_monthly_label_cannot_override_rule_and_listing_facts():
    p = payload(); p["monthly_rules"][0]["venue_rule_date"] = "2025-01-16"
    with pytest.raises(CalendarGapError, match="third-Friday"):
        resolve_opex(load_snapshot(p), product_family="EQUITY_ETF", nominal_or_observed="2025-01-17", as_of="2025-01-01T00:00:00Z")


def test_window_policies_derive_distinct_boundaries():
    s = load_snapshot(payload())
    values = [resolve_event_window(s, event_type="FOMC", event_day="2025-01-17", window_policy=policy, as_of="2025-01-01T00:00:00Z") for policy in ("PRE_OPEX_SESSION", "OPEX_DAY", "POST_OPEX_RESPONSE")]
    assert len({(v.window_start, v.window_end) for v in values}) == 3
    assert values[1].anchor == "SESSION_BOUNDARY"


def test_settlement_must_be_on_observed_session_day():
    p = payload(); p["monthly_rules"][0]["settlement_timestamp"] = "2025-01-18T16:00:00-05:00"
    with pytest.raises(CalendarGapError, match="local day"):
        resolve_opex(load_snapshot(p), product_family="EQUITY_ETF", nominal_or_observed="2025-01-17", as_of="2025-01-01T00:00:00Z")


def test_probe_binding_requires_complete_identity_and_is_deeply_immutable():
    binding = calendar_for_probe(load_snapshot(payload()), ticker="ABC", calendar_day="2025-01-17", expiry="2025-01-17", dte=0, as_of="2025-01-01T00:00:00Z")
    for key in ("observed_expiry", "nominal_date", "session_id", "settlement_timestamp", "event_ids", "event_windows", "as_of", "snapshot_hash", "calendar_binding_hash", "source_hashes", "calendar_policy_version", "resolver_code_version", "exact_dte", "window_id", "window_start", "window_end", "window_policy"):
        assert key in binding
    assert binding["window_policy"] == "OPEX_DAY"
    assert binding["event_windows"]["fomc-1"]["window_id"] == binding["window_id"]
    with pytest.raises(TypeError):
        binding["event_windows"]["fomc-1"]["window_start"] = "bad"
    with pytest.raises(CalendarGapError, match="as_of"):
        calendar_for_probe(load_snapshot(payload()), ticker="ABC", calendar_day="2025-01-17", expiry="2025-01-17", dte=0)


def test_probe_rejects_empty_event_context_and_unknown_policy():
    p = payload(); p["event_records"] = []
    with pytest.raises(CalendarGapError, match="event context"):
        calendar_for_probe(load_snapshot(p), ticker="ABC", calendar_day="2025-01-17", expiry="2025-01-17", dte=0, as_of="2025-01-01T00:00:00Z")
    with pytest.raises(CalendarGapError, match="window policy"):
        calendar_for_probe(load_snapshot(payload()), ticker="ABC", calendar_day="2025-01-17", expiry="2025-01-17", dte=0, as_of="2025-01-01T00:00:00Z", window_policy="LATEST")


def test_probe_resolves_every_event_and_rejects_event_window_relationships():
    p = payload(); p["event_records"][0]["window_start"] = "2025-01-17T16:00:00-05:00"
    p["event_records"][0]["window_end"] = "2025-01-17T09:30:00-05:00"
    with pytest.raises(CalendarGapError, match="window"):
        calendar_for_probe(load_snapshot(p), ticker="ABC", calendar_day="2025-01-17", expiry="2025-01-17", dte=0, as_of="2025-01-01T00:00:00Z")


def test_probe_rejects_invalid_surprise_combinations_and_unknown_status():
    for status, eligible in (("NOT_APPLICABLE", True), ("DESCRIPTIVE-HABITAT", True), ("OPERATIONAL", False), ("UNKNOWN", False)):
        p = payload(); p["event_records"][0].update(surprise_status=status, causal_surprise_eligible=eligible)
        with pytest.raises((ValueError, CalendarGapError)):
            load_snapshot(p)


def test_probe_resolves_holiday_sources_and_expiry_facts_before_binding():
    p = payload(); p["holidays"] = [{"holiday_date": "2025-01-17", "source_ref": "missing"}]
    with pytest.raises(CalendarGapError, match="holiday"):
        calendar_for_probe(load_snapshot(p), ticker="ABC", calendar_day="2025-01-17", expiry="2025-01-17", dte=0, as_of="2025-01-01T00:00:00Z")
    p = payload(); p["monthly_rules"][0]["settlement_style"] = "UNKNOWN"
    with pytest.raises(CalendarGapError, match="settlement"):
        calendar_for_probe(load_snapshot(p), ticker="ABC", calendar_day="2025-01-17", expiry="2025-01-17", dte=0, as_of="2025-01-01T00:00:00Z")
    p = payload(); p["monthly_rules"][0]["listing_source_ref"] = "missing"
    with pytest.raises(CalendarGapError, match="listing"):
        calendar_for_probe(load_snapshot(p), ticker="ABC", calendar_day="2025-01-17", expiry="2025-01-17", dte=0, as_of="2025-01-01T00:00:00Z")


def test_adjacent_duplicate_dates_are_deterministic_hard_gaps():
    p = payload(); p["sessions"].append(deepcopy(p["sessions"][1]))
    p["sessions"][-1]["session_id"] = "conflicting-copy"
    snapshot = load_snapshot(p)
    with pytest.raises(CalendarGapError, match="HARD_GAP"):
        resolve_event_window(snapshot, event_type="FOMC", event_day="2025-01-17", window_policy="PRE_OPEX_SESSION", as_of="2025-01-01T00:00:00Z")
    with pytest.raises(CalendarGapError, match="HARD_GAP"):
        calendar_for_probe(snapshot, ticker="ABC", calendar_day="2025-01-17", expiry="2025-01-17", dte=0, as_of="2025-01-01T00:00:00Z", window_policy="PRE_OPEX_SESSION")


def test_probe_rejects_invalid_session_facts_and_wrong_expiry_date():
    p = payload(); p["sessions"][0]["regular_close"] = "2025-01-17T09:30:00-05:00"
    with pytest.raises(CalendarGapError, match="session"):
        calendar_for_probe(load_snapshot(p), ticker="ABC", calendar_day="2025-01-17", expiry="2025-01-17", dte=0, as_of="2025-01-01T00:00:00Z")
    with pytest.raises(CalendarGapError, match="expiry"):
        calendar_for_probe(load_snapshot(payload()), ticker="ABC", calendar_day="2025-01-17", expiry="2025-01-18", dte=1, as_of="2025-01-01T00:00:00Z")


def test_probe_output_is_stable_for_permuted_events_and_sessions():
    first = calendar_for_probe(load_snapshot(payload()), ticker="ABC", calendar_day="2025-01-17", expiry="2025-01-17", dte=0, as_of="2025-01-01T00:00:00Z")
    p = payload(); p["sessions"].reverse(); p["event_records"].reverse()
    second = calendar_for_probe(load_snapshot(p), ticker="ABC", calendar_day="2025-01-17", expiry="2025-01-17", dte=0, as_of="2025-01-01T00:00:00Z")
    assert dict(first) == dict(second)
    assert first["calendar_binding_hash"] == second["calendar_binding_hash"]
