#!/usr/bin/env python3
"""Tier 2B FINAL puller — genuine PRIOR 2-7 DTE windows per ticker from ThetaData.

Discovery method (pre-registered amendment): ThetaData list_expirations returns only
currently-listed expiries, so historical windows are pulled by EXPLICIT expiry chosen from
the deterministic candidate set [as_of+2 .. as_of+7] (weekdays) that probe nonempty.

Per (as_of, ticker): select the valid candidate with smallest DTE >= 2 (ties -> Friday
preferred), mirroring the reused window's dte=3 choice. Acceptance requires nonempty
greeks AND OI>0 AND spot-date coverage.

The most-recent window (as_of 2026-08-14) is REUSED from _scratch_tier2/ — not re-pulled.

Output: <scratch2b>/window_<ASOF>/seed_data_<TICKER>_<EXPIRY>_short.json + final manifest.
"""
import datetime
import glob
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from thetadata_client import ThetaDataController  # noqa: E402

SCRATCH2B = os.path.dirname(os.path.abspath(__file__))
TICKERS = ["AAPL", "AMD", "AMZN", "GOOGL", "JPM", "META",
           "MSFT", "NFLX", "NVDA", "QQQ", "SPY", "TSLA"]

# PRIOR as_ofs (calendar). 14-day lookbacks. Extended backward to guarantee >=3 prior windows.
PRIOR_ASOF = ["2026-07-31", "2026-07-17", "2026-07-03", "2026-06-19", "2026-06-05",
              "2026-05-22", "2026-05-08"]

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
_env = os.path.join(_root, ".env")
if os.path.exists(_env):
    for line in open(_env, encoding="utf-8").read().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
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


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def discover_candidates(td, ticker, as_of, lookback_days=14):
    """Probe weekdays in [as_of+2..as_of+7]; return sorted [(dte, expiry)] that are nonempty."""
    valid = []
    start = as_of - datetime.timedelta(days=lookback_days)
    for offset in range(2, 8):
        exp_date = as_of + datetime.timedelta(days=offset)
        if exp_date.weekday() >= 5:
            continue
        exp_s = exp_date.strftime("%Y%m%d")
        try:
            gr = td.option_bulk_hist_eod_greeks(ticker, exp_s,
                                                start.strftime("%Y%m%d"),
                                                as_of.strftime("%Y%m%d"))
        except Exception:
            continue
        if len(gr) > 0:
            valid.append(((exp_date - as_of).days, exp_s))
    valid.sort(key=lambda x: (x[0], 0 if x[1].endswith(("4", "5", "6")) else 1))
    return valid


def select_expiry(candidates):
    """Smallest DTE >= 2; ties -> Friday preferred (keep first of sorted list)."""
    if not candidates:
        return None
    return candidates[0][1]


