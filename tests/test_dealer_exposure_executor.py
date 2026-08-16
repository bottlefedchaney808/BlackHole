from __future__ import annotations

from types import MappingProxyType, SimpleNamespace

import pytest

import Vol_Suite.dealer_exposure_acquisition as acquisition
from Vol_Suite.dealer_exposure_executor import AdapterRegistry, ExecutorFailure, RestrictedExecutor
from Vol_Suite.dealer_exposure_acquisition import _AdmissionContext, _immutable
from Vol_Suite.dealer_exposure_authorization import AcquisitionAuthorization, candidate_manifest_sha256
from Vol_Suite.provenance_contract import canonical_sha256
from test_dealer_exposure_authorization import _authorization, _manifest

H = "d" * 64


def _context(registered=None, *, executor_id=None, executor_entrypoint=None):
    """Build an authorization/context pair.

    By default, ``allowed_executor_id``/``allowed_executor_entrypoint`` are set to
    match ``registered``'s actual registration identity/endpoint exactly, so a
    "happy path" caller that passes its registered adapter here gets a matching,
    dispatch-eligible authorization.  Explicit ``executor_id``/``executor_entrypoint``
    overrides let adversarial tests force a mismatch.
    """
    manifest = _manifest()
    payload = _authorization(manifest)
    payload["executor_policy"]["network_fetch_allowed"] = True
    if executor_id is not None:
        payload["executor_policy"]["allowed_executor_id"] = executor_id
    elif registered is not None:
        payload["executor_policy"]["allowed_executor_id"] = registered._registration.identity
    if executor_entrypoint is not None:
        payload["executor_policy"]["allowed_executor_entrypoint"] = executor_entrypoint
    elif registered is not None:
        payload["executor_policy"]["allowed_executor_entrypoint"] = registered._registration.endpoint
    payload["authorization_sha256"] = canonical_sha256({k: v for k, v in payload.items() if k != "authorization_sha256"})
    auth = AcquisitionAuthorization.from_mapping(payload, candidate_manifest=manifest, now="2026-08-15T12:30:00+00:00")
    return auth, manifest, _AdmissionContext(auth, manifest)


class FakeAdapter:
    def __init__(self, result=None):
        self.result = result or {"status": "SUCCESS", "validated": True, "success": True}
        self.calls = []

    def __call__(self, unit):
        self.calls.append(unit)
        return self.result


def _registered(adapter=None):
    registry = AdapterRegistry()
    raw = adapter or FakeAdapter()
    return registry, raw, registry.register(raw, endpoint="/hist/option/all_greeks", method="GET", scope_binding="candidate keys", family="hist/option/all_greeks")


def _admitted(manifest, auth, registered):
    unit = dict(manifest["units"][0])
    unit.update({
        "artifact_hash": H,
        "manifest_hash": candidate_manifest_sha256(manifest),
        "authorization_hash": auth.authorization_sha256(),
        "registry_key": registered._registration.registry_key,
        "pre_window_evidence_hashes": [H],
    })
    return (_immutable(unit),)


def test_forged_duck_context_is_hard_gap_without_adapter_dispatch():
    auth, manifest, _ = _context()
    registry, raw, registered = _registered()
    fake = SimpleNamespace(authorization=auth, manifest=auth.candidate_manifest_projection(), call=lambda *_: raw(None), finalized_usage={})
    output = RestrictedExecutor(registered, auth).run(_admitted(manifest, auth, registered), fake)
    assert output["classification"] == "HARD_GAP"
    assert raw.calls == []


def test_unregistered_or_malicious_callable_is_rejected():
    auth, _, _ = _context()
    with pytest.raises(ExecutorFailure, match="unregistered"):
        RestrictedExecutor(FakeAdapter(), auth)


def test_self_declared_metadata_mismatch_cannot_authorize_callable():
    auth, _, _ = _context()
    raw = FakeAdapter()
    raw.executor_id = "staged_historical_adapter_v1"
    raw.endpoint = "/hist/option/all_greeks"
    with pytest.raises(ExecutorFailure, match="unregistered"):
        RestrictedExecutor(raw, auth)


def test_valid_registered_fake_adapter_emits_executor_receipt_and_all_hashes():
    registry, raw, registered = _registered()
    auth, manifest, context = _context(registered)
    output = RestrictedExecutor(registered, auth).run(_admitted(manifest, auth, registered), context)
    assert output["status"] == "SUCCESS"
    assert raw.calls
    receipt = output["audit"]["receipts"][0]
    for field in ("source_hashes", "artifact_hash", "registry_key", "authorization_hash", "manifest_hash", "pre_window_evidence_hashes", "receipt_sha256"):
        assert receipt[field]
    assert len(receipt["receipt_sha256"]) == 64
    assert "calls" not in output["audit"]["requests"][0]


