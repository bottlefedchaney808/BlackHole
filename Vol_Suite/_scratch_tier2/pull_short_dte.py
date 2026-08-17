#!/usr/bin/env python3
"""Tier 2 scratch puller: genuine 2-7 DTE seed data for 12 tickers via ThetaData.

Writes one seed_data_<TICKER>_<EXPIRY>_short.json per ticker into a scratch dir.
"""
import datetime
import json
import math
import os
import sys
import time

# Ensure Vol_Suite on path for thetadata_client + expiry_selector + implied_vol
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from thetadata_client import ThetaDataController
import expiry_selector
import implied_vol as implied_vol_mod

SCRATCH_DIR = os.path.dirname(os.path.abspath(__file__))
os.makedirs(SCRATCH_DIR, exist_ok=True)

TICKERS = [
    "AAPL", "AMD", "AMZN", "GOOGL", "JPM", "META",
    "MSFT", "NFLX", "NVDA", "QQQ", "SPY", "TSLA",
]

# Load .env from the FinancialDevelopment repo root (parent of this worktree)
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
_env = os.path.join(_root, ".env")
if not os.path.exists(_env):
    # fallback: two levels up from Vol_Suite/_scratch_tier2 = worktree root
    _root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    _env = os.path.join(_root, ".env")
if os.path.exists(_env):
    for line in open(_env, encoding="utf-8").read().splitlines():
        line=line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k=k.strip(); v=v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v

def _norm_date(row):
    for key in ("date", "Date", "created", "datetime"):
        val = row.get(key)
        if val:
            digits = "".join(ch for ch in str(val) if ch.isdigit())[:8]
            if len(digits) == 8:
                return digits
    return None

def dte(expiry: str, as_of: datetime.date) -> int:
    try:
        exp = datetime.datetime.strptime(expiry, "%Y%m%d").date()
        return max((exp - as_of).days, 0)
    except Exception:
        return 10**9

def pick_short_dte_expiry(td, ticker, as_of):
    exps = td.list_expirations(ticker)
    eligible = []
    for e in exps:
        dd = dte(e, as_of)
        if 2 <= dd <= 7:
            eligible.append((dd, e))
    if not eligible:
        return None, []
    eligible.sort()
    return eligible[0][1], eligible

def pull_one(ticker, as_of):
    td = ThetaDataController()
    out = {"ticker": ticker, "as_of": as_of.isoformat(), "status": "ok",
           "expiry": None, "dte": None, "eligible": [],
           "greeks_rows": 0, "oi_rows": 0, "spot_rows": 0,
           "error": None, "file": None}
    try:
        expiry, eligible = pick_short_dte_expiry(td, ticker, as_of)
        out["expiry"] = expiry
        out["eligible"] = [(dd, e) for dd, e in eligible[:10]]
        if not expiry:
            out["status"] = "BLOCKED"
            out["error"] = "no expiry in 2-7 DTE window"
            return out
        out["dte"] = dte(expiry, as_of)
        end = as_of
        start = as_of - datetime.timedelta(days=14)
        start_s = start.strftime("%Y%m%d")
        end_s = end.strftime("%Y%m%d")

        price_rows = td.option_bulk_hist_eod_greeks(ticker, expiry, start_s, end_s)
        spot_rows = td.hist_stock_eod(ticker, start_s, end_s)

        oi_rows = []
        for attempt in range(1, 4):
            try:
                oi_rows = td.option_bulk_hist_oi_by_day(ticker, expiry, start_s, end_s)
                if len(oi_rows) > 0:
                    break
            except Exception as e:
                if attempt == 3:
                    raise
                time.sleep(5 * attempt)

        # filter: only rows where date has spot (to align with driver expectations)
        close_by_date = {}
        for row in spot_rows:
            d = _norm_date(row)
            if not d:
                continue
            try:
                c = float(row.get("close", 0) or 0)
            except (TypeError, ValueError):
                c = 0.0
            if c > 0:
                close_by_date[d] = c

        greeks = []
        for row in price_rows:
            d = _norm_date(row)
            if not d or d not in close_by_date:
                continue
            try:
                k = int(float(row["strike"]))
                right = str(row.get("right", ""))[:1].upper()
            except (KeyError, TypeError, ValueError):
                continue
            if right not in ("C", "P"):
                continue
            iv = float(row.get("implied_vol", 0) or 0)
            if iv <= 0:
                continue
            greeks.append({
                "date": d, "strike": str(k), "right": right,
                "implied_vol": iv,
                "close": row.get("close"),
                "vanna": row.get("vanna"),
                "gamma": row.get("gamma"),
                "delta": row.get("delta"),
            })

        out["greeks_rows"] = len(greeks)
        out["oi_rows"] = len(oi_rows)
        out["spot_rows"] = len(spot_rows)

        manifest = {
            "ticker": ticker,
            "expiry": expiry,
            "lookback_days": 14,
            "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "n_greeks": len(greeks),
            "n_oi": len(oi_rows),
            "n_spot": len(spot_rows),
            "dte_at_pull": out["dte"],
            "eligible_expiries_2_7": out["eligible"],
        }
        payload = {"manifest": manifest, "greeks": greeks, "oi": oi_rows, "spot": spot_rows}
        out_path = os.path.join(SCRATCH_DIR, f"seed_data_{ticker}_{expiry}_short.json")
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        out["file"] = out_path
    except Exception as e:
        out["status"] = "BLOCKED"
        out["error"] = f"{type(e).__name__}: {e}"
    finally:
        try:
            td.close()
        except Exception:
            pass
    return out

def main():
    as_of = datetime.date.today()
    print(f"[tier2-pull] as_of={as_of}  scratch={SCRATCH_DIR}")
    results = []
    for tk in TICKERS:
        print(f"[tier2-pull] {tk} ...", flush=True)
        r = pull_one(tk, as_of)
        results.append(r)
        print(f"  -> {r['status']} expiry={r.get('expiry')} dte={r.get('dte')} "
              f"greeks={r['greeks_rows']} oi={r['oi_rows']} spot={r['spot_rows']} "
              f"err={r.get('error')}")
    # Write manifest
    mpath = os.path.join(SCRATCH_DIR, "pull_manifest.json")
    with open(mpath, "w", encoding="utf-8") as fh:
        json.dump({"as_of": as_of.isoformat(), "results": results}, fh, indent=2, default=str)
    print(f"\n[tier2-pull] manifest -> {mpath}")
    return results

if __name__ == "__main__":
    main()
