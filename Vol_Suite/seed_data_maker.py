#!/usr/bin/env python3
"""seed_data_maker.py — fetch a ticker's dense EOD chain payload ONCE and
save to disk, for offline falsifier/accumulation testing.

Ported into Vol_Suite/ proper (2026-08-13) from the WSL/Ubuntu-environment
handoff package (docs/Dealer posistioning notes/_extracted/handoff_20260812/
code/seed_data_maker.py + seed_flip_compare.py's _build_payload), used to
re-pull the two tickers (QQQ, SPY) whose cached seed_data_*.json files were
found to only cover ~20-60 of the intended 150 trading days (the other 10
cached tickers already have 117-163 days -- see the falsifier real-data
verification notes).

Uses the DENSE EOD route (option_bulk_hist_eod, IV solved from bid/ask via
implied_vol.implied_vol) + hist_stock_eod for spot -- NOT the sparser
per-contract option_bulk_hist_greeks route replication_reference.py's live
`compute_accumulated_position` default uses, which is the route that starved
these two tickers' history in the first place. option_bulk_hist_oi_by_day is
the proxy-fragile leg (see .claude/skills/rate-limit-options/SKILL.md) --
retried up to 3x with backoff, run ONE TICKER AT A TIME (never concurrent
whole-chain fan-outs for this route).

Usage (from Vol_Suite/):
  python seed_data_maker.py QQQ 150 "docs/Dealer posistioning notes/_extracted/handoff_20260812/seed_data"

Writes <out_dir>/seed_data_<TICKER>_<expiry>_<lookback>d.json — same shape
seed_data_loader.py reads.
"""
import datetime
import json
import math
import os
import sys
import time

from thetadata_client import ThetaDataController
import expiry_selector
import implied_vol as implied_vol_mod

_BT_R = 0.04
_BT_Q = 0.012


def _bs_vanna(S, K, T, sigma):
    """BS closed-form vanna (right-symmetric), for the vanna-seed injection --
    same measured convention (rec.vanna = -1 * BS_vanna, Gate-0 pin) the WSL
    handoff pinned on 2026-08-11."""
    if sigma <= 0 or T <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(S / K) + (0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    phi = math.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi)
    return phi * (d2 / sigma)


def _norm_date(row):
    for key in ("date", "Date", "created", "datetime"):
        val = row.get(key)
        if val:
            digits = "".join(ch for ch in str(val) if ch.isdigit())[:8]
            if len(digits) == 8:
                return digits
    return None


def build_payload(td, ticker: str, expiry: str, lookback_days: int = 150):
    """Dense EOD greeks (IV solved from bid/ask, vanna injected) + OI-by-day
    + spot. Returns (greeks, oi_rows, spot_rows) in the exact shape
    seed_data_loader.load_seed_data() / replication_reference._accumulate_from_history
    expect.
    """
    end = datetime.date.today() - datetime.timedelta(days=1)
    start = end - datetime.timedelta(days=int(lookback_days * 1.6) + 10)
    start_str, end_str = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    price_rows = td.option_bulk_hist_eod(ticker, expiry, start_str, end_str)
    spot_rows = td.hist_stock_eod(ticker, start_str, end_str)

    # OI-by-day is the proxy-fragile route -- retry the whole fetch up to 3x
    # (transient 502s, not real missing data), per the rate-limit-options skill.
    oi_rows = []
    for attempt in range(1, 4):
        try:
            oi_rows = td.option_bulk_hist_oi_by_day(ticker, expiry, start_str, end_str)
            if len(oi_rows) > 0:
                break
            print(f"  [seed_data_maker] {ticker} OI fetch attempt {attempt}: 0 rows, retrying", flush=True)
        except Exception as e:
            print(f"  [seed_data_maker] {ticker} OI fetch attempt {attempt}: "
                  f"{type(e).__name__} {str(e)[:80]}, retrying", flush=True)
        time.sleep(15 * attempt)
    print(f"  [seed_data_maker] {ticker} EOD={len(price_rows)} oi={len(oi_rows)} "
          f"spot={len(spot_rows)} {start_str}->{end_str}", flush=True)

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
    n_no_spot = n_no_mark = n_unsolved = 0
    for row in price_rows:
        d = _norm_date(row)
        if not d or d not in close_by_date:
            n_no_spot += 1
            continue
        try:
            k = float(row["strike"]) / 1000.0 if float(row["strike"]) > 1000 else float(row["strike"])
            right = "C" if str(row.get("right", ""))[:1] == "C" else (
                "P" if str(row.get("right", ""))[:1] == "P" else "?")
        except (KeyError, TypeError, ValueError):
            continue
        if right not in ("C", "P"):
            continue
        spot = close_by_date[d]
        T = max((exp_date - datetime.datetime.strptime(d, "%Y%m%d").date()).days, 1) / 365.0
        mark = implied_vol_mod.mid_price(row.get("bid"), row.get("ask"), row.get("close"))
        if not mark or mark <= 0:
            n_no_mark += 1
            continue
        solved = implied_vol_mod.implied_vol(mark, spot, k, T, _BT_R, _BT_Q, right)
        if solved is None:
            n_unsolved += 1
            continue
        vanna = -1.0 * _bs_vanna(spot, k, T, solved)
        greeks.append({"date": d, "strike": str(int(round(k * 1000))), "right": right,
                        "implied_vol": solved, "close": row.get("close"), "vanna": vanna})

    n_dates = len({g["date"] for g in greeks})
    print(f"  [seed_data_maker] {ticker}: {len(greeks)} greek rows across {n_dates} distinct "
          f"dates ({n_no_spot} no-spot, {n_no_mark} no-mark, {n_unsolved} unsolved dropped)")
    return greeks, oi_rows, spot_rows


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    ticker = sys.argv[1].upper()
    lookback = int(sys.argv[2]) if len(sys.argv) > 2 else 150
    out_dir = sys.argv[3] if len(sys.argv) > 3 else "."
    os.makedirs(out_dir, exist_ok=True)

    td = ThetaDataController()
    try:
        expiry, _ = expiry_selector.resolve_expiration(td, ticker, None, 0.25)
        greeks, oi, spot = build_payload(td, ticker, expiry, lookback)
    finally:
        td.close()

    if not greeks:
        print(f"[seed_data_maker] no IV-history for {ticker}/{expiry}; nothing saved")
        return 1

    out = os.path.join(out_dir, f"seed_data_{ticker}_{expiry}_{lookback}d.json")
    manifest = {
        "ticker": ticker, "expiry": expiry, "lookback_days": lookback,
        "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "n_greeks": len(greeks), "n_oi": len(oi), "n_spot": len(spot),
    }
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"manifest": manifest, "greeks": greeks, "oi": oi, "spot": spot}, fh)
    print(f"[seed_data_maker] saved {len(greeks)} greeks, {len(oi)} oi, "
          f"{len(spot)} spot for {ticker}/{expiry} -> {out}")
    print(f"  manifest: {json.dumps(manifest)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
