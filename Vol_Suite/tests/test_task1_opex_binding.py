from __future__ import annotations

import copy

import pytest

from Vol_Suite.dealer_exposure_acquisition import build_candidate_schedule
from Vol_Suite.dealer_exposure_universe import _validate_calendar_binding


@pytest.fixture
def binding() -> dict:
    return {
        "ticker": "ABC", "calendar_day": "2026-01-02", "nominal_date": "2026-01-09",
        "observed_expiry": "2026-01-09", "expiry": "2026-01-09", "dte": 7,
        "exact_dte": 7, "session_id": "S-2026-01-16", "session_status": "OPEN",
        "settlement_style": "PM_CLOSE", "timezone": "America/New_York",
        "as_of": "2026-01-01T00:00:00Z", "event_ids": ["opex-1"],
        "event_windows": {"opex-1": {"window_id": "opex-1:OPEX_DAY"}},
        "window_id": "opex-1:OPEX_DAY", "window_start": "2026-01-16T09:30:00-05:00",
        "window_end": "2026-01-16T16:00:00-05:00", "window_policy": "OPEX_DAY",
        "snapshot_hash": "a" * 64, "calendar_hash": "a" * 64,
        "calendar_policy_version": "us-options-v1", "resolver_code_version": "test-v1",
        "calendar_binding_hash": "b" * 64,
        "source_hashes": ["c" * 64], "settlement_timestamp": "2026-01-16T16:00:00-05:00",
    }


def test_fixture_binding_is_carried_by_schedule_and_is_immutable_shape(binding):
    rows = build_candidate_schedule([{
        "ticker": "ABC", "calendar_day": "2026-01-02", "expiry": "2026-01-09", "dte": 7,
        "habitat": "OPEX", "sector": "technology", "candidate_source": "reviewed-fixture",
        "calendar_binding": binding,
    }])
    assert rows[0]["calendar_binding"] == binding
    assert rows[0]["candidate_key"].startswith("2026-01-02|ABC|2026-01-09|7")


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
    with pytest.raises(ValueError, match="conflicts"):
        _validate_calendar_binding(conflicting, ticker="ABC", day="2026-01-02", expiry="2026-01-09", dte=7, expected=binding)
