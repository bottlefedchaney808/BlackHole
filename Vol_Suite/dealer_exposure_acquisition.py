"""Approval-gated, deterministic acquisition layer for dealer-exposure expansion.

This module is network-capable by dependency injection, but importing it and using
``dry_run``/``probe_only`` never performs endpoint acquisition.  All acquisition
is sequential ticker x calendar-day and the historical concurrency contract is
fail-closed at one.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .dealer_exposure_universe import (
    DTE_STRATA,
    EVENT_HABITATS,
    _validate_calendar_binding,
    held_pairs_from_paths,
)
from .opex_calendar import CalendarGapError, calendar_for_probe
from .provenance_contract import (
    canonical_json_bytes,
    canonical_sha256,
    validate_source_hashes,
)
from .dealer_exposure_authorization import AcquisitionAuthorization, candidate_manifest_projection

NETWORK_ACQUISITION_EXECUTED = False
_STATUS = {"PASS", "INELIGIBLE", "HARD_GAP", "ASSOCIATIONAL"}
_PREWINDOW = "PRE_WINDOW"


class AcquisitionGateError(RuntimeError):
    """Raised when an approval, concurrency, or provenance gate fails."""


def _strict_prewindow(item: Mapping[str, Any], manifest_unit: Mapping[str, Any], registry_entry: Mapping[str, Any]) -> list[str]:
    """Validate the complete, registered two-point PRE_WINDOW contract."""
    errors: list[str] = []
    observations = item.get("pre_window_observations")
    if not isinstance(observations, list) or len(observations) != 2:
        return ["exactly two PRE_WINDOW observations are required"]
    cutoff = item.get("breach_window_start_prov")
    timezone = item.get("declared_timezone")
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(timezone) if isinstance(timezone, str) and timezone else None
        if zone is None:
            raise ValueError("declared_timezone is required")
        cutoff_dt = _timestamp(cutoff)
        day = str(manifest_unit.get("calendar_day"))
        parsed: list[tuple[dt.datetime, float, Mapping[str, Any]]] = []
        for index, observation in enumerate(observations):
            if not isinstance(observation, Mapping) or observation.get("role") != _PREWINDOW:
                errors.append(f"observation {index} must declare role PRE_WINDOW")
                continue
            try:
                stamp = _timestamp(observation.get("timestamp"))
                value = observation.get("iv")
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    raise ValueError("IV value must be finite")
                identity = observation.get("source_identity")
                source_hash = observation.get("source_hash")
                if not isinstance(identity, str) or not identity.strip():
                    raise ValueError("source identity is required")
                if not isinstance(source_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", source_hash):
                    raise ValueError("source hash must be SHA-256")
                if stamp >= cutoff_dt:
                    raise ValueError("observation must strictly precede breach_window_start_prov")
                if stamp.astimezone(zone).date().isoformat() != day:
                    raise ValueError("observation must be on the local calendar day")
                parsed.append((stamp, float(value), observation))
            except (TypeError, ValueError, OverflowError) as exc:
                errors.append(f"observation {index}: {exc}")
        if errors:
            return errors
        if parsed[0][0] >= parsed[1][0] or parsed[0][0] == parsed[1][0]:
            return ["PRE_WINDOW observations must be ordered and distinct"]
        try:
            registered = set(validate_source_hashes(registry_entry.get("source_hashes")))
        except (TypeError, ValueError) as exc:
            errors.append(f"registry source hashes are invalid: {exc}")
            registered = set()
        if any(obs[2]["source_hash"] not in registered for obs in parsed):
            errors.append("PRE_WINDOW source hash is absent from the manifest/registry")
        if item.get("imputed") is not False or item.get("no_imputation") is not True:
            errors.append("PRE_WINDOW evidence must explicitly reject imputation")
        if item.get("delta_iv_aggregation") != "iv_source_minus_iv_before" or str(item.get("delta_iv_aggregation_version")) != "1":
            errors.append("PRE_WINDOW aggregation identity is unsupported")
        delta = item.get("delta_iv_pre_window")
        if isinstance(delta, bool) or not isinstance(delta, (int, float)) or not math.isfinite(float(delta)):
            errors.append("delta_iv_pre_window must be finite")
        elif not math.isclose(float(delta), parsed[1][1] - parsed[0][1], rel_tol=1e-12, abs_tol=1e-12):
            errors.append("delta_iv_pre_window does not equal source-minus-before")
    except (TypeError, ValueError, OverflowError, OSError) as exc:
        errors.append(str(exc))
    return errors


def _cost_usage(evidence: Mapping[str, Any]) -> Mapping[str, Any] | None:
    usage = evidence.get("usage", evidence.get("cost_usage"))
    return usage if isinstance(usage, Mapping) else None


class _AdmissionContext:
    """Opaque token minted only after static authorization preflight."""

    __slots__ = ("authorization", "manifest", "limits", "usage", "lock", "blocked")

    def __init__(self, authorization: AcquisitionAuthorization, manifest: Mapping[str, Any]) -> None:
        self.authorization = authorization
        self.manifest = candidate_manifest_projection(manifest)
        cost = authorization.to_mapping()["cost_ceiling"]
        self.limits = {"units": cost["max_units"], "probe_calls": cost["max_probe_calls"], "heavy_calls": cost["max_heavy_calls"], "total_endpoint_calls": cost["max_total_endpoint_calls"], "payload_bytes": cost["max_payload_bytes"], "wall_seconds": cost["max_wall_seconds"], "concurrency": cost["concurrency"]}
        self.usage = {"units": 0, "probe_calls": 0, "heavy_calls": 0, "total_endpoint_calls": 0, "payload_bytes": 0, "wall_seconds": 0.0, "concurrency": 0}
        self.lock = threading.Lock()
        self.blocked: str | None = None

    def call(self, kind: str, fn: Callable[[], Any]) -> tuple[Any, dict[str, Any]]:
        """Permit exactly one call and commit all call counters before dispatch.

        The reservation is made while holding the same lock used by every
        caller.  A failing adapter therefore still consumes its authorization
        unit and endpoint-call budget; retries cannot obtain a fresh projection.
        """
        if kind not in {"probe", "heavy"}:
            raise ValueError("authorization runtime call kind must be probe or heavy")
        with self.lock:
            if self.blocked:
                raise AcquisitionGateError(self.blocked)
            if self.usage["concurrency"] >= self.limits["concurrency"]:
                self.blocked = "authorization cost ceiling exceeded: concurrency"
                raise AcquisitionGateError(self.blocked)
            projected = dict(self.usage)
            projected[f"{kind}_calls"] += 1
            projected["total_endpoint_calls"] += 1
            projected["units"] += 1
            for field, limit in self.limits.items():
                if field in projected and projected[field] > limit:
                    self.blocked = f"authorization cost ceiling exceeded: {field}"
                    raise AcquisitionGateError(self.blocked)
            # Atomic commit: no permitted call can be observed without its
            # probe/heavy/total/unit reservation already present in usage.
            self.usage.update(projected)
        started = time.perf_counter()
        value: Any = None
        try:
            value = fn()
            return value, dict(self.usage)
        finally:
            elapsed = time.perf_counter() - started
            size = len(canonical_json_bytes(value)) if value is not None else 0
            with self.lock:
                self.usage["wall_seconds"] += elapsed
                self.usage["payload_bytes"] += size
                self.usage["concurrency"] = 0
                if self.usage["wall_seconds"] > self.limits["wall_seconds"]:
                    self.blocked = "authorization cost ceiling exceeded: wall_seconds"
                elif self.usage["payload_bytes"] > self.limits["payload_bytes"]:
                    self.blocked = "authorization cost ceiling exceeded: payload_bytes"
                if self.blocked:
                    # Raise after the adapter returns, so over-budget output is
                    # never treated as an authorized result.
                    raise AcquisitionGateError(self.blocked)


def _preflight_authorization(authorization: Any, manifest: Mapping[str, Any]) -> _AdmissionContext | None:
    """Validate the calendar-enriched manifest and authorization before any call."""
    if not isinstance(authorization, AcquisitionAuthorization):
        return None
    try:
        projection = candidate_manifest_projection(manifest)
        if authorization.candidate_manifest_projection() != projection:
            return None
        if authorization.authorization_sha256() != authorization.to_mapping()["authorization_sha256"]:
            return None
        return _AdmissionContext(authorization, projection)
    except (TypeError, ValueError, KeyError):
        return None


def _authorized_executor(executor: Any, authorization: AcquisitionAuthorization) -> tuple[bool, dict[str, Any], str]:
    """Validate one named adapter against the exact authorization policy."""
    if executor is None or not callable(executor) or not isinstance(authorization, AcquisitionAuthorization):
        return False, {}, "typed named adapter and authorization are required"
    policy = authorization.to_mapping()["executor_policy"]
    identity = {
        "executor_id": getattr(executor, "executor_id", None),
        "entrypoint": getattr(executor, "entrypoint", None),
        "endpoint": getattr(executor, "endpoint", None),
        "request_method": getattr(executor, "request_method", None),
        "scope_binding": getattr(executor, "scope_binding", None),
    }
    if any(not isinstance(value, str) or not value.strip() for value in identity.values()):
        return False, identity, "executor identity/scope binding is incomplete or untrusted"
    if identity["executor_id"] != policy["allowed_executor_id"] or identity["entrypoint"] != policy["allowed_executor_entrypoint"]:
        return False, identity, "executor identity is not authorized"
    # Families are descriptive metadata only; require exact enumerated paths.
    if identity["endpoint"] not in tuple(policy["allowed_endpoint_paths"]):
        return False, identity, "executor endpoint path is outside authorization scope"
    if identity["request_method"].upper() not in tuple(method.upper() for method in policy["allowed_request_methods"]):
        return False, identity, "executor request method is outside authorization scope"
    if identity["scope_binding"] != policy["scope_binding"]:
        return False, identity, "executor scope binding is outside authorization scope"
    if policy["network_fetch_allowed"] is not True:
        return False, identity, "authorization does not permit network fetch"
    return True, identity, ""


def _immutable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): _immutable(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_immutable(v) for v in value)
    return value


def _admission_failure(reasons: list[str], stages: list[str]) -> tuple[tuple[Any, ...], Mapping[str, Any]]:
    return (), _immutable({"admitted": False, "stages": stages, "admitted_keys": [], "blocked": reasons})


def admit_acquisition(
    authorization: AcquisitionAuthorization | Mapping[str, Any] | None,
    manifest: Mapping[str, Any],
    probes: Iterable[Mapping[str, Any]],
    evidence: Mapping[str, Any],
    registry: Mapping[str, Any],
) -> tuple[tuple[Mapping[str, Any], ...], Mapping[str, Any]]:
    """Atomically validate acquisition evidence before restricted handoff."""
    stages: list[str] = []
    reasons: list[str] = []
    try:
        projection = candidate_manifest_projection(manifest)
        stages.append("calendar-enriched manifest")
        if authorization is None or isinstance(authorization, bool):
            return _admission_failure(["authorization is mandatory; boolean approval is not authorization"], stages)
        auth = authorization if isinstance(authorization, AcquisitionAuthorization) else AcquisitionAuthorization.from_mapping(authorization, candidate_manifest=projection)
        if auth.candidate_manifest_projection() != projection:
            return _admission_failure(["authorization manifest projection is detached or mutated"], stages)
        auth_data = auth.to_mapping()
        keys = tuple(unit["candidate_key"] for unit in projection["units"])
        scope = auth_data["scope"]
        if list(scope["candidate_keys"]) != list(keys):
            reasons.append("authorization candidate scope is not exact")
        quota = manifest.get("quota")
        cost = auth_data["cost_ceiling"]
        if not isinstance(quota, Mapping) or type(quota.get("max_units")) is not int:
            reasons.append("manifest quota.max_units is required")
        elif len(keys) > quota["max_units"] or len(keys) > cost["max_units"]:
            reasons.append("candidate quota/cost ceiling exceeded")
        if cost.get("concurrency") != 1:
            reasons.append("concurrency must be exactly one")
        usage = _cost_usage(evidence) if isinstance(evidence, Mapping) else None
        required_usage = ("units", "probe_calls", "heavy_calls", "total_endpoint_calls", "payload_bytes", "wall_seconds", "concurrency")
        if usage is None or any(field not in usage for field in required_usage):
            reasons.append("complete authorization cost usage counters are required; missing is not zero")
        else:
            for field in required_usage:
                value = usage[field]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    reasons.append(f"cost usage {field} is missing or invalid")
            limits = {"units": cost["max_units"], "probe_calls": cost["max_probe_calls"], "heavy_calls": cost["max_heavy_calls"], "total_endpoint_calls": cost["max_total_endpoint_calls"], "payload_bytes": cost["max_payload_bytes"], "wall_seconds": cost["max_wall_seconds"], "concurrency": cost["concurrency"]}
            for field, limit in limits.items():
                if field in usage and usage[field] > limit:
                    reasons.append(f"authorization cost ceiling exceeded: {field}")
        if any(u.get("held_pair_exclusion") or u.get("network") is True or u.get("imputed") is True for u in projection["units"]):
            reasons.append("held/new/live/scheduler/out-of-root or no-imputation permission violation")
        stages.append("exact candidate scope/hash/quota/cost/concurrency")

        raw_probes = list(probes) if isinstance(probes, Iterable) and not isinstance(probes, (str, bytes, Mapping)) else []
        probe_keys = [p.get("candidate_key") for p in raw_probes if isinstance(p, Mapping)]
        if len(raw_probes) != len(probe_keys) or len(set(probe_keys)) != len(probe_keys):
            reasons.append("probe identities are malformed or duplicated")
        if set(probe_keys) != set(keys):
            reasons.append("probe coverage is not exact; unknown or missing probe")
        expected_code_hash = auth_data["probe_policy"]["probe_code_hash"]
        required_checks = auth_data["probe_policy"]["required_checks"]
        for probe in raw_probes:
            if not isinstance(probe, Mapping):
                continue
            key = probe.get("candidate_key")
            unit = next((u for u in projection["units"] if u["candidate_key"] == key), None)
            if unit is None:
                continue
            expected_request = {name: unit[name] for name in ("ticker", "calendar_day", "expiry", "dte", "habitat", "sector", "candidate_source")}
            expected_request["calendar_binding"] = unit.get("calendar_binding")
            if probe.get("request_parameters") != expected_request:
                reasons.append(f"probe {key} request identity is detached")
            evidence_identity = probe.get("evidence") if isinstance(probe.get("evidence"), Mapping) else {}
            identity_fields = ("snapshot_hash", "calendar_hash", "calendar_policy_version", "resolver_code_hash", "session_id", "settlement_style", "window_policy", "calendar_binding_hash", "as_of", "source_hashes")
            identity = probe.get("probe_identity") if isinstance(probe.get("probe_identity"), Mapping) else evidence_identity.get("probe_identity")
            if not isinstance(identity, Mapping):
                reasons.append(f"probe {key} exact identity is missing")
            else:
                for field in identity_fields:
                    expected = unit.get(field)
                    actual = identity.get(field)
                    if expected in (None, "") or actual != expected:
                        reasons.append(f"probe {key} identity mismatch: {field}")
                if identity.get("request_identity", identity.get("request_parameters")) != expected_request:
                    reasons.append(f"probe {key} candidate request identity is detached")
            if probe.get("probe_code_hash") != expected_code_hash:
                reasons.append(f"probe {key} code hash mismatch")
            for field in ("ticker", "expiry", "dte"):
                if probe.get(field) != unit[field]:
                    reasons.append(f"probe {key} {field} identity mismatch")
            if probe.get("day") != unit["calendar_day"] or any(probe.get(f) != unit.get(f) for f in ("habitat", "sector", "candidate_source")):
                reasons.append(f"probe {key} ticker/day/expiry/DTE/habitat/sector/source identity mismatch")
            binding = probe.get("calendar_binding") or (probe.get("evidence") or {}).get("calendar_binding")
            if binding != unit.get("calendar_binding"):
                reasons.append(f"probe {key} calendar identity detached")
            if probe.get("probe_code_hash") != expected_code_hash:
                reasons.append(f"probe {key} code hash mismatch")
            if probe.get("status") != "PASS" or probe.get("validated") is not True or probe.get("invoked") is not True:
                reasons.append(f"probe {key} is not a validated invoked PASS")
            checks = probe.get("checks")
            if not isinstance(checks, Mapping) or any(checks.get(check) != "PASS" for check in required_checks):
                reasons.append(f"probe {key} checks are incomplete")
        stages.append("complete Task 1 probe contract")

        raw_units = evidence.get("units") if isinstance(evidence, Mapping) else None
        if not isinstance(raw_units, list) or len(raw_units) != len(keys):
            reasons.append("evidence unit coverage is incomplete")
            raw_units = []
        evidence_keys = [u.get("candidate_key") for u in raw_units if isinstance(u, Mapping)]
        if len(evidence_keys) != len(raw_units) or len(set(evidence_keys)) != len(evidence_keys) or set(evidence_keys) != set(keys):
            reasons.append("evidence contains duplicate, unknown, or missing candidates")
        if not isinstance(registry, Mapping) or not registry or (evidence.get("artifact_registry") is not None and evidence.get("artifact_registry") != registry):
            reasons.append("artifact registry is missing or detached")
        admitted: list[dict[str, Any]] = []
        for item in raw_units:
            if not isinstance(item, Mapping):
                continue
            key = item.get("candidate_key")
            manifest_unit = next((u for u in projection["units"] if u["candidate_key"] == key), None)
            artifact_hash = item.get("artifact_hash")
            entry = registry.get(artifact_hash) if isinstance(artifact_hash, str) else None
            if manifest_unit is None or not isinstance(entry, Mapping):
                reasons.append(f"artifact/source closure missing for {key}")
                continue
            if entry.get("candidate_key") != key or entry.get("artifact_hash") != artifact_hash or entry.get("artifact_manifest") != item.get("artifact_manifest"):
                reasons.append(f"artifact registry closure mismatch for {key}")
            closure_fields = ("source_hashes", "calendar_binding", "request_identity", "raw_payload_hash", "artifact_hash", "artifact_manifest", "evidence")
            for field in closure_fields:
                expected = item.get(field)
                if field == "request_identity":
                    expected = item.get("artifact_manifest", {}).get("request_identity")
                if field not in entry or entry.get(field) != expected:
                    reasons.append(f"registry closure is shallow or detached for {key}: {field}")
            if item.get("status") != "PASS" or item.get("imputed") is not False or item.get("no_imputation") is not True:
                reasons.append(f"evidence {key} is not an unimputed PASS")
            if item.get("calendar_binding") != manifest_unit.get("calendar_binding") or item.get("source_hashes") != manifest_unit.get("source_hashes"):
                reasons.append(f"calendar/source hashes detached for {key}")
            for pre_reason in _strict_prewindow(item, manifest_unit, entry):
                reasons.append(f"{key}: {pre_reason}")
            try:
                if canonical_sha256(item.get("artifact_manifest")) != artifact_hash:
                    reasons.append(f"artifact hash mutated for {key}")
            except (TypeError, ValueError):
                reasons.append(f"artifact manifest is not canonical for {key}")
            if entry.get("payload_bytes") is None and not entry.get("raw_payload_hash"):
                reasons.append(f"raw payload identity is missing for {key}")
            admitted.append(dict(item))
        stages.append("registry/source/artifact closure")
        if auth.authorization_sha256() != auth_data["authorization_sha256"]:
            reasons.append("authorization self-hash is invalid")
        policy = auth_data["executor_policy"]
        if any(policy[field] for field in ("allow_new_candidate_keys", "allow_held_pairs", "allow_live_model_calls", "allow_scheduler_calls", "allow_writes_outside_artifact_root")):
            reasons.append("executor policy contains restricted permission")
        stages.append("authorization self-hash/scope/expiry")
        if reasons:
            return _admission_failure(sorted(set(reasons)), stages)
        admitted.sort(key=lambda item: item["candidate_key"])
        frozen = tuple(_immutable(item) for item in admitted)
        return frozen, _immutable({"admitted": True, "stages": stages + ["restricted executor handoff"], "admitted_keys": [item["candidate_key"] for item in admitted], "blocked": []})
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        return _admission_failure([str(exc)], stages)


def _date(value: Any) -> str:
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return dt.date.fromisoformat(text).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid calendar day: {value!r}") from exc


def _hash(value: Any) -> str:
    """Use the shared strict canonical representation for every artifact hash."""
    return canonical_sha256(value)


def _held_references(paths: Iterable[str | Path]) -> dict[tuple[str, str], str]:
    """Return deterministic source references alongside contract held pairs."""
    refs: dict[tuple[str, str], str] = {}
    for root in sorted((Path(p) for p in paths), key=lambda p: str(p)):
        files = [root] if root.is_file() else sorted(root.rglob("*.json"))
        for path in files:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeDecodeError):
                continue
            objects: list[Mapping[str, Any]] = []

            def visit(value: Any, collected: list[Mapping[str, Any]]) -> None:
                if isinstance(value, Mapping):
                    collected.append(value)
                    for child in value.values():
                        visit(child, collected)
                elif isinstance(value, list):
                    for child in value:
                        visit(child, collected)

            visit(payload, objects)
            for obj in objects:
                ticker = str(obj.get("ticker", "")).strip().lstrip("$").upper()
                day = next((obj.get(k) for k in ("calendar_day", "day", "date", "trade_date", "as_of", "acquired_on") if obj.get(k) is not None), None)
                if ticker and day is not None:
                    try: refs[(ticker, _date(day))] = f"{path.name}:{_date(day)}"
                    except ValueError: pass
    return refs


def build_candidate_schedule(candidates: Iterable[Mapping[str, Any]], *, held_paths: Iterable[str | Path] = (), held_pairs: Iterable[tuple[str, str]] = (), calendar_snapshot: Any | None = None, as_of: str | None = None, window_policy: str = "OPEX_DAY") -> list[dict[str, Any]]:
    """Normalize and deterministically sort candidate ticker x day requests.

    Held rows remain in the census schedule and are marked, never silently
    replaced.  The schedule itself does no probing or network work.
    """
    paths = tuple(held_paths)
    held = {(str(t).strip().lstrip("$").upper(), _date(d)) for t, d in held_pairs}
    held |= held_pairs_from_paths(paths)
    refs = _held_references(paths)
    result_by_key: dict[str, dict[str, Any]] = {}
    for raw in candidates:
        ticker = str(raw.get("ticker", "")).strip().lstrip("$").upper()
        if ticker in {"SPY", "QQQ"}: raise ValueError("reference families cannot be expansion candidates")
        if not ticker: raise ValueError("ticker is required")
        day = _date(raw.get("calendar_day", raw.get("day", raw.get("date"))))
        expiry = _date(raw.get("expiry"))
        try: dte = int(raw.get("dte"))
        except (TypeError, ValueError) as exc: raise ValueError("DTE is required") from exc
        if dte <= 0 or (dt.date.fromisoformat(expiry) - dt.date.fromisoformat(day)).days != dte:
            raise ValueError("expiry and DTE must agree and DTE must be positive")
        if not any(lo <= dte <= hi for lo, hi in DTE_STRATA): raise ValueError("DTE outside locked strata")
        habitat = str(raw.get("habitat", raw.get("event_habitat", "NONE"))).strip().upper()
        if habitat not in {"NONE", *EVENT_HABITATS, "DESCRIPTIVE-HABITAT"}: raise ValueError("invalid habitat")
        sector = str(raw.get("sector", "")).strip()
        if not sector: raise ValueError("sector is required")
        source = raw.get("candidate_source", raw.get("source_list"))
        if not isinstance(source, str) or not source.strip(): raise ValueError("candidate_source is required")
        source = source.strip()
        key = "|".join((day, ticker, expiry, str(dte), habitat, sector, source))
        pair = (ticker, day)
        excluded = pair in held
        binding = raw.get("calendar_binding")
        if not excluded:
            if calendar_snapshot is None:
                if isinstance(binding, Mapping):
                    raise ValueError("calendar snapshot is required to validate a caller calendar binding")
                # Legacy census construction remains network-free, but this
                # unbound row is never eligible for non-dry-run acquisition.
                binding = None
            else:
                try:
                    if not isinstance(binding, Mapping):
                        binding = calendar_for_probe(calendar_snapshot, ticker=ticker, calendar_day=day, expiry=expiry, dte=dte, as_of=as_of, window_policy=window_policy)
                    binding = _validate_calendar_binding(binding, ticker=ticker, day=day, expiry=expiry, dte=dte, calendar_snapshot=calendar_snapshot)
                except (AttributeError, CalendarGapError, TypeError, ValueError) as exc:
                    raise ValueError(f"calendar binding: {exc}") from exc
        elif isinstance(binding, Mapping):
            binding = _validate_calendar_binding(binding, ticker=ticker, day=day, expiry=expiry, dte=dte)
        item = {"calendar_day": day, "ticker": ticker, "expiry": expiry, "dte": dte, "habitat": habitat, "sector": sector, "candidate_source": source, "asset_type": str(raw.get("asset_type", "equity")), "dte_stratum": list(next(s for s in DTE_STRATA if s[0] <= dte <= s[1])), "candidate_key": key, "held_pair_exclusion": excluded, "held_pair_exclusion_reason": "held_ticker_day" if excluded else None, "held_day_reference": refs.get(pair), "calendar_binding": binding, **({"declared_timezone": raw["declared_timezone"]} if raw.get("declared_timezone") is not None else {})}
        previous = result_by_key.get(key)
        if previous is None or canonical_json_bytes(item) < canonical_json_bytes(previous):
            result_by_key[key] = item
    return sorted(result_by_key.values(), key=lambda x: tuple(x[k] for k in ("calendar_day", "ticker", "expiry", "dte", "habitat", "sector", "candidate_source")))


def _extract_l2(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    record = payload.get("record") if isinstance(payload.get("record"), Mapping) else payload
    l2 = record.get("l2") if isinstance(record, Mapping) and isinstance(record.get("l2"), Mapping) else record
    return l2 if isinstance(l2, Mapping) else {}


_ISO_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")
def _timestamp(value: Any) -> dt.datetime:
    """Parse a timezone-qualified ISO-8601 instant and normalize it to UTC."""
    if not isinstance(value, str) or not _ISO_TIMESTAMP_RE.fullmatch(value):
        raise ValueError(f"invalid timezone-qualified ISO-8601 timestamp: {value!r}")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    return dt.datetime.fromisoformat(text).astimezone(dt.UTC)


def _captured_row_timestamp(day: Any, ms_of_day: Any, timezone: Any) -> str:
    """Convert an attested endpoint date/ms pair to a qualified UTC instant."""
    if not isinstance(timezone, str) or not timezone.strip():
        raise ValueError("declared_timezone is required for captured rows")
    from zoneinfo import ZoneInfo

    date_text = _date(day)
    if isinstance(ms_of_day, bool) or not isinstance(ms_of_day, int) or not 0 <= ms_of_day < 86_400_000:
        raise ValueError("invalid captured row ms_of_day")
    local = dt.datetime.combine(dt.date.fromisoformat(date_text), dt.time())
    local += dt.timedelta(milliseconds=ms_of_day)
    return local.replace(tzinfo=ZoneInfo(timezone.strip())).astimezone(dt.UTC).isoformat().replace("+00:00", "Z")


def _captured_table_rows(call: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    """Decode a successful table call without hiding malformed required rows."""
    payload = call.get("payload")
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
        return None
    headers = [str(header) for header in payload[0]]
    if len(headers) != len(set(headers)) or not headers:
        return None
    rows: list[dict[str, Any]] = []
    for row in payload[1:]:
        if not isinstance(row, list) or len(row) != len(headers):
            return None
        rows.append(dict(zip(headers, row, strict=True)))
    return rows


def _captured_call_hash(call: Mapping[str, Any]) -> str:
    """Validate the recorded hash against the exact captured table payload."""
    value = call.get("payload_sha256")
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise ValueError("successful captured call lacks a valid payload_sha256")
    try:
        expected = canonical_sha256(call.get("payload"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("successful captured call payload cannot be canonically hashed") from exc
    if value.lower() != expected:
        raise ValueError("captured call payload_sha256 does not match payload")
    return expected


def _captured_call_rows(calls: Iterable[Mapping[str, Any]], required: str) -> list[tuple[Mapping[str, Any], dict[str, Any]]]:
    """Return rows with their producing call; any malformed successful call blocks."""
    selected: list[tuple[Mapping[str, Any], dict[str, Any]]] = []
    for call in calls:
        rows = _captured_table_rows(call)
        if rows is None:
            raise ValueError(f"malformed required {required} endpoint payload")
        _captured_call_hash(call)
        selected.extend((call, row) for row in rows)
    if not selected:
        raise ValueError(f"required {required} endpoint data is absent")
    return selected


def _map_captured_payload(unit: Mapping[str, Any], payload: Any) -> dict[str, Any]:
    """Map captured Theta table rows into the strict provenance fields.

    Missing timezone, malformed rows, or fewer than two IV observations leave
    the mapping incomplete; the existing validator then fails closed.
    """
    if not isinstance(payload, Mapping) or not isinstance(payload.get("calls"), list):
        return {}
    timezone = unit.get("declared_timezone")
    metrics = payload.get("metrics") if isinstance(payload.get("metrics"), Mapping) else {}
    # Acquisition-side eligibility is an attestation, not something mapping may infer.
    decision = str(payload.get("decision", metrics.get("decision", ""))).upper()
    if metrics.get("breach_eligible") is not True or decision == "HARD_GAP" or str(payload.get("status", "")).upper() == "HARD_GAP":
        return {"_mapping_status": "HARD_GAP", "_mapping_reason": "raw acquisition is not breach eligible"}
    calls = [call for call in payload["calls"] if isinstance(call, Mapping) and call.get("response_status") == 200]
    spot_calls = [call for call in calls if "/stock/ohlc/" in str(call.get("endpoint", ""))]
    chain_calls = [call for call in calls if "/option/all_greeks/" in str(call.get("endpoint", ""))]
    try:
        spot_rows = _captured_call_rows(spot_calls, "spot")
        chain_rows = _captured_call_rows(chain_calls, "chain")
        breach_ms = metrics.get("breach_window_start_prov")
        breach = _captured_row_timestamp(unit.get("calendar_day"), breach_ms, timezone)
        breach_dt = _timestamp(breach)
        spot_observed = sorted(((_timestamp(_captured_row_timestamp(row.get("date"), row.get("ms_of_day"), timezone)), call) for call, row in spot_rows if _timestamp(_captured_row_timestamp(row.get("date"), row.get("ms_of_day"), timezone)) < breach_dt), key=lambda item: item[0])
        chain_observed = sorted(((_timestamp(_captured_row_timestamp(row.get("date"), row.get("ms_of_day"), timezone)), call) for call, row in chain_rows if _timestamp(_captured_row_timestamp(row.get("date"), row.get("ms_of_day"), timezone)) < breach_dt), key=lambda item: item[0])
        if not spot_observed or not chain_observed:
            return {}
        spot_ts, spot_call = spot_observed[-1]
        chain_ts, chain_call = chain_observed[-1]
        iv_rows: list[tuple[dt.datetime, float, Mapping[str, Any], str, str]] = []
        for call, row in chain_rows:
            endpoint = str(call.get("endpoint", ""))
            # Every row in required successful chain data must be well formed;
            # valid rows must not hide a malformed mixed response.
            timestamp = _captured_row_timestamp(row.get("date"), row.get("ms_of_day"), timezone)
            iv = float(row.get("implied_vol"))
            if not math.isfinite(iv):
                raise ValueError("malformed required IV row")
            row_ts = _timestamp(timestamp)
            if row_ts < breach_dt:
                iv_rows.append((row_ts, iv, row, endpoint, _captured_call_hash(call)))
        # Keep one observed IV per timestamp, preferring the row closest to
        # its attested underlying price rather than averaging strikes.
        by_timestamp: dict[dt.datetime, tuple[float, Mapping[str, Any], str, str]] = {}
        for timestamp, iv, row, endpoint, call_hash in iv_rows:
            try:
                distance = abs(float(row.get("strike")) / 1000.0 - float(row.get("underlying_price")))
            except (TypeError, ValueError):
                distance = math.inf
            previous = by_timestamp.get(timestamp)
            if previous is None or distance < previous[0]:
                by_timestamp[timestamp] = (distance, {"iv": iv, **row}, endpoint, call_hash)
        observed = sorted((timestamp, value[1], value[2], value[3]) for timestamp, value in by_timestamp.items())
        if len(observed) < 2:
            return {}
        before_ts, before_row, before_endpoint, before_hash = observed[-2]
        source_ts, source_row, source_endpoint, source_hash = observed[-1]
        observations = [
            {"role": _PREWINDOW, "timestamp": before_ts.isoformat().replace("+00:00", "Z"), "iv": float(before_row["iv"]), "source_identity": before_endpoint, "source_hash": before_hash},
            {"role": _PREWINDOW, "timestamp": source_ts.isoformat().replace("+00:00", "Z"), "iv": float(source_row["iv"]), "source_identity": source_endpoint, "source_hash": source_hash},
        ]
        source_hashes = sorted({before_hash, source_hash, _captured_call_hash(spot_call), _captured_call_hash(chain_call)})
        if len(source_hashes) < 2:
            return {}
        return {"delta_iv_provenance": _PREWINDOW, "delta_iv_pre_window": observations[1]["iv"] - observations[0]["iv"],
                "iv_before_ts": observations[0]["timestamp"], "iv_before_value": observations[0]["iv"],
                "iv_source_ts": observations[1]["timestamp"], "iv_source_value": observations[1]["iv"],
                "breach_window_start_prov": breach, "declared_timezone": timezone,
                "spot_timestamp": spot_ts.isoformat().replace("+00:00", "Z"), "chain_timestamp": chain_ts.isoformat().replace("+00:00", "Z"),
                "spot_source_identity": str(spot_call.get("endpoint")), "spot_source_hash": _captured_call_hash(spot_call),
                "chain_source_identity": str(chain_call.get("endpoint")), "chain_source_hash": _captured_call_hash(chain_call),
                "endpoint": source_endpoint, "request_parameters": {"captured_calls": len(calls)},
                "source_hashes": source_hashes, "pre_window_observations": observations,
                "delta_iv_aggregation": "iv_source_minus_iv_before", "delta_iv_aggregation_version": "1"}
    except (TypeError, ValueError, OSError) as exc:
        return {"_mapping_status": "HARD_GAP", "_mapping_reason": str(exc)[:200]}


def _unit_from_payload(unit: Mapping[str, Any], payload: Any) -> dict[str, Any]:
    raw_hash = _hash(payload)
    l2 = dict(_extract_l2(payload)) if isinstance(payload, Mapping) else {}
    mapped = _map_captured_payload(unit, payload)
    mapping_status = mapped.get("_mapping_status") if isinstance(mapped, Mapping) else None
    mapping_reason = mapped.get("_mapping_reason") if isinstance(mapped, Mapping) else None
    if mapping_status is None and isinstance(mapped, Mapping):
        conflicts = [
            key for key, value in mapped.items()
            if not key.startswith("_") and key in l2 and l2[key] is not None and l2[key] != value
        ]
        if conflicts:
            mapped = {
                "_mapping_status": "HARD_GAP",
                "_mapping_reason": "record.l2 conflicts with captured endpoint provenance: " + ", ".join(sorted(conflicts)),
            }
            mapping_status = mapped["_mapping_status"]
            mapping_reason = mapped["_mapping_reason"]
    l2.update({key: value for key, value in mapped.items() if not key.startswith("_") and (key not in l2 or l2[key] is None)})
    prov = str(l2.get("delta_iv_provenance", "")).upper()
    value = l2.get("delta_iv_pre_window")
    source_ts, breach_ts = l2.get("iv_source_ts"), l2.get("breach_window_start_prov")
    root = payload if isinstance(payload, Mapping) else {}
    supplied_hashes = l2.get("source_hashes", root.get("source_hashes"))
    declared_timezone = l2.get("declared_timezone", root.get("declared_timezone", unit.get("declared_timezone")))
    endpoint = l2.get("endpoint", l2.get("request_endpoint", root.get("endpoint", root.get("request_endpoint"))))
    parameters = l2.get("request_parameters", l2.get("parameters", root.get("request_parameters", root.get("parameters"))))
    spot_timestamp = l2.get("spot_timestamp", root.get("spot_timestamp"))
    chain_timestamp = l2.get("chain_timestamp", root.get("chain_timestamp"))
    iv_before_ts = l2.get("iv_before_ts", root.get("iv_before_ts"))
    iv_before_value = l2.get("iv_before_value", root.get("iv_before_value"))
    iv_source_value = l2.get("iv_source_value", root.get("iv_source_value"))
    aggregation = l2.get("delta_iv_aggregation", l2.get("aggregation_id", root.get("delta_iv_aggregation", root.get("aggregation_id"))))
    aggregation_version = l2.get("delta_iv_aggregation_version", l2.get("aggregation_version", root.get("delta_iv_aggregation_version", root.get("aggregation_version"))))
    cluster = l2.get("same_day_cluster", root.get("same_day_cluster"))
    request_parameters = dict(parameters) if isinstance(parameters, Mapping) else parameters
    valid = False
    timestamp_reason = mapping_reason
    eligibility = payload.get("metrics", {}).get("breach_eligible") if isinstance(payload, Mapping) and isinstance(payload.get("metrics"), Mapping) else None
    raw_decision = str(payload.get("decision", payload.get("metrics", {}).get("decision", ""))).upper() if isinstance(payload, Mapping) else ""
    if mapping_status == "HARD_GAP":
        timestamp_reason = mapping_reason or "raw acquisition is not breach eligible"
    elif eligibility is not True or raw_decision == "HARD_GAP":
        timestamp_reason = "explicit breach eligibility is required"
    elif prov == _PREWINDOW and value is not None and not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value)):
        try:
            validate_source_hashes(supplied_hashes)
            source = _timestamp(source_ts)
            breach = _timestamp(breach_ts)
            spot = _timestamp(spot_timestamp)
            chain = _timestamp(chain_timestamp)
            if not isinstance(declared_timezone, str) or not declared_timezone:
                raise ValueError("declared_timezone is required")
            from zoneinfo import ZoneInfo
            zone = ZoneInfo(declared_timezone)
            day = _date(unit.get("calendar_day"))
            if source >= breach:
                timestamp_reason = "PRE_WINDOW source timestamp must strictly precede breach"
            elif any(ts.astimezone(zone).date().isoformat() != day for ts in (source, breach, spot, chain)):
                timestamp_reason = "PRE_WINDOW timestamps must match calendar day"
            elif not isinstance(endpoint, str) or not endpoint.strip() or not isinstance(request_parameters, Mapping):
                timestamp_reason = "acquisition endpoint and request_parameters are required"
            elif iv_before_ts is None or iv_before_value is None or iv_source_value is None or not aggregation or not aggregation_version:
                timestamp_reason = "two timestamped pre-window IV observations and aggregation are required"
            else:
                before = _timestamp(iv_before_ts)
                source_value = float(iv_source_value)
                before_value = float(iv_before_value)
                if before >= source or source >= breach:
                    timestamp_reason = "pre-window IV timestamps must be ordered before breach"
                elif before.astimezone(zone).date().isoformat() != day or not math.isfinite(source_value) or not math.isfinite(before_value):
                    timestamp_reason = "pre-window IV observations are invalid or wrong-day"
                elif aggregation != "iv_source_minus_iv_before" or str(aggregation_version) != "1":
                    timestamp_reason = "unsupported pre-window IV aggregation"
                elif not math.isclose(float(value), source_value - before_value, rel_tol=1e-12, abs_tol=1e-12):
                    timestamp_reason = "delta_iv_pre_window does not equal registered aggregation"
                else:
                    valid = True
        except (TypeError, ValueError, OSError) as exc:
            timestamp_reason = str(exc)
    status = "PASS" if valid else ("HARD_GAP" if mapping_status == "HARD_GAP" or payload is None or eligibility is not True or raw_decision == "HARD_GAP" else "ASSOCIATIONAL")
    artifact = dict(unit)
    binding = unit.get("calendar_binding")
    if not isinstance(binding, Mapping):
        status = "HARD_GAP"
        timestamp_reason = timestamp_reason or "COMPARISON_INVALID: calendar binding is required"
    else:
        # Persist the supplied Stage-2 binding as immutable Task-3 evidence;
        # Task 4 consumes these fields and never re-resolves them.
        artifact.update({
            "calendar_hash": binding.get("calendar_hash"),
            "calendar_policy_version": binding.get("calendar_policy_version"),
            "resolver_code_hash": binding.get("resolver_code_hash"),
            "snapshot_hash": binding.get("snapshot_hash"),
            "as_of": binding.get("as_of"),
            "event_ids": list(binding.get("event_ids", ())),
            "event_types": sorted({str(v.get("event_type")) for v in binding.get("event_windows", {}).values() if isinstance(v, Mapping) and v.get("event_type")}),
            "event_overlap": len(binding.get("event_ids", ())) > 1,
            "event_window_id": binding.get("event_window_id"),
            "window_start": binding.get("window_start"),
            "window_end": binding.get("window_end"),
            "window_policy": binding.get("window_policy"),
            "nominal_date": binding.get("nominal_date"),
            "observed_expiry_date": binding.get("observed_expiry_date"),
            "session_id": binding.get("session_id"),
            "session_status": binding.get("session_status"),
            "regular_open": binding.get("regular_open"),
            "regular_close": binding.get("regular_close"),
            "early_close": binding.get("early_close"),
            "settlement_style": binding.get("settlement_style"),
            "settlement_timestamp": binding.get("settlement_timestamp"),
            "timezone": binding.get("timezone"),
            "calendar_binding_hash": binding.get("calendar_binding_hash"),
        })
    artifact.update({"status": status, "breach_eligible": eligibility, "acquisition_decision": raw_decision or None, "pre_window_provenance": prov or "ASSOCIATIONAL", "pre_window_value": value if valid else None, "delta_iv_pre_window": value if valid else None, "iv_source_ts": source_ts, "breach_window_start_prov": breach_ts, "declared_timezone": declared_timezone, "endpoint": endpoint, "parameters": request_parameters, "request_parameters": request_parameters, "spot_timestamp": spot_timestamp, "chain_timestamp": chain_timestamp, "iv_before_ts": iv_before_ts, "iv_before_value": iv_before_value, "iv_source_value": iv_source_value, "pre_window_observations": l2.get("pre_window_observations", []), "delta_iv_aggregation": aggregation, "delta_iv_aggregation_version": aggregation_version, "same_day_cluster": cluster, "spot_source_identity": l2.get("spot_source_identity"), "spot_source_hash": l2.get("spot_source_hash"), "chain_source_identity": l2.get("chain_source_identity"), "chain_source_hash": l2.get("chain_source_hash"), "source_hashes": list(supplied_hashes) if isinstance(supplied_hashes, (list, tuple)) else None, "canonical_input_hash": unit.get("canonical_input_hash"), "imputed": False, "no_imputation": True, "raw_payload_hash": raw_hash})
    manifest = {"candidate_key": unit["candidate_key"], "ticker": unit.get("ticker"),
                "calendar_day": unit.get("calendar_day"), "canonical_input_hash": unit.get("canonical_input_hash"),
                "raw_payload_hash": raw_hash, "status": status,
                "expiry": unit.get("expiry"), "dte": unit.get("dte"),
                "request_identity": _probe_request(unit), "calendar_binding": unit.get("calendar_binding"),
                "source_hashes": artifact.get("source_hashes"),
                "imputed": False, "no_imputation": True}
    for field in ("calendar_hash", "calendar_policy_version", "resolver_code_hash", "snapshot_hash", "as_of",
                  "event_ids", "event_types", "event_overlap", "event_window_id", "window_start", "window_end",
                  "window_policy", "nominal_date", "observed_expiry_date", "session_id", "session_status",
                  "regular_open", "regular_close", "early_close", "settlement_style", "settlement_timestamp",
                  "timezone", "calendar_binding_hash"):
        manifest[field] = artifact.get(field)
    artifact["artifact_manifest"] = manifest
    artifact["artifact_basis"] = canonical_json_bytes(manifest).decode("utf-8")
    artifact["artifact_hash"] = _hash(manifest)
    artifact["reason"] = None if status == "PASS" else (timestamp_reason or ("missing_or_associational_prewindow" if status == "ASSOCIATIONAL" else "hard_gap"))
    return artifact


def build_provenance_census(units: Iterable[Mapping[str, Any]], *, intended_units: int, fail_loud: bool = False, generated_at: str | None = None, artifact_registry: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if intended_units < 0: raise ValueError("intended_units cannot be negative")
    rows = [dict(u) for u in units]
    rows.sort(key=lambda u: (str(u.get("calendar_day", "")), str(u.get("ticker", "")), str(u.get("candidate_key", ""))))
    from .run_live_vs_expiry_book_common_input import validate_causal_eligibility
    causal = validate_causal_eligibility(rows, intended_units=intended_units, artifact_registry=artifact_registry)
    n = sum(u.get("status") == "PASS" and str(u.get("pre_window_provenance", "")).upper() == _PREWINDOW for u in rows)
    coverage = n / intended_units if intended_units else 1.0
    gate = len(rows) == intended_units and coverage == 1.0 and causal["causal_status"] == "CAUSAL_ELIGIBLE"
    day_groups = defaultdict(list)
    for unit in rows: day_groups[str(unit.get("calendar_day", ""))].append(unit)
    day_gate = all(all(item.get("status") == "PASS" and str(item.get("pre_window_provenance", "")).upper() == _PREWINDOW for item in group) for group in day_groups.values())
    gate = gate and day_gate
    reasons = list(causal["reasons"])
    doc = {"provenance_census": True, "units": rows, "unit_count": len(rows), "intended_units": intended_units, "pre_window_n": n, "pre_window_N": intended_units, "pre_window_coverage": coverage, "gate_pass": gate, "causal_status": "CAUSAL_ELIGIBLE" if gate else "CAUSAL_BLOCKED", "comparison_status": "COMPARISON_VALID" if gate else "COMPARISON_INVALID", "reasons": reasons, "fail_loud": fail_loud, "generated_at": generated_at or dt.datetime.now(dt.UTC).isoformat(), "no_imputation": True, "raw_payload_hash_census": all(bool(u.get("raw_payload_hash")) for u in rows), "statuses": {s: sum(u.get("status") == s for u in rows) for s in ("PASS", "INELIGIBLE", "HARD_GAP", "ASSOCIATIONAL")}, "pass_n": sum(u.get("status") == "PASS" for u in rows), "ineligible_n": sum(u.get("status") == "INELIGIBLE" for u in rows), "hard_gap_n": sum(u.get("status") == "HARD_GAP" for u in rows), "associational_n": sum(u.get("status") == "ASSOCIATIONAL" for u in rows), "associational_exclusions": [u for u in rows if u.get("status") == "ASSOCIATIONAL"], "ineligible_exclusions": [u for u in rows if u.get("status") == "INELIGIBLE"], "same_day_gate": day_gate, "gate_reason": "100% PRE_WINDOW coverage" if gate else f"strict causal eligibility failed ({n}/{intended_units})", "causal_reasons": reasons}
    if fail_loud and not gate: raise AcquisitionGateError(f"100% PRE_WINDOW gate failed: {n}/{intended_units}")
    return doc


def cluster_same_day(units: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for u in units: grouped[str(u["calendar_day"])].append(u)
    rank = {"PASS": 0, "INELIGIBLE": 1, "ASSOCIATIONAL": 2, "HARD_GAP": 3}
    out = {}
    for day in sorted(grouped):
        rows = grouped[day]; statuses = [str(u.get("status", "HARD_GAP")) if u.get("status") in _STATUS else "HARD_GAP" for u in rows]
        nested = sorted((dict(u) for u in rows), key=lambda u: (str(u.get("ticker", "")), str(u.get("candidate_key", ""))))
        out[day] = {"calendar_day": day, "tickers": sorted({str(u.get("ticker", "")) for u in rows}), "n_tickers": len({u.get("ticker") for u in rows}), "expiry_set": sorted({str(u.get("expiry")) for u in rows if u.get("expiry") is not None}), "status": max(statuses, key=lambda s: rank[s]), "status_counts": {s: statuses.count(s) for s in ("PASS", "INELIGIBLE", "HARD_GAP", "ASSOCIATIONAL")}, "units": nested, "aggregation_rule": "preserve ticker-specific values; no averaging before fitting"}
    return out


def _probe_request(unit: Mapping[str, Any]) -> dict[str, Any]:
    request = {k: unit[k] for k in ("calendar_day", "ticker", "expiry", "dte", "habitat", "sector", "candidate_source")}
    request["calendar_binding"] = unit.get("calendar_binding")
    return request


def run_availability_probes(schedule: Iterable[Mapping[str, Any]], *, probe_fetcher: Callable[[Mapping[str, Any]], Any] | None = None, approval: bool = False, authorization: AcquisitionAuthorization | None = None, authorization_context: Any | None = None, dry_run: bool = True, probe_only: bool = False, code_version: str = "dealer-exposure-probe-v1", code_hash: str | None = None, calendar_snapshot: Any | None = None) -> list[dict[str, Any]]:
    """Run sequential lightweight probes; never dispatches the heavy fetcher."""
    ordered = sorted((dict(u) for u in schedule), key=lambda x: x["candidate_key"])
    if dry_run or probe_only or not approval or probe_fetcher is None:
        return [{"candidate_key": u["candidate_key"], "status": "HARD_GAP" if probe_fetcher is None and approval and not (dry_run or probe_only) else "INELIGIBLE", "reason": "probe_not_run", "request_parameters": _probe_request(u), "response_status": None, "response_counts": {}, "source_counts": {}, "probe_code_version": code_version, "probe_code_hash": code_hash or _hash(code_version), "validated": False, "invoked": False} for u in ordered]
    if not isinstance(authorization_context, _AdmissionContext) or authorization_context.authorization is not authorization:
        return [{"candidate_key": u["candidate_key"], "status": "HARD_GAP", "reason": "admitted authorization context is required; boolean approval is not authorization", "request_parameters": _probe_request(u), "response_status": None, "response_counts": {}, "source_counts": {}, "probe_code_version": code_version, "probe_code_hash": code_hash or _hash(code_version), "validated": False, "invoked": False, "comparison_status": "COMPARISON_INVALID"} for u in ordered]
    results = []
    for unit in ordered:
        request = _probe_request(unit)
        if not unit.get("held_pair_exclusion") and calendar_snapshot is None:
            results.append({"candidate_key": unit["candidate_key"], "ticker": unit.get("ticker"), "day": unit.get("calendar_day"), "expiry": unit.get("expiry"), "dte": unit.get("dte"), "status": "HARD_GAP", "reason": "COMPARISON_INVALID: exact CalendarSnapshot is required", "request_parameters": request, "response_status": None, "response_counts": {}, "source_counts": {}, "evidence": {}, "probe_code_version": code_version, "probe_code_hash": code_hash or _hash(code_version), "validated": False, "invoked": False, "comparison_status": "COMPARISON_INVALID"})
            continue
        try:
            response, runtime_usage = authorization_context.call("probe", lambda: probe_fetcher(request))
            if not isinstance(response, Mapping):
                raise TypeError("probe response must be a mapping")
            status = str(response.get("status", "HARD_GAP")).upper()
            if status not in {"PASS", "INELIGIBLE", "HARD_GAP"}:
                status = "HARD_GAP"
            response_status = response.get("response_status", response.get("status_code"))
            response_counts = dict(response.get("response_counts", response.get("counts", {})) or {})
            source_counts = dict(response.get("source_counts", {}) or {})
            evidence = dict(response.get("evidence", {}) or {})
            if "calendar_binding" in response:
                evidence["calendar_binding"] = response["calendar_binding"]
            validated = status == "PASS" and response_status is not None and bool(response_counts) and bool(source_counts)
            reason = response.get("reason")
            comparison_status = "COMPARISON_VALID"
            if status == "PASS" and not validated:
                status = "HARD_GAP"
                comparison_status = "COMPARISON_INVALID"
                reason = reason or "COMPARISON_INVALID: incomplete probe evidence"
            if validated:
                supplied = evidence.get("calendar_binding")
                try:
                    checked = _validate_calendar_binding(supplied, ticker=str(unit["ticker"]), day=str(unit["calendar_day"]), expiry=str(unit["expiry"]), dte=int(unit["dte"]), expected=unit.get("calendar_binding"), calendar_snapshot=calendar_snapshot)
                    if not isinstance(unit.get("calendar_binding"), Mapping) or checked != dict(unit["calendar_binding"]):
                        raise ValueError("calendar binding does not exactly match schedule")
                except (TypeError, ValueError) as exc:
                    status, validated, comparison_status = "HARD_GAP", False, "COMPARISON_INVALID"
                    reason = f"COMPARISON_INVALID: {exc}"
                if status == "PASS" and unit.get("held_pair_exclusion"):
                    status, validated, comparison_status = "HARD_GAP", False, "COMPARISON_INVALID"
                    reason = reason or "COMPARISON_INVALID: held-pair exclusion is non-admissible"
            results.append({"candidate_key": unit["candidate_key"], "ticker": unit.get("ticker"), "day": unit.get("calendar_day"), "expiry": unit.get("expiry"), "dte": unit.get("dte"), "status": status, "reason": reason, "request_parameters": request, "response_status": response_status, "response_counts": response_counts, "source_counts": source_counts, "evidence": evidence, "probe_code_version": code_version, "probe_code_hash": code_hash or _hash(code_version), "validated": validated, "invoked": True, "comparison_status": comparison_status, "network_executed": False, "admitted": False, "network": False, "admission": False, "runtime_usage": runtime_usage})
        except Exception as exc:  # noqa: BLE001 - adapter failures are auditable HARD_GAPs
            results.append({"candidate_key": unit["candidate_key"], "status": "HARD_GAP", "reason": str(exc)[:200], "request_parameters": request, "response_status": None, "response_counts": {}, "source_counts": {}, "probe_code_version": code_version, "probe_code_hash": code_hash or _hash(code_version), "validated": False, "invoked": True, "comparison_status": "COMPARISON_INVALID"})
    return results


def select_primary_schedule(schedule: Iterable[Mapping[str, Any]], probes: Iterable[Mapping[str, Any]], *, calendar_snapshot: Any | None = None) -> list[dict[str, Any]]:
    """Admit only PASS probes with a recomputed, exact calendar binding."""
    by_key: dict[str, Mapping[str, Any]] = {}
    if calendar_snapshot is None:
        return []
    for probe in probes:
        if probe.get("status") != "PASS" or probe.get("validated") is not True or probe.get("invoked") is not True:
            continue
        key = probe.get("candidate_key")
        evidence = probe.get("evidence")
        binding = evidence.get("calendar_binding") if isinstance(evidence, Mapping) else None
        if isinstance(key, str) and isinstance(binding, Mapping):
            try:
                checked = _validate_calendar_binding(binding, ticker=str(probe.get("ticker", "")), day=str(probe.get("day", "")), expiry=str(probe.get("expiry", "")), dte=int(probe.get("dte")), calendar_snapshot=calendar_snapshot)
                if checked == dict(binding):
                    by_key[key] = probe
            except (TypeError, ValueError):
                continue
    admitted = []
    for unit in sorted((dict(u) for u in schedule), key=lambda x: x["candidate_key"]):
        probe = by_key.get(unit.get("candidate_key")); schedule_binding = unit.get("calendar_binding")
        if unit.get("held_pair_exclusion") or probe is None or not isinstance(schedule_binding, Mapping):
            continue
        try:
            _validate_calendar_binding(schedule_binding, ticker=str(unit["ticker"]), day=str(unit["calendar_day"]), expiry=str(unit["expiry"]), dte=int(unit["dte"]), calendar_snapshot=calendar_snapshot)
        except (TypeError, ValueError):
            continue
        if dict(probe["evidence"]["calendar_binding"]) != dict(schedule_binding):
            continue
        admitted.append(unit)
    return admitted


def execute_sequential_acquisition(schedule: Iterable[Mapping[str, Any]], *, fetcher: Callable[[Mapping[str, Any]], Any] | None = None, approval: bool = False, authorization: AcquisitionAuthorization | Mapping[str, Any] | None = None, admission_evidence: Mapping[str, Any] | None = None, registry: Mapping[str, Any] | None = None, dry_run: bool = True, probe_only: bool = False, fail_loud: bool = False, output_dir: str | Path | None = None, probe_fetcher: Callable[[Mapping[str, Any]], Any] | None = None, probe_code_version: str = "dealer-exposure-probe-v1", probe_code_hash: str | None = None, generated_at: str | None = None, calendar_snapshot: Any | None = None) -> dict[str, Any]:
    if os.environ.get("THETADATA_HIST_CONCURRENCY", "1") != "1": raise AcquisitionGateError("THETADATA_HIST_CONCURRENCY=1 is required")
    if not dry_run and (authorization is None or isinstance(authorization, bool)):
        raise AcquisitionGateError("validated authorization is required; boolean approval is not authorization")
    if not dry_run and not approval: raise AcquisitionGateError("explicit authorization handoff is required")
    executor_identity: dict[str, Any] | None = None
    if not dry_run and not probe_only:
        executor_ok, executor_identity, executor_reason = _authorized_executor(fetcher, authorization)
        if not executor_ok:
            raise AcquisitionGateError(f"authorized executor required: {executor_reason}")
    ordered = sorted((dict(u) for u in schedule), key=lambda x: x["candidate_key"])
    authorization_context = None
    if not dry_run and not probe_only:
        if not isinstance(admission_evidence, Mapping) or not isinstance(registry, Mapping):
            raise AcquisitionGateError("evidence and artifact registry are required before probe admission")
        authorization_context = _preflight_authorization(authorization, authorization.candidate_manifest_projection())
        if authorization_context is None:
            raise AcquisitionGateError("calendar-enriched manifest and authorization preflight failed")
        pre_admitted, pre_audit = admit_acquisition(
            authorization, authorization_context.manifest,
            admission_evidence.get("probes", ()), admission_evidence, registry,
        )
        if not pre_admitted:
            raise AcquisitionGateError("acquisition admission failed before probe dispatch")
        probes = list(admission_evidence.get("probes", ()))
    else:
        pre_audit = None
        probes = run_availability_probes(ordered, probe_fetcher=probe_fetcher, approval=approval, authorization=authorization, authorization_context=authorization_context, dry_run=dry_run, probe_only=probe_only, code_version=probe_code_version, code_hash=probe_code_hash, calendar_snapshot=calendar_snapshot)
    admission_audit = pre_audit
    if not dry_run and not probe_only:
        if not hasattr(authorization, "candidate_manifest_projection"):
            admission_audit = _immutable({"admitted": False, "stages": (), "admitted_keys": (), "blocked": ["typed AcquisitionAuthorization is required at acquisition handoff"]})
            primary_schedule = []
        else:
            admitted, admission_audit = admit_acquisition(
                authorization, authorization.candidate_manifest_projection(), probes,
                admission_evidence or {}, registry or {},
            )
            primary_schedule = [dict(unit) for unit in admitted]
    else:
        primary_schedule = select_primary_schedule(ordered, probes, calendar_snapshot=calendar_snapshot)
    primary_keys = {u["candidate_key"] for u in primary_schedule}
    probe_by_key = {p["candidate_key"]: p for p in probes}
    units = []
    payloads: dict[str, Any] = {}
    network_executed = False
    runtime_usage = dict(authorization_context.usage) if isinstance(authorization_context, _AdmissionContext) else {"units": 0, "probe_calls": 0, "heavy_calls": 0, "total_endpoint_calls": 0, "payload_bytes": 0, "wall_seconds": 0.0, "concurrency": 0}
    for unit in ordered:
        if unit.get("held_pair_exclusion"):
            payload, status, reason = {"mode": "held-exclusion", "candidate_key": unit["candidate_key"], "network": False}, "INELIGIBLE", "held_pair_exclusion"
            item = _unit_from_payload(unit, None)
        elif dry_run or probe_only:
            payload, status, reason = {"mode": "probe-only", "candidate_key": unit["candidate_key"], "network": False}, "INELIGIBLE", "dry_run_probe_only"
            item = _unit_from_payload(unit, None)
        elif unit["candidate_key"] not in primary_keys:
            probe = probe_by_key.get(unit["candidate_key"], {})
            status = probe.get("status") if probe.get("status") in {"INELIGIBLE", "HARD_GAP"} else "HARD_GAP"
            reason = probe.get("reason") or ("validated PASS probe required" if status == "HARD_GAP" else "probe_ineligible")
            payload = {"mode": "not-primary", "candidate_key": unit["candidate_key"], "network": False}
            item = _unit_from_payload(unit, None)
        elif fetcher is None:
            payload, status, reason = {"mode": "acquisition", "candidate_key": unit["candidate_key"], "network": False}, "HARD_GAP", "heavy acquisition requires an injected fetcher"
            item = _unit_from_payload(unit, None)
        else:
            try:
                # This is deliberately immediately before the injected call: a
                # raised fetcher still proves that acquisition was attempted.
                _validate_calendar_binding(unit.get("calendar_binding"), ticker=str(unit["ticker"]), day=str(unit["calendar_day"]), expiry=str(unit["expiry"]), dte=int(unit["dte"]), calendar_snapshot=calendar_snapshot)
                network_executed = True
                payload, runtime_usage = authorization_context.call("heavy", lambda: fetcher(unit))
                item = _unit_from_payload(unit, payload)
                status, reason = item["status"], item["reason"]
            except Exception as exc:  # noqa: BLE001 - adapter failures are auditable HARD_GAPs
                payload, status, reason = {"error": str(exc)}, "HARD_GAP", str(exc)[:200]
                item = _unit_from_payload(unit, payload)
        item.update({"status": status, "reason": reason, "raw_payload_hash": _hash(payload)})
        payloads[unit["candidate_key"]] = payload
        manifest = {"candidate_key": unit["candidate_key"], "ticker": unit.get("ticker"), "calendar_day": unit.get("calendar_day"), "expiry": unit.get("expiry"), "dte": unit.get("dte"), "request_identity": _probe_request(unit), "calendar_binding": unit.get("calendar_binding"), "raw_payload_hash": item["raw_payload_hash"], "source_hashes": item.get("source_hashes"), "status": status, "imputed": False, "no_imputation": True}
        item["artifact_manifest"] = manifest
        item["artifact_basis"] = canonical_json_bytes(manifest).decode("utf-8")
        item["artifact_hash"] = _hash(manifest)
        units.append(item)
    # Persist explicit same-day cluster metadata on every acquired unit before
    # hashing the manifest; causal validation must see the clustering boundary.
    by_day: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in units:
        by_day[str(item["calendar_day"])].append(item)
    for day, group in by_day.items():
        tickers = sorted({str(item.get("ticker", "")) for item in group})
        cluster = {"cluster_id": day, "calendar_day": day, "tickers": tickers,
                   "n_tickers": len(tickers),
                   "aggregation_rule": "preserve_ticker_values_v1"}
        for item in group:
            item["same_day_cluster"] = cluster
            if item.get("status") == "PASS":
                manifest = {key: item[key] for key in (
                    "candidate_key", "ticker", "calendar_day", "canonical_input_hash", "status", "raw_payload_hash", "endpoint", "request_parameters",
                    "declared_timezone", "spot_timestamp", "chain_timestamp", "spot_source_identity", "spot_source_hash",
                    "chain_source_identity", "chain_source_hash", "iv_source_ts",
                    "breach_window_start_prov", "source_hashes", "pre_window_observations", "same_day_cluster", "iv_before_ts",
                    "iv_before_value", "iv_source_value", "delta_iv_aggregation",
                    "delta_iv_aggregation_version", "expiry", "dte", "calendar_binding", "imputed", "no_imputation",
                    "calendar_hash", "calendar_policy_version", "resolver_code_hash", "snapshot_hash", "as_of",
                    "event_ids", "event_types", "event_overlap", "event_window_id", "window_start", "window_end",
                    "window_policy", "nominal_date", "observed_expiry_date", "session_id", "session_status",
                    "regular_open", "regular_close", "early_close", "settlement_style", "settlement_timestamp",
                    "timezone", "calendar_binding_hash")}
                manifest["request_identity"] = _probe_request(item)
                item["artifact_manifest"] = manifest
                item["artifact_basis"] = canonical_json_bytes(manifest).decode("utf-8")
                item["artifact_hash"] = _hash(manifest)
    generated_at = generated_at or dt.datetime.now(dt.UTC).isoformat()
    artifact_registry = {
        str(item["artifact_hash"]): {
            "artifact_hash": item["artifact_hash"],
            "raw_payload_hash": item["raw_payload_hash"],
            "candidate_key": item.get("candidate_key"),
            "status": item.get("status"),
            "artifact_manifest": item["artifact_manifest"],
            "source_hashes": item.get("source_hashes"),
            "calendar_binding": item.get("calendar_binding"),
            "request_identity": item["artifact_manifest"].get("request_identity"),
            "evidence": item,
            "payload_bytes": payloads.get(item["candidate_key"]),
            "ticker": item.get("ticker"),
            "expiry": item.get("expiry"),
            "dte": item.get("dte"),
            "calendar_day": item.get("calendar_day"),
            "canonical_input_hash": item.get("canonical_input_hash"),
            **{field: item.get(field) for field in (
                "calendar_hash", "calendar_policy_version", "resolver_code_hash", "snapshot_hash", "as_of",
                "event_ids", "event_types", "event_overlap", "event_window_id", "window_start", "window_end",
                "window_policy", "nominal_date", "observed_expiry_date", "session_id", "session_status",
                "regular_open", "regular_close", "early_close", "settlement_style", "settlement_timestamp",
                "timezone", "calendar_binding_hash")},
        }
        for item in units
    }
    census = build_provenance_census(units, intended_units=len(ordered), fail_loud=fail_loud, generated_at=generated_at, artifact_registry=artifact_registry)
    if isinstance(authorization_context, _AdmissionContext):
        runtime_usage = dict(authorization_context.usage)
    result = {"mode": "probe-only" if (dry_run or probe_only) else "acquisition", "approval_required": True, "approval_granted": approval, "network_heavy_acquisition_executed": network_executed, "heavy_calls": runtime_usage["heavy_calls"], "runtime_usage": runtime_usage, "network_flag": network_executed, "no_imputation": True, "schedule": ordered, "primary_schedule": primary_schedule, "probes": probes, "units": units, "census": census, "same_day_clusters": cluster_same_day(units), "artifact_registry": artifact_registry, "admission_audit": admission_audit, "executor": executor_identity, "comparison_status": "COMPARISON_VALID" if census["gate_pass"] else "COMPARISON_INVALID", "causal_status": census["causal_status"], "generated_at": generated_at}
    if output_dir is not None:
        artifact_dir = Path(output_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / "dealer_exposure_acquisition.json"
        result["artifact_path"] = str(artifact_path)
        artifact_path.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return result


__all__ = ["NETWORK_ACQUISITION_EXECUTED", "AcquisitionGateError", "admit_acquisition", "build_candidate_schedule", "build_provenance_census", "cluster_same_day", "execute_sequential_acquisition", "run_availability_probes", "select_primary_schedule"]
