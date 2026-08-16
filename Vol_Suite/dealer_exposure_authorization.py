"""Fail-closed authorization contract for staged dealer-exposure acquisition.

This module is deliberately independent of acquisition and live-model code.  It
only binds an immutable, calendar-enriched candidate manifest to a bounded
authorization object.  Network execution is a later admission concern.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import isfinite
from types import MappingProxyType
from typing import Any

from .provenance_contract import (
    SHA256_RE,
    canonical_json_bytes,
    sha256_bytes,
    validate_source_hashes,
)

SCHEMA_VERSION = 1
MANIFEST_PROJECTION = "candidate_manifest_calendar_enriched_v2"
PURPOSE = "staged-dealer-exposure-acquisition"
_ALLOWED_SESSION_STATUS = {"OPEN", "HOLIDAY_CLOSED", "UNKNOWN", "EARLY_CLOSE"}
_ALLOWED_SETTLEMENT = {"PM_CLOSE", "AM_SETTLEMENT", "UNKNOWN"}

_AUTH_FIELDS = {
    "schema_version", "authorization_id", "issued_at", "expires_at", "issued_by",
    "purpose", "environment", "candidate_manifest_sha256",
    "candidate_manifest_projection", "calendar", "scope", "probe_policy",
    "cost_ceiling", "executor_policy", "stop_conditions", "authorization_sha256",
}
_CALENDAR_FIELDS = {
    "calendar_hash", "calendar_policy_version", "resolver_code_version",
    "resolver_code_hash", "observed_session_id", "calendar_binding_hash",
}
_SCOPE_FIELDS = {
    "candidate_keys", "calendar_hash", "calendar_policy_version",
    "resolver_code_version", "resolver_code_hash", "observed_bindings",
    "ticker_day_pairs", "dte_strata", "event_habitats", "event_fraction",
    "control_fraction", "max_arm_ratio", "held_pair_policy", "no_imputation",
    "same_day_aggregation", "calendar_binding_hash",
}
_BINDING_FIELDS = {
    "candidate_key", "observed_expiry_date", "session_id", "session_status",
    "settlement_style", "settlement_timestamp", "event_window_id", "window_start",
    "window_end", "calendar_binding_hash",
}
_PROBE_FIELDS = {
    "required_status", "required_validated", "required_invoked", "required_checks",
    "pre_window_observations_exact", "probe_code_hash",
}
_COST_FIELDS = {
    "max_units", "max_probe_calls", "max_heavy_calls", "max_total_endpoint_calls",
    "max_payload_bytes", "max_wall_seconds", "concurrency", "on_exceed",
}
_EXECUTOR_FIELDS = {
    "allowed_executor_id", "allowed_executor_entrypoint", "allowed_endpoint_families",
    "allowed_endpoint_paths", "allowed_request_methods", "scope_binding",
    "network_fetch_allowed", "allow_new_candidate_keys", "allow_held_pairs",
    "allow_live_model_calls", "allow_scheduler_calls", "allow_writes_outside_artifact_root",
}
_MANIFEST_FIELDS = {
    "schema_version", "manifest_schema", "calendar_enriched", "units", "exclusions",
    "selection_provenance", "quota", "held_pair_evidence_hash", "probe_policy",
    "executor_policy", "cost_ceiling", "calendar",
}
_UNIT_FIELDS = {
    "candidate_key", "ticker", "calendar_day", "expiry", "dte", "habitat", "sector",
    "candidate_source", "calendar_hash", "calendar_policy_version", "resolver_code_version",
    "resolver_code_hash", "snapshot_hash", "as_of", "event_ids", "event_types",
    "event_overlap", "event_window_id", "event_windows", "window_id", "window_start",
    "window_end", "window_policy", "nominal_date", "observed_expiry", "observed_expiry_date",
    "session_id", "observed_session_id", "session_status", "regular_open", "regular_close",
    "early_close", "settlement_style", "settlement_timestamp", "timezone", "exact_dte",
    "calendar_binding_hash", "source_hashes", "close_reason",
}


def _source_registry(selection_provenance: Any) -> Mapping[str, Any]:
    if not isinstance(selection_provenance, Mapping):
        raise TypeError("selection_provenance must be an object")
    registry = selection_provenance.get("source_registry")
    if registry is None:
        registry = selection_provenance.get("candidate_source_registry")
    if not isinstance(registry, Mapping) or not registry:
        raise ValueError("selection_provenance.source_registry is required")
    return registry


def _registered_source_hashes(registry: Mapping[str, Any], source: str) -> tuple[str, ...]:
    if not isinstance(source, str) or not source.strip():
        raise ValueError("candidate_source must be a non-empty source identity")
    entry = registry.get(source)
    hashes = entry.get("source_hashes") if isinstance(entry, Mapping) else entry
    try:
        return tuple(sorted(set(validate_source_hashes(hashes))))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"source registry entry {source!r} is invalid") from exc


def _reject_unknown(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"{label} contains unknown fields: {sorted(unknown)!r}")


def _strict_bool(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{label} must be a JSON boolean")
    return value


def _strict_schema_version(value: Any, label: str) -> int:
    if type(value) is not int or value != SCHEMA_VERSION:
        raise ValueError(f"{label} must be supported schema_version {SCHEMA_VERSION}")
    return value


def _hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None or value != value.lower():
        raise ValueError(f"{label} must be a lowercase SHA-256 hash")
    return value


def _aware(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be timezone-qualified ISO-8601")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} is not ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed


def _finite(value: Any, label: str) -> None:
    if isinstance(value, float) and not isfinite(value):
        raise ValueError(f"{label} must be finite")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _finite(item, f"{label}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _finite(item, f"{label}[{index}]")


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, tuple):
        return tuple(_freeze(v) for v in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _validate_hash_fields(value: Mapping[str, Any], fields: Sequence[str], label: str) -> None:
    for field in fields:
        if field not in value or value[field] in (None, ""):
            raise ValueError(f"{label} is missing {field}")
        _hash(value[field], f"{label}.{field}")


def _manifest_projection(manifest: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(manifest, Mapping):
        raise TypeError("candidate manifest must be an object")
    _reject_unknown(manifest, _MANIFEST_FIELDS, "candidate manifest")
    required = tuple(_MANIFEST_FIELDS)
    if any(field not in manifest for field in required):
        raise ValueError("candidate manifest is missing required calendar-enriched fields")
    _strict_schema_version(manifest["schema_version"], "candidate manifest.schema_version")
    if manifest["manifest_schema"] != MANIFEST_PROJECTION:
        raise ValueError("only the calendar-enriched manifest projection is admissible")
    _strict_bool(manifest["calendar_enriched"], "calendar_enriched")
    if manifest["calendar_enriched"] is not True:
        raise ValueError("pre-calendar manifests cannot be authorized")
    units = manifest["units"]
    if not isinstance(units, (list, tuple)) or not units:
        raise ValueError("candidate manifest units must be a non-empty list")
    calendar = manifest["calendar"]
    if not isinstance(calendar, Mapping):
        raise TypeError("candidate manifest calendar must be an object")
    _reject_unknown(calendar, _CALENDAR_FIELDS, "candidate manifest calendar")
    if set(calendar) != _CALENDAR_FIELDS:
        raise ValueError("candidate manifest calendar identity is incomplete")
    _validate_hash_fields(calendar, ("calendar_hash", "resolver_code_hash", "calendar_binding_hash"), "calendar")
    for field in ("calendar_policy_version", "resolver_code_version", "observed_session_id"):
        if not isinstance(calendar[field], str) or not calendar[field]:
            raise ValueError(f"calendar.{field} must be non-empty")

    normalized_units: list[dict[str, Any]] = []
    keys: list[str] = []
    for index, raw in enumerate(units):
        if not isinstance(raw, Mapping):
            raise TypeError(f"unit {index} must be an object")
        _reject_unknown(raw, _UNIT_FIELDS, f"unit {index}")
        required_unit = (
            "candidate_key", "ticker", "calendar_day", "expiry", "dte", "habitat", "sector",
            "candidate_source", "calendar_hash", "calendar_policy_version", "resolver_code_version",
            "resolver_code_hash", "observed_expiry_date", "session_id", "session_status",
            "regular_open", "regular_close", "early_close", "settlement_style",
            "settlement_timestamp", "event_window_id", "window_start", "window_end",
            "calendar_binding_hash",
        )
        if any(field not in raw or raw[field] in (None, "") for field in required_unit):
            raise ValueError(f"unit {index} is missing calendar identity")
        key = raw["candidate_key"]
        if not isinstance(key, str) or not key:
            raise TypeError("candidate_key must be a non-empty string")
        keys.append(key)
        _strict_bool(raw["early_close"], f"unit {index}.early_close")
        _finite(raw, f"unit {index}")
        source_hashes = tuple(sorted(set(validate_source_hashes(raw.get("source_hashes")))))
        registered_hashes = _registered_source_hashes(
            _source_registry(manifest["selection_provenance"]), raw["candidate_source"]
        )
        if source_hashes != registered_hashes:
            raise ValueError(f"unit {index} source registry identity mismatch")
        normalized = {str(k): raw[k] for k in sorted(raw)}
        normalized["source_hashes"] = list(source_hashes)
        normalized_units.append(normalized)
        for field in ("calendar_hash", "resolver_code_hash", "calendar_binding_hash"):
            _hash(raw[field], f"unit {index}.{field}")
        if (raw["calendar_hash"], raw["calendar_policy_version"], raw["resolver_code_version"], raw["resolver_code_hash"]) != (
            calendar["calendar_hash"], calendar["calendar_policy_version"], calendar["resolver_code_version"], calendar["resolver_code_hash"]
        ):
            raise ValueError("unit/calendar identity mismatch")
        if raw["calendar_binding_hash"] != calendar["calendar_binding_hash"]:
            raise ValueError("unit/calendar binding identity mismatch")
        if raw["session_id"] != calendar["observed_session_id"]:
            raise ValueError("unit/session identity mismatch")
        if raw["session_status"] not in _ALLOWED_SESSION_STATUS or raw["settlement_style"] not in _ALLOWED_SETTLEMENT:
            raise ValueError("invalid session or settlement identity")
    if len(set(keys)) != len(keys):
        raise ValueError("candidate keys must be unique")
    if keys != sorted(keys):
        raise ValueError("candidate keys must be exactly sorted")

    _hash(manifest["held_pair_evidence_hash"], "held_pair_evidence_hash")
    result: dict[str, Any] = {field: manifest[field] for field in _MANIFEST_FIELDS}
    result["units"] = normalized_units
    result["calendar"] = {key: calendar[key] for key in sorted(calendar)}
    _finite(result, "candidate manifest")
    # Ensure the result itself is JSON serializable with the shared strict routine.
    try:
        canonical_json_bytes(result)
    except (TypeError, ValueError) as exc:
        raise ValueError("candidate manifest is not strict canonical JSON") from exc
    return result


def candidate_manifest_projection(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return the immutable calendar-enriched identity projection."""
    return _manifest_projection(manifest)


