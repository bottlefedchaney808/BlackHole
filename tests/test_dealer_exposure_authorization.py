from __future__ import annotations

from datetime import datetime, timezone
from copy import deepcopy

import pytest

from Vol_Suite.dealer_exposure_authorization import (
    AcquisitionAuthorization,
    candidate_manifest_projection,
    candidate_manifest_sha256,
)
from Vol_Suite.provenance_contract import canonical_sha256


H = "a" * 64
H2 = "b" * 64
SOURCE_HASH = "c" * 64


def _binding(key="XLE|2026-07-06|2026-07-06|1"):
    return {
        "candidate_key": key,
        "observed_expiry_date": "2026-07-06",
        "session_id": "session-20260706",
        "session_status": "OPEN",
        "settlement_style": "PM_CLOSE",
        "settlement_timestamp": "2026-07-06T16:00:00-04:00",
        "event_window_id": "window-1",
        "window_start": "2026-07-06T09:30:00-04:00",
        "window_end": "2026-07-06T16:00:00-04:00",
        "calendar_binding_hash": H2,
    }


def _unit(key="XLE|2026-07-06|2026-07-06|1"):
    return {
        "candidate_key": key,
        "ticker": "XLE",
        "calendar_day": "2026-07-06",
        "expiry": "2026-07-06",
        "dte": 1,
        "habitat": "OPEX",
        "sector": "ENERGY",
        "candidate_source": "fixture",
        "source_hashes": [SOURCE_HASH],
        "calendar_hash": H,
        "calendar_policy_version": "opex-calendar-v1",
        "resolver_code_version": "resolver-v1",
        "resolver_code_hash": H,
        "observed_expiry_date": "2026-07-06",
        "session_id": "session-20260706",
        "session_status": "OPEN",
        "regular_open": "2026-07-06T09:30:00-04:00",
        "regular_close": "2026-07-06T16:00:00-04:00",
        "early_close": False,
        "settlement_style": "PM_CLOSE",
        "settlement_timestamp": "2026-07-06T16:00:00-04:00",
        "event_window_id": "window-1",
        "window_start": "2026-07-06T09:30:00-04:00",
        "window_end": "2026-07-06T16:00:00-04:00",
        "calendar_binding_hash": H2,
    }


def _manifest():
    unit = _unit()
    return {
        "schema_version": 1,
        "manifest_schema": "candidate_manifest_calendar_enriched_v2",
        "calendar_enriched": True,
        "units": [unit],
        "exclusions": [],
        "selection_provenance": {"source": "fixture", "source_registry": {"fixture": {"source_hashes": [SOURCE_HASH]}}},
        "quota": {"max_units": 1},
        "held_pair_evidence_hash": H,
        "probe_policy": {"required_status": "PASS"},
        "executor_policy": {"allowed_executor_id": "staged_historical_adapter_v1"},
        "cost_ceiling": {
            "max_units": 1,
            "max_probe_calls": 2,
            "max_heavy_calls": 4,
            "max_total_endpoint_calls": 6,
            "max_payload_bytes": 1000,
            "max_wall_seconds": 10,
            "concurrency": 1,
        },
        "calendar": {
            "calendar_hash": H,
            "calendar_policy_version": "opex-calendar-v1",
            "resolver_code_version": "resolver-v1",
            "resolver_code_hash": H,
            "observed_session_id": "session-20260706",
            "calendar_binding_hash": H2,
        },
    }


