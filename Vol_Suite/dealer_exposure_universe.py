"""Deterministic, network-free candidate-universe contracts for dealer exposure studies."""
from __future__ import annotations

import json
import datetime as dt
import math
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

DTE_STRATA = ((1, 3), (4, 7), (8, 10))
EVENT_HABITATS = ("FOMC", "EARNINGS", "OPEX")
PROBE_STATUSES = {"PASS", "INELIGIBLE", "HARD_GAP"}
CHECK_STATUSES = PROBE_STATUSES
PROBE_CHECKS = (
    "chain_listing", "historical_greeks_iv", "open_interest", "spot_ohlc",
    "timestamp_granularity", "expiry_dte", "post_window_returns",
    "spot_ohlc_coverage", "same_expiry_grid_oi_iv", "strike_side_moneyness",
    "strict_pre_window_ordering", "return_clocks", "no_imputation",
)
_REQUIRED_EVIDENCE = {
    "spot_ohlc_coverage": ("pre_window", "firing_window", "response_window", "return_clocks"),
    "same_expiry_grid_oi_iv": ("expiry", "grid", "oi", "iv"),
    "strike_side_moneyness": ("call_side", "put_side", "moneyness_band"),
    "strict_pre_window_ordering": ("pre_window_last", "breach_first", "strictly_before"),
    "return_clocks": ("daily", "from_breach"),
}
_REFERENCE_FAMILIES = {"SPY", "QQQ"}
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
_DATE_RE = re.compile(r"^\d{4}[-]?\d{2}[-]?\d{2}$")
_CLOCK_RE = re.compile(r"^(?:[1-9]\d*)(?:d|h|m)$", re.IGNORECASE)


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

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["dte_stratum"] = list(self.dte_stratum)
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
        return {"schema_version": 1, "selection_date": self.selection_date,
                "source_list": self.source_list, "intended_units": self.intended_units,
                "units": [u.to_dict() for u in self.units], "exclusions": dict(self.exclusions),
                "quota_schema": dict(self.quota_schema), "unique_days": list(self.unique_days),
                "sector_counts": dict(self.sector_counts), "ticker_counts": dict(self.ticker_counts),
                "probe_results": [p.to_dict() for p in self.probe_results]}


def normalize_candidate(raw: Mapping[str, Any], *, selection_date: str | None = None,
                        source_list: str | None = None) -> Candidate:
    ticker = str(raw.get("ticker", "")).strip().lstrip("$").upper()
    if not _TICKER_RE.fullmatch(ticker):
        raise ValueError(f"invalid ticker: {ticker!r}")
    if ticker in _REFERENCE_FAMILIES or raw.get("reference_family"):
        raise ValueError("SPY/QQQ are reference families, not expansion candidates")
    date = selection_date or raw.get("selection_date")
    source = source_list or raw.get("source_list")
    if not date or not source:
        raise ValueError("selection_date and source_list are required provenance")
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
                            manifests[Path(file_name).name] = (str(result["ticker"]), as_of)
                    if payload.get("ticker"):
                        manifests[path.name] = (str(payload["ticker"]), as_of)
                for obj in _json_objects(payload):
                    day = next((obj.get(k) for k in ("day", "date", "calendar_day", "trade_date", "as_of", "acquired_on") if obj.get(k) is not None), None)
                    tickers = obj.get("families") or obj.get("tickers")
                    if isinstance(tickers, str): tickers = [tickers]
                    if obj.get("ticker"): tickers = list(tickers or []) + [obj["ticker"]]
                    for ticker in tickers or []:
                        if day is not None: _add_pair(pairs, ticker, day)
                manifest = payload.get("manifest")
                if isinstance(manifest, Mapping) and manifest.get("as_of") and manifest.get("ticker"):
                    _add_pair(pairs, manifest["ticker"], manifest["as_of"])
            elif isinstance(payload, list):
                for obj in _json_objects(payload):
                    if isinstance(obj, Mapping) and obj.get("ticker") and obj.get("as_of"):
                        _add_pair(pairs, obj["ticker"], obj["as_of"])
        for path in files:
            match = re.search(r"seed_data_([A-Za-z0-9.-]+)_\d{8}", path.name, re.I)
            if not match: continue
            if path.name in manifests:
                _add_pair(pairs, *manifests[path.name])
                continue
            window = re.search(r"(?:^|[\\/])window_(\d{8})(?:[\\/]|$)", str(path.parent), re.I)
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
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)


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
        expected = {"ticker": result.ticker.upper(), "day": _date(result.day),
                    "expiry": _date(result.expiry), "dte": result.dte}
        actual = {"ticker": str(identity.get("ticker", "")).strip().upper(),
                  "day": _date(identity.get("day")),
                  "expiry": _date(identity.get("expiry")), "dte": identity.get("dte")}
        if actual != expected:
            raise ValueError("probe evidence identity does not match probe")
    spot = evidence["spot_ohlc_coverage"]
    # Coverage is evidence, not a set of truthy labels: every required window
    # must contain a timestamp and real positive spot/OHLC values.
    for window in ("pre_window", "firing_window", "response_window"):
        rows = _rows(spot[window], window)
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError(f"{window} evidence rows must be mappings with spot/OHLC values")
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
    strikes = [_number(v, positive=True) for v in _rows(grid.get("grid"), "strike grid")]
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
    if pre.date().isoformat() != _date(result.day) or breach.date().isoformat() != _date(result.day):
        raise ValueError("PRE_WINDOW/breach timestamps must be on probe day")

    clocks = evidence["return_clocks"]
    daily, from_breach = clocks.get("daily"), clocks.get("from_breach")
    if not (isinstance(daily, str) and isinstance(from_breach, str)
            and _CLOCK_RE.fullmatch(daily.strip()) and _CLOCK_RE.fullmatch(from_breach.strip())):
        raise ValueError("daily and from-breach clocks must be valid positive durations")
    if daily.strip().lower() == from_breach.strip().lower():
        raise ValueError("daily and from-breach clocks must be distinct")

    if evidence.get("no_imputation") is not True or evidence.get("zero_dte") is not False:
        raise ValueError("imputation markers and zero-DTE evidence must be explicitly clean")