def test_missing_receipt_prerequisite_fails_closed_without_uncaught_exception():
    _, raw, registered = _registered()
    auth, manifest, context = _context(registered)
    unit = dict(_admitted(manifest, auth, registered)[0])
    unit.pop("artifact_hash")
    output = RestrictedExecutor(registered, auth).run((_immutable(unit),), context)
    assert output["status"] == "FAILED_EXECUTION"
    assert output["classification"] == "HARD_GAP"
    assert "artifact_hash" in output["reason"]
    assert raw.calls == []


@pytest.mark.parametrize("field,value", [("endpoint", "/hist/option/all_greeks/extra"), ("method", "POST"), ("family", "hist/option/other")])
def test_wrong_family_path_or_method_rejected(field, value):
    auth, _, _ = _context()
    registry = AdapterRegistry()
    kwargs = {"endpoint": "/hist/option/all_greeks", "method": "GET", "scope_binding": "candidate keys", "family": "hist/option/all_greeks"}
    kwargs[field] = value
    registered = registry.register(FakeAdapter(), **kwargs)
    with pytest.raises(ExecutorFailure):
        RestrictedExecutor(registered, auth)


def test_wrong_scope_rejected():
    auth, _, _ = _context()
    registered = AdapterRegistry().register(FakeAdapter(), endpoint="/hist/option/all_greeks", method="GET", scope_binding="other scope", family="hist/option/all_greeks")
    with pytest.raises(ExecutorFailure, match="scope"):
        RestrictedExecutor(registered, auth)


def test_exception_hard_stops_after_first_call():
    class Failing(FakeAdapter):
        def __call__(self, unit):
            self.calls.append(unit)
            raise RuntimeError("boom")

    _, raw, registered = _registered(Failing())
    auth, manifest, context = _context(registered)
    output = RestrictedExecutor(registered, auth).run(_admitted(manifest, auth, registered), context)
    assert output["status"] == "FAILED_EXECUTION"
    assert output["network_fetch_allowed"] is False
    assert len(raw.calls) == 1


def test_real_context_ceilings_are_used_and_overrun_is_hard_gap():
    _, _, registered = _registered()
    auth, manifest, context = _context(registered)
    with pytest.raises(TypeError):
        context.limits["heavy_calls"] = 0
    output = RestrictedExecutor(registered, auth).run(_admitted(manifest, auth, registered), context)
    assert output["status"] == "SUCCESS"
    assert output["audit"]["finalized_usage"]["heavy_calls"] == 1


def test_mutable_units_are_rejected_without_uncaught_exception():
    _, raw, registered = _registered()
    auth, manifest, context = _context(registered)
    output = RestrictedExecutor(registered, auth).run([dict(manifest["units"][0])], context)
    assert output["status"] == "FAILED_EXECUTION"
    assert output["classification"] == "HARD_GAP"
    assert "immutable" in output["reason"]
    assert raw.calls == []


@pytest.mark.parametrize("mutation,expected", [
    ("held_pair_exclusion", "held"),
    ("registry_key", "registry key"),
    ("candidate_key", "candidate scope"),
])
def test_malformed_held_or_detached_units_fail_closed_without_uncaught_exception(mutation, expected):
    _, raw, registered = _registered()
    auth, manifest, context = _context(registered)
    unit = dict(_admitted(manifest, auth, registered)[0])
    if mutation == "held_pair_exclusion":
        unit[mutation] = True
    elif mutation == "registry_key":
        unit[mutation] = "e" * 64
    else:
        unit[mutation] = "detached-candidate"
    output = RestrictedExecutor(registered, auth).run((_immutable(unit),), context)
    assert output["status"] == "FAILED_EXECUTION"
    assert output["classification"] == "HARD_GAP"
    assert expected in output["reason"]
    assert raw.calls == []


def test_probe_accepts_raw_response_before_post_validation_and_emits_no_premature_receipt():
    _, raw, registered = _registered(FakeAdapter({"status": "PASS", "response_status": 200, "counts": {"rows": 1}, "source_counts": {"theta": 1}}))
    auth, manifest, context = _context(registered)
    output = RestrictedExecutor(registered, auth).run(_admitted(manifest, auth, registered), context, call_kind="probe")
    assert output["status"] == "SUCCESS"
    assert output["results"][0]["status"] == "PASS"
    assert output["audit"]["receipts"] == []
    assert raw.calls


