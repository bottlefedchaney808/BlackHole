"""Fail-closed, common-input live-vs-expiry-book comparison.

This module is intentionally an offline harness. Acquisition is out of scope:
callers provide one canonical snapshot and may inject engine adapters in tests.
Adapters receive one immutable byte payload and must attest to the digest of the
bytes actually consumed.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


@dataclass(frozen=True)
class CanonicalRow:
    strike: float
    right: str
    iv: float
    oi: int

    def as_dict(self) -> dict[str, Any]:
        return {"strike": self.strike, "right": self.right, "iv": self.iv, "oi": self.oi}


@dataclass(frozen=True)
class CanonicalInput:
    ticker: str
    calendar_day: str
    expiry: str
    dte: int
    spot: float
    iv_source_ts: str
    rows: tuple[CanonicalRow, ...]
    source_hashes: tuple[str, ...]
    chain_source: str = "offline-canonical"

    def __post_init__(self) -> None:
        if not self.ticker or not self.expiry or not self.calendar_day:
            raise ValueError("ticker, calendar_day, and expiry are required")
        if self.dte <= 0 or self.spot <= 0 or not self.rows:
            raise ValueError("positive DTE/spot and at least one row are required")
        if len({(r.strike, r.right) for r in self.rows}) != len(self.rows):
            raise ValueError("duplicate strike/right rows are not canonical")
        if any(r.right not in {"C", "P"} or r.iv <= 0 or r.oi < 0 for r in self.rows):
            raise ValueError("rows contain invalid right, IV, or OI")
        if not self.source_hashes or any(not re.fullmatch(r"[0-9a-fA-F]{64}", str(item)) for item in self.source_hashes):
            raise ValueError("source_hashes must contain 64-character hexadecimal SHA-256 hashes")
        if not isinstance(self.iv_source_ts, str) or not self.iv_source_ts.endswith("Z") and "+" not in self.iv_source_ts and "-" not in self.iv_source_ts[10:]:
            raise ValueError("iv_source_ts must be timezone-qualified ISO-8601")
        import datetime as dt
        try:
            dt.datetime.fromisoformat(self.iv_source_ts[:-1] + "+00:00" if self.iv_source_ts.endswith("Z") else self.iv_source_ts)
        except ValueError as exc:
            raise ValueError("iv_source_ts must be a valid ISO-8601 timestamp") from exc

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["rows"] = [r.as_dict() for r in self.rows]
        result["source_hashes"] = list(self.source_hashes)
        return result

    def canonical_bytes(self) -> bytes:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":")).encode()

    @property
    def input_hash(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


class CanonicalPayload:
    """Opaque canonical bytes with one harness-controlled read operation.

    The input record and backing bytes are deliberately not exposed.  ``read``
    returns a fresh immutable copy and records the exact bytes returned.  The
    harness, not an adapter result, owns the resulting attestation.
    """

    __slots__ = ("__read_bytes", "__reader")

    def __init__(self, data: bytes) -> None:
        canonical = bytes(data)
        self.__reader = lambda: canonical
        self.__read_bytes: list[bytes] = []

    def read(self) -> bytes:
        """Read the complete canonical payload and record the returned bytes."""
        returned = bytes(self.__reader())
        self.__read_bytes.append(returned)
        return returned

    @property
    def read_sha256(self) -> str | None:
        """Harness-owned digest of the bytes actually returned by ``read``."""
        if not self.__read_bytes:
            return None
        return hashlib.sha256(b"".join(self.__read_bytes)).hexdigest()

    @property
    def read_bytes(self) -> tuple[bytes, ...]:
        """Read audit data for the harness; adapters must not use this metadata."""
        return tuple(self.__read_bytes)


class ComparisonInvalid(ValueError):
    """The comparison cannot support a better/worse/descriptive conclusion."""

    def __init__(self, reason: str, *, exclusions: Sequence[Mapping[str, Any]] = (),
                 causal_blocked: bool = False) -> None:
        self.invalid_result = {"status": "COMPARISON_INVALID" if causal_blocked else "INVALID",
                               "causal_status": "CAUSAL_BLOCKED" if causal_blocked else None,
                               "reason": reason,
                               "exclusions": [dict(item) for item in exclusions]}
        super().__init__(reason)


def make_canonical_input(
    ticker: str, calendar_day: str, expiry: str, dte: int, spot: float,
    iv_source_ts: str, rows: Iterable[Mapping[str, Any]],
    source_hashes: Iterable[str], chain_source: str = "offline-canonical",
) -> CanonicalInput:
    canonical_rows = tuple(sorted(
        (CanonicalRow(float(r["strike"]), r["right"],
                      float(r.get("iv", r.get("implied_vol"))), int(r["oi"]))
         for r in rows), key=lambda r: (r.strike, r.right)))
    return CanonicalInput(ticker, calendar_day, expiry, int(dte), float(spot),
                          iv_source_ts, canonical_rows, tuple(sorted(source_hashes)),
                          chain_source)


def _pearson(a: Sequence[float], b: Sequence[float]) -> float | None:
    if len(a) < 2:
        return None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    return (sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (da * db)
            if da and db else None)


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            out[order[k]] = rank
        i = j + 1
    return out


def _deadband(v: float, eps: float) -> int:
    return 0 if abs(v) <= eps else (1 if v > 0 else -1)


def _classify(live: float, new: float, eps: float) -> str:
    ls, ns = _deadband(live, eps), _deadband(new, eps)
    if ls == 0 and ns == 0:
        return "zero"
    if ls == 0 or ns == 0:
        return "zero-vs-nonzero"
    return "same" if ls == ns else "opposite"


def _row_key(row: Any) -> tuple[float, str]:
    return float(row.strike), row.right


def _coverage(rows: Iterable[Any], inp: CanonicalInput, engine: str, value_fn: Callable[[Any], float],
              parent_expiry: str | None = None) -> tuple[dict[tuple[float, str], float], list[dict[str, Any]]]:
    levels: dict[tuple[float, str], float] = {}
    exclusions: list[dict[str, Any]] = []
    expected = {(r.strike, r.right) for r in inp.rows}
    for row in rows:
        try:
            key = _row_key(row)
        except (AttributeError, TypeError, ValueError) as exc:
            exclusions.append({"engine": engine, "key": None, "expiry": getattr(row, "expiry", None) or parent_expiry,
                               "reason": f"invalid output record: malformed strike/right row ({exc})"})
            continue
        expiry = getattr(row, "expiry", None) or parent_expiry
        if key[1] not in {"C", "P"}:
            exclusions.append({"engine": engine, "key": list(key), "expiry": expiry,
                               "reason": "malformed right; expected exact C or P"})
            continue
        if expiry != inp.expiry:
            exclusions.append({"engine": engine, "key": list(key), "expiry": expiry,
                               "reason": "wrong expiry"})
            continue
        if key not in expected:
            exclusions.append({"engine": engine, "key": list(key), "expiry": expiry,
                               "reason": "extra strike/right key"})
            continue
        if key in levels:
            exclusions.append({"engine": engine, "key": list(key), "expiry": expiry,
                               "reason": "duplicate strike/right row"})
            continue
        try:
            levels[key] = value_fn(row)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            exclusions.append({"engine": engine, "key": list(key), "expiry": expiry,
                               "reason": f"invalid output record: {exc}"})
    missing = sorted(expected - set(levels))
    exclusions.extend({"engine": engine, "key": list(key), "reason": "missing strike/right key"}
                      for key in missing)
    return levels, exclusions


def _new_vanna_level(row: Any, inp: CanonicalInput) -> float:
    """Validate rec.vanna against an independently computed BS invariant."""
    sigma = float(row.iv)
    T = float(row.T)
    d1 = (math.log(inp.spot / float(row.strike)) + (0.05 + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    expected = -math.exp(0.0) * math.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi) * d2 / sigma
    actual = float(row.greeks["vanna"])
    if not math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-12):
        raise ValueError("rec.vanna is not -1xBS")
    return float(row.exposure_of("vanna"))


def _new_levels(engine_result: Any, inp: CanonicalInput) -> tuple[dict[tuple[float, str], float], list[dict[str, Any]]]:
    return _coverage(engine_result.rows, inp, "new", lambda row: _new_vanna_level(row, inp),
                     parent_expiry=getattr(engine_result, "expiry", None))


def _live_levels(engine_result: Any, inp: CanonicalInput) -> tuple[dict[tuple[float, str], float], list[dict[str, Any]]]:
    return _coverage(
        engine_result.gamma_records, inp, "live",
        lambda row: float(row.vanna) * float(row.oi) * 100.0 * 0.01 * float(row.applied_sign),
    )


def _default_live_runner(payload: CanonicalPayload) -> Any:
    """Run locked live code against canonical rows, with no network."""
    from unittest.mock import patch

    import dealer_positioning as dp

    inp_dict = json.loads(payload.read())
    inp = CanonicalInput(
        inp_dict["ticker"], inp_dict["calendar_day"], inp_dict["expiry"], inp_dict["dte"],
        inp_dict["spot"], inp_dict["iv_source_ts"],
        tuple(CanonicalRow(**row) for row in inp_dict["rows"]),
        tuple(inp_dict["source_hashes"]), inp_dict["chain_source"],
    )
    class SeedController:
        def fetch_spot_price(self, ticker: str) -> float: return inp.spot
        def fetch_dividend_yield(self, ticker: str, spot: float | None = None) -> float: return 0.0
        def fetch_risk_free_rate(self, T: float) -> float: return 0.04
        def list_expirations(self, root: str) -> list[str]: return [inp.expiry]
        def option_bulk_greeks(self, root: str, exp: str) -> list[dict[str, Any]]:
            return [{"strike": int(r.strike * 1000), "right": r.right,
                     "implied_vol": r.iv, "gamma": 0.01, "delta": 0.1,
                     "vanna": -0.02, "charm": 0.01, "bid": 1.0, "ask": 1.1,
                     "expiry": inp.expiry} for r in inp.rows]
        def option_bulk_oi(self, root: str, exp: str) -> list[dict[str, Any]]:
            return [{"strike": int(r.strike * 1000), "right": r.right,
                     "open_interest": r.oi, "expiry": inp.expiry} for r in inp.rows]
        def close(self) -> None: pass

    hist_g = [{"date": inp.calendar_day, "strike": int(r.strike * 1000),
               "right": r.right, "implied_vol": r.iv, "vanna": -0.02}
              for r in inp.rows]
    hist_oi = [{"date": inp.calendar_day, "strike": int(r.strike * 1000),
                "right": r.right, "open_interest": r.oi} for r in inp.rows]
    hist_spot = [{"date": inp.calendar_day, "close": inp.spot}]
    with patch.object(dp, "ThetaDataController", SeedController):
        result = dp.compute_dealer_positioning(
            inp.ticker, target_years=inp.dte / 365, expiration=inp.expiry,
            sign_model="vol_surface_replication", accumulate=True,
            _accumulation_hist_rows=(hist_g, hist_oi, hist_spot))
    if result.sign_model != "vol_surface_replication" or not result.accumulate:
        raise ComparisonInvalid("live accumulation fell back or identity changed")
    return result


def _default_new_runner(payload: CanonicalPayload) -> Any:
    import expiry_book_exposure as ebe
    inp_dict = json.loads(payload.read())
    inp = CanonicalInput(
        inp_dict["ticker"], inp_dict["calendar_day"], inp_dict["expiry"], inp_dict["dte"],
        inp_dict["spot"], inp_dict["iv_source_ts"],
        tuple(CanonicalRow(**row) for row in inp_dict["rows"]),
        tuple(inp_dict["source_hashes"]), inp_dict["chain_source"],
    )
    result = ebe.build_net_exposure(
        [{"strike": r.strike, "right": r.right, "oi": r.oi, "implied_vol": r.iv,
          "expiry": inp.expiry} for r in inp.rows], inp.spot, ticker=inp.ticker,
        expiry=inp.expiry, T=inp.dte / 365.0, dte=inp.dte)
    return result


def _validate_source_hashes(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value or any(not re.fullmatch(r"[0-9a-fA-F]{64}", str(item)) for item in value):
        raise ValueError("source_hashes must contain non-empty SHA-256 hashes")
    return tuple(str(item).lower() for item in value)


def validate_causal_eligibility(units: Iterable[Mapping[str, Any]], *, intended_units: int | None = None) -> dict[str, Any]:
    """Validate the strict, all-unit causal provenance boundary.

    This is deliberately an adapter-boundary gate: it validates supplied
    provenance evidence and never claims to prove arbitrary code is honest.
    """
    rows = [dict(unit) for unit in units]
    expected = len(rows) if intended_units is None else intended_units
    reasons: list[dict[str, Any]] = []
    if expected < 0 or len(rows) != expected:
        reasons.append({"reason": "unit coverage is not complete", "n": len(rows), "N": expected})
    for unit in rows:
        try:
            if unit.get("status") != "PASS":
                raise ValueError(f"status={unit.get('status')}")
            if str(unit.get("pre_window_provenance", "")).upper() != "PRE_WINDOW":
                raise ValueError("provenance is not PRE_WINDOW")
            if unit.get("pre_window_value") is None or unit.get("delta_iv_pre_window") is None:
                raise ValueError("delta_iv_pre_window is missing")
            _validate_source_hashes(unit.get("source_hashes", [unit.get("raw_payload_hash")]))
            source = _parse_timestamp(unit.get("iv_source_ts"))
            breach = _parse_timestamp(unit.get("breach_window_start_prov"))
            if source >= breach:
                raise ValueError("iv_source_ts must strictly precede breach")
        except (TypeError, ValueError) as exc:
            reasons.append({"ticker": unit.get("ticker"), "calendar_day": unit.get("calendar_day"), "reason": str(exc)})
    return {"causal_status": "CAUSAL_ELIGIBLE" if not reasons else "CAUSAL_BLOCKED",
            "status": "VALID" if not reasons else "COMPARISON_INVALID", "n": len(rows), "N": expected,
            "coverage": len(rows) / expected if expected else 1.0, "reasons": reasons}


def _parse_timestamp(value: Any) -> Any:
    if not isinstance(value, str) or not value.endswith(("Z",)) and not any(value.endswith(f"{sign}{hour:02d}:00") for sign in "+-" for hour in range(24)):
        raise ValueError("timestamp must be timezone-qualified ISO-8601")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    import datetime as dt
    return dt.datetime.fromisoformat(text).astimezone(dt.UTC)


def compare_common_input(
    inp: CanonicalInput,
    live_runner: Callable[[CanonicalPayload], Any] | None = None,
    new_runner: Callable[[CanonicalPayload], Any] | None = None,
    deadband: float = 1e-12,
    provenance_units: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run both adapters with one immutable serialization and return diagnostics."""
    if provenance_units is not None:
        causal = validate_causal_eligibility(provenance_units)
        if causal["causal_status"] != "CAUSAL_ELIGIBLE":
            raise ComparisonInvalid("causal provenance is incomplete", exclusions=causal["reasons"], causal_blocked=True)
    live_runner = live_runner or _default_live_runner
    new_runner = new_runner or _default_new_runner
    canonical_bytes = inp.canonical_bytes()
    live_payload = CanonicalPayload(canonical_bytes)
    new_payload = CanonicalPayload(canonical_bytes)
    live = live_runner(live_payload)
    live_attestation = live_payload.read_sha256
    new = new_runner(new_payload)
    new_attestation = new_payload.read_sha256
    canonical_digest = hashlib.sha256(canonical_bytes).hexdigest()
    if live_attestation != canonical_digest or new_attestation != canonical_digest:
        raise ComparisonInvalid(
            "adapter did not consume canonical payload bytes",
            exclusions=[{"engine": name, "consumed_sha256": digest,
                         "canonical_sha256": canonical_digest,
                         "reason": "canonical payload consumption not observed"}
                        for name, digest in (("live", live_attestation), ("new", new_attestation))
                        if digest != canonical_digest],
        )
    if getattr(live, "sign_model", None) != "vol_surface_replication" or not getattr(live, "accumulate", False):
        raise ComparisonInvalid("live identity/actual accumulation assertion failed")
    live_levels, live_exclusions = _live_levels(live, inp)
    new_levels, new_exclusions = _new_levels(new, inp)
    exclusions = live_exclusions + new_exclusions
    if exclusions:
        raise ComparisonInvalid("common-input coverage is not exactly the canonical key set",
                                exclusions=exclusions)
    keys = sorted(set(live_levels) | set(new_levels))
    pairs = []
    for key in keys:
        lv, nv = live_levels[key], new_levels[key]
        pairs.append({"strike": key[0], "right": key[1], "live_vanna_level": lv,
                      "new_vanna_level": nv, "live_units": "shares/vol-pt",
                      "new_units": "shares/vol-pt", "class": _classify(lv, nv, deadband),
                      "live_sign_source": "accumulated", "new_sign_source": "-1xBS"})
    lv = [p["live_vanna_level"] for p in pairs]
    nv = [p["new_vanna_level"] for p in pairs]
    denom = sum(abs(x) + abs(y) for x, y in zip(lv, nv))
    d_conv = sum(abs(x - y) for x, y in zip(lv, nv)) / denom if denom else 0.0
    return {"status": "VALID", "input_hash": canonical_digest,
            "coverage": {"live": len(live_levels), "new": len(new_levels),
                          "common": len(keys), "total": len(inp.rows)},
            "pairs": pairs,
            "aggregate": {"live_vanna_level": sum(lv), "new_vanna_level": sum(nv),
                           "absolute_error": sum(abs(x - y) for x, y in zip(lv, nv)),
                           "pearson": _pearson(lv, nv), "spearman": _pearson(_ranks(lv), _ranks(nv)),
                           "sign_agreement": sum(p["class"] == "same" for p in pairs) / len(pairs),
                           "D_conv": d_conv, "deadband": deadband,
                           "comparison": "levels-only; live vanna level vs new net vanna level"},
            "config": {"sign_model": "vol_surface_replication", "accumulate": True,
                       "new_vanna": "rec.vanna=-1xBS", "flow_compared": False,
                       "canonical_sha256": canonical_digest}}


def write_deterministic_artifact(path: str, comparison: Mapping[str, Any]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(comparison, handle, sort_keys=True, indent=2)
        handle.write("\n")


__all__ = ["CanonicalInput", "CanonicalPayload", "CanonicalRow", "ComparisonInvalid",
           "compare_common_input", "make_canonical_input", "validate_causal_eligibility",
           "write_deterministic_artifact"]