def candidate_manifest_sha256(manifest: Mapping[str, Any]) -> str:
    """Hash the strict canonical calendar-enriched candidate projection."""
    return sha256_bytes(canonical_json_bytes(candidate_manifest_projection(manifest)))


@dataclass(frozen=True)
class AcquisitionAuthorization:
    """Typed immutable authorization for one exact candidate manifest."""

    _data: Mapping[str, Any]
    _manifest: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any],
        candidate_manifest: Mapping[str, Any] | None = None,
        *,
        now: datetime | str | None = None,
    ) -> "AcquisitionAuthorization":
        if not isinstance(payload, Mapping):
            raise TypeError("authorization must be an object")
        _reject_unknown(payload, _AUTH_FIELDS, "authorization")
        required = _AUTH_FIELDS
        missing = sorted(required - set(payload))
        if missing:
            raise ValueError(f"authorization is missing required fields: {missing}")
        _strict_schema_version(payload["schema_version"], "authorization.schema_version")
        for field in ("authorization_id", "issued_by", "purpose", "environment", "candidate_manifest_projection"):
            if not isinstance(payload[field], str) or not payload[field]:
                raise ValueError(f"{field} must be a non-empty string")
        if payload["purpose"] != PURPOSE or payload["candidate_manifest_projection"] != MANIFEST_PROJECTION:
            raise ValueError("authorization purpose/projection mismatch")
        issued = _aware(payload["issued_at"], "issued_at")
        expires = _aware(payload["expires_at"], "expires_at")
        if expires <= issued:
            raise ValueError("expires_at must be later than issued_at")
        if now is None:
            current = datetime.now(timezone.utc)
        else:
            current = _aware(now, "now") if isinstance(now, str) else now
            if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
                raise TypeError("now must be timezone-aware")
        if issued > current:
            raise ValueError("issued_at cannot be in the future")
        if current >= expires:
            raise ValueError("authorization is expired")

        manifest_digest = _hash(payload["candidate_manifest_sha256"], "candidate_manifest_sha256")
        if candidate_manifest is None:
            raise ValueError("candidate_manifest is required for fail-closed authorization")
        expected_digest = candidate_manifest_sha256(candidate_manifest)
        if manifest_digest != expected_digest:
            raise ValueError("candidate_manifest_sha256 does not match manifest projection")
        projection = candidate_manifest_projection(candidate_manifest)
        calendar = payload["calendar"]
        scope = payload["scope"]
        if not isinstance(calendar, Mapping) or not isinstance(scope, Mapping):
            raise TypeError("calendar and scope must be objects")
        _reject_unknown(calendar, _CALENDAR_FIELDS, "authorization calendar")
        _reject_unknown(scope, _SCOPE_FIELDS, "authorization scope")
        if set(calendar) != _CALENDAR_FIELDS or set(scope) != _SCOPE_FIELDS:
            raise ValueError("authorization calendar/scope identity is incomplete")
        _validate_hash_fields(calendar, ("calendar_hash", "resolver_code_hash", "calendar_binding_hash"), "authorization calendar")
        _validate_hash_fields(scope, ("calendar_hash", "resolver_code_hash", "calendar_binding_hash"), "authorization scope")
        for field in ("calendar_policy_version", "resolver_code_version", "observed_session_id"):
            if not isinstance(calendar[field], str) or not calendar[field]:
                raise ValueError(f"authorization calendar.{field} must be non-empty")
        if dict(calendar) != dict(projection["calendar"]):
            raise ValueError("authorization calendar does not match candidate manifest")
        if scope["calendar_hash"] != calendar["calendar_hash"] or scope["calendar_policy_version"] != calendar["calendar_policy_version"] or scope["resolver_code_version"] != calendar["resolver_code_version"] or scope["resolver_code_hash"] != calendar["resolver_code_hash"] or scope["calendar_binding_hash"] != calendar["calendar_binding_hash"]:
            raise ValueError("authorization scope calendar identity mismatch")
        keys = [unit["candidate_key"] for unit in projection["units"]]
        scope_keys = scope["candidate_keys"]
        if not isinstance(scope_keys, list) or scope_keys != sorted(scope_keys) or len(set(scope_keys)) != len(scope_keys):
            raise ValueError("scope candidate_keys must be an exact sorted unique list")
        if scope_keys != keys:
            raise ValueError("authorization scope keys do not exactly match manifest")
        bindings = scope["observed_bindings"]
        if not isinstance(bindings, list) or [b.get("candidate_key") for b in bindings if isinstance(b, Mapping)] != keys:
            raise ValueError("scope observed_bindings must exactly follow manifest keys")
        for index, binding in enumerate(bindings):
            if not isinstance(binding, Mapping):
                raise TypeError("observed binding must be an object")
            _reject_unknown(binding, _BINDING_FIELDS, f"observed binding {index}")
            if set(binding) != _BINDING_FIELDS:
                raise ValueError("observed binding identity is incomplete")
            _validate_hash_fields(binding, ("calendar_binding_hash",), f"observed binding {index}")
            if binding["calendar_binding_hash"] != calendar["calendar_binding_hash"]:
                raise ValueError("observed binding/calendar binding identity mismatch")
            unit = projection["units"][index]
            for field in _BINDING_FIELDS - {"candidate_key"}:
                expected = unit.get(field)
                if field == "session_id":
                    expected = unit["session_id"]
                if binding[field] != expected:
                    raise ValueError(f"observed binding does not match manifest: {field}")

        if not isinstance(scope["no_imputation"], bool) or scope["no_imputation"] is not True:
            raise ValueError("no_imputation must be true")
        if scope["max_arm_ratio"] > 2.0:
            raise ValueError("max_arm_ratio cannot exceed 2:1")
        if not isinstance(scope["max_arm_ratio"], (int, float)) or isinstance(scope["max_arm_ratio"], bool):
            raise TypeError("max_arm_ratio must be numeric")
        _strict_bool(scope["no_imputation"], "scope.no_imputation")
        probe = payload["probe_policy"]
        cost = payload["cost_ceiling"]
        executor = payload["executor_policy"]
        for value, fields, label in ((probe, _PROBE_FIELDS, "probe_policy"), (cost, _COST_FIELDS, "cost_ceiling"), (executor, _EXECUTOR_FIELDS, "executor_policy")):
            if not isinstance(value, Mapping):
                raise TypeError(f"{label} must be an object")
            _reject_unknown(value, fields, label)
            if set(value) != fields:
                raise ValueError(f"{label} is incomplete")
        for field in ("required_validated", "required_invoked"):
            _strict_bool(probe[field], f"probe_policy.{field}")
        _hash(probe["probe_code_hash"], "probe_policy.probe_code_hash")
        if probe["required_status"] != "PASS" or probe["required_validated"] is not True or probe["required_invoked"] is not True or probe["pre_window_observations_exact"] != 2:
            raise ValueError("probe policy is not the exact PASS/two-observation policy")
        for field in ("max_units", "max_probe_calls", "max_heavy_calls", "max_total_endpoint_calls", "max_payload_bytes", "max_wall_seconds", "concurrency"):
            if isinstance(cost[field], bool) or not isinstance(cost[field], int) or cost[field] < 0:
                raise TypeError(f"cost_ceiling.{field} must be a non-negative integer")
        if cost["concurrency"] != 1 or cost["on_exceed"] != "stop_and_hard_gap":
            raise ValueError("cost ceiling must be serial and stop on exceed")
        for field in ("network_fetch_allowed", "allow_new_candidate_keys", "allow_held_pairs", "allow_live_model_calls", "allow_scheduler_calls", "allow_writes_outside_artifact_root"):
            _strict_bool(executor[field], f"executor_policy.{field}")
        for field in ("allowed_executor_id", "allowed_executor_entrypoint", "scope_binding"):
            if not isinstance(executor[field], str) or not executor[field].strip():
                raise ValueError(f"executor_policy.{field} must be a non-empty string")
        for field in ("allowed_endpoint_families", "allowed_endpoint_paths", "allowed_request_methods"):
            values = executor[field]
            if not isinstance(values, list) or not values or any(not isinstance(item, str) or not item.strip() for item in values):
                raise ValueError(f"executor_policy.{field} must be a non-empty string list")
        if len(set(executor["allowed_endpoint_paths"])) != len(executor["allowed_endpoint_paths"]):
            raise ValueError("executor_policy.allowed_endpoint_paths must be unique")
        if any(not item.startswith("/") or "?" in item or "#" in item for item in executor["allowed_endpoint_paths"]):
            raise ValueError("executor_policy.allowed_endpoint_paths must be exact absolute paths")
        if any(item.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"} for item in executor["allowed_request_methods"]):
            raise ValueError("executor_policy.allowed_request_methods contains an unsupported method")
        if any(executor[field] is not False for field in ("allow_new_candidate_keys", "allow_held_pairs", "allow_live_model_calls", "allow_scheduler_calls", "allow_writes_outside_artifact_root")):
            raise ValueError("executor policy attempts an unsafe capability")
        if not isinstance(payload["stop_conditions"], list) or not payload["stop_conditions"]:
            raise ValueError("stop_conditions must be a non-empty list")
        supplied = payload["authorization_sha256"]
        _hash(supplied, "authorization_sha256")
        normalized = {key: _thaw(_freeze(payload[key])) for key in payload if key != "authorization_sha256"}
        digest = sha256_bytes(canonical_json_bytes(normalized))
        if supplied != digest:
            raise ValueError("authorization_sha256 does not match authorization contents")
        normalized["authorization_sha256"] = supplied
        return cls(_freeze(normalized), _freeze(projection))

    def to_mapping(self) -> dict[str, Any]:
        return _thaw(self._data)

    @property
    def scope(self) -> Mapping[str, Any]:
        return self._data["scope"]

    def candidate_manifest_projection(self) -> dict[str, Any]:
        return _thaw(self._manifest)

    def authorization_sha256(self) -> str:
        payload = self.to_mapping()
        payload.pop("authorization_sha256", None)
        return sha256_bytes(canonical_json_bytes(payload))


__all__ = ["AcquisitionAuthorization", "MANIFEST_PROJECTION", "candidate_manifest_projection", "candidate_manifest_sha256"]