def pull_one(td, ticker, as_of, expiry):
    out = {"ticker": ticker, "as_of": as_of.isoformat(), "expiry": expiry,
           "dte": (datetime.datetime.strptime(expiry, "%Y%m%d").date() - as_of).days,
           "status": "ok", "greeks_rows": 0, "oi_rows": 0, "spot_rows": 0,
           "error": None, "file": None, "sha256": None,
           "obs_date_range": None, "dte_range_obs": None}
    try:
        end = as_of
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
        # validation: nonempty + OI>0 + spot coverage + genuine 2-7 DTE at end
        obs_dates = sorted({g["date"] for g in greeks})
        exp_dt = datetime.datetime.strptime(expiry, "%Y%m%d").date()
        dte_range = None
        if obs_dates:
            dtes = [(exp_dt - datetime.datetime.strptime(d, "%Y%m%d").date()).days
                    for d in obs_dates]
            dte_range = [min(dtes), max(dtes)]
        out["obs_date_range"] = [obs_dates[0], obs_dates[-1]] if obs_dates else None
        out["dte_range_obs"] = dte_range
        if len(greeks) == 0:
            out["status"] = "BLOCKED"; out["error"] = "no greeks rows after spot-alignment"
            return out
        if len(oi_rows) == 0:
            out["status"] = "BLOCKED"; out["error"] = "OI empty after retries"
            return out
        if not close_by_date:
            out["status"] = "BLOCKED"; out["error"] = "no spot closes in lookback"
            return out
        out["greeks_rows"] = len(greeks)
        out["oi_rows"] = len(oi_rows)
        out["spot_rows"] = len(spot_rows)
        manifest = {
            "ticker": ticker, "expiry": expiry, "lookback_days": 14,
            "as_of": as_of.isoformat(),
            "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "n_greeks": len(greeks), "n_oi": len(oi_rows), "n_spot": len(spot_rows),
            "dte_at_pull": out["dte"], "obs_date_range": out["obs_date_range"],
            "dte_range_obs": dte_range}
        payload = {"manifest": manifest, "greeks": greeks, "oi": oi_rows, "spot": spot_rows}
        wdir = os.path.join(SCRATCH2B, f"window_{as_of.strftime('%Y%m%d')}")
        os.makedirs(wdir, exist_ok=True)
        out_path = os.path.join(wdir, f"seed_data_{ticker}_{expiry}_short.json")
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        out["file"] = out_path
        out["sha256"] = sha256(out_path)
    except Exception as e:
        out["status"] = "BLOCKED"
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def main():
    results = []
    for asof_s in PRIOR_ASOF:
        as_of = datetime.date.fromisoformat(asof_s)
        td = ThetaDataController()
        print(f"=== as_of {asof_s} ===", flush=True)
        for tk in TICKERS:
            print(f"  {tk} ...", flush=True)
            try:
                cand = discover_candidates(td, tk, as_of)
            except Exception as e:
                results.append({"ticker": tk, "as_of": asof_s, "status": "BLOCKED",
                                "expiry": None, "dte": None, "error": f"discover {type(e).__name__}: {e}",
                                "eligible": [], "greeks_rows": 0, "oi_rows": 0, "spot_rows": 0,
                                "file": None, "sha256": None, "obs_date_range": None,
                                "dte_range_obs": None, "eligible_candidates": []})
                print(f"    -> BLOCKED discover err={type(e).__name__}", flush=True)
                continue
            exp = select_expiry(cand)
            rec = {"ticker": tk, "as_of": asof_s, "expiry": exp,
                   "eligible_candidates": cand, "status": "ok"}
            if not exp:
                rec.update({"status": "BLOCKED", "error": "no valid candidate in 2-7 DTE",
                            "dte": None, "greeks_rows": 0, "oi_rows": 0, "spot_rows": 0,
                            "file": None, "sha256": None, "obs_date_range": None,
                            "dte_range_obs": None})
                results.append(rec)
                print(f"    -> BLOCKED no candidate", flush=True)
                continue
            r = pull_one(td, tk, as_of, exp)
            r["eligible_candidates"] = cand
            results.append(r)
            print(f"    -> {r['status']} expiry={r.get('expiry')} dte={r.get('dte')} "
                  f"greeks={r['greeks_rows']} oi={r['oi_rows']} spot={r['spot_rows']} "
                  f"err={r.get('error')}", flush=True)
        td.close()
    # record reused most-recent window hashes (NOT re-pulled)
    reused = []
    reuse_dir = os.path.join(os.path.dirname(SCRATCH2B), "_scratch_tier2")
    for f in sorted(glob.glob(os.path.join(reuse_dir, "seed_data_*.json"))):
        base = os.path.basename(f)
        tk = base.split("_")[1]; exp = base.split("_")[2]
        reused.append({"ticker": tk, "expiry": exp, "window": "2026-08-14(REUSED)",
                       "sha256": sha256(f), "file": f})
    mpath = os.path.join(SCRATCH2B, "tier2b_pull_manifest_final.json")
    with open(mpath, "w", encoding="utf-8") as fh:
        json.dump({"prior_asof": PRIOR_ASOF, "results": results,
                   "reused_most_recent": reused}, fh, indent=2, default=str)
    print(f"\n[tier2b-pull-final] manifest -> {mpath}")
    return results


if __name__ == "__main__":
    main()
