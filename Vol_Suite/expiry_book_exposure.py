#!/usr/bin/env python3
"""expiry_book_exposure.py — the LIVE dealer-frame greeks engine.

Implements PLAN_expiry_book_exposure_v2_20260814.md Phases 0-6. As of
2026-08-17 this is the production dealer-positioning model: Jason decided to
move forward with it in place of continuing to sink cost into the old
`dealer_positioning.py` accumulation model, which was broken. See
`docs/Dealer posistioning notes/HANDOFF_dealer_exposure_dev_20260814.md` for
the full history and the explicit promotion decision (overriding the
in-progress Cem-arbiter loop's "NOT ACCEPTED" verdict on cost/pragmatism
grounds, not on a claim that the statistical validation completed). This file
is consumed via `expiry_book_production.py` by `volatility_suite.py`,
`options_chain_scanner.py`, and `sentiment-scanner/scanner/gex_scanner.py`.
`dealer_positioning.py`'s `compute_dealer_positioning`/`compute_accumulated_position`
are now locked (`_assert_legacy_backtest_access`) to backtest/test callers only.

Global constraints honored (plan §4):
  1. (Superseded 2026-08-17 — see above. This file is no longer a
     read-only addition alongside an untouched live model; it IS the live
     model.)
  2. All six greeks in DEALER-FRAME; sign each greek once, never stack a
     `right_dir`.
  3. Dealer-frame = -1 * customer-frame for delta/vega/volga.
     `bs_vanna` already returns that negated quantity (pass-through).
     Charm is customer per-right calendar dΔ/dt, net = CallCharm*callOI
     + PutCharm*putOI (ITM call+/OTM put+, ITM put-/OTM call-). No extra
     dealer -1 on charm.
  4. Real spot, NOT median-strike.
  5. Charm scaled x(1/DEFAULT_A) if CHARM_ANNUALIZED; NEVER x(1/DTE).
  6. Vanna flow dIV unit is DECIMAL vol, ONE formula:
     vanna_flow = SUM signed_vanna * OI * 100 * VANNA_PP_SCALE * (dIV/0.01).
  7. GEX pinned to dollar-gamma-per-1%: Gamma*OI*100*spot^2*0.01.
  8. DEX multiplier pinned; reported number is POST-multiplier shares.
  9. Falsifier pre-registered (Phase 6).

Design note (Phases 0-6 live here as one cohesive, test-only model):
  Phase 0  El-Karoui measurement closure gate (blocking).
  Phase 1  Greeks engine + full dealer-frame sign/units contract.
  Phase 2  Execution-locus futures-flow map (zero-gamma / walls / tolerance
           band / threshold-gated bursts).
  Phase 3  Scenario-conditional hedge-flow budget (the actionable output).
  Phase 4  Structural/regime arm (co-primary "better seed", fresh,
           term-structure-weighted, NO dependency on the live RP seed).
  Phase 5  SVI-RP + term-structure overlays.
  Phase 6  Pre-registered falsifier (GEX flow -> forward returns primary,
           event-gated vanna lead arm, event-window + short-DTE arms,
           per-underline handling, SPY/QQQ sign-consistency + FDR).
"""

from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

# Keep production constants local. The legacy live module is locked and is not
# a dependency of the authoritative production engine.
CONTRACT_MULTIPLIER = 100
VANNA_PP_SCALE = 0.01
DEFAULT_A = 365
CHARM_ANNUALIZED = True
RISK_FREE_RATE = 0.05

GREEKS = ("delta", "gamma", "vega", "vanna", "charm", "volga")


def vannacharm_row(r, spot, greek: str, oi=None) -> float:
    """VannaCharm stock exposure for one contract (not SVI).
    GEX = γ*OI*100*S²*0.01 with γ already call+/put−.
    VEX = call+|ν|*S*σ − put+|ν|*S*σ.
    CEX = χ*OI*100*S/365 with χ already per-right.
    Pass oi=net_contracts for dGEX/dVEX/dCEX (bought−sold).
    """
    if oi is None:
        oi = float(getattr(r, "oi", 0.0) or 0.0)
    iv = float(getattr(r, "iv", 0.0) or 0.0)
    if iv != iv:
        iv = 0.0
    greeks = getattr(r, "greeks", {}) or {}
    rs = 1.0 if str(getattr(r, "right", "C")).upper()[:1] == "C" else -1.0
    if greek == "gamma":
        return (
            float(greeks.get("gamma", 0.0) or 0.0)
            * oi
            * CONTRACT_MULTIPLIER
            * spot**2
            * 0.01
        )
    if greek == "vanna":
        return (
            rs
            * abs(float(greeks.get("vanna", 0.0) or 0.0))
            * oi
            * CONTRACT_MULTIPLIER
            * spot
            * iv
        )
    if greek == "charm":
        return (
            float(greeks.get("charm", 0.0) or 0.0)
            * oi
            * CONTRACT_MULTIPLIER
            * spot
            / 365.0
        )
    return 0.0


def _quote_num(row, *names) -> float:
    for name in names:
        if isinstance(row, dict) and name in row and row[name] not in (None, ""):
            try:
                v = float(row[name])
            except (TypeError, ValueError):
                continue
            if v == v:
                return v
    return 0.0


def net_contracts_from_quote(volume, bid_size, ask_size) -> float:
    """bought − sold. Article: volume=100, bid/ask ratio=0.6 → 60 bought, 40 sold.
    buy_frac = bid_size / (bid_size + ask_size), else 0.5 if no sizes.
    """
    vol = float(volume or 0.0)
    if vol <= 0:
        return 0.0
    b = max(float(bid_size or 0.0), 0.0)
    a = max(float(ask_size or 0.0), 0.0)
    denom = b + a
    buy_frac = (b / denom) if denom > 0 else 0.5
    bought = vol * buy_frac
    sold = vol - bought
    return bought - sold


def apply_vannacharm_flow(ne: NetExposure, quote_rows, spot: float) -> int:
    """Stamp net_contracts / d_gex / d_vex / d_cex from quote volume+size.
    quote strikes must already be dollars. Returns number of rows with volume."""
    by_key = {}
    for q in quote_rows or []:
        try:
            k = float(q.get("strike", 0.0))
            right = str(q.get("right", "")).upper()[:1]
        except (TypeError, ValueError):
            continue
        by_key[(round(k, 8), right)] = q
    n_vol = 0
    for r in ne.rows:
        q = by_key.get((round(r.strike, 8), r.right), {})
        vol = _quote_num(q, "volume", "Volume")
        bid_sz = _quote_num(q, "bid_size", "bidSize", "bid_sz", "bidsize")
        ask_sz = _quote_num(q, "ask_size", "askSize", "ask_sz", "asksize")
        net = net_contracts_from_quote(vol, bid_sz, ask_sz)
        r.net_contracts = net
        r.d_gex = vannacharm_row(r, spot, "gamma", oi=net)
        r.d_vex = vannacharm_row(r, spot, "vanna", oi=net)
        r.d_cex = vannacharm_row(r, spot, "charm", oi=net)
        if vol > 0:
            n_vol += 1
    return n_vol


# Stock/futures hedging channel vs options/vol hedging channel (plan §3 table).
STOCK_CHANNEL = ("delta", "gamma", "vanna", "charm")
VOL_CHANNEL = ("vega", "volga")

# vol-point scale used for finite-difference bumps (repo convention).
VOL_POINT = 0.01


# ---------------------------------------------------------------------------
# Black-Scholes greeks engine (dealer-frame, real spot/IV/DTE)
# ---------------------------------------------------------------------------
def _d1d2(S, K, T, sigma, r=RISK_FREE_RATE, q=0.0):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return float("nan"), float("nan")
    sig_sqrt = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / sig_sqrt
    d2 = d1 - sig_sqrt
    return d1, d2


def bs_price(S, K, T, sigma, r=RISK_FREE_RATE, q=0.0, right="C"):
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return float("nan")
    d1, d2 = _d1d2(S, K, T, sigma, r, q)
    if math.isnan(d1):
        return float("nan")
    disc = math.exp(-q * T)
    if right == "C":
        return S * disc * _N(d1) - K * math.exp(-r * T) * _N(d2)
    return K * math.exp(-r * T) * _N(-d2) - S * disc * _N(-d1)


