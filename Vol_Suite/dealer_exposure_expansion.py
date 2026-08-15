"""Deterministic Task 5 expansion manifest and approval boundary.

The default path is entirely network-free.  It composes Task 1 held-pair and
DTE contracts with Task 2's sequential/approval requirements, and only an
explicit ``approve_network=True`` (the CLI ``--approve-network`` flag) can
invoke a caller-supplied executor.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from .dealer_exposure_acquisition import build_candidate_schedule
from .dealer_exposure_universe import DTE_STRATA, held_pairs_from_paths


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
) -> dict[str, Any]:
    if not dry_run and not approve_network:
        raise ExpansionApprovalError("explicit --approve-network approval is required")
    if not dry_run and executor is None:
        raise ExpansionApprovalError("an injected executor is required after --approve-network")
    result = build_expansion_manifest(candidates, held_pairs=held_pairs, held_paths=held_paths, output_root=output_root)
    if not dry_run:
        result["mode"] = "approved-execution"
        result["network_fetch_allowed"] = True
        for unit in result["units"]:
            executor(unit)  # type: ignore[misc]
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


__all__ = ["ExpansionApprovalError", "build_expansion_manifest", "main", "run_expansion_plan"]

if __name__ == "__main__":
    raise SystemExit(main())
