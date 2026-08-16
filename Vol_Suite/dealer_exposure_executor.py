"""Fail-closed restricted executor for admitted dealer-exposure units.

This boundary deliberately admits only an opaque adapter registration.  A callable's
self-declared attributes and returned ``calls`` are never authorization evidence.
The in-process adapter is a controlled boundary, not a sandbox: code inside a
trusted registered adapter must still be audited separately.
"""
from __future__ import annotations

import hashlib
import inspect
import marshal
import secrets
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

from .dealer_exposure_acquisition import _AdmissionContext, _context_is_owned_and_initialized
from .dealer_exposure_authorization import AcquisitionAuthorization
from .provenance_contract import canonical_json_bytes, sha256_bytes, validate_source_hashes


class ExecutorFailure(RuntimeError):
    """A static executor-policy or immutable-handoff violation."""


_REGISTRY_CONSTRUCTION_TOKEN = object()
_REGISTRATION_OWNERS: dict[int, "AdapterRegistry"] = {}
_HANDLE_OWNERS: dict[int, "AdapterRegistry"] = {}


class _Registration:
    __slots__ = ("adapter", "registry_key", "code_hash", "identity", "endpoint", "method", "scope", "family", "_token")

    def __init__(self, adapter: Any, *, endpoint: str, method: str, scope: str, family: str, _token: object = None, _owner: "AdapterRegistry" = None) -> None:
        if _token is not _REGISTRY_CONSTRUCTION_TOKEN or _owner is None:
            raise ExecutorFailure("registration construction is private to AdapterRegistry")
        if not callable(adapter) or inspect.isfunction(adapter) or inspect.ismethod(adapter):
            raise ExecutorFailure("only callable adapter objects may be registered")
        if not all(isinstance(v, str) and v.strip() for v in (endpoint, method, scope, family)):
            raise ExecutorFailure("adapter registration identity is incomplete")
        code = getattr(type(adapter).__call__, "__code__", None)
        if code is None:
            raise ExecutorFailure("adapter entrypoint has no attestable code")
        self.adapter = adapter
        self.identity = f"{type(adapter).__module__}:{type(adapter).__qualname__}.__call__"
        self.code_hash = hashlib.sha256(marshal.dumps(code)).hexdigest()
        self.endpoint, self.method, self.scope, self.family = endpoint, method.upper(), scope, family
        self.registry_key = secrets.token_hex(32)
        self._token = object()
        _REGISTRATION_OWNERS[id(self)] = _owner


class RegisteredAdapter:
    """Opaque handle returned by :meth:`AdapterRegistry.register`."""
    __slots__ = ("_registration",)

    def __init__(self, registration: _Registration, token: object, owner: "AdapterRegistry") -> None:
        if not isinstance(registration, _Registration) or _REGISTRATION_OWNERS.get(id(registration)) is not owner:
            raise ExecutorFailure("registration is not owned by AdapterRegistry")
        if token is not registration._token:
            raise ExecutorFailure("invalid adapter registration token")
        self._registration = registration
        _HANDLE_OWNERS[id(self)] = owner

    def __call__(self, unit: Mapping[str, Any]) -> Any:
        raise ExecutorFailure("registered adapter dispatch is restricted to RestrictedExecutor")


class AdapterRegistry:
    """Explicit registry for the only callable boundary the executor admits."""
    __slots__ = ("_entries",)

    def __init__(self) -> None:
        self._entries: dict[str, _Registration] = {}

    def register(self, adapter: Any, *, endpoint: str, method: str, scope_binding: str, family: str) -> RegisteredAdapter:
        entry = _Registration(adapter, endpoint=endpoint, method=method, scope=scope_binding, family=family, _token=_REGISTRY_CONSTRUCTION_TOKEN, _owner=self)
        self._entries[entry.registry_key] = entry
        return RegisteredAdapter(entry, entry._token, self)


def _valid_hash(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value.lower())


def _json_plain(value: Any) -> Any:
    """Recursively convert frozen Mapping/tuple wrappers to plain dict/list.

    Admitted units are deeply frozen to ``MappingProxyType``/``tuple`` before
    reaching the executor (see ``_immutable`` in ``dealer_exposure_acquisition``).
    ``canonical_json_bytes`` uses the stdlib ``json`` module, which cannot
    serialize ``MappingProxyType`` directly, so any field that may itself be a
    nested mapping (e.g. a real ``calendar_binding``) must be converted back to
    plain JSON types before it is embedded in signed request/receipt material.
    This is a pure representation conversion, not a trust boundary: it changes
    nothing about which values are permitted, only how a permitted value is
    serialized.
    """
    if isinstance(value, Mapping):
        return {str(k): _json_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_plain(v) for v in value]
    return value


