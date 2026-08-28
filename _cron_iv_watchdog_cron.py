"""IV alert watchdog cron - scans watchlist tickers for vol anomalies.
Watchlist = all tickers in latest sentiment manifest + core broad-market defaults (deduped).
Fast path: 30-DTE ATM IV (live greeks, calls) vs 20d realized vol from EOD closes.
Alert: IV/RV ratio > 1.5 (spike). Crush notify: ratio < 0.7 confirmed by recent RV.
"""
import json, os, sys, time, math
sys.path.insert(0, '.')
import numpy as np
from shared.thetadata import ThetaDataController
from datetime import date, timedelta

CORE = ["SPY", "QQQ", "AAPL", "NVDA", "MSFT", "AMD", "MU"]
MANIFEST = "sentiment-scanner/data/exports/highlighted_ticker_packs/latest_manifest.json"
OUT = "artifacts/iv_watchdog_results.json"


def load_manifest_tickers():
    t = []
    try:
        with open(MANIFEST) as f:
            m = json.load(f)
        for p in m.get("packs", []):
            t.extend(p.get("tickers", []))
    except Exception as e:
        print(f"WARN manifest: {e}")
    return t


def dedupe(t):
    out, seen = [], set()
    for s in t:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def classify(iv, rv20, rv_recent):
    """Return (status, note). rv_recent = ~12d RV used to confirm crush."""
    if rv20 <= 0 or rv_recent <= 0:
        return "na", "no RV baseline"
    ratio = iv / rv20
    if ratio > 1.5:
        return "ALERT", f"IV {ratio:.2f}x 20d RV"
    if iv > 150:
        return "elevated", "raw IV > 150%"
    if ratio < 0.7:
        if rv_recent < 35:
            return "crush", f"IV/RV {ratio:.2f}x (recent RV {rv_recent:.0f}% confirms)"
        return "check", f"IV/RV {ratio:.2f}x but recent RV {rv_recent:.0f}% (stale-event?)"
    return "normal", ""


def main():
    manifest_t = load_manifest_tickers()
    tickers = dedupe(CORE + manifest_t)
    print(f"Watchlist({len(tickers)}): {','.join(tickers)}", flush=True)

    td = ThetaDataController()
    today = date.today()
    target = int((today + timedelta(days=30)).strftime("%Y%m%d"))
    today_int = int(today.strftime("%Y%m%d"))
    start = (today - timedelta(days=40)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")

    results = []
    for t in tickers:
        rec = {"ticker": t, "spot": None, "atm_iv": None, "rv20": None,
               "rv_recent": None, "ratio": None, "status": "skip", "note": "", "exp": None}
        try:
            spot_raw = td.fetch_spot_price(t)
            time.sleep(0.3)
            if not spot_raw:
                rec["note"] = "no spot"; results.append(rec); continue
            spot = float(spot_raw)
            rec["spot"] = round(spot, 2)

            exps = td.list_expirations(t)
            time.sleep(0.3)
            if not exps:
                rec["note"] = "no options"; results.append(rec); continue
            good = sorted([(abs(int(e) - target), e) for e in exps if int(e) > today_int + 14])
            if not good:
                rec["note"] = "no valid exp"; results.append(rec); continue
            exp = good[0][1]
            rec["exp"] = exp

            rows = td.option_bulk_greeks(t, exp)
            time.sleep(0.5)
            window = max(5.0, spot * 0.02)
            cur_iv = None
            for r in (rows or []):
                try:
                    k = float(r.get("strike", 0)) / 1000.0
                    iv = r.get("implied_vol")
                    if abs(k - spot) < window and r.get("right") == "C" and iv and float(iv) > 0:
                        cur_iv = float(iv) * 100.0
                        break
                except Exception:
                    continue
            if not cur_iv:
                rec["note"] = "no ATM IV"; results.append(rec); continue
            rec["atm_iv"] = round(cur_iv, 1)

            eod = td.hist_stock_eod(t, start, end)
            time.sleep(0.3)
            closes = [float(r["close"]) for r in (eod or []) if r.get("close") and float(r["close"]) > 0]
            if len(closes) >= 16:
                rets = np.diff(np.log(closes))
                rv20 = float(np.std(rets[-20:]) * math.sqrt(252) * 100)
                rv_recent = float(np.std(rets[-12:]) * math.sqrt(252) * 100)
                rec["rv20"] = round(rv20, 2)
                rec["rv_recent"] = round(rv_recent, 2)
                rec["ratio"] = round(cur_iv / rv20, 3) if rv20 > 0 else None
                status, note = classify(cur_iv, rv20, rv_recent)
                rec["status"] = status
                rec["note"] = note
            else:
                rec["note"] = "insufficient EOD history"
        except Exception as e:
            rec["note"] = f"ERR {str(e)[:70]}"
        results.append(rec)
        print(f"{rec['ticker']:8s} spot={str(rec['spot']):>8s} iv={str(rec['atm_iv']):>6s} "
              f"rv20={str(rec['rv20']):>6s} ratio={str(rec['ratio']):>7s} {rec['status']:8s} {rec['note'][:50]}", flush=True)
    td.close()

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump({"ts": today.isoformat(), "watchlist": tickers, "results": results}, f, indent=1)
    print(f"WROTE {OUT}", flush=True)


if __name__ == "__main__":
    main()
