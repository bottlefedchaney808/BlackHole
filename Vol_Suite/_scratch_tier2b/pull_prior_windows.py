#!/usr/bin/env python3
"""Tier 2B — pull genuine PRIOR 2-7 DTE windows per ticker from ThetaData.

Reuses the EXACT pull pattern from the reference `_scratch_tier2/pull_short_dte.py`
(nearest 2-7 DTE expiry at a chosen as-of, 14-day greeks/OI/spot lookback), but
loops over MULTIPLE prior as-of dates spaced so their 14-day lookbacks do NOT overlap.

The most-recent window (as_of 2026-08-14, expiries 20260817/20260821) is REUSED from
`_scratch_tier2/` — NOT re-pulled, NOT relabeled. This script pulls only PRIOR windows.

Output layout: <scratch2b>/window_<ASOF>/seed_data_<TICKER>_<EXPIRY>_short.json
plus a manifest with SHA-256 per file.
"""
import datetime
import glob
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))  # Vol_Suite
from thetadata_client import ThetaDataController  # noqa: E402

SCRATCH2B = os.path.dirname(os.path.abspath(__file__))
os.makedirs(SCRATCH2B, exist_ok=True)

TICKERS = ["AAPL", "AMD", "AMZN", "GOOGL", "JPM", "META",
           "MSFT", "NFLX", "NVDA", "QQQ", "SPY", "TSLA"]

# PRIOR as_of dates (calendar). Lookbacks (as_of-14d .. as_of) do not overlap.
# as_of 2026-08-14 is the reused most-recent window (pulled separately).
PRIOR_ASOF = ["2026-07-31", "2026-07-17", "2026-07-03", "2026-06-19", "2026-06-05"]

# Load .env from repo root
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
_env = os.path.join(_root, ".env")
if not os.path.exists(_env):
    _root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    _env = os.path.join(_root, ".env")
if os.path.exists(_env):
    for line in open(_env, encoding="utf-8").read().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip(); v = v.strip().strip('"').strip("'")
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


def dte(expiry, as_of):
    try:
        return max((datetime.datetime.strptime(expiry, "%Y%m%d").date() - as_of).days, 0)
    except Exception:
        return 10 ** 9


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


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def pull_one(ticker, as_of_date):
    td = ThetaDataController()
    out = {"ticker": ticker, "as_of": as_of_date.isoformat(), "status": "ok",
           "expiry": None, "dte": None, "eligible": [], "greeks_rows": 0,
           "oi_rows": 0, "spot_rows": 0, "error": None, "file": None, "sha256": None}
    try:
        expiry, eligible = pick_short_dte_expiry(td, ticker, as_of_date)
        out["expiry"] = expiry
        out["eligible"] = [(dd, e) for dd, e in eligible[:10]]
        if not expiry:
            out["status"] = "BLOCKED"
            out["error"] = "no expiry in 2-7 DTE window"
            return out
        out["dte"] = dte(expiry, as_of_date)
        end = as_of_date
        start = end - datetime.timedelta(days=14)
        start_s = start.strftime("%Y%m%d"); end_s = end.strftime("%Y%m%d")
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
                "date": d, "strike": str(k), "right": right, "implied_vol": iv,
                "close": row.get("close"), "vanna": row.get("vanna"),
                "gamma": row.get("gamma"), "delta": row.get("delta")})
        out["greeks_rows"] = len(greeks)
        out["oi_rows"] = len(oi_rows)
        out["spot_rows"] = len(spot_rows)
        manifest = {
            "ticker": ticker, "expiry": expiry, "lookback_days": 14,
            "as_of": as_of_date.isoformat(),
            "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "n_greeks": len(greeks), "n_oi": len(oi_rows), "n_spot": len(spot_rows),
            "dte_at_pull": out["dte"], "eligible_expiries_2_7": out["eligible"]}
        payload = {"manifest": manifest, "greeks": greeks, "oi": oi_rows, "spot": spot_rows}
        wdir = os.path.join(SCRATCH2B, f"window_{as_of_date.strftime('%Y%m%d')}")
        os.makedirs(wdir, exist_ok=True)
        out_path = os.path.join(wdir, f"seed_data_{ticker}_{expiry}_short.json")
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        out["file"] = out_path
        out["sha256"] = sha256(out_path)
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
    results = []
    for asof_s in PRIOR_ASOF:
        as_of = datetime.date.fromisoformat(asof_s)
        for tk in TICKERS:
            print(f"[tier2b-pull] as_of={asof_s} {tk} ...", flush=True)
            r = pull_one(tk, as_of)
            results.append(r)
            print(f"  -> {r['status']} expiry={r.get('expiry')} dte={r.get('dte')} "
                  f"greeks={r['greeks_rows']} oi={r['oi_rows']} spot={r['spot_rows']} "
                  f"err={r.get('error')}", flush=True)
    # Also record reused most-recent window hashes (NOT re-pulled)
    reused = []
    reuse_dir = os.path.join(os.path.dirname(SCRATCH2B), "_scratch_tier2")
    for f in sorted(glob.glob(os.path.join(reuse_dir, "seed_data_*.json"))):
        base = os.path.basename(f)
        tk = base.split("_")[1]
        exp = base.split("_")[2]
        reused.append({"ticker": tk, "expiry": exp, "window": "20260814(REUSED)",
                       "sha256": sha256(f), "file": f})
    mpath = os.path.join(SCRATCH2B, "tier2b_pull_manifest.json")
    with open(mpath, "w", encoding="utf-8") as fh:
        json.dump({"prior_asof": PRIOR_ASOF, "results": results,
                   "reused_most_recent": reused}, fh, indent=2, default=str)
    print(f"\n[tier2b-pull] manifest -> {mpath}")
    return results


if __name__ == "__main__":
    main()
