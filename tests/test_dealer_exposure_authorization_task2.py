from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import threading

import pytest

from Vol_Suite.dealer_exposure_acquisition import (
    _AdmissionContext,
    _authorized_executor,
    _strict_prewindow,
    admit_acquisition,
    execute_sequential_acquisition,
    run_availability_probes,
)
from Vol_Suite.dealer_exposure_authorization import AcquisitionAuthorization, candidate_manifest_sha256
from Vol_Suite.dealer_exposure_executor import AdapterRegistry
import Vol_Suite.dealer_exposure_expansion as expansion
from Vol_Suite.dealer_exposure_expansion import run_expansion_plan
from Vol_Suite.opex_calendar import calendar_for_probe, load_snapshot
from Vol_Suite.provenance_contract import canonical_sha256

from test_dealer_exposure_authorization import _authorization, _manifest


H1 = "a" * 64
H2 = "b" * 64


def _auth_and_manifest():
    manifest = _manifest()
    auth = AcquisitionAuthorization.from_mapping(
        _authorization(manifest), candidate_manifest=manifest,
        now="2026-08-15T12:30:00+00:00",
    )
    return auth, manifest


def _prewindow():
    return {
        "candidate_key": "XLE|2026-07-06|2026-07-06|1",
        "calendar_day": "2026-07-06",
        "declared_timezone": "UTC",
        "breach_window_start_prov": "2026-07-06T15:00:00Z",
        "imputed": False,
        "no_imputation": True,
        "delta_iv_pre_window": 0.3 - 0.2,
        "delta_iv_aggregation": "iv_source_minus_iv_before",
        "delta_iv_aggregation_version": "1",
        "source_hashes": [H1, H2],
        "pre_window_observations": [
            {"role": "PRE_WINDOW", "timestamp": "2026-07-06T13:00:00Z", "iv": 0.2, "source_identity": "before", "source_hash": H1},
            {"role": "PRE_WINDOW", "timestamp": "2026-07-06T14:00:00Z", "iv": 0.3, "source_identity": "source", "source_hash": H2},
        ],
    }


def test_strict_prewindow_rejects_role_only_and_accepts_fully_evidenced_control():
    item = _prewindow()
    unit = {"calendar_day": item["calendar_day"], "source_hashes": [H1, H2]}
    registry = {"source_hashes": [H1, H2]}
    assert _strict_prewindow(item, unit, registry) == []
    incomplete = deepcopy(item)
    incomplete["pre_window_observations"] = [{"role": "PRE_WINDOW"}, {"role": "PRE_WINDOW"}]
    assert _strict_prewindow(incomplete, unit, registry)


@pytest.mark.parametrize("field", ["units", "probe_calls", "heavy_calls", "total_endpoint_calls", "payload_bytes", "wall_seconds", "concurrency"])
def test_missing_each_cost_counter_is_not_zero(field):
    auth, manifest = _auth_and_manifest()
    evidence = {"usage": {name: 0 for name in ("units", "probe_calls", "heavy_calls", "total_endpoint_calls", "payload_bytes", "wall_seconds", "concurrency")}}
    evidence["usage"].pop(field)
    _, audit = admit_acquisition(auth, manifest, [], evidence, {})
    assert not audit["admitted"]
    assert any("cost usage counters" in reason for reason in audit["blocked"])


def test_forged_probe_identity_and_shallow_registry_are_blocked():
    auth, manifest = _auth_and_manifest()
    unit = manifest["units"][0]
    probe = {"candidate_key": unit["candidate_key"], "request_parameters": {}, "probe_identity": {"calendar_hash": "forged"}}
    evidence = {"usage": {"units": 0, "probe_calls": 0, "heavy_calls": 0, "total_endpoint_calls": 0, "payload_bytes": 0, "wall_seconds": 0, "concurrency": 1}}
    _, audit = admit_acquisition(auth, manifest, [probe], evidence, {"x": {}})
    assert not audit["admitted"]
    assert any("identity" in reason or "registry" in reason for reason in audit["blocked"])


