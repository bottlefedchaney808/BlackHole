"""Fail-closed, common-input live-vs-expiry-book comparison.

This module is intentionally an offline harness.  Acquisition is out of scope:
callers provide one canonical snapshot and may inject engine runners in tests.
The default live adapter still calls the locked engine with explicit flags; it
never turns a same-day fallback into comparison evidence.
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


class ComparisonInvalid(ValueError):
    """The comparison cannot support a better/worse/descriptive conclusion."""


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


def _canonical_rows(inp: CanonicalInput) -> list[dict[str, Any]]:
    return [r.as_dict() for r in inp.rows]


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


def _new_levels(engine_result: Any) -> dict[tuple[float, str], float]:
    levels: dict[tuple[float, str], float] = {}
    for row in engine_result.rows:
        levels[(float(row.strike), str(row.right).upper()[:1])] = float(row.exposure_of("vanna"))
    return levels


def _live_levels(engine_result: Any) -> dict[tuple[float, str], float]:
    # gamma_records preserve the exact right key and the applied sign.  This is
    # the live vanna *level*, not vanna_flow and not a delta-IV multiplier.
    return {(float(r.strike), str(r.right).upper()[:1]):
            float(r.vanna) * float(r.oi) * 100.0 * 0.01 * float(r.applied_sign)
            for r in engine_result.gamma_records
            if not math.isnan(float(r.vanna))}


def _default_live_runner(inp: CanonicalInput) -> Any:
    """Run locked live code against canonical rows, with no network."""
    from unittest.mock import patch

    import dealer_positioning as dp

    class SeedController:
        def fetch_spot_price(self, ticker: str) -> float: return inp.spot
        def fetch_dividend_yield(self, ticker: str, spot: float | None = None) -> float: return 0.0
        def fetch_risk_free_rate(self, T: float) -> float: return 0.04
        def list_expirations(self, root: str) -> list[str]: return [inp.expiry]
        def option_bulk_greeks(self, root: str, exp: str) -> list[dict[str, Any]]:
            return [{"strike": int(r.strike * 1000), "right": r.right,
                     "implied_vol": r.iv, "gamma": 0.01, "delta": 0.1,
                     "vanna": -0.02, "charm": 0.01, "bid": 1.0, "ask": 1.1}
                    for r in inp.rows]
        def option_bulk_oi(self, root: str, exp: str) -> list[dict[str, Any]]:
            return [{"strike": int(r.strike * 1000), "right": r.right,
                     "open_interest": r.oi} for r in inp.rows]
        def close(self) -> None: pass

    hist_g = [{"date": inp.calendar_day, "strike": int(r.strike * 1000),
               "right": r.right, "implied_vol": r.iv, "vanna": -0.02}
              for r in inp.rows]
    hist_oi = [{"date": inp.calendar_day, "strike": int(r.strike * 1000),
                "right": r.right, "open_interest": r.oi} for r in inp.rows]
    hist_spot = [{"date": inp.calendar_day, "close": inp.spot}]
    # The production function is called with explicit locked identity and ON.
    with patch.object(dp, "ThetaDataController", SeedController):
        result = dp.compute_dealer_positioning(
            inp.ticker, target_years=inp.dte / 365, expiration=inp.expiry,
            sign_model="vol_surface_replication", accumulate=True,
            _accumulation_hist_rows=(hist_g, hist_oi, hist_spot))
    if result.sign_model != "vol_surface_replication" or not result.accumulate:
        raise ComparisonInvalid("live accumulation fell back or identity changed")
    return result


def _default_new_runner(inp: CanonicalInput) -> Any:
    import expiry_book_exposure as ebe
    return ebe.build_net_exposure(
        [{"strike": r.strike, "right": r.right, "oi": r.oi, "implied_vol": r.iv}
         for r in inp.rows], inp.spot, ticker=inp.ticker, expiry=inp.expiry,
        T=inp.dte / 365.0, dte=inp.dte)


def compare_common_input(
    inp: CanonicalInput,
    live_runner: Callable[[CanonicalInput], Any] | None = None,
    new_runner: Callable[[CanonicalInput], Any] | None = None,
    deadband: float = 1e-12,
) -> dict[str, Any]:
    """Run both engines and return deterministic levels-only diagnostics."""
    live_runner = live_runner or _default_live_runner
    new_runner = new_runner or _default_new_runner
    # Pass the same immutable object and assert each adapter received its exact bytes.
    expected = inp.canonical_bytes()
    seen: list[bytes] = []
    def invoke(runner: Callable[[CanonicalInput], Any]) -> Any:
        result = runner(inp)
        seen.append(inp.canonical_bytes())
        return result
    live = invoke(live_runner)
    new = invoke(new_runner)
    if seen != [expected, expected]:
        raise ComparisonInvalid("engines did not receive byte-identical canonical input")
    if getattr(live, "sign_model", None) != "vol_surface_replication" or not getattr(live, "accumulate", False):
        raise ComparisonInvalid("live identity/actual accumulation assertion failed")
    live_levels, new_levels = _live_levels(live), _new_levels(new)
    keys = sorted(set(live_levels) | set(new_levels))
    common = set(live_levels) & set(new_levels)
    if common != set(keys):
        raise ComparisonInvalid("headline comparison requires 100% strike/right coverage")
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
    return {"status": "VALID", "input_hash": inp.input_hash,
            "coverage": {"live": len(live_levels), "new": len(new_levels),
                          "common": len(common), "total": len(keys)},
            "pairs": pairs,
            "aggregate": {"live_vanna_level": sum(lv), "new_vanna_level": sum(nv),
                           "absolute_error": sum(abs(x - y) for x, y in zip(lv, nv)),
                           "pearson": _pearson(lv, nv),
                           "spearman": _pearson(_ranks(lv), _ranks(nv)),
                           "sign_agreement": sum(p["class"] == "same" for p in pairs) / len(pairs),
                           "D_conv": d_conv, "deadband": deadband,
                           "comparison": "levels-only; live vanna level vs new net vanna level"},
            "config": {"sign_model": "vol_surface_replication", "accumulate": True,
                       "new_vanna": "rec.vanna=-1xBS", "flow_compared": False}}


def write_deterministic_artifact(path: str, comparison: Mapping[str, Any]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(comparison, handle, sort_keys=True, indent=2)
        handle.write("\n")


__all__ = ["CanonicalInput", "CanonicalRow", "ComparisonInvalid", "compare_common_input",
           "make_canonical_input", "write_deterministic_artifact"]