def _authorization(manifest=None):
    manifest = manifest or _manifest()
    digest = candidate_manifest_sha256(manifest)
    payload = {
        "schema_version": 1,
        "authorization_id": "auth-fixture-1",
        "issued_at": "2026-08-15T12:00:00+00:00",
        "expires_at": "2026-08-15T13:00:00+00:00",
        "issued_by": "pm-fixture",
        "purpose": "staged-dealer-exposure-acquisition",
        "environment": "offline-test",
        "candidate_manifest_sha256": digest,
        "candidate_manifest_projection": "candidate_manifest_calendar_enriched_v2",
        "calendar": manifest["calendar"],
        "scope": {
            "candidate_keys": ["XLE|2026-07-06|2026-07-06|1"],
            "calendar_hash": H,
            "calendar_policy_version": "opex-calendar-v1",
            "resolver_code_version": "resolver-v1",
            "resolver_code_hash": H,
            "observed_bindings": [_binding()],
            "ticker_day_pairs": [["XLE", "2026-07-06"]],
            "dte_strata": [[1, 3], [4, 7], [8, 10]],
            "event_habitats": ["OPEX"],
            "event_fraction": 1.0,
            "control_fraction": 0.0,
            "max_arm_ratio": 2.0,
            "held_pair_policy": "reject",
            "no_imputation": True,
            "same_day_aggregation": "preserve_ticker_values_v1",
            "calendar_binding_hash": H2,
        },
        "probe_policy": {
            "required_status": "PASS",
            "required_validated": True,
            "required_invoked": True,
            "required_checks": ["chain"],
            "pre_window_observations_exact": 2,
            "probe_code_hash": H,
        },
        "cost_ceiling": {
            "max_units": 1,
            "max_probe_calls": 2,
            "max_heavy_calls": 4,
            "max_total_endpoint_calls": 6,
            "max_payload_bytes": 1000,
            "max_wall_seconds": 10,
            "concurrency": 1,
            "on_exceed": "stop_and_hard_gap",
        },
        "executor_policy": {
            "allowed_executor_id": "staged_historical_adapter_v1",
            "allowed_executor_entrypoint": "fixture",
            "allowed_endpoint_families": ["hist/option/all_greeks"],
            "allowed_endpoint_paths": ["/hist/option/all_greeks"],
            "allowed_request_methods": ["GET"],
            "scope_binding": "candidate keys",
            "network_fetch_allowed": False,
            "allow_new_candidate_keys": False,
            "allow_held_pairs": False,
            "allow_live_model_calls": False,
            "allow_scheduler_calls": False,
            "allow_writes_outside_artifact_root": False,
        },
        "stop_conditions": ["hard gap"],
    }
    payload["authorization_sha256"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "authorization_sha256"}
    )
    return payload


def test_projection_is_calendar_enriched_sorted_and_deterministic():
    manifest = _manifest()
    altered = deepcopy(manifest)
    altered["units"] = list(reversed(altered["units"]))
    assert candidate_manifest_projection(manifest) == candidate_manifest_projection(altered)
    assert candidate_manifest_sha256(manifest) == canonical_sha256(candidate_manifest_projection(manifest))


def test_authorization_round_trip_is_immutable_and_self_hashes():
    auth = AcquisitionAuthorization.from_mapping(_authorization(), candidate_manifest=_manifest(), now="2026-08-15T12:30:00+00:00")
    assert auth.authorization_sha256() == auth.to_mapping()["authorization_sha256"]
    assert AcquisitionAuthorization.from_mapping(auth.to_mapping(), candidate_manifest=_manifest(), now="2026-08-15T12:30:00+00:00") == auth
    with pytest.raises(TypeError):
        auth.scope["candidate_keys"] = ()


def test_missing_calendar_identity_is_rejected():
    payload = _authorization()
    del payload["calendar"]["calendar_hash"]
    with pytest.raises((ValueError, TypeError)):
        AcquisitionAuthorization.from_mapping(payload, candidate_manifest=_manifest())


@pytest.mark.parametrize("mutator", [
    lambda p: p["calendar"].update(calendar_hash=H2),
    lambda p: p["scope"].update(candidate_keys=["other"]),
    lambda p: p.update(extraneous=True),
    lambda p: p["executor_policy"].update(allow_live_model_calls="false"),
    lambda p: p["cost_ceiling"].update(concurrency=2),
])
def test_authorization_fail_closed_for_binding_scope_unknown_types_and_limits(mutator):
    payload = _authorization()
    mutator(payload)
    with pytest.raises((ValueError, TypeError)):
        AcquisitionAuthorization.from_mapping(payload, candidate_manifest=_manifest(), now="2026-08-15T12:30:00+00:00")


