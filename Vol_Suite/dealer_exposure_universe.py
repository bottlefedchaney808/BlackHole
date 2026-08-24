"""Deterministic, network-free candidate-universe contracts for dealer exposure studies."""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .opex_calendar import CalendarGapError, calendar_for_probe, resolver_code_hash
from .provenance_contract import canonical_sha256

DTE_STRATA = ((1, 3), (4, 7), (8, 10))
EVENT_HABITATS = ("FOMC", "EARNINGS", "OPEX")
PROBE_STATUSES = {"PASS", "INELIGIBLE", "HARD_GAP"}
CHECK_STATUSES = PROBE_STATUSES
PROBE_CHECKS = (
    "chain_listing",
    "historical_greeks_iv",
    "open_interest",
    "spot_ohlc",
    "timestamp_granularity",
    "expiry_dte",
    "post_window_returns",
    "spot_ohlc_coverage",
    "same_expiry_grid_oi_iv",
    "strike_side_moneyness",
    "strict_pre_window_ordering",
    "return_clocks",
    "no_imputation",
)
_REQUIRED_EVIDENCE = {
    "spot_ohlc_coverage": (
        "pre_window",
        "firing_window",
        "response_window",
        "return_clocks",
    ),
    "same_expiry_grid_oi_iv": ("expiry", "grid", "oi", "iv"),
    "strike_side_moneyness": ("call_side", "put_side", "moneyness_band"),
    "strict_pre_window_ordering": (
        "pre_window_last",
        "breach_first",
        "strictly_before",
    ),
    "return_clocks": ("daily", "from_breach"),
}
_REFERENCE_FAMILIES = {"SPY", "QQQ"}
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
_DATE_RE = re.compile(r"^\d{4}[-]?\d{2}[-]?\d{2}$")
_CLOCK_RE = re.compile(r"^(?:[1-9]\d*)(?:d|h|m)$", re.IGNORECASE)
_PROVENANCE_SOURCE_DEFAULT = "point-in-time-static"


def _selection_date(value: Any) -> str:
    """Validate the manifest's provenance date as a canonical ISO date."""
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
    ):
        raise ValueError(
            "selection_date must be a supplied valid ISO date (YYYY-MM-DD)"
        )
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            "selection_date must be a supplied valid ISO date (YYYY-MM-DD)"
        ) from exc
    return value