def test_invalid_non_mapping_probe_response_is_structured_failure_without_dispatch_leak():
    _, raw, registered = _registered(FakeAdapter(["invalid"]))
    auth, manifest, context = _context(registered)
    output = RestrictedExecutor(registered, auth).run(_admitted(manifest, auth, registered), context, call_kind="probe")
    assert output["status"] == "FAILED_EXECUTION"
    assert output["classification"] == "HARD_GAP"
    assert raw.calls


def test_missing_adapter_receipt_self_report_does_not_bypass_executor_audit():
    class SelfReporting(FakeAdapter):
        def __call__(self, unit):
            return {"status": "SUCCESS", "validated": True, "success": True, "calls": [{"path": "/evil", "method": "POST"}]}

    _, _, registered = _registered(SelfReporting())
    auth, manifest, context = _context(registered)
    output = RestrictedExecutor(registered, auth).run(_admitted(manifest, auth, registered), context)
    assert output["status"] == "SUCCESS"
    assert output["audit"]["receipts"]
    assert output["audit"]["requests"][0]["path"] == "/hist/option/all_greeks"


def test_registry_entrypoint_code_hash_is_attested():
    auth, _, _ = _context()
    _, _, registered = _registered()
    assert len(registered._registration.code_hash) == 64
    assert registered._registration.identity.endswith(".__call__")
    RestrictedExecutor(registered, auth)


def test_context_requires_exact_admission_class():
    auth, _, _ = _context()
    registry, _, registered = _registered()
    forged = type("FakeContext", (), {})()
    output = RestrictedExecutor(registered, auth).run((), forged)
    assert output["classification"] == "HARD_GAP"
    assert registry


def test_object_new_context_forgery_is_hard_gap_without_dispatch():
    auth, manifest, _ = _context()
    registry, raw, registered = _registered()
    forged = object.__new__(_AdmissionContext)
    output = RestrictedExecutor(registered, auth).run(_admitted(manifest, auth, registered), forged)
    assert output["classification"] == "HARD_GAP"
    assert raw.calls == []


def test_directly_fabricated_registration_handle_is_hard_gap_without_dispatch():
    auth, manifest, _ = _context()
    registry, raw, registered = _registered()
    forged = object.__new__(type(registered))
    object.__setattr__(forged, "_registration", registered._registration)
    with pytest.raises(ExecutorFailure, match="adapter handle is not registry-owned|fabricated adapter handle"):
        RestrictedExecutor(forged, auth)
    assert raw.calls == []
    assert registry


def test_post_registration_entrypoint_mutation_is_hard_gap_without_dispatch():
    registry, raw, registered = _registered()
    auth, manifest, context = _context(registered)
    original = type(raw).__call__
    try:
        def mutated(self, unit):
            self.calls.append(unit)
            return {"status": "SUCCESS", "validated": True, "success": True}
        type(raw).__call__ = mutated
        output = RestrictedExecutor(registered, auth).run(_admitted(manifest, auth, registered), context)
    finally:
        type(raw).__call__ = original
    assert output["classification"] == "HARD_GAP"
    assert raw.calls == []
    assert registry


def test_valid_registry_context_control_still_dispatches():
    registry, raw, registered = _registered()
    auth, manifest, context = _context(registered)
    output = RestrictedExecutor(registered, auth).run(_admitted(manifest, auth, registered), context)
    assert output["status"] == "SUCCESS"
    assert raw.calls
    assert registry


def test_mismatched_allowed_executor_id_is_structured_failure_with_zero_dispatch():
    # Review finding 1 (CRITICAL): a registry adapter with a merely allowed
    # path/method/scope must not be able to dispatch when the authorization
    # names a different executor id.  This must come back as a structured
    # FAILED_EXECUTION/HARD_GAP audit, not an uncaught exception, and the
    # adapter must never be invoked.
    _, raw, registered = _registered()
    auth, manifest, context = _context(registered, executor_id="some-other-executor-id")
    output = RestrictedExecutor(registered, auth).run(_admitted(manifest, auth, registered), context)
    assert output["status"] == "FAILED_EXECUTION"
    assert output["classification"] == "HARD_GAP"
    assert output["network_fetch_allowed"] is False
    assert "executor id" in output["reason"]
    assert raw.calls == []
    assert output["audit"]["receipts"] == []


def test_mismatched_allowed_executor_entrypoint_is_structured_failure_with_zero_dispatch():
    # Review finding 1 (CRITICAL), entrypoint half of the identity check.
    _, raw, registered = _registered()
    auth, manifest, context = _context(registered, executor_entrypoint="/some/other/endpoint")
    output = RestrictedExecutor(registered, auth).run(_admitted(manifest, auth, registered), context)
    assert output["status"] == "FAILED_EXECUTION"
    assert output["classification"] == "HARD_GAP"
    assert output["network_fetch_allowed"] is False
    assert "executor entrypoint" in output["reason"]
    assert raw.calls == []
    assert output["audit"]["receipts"] == []