def test_manifest_projection_mismatch_is_rejected_before_admission():
    auth, manifest = _auth_and_manifest()
    altered = deepcopy(manifest)
    altered["units"][0]["ticker"] = "MSFT"
    _, audit = admit_acquisition(auth, altered, [], {}, {})
    assert not audit["admitted"]
    assert audit["blocked"]


def test_boolean_probe_approval_has_zero_fetcher_calls():
    calls = []
    schedule = [{"candidate_key": "x", "ticker": "X", "calendar_day": "2026-07-06", "expiry": "2026-07-06", "dte": 1,
                 "habitat": "OPEX", "sector": "ENERGY", "candidate_source": "fixture", "calendar_binding": {}}]
    probes = run_availability_probes(schedule, approval=True, dry_run=False,
                                     probe_fetcher=lambda request: calls.append(request))
    assert calls == []
    assert probes[0]["status"] == "HARD_GAP"
    assert probes[0]["invoked"] is False


def test_runtime_context_limits_are_immutable_to_callers():
    auth, manifest = _auth_and_manifest()
    context = _AdmissionContext(auth, manifest)
    with pytest.raises(TypeError):
        context.limits["units"] = 0
    assert context.usage["units"] == 0


def test_runtime_counter_commit_blocks_repeated_calls_immediately():
    auth, manifest = _auth_and_manifest()
    context = _AdmissionContext(auth, manifest)
    calls = []
    context.call("probe", lambda: calls.append("first") or {})
    with pytest.raises(Exception, match="authorization cost ceiling exceeded: units"):
        context.call("probe", lambda: calls.append("second") or {})
    assert calls == ["first"]
    assert context.usage["units"] == 1


def test_zero_unit_limit_stops_before_adapter_dispatch():
    auth, manifest = _auth_and_manifest()
    context = _AdmissionContext(auth, manifest)
    calls = []
    with pytest.raises(TypeError):
        context.limits["units"] = 0
    context.call("heavy", lambda: calls.append(1))
    assert calls == [1]


def test_executor_policy_requires_scope_binding_and_exact_path():
    payload = _authorization()
    del payload["executor_policy"]["scope_binding"]
    with pytest.raises((ValueError, TypeError)):
        AcquisitionAuthorization.from_mapping(payload, candidate_manifest=_manifest(), now="2026-08-15T12:30:00+00:00")


def _network_auth(executor_id=None, executor_entrypoint=None):
    """Build a network-enabled authorization.

    ``executor_id``/``executor_entrypoint`` let a caller align
    ``allowed_executor_id``/``allowed_executor_entrypoint`` with the actual
    identity/endpoint of a ``_registered_adapter()`` handle it intends to
    dispatch, since ``RestrictedExecutor.run()`` now enforces that match
    (review finding 1).  Left unset, the fixture's placeholder values are kept,
    which is fine for tests that never reach a successful dispatch.
    """
    manifest = _manifest()
    payload = _authorization(manifest)
    payload["executor_policy"]["network_fetch_allowed"] = True
    if executor_id is not None:
        payload["executor_policy"]["allowed_executor_id"] = executor_id
    if executor_entrypoint is not None:
        payload["executor_policy"]["allowed_executor_entrypoint"] = executor_entrypoint
    payload["authorization_sha256"] = canonical_sha256({key: value for key, value in payload.items() if key != "authorization_sha256"})
    return AcquisitionAuthorization.from_mapping(payload, candidate_manifest=manifest, now="2026-08-15T12:30:00+00:00"), manifest


def _adapter(**overrides):
    def adapter(_unit):
        return {"status": "SUCCESS", "validated": True, "success": True}
    values = {
        "executor_id": "staged_historical_adapter_v1",
        "entrypoint": "fixture",
        "endpoint": "/hist/option/all_greeks",
        "request_method": "GET",
        "scope_binding": "candidate keys",
    }
    values.update(overrides)
    for key, value in values.items():
        setattr(adapter, key, value)
    return adapter


def _registered_adapter(**overrides):
    class Adapter:
        def __call__(self, _unit):
            return {"status": "SUCCESS", "validated": True, "success": True}
    registry = AdapterRegistry()
    return registry.register(Adapter(), endpoint=overrides.get("endpoint", "/hist/option/all_greeks"), method=overrides.get("request_method", "GET"), scope_binding=overrides.get("scope_binding", "candidate keys"), family="hist/option/all_greeks")


