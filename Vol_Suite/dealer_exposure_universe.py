"""Deterministic, network-free candidate-universe contracts for dealer exposure studies.

This module deliberately stops at eligibility and manifest construction.  It does not
fetch quotes, option chains, events, or sector data.  Callers must supply point-in-time
candidate metadata and probe results from an approved source.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

DTE_STRATA = ((1, 3), (4, 7), (8, 10))
EVENT_HABITATS = ("FOMC", "EARNINGS", "OPEX")
PROBE_CHECKS = (
    "chain_listing", "historical_greeks_iv", "open_interest", "spot_ohlc",
    "timestamp_granularity", "expiry_dte", "post_window_returns",
)
PROBE_STATUSES = {"PASS", "INELIGIBLE", "HARD_GAP"}
CHECK_STATUSES = PROBE_STATUSES
_REFERENCE_FAMILIES = {"SPY", "QQQ"}
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
_DATE_RE = re.compile(r"^\d{4}[-]?\d{2}[-]?\d{2}$")


def _date(value: Any) -> str:
    text = str(value).strip()
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    if not _DATE_RE.fullmatch(text):
        raise ValueError(f"invalid calendar day: {value!r}")
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker, "day": self.day, "expiry": self.expiry,
            "dte": self.dte, "status": self.status, "checks": dict(self.checks),
            "reasons": list(self.reasons), "imputed_zero": self.imputed_zero,
        }


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
    raw: Mapping[str, Any], *, selection_date: str | None = None,
    source_list: str | None = None,
) -> Candidate:
    """Normalize one static candidate without silently supplying provenance."""
    ticker = str(raw.get("ticker", "")).strip().lstrip("$").upper()
    if not _TICKER_RE.fullmatch(ticker):
        raise ValueError(f"invalid ticker: {ticker!r}")
    reference = raw.get("reference_family")
    if ticker in _REFERENCE_FAMILIES or reference:
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


def held_pairs_from_paths(paths: Iterable[str | Path]) -> set[tuple[str, str]]:
    """Read held ticker×day pairs from committed manifests and seed corpora only."""
    pairs: set[tuple[str, str]] = set()
    for root in sorted((Path(p) for p in paths), key=lambda p: str(p)):
        files = [root] if root.is_file() else sorted(root.rglob("*.json"))
        for path in files:
            match = re.search(r"seed_data_([A-Za-z0-9.-]+)_(\d{8})", path.name, re.I)
            if match:
                pairs.add((match.group(1).upper(), _date(match.group(2))))
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeDecodeError):
                continue
            for obj in _json_objects(payload):
                day = next((obj.get(k) for k in ("day", "date", "calendar_day", "trade_date") if obj.get(k) is not None), None)
                if day is None:
                    continue
                tickers = obj.get("families") or obj.get("tickers")
                if isinstance(tickers, str):
                    tickers = [tickers]
                if obj.get("ticker"):
                    tickers = list(tickers or []) + [obj["ticker"]]
                for ticker in tickers or []:
                    ticker = str(ticker).strip().upper()
                    if _TICKER_RE.fullmatch(ticker):
                        try:
                            pairs.add((ticker, _date(day)))
                        except ValueError:
                            pass
    return pairs


def validate_probe_result(result: ProbeResult) -> ProbeResult:
    if result.status not in PROBE_STATUSES:
        raise ValueError(f"invalid probe status: {result.status}")
    if not _TICKER_RE.fullmatch(result.ticker.upper()):
        raise ValueError("invalid probe ticker")
    if result.dte <= 0 or _stratum(result.dte) is None:
        raise ValueError("probe DTE must be in the locked 1-10 strata")
    _date(result.day)
    _date(result.expiry)
    if set(result.checks) != set(PROBE_CHECKS):
        raise ValueError("probe checks must contain exactly the locked checks")
    if any(value not in CHECK_STATUSES for value in result.checks.values()):
        raise ValueError("invalid check status")
    if result.status == "PASS" and any(value != "PASS" for value in result.checks.values()):
        raise ValueError("PASS probe requires every check to PASS")
    if result.status != "PASS" and not result.reasons:
        raise ValueError("non-PASS probe requires a reason")
    if result.imputed_zero:
        raise ValueError("probe failure cannot be represented as an imputed zero")
    return result


def _key(ticker: str, day: str, occurrence: int = 0) -> str:
    return f"{ticker}|{day}" + (f"#{occurrence}" if occurrence else "")


def build_manifest(
    candidates: Iterable[Mapping[str, Any]], *, held_pairs: Iterable[tuple[str, str]] = (),
    intended_units: int | None = None, sector_cap: float = 0.20,
    ticker_cap: float = 0.10, probe_results: Iterable[ProbeResult] = (),
    selection_date: str = "", source_list: str = "point-in-time-static",
) -> UniverseManifest:
    rows = list(candidates)
    target = intended_units if intended_units is not None else len(rows)
    if target < 0:
        raise ValueError("intended_units cannot be negative")
    held = {(str(t).upper(), _date(d)) for t, d in held_pairs}
    normalized: list[tuple[str, str, Candidate, int, str]] = []
    exclusions: dict[str, str] = {}
    occurrences: Counter[tuple[str, str]] = Counter()
    for raw in rows:
        ticker = str(raw.get("ticker", "")).strip().lstrip("$").upper()
        day = _date(raw.get("day", raw.get("date")))
        pair = (ticker, day)
        occurrence = occurrences[pair]
        occurrences[pair] += 1
        key = _key(ticker, day, occurrence)
        if pair in held:
            exclusions[key] = "held_ticker_day"
            continue
        dte = int(raw.get("dte", -1))
        if dte == 0:
            exclusions[key] = "zero_dte"
            continue
        if _stratum(dte) is None:
            exclusions[key] = "dte_outside_locked_strata"
            continue
        event = str(raw.get("event_habitat", "NONE")).upper()
        if event not in {"NONE", *EVENT_HABITATS, "DESCRIPTIVE-HABITAT"}:
            raise ValueError(f"unknown event habitat: {event}")
        candidate = normalize_candidate(raw, selection_date=selection_date or None, source_list=source_list)
        normalized.append((key, day, candidate, dte, event))
    normalized.sort(key=lambda item: (item[1], item[2].ticker, item[3], item[0]))
    max_sector = max(1, int(target * sector_cap)) if target else 0
    max_ticker = max(1, int(target * ticker_cap)) if target else 0
    sector_counts: Counter[str] = Counter()
    ticker_counts: Counter[str] = Counter()
    units: list[ManifestUnit] = []
    for key, day, candidate, dte, event in normalized:
        if sector_counts[candidate.sector] >= max_sector:
            exclusions[key] = "sector_cap"
            continue
        if ticker_counts[candidate.ticker] >= max_ticker:
            exclusions[key] = "ticker_cap"
            continue
        sector_counts[candidate.sector] += 1
        ticker_counts[candidate.ticker] += 1
        units.append(ManifestUnit(candidate.ticker, day, candidate.sector, candidate.asset_type, dte, event, _stratum(dte)))
    validated_probes = tuple(validate_probe_result(p) for p in probe_results)
    quota_schema = {
        "dte_strata": [list(s) for s in DTE_STRATA], "event_habitats": list(EVENT_HABITATS),
        "event_habitat_target": 1 / 3, "control_target": 2 / 3,
        "sector_cap_fraction": sector_cap, "ticker_cap_fraction": ticker_cap,
        "max_sector_units": max_sector, "max_ticker_units": max_ticker,
        "event_surprise_required_for_causal_claim": True,
    }
    return UniverseManifest(
        _date(selection_date) if selection_date else "", source_list, target,
        tuple(units), dict(sorted(exclusions.items())), quota_schema,
        tuple(sorted({u.day for u in units})), dict(sorted(sector_counts.items())),
        dict(sorted(ticker_counts.items())), validated_probes,
    )


__all__ = ["Candidate", "ManifestUnit", "ProbeResult", "UniverseManifest", "DTE_STRATA", "EVENT_HABITATS", "PROBE_CHECKS", "normalize_candidate", "held_pairs_from_paths", "validate_probe_result", "build_manifest"]


if __name__ == "__main__":
    raise SystemExit("library module; no network acquisition is performed")
