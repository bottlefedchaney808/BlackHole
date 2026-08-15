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
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from .dealer_exposure_universe import DTE_STRATA, EVENT_HABITATS, held_pairs_from_paths

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
            def visit(value: Any, _objects: list[Mapping[str, Any]] = objects) -> None:
                if isinstance(value, Mapping):
                    _objects.append(value)
                    for child in value.values(): visit(child)
                elif isinstance(value, list):
                    for child in value: visit(child)
            visit(payload)
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
    result = []
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
        result.append({"calendar_day": day, "ticker": ticker, "expiry": expiry, "dte": dte, "habitat": habitat, "sector": sector, "candidate_source": source, "asset_type": str(raw.get("asset_type", "equity")), "dte_stratum": list(next(s for s in DTE_STRATA if s[0] <= dte <= s[1])), "candidate_key": key, "held_pair_exclusion": excluded, "held_pair_exclusion_reason": "held_ticker_day" if excluded else None, "held_day_reference": refs.get(pair)})
    return sorted(result, key=lambda x: tuple(x[k] for k in ("calendar_day", "ticker", "expiry", "dte", "habitat", "sector", "candidate_source")))


def _extract_l2(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    record = payload.get("record") if isinstance(payload.get("record"), Mapping) else payload
    l2 = record.get("l2") if isinstance(record, Mapping) and isinstance(record.get("l2"), Mapping) else record
    return l2 if isinstance(l2, Mapping) else {}


def _unit_from_payload(unit: Mapping[str, Any], payload: Any) -> dict[str, Any]:
    raw_hash = _hash(payload)
    l2 = _extract_l2(payload) if isinstance(payload, Mapping) else {}
    prov = str(l2.get("delta_iv_provenance", "")).upper()
    value = l2.get("delta_iv_pre_window")
    source_ts, breach_ts = l2.get("iv_source_ts"), l2.get("breach_window_start_prov")
    valid = prov == _PREWINDOW and value is not None and not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value)) and source_ts and breach_ts and str(source_ts) < str(breach_ts)
    status = "PASS" if valid else ("ASSOCIATIONAL" if payload is not None else "HARD_GAP")
    artifact = dict(unit); artifact.update({"status": status, "pre_window_provenance": prov or "ASSOCIATIONAL", "pre_window_value": value if valid else None, "imputed": False, "no_imputation": True, "raw_payload_hash": raw_hash})
    artifact["artifact_basis"] = json.dumps({"candidate_key": unit["candidate_key"], "raw_payload": payload, "status": status, "imputed": False}, sort_keys=True, default=str, allow_nan=False)
    artifact["artifact_hash"] = _hash(artifact["artifact_basis"])
    artifact["reason"] = None if status == "PASS" else ("missing_or_associational_prewindow" if status == "ASSOCIATIONAL" else "hard_gap")
    return artifact


def build_provenance_census(units: Iterable[Mapping[str, Any]], *, intended_units: int, fail_loud: bool = False) -> dict[str, Any]:
    if intended_units < 0: raise ValueError("intended_units cannot be negative")
    rows = [dict(u) for u in units]
    rows.sort(key=lambda u: (str(u.get("calendar_day", "")), str(u.get("ticker", "")), str(u.get("candidate_key", ""))))
    for u in rows:
        if u.get("status") not in _STATUS: raise AcquisitionGateError("invalid unit status")
        if not isinstance(u.get("raw_payload_hash"), str) or not u["raw_payload_hash"]: raise AcquisitionGateError("provenance census requires raw payload hashes")
    n = sum(u.get("status") == "PASS" and u.get("pre_window_provenance") == _PREWINDOW for u in rows)
    coverage = n / intended_units if intended_units else 1.0
    gate = len(rows) == intended_units and coverage == 1.0
    doc = {"provenance_census": True, "units": rows, "unit_count": len(rows), "intended_units": intended_units, "pre_window_n": n, "pre_window_N": intended_units, "pre_window_coverage": coverage, "gate_pass": gate, "fail_loud": fail_loud, "generated_at": dt.datetime.now(dt.UTC).isoformat(), "no_imputation": True, "raw_payload_hash_census": all(bool(u.get("raw_payload_hash")) for u in rows), "statuses": {s: sum(u.get("status") == s for u in rows) for s in ("PASS", "INELIGIBLE", "HARD_GAP", "ASSOCIATIONAL")}, "pass_n": sum(u.get("status") == "PASS" for u in rows), "ineligible_n": sum(u.get("status") == "INELIGIBLE" for u in rows), "hard_gap_n": sum(u.get("status") == "HARD_GAP" for u in rows), "associational_n": sum(u.get("status") == "ASSOCIATIONAL" for u in rows), "associational_exclusions": [u for u in rows if u.get("status") == "ASSOCIATIONAL"], "ineligible_exclusions": [u for u in rows if u.get("status") == "INELIGIBLE"], "gate_reason": "100% PRE_WINDOW coverage" if gate else f"below 100% PRE_WINDOW coverage ({n}/{intended_units})"}
    if fail_loud and not gate: raise AcquisitionGateError(f"100% PRE_WINDOW gate failed: {n}/{intended_units}")
    return doc


