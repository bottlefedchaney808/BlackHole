"""Production adapter for the expiry-book dealer exposure engine."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

import expiry_book_exposure as ebe


class ExpiryBookUnavailable(RuntimeError):
    """Required market inputs were not available or failed validation."""


def _number(row: Mapping[str, Any], names: tuple[str, ...], label: str) -> float:
    for name in names:
        if name in row and row[name] not in (None, ""):
            try:
                value = float(row[name])
            except (TypeError, ValueError):
                break
            if math.isfinite(value):
                return value
            break
    raise ExpiryBookUnavailable(f"missing or invalid {label}")


def normalize_snapshot_rows(raw_rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalize raw ThetaData option rows, dropping individually-unusable
    contracts instead of failing the whole chain.

    A single illiquid/far-dated contract with an uncomputable IV (ThetaData
    returns implied_vol=0.0000 -- a real, legitimate response, not a
    sentinel) or a missing field used to abort the ENTIRE snapshot. That's
    wrong: the rest of the chain is still real, usable data. Only raise when
    *nothing* in the chain survives normalization.
    """
    if not raw_rows:
        raise ExpiryBookUnavailable("empty option snapshot")
    normalized: list[dict[str, Any]] = []
    skip_reasons: list[str] = []
    for raw in raw_rows:
        try:
            strike = _number(raw, ("strike", "strike_price"), "strike")
            if abs(strike) > 10_000:
                strike /= 1000.0
            right = str(raw.get("right", raw.get("put_call", ""))).upper()[:1]
            if right not in {"C", "P"}:
                raise ExpiryBookUnavailable("missing or invalid option right")
            oi = _number(raw, ("oi", "open_interest", "openInterest"), "open interest")
            iv = _number(raw, ("implied_vol", "impliedVol", "iv", "IV"), "implied volatility")
            if strike <= 0 or oi < 0 or iv <= 0:
                raise ExpiryBookUnavailable("invalid normalized option row")
        except ExpiryBookUnavailable as exc:
            skip_reasons.append(str(exc))
            continue
        normalized.append({"strike": strike, "right": right, "oi": oi, "implied_vol": iv})
    if not normalized:
        raise ExpiryBookUnavailable(skip_reasons[0] if skip_reasons else "no usable option rows")
    if skip_reasons:
        print(f"  [normalize_snapshot_rows] dropped {len(skip_reasons)}/{len(raw_rows)} "
              f"unusable contract(s) ({skip_reasons[0]!r} etc.), kept {len(normalized)}")
    return normalized


@dataclass(frozen=True)
class StructuralStatus:
    status: str
    reason: str | None = None


@dataclass(frozen=True)
class ProductionDealerExposure:
    ticker: str
    expiry: str
    spot: float
    status: str
    snapshot: ebe.NetExposure
    execution_locus: ebe.ExecutionLocus
    scenario_budget: ebe.ScenarioBudget
    structural: StructuralStatus
    units: dict[str, str]
    provenance: dict[str, str]
    accumulation_claimed: bool = False


def fetch_production_result(td: Any, ticker: str, expiry: str) -> ProductionDealerExposure:
    """Fetch one strict snapshot through the shared ThetaData controller."""
    try:
        spot = float(td.fetch_spot_price(ticker))
        rows = td.option_bulk_greeks(ticker, expiry)
        oi_rows = td.option_bulk_oi(ticker, expiry)
    except Exception as exc:
        raise ExpiryBookUnavailable(f"ThetaData snapshot unavailable: {type(exc).__name__}: {exc}") from exc
    oi_by_key = {}
    for row in oi_rows or []:
        try:
            strike = float(row.get("strike", 0.0))
            if abs(strike) > 10_000:
                strike /= 1000.0
            right = str(row.get("right", "")).upper()[:1]
            oi_by_key[(round(strike, 8), right)] = row.get("open_interest", row.get("oi"))
        except (TypeError, ValueError):
            continue
    merged = []
    for row in rows or []:
        strike = float(row.get("strike", 0.0))
        if abs(strike) > 10_000:
            strike /= 1000.0
        right = str(row.get("right", "")).upper()[:1]
        item = dict(row)
        item["strike"] = strike
        item["right"] = right
        item["open_interest"] = oi_by_key.get((round(strike, 8), right), row.get("open_interest"))
        merged.append(item)
    from datetime import date
    dte = (date.fromisoformat(f"{expiry[:4]}-{expiry[4:6]}-{expiry[6:8]}") - date.today()).days
    return production_result_from_rows(ticker, expiry, spot, merged, dte=dte)


def production_result_from_rows(ticker: str, expiry: str, spot: float,
                                raw_rows: list[Mapping[str, Any]], *, dte: int) -> ProductionDealerExposure:
    if not isinstance(ticker, str) or not ticker.strip():
        raise ExpiryBookUnavailable("ticker is required")
    if not isinstance(expiry, str) or len(expiry) != 8 or not expiry.isdigit():
        raise ExpiryBookUnavailable("expiry must be YYYYMMDD")
    if not math.isfinite(float(spot)) or float(spot) <= 0:
        raise ExpiryBookUnavailable("real spot is required")
    if int(dte) <= 0:
        raise ExpiryBookUnavailable("positive DTE is required")
    rows = normalize_snapshot_rows(raw_rows)
    t = int(dte) / ebe.DEFAULT_A
    snapshot = ebe.build_net_exposure(rows, float(spot), ticker=ticker,
                                      expiry=expiry, T=t, dte=int(dte))
    if not snapshot.rows:
        raise ExpiryBookUnavailable("snapshot has no usable option rows")
    locus = ebe.execution_locus(rows, float(spot), T=t)
    budget = ebe.scenario_hedge_flow(rows, float(spot), T=t, dte=int(dte), ticker=ticker)
    return ProductionDealerExposure(
        ticker=ticker.upper(), expiry=expiry, spot=float(spot), status="available",
        snapshot=snapshot, execution_locus=locus, scenario_budget=budget,
        structural=StructuralStatus("unavailable", "multi_expiry_book_required"),
        units={"gex": "dollar_gamma_per_1pct_move", "dex": "post_multiplier_shares", "vanna": "shares_per_vol_point"},
        provenance={"source": "ThetaData snapshot", "greeks": "Black-Scholes from real spot/IV/DTE", "accumulation": "not_claimed"},
    )