@pytest.mark.parametrize("call_kind", ["probe", "heavy"])
def test_mismatched_executor_identity_blocks_both_probe_and_heavy_dispatch(call_kind):
    _, raw, registered = _registered()
    auth, manifest, context = _context(registered, executor_id="wrong-id")
    output = RestrictedExecutor(registered, auth).run(_admitted(manifest, auth, registered), context, call_kind=call_kind)
    assert output["status"] == "FAILED_EXECUTION"
    assert output["classification"] == "HARD_GAP"
    assert raw.calls == []


@pytest.mark.parametrize("mutation", ["reset", "rollback", "lock", "limits", "blocked"])
def test_backing_context_state_tamper_is_hard_gap_before_dispatch(mutation):
    auth, manifest, context = _context()
    registry, raw, registered = _registered()
    state = acquisition._CONTEXT_STATES[id(context)]
    if mutation == "reset":
        state["usage"] = {field: 0 for field in acquisition._CONTEXT_FIELDS}
        state["finalized_usage"] = dict(state["usage"])
        state["blocked"] = None
    elif mutation == "rollback":
        state["usage"]["heavy_calls"] = -1
    elif mutation == "lock":
        import threading
        state["lock"] = threading.Lock()
    elif mutation == "limits":
        state["limits"]["heavy_calls"] = 999
    else:
        state["usage"]["heavy_calls"] = 1
        state["finalized_usage"] = dict(state["usage"])
        state["blocked"] = "prior failure"
        state["blocked"] = None
    output = RestrictedExecutor(registered, auth).run(_admitted(manifest, auth, registered), context)
    assert output["classification"] == "HARD_GAP"
    assert raw.calls == []


def test_probe_call_kind_self_reported_success_is_not_receipt_eligible_and_registered_control_still_dispatches():
    # Contract-transition update (review finding 2): a probe adapter that
    # self-reports validated=True/success=True -- the same default FakeAdapter
    # result used for heavy dispatch -- must NOT receive a receipt from run()
    # alone.  This replaces the prior version of this test, which asserted the
    # opposite (a receipt was emitted directly off that adapter self-report);
    # that assertion exercised exactly the broken trust behavior finding 2
    # closes, so it is corrected rather than kept as coverage.
    registry, raw, registered = _registered()
    auth, manifest, context = _context(registered)
    output = RestrictedExecutor(registered, auth).run(_admitted(manifest, auth, registered), context, call_kind="probe")
    assert output["status"] == "SUCCESS"
    assert output["audit"]["receipts"] == []
    assert raw.calls and registry


def test_build_post_validated_probe_receipt_succeeds_only_after_caller_validation():
    _, _, registered = _registered(FakeAdapter({"status": "PASS", "response_status": 200, "counts": {"rows": 1}, "source_counts": {"theta": 1}}))
    auth, manifest, context = _context(registered)
    executor = RestrictedExecutor(registered, auth)
    admitted = _admitted(manifest, auth, registered)
    output = executor.run(admitted, context, call_kind="probe")
    assert output["status"] == "SUCCESS"
    assert output["audit"]["receipts"] == []
    unit = admitted[0]
    request = output["audit"]["requests"][0]
    response = output["audit"]["responses"][0]
    # Only after this stands in for the caller's own post-dispatch validation
    # (status/success/source/count/calendar evidence, done in
    # run_availability_probes) may a receipt be minted.
    receipt = executor.build_post_validated_probe_receipt(unit, request, response)
    assert len(receipt["receipt_sha256"]) == 64
    assert receipt["candidate_key"] == unit["candidate_key"]
    with pytest.raises(ExecutorFailure, match="detached"):
        executor.build_post_validated_probe_receipt(unit, {**request, "candidate_key": "other"}, response)


def test_mapping_proxy_is_deeply_immutable():
    assert RestrictedExecutor._immutable(MappingProxyType({"x": ("y",)}))
    assert not RestrictedExecutor._immutable({"x": 1})


def test_registry_key_is_opaque_and_not_self_declared():
    registry, raw, registered = _registered()
    assert registered._registration.registry_key not in vars(raw) if hasattr(raw, "__dict__") else True
    assert registry


__all__ = []


def _unused():
    return _AdmissionContext, _immutable


if __name__ == "__main__":
    pytest.main()


# Keep an explicit reference to the imported canonical helper in this fixture-only test module.
assert canonical_sha256 is not None
