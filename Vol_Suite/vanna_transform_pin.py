#!/usr/bin/env python3
"""Pin the ThetaData vanna sign/magnitude transform vs local BS closed form.

Gate-0 follow-up: SPY + QQQ established that rec.vanna = BS magnitude (~1.04x)
but with OTM-call=+/OTM-put=- sign (opposite to raw BS where OTM is -1 both
rights). This characterizes the transform across a THIRD ticker and the full
moneyness range (ITM / ATM / OTM) so the exact per-strike sign rule is pinned
before any vanna-unit number is read.

Per the battery pre-registration, Gate-0 FAIL freezes vanna-unit reads until
this transform is resolved.

Run: env -u PYTHONPATH -u VIRTUAL_ENV ../Financial_Dev_Env/bin/python3
     vanna_transform_pin.py [TICKER]
"""

import datetime
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from shared.thetadata import ThetaDataController


def bs_vanna(S, K, T, sigma):
    if sigma <= 0 or T <= 0:
        return 0.0
    d1 = (math.log(S / K) + (0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    phi = math.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi)
    return -math.exp(0.0) * phi * (d2 / sigma)


def characterize(td, ticker):
    all_exp = td.list_expirations(ticker)
    today = datetime.datetime.now().date()
    cand = []
    for exp in all_exp:
        try:
            d = datetime.datetime.strptime(exp.strip(), "%Y%m%d").date()
        except ValueError:
            continue
        tte = (d - today).days
        if 20 <= tte <= 45:
            cand.append((exp, tte))
    if not cand:
        print(f"  [{ticker}] no 20-45d expiry")
        return
    exp, tte_days = cand[0]
    T = tte_days / 365.0
    rows = td.option_bulk_greeks(ticker, exp)
    parsed = {}
    for r in rows:
        try:
            k = float(r["strike"]) / 1000.0
            right = r["right"][0] if r.get("right") else "?"
            v = float(r.get("vanna", r.get("Vanna", r.get("VANNA", 0))) or 0)
            iv = float(r.get("implied_vol", r.get("iv", 0)) or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if right in ("C", "P"):
            parsed[(k, right)] = (v, iv)
    strikes = sorted({k for k, _ in parsed})
    if not strikes:
        print(f"  [{ticker}] no strikes parsed")
        return
    # use the REAL spot quote (not median strike) so moneyness bands are correct
    S = None
    try:
        sq = td.stock_snapshot_quote(ticker)
        if isinstance(sq, dict):
            S = float(
                sq.get("mid", sq.get("last", sq.get("close", sq.get("price", 0)))) or 0
            )
    except Exception:
        pass
    if not S or S <= 0:
        # fallback: use the underlying_price stamped on the greek rows
        S = None
        for r in rows[:20]:
            try:
                up = float(r.get("underlying_price", 0) or 0)
                if up > 0:
                    S = up
                    break
            except (TypeError, ValueError):
                continue
    if not S or S <= 0:
        S = strikes[len(strikes) // 2]

    print(
        f"\n[{ticker}] {exp} TTE={tte_days}d spot~{S:.2f} rows={len(rows)} parsed={len(parsed)}"
    )

    # magnitude + sign ratio per moneyness band, per right
    bands = [
        ("deep-ITM<0.9", 0.0, 0.9),
        ("ITM 0.9-0.97", 0.9, 0.97),
        ("ATM 0.97-1.03", 0.97, 1.03),
        ("OTM 1.03-1.10", 1.03, 1.10),
        ("deep-OTM>1.10", 1.10, 9.9),
    ]
    # report by moneyness (k/S) since call/put moneyness mirrors around 1.0
    # call: OTM = k/S > 1 ; put: OTM = k/S < 1. Use call-moneyness m=k/S for
    # calls and m=1/(k/S)=S/k... simpler: report per (right, m-call) cell.
    results = {}
    for (k, right), (v, iv) in parsed.items():
        if iv <= 0:
            continue
        b = bs_vanna(S, k, T, iv)
        if abs(b) < 1e-12:
            continue
        mc = k / S  # call moneyness
        mp = S / k  # put moneyness
        m = mc if right == "C" else mp
        band = next((name for name, lo, hi in bands if lo <= m < hi), "deep-OTM>1.10")
        key = (right, band)
        d = results.setdefault(key, {"n": 0, "same": 0, "opp": 0, "ratios": []})
        d["n"] += 1
        if math.copysign(1.0, b) == math.copysign(1.0, v):
            d["same"] += 1
        else:
            d["opp"] += 1
        d["ratios"].append(v / b)

    import numpy as np

    print(
        f"  {'right':<5} {'band':<16} {'n':>4} {'same%':>7} {'sign(v/b)med':>12} {'|v/b|':>7}"
    )
    for right in ("C", "P"):
        for band, _, _ in bands:
            d = results.get((right, band))
            if not d or d["n"] == 0:
                continue
            r = np.array(d["ratios"])
            logr = np.log(np.abs(r))
            same_pct = d["same"] / d["n"]
            med_sign = np.median(np.sign(r))
            scale = np.exp(np.median(logr))
            print(
                f"  {right:<5} {band:<16} {d['n']:>4} {same_pct:>7.3f} {med_sign:>12.3f} {scale:>7.3f}"
            )

    # overall: is it a pure -1 sign flip with ~1 magnitude? fit v = a*b via robust median
    all_r = []
    for d in results.values():
        all_r.extend(d["ratios"])
    if all_r:
        r = np.array(all_r)
        logr = np.log(np.abs(r))
        print(
            f"  OVERALL: n={len(r)} |v/b| scale={np.exp(np.median(logr)):.3f} "
            f"sign-consistency (mean sign ratio)={np.mean(np.sign(r)):.3f} "
            f"(=+1 pure same, =0 random, =-1 pure flip)"
        )


def main():
    ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    td = ThetaDataController()
    try:
        for t in [ticker] if ticker != "MULTI" else ["AAPL", "AMD", "NVDA"]:
            if ticker == "MULTI" and t != "AAPL":
                pass
            characterize(td, t)
            if ticker != "MULTI":
                break
    finally:
        td.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