def cluster_same_day(units: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for u in units: grouped[str(u["calendar_day"])].append(u)
    rank = {"PASS": 0, "INELIGIBLE": 1, "ASSOCIATIONAL": 2, "HARD_GAP": 3}
    out = {}
    for day in sorted(grouped):
        rows = grouped[day]; statuses = [str(u.get("status", "HARD_GAP")) if u.get("status") in _STATUS else "HARD_GAP" for u in rows]
        out[day] = {"calendar_day": day, "tickers": sorted({str(u.get("ticker", "")) for u in rows}), "n_tickers": len({u.get("ticker") for u in rows}), "expiry_set": sorted({str(u.get("expiry")) for u in rows if u.get("expiry") is not None}), "status": max(statuses, key=lambda s: rank[s]), "status_counts": {s: statuses.count(s) for s in ("PASS", "INELIGIBLE", "HARD_GAP", "ASSOCIATIONAL")}}
    return out


def execute_sequential_acquisition(schedule: Iterable[Mapping[str, Any]], *, fetcher: Callable[[Mapping[str, Any]], Any] | None = None, approval: bool = False, dry_run: bool = True, probe_only: bool = False, fail_loud: bool = False, output_dir: str | Path | None = None) -> dict[str, Any]:
    if os.environ.get("THETADATA_HIST_CONCURRENCY", "1") != "1": raise AcquisitionGateError("THETADATA_HIST_CONCURRENCY=1 is required")
    if not dry_run and not approval: raise AcquisitionGateError("explicit approval is required")
    ordered = sorted((dict(u) for u in schedule), key=lambda x: x["candidate_key"])
    units = []
    network_executed = False
    for unit in ordered:
        if unit.get("held_pair_exclusion"):
            payload = {"mode": "held-exclusion", "candidate_key": unit["candidate_key"], "network": False}
            item = _unit_from_payload(unit, None)
            item.update({"status": "INELIGIBLE", "reason": "held_pair_exclusion", "raw_payload_hash": _hash(payload)})
            item["artifact_basis"] = json.dumps({"candidate_key": unit["candidate_key"], "raw_payload": payload, "status": "INELIGIBLE", "imputed": False}, sort_keys=True)
            item["artifact_hash"] = _hash(item["artifact_basis"])
        elif dry_run or probe_only:
            payload = {"mode": "probe-only", "candidate_key": unit["candidate_key"], "network": False}
            item = _unit_from_payload(unit, None)
            item.update({"status": "INELIGIBLE", "reason": "dry_run_probe_only", "raw_payload_hash": _hash(payload)})
            item["artifact_basis"] = json.dumps({"candidate_key": unit["candidate_key"], "raw_payload": payload, "status": "INELIGIBLE", "imputed": False}, sort_keys=True)
            item["artifact_hash"] = _hash(item["artifact_basis"])
        else:
            try:
                payload = fetcher(unit) if fetcher else None
                item = _unit_from_payload(unit, payload)
                network_executed = True
            except Exception as exc:  # noqa: BLE001 - injected network adapter must fail closed
                item = _unit_from_payload(unit, {"error": str(exc)})
                item.update({"status": "HARD_GAP", "reason": str(exc)[:200]})
        units.append(item)
    census = build_provenance_census(units, intended_units=len(ordered), fail_loud=fail_loud)
    return {"mode": "probe-only" if (dry_run or probe_only) else "acquisition", "approval_required": True, "approval_granted": approval, "network_heavy_acquisition_executed": False if (dry_run or probe_only) else network_executed, "no_imputation": True, "schedule": ordered, "units": units, "census": census, "same_day_clusters": cluster_same_day(units), "generated_at": dt.datetime.now(dt.UTC).isoformat()}


__all__ = ["NETWORK_ACQUISITION_EXECUTED", "AcquisitionGateError", "build_candidate_schedule", "build_provenance_census", "cluster_same_day", "execute_sequential_acquisition"]
