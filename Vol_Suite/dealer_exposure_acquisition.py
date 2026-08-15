"""Approval-gated, deterministic acquisition layer for dealer-exposure expansion.

This module is network-capable by dependency injection, but importing it and using
``dry_run``/``probe_only`` never performs endpoint acquisition.  All acquisition
is sequential ticker x calendar-day and the historical concurrency contract is
fail-closed at one.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from .dealer_exposure_universe import DTE_STRATA, EVENT_HABITATS, held_pairs_from_paths
from .provenance_contract import SHA256_RE, canonical_json_bytes, validate_source_hashes

NETWORK_ACQUISITION_EXECUTED = False
_STATUS = {"PASS", "INELIGIBLE", "HARD_GAP", "ASSOCIATIONAL"}
_PREWINDOW = "PRE_WINDOW"


class AcquisitionGateError(RuntimeError):
    """Raised when an approval, concurrency, or provenance gate fails."""


def _date(value: Any) -> str:
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return dt.date.fromisoformat(text).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid calendar day: {value!r}") from exc


def _hash(value: Any) -> str:
    blob = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False).encode()
    return hashlib.sha256(blob).hexdigest()


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


def build_candidate_schedule(candidates: Iterable[Mapping[str, Any]], *, held_paths: Iterable[str | Path] = (), held_pairs: Iterable[tuple[str, str]] = ()) -> list[dict[str, Any]]:
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
        item = {"calendar_day": day, "ticker": ticker, "expiry": expiry, "dte": dte, "habitat": habitat, "sector": sector, "candidate_source": source, "asset_type": str(raw.get("asset_type", "equity")), "dte_stratum": list(next(s for s in DTE_STRATA if s[0] <= dte <= s[1])), "candidate_key": key, "held_pair_exclusion": excluded, "held_pair_exclusion_reason": "held_ticker_day" if excluded else None, "held_day_reference": refs.get(pair)}
        previous = result_by_key.get(key)
        if previous is None or json.dumps(item, sort_keys=True, default=str) < json.dumps(previous, sort_keys=True, default=str):
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


def _unit_from_payload(unit: Mapping[str, Any], payload: Any) -> dict[str, Any]:
    raw_hash = _hash(payload)
    l2 = _extract_l2(payload) if isinstance(payload, Mapping) else {}
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
    timestamp_reason = None
    if prov == _PREWINDOW and value is not None and not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value)):
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
    status = "PASS" if valid else ("ASSOCIATIONAL" if payload is not None else "HARD_GAP")
    artifact = dict(unit)
    artifact.update({"status": status, "pre_window_provenance": prov or "ASSOCIATIONAL", "pre_window_value": value if valid else None, "delta_iv_pre_window": value if valid else None, "iv_source_ts": source_ts, "breach_window_start_prov": breach_ts, "declared_timezone": declared_timezone, "endpoint": endpoint, "parameters": request_parameters, "request_parameters": request_parameters, "spot_timestamp": spot_timestamp, "chain_timestamp": chain_timestamp, "iv_before_ts": iv_before_ts, "iv_before_value": iv_before_value, "iv_source_value": iv_source_value, "delta_iv_aggregation": aggregation, "delta_iv_aggregation_version": aggregation_version, "same_day_cluster": cluster, "source_hashes": list(supplied_hashes) if isinstance(supplied_hashes, (list, tuple)) else None, "imputed": False, "no_imputation": True, "raw_payload_hash": raw_hash})
    manifest = {"candidate_key": unit["candidate_key"], "raw_payload_hash": raw_hash, "status": status, "imputed": False}
    artifact["artifact_manifest"] = manifest
    artifact["artifact_basis"] = canonical_json_bytes(manifest).decode("utf-8")
    artifact["artifact_hash"] = _hash(manifest)
    artifact["reason"] = None if status == "PASS" else (timestamp_reason or ("missing_or_associational_prewindow" if status == "ASSOCIATIONAL" else "hard_gap"))
    return artifact


def build_provenance_census(units: Iterable[Mapping[str, Any]], *, intended_units: int, fail_loud: bool = False, generated_at: str | None = None) -> dict[str, Any]:
    if intended_units < 0: raise ValueError("intended_units cannot be negative")
    rows = [dict(u) for u in units]
    rows.sort(key=lambda u: (str(u.get("calendar_day", "")), str(u.get("ticker", "")), str(u.get("candidate_key", ""))))
    for u in rows:
        if u.get("status") not in _STATUS: raise AcquisitionGateError("invalid unit status")
        if not isinstance(u.get("raw_payload_hash"), str) or not SHA256_RE.fullmatch(u["raw_payload_hash"]): raise AcquisitionGateError("provenance census requires semantic SHA-256 payload hashes")
        if u.get("source_hashes") is not None:
            try: validate_source_hashes(u["source_hashes"])
            except ValueError as exc: raise AcquisitionGateError(str(exc)) from exc
        if u.get("status") == "PASS" and str(u.get("pre_window_provenance", "")).upper() == _PREWINDOW:
            required = ("source_hashes", "iv_source_ts", "breach_window_start_prov", "declared_timezone", "endpoint", "parameters", "spot_timestamp", "chain_timestamp", "artifact_manifest", "artifact_hash")
            missing = [key for key in required if u.get(key) in (None, "", [])]
            if missing:
                raise AcquisitionGateError(f"causal PASS unit missing required provenance: {', '.join(missing)}")
            try:
                validate_source_hashes(u["source_hashes"])
                if _hash(u["artifact_manifest"]) != str(u["artifact_hash"]).lower():
                    raise ValueError("artifact_hash does not match canonical artifact manifest")
            except (TypeError, ValueError) as exc:
                raise AcquisitionGateError(str(exc)) from exc
    n = sum(u.get("status") == "PASS" and u.get("pre_window_provenance") == _PREWINDOW for u in rows)
    coverage = n / intended_units if intended_units else 1.0
    gate = len(rows) == intended_units and coverage == 1.0
    day_groups = defaultdict(list)
    for unit in rows:
        day_groups[str(unit.get("calendar_day", ""))].append(unit)
    day_gate = all(all(item.get("status") == "PASS" and item.get("pre_window_provenance") == _PREWINDOW for item in group) for group in day_groups.values())
    gate = gate and day_gate
    doc = {"provenance_census": True, "units": rows, "unit_count": len(rows), "intended_units": intended_units, "pre_window_n": n, "pre_window_N": intended_units, "pre_window_coverage": coverage, "gate_pass": gate, "causal_status": "CAUSAL_ELIGIBLE" if gate else "CAUSAL_BLOCKED", "fail_loud": fail_loud, "generated_at": generated_at or dt.datetime.now(dt.UTC).isoformat(), "no_imputation": True, "raw_payload_hash_census": all(bool(u.get("raw_payload_hash")) for u in rows), "statuses": {s: sum(u.get("status") == s for u in rows) for s in ("PASS", "INELIGIBLE", "HARD_GAP", "ASSOCIATIONAL")}, "pass_n": sum(u.get("status") == "PASS" for u in rows), "ineligible_n": sum(u.get("status") == "INELIGIBLE" for u in rows), "hard_gap_n": sum(u.get("status") == "HARD_GAP" for u in rows), "associational_n": sum(u.get("status") == "ASSOCIATIONAL" for u in rows), "associational_exclusions": [u for u in rows if u.get("status") == "ASSOCIATIONAL"], "ineligible_exclusions": [u for u in rows if u.get("status") == "INELIGIBLE"], "same_day_gate": day_gate, "gate_reason": "100% PRE_WINDOW coverage" if gate else f"below strict PRE_WINDOW coverage ({n}/{intended_units}) or mixed same-day provenance"}
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
    return {k: unit[k] for k in ("calendar_day", "ticker", "expiry", "dte", "habitat", "sector", "candidate_source")}


def run_availability_probes(schedule: Iterable[Mapping[str, Any]], *, probe_fetcher: Callable[[Mapping[str, Any]], Any] | None = None, approval: bool = False, dry_run: bool = True, probe_only: bool = False, code_version: str = "dealer-exposure-probe-v1", code_hash: str | None = None) -> list[dict[str, Any]]:
    """Run sequential lightweight probes; never dispatches the heavy fetcher."""
    ordered = sorted((dict(u) for u in schedule), key=lambda x: x["candidate_key"])
    if dry_run or probe_only or not approval or probe_fetcher is None:
        return [{"candidate_key": u["candidate_key"], "status": "HARD_GAP" if probe_fetcher is None and approval and not (dry_run or probe_only) else "INELIGIBLE", "reason": "probe_not_run", "request_parameters": _probe_request(u), "response_status": None, "response_counts": {}, "source_counts": {}, "probe_code_version": code_version, "probe_code_hash": code_hash or _hash(code_version), "validated": False, "invoked": False} for u in ordered]
    results = []
    for unit in ordered:
        request = _probe_request(unit)
        try:
            response = probe_fetcher(request)
            if not isinstance(response, Mapping):
                raise TypeError("probe response must be a mapping")
            status = str(response.get("status", "HARD_GAP")).upper()
            if status not in {"PASS", "INELIGIBLE", "HARD_GAP"}:
                status = "HARD_GAP"
            response_status = response.get("response_status", response.get("status_code"))
            response_counts = dict(response.get("response_counts", response.get("counts", {})) or {})
            source_counts = dict(response.get("source_counts", {}) or {})
            validated = status == "PASS" and response_status is not None and bool(response_counts) and bool(source_counts)
            results.append({"candidate_key": unit["candidate_key"], "status": status, "reason": response.get("reason"), "request_parameters": request, "response_status": response_status, "response_counts": response_counts, "source_counts": source_counts, "probe_code_version": code_version, "probe_code_hash": code_hash or _hash(code_version), "validated": validated, "invoked": True})
        except Exception as exc:  # noqa: BLE001 - adapter failures are auditable HARD_GAPs
            results.append({"candidate_key": unit["candidate_key"], "status": "HARD_GAP", "reason": str(exc)[:200], "request_parameters": request, "response_status": None, "response_counts": {}, "source_counts": {}, "probe_code_version": code_version, "probe_code_hash": code_hash or _hash(code_version), "validated": False, "invoked": True})
    return results


def select_primary_schedule(schedule: Iterable[Mapping[str, Any]], probes: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Admit candidates only when their corresponding validated probe is PASS."""
    by_key = {p.get("candidate_key"): p for p in probes if p.get("status") == "PASS" and p.get("validated") is True and p.get("invoked") is True}
    return [dict(u) for u in sorted(schedule, key=lambda x: x["candidate_key"]) if u.get("candidate_key") in by_key and not u.get("held_pair_exclusion")]


