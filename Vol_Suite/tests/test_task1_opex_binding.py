from __future__ import annotations

import copy

import pytest

from Vol_Suite.provenance_contract import canonical_sha256
from Vol_Suite.dealer_exposure_acquisition import (
    AcquisitionGateError,
    build_candidate_schedule,
    execute_sequential_acquisition,
    select_primary_schedule,
)
from Vol_Suite.dealer_exposure_universe import _validate_calendar_binding


@pytest.fixture
def binding() -> dict:
    return {
        "ticker": "ABC", "calendar_day": "2026-01-02", "nominal_date": "2026-01-09",
        "observed_expiry": "2026-01-09", "observed_expiry_date": "2026-01-09", "expiry": "2026-01-09", "dte": 7,
        "exact_dte": 7, "session_id": "S-2026-01-16", "observed_session_id": "S-2026-01-16", "session_status": "OPEN",
        "settlement_style": "PM_CLOSE", "timezone": "America/New_York",
        "as_of": "2026-01-01T00:00:00Z", "event_ids": ["opex-1"],
        "event_windows": {"opex-1": {"window_id": "opex-1:OPEX_DAY"}},
        "event_window_id": "opex-1:OPEX_DAY", "window_id": "opex-1:OPEX_DAY", "window_start": "2026-01-16T09:30:00-05:00",
        "window_end": "2026-01-16T16:00:00-05:00", "window_policy": "OPEX_DAY",
        "snapshot_hash": "a" * 64, "calendar_hash": "a" * 64,
        "calendar_policy_version": "us-options-v1", "resolver_code_version": "test-v1",
        "resolver_code_hash": canonical_sha256({"resolver_code_version": "test-v1"}),
        "calendar_binding_hash": "",
        "source_hashes": ["c" * 64], "settlement_timestamp": "2026-01-16T16:00:00-05:00",
    }
    value["calendar_binding_hash"] = canonical_sha256({k: v for k, v in value.items() if k != "calendar_binding_hash"})
    return value


def test_schedule_rejects_probe_only_binding_without_snapshot(binding):
    with pytest.raises(ValueError, match="snapshot"):
        build_candidate_schedule([{
            "ticker": "ABC", "calendar_day": "2026-01-02", "expiry": "2026-01-09", "dte": 7,
            "habitat": "OPEX", "sector": "technology", "candidate_source": "reviewed-fixture",
            "calendar_binding": binding,
        }])


def test_binding_rejects_wrong_day_timezone_and_zero_dte(binding):
    for field, value in (("calendar_day", "2026-01-03"), ("timezone", "UTC")):
        bad = copy.deepcopy(binding)
        bad[field] = value
        with pytest.raises(ValueError):
            _validate_calendar_binding(bad, ticker="ABC", day="2026-01-02", expiry="2026-01-09", dte=7)
    with pytest.raises(ValueError, match="zero"):
        _validate_calendar_binding(binding, ticker="ABC", day="2026-01-02", expiry="2026-01-09", dte=0)


def test_schedule_rejects_missing_binding_when_snapshot_binding_is_requested(binding):
    class NoResolver:
        pass

    with pytest.raises(ValueError, match="calendar binding"):
        build_candidate_schedule([{
            "ticker": "ABC", "calendar_day": "2026-01-02", "expiry": "2026-01-09", "dte": 7,
            "habitat": "OPEX", "sector": "technology", "candidate_source": "reviewed-fixture",
        }], calendar_snapshot=NoResolver(), as_of="2026-01-01T00:00:00Z")


def test_conflicting_binding_is_rejected(binding):
    conflicting = copy.deepcopy(binding)
    conflicting["calendar_binding_hash"] = "d" * 64
    with pytest.raises(ValueError, match="canonical binding|conflicts"):
        _validate_calendar_binding(conflicting, ticker="ABC", day="2026-01-02", expiry="2026-01-09", dte=7, expected=binding)