def test_expired_authorization_is_rejected():
    payload = _authorization()
    payload["expires_at"] = "2026-08-15T12:29:00+00:00"
    with pytest.raises(ValueError):
        AcquisitionAuthorization.from_mapping(payload, candidate_manifest=_manifest(), now="2026-08-15T12:30:00+00:00")


def test_pre_calendar_manifest_and_unsorted_duplicate_keys_are_rejected():
    manifest = _manifest()
    manifest["manifest_schema"] = "candidate_manifest_v1"
    with pytest.raises(ValueError):
        candidate_manifest_projection(manifest)
    manifest = _manifest()
    manifest["units"].append(deepcopy(manifest["units"][0]))
    with pytest.raises(ValueError):
        candidate_manifest_projection(manifest)


@pytest.mark.parametrize("mutator", [
    lambda m: m["units"][0].update(calendar_binding_hash=H),
    lambda m: m["units"][0].update(source_hashes=[]),
    lambda m: m.pop("quota"),
])
def test_manifest_rejects_transitive_binding_provenance_and_missing_normative_fields(mutator):
    manifest = _manifest()
    mutator(manifest)
    with pytest.raises((ValueError, TypeError)):
        candidate_manifest_projection(manifest)


def test_manifest_rejects_source_registry_hash_mismatch():
    manifest = _manifest()
    manifest["selection_provenance"]["source_registry"]["fixture"]["source_hashes"] = [H]
    with pytest.raises(ValueError, match="source registry"):
        candidate_manifest_projection(manifest)


@pytest.mark.parametrize("mutator", [
    lambda p: p.pop("authorization_sha256"),
    lambda p: p.update(authorization_sha256="not-a-sha256"),
    lambda p: p.update(authorization_sha256=H),
])
def test_authorization_requires_independent_valid_self_hash(mutator):
    payload = _authorization()
    mutator(payload)
    with pytest.raises((ValueError, TypeError)):
        AcquisitionAuthorization.from_mapping(payload, candidate_manifest=_manifest())


def test_valid_source_and_calendar_binding_control_is_admitted():
    manifest = _manifest()
    payload = _authorization(manifest)
    auth = AcquisitionAuthorization.from_mapping(
        payload, candidate_manifest=manifest, now="2026-08-15T12:30:00+00:00"
    )
    assert auth.to_mapping()["authorization_sha256"] == payload["authorization_sha256"]


def test_authorization_schema_version_rejects_boolean_true():
    payload = _authorization()
    payload["schema_version"] = True
    payload["authorization_sha256"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "authorization_sha256"}
    )
    with pytest.raises((ValueError, TypeError), match="schema_version"):
        AcquisitionAuthorization.from_mapping(
            payload, candidate_manifest=_manifest(), now="2026-08-15T12:30:00+00:00"
        )


def test_manifest_schema_version_rejects_boolean_true():
    manifest = _manifest()
    manifest["schema_version"] = True
    with pytest.raises((ValueError, TypeError), match="schema_version"):
        candidate_manifest_projection(manifest)


def test_future_issued_authorization_is_rejected_at_injected_validation_time():
    payload = _authorization()
    payload["issued_at"] = "2026-08-15T13:00:01+00:00"
    payload["expires_at"] = "2026-08-15T14:00:00+00:00"
    payload["authorization_sha256"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "authorization_sha256"}
    )
    with pytest.raises(ValueError, match="issued_at"):
        AcquisitionAuthorization.from_mapping(
            payload, candidate_manifest=_manifest(), now="2026-08-15T12:30:00+00:00"
        )