def execute_sequential_acquisition(schedule: Iterable[Mapping[str, Any]], *, fetcher: Callable[[Mapping[str, Any]], Any] | None = None, approval: bool = False, dry_run: bool = True, probe_only: bool = False, fail_loud: bool = False, output_dir: str | Path | None = None, probe_fetcher: Callable[[Mapping[str, Any]], Any] | None = None, probe_code_version: str = "dealer-exposure-probe-v1", probe_code_hash: str | None = None, generated_at: str | None = None) -> dict[str, Any]:
    if os.environ.get("THETADATA_HIST_CONCURRENCY", "1") != "1": raise AcquisitionGateError("THETADATA_HIST_CONCURRENCY=1 is required")
    if not dry_run and not approval: raise AcquisitionGateError("explicit approval is required")
    ordered = sorted((dict(u) for u in schedule), key=lambda x: x["candidate_key"])
    probes = run_availability_probes(ordered, probe_fetcher=probe_fetcher, approval=approval, dry_run=dry_run, probe_only=probe_only, code_version=probe_code_version, code_hash=probe_code_hash)
    primary_schedule = select_primary_schedule(ordered, probes)
    primary_keys = {u["candidate_key"] for u in primary_schedule}
    probe_by_key = {p["candidate_key"]: p for p in probes}
    units = []
    network_executed = False
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
                network_executed = True
                payload = fetcher(unit)
                item = _unit_from_payload(unit, payload)
                status, reason = item["status"], item["reason"]
            except Exception as exc:  # noqa: BLE001 - adapter failures are auditable HARD_GAPs
                payload, status, reason = {"error": str(exc)}, "HARD_GAP", str(exc)[:200]
                item = _unit_from_payload(unit, payload)
        item.update({"status": status, "reason": reason, "raw_payload_hash": _hash(payload)})
        manifest = {"candidate_key": unit["candidate_key"], "raw_payload_hash": item["raw_payload_hash"], "status": status, "imputed": False}
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
                   "aggregation_rule": "preserve_ticker_values_v1"}
        for item in group:
            item["same_day_cluster"] = cluster
            if item.get("status") == "PASS":
                manifest = {key: item[key] for key in (
                    "candidate_key", "status", "raw_payload_hash", "endpoint", "request_parameters",
                    "declared_timezone", "spot_timestamp", "chain_timestamp", "iv_source_ts",
                    "breach_window_start_prov", "source_hashes", "same_day_cluster", "iv_before_ts",
                    "iv_before_value", "iv_source_value", "delta_iv_aggregation",
                    "delta_iv_aggregation_version")}
                item["artifact_manifest"] = manifest
                item["artifact_basis"] = canonical_json_bytes(manifest).decode("utf-8")
                item["artifact_hash"] = _hash(manifest)
    generated_at = generated_at or dt.datetime.now(dt.UTC).isoformat()
    census = build_provenance_census(units, intended_units=len(ordered), fail_loud=fail_loud, generated_at=generated_at)
    result = {"mode": "probe-only" if (dry_run or probe_only) else "acquisition", "approval_required": True, "approval_granted": approval, "network_heavy_acquisition_executed": network_executed, "no_imputation": True, "schedule": ordered, "primary_schedule": primary_schedule, "probes": probes, "units": units, "census": census, "same_day_clusters": cluster_same_day(units), "comparison_status": "COMPARISON_VALID" if census["gate_pass"] else "COMPARISON_INVALID", "causal_status": census["causal_status"], "generated_at": generated_at}
    if output_dir is not None:
        artifact_dir = Path(output_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / "dealer_exposure_acquisition.json"
        result["artifact_path"] = str(artifact_path)
        artifact_path.write_text(json.dumps(result, indent=2, sort_keys=True, default=str, allow_nan=False) + "\n", encoding="utf-8")
    return result


__all__ = ["NETWORK_ACQUISITION_EXECUTED", "AcquisitionGateError", "build_candidate_schedule", "build_provenance_census", "cluster_same_day", "execute_sequential_acquisition", "run_availability_probes", "select_primary_schedule"]
