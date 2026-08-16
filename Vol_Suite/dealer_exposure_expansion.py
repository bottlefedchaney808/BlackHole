"""Deterministic Task 5 expansion manifest and approval boundary.

The default path is entirely network-free.  It composes Task 1 held-pair and
DTE contracts with Task 2's sequential/approval requirements, and only an
explicit ``approve_network=True`` (the CLI ``--approve-network`` flag) can
invoke a caller-supplied executor.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

EXPECTED_DEADBAND = 0.01
LIVE_CONFIG = {
    "sign_model": "vol_surface_replication",
    "accumulate": True,
    "route": "SVI",
    "deadband": EXPECTED_DEADBAND,
    "dealer_vanna_flow": 1,
}

from .dealer_exposure_acquisition import (
    admit_acquisition,
    build_candidate_schedule,
    select_primary_schedule,
)
from .dealer_exposure_universe import (
    DTE_STRATA,
    ProbeResult,
    held_pairs_from_paths,
    validate_probe_result,
)
from .run_live_vs_expiry_book_common_input import (
    CanonicalInput,
    ComparisonInvalid,
    compare_common_input,
    validate_causal_eligibility,
)


class ExpansionApprovalError(RuntimeError):
    """Raised when a network execution boundary is not explicitly satisfied."""


def _stratum(dte: int) -> str:
    for lo, hi in DTE_STRATA:
        if lo <= dte <= hi:
            return f"{lo}-{hi}"
    raise ValueError("DTE outside locked strata")


def _paths(root: str | Path, ticker: str, day: str) -> tuple[str, str]:
    root = str(root).replace("\\", "/").rstrip("/")
    return f"{root}/raw/{ticker}/{day}.json", f"{root}/records/{ticker}/{day}.json"


def _exclusion(raw: Mapping[str, Any], reason: str, *, held_reference: str | None = None) -> dict[str, Any]:
    ticker = str(raw.get("ticker", "")).strip().lstrip("$").upper()
    day = raw.get("calendar_day", raw.get("day", raw.get("date")))
    return {"ticker": ticker, "calendar_day": str(day), "reason": reason, "held_reference": held_reference}


def build_expansion_manifest(
    candidates: Iterable[Mapping[str, Any]],
    *,
    held_pairs: Iterable[tuple[str, str]] = (),
    held_paths: Iterable[str | Path] = (),
    output_root: str | Path = "Vol_Suite/_causal_acquisition_20260815",
    calendar_snapshot: Any | None = None,
    as_of: str | None = None,
    window_policy: str = "OPEX_DAY",
) -> dict[str, Any]:
    """Build a stable, serializable acquisition plan without endpoint calls."""
    if os.environ.get("THETADATA_HIST_CONCURRENCY", "1") != "1":
        raise ExpansionApprovalError("THETADATA_HIST_CONCURRENCY=1 is required")
    raw_rows = [dict(row) for row in candidates]
    held = {(str(t).strip().lstrip("$").upper(), str(d)) for t, d in held_pairs}
    held |= held_pairs_from_paths(held_paths)
    if raw_rows and calendar_snapshot is None and any((str(row.get("ticker", "")).strip().lstrip("$").upper(), str(row.get("calendar_day", row.get("day", row.get("date"))))) not in held for row in raw_rows):
        raise ExpansionApprovalError("calendar snapshot is required for acquisition-eligible manifest")
    held_refs = {pair: "held-reference" for pair in held}
    for path in held_paths:
        path_name = Path(path).name
        # The Task 1 extractor is authoritative; the name is only provenance.
        for pair in held:
            held_refs[pair] = path_name

    valid: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for raw in raw_rows:
        try:
            schedule = build_candidate_schedule([raw], held_pairs=held, calendar_snapshot=calendar_snapshot, as_of=as_of, window_policy=window_policy)
        except (TypeError, ValueError) as exc:
            exclusions.append(_exclusion(raw, "invalid_dte" if "DTE" in str(exc) or "dte" in str(exc) else f"invalid_candidate:{exc}"))
            continue
        if not schedule:
            ticker = str(raw.get("ticker", "")).strip().lstrip("$").upper()
            day = str(raw.get("calendar_day", raw.get("day", raw.get("date"))))
            exclusions.append(_exclusion(raw, "held_ticker_day", held_reference=held_refs.get((ticker, day))))
            continue
        if schedule[0].get("held_pair_exclusion"):
            ticker = schedule[0]["ticker"]
            day = schedule[0]["calendar_day"]
            exclusions.append(_exclusion(raw, "held_ticker_day", held_reference=held_refs.get((ticker, day))))
            continue
        valid.extend(schedule)

    valid.sort(key=lambda row: row["candidate_key"])
    # Duplicate contract keys are excluded deterministically, not counted twice.
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in valid:
        if row["candidate_key"] in seen:
            exclusions.append(_exclusion(row, "duplicate_candidate_key"))
        else:
            seen.add(row["candidate_key"])
            unique.append(row)

    units: list[dict[str, Any]] = []
    for row in unique:
        raw_path, record_path = _paths(output_root, row["ticker"], row["calendar_day"])
        unit = dict(row)
        unit.update({"raw_artifact_path": raw_path, "record_artifact_path": record_path, "network": False, "imputed": False, "no_imputation": True})
        units.append(unit)
    units.sort(key=lambda row: row["candidate_key"])
    event_n = sum(row["habitat"] in {"FOMC", "EARNINGS", "OPEX", "DESCRIPTIVE-HABITAT"} for row in units)
    control_n = len(units) - event_n
    event_days = {row["calendar_day"] for row in units if row["habitat"] != "NONE"}
    control_days = {row["calendar_day"] for row in units if row["habitat"] == "NONE"}
    balance_gate = event_n > 0 and control_n > 0 and max(event_n, control_n) <= 2 * min(event_n, control_n)
    strata = Counter(_stratum(int(row["dte"])) for row in units)
    quota = {"event_target_fraction": 1 / 3, "control_target_fraction": 2 / 3, "event_habitats": ["FOMC", "EARNINGS", "OPEX"], "dte_strata": [list(s) for s in DTE_STRATA], "balanced_panel_rule": "event/control arm ratio <= 2:1; preserve ticker-specific rows"}
    stops = ["stop if any required chain/spot/IV/OI probe is not PASS", "stop if PRE_WINDOW lacks two timestamped observations", "stop if any imputation or zero-DTE representation is observed", "stop if event/control arms are absent or exceed 2:1 imbalance", "stop if any held ticker×day reappears"]
    if not balance_gate:
        stops.append("stop: event/control quota is not balanced")
    sources = sorted({row["candidate_source"] for row in units})
    registry_path = f"{str(output_root).replace(chr(92), '/').rstrip('/')}/registry.json"
    return {"schema_version": 1, "mode": "dry-run", "network_fetch_allowed": False, "approval_required": True, "approval_command": "python -m Vol_Suite.dealer_exposure_expansion --approve-network", "selection_provenance": {"candidate_source": sources[0] if len(sources) == 1 else sources, "candidate_count": len(raw_rows)}, "planned_candidates": [row["candidate_key"] for row in units], "units": units, "exclusions": sorted(exclusions, key=lambda row: (row["calendar_day"], row["ticker"], row["reason"])), "expected_ticker_day_units": len(units), "intended_unique_day_denominator": len({row["calendar_day"] for row in units}), "counts": {"event": event_n, "control": control_n, "event_unique_days": len(event_days), "control_unique_days": len(control_days), "dte_strata": dict(sorted(strata.items()))}, "quota": quota, "balance_gate": balance_gate, "registry_path": registry_path, "gates": {"THETADATA_HIST_CONCURRENCY": 1, "pre_window_observations_required": 2, "imputation_policy": "reject_missing", "no_imputation": True, "balanced_panel": True, "approval_required": True}, "stop_conditions": stops, "no_imputation": True}


def _field(obj: Any, *names: str) -> Any:
    """Read an attested field without inventing a default."""
    for name in names:
        value = obj.get(name) if isinstance(obj, Mapping) else getattr(obj, name, None)
        if value is not None:
            return value
    return None


def _require_result_identity(result: Any, inp: CanonicalInput, engine: str) -> list[Any]:
    """Require container *and every row* to identify the canonical snapshot.

    The common harness checks expiry/T again for its numerical path, but Task 5
    must reject an apparently complete result before any pair is assembled.
    In particular, a row may not inherit spot, IV, OI, or provenance from the
    canonical input merely because its strike/right key happens to match.
    """
    def number(value: Any, label: str) -> float:
        if isinstance(value, bool):
            raise ComparisonInvalid(f"{engine} {label} identity is missing or malformed", structured_invalid=True)
        try:
            value = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ComparisonInvalid(f"{engine} {label} identity is missing or malformed", structured_invalid=True) from exc
        if not math.isfinite(value):
            raise ComparisonInvalid(f"{engine} {label} identity is missing or malformed", structured_invalid=True)
        return value

    spot = _field(result, "spot", "result_spot")
    expiry = _field(result, "expiry", "selected_expiry", "result_expiry")
    dte = _field(result, "dte", "DTE", "result_dte")
    T = _field(result, "T", "t", "result_T")
    if spot is None or number(spot, "spot") != inp.spot:
        raise ComparisonInvalid(f"{engine} result spot identity is missing or mismatched", structured_invalid=True)
    if expiry != inp.expiry:
        raise ComparisonInvalid(f"{engine} selected expiry identity is missing or mismatched", structured_invalid=True)
    if dte is None or number(dte, "DTE") != inp.dte:
        raise ComparisonInvalid(f"{engine} DTE identity is missing or mismatched", structured_invalid=True)
    if T is None or abs(number(T, "T") - inp.dte / 365.0) > 1e-12:
        raise ComparisonInvalid(f"{engine} T identity is missing or mismatched", structured_invalid=True)
    rows = _field(result, "gamma_records" if engine == "live" else "rows")
    if not isinstance(rows, (list, tuple)) or len(rows) != len(inp.rows):
        raise ComparisonInvalid(f"{engine} result record count is not exactly canonical", structured_invalid=True)
    expected = {(r.strike, r.right): r for r in inp.rows}
    actual: set[tuple[float, str]] = set()
    for row in rows:
        row_expiry = _field(row, "expiry", "selected_expiry", "result_expiry")
        row_spot = _field(row, "spot", "result_spot")
        row_dte = _field(row, "dte", "DTE", "result_dte")
        row_t = _field(row, "T", "t", "result_T")
        strike = _field(row, "strike")
        right = _field(row, "right")
        if row_expiry != inp.expiry or row_spot is None or number(row_spot, "per-strike spot") != inp.spot:
            raise ComparisonInvalid(f"{engine} per-strike expiry/spot identity is missing or mismatched", structured_invalid=True)
        if row_dte is None or number(row_dte, "per-strike DTE") != inp.dte or row_t is None or abs(number(row_t, "per-strike T") - inp.dte / 365.0) > 1e-12:
            raise ComparisonInvalid(f"{engine} per-strike DTE/T identity is missing or mismatched", structured_invalid=True)
        if right not in {"C", "P"} or strike is None:
            raise ComparisonInvalid(f"{engine} per-strike strike/right identity is missing or malformed", structured_invalid=True)
        key = (number(strike, "per-strike strike"), right)
        if key not in expected or key in actual:
            raise ComparisonInvalid(f"{engine} result strike/right coverage is not exact", structured_invalid=True)
        canonical = expected[key]
        iv = _field(row, "iv", "implied_vol")
        oi = _field(row, "oi", "open_interest")
        if iv is None or number(iv, "per-strike IV") != canonical.iv or oi is None or number(oi, "per-strike OI") != canonical.oi:
            raise ComparisonInvalid(f"{engine} per-strike IV/OI identity is missing or mismatched", structured_invalid=True)
        source_hash = _field(row, "source_hash", "source_sha256", "per_strike_source_hash")
        config_hash = _field(row, "config_hash", "per_strike_config_hash", "config_identity")
        if not isinstance(source_hash, str) or source_hash not in inp.source_hashes or not isinstance(config_hash, str) or not config_hash.strip():
            raise ComparisonInvalid(f"{engine} per-strike source/config identity is missing or mismatched", structured_invalid=True)
        expected_config_hash = getattr(inp, f"{engine}_config_hash")
        if config_hash != expected_config_hash:
            raise ComparisonInvalid(f"{engine} per-strike config identity is missing or mismatched", structured_invalid=True)
        actual.add(key)
    return list(rows)


def _require_live_attestation(result: Any) -> Mapping[str, Any]:
    attestation = _field(result, "config_attestation", "adapter_attestation", "live_attestation")
    if not isinstance(attestation, Mapping):
        raise ComparisonInvalid("live adapter/result configuration attestation is unavailable", structured_invalid=True)
    aliases = {"sign_model": ("sign_model",), "accumulate": ("accumulate",),
               "route": ("route", "svi_route"), "deadband": ("deadband", "iv_deadband"),
               "dealer_vanna_flow": ("dealer_vanna_flow",)}
    for expected, names in aliases.items():
        actual = next((attestation.get(name) for name in names if name in attestation), None)
        if actual != LIVE_CONFIG[expected]:
            raise ComparisonInvalid(f"live attestation does not verify {expected}={LIVE_CONFIG[expected]!r}", structured_invalid=True)
    if attestation.get("attested") is not True:
        raise ComparisonInvalid("live configuration attestation is not explicitly validated", structured_invalid=True)
    return attestation


def _require_strike_provenance(row: Any, engine: str) -> None:
    source_hash = _field(row, "source_hash", "source_sha256", "per_strike_source_hash")
    config_hash = _field(row, "config_hash", "per_strike_config_hash")
    provenance = _field(row, "sign_provenance", "resolved_sign_provenance", "sign_source")
    if not isinstance(source_hash, str) or not source_hash.strip() or not isinstance(config_hash, str) or not config_hash.strip() or not isinstance(provenance, str) or not provenance.strip():
        raise ComparisonInvalid(f"{engine} per-strike source/config/sign provenance is missing", structured_invalid=True)


def compare_expansion_common_input(
    canonical_input: CanonicalInput, *, live_runner: Callable[[Any], Any],
    new_runner: Callable[[Any], Any], deadband: float = EXPECTED_DEADBAND,
    provenance_units: Iterable[Mapping[str, Any]] | None = None,
    artifact_registry: Mapping[str, Any] | None = None,
    intended_units: int | None = None,
    intended_corpus_manifest: Any = None,
) -> dict[str, Any]:
    """Compose Task 3 with explicit live configuration and fail-closed identity."""
    if isinstance(deadband, bool) or deadband != EXPECTED_DEADBAND:
        raise ComparisonInvalid("deadband must be exactly 0.01", structured_invalid=True)
    captured: dict[str, Any] = {}

    def capture(name: str, runner: Callable[[Any], Any]) -> Callable[[Any], Any]:
        def invoke(payload: Any) -> Any:
            try:
                if name == "live":
                    result = runner(payload, **LIVE_CONFIG)
                else:
                    result = runner(payload)
            except TypeError as exc:
                raise ComparisonInvalid("live adapter invocation cannot attest explicit configuration", structured_invalid=True) from exc
            captured[name] = result
            rows = _require_result_identity(result, canonical_input, name)
            for row in rows:
                _require_strike_provenance(row, name)
            if name == "live":
                _require_live_attestation(result)
            return result
        return invoke

    comparison = compare_common_input(
        canonical_input, capture("live", live_runner), capture("new", new_runner),
        deadband=EXPECTED_DEADBAND, provenance_units=provenance_units,
        artifact_registry=artifact_registry, intended_units=intended_units,
        intended_corpus_manifest=intended_corpus_manifest,
    )
    live, new = captured["live"], captured["new"]
    live_rows, new_rows = _require_result_identity(live, canonical_input, "live"), _require_result_identity(new, canonical_input, "new")
    live_by_key = {(_field(r, "strike"), _field(r, "right")): r for r in live_rows}
    new_by_key = {(_field(r, "strike"), _field(r, "right")): r for r in new_rows}
    for pair in comparison["pairs"]:
        key = (pair["strike"], pair["right"])
        lr, nr = live_by_key[key], new_by_key[key]
        pair.pop("live_sign_source", None)
        pair.pop("new_sign_source", None)
        pair.update({"oi": _field(lr, "oi", "open_interest"), "iv": _field(lr, "iv", "implied_vol"),
                     "new_oi": _field(nr, "oi", "open_interest"), "new_iv": _field(nr, "iv", "implied_vol"),
                     "spot": _field(lr, "spot", "result_spot"), "T": _field(lr, "T", "t", "result_T"),
                     "dte": _field(lr, "dte", "DTE", "result_dte"),
                     "live_spot": _field(lr, "spot", "result_spot"), "new_spot": _field(nr, "spot", "result_spot"),
                     "live_T": _field(lr, "T", "t", "result_T"), "new_T": _field(nr, "T", "t", "result_T"),
                     "live_dte": _field(lr, "dte", "DTE", "result_dte"), "new_dte": _field(nr, "dte", "DTE", "result_dte"),
                     "live_source_hash": _field(lr, "source_hash", "source_sha256", "per_strike_source_hash"),
                     "new_source_hash": _field(nr, "source_hash", "source_sha256", "per_strike_source_hash"),
                     "live_config_hash": _field(lr, "config_hash", "per_strike_config_hash"),
                     "new_config_hash": _field(nr, "config_hash", "per_strike_config_hash"),
                     "live_sign_provenance": _field(lr, "sign_provenance", "resolved_sign_provenance", "sign_source"),
                     "new_sign_provenance": _field(nr, "sign_provenance", "resolved_sign_provenance", "sign_source")})
    attestation = _require_live_attestation(live)
    comparison["config"].update(dict(attestation))
    comparison["headline_eligible"] = comparison["coverage"]["common"] == comparison["coverage"]["total"]
    return comparison


_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


def _validate_pre_window_observations(unit: Mapping[str, Any]) -> list[str]:
    observations = unit.get("pre_window_observations")
    if not isinstance(observations, list) or len(observations) < 2:
        return ["two PRE_WINDOW observations are required"]
    timezone = unit.get("declared_timezone")
    try:
        zone = ZoneInfo(timezone) if isinstance(timezone, str) and timezone else None
        if zone is None:
            raise ValueError("declared_timezone is required")
        cutoff_value = unit.get("breach_window_start_prov", unit.get("cutoff_timestamp"))
        if not isinstance(cutoff_value, str):
            raise TypeError("PRE_WINDOW breach/cutoff timestamp is required")
        cutoff_text = cutoff_value[:-1] + "+00:00" if cutoff_value.endswith(("Z", "z")) else cutoff_value
        cutoff = dt.datetime.fromisoformat(cutoff_text)
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("PRE_WINDOW cutoff must be timezone-qualified")
        cutoff = cutoff.astimezone(dt.UTC)
        parsed: list[tuple[dt.datetime, float]] = []
        errors: list[str] = []
        for index, observation in enumerate(observations):
            if not isinstance(observation, Mapping):
                errors.append(f"observation {index} must be a mapping")
                continue
            if observation.get("role") != "PRE_WINDOW":
                errors.append(f"observation {index} must declare role PRE_WINDOW")
            timestamp_value = observation.get("timestamp", observation.get("ts"))
            value = observation.get("iv", observation.get("iv_value", observation.get("value")))
            source_hash = observation.get("source_hash", observation.get("source_sha256"))
            source_identity = observation.get("source_identity", observation.get("source"))
            try:
                text = timestamp_value[:-1] + "+00:00" if isinstance(timestamp_value, str) and timestamp_value.endswith(("Z", "z")) else timestamp_value
                timestamp = dt.datetime.fromisoformat(text)
                if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                    raise ValueError("timestamp must be timezone-qualified")
                timestamp = timestamp.astimezone(dt.UTC)
                number = float(value)
                if isinstance(value, bool) or not math.isfinite(number):
                    raise ValueError("IV value must be finite")
                if not isinstance(source_identity, str) or not source_identity.strip():
                    raise ValueError("source identity is required")
                if not isinstance(source_hash, str) or not _SHA256.fullmatch(source_hash):
                    raise ValueError("source hash must be a SHA-256 identity")
                if timestamp >= cutoff:
                    raise ValueError("observation timestamp must strictly precede breach/cutoff")
                if timestamp.astimezone(zone).date().isoformat() != str(unit.get("calendar_day")):
                    raise ValueError("observation timestamp is wrong-day in declared timezone")
                parsed.append((timestamp, number))
            except (TypeError, ValueError, OverflowError) as exc:
                errors.append(f"observation {index}: {exc}")
        if errors:
            return errors
        if len({timestamp for timestamp, _ in parsed}) != len(parsed):
            return ["PRE_WINDOW observations must have distinct timestamps"]
        if len(parsed) < 2:
            return ["two PRE_WINDOW observations are required"]
        if parsed != sorted(parsed, key=lambda item: item[0]):
            return ["PRE_WINDOW observations must be ordered"]
        aggregation = unit.get("delta_iv_aggregation")
        version = unit.get("delta_iv_aggregation_version")
        delta = unit.get("delta_iv_pre_window")
        if delta is not None or aggregation is not None or version is not None:
            if aggregation != "iv_source_minus_iv_before" or str(version) != "1":
                return ["unsupported PRE_WINDOW IV aggregation"]
            if delta is None or not math.isfinite(float(delta)):
                return ["delta_iv_pre_window must be finite"]
            expected = parsed[-1][1] - parsed[0][1]
            if not math.isclose(float(delta), expected, rel_tol=1e-12, abs_tol=1e-12):
                return ["delta_iv_pre_window does not bind ordered observations"]
    except (ZoneInfoNotFoundError, KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
        return [str(exc)]
    return []


def _validate_probe_contract(probe: Mapping[str, Any], schedule_by_key: Mapping[str, Mapping[str, Any]]) -> tuple[str | None, str | None]:
    """Validate Task 1 evidence and bind its request to one exact schedule key."""
    key = probe.get("candidate_key")
    if not isinstance(key, str) or key not in schedule_by_key:
        return None, "probe request is detached from the exact candidate schedule key"
    schedule = schedule_by_key[key]
    request = probe.get("request_parameters")
    if not isinstance(request, Mapping):
        return key, "probe request_parameters are required"
    expected = {"ticker": schedule.get("ticker"), "day": schedule.get("calendar_day"),
                "expiry": schedule.get("expiry"), "dte": schedule.get("dte")}
    actual = {name: request.get(name) for name in expected}
    if actual != expected:
        return key, "probe request identity does not match candidate schedule"
    try:
        result = ProbeResult(
            ticker=str(probe.get("ticker", schedule.get("ticker"))),
            day=str(probe.get("day", schedule.get("calendar_day"))),
            expiry=str(probe.get("expiry", schedule.get("expiry"))),
            dte=int(probe.get("dte", schedule.get("dte"))),
            status=str(probe.get("status", "HARD_GAP")),
            checks=probe.get("checks", {}), reasons=probe.get("reasons", ()),
            imputed_zero=probe.get("imputed_zero", False), evidence=probe.get("evidence", {}),
        )
        validate_probe_result(result)
    except (TypeError, ValueError, OverflowError) as exc:
        return key, f"probe contract invalid: {exc}"
    if result.status == "PASS":
        schedule_binding = schedule.get("calendar_binding")
        probe_binding = result.evidence.get("calendar_binding")
        if not isinstance(schedule_binding, Mapping) or not isinstance(probe_binding, Mapping):
            return key, "PASS probe and schedule require calendar binding"
        if dict(probe_binding) != dict(schedule_binding):
            return key, "PASS probe calendar binding does not exactly match candidate schedule"
        if not (probe.get("validated") is True and probe.get("invoked") is True):
            return key, "PASS probe must be explicitly validated and invoked"
    return key, None


def _execution_gate(manifest: Mapping[str, Any], evidence: Mapping[str, Any] | None, *, calendar_snapshot: Any | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return admitted units and auditable reasons; approval alone never admits."""
    reasons: list[dict[str, Any]] = []
    schedule = [dict(unit) for unit in manifest.get("units", ())]
    if not manifest.get("balance_gate"):
        reasons.append({"reason": "event/control balance gate failed"})
    if not schedule:
        reasons.append({"reason": "empty primary schedule"})
    if not isinstance(evidence, Mapping):
        reasons.append({"reason": "canonical acquisition evidence is required"})
        return [], reasons
    probes = evidence.get("probes")
    if not isinstance(probes, list):
        reasons.append({"reason": "validated probes are required"})
        probes = []
    schedule_by_key = {unit.get("candidate_key"): unit for unit in schedule}
    valid_probes: list[dict[str, Any]] = []
    probe_keys: set[str] = set()
    for probe in probes:
        if not isinstance(probe, Mapping):
            reasons.append({"classification": "HARD_GAP", "status": "COMPARISON_INVALID", "reason": "probe must be a mapping"})
            continue
        key, error = _validate_probe_contract(probe, schedule_by_key)
        if error:
            reasons.append({"candidate_key": key, "classification": "HARD_GAP", "status": "COMPARISON_INVALID", "reason": error})
            continue
        if key in probe_keys:
            reasons.append({"candidate_key": key, "classification": "HARD_GAP", "status": "COMPARISON_INVALID", "reason": "duplicate probe identity"})
            continue
        probe_keys.add(key)
        valid_probes.append(dict(probe))
    primary = select_primary_schedule(schedule, valid_probes, calendar_snapshot=calendar_snapshot)
    if {u["candidate_key"] for u in primary} != {u["candidate_key"] for u in schedule}:
        reasons.append({"reason": "every schedule unit requires a validated PASS probe"})
    units = evidence.get("units")
    registry = evidence.get("artifact_registry")
    if not isinstance(units, list) or not isinstance(registry, Mapping):
        reasons.append({"reason": "complete canonical evidence and verified registry are required"})
        return [], reasons
    # Validate identities in one pass before indexing.  Candidate identities are
    # canonical strings produced by the manifest; rejecting anything else keeps
    # malformed/unhashable caller data at this safety boundary as audit evidence
    # instead of allowing TypeError or silent dict overwrites.
    by_identity: dict[str, dict[str, Any]] = {}
    identity_counts: Counter[str] = Counter()
    for unit in units:
        if not isinstance(unit, Mapping):
            reasons.append({"reason": "evidence unit must be a mapping"})
            return [], reasons
        key = unit.get("candidate_key")
        if not isinstance(key, str) or not key:
            reasons.append({
                "candidate_key": repr(key)[:200],
                "reason": "malformed evidence candidate identity",
            })
            return [], reasons
        identity_counts[key] += 1
        by_identity.setdefault(key, dict(unit))
    duplicate_keys = [key for key, count in identity_counts.items() if count > 1]
    if duplicate_keys:
        reasons.extend({"candidate_key": key, "reason": "duplicate evidence candidate identity"}
                       for key in sorted(duplicate_keys))
        return [], reasons
    schedule_keys = {u["candidate_key"] for u in primary}
    if set(by_identity) != schedule_keys:
        reasons.append({"reason": "evidence coverage is not 100% of primary schedule"})
    for key, unit in by_identity.items():
        for observation_reason in _validate_pre_window_observations(unit):
            # Provenance-shape failures are comparison-invalid hard gaps, not
            # ordinary executor or Python exceptions.  Keep the reason
            # auditable while ensuring malformed evidence can never admit work.
            reasons.append({"candidate_key": key, "classification": "HARD_GAP",
                            "status": "COMPARISON_INVALID", "reason": observation_reason})
    causal = validate_causal_eligibility(by_identity.values(), intended_units=len(schedule),
                                         intended_corpus_manifest={"units": schedule},
                                         artifact_registry=registry)
    if causal.get("causal_status") != "CAUSAL_ELIGIBLE":
        reasons.extend(causal.get("reasons", []))
    return ([by_identity[u["candidate_key"]] for u in primary] if not reasons else []), reasons