def validate_probe_result(result: ProbeResult) -> ProbeResult:
    if result.status not in PROBE_STATUSES: raise ValueError(f"invalid probe status: {result.status}")
    if not _TICKER_RE.fullmatch(result.ticker.upper()): raise ValueError("invalid probe ticker")
    if result.dte <= 0 or _stratum(result.dte) is None: raise ValueError("probe DTE must be in the locked 1-10 strata")
    day, expiry = _date(result.day), _date(result.expiry)
    if (dt.date.fromisoformat(expiry) - dt.date.fromisoformat(day)).days != result.dte:
        raise ValueError("probe expiry and DTE do not match probe identity")
    if set(result.checks) != set(PROBE_CHECKS): raise ValueError("probe checks must contain exactly the locked checks")
    if any(v not in CHECK_STATUSES for v in result.checks.values()): raise ValueError("invalid check status")
    if result.status == "PASS" and any(v != "PASS" for v in result.checks.values()): raise ValueError("PASS probe requires every check to PASS")
    if result.status != "PASS" and not result.reasons: raise ValueError("non-PASS probe requires a reason")
    if result.imputed_zero: raise ValueError("probe failure cannot be represented as an imputed zero")
    if result.status == "PASS":
        missing = []
        for check, keys in _REQUIRED_EVIDENCE.items():
            evidence = result.evidence.get(check)
            if not isinstance(evidence, Mapping) or any(not evidence.get(k) for k in keys): missing.append(check)
        if result.evidence.get("zero_dte") is True: missing.append("zero_dte_rejection")
        if missing: raise ValueError("PASS probe lacks eligibility evidence: " + ", ".join(missing))
        if result.evidence.get("no_imputation") is not True: raise ValueError("PASS probe requires no_imputation=true")
        _validate_probe_evidence(result)
    return ProbeResult(result.ticker.upper(), day, expiry, result.dte, result.status, result.checks, tuple(result.reasons), result.imputed_zero, result.evidence)


def _key(ticker: str, day: str, occurrence: int = 0) -> str:
    return f"{ticker}|{day}" + (f"#{occurrence}" if occurrence else "")


