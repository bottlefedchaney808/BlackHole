"""Production adapter for the expiry-book dealer exposure engine."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Mapping, Optional

import expiry_book_exposure as ebe

# Session increment, not 150d accumulation. 7 trading-ish calendar days;
# hard cap 14 if anyone overrides.
VANNA_FLOW_LOOKBACK_DAYS = 7
VANNA_FLOW_LOOKBACK_MAX = 14
# Measured ΔIV below this is noise. Not applied to hypothetical ±1pt budget.
VANNA_FLOW_DIV_DEADBAND = 0.01


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


def _theta_strike_to_dollars(strike: float) -> float:
    """ThetaData strikes are integer thousandths ($13.85 → 13850).

    The old abs>10_000 gate left sub-$10 strikes (4000 → $4) unscaled, so
    SLS charts plotted a $0–$10,000 axis with spot glued to zero.
    Dollar-scale inputs (<1000) pass through.
    """
    if abs(strike) >= 1000:
        return strike / 1000.0
    return strike


def normalize_snapshot_rows(raw_rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not raw_rows:
        raise ExpiryBookUnavailable("empty option snapshot")
    normalized: list[dict[str, Any]] = []
    for raw in raw_rows:
        strike = _theta_strike_to_dollars(_number(raw, ("strike", "strike_price"), "strike"))
        right = str(raw.get("right", raw.get("put_call", ""))).upper()[:1]
        if right not in {"C", "P"}:
            raise ExpiryBookUnavailable("missing or invalid option right")
        oi = _number(raw, ("oi", "open_interest", "openInterest"), "open interest")
        iv = _number(raw, ("implied_vol", "impliedVol", "iv", "IV"), "implied volatility")
        # Vendor leaves implied_vol=0 / greeks=0 on unsolved deep-ITM legs
        # (WMT 20261120: 5/54 rows). That is not a missing field — drop the
        # unusable row instead of aborting the whole book.
        if strike <= 0 or oi < 0 or iv <= 0:
            continue
        normalized.append({"strike": strike, "right": right, "oi": oi, "implied_vol": iv})
    if not normalized:
        raise ExpiryBookUnavailable("snapshot has no usable option rows")
    return normalized


@dataclass(frozen=True)
class StructuralStatus:
    status: str
    reason: str | None = None


@dataclass
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
    structural_regime: Optional[ebe.StructuralRegime] = None
    svi_overlay: Optional[ebe.SviOverlay] = None
    vanna_flow_live: Optional[float] = None
    vanna_flow_provenance: str = "delta_iv_missing"
    charm_1d: float = 0.0
    gex_reference: float = 0.0
    book_gamma: float = 0.0
    extra_books: list = field(default_factory=list)
    vendor_dealer: Optional[dict] = None
    d_iv_used: Optional[float] = None
    residual_vanna_inventory: Optional[float] = None


def _merge_greeks_oi(rows, oi_rows) -> list:
    oi_by_key = {}
    for row in oi_rows or []:
        try:
            strike = _theta_strike_to_dollars(float(row.get("strike", 0.0)))
            right = str(row.get("right", "")).upper()[:1]
            oi_by_key[(round(strike, 8), right)] = row.get("open_interest", row.get("oi"))
        except (TypeError, ValueError):
            continue
    merged = []
    for row in rows or []:
        strike = _theta_strike_to_dollars(float(row.get("strike", 0.0)))
        right = str(row.get("right", "")).upper()[:1]
        item = dict(row)
        item["strike"] = strike
        item["right"] = right
        item["open_interest"] = oi_by_key.get((round(strike, 8), right), row.get("open_interest"))
        merged.append(item)
    return merged


def _dte_of(expiry: str) -> int:
    return (date.fromisoformat(f"{expiry[:4]}-{expiry[4:6]}-{expiry[6:8]}") - date.today()).days


def _bucket(dte: int) -> str | None:
    if 2 <= dte <= 10:
        return "near"
    if 20 <= dte <= 45:
        return "mid"
    if 80 <= dte <= 180:
        return "far"
    return None


def fetch_production_result(td: Any, ticker: str, expiry: str) -> ProductionDealerExposure:
    """Fetch the primary expiry plus near/mid/far books when listed."""
    try:
        spot = float(td.fetch_spot_price(ticker))
        rows = td.option_bulk_greeks(ticker, expiry)
        oi_rows = td.option_bulk_oi(ticker, expiry)
        try:
            q = float(td.fetch_dividend_yield(ticker))
            q_source = "thetadata"
        except Exception:
            q = 0.0
            q_source = "fallback_zero"
    except Exception as exc:
        raise ExpiryBookUnavailable(
            f"ThetaData snapshot unavailable: {type(exc).__name__}: {exc}"
        ) from exc
    merged = _merge_greeks_oi(rows, oi_rows)
    dte = _dte_of(expiry)

    extra: list[dict] = []
    try:
        listed = [str(e).replace("-", "") for e in (td.list_expirations(ticker) or [])]
    except Exception:
        listed = []
    have = {_bucket(dte): expiry} if _bucket(dte) else {}
    for cand in sorted(listed):
        if cand == expiry or len(cand) != 8:
            continue
        try:
            cdte = _dte_of(cand)
        except Exception:
            continue
        b = _bucket(cdte)
        if b is None or b in have:
            continue
        try:
            grows = td.option_bulk_greeks(ticker, cand)
            goi = td.option_bulk_oi(ticker, cand)
            extra.append({"expiry": cand, "rows": _merge_greeks_oi(grows, goi), "dte": cdte})
            have[b] = cand
        except Exception:
            continue
        if len(have) >= 3:
            break

    prior_atm_iv = None
    prior_iv_asof = None
    prior_close = None
    try:
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=10)
        hist = td.hist_stock_eod(ticker, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
        if hist:
            last = hist[-1]
            prior_close = float(last.get("close") or 0) or None
    except Exception:
        pass
    # Dense whole-chain EOD greeks (1 req/expiry). Not hist_stock_eod, not
    # the sparse option_bulk_hist_greeks fan-out. PH v2 WIKI + theta-data skill.
    hist_fn = getattr(td, "option_bulk_hist_eod_greeks", None)
    if callable(hist_fn):
        try:
            end = date.today() - timedelta(days=1)
            start = end - timedelta(days=min(VANNA_FLOW_LOOKBACK_DAYS, VANNA_FLOW_LOOKBACK_MAX))
            grows = hist_fn(ticker, expiry, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
            by_day = {}
            for row in grows or []:
                d = str(row.get("date") or row.get("created") or "")
                digits = "".join(ch for ch in d if ch.isdigit())[:8]
                if len(digits) == 8:
                    by_day.setdefault(digits, []).append(row)
            if by_day:
                last_d = sorted(by_day)[-1]
                prior_atm_iv = ebe.atm_iv_otm(by_day[last_d], spot)
                prior_iv_asof = last_d
        except Exception:
            prior_atm_iv = None
            prior_iv_asof = None

    vendor_dealer = None
    vendor_fn = getattr(td, "get_dealer_weighted_greeks_summary", None)
    vendor_endpoint = "dealer.weighted_greeks_summary"
    if not callable(vendor_fn):
        vendor_fn = getattr(td, "get_dealer_positioning", None)
        vendor_endpoint = "dealer.positioning"
    if callable(vendor_fn):
        try:
            end = date.today()
            start = end - timedelta(days=10)
            if vendor_endpoint == "dealer.weighted_greeks_summary":
                payload = vendor_fn(
                    ticker, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"),
                )
            else:
                payload = vendor_fn(
                    ticker, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"),
                    latest_only=True,
                )
            vendor_dealer = _summarize_vendor_dealer(payload, endpoint=vendor_endpoint)
        except Exception:
            vendor_dealer = {"status": "unavailable", "endpoint": vendor_endpoint}

    d_iv_measured = None
    d_iv_source = None
    surf_fn = getattr(td, "get_iv_surface_change", None)
    if callable(surf_fn):
        try:
            lookback = min(VANNA_FLOW_LOOKBACK_DAYS, VANNA_FLOW_LOOKBACK_MAX)
            baseline = (date.today() - timedelta(days=lookback)).strftime("%Y%m%d")
            payload = surf_fn(ticker, expiry, baseline)
            d_iv_measured = _extract_surface_div(payload)
            if d_iv_measured is not None:
                d_iv_source = "SURFACE_CHANGE"
        except Exception:
            d_iv_measured = None

    result = production_result_from_rows(
        ticker, expiry, spot, merged, dte=dte, q=q,
        extra_books=extra,
        prior_atm_iv=prior_atm_iv,
        prior_iv_asof=prior_iv_asof,
        prior_close=prior_close,
        vendor_dealer=vendor_dealer,
        d_iv_measured=d_iv_measured,
        d_iv_source=d_iv_source,
    )
    result.provenance["dividend_yield_q"] = f"{float(q):.6f}"
    result.provenance["q_source"] = q_source
    return result


def production_result_from_rows(ticker: str, expiry: str, spot: float,
                                raw_rows: list[Mapping[str, Any]], *, dte: int,
                                q: float = 0.0,
                                extra_books: Optional[list] = None,
                                prior_atm_iv: Optional[float] = None,
                                prior_iv_asof: Optional[str] = None,
                                prior_close: Optional[float] = None,
                                vendor_dealer: Optional[dict] = None,
                                d_iv_measured: Optional[float] = None,
                                d_iv_source: Optional[str] = None,
                                ) -> ProductionDealerExposure:
    if not isinstance(ticker, str) or not ticker.strip():
        raise ExpiryBookUnavailable("ticker is required")
    if not isinstance(expiry, str) or len(expiry) != 8 or not expiry.isdigit():
        raise ExpiryBookUnavailable("expiry must be YYYYMMDD")
    if not math.isfinite(float(spot)) or float(spot) <= 0:
        raise ExpiryBookUnavailable("real spot is required")
    if int(dte) <= 0:
        raise ExpiryBookUnavailable("positive DTE is required")
    if not math.isfinite(float(q)):
        raise ExpiryBookUnavailable("dividend yield q must be finite")
    rows = normalize_snapshot_rows(raw_rows)
    t = int(dte) / ebe.DEFAULT_A
    snapshot = ebe.build_net_exposure(rows, float(spot), ticker=ticker,
                                      expiry=expiry, T=t, dte=int(dte), q=float(q))
    if not snapshot.rows:
        raise ExpiryBookUnavailable("snapshot has no usable option rows")

    chain_iv = {(r.strike, r.right): r.iv for r in snapshot.rows
                if r.iv == r.iv and r.iv > 0}
    oi_by = {(r.strike, r.right): int(r.oi) for r in snapshot.rows}
    overlay = ebe.svi_rp_overlay(
        chain_iv, float(spot), t, oi_by=oi_by, ticker=ticker,
        r=ebe.RISK_FREE_RATE, q=float(q),
    )
    ebe.apply_svi_book_signs(snapshot, overlay, float(spot), t)

    locus = ebe.execution_locus(rows, float(spot), T=t, q=float(q), ne=snapshot)
    budget = ebe.scenario_hedge_flow(rows, float(spot), T=t, dte=int(dte),
                                     ticker=ticker, q=float(q), ne=snapshot)

    books = [{"expiry": expiry, "spot": float(spot), "rows": rows, "T": t, "dte": int(dte)}]
    for extra in extra_books or []:
        try:
            erows = normalize_snapshot_rows(extra["rows"])
        except ExpiryBookUnavailable:
            continue
        edte = int(extra.get("dte") or 0)
        if edte <= 0 or len(erows) < 8:
            continue
        books.append({
            "expiry": str(extra["expiry"]),
            "spot": float(spot),
            "rows": erows,
            "T": edte / ebe.DEFAULT_A,
            "dte": edte,
        })

    buckets = {}
    for b in books:
        bk = _bucket(int(b["dte"]))
        if bk and bk not in buckets:
            buckets[bk] = b
    regime = None
    if {"near", "mid", "far"} <= set(buckets):
        regime = ebe.build_structural_regime(list(buckets.values()), q=float(q))
        st = StructuralStatus("available", None)
    else:
        st = StructuralStatus("unavailable", "insufficient_tenor_buckets")

    vflow = None
    vprov = "delta_iv_missing"
    d_iv_used = None
    # Vendor 7d ΔIV only. ATM-subtract manufactures flow (Cem/Jason 2026-08-20).
    if d_iv_measured is not None and math.isfinite(float(d_iv_measured)):
        d_iv = float(d_iv_measured)
        d_iv_used = d_iv
        src = d_iv_source or "SURFACE_CHANGE"
        if abs(d_iv) <= VANNA_FLOW_DIV_DEADBAND:
            vflow = 0.0
            vprov = f"{src}:deadband"
        else:
            vflow = ebe.vanna_flow(snapshot, d_iv)
            vprov = src

    charm_1d = snapshot.net("charm")
    gex_ref = snapshot.gex()
    book_g = snapshot.book_gamma()
    vanna_inv = float(snapshot.net("vanna"))

    return ProductionDealerExposure(
        ticker=ticker.upper(), expiry=expiry, spot=float(spot),
        status="available",
        snapshot=snapshot, execution_locus=locus, scenario_budget=budget,
        structural=st, structural_regime=regime, svi_overlay=overlay,
        vanna_flow_live=vflow, vanna_flow_provenance=vprov,
        charm_1d=float(charm_1d), gex_reference=float(gex_ref),
        book_gamma=float(book_g), extra_books=list(extra_books or []),
        vendor_dealer=vendor_dealer,
        d_iv_used=d_iv_used,
        residual_vanna_inventory=vanna_inv,
        units={"gex": "dollar_gamma_per_1pct_move_imported_call_put",
               "book_gamma": "dollar_gamma_per_1pct_svi_otm",
               "dex": "post_multiplier_shares",
               "vanna": "shares_per_vol_point",
               "residual_vanna_inventory": "shares_per_vol_point",
               "vanna_flow": "shares"},
        provenance={"source": "ThetaData snapshot",
                    "greeks": "Black-Scholes from real spot/IV/DTE",
                    "accumulation": "not_claimed",
                    "book_sign": "svi_cheap_rich_otm_deadband_0.01",
                    "dividend_yield_q": f"{float(q):.6f}",
                    "vanna_flow": vprov,
                    "vendor_dealer": (vendor_dealer or {}).get("endpoint", "not_fetched")},
    )


def format_production_interp(result: ProductionDealerExposure) -> str:
    loc = result.execution_locus
    ov = result.svi_overlay
    cheap = len(ov.cheap_strikes) if ov else 0
    rich = len(ov.rich_strikes) if ov else 0
    unmarked = 0
    if ov:
        unmarked = sum(1 for m in ov.marks if m[2] == "UNMARKED")
    st = result.structural
    regime_txt = st.status
    if result.structural_regime is not None:
        sr = result.structural_regime
        regime_txt = f"{sr.regime} carry={sr.carry:,.0f} persist={sr.persistence:.2f} {sr.term_structure_flag}"
    elif st.reason:
        regime_txt = f"unavailable ({st.reason})"
    vf = result.vanna_flow_live
    vf_txt = "unavailable" if vf is None else f"{vf:,.0f}"
    inv = result.residual_vanna_inventory
    inv_txt = "n/a" if inv is None else f"{inv:,.0f}"
    d_iv = result.d_iv_used
    d_iv_txt = "n/a" if d_iv is None else f"{float(d_iv):+.4f}"
    vd = result.vendor_dealer or {}
    if vd.get("status") == "available":
        if "net" in vd:
            vd_txt = f"{vd.get('endpoint')} {vd.get('net_field')}={vd['net']:,.0f} (not OI-multiplied)"
        else:
            vd_txt = f"{vd.get('endpoint')} keys={vd.get('keys')}"
    else:
        vd_txt = vd.get("status", "not_fetched")
    return (
        f"Ticker: {result.ticker}\n"
        f"Spot: ${result.spot:.2f}\n"
        f"GEX (imported call+/put- reference): ${result.gex_reference:,.0f}\n"
        f"Book gamma (SVI×OTM): ${result.book_gamma:,.0f}\n"
        f"DEX (carry, not a forecast): {result.snapshot.dex():,.0f}\n"
        f"Charm 1d (shares/day): {result.charm_1d:,.0f}\n"
        f"Vanna inventory (shares/vol-pt): {inv_txt}\n"
        f"Vanna flow 7d (shares, dIV={d_iv_txt}, {result.vanna_flow_provenance}): {vf_txt}\n"
        f"SVI marks cheap={cheap} rich={rich} unmarked={unmarked}\n"
        f"Structural: {regime_txt}\n"
        f"Local gamma boundary: ${loc.local_gamma_boundary:.2f}\n"
        f"Call gamma wall: ${loc.call_gamma_wall:.2f}\n"
        f"Put gamma wall: ${loc.put_gamma_wall:.2f}\n"
        f"Vendor PH dealer.positioning: {vd_txt}\n"
        f"Scenario budget: full hypothetical shocks (not a gate)"
    )


def _summarize_vendor_dealer(payload, endpoint: str = "dealer.positioning") -> dict:
    """Label-only vendor book. Already position-weighted — never * OI."""
    if payload is None:
        return {"status": "unavailable", "endpoint": endpoint}
    if isinstance(payload, list):
        payload = payload[-1] if payload and isinstance(payload[-1], dict) else {"rows": payload}
    if not isinstance(payload, dict):
        return {"status": "unavailable", "endpoint": endpoint}
    out = {"status": "available", "endpoint": endpoint, "oi_multiplied": False}
    for key in ("net_gamma", "net_gex", "gex", "total_net_dollar_gamma",
                "gamma", "netGamma", "dealer_gamma", "weighted_gamma"):
        if key in payload and payload[key] not in (None, ""):
            try:
                out["net"] = float(payload[key])
                out["net_field"] = key
                break
            except (TypeError, ValueError):
                continue
    out["keys"] = sorted(str(k) for k in payload.keys())[:20]
    return out


def _extract_surface_div(payload) -> float | None:
    """Pull a decimal ΔIV from volatility.surface_change. No local solve."""
    if payload is None:
        return None
    if isinstance(payload, (int, float)) and math.isfinite(float(payload)):
        return float(payload)
    if isinstance(payload, list) and payload:
        payload = payload[-1]
    if not isinstance(payload, dict):
        return None
    for key in ("atm_change", "d_iv", "delta_iv", "atm_iv_change", "atm_delta",
                "mean_change", "iv_change", "surface_change"):
        if key in payload and payload[key] not in (None, ""):
            try:
                v = float(payload[key])
            except (TypeError, ValueError):
                continue
            if math.isfinite(v):
                return v
    return None
