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
        if not self.source_hashes:
            raise ValueError("raw source hashes are required")

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


@dataclass(frozen=True)
class CanonicalPayload:
    """The one serialized payload supplied to each engine adapter."""

    data: bytes
    digest: str
    canonical_input: CanonicalInput

    @classmethod
    def from_input(cls, inp: CanonicalInput, data: bytes) -> CanonicalPayload:
        return cls(data=data, digest=hashlib.sha256(data).hexdigest(), canonical_input=inp)


class ComparisonInvalid(ValueError):
    """The comparison cannot support a better/worse/descriptive conclusion."""

    def __init__(self, reason: str, *, exclusions: Sequence[Mapping[str, Any]] = ()) -> None:
        self.invalid_result = {"status": "INVALID", "reason": reason,
                               "exclusions": [dict(item) for item in exclusions]}
        super().__init__(reason)


def make_canonical_input(
    ticker: str, calendar_day: str, expiry: str, dte: int, spot: float,
    iv_source_ts: str, rows: Iterable[Mapping[str, Any]],
    source_hashes: Iterable[str], chain_source: str = "offline-canonical",
) -> CanonicalInput:
    canonical_rows = tuple(sorted(
        (CanonicalRow(float(r["strike"]), str(r["right"]).upper()[:1],
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
    return float(row.strike), str(row.right).upper()[:1]


def _coverage(rows: Iterable[Any], inp: CanonicalInput, engine: str, value_fn: Callable[[Any], float],
              parent_expiry: str | None = None) -> tuple[dict[tuple[float, str], float], list[dict[str, Any]]]:
    levels: dict[tuple[float, str], float] = {}
    exclusions: list[dict[str, Any]] = []
    expected = {(r.strike, r.right) for r in inp.rows}
    for row in rows:
        key = _row_key(row)
        expiry = getattr(row, "expiry", None) or parent_expiry
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
        levels[key] = value_fn(row)
    missing = sorted(expected - set(levels))
    exclusions.extend({"engine": engine, "key": list(key), "reason": "missing strike/right key"}
                      for key in missing)
    return levels, exclusions


def _new_levels(engine_result: Any, inp: CanonicalInput) -> tuple[dict[tuple[float, str], float], list[dict[str, Any]]]:
    return _coverage(engine_result.rows, inp, "new", lambda row: float(row.exposure_of("vanna")),
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

    inp = payload.canonical_input
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
    result.consumed_input_sha256 = payload.digest
    if result.sign_model != "vol_surface_replication" or not result.accumulate:
        raise ComparisonInvalid("live accumulation fell back or identity changed")
    return result


def _default_new_runner(payload: CanonicalPayload) -> Any:
    import expiry_book_exposure as ebe
    inp = payload.canonical_input
    result = ebe.build_net_exposure(
        [{"strike": r.strike, "right": r.right, "oi": r.oi, "implied_vol": r.iv,
          "expiry": inp.expiry} for r in inp.rows], inp.spot, ticker=inp.ticker,
        expiry=inp.expiry, T=inp.dte / 365.0, dte=inp.dte)
    result.consumed_input_sha256 = payload.digest
    return result


def compare_common_input(
    inp: CanonicalInput,
    live_runner: Callable[[CanonicalPayload], Any] | None = None,
    new_runner: Callable[[CanonicalPayload], Any] | None = None,
    deadband: float = 1e-12,
) -> dict[str, Any]:
    """Run both adapters with one immutable serialization and return diagnostics."""
    live_runner = live_runner or _default_live_runner
    new_runner = new_runner or _default_new_runner
    canonical_bytes = inp.canonical_bytes()
    payload = CanonicalPayload.from_input(inp, canonical_bytes)
    live = live_runner(payload)
    new = new_runner(payload)
    consumed = [getattr(live, "consumed_input_sha256", None),
                getattr(new, "consumed_input_sha256", None)]
    if consumed != [payload.digest, payload.digest]:
        raise ComparisonInvalid("adapter consumed-input digest does not match canonical hash",
                                exclusions=[{"engine": name, "consumed_sha256": digest,
                                             "canonical_sha256": payload.digest,
                                             "reason": "canonical payload attestation failed"}
                                            for name, digest in zip(("live", "new"), consumed)
                                            if digest != payload.digest])
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
    return {"status": "VALID", "input_hash": payload.digest,
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
                       "canonical_sha256": payload.digest}}


def write_deterministic_artifact(path: str, comparison: Mapping[str, Any]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(comparison, handle, sort_keys=True, indent=2)
        handle.write("\n")


__all__ = ["CanonicalInput", "CanonicalPayload", "CanonicalRow", "ComparisonInvalid",
           "compare_common_input", "make_canonical_input", "write_deterministic_artifact"]
