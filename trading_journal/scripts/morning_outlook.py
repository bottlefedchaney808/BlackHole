#!/usr/bin/env python3
"""morning_outlook.py — Direction 5-signal + ThetaData morning outlook.

Sequential, proxy-saturation-safe (one chain per ticker, nearest expiry only).
Runs the 10-name watchlist plus Jason's open positions. Writes
trading_journal/morning_outlook_<date>.md and prints a compact table.
"""
from __future__ import annotations

import datetime as dt
import math
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
os.chdir(REPO)

WATCHLIST = ["SPY", "QQQ", "AAPL", "NVDA", "MSFT", "AMD", "MU", "TSLA", "META", "GOOGL"]
POSITIONS = ["SOUN", "UUUU", "TGB", "KOS"]
TICKERS = WATCHLIST + POSITIONS


def normalize_theta_strike(k):
    k = float(k)
    return k / 1000.0 if k > 1000 else k


def _atm_iv(td, ticker, expiry):
    try:
        rows = td.option_bulk_greeks(ticker, expiry)
    except Exception as e:
        return None, f"ERR {type(e).__name__}"
    best_k, best_iv, spot = None, None, None
    for r in rows:
        if r.get("underlying_price"):
            spot = float(r["underlying_price"])
            break
    if spot is None:
        return None, "no spot in chain"
    best = None
    for r in rows:
        try:
            iv = float(r.get("implied_vol") or 0)
            if iv <= 0:
                continue
            k = normalize_theta_strike(r.get("strike") or 0)
        except (TypeError, ValueError):
            continue
        if best is None or abs(k - spot) < abs(best - spot):
            best, best_iv, best_k = abs(k - spot), iv, k
    if best is None:
        return None, "no IV in chain"
    return best_k, best_iv


def main():
    from shared.thetadata import ThetaDataController
    from Direction.signal_generator import generate as direction_generate

    td = ThetaDataController()
    today = dt.date.today().strftime("%Y%m%d")
    lines = []
    lines.append(f"# Morning Outlook — {today}")
    lines.append(f"Generated: {dt.datetime.now().isoformat(timespec='seconds')} | source: ThetaData (repo client) + Direction 5-signal")
    lines.append("")
    lines.append("| Ticker | Spot | ATM IV | 1d move | DirScore | Conv | whale | wave3 | squeeze | trend | liq |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    rows = []
    for t in TICKERS:
        spot = None
        try:
            spot = td.fetch_spot_price(t)
        except Exception:
            spot = None
        expiry = None
        try:
            exps = td.list_expirations(t)
            for e in exps:
                d = dt.datetime.strptime(str(e), "%Y%m%d")
                if (d.date() - dt.date.today()).days >= 7:
                    expiry = str(e)
                    break
        except Exception:
            pass
        atm_iv, move = None, None
        if expiry and spot:
            _k, iv = _atm_iv(td, t, expiry)
            if isinstance(iv, float) and iv > 0:
                atm_iv = iv
                move = atm_iv / math.sqrt(365) * 100
        ds = dc = dw = d3 = dq = dt_ = dl = ""
        err = ""
        try:
            dr = direction_generate(t)
            s = dr.get("signals", {})
            ds, dc = str(dr.get("score", "")), str(dr.get("conviction", ""))[:6]
            dw = str(s.get("whale"))
            d3 = str(s.get("wave3"))
            dq = str(s.get("squeeze"))
            dt_ = str(s.get("trend"))
            dl = str(s.get("liquidity"))
        except Exception as e:
            dc = f"ERR {type(e).__name__}"
            err = f"{type(e).__name__}: {e}"
        row = {
            "t": t, "spot": spot, "iv": atm_iv, "move": move,
            "score": ds, "conv": dc, "whale": dw, "wave3": d3,
            "squeeze": dq, "trend": dt_, "liq": dl, "err": err,
        }
        rows.append(row)
        lines.append(
            f"| {t} | {spot if spot else '-'} | {f'{atm_iv*100:.1f}%' if atm_iv else '-'} | "
            f"{f'{move:.2f}%' if move else '-'} | {ds or '-'} | {dc or '-'} | {dw} | {d3} | {dq} | {dt_} | {dl} |"
        )
    lines.append("")
    lines.append("Direction: score 0-5 (whale, wave3, squeeze, trend, liquidity). conv = conviction (HIGH/MEDIUM/NONE).")
    lines.append("Rule: strong Direction (>=3) AND whale=True = buy candidate. High 1d-implied-move + strong Direction = power-hour candidate.")
    lines.append("Positions (SOUN, UUUU, TGB, KOS) flagged separately.")
    td.close()

    out = "\n".join(lines) + "\n"
    os.makedirs(f"{REPO}/trading_journal", exist_ok=True)
    path = f"{REPO}/trading_journal/morning_outlook_{today}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    print(out)
    # machine-readable for caller
    import json
    jpath = f"{REPO}/trading_journal/morning_outlook_{today}.json"
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, default=str)
    print("JSON->", jpath)


if __name__ == "__main__":
    main()
