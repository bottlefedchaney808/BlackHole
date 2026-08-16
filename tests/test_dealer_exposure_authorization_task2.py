from __future__ import annotations

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
from Vol_Suite.dealer_exposure_authorization import AcquisitionAuthorization
import Vol_Suite.dealer_exposure_expansion as expansion
from Vol_Suite.dealer_exposure_expansion import run_expansion_plan
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


@pytest.mark.parametrize("ceiling,kind", [
    ("max_probe_calls", "probe"), ("max_heavy_calls", "heavy"),
    ("max_total_endpoint_calls", "probe"), ("max_payload_bytes", "probe"),
    ("max_wall_seconds", "probe"), ("concurrency", "probe"),
])
def test_runtime_context_stops_at_each_authorization_ceiling(ceiling, kind):
    auth, manifest = _auth_and_manifest()
    context = _AdmissionContext(auth, manifest)
    limit_key = {"max_probe_calls": "probe_calls", "max_heavy_calls": "heavy_calls", "max_total_endpoint_calls": "total_endpoint_calls", "max_payload_bytes": "payload_bytes", "max_wall_seconds": "wall_seconds", "concurrency": "concurrency"}[ceiling]
    context.limits[limit_key] = 0
    calls = []
    with pytest.raises(Exception, match="authorization cost ceiling exceeded"):
        context.call(kind, lambda: calls.append(1) or {"payload": "x"})
    assert calls == ([] if ceiling in {"max_probe_calls", "max_heavy_calls", "max_total_endpoint_calls", "concurrency"} else [1])
    if calls:
        assert context.usage["units"] == 1
        assert context.usage[f"{kind}_calls"] == 1
        assert context.usage["total_endpoint_calls"] == 1


def test_runtime_counter_commit_blocks_repeated_calls_immediately():
    auth, manifest = _auth_and_manifest()
    context = _AdmissionContext(auth, manifest)
    context.limits.update(units=1, probe_calls=10, total_endpoint_calls=10)
    calls = []
    context.call("probe", lambda: calls.append("first") or {})
    with pytest.raises(Exception, match="authorization cost ceiling exceeded: units"):
        context.call("probe", lambda: calls.append("second") or {})
    assert calls == ["first"]
    assert context.usage["units"] == 1
    assert context.usage["probe_calls"] == 1
    assert context.usage["total_endpoint_calls"] == 1


def test_zero_unit_limit_stops_before_adapter_dispatch():
    auth, manifest = _auth_and_manifest()
    context = _AdmissionContext(auth, manifest)
    context.limits["units"] = 0
    calls = []
    with pytest.raises(Exception, match="authorization cost ceiling exceeded: units"):
        context.call("heavy", lambda: calls.append(1))
    assert calls == []


def test_executor_policy_requires_scope_binding_and_exact_path():
    payload = _authorization()
    del payload["executor_policy"]["scope_binding"]
    with pytest.raises((ValueError, TypeError)):
        AcquisitionAuthorization.from_mapping(payload, candidate_manifest=_manifest(), now="2026-08-15T12:30:00+00:00")


def _network_auth():
    manifest = _manifest()
    payload = _authorization(manifest)
    payload["executor_policy"]["network_fetch_allowed"] = True
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
    auth, _ = _network_auth()
    ok, identity, reason = _authorized_executor(_adapter(), auth)
    assert ok is True
    assert reason == ""
    assert identity["scope_binding"] == "candidate keys"


def test_concurrent_second_call_is_rejected_atomically_and_slot_is_released():
    auth, manifest = _network_auth()
    context = _AdmissionContext(auth, manifest)
    context.limits.update(units=3, heavy_calls=3, total_endpoint_calls=3)
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
    value, usage = context.call("heavy", lambda: {"payload": "third"})
    assert value["payload"] == "third"
    assert usage["concurrency"] == 0
    assert usage["payload_bytes"] > 0
    assert usage["wall_seconds"] > 0


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
        [], dry_run=False, approve_network=True, executor=_adapter(),
        authorization=auth, acquisition_evidence={"probes": []}, registry={},
    )
    usage = result["execution_audit"]["runtime_usage"]
    assert result["execution_audit"]["invoked"] == [manifest["units"][0]["candidate_key"]]
    assert usage["payload_bytes"] > 0
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
    auth, manifest = _network_auth()
    context = _AdmissionContext(auth, manifest)
    context.limits["units"] = 0

    with pytest.raises(Exception, match="authorization cost ceiling exceeded: units") as caught:
        context.call("heavy", lambda: pytest.fail("adapter must not dispatch"))

    assert context.finalized_usage["units"] == 0
    assert context.finalized_usage["heavy_calls"] == 0
    assert context.finalized_usage["concurrency"] == 0
    assert getattr(caught.value, "runtime_usage") == context.finalized_usage


def test_probe_runtime_rejection_is_not_marked_invoked():
    auth, manifest = _network_auth()
    context = _AdmissionContext(auth, manifest)
    context.limits["units"] = 0
    unit = dict(manifest["units"][0])
    unit["held_pair_exclusion"] = True
    calls = []

    probe = run_availability_probes(
        [unit], probe_fetcher=_adapter(), approval=True, authorization=auth,
        authorization_context=context, dry_run=False,
    )

    assert calls == []
    assert probe[0]["invoked"] is False
    assert "authorization cost ceiling exceeded: units" in probe[0]["reason"]
    assert probe[0]["runtime_usage"] == context.finalized_usage


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
    assert result["execution_audit"]["invoked"] == [manifest["units"][0]["candidate_key"]]
    assert usage["heavy_calls"] == 1
    assert usage["concurrency"] == 0
    assert usage == result["runtime_usage"]