def _source_list(value: Any) -> str:
    """Require a real, non-placeholder source-list provenance label."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("source_list must be supplied and non-empty")
    source = value.strip()
    if source.casefold() == _PROVENANCE_SOURCE_DEFAULT:
        raise ValueError(
            "source_list must identify a real source, not the fabricated default"
        )
    return source


def _date(value: Any) -> str:
    text = str(value).strip()
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    if not _DATE_RE.fullmatch(text):
        raise ValueError(f"invalid calendar day: {value!r}")
    import datetime

    try:
        datetime.date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid calendar day: {value!r}") from exc
    return text


def _stratum(dte: int) -> tuple[int, int] | None:
    return next((s for s in DTE_STRATA if s[0] <= dte <= s[1]), None)


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


_CALENDAR_HASH_FIELDS = (
    "snapshot_hash",
    "calendar_hash",
    "calendar_policy_version",
    "resolver_code_version",
    "resolver_code_hash",
    "calendar_binding_hash",
)
_CALENDAR_VALUE_FIELDS = (
    "as_of",
    "calendar_day",
    "nominal_date",
    "observed_expiry",
    "observed_expiry_date",
    "session_id",
    "observed_session_id",
    "session_status",
    "settlement_style",
    "settlement_timestamp",
    "timezone",
    "exact_dte",
    "event_ids",
    "event_windows",
    "event_window_id",
    "window_id",
    "window_start",
    "window_end",
    "window_policy",
    "source_hashes",
)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _validate_calendar_binding(
    binding: Any,
    *,
    ticker: str,
    day: str,
    expiry: str,
    dte: int,
    expected: Mapping[str, Any] | None = None,
    calendar_snapshot: Any | None = None,
) -> dict[str, Any]:
    """Validate a binding by recomputing it from the frozen OpEx snapshot.

    A caller-provided mapping is evidence only; it is never a source of truth.
    """
    if dte <= 0 or _stratum(dte) is None:
        raise ValueError("calendar binding DTE mismatch or zero-DTE")
    if calendar_snapshot is not None:
        try:
            resolved = calendar_for_probe(
                calendar_snapshot,
                ticker=ticker,
                calendar_day=day,
                expiry=expiry,
                dte=dte,
                as_of=(binding or {}).get("as_of"),
                window_policy=(binding or {}).get("window_policy", "OPEX_DAY"),
            )
        except (AttributeError, CalendarGapError, TypeError, ValueError) as exc:
            raise ValueError(f"calendar binding cannot be resolved: {exc}") from exc
        if not isinstance(binding, Mapping) or _plain(binding) != _plain(resolved):
            raise ValueError("calendar binding does not match the injected snapshot")
        value = _plain(resolved)
    else:
        if not isinstance(binding, Mapping):
            raise ValueError("calendar binding is required")
        value = _plain(binding)
    for key in (*_CALENDAR_HASH_FIELDS, *_CALENDAR_VALUE_FIELDS):
        if key not in value or value[key] in (None, ""):
            raise ValueError(f"calendar binding is missing {key}")
    for key in (
        "snapshot_hash",
        "calendar_hash",
        "resolver_code_hash",
        "calendar_binding_hash",
        *["source_hashes"],
    ):
        if key != "source_hashes" and (
            not isinstance(value[key], str) or not _HEX64.fullmatch(value[key])
        ):
            raise ValueError(
                f"calendar binding {key} is not a lowercase SHA-256 identity"
            )
    if value["snapshot_hash"] != value["calendar_hash"]:
        raise ValueError("calendar hash does not match snapshot hash")
    if calendar_snapshot is not None:
        if value["snapshot_hash"] != calendar_snapshot.snapshot_hash or value[
            "resolver_code_hash"
        ] != resolver_code_hash(calendar_snapshot):
            raise ValueError("calendar binding snapshot/resolver identity mismatch")
    if value["calendar_binding_hash"] != canonical_sha256(
        {k: v for k, v in value.items() if k != "calendar_binding_hash"}
    ):
        raise ValueError("calendar_binding_hash does not match canonical binding")
    if (
        str(value.get("ticker", ticker)).upper() != ticker.upper()
        or value["calendar_day"] != day
        or value["observed_expiry"] != expiry
        or value["observed_expiry_date"] != expiry
    ):
        raise ValueError("calendar binding identity mismatch")
    if (
        value.get("exact_dte") != dte
        or value.get("dte", dte) != dte
        or dte <= 0
        or _stratum(dte) is None
    ):
        raise ValueError("calendar binding DTE mismatch or zero-DTE")
    if value["timezone"] != "America/New_York" or value["session_status"] not in {
        "OPEN",
        "EARLY_CLOSE",
    }:
        raise ValueError("calendar binding has unknown timezone or session status")
    if (
        not isinstance(value["source_hashes"], list)
        or not value["source_hashes"]
        or any(
            not isinstance(h, str) or not _HEX64.fullmatch(h)
            for h in value["source_hashes"]
        )
    ):
        raise ValueError("calendar binding source hashes are incomplete")
    if (
        not isinstance(value["event_ids"], list)
        or not value["event_ids"]
        or not isinstance(value["event_windows"], Mapping)
    ):
        raise ValueError("calendar binding event identity is required")
    if expected is not None and value != _plain(expected):
        raise ValueError("calendar binding conflicts with schedule/probe identity")
    return value


@dataclass(frozen=True, order=True)
class Candidate:
    ticker: str
    sector: str
    asset_type: str
    selection_date: str
    source_list: str
    reference_family: str | None = None


@dataclass(frozen=True)
class ProbeResult:
    ticker: str
    day: str
    expiry: str
    dte: int
    status: str
    checks: Mapping[str, str]
    reasons: Sequence[str]
    imputed_zero: bool = False
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["checks"] = dict(self.checks)
        data["reasons"] = list(self.reasons)
        data["evidence"] = dict(self.evidence)
        return data


@dataclass(frozen=True)
class ManifestUnit:
    ticker: str
    day: str
    sector: str
    asset_type: str
    dte: int
    event_habitat: str
    dte_stratum: tuple[int, int]
    calendar_binding: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["dte_stratum"] = list(self.dte_stratum)
        if self.calendar_binding is not None:
            data["calendar_binding"] = _plain(self.calendar_binding)
        return data


@dataclass(frozen=True)
class UniverseManifest:
    selection_date: str
    source_list: str
    intended_units: int
    units: tuple[ManifestUnit, ...]
    exclusions: Mapping[str, str]
    quota_schema: Mapping[str, Any]
    unique_days: tuple[str, ...]
    sector_counts: Mapping[str, int]
    ticker_counts: Mapping[str, int]
    probe_results: tuple[ProbeResult, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "selection_date": self.selection_date,
            "source_list": self.source_list,
            "intended_units": self.intended_units,
            "units": [u.to_dict() for u in self.units],
            "exclusions": dict(self.exclusions),
            "quota_schema": dict(self.quota_schema),
            "unique_days": list(self.unique_days),
            "sector_counts": dict(self.sector_counts),
            "ticker_counts": dict(self.ticker_counts),
            "probe_results": [p.to_dict() for p in self.probe_results],
        }


def normalize_candidate(
    raw: Mapping[str, Any],
    *,
    selection_date: str | None = None,
    source_list: str | None = None,
) -> Candidate:
    ticker = str(raw.get("ticker", "")).strip().lstrip("$").upper()
    if not _TICKER_RE.fullmatch(ticker):
        raise ValueError(f"invalid ticker: {ticker!r}")
    if ticker in _REFERENCE_FAMILIES or raw.get("reference_family"):
        raise ValueError("SPY/QQQ are reference families, not expansion candidates")
    candidate_date = raw.get("selection_date")
    candidate_source = raw.get("source_list")
    if (
        selection_date is not None
        and candidate_date is not None
        and _selection_date(candidate_date) != selection_date
    ):
        raise ValueError("candidate selection_date conflicts with manifest provenance")
    if (
        source_list is not None
        and candidate_source is not None
        and _source_list(candidate_source) != source_list
    ):
        raise ValueError("candidate source_list conflicts with manifest provenance")
    date = selection_date if selection_date is not None else candidate_date
    source = source_list if source_list is not None else candidate_source
    if date is None or source is None:
        raise ValueError("selection_date and source_list are required provenance")
    date = _selection_date(date)
    source = _source_list(source)
    sector = str(raw.get("sector", "")).strip()
    if not sector:
        raise ValueError("point-in-time sector is required")
    asset_type = str(raw.get("asset_type", "equity")).strip().lower()
    if asset_type not in {"equity", "etf"}:
        raise ValueError("asset_type must be equity or etf")
    return Candidate(ticker, sector, asset_type, _date(date), str(source).strip())


def _json_objects(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _json_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _json_objects(child)


def _add_pair(pairs: set[tuple[str, str]], ticker: Any, day: Any) -> None:
    ticker = str(ticker or "").strip().upper()
    if _TICKER_RE.fullmatch(ticker):
        try:
            pairs.add((ticker, _date(day)))
        except ValueError:
            pass


def held_pairs_from_paths(paths: Iterable[str | Path]) -> set[tuple[str, str]]:
    """Extract held pairs using acquisition/as-of fields, never expiry filenames.

    Tier2 seed filenames contain expiry, not the held calendar day.  A seed is
    therefore joined to its pull-manifest result (or its explicit manifest.as_of).
    Filename dates are used only for a ``window_YYYYMMDD`` directory, which is an
    explicit acquisition-day marker in tier2b artifacts.
    """
    pairs: set[tuple[str, str]] = set()
    for root in sorted((Path(p) for p in paths), key=lambda p: str(p)):
        files = [root] if root.is_file() else sorted(root.rglob("*.json"))
        manifests: dict[str, tuple[str, str]] = {}
        for path in files:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeDecodeError):
                continue
            if isinstance(payload, Mapping):
                as_of = payload.get("as_of")
                if isinstance(as_of, str):
                    for result in payload.get("results", []):
                        if isinstance(result, Mapping) and result.get("ticker"):
                            file_name = str(result.get("file", ""))
                            manifests[Path(file_name).name] = (
                                str(result["ticker"]),
                                as_of,
                            )
                    if payload.get("ticker"):
                        manifests[path.name] = (str(payload["ticker"]), as_of)
                for obj in _json_objects(payload):
                    day = next(
                        (
                            obj.get(k)
                            for k in (
                                "day",
                                "date",
                                "calendar_day",
                                "trade_date",
                                "as_of",
                                "acquired_on",
                            )
                            if obj.get(k) is not None
                        ),
                        None,
                    )
                    tickers = obj.get("families") or obj.get("tickers")
                    if isinstance(tickers, str):
                        tickers = [tickers]
                    if obj.get("ticker"):
                        tickers = list(tickers or []) + [obj["ticker"]]
                    for ticker in tickers or []:
                        if day is not None:
                            _add_pair(pairs, ticker, day)
                manifest = payload.get("manifest")
                if (
                    isinstance(manifest, Mapping)
                    and manifest.get("as_of")
                    and manifest.get("ticker")
                ):
                    _add_pair(pairs, manifest["ticker"], manifest["as_of"])
            elif isinstance(payload, list):
                for obj in _json_objects(payload):
                    if (
                        isinstance(obj, Mapping)
                        and obj.get("ticker")
                        and obj.get("as_of")
                    ):
                        _add_pair(pairs, obj["ticker"], obj["as_of"])
        for path in files:
            match = re.search(
                r"seed_data_([A-Za-z0-9.-]+)_\d{8}", path.name, re.IGNORECASE
            )
            if not match:
                continue
            if path.name in manifests:
                _add_pair(pairs, *manifests[path.name])
                continue
            window = re.search(
                r"(?:^|[\\/])window_(\d{8})(?:[\\/]|$)", str(path.parent), re.IGNORECASE
            )
            if window:
                _add_pair(pairs, match.group(1), window.group(1))
    return pairs


def _timestamp(value: Any) -> dt.datetime:
    """Parse an evidence timestamp, rejecting dates and non-finite placeholders."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("evidence timestamp is required")
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid evidence timestamp: {value!r}") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(dt.UTC).replace(tzinfo=None)
    return parsed


