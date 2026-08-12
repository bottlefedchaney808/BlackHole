"""data.py — chain dataset fetching and ChainDay assembly for the tournament.

One ChainDay = one (ticker, expiry, date) snapshot of the whole chain with
the market-derived contexts the smile models need (VV pillars, SABR fit,
Heston variance level). Fetching is deliberately SEQUENTIAL across
tickers/expiries (PotatoHedge proxy saturation rule: never overlap heavy
whole-chain fan-outs).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date as _date
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from Backtests.core import GREEK_FIELDS_L2, normalize_rows, parse_yyyymmdd, tte_years
from Backtests.models import build_heston_context, build_sabr_context, build_vv_context


@dataclass
class ChainDay:
    ticker: str
    expiry: str
    date: str
    rows: List[Dict[str, Any]]
    spot: float
    T: Optional[float]
    r: float
    q: float
    forward: float
    vv_ctx: Dict[str, Any] = field(default_factory=dict)
    sabr_cal: Optional[Dict[str, Any]] = None
    heston_ctx: Optional[Dict[str, Any]] = None
    l2_status: str = "not_requested"
    l2_error: Optional[str] = None


def _day_spot(rows: List[Dict[str, Any]]) -> float:
    spots = [r["underlying_price"] for r in rows
             if r.get("underlying_price") and r["underlying_price"] > 0]
    if not spots:
        return 0.0
    spots.sort()
    return spots[len(spots) // 2]


def _merge_second_order_rows(
    rows: List[Dict[str, Any]],
    second_order_rows: List[Dict[str, Any]],
) -> int:
    """Merge snapshot second-order greeks onto first-order rows by contract."""
    by_contract = {
        (row["strike"], row["right"]): row
        for row in rows
        if row.get("strike") is not None and row.get("right") is not None
    }
    merged = 0
    for row in second_order_rows:
        key = (row.get("strike"), row.get("right"))
        target = by_contract.get(key)
        if target is not None:
            target.update({
                field: value
                for field, value in row.items()
                if field not in ("strike", "right")
            })
            merged += 1
    return merged


def _usable_second_order_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    usable_rows: List[Dict[str, Any]] = []
    for row in rows:
        clean = dict(row)
        has_value = False
        for field in GREEK_FIELDS_L2:
            value = row.get(field)
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                clean.pop(field, None)
                continue
            if not math.isfinite(numeric):
                clean.pop(field, None)
                continue
            clean[field] = numeric
            has_value = True
        if has_value:
            usable_rows.append(clean)
    return usable_rows


def _classify_l2_coverage(
    rows: List[Dict[str, Any]],
    second_order_rows: List[Dict[str, Any]],
    merged: int,
) -> tuple[str, Optional[str]]:
    if not second_order_rows:
        return "unusable", "second-order endpoint returned no usable rows"
    if merged == 0:
        return "unusable", "second-order rows did not match the snapshot contracts"

    matched_rows = 0
    full_field_rows = 0
    required_fields = set(GREEK_FIELDS_L2)
    for row in rows:
        present_fields = {
            field for field in GREEK_FIELDS_L2
            if isinstance(row.get(field), (int, float)) and math.isfinite(float(row[field]))
        }
        if present_fields:
            matched_rows += 1
        if present_fields == required_fields:
            full_field_rows += 1

    if full_field_rows == len(rows):
        return "available", None
    if matched_rows == 0:
        return "unusable", "second-order rows did not produce any usable contract coverage"
    return "partial", "second-order rows only partially covered required L2 fields"


def build_chain_day(
    td: Any,
    ticker: str,
    expiry: str,
    date: str,
    r: float,
    q: float,
    use_hist: bool,
) -> Optional[ChainDay]:
    """Fetch + assemble one ChainDay. Returns None on total failure."""
    try:
        if use_hist:
            rows = td.option_bulk_hist_greeks(ticker, expiry, date, date)
        else:
            rows = td.option_bulk_greeks(ticker, expiry)
    except Exception:
        return None
    rows = normalize_rows(rows)
    l2_status = "not_requested"
    l2_error = None
    if use_hist:
        historical_fields = {
            field for row in rows for field in GREEK_FIELDS_L2
            if isinstance(row.get(field), (int, float)) and math.isfinite(float(row[field]))
        }
        if historical_fields:
            if historical_fields == set(GREEK_FIELDS_L2):
                l2_status = "available"
            else:
                l2_status = "partial"
                l2_error = "historical rows only partially covered required L2 fields"
        else:
            l2_status = "unavailable"
            l2_error = "historical rows did not include second-order greeks"
    else:
        try:
            second_order_rows = normalize_rows(
                td.option_bulk_greeks_second_order(ticker, expiry)
            )
        except Exception as exc:
            second_order_rows = []
            l2_status = "unavailable"
            l2_error = f"{type(exc).__name__}: {exc}"
        else:
            second_order_rows = _usable_second_order_rows(second_order_rows)
            merged = _merge_second_order_rows(rows, second_order_rows)
            l2_status, l2_error = _classify_l2_coverage(rows, second_order_rows, merged)
    if not rows:
        return None
    spot = _day_spot(rows)
    if spot <= 0:
        return None
    T = tte_years(expiry, date)
    if T is None:
        return None
    forward = spot * __exp((r - q) * T)
    vv_ctx = build_vv_context(rows, forward, T, r, q)
    sabr_cal = build_sabr_context(rows, forward, T)
    atm_iv = vv_ctx.get("atm_vol")
    heston_ctx = build_heston_context(atm_iv)
    return ChainDay(
        ticker=ticker, expiry=expiry, date=date, rows=rows, spot=spot, T=T,
        r=r, q=q, forward=forward, vv_ctx=vv_ctx, sabr_cal=sabr_cal,
        heston_ctx=heston_ctx, l2_status=l2_status, l2_error=l2_error,
    )


def recent_calendar_dates(n: int, end: Optional[str] = None) -> List[str]:
    """Last n calendar days ending at `end` (default today), oldest first."""
    end_dt = parse_yyyymmdd(end) or datetime.now()
    return [(end_dt - timedelta(days=i)).strftime("%Y%m%d") for i in range(n - 1, -1, -1)]


def pick_expiry(td: Any, ticker: str, min_days_out: int) -> Optional[str]:
    """First listed expiry >= min_days_out from today."""
    try:
        exps = td.list_expirations(ticker)
    except Exception:
        return None
    today = _date.today()
    for e in exps:
        d = parse_yyyymmdd(e)
        if d and (d.date() - today).days >= min_days_out:
            return e
    return None


def fetch_chain_days(
    td: Any,
    ticker: str,
    expiry: str,
    dates: List[str],
    r: float,
    q: float,
) -> List[ChainDay]:
    """Sequential per-day ChainDay fetch (proxy-saturation-safe)."""
    out: List[ChainDay] = []
    for d in dates:
        cd = build_chain_day(td, ticker, expiry, d, r, q, use_hist=(len(dates) > 1))
        if cd is not None:
            out.append(cd)
    return out


def __exp(x: float) -> float:
    import math
    return math.exp(x)