def run_expansion_plan(
    candidates: Iterable[Mapping[str, Any]],
    *,
    held_pairs: Iterable[tuple[str, str]] = (),
    held_paths: Iterable[str | Path] = (),
    output_root: str | Path = "Vol_Suite/_causal_acquisition_20260815",
    dry_run: bool = True,
    approve_network: bool = False,
    executor: Callable[[Mapping[str, Any]], Any] | None = None,
    write_manifest: bool = False,
    acquisition_evidence: Mapping[str, Any] | None = None,
    authorization: Any | None = None,
    registry: Mapping[str, Any] | None = None,
    calendar_snapshot: Any | None = None,
    as_of: str | None = None,
    window_policy: str = "OPEX_DAY",
) -> dict[str, Any]:
    if not dry_run and authorization is None:
        raise ExpansionApprovalError("a validated authorization is required; approve_network is not authorization")
    if not dry_run and executor is None:
        raise ExpansionApprovalError("an injected restricted executor is required after admission")
    result = build_expansion_manifest(candidates, held_pairs=held_pairs, held_paths=held_paths, output_root=output_root, calendar_snapshot=calendar_snapshot, as_of=as_of, window_policy=window_policy)
    if not dry_run:
        result["mode"] = "approved-execution"
        auth_manifest = authorization.candidate_manifest_projection() if hasattr(authorization, "candidate_manifest_projection") else result
        payload = acquisition_evidence if isinstance(acquisition_evidence, Mapping) else {}
        admitted, audit = admit_acquisition(
            authorization, auth_manifest, payload.get("probes", ()), payload,
            registry if registry is not None else payload.get("artifact_registry", {}),
        )
        result["network_fetch_allowed"] = bool(audit.get("admitted"))
        result["execution_audit"] = {"invoked": [], "blocked": list(audit.get("blocked", ())), "admission": audit}
        if not admitted:
            result["mode"] = "blocked"
        else:
            for unit in admitted:
                try:
                    execution = executor(unit)  # type: ignore[misc]
                    status = str(execution.get("status", "")).upper() if isinstance(execution, Mapping) else ""
                    failure_marker = (
                        isinstance(execution, Mapping)
                        and (
                            status in {"FAILED", "FAIL", "ERROR", "BLOCKED", "HARD_GAP", "FAILED_EXECUTION"}
                            or any(execution.get(name) not in (None, False, "")
                                   for name in ("error", "failure", "failure_marker"))
                        )
                    )
                    boolean_fields_valid = (
                        isinstance(execution, Mapping)
                        and all(
                            type(execution[field]) is bool
                            for field in ("validated", "success")
                            if field in execution
                        )
                        and ("ok" not in execution or type(execution["ok"]) is bool)
                    )
                    validated_success = (
                        boolean_fields_valid
                        and not failure_marker
                        and status in {"SUCCESS", "SUCCEEDED", "PASS", "OK"}
                        and execution.get("validated") is True
                        and execution.get("success") is True
                        and ("ok" not in execution or execution.get("ok") is True)
                    )
                    if not validated_success:
                        result["execution_audit"]["blocked"].append({
                            "candidate_key": unit["candidate_key"],
                            "classification": "HARD_GAP",
                            "status": "FAILED_EXECUTION",
                            "reason": str(execution.get("reason", execution))[:200],
                        })
                        result["mode"] = "failed-execution"
                        result["network_fetch_allowed"] = False
                        break
                    result["execution_audit"]["invoked"].append(unit["candidate_key"])
                except Exception as exc:  # noqa: BLE001 - executor failures are audit evidence
                    result["execution_audit"]["blocked"].append({"candidate_key": unit["candidate_key"], "classification": "HARD_GAP", "status": "FAILED_EXECUTION", "reason": str(exc)[:200]})
                    result["mode"] = "failed-execution"
                    result["network_fetch_allowed"] = False
                    break
    if write_manifest:
        path = Path(output_root) / "expansion_manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a network-free dealer exposure expansion plan")
    parser.add_argument("--approve-network", action="store_true", help="explicitly cross the network approval boundary")
    parser.add_argument("--write-manifest", action="store_true")
    args = parser.parse_args(argv)
    # No implicit universe is supplied by this command: callers must provide a
    # reviewed candidate file through the Python API before approval.
    if args.approve_network:
        raise SystemExit("--approve-network requires a caller-supplied reviewed universe and executor")
    print(json.dumps(build_expansion_manifest([]), indent=2, sort_keys=True))
    return 0


__all__ = ["ExpansionApprovalError", "admit_acquisition", "build_expansion_manifest", "compare_expansion_common_input", "main", "run_expansion_plan"]

if __name__ == "__main__":
    raise SystemExit(main())
