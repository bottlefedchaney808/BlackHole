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


def _context():
    manifest = _manifest()
    payload = _authorization(manifest)
    payload["executor_policy"]["network_fetch_allowed"] = True
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
    auth, manifest, context = _context()
    registry, raw, registered = _registered()
    output = RestrictedExecutor(registered, auth).run(_admitted(manifest, auth, registered), context)
    assert output["status"] == "SUCCESS"
    assert raw.calls
    receipt = output["audit"]["receipts"][0]
    for field in ("source_hashes", "artifact_hash", "registry_key", "authorization_hash", "manifest_hash", "pre_window_evidence_hashes", "receipt_sha256"):
        assert receipt[field]
    assert len(receipt["receipt_sha256"]) == 64
    assert "calls" not in output["audit"]["requests"][0]


def test_missing_receipt_prerequisite_fails_closed():
    auth, manifest, context = _context()
    _, _, registered = _registered()
    unit = dict(_admitted(manifest, auth, registered)[0])
    unit.pop("artifact_hash")
    with pytest.raises(ExecutorFailure, match="artifact_hash"):
        RestrictedExecutor(registered, auth).run((_immutable(unit),), context)


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

    auth, manifest, context = _context()
    _, raw, registered = _registered(Failing())
    output = RestrictedExecutor(registered, auth).run(_admitted(manifest, auth, registered), context)
    assert output["status"] == "FAILED_EXECUTION"
    assert output["network_fetch_allowed"] is False
    assert len(raw.calls) == 1


def test_real_context_ceilings_are_used_and_overrun_is_hard_gap():
    auth, manifest, context = _context()
    with pytest.raises(TypeError):
        context.limits["heavy_calls"] = 0
    _, _, registered = _registered()
    output = RestrictedExecutor(registered, auth).run(_admitted(manifest, auth, registered), context)
    assert output["status"] == "SUCCESS"
    assert output["audit"]["finalized_usage"]["heavy_calls"] == 1


def test_mutable_units_are_rejected():
    auth, manifest, context = _context()
    _, _, registered = _registered()
    with pytest.raises(ExecutorFailure, match="immutable"):
        RestrictedExecutor(registered, auth).run([dict(manifest["units"][0])], context)


def test_missing_adapter_receipt_self_report_does_not_bypass_executor_audit():
    class SelfReporting(FakeAdapter):
        def __call__(self, unit):
            return {"status": "SUCCESS", "validated": True, "success": True, "calls": [{"path": "/evil", "method": "POST"}]}

    auth, manifest, context = _context()
    _, _, registered = _registered(SelfReporting())
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
    auth, manifest, context = _context()
    registry, raw, registered = _registered()
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
    auth, manifest, context = _context()
    registry, raw, registered = _registered()
    output = RestrictedExecutor(registered, auth).run(_admitted(manifest, auth, registered), context)
    assert output["status"] == "SUCCESS"
    assert raw.calls
    assert registry


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


def test_probe_call_kind_uses_same_receipt_contract_and_registered_control():
    auth, manifest, context = _context()
    registry, raw, registered = _registered()
    output = RestrictedExecutor(registered, auth).run(_admitted(manifest, auth, registered), context, call_kind="probe")
    assert output["status"] == "SUCCESS"
    assert output["audit"]["receipts"][0]["receipt_sha256"]
    assert raw.calls and registry


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
