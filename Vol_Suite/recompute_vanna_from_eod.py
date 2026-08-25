#!/usr/bin/env python3
"""Recompute historical vanna from dense EOD IV history (NON-CIRCULAR).

Resolves the M2-joint-test data-availability blocker (battery-consolidated
-20260811.md): ThetaData hist/all_greeks is sparse (~2 dates/40d), but the EOD
price route (option_bulk_hist_eod) is dense and carries implied_vol + spot per
day. So:

  1. measure the vanna convention from the AUTHORITATIVE rec.vanna (dense in
     the present) -- result (Gate-0 pin, 2026-08-11): rec.vanna = -1 * BS_vanna,
     |scale| ~1.0, uniform across moneyness + rights (SPY -0.956, QQQ -0.989).
  2. recompute historical per-(strike,right)-day vanna = -1 * BS_vanna(
     implied_vol, spot, TTE) from the dense EOD IV history.
  3. feed the recomputed vanna into book_b (the branch-(b) Dvanna column) for
     the M2 joint test.

This is NON-CIRCULAR because the -1 sign is MEASURED from the data's own
source of truth, not assumed. The only assumption is that the flip is stable
over the lookback (a separate, testable claim).

Usage:
  env -u PYTHONPATH -u VIRTUAL_ENV ../Financial_Dev_Env/bin/python3 \
    recompute_vanna_from_eod.py SPY 20260831 [out.json]

Emits a JSON map: { "date": { "K": {"C": vanna, "P": vanna}, ... }, ... }
(per-strike vanna, NOT netted across rights -- the battery's construction rule).
"""

import datetime
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import implied_vol as implied_vol_mod  # same IV-derivation the backtest uses

from shared.thetadata import ThetaDataController

# Measured convention (Gate-0 pin, 2026-08-11): rec.vanna = -1 * BS_vanna.
# |scale| ~1.0 (SPY 0.951, QQQ 1.005); uniform across moneyness + both rights.
VANNA_SIGN = -1.0
# Same risk assumptions the backtest uses (backtest_stage3.py:117-123).
_BT_R = 0.04
_BT_Q = 0.012


def bs_vanna(S, K, T, sigma, r=_BT_R, q=_BT_Q):
    """BS closed-form vanna (right-symmetric)."""
    if sigma <= 0 or T <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    phi = math.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi)
    return math.exp(-q * T) * phi * (d2 / sigma)


def _norm_date(row):
    for key in ("date", "Date", "created", "datetime"):
        val = row.get(key)
        if val:
            s = str(val)
            digits = "".join(ch for ch in s if ch.isdigit())[:8]
            if len(digits) == 8:
                return digits
    return None


def recompute(td, ticker, expiry):
    """Return (vanna_by_day, tte_years, spot, n_days) from dense EOD IV history.

    Mirrors backtest_stage3._build_day_records IV derivation exactly: IV is
    SOLVED from bid/ask/close via implied_vol.implied_vol (the dense EOD rows
    carry prices but no IV field), spot comes from the stock EOD route.
    """
    # window: last 45 calendar days ending yesterday (dense recent history)
    end = datetime.date.today() - datetime.timedelta(days=1)
    start = end - datetime.timedelta(days=45)
    start_str = start.strftime("%Y%m%d")
    end_str = end.strftime("%Y%m%d")

    # dense EOD rows (prices only, no IV field). Full history.
    price_rows = td.option_bulk_hist_eod(ticker, expiry, start_str, end_str)
    print(
        f"[recompute] {ticker}/{expiry} EOD rows {start_str}-{end_str}: {len(price_rows)}",
        flush=True,
    )

    # stock spot per day
    spot_rows = td.hist_stock_eod(ticker, start_str, end_str)
    close_by_date = {}
    for row in spot_rows:
        d = _norm_date(row)
        if d:
            try:
                c = float(row.get("close", 0) or 0)
            except (TypeError, ValueError):
                c = 0.0
            if c > 0:
                close_by_date[d] = c
    print(
        f"[recompute] spot days: {len(close_by_date)} ({start_str}->{end_str})",
        flush=True,
    )

    exp_date = datetime.datetime.strptime(expiry, "%Y%m%d").date()
    vanna_by_day = {}
    n_iv = n_unrecoverable = n_total = 0
    for row in price_rows:
        d = _norm_date(row)
        if not d:
            continue
        try:
            k = (
                float(row["strike"]) / 1000.0
                if float(row["strike"]) >= 1000
                else float(row["strike"])
            )
            right = row["right"][0] if row.get("right") else "?"
        except (KeyError, TypeError, ValueError):
            continue
        if right not in ("C", "P"):
            continue
        n_total += 1
        try:
            d_date = datetime.datetime.strptime(d[:8], "%Y%m%d").date()
            T = max((exp_date - d_date).days, 1) / 365.0
        except (ValueError, TypeError):
            T = max((exp_date - datetime.date.today()).days, 1) / 365.0
        spot = close_by_date.get(d)
        if not spot:
            n_unrecoverable += 1
            continue
        mark = implied_vol_mod.mid_price(
            row.get("bid"), row.get("ask"), row.get("close")
        )
        if not mark or mark <= 0:
            n_unrecoverable += 1
            continue
        solved = implied_vol_mod.implied_vol(mark, spot, k, T, _BT_R, _BT_Q, right)
        if solved is None:
            n_unrecoverable += 1
            continue
        n_iv += 1
        v = VANNA_SIGN * bs_vanna(spot, k, T, solved, _BT_R, _BT_Q)
        vanna_by_day.setdefault(d, {}).setdefault(k, {})[right] = v

    tte_years = max((exp_date - datetime.date.today()).days, 1) / 365.0
    n_days = len(vanna_by_day)
    last_spot = close_by_date[max(close_by_date)] if close_by_date else None
    print(
        f"[recompute] {n_iv}/{n_total} cells solved IV "
        f"({n_unrecoverable} no IV/spot); {n_days} distinct days; "
        f"last spot~{last_spot if last_spot else 'n/a'}; TTE={tte_years:.4f}",
        flush=True,
    )
    return vanna_by_day, tte_years, last_spot, n_days


def main():
    ticker = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    expiry = sys.argv[2] if len(sys.argv) > 2 else "20260831"
    out_path = sys.argv[3] if len(sys.argv) > 3 else None

    td = ThetaDataController()
    try:
        vanna_by_day, tte_years, spot, n_days = recompute(td, ticker, expiry)
    finally:
        td.close()

    if not vanna_by_day:
        print("[recompute] no vanna recomputed (no IV history)")
        return 2

    result = {
        "ticker": ticker,
        "expiry": expiry,
        "tte_years": tte_years,
        "spot": spot,
        "n_days": n_days,
        "convention": "rec.vanna = -1 * BS_vanna",
        "vanna_by_day": vanna_by_day,
    }
    if out_path:
        with open(out_path, "w") as f:
            json.dump(result, f, indent=1)
        print(f"[recompute] wrote {out_path}")
    else:
        print(json.dumps(result, indent=1)[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
