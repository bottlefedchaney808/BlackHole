"""Pure, point-in-time OpEx calendar resolver.

This module deliberately accepts only caller-supplied snapshots.  It never
consults a live calendar, wall clock, or listing endpoint.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from types import MappingProxyType
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .provenance_contract import canonical_json_bytes

UTC = ZoneInfo("UTC")
DEFAULT_TZ = "America/New_York"
_HEX64 = set("0123456789abcdefABCDEF")


class CalendarGapError(ValueError):
    """A calendar fact is missing, unavailable, ambiguous, or contradictory."""

    def __init__(self, message: str):
        super().__init__(message if message.startswith(("HARD_GAP", "INELIGIBLE")) else f"HARD_GAP: {message}")


def _reject_nan(value: Any) -> None:
    if isinstance(value, float) and math.isnan(value):
        raise ValueError("NaN is not valid calendar data")
    if isinstance(value, Mapping):
        for item in value.values():
            _reject_nan(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_nan(item)


def _aware(value: str, *, timezone: str, local_day: str | None = None) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp timezone is required")
    if local_day is not None and parsed.astimezone(ZoneInfo(timezone)).date().isoformat() != local_day:
        raise ValueError("timestamp local date does not match session local day")
    return parsed


def _day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid local date: {value!r}") from exc


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _tuple_records(values: Any, name: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"{name} must be a list")
    records = []
    for row in values:
        if not isinstance(row, Mapping):
            raise ValueError(f"{name} entries must be objects")
        _reject_nan(row)
        records.append(MappingProxyType(dict(row)))
    return tuple(records)


@dataclass(frozen=True)
class CalendarSnapshot:
    schema_version: int
    calendar_policy_version: str
    resolver_code_version: str
    knowledge_cutoff: str
    timezone: str
    venue_scope: str
    source_records: tuple[Mapping[str, Any], ...]
    holidays: tuple[Mapping[str, Any], ...]
    sessions: tuple[Mapping[str, Any], ...]
    monthly_rules: tuple[Mapping[str, Any], ...]
    event_records: tuple[Mapping[str, Any], ...]
    snapshot_hash: str


@dataclass(frozen=True)
class OpExRecord:
    product_family: str
    nominal_date: str
    observed_expiry_date: str
    expiry_class: str
    session_id: str
    session_status: str
    regular_open: str | None
    regular_close: str | None
    early_close: bool
    close_reason: str | None
    settlement_style: str
    settlement_timestamp: str | None
    listing_source_ref: str | None
    calendar_hash: str


@dataclass(frozen=True)
class EventWindow:
    event_id: str
    event_type: str
    event_day: str
    window_start: str
    window_end: str
    timezone: str
    anchor: str
    source_ref: str
    surprise_status: str
    causal_surprise_eligible: bool
    calendar_hash: str


def _snapshot_payload(snapshot: CalendarSnapshot) -> dict[str, Any]:
    def rows(items: tuple[Mapping[str, Any], ...]) -> list[dict[str, Any]]:
        return [dict(row) for row in items]
    return {
        "schema_version": snapshot.schema_version,
        "calendar_policy_version": snapshot.calendar_policy_version,
        "resolver_code_version": snapshot.resolver_code_version,
        "knowledge_cutoff": snapshot.knowledge_cutoff,
        "timezone": snapshot.timezone,
        "venue_scope": snapshot.venue_scope,
        "source_records": rows(snapshot.source_records),
        "holidays": rows(snapshot.holidays),
        "sessions": rows(snapshot.sessions),
        "monthly_rules": rows(snapshot.monthly_rules),
        "event_records": rows(snapshot.event_records),
    }


def _canonical_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result.pop("snapshot_hash", None)
    for key in ("source_records", "holidays", "sessions", "monthly_rules", "event_records"):
        result[key] = sorted((dict(row) for row in result.get(key, [])), key=lambda row: canonical_json_bytes(row))
    return result


def load_snapshot(payload: Mapping[str, Any]) -> CalendarSnapshot:
    if not isinstance(payload, Mapping):
        raise ValueError("snapshot must be an object")
    _reject_nan(payload)
    required = ("schema_version", "calendar_policy_version", "resolver_code_version", "knowledge_cutoff", "timezone", "venue_scope")
    missing = [key for key in required if key not in payload]
    if missing:
        raise ValueError(f"missing snapshot fields: {', '.join(missing)}")
    timezone = str(payload["timezone"])
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {timezone}") from exc
    cutoff = _aware(str(payload["knowledge_cutoff"]), timezone=timezone)
    if int(payload["schema_version"]) != 1:
        raise ValueError("unsupported calendar schema_version")
    sources = _tuple_records(payload.get("source_records", []), "source_records")
    for source in sources:
        content = str(source.get("content_sha256", ""))
        if len(content) != 64 or any(ch not in _HEX64 for ch in content):
            raise ValueError("source content_sha256 must be a SHA-256 hash")
        if "available_at" in source and source["available_at"] is not None:
            _aware(str(source["available_at"]), timezone=timezone)
    sessions = _tuple_records(payload.get("sessions", []), "sessions")
    for row in sessions:
        local = str(row.get("session_date"))
        _day(local)
        for key in ("regular_open", "regular_close"):
            if row.get(key) is not None:
                _aware(str(row[key]), timezone=timezone, local_day=local)
    for row in _tuple_records(payload.get("monthly_rules", []), "monthly_rules"):
        _day(str(row.get("nominal_date")))
        _day(str(row.get("observed_expiry_date")))
        if row.get("settlement_timestamp") is not None:
            # Keep duplicate/conflicting rows loadable so the resolver can
            # report the conflict rather than a lower-level parse failure.
            _aware(str(row["settlement_timestamp"]), timezone=timezone)
    events = _tuple_records(payload.get("event_records", []), "event_records")
    for row in events:
        _day(str(row.get("event_day")))
        for key in ("window_start", "window_end"):
            _aware(str(row[key]), timezone=timezone, local_day=str(row["event_day"]))
    normalized = dict(payload)
    normalized.update({"timezone": timezone, "knowledge_cutoff": str(payload["knowledge_cutoff"])})
    expected = _hash(_canonical_payload(normalized))
    supplied = payload.get("snapshot_hash", expected)
    if str(supplied).lower() != expected:
        raise ValueError("snapshot_hash does not match canonical snapshot")
    return CalendarSnapshot(int(payload["schema_version"]), str(payload["calendar_policy_version"]), str(payload["resolver_code_version"]), str(payload["knowledge_cutoff"]), timezone, str(payload["venue_scope"]), tuple(sorted(sources, key=lambda x: canonical_json_bytes(dict(x)))), tuple(sorted(_tuple_records(payload.get("holidays", []), "holidays"), key=lambda x: canonical_json_bytes(dict(x)))), tuple(sorted(sessions, key=lambda x: canonical_json_bytes(dict(x)))), tuple(sorted(_tuple_records(payload.get("monthly_rules", []), "monthly_rules"), key=lambda x: canonical_json_bytes(dict(x)))), tuple(sorted(events, key=lambda x: canonical_json_bytes(dict(x)))), expected)


def canonical_calendar_bytes(snapshot: CalendarSnapshot) -> bytes:
    return canonical_json_bytes(_snapshot_payload(snapshot))


def calendar_hash(snapshot: CalendarSnapshot) -> str:
    return snapshot.snapshot_hash


def standard_monthly_candidate(year: int, month: int) -> date:
    if not 1 <= month <= 12:
        raise ValueError("month must be between 1 and 12")
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7 + 14)


def _as_of(snapshot: CalendarSnapshot, value: str) -> datetime:
    parsed = _aware(value, timezone=snapshot.timezone)
    cutoff = _aware(snapshot.knowledge_cutoff, timezone=snapshot.timezone)
    if parsed > cutoff:
        raise CalendarGapError("as_of is later than snapshot cutoff")
    for source in snapshot.source_records:
        available = source.get("available_at")
        if available and _aware(str(available), timezone=snapshot.timezone) > parsed:
            raise CalendarGapError("source was not available by as_of")
    return parsed


def resolve_opex(snapshot: CalendarSnapshot, *, product_family: str, nominal_or_observed: str, as_of: str) -> OpExRecord:
    _as_of(snapshot, as_of)
    requested = _day(nominal_or_observed)
    rules = [r for r in snapshot.monthly_rules if r.get("product_family") == product_family and (r.get("nominal_date") == nominal_or_observed or r.get("observed_expiry_date") == nominal_or_observed)]
    if not rules:
        raise CalendarGapError("no monthly rule for requested date")
    observed_values = {str(r.get("observed_expiry_date")) for r in rules}
    if len(observed_values) != 1:
        raise CalendarGapError("CONFLICTING monthly expiry facts")
    rule = rules[0]
    observed = str(rule["observed_expiry_date"])
    nominal = str(rule["nominal_date"])
    matching_sessions = [s for s in snapshot.sessions if s.get("session_date") == observed]
    if not matching_sessions:
        raise CalendarGapError("missing observed session")
    if len(matching_sessions) != 1:
        raise CalendarGapError("CONFLICTING observed sessions")
    session = matching_sessions[0]
    status = str(session.get("status", "UNKNOWN"))
    if status == "HOLIDAY_CLOSED":
        raise CalendarGapError("holiday-closed session has no approved shift")
    if status not in {"OPEN", "EARLY_CLOSE"}:
        raise CalendarGapError(f"holiday/unknown/closed session status {status}")
    settlement = str(rule.get("settlement_style", "UNKNOWN"))
    if settlement == "UNKNOWN" or not rule.get("settlement_timestamp"):
        raise CalendarGapError("unknown settlement")
    listing = rule.get("listing_source_ref")
    expiry_class = "STANDARD_MONTHLY" if nominal == observed else "SHIFTED_MONTHLY"
    return OpExRecord(product_family, nominal, observed, expiry_class, str(session.get("session_id", "")), status, session.get("regular_open"), session.get("regular_close"), bool(session.get("early_close", False)), session.get("close_reason"), settlement, rule.get("settlement_timestamp"), str(listing) if listing is not None else None, snapshot.snapshot_hash)


def resolve_event_window(snapshot: CalendarSnapshot, *, event_type: str, event_day: str, window_policy: str, as_of: str) -> EventWindow:
    _as_of(snapshot, as_of)
    if event_type not in {"OPEX", "FOMC", "EARNINGS"}:
        raise ValueError("unsupported event type")
    if window_policy not in {"OPEX_DAY", "PRE_OPEX_SESSION", "POST_OPEX_RESPONSE"}:
        raise CalendarGapError("unknown window policy")
    rows = [r for r in snapshot.event_records if r.get("event_type") == event_type and r.get("event_day") == event_day]
    if not rows:
        raise CalendarGapError("missing event window")
    if len(rows) != 1:
        raise CalendarGapError("CONFLICTING event windows")
    row = rows[0]
    return EventWindow(str(row["event_id"]), event_type, event_day, str(row["window_start"]), str(row["window_end"]), snapshot.timezone, str(row.get("anchor", "SCHEDULED")), str(row.get("source_ref", "")), str(row.get("surprise_status", "DESCRIPTIVE-HABITAT")), bool(row.get("causal_surprise_eligible", False)), snapshot.snapshot_hash)


def calendar_for_probe(snapshot: CalendarSnapshot, *, ticker: str, calendar_day: str, expiry: str, dte: int) -> Mapping[str, Any]:
    day = _day(calendar_day)
    observed = _day(expiry)
    if (observed - day).days != dte:
        raise CalendarGapError("DTE does not match exact local dates")
    session = next((s for s in snapshot.sessions if s.get("session_date") == calendar_day), None)
    if session is None:
        raise CalendarGapError("missing probe session")
    if session.get("status") not in {"OPEN", "EARLY_CLOSE"}:
        raise CalendarGapError("unknown/closed probe session")
    binding = {"snapshot_hash": snapshot.snapshot_hash, "calendar_hash": snapshot.snapshot_hash, "calendar_policy_version": snapshot.calendar_policy_version, "resolver_code_version": snapshot.resolver_code_version, "ticker": ticker, "calendar_day": calendar_day, "expiry": expiry, "dte": dte, "session_id": session.get("session_id"), "session_status": session.get("status"), "regular_open": session.get("regular_open"), "regular_close": session.get("regular_close"), "timezone": snapshot.timezone}
    binding["calendar_binding_hash"] = _hash(binding)
    return binding


__all__ = ["CalendarGapError", "CalendarSnapshot", "EventWindow", "OpExRecord", "calendar_for_probe", "calendar_hash", "canonical_calendar_bytes", "load_snapshot", "resolve_event_window", "resolve_opex", "standard_monthly_candidate"]
