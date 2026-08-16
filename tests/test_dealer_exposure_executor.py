from __future__ import annotations

from types import MappingProxyType

import pytest

from Vol_Suite.dealer_exposure_executor import (
    RestrictedExecutor,
    ExecutorFailure,
)
from Vol_Suite.dealer_exposure_acquisition import _AdmissionContext, _immutable
from Vol_Suite.dealer_exposure_authorization import AcquisitionAuthorization
from test_dealer_exposure_authorization import _authorization, _manifest


def _context():
    manifest = _manifest()
    auth_payload = _authorization(manifest)
    auth_payload["executor_policy"]["network_fetch_allowed"] = True
    from Vol_Suite.provenance_contract import canonical_sha256
    auth_payload["authorization_sha256"] = canonical_sha256({k: v for k, v in auth_payload.items() if k != "authorization_sha256"})
    auth = AcquisitionAuthorization.from_mapping(auth_payload, candidate_manifest=manifest, now="2026-08-15T12:30:00+00:00")
    return auth, manifest, _AdmissionContext(auth, manifest)


class FakeAdapter:
    executor_id = "staged_historical_adapter_v1"
    entrypoint = "fixture"
    endpoint = "/hist/option/all_greeks"
    request_method = "GET"
    scope_binding = "candidate keys"

    def __init__(self, result=None):
        self.result = result or {"status": "SUCCESS", "validated": True, "success": True}
        self.calls = []

    def __call__(self, unit):
        self.calls.append(unit)
        return self.result


def _admitted(manifest):
    return tuple(_immutable(dict(unit)) for unit in manifest["units"])


def test_wrapper_requires_immutable_admitted_units_and_authenticated_context():
    auth, manifest, context = _context()
    adapter = FakeAdapter()
    wrapper = RestrictedExecutor(adapter, auth)
    with pytest.raises(ExecutorFailure, match="immutable"):
        wrapper.run([manifest["units"][0]], context)


def test_wrapper_records_invocation_request_status_and_hashes():
    auth, manifest, context = _context()
    adapter = FakeAdapter()
    unit = _admitted(manifest)[0]
    result = RestrictedExecutor(adapter, auth).run((unit,), context)
    assert result["status"] == "SUCCESS"
    assert result["network_fetch_allowed"] is True
    assert result["audit"]["invocations"] == [unit["candidate_key"]]
    assert result["audit"]["requests"][0]["path"] == "/hist/option/all_greeks"
    assert result["audit"]["requests"][0]["method"] == "GET"
    assert len(result["audit"]["requests"][0]["request_sha256"]) == 64
    assert len(result["audit"]["responses"][0]["payload_sha256"]) == 64


def test_wrapper_stops_after_first_adapter_exception():
    auth, manifest, context = _context()
    class Failing(FakeAdapter):
        def __call__(self, unit):
            self.calls.append(unit)
            raise RuntimeError("boom")
    adapter = Failing()
    units = _admitted(manifest)
    output = RestrictedExecutor(adapter, auth).run(units, context)
    assert output["status"] == "FAILED_EXECUTION"
    assert output["classification"] == "HARD_GAP"
    assert output["network_fetch_allowed"] is False
    assert len(adapter.calls) == 1


def test_wrapper_rejects_wrong_endpoint():
    auth, _, context = _context()
    adapter = FakeAdapter()
    adapter.endpoint = "/hist/option/all_greeks/extra"
    with pytest.raises(ExecutorFailure, match="endpoint"):
        RestrictedExecutor(adapter, auth)


def test_wrapper_rejects_new_and_held_units():
    auth, manifest, context = _context()
    unit = dict(manifest["units"][0])
    unit["candidate_key"] = "new"
    with pytest.raises(ExecutorFailure, match="candidate scope"):
        RestrictedExecutor(FakeAdapter(), auth).run((_immutable(unit),), context)


@pytest.mark.parametrize("field", ["max_heavy_calls", "max_total_endpoint_calls", "max_payload_bytes", "max_wall_seconds"])
def test_wrapper_returns_hard_gap_on_ceiling(field):
    auth, manifest, context = _context()
    context.limits[{"max_heavy_calls": "heavy_calls", "max_total_endpoint_calls": "total_endpoint_calls", "max_payload_bytes": "payload_bytes", "max_wall_seconds": "wall_seconds"}[field]] = 0
    output = RestrictedExecutor(FakeAdapter(), auth).run(_admitted(manifest), context)
    assert output["status"] == "FAILED_EXECUTION"
    assert output["classification"] == "HARD_GAP"
    assert output["network_fetch_allowed"] is False
    assert output["audit"]["finalized_usage"] == context.finalized_usage


def test_wrapper_rejects_plain_arbitrary_callable():
    auth, _, _ = _context()
    with pytest.raises(ExecutorFailure, match="named adapter"):
        RestrictedExecutor(lambda unit: {}, auth)