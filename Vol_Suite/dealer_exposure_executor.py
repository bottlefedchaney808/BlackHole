"""Restricted, fail-closed executor for admitted dealer-exposure units.

The executor is intentionally a small policy boundary.  It accepts an immutable
admission handoff and the opaque runtime context minted by acquisition; it does
not accept endpoint strings, counters, approval booleans, or arbitrary functions
from a caller.
"""
from __future__ import annotations

import inspect
from types import MappingProxyType
from collections.abc import Mapping, Sequence
from typing import Any

from .dealer_exposure_authorization import AcquisitionAuthorization
from .provenance_contract import canonical_json_bytes, sha256_bytes


class ExecutorFailure(RuntimeError):
    """A static executor-policy or immutable-handoff violation."""


class RestrictedExecutor:
    """Run one exact named adapter over one exact admitted candidate scope."""

    def __init__(self, adapter: Any, authorization: AcquisitionAuthorization) -> None:
        self.adapter = adapter
        self.authorization = authorization
        if not isinstance(authorization, AcquisitionAuthorization):
            raise ExecutorFailure("authenticated authorization is required")
        if inspect.isfunction(adapter) or inspect.ismethod(adapter) or not callable(adapter):
            raise ExecutorFailure("a named adapter object is required; arbitrary callables are forbidden")
        policy = authorization.to_mapping()["executor_policy"]
        self.identity = {
            "executor_id": getattr(adapter, "executor_id", None),
            "entrypoint": getattr(adapter, "entrypoint", None),
            "endpoint": getattr(adapter, "endpoint", None),
            "method": getattr(adapter, "request_method", getattr(adapter, "method", None)),
            "scope_binding": getattr(adapter, "scope_binding", None),
        }
        if any(not isinstance(v, str) or not v.strip() for v in self.identity.values()):
            raise ExecutorFailure("named adapter identity is incomplete")
        if self.identity["executor_id"] != policy["allowed_executor_id"]:
            raise ExecutorFailure("executor ID is outside authorization scope")
        if self.identity["entrypoint"] != policy["allowed_executor_entrypoint"]:
            raise ExecutorFailure("executor entrypoint is outside authorization scope")
        if self.identity["endpoint"] not in policy["allowed_endpoint_paths"]:
            raise ExecutorFailure("executor endpoint path is outside authorization scope")
        if "?" in self.identity["endpoint"] or "#" in self.identity["endpoint"]:
            raise ExecutorFailure("dynamic endpoint expansion is forbidden")
        if self.identity["method"].upper() not in {m.upper() for m in policy["allowed_request_methods"]}:
            raise ExecutorFailure("executor request method is outside authorization scope")
        if self.identity["scope_binding"] != policy["scope_binding"]:
            raise ExecutorFailure("executor scope binding is outside authorization scope")
        for name in ("allow_new_candidate_keys", "allow_held_pairs", "allow_live_model_calls",
                     "allow_scheduler_calls", "allow_writes_outside_artifact_root"):
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
        if context is None or getattr(context, "authorization", None) is not self.authorization:
            raise ExecutorFailure("authenticated runtime context is required")
        if not hasattr(context, "call") or not hasattr(context, "finalized_usage"):
            raise ExecutorFailure("authenticated runtime context is incomplete")
        try:
            if context.manifest != self.authorization.candidate_manifest_projection():
                raise ExecutorFailure("runtime context manifest is detached")
        except AttributeError as exc:
            raise ExecutorFailure("authenticated runtime context is incomplete") from exc

    def _check_units(self, units: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
        if not isinstance(units, (tuple, list)):
            raise ExecutorFailure("admitted units must be an immutable sequence")
        if not isinstance(units, tuple) or not self._immutable(units):
            raise ExecutorFailure("admitted units must be deeply immutable")
        allowed = tuple(self.authorization.scope["candidate_keys"])
        manifest_units = {item["candidate_key"]: item for item in self.authorization.candidate_manifest_projection()["units"]}
        seen: set[str] = set()
        checked: list[Mapping[str, Any]] = []
        for unit in units:
            if not isinstance(unit, MappingProxyType) or not self._immutable(unit):
                raise ExecutorFailure("admitted units must be deeply immutable")
            key = unit.get("candidate_key")
            if not isinstance(key, str) or key not in allowed or key in seen:
                raise ExecutorFailure("candidate scope is not exact")
            expected = manifest_units[key]
            for field in ("ticker", "calendar_day", "expiry", "dte", "habitat", "sector",
                          "calendar_hash", "calendar_binding_hash", "session_id"):
                if unit.get(field) != expected.get(field):
                    raise ExecutorFailure("calendar-bound candidate scope is detached")
            if unit.get("held_pair_exclusion") is True or unit.get("network") is True or unit.get("imputed") is True:
                raise ExecutorFailure("held/new/live/scheduler or imputation unit is forbidden")
            if unit.get("scheduler") is True or unit.get("live_model") is True:
                raise ExecutorFailure("scheduler/live model unit is forbidden")
            seen.add(key)
            checked.append(unit)
        if tuple(sorted(seen)) != tuple(sorted(allowed)):
            raise ExecutorFailure("candidate scope is incomplete")
        return tuple(checked)

    def _request(self, unit: Mapping[str, Any]) -> dict[str, Any]:
        return {"executor_id": self.identity["executor_id"], "entrypoint": self.identity["entrypoint"],
                "path": self.identity["endpoint"], "method": self.identity["method"].upper(),
                "scope_binding": self.identity["scope_binding"], "candidate_key": unit["candidate_key"],
                "ticker": unit.get("ticker"), "calendar_day": unit.get("calendar_day"),
                "expiry": unit.get("expiry"), "dte": unit.get("dte"),
                "calendar_binding_hash": unit.get("calendar_binding_hash")}

    def run(self, units: Sequence[Mapping[str, Any]], context: Any) -> dict[str, Any]:
        """Execute serially; return a finalized audit even after the first failure."""
        audit: dict[str, Any] = {"invocations": [], "requests": [], "responses": [],
                                 "finalized_usage": {}}
        try:
            self._check_context(context)
            admitted = self._check_units(units)
        except ExecutorFailure:
            raise
        for unit in admitted:
            request = self._request(unit)
            request["request_sha256"] = sha256_bytes(canonical_json_bytes(request))
            audit["requests"].append(request)
            try:
                audit["invocations"].append(unit["candidate_key"])
                result, _usage = context.call("heavy", lambda u=unit: self.adapter(u))
                captured_calls = result.get("calls", ()) if isinstance(result, Mapping) else ()
                if captured_calls:
                    if not isinstance(captured_calls, (list, tuple)):
                        raise ExecutorFailure("adapter request audit is malformed")
                    for captured in captured_calls:
                        if not isinstance(captured, Mapping):
                            raise ExecutorFailure("adapter request audit is malformed")
                        path = captured.get("path", captured.get("endpoint"))
                        method = captured.get("method", captured.get("request_method"))
                        if path != self.identity["endpoint"] or str(method).upper() != self.identity["method"].upper():
                            raise ExecutorFailure("dynamic or unauthorized endpoint in adapter request audit")
                        request_audit = dict(captured)
                        request_audit["path"] = path
                        request_audit["method"] = str(method).upper()
                        request_audit["request_sha256"] = sha256_bytes(canonical_json_bytes(request_audit))
                        audit["requests"].append(request_audit)
                response = {"candidate_key": unit["candidate_key"],
                            "status": result.get("status") if isinstance(result, Mapping) else None,
                            "payload_sha256": sha256_bytes(canonical_json_bytes(result))}
                audit["responses"].append(response)
                status = str(response["status"] or "").upper()
                valid = (isinstance(result, Mapping) and status in {"SUCCESS", "SUCCEEDED", "PASS", "OK"}
                         and type(result.get("validated")) is bool and result["validated"] is True
                         and type(result.get("success")) is bool and result["success"] is True)
                if not valid:
                    raise ExecutorFailure("adapter returned non-success execution evidence")
            except BaseException as exc:
                audit["finalized_usage"] = dict(getattr(exc, "runtime_usage", context.finalized_usage))
                return {"status": "FAILED_EXECUTION", "classification": "HARD_GAP",
                        "network_fetch_allowed": False, "reason": str(exc)[:200], "audit": audit}
            audit["finalized_usage"] = dict(context.finalized_usage)
        return {"status": "SUCCESS", "classification": "SUCCESS", "network_fetch_allowed": True,
                "audit": audit}


__all__ = ["ExecutorFailure", "RestrictedExecutor"]