def test_approval_false_blocks_expansion_without_executor_calls():
    auth, _ = _network_auth()
    calls = []
    result = run_expansion_plan([], dry_run=False, approve_network=False, authorization=auth,
                                executor=lambda unit: calls.append(unit))
    assert result["mode"] == "blocked"
    assert result["execution_audit"]["invoked"] == []
    assert calls == []


def test_arbitrary_fetcher_is_rejected_before_acquisition_dispatch():
    auth, _ = _network_auth()
    calls = []
    with pytest.raises(Exception, match="authorized executor required"):
        execute_sequential_acquisition([], fetcher=lambda unit: calls.append(unit), approval=True,
                                      authorization=auth, admission_evidence={}, registry={}, dry_run=False)
    assert calls == []


@pytest.mark.parametrize("field,value", [("scope_binding", "other scope"), ("endpoint", "/hist/option/all_greeks/extra"), ("request_method", "POST")])
def test_wrong_executor_scope_path_or_method_is_rejected(field, value):
    auth, _ = _network_auth()
    ok, _, reason = _authorized_executor(_adapter(**{field: value}), auth)
    assert ok is False
    assert reason


def test_valid_authorized_adapter_matches_all_execution_identity_fields():
    registered = _registered_adapter()
    auth, _ = _network_auth(executor_id=registered._registration.identity, executor_entrypoint=registered._registration.endpoint)
    ok, identity, reason = _authorized_executor(registered, auth)
    assert ok is True
    assert reason == ""
    assert identity["scope_binding"] == "candidate keys"


def test_mismatched_authorized_executor_id_is_rejected_before_dispatch():
    # Review finding 1 (CRITICAL): a registered adapter with a fully allowed
    # path/method/scope must still be rejected if the authorization names a
    # different executor id.
    registered = _registered_adapter()
    auth, _ = _network_auth(executor_id="a-different-executor-id", executor_entrypoint=registered._registration.endpoint)
    ok, _, reason = _authorized_executor(registered, auth)
    assert ok is False
    assert "executor id" in reason


def test_concurrent_second_call_is_rejected_atomically_and_slot_is_released():
    auth, manifest = _network_auth()
    context = _AdmissionContext(auth, manifest)

    entered = threading.Event()
    release = threading.Event()
    calls = []

    def first():
        calls.append("first")
        entered.set()
        assert release.wait(2)
        return {"payload": "first"}

    worker = threading.Thread(target=lambda: context.call("heavy", first))
    worker.start()
    assert entered.wait(2)
    with pytest.raises(Exception, match="concurrency"):
        context.call("heavy", lambda: calls.append("second"))
    assert calls == ["first"]
    assert context.usage["concurrency"] == 1
    release.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert context.usage["concurrency"] == 0
    assert context.usage["concurrency"] == 0
    assert context.usage["payload_bytes"] > 0
    assert context.usage["wall_seconds"] > 0


@pytest.mark.parametrize("approval", [1, "yes", [], {}])
def test_probe_and_acquisition_require_boolean_true_approval(approval):
    auth, manifest = _network_auth()
    schedule = manifest["units"]
    calls = []
    probe = run_availability_probes(
        schedule,
        probe_fetcher=lambda request: calls.append(request),
        approval=approval,
        authorization=auth,
        authorization_context=_AdmissionContext(auth, manifest),
        dry_run=False,
    )
    assert calls == []
    assert all(item["invoked"] is False for item in probe)
    with pytest.raises(Exception, match="explicit authorization handoff"):
        execute_sequential_acquisition(
            schedule, fetcher=_adapter(), approval=approval, authorization=auth,
            admission_evidence={}, registry={}, dry_run=False,
        )


