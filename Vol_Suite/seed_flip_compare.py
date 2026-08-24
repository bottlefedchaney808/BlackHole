#!/usr/bin/env python3
"""Live vs vanna-seed accumulation comparison (no seed flip — ditched).

Compares two SEED constructions on the SAME accumulation logic + SABR-signed
flow (no v5, no flip):
  - live(repl): the canonical replication seed (-OI on day-1 OTM set)
  - vanna: the brainstorming vanna-smile seed (LARP Round 1: sign(vanna) marks
    rich vanna OI short / cheap long)

Uses the DENSE EOD route (IV solved from bid/ask, spot from hist_stock_eod),
NOT the sparse hist_greeks route the earlier falsifier hit.

Run from Vol_Suite/ with the scrubbed venv:
  env -u PYTHONPATH -u VIRTUAL_ENV ../Financial_Dev_Env/bin/python3 \
    seed_flip_compare.py SPY 120
"""

import datetime
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import expiry_selector
import implied_vol as implied_vol_mod
import replication_reference as rr

from shared.thetadata import ThetaDataController

_BT_R = 0.04
_BT_Q = 0.012
_DAYS = int(os.environ.get("SEED_FLIP_DAYS", "150"))


def _bs_vanna(S, K, T, sigma):
    """BS closed-form vanna (right-symmetric), for the vanna-seed injection."""
    if sigma <= 0 or T <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(S / K) + (0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    phi = math.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi)
    return math.exp(0.0) * phi * (d2 / sigma)


def _norm_date(row):
    for key in ("date", "Date", "created", "datetime"):
        val = row.get(key)
        if val:
            digits = "".join(ch for ch in str(val) if ch.isdigit())[:8]
            if len(digits) == 8:
                return digits
    return None


def _build_payload(td, ticker, expiry):
    """Dense EOD greeks (IV solved from bid/ask) + OI-by-day + spot, same as
    the pooled backtest. Returns (greeks, oi, spot)."""
    end = datetime.date.today() - datetime.timedelta(days=1)
    start = end - datetime.timedelta(days=int(_DAYS * 1.6) + 10)
    start_str, end_str = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    price_rows = td.option_bulk_hist_eod(ticker, expiry, start_str, end_str)
    spot_rows = td.hist_stock_eod(ticker, start_str, end_str)
    # OI-by-day is the proxy-fragile route (502-sensitive under concurrency).
    # Retry the whole OI fetch up to 3x (transient 502s, not real missing data).
    oi_rows = []
    for attempt in range(1, 4):
        try:
            oi_rows = td.option_bulk_hist_oi_by_day(ticker, expiry, start_str, end_str)
            if len(oi_rows) > 0:
                break
            print(
                f"  [seed_flip] {ticker} OI fetch attempt {attempt}: 0 rows, retrying",
                flush=True,
            )
        except Exception as e:
            print(
                f"  [seed_flip] {ticker} OI fetch attempt {attempt}: {type(e).__name__} {str(e)[:60]}, retrying",
                flush=True,
            )
        import time

        time.sleep(15 * attempt)
    print(
        f"  [seed_flip] {ticker} EOD={len(price_rows)} oi={len(oi_rows)} "
        f"spot={len(spot_rows)} {start_str}->{end_str}",
        flush=True,
    )

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

    exp_date = datetime.datetime.strptime(expiry, "%Y%m%d").date()
    greeks = []
    for row in price_rows:
        d = _norm_date(row)
        if not d or d not in close_by_date:
            continue
        try:
            k = float(row["strike"]) / 1000.0
            right = (
                "C"
                if str(row.get("right", ""))[:1] == "C"
                else ("P" if str(row.get("right", ""))[:1] == "P" else "?")
            )
        except (KeyError, TypeError, ValueError):
            continue
        if right not in ("C", "P"):
            continue
        spot = close_by_date[d]
        T = (
            max((exp_date - datetime.datetime.strptime(d, "%Y%m%d").date()).days, 1)
            / 365.0
        )
        mark = implied_vol_mod.mid_price(
            row.get("bid"), row.get("ask"), row.get("close")
        )
        if not mark or mark <= 0:
            continue
        solved = implied_vol_mod.implied_vol(mark, spot, k, T, _BT_R, _BT_Q, right)
        if solved is None:
            continue
        # Inject the MEASURED-convention vanna (rec.vanna = -1 * BS_vanna,
        # Gate-0 pin) so the 'vanna' seed_mode has per-strike vanna to sign by.
        vanna = -1.0 * _bs_vanna(spot, k, T, solved)
        greeks.append(
            {
                "date": d,
                "strike": str(int(round(k * 1000))),
                "right": right,
                "implied_vol": solved,
                "close": row.get("close"),
                "vanna": vanna,
            }
        )
    return greeks, oi_rows, spot_rows