class RestrictedExecutor:
    """Run one exact registered adapter over one exact admitted candidate scope."""

    def __init__(self, adapter: RegisteredAdapter, authorization: AcquisitionAuthorization) -> None:
        self.adapter = adapter
        self.authorization = authorization
        if not isinstance(authorization, AcquisitionAuthorization):
            raise ExecutorFailure("authenticated authorization is required")
        if not isinstance(adapter, RegisteredAdapter) or not isinstance(adapter._registration, _Registration):
            raise ExecutorFailure("unregistered adapter is forbidden; register it at the controlled boundary")
        if _HANDLE_OWNERS.get(id(adapter)) is None:
            raise ExecutorFailure("fabricated adapter handle is forbidden")
        owner = _HANDLE_OWNERS[id(adapter)]
        if _REGISTRATION_OWNERS.get(id(adapter._registration)) is not owner or owner._entries.get(adapter._registration.registry_key) is not adapter._registration:
            raise ExecutorFailure("adapter handle is not registry-owned")
        self.registration = adapter._registration
        policy = authorization.to_mapping()["executor_policy"]
        paths = tuple(policy["allowed_endpoint_paths"])
        families = tuple(policy["allowed_endpoint_families"])
        family_paths = {str(path).lstrip("/"): str(path) for path in paths}
        if family_paths.get(self.registration.family) != self.registration.endpoint or self.registration.family not in families:
            raise ExecutorFailure("endpoint family/path mapping is not exact")
        if self.registration.endpoint not in paths or self.registration.method not in {str(m).upper() for m in policy["allowed_request_methods"]}:
            raise ExecutorFailure("registered endpoint or method is outside authorization scope")
        if self.registration.scope != policy["scope_binding"]:
            raise ExecutorFailure("registered scope binding is outside authorization scope")
        if policy["network_fetch_allowed"] is not True:
            raise ExecutorFailure("authorization does not permit executor dispatch")
        for name in ("allow_new_candidate_keys", "allow_held_pairs", "allow_live_model_calls", "allow_scheduler_calls", "allow_writes_outside_artifact_root"):
            if policy[name] is not False:
                raise ExecutorFailure(f"unsafe executor permission: {name}")

    @staticmethod
    def _immutable(value: Any) -> bool:
        if isinstance(value, MappingProxyType):
            return all(RestrictedExecutor._immutable(v) for v in value.values())
        if isinstance(value, Mapping):
            return False
        if isinstance(value, (list, bytearray, set)):
            return False
        if isinstance(value, tuple):
            return all(RestrictedExecutor._immutable(v) for v in value)
        return True

    def _check_context(self, context: Any) -> None:
        if not _context_is_owned_and_initialized(context, self.authorization):
            raise ExecutorFailure("authenticated runtime context is required")
        if context.call.__func__ is not _AdmissionContext.call:
            raise ExecutorFailure("runtime context atomic call implementation is detached")

    def _check_executor_identity(self) -> None:
        """Enforce the authorized executor identity/entrypoint on every dispatch path.

        A registry adapter with a merely allowed ``path``/``method``/``scope`` is not
        sufficient authorization to dispatch: the authorization must also name this
        exact registered executor.  This is checked here, inside ``run()``'s
        fail-closed try block, rather than in the constructor, so that a mismatch is
        always surfaced as a structured ``FAILED_EXECUTION``/``HARD_GAP`` audit with
        zero adapter dispatch and never as an uncaught exception, regardless of
        whether the caller pre-validated construction via ``_authorized_executor``.
        """
        policy = self.authorization.to_mapping()["executor_policy"]
        if policy.get("allowed_executor_id") != self.registration.identity:
            raise ExecutorFailure("registered adapter identity is outside the authorized executor id")
        if policy.get("allowed_executor_entrypoint") != self.registration.endpoint:
            raise ExecutorFailure("registered adapter entrypoint is outside the authorized executor entrypoint")

    def _attest_entrypoint(self) -> None:
        entrypoint = getattr(type(self.registration.adapter), "__call__", None)
        code = getattr(entrypoint, "__code__", None)
        if code is None:
            raise ExecutorFailure("registered adapter entrypoint is no longer attestable")
        identity = f"{type(self.registration.adapter).__module__}:{type(self.registration.adapter).__qualname__}.__call__"
        live_hash = hashlib.sha256(marshal.dumps(code)).hexdigest()
        if identity != self.registration.identity or live_hash != self.registration.code_hash:
            raise ExecutorFailure("registered adapter entrypoint attestation changed")

    def _check_units(self, units: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
        if not isinstance(units, tuple) or not self._immutable(units):
            raise ExecutorFailure("admitted units must be a deeply immutable tuple")
        allowed = tuple(self.authorization.scope["candidate_keys"])
        manifest_units = {item["candidate_key"]: item for item in self.authorization.candidate_manifest_projection()["units"]}
        checked: list[Mapping[str, Any]] = []
        seen: set[str] = set()
        for unit in units:
            if not isinstance(unit, MappingProxyType) or not self._immutable(unit):
                raise ExecutorFailure("admitted units must be deeply immutable")
            key = unit.get("candidate_key")
            if not isinstance(key, str) or key not in allowed or key in seen:
                raise ExecutorFailure("candidate scope is not exact")
            expected = manifest_units[key]
            for field in ("ticker", "calendar_day", "expiry", "dte", "habitat", "sector", "calendar_hash", "calendar_binding_hash", "session_id"):
                if unit.get(field) != expected.get(field):
                    raise ExecutorFailure("calendar-bound candidate scope is detached")
            if any(unit.get(flag) is True for flag in ("held_pair_exclusion", "network", "imputed", "scheduler", "live_model")):
                raise ExecutorFailure("held/new/live/scheduler or imputation unit is forbidden")
            for field in ("artifact_hash", "manifest_hash", "authorization_hash", "registry_key"):
                if not _valid_hash(unit.get(field)) and field != "registry_key":
                    raise ExecutorFailure(f"receipt prerequisite {field} is missing or invalid")
            if unit.get("registry_key") != self.registration.registry_key:
                raise ExecutorFailure("unit registry key is detached")
            if not validate_source_hashes(unit.get("source_hashes")):
                raise ExecutorFailure("source hashes are required")
            evidence = unit.get("pre_window_evidence_hashes")
            if not isinstance(evidence, (list, tuple)) or not evidence or any(not _valid_hash(v) for v in evidence):
                raise ExecutorFailure("PRE_WINDOW evidence hashes are required")
            seen.add(key)
            checked.append(unit)
        if tuple(sorted(seen)) != tuple(sorted(allowed)):
            raise ExecutorFailure("candidate scope is incomplete")
        return tuple(checked)

    def _request(self, unit: Mapping[str, Any]) -> dict[str, Any]:
        # Keep the complete candidate/request identity in the signed request
        # material.  In particular, habitat/sector/source must not be inferred
        # later from a candidate key or from an adapter response.
        return {"registry_key": self.registration.registry_key, "entrypoint": self.registration.identity, "entrypoint_code_hash": self.registration.code_hash, "family": self.registration.family, "path": self.registration.endpoint, "method": self.registration.method, "scope_binding": self.registration.scope, "candidate_key": unit["candidate_key"], "ticker": unit.get("ticker"), "calendar_day": unit.get("calendar_day"), "expiry": unit.get("expiry"), "dte": unit.get("dte"), "habitat": unit.get("habitat"), "sector": unit.get("sector"), "candidate_source": unit.get("candidate_source"), "calendar_binding": _json_plain(unit.get("calendar_binding")), "calendar_binding_hash": unit.get("calendar_binding_hash"), "canonical_input_hash": unit.get("canonical_input_hash")}

    def _build_receipt(self, unit: Mapping[str, Any], request: Mapping[str, Any], response: Mapping[str, Any]) -> dict[str, Any]:
        receipt = {"candidate_key": unit["candidate_key"], "request_sha256": request["request_sha256"], "response_payload_sha256": response["payload_sha256"], "source_hashes": list(validate_source_hashes(unit["source_hashes"])), "artifact_hash": unit["artifact_hash"], "manifest_hash": unit["manifest_hash"], "authorization_hash": unit["authorization_hash"], "registry_key": self.registration.registry_key, "entrypoint_code_hash": self.registration.code_hash, "pre_window_evidence_hashes": list(unit["pre_window_evidence_hashes"])}
        required = ("source_hashes", "artifact_hash", "registry_key", "authorization_hash", "manifest_hash", "pre_window_evidence_hashes")
        if any(not receipt.get(k) for k in required):
            raise ExecutorFailure("generated call receipt is incomplete")
        receipt["receipt_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
        return receipt

    _PROBE_RECEIPT_PASSING_STATUSES = frozenset({"PASS"})

    def build_post_validated_probe_receipt(self, unit: Mapping[str, Any], request: Mapping[str, Any], response: Mapping[str, Any]) -> dict[str, Any]:
        """Mint a probe receipt strictly after the caller's post-dispatch validation.

        ``unit``, ``request``, and ``response`` must be the exact admitted unit and
        the matching ``request``/``response`` audit entries returned by a prior
        ``run(..., call_kind="probe")`` dispatch for this candidate.  Callers must
        invoke this only once their own post-dispatch validation (status/success/
        source/count/calendar evidence -- see ``run_availability_probes``) has fully
        passed.  A raw probe response is never receipt-eligible on its own; missing
        or incomplete post-validation evidence must fail closed with no receipt.

        This contract is not enforced purely by caller convention/docstring: the
        method itself also inspects ``response["status"]`` (the same evidence field
        every caller already has, from the executor's own dispatch audit) and
        refuses to mint a receipt unless it is in the allowed passing set.  This is
        deliberately in addition to, not a replacement for, the caller's own fuller
        post-dispatch validation (response_status/counts/source_counts/calendar
        binding) -- a caller that skips its own validation and passes through a
        HARD_GAP/INELIGIBLE response's audit entries still gets no receipt.
        """
        if request.get("candidate_key") != unit.get("candidate_key") or response.get("candidate_key") != unit.get("candidate_key"):
            raise ExecutorFailure("post-validated probe receipt request/response identity is detached")
        status = str(response.get("status", "")).upper()
        if status not in self._PROBE_RECEIPT_PASSING_STATUSES:
            raise ExecutorFailure("post-validated probe receipt requires a passing dispatch status")
        return self._build_receipt(unit, request, response)

    def run(self, units: Sequence[Mapping[str, Any]], context: Any, *, call_kind: str = "heavy") -> dict[str, Any]:
        audit: dict[str, Any] = {"invocations": [], "requests": [], "responses": [], "receipts": [], "finalized_usage": {}}
        if call_kind not in {"probe", "heavy"}:
            return {"status": "FAILED_EXECUTION", "classification": "HARD_GAP", "network_fetch_allowed": False, "reason": "executor call kind must be probe or heavy", "audit": audit}
        results: list[Mapping[str, Any]] = []
        try:
            self._check_context(context)
            self._check_executor_identity()
            admitted = self._check_units(units)
        except BaseException as exc:
            return {"status": "FAILED_EXECUTION", "classification": "HARD_GAP", "network_fetch_allowed": False, "reason": str(exc)[:200], "audit": audit}
        for unit in admitted:
            request = self._request(unit)
            request["request_sha256"] = sha256_bytes(canonical_json_bytes(request))
            audit["requests"].append(request)
            try:
                audit["invocations"].append(unit["candidate_key"])
                self._attest_entrypoint()
                result, _ = context.call(call_kind, lambda u=unit: self.registration.adapter(u))
                if not isinstance(result, Mapping):
                    raise ExecutorFailure("adapter response is not a mapping")
                status = str(result.get("status", "")).upper()
                # Probe adapters return the raw provider response.  Validation
                # (status/count/source/calendar evidence) belongs to the probe
                # contract in acquisition and therefore happens after this
                # dispatch.  Heavy adapters retain the stricter execution
                # contract here.
                if call_kind == "heavy" and (status not in {"SUCCESS", "SUCCEEDED", "PASS", "OK"} or type(result.get("validated")) is not bool or result["validated"] is not True or type(result.get("success")) is not bool or result["success"] is not True):
                    raise ExecutorFailure("adapter returned non-success execution evidence")
                response = {"candidate_key": unit["candidate_key"], "status": status, "payload_sha256": sha256_bytes(canonical_json_bytes(result))}
                audit["responses"].append(response)
                results.append(result)
                # A raw probe response -- including one that self-reports
                # validated=True/success=True -- is never receipt-eligible on
                # its own.  Adapter-returned flags are not authorization
                # evidence.  Probe receipts may only be minted afterwards, by
                # the caller, via build_post_validated_probe_receipt(), once
                # the caller's own post-dispatch validation (status/success/
                # source/count/calendar evidence) has fully passed.  Heavy
                # responses keep the stricter in-line success check above and
                # receive their receipt immediately, since heavy execution has
                # no further post-dispatch validation stage.
                if call_kind == "heavy":
                    receipt = self._build_receipt(unit, request, response)
                    audit["receipts"].append(receipt)
                audit["finalized_usage"] = dict(context.finalized_usage)
            except BaseException as exc:
                audit["finalized_usage"] = dict(getattr(exc, "runtime_usage", context.finalized_usage))
                return {"status": "FAILED_EXECUTION", "classification": "HARD_GAP", "network_fetch_allowed": False, "reason": str(exc)[:200], "audit": audit}
        return {"status": "SUCCESS", "classification": "SUCCESS", "network_fetch_allowed": True, "audit": audit, "results": tuple(results)}


__all__ = ["AdapterRegistry", "ExecutorFailure", "RegisteredAdapter", "RestrictedExecutor"]