def _number(value: Any, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("evidence value must be numeric")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError("evidence value must be finite and positive")
    return result


def _rows(value: Any, label: str) -> list[Any]:
    if isinstance(value, Mapping):
        value = list(value.values())
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"{label} evidence must be non-empty")
    return list(value)


def _validate_probe_evidence(result: ProbeResult) -> None:
    evidence = result.evidence
    identity = evidence.get("probe_identity")
    if identity is not None:
        if not isinstance(identity, Mapping):
            raise ValueError("probe identity evidence must be a mapping")
        expected = {
            "ticker": result.ticker.upper(),
            "day": _date(result.day),
            "expiry": _date(result.expiry),
            "dte": result.dte,
        }
        actual = {
            "ticker": str(identity.get("ticker", "")).strip().upper(),
            "day": _date(identity.get("day")),
            "expiry": _date(identity.get("expiry")),
            "dte": identity.get("dte"),
        }
        if actual != expected:
            raise ValueError("probe evidence identity does not match probe")
    _validate_calendar_binding(
        evidence.get("calendar_binding"),
        ticker=result.ticker,
        day=_date(result.day),
        expiry=_date(result.expiry),
        dte=result.dte,
    )
    spot = evidence["spot_ohlc_coverage"]
    # Coverage is evidence, not a set of truthy labels: every required window
    # must contain a timestamp and real positive spot/OHLC values.
    for window in ("pre_window", "firing_window", "response_window"):
        rows = _rows(spot[window], window)
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError(
                    f"{window} evidence rows must be mappings with spot/OHLC values"
                )
            if "timestamp" not in row:
                raise ValueError(f"{window} evidence rows must contain timestamp")
            _timestamp(row["timestamp"])
            _number(row.get("spot"), positive=True)
            ohlc = row.get("ohlc", row)
            if not isinstance(ohlc, Mapping):
                raise ValueError(f"{window} evidence OHLC values must be a mapping")
            for field in ("open", "high", "low", "close"):
                _number(ohlc.get(field), positive=True)
    if spot.get("return_clocks") != "daily/from_breach":
        raise ValueError("spot/OHLC coverage must identify daily/from_breach clocks")

    grid = evidence["same_expiry_grid_oi_iv"]
    if _date(grid.get("expiry")) != _date(result.expiry):
        raise ValueError("same-expiry evidence does not match probe expiry")
    strikes = [
        _number(v, positive=True) for v in _rows(grid.get("grid"), "strike grid")
    ]
    oi = [_number(v, positive=True) for v in _rows(grid.get("oi"), "OI")]
    iv = [_number(v, positive=True) for v in _rows(grid.get("iv"), "IV")]
    if not (len(strikes) == len(oi) == len(iv)):
        raise ValueError("same-expiry OI/IV evidence is not grid-consistent")

    sides = evidence["strike_side_moneyness"]
    band = sides.get("moneyness_band")
    if not isinstance(band, (list, tuple)) or len(band) != 2:
        raise ValueError("moneyness band must declare numeric lower and upper bounds")
    low, high = (_number(v, positive=True) for v in band)
    if low >= high:
        raise ValueError("moneyness band is invalid")
    for side in ("call_side", "put_side"):
        values = [_number(v, positive=True) for v in _rows(sides.get(side), side)]
        if not all(low <= value <= high for value in values):
            raise ValueError(f"{side} coverage falls outside declared moneyness band")

    ordering = evidence["strict_pre_window_ordering"]
    pre = _timestamp(ordering.get("pre_window_last"))
    breach = _timestamp(ordering.get("breach_first"))
    if pre >= breach:
        raise ValueError("PRE_WINDOW source timestamp must precede breach")
    if "strictly_before" in ordering and ordering["strictly_before"] is not True:
        raise ValueError("contradictory strictly_before marker")
    if pre.date().isoformat() != _date(
        result.day
    ) or breach.date().isoformat() != _date(result.day):
        raise ValueError("PRE_WINDOW/breach timestamps must be on probe day")

    clocks = evidence["return_clocks"]
    daily, from_breach = clocks.get("daily"), clocks.get("from_breach")
    if not (
        isinstance(daily, str)
        and isinstance(from_breach, str)
        and _CLOCK_RE.fullmatch(daily.strip())
        and _CLOCK_RE.fullmatch(from_breach.strip())
    ):
        raise ValueError(
            "daily and from-breach clocks must be valid positive durations"
        )
    if daily.strip().lower() == from_breach.strip().lower():
        raise ValueError("daily and from-breach clocks must be distinct")

    if (
        evidence.get("no_imputation") is not True
        or evidence.get("zero_dte") is not False
    ):
        raise ValueError(
            "imputation markers and zero-DTE evidence must be explicitly clean"
        )


