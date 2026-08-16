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

from .dealer_exposure_acquisition import _AdmissionContext
from .dealer_exposure_authorization import AcquisitionAuthorization
from .provenance_contract import canonical_json_bytes, sha256_bytes, validate_source_hashes


class ExecutorFailure(RuntimeError):
    """A static executor-policy or immutable-handoff violation."""


class _Registration:
    __slots__ = ("adapter", "registry_key", "code_hash", "identity", "endpoint", "method", "scope", "family", "_token")

    def __init__(self, adapter: Any, *, endpoint: str, method: str, scope: str, family: str) -> None:
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


class RegisteredAdapter:
    """Opaque handle returned by :meth:`AdapterRegistry.register`."""
    __slots__ = ("_registration",)

    def __init__(self, registration: _Registration, token: object) -> None:
        if token is not registration._token:
            raise ExecutorFailure("invalid adapter registration token")
        self._registration = registration

    def __call__(self, unit: Mapping[str, Any]) -> Any:
        return self._registration.adapter(unit)


class AdapterRegistry:
    """Explicit registry for the only callable boundary the executor admits."""
    __slots__ = ("_entries",)

    def __init__(self) -> None:
        self._entries: dict[str, _Registration] = {}

    def register(self, adapter: Any, *, endpoint: str, method: str, scope_binding: str, family: str) -> RegisteredAdapter:
        entry = _Registration(adapter, endpoint=endpoint, method=method, scope=scope_binding, family=family)
        self._entries[entry.registry_key] = entry
        return RegisteredAdapter(entry, entry._token)


def _valid_hash(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value.lower())


class RestrictedExecutor:
    """Run one exact registered adapter over one exact admitted candidate scope."""

    def __init__(self, adapter: RegisteredAdapter, authorization: AcquisitionAuthorization) -> None:
        self.adapter = adapter
        self.authorization = authorization
        if not isinstance(authorization, AcquisitionAuthorization):
            raise ExecutorFailure("authenticated authorization is required")
        if not isinstance(adapter, RegisteredAdapter) or not isinstance(adapter._registration, _Registration):
            raise ExecutorFailure("unregistered adapter is forbidden; register it at the controlled boundary")
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
        # Exact type and exact method implementation close the duck-typing gap.
        if type(context) is not _AdmissionContext or getattr(context, "_executor_seal", None) is not _AdmissionContext._SEAL:
            raise ExecutorFailure("authenticated runtime context is required")
        if context.authorization is not self.authorization:
            raise ExecutorFailure("runtime context ownership is detached")
        if context.manifest != self.authorization.candidate_manifest_projection():
            raise ExecutorFailure("runtime context manifest is detached")
        if context.call.__func__ is not _AdmissionContext.call:
            raise ExecutorFailure("runtime context atomic call implementation is detached")
        if not isinstance(context.limits, dict) or not isinstance(context.usage, dict) or not hasattr(context, "lock"):
            raise ExecutorFailure("runtime context atomic counters are incomplete")

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
        return {"registry_key": self.registration.registry_key, "entrypoint": self.registration.identity, "entrypoint_code_hash": self.registration.code_hash, "family": self.registration.family, "path": self.registration.endpoint, "method": self.registration.method, "scope_binding": self.registration.scope, "candidate_key": unit["candidate_key"], "ticker": unit.get("ticker"), "calendar_day": unit.get("calendar_day"), "expiry": unit.get("expiry"), "dte": unit.get("dte"), "calendar_binding_hash": unit.get("calendar_binding_hash")}

    def run(self, units: Sequence[Mapping[str, Any]], context: Any) -> dict[str, Any]:
        audit: dict[str, Any] = {"invocations": [], "requests": [], "responses": [], "receipts": [], "finalized_usage": {}}
        try:
            self._check_context(context)
        except ExecutorFailure as exc:
            return {"status": "FAILED_EXECUTION", "classification": "HARD_GAP", "network_fetch_allowed": False, "reason": str(exc)[:200], "audit": audit}
        admitted = self._check_units(units)
        for unit in admitted:
            request = self._request(unit)
            request["request_sha256"] = sha256_bytes(canonical_json_bytes(request))
            audit["requests"].append(request)
            try:
                audit["invocations"].append(unit["candidate_key"])
                result, _ = context.call("heavy", lambda u=unit: self.adapter(u))
                if not isinstance(result, Mapping):
                    raise ExecutorFailure("adapter response is not a mapping")
                status = str(result.get("status", "")).upper()
                if status not in {"SUCCESS", "SUCCEEDED", "PASS", "OK"} or type(result.get("validated")) is not bool or result["validated"] is not True or type(result.get("success")) is not bool or result["success"] is not True:
                    raise ExecutorFailure("adapter returned non-success execution evidence")
                response = {"candidate_key": unit["candidate_key"], "status": status, "payload_sha256": sha256_bytes(canonical_json_bytes(result))}
                audit["responses"].append(response)
                receipt = {"candidate_key": unit["candidate_key"], "request_sha256": request["request_sha256"], "response_payload_sha256": response["payload_sha256"], "source_hashes": list(validate_source_hashes(unit["source_hashes"])), "artifact_hash": unit["artifact_hash"], "manifest_hash": unit["manifest_hash"], "authorization_hash": unit["authorization_hash"], "registry_key": self.registration.registry_key, "entrypoint_code_hash": self.registration.code_hash, "pre_window_evidence_hashes": list(unit["pre_window_evidence_hashes"])}
                required = ("source_hashes", "artifact_hash", "registry_key", "authorization_hash", "manifest_hash", "pre_window_evidence_hashes")
                if any(not receipt.get(k) for k in required):
                    raise ExecutorFailure("generated call receipt is incomplete")
                receipt["receipt_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
                audit["receipts"].append(receipt)
                audit["finalized_usage"] = dict(context.finalized_usage)
            except BaseException as exc:
                audit["finalized_usage"] = dict(getattr(exc, "runtime_usage", context.finalized_usage))
                return {"status": "FAILED_EXECUTION", "classification": "HARD_GAP", "network_fetch_allowed": False, "reason": str(exc)[:200], "audit": audit}
        return {"status": "SUCCESS", "classification": "SUCCESS", "network_fetch_allowed": True, "audit": audit}


__all__ = ["AdapterRegistry", "ExecutorFailure", "RegisteredAdapter", "RestrictedExecutor"]