def test_selector_rejects_unbound_pass_probe(binding):
    schedule = [{"candidate_key": "k", "calendar_binding": binding, "held_pair_exclusion": False}]
    probe = {"candidate_key": "k", "status": "PASS", "validated": True, "invoked": True,
             "ticker": "ABC", "day": "2026-01-02", "expiry": "2026-01-09", "dte": 7,
             "evidence": {}}
    assert select_primary_schedule(schedule, [probe]) == []


def test_heavy_gate_rejects_unbound_schedule_before_injected_executor():
    schedule = build_candidate_schedule([{
        "ticker": "ABC", "calendar_day": "2026-01-02", "expiry": "2026-01-09", "dte": 7,
        "habitat": "OPEX", "sector": "technology", "candidate_source": "reviewed-fixture",
    }])
    calls = []
    result = execute_sequential_acquisition(schedule, approval=True, dry_run=False,
                                            probe_fetcher=lambda _: calls.append("probe"),
                                            fetcher=lambda _: calls.append("heavy"))
    assert result["comparison_status"] == "COMPARISON_INVALID"
    assert result["probes"][0]["status"] == "HARD_GAP"
    assert result["heavy_calls"] == 0 and result["network_flag"] is False
    assert calls == []


def test_availability_probe_rejects_pass_without_complete_calendar_evidence(binding):
    schedule = [{"candidate_key": "k", "ticker": "ABC", "calendar_day": "2026-01-02",
                 "expiry": "2026-01-09", "dte": 7, "held_pair_exclusion": False,
                 "calendar_binding": binding, "habitat": "OPEX", "sector": "technology",
                 "candidate_source": "reviewed-fixture"}]
    from Vol_Suite.dealer_exposure_acquisition import run_availability_probes
    probes = run_availability_probes(
        schedule,
        probe_fetcher=lambda _: {"status": "PASS", "response_status": 200,
                                 "counts": {"rows": 1}, "source_counts": {"x": 1}},
        approval=True, dry_run=False,
    )
    assert probes[0]["status"] == "HARD_GAP"
    assert probes[0]["validated"] is False
    assert probes[0]["comparison_status"] == "COMPARISON_INVALID"


def test_snapshot_backed_control_reaches_only_injected_executor():
    from copy import deepcopy
    from Vol_Suite.opex_calendar import load_snapshot, calendar_for_probe
    from Vol_Suite.tests.test_opex_calendar import payload

    p = payload()
    p["sessions"].append({"session_id": "S-2025-01-10", "session_date": "2025-01-10", "status": "OPEN",
                           "regular_open": "2025-01-10T09:30:00-05:00", "regular_close": "2025-01-10T16:00:00-05:00",
                           "early_close": False, "close_reason": None, "source_ref": "rule"})
    p["event_records"] = [dict(p["event_records"][0], event_day="2025-01-10",
                                window_start="2025-01-10T09:30:00-05:00", window_end="2025-01-10T16:00:00-05:00")]
    snapshot = load_snapshot(deepcopy(p))
    binding = dict(calendar_for_probe(snapshot, ticker="ABC", calendar_day="2025-01-10", expiry="2025-01-17", dte=7,
                                      as_of="2025-01-01T00:00:00Z"))
    schedule = build_candidate_schedule([{"ticker": "ABC", "calendar_day": "2025-01-10", "expiry": "2025-01-17", "dte": 7,
                                          "habitat": "OPEX", "sector": "technology", "candidate_source": "reviewed-fixture",
                                          "calendar_binding": binding}], calendar_snapshot=snapshot,
                                        as_of="2025-01-01T00:00:00Z")
    calls = []
    result = execute_sequential_acquisition(
        schedule, calendar_snapshot=snapshot, approval=True, dry_run=False,
        probe_fetcher=lambda request: {"status": "PASS", "response_status": 200, "counts": {"rows": 1},
                                       "source_counts": {"x": 1}, "evidence": {"calendar_binding": request["calendar_binding"]}},
        fetcher=lambda unit: calls.append(unit["candidate_key"]) or {},
    )
    assert calls == [schedule[0]["candidate_key"]]
    assert result["heavy_calls"] == 1 and result["network_flag"] is True