def validate_probe_result(result: ProbeResult) -> ProbeResult:
    if result.status not in PROBE_STATUSES:
        raise ValueError(f"invalid probe status: {result.status}")
    if not _TICKER_RE.fullmatch(result.ticker.upper()):
        raise ValueError("invalid probe ticker")
    if result.dte <= 0 or _stratum(result.dte) is None:
        raise ValueError("probe DTE must be in the locked 1-10 strata")
    day, expiry = _date(result.day), _date(result.expiry)
    if (dt.date.fromisoformat(expiry) - dt.date.fromisoformat(day)).days != result.dte:
        raise ValueError("probe expiry and DTE do not match probe identity")
    if set(result.checks) != set(PROBE_CHECKS):
        raise ValueError("probe checks must contain exactly the locked checks")
    if any(v not in CHECK_STATUSES for v in result.checks.values()):
        raise ValueError("invalid check status")
    if result.status == "PASS" and any(v != "PASS" for v in result.checks.values()):
        raise ValueError("PASS probe requires every check to PASS")
    if result.status != "PASS" and not result.reasons:
        raise ValueError("non-PASS probe requires a reason")
    if result.imputed_zero:
        raise ValueError("probe failure cannot be represented as an imputed zero")
    if result.status == "PASS":
        missing = []
        for check, keys in _REQUIRED_EVIDENCE.items():
            evidence = result.evidence.get(check)
            if not isinstance(evidence, Mapping) or any(
                not evidence.get(k) for k in keys
            ):
                missing.append(check)
        if result.evidence.get("zero_dte") is True:
            missing.append("zero_dte_rejection")
        if missing:
            raise ValueError(
                "PASS probe lacks eligibility evidence: " + ", ".join(missing)
            )
        if result.evidence.get("no_imputation") is not True:
            raise ValueError("PASS probe requires no_imputation=true")
        _validate_probe_evidence(result)
    return ProbeResult(
        result.ticker.upper(),
        day,
        expiry,
        result.dte,
        result.status,
        result.checks,
        tuple(result.reasons),
        result.imputed_zero,
        result.evidence,
    )