def _N(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _phi(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_delta(S, K, T, sigma, r=RISK_FREE_RATE, q=0.0, right="C"):
    d1, _ = _d1d2(S, K, T, sigma, r, q)
    if math.isnan(d1):
        return float("nan")
    disc = math.exp(-q * T)
    return disc * _N(d1) if right == "C" else disc * (_N(d1) - 1.0)


def bs_gamma(S, K, T, sigma, r=RISK_FREE_RATE, q=0.0):
    d1, _ = _d1d2(S, K, T, sigma, r, q)
    if math.isnan(d1):
        return float("nan")
    return math.exp(-q * T) * _phi(d1) / (S * sigma * math.sqrt(T))


def bs_vega(S, K, T, sigma, r=RISK_FREE_RATE, q=0.0):
    """dV/dsigma per 1.0 vol (i.e. per 100 vol-points). Divide by 100 for
    vega-$ per 1 vol-point, which is what the options/vol channel reports."""
    d1, _ = _d1d2(S, K, T, sigma, r, q)
    if math.isnan(d1):
        return float("nan")
    return S * math.exp(-q * T) * _phi(d1) * math.sqrt(T)


def bs_vanna(S, K, T, sigma, r=RISK_FREE_RATE, q=0.0):
    """d(delta_call)/dsigma, ALREADY in the -1*customer-raw dealer-frame sign
    (verified by calculus: d(d1)/dsigma = -d2/sigma, so this function's
    phi(d1)*d2/sigma equals -1 * the textbook customer-frame d(delta_call)/dsigma).
    Consumed directly by dealer_frame_vanna as a pass-through -- see that
    function's docstring for the 2026-08-17 fix. Raw BS vanna has the SAME
    sign for call & put at the same OTM strike (negative when d2 < 0)."""
    d1, d2 = _d1d2(S, K, T, sigma, r, q)
    if math.isnan(d1):
        return float("nan")
    return math.exp(-q * T) * _phi(d1) * (d2 / sigma)


def bs_charm(S, K, T, sigma, r=RISK_FREE_RATE, q=0.0, right="C"):
    """Customer-frame calendar charm ≈ d(delta)/dt as expiry approaches
    (T falling). NOT dealer-signed — unlike bs_vanna.

    Sign vs strike (spot fixed): ITM call / OTM put → positive; OTM call /
    ITM put → negative. Net CEX = CallCharm*callOI + PutCharm*putOI.
    dealer_frame_greek("charm") is a pass-through (no extra -1).

    q term: calls +q*disc*N(d1), puts -q*disc*N(-d1). phi_term is shared.
    """
    d1, d2 = _d1d2(S, K, T, sigma, r, q)
    if math.isnan(d1):
        return float("nan")
    disc = math.exp(-q * T)
    phi_term = disc * _phi(d1) * ((d2 / (2.0 * T)) - (r - q) / (sigma * math.sqrt(T)))
    if right == "C":
        return q * disc * _N(d1) + phi_term
    return -q * disc * _N(-d1) + phi_term


def bs_volga(S, K, T, sigma, r=RISK_FREE_RATE, q=0.0):
    """Volga (vomma) = d2V/dsigma^2 = vega * d1 * d2 / sigma."""
    d1, d2 = _d1d2(S, K, T, sigma, r, q)
    if math.isnan(d1):
        return float("nan")
    vega = bs_vega(S, K, T, sigma, r, q)
    return vega * d1 * d2 / sigma


_BS_GREEK_FNS = {
    "delta": bs_delta,
    "gamma": bs_gamma,
    "vega": bs_vega,
    "vanna": bs_vanna,
    "charm": bs_charm,
    "volga": bs_volga,
}


def _right_sign(right):
    """Dealer-frame right sign. The v2 plan §5 pin: call = +1 / put = -1 is
    the OI convention used ONLY where the reference's own sign table calls for
    it (vanna's OTM call + / OTM put -); every other greek is signed once in
    the dealer frame WITHOUT stacking an extra right_dir (constraint 2)."""
    r = str(right).upper()[:1]
    if r == "C":
        return 1.0
    if r == "P":
        return -1.0
    return 0.0


def dealer_frame_vanna(raw_bs_vanna):
    """Dealer-frame vanna, composed the SAME way as dealer_frame_delta and
    dealer_frame_charm: dealer-frame = -1 * (customer-frame raw value).

    FIXED 2026-08-17 (CARL review): `bs_vanna(S,K,T,sigma)` = disc*phi(d1)*d2/sigma
    already equals -1 * (customer-frame raw d(delta_call)/dsigma) -- verified
    by direct calculus (d(d1)/dsigma = -d2/sigma, so
    d(delta_call)/dsigma = phi(d1)*d(d1)/dsigma = -phi(d1)*d2/sigma, i.e. the
    negative of bs_vanna's return value). Previously this function applied a
    SECOND -1 on top of that, so the two negations canceled and
    dealer_frame_vanna ended up equal to +1 * customer-frame raw -- the
    opposite composition from delta/charm (both -1 * customer-frame raw),
    with the flow-direction sign reversed for vanna alone. bs_vanna's own
    return value already IS the correctly-composed dealer-frame quantity, so
    this is now a pass-through -- do not re-add a negation here without
    re-deriving both functions together.
    """
    return raw_bs_vanna


def dealer_frame_greek(greek, raw_value, right):
    """Sign a raw BS greek ONCE into the dealer frame. Never stack a
    right_dir: each greek has exactly one sign convention applied here.
    Returns NaN if raw_value is NaN (caller skips, does not raise)."""
    if raw_value is None or (isinstance(raw_value, float) and math.isnan(raw_value)):
        return float("nan")
    if greek == "delta":
        # Dealer-frame delta: dealer is short the option side the customer is
        # long -> the hedge is the opposite sign. Use -1 * raw for both rights
        # (this is the "buy/sell N shares to become delta-neutral" direction).
        return -1.0 * raw_value
    if greek == "gamma":
        # GEX sign via long/short-option, NOT call+/put- (call+/put- would
        # mis-sign puts: a long put is +gamma, stabilizing). Dealer-frame uses
        # the OI convention the reference pins: call OI -> +gamma, put OI ->
        # -gamma, matching the standard GEX smile and the live sign model.
        return _right_sign(right) * raw_value
    if greek == "vanna":
        # Dealer-frame vanna, composed like delta/charm: -1 * customer-frame
        # raw value. bs_vanna already returns that negated quantity (see its
        # docstring), so dealer_frame_vanna is a pass-through, not a second
        # negation -- fixed 2026-08-17, see dealer_frame_vanna's docstring.
        return dealer_frame_vanna(raw_value)
    if greek == "charm":
        # Customer per-right charm. Net = C*OI + P*OI. Do not apply a
        # second dealer -1 — that inverted CEX vs VannaCharm/daytrading.
        return raw_value
    if greek in ("vega", "volga"):
        # Options/vol channel. Dealer-frame: the portfolio vol-book sensitivity
        # is the opposite of the option-holder's. Signed once, no right_dir.
        return -1.0 * raw_value
    raise ValueError(f"unknown greek {greek!r}")


def _extract(row, key, default=float("nan")):
    """Best-effort numeric extraction (matches the live NaN-tolerance pattern:
    returns NaN, never raises, on a missing/empty field)."""
    try:
        v = row.get(key)
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Phase 1 — Greeks engine: net_exposure[expiry][strike][greek] snapshot vector
# ---------------------------------------------------------------------------
@dataclass
class NetExposureRow:
    """One strike's net dealer-frame exposure for all six greeks, at real
    spot/IV/DTE, multiplier applied exactly once, explicit units."""

    strike: float
    right: str
    oi: float
    T: float
    dte: int
    iv: float = float("nan")
    greeks: dict[str, float] = field(
        default_factory=dict
    )  # raw dealer-frame signed value per greek (pre-scalar)
    exposure: dict[str, float] = field(
        default_factory=dict
    )  # signed_greek * OI * CONTRACT_MULTIPLIER
    units: dict[str, str] = field(default_factory=dict)
    measured: dict[str, str] = field(
        default_factory=dict
    )  # per-greek MEASURED/ESTIMATED provenance
    book_sign: float = 0.0  # SVI cheap/rich × OTM gate; 0 = unmarked/ITM
    gamma_book: float = 0.0  # unsigned BS gamma × book_sign × OI × 100 × S² × 0.01
    net_contracts: float = 0.0  # bought − sold from volume × bid/ask split
    d_gex: float = 0.0
    d_vex: float = 0.0
    d_cex: float = 0.0

    def exposure_of(self, greek):
        return self.exposure.get(greek, 0.0)


@dataclass
class NetExposure:
    """Per-expiry net exposure snapshot vector."""

    ticker: str
    spot: float
    rows: list[NetExposureRow] = field(default_factory=list)
    expiry: str = ""

    def net(self, greek):
        return sum(r.exposure_of(greek) for r in self.rows)

    def net_per_strike(self, greek):
        return {r.strike: r.exposure_of(greek) for r in self.rows}

    def gex(self):
        """Imported GEX reference: call+/put- dollar-gamma-per-1%."""
        return sum(
            r.greeks.get("gamma", 0.0)
            * r.oi
            * CONTRACT_MULTIPLIER
            * self.spot**2
            * 0.01
            for r in self.rows
        )

    def book_gamma(self):
        """Dealer-book dollar-gamma-per-1% (SVI-signed, OTM-gated)."""
        return sum(r.gamma_book for r in self.rows)

    def dex(self):
        """DEX = post-multiplier shares = SUM signed_delta * OI * 100."""
        return sum(
            r.greeks.get("delta", 0.0) * r.oi * CONTRACT_MULTIPLIER for r in self.rows
        )

    def dex_by_strike(self):
        return {
            r.strike: r.greeks.get("delta", 0.0) * r.oi * CONTRACT_MULTIPLIER
            for r in self.rows
        }


def build_net_exposure(
    rows: list[dict],
    spot: float,
    ticker: str = "MOCK",
    expiry: str = "",
    T: float | None = None,
    dte: int | None = None,
    q: float = 0.0,
) -> NetExposure:
    """Build the per-strike, per-expiry, per-greek net-exposure snapshot vector.

    Each input row: {'strike', 'right', 'oi', 'implied_vol', [spot], [T]}.
    Greeks are derived from (real spot, strike, T, IV) via BS in the
    dealer frame. A NaN greek field degrades to a per-greek skip (record kept,
    exposure 0 for that greek), never raising.

    `q`: continuous dividend yield, defaults to 0.0 (same default every
    bs_* function already had). Added 2026-08-17 (CARL review) -- every
    bs_* function always accepted q, but nothing upstream ever passed a
    real one through. See expiry_book_production.py for where this now
    comes from live.
    """
    if dte is None and T is not None:
        dte = max(int(round(T * DEFAULT_A)), 1)
    ne = NetExposure(ticker=ticker, spot=spot, expiry=expiry)
    for row in rows:
        k = _extract(row, "strike")
        right = str(row.get("right", "C")).upper()[:1]
        oi = _extract(row, "oi", 0.0)
        iv = _extract(row, "implied_vol")
        rT = T if T is not None else _extract(row, "T", DEFAULT_A and 0.25)
        if math.isnan(k) or math.isnan(iv) or iv <= 0 or k <= 0:
            continue
        rspot = spot if spot and spot > 0 else _extract(row, "spot", float("nan"))
        if math.isnan(rspot) or rspot <= 0:
            continue
        rr_dte = dte if dte is not None else max(int(round(rT * DEFAULT_A)), 1)
        ne_row = NetExposureRow(strike=k, right=right, oi=oi, T=rT, dte=rr_dte, iv=iv)
        for greek in GREEKS:
            if greek == "charm":
                raw = bs_charm(rspot, k, rT, iv, q=q, right=right)
            elif greek == "volga":
                raw = bs_volga(rspot, k, rT, iv, q=q)
            elif greek == "vega":
                raw = bs_vega(rspot, k, rT, iv, q=q)
            elif greek == "gamma":
                raw = bs_gamma(rspot, k, rT, iv, q=q)
            elif greek == "vanna":
                raw = bs_vanna(rspot, k, rT, iv, q=q)
            else:
                raw = bs_delta(rspot, k, rT, iv, q=q, right=right)
            if math.isnan(raw):
                ne_row.measured[greek] = "ESTIMATED-unavailable"
                ne_row.exposure[greek] = 0.0
                continue
            signed = dealer_frame_greek(greek, raw, right)
            ne_row.greeks[greek] = signed
            # Unit scalars applied exactly once, per plan §3.1/§3.4:
            if greek == "gamma":
                # dollar-gamma-per-1% -> exposure is per the GEX formula below;
                # store the raw signed gamma so gex() applies the spot^2*0.01.
                scalar = 1.0
                ne_row.exposure[greek] = signed * oi * CONTRACT_MULTIPLIER
                ne_row.units[greek] = "gamma*OI*100"
            elif greek == "charm":
                # x(1/DEFAULT_A) if CHARM_ANNUALIZED; NEVER x(1/DTE).
                scalar = (1.0 / DEFAULT_A) if CHARM_ANNUALIZED else 1.0
                ne_row.exposure[greek] = signed * scalar * oi * CONTRACT_MULTIPLIER
                ne_row.units[greek] = "shares/day"
            elif greek in ("vega", "volga"):
                # vega-$ per 1 vol-point = raw_vega / 100 (VANNA_PP_SCALE
                # analog). volga = vega-$ per vol-point^2.
                scalar = VANNA_PP_SCALE
                ne_row.exposure[greek] = signed * scalar * oi * CONTRACT_MULTIPLIER
                ne_row.units[greek] = (
                    "vega-$/vol-pt" if greek == "vega" else "vega-$/vol-pt^2"
                )
            elif greek == "vanna":
                # shares per vol-point (x dIV decimal); VANNA_PP_SCALE is the
                # per-vol-point factor; the flow formula applies dIV separately.
                scalar = VANNA_PP_SCALE
                ne_row.exposure[greek] = signed * scalar * oi * CONTRACT_MULTIPLIER
                ne_row.units[greek] = "shares/vol-pt"
            else:  # delta
                ne_row.exposure[greek] = signed * oi * CONTRACT_MULTIPLIER
                ne_row.units[greek] = "shares"
            ne_row.measured[greek] = "MEASURED"
        ne.rows.append(ne_row)
    return ne


BOOK_SIGN_DEADBAND = 0.01  # 1 vol point; matches vol_surface IV_DEADBAND_VOL


def atm_iv_otm(rows, spot: float) -> float | None:
    """OTM-side IV at the strike nearest spot (put if K<=spot, call if K>spot)."""
    best = None
    best_dist = None
    for row in rows:
        if isinstance(row, dict):
            k = float(row.get("strike", 0) or 0)
            right = str(row.get("right", "")).upper()[:1]
            iv = row.get("implied_vol", row.get("iv"))
        else:
            k = float(row.strike)
            right = str(row.right).upper()[:1]
            iv = row.iv
        try:
            iv = float(iv)
        except (TypeError, ValueError):
            continue
        if k <= 0 or iv <= 0 or iv != iv:
            continue
        want = "P" if k <= spot else "C"
        if right != want:
            continue
        dist = abs(k - spot)
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best = iv
    return best


def apply_svi_book_signs(
    ne: NetExposure, overlay: SviOverlay, spot: float, T: float
) -> NetExposure:
    """Stamp book_sign + gamma_book on every row. OTM-only, SVI deadbanded.

    SHORT (rich) → dealer short that strike → gamma_book negative.
    LONG (cheap) → dealer long → gamma_book positive.
    UNMARKED / ITM → 0.
    """
    try:
        from replication_reference import _otm_leg_weights

        chain_iv = {
            (r.strike, r.right): r.iv for r in ne.rows if r.iv == r.iv and r.iv > 0
        }
        otm = set(_otm_leg_weights(chain_iv, spot, T).keys())
    except Exception:
        otm = {(r.strike, r.right) for r in ne.rows}
    mark_map = {(k, right): mark for (k, right, mark, _diff) in overlay.marks}
    for r in ne.rows:
        mark = mark_map.get((r.strike, r.right), "UNMARKED")
        if (r.strike, r.right) not in otm:
            sign = 0.0
        elif mark == "SHORT":
            sign = -1.0
        elif mark == "LONG":
            sign = 1.0
        else:
            sign = 0.0
        raw_g = abs(float(r.greeks.get("gamma", 0.0) or 0.0))
        r.book_sign = sign
        r.gamma_book = sign * raw_g * r.oi * CONTRACT_MULTIPLIER * ne.spot**2 * 0.01
    return ne


def vanna_flow_31(ne: NetExposure, d_iv: float) -> float:
    """PLAN §3.1 form: the plan's VEX unit table writes the flow as
    `signed_vanna * OI * 100 * 0.01 * (dIV/0.01)` with dIV in DECIMAL vol —
    the per-vol-point factor (VANNA_PP_SCALE) times the decimal dIV. This is
    the SAME quantity as the canonical constraint-6 formula (the 0.01's cancel
    into VANNA_PP_SCALE * (dIV/0.01))."""
    return vanna_flow_34(ne, d_iv)


def vanna_flow_34(ne: NetExposure, d_iv: float) -> float:
    """PLAN §3.4 form: SUM signed_vanna * OI * 100 * VANNA_PP_SCALE * (dIV/0.01)
    with dIV in DECIMAL vol. This is the canonical constraint-6 form."""
    return sum(
        r.greeks["vanna"] * r.oi * CONTRACT_MULTIPLIER * VANNA_PP_SCALE * (d_iv / 0.01)
        for r in ne.rows
    )


def vanna_flow(ne: NetExposure, d_iv: float) -> float:
    """ONE formula (constraint 6, decimal-vol dIV):
    vanna_flow = SUM signed_vanna * OI * 100 * VANNA_PP_SCALE * (dIV/0.01)."""
    return vanna_flow_34(ne, d_iv)


# ---------------------------------------------------------------------------
# Phase 0 — El-Karoui measurement closure gate (blocking)
# ---------------------------------------------------------------------------
@dataclass
class ClosureResult:
    closure_r2: float
    max_per_greek_error: float
    bump_match_max: float
    per_greek_error: dict[str, float] = field(default_factory=dict)
    per_greek_provenance: dict[str, str] = field(default_factory=dict)
    n_options: int = 0


def _fd_greek(greek, S, K, T, sigma, right="C", h_s=None, h_v=VOL_POINT):
    """Central finite-difference recompute of a greek (bump-recomputed side)."""
    h_s = h_s or max(S * 0.005, 1e-6)
    fns = _BS_GREEK_FNS
    if greek == "delta":
        return (
            bs_price(S + h_s, K, T, sigma, right=right)
            - bs_price(S - h_s, K, T, sigma, right=right)
        ) / (2 * h_s)
    if greek == "gamma":
        d_up = (
            bs_price(S + h_s, K, T, sigma, right=right)
            - bs_price(S, K, T, sigma, right=right)
        ) / h_s
        d_dn = (
            bs_price(S, K, T, sigma, right=right)
            - bs_price(S - h_s, K, T, sigma, right=right)
        ) / h_s
        return (d_up - d_dn) / h_s
    if greek == "vega":
        return (
            bs_price(S, K, T, sigma + h_v, right=right)
            - bs_price(S, K, T, sigma - h_v, right=right)
        ) / (2 * h_v)
    if greek == "volga":
        v_up = (
            bs_price(S, K, T, sigma + h_v, right=right)
            - bs_price(S, K, T, sigma, right=right)
        ) / h_v
        v_dn = (
            bs_price(S, K, T, sigma, right=right)
            - bs_price(S, K, T, sigma - h_v, right=right)
        ) / h_v
        return (v_up - v_dn) / h_v
    if greek == "vanna":
        # Vanna convention matches the analytic bs_vanna = phi(d1)*d2/sigma
        # (the codebase/reference convention that rec.vanna = -1*BS_vanna is
        # defined against; note this is the NEGATIVE of the textbook d(delta)/d
        # sigma). So the bump must reproduce that same signed quantity:
        # bump_vanna = -(d(delta)/dsigma central diff).
        return -(
            (
                bs_delta(S, K, T, sigma + h_v, right=right)
                - bs_delta(S, K, T, sigma - h_v, right=right)
            )
            / (2 * h_v)
        )
    if greek == "charm":
        # Charm convention matches the analytic bs_charm below (delta-decay
        # toward terminal, reported negative for OTM). The analytic form is the
        # NEGATIVE of textbook d(delta)/dT; bump mirrors that sign.
        dt = max(T * 0.01, 1e-5)
        return -(
            (
                bs_delta(S, K, T + dt, sigma, right=right)
                - bs_delta(S, K, T - dt, sigma, right=right)
            )
            / (2 * dt)
        )
    raise ValueError(greek)


def _taylor_terms(S, K, T, sigma, right, d_s, d_sigma, dt, greeks):
    """Taylor P&L identity: dV ~= Delta*dS + 0.5 Gamma dS^2 + vega dsigma
    + vanna dS dsigma + charm dS dt + 0.5 volga dsigma^2. `dt` is the time
    step (1/365 if annualized, or 1/DTE for the mis-scale probe)."""
    terms = {
        "delta": greeks["delta"] * d_s,
        "gamma": 0.5 * greeks["gamma"] * d_s * d_s,
        "vega": greeks["vega"] * d_sigma,
        "vanna": greeks["vanna"] * d_s * d_sigma,
        "charm": greeks["charm"] * d_s * dt,
        "volga": 0.5 * greeks["volga"] * d_sigma * d_sigma,
    }
    return sum(terms.values()), terms


def el_karoui_closure_gate(
    rows: list[dict],
    spot: float,
    T: float = 0.25,
    charm_scale: str = "annualized",
    d_s_frac: float = 0.005,
    d_sigma_vol: float = 0.005,
) -> ClosureResult:
    """Phase 0 blocking gate.

    Computes the Taylor P&L identity TWICE — once with feed-provided (analytic
    BS) greeks, once with bump-recomputed (finite-difference) greeks — over a
    small scenario (dS, dsigma, dt), on the offline seed corpus shape (rows).
    Reports per-greek closure error (feed-vs-bump per term) and the closure-R2
    of the identity (predicted total via feed greeks vs bump greeks).

    Pre-registered blocking criterion: closure_r2 >= 0.90 AND
    max_per_greek_error <= tolerance. Charm scale: 'annualized' x(1/DEFAULT_A);
    'over_dte' x(1/DTE) — the probe that MUST break closure.
    """
    per_greek_error: dict[str, float] = {}
    total_pred_feed, total_pred_bump = [], []
    n_ok = 0
    for row in rows:
        k = _extract(row, "strike")
        right = str(row.get("right", "C")).upper()[:1]
        iv = _extract(row, "implied_vol")
        if math.isnan(k) or math.isnan(iv) or iv <= 0:
            continue
        rT = _extract(row, "T", T) or T
        dte = max(int(round(rT * DEFAULT_A)), 1)
        d_s = d_s_frac * spot
        d_sigma = d_sigma_vol * VOL_POINT
        if charm_scale == "over_dte":
            dt = 1.0 / dte
        else:
            dt = 1.0 / DEFAULT_A
        feed = {
            g: fns(spot, k, rT, iv, right=right)
            if g in ("delta", "charm")
            else fns(spot, k, rT, iv)
            for g, fns in _BS_GREEK_FNS.items()
        }
        bump = {g: _fd_greek(g, spot, k, rT, iv, right=right) for g in GREEKS}
        # dealer-frame both sides, charm scaled per scale
        feed_df = {g: dealer_frame_greek(g, feed[g], right) for g in GREEKS}
        bump_df = {g: dealer_frame_greek(g, bump[g], right) for g in GREEKS}
        feed_df["charm"] = feed_df["charm"] * (dt / (1.0 / DEFAULT_A))
        bump_df["charm"] = bump_df["charm"] * (dt / (1.0 / DEFAULT_A))
        p_feed, terms_feed = _taylor_terms(
            spot, k, rT, iv, right, d_s, d_sigma, dt, feed_df
        )
        p_bump, terms_bump = _taylor_terms(
            spot, k, rT, iv, right, d_s, d_sigma, dt, bump_df
        )
        total_pred_feed.append(p_feed)
        total_pred_bump.append(p_bump)
        for g in GREEKS:
            err = abs(terms_feed[g] - terms_bump[g])
            per_greek_error[g] = max(per_greek_error.get(g, 0.0), err)
        n_ok += 1
    if n_ok == 0:
        raise ValueError("closure gate: no usable options")

    closure_r2 = _r2(np.asarray(total_pred_bump), np.asarray(total_pred_feed))
    max_per_greek_error = max(per_greek_error.values())
    # bump_match_max: max per-greek feed-vs-bump on the RAW greek values (not
    # scaled terms) — proves each greek is measurable incl. volga. Use a
    # GLOBAL per-greek scale (max |feed| across the chain) so near-zero sign
    # crossings don't blow up the per-row normalization.
    bump_match_max = 0.0
    per_greek_scale = {g: 1e-9 for g in GREEKS}
    feed_vals = {g: [] for g in GREEKS}
    bump_vals = {g: [] for g in GREEKS}
    for row in rows:
        k = _extract(row, "strike")
        right = str(row.get("right", "C")).upper()[:1]
        iv = _extract(row, "implied_vol")
        if math.isnan(k) or math.isnan(iv) or iv <= 0:
            continue
        rT = _extract(row, "T", T) or T
        for g in GREEKS:
            fv = (
                _BS_GREEK_FNS[g](spot, k, rT, iv, right=right)
                if g == "delta"
                else _BS_GREEK_FNS[g](spot, k, rT, iv)
            )
            bv = _fd_greek(g, spot, k, rT, iv, right=right)
            if not math.isnan(fv) and not math.isnan(bv):
                feed_vals[g].append(fv)
                bump_vals[g].append(bv)
                per_greek_scale[g] = max(per_greek_scale[g], abs(fv))
    for g in GREEKS:
        scale = per_greek_scale[g] or 1e-9
        for fv, bv in zip(feed_vals[g], bump_vals[g]):
            bump_match_max = max(bump_match_max, abs(fv - bv) / scale)
    provenance = {g: "MEASURED" for g in GREEKS}
    return ClosureResult(
        closure_r2=float(closure_r2),
        max_per_greek_error=float(max_per_greek_error),
        bump_match_max=float(bump_match_max),
        per_greek_error=per_greek_error,
        per_greek_provenance=provenance,
        n_options=n_ok,
    )


def _r2(y, yhat):
    y = np.asarray(y, dtype=float)
    yhat = np.asarray(yhat, dtype=float)
    if len(y) < 2 or np.std(y) == 0:
        return 0.0
    ss_res = float(np.sum((y - yhat) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0


# ---------------------------------------------------------------------------
# Phase 2 — Execution-locus futures-flow map
# ---------------------------------------------------------------------------
def _signflip(a: float, b: float) -> bool:
    """True if `a` and `b` are a genuine sign change (both nonzero, opposite)."""
    return (a > 0 > b) or (a < 0 < b)


@dataclass
class ExecutionLocus:
    """Collapse of the per-strike vector to execution LEVELS."""

    spot: float
    zero_gamma: float
    call_wall: float
    put_wall: float
    band_lower: float
    band_upper: float
    tolerance_pct: float
    residual_delta: float
    gex_slope: float  # local dollar-gamma-per-1% slope at the zero-gamma level
    local_gamma_boundary: float = 0.0
    call_gamma_wall: float = 0.0
    put_gamma_wall: float = 0.0


def execution_locus(
    rows: list[dict],
    spot: float,
    T: float = 0.25,
    tolerance_pct: float = 0.01,
    q: float = 0.0,
    ne: NetExposure | None = None,
) -> ExecutionLocus:
    """Compute the execution-locus map from a chain (rows -> NetExposure).

    - zero_gamma level: the strike where cumulative signed dealer gamma (in
      dollar-gamma-per-1% units) crosses zero.
    - call_wall / put_wall: the strike of highest concentrated OI above / below
      spot (per right).
    - tolerance band: spot +- tolerance_pct (delta-hedge band).
    - gex_slope: local slope of dollar-gamma-per-1% vs strike at zero-gamma.
    - residual_delta: the carry DEX (signed shares) not yet offset.
    """
    ne = ne if ne is not None else build_net_exposure(rows, spot, T=T, q=q)
    strikes = sorted({r.strike for r in ne.rows})
    # VannaCharm GEX (call+/put-), not SVI book — wing SVI marks were
    # putting TSLA's flip at $25 with spot $341.
    dg_per_strike: dict[float, float] = {}
    for k in strikes:
        dg = sum(
            r.greeks.get("gamma", 0.0) * r.oi * CONTRACT_MULTIPLIER * spot**2 * 0.01
            for r in ne.rows
            if abs(r.strike - k) < 1e-9
        )
        dg_per_strike[k] = dg
    ordered = sorted(strikes)
    # zero-gamma = the LOCAL (per-strike) signed dollar-gamma sign-change
    # nearest spot. A pure cumulative-crossing test spuriously lands on the
    # heavy CALL WALL when the below-spot put book keeps the cumulative net
    # negative the whole way up (see test_expiry_book_phase2_locus). The local
    # boundary is where dealer gamma actually flips from net-short (put-heavy,
    # -gamma) to net-long (call-heavy, +gamma), and it excludes the wall spur.
    flip_strikes = sorted(
        {
            k
            for i in range(1, len(ordered))
            if _signflip(
                dg_per_strike.get(ordered[i - 1], 0.0),
                dg_per_strike.get(ordered[i], 0.0),
            )
            for k in (ordered[i - 1], ordered[i])
        }
    )
    # Ignore far-wing noise (TSLA $25 flip with spot $341). Keep flips
    # inside ±50% of spot; if none, fall back to all flips then cumulative.
    near = [k for k in flip_strikes if spot > 0 and abs(k - spot) / spot <= 0.50]
    pick_from = near or flip_strikes
    if pick_from:
        zero_gamma = min(pick_from, key=lambda k: (abs(k - spot), -k))
    else:
        # fallback: cumulative crossing (no local sign change found)
        cum = 0.0
        zero_gamma = ordered[0]
        prev_sign = 0
        for i, k in enumerate(ordered):
            cum += dg_per_strike[k]
            sign = 1 if cum > 0 else (-1 if cum < 0 else 0)
            if i > 0 and prev_sign != 0 and sign != 0 and sign != prev_sign:
                zero_gamma = k
                break
            if sign != 0:
                prev_sign = sign
            zero_gamma = k
    # OI concentration per strike per right
    oi_by_right_strike: dict[tuple[str, float], float] = {}
    for r in ne.rows:
        oi_by_right_strike[(r.right, r.strike)] = (
            oi_by_right_strike.get((r.right, r.strike), 0.0) + r.oi
        )
    call_strikes = [k for k in strikes if k > spot]
    put_strikes = [k for k in strikes if k < spot]
    gex_by_right_strike: dict[tuple[str, float], float] = defaultdict(float)
    for r in ne.rows:
        gex_by_right_strike[(r.right, r.strike)] += (
            r.greeks.get("gamma", 0.0) * r.oi * CONTRACT_MULTIPLIER * spot**2 * 0.01
        )
    call_wall = (
        max(call_strikes, key=lambda k: abs(gex_by_right_strike.get(("C", k), 0.0)))
        if call_strikes
        else spot
    )
    put_wall = (
        max(put_strikes, key=lambda k: abs(gex_by_right_strike.get(("P", k), 0.0)))
        if put_strikes
        else spot
    )
    band_lower = spot * (1 - tolerance_pct)
    band_upper = spot * (1 + tolerance_pct)
    # local GEX slope: net dollar-gamma-per-1% across the window from the
    # zero-gamma level up to the call wall, normalized per 1% of spot. This is
    # the average dealer hedging intensity on the up-move side, and (unlike a
    # 2-strike central difference at the locus) it IS perturbed by the call-wall
    # OI concentration — a heavier wall raises the burst size (Phase-2 pin).
    window_hi = max(call_wall, zero_gamma)
    above = [k for k in ordered if k > zero_gamma]
    if above:
        window_hi = max(window_hi, above[0])
    window_strikes = [k for k in ordered if zero_gamma <= k <= window_hi]
    window_dg = sum(dg_per_strike.get(k, 0.0) for k in window_strikes)
    window_pct = max((window_hi - zero_gamma) / spot, 1e-9)
    gex_slope = window_dg / window_pct if window_pct > 0 else 0.0
    residual_delta = ne.dex()
    return ExecutionLocus(
        spot=spot,
        zero_gamma=zero_gamma,
        call_wall=call_wall,
        put_wall=put_wall,
        band_lower=band_lower,
        band_upper=band_upper,
        tolerance_pct=tolerance_pct,
        residual_delta=residual_delta,
        gex_slope=gex_slope,
        local_gamma_boundary=float(zero_gamma),
        call_gamma_wall=float(call_wall),
        put_gamma_wall=float(put_wall),
    )


def hedge_flow_at(locus: ExecutionLocus, price: float) -> float:
    """Threshold-gated burst: flow fires only when `price` breaches the
    tolerance band, sized by the local GEX slope, sign by side.

    - price inside band -> 0.0 (no flow).
    - price above upper band -> -burst (sell pressure), proportional to gex_slope.
    - price below lower band -> +burst (buy pressure), proportional to gex_slope.
    """
    if locus.band_lower <= price <= locus.band_upper:
        return 0.0
    # breach depth relative to band
    if price > locus.band_upper:
        depth = (price - locus.band_upper) / locus.band_upper
        return -locus.gex_slope * depth
    depth = (locus.band_lower - price) / locus.band_lower
    return locus.gex_slope * depth


# ---------------------------------------------------------------------------
# Phase 3 — Scenario-conditional hedge-flow budget (THE ACTIONABLE OUTPUT)
# ---------------------------------------------------------------------------
# Named scenarios (plan §5 Phase 3): dS=+-1%, dIV=+-1 vol pt, dt=1 day toward
# OpEx. Each scenario-cell is the forced dealer hedge flow = SUM over strikes
# (signed sensitivity x OI x multiplier x scenario shock), weighted by the
# Phase-2 execution-locus activation.
SCENARIOS = ("up_1pct", "down_1pct", "iv_up_1pt", "iv_down_1pt", "dt_1d_opex")
# DEX is a CARRY DESCRIPTOR ONLY — never a forecast channel (plan §3/§4).
FLOW_CHANNELS = ("gamma", "vanna", "charm", "vega", "volga")
CHANNEL_SPLIT = {
    "stock/futures": ("gamma", "vanna", "charm"),
    "options/vol": ("vega", "volga"),
    "carry_descriptor": ("delta",),
}


@dataclass
class ScenarioBudget:
    """Scenario x channel hedge-flow matrix (the actionable output)."""

    ticker: str
    spot: float
    scenarios: dict[str, dict[str, float]]  # scenario -> channel -> signed flow
    carry_descriptor: float  # DEX (post-multiplier shares), carry only
    activation: dict[str, float]  # per-scenario Phase-2 locus activation
    channel_split: dict[str, tuple[str, ...]]

    def flow(self, scenario, channel):
        return self.scenarios.get(scenario, {}).get(channel, 0.0)


def _locus_activation(locus: ExecutionLocus, scenario: str, d_iv: float) -> float:
    """Weight each scenario-cell by Phase-2 locus activation.

    A spot-move scenario activates only to the degree it breaches the
    delta-hedge tolerance band (ramps 0 -> 1 from the band edge outward,
    saturating at full breach). Vol/tim e scenarios activate fully by
    construction (they are exogenous shocks, not price-dependent).
    """
    if scenario in ("up_1pct", "down_1pct"):
        half_width = locus.band_upper - locus.spot
        breach = 0.01 * locus.spot  # 1% spot move
        return min(1.0, breach / max(half_width, 1e-9))
    if scenario in ("iv_up_1pt", "iv_down_1pt"):
        return 1.0 if d_iv > 0 else 1.0
    return 1.0  # dt_1d_opex


def scenario_hedge_flow(
    rows: list[dict],
    spot: float,
    T: float = 0.25,
    dte: int | None = None,
    tolerance_pct: float = 0.01,
    d_iv: float = 0.01,
    ticker: str = "MOCK",
    q: float = 0.0,
    ne: NetExposure | None = None,
) -> ScenarioBudget:
    """Build the scenario x channel hedge-flow budget for a chain.

    For each named scenario, report the forced dealer hedge flow as one signed
    number per hedging channel. DEX appears ONLY as carry_descriptor (never a
    forecast). Channel split is exhaustive and non-overlapping over the six
    greeks.
    """
    ne = (
        ne
        if ne is not None
        else build_net_exposure(rows, spot, T=T, dte=dte, ticker=ticker, q=q)
    )
    locus = execution_locus(rows, spot, T=T, tolerance_pct=tolerance_pct, q=q, ne=ne)
    budget: dict[str, dict[str, float]] = {s: {} for s in SCENARIOS}

    def _sum_exposure(greek):
        return sum(r.exposure_of(greek) for r in ne.rows)

    use_book = any(r.gamma_book != 0.0 or r.book_sign != 0.0 for r in ne.rows)
    gex = ne.book_gamma() if use_book else ne.gex()
    for scen, sgn in (("up_1pct", +1.0), ("down_1pct", -1.0)):
        act = _locus_activation(locus, scen, d_iv)
        budget[scen]["gamma"] = -gex * sgn * act
        budget[scen]["vanna"] = 0.0
        budget[scen]["charm"] = 0.0
        budget[scen]["vega"] = 0.0
        budget[scen]["volga"] = 0.0
    # --- vol scenarios (VEX lead, vega/volga options-vol channel) ---
    vega_tot = _sum_exposure("vega")
    volga_tot = _sum_exposure("volga")
    for scen, sgn in (("iv_up_1pt", +1.0), ("iv_down_1pt", -1.0)):
        budget[scen]["vanna"] = vanna_flow(ne, sgn * d_iv)
        budget[scen]["vega"] = vega_tot * sgn
        # volga is quadratic in dIV -> symmetric second-order contribution
        budget[scen]["volga"] = 0.5 * volga_tot * (d_iv / 0.01) ** 2
        budget[scen]["gamma"] = 0.0
        budget[scen]["charm"] = 0.0
    # --- time scenario (ChaEX, DTE -> OpEx clockwork) ---
    budget["dt_1d_opex"]["charm"] = _sum_exposure("charm")  # shares/day
    budget["dt_1d_opex"]["gamma"] = 0.0
    budget["dt_1d_opex"]["vanna"] = 0.0
    budget["dt_1d_opex"]["vega"] = 0.0
    budget["dt_1d_opex"]["volga"] = 0.0

    activation = {s: _locus_activation(locus, s, d_iv) for s in SCENARIOS}
    return ScenarioBudget(
        ticker=ticker,
        spot=spot,
        scenarios=budget,
        carry_descriptor=ne.dex(),
        activation=activation,
        channel_split=dict(CHANNEL_SPLIT),
    )


# ---------------------------------------------------------------------------
# Phase 4 — Structural / regime arm (co-primary "better seed", FRESH)
# ---------------------------------------------------------------------------
@dataclass
class StructuralRegime:
    """Term-structure-weighted, multi-expiry persistent-carry object.

    Fresh construction — NO dependency on the live replication RP seed. It
    sets the DIRECTION of the carry at the regime level (the improved
    replacement for the old single-anchor accumulation), event-clocked.
    """

    regime: str  # persistent-short-gamma / persistent-long-gamma / neutral
    term_structure_flag: str  # contango / backwardation / flat
    carry: float  # term-structure-weighted net dollar-gamma-per-1%
    persistence: float  # fraction of expiries agreeing with the weighted sign
    per_expiry_gamma: dict[str, float]
    per_expiry_carry: dict[str, float]
    event_clock: dict[str, bool]


def _book_sigma_atm(book_rows: list[dict], spot: float) -> float | None:
    """ATM implied vol for a book (mean IV over strikes within +-5% of spot)."""
    ivs = []
    for r in book_rows:
        k = _extract(r, "strike")
        iv = _extract(r, "implied_vol")
        if math.isnan(k) or math.isnan(iv) or iv <= 0:
            continue
        if abs(k - spot) <= 0.05 * spot:
            ivs.append(iv)
    if not ivs:
        return None
    return float(sum(ivs) / len(ivs))


def build_structural_regime(
    expiry_books: list[dict],
    term_weights: dict[str, float] | None = None,
    opex_window_days: int = 5,
    gamma_tol: float | None = None,
    q: float = 0.0,
) -> StructuralRegime:
    """Build the structural/regime arm from multiple expiry books.

    expiry_books: list of {'expiry', 'spot', 'rows', 'T'(years), ['dte']}.
    Per-expiry directional carry = SVI-signed OTM book_gamma
    (dollar-gamma-per-1%), NOT imported GEX. Persistent carry is the
    term-weighted sum of those book gammas.
    term-weight favoring the near/medium tenor (w(T) = 1/(1+T) by default).
    term_structure_flag compares ATM IV at the near vs far tenor.
    """
    books = sorted(expiry_books, key=lambda b: float(b.get("T", 1.0)))
    if not books:
        raise ValueError("structural regime: no expiry books")
    per_gamma: dict[str, float] = {}
    per_carry: dict[str, float] = {}
    sig_atm: dict[str, float | None] = {}
    for b in books:
        exp = str(b.get("expiry", "?"))
        spot = float(b.get("spot", 0.0))
        rows = b.get("rows", [])
        if not rows or not spot:
            per_gamma[exp] = 0.0
            per_carry[exp] = 0.0
            sig_atm[exp] = None
            continue
        ne = build_net_exposure(rows, spot, T=float(b.get("T", 0.25)), q=q)
        chain_iv = {
            (r.strike, r.right): r.iv for r in ne.rows if r.iv == r.iv and r.iv > 0
        }
        oi_by = {(r.strike, r.right): int(r.oi) for r in ne.rows}
        if chain_iv:
            ov = svi_rp_overlay(
                chain_iv,
                spot,
                float(b.get("T", 0.25)),
                oi_by=oi_by,
                q=q,
                r=RISK_FREE_RATE,
            )
            apply_svi_book_signs(ne, ov, spot, float(b.get("T", 0.25)))
            per_gamma[exp] = ne.book_gamma()
        else:
            per_gamma[exp] = 0.0
        per_carry[exp] = ne.dex()
        sig_atm[exp] = _book_sigma_atm(rows, spot)

    # term-structure weight (persist to near/medium tenor)
    if term_weights is None:
        term_weights = {}
        for b in books:
            exp = str(b.get("expiry", "?"))
            T = float(b.get("T", 1.0))
            term_weights[exp] = 1.0 / (1.0 + max(T, 1e-6))
    wsum = sum(max(term_weights.get(exp, 0.0), 0.0) for exp in per_gamma) or 1.0
    carry = (
        sum(max(term_weights.get(exp, 0.0), 0.0) * per_gamma[exp] for exp in per_gamma)
        / wsum
    )

    # persistence = fraction of NONZERO expiries agreeing with weighted sign
    wsign = 1.0 if carry > 0 else (-1.0 if carry < 0 else 0.0)
    nonzero = [exp for exp in per_gamma if per_gamma[exp] != 0.0]
    agreeing = [exp for exp in nonzero if (per_gamma[exp] > 0) == (wsign > 0)]
    persistence = (len(agreeing) / len(nonzero)) if nonzero else 0.0

    if gamma_tol is None:
        mag = [abs(v) for v in per_gamma.values()]
        gamma_tol = 0.05 * (sum(mag) / len(mag)) if mag else 0.0
    if abs(carry) < float(gamma_tol):
        regime = "neutral"
    elif carry < 0:
        regime = "persistent-short-gamma"
    else:
        regime = "persistent-long-gamma"

    # term-structure flag: ATM IV at near vs far tenor
    nonnull = [
        (b, sig_atm.get(str(b.get("expiry", "?"))))
        for b in books
        if sig_atm.get(str(b.get("expiry", "?"))) is not None
    ]
    flag = "flat"
    if len(nonnull) >= 2:
        T_near = min(nonnull, key=lambda t: float(t[0].get("T", 1e9)))
        T_far = max(nonnull, key=lambda t: float(t[0].get("T", -1e9)))
        if float(T_far[0].get("T", 0)) > float(T_near[0].get("T", 0)):
            if T_far[1] > T_near[1]:
                flag = "contango"
            elif T_far[1] < T_near[1]:
                flag = "backwardation"

    # event clock: OpEx crescendo = an accumulated charm channel near expiry;
    # short-DTE anchor present.
    dtemap = {str(b.get("expiry", "?")): int(b.get("dte", 0) or 0) for b in books}
    event_clock = {
        "opex_crescendo": any(
            0 < dtemap.get(exp, 0) <= opex_window_days and dtemap.get(exp, 0) <= 5
            for exp in dtemap
        ),
        "short_dte_anchor": any(0 < dtemap.get(exp, 0) <= 3 for exp in dtemap),
    }
    return StructuralRegime(
        regime=regime,
        term_structure_flag=flag,
        carry=float(carry),
        persistence=float(persistence),
        per_expiry_gamma=per_gamma,
        per_expiry_carry=per_carry,
        event_clock=event_clock,
    )


# ---------------------------------------------------------------------------
# Phase 5 — SVI-RP overlays (fixed-strike cheap/rich + term-structure flag)
# ---------------------------------------------------------------------------
@dataclass
class SviOverlay:
    ticker: str
    sigma_atm: float
    cheap_strikes: list[float]
    rich_strikes: list[float]
    marks: list[tuple[float, str, str, float]]  # (strike, right, mark, diff)
    net_cheap_oi: float
    net_rich_oi: float
    term_structure_flag: str
    butterfly_clamped: bool


def _term_structure_flag(tenor_vols: list[tuple[float, float]]) -> str:
    """contango if ATM vol increases with tenor, backwardation if it falls."""
    tv = [(float(t), float(v)) for (t, v) in tenor_vols if v and v > 0]
    if len(tv) < 2:
        return "flat"
    t_near = min(tv, key=lambda x: x[0])
    t_far = max(tv, key=lambda x: x[0])
    if t_far[0] <= t_near[0]:
        return "flat"
    if t_far[1] > t_near[1]:
        return "contango"
    if t_far[1] < t_near[1]:
        return "backwardation"
    return "flat"


def svi_rp_overlay(
    chain_iv: dict[tuple[float, str], float],
    spot: float,
    T: float,
    oi_by: dict[tuple[float, str], int] | None = None,
    tenor_vols: list[tuple[float, float]] | None = None,
    ticker: str = "MOCK",
    deadband: float = BOOK_SIGN_DEADBAND,
    r: float = RISK_FREE_RATE,
    q: float = 0.0,
) -> SviOverlay:
    """Fixed-strike cheap/rich overlay via svi_rp.calibrate_svi + term flag.

    Reuses `svi_rp` (read-only); does NOT modify vol_surface_reference.py.
    A strike whose market IV is BELOW the fitted reference smile is marked
    LONG/cheap (dealer long / mean-revert-prone); above -> SHORT/rich.
    |diff| <= deadband -> UNMARKED (no book-sign).
    """
    import svi_rp

    ref = svi_rp.calibrate_svi(chain_iv, spot, T, oi_by=oi_by, r=r, q=q)
    marks_raw = ref.mark_chain(chain_iv, oi_by, deadband=deadband)
    marks: list[tuple[float, str, str, float]] = [
        (float(k), right, mark, float(diff))
        for (k, right, _, _, diff, mark, _) in marks_raw
    ]
    cheap = sorted({float(k) for (k, _, mark, _) in marks if mark == "LONG"})
    rich = sorted({float(k) for (k, _, mark, _) in marks if mark == "SHORT"})
    oi_map = oi_by or {}
    net_cheap = sum(
        int(oi_map.get((k, right), 0))
        for (k, right, mark, _) in marks
        if mark == "LONG"
    )
    net_rich = sum(
        int(oi_map.get((k, right), 0))
        for (k, right, mark, _) in marks
        if mark == "SHORT"
    )
    flag = _term_structure_flag(tenor_vols) if tenor_vols else "flat"
    return SviOverlay(
        ticker=ticker,
        sigma_atm=ref.sigma_atm,
        cheap_strikes=cheap,
        rich_strikes=rich,
        marks=marks,
        net_cheap_oi=float(net_cheap),
        net_rich_oi=float(net_rich),
        term_structure_flag=flag,
        butterfly_clamped=ref.butterfly_clamped,
    )


# ---------------------------------------------------------------------------
# Phase 6 — Pre-registered falsifier (GEX flow -> forward returns, network-free)
# ---------------------------------------------------------------------------
FALSIFIER_FORCE = "FALSIFIER_FORCE"
_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "_expiry_falsifier_cache"
)
_PERM_P_THRESHOLD = 0.05
_BH_Q_THRESHOLD = 0.10
_MIN_USABLE_DAYS = 100
_MIN_TICKERS = 12
_CORR_THRESHOLD_DEFAULT = 0.15
_N_PERMS = 500
_BLOCK_SIZE = 5
VERDICTS = ("SUPPORTED", "NOT_SUPPORTED", "INCONCLUSIVE", "SIGN_FLIP")

INDEX_TICKERS = {"SPY", "QQQ", "IWM", "DIA", "^VIX"}


def per_underline_class(ticker: str) -> str:
    """index keeps call+/put- + 0DTE exclusion; single names get a
    low-confidence inventory label."""
    return "index" if ticker.upper() in INDEX_TICKERS else "single-name"


def _safe_corr(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    n = min(len(a), len(b))
    if n < 2 or np.std(a[:n]) == 0 or np.std(b[:n]) == 0:
        return 0.0
    return float(np.corrcoef(a[:n], b[:n])[0, 1])


def _block_perm_p(
    signal, forward, n_perms=_N_PERMS, block_size=_BLOCK_SIZE, seed=0
) -> float:
    """Two-sided block-permutation p-value on |corr(signal, forward)|.
    Shuffles contiguous blocks of the signal (preserves short-range
    autocorrelation) and counts how often a random permutation produces a
    |corr| >= the observed one."""
    n = len(signal)
    corr = abs(_safe_corr(signal, forward))
    if n < 4:
        return 1.0
    rng = np.random.RandomState(seed)
    bs = max(1, min(block_size, n // 2))
    nb = n // bs
    hits = 0
    for _ in range(n_perms):
        order = rng.permutation(nb)
        pieces = [signal[b * bs : (b + 1) * bs] for b in order]
        shuffled = np.concatenate(pieces)
        if len(shuffled) < n:
            shuffled = np.concatenate([shuffled, signal[len(shuffled) : n]])
        if abs(_safe_corr(shuffled[:n], forward)) >= corr:
            hits += 1
    return hits / n_perms


def _bh_qvalues(pvalues: dict[str, float]) -> dict[str, float]:
    """Benjamini-Hochberg q-values over the greek channel family."""
    items = sorted(pvalues.items(), key=lambda kv: kv[1])
    m = len(items)
    q: dict[str, float] = {}
    if m == 0:
        return q
    prev = 1.0
    for rank, (name, p) in enumerate(reversed(items), start=1):
        qval = p * m / (m - rank + 1)
        qval = min(qval, prev)  # enforce monotonicity (BH step-up)
        prev = qval
        q[name] = qval
    # reverse map: above loop goes from largest p down, so q is monotone
    return q


@dataclass
class DailySignals:
    ticker: str
    dates: list[str]
    gex_flow: list[float]
    vanna_flow: list[float]
    charm: list[float]
    fwd_ret: list[float]
    fwd_ret_sign: list[float]
    iv_shock: list[float]

    def n_days(self):
        return len(self.dates)


def _date_of(row, key="date"):
    d = row.get(key)
    if d is None:
        d = str(row.get("created", ""))[:10].replace("-", "")
    return str(d)[:8].replace("-", "")


def build_daily_signals(
    hist_greek_rows: list[dict],
    hist_oi_rows: list[dict],
    hist_spot_rows: list[dict],
    expiry: str,
    ticker: str = "MOCK",
    iv_shock_vol: float = 0.01,
) -> DailySignals:
    """Build per-day greek-flow signals + forward 1-day returns from
    historical rows (network-free). GEX flow = day-over-day change in net
    dollar-gamma-per-1%; vanna flow = daily VEX; charm = daily ChaEX. The
    forward return is the NEXT day's signed return."""
    from datetime import datetime

    try:
        exp_dt = datetime.strptime(str(expiry), "%Y%m%d")
    except Exception:
        exp_dt = None
    spot_by_date: dict[str, float] = {}
    for r in hist_spot_rows:
        d = _date_of(r)
        v = _extract(r, "close")
        if not math.isnan(v):
            spot_by_date[d] = v
    greeks_by_date: dict[str, list[dict]] = defaultdict(list)
    for g in hist_greek_rows:
        d = _date_of(g)
        k = _extract(g, "strike")
        if not math.isnan(k) and abs(k) >= 1000:
            k = k / 1000.0  # theta thousandths (SPY 150000 -> 150; SLS 4000 -> 4)
        iv = _extract(g, "implied_vol")
        if not math.isnan(k) and not math.isnan(iv) and iv > 0:
            greeks_by_date[d].append(
                {
                    "strike": k,
                    "right": str(g.get("right", "C")).upper()[:1],
                    "implied_vol": iv,
                }
            )
    oi_by_date: dict[str, dict[tuple[float, str], int]] = defaultdict(dict)
    for o in hist_oi_rows:
        d = _date_of(o)
        k = _extract(o, "strike")
        if not math.isnan(k) and abs(k) >= 1000:
            k = k / 1000.0
        oi_by_date[d][(k, str(o.get("right", "C")).upper()[:1])] = int(
            _extract(o, "open_interest", 0.0)
        )
    ordered = sorted(spot_by_date)
    gex = []
    vanna = []
    charm = []
    fwd_ret = []
    iv_shock = []
    dates = []
    prev_ivs = {}
    prev_gex = None
    for d in ordered:
        if d not in greeks_by_date:
            continue
        spot = spot_by_date[d]
        rows = [
            {
                "strike": g["strike"],
                "right": g["right"],
                "oi": oi_by_date[d].get((g["strike"], g["right"]), 0),
                "implied_vol": g["implied_vol"],
            }
            for g in greeks_by_date[d]
        ]
        T = 0.25
        if exp_dt is not None:
            try:
                ddt = datetime.strptime(d, "%Y%m%d")
                T = max((exp_dt - ddt).days, 1) / 365.0
            except Exception:
                T = 0.25
        ne = build_net_exposure(rows, spot, T=T, ticker=ticker)
        g = ne.gex()
        gex.append(g if prev_gex is None else g - prev_gex)
        prev_gex = g
        vanna.append(vanna_flow(ne, 0.01))
        charm.append(sum(r.exposure_of("charm") for r in ne.rows))
        # exogenous |dIV| shock detector at ATM
        ivs = [r["implied_vol"] for r in rows if abs(r["strike"] - spot) <= 0.05 * spot]
        atm = float(sum(ivs) / len(ivs)) if ivs else 0.0
        shock = 0.0
        if prev_ivs:
            shock = abs(atm - prev_ivs)
        prev_ivs = atm
        iv_shock.append(shock)
        dates.append(d)
    # forward 1-day return (from spot) aligned to signal dates
    spot_idx = {d: i for i, d in enumerate(ordered)}
    for d in dates:
        i = spot_idx.get(d)
        if i is not None and i + 1 < len(ordered):
            s_now = spot_by_date[ordered[i]]
            s_nxt = spot_by_date[ordered[i + 1]]
            r = (s_nxt - s_now) / s_now if s_now else 0.0
            fwd_ret.append(r)
        else:
            break
    # align lengths (drop trailing day(s) with no forward return)
    minlen = min(len(dates), len(fwd_ret))
    return DailySignals(
        ticker=ticker,
        dates=dates[:minlen],
        gex_flow=gex[:minlen],
        vanna_flow=vanna[:minlen],
        charm=charm[:minlen],
        fwd_ret=fwd_ret[:minlen],
        fwd_ret_sign=[
            1.0 if r > 0 else (-1.0 if r < 0 else 0.0) for r in fwd_ret[:minlen]
        ],
        iv_shock=iv_shock[:minlen],
    )


_SEED_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "docs",
    "Dealer posistioning notes",
    "_extracted",
    "handoff_20260812",
    "seed_data",
)


def _load_seed(ticker: str) -> dict:
    import glob

    pat = os.path.join(_SEED_DIR, f"seed_data_{ticker.upper()}_*.json")
    files = glob.glob(pat)
    if not files:
        raise FileNotFoundError(f"no seed corpus for {ticker} in {_SEED_DIR}")
    with open(files[0]) as fh:
        return json.load(fh)


def build_daily_signals_from_seed(ticker: str) -> DailySignals:
    """Build DailySignals from the offline seed corpus (network-free)."""
    seed = _load_seed(ticker)
    expiry = str(seed.get("manifest", {}).get("expiry", ""))
    return build_daily_signals(
        seed.get("greeks", []),
        seed.get("oi", []),
        seed.get("spot", []),
        expiry,
        ticker=ticker,
    )


def seed_corpus_tickers() -> list[str]:
    import glob

    tickers = []
    for f in glob.glob(os.path.join(_SEED_DIR, "seed_data_*.json")):
        base = os.path.basename(f)
        tickers.append(base[len("seed_data_") :].split("_")[0].upper())
    return sorted(tickers)


@dataclass
class ChannelVerdict:
    ticker: str
    channel: str
    n_days: int
    corr: float
    block_perm_p: float
    q_value: float
    verdict: str


@dataclass
class ExpiryFalsifierRun:
    tickers: list[str]
    overall: str
    primary_gex: dict[str, ChannelVerdict]
    channels: dict[str, ChannelVerdict]
    spy_qqq_sign_consistent: bool
    arms: dict[str, object] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _channel_verdict(ticker, channel, signals: DailySignals, n_perms, corr_threshold):
    if channel == "gex":
        sig = signals.gex_flow
    elif channel == "vanna":
        sig = signals.vanna_flow
    else:
        sig = signals.charm
    fwd = signals.fwd_ret_sign
    n = signals.n_days()
    corr = _safe_corr(sig, fwd)
    p = _block_perm_p(sig, fwd, n_perms=n_perms)
    if n < _MIN_USABLE_DAYS:
        verdict = "INCONCLUSIVE"
    elif abs(corr) >= corr_threshold and p < _PERM_P_THRESHOLD:
        verdict = "SUPPORTED"
    else:
        verdict = "NOT_SUPPORTED"
    return ChannelVerdict(
        ticker=ticker,
        channel=channel,
        n_days=n,
        corr=corr,
        block_perm_p=p,
        q_value=1.0,
        verdict=verdict,
    )


def run_expiry_falsifier(
    tickers_data: dict[str, DailySignals],
    n_perms: int = _N_PERMS,
    corr_threshold: float = _CORR_THRESHOLD_DEFAULT,
    min_tickers: int = _MIN_TICKERS,
) -> ExpiryFalsifierRun:
    """Pre-registered primary falsifier: GEX flow -> forward 1-day return sign.

    Rules (pre-registered): n_tickers >= min_tickers, n_days >= 100/ticker,
    |corr| >= corr_threshold, block-perm p < 0.05, BH q < 0.10 over the greek
    family, and SPY/QQQ sign-consistency (a flip = INCONCLUSIVE/FAIL).
    """
    channels = {}
    for tk, sig in tickers_data.items():
        for ch in ("gex", "vanna", "charm"):
            channels[(tk, ch)] = _channel_verdict(tk, ch, sig, n_perms, corr_threshold)
    # BH multiplicity over the greek family (per channel, pooled across tickers)
    fam_p = {}
    for ch in ("gex", "vanna", "charm"):
        ps = [channels[(tk, ch)].block_perm_p for tk in tickers_data]
        fam_p[ch] = float(np.mean(ps)) if ps else 1.0
    qvals = _bh_qvalues(fam_p)
    for tk in tickers_data:
        for ch in ("gex", "vanna", "charm"):
            channels[(tk, ch)].q_value = qvals[ch]

    # primary GEX per-ticker verdicts
    primary = {tk: channels[(tk, ch)] for tk in tickers_data for ch in ("gex",)}
    # SPY/QQQ sign-consistency on the PRIMARY construct
    sign_consistent = True
    spy_c = qqq_c = None
    for tk in ("SPY", "QQQ"):
        if tk in tickers_data:
            c = channels[(tk, "gex")].corr
            if tk == "SPY":
                spy_c = c
            else:
                qqq_c = c
    if spy_c is not None and qqq_c is not None:
        if (spy_c > 0) != (qqq_c > 0):
            sign_consistent = False

    notes = []
    if len(tickers_data) < min_tickers:
        notes.append(
            f"below pre-registered n_tickers floor ({len(tickers_data)}<{min_tickers})"
        )
    if not sign_consistent:
        notes.append("SPY/QQQ sign flip on primary GEX construct -> INCONCLUSIVE/FAIL")

    # overall: require primary GEX supported, sign-consistent, q<0.10
    supported = all(
        v.verdict == "SUPPORTED" and v.q_value < _BH_Q_THRESHOLD
        for v in primary.values()
    )
    if not sign_consistent:
        overall = "SIGN_FLIP"
    elif len(tickers_data) < min_tickers:
        overall = "INCONCLUSIVE"
    elif supported:
        overall = "SUPPORTED"
    else:
        overall = "NOT_SUPPORTED"
    return ExpiryFalsifierRun(
        tickers=list(tickers_data),
        overall=overall,
        primary_gex=primary,
        channels=channels,
        spy_qqq_sign_consistent=sign_consistent,
        notes=notes,
    )


# --- Phase 6 arms ----------------------------------------------------------
def vanna_lead_arm(
    signals: DailySignals,
    iv_shock_threshold: float = 0.005,
    k: int = 1,
    n_perms: int = _N_PERMS,
) -> ChannelVerdict:
    """Event-gated vanna lead arm: test vanna flow(t) -> forward return(t+k)
    ONLY on days with a dated exogenous |dIV| shock >= threshold. On days with
    no shock, the channel is silent (vanna is never a standalone daily lead)."""
    idx = [
        i for i in range(signals.n_days()) if signals.iv_shock[i] >= iv_shock_threshold
    ]
    sig = np.array([signals.vanna_flow[i] for i in idx], dtype=float)
    fwd = np.array(
        [signals.fwd_ret_sign[min(i + k, signals.n_days() - 1)] for i in idx],
        dtype=float,
    )
    n = len(idx)
    corr = _safe_corr(sig, fwd)
    p = _block_perm_p(sig, fwd, n_perms=n_perms)
    if n < _MIN_USABLE_DAYS:
        verdict = "INCONCLUSIVE"
    elif abs(corr) >= _CORR_THRESHOLD_DEFAULT and p < _PERM_P_THRESHOLD:
        verdict = "SUPPORTED"
    else:
        verdict = "NOT_SUPPORTED"
    return ChannelVerdict(
        ticker=signals.ticker,
        channel="vanna_lead",
        n_days=n,
        corr=corr,
        block_perm_p=p,
        q_value=1.0,
        verdict=verdict,
    )


def opex_event_window_arm(
    signals: DailySignals, opex_dates: list[str], k: int = 1
) -> ChannelVerdict:
    """Event-window arm: GEX flow -> forward return measured on OpEx Thu->Fri.
    Each channel is falsified at the horizon it actually acts on."""
    idx = [i for i, d in enumerate(signals.dates) if d in set(opex_dates)]
    sig = np.array([signals.gex_flow[i] for i in idx], dtype=float)
    fwd = np.array(
        [signals.fwd_ret_sign[min(i + k, signals.n_days() - 1)] for i in idx],
        dtype=float,
    )
    n = len(idx)
    corr = _safe_corr(sig, fwd)
    p = _block_perm_p(sig, fwd, n_perms=200)
    if n < 2:
        verdict = "INCONCLUSIVE"
    elif abs(corr) >= _CORR_THRESHOLD_DEFAULT and p < _PERM_P_THRESHOLD:
        verdict = "SUPPORTED"
    else:
        verdict = "NOT_SUPPORTED"
    return ChannelVerdict(
        ticker=signals.ticker,
        channel="opex_window",
        n_days=n,
        corr=corr,
        block_perm_p=p,
        q_value=1.0,
        verdict=verdict,
    )


def accumulated_overlay_retest(signals: DailySignals) -> dict:
    """Retained accumulated-book overlay re-test: does a multi-day accumulated
    GEX read add R2 over the same-day snapshot for forward returns?"""
    import numpy as _np

    snap = _np.asarray(signals.gex_flow, dtype=float)
    fwd = _np.asarray(signals.fwd_ret_sign, dtype=float)
    acc = _np.convolve(snap, _np.ones(3) / 3.0, mode="same")
    r2_snap = _r2(fwd, snap)
    r2_both = _r2(fwd, snap + 0.5 * acc)
    return {
        "snapshot_r2": float(r2_snap),
        "both_r2": float(r2_both),
        "delta_r2": float(r2_both - r2_snap),
        "redundant": bool(r2_both - r2_snap < 0.01),
    }


# --- Phase 6 cache wrapper (FALSIFIER_FORCE bypass) ------------------------
def _cache_path(kind: str, key: dict) -> str:
    if not os.path.isdir(_CACHE_DIR):
        os.makedirs(_CACHE_DIR, exist_ok=True)
    name = kind + "_" + "_".join(f"{k}={v}" for k, v in sorted(key.items()))
    return os.path.join(
        _CACHE_DIR, name.replace(" ", "_").replace(os.sep, "_") + ".json"
    )


def run_expiry_falsifier_cached(
    tickers_data: dict[str, DailySignals],
    n_perms: int = _N_PERMS,
    corr_threshold: float = _CORR_THRESHOLD_DEFAULT,
    force_recompute: bool | None = None,
) -> ExpiryFalsifierRun:
    """Cache wrapper honoring FALSIFIER_FORCE=1 to bypass the cache."""
    if force_recompute is None:
        force = os.environ.get(FALSIFIER_FORCE, "0") == "1"
    else:
        force = bool(force_recompute)
    key = {
        "n_perms": n_perms,
        "corr_threshold": corr_threshold,
        "tickers": ",".join(sorted(tickers_data)),
    }
    path = _cache_path("expiry_falsifier", key)
    if not force and os.path.exists(path):
        try:
            with open(path) as fh:
                raw = json.load(fh)
            return ExpiryFalsifierRun(
                tickers=raw["tickers"],
                overall=raw["overall"],
                primary_gex={},
                channels={},
                spy_qqq_sign_consistent=raw["spy_qqq_sign_consistent"],
                notes=raw.get("notes", []) + ["CACHE_HIT"],
            )
        except Exception:
            pass
    result = run_expiry_falsifier(
        tickers_data, n_perms=n_perms, corr_threshold=corr_threshold
    )
    try:
        with open(path, "w") as fh:
            json.dump(
                {
                    "tickers": result.tickers,
                    "overall": result.overall,
                    "spy_qqq_sign_consistent": result.spy_qqq_sign_consistent,
                    "notes": result.notes,
                },
                fh,
            )
    except Exception:
        pass
    return result
