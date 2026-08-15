"""Deterministic Task 5 expansion manifest and approval boundary.

The default path is entirely network-free.  It composes Task 1 held-pair and
DTE contracts with Task 2's sequential/approval requirements, and only an
explicit ``approve_network=True`` (the CLI ``--approve-network`` flag) can
invoke a caller-supplied executor.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

EXPECTED_DEADBAND = 0.01
LIVE_CONFIG = {
    "sign_model": "vol_surface_replication",
    "accumulate": True,
    "route": "SVI",
    "deadband": EXPECTED_DEADBAND,
    "dealer_vanna_flow": 1,
}

from .dealer_exposure_acquisition import (
    build_candidate_schedule,
    select_primary_schedule,
)
from .dealer_exposure_universe import DTE_STRATA, held_pairs_from_paths
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
) -> dict[str, Any]:
    """Build a stable, serializable acquisition plan without endpoint calls."""
    if os.environ.get("THETADATA_HIST_CONCURRENCY", "1") != "1":
        raise ExpansionApprovalError("THETADATA_HIST_CONCURRENCY=1 is required")
    raw_rows = [dict(row) for row in candidates]
    held = {(str(t).strip().lstrip("$").upper(), str(d)) for t, d in held_pairs}
    held |= held_pairs_from_paths(held_paths)
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
            schedule = build_candidate_schedule([raw], held_pairs=held)
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
        expected_config_hash = getattr(inp, "config_hash", None)
        if expected_config_hash is not None and config_hash != expected_config_hash:
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


def _execution_gate(manifest: Mapping[str, Any], evidence: Mapping[str, Any] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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
    primary = select_primary_schedule(schedule, probes)
    if {u["candidate_key"] for u in primary} != {u["candidate_key"] for u in schedule}:
        reasons.append({"reason": "every schedule unit requires a validated PASS probe"})
    units = evidence.get("units")
    registry = evidence.get("artifact_registry")
    if not isinstance(units, list) or not isinstance(registry, Mapping):
        reasons.append({"reason": "complete canonical evidence and verified registry are required"})
        return [], reasons
    # Validate the source list before indexing it.  A dict-comprehension here
    # would silently discard all but the last contradictory evidence unit.
    evidence_keys: list[Any] = []
    for unit in units:
        if not isinstance(unit, Mapping):
            reasons.append({"reason": "evidence unit must be a mapping"})
            return [], reasons
        evidence_keys.append(unit.get("candidate_key"))
    duplicate_keys = sorted({key for key in evidence_keys if evidence_keys.count(key) > 1}, key=str)
    if duplicate_keys:
        reasons.extend({"candidate_key": key, "reason": "duplicate evidence candidate identity"} for key in duplicate_keys)
        return [], reasons
    by_key = {u.get("candidate_key"): dict(u) for u in units}
    if set(by_key) != {u["candidate_key"] for u in primary}:
        reasons.append({"reason": "evidence coverage is not 100% of primary schedule"})
    for key, unit in by_key.items():
        observations = unit.get("pre_window_observations")
        if not isinstance(observations, list) or len(observations) < 2:
            reasons.append({"candidate_key": key, "reason": "two PRE_WINDOW observations are required"})
    causal = validate_causal_eligibility(by_key.values(), intended_units=len(schedule),
                                         intended_corpus_manifest={"units": schedule},
                                         artifact_registry=registry)
    if causal.get("causal_status") != "CAUSAL_ELIGIBLE":
        reasons.extend(causal.get("reasons", []))
    return ([by_key[u["candidate_key"]] for u in primary] if not reasons else []), reasons


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
) -> dict[str, Any]:
    if not dry_run and not approve_network:
        raise ExpansionApprovalError("explicit --approve-network approval is required")
    if not dry_run and executor is None:
        raise ExpansionApprovalError("an injected executor is required after --approve-network")
    result = build_expansion_manifest(candidates, held_pairs=held_pairs, held_paths=held_paths, output_root=output_root)
    if not dry_run:
        result["mode"] = "approved-execution"
        admitted, reasons = _execution_gate(result, acquisition_evidence)
        result["network_fetch_allowed"] = bool(admitted)
        result["execution_audit"] = {"invoked": [], "blocked": reasons}
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
                            or execution.get("success") is False
                            or execution.get("ok") is False
                            or any(execution.get(name) not in (None, False, "") for name in ("error", "failure", "failure_marker"))
                        )
                    )
                    validated_success = (
                        isinstance(execution, Mapping)
                        and not failure_marker
                        and status in {"SUCCESS", "SUCCEEDED", "PASS", "OK"}
                        and execution.get("validated") is True
                        and (execution.get("success") is not False)
                        and (execution.get("ok") is not False)
                        and (execution.get("success") is True or execution.get("ok") is True)
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


__all__ = ["ExpansionApprovalError", "build_expansion_manifest", "compare_expansion_common_input", "main", "run_expansion_plan"]

if __name__ == "__main__":
    raise SystemExit(main())