def build_manifest(candidates: Iterable[Mapping[str, Any]], *, held_pairs: Iterable[tuple[str, str]] = (), intended_units: int | None = None,
                   sector_cap: float = 0.20, ticker_cap: float = 0.10, probe_results: Iterable[ProbeResult] = (),
                   selection_date: str = "", source_list: str = "point-in-time-static") -> UniverseManifest:
    rows = list(candidates); target = intended_units if intended_units is not None else len(rows)
    if target < 0: raise ValueError("intended_units cannot be negative")
    held = {(str(t).upper(), _date(d)) for t, d in held_pairs}
    validated_probes = tuple(sorted((validate_probe_result(p) for p in probe_results), key=lambda p: (p.ticker, p.day, p.expiry, p.dte, p.status, tuple(p.reasons))))
    by_key: defaultdict[tuple[str, str, str | None, int], list[ProbeResult]] = defaultdict(list)
    for p in validated_probes: by_key[(p.ticker, p.day, p.expiry, p.dte)].append(p)
    normalized = []; exclusions: dict[str, str] = {}; occurrences: Counter[tuple[str, str]] = Counter()
    for raw in rows:
        ticker = str(raw.get("ticker", "")).strip().lstrip("$").upper()
        try: day = _date(raw.get("day", raw.get("date")))
        except ValueError: exclusions[f"{ticker}|invalid"] = "invalid_calendar_day"; continue
        pair = (ticker, day); occurrence = occurrences[pair]; occurrences[pair] += 1; key = _key(ticker, day, occurrence)
        if pair in held: exclusions[key] = "held_ticker_day"; continue
        try: dte = int(raw.get("dte", -1))
        except (TypeError, ValueError): exclusions[key] = "invalid_dte"; continue
        if dte == 0: exclusions[key] = "zero_dte"; continue
        if _stratum(dte) is None: exclusions[key] = "dte_outside_locked_strata"; continue
        event = str(raw.get("event_habitat", "NONE")).upper()
        if event not in {"NONE", *EVENT_HABITATS, "DESCRIPTIVE-HABITAT"}: raise ValueError(f"unknown event habitat: {event}")
        expiry = raw.get("expiry")
        try: expiry = _date(expiry) if expiry is not None else None
        except ValueError: exclusions[key] = "invalid_expiry"; continue
        # Expiry is part of probe identity.  Never admit a candidate by a
        # weaker ticker/day/DTE fallback when the candidate omitted expiry.
        matches = by_key.get((ticker, day, expiry, dte), []) if expiry is not None else []
        if len(matches) != 1: exclusions[key] = "missing_probe" if not matches else "ambiguous_probe"; continue
        if matches[0].status != "PASS": exclusions[key] = f"probe_{matches[0].status.lower()}"; continue
        try: candidate = normalize_candidate(raw, selection_date=selection_date or None, source_list=source_list)
        except ValueError as exc: exclusions[key] = f"invalid_candidate:{exc}"; continue
        normalized.append((key, day, candidate, dte, event))
    normalized.sort(key=lambda item: (item[1], item[2].ticker, item[3], item[0]))
    max_sector = max(1, int(target * sector_cap)) if target else 0; max_ticker = max(1, int(target * ticker_cap)) if target else 0
    sector_counts: Counter[str] = Counter(); ticker_counts: Counter[str] = Counter(); units = []
    for key, day, candidate, dte, event in normalized:
        if sector_counts[candidate.sector] >= max_sector: exclusions[key] = "sector_cap"; continue
        if ticker_counts[candidate.ticker] >= max_ticker: exclusions[key] = "ticker_cap"; continue
        sector_counts[candidate.sector] += 1; ticker_counts[candidate.ticker] += 1
        units.append(ManifestUnit(candidate.ticker, day, candidate.sector, candidate.asset_type, dte, event, _stratum(dte)))
    quota_schema = {"dte_strata": [list(s) for s in DTE_STRATA], "event_habitats": list(EVENT_HABITATS), "event_habitat_target": 1 / 3, "control_target": 2 / 3, "sector_cap_fraction": sector_cap, "ticker_cap_fraction": ticker_cap, "max_sector_units": max_sector, "max_ticker_units": max_ticker, "event_surprise_required_for_causal_claim": True}
    return UniverseManifest(_date(selection_date) if selection_date else "", source_list, target, tuple(units), dict(sorted(exclusions.items())), quota_schema, tuple(sorted({u.day for u in units})), dict(sorted(sector_counts.items())), dict(sorted(ticker_counts.items())), validated_probes)


__all__ = ["Candidate", "ManifestUnit", "ProbeResult", "UniverseManifest", "DTE_STRATA", "EVENT_HABITATS", "PROBE_CHECKS", "normalize_candidate", "held_pairs_from_paths", "validate_probe_result", "build_manifest"]

if __name__ == "__main__": raise SystemExit("library module; no network acquisition is performed")