def main() -> int:
    ticker = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    lookback = int(sys.argv[2]) if len(sys.argv) > 2 else _DAYS
    td = ThetaDataController()
    try:
        expiry, _ = expiry_selector.resolve_expiration(td, ticker, None, 0.25)
        greeks, oi, spot = _build_payload(td, ticker, expiry)
    finally:
        td.close()

    if not greeks:
        print(f"[seed_flip] no IV-history for {ticker}/{expiry}")
        return 2

    results = {}
    arms = [
        ("live(repl)", "replication", "0", "0"),
        ("vanna_seed", "vanna", "0", "0"),
        ("live+vannaflow", "replication", "0", "1"),
        ("svi_rp_seed", "svi_rp", "0", "0"),
    ]
    for label, seed_mode, seed_sign_env, vanna_flow_env in arms:
        os.environ["DEALER_SEED_SIGN"] = seed_sign_env
        os.environ["DEALER_VANNA_FLOW"] = vanna_flow_env
        try:
            acc = rr._accumulate_from_history(
                ticker, expiry, lookback, seed_mode, greeks, oi, spot
            )
        finally:
            os.environ.pop("DEALER_SEED_SIGN", None)
            os.environ.pop("DEALER_VANNA_FLOW", None)
        book = acc.position_by_strike
        end_book = sum(book.values())
        seed_net = acc.daily_trace[0]["net_change"]
        flow_sum = (
            sum(t["net_change"] for t in acc.daily_trace[1:])
            if len(acc.daily_trace) > 1
            else 0.0
        )
        total_flow = abs(seed_net) + sum(
            abs(t["net_change"]) for t in acc.daily_trace[1:]
        )
        results[label] = {
            "end_book": end_book,
            "seed_net": seed_net,
            "flow_sum": flow_sum,
            "n_days": len(acc.daily_trace),
            "n_strikes": len(book),
            "regime": "SHORT" if end_book < 0 else ("LONG" if end_book > 0 else "FLAT"),
            "seed_share": (abs(seed_net) / total_flow) if total_flow else 1.0,
        }
        print(
            f"[seed_flip] {ticker} {label}: end_book={end_book:,.0f} "
            f"({results[label]['regime']}) seed={seed_net:,.0f} "
            f"flow={flow_sum:,.0f} seed_share={results[label]['seed_share']:.3f} "
            f"n_days={len(acc.daily_trace)}"
        )

    live, vanna_r, vf = (
        results["live(repl)"],
        results["vanna_seed"],
        results["live+vannaflow"],
    )
    svi = results["svi_rp_seed"]
    print(
        f"\n[seed_flip] VERDICT {ticker} (live vs vanna-seed vs live+vannaflow vs svi_rp_seed):"
    )
    for label in ("live(repl)", "vanna_seed", "live+vannaflow", "svi_rp_seed"):
        r = results[label]
        print(
            f"  {label:<16} end_book={r['end_book']:,.0f} ({r['regime']}) "
            f"seed_share={r['seed_share']:.3f} n_days={r['n_days']}"
        )
    regime_changed = (
        live["regime"] != vanna_r["regime"]
        or live["regime"] != vf["regime"]
        or live["regime"] != svi["regime"]
    )
    sign_changed = (
        math.copysign(1.0, live["end_book"]) != math.copysign(1.0, vanna_r["end_book"])
        or math.copysign(1.0, live["end_book"]) != math.copysign(1.0, vf["end_book"])
        or math.copysign(1.0, live["end_book"]) != math.copysign(1.0, svi["end_book"])
    )
    print(f"  any_regime_change={regime_changed} any_sign_change={sign_changed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