def test_probe_self_reported_success_without_post_dispatch_evidence_has_no_receipt():
    # Review finding 2 (IMPORTANT), exercised through the real
    # run_availability_probes caller: a probe adapter self-reporting
    # validated=True/success=True, with no response_status/counts evidence,
    # must not be admitted and must not carry an executor receipt.
    class SelfReporting:
        def __call__(self, _unit):
            return {"status": "SUCCESS", "validated": True, "success": True}

    registry = AdapterRegistry()
    registered = registry.register(SelfReporting(), endpoint="/hist/option/all_greeks", method="GET", scope_binding="candidate keys", family="hist/option/all_greeks")
    auth, manifest = _network_auth(executor_id=registered._registration.identity, executor_entrypoint=registered._registration.endpoint)
    context = _AdmissionContext(auth, manifest)
    probes = run_availability_probes(
        manifest["units"], probe_fetcher=registered, approval=True, authorization=auth,
        authorization_context=context, dry_run=False, calendar_snapshot=object(),
    )
    probe = probes[0]
    assert probe["status"] != "PASS"
    assert probe["validated"] is False
    assert probe["executor_receipts"] == ()


def _plain(value):
    """Recursively convert Mapping/tuple wrappers to plain dict/list for JSON hashing."""
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _real_calendar_snapshot_and_binding():
    """Build a real ``CalendarSnapshot`` and a real, recomputable probe binding.

    Mirrors the ``payload()`` fixture in ``Vol_Suite/tests/test_opex_calendar.py``
    (kept local rather than imported across test roots) but adds a second event
    record on 2025-01-16 so a real probe binding can be resolved one DTE before
    the observed monthly expiry on 2025-01-17.
    """
    payload = {
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
            "content_sha256": "a" * 64, "parser_version": "1", "selection_reason": "authoritative",
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
        }, {
            "event_id": "fomc-0", "event_type": "FOMC", "event_day": "2025-01-16",
            "window_start": "2025-01-16T09:30:00-05:00", "window_end": "2025-01-16T16:00:00-05:00",
            "anchor": "SCHEDULED", "source_ref": "rule", "surprise_status": "NOT_APPLICABLE",
            "causal_surprise_eligible": False,
        }],
    }
    snapshot = load_snapshot(payload)
    binding = calendar_for_probe(
        snapshot, ticker="XLE", calendar_day="2025-01-16", expiry="2025-01-17",
        dte=1, as_of="2025-01-01T00:00:00Z", window_policy="OPEX_DAY",
    )
    return snapshot, _plain(binding)


_REAL_PROBE_KEY = "XLE|2025-01-16|2025-01-17|1"
_REAL_CAL_H = "d" * 64
_REAL_BINDING_H = "e" * 64
_REAL_SRC_H = "c" * 64
_REAL_PROBE_H = "f" * 64


def _real_probe_unit():
    # These calendar_hash/session_id/calendar_binding_hash values are the
    # authorization-manifest's own internal identity fields (checked for
    # self-consistency between the manifest unit and the dispatched schedule
    # unit by RestrictedExecutor._check_units).  They are deliberately
    # independent of the *real* recomputed calendar_binding object below,
    # which is validated separately by run_availability_probes against the
    # real CalendarSnapshot -- the manifest schema (_UNIT_FIELDS) has no
    # "calendar_binding" field, only "calendar_binding_hash".
    return {
        "candidate_key": _REAL_PROBE_KEY,
        "ticker": "XLE",
        "calendar_day": "2025-01-16",
        "expiry": "2025-01-17",
        "dte": 1,
        "habitat": "OPEX",
        "sector": "ENERGY",
        "candidate_source": "fixture",
        "source_hashes": [_REAL_SRC_H],
        "calendar_hash": _REAL_CAL_H,
        "calendar_policy_version": "opex-calendar-v1",
        "resolver_code_version": "resolver-v1",
        "resolver_code_hash": _REAL_CAL_H,
        "observed_expiry_date": "2025-01-17",
        "session_id": "session-dummy",
        "session_status": "OPEN",
        "regular_open": "2025-01-16T09:30:00-05:00",
        "regular_close": "2025-01-16T16:00:00-05:00",
        "early_close": False,
        "settlement_style": "PM_CLOSE",
        "settlement_timestamp": "2025-01-17T16:00:00-05:00",
        "event_window_id": "window-dummy",
        "window_start": "2025-01-16T09:30:00-05:00",
        "window_end": "2025-01-16T16:00:00-05:00",
        "calendar_binding_hash": _REAL_BINDING_H,
    }


