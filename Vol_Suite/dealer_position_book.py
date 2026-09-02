"""Phase 7 (PLAN §8/§8.5): the ASSUMED DEALER POSITION accumulator.

150-trading-day rolling window over the full option book. Neutral seed;
the daily increment follows Cem's ΔIV-signed flow rule (deepdive §4.4 /
signal #1, plan §8.2 SELECTED VARIANT, Jason 2026-09-01):

    day_flow(strike) = day_sign * delta_oi * (vanna / mean|vanna|)

with the *day-level* sign from day-over-day ATM IV change per expiry:

    d_iv = atm_iv_today - atm_iv_prev
    |d_iv| <= iv_deadband (0.01) -> no confident vol-direction read -> the
        ENTIRE day contributes 0 for ALL strikes (NOT a fallback sign --
        a missing vol read must not invent a direction).
    d_iv < 0 (vol-down) -> day_sign = +1.0 (dealer buying back inventory)
    d_iv > 0 (vol-up)   -> day_sign = -1.0 (dealer selling / getting short)

The static per-strike SVI/SABR sign (`resolve_vol_surface_sign`, deadband
0.01 -> fallback -1.0, mirroring replication_reference.py:869) is kept as
arm (a) 'fixed_sign' for the Phase 9.2 A/B, and as the per-day fallback
inside the primary arm when the vanna weighting is unavailable.

Day-loop conventions are mirrored from replication_reference.py:841-959:
only strikes present in BOTH days accumulate (sparse endpoint gaps are
skipped, :899-905), delta_oi == 0 is skipped, and vanna is normalized by
the day's own mean |vanna| (:924-929).

DayData contract (dict or object, both accepted)::

    date:  str   YYYYMMDD
    spot:  float
    per_expiry: list of {
        'expiry': str YYYYMMDD,
        'rows': [ { 'strike': float (dollar scale),
                    'right':  'C' | 'P',
                    'oi':     float,
                    'iv':     float,
                    'vanna':  float (optional) } ]
    }

PositionBookResult (dataclass)::

    position_by_strike: dict[(strike, right), float]
    daily_trace:        list[{date, kind, d_iv, day_sign, net_change,
                               n_strikes_included, n_new_strikes}]
    dates_used:         list[str] (post-rollover, len <= lookback)
    lookback:           int
    arm:                'div_signed' | 'fixed_sign'
    total_net:          float  (sum of position_by_strike)

Run the CLI for a real cache-backed accumulation:

    python dealer_position_book.py SPY 150 [--arm div_signed|fixed_sign]
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import sys
from dataclasses import dataclass, field

import expiry_book_exposure as ebe
import vol_surface_reference

# Cache layout (Phase-1 research cache):
#   Vol_Suite/_expiry_falsifier_cache/opex_full_book/
#       greeks/YYYYMMDD_all.json.gz   (ThetaData greeks rows)
#       oi/YYYYMMDD_all.json.gz       (ThetaData OI rows)
_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "_expiry_falsifier_cache",
    "opex_full_book",
)

# |ΔIV| at or below this is "no confident vol-direction read" for the day.
# Matches VANNA_FLOW_DIV_DEADBAND / BOOK_SIGN_DEADBAND / IV_DEADBAND_VOL.
DEFAULT_IV_DEADBAND = 0.01

# Fallback static sign when the vol-surface reference cannot produce a
# confident per-strike read (deviation inside deadband -> 0.0 -> -1.0),
# mirroring replication_reference.py:869.
FALLBACK_STATIC_SIGN = -1.0


@dataclass
class PositionBookResult:
    ticker: str = "SPY"
    position_by_strike: dict[tuple[float, str], float] = field(default_factory=dict)
    daily_trace: list[dict] = field(default_factory=list)
    dates_used: list[str] = field(default_factory=list)
    lookback: int = 150
    arm: str = "div_signed"
    total_net: float = 0.0


# ---------------------------------------------------------------------------
# DayData access helpers (accept dicts or objects with attributes)
# ---------------------------------------------------------------------------


def _day_date(day) -> str:
    v = day["date"] if isinstance(day, dict) else day.date
    return str(v)


def _day_spot(day) -> float:
    v = day["spot"] if isinstance(day, dict) else day.spot
    return float(v)


def _day_per_expiry(day) -> list:
    return day["per_expiry"] if isinstance(day, dict) else day.per_expiry


def _exp_expiry(exp) -> str:
    v = exp["expiry"] if isinstance(exp, dict) else exp.expiry
    return str(v)


def _exp_rows(exp) -> list:
    return exp["rows"] if isinstance(exp, dict) else exp.rows


def _row_get(row, key, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


# ---------------------------------------------------------------------------
# Core accumulator
# ---------------------------------------------------------------------------


def _window(dates: list[str], lookback: int) -> list[str]:
    """Rolling window: keep the LAST `lookback` dates (150d rollover)."""
    if lookback and len(dates) > lookback:
        return dates[-lookback:]
    return list(dates)


def _atm_iv_from_rows(rows: list, spot: float) -> float | None:
    """ATM IV via the same otm-IV logic as ebe.atm_iv_otm (reused import).

    Rows here are the dollar-scale book rows (strike/right/iv); atm_iv_otm
    already accepts dicts or objects and prefers 'implied_vol' over 'iv'.
    """
    return ebe.atm_iv_otm(rows, spot)


def _compute_vanna_stats(rows: list) -> tuple[dict[tuple[float, str], float], float | None]:
    """vanna map + mean |vanna| over rows that carry a usable vanna."""
    vanna_map: dict[tuple[float, str], float] = {}
    abs_vals: list[float] = []
    for row in rows:
        try:
            k = float(_row_get(row, "strike"))
            right = str(_row_get(row, "right", "")).upper()[:1]
            v = _row_get(row, "vanna")
            if v is None:
                continue
            v = float(v)
            if not math.isfinite(v) or v == 0.0:
                continue
        except (TypeError, ValueError):
            continue
        vanna_map[(k, right)] = v
        abs_vals.append(abs(v))
    mean_abs = sum(abs_vals) / len(abs_vals) if abs_vals else None
    return vanna_map, mean_abs


def _day_rows_by_expiry(day) -> dict[str, list]:
    return {_exp_expiry(e): _exp_rows(e) for e in _day_per_expiry(day)}


def accumulate_position_book(
    days: list,
    lookback: int = 150,
    iv_deadband: float = DEFAULT_IV_DEADBAND,
    arm: str = "div_signed",
    ticker: str = "SPY",
) -> PositionBookResult:
    """Accumulate the assumed dealer position over a rolling `lookback` window.

    arm='div_signed' (primary): day-level sign from day-over-day ATM ΔIV;
    in-deadband days contribute exactly 0. arm='fixed_sign' (A/B arm):
    per-strike static sign from resolve_vol_surface_sign, 0.0 -> -1.0.
    """
    if arm not in ("div_signed", "fixed_sign"):
        raise ValueError(f"unknown arm {arm!r}; expected 'div_signed' or 'fixed_sign'")

    # Rollover FIRST (the window is rolling, per plan §8.5 Phase 7), then
    # accumulate pairs inside the window.
    dates_all = [_day_date(d) for d in days]
    keep = _window(list(range(len(days))), lookback)
    days = [days[i] for i in keep]
    dates = [dates_all[i] for i in keep]

    position: dict[tuple[float, str], float] = {}
    daily_trace: list[dict] = []

    # Neutral seed: day 1 contributes nothing (no prev day to Δ against).
    if days:
        daily_trace.append(
            {
                "date": dates[0],
                "kind": "seed",
                "d_iv": None,
                "day_sign": None,
                "net_change": 0.0,
                "n_strikes_included": 0,
                "n_new_strikes": 0,
            }
        )

    for prev_day, day in zip(days[:-1], days[1:]):
        d = _day_date(day)
        prev_date = _day_date(prev_day)
        rows_today = _day_rows_by_expiry(day)
        rows_prev = _day_rows_by_expiry(prev_day)

        day_change = 0.0
        n_included = 0
        n_new_strikes = 0

        # --- PRIMARY ARM: day-level ΔIV sign, computed per expiry ---
        # One expiry may lack readable ATM IV on either side -> fall back
        # per-day (per expiry), matching plan §8.2 ("static SVI/SABR sign
        # ... kept as per-day fallback when ATM IV is unavailable").
        day_sign_for_expiry: dict[str, float | None] = {}
        d_iv_for_expiry: dict[str, float | None] = {}

        if arm == "div_signed":
            spot = _day_spot(day)
            for expiry, rows in rows_today.items():
                iv_t = _atm_iv_from_rows(rows, spot)
                iv_p = _atm_iv_from_rows(rows_prev.get(expiry, []), _day_spot(prev_day))
                if iv_t is None or iv_p is None:
                    day_sign_for_expiry[expiry] = None  # static-sign fallback day
                    d_iv_for_expiry[expiry] = None
                    continue
                d_iv = iv_t - iv_p
                d_iv_for_expiry[expiry] = d_iv
                if abs(d_iv) <= iv_deadband:
                    day_sign_for_expiry[expiry] = 0.0  # in-deadband: contribute 0
                else:
                    day_sign_for_expiry[expiry] = 1.0 if d_iv < 0 else -1.0

        for expiry, rows in rows_today.items():
            rows_p = rows_prev.get(expiry, [])
            if arm == "div_signed":
                day_sign = day_sign_for_expiry.get(expiry)
            else:
                day_sign = None  # fixed_sign arm never uses ΔIV

            # In-deadband day (PRIMARY arm): no confident vol read -> the
            # whole expiry contributes 0, no map building needed.
            if day_sign == 0.0:
                continue

            vanna_map, mean_abs_vanna = _compute_vanna_stats(rows)
            rows_p_map = {}
            for row in rows_p:
                try:
                    k = float(_row_get(row, "strike"))
                    right = str(_row_get(row, "right", "")).upper()[:1]
                    rows_p_map[(k, right)] = float(_row_get(row, "oi"))
                except (TypeError, ValueError):
                    continue

            # Static per-strike sign map from the vol-surface reference for
            # THIS day (mirror of replication_reference.py:851-871; forward
            # ≈ spot is the same approximation the reference loop uses).
            # Built when it can actually be consulted: fixed_sign arm
            # (always), or the div_signed static fallback when ATM ΔIV or
            # vanna weighting is unavailable for this expiry.
            day_sign_map: dict[tuple[float, str], float] | None = None
            need_static_map = day_sign is None or not vanna_map or not mean_abs_vanna
            if need_static_map:
                try:
                    # Match the reference's T for this expiry.
                    from datetime import datetime

                    T = max(
                        (datetime.strptime(_exp_expiry_expiry(expiry), "%Y%m%d")
                         - datetime.strptime(d, "%Y%m%d")).days,
                        1,
                    ) / 365.0
                    chain_iv = {}
                    for row in rows:
                        try:
                            k = float(_row_get(row, "strike"))
                            right = str(_row_get(row, "right", "")).upper()[:1]
                            iv = _row_get(row, "iv")
                            if iv is None:
                                iv = _row_get(row, "implied_vol")
                            iv = float(iv)
                        except (TypeError, ValueError):
                            continue
                        if k > 0 and iv == iv and iv > 0:
                            chain_iv[(k, right)] = iv
                    vs_ref = vol_surface_reference.compute_vol_surface_reference(
                        ticker, chain_iv, _day_spot(day), forward=_day_spot(day), T=T
                    )
                    if vs_ref is not None:
                        day_sign_map = {}
                        for (k, right) in chain_iv:
                            s = vol_surface_reference.resolve_vol_surface_sign(vs_ref, k, right)
                            day_sign_map[(k, right)] = (
                                s if s != 0.0 else FALLBACK_STATIC_SIGN
                            )
                except Exception:
                    day_sign_map = None

            if day_sign_map is None:
                # No surface read at all -> flat fallback sign (mirrors
                # replication_reference.py:916).
                day_sign_map_fallback = FALLBACK_STATIC_SIGN
            else:
                day_sign_map_fallback = None

            for row in rows:
                try:
                    k = float(_row_get(row, "strike"))
                    right = str(_row_get(row, "right", "")).upper()[:1]
                    oi_t = float(_row_get(row, "oi"))
                except (TypeError, ValueError):
                    continue
                if (k, right) not in rows_p_map:
                    # Sparse endpoint gap: strike missing in prev day -> skip
                    # (mirror replication_reference.py:899-905).
                    n_new_strikes += 1
                    continue
                delta_oi = oi_t - rows_p_map[(k, right)]
                if delta_oi == 0:
                    continue

                if day_sign == 0.0:
                    # PRIMARY arm, |ΔIV| in deadband: no confident vol read
                    # -> the whole day contributes 0 (NOT a fallback sign).
                    continue

                vanna_weight = vanna_map.get((k, right))
                if day_sign is not None:
                    # PRIMARY arm with a confident ΔIV read; vanna-weighted
                    # (mirror replication_reference.py:926-929).
                    if vanna_weight is not None and mean_abs_vanna:
                        signed_change = (
                            day_sign * delta_oi * (vanna_weight / mean_abs_vanna)
                        )
                    else:
                        # vanna missing / zero-mean -> static-sign fallback
                        sign = (
                            day_sign_map.get((k, right), FALLBACK_STATIC_SIGN)
                            if day_sign_map
                            else FALLBACK_STATIC_SIGN
                        )
                        signed_change = sign * delta_oi
                else:
                    # FIXED_SIGN arm: pure static per-strike sign.
                    if day_sign_map_fallback is not None:
                        signed_change = day_sign_map_fallback * delta_oi
                    else:
                        signed_change = (
                            day_sign_map.get((k, right), FALLBACK_STATIC_SIGN)
                        ) * delta_oi

                position[(k, right)] = position.get((k, right), 0.0) + signed_change
                day_change += signed_change
                n_included += 1

        daily_trace.append(
            {
                "date": d,
                "kind": "accumulate",
                "d_iv": d_iv_for_expiry if arm == "div_signed" else None,
                "day_sign": (
                    {e: s for e, s in day_sign_for_expiry.items()}
                    if arm == "div_signed"
                    else None
                ),
                "net_change": day_change,
                "n_strikes_included": n_included,
                "n_new_strikes": n_new_strikes,
            }
        )

    return PositionBookResult(
        ticker=ticker,
        position_by_strike=position,
        daily_trace=daily_trace,
        dates_used=dates,
        lookback=lookback,
        arm=arm,
        total_net=sum(position.values()),
    )


def _exp_expiry_expiry(expiry: str) -> str:
    return str(expiry)


# ---------------------------------------------------------------------------
# Loader: Phase-1 research cache (greeks + oi JSON.gz joins)
# ---------------------------------------------------------------------------


def _theta_strike_to_dollars(strike: float) -> float:
    """Same thousandths convention as expiry_book_production."""
    if abs(strike) >= 1000:
        return strike / 1000.0
    return float(strike)


def _load_json_gz(path: str):
    with gzip.open(path, "rt") as f:
        return json.load(f)


def load_history_days(cache_dir: str = _CACHE_DIR, lookback: int = 150) -> list[dict]:
    """Load the last `lookback` days from the Phase-1 cache into DayData dicts.

    Cache layout (verified against 20250826_all.json.gz):
      greeks rows: {'root', 'expiration': int YYYYMMDD, 'strike': int
        thousandths, 'right': 'C'|'P', 'implied_vol': str, 'vanna': str, ...}
      oi rows:     {'expiration': int, 'strike': int thousandths,
                    'right': 'C'|'P', 'open_interest': str, 'date', ...}

    Joined on (expiration, strike, right); strikes scaled to dollars via the
    same /1000 rule as expiry_book_production._theta_strike_to_dollars.
    """
    greeks_dir = os.path.join(cache_dir, "greeks")
    oi_dir = os.path.join(cache_dir, "oi")
    dates = sorted(
        f.replace("_all.json.gz", "")
        for f in os.listdir(greeks_dir)
        if f.endswith("_all.json.gz")
    )
    dates = dates[-lookback:] if lookback and len(dates) > lookback else dates

    days: list[dict] = []
    for d in dates:
        g_path = os.path.join(greeks_dir, f"{d}_all.json.gz")
        o_path = os.path.join(oi_dir, f"{d}_all.json.gz")
        if not (os.path.exists(g_path) and os.path.exists(o_path)):
            continue
        greeks_rows = _load_json_gz(g_path)
        oi_by_key: dict[tuple[str, int, str], float] = {}
        for row in _load_json_gz(o_path):
            key = (str(int(row["expiration"])), int(row["strike"]), str(row["right"]))
            try:
                oi_by_key[key] = float(row["open_interest"])
            except (TypeError, ValueError, KeyError):
                continue

        # Group greeks by expiry; join OI on (expiration, strike, right).
        per_expiry: dict[str, list[dict]] = {}
        for row in greeks_rows:
            try:
                expiry = str(int(row["expiration"]))
                strike = _theta_strike_to_dollars(float(row["strike"]))
                right = str(row["right"]).upper()[:1]
                oi = oi_by_key.get((expiry, int(row["strike"]), right))
                if oi is None:
                    continue  # right-join: greeks rows without OI are dropped
                iv = float(row.get("implied_vol") or 0)
                vanna = float(row.get("vanna") or 0)
            except (TypeError, ValueError, KeyError):
                continue
            per_expiry.setdefault(expiry, []).append(
                {
                    "strike": strike,
                    "right": right,
                    "oi": oi,
                    "iv": iv,
                    "vanna": vanna,
                }
            )
        days.append({"date": d, "spot": None, "per_expiry": [
            {"expiry": e, "rows": rows} for e, rows in sorted(per_expiry.items())
        ]})

    # Fill spot from the daily_book trace when available.
    spot_path = os.path.join(cache_dir, "daily_book.jsonl")
    if os.path.exists(spot_path):
        spots: dict[str, float] = {}
        with open(spot_path) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    spots[str(rec["date"])] = float(rec["spot"])
                except (ValueError, KeyError):
                    continue
        for day in days:
            day["spot"] = spots.get(day["date"])

    return days


def format_accumulated_report(r: PositionBookResult) -> str:
    """Mirrors replication_reference.format_accumulated_report style."""
    arm_note = (
        "ΔIV-signed (Cem primary)" if r.arm == "div_signed" else "fixed static sign (A/B)"
    )
    lines = [
        f"{r.ticker} assumed dealer position book, arm={r.arm} ({arm_note}), "
        f"{r.dates_used[0]} -> {r.dates_used[-1]} "
        f"({len(r.dates_used)} trading days, lookback={r.lookback})",
        "  daily trace (last 10):",
    ]
    for row in r.daily_trace[-10:]:
        d_iv = row.get("d_iv")
        if isinstance(d_iv, dict):
            vals = [v for v in d_iv.values() if v is not None]
            d_iv_txt = f"d_iv~{vals[0]:+.4f}" if vals else "d_iv=missing"
        else:
            d_iv_txt = ""
        lines.append(
            f"    {row['date']} [{row['kind']:<10}] net_change={row['net_change']:>14.2f}  "
            f"strikes_included={row['n_strikes_included']} {d_iv_txt}"
        )
    lines.append(f"  final net accumulated position (all strikes): {r.total_net:.2f}")
    top5 = sorted(
        r.position_by_strike.items(), key=lambda kv: abs(kv[1]), reverse=True
    )[:5]
    lines.append("  largest 5 strike-level positions:")
    for (k, right), pos in top5:
        lines.append(f"    {right} {k:.1f}: {pos:.2f}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Assumed dealer position book accumulator")
    ap.add_argument("ticker", nargs="?", default="SPY")
    ap.add_argument("lookback", nargs="?", type=int, default=150)
    ap.add_argument("--arm", choices=("div_signed", "fixed_sign"), default="div_signed")
    args = ap.parse_args(argv)

    days = load_history_days(lookback=args.lookback)
    if len(days) < 2:
        print(f"ERROR: need >=2 cache days, got {len(days)}", file=sys.stderr)
        return 1
    result = accumulate_position_book(
        days, lookback=args.lookback, arm=args.arm, ticker=args.ticker.upper()
    )
    print(format_accumulated_report(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
