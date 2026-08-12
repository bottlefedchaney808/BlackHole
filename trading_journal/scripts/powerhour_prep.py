#!/usr/bin/env python3
"""powerhour_prep.py — intraday options look for power-hour prep.

Pulls current spot + near-term ATM implied vol + implied 1-day move for the
honed-in watchlist tickers and QQQ/SPY, via ThetaData (repo client). Writes a
machine-readable prep report to trading_journal/powerhour_prep_<date>.md and
prints a compact table to stdout.

Deterministic + sequential (proxy-saturation safe: one chain per ticker,
nearest expiry only). Failures are recorded per-ticker, never fatal.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import math

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
os.chdir(REPO)

# Honed-in tickers (positions + energy shortlist) + QQQ/SPY
TICKERS = ["SOUN", "UUUU", "TGB", "KOS", "INR", "CRGY", "PTEN", "QQQ", "SPY"]
EARNINGS = {  # upcoming scheduled earnings (power-hour-relevant catalysts)
    "SOUN": None, "UUUU": None, "TGB": None, "KOS": None,
    "INR": None, "CRGY": None, "PTEN": None, "QQQ": None, "SPY": None,
}


def normalize_theta_strike(k):
    """Theta-scaled int strike -> dollars (shared normalization contract): >1000 is cents-based int, /1000."""
    k = float(k)
    return k / 1000.0 if k > 1000 else k


def _atm_iv(td, ticker, expiry):
    """Return (atm_strike, atm_iv) for the nearest expiry, ATM strike closest to spot."""
    try:
        rows = td.option_bulk_greeks(ticker, expiry)
    except Exception as e:
        return None, f"ERR {type(e).__name__}"
    best_k, best_iv = None, None
    spot = None
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
    lines.append(f"# Power-Hour Prep — {today}")
    lines.append(f"Generated: {dt.datetime.now().isoformat(timespec='seconds')} | source: ThetaData (repo client) + Direction 5-signal")
    lines.append("")
    lines.append("| Ticker | Spot | ATM IV | 1d move | DirScore | Conv | whale | wave3 | squeeze | trend | liq |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for t in TICKERS:
        spot = None
        try:
            spot = td.fetch_spot_price(t)
        except Exception as e:
            spot = None
        # nearest expiry >= 7 days out (EOD convention, not 0DTE)
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
        # Direction 5-signal read
        ds, dc, dw, d3, dq, dt_, dl = "", "", "", "", "", "", ""
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
        lines.append(
            f"| {t} | {spot if spot else '-'} | {f'{atm_iv*100:.1f}%' if atm_iv else '-'} | "
            f"{f'{move:.2f}%' if move else '-'} | {ds or '-'} | {dc or '-'} | {dw} | {d3} | {dq} | {dt_} | {dl} |"
        )
    lines.append("")
    lines.append("Direction: score 0-5 (whale=dealer/whale flow, wave3, squeeze, trend, liquidity); conv = conviction.")
    lines.append("Rule: strong Direction (>=3) AND whale=True = buy candidate. High 1d-implied-move + strong Direction = power-hour candidate.")
    td.close()
    out = "\n".join(lines) + "\n"
    os.makedirs(f"{REPO}/trading_journal", exist_ok=True)
    path = f"{REPO}/trading_journal/powerhour_prep_{today}.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)
    print(out)


if __name__ == "__main__":
    main()