def _real_probe_observed_binding():
    return {
        "candidate_key": _REAL_PROBE_KEY,
        "observed_expiry_date": "2025-01-17",
        "session_id": "session-dummy",
        "session_status": "OPEN",
        "settlement_style": "PM_CLOSE",
        "settlement_timestamp": "2025-01-17T16:00:00-05:00",
        "event_window_id": "window-dummy",
        "window_start": "2025-01-16T09:30:00-05:00",
        "window_end": "2025-01-16T16:00:00-05:00",
        "calendar_binding_hash": _REAL_BINDING_H,
    }


def _real_probe_manifest():
    unit = _real_probe_unit()
    return {
        "schema_version": 1,
        "manifest_schema": "candidate_manifest_calendar_enriched_v2",
        "calendar_enriched": True,
        "units": [unit],
        "exclusions": [],
        "selection_provenance": {"source": "fixture", "source_registry": {"fixture": {"source_hashes": [_REAL_SRC_H]}}},
        "quota": {"max_units": 1},
        "held_pair_evidence_hash": _REAL_CAL_H,
        "probe_policy": {"required_status": "PASS"},
        "executor_policy": {"allowed_executor_id": "staged_historical_adapter_v1"},
        "cost_ceiling": {
            "max_units": 1, "max_probe_calls": 2, "max_heavy_calls": 4,
            "max_total_endpoint_calls": 6, "max_payload_bytes": 20000,
            "max_wall_seconds": 30, "concurrency": 1,
        },
        "calendar": {
            "calendar_hash": _REAL_CAL_H,
            "calendar_policy_version": "opex-calendar-v1",
            "resolver_code_version": "resolver-v1",
            "resolver_code_hash": _REAL_CAL_H,
            "observed_session_id": "session-dummy",
            "calendar_binding_hash": _REAL_BINDING_H,
        },
    }


