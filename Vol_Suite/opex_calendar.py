"""Pure, fail-closed, point-in-time OpEx calendar resolver."""
from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from types import MappingProxyType
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .provenance_contract import canonical_json_bytes, canonical_sha256

_HEX64 = set("0123456789abcdefABCDEF")
_ALLOWED_EVENTS = {"OPEX", "FOMC", "EARNINGS"}
_ALLOWED_POLICIES = {"OPEX_DAY", "PRE_OPEX_SESSION", "POST_OPEX_RESPONSE"}
_ALLOWED_SURPRISE = {"NOT_APPLICABLE", "OPERATIONAL", "OPERATIONAL-NO-EVENT", "DESCRIPTIVE-HABITAT"}


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
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_thaw(v) for v in value]
    return value


def _aware(value: str, *, timezone: str, local_day: str | None = None) -> datetime:
    if not isinstance(value, str):
        raise TypeError("timestamp must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("invalid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp timezone is required")
    if local_day is not None and parsed.astimezone(ZoneInfo(timezone)).date().isoformat() != local_day:
        raise ValueError("timestamp local date does not match session local day")
    return parsed


def _day(value: Any) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid local date: {value!r}") from exc


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(_thaw(value))).hexdigest()


def _rows(values: Any, name: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(values, (list, tuple)):
        raise TypeError(f"{name} must be a list")
    rows = []
    for row in values:
        if not isinstance(row, Mapping):
            raise TypeError(f"{name} entries must be objects")
        _reject_nan(row)
        rows.append(_freeze(row))
    return tuple(rows)


def _field(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if row.get(name) not in (None, ""):
            return row[name]
    return None


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
    regular_open: str
    regular_close: str
    early_close: bool
    close_reason: str | None
    settlement_style: str
    settlement_timestamp: str
    listing_source_ref: str
    calendar_hash: str
    source_hashes: tuple[str, ...] = ()


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
    source_hashes: tuple[str, ...] = ()
    window_id: str = ""


def _snapshot_payload(snapshot: CalendarSnapshot) -> dict[str, Any]:
    return {
        "schema_version": snapshot.schema_version,
        "calendar_policy_version": snapshot.calendar_policy_version,
        "resolver_code_version": snapshot.resolver_code_version,
        "knowledge_cutoff": snapshot.knowledge_cutoff,
        "timezone": snapshot.timezone,
        "venue_scope": snapshot.venue_scope,
        "source_records": [_thaw(x) for x in snapshot.source_records],
        "holidays": [_thaw(x) for x in snapshot.holidays],
        "sessions": [_thaw(x) for x in snapshot.sessions],
        "monthly_rules": [_thaw(x) for x in snapshot.monthly_rules],
        "event_records": [_thaw(x) for x in snapshot.event_records],
    }


def _canonical_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result.pop("snapshot_hash", None)
    for key in ("source_records", "holidays", "sessions", "monthly_rules", "event_records"):
        result[key] = sorted((dict(row) for row in result.get(key, [])), key=lambda row: canonical_json_bytes(row))
    return result


def _validate_source(source: Mapping[str, Any], timezone: str) -> None:
    required = {
        "source_id": source.get("source_id"),
        "kind": _field(source, "kind", "source_kind"),
        "publisher": source.get("publisher"),
        "version": _field(source, "version", "source_version"),
        "available_at": source.get("available_at"),
        "content_sha256": source.get("content_sha256"),
        "selection_reason": source.get("selection_reason"),
    }
    if any(value in (None, "") for value in required.values()):
        raise ValueError("source_records require source_id/kind/publisher/version/available_at/content_sha256/selection_reason")
    digest = str(required["content_sha256"])
    if len(digest) != 64 or any(ch not in _HEX64 for ch in digest):
        raise ValueError("source content_sha256 must be a SHA-256 hash")
    _aware(str(required["available_at"]), timezone=timezone)


def load_snapshot(payload: Mapping[str, Any]) -> CalendarSnapshot:
    if not isinstance(payload, Mapping):
        raise TypeError("snapshot must be an object")
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
    _aware(str(payload["knowledge_cutoff"]), timezone=timezone)
    if int(payload["schema_version"]) != 1:
        raise ValueError("unsupported calendar schema_version")
    sources = _rows(payload.get("source_records", []), "source_records")
    if not sources:
        raise ValueError("source_records must not be empty")
    source_ids = set()
    for source in sources:
        _validate_source(source, timezone)
        sid = str(source["source_id"])
        if sid in source_ids:
            raise ValueError("duplicate source_id")
        source_ids.add(sid)
    sessions = _rows(payload.get("sessions", []), "sessions")
    for row in sessions:
        local = str(row.get("session_date")); _day(local)
        if not row.get("session_id") or not row.get("source_ref"):
            raise ValueError("session_id and source_ref are required for sessions")
        for key in ("regular_open", "regular_close"):
            if not row.get(key):
                raise ValueError("session open/close facts are required")
            _aware(str(row[key]), timezone=timezone, local_day=local)
    monthly = _rows(payload.get("monthly_rules", []), "monthly_rules")
    for row in monthly:
        _day(row.get("nominal_date")); _day(row.get("observed_expiry_date"))
        if not row.get("source_ref") or not row.get("listing_source_ref"):
            raise ValueError("monthly and listing source refs are required")
        if not row.get("settlement_style") or not row.get("settlement_timestamp"):
            raise ValueError("settlement style and timestamp are required")
        _aware(str(row["settlement_timestamp"]), timezone=timezone)
    holidays = _rows(payload.get("holidays", []), "holidays")
    for row in holidays:
        if not row.get("source_ref"):
            raise ValueError("holiday source_ref is required")
    events = _rows(payload.get("event_records", []), "event_records")
    for row in events:
        if not row.get("event_id") or not row.get("source_ref"):
            raise ValueError("event_id and source_ref are required")
        if row.get("event_type") not in _ALLOWED_EVENTS:
            raise ValueError("invalid event_type")
        _day(row.get("event_day"))
        if not row.get("window_start") or not row.get("window_end"):
            raise ValueError("event windows are required")
        _aware(str(row["window_start"]), timezone=timezone, local_day=str(row["event_day"]))
        _aware(str(row["window_end"]), timezone=timezone, local_day=str(row["event_day"]))
        status = str(row.get("surprise_status", ""))
        if status not in _ALLOWED_SURPRISE:
            raise ValueError("invalid surprise_status")
        if not isinstance(row.get("causal_surprise_eligible"), bool):
            raise TypeError("causal_surprise_eligible must be boolean")
        _validate_surprise(str(row["surprise_status"]), row["causal_surprise_eligible"])
    normalized = dict(payload)
    normalized.update({"timezone": timezone, "knowledge_cutoff": str(payload["knowledge_cutoff"])})
    expected = _hash(_canonical_payload(normalized))
    supplied = payload.get("snapshot_hash", expected)
    if str(supplied).lower() != expected:
        raise ValueError("snapshot_hash does not match canonical snapshot")
    freeze_sorted = lambda xs: tuple(sorted(xs, key=lambda x: canonical_json_bytes(_thaw(x))))
    return CalendarSnapshot(int(payload["schema_version"]), str(payload["calendar_policy_version"]), str(payload["resolver_code_version"]), str(payload["knowledge_cutoff"]), timezone, str(payload["venue_scope"]), freeze_sorted(sources), freeze_sorted(holidays), freeze_sorted(sessions), freeze_sorted(monthly), freeze_sorted(events), expected)


def canonical_calendar_bytes(snapshot: CalendarSnapshot) -> bytes:
    return canonical_json_bytes(_snapshot_payload(snapshot))


def calendar_hash(snapshot: CalendarSnapshot) -> str:
    return snapshot.snapshot_hash


def resolver_code_hash(snapshot: CalendarSnapshot) -> str:
    """Return the shared canonical identity of the frozen resolver version."""
    return canonical_sha256({"resolver_code_version": snapshot.resolver_code_version})


def standard_monthly_candidate(year: int, month: int) -> date:
    if not 1 <= month <= 12:
        raise ValueError("month must be between 1 and 12")
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7 + 14)


def _as_of(snapshot: CalendarSnapshot, value: str) -> datetime:
    parsed = _aware(value, timezone=snapshot.timezone)
    if parsed > _aware(snapshot.knowledge_cutoff, timezone=snapshot.timezone):
        raise CalendarGapError("as_of is later than snapshot cutoff")
    return parsed


def _source(snapshot: CalendarSnapshot, ref: Any, as_of: datetime, *, role: str) -> Mapping[str, Any]:
    if not isinstance(ref, str) or not ref:
        raise CalendarGapError(f"missing {role} source ref")
    matches = [s for s in snapshot.source_records if str(s.get("source_id")) == ref]
    if len(matches) != 1:
        raise CalendarGapError(f"unknown/conflicting {role} source ref")
    available = _aware(str(matches[0]["available_at"]), timezone=snapshot.timezone)
    if available > as_of:
        raise CalendarGapError(f"{role} source was not available by as_of")
    return matches[0]


def _source_hashes(sources: list[Mapping[str, Any]]) -> tuple[str, ...]:
    return tuple(sorted(str(s["content_sha256"]).lower() for s in sources))


def _session(snapshot: CalendarSnapshot, day: str) -> Mapping[str, Any]:
    rows = [s for s in snapshot.sessions if s.get("session_date") == day]
    if len(rows) != 1:
        raise CalendarGapError("missing/conflicting session")
    row = rows[0]
    if str(row.get("status")) == "HOLIDAY_CLOSED":
        raise CalendarGapError("holiday-closed session has no approved shift")
    if str(row.get("status")) not in {"OPEN", "EARLY_CLOSE"}:
        raise CalendarGapError("unknown/closed session status")
    if not isinstance(row.get("early_close"), bool):
        raise CalendarGapError("invalid early_close session fact")
    try:
        opened = _aware(str(row["regular_open"]), timezone=snapshot.timezone, local_day=day)
        closed = _aware(str(row["regular_close"]), timezone=snapshot.timezone, local_day=day)
    except (TypeError, ValueError) as exc:
        raise CalendarGapError(f"invalid session timestamp: {exc}") from exc
    if opened >= closed:
        raise CalendarGapError("reversed session boundaries")
    return row


def _validate_surprise(status: str, causal: bool) -> None:
    if status not in _ALLOWED_SURPRISE or status == "UNKNOWN":
        raise CalendarGapError("unknown/invalid surprise status")
    expected = status in {"OPERATIONAL", "OPERATIONAL-NO-EVENT"}
    if causal is not expected:
        raise CalendarGapError("invalid surprise status/causal eligibility combination")


def _validate_holidays(snapshot: CalendarSnapshot, asof: datetime) -> None:
    for holiday in snapshot.holidays:
        try:
            _day(holiday.get("holiday_date"))
        except (TypeError, ValueError) as exc:
            raise CalendarGapError(f"invalid holiday date: {exc}") from exc
        _source(snapshot, holiday.get("source_ref"), asof, role="holiday")


def resolve_opex(snapshot: CalendarSnapshot, *, product_family: str, nominal_or_observed: str, as_of: str) -> OpExRecord:
    asof = _as_of(snapshot, as_of); _day(nominal_or_observed)
    _validate_holidays(snapshot, asof)
    rules = [r for r in snapshot.monthly_rules if r.get("product_family") == product_family and (r.get("nominal_date") == nominal_or_observed or r.get("observed_expiry_date") == nominal_or_observed)]
    if not rules:
        raise CalendarGapError("no monthly expiry facts")
    if len(rules) != 1:
        raise CalendarGapError("CONFLICTING monthly expiry facts")
    rule = rules[0]; nominal = str(rule["nominal_date"]); observed = str(rule["observed_expiry_date"])
    monthly_source = _source(snapshot, rule.get("source_ref"), asof, role="monthly")
    listing_source = _source(snapshot, rule.get("listing_source_ref"), asof, role="listing")
    session = _session(snapshot, observed)
    session_source = _source(snapshot, session.get("source_ref"), asof, role="session")
    try:
        settlement = _aware(str(rule["settlement_timestamp"]), timezone=snapshot.timezone, local_day=observed)
    except ValueError as exc:
        raise CalendarGapError(str(exc)) from exc
    if str(rule["settlement_style"]) not in {"PM_CLOSE", "AM_SETTLEMENT"}:
        raise CalendarGapError("unknown settlement style")
    if settlement.date() != _aware(str(session["regular_open"]), timezone=snapshot.timezone).date():
        raise CalendarGapError("settlement timestamp is not on session local day")
    candidate = standard_monthly_candidate(*(_day(nominal).year, _day(nominal).month))
    venue_date = _field(rule, "venue_rule_date", "venue_rule_expiry_date", "rule_expiry_date")
    listing_date = _field(rule, "listing_expiry_date", "listed_expiry_date", "listing_date")
    if nominal == observed and (venue_date is None or listing_date is None or _day(venue_date) != candidate or _day(listing_date) != candidate):
        raise CalendarGapError("standard monthly lacks third-Friday venue-rule/listing agreement")
    if venue_date is None or listing_date is None or _day(venue_date) != _day(listing_date):
        raise CalendarGapError("venue-rule/listing disagreement")
    if _day(venue_date) != _day(observed):
        raise CalendarGapError("venue-rule/listing date does not match observed expiry")
    return OpExRecord(product_family, nominal, observed, "STANDARD_MONTHLY" if nominal == observed else "SHIFTED_MONTHLY", str(session["session_id"]), str(session["status"]), str(session["regular_open"]), str(session["regular_close"]), bool(session.get("early_close", False)), session.get("close_reason"), str(rule["settlement_style"]), str(rule["settlement_timestamp"]), str(rule["listing_source_ref"]), snapshot.snapshot_hash, _source_hashes([monthly_source, listing_source, session_source]))


def _adjacent_session(snapshot: CalendarSnapshot, day: str, direction: int) -> Mapping[str, Any]:
    target = _day(day)
    candidates = []
    seen_dates = set()
    for session in snapshot.sessions:
        session_day = _day(session.get("session_date"))
        if session_day in seen_dates:
            raise CalendarGapError("conflicting adjacent session dates")
        seen_dates.add(session_day)
        if str(session.get("status")) not in {"OPEN", "EARLY_CLOSE"}:
            continue
        _session(snapshot, str(session["session_date"]))
        candidates.append((session_day, session))
    candidates.sort(key=lambda item: (item[0], canonical_json_bytes(_thaw(item[1]))))
    prior = [item for item in candidates if (item[0] < target if direction < 0 else item[0] > target)]
    if not prior:
        raise CalendarGapError("missing adjacent session for window policy")
    return prior[-1 if direction < 0 else 0][1]


def resolve_event_window(snapshot: CalendarSnapshot, *, event_type: str, event_day: str, window_policy: str, as_of: str) -> EventWindow:
    asof = _as_of(snapshot, as_of)
    if event_type not in _ALLOWED_EVENTS:
        raise ValueError("unsupported event type")
    if window_policy not in _ALLOWED_POLICIES:
        raise CalendarGapError("unknown window policy")
    rows = [r for r in snapshot.event_records if r.get("event_type") == event_type and r.get("event_day") == event_day]
    if len(rows) != 1:
        raise CalendarGapError("missing or conflicting event window")
    row = rows[0]; event_source = _source(snapshot, row.get("source_ref"), asof, role="event")
    session = _session(snapshot, event_day); session_source = _source(snapshot, session.get("source_ref"), asof, role="session")
    try:
        event_start = _aware(str(row["window_start"]), timezone=snapshot.timezone, local_day=event_day)
        event_end = _aware(str(row["window_end"]), timezone=snapshot.timezone, local_day=event_day)
        open_ = _aware(str(session["regular_open"]), timezone=snapshot.timezone, local_day=event_day)
        close = _aware(str(session["regular_close"]), timezone=snapshot.timezone, local_day=event_day)
    except (TypeError, ValueError) as exc:
        raise CalendarGapError(f"invalid event/session window: {exc}") from exc
    if event_start >= event_end or event_start < open_ or event_end > close:
        raise CalendarGapError("event window conflicts with session boundaries")
    if window_policy == "OPEX_DAY":
        start, end, anchor = open_, close, "SESSION_BOUNDARY"
    elif window_policy == "PRE_OPEX_SESSION":
        prior = _adjacent_session(snapshot, event_day, -1)
        _source(snapshot, prior.get("source_ref"), asof, role="prior session")
        start = _aware(str(prior["regular_close"]), timezone=snapshot.timezone, local_day=str(prior["session_date"]))
        end = event_start; anchor = "PRIOR_SESSION_TO_EVENT"
    else:
        nxt = _adjacent_session(snapshot, event_day, 1)
        _source(snapshot, nxt.get("source_ref"), asof, role="next session")
        start = event_end; end = _aware(str(nxt["regular_open"]), timezone=snapshot.timezone, local_day=str(nxt["session_date"]))
        anchor = "EVENT_TO_NEXT_SESSION"
    if start >= end:
        raise CalendarGapError("derived window boundaries conflict")
    causal = bool(row["causal_surprise_eligible"])
    _validate_surprise(str(row["surprise_status"]), causal)
    window_id = f"{row['event_id']}:{window_policy}"
    return EventWindow(str(row["event_id"]), event_type, event_day, start.isoformat(), end.isoformat(), snapshot.timezone, anchor, str(row["source_ref"]), str(row["surprise_status"]), causal, snapshot.snapshot_hash, _source_hashes([event_source, session_source]), window_id)


def calendar_for_probe(snapshot: CalendarSnapshot, *, ticker: str, calendar_day: str, expiry: str, dte: int, as_of: str | None = None, window_policy: str = "OPEX_DAY") -> Mapping[str, Any]:
    if as_of is None:
        raise CalendarGapError("as_of is required for probe binding")
    asof = _as_of(snapshot, as_of); day = _day(calendar_day); observed = _day(expiry)
    if window_policy not in _ALLOWED_POLICIES:
        raise CalendarGapError("unknown window policy")
    if isinstance(dte, bool) or not isinstance(dte, int) or (observed - day).days != dte:
        raise CalendarGapError("DTE does not match exact local dates")
    monthly_matches = [r for r in snapshot.monthly_rules if r.get("observed_expiry_date") == expiry]
    families = {str(r.get("product_family")) for r in monthly_matches}
    if len(monthly_matches) != 1 or len(families) != 1:
        raise CalendarGapError("missing/conflicting observed expiry binding")
    opex = resolve_opex(snapshot, product_family=next(iter(families)), nominal_or_observed=expiry, as_of=as_of)
    session = _session(snapshot, calendar_day); session_source = _source(snapshot, session.get("source_ref"), asof, role="session")
    _validate_holidays(snapshot, asof)
    event_rows = sorted((e for e in snapshot.event_records if e.get("event_day") == calendar_day), key=lambda e: str(e.get("event_id")))
    if not event_rows:
        raise CalendarGapError("event context is required")
    event_ids = [str(e["event_id"]) for e in event_rows]
    if len(set(event_ids)) != len(event_ids):
        raise CalendarGapError("duplicate event IDs")
    resolved_events = [resolve_event_window(snapshot, event_type=str(e.get("event_type")), event_day=calendar_day, window_policy=window_policy, as_of=as_of) for e in event_rows]
    event_windows = {event.event_id: {"window_id": event.window_id, "window_start": event.window_start, "window_end": event.window_end, "window_policy": window_policy} for event in resolved_events}
    first_window = resolved_events[0]
    event_source_hashes = [source_hash for event in resolved_events for source_hash in event.source_hashes]
    source_hashes = sorted({*opex.source_hashes, *event_source_hashes, str(session_source["content_sha256"])})
    binding = {"ticker": ticker, "calendar_day": calendar_day, "nominal_date": opex.nominal_date, "observed_expiry": opex.observed_expiry_date, "observed_expiry_date": opex.observed_expiry_date, "expiry": expiry, "dte": dte, "exact_dte": (observed - day).days, "session_id": opex.session_id, "observed_session_id": opex.session_id, "session_status": opex.session_status, "regular_open": opex.regular_open, "regular_close": opex.regular_close, "early_close": opex.early_close, "close_reason": opex.close_reason, "settlement_style": opex.settlement_style, "settlement_timestamp": opex.settlement_timestamp, "event_ids": event_ids, "event_windows": event_windows, "event_window_id": first_window.window_id, "window_id": first_window.window_id, "window_start": first_window.window_start, "window_end": first_window.window_end, "timezone": snapshot.timezone, "as_of": as_of, "snapshot_hash": snapshot.snapshot_hash, "calendar_hash": snapshot.snapshot_hash, "source_hashes": source_hashes, "calendar_policy_version": snapshot.calendar_policy_version, "resolver_code_version": snapshot.resolver_code_version, "resolver_code_hash": resolver_code_hash(snapshot), "window_policy": window_policy}
    binding["calendar_binding_hash"] = _hash(binding)
    return MappingProxyType({k: _freeze(v) for k, v in binding.items()})


__all__ = ["CalendarGapError", "CalendarSnapshot", "EventWindow", "OpExRecord", "calendar_for_probe", "calendar_hash", "canonical_calendar_bytes", "load_snapshot", "resolve_event_window", "resolve_opex", "resolver_code_hash", "standard_monthly_candidate"]
