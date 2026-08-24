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

    price_rows = td.option_bulk_hist_eod_greeks(ticker, expiry, start_str, end_str)
    spot_rows = td.hist_stock_eod(ticker, start_str, end_str)

    # OI-by-day is the proxy-fragile route -- retry the whole fetch up to 3x
    # (transient 502s, not real missing data), per the rate-limit-options skill.
    oi_rows = []
    for attempt in range(1, 4):
        try:
            oi_rows = td.option_bulk_hist_oi_by_day(ticker, expiry, start_str, end_str)
            if len(oi_rows) > 0:
                break
            print(
                f"  [seed_data_maker] {ticker} OI fetch attempt {attempt}: 0 rows, retrying",
                flush=True,
            )
        except Exception as e:
            print(
                f"  [seed_data_maker] {ticker} OI fetch attempt {attempt}: "
                f"{type(e).__name__} {str(e)[:80]}, retrying",
                flush=True,
            )
        time.sleep(15 * attempt)
    print(
        f"  [seed_data_maker] {ticker} EOD_greeks={len(price_rows)} oi={len(oi_rows)} "
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

    # The eod_greeks route already carries implied_vol + full greeks (solved by
    # ThetaData, 17:15 ET close) -- no local IV inversion needed. We keep the
    # same greek-row shape seed_data_loader expects: date, strike (theta-int),
    # right, implied_vol, plus the dense vanna the accumulation/falsifier arms
    # read. Preserve the other dense greeks (gamma/delta) for the snapshot arm.
    greeks = []
    n_no_spot = 0
    for row in price_rows:
        d = _norm_date(row)
        if not d or d not in close_by_date:
            n_no_spot += 1
            continue
        try:
            k = int(float(row["strike"]))
            right = str(row.get("right", ""))[:1].upper()
            iv = float(row.get("implied_vol", 0) or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if right not in ("C", "P"):
            continue
        if iv <= 0:
            continue
        greeks.append(
            {
                "date": d,
                "strike": str(k),
                "right": right,
                "implied_vol": iv,
                "close": row.get("close"),
                "vanna": row.get("vanna"),
                "gamma": row.get("gamma"),
                "delta": row.get("delta"),
            }
        )

    n_dates = len({g["date"] for g in greeks})
    print(
        f"  [seed_data_maker] {ticker}: {len(greeks)} greek rows across {n_dates} distinct "
        f"dates ({n_no_spot} no-spot dropped)"
    )
    return greeks, oi_rows, spot_rows


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    ticker = sys.argv[1].upper()
    lookback = int(sys.argv[2]) if len(sys.argv) > 2 else 150
    out_dir = sys.argv[3] if len(sys.argv) > 3 else "."
    # Optional 4th arg = explicit expiry (skips the slow longest-history probe)
    explicit_expiry = sys.argv[4].upper() if len(sys.argv) > 4 else None
    os.makedirs(out_dir, exist_ok=True)

    td = ThetaDataController()
    try:
        if explicit_expiry:
            expiry = explicit_expiry
            print(
                f"[seed_data_maker] {ticker}: using explicit expiry {expiry}",
                flush=True,
            )
        else:
            # Resolve to the expiry with the LONGEST available option-chain history
            # (a far-dated/LEAPS expiry listed >= lookback trading days ago), NOT the
            # nearest 0.25-year one -- a recently-listed expiry has only a few weeks
            # of history, which starves the falsifier/backtest window. This probe is
            # slow (probes up to 14 far-dated expiries); pass the expiry explicitly
            # to skip it once you know it (e.g. 20261218 for QQQ/SPY).
            expiry = td.resolve_longest_history_expiry(ticker, lookback_days=lookback)
            print(
                f"[seed_data_maker] {ticker}: resolved longest-history expiry {expiry} "
                f"for {lookback}d lookback",
                flush=True,
            )
        greeks, oi, spot = build_payload(td, ticker, expiry, lookback)
    finally:
        td.close()

    if not greeks:
        print(f"[seed_data_maker] no IV-history for {ticker}/{expiry}; nothing saved")
        return 1

    out = os.path.join(out_dir, f"seed_data_{ticker}_{expiry}_{lookback}d.json")
    manifest = {
        "ticker": ticker,
        "expiry": expiry,
        "lookback_days": lookback,
        "generated": datetime.datetime.now(datetime.UTC).isoformat(),
        "n_greeks": len(greeks),
        "n_oi": len(oi),
        "n_spot": len(spot),
    }
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"manifest": manifest, "greeks": greeks, "oi": oi, "spot": spot}, fh)
    print(
        f"[seed_data_maker] saved {len(greeks)} greeks, {len(oi)} oi, "
        f"{len(spot)} spot for {ticker}/{expiry} -> {out}"
    )
    print(f"  manifest: {json.dumps(manifest)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