def _real_probe_authorization(manifest, *, executor_id, executor_entrypoint):
    digest = candidate_manifest_sha256(manifest)
    payload = {
        "schema_version": 1,
        "authorization_id": "auth-real-probe-1",
        "issued_at": "2026-08-15T12:00:00+00:00",
        "expires_at": "2026-08-15T13:00:00+00:00",
        "issued_by": "pm-fixture",
        "purpose": "staged-dealer-exposure-acquisition",
        "environment": "offline-test",
        "candidate_manifest_sha256": digest,
        "candidate_manifest_projection": "candidate_manifest_calendar_enriched_v2",
        "calendar": manifest["calendar"],
        "scope": {
            "candidate_keys": [_REAL_PROBE_KEY],
            "calendar_hash": _REAL_CAL_H,
            "calendar_policy_version": "opex-calendar-v1",
            "resolver_code_version": "resolver-v1",
            "resolver_code_hash": _REAL_CAL_H,
            "observed_bindings": [_real_probe_observed_binding()],
            "ticker_day_pairs": [["XLE", "2025-01-16"]],
            "dte_strata": [[1, 3], [4, 7], [8, 10]],
            "event_habitats": ["OPEX"],
            "event_fraction": 1.0,
            "control_fraction": 0.0,
            "max_arm_ratio": 2.0,
            "held_pair_policy": "reject",
            "no_imputation": True,
            "same_day_aggregation": "preserve_ticker_values_v1",
            "calendar_binding_hash": _REAL_BINDING_H,
        },
        "probe_policy": {
            "required_status": "PASS",
            "required_validated": True,
            "required_invoked": True,
            "required_checks": ["chain"],
            "pre_window_observations_exact": 2,
            "probe_code_hash": _REAL_PROBE_H,
        },
        "cost_ceiling": {
            "max_units": 1, "max_probe_calls": 2, "max_heavy_calls": 4,
            "max_total_endpoint_calls": 6, "max_payload_bytes": 20000,
            "max_wall_seconds": 30, "concurrency": 1, "on_exceed": "stop_and_hard_gap",
        },
        "executor_policy": {
            "allowed_executor_id": executor_id,
            "allowed_executor_entrypoint": executor_entrypoint,
            "allowed_endpoint_families": ["hist/option/all_greeks"],
            "allowed_endpoint_paths": ["/hist/option/all_greeks"],
            "allowed_request_methods": ["GET"],
            "scope_binding": "candidate keys",
            "network_fetch_allowed": True,
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
    return AcquisitionAuthorization.from_mapping(payload, candidate_manifest=manifest, now="2026-08-15T12:30:00+00:00")


def test_run_availability_probes_real_end_to_end_positive_control_emits_receipt():
    # Review finding 2 positive control (d), exercised through the real,
    # unmodified run_availability_probes caller -- not just RestrictedExecutor
    # directly.  The prior attempt at this test was abandoned because someone
    # tried to put "calendar_binding" on an AUTHORIZATION MANIFEST unit, which
    # is genuinely rejected (manifest units only carry "calendar_binding_hash";
    # see Vol_Suite/dealer_exposure_authorization.py::_UNIT_FIELDS). But the
    # *schedule* item passed as run_availability_probes' `schedule` argument is
    # a separate object that is never validated against that manifest schema,
    # and it is exactly where production code (dealer_exposure_universe.py's
    # candidate-schedule builder) already puts the full calendar_binding
    # mapping for the same reason. So a real fixture is possible without
    # touching production schema/logic: build a real CalendarSnapshot, resolve
    # a real probe binding from it, and thread the recomputed binding through
    # the schedule item's "calendar_binding" field (and the adapter's returned
    # evidence) -- exactly the shape run_availability_probes already expects.
    snapshot, binding = _real_calendar_snapshot_and_binding()

    class RealisticProbeAdapter:
        def __init__(self, calendar_binding):
            self._calendar_binding = calendar_binding

        def __call__(self, unit):
            # run_availability_probes matches each dispatched adapter result back
            # to its unit by response["candidate_key"] (see response_by_key in
            # run_availability_probes); a raw adapter response omitting it is
            # never matched to any unit and the probe becomes a HARD_GAP.
            return {
                "candidate_key": unit["candidate_key"],
                "status": "PASS",
                "response_status": 200,
                "counts": {"rows": 1},
                "source_counts": {"theta": 1},
                "calendar_binding": self._calendar_binding,
            }

    registry = AdapterRegistry()
    registered = registry.register(
        RealisticProbeAdapter(binding), endpoint="/hist/option/all_greeks", method="GET",
        scope_binding="candidate keys", family="hist/option/all_greeks",
    )
    manifest = _real_probe_manifest()
    auth = _real_probe_authorization(
        manifest, executor_id=registered._registration.identity,
        executor_entrypoint=registered._registration.endpoint,
    )
    context = _AdmissionContext(auth, manifest)
    schedule_unit = {**_real_probe_unit(), "calendar_binding": binding}

    probes = run_availability_probes(
        [schedule_unit], probe_fetcher=registered, approval=True, authorization=auth,
        authorization_context=context, dry_run=False, calendar_snapshot=snapshot,
    )

    assert len(probes) == 1
    probe = probes[0]
    assert probe["status"] == "PASS", probe.get("reason")
    assert probe["validated"] is True
    assert probe["invoked"] is True
    assert probe["comparison_status"] == "COMPARISON_VALID"
    assert probe["executor_receipts"] != ()
    assert len(probe["executor_receipts"]) == 1
    receipt = probe["executor_receipts"][0]
    assert receipt["candidate_key"] == _REAL_PROBE_KEY
    assert len(receipt["receipt_sha256"]) == 64


def test_probe_arbitrary_callable_is_rejected_before_any_call():
    auth, manifest = _network_auth()
    calls = []
    probe = run_availability_probes(
        manifest["units"],
        probe_fetcher=lambda request: calls.append(request),
        approval=True,
        authorization=auth,
        authorization_context=_AdmissionContext(auth, manifest),
        dry_run=False,
    )
    assert calls == []
    assert probe[0]["invoked"] is False
    assert "authorized probe executor required" in probe[0]["reason"]


def test_expansion_audit_uses_finalized_usage_snapshot(monkeypatch):
    auth, manifest = _network_auth()
    context = _AdmissionContext(auth, manifest)
    monkeypatch.setattr(expansion, "build_expansion_manifest", lambda *args, **kwargs: manifest)
    monkeypatch.setattr(expansion, "admit_acquisition", lambda *args, **kwargs: (tuple(manifest["units"]), {"admitted": True, "blocked": []}))
    monkeypatch.setattr(expansion, "_preflight_authorization", lambda *args, **kwargs: context)
    monkeypatch.setattr(expansion, "candidate_manifest_projection", lambda *args, **kwargs: auth.candidate_manifest_projection())
    result = run_expansion_plan(
        [], dry_run=False, approve_network=True, executor=_registered_adapter(),
        authorization=auth, acquisition_evidence={"probes": []}, registry={},
    )
    usage = result["execution_audit"]["runtime_usage"]
    assert result["execution_audit"]["invoked"] == []
    assert usage["payload_bytes"] == 0
    assert usage["wall_seconds"] >= 0
    assert usage == result["runtime_usage"]


def test_runtime_exception_exposes_finalized_usage_snapshot():
    auth, manifest = _network_auth()
    context = _AdmissionContext(auth, manifest)

    def failing_executor():
        raise RuntimeError("executor exploded")

    with pytest.raises(RuntimeError, match="executor exploded"):
        context.call("heavy", failing_executor)

    usage = context.finalized_usage
    assert usage["units"] == 1
    assert usage["heavy_calls"] == 1
    assert usage["total_endpoint_calls"] == 1
    assert usage["concurrency"] == 0
    assert usage["wall_seconds"] >= 0


def test_admission_rejection_exposes_finalized_usage_snapshot():
    auth, manifest = _auth_and_manifest()
    context = _AdmissionContext(auth, manifest)
    with pytest.raises(TypeError):
        context.finalized_usage["units"] = 99
    with pytest.raises(TypeError):
        context.usage["units"] = 99
    assert context.finalized_usage["units"] == 0


def test_probe_runtime_rejection_is_not_marked_invoked():
    auth, manifest = _network_auth()
    context = _AdmissionContext(auth, manifest)
    unit = dict(manifest["units"][0])
    unit["held_pair_exclusion"] = True
    calls = []

    probe = run_availability_probes(
        [unit], probe_fetcher=_adapter(), approval=True, authorization=auth,
        authorization_context=context, dry_run=False,
    )

    assert calls == []
    assert probe[0]["invoked"] is False
    assert "authorized probe executor required" in probe[0]["reason"]



def test_expansion_executor_exception_audits_finalized_usage(monkeypatch):
    auth, manifest = _network_auth()
    context = _AdmissionContext(auth, manifest)
    monkeypatch.setattr(expansion, "build_expansion_manifest", lambda *args, **kwargs: manifest)
    monkeypatch.setattr(expansion, "admit_acquisition", lambda *args, **kwargs: (tuple(manifest["units"]), {"admitted": True, "blocked": []}))
    monkeypatch.setattr(expansion, "_preflight_authorization", lambda *args, **kwargs: context)
    monkeypatch.setattr(expansion, "candidate_manifest_projection", lambda *args, **kwargs: auth.candidate_manifest_projection())

    def failing_executor(_unit):
        raise RuntimeError("executor exploded")

    authorized = _adapter()
    for name in ("executor_id", "entrypoint", "endpoint", "request_method", "scope_binding"):
        setattr(failing_executor, name, getattr(authorized, name))

    result = run_expansion_plan(
        [], dry_run=False, approve_network=True, executor=failing_executor,
        authorization=auth, acquisition_evidence={"probes": []}, registry={},
    )
    usage = result["execution_audit"]["runtime_usage"]
    assert result["mode"] == "failed-execution"
    assert result["execution_audit"]["invoked"] == []
    assert usage["heavy_calls"] == 0
    assert usage["concurrency"] == 0
    assert usage == result["runtime_usage"]