def _key(ticker: str, day: str, occurrence: int = 0) -> str:
    return f"{ticker}|{day}" + (f"#{occurrence}" if occurrence else "")


def build_manifest(
    candidates: Iterable[Mapping[str, Any]],
    *,
    held_pairs: Iterable[tuple[str, str]] = (),
    intended_units: int | None = None,
    sector_cap: float = 0.20,
    ticker_cap: float = 0.10,
    probe_results: Iterable[ProbeResult] = (),
    selection_date: str | None = None,
    source_list: str | None = None,
    calendar_snapshot: Any | None = None,
    as_of: str | None = None,
    window_policy: str = "OPEX_DAY",
) -> UniverseManifest:
    # Provenance is manifest metadata, never an optional display default.
    manifest_selection_date = _selection_date(selection_date)
    manifest_source_list = _source_list(source_list)
    rows = list(candidates)
    target = intended_units if intended_units is not None else len(rows)
    if target < 0:
        raise ValueError("intended_units cannot be negative")
    held = {(str(t).upper(), _date(d)) for t, d in held_pairs}

    def _row_requires_calendar(r: Mapping[str, Any]) -> bool:
        # A malformed calendar day must not abort the manifest build here;
        # it is excluded below via the normal per-row invalid-day handling.
        try:
            pair = (
                str(r.get("ticker", "")).upper(),
                _date(r.get("day", r.get("date"))),
            )
        except ValueError:
            return False
        return pair not in held

    if (
        rows
        and calendar_snapshot is None
        and any(_row_requires_calendar(r) for r in rows)
    ):
        raise ValueError(
            "calendar snapshot is required for acquisition-eligible manifest candidates"
        )
    validated_probes = tuple(
        sorted(
            (validate_probe_result(p) for p in probe_results),
            key=lambda p: (
                p.ticker,
                p.day,
                p.expiry,
                p.dte,
                p.status,
                tuple(p.reasons),
            ),
        )
    )
    by_key: defaultdict[tuple[str, str, str | None, int], list[ProbeResult]] = (
        defaultdict(list)
    )
    for p in validated_probes:
        by_key[(p.ticker, p.day, p.expiry, p.dte)].append(p)
    normalized = []
    exclusions: dict[str, str] = {}
    occurrences: Counter[tuple[str, str]] = Counter()
    for raw in rows:
        ticker = str(raw.get("ticker", "")).strip().lstrip("$").upper()
        if (
            raw.get("selection_date") is not None
            and _selection_date(raw["selection_date"]) != manifest_selection_date
        ):
            raise ValueError(
                "candidate selection_date conflicts with manifest provenance"
            )
        if (
            raw.get("source_list") is not None
            and _source_list(raw["source_list"]) != manifest_source_list
        ):
            raise ValueError("candidate source_list conflicts with manifest provenance")
        try:
            day = _date(raw.get("day", raw.get("date")))
        except ValueError:
            exclusions[f"{ticker}|invalid"] = "invalid_calendar_day"
            continue
        pair = (ticker, day)
        occurrence = occurrences[pair]
        occurrences[pair] += 1
        key = _key(ticker, day, occurrence)
        if pair in held:
            exclusions[key] = "held_ticker_day"
            continue
        try:
            dte = int(raw.get("dte", -1))
        except (TypeError, ValueError):
            exclusions[key] = "invalid_dte"
            continue
        if dte == 0:
            exclusions[key] = "zero_dte"
            continue
        if _stratum(dte) is None:
            exclusions[key] = "dte_outside_locked_strata"
            continue
        event = str(raw.get("event_habitat", "NONE")).upper()
        if event not in {"NONE", *EVENT_HABITATS, "DESCRIPTIVE-HABITAT"}:
            raise ValueError(f"unknown event habitat: {event}")
        expiry = raw.get("expiry")
        try:
            expiry = _date(expiry) if expiry is not None else None
        except ValueError:
            exclusions[key] = "invalid_expiry"
            continue
        # Expiry is part of probe identity.  Never admit a candidate by a
        # weaker ticker/day/DTE fallback when the candidate omitted expiry.
        matches = (
            by_key.get((ticker, day, expiry, dte), []) if expiry is not None else []
        )
        if len(matches) != 1:
            exclusions[key] = "missing_probe" if not matches else "ambiguous_probe"
            continue
        if matches[0].status != "PASS":
            exclusions[key] = f"probe_{matches[0].status.lower()}"
            continue
        try:
            binding = _validate_calendar_binding(
                matches[0].evidence.get("calendar_binding"),
                ticker=ticker,
                day=day,
                expiry=expiry,
                dte=dte,
            )
            if calendar_snapshot is None:
                raise ValueError("calendar snapshot is required for manifest binding")
            generated = calendar_for_probe(
                calendar_snapshot,
                ticker=ticker,
                calendar_day=day,
                expiry=expiry,
                dte=dte,
                as_of=as_of,
                window_policy=window_policy,
            )
            _validate_calendar_binding(
                generated,
                ticker=ticker,
                day=day,
                expiry=expiry,
                dte=dte,
                expected=binding,
                calendar_snapshot=calendar_snapshot,
            )
            if raw.get("calendar_binding") is not None:
                _validate_calendar_binding(
                    raw["calendar_binding"],
                    ticker=ticker,
                    day=day,
                    expiry=expiry,
                    dte=dte,
                    expected=binding,
                    calendar_snapshot=calendar_snapshot,
                )
        except (TypeError, ValueError, CalendarGapError) as exc:
            exclusions[key] = f"calendar_binding:{exc}"
            continue
        try:
            candidate = normalize_candidate(
                raw,
                selection_date=manifest_selection_date,
                source_list=manifest_source_list,
            )
        except ValueError as exc:
            if "conflicts with manifest provenance" in str(exc):
                raise
            exclusions[key] = f"invalid_candidate:{exc}"
            continue
        normalized.append((key, day, candidate, dte, event, binding))
    normalized.sort(key=lambda item: (item[1], item[2].ticker, item[3], item[0]))
    max_sector = max(1, int(target * sector_cap)) if target else 0
    max_ticker = max(1, int(target * ticker_cap)) if target else 0
    sector_counts: Counter[str] = Counter()
    ticker_counts: Counter[str] = Counter()
    units = []
    for key, day, candidate, dte, event, binding in normalized:
        if sector_counts[candidate.sector] >= max_sector:
            exclusions[key] = "sector_cap"
            continue
        if ticker_counts[candidate.ticker] >= max_ticker:
            exclusions[key] = "ticker_cap"
            continue
        sector_counts[candidate.sector] += 1
        ticker_counts[candidate.ticker] += 1
        units.append(
            ManifestUnit(
                candidate.ticker,
                day,
                candidate.sector,
                candidate.asset_type,
                dte,
                event,
                _stratum(dte),
                binding,
            )
        )
    quota_schema = {
        "dte_strata": [list(s) for s in DTE_STRATA],
        "event_habitats": list(EVENT_HABITATS),
        "event_habitat_target": 1 / 3,
        "control_target": 2 / 3,
        "sector_cap_fraction": sector_cap,
        "ticker_cap_fraction": ticker_cap,
        "max_sector_units": max_sector,
        "max_ticker_units": max_ticker,
        "event_surprise_required_for_causal_claim": True,
    }
    return UniverseManifest(
        manifest_selection_date,
        manifest_source_list,
        target,
        tuple(units),
        dict(sorted(exclusions.items())),
        quota_schema,
        tuple(sorted({u.day for u in units})),
        dict(sorted(sector_counts.items())),
        dict(sorted(ticker_counts.items())),
        validated_probes,
    )


__all__ = [
    "DTE_STRATA",
    "EVENT_HABITATS",
    "PROBE_CHECKS",
    "Candidate",
    "ManifestUnit",
    "ProbeResult",
    "UniverseManifest",
    "build_manifest",
    "held_pairs_from_paths",
    "normalize_candidate",
    "validate_probe_result",
]

if __name__ == "__main__":
    raise SystemExit("library module; no network acquisition is performed")
